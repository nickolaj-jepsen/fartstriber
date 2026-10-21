"""The lines the near-tangent lint judges an element's edges against, and how it measures a run along them.

An island's outline is such a line only where the paint stops on the car: a stretch `seams.json` pairs with
another island is continuous surface, the paint carries on across it, so it is no line to keep clear of.
Outline with no seam partner (glass, arches, unpainted parts), shut lines and the outlines of the strict
keep-clear zones are kept. Each kept line belongs to the islands it lies on, and an element's edge is judged
only against the lines of the island it is on: in the texture another island's outline may lie a few
pixels away, on the car it is somewhere else.

An edge meets a line within MEET_PX of it, and an edge on any outline, paired or kept, is where the element
was cut to its island, not a designed edge. A free edge is the rest. A run is the length of kept line,
measured along that line, that a free edge runs alongside within reach.

Lines are texture geometry in template pixels; the lint rasterises them, the library (`livery.geom`) uses
them as they are.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
import shapely
import shapely.ops
from PIL import Image
from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry

from livery import carspace
from livery.kit import Kit

MEET_PX = 3.0  # an edge this close to a line meets it, in texture px
RUN_GAP_PX = 6.0  # a free edge's points closer than this along a line are one run
# A seam point within this of an outline is on it (seam points sit just inside their island).
SEAM_NEAR_PX = 4.0
# `kit fit` samples island edges every `kit_fit.SEAM_STEP_PX` and keeps the pairs it can confirm: outline
# between seam points up to SEAM_GAP_PX apart is paired too (one sample it dropped), and a paired stretch
# reaches SEAM_END_PX past its last point.
SEAM_GAP_PX, SEAM_END_PX = 20.0, 4.0

PAINT_EDGE, SHUT_LINE = "paint edge", "shut line"


@dataclass(frozen=True, eq=False)
class Boundary:
    """The kit's islands and the near-tangent lines on them.

    `lines` are the kept lines (each a stretch of one outline, a shut line or a zone's outline), `kinds`
    what each is, `owners` the islands (indices into `islands`) each is judged on. `seams` are the
    outline stretches paired as continuous surface: no lines, but an edge on one meets it."""

    ids: tuple[str, ...]
    islands: tuple[BaseGeometry, ...]
    lines: tuple[LineString, ...]
    kinds: tuple[str, ...]
    owners: tuple[tuple[int, ...], ...]
    seams: tuple[LineString, ...]

    @property
    def lengths(self) -> np.ndarray:
        return np.array([ln.length for ln in self.lines])

    @property
    def closed(self) -> np.ndarray:
        return np.array([ln.is_closed for ln in self.lines], bool)

    def owned(self) -> dict[int, list[int]]:
        """Each island's lines, by island index."""
        out: dict[int, list[int]] = {}
        for i, owners in enumerate(self.owners):
            for k in owners:
                out.setdefault(k, []).append(i)
        return out

    def at(self, line: int, s: float) -> tuple[float, float]:
        """The texture point `s` px along a line (around a closed one)."""
        ln = self.lines[line]
        p = ln.interpolate(s % ln.length if ln.is_closed else s)
        return float(p.x), float(p.y)


@dataclass(frozen=True)
class Run:
    """A stretch of kept line a free edge runs alongside: `s0` to `s1` px along line `line` (past its
    length where it wraps round a closed line's start)."""

    length: float
    line: int
    s0: float
    s1: float


def polygons(geom: BaseGeometry) -> list[Polygon]:
    return [g for g in getattr(geom, "geoms", [geom]) if isinstance(g, Polygon) and not g.is_empty]


def _traced(mask: np.ndarray) -> list[Polygon]:
    """A mask's pieces as polygons, outlines traced through the outer pixels' centres, holes cut out."""
    import cv2

    contours, hierarchy = cv2.findContours(mask.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for i, c in enumerate(contours):
        if hierarchy[0][i][3] >= 0:  # a hole: taken with its outer contour
            continue
        pts = c[:, 0, :].astype(float)
        if len(pts) < 3:
            out.append(shapely.Point(pts[0]).buffer(0.5))
            continue
        holes, child = [], hierarchy[0][i][2]
        while child >= 0:
            hp = contours[child][:, 0, :].astype(float)
            if len(hp) >= 3:
                holes.append(hp)
            child = hierarchy[0][child][0]
        out += [
            p if p.area >= 1 else p.buffer(0.5) for p in polygons(shapely.make_valid(Polygon(pts, holes)))
        ]
    return out


def shut_lines(kit: Kit) -> list[Polygon]:
    """The kit's shut lines (`overlays/shutlines.png`, the panel gaps the wireframe draws thick)."""
    path = kit.dir / "overlays" / "shutlines.png"
    if not path.exists():
        return []
    return _traced(np.asarray(Image.open(path).convert("L")) > 127)


def _islands(kit: Kit) -> dict[str, BaseGeometry]:
    """The island shapes, or for a kit not yet split into islands, its mask's pieces."""
    shapes = carspace.island_shapes(kit)
    if shapes or not kit.mask_path.exists():
        return shapes
    mask = np.asarray(Image.open(kit.mask_path).convert("L")) > 127
    return {f"m{i:04d}": p for i, p in enumerate(_traced(mask))}


def _intervals(s: np.ndarray, length: float) -> list[tuple[float, float]]:
    """Seam points' positions along a closed line as the stretches they pair; one may wrap past the line's
    start."""
    if not len(s):
        return []
    s = np.sort(s)
    cut = np.nonzero(np.diff(s) > SEAM_GAP_PX)[0]
    spans = [[s[a], s[b]] for a, b in zip(np.r_[0, cut + 1], np.r_[cut, len(s) - 1], strict=True)]
    if len(spans) > 1 and spans[0][0] + length - spans[-1][1] <= SEAM_GAP_PX:
        first = spans.pop(0)
        spans[-1][1] = first[1] + length
    return [(float(a - SEAM_END_PX), float(b + SEAM_END_PX)) for a, b in spans]


def ring_part(ring: LineString, a: float, b: float) -> LineString:
    """The part of a closed line from `a` to `b` px along it, wrapping past its start."""
    length = ring.length
    a, b = a % length, a % length + (b - a)
    if b <= length:
        return LineString(shapely.ops.substring(ring, a, b).coords)
    one, two = shapely.ops.substring(ring, a, length), shapely.ops.substring(ring, 0, b - length)
    return LineString([*one.coords, *list(two.coords)[1:]])


def _split(ring: LineString, paired: list[tuple[float, float]]) -> tuple[list[LineString], list[LineString]]:
    """A closed line as its parts outside the paired stretches, and the paired stretches."""
    length = ring.length
    covered: list[tuple[float, float]] = []
    for a, b in paired:
        if b - a >= length:
            return [], [ring]
        a0 = a % length
        covered += [(a0, length), (0.0, a0 + b - a - length)] if a0 + b - a > length else [(a0, a0 + b - a)]
    merged: list[list[float]] = []
    for a, b in sorted(covered):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    gaps, at = [], 0.0
    for a, b in merged:
        if a > at:
            gaps.append([at, a])
        at = b
    if at < length:
        gaps.append([at, length])
    if len(gaps) > 1 and gaps[0][0] == 0.0 and gaps[-1][1] == length:  # one gap across the start
        gaps[-1][1] = length + gaps.pop(0)[1]
    if len(merged) > 1 and merged[0][0] == 0.0 and merged[-1][1] == length:
        merged[-1][1] = length + merged.pop(0)[1]
    kept = [ring_part(ring, a, b) for a, b in gaps if b > a]
    return kept, [ring_part(ring, a, b) for a, b in merged if b > a]


def _seam_points(kit: Kit) -> np.ndarray:
    path = kit.dir / "seams.json"
    if not path.exists():
        return np.zeros((0, 2))
    pairs = np.asarray(json.loads(path.read_text(encoding="utf-8")), float).reshape(-1, 4)
    return np.unique(np.concatenate([pairs[:, :2], pairs[:, 2:]]), axis=0)


def _stamp(kit: Kit) -> tuple[int, ...]:
    files = ("kit.yaml", "seams.json", "regions.svg", "overlays/shutlines.png", "overlays/mask.png")
    return tuple((kit.dir / f).stat().st_mtime_ns if (kit.dir / f).exists() else 0 for f in files)


_cache: dict[tuple, Boundary] = {}


def boundary(kit: Kit, strict: Iterable[str]) -> Boundary:
    """The kit's near-tangent lines: island outlines less their seam-paired stretches, the shut lines, and
    the outlines of the keep-clear zones of the `strict` kinds. Built once per kit state."""
    key = (str(kit.dir), _stamp(kit), tuple(sorted(strict)))
    if key not in _cache:
        _cache.clear()
        _cache[key] = _boundary(kit, key[2])
    return _cache[key]


def _boundary(kit: Kit, strict: tuple[str, ...]) -> Boundary:
    shapes = sorted(_islands(kit).items())
    ids = tuple(rid for rid, _ in shapes)
    islands = tuple(g for _, g in shapes)
    tree = shapely.STRtree(islands)
    points = np.asarray(shapely.points(_seam_points(kit)), object).reshape(-1)
    lines: list[LineString] = []
    kinds: list[str] = []
    owners: list[tuple[int, ...]] = []
    seams: list[LineString] = []
    for k, shape in enumerate(islands):
        mine = points[shapely.contains(shape.buffer(1.0), points)] if len(points) else points
        for poly in polygons(shape):
            for ring in (poly.exterior, *poly.interiors):
                line = LineString(ring.coords)
                near = mine[np.asarray(shapely.distance(line, mine)) <= SEAM_NEAR_PX] if len(mine) else mine
                s = np.asarray(shapely.line_locate_point(line, near), float).reshape(-1)
                kept, paired = _split(line, _intervals(s, line.length))
                seams += paired
                lines += kept
                kinds += [PAINT_EDGE] * len(kept)
                owners += [(k,)] * len(kept)

    def on(geom: BaseGeometry) -> tuple[int, ...]:
        hit = tree.query(geom.buffer(1.0), predicate="intersects")
        return tuple(sorted(int(i) for i in hit)) if len(hit) else (int(tree.query_nearest(geom)[0]),)

    for poly in shut_lines(kit):
        for ring in (poly.exterior, *poly.interiors):
            lines.append(LineString(ring.coords))
            kinds.append(SHUT_LINE)
            owners.append(on(poly) if islands else ())
    for zone in carspace.kit_data(kit).get("keep_clear", []):
        if zone["kind"] in strict:
            box = shapely.box(*zone["box"])
            lines.append(LineString(box.exterior.coords))
            kinds.append(f"{zone['kind']} zone")
            owners.append(on(box) if islands else ())
    return Boundary(ids, islands, tuple(lines), tuple(kinds), tuple(owners), tuple(seams))


def runs(b: Boundary, line: np.ndarray, pos: np.ndarray, gap: float = RUN_GAP_PX) -> list[Run]:
    """The stretches of kept line that points of a free edge project onto (`line` and `pos`: each point's
    nearest line and how far along it), joined where they lie within `gap` of each other."""
    out = []
    lengths, closed = b.lengths, b.closed
    for i in np.unique(line):
        s = np.sort(pos[line == i])
        cut = np.nonzero(np.diff(s) > gap)[0]
        spans = [
            [float(s[a]), float(s[e])] for a, e in zip(np.r_[0, cut + 1], np.r_[cut, len(s) - 1], strict=True)
        ]
        size = float(lengths[i])
        if closed[i] and spans[0][0] + size - spans[-1][1] <= gap:
            if len(spans) == 1:
                spans = [[0.0, size]]
            else:
                first = spans.pop(0)
                spans[-1][1] = first[1] + size
        out += [Run(e - a, int(i), a, e) for a, e in spans]
    return out
