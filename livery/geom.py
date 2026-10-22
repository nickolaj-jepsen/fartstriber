"""Geometry for designs that draw many pieces: car-space shapes projected in batches into one element per
view, rake and scale, clean-up to the lint's minimum feature, and clearance from (and snapping to) the lines
the near-tangent lint judges, read from its own thresholds.

Car space is as in `livery.carspace`. Texture geometry is shapely, in template pixels.
"""

import itertools
import math
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import cast

import numpy as np
import shapely
import shapely.affinity
import shapely.ops
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from livery import carspace
from livery.carspace import Piece
from livery.errors import LiveryError
from livery.kit import Kit
from livery.lint.edges import MEET_PX, RUN_GAP_PX, Boundary, Run, boundary, ring_part, runs
from livery.lint.run import thresholds
from livery.model import Element, Material

# A car-space shape: a polygon as a point list, or a shapely polygon or multipolygon (holes cut out).
Shape = Sequence[tuple[float, float]] | BaseGeometry


def polygons(geom: BaseGeometry) -> list[Polygon]:
    """The non-empty polygons in a geometry."""
    return [g for g in getattr(geom, "geoms", [geom]) if isinstance(g, Polygon) and not g.is_empty]


def _union(geoms: list[BaseGeometry]) -> BaseGeometry:
    return shapely.union_all(geoms) if geoms else Polygon()


# --- projection ----------------------------------------------------------------------------------------


def footprint(kit: Kit, view: str, shape: Shape, through: Iterable[Piece] | None = None) -> BaseGeometry:
    """A car-space shape's footprint in the texture, on every island it lands on. A shapely shape is
    projected polygon by polygon with its holes cut out. `through` replaces the view's pieces
    (`carspace.project`)."""
    if not isinstance(shape, BaseGeometry):
        return carspace.project(kit, view, shape, through)
    through = None if through is None else tuple(through)
    parts = []
    for g in polygons(shape):
        f = carspace.project(kit, view, list(g.exterior.coords[:-1]), through)
        for hole in g.interiors:
            f = f.difference(carspace.project(kit, view, list(hole.coords[:-1]), through))
        parts.append(f)
    return _union(parts)


def _path(geom: BaseGeometry, color: str) -> str:
    d = carspace.path_d(geom)
    return f'<path fill-rule="evenodd" d="{d}" fill="{color}"/>' if d else ""


def paint(
    id: str,
    geom: BaseGeometry,
    color: str,
    material: Material = Material.gloss,
    z: int = 0,
    tags: tuple[str, ...] = (),
) -> Element:
    """Texture geometry in one colour, as one element: for footprints a design reshapes after projecting."""
    return Element(id=id, kind="svg", svg=_path(geom, color), material=Material(material), z=z, tags=tags)


def fill(
    kit: Kit,
    id: str,
    view: str,
    layers: Mapping[str, Iterable[Shape]],
    avoid: BaseGeometry | None = None,
    edges: "Edges | None" = None,
    grow: bool = False,
    through: Iterable[Piece] | None = None,
    material: Material = Material.gloss,
    z: int = 0,
    tags: tuple[str, ...] = (),
) -> Element:
    """Many car-space shapes on one view as one element, one path per colour (`layers` maps a colour to its
    shapes): a pattern of hundreds of pieces stays one element for the lints. With `avoid` (texture
    geometry), pieces touching it are left out; with `edges`, each piece is kept clear of them
    (`Edges.settle`, `grow` to let it grow onto a line)."""
    through = None if through is None else tuple(through)
    paths = []
    for color, shapes in layers.items():
        geoms = [footprint(kit, view, s, through) for s in shapes]
        if avoid is not None:
            geoms = [g for g in geoms if not g.intersects(avoid)]
        if edges is not None:
            geoms = [c for g in geoms if (c := edges.settle(g, grow)) is not None]
        if geoms:
            paths.append(_path(shapely.union_all(geoms), color))
    return Element(
        id=id,
        kind="shape",
        svg="".join(paths),
        material=Material(material),
        z=z,
        tags=tags,
        car={"view": view},
    )


def shows(
    kit: Kit, view: str, shape: BaseGeometry, share: float, through: Iterable[Piece] | None = None
) -> bool:
    """Whether the paint shows at least `share` of a car-space shape, judged by its footprint's area at the
    map's scale where the shape sits: a piece cut by glass, an arch or the edge of the view doesn't.
    False when its middle is off every map."""
    if view == "top":
        t = carspace.kit_data(kit)["top_transform"]
        scale = abs(t["x_per_u"] * t["y_per_w"])
    else:
        q = shape.representative_point()
        try:
            _, _, jac = carspace.anchor(kit, view, (q.x, q.y))
        except ValueError:
            return False
        scale = abs(float(np.linalg.det(jac)))
    return footprint(kit, view, shape, through).area >= share * shape.area * scale


# --- car-space shapes ----------------------------------------------------------------------------------


def rake(kit: Kit, deg: float, view: str = "side_l") -> float:
    """u per unit of v (w on top) along a line leaning `deg` as seen: positive leans its top (on top, its
    outer end) towards the tail."""
    return math.tan(math.radians(deg)) * carspace.aspect(kit, view)


def slant(u0: float, u1: float, a0: float, a1: float, k: float) -> list[tuple[float, float]]:
    """The strip between u0 and u1 (where v or w is 0) from a0 to a1, leaning `k` u per unit (`rake`)."""
    return [(u0 + k * a0, a0), (u1 + k * a0, a0), (u1 + k * a1, a1), (u0 + k * a1, a1)]


def as_seen(geom: BaseGeometry, k: float) -> BaseGeometry:
    """Car space to the view as seen, where v or w is scaled by `k` (`carspace.aspect`) into units of u."""
    return shapely.affinity.scale(geom, 1.0, k, origin=(0, 0))


def as_car(geom: BaseGeometry, k: float) -> BaseGeometry:
    """The view as seen back to car space."""
    return shapely.affinity.scale(geom, 1.0, 1.0 / k, origin=(0, 0))


def grow(
    points: Sequence[tuple[float, float]], px: float, scale: tuple[float, float], mitre_limit: float = 3.0
) -> list[tuple[float, float]]:
    """A car-space polygon grown by `px` texture px, mitred, at `scale` (`carspace.px_per_unit`)."""
    sx, sy = scale
    poly = Polygon([(u * sx, a * sy) for u, a in points]).buffer(
        px, join_style="mitre", mitre_limit=mitre_limit
    )
    return [(x / sx, y / sy) for x, y in poly.exterior.coords[:-1]]


def segment(
    start: tuple[float, float], end: tuple[float, float], px: float, scale: tuple[float, float]
) -> list[tuple[float, float]]:
    """A straight line `px` texture px wide between two car-space points, at `scale`."""
    sx, sy = scale
    (u0, a0), (u1, a1) = start, end
    du, da = (u1 - u0) * sx, (a1 - a0) * sy
    n = math.hypot(du, da)
    ou, oa = -da / n * px / 2 / sx, du / n * px / 2 / sy
    return [(u0 + ou, a0 + oa), (u1 + ou, a1 + oa), (u1 - ou, a1 - oa), (u0 - ou, a0 - oa)]


# --- clean-up and clearance ----------------------------------------------------------------------------


def drop_crumbs(geom: BaseGeometry, min_area: float) -> BaseGeometry:
    """The geometry without its pieces smaller than `min_area` texture px²."""
    return _union([g for g in polygons(geom) if g.area >= min_area])


def clean(geom: BaseGeometry, min_area: float = 0.0, margin: float = 0.0) -> BaseGeometry:
    """Features thinner than the lint's `min_feature_px` (plus `margin`) opened away with square corners,
    then pieces under `min_area` texture px² dropped."""
    r = (thresholds().min_feature_px + margin) / 2
    return drop_crumbs(geom.buffer(-r, join_style="mitre").buffer(r, join_style="mitre"), min_area)


# A piece pulled back from a line must keep this share of itself and stay at least twice this thick (px).
PULL_KEEP, PULL_MIN_PX = 0.4, 4.0
# A piece grown onto a line looks for it this far past the reach (px), and may at most double.
GROW_PAST_PX, GROW_MAX = 4.0, 2.0
# How much stricter than the lint the library keeps its own pieces, in texture px: a wider reach, a
# narrower meeting distance and a shorter run (`edges`), since the lint judges a rendered mask.
MARGINS = (2.0, 0.5, 6.0)
# A snapped edge goes this far past the line it is moved onto, or stops this far beyond the lint's reach,
# the slack its half-scale rendering leaves; the stretch to move is found this much beyond the reach (all
# texture px). The lint's own measure confirms every move.
OVERSHOOT_PX, CLEAR_PX, SNAP_MARGIN = 1.0, 3.0, 1.0
# A cut is no fix if less than this share of the points BEYOND_PX past its new edge is still shape.
KEEP_BEYOND, BEYOND_PX = 0.8, 3.0
# A free edge is followed at this spacing (texture px) to project it onto the lines.
STEP_PX = 1.0


@dataclass(frozen=True)
class Snap:
    """One move `Edges.snap` made: the edge alongside `run` px of a line moved at most `px` px onto it or
    clear of it."""

    to: str  # "onto" or "clear of"
    line: str  # what the line is: a paint edge, a shut line, a keep-clear zone
    px: float
    run: float
    at: tuple[float, float]  # the middle of the edge stretch that moved, texture px


@dataclass(frozen=True)
class _Free:
    """A free edge stretch's points in order, each one's line (the nearest of its island's), how far along
    the line it is, that point on the line, and the island it is on."""

    pts: np.ndarray
    line: np.ndarray
    pos: np.ndarray
    foot: np.ndarray
    island: np.ndarray


@dataclass(frozen=True, eq=False)
class Edges:
    """The lines the near-tangent lint judges edges against (`livery.lint.edges`), at its thresholds:
    buffered to where an edge meets a line or outline, and to the reach of each island's own lines, on
    that island."""

    b: Boundary
    zones: tuple[Polygon, ...]  # the strict keep-clear zones (the sim's number)
    islands: tuple[BaseGeometry, ...]
    meet: BaseGeometry
    near: BaseGeometry
    reach: float  # px
    run: float  # px: a free edge running alongside a line this long fails
    on: shapely.STRtree  # the islands grown to hold their outlines: which island a point is on
    trees: dict[int, tuple[np.ndarray, shapely.STRtree]]  # each island's lines, to find the nearest

    def alongside(self, geom: BaseGeometry, within: BaseGeometry | None = None) -> BaseGeometry:
        """The parts of a texture shape's outline (in `within`, if given) within reach of a line of their
        own island, meeting no line or outline."""
        edge = _outline(geom) if within is None else _outline(geom).intersection(within)
        if edge.is_empty or not self.near.intersects(edge):  # far from every line: skip the costly overlay
            return LineString()
        return edge.difference(self.meet).intersection(self.near)

    def touches(self, geom: BaseGeometry) -> bool:
        """Whether any free part of its outline comes within reach of a line."""
        edge = _outline(geom)
        return self.near.intersects(edge) and edge.difference(self.meet).intersects(self.near)

    def _free(self, geom: BaseGeometry, within: BaseGeometry | None = None) -> list[_Free]:
        lines = np.asarray(self.b.lines, object)
        out = []
        for ln in _lines(self.alongside(geom, within)):
            pts = shapely.get_coordinates(shapely.segmentize(ln, STEP_PX))
            where = np.asarray(shapely.points(pts), object).reshape(-1)
            hit = np.asarray(self.on.query(where, predicate="intersects"))
            island = np.full(len(pts), -1)
            island[hit[0][::-1]] = hit[1][::-1]  # the lowest island index each point is on
            line, pos = np.full(len(pts), -1), np.zeros(len(pts))
            for k in np.unique(island[island >= 0]):
                if int(k) not in self.trees:
                    continue
                ids, tree = self.trees[int(k)]
                sel = island == k
                nearest = ids[tree.query_nearest(where[sel], all_matches=False)[1]]
                line[sel] = nearest
                pos[sel] = shapely.line_locate_point(lines[nearest], where[sel])
            ok = line >= 0
            if ok.any():
                foot = shapely.get_coordinates(shapely.line_interpolate_point(lines[line[ok]], pos[ok]))
                out.append(_Free(pts[ok], line[ok], pos[ok], foot, island[ok]))
        return out

    def runs(self, geom: BaseGeometry, within: BaseGeometry | None = None) -> list[Run]:
        """The stretches of line its free edge runs alongside, measured along the line as the lint does."""
        free = self._free(geom, within)
        if not free:
            return []
        return runs(self.b, np.concatenate([f.line for f in free]), np.concatenate([f.pos for f in free]))

    def longest(self, geom: BaseGeometry) -> float:
        """The longest stretch of line its free edge runs alongside."""
        return max((r.length for r in self.runs(geom)), default=0.0)

    def clear(self, geom: BaseGeometry) -> bool:
        """Whether no free stretch runs alongside a line for the lint's run."""
        return geom.is_empty or self.longest(geom) < self.run

    def settle(self, geom: BaseGeometry, grow: bool = False) -> BaseGeometry | None:
        """A texture piece kept clear of the lines: as it is if no free edge comes within reach; else pulled
        back out of reach if enough of it is left; else, with `grow`, grown within its islands out to the
        line it nears if that at most doubles it; else None."""
        if geom.is_empty or not self.touches(geom):
            return geom
        rest = geom.difference(self.near)
        if rest.area > PULL_KEEP * geom.area and not rest.buffer(-PULL_MIN_PX).is_empty:
            return rest
        if not grow:
            return None
        reach = geom.buffer(self.reach + GROW_PAST_PX, join_style="mitre").intersection(self.near)
        grown = shapely.union_all(
            [geom.union(reach).intersection(i) for i in self.islands if i.intersects(geom)]
        )
        return grown if 0 < grown.area <= GROW_MAX * geom.area else None

    def snap(
        self, geom: BaseGeometry, limit: float, failing: Callable[[BaseGeometry], list[Run]]
    ) -> tuple[BaseGeometry, list[Snap], list[Run]]:
        """A texture shape with each stretch of free edge that `failing` (the lint's own measure) finds
        running alongside a line moved onto the line, meeting it, or clear of its reach, whichever moves
        it less, by at most `limit` px; a move is kept only if fewer stretches fail after it. Returns the
        shape, the moves and the stretches still failing, for the lint to name."""
        moves: list[Snap] = []
        fails = failing(geom)
        tried: set[tuple[int, int]] = set()
        while True:
            todo = [r for r in fails if (r.line, round(r.s0)) not in tried]
            if not todo:
                return geom, moves, fails
            r = max(todo, key=lambda r: r.length)
            tried.add((r.line, round(r.s0)))
            for moved, move in self._moves(geom, r, limit):
                after = failing(moved)
                if len(after) < len(fails):
                    geom, fails = moved, after
                    moves.append(move)
                    break

    def _stretch(self, geom: BaseGeometry, r: Run) -> list[_Free]:
        """The ordered pieces of the free edge that run alongside one run, each whole: from where it comes
        within reach of the run's line to where it leaves it, meets it or meets an outline."""
        line = self.b.lines[r.line]
        size, owners = line.length, np.asarray(self.b.owners[r.line])
        lo, hi = r.s0 - RUN_GAP_PX, r.s1 + RUN_GAP_PX
        out = []
        for f in self._free(geom, _band(line, r, self.reach + self.run)):
            # Measured against this line, even where another one of the island is nearer.
            pos = np.asarray(shapely.line_locate_point(line, shapely.points(f.pts)), float)
            foot = shapely.get_coordinates(shapely.line_interpolate_point(line, pos))
            ok = (np.hypot(*(f.pts - foot).T) <= self.reach) & np.isin(f.island, owners)
            for a, b in _spans(ok):
                s = pos[a:b]
                if b - a >= 2 and (((s >= lo) & (s <= hi)) | ((s + size >= lo) & (s + size <= hi))).any():
                    out.append(_Free(f.pts[a:b], np.full(b - a, r.line), s, foot[a:b], f.island[a:b]))
        return out

    def _cuts(self, f: _Free) -> tuple[bool, bool]:
        """Whether each end of a free edge piece is where the shape was cut at an island outline: there
        the edge runs on over the next island, so a move there runs on with it."""
        d = np.hypot(*(f.pts[[0, -1]] - f.foot[[0, -1]]).T)
        start, end = (
            bool(dd > MEET_PX + 0.5 and self.meet.distance(shapely.Point(p)) < 1.0)
            for dd, p in zip(d, f.pts[[0, -1]], strict=True)
        )
        return start, end

    def _moves(self, geom: BaseGeometry, r: Run, limit: float) -> Iterator[tuple[BaseGeometry, Snap]]:
        """The shape with one run's stretch moved onto its line and clear of it, the smaller move first."""
        pieces = self._stretch(geom, r)
        if not pieces:
            return
        window = _band(self.b.lines[r.line], r, self.reach + self.run)
        thin = _thin(geom.intersection(window))
        options = []
        reach = thresholds().near_tangent_px  # the lint's, which a move clear must get beyond
        for to, target in (("onto", -OVERSHOOT_PX), ("clear of", reach + CLEAR_PX)):
            strips = [_strip(f, target, self.reach, MEET_PX, reach, self._cuts(f)) for f in pieces]
            px = max(m for _, m, _ in strips)
            if px <= limit:
                region = _union([g for g, _, _ in strips]).buffer(0.3, join_style="mitre")
                options.append((px, to, region, np.concatenate([b for _, _, b in strips])))
        for px, to, region, beyond in sorted(options, key=lambda o: o[0]):
            if geom.intersection(region).area > region.area / 2:  # the stretch moves into the shape
                moved = geom.difference(region)
                # A cut must leave the shape standing beyond its new edge, not go through it.
                if len(beyond) and shapely.contains_xy(moved, *beyond.T).mean() < KEEP_BEYOND:
                    continue
            else:  # it moves out, over its islands and never into the sim's number
                room = _union([i for i in self.islands if i.intersects(region)]).difference(
                    _union(list(self.zones))
                )
                moved = geom.union(region.intersection(room))
            # Crumbs the cut leaves behind go; a move that leaves slivers under the minimum feature is no fix.
            moved = _union(
                [g for g in polygons(moved) if g.area >= 4 * PULL_MIN_PX**2 or not g.intersects(region)]
            )
            if _thin(moved.intersection(window)) > thin + 4 * PULL_MIN_PX**2:
                continue
            mid = max(pieces, key=lambda f: len(f.pts))
            x, y = mid.pts[len(mid.pts) // 2]
            yield moved, Snap(to, self.b.kinds[r.line], round(px, 1), round(r.length), (round(x), round(y)))


def _outline(geom: BaseGeometry) -> BaseGeometry:
    """The boundary of a texture shape's polygonal part. A difference or union can leave polygons with stray
    lines or points, a collection with no boundary of its own."""
    if not isinstance(geom, Polygon | MultiPolygon):
        geom = _union(polygons(geom))
    return geom.boundary


def _spans(mask: np.ndarray) -> list[tuple[int, int]]:
    """The [start, stop) index ranges where a boolean array is true."""
    step = np.diff(np.r_[0, mask.astype(int), 0])
    return [
        (int(a), int(b)) for a, b in zip(np.nonzero(step == 1)[0], np.nonzero(step == -1)[0], strict=True)
    ]


def _band(line: LineString, r: Run, px: float) -> BaseGeometry:
    """The texture within `px` of a run's stretch of line."""
    if line.is_closed and r.s1 > line.length:
        part = ring_part(line, r.s0, r.s1)
    else:
        part = shapely.ops.substring(line, max(r.s0, 0.0), min(r.s1, line.length))
    return part.buffer(px)


def _strip(
    f: _Free, target: float, ramp: float, meet: float, reach: float, cuts: tuple[bool, bool] = (False, False)
) -> tuple[BaseGeometry, float, np.ndarray]:
    """The texture a free edge piece sweeps moving to `target` px from its nearest line (beyond the line
    for a negative target), the farthest any point moves, and points a little beyond the moved edge in the
    direction it moved. Where the piece ends still short of where the move leaves it (an end within reach,
    for a move clear; one not yet meeting the line, for a move onto it), the move tapers to nothing over
    `ramp` px, so the edge bends back to its own course; at an end in `cuts`, where the shape was cut at
    an island outline, the move runs on to the outline instead."""
    v = f.pts - f.foot
    d = np.hypot(*v.T)
    n = v / np.maximum(d, 1e-9)[:, None]
    s = np.r_[0.0, np.cumsum(np.hypot(*np.diff(f.pts, axis=0).T))]
    w = np.ones(len(s))
    for end, along, cut in ((0, s, cuts[0]), (-1, s[-1] - s, cuts[1])):
        if not cut and ((d[end] < reach - 0.5) if target > 0 else (d[end] > meet + 0.5)):
            w = np.minimum(w, along / ramp)
    to = f.pts + (f.foot + n * target - f.pts) * np.clip(w, 0.0, 1.0)[:, None]
    quads = [
        shapely.make_valid(Polygon([f.pts[i], f.pts[i + 1], to[i + 1], to[i]])) for i in range(len(f.pts) - 1)
    ]
    for (end, inner), cut in zip(((0, 1), (-1, -2)), cuts, strict=True):
        if cut:  # on over the last pixels before the outline, which count as meeting it
            t = f.pts[end] - f.pts[inner]
            t = t / max(float(np.hypot(*t)), 1e-9) * (meet + 1.0)
            quads.append(shapely.make_valid(Polygon([f.pts[end], f.pts[end] + t, to[end] + t, to[end]])))
    step = to - f.pts
    size = np.hypot(*step.T)
    full = (w >= 1.0) & (size > 0)
    beyond = to[full] + step[full] / size[full, None] * BEYOND_PX
    return _union([q for q in quads if not q.is_empty]), float(size.max()), beyond


def _thin(geom: BaseGeometry) -> float:
    """The area of a shape's parts thinner than the lint's minimum feature."""
    r = thresholds().min_feature_px / 2
    return geom.difference(geom.buffer(-r, join_style="mitre").buffer(r, join_style="mitre")).area


_edge_cache: dict[tuple, Edges] = {}


def edges(kit: Kit, margin: float = 0.0, meet_margin: float = 0.0, run_margin: float = 0.0) -> Edges:
    """The near-tangent lint's lines for a kit (`livery.lint.edges`: island outlines less their seam-paired
    stretches, the shut lines and the strict keep-clear zones), at its thresholds in policy.toml. The
    margins make the test stricter than the lint, which judges a rendered mask: `margin` widens the reach,
    `meet_margin` narrows what counts as meeting a line, and `run_margin` shortens the run that fails
    (all texture px). Built once per kit state and margins."""
    t = thresholds()
    b = boundary(kit, t.keep_clear_strict)
    key = (id(b), margin, meet_margin, run_margin, t.near_tangent_px, t.near_tangent_run_px)
    if key in _edge_cache:
        return _edge_cache[key]
    zones = tuple(
        shapely.box(*z["box"])
        for z in carspace.kit_data(kit).get("keep_clear", [])
        if z["kind"] in t.keep_clear_strict
    )
    reach = t.near_tangent_px + margin
    meet = _union([g.buffer(MEET_PX - meet_margin) for g in (*b.lines, *b.seams)])
    on = [i.buffer(MEET_PX) for i in b.islands]
    near, trees = [], {}
    for k, mine in sorted(b.owned().items()):
        trees[k] = (np.asarray(mine), shapely.STRtree([b.lines[i] for i in mine]))
        near.append(shapely.union_all([b.lines[i] for i in mine]).buffer(reach).intersection(on[k]))
    near_all = _union(near)
    shapely.prepare(meet)
    shapely.prepare(near_all)
    run = t.near_tangent_run_px - run_margin
    e = Edges(b, zones, b.islands, meet, near_all, reach, run, shapely.STRtree(on), trees)
    if len(_edge_cache) >= 8:
        _edge_cache.clear()
    _edge_cache[key] = e
    return e


def failing(kit: Kit, geom: BaseGeometry) -> list[Run]:
    """The stretches of a texture shape's edge the near-tangent lint fails, judged as the lint judges an
    element drawn as that shape."""
    from livery.lint.output import near_lines, near_tangent, path_mask

    d = carspace.path_d(geom)
    if not d:
        return []
    t = thresholds()
    found = near_tangent(path_mask(d, kit.canvas), near_lines(kit, t), t)
    return [r for r in found if r.length >= t.near_tangent_run_px]


def _car_at(kit: Kit, view: str, xy: tuple[float, float]) -> list[float] | None:
    """The car-space point of `view` a texture point shows, where one of its maps covers it."""
    p = shapely.Point(xy)
    for pc in carspace.pieces(kit, view):
        if pc.area.distance(p) <= MEET_PX:
            car, _ = carspace.invert(pc, xy, carspace.start(view))
            return [round(float(c), 3) for c in car]
    return None


def snapped(kit: Kit, view: str | None, geom: BaseGeometry) -> tuple[BaseGeometry, tuple[dict, ...]]:
    """A footprint with each stretch of edge the near-tangent lint would fail snapped onto or clear of its
    line (`Edges.snap`, by at most the lint's reach and its slack), and each move as the lint report lists
    it: onto or clear of which line, by how much, over how long a run, and where (texture px, and car space
    where the view's maps cover it). A stretch no move fixes is left for the lint to name."""
    if geom.is_empty:
        return geom, ()
    limit = thresholds().near_tangent_px + CLEAR_PX
    out, moves, _ = edges(kit, SNAP_MARGIN).snap(geom, limit, lambda g: failing(kit, g))
    report = []
    for m in moves:
        row: dict = {"to": m.to, "line": m.line, "px": m.px, "run": m.run, "at": list(m.at)}
        if view is not None:
            row["view"] = view
            car = _car_at(kit, view, m.at)
            if car is not None:
                row["car"] = car
        report.append(row)
    return out, tuple(report)


# --- strokes -------------------------------------------------------------------------------------------

EDGE_STEP = 0.005  # car-space spacing a stroke's edge is sampled at before it is mapped
ALONG_TOL = 1e-5  # car units: how close a shape's edge must lie to `along` to count as the split
UNDERLAP_PX = 1.0  # a ring reaches this far under the ring drawn after it, so no base shows between
MITRE = 3.0


def _lines(geom: BaseGeometry) -> list[LineString]:
    """The non-empty line strings in a geometry, merged where they meet end to end."""
    if geom.is_empty:
        return []
    merged = shapely.line_merge(geom) if geom.geom_type == "MultiLineString" else geom
    out = []
    for g in getattr(merged, "geoms", [merged]):
        if isinstance(g, LineString) and not g.is_empty:
            out.append(g)
        elif hasattr(g, "geoms"):
            out += _lines(g)
    return out


def _densify_line(points: np.ndarray, step: float) -> np.ndarray:
    out = [points[0]]
    for a, b in itertools.pairwise(points):
        n = max(1, int(np.ceil(np.linalg.norm(b - a) / step)))
        out.extend(a + (b - a) * t for t in np.arange(1, n + 1) / n)
    return np.asarray(out)


def _edge(shape: BaseGeometry, sides: str, along: "Shape | None") -> BaseGeometry:
    """The car-space edge a stroke follows: every ring of the shape, or only its outer ones; with `along`,
    only where it borders that shape or line."""
    rings = []
    for g in polygons(shape):
        rings.append(LineString(g.exterior.coords))
        if sides == "all":
            rings += [LineString(r.coords) for r in g.interiors]
    edge: BaseGeometry = MultiLineString(rings)
    if along is not None:
        other = along if isinstance(along, BaseGeometry) else Polygon(along)
        edge = edge.intersection(other.buffer(ALONG_TOL))
    return edge


def _within(lines: list[LineString], px: float) -> BaseGeometry:
    if px <= 0:
        return Polygon()
    return _union([ln.buffer(px, cap_style="flat", join_style="mitre", mitre_limit=MITRE) for ln in lines])


def _ring(lines: list[LineString], lo: float, hi: float, inside: BaseGeometry, filled: BaseGeometry):
    """Texture points between signed distances lo and hi (px, negative inside the shape) from the edge."""
    parts = []
    if hi > 0:
        parts.append(_within(lines, hi).difference(_within(lines, max(lo, 0.0))).difference(filled))
    if lo < 0:
        parts.append(_within(lines, -lo).difference(_within(lines, max(-hi, 0.0))).intersection(inside))
    return _union(parts)


def outline(
    kit: Kit,
    id: str,
    view: str | None,
    shape: Shape | str | Iterable[str],
    strokes: Sequence[tuple[float, str]],
    place: str = "outside",
    sides: str = "all",
    along: Shape | None = None,
    through: Iterable[Piece] | None = None,
    material: Material = Material.gloss,
    z: int = 0,
    tags: tuple[str, ...] = (),
) -> list[Element]:
    """Keylines and pinlines on a car-space shape's edge: `strokes` is [(width_px, colour), ...] from the
    edge out (`place="outside"`), from the edge in ("inside"), or from inside to outside across it
    ("centred"). Each is the same width in texture px on every island, measured in the texture from the
    mapped edge, so it doesn't swell where a map is stretched; seams the shape crosses get no stroke.
    `sides="outer"` leaves holes unstroked; `along` (another field, or a line) strokes only the split
    between the two. With `view=None`, `shape` is an island id or a part's name, or several, and the
    strokes follow their outlines in the texture: a trim along a panel's own edge, for parts no view maps.

    One element per stroke, `<id>.stroke<k>`, tagged `stroke_of:<id>`, in drawing order: the farthest
    from the edge first, each reaching a pixel under the next. Add outside strokes before the shape (or
    under it), inside and centred ones after it. A stroke narrower than the lint's `min_outline_px` is
    refused: thinner ones vanish in the sim."""
    if place not in ("outside", "inside", "centred") or sides not in ("all", "outer"):
        raise LiveryError(f"{id}: place is outside, inside or centred, and sides all or outer")
    least = thresholds().min_outline_px
    thin = [w for w, _ in strokes if w < least]
    if thin or not strokes:
        raise LiveryError(f"{id}: strokes of {thin} px; an outline stroke needs at least {least} px")
    mapped: list[tuple[BaseGeometry | None, list[LineString]]] = []  # (where it may draw, the edge there)
    if view is None:
        from livery import body

        inside = body.regions(kit, cast(str | Iterable[str], shape))
        filled = _union([Polygon(g.exterior) for g in polygons(inside)]) if sides == "outer" else inside
        mapped.append((None, _lines(_edge(inside, sides, along))))
    else:
        geom = (
            shape if isinstance(shape, BaseGeometry) else Polygon(cast(Sequence[tuple[float, float]], shape))
        )
        pieces = carspace.pieces(kit, view) if through is None else tuple(through)
        drawn = footprint(kit, view, geom, pieces)
        inside, _ = snapped(kit, view, drawn)  # as `shape` draws it, so the strokes follow its moves
        outer = _union([Polygon(g.exterior) for g in polygons(geom)])
        filled = footprint(kit, view, outer, pieces) if sides == "outer" else inside
        filled = filled.union(inside.difference(drawn)).difference(drawn.difference(inside))
        moved = inside.symmetric_difference(drawn).buffer(1.0)
        edge = _edge(geom, sides, along)
        for pc in pieces:
            seen = edge if pc.car_area is None else edge.intersection(pc.car_area)
            pts = [_densify_line(np.asarray(ln.coords), EDGE_STEP) for ln in _lines(seen)]
            if pts:
                mapped.append((pc.area, [LineString(pc.to_px(p)) for p in pts]))
        if not moved.is_empty:  # where a snap moved the edge, the stroke follows the moved edge
            gone = _union([ln.intersection(moved) for _, lines in mapped for ln in lines])
            mapped = [
                (area, _lines(_union([ln.difference(moved) for ln in lines]))) for area, lines in mapped
            ]
            if not gone.is_empty:
                reach = thresholds().near_tangent_px + MARGINS[0] + OVERSHOOT_PX + 1  # as far as a snap moves
                follow = inside.boundary.intersection(moved).intersection(gone.buffer(reach))
                mapped.append((_union([pc.area for pc in pieces]), _lines(follow)))

    widths = [float(w) for w, _ in strokes]
    total = sum(widths)
    if place == "outside":
        cuts = np.cumsum([0.0, *widths])
    elif place == "inside":
        cuts = -np.cumsum([0.0, *widths])
    else:
        cuts = np.cumsum([-total / 2, *widths])
    spans = [tuple(sorted((float(a), float(b)))) for a, b in itertools.pairwise(cuts)]
    out = []
    for k in sorted(range(len(spans)), key=lambda k: -min(abs(spans[k][0]), abs(spans[k][1]))):
        lo, hi = spans[k]
        if hi <= 0:  # each ring reaches under the one nearer the edge, never past the edge
            hi = min(hi + UNDERLAP_PX, 0.0)
        elif lo >= 0:
            lo -= UNDERLAP_PX
        rings = [_ring(lines, lo, hi, inside, filled) for _, lines in mapped]
        ring = clean(
            _union(
                [
                    r if area is None else r.intersection(area)
                    for r, (area, _) in zip(rings, mapped, strict=True)
                ]
            )
        )
        if ring.is_empty:
            raise LiveryError(f"{id}: the shape's edge lands on no mapped island of {view or 'the texture'}")
        out.append(
            Element(
                id=f"{id}.stroke{k}",
                kind="stroke",
                svg=_path(ring, strokes[k][1]),
                material=Material(material),
                z=z,
                tags=(f"stroke_of:{id}", *tags),
                car={"view": view, "stroke_of": id, "px": (lo, hi)} if view else {"stroke_of": id},
            )
        )
    return out
