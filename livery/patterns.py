"""The 10 m layer's devices: hazard tape, dissolves, dash-run band ends, stripe bundles, chevrons, and
pixels scattered over islands no view maps.

Each takes car-space input and returns one element per view (a stripe bundle: one per stripe, as `stripe`
does), and makes its pieces lint-clean by construction: none so thin that the lint's minimum-feature
opening eats it (`lint.output.opening_px`), and none running alongside a line the near-tangent lint
judges (`geom.edges`, at the library's margins). A designed piece (a hazard region, a band's kept part,
a dash, a chevron) is snapped onto or clear of such a line as `shape` is (`geom.snapped`), each move
listed in the lint report; one no snap clears is an error naming it. A dissolve's cell is random, so
one that can't be kept clear is left out, like the cells that would land on the sim's number or that the
paint doesn't show whole.

Distances and sizes along the car are in car lengths as seen (`geom.as_seen`); sizes in texture px are
turned into car lengths at the map's scale where the pattern sits.
"""

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import cast

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry

from livery import carspace, geom
from livery.carspace import Piece
from livery.errors import LiveryError
from livery.geom import Shape
from livery.kit import Kit
from livery.lint.output import opening_px
from livery.lint.run import thresholds
from livery.model import Element, Material
from livery.primitives import _views, shape

# A dissolve cell the paint shows less of than this (glass, an arch, the view's edge) is left out.
SHOWN = 0.85
SQ3 = math.sqrt(3.0)
# Each cell shape's size over the side of the largest square it holds (a hexagon across its flats).
CELL_SQUARE = {"square": 1.0, "hex": (SQ3 + 1) / 2, "triangle": (2 + SQ3) / SQ3}

Weights = Mapping[str | None, float]  # colour -> weight; a weight on None leaves the cell empty


class PatternError(LiveryError, ValueError):
    pass


def _edges(kit: Kit) -> geom.Edges:
    """The kit's near-tangent lines at the library's margins."""
    return geom.edges(kit, *geom.MARGINS)


def _as_geom(s: Shape) -> BaseGeometry:
    return s if isinstance(s, BaseGeometry) else Polygon(s)


def _scales(kit: Kit, view: str, near: BaseGeometry) -> tuple[float, float]:
    """Texture px per unit of u and of v (w on top) where a car-space shape sits: at its middle, or the
    nearest point to it that lands on a mapped island."""
    if view == "top":
        return carspace.px_per_unit(kit, "top")
    x0, y0, x1, y1 = near.bounds
    gx, gy = np.meshgrid(np.linspace(x0, x1, 9), np.linspace(y0, y1, 9))
    grid = [Point(x, y) for x, y in zip(gx.ravel(), gy.ravel(), strict=True)]
    middle = sorted((p for p in grid if near.intersects(p)), key=near.centroid.distance)
    for q in (near.representative_point(), *middle):
        try:
            return carspace.px_per_unit(kit, view, (q.x, q.y))
        except ValueError:
            continue
    raise PatternError(f"{view}: the pattern lands on no mapped island")


def _scale(kit: Kit, view: str, near: BaseGeometry) -> float:
    """Texture px per car length, along the car, where a car-space shape sits."""
    return _scales(kit, view, near)[0]


def _min_feature(id: str, what: str, px: float, lean: float = 0.0, factor: float = 1.0) -> None:
    """Refuses a piece the lint's min_size opening would eat: `px` across it (along the car for one raked
    `lean` degrees, which must hold the opening's square across its slant); `factor` for a cell shape
    that holds a smaller square than its size."""
    need = opening_px(thresholds()) * (1 + abs(math.tan(math.radians(lean)))) * factor
    if px < need:
        raise PatternError(
            f"{id}: {what} is {px:.1f} px; the lint's minimum feature needs {need:.0f} px here"
        )


def _clean(g: BaseGeometry) -> BaseGeometry:
    """Opened as the lint's min_size check opens it, so what is left keeps its pixels."""
    t = thresholds()
    return geom.clean(g, margin=opening_px(t) - t.min_feature_px)


def _clear(kit: Kit, view: str, id: str, what: str, foot: BaseGeometry, snaps: list) -> BaseGeometry:
    """A designed piece's footprint as the lint will pass it: as it is, or snapped onto or clear of a line
    it runs alongside (its moves added to `snaps`); else an error naming it."""
    if foot.is_empty or not geom.failing(kit, foot):
        return foot
    moved, rows = geom.snapped(kit, view, foot)
    left = geom.failing(kit, moved)
    if left:
        x, y = foot.representative_point().coords[0]
        line = _edges(kit).b.kinds[left[0].line]
        raise PatternError(
            f"{id}: {what} runs alongside a {line} near texture ({x:.0f}, {y:.0f}) and no snap within the "
            "lint's reach clears it; move it"
        )
    snaps += [{"piece": what, **row} for row in rows]
    return moved


def _element(
    id: str, view: str | None, layers: Mapping[str, BaseGeometry], material, z, tags, snaps=()
) -> Element:
    paths = []
    for color, g in layers.items():
        d = carspace.path_d(g)
        if d:
            paths.append(f'<path fill-rule="evenodd" d="{d}" fill="{color}"/>')
    if not paths:
        raise PatternError(f"{id}: draws nothing on {view or 'its islands'}")
    car = {"view": view} if view else {}
    return Element(
        id=id,
        kind="shape",
        svg="".join(paths),
        material=Material(material),
        z=z,
        tags=tags,
        car=car,
        snaps=tuple(snaps),
    )


def _islands(kit: Kit, id: str, names: str | Iterable[str]) -> BaseGeometry:
    """Islands or parts of the kit's body model, as one texture geometry."""
    from livery import body
    from livery.kit import KitError

    try:
        return body.regions(kit, names)
    except KitError as e:
        raise PatternError(f"{id}: {e}") from None


def _near(kit: Kit, view: str, area: BaseGeometry, through) -> tuple[Piece, ...]:
    """The view's pieces (or `through`) whose measured car area meets a car-space area."""
    pieces = carspace.pieces(kit, view) if through is None else tuple(through)
    return tuple(pc for pc in pieces if pc.car_area is None or pc.car_area.intersects(area))


# --- hazard tape ---------------------------------------------------------------------------------------


def hazard(
    kit: Kit,
    id: str,
    region: Shape | str | Iterable[str],
    colors: tuple[str, str],
    period_px: float,
    lean: float = 45.0,
    view: str | None = None,
    duty: float = 0.5,
    phase: float = 0.0,
    through: Iterable[Piece] | None = None,
    material: Material = Material.gloss,
    z: int = 0,
) -> Element:
    """Hazard tape: a region in `colors[0]` with slashes in `colors[1]` every `period_px` texture px along
    the car, `duty` of each period wide, leaning `lean` degrees as seen; `phase` slides them a share of a
    period. With `view`, `region` is a car-space shape and the slashes run on across seams; without, it
    is an island id or a part's name, or several, for parts no view maps, and the slashes lean from the
    texture's vertical. Slash ends too thin for the lint are trimmed off, and the element is the whole
    region, so only the region's own outline meets the lint's edges."""
    _min_feature(id, "a slash", duty * period_px, lean)
    _min_feature(id, "the gap between slashes", (1 - duty) * period_px, lean)
    if view is None:
        ground = _islands(kit, id, cast(str | Iterable[str], region))
        x0, y0, x1, y1 = ground.bounds
        t = math.tan(math.radians(lean)) * (y1 - y0)  # tops lean towards +x
        start = x0 - max(t, 0) - period_px + phase * period_px
        slashes = []
        for x in np.arange(start, x1 + abs(t) + period_px, period_px):
            w = duty * period_px
            slashes.append(Polygon([(x, y1), (x + w, y1), (x + w + t, y0), (x + t, y0)]))
        stripes = _clean(shapely.union_all(slashes).intersection(ground))
        return _element(id, None, {colors[0]: ground, colors[1]: stripes}, material, z, ())
    area = _as_geom(cast(Shape, region))
    pieces = _near(kit, view, area, through)
    period = period_px / _scale(kit, view, area)
    k = geom.rake(kit, lean, view)
    u0, a0, u1, a1 = area.bounds
    reach = abs(k) * max(abs(a0), abs(a1))
    n0 = math.floor((u0 - reach) / period - phase) - 1
    n1 = math.ceil((u1 + reach) / period - phase) + 1
    cuts = [
        Polygon(geom.slant((n + phase) * period, (n + phase + duty) * period, a0, a1, k))
        for n in range(n0, n1)
    ]
    ground, snaps = geom.snapped(kit, view, geom.footprint(kit, view, area, pieces))
    cut = shapely.union_all(cuts).intersection(area)
    stripes = _clean(geom.footprint(kit, view, cut, pieces).intersection(ground))
    tags = ("crosses_seam",)
    return _element(id, view, {colors[0]: ground, colors[1]: stripes}, material, z, tags, snaps)


# --- dissolves -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Tier:
    """One size of a dissolve's cells: `size_px` across (texture px), the chance of a cell at the line,
    and how far from it (car lengths as seen) the chance falls to nothing."""

    size_px: float
    density: float
    reach: float


def _lattice(cell: str, size: float, box: tuple[float, float, float, float], origin) -> list[Polygon]:
    """Cells of one size covering a box (as seen), in rows from the bottom: squares, pointy-top hexagons
    `size` across the flats, or equilateral triangles `size` on a side."""
    u_lo, y_lo, u_hi, y_hi = box
    ox, oy = origin
    out = []
    if cell == "square":
        for j in range(math.floor((y_lo - oy) / size), math.ceil((y_hi - oy) / size) + 1):
            for i in range(math.floor((u_lo - ox) / size), math.ceil((u_hi - ox) / size) + 1):
                x, y = ox + i * size, oy + j * size
                out.append(shapely.box(x, y, x + size, y + size))
    elif cell == "hex":
        r = size / SQ3
        dy = 1.5 * r
        for j in range(math.floor((y_lo - oy) / dy) - 1, math.ceil((y_hi - oy) / dy) + 2):
            off = (j % 2) * size / 2
            for i in range(math.floor((u_lo - ox - off) / size) - 1, math.ceil((u_hi - ox - off) / size) + 2):
                cx, cy = ox + off + i * size, oy + j * dy
                out.append(
                    Polygon(
                        [
                            (
                                cx + r * math.cos(math.radians(30 + 60 * k)),
                                cy + r * math.sin(math.radians(30 + 60 * k)),
                            )
                            for k in range(6)
                        ]
                    )
                )
    elif cell == "triangle":
        h = size * SQ3 / 2
        for j in range(math.floor((y_lo - oy) / h), math.ceil((y_hi - oy) / h) + 1):
            y = oy + j * h
            for i in range(math.floor((u_lo - ox) / (size / 2)) - 2, math.ceil((u_hi - ox) / (size / 2)) + 2):
                x = ox + i * size / 2
                up = (i + j) % 2 == 0
                pts = (
                    [(x, y), (x + size, y), (x + size / 2, y + h)]
                    if up
                    else [(x, y + h), (x + size, y + h), (x + size / 2, y)]
                )
                out.append(Polygon(pts))
    else:
        raise PatternError(f"cells are square, hex or triangle, not {cell!r}")
    return out


def _pick(weights: Weights, x: float) -> str | None:
    total = sum(weights.values())
    if total <= 0:
        return None
    acc = 0.0
    for color, w in weights.items():
        acc += w / total
        if x < acc:
            return color
    return list(weights)[-1]


def dissolve(
    kit: Kit,
    id: str,
    view: str,
    line: Shape | LineString,
    tiers: Sequence[Tier],
    palette: Weights | Callable[[float, float, float], Weights],
    rng: np.random.Generator,
    field: Shape | None = None,
    cell: str = "square",
    gap_px: float = 0.0,
    falloff: float = 2.0,
    origin: tuple[float, float] = (0.0, 0.0),
    avoid: Shape | None = None,
    through: Iterable[Piece] | None = None,
    material: Material = Material.gloss,
    z: int = 0,
    tags: tuple[str, ...] = (),
) -> Element:
    """Cells scattered along a car-space line (or a shape's edge): densest at it and thinning out over each
    tier's reach as (1 - distance / reach) ^ `falloff`. Tiers go largest first, and a smaller cell never
    overlaps a larger one; square tiers sized in multiples share one grid, from `origin` (as seen).

    `field` keeps cells wholly inside a car-space shape (one side of a split, say), `avoid` keeps them out
    of one. `palette` is colour weights, or a function of a cell's centre (u, v or w) and its distance from
    the line that returns them, for a palette by zone; weight on None leaves the cell out. Every cell
    draws its two numbers from `rng` (`p.rng(name)`) whether it is placed or not, so changing a density or
    a palette doesn't reshuffle the rest. `gap_px` shrinks cells to leave the ground between them.

    One element, one path per colour. Cells the paint doesn't show whole, cells over the sim's number,
    and cells that can't be kept clear of the lint's edges are left out."""
    for tier in tiers:
        what = f"a {tier.size_px:g} px {cell} cell less its gap"
        _min_feature(id, what, tier.size_px - gap_px, factor=CELL_SQUARE.get(cell, 1.0))
        if not 0 <= tier.density <= 1 or tier.reach <= 0:
            raise PatternError(f"{id}: a tier's density is 0..1 and its reach above 0, not {tier}")
    k = carspace.aspect(kit, view)
    edge = _as_geom(line)
    edge = edge.boundary if isinstance(edge, Polygon) else edge
    seen_line = geom.as_seen(edge, k)
    shapely.prepare(seen_line)
    reach = max(tier.reach for tier in tiers)
    region = seen_line.buffer(reach)
    seen_field = geom.as_seen(_as_geom(field), k) if field is not None else None
    if seen_field is not None:
        region = region.intersection(seen_field)
        shapely.prepare(seen_field)
    seen_avoid = geom.as_seen(_as_geom(avoid), k) if avoid is not None else None
    car_region = geom.as_car(region, k)
    scale = _scale(kit, view, car_region if not car_region.is_empty else geom.as_car(seen_line, k))
    pieces = _near(kit, view, car_region, through)
    lines = _edges(kit)
    number = shapely.union_all([zone.buffer(lines.reach) for zone in lines.zones])

    taken = Polygon()
    chosen: dict[str, list[BaseGeometry]] = {}
    for tier in sorted(tiers, key=lambda tr: -tr.size_px):
        size, inset = tier.size_px / scale, gap_px / scale / 2
        placed = []
        for c in _lattice(cell, size, region.bounds, origin):
            x, y = rng.random(), rng.random()
            centre = c.centroid
            d = seen_line.distance(centre)
            if d >= tier.reach or x >= tier.density * (1 - d / tier.reach) ** falloff:
                continue
            if seen_field is not None and not seen_field.contains(c):
                continue
            if (seen_avoid is not None and seen_avoid.intersects(c)) or taken.intersects(c):
                continue
            weights = palette(centre.x, centre.y / k, d) if callable(palette) else palette
            color = _pick(weights, y)
            if color is None:
                continue
            drawn = c if inset <= 0 else c.buffer(-inset, join_style="mitre")
            car_cell = geom.as_car(drawn, k)
            foot = geom.footprint(kit, view, car_cell, pieces)
            if foot.is_empty or foot.intersects(number) or not geom.shows(kit, view, car_cell, SHOWN, pieces):
                continue
            foot = lines.settle(foot)  # cells side by side merge, so each keeps out of reach altogether
            if foot is None:
                continue
            chosen.setdefault(color, []).append(foot)
            placed.append(c)
        if placed:
            taken = shapely.union_all([taken, *placed]).buffer(-1e-9)
            shapely.prepare(taken)
    layers = {color: _clean(shapely.union_all(feet)) for color, feet in chosen.items()}
    if not any(not g.is_empty for g in layers.values()):
        raise PatternError(f"{id}: places no cell on {view}; check the line, the field and the densities")
    return _element(id, view, layers, material, z, tags)


# --- band ends, bundles, chevrons ----------------------------------------------------------------------


def dash_run(
    kit: Kit,
    id: str,
    view: str,
    band: Shape,
    u0: float,
    u1: float,
    color: str,
    length: float,
    n: int = 3,
    shrink: float = 0.6,
    gap: float = 0.35,
    lean: float = 0.0,
    taper: str = "length",
    front: bool = True,
    rear: bool = True,
    through: Iterable[Piece] | None = None,
    material: Material = Material.gloss,
    z: int = 0,
) -> Element:
    """A band kept from u0 to u1 with designed ends: beyond each end it opens (`front` towards the nose,
    `rear` towards the tail) a run of `n` raked dashes, `gap` (a share of `length`) apart. With
    `taper="length"` the first is `length` car lengths long and each `shrink` of the one before; with
    "height", each is `length` long and `shrink` as tall as the one before, on the band's middle. Every
    cut leans `lean` degrees as seen. A dash too small for the lint is refused, as is one that can't be
    kept clear of a seam."""
    if not 1 <= n <= 6 or taper not in ("length", "height"):
        raise PatternError(f"{id}: a dash run has 1 to 6 dashes, tapering in length or height")
    area = _as_geom(band)
    pieces = _near(kit, view, area, through)
    sx, sa = _scales(kit, view, area)
    _, a0, _, a1 = area.bounds
    if taper == "length":
        _min_feature(id, f"dash {n}", length * shrink ** (n - 1) * sx, lean)
    else:
        _min_feature(id, "a dash", length * sx, lean)
        _min_feature(id, f"dash {n}", (a1 - a0) * shrink ** (n - 1) * sa)
    k = geom.rake(kit, lean, view)
    kept, snaps = geom.snapped(
        kit,
        view,
        geom.footprint(kit, view, area.intersection(Polygon(geom.slant(u0, u1, a0, a1, k))), pieces),
    )
    snaps = [{"piece": "the band", **row} for row in snaps]
    parts = [kept]
    g = gap * length
    for side, on, sign in (("front", front, -1), ("rear", rear, 1)):
        if not on:
            continue
        edge = u0 if sign < 0 else u1
        for i in range(n):
            d = length * shrink**i if taper == "length" else length
            half = (a1 - a0) / 2 * (1.0 if taper == "length" else shrink**i)
            lo, hi = (a0 + a1) / 2 - half, (a0 + a1) / 2 + half
            near, far = edge + sign * g, edge + sign * (g + d)
            cut = Polygon(geom.slant(min(near, far), max(near, far), lo, hi, k))
            foot = geom.footprint(kit, view, area.intersection(cut), pieces)
            parts.append(_clear(kit, view, id, f"{side} dash {i + 1}", _clean(foot), snaps))
            edge = far
    return _element(id, view, {color: shapely.union_all(parts)}, material, z, ("crosses_seam",), snaps)


def stripe_bundle(
    kit: Kit,
    id: str,
    start: float,
    widths: Sequence[float],
    colors: Sequence[str],
    gap: float | Sequence[float] = 0.0,
    u0: float | Sequence[float] = -0.05,
    u1: float | Sequence[float] = 1.05,
    lean: float = 0.0,
    up: bool = True,
    views: str | Iterable[str] = "sides",
    material: Material = Material.gloss,
    z: int = 0,
) -> list[Element]:
    """Parallel stripes along the car read as one element: stacked from `start` (v on the sides, w on top)
    upwards (or down, `up=False`), each `widths[i]` wide in `colors[i]`, `gap` apart, each running from
    its own u0 to u1 (scalars or one per stripe, for graded lengths) with its ends raked `lean` degrees.
    Stripes and gaps thinner than the lint's minimum feature, where the bundle sits, are refused.
    One element per stripe per view, `<id>.<i>.<view>`, tagged to cross seams."""
    m = len(widths)
    if len(colors) != m:
        raise PatternError(f"{id}: {m} widths and {len(colors)} colours")

    def each(v: float | Sequence[float]) -> list[float]:
        vals = [float(v)] * m if isinstance(v, int | float) else [float(x) for x in v]
        if len(vals) != m:
            raise PatternError(f"{id}: give one value, or one per stripe ({m})")
        return vals

    gaps, starts, ends = each(gap), each(u0), each(u1)
    sign = 1.0 if up else -1.0
    spans, a = [], start
    for i, w in enumerate(widths):
        spans.append(tuple(sorted((a, a + sign * w))))
        a += sign * (w + gaps[i])
    out = []
    for view in _views(kit, views):
        k = geom.rake(kit, lean, view)
        lo_a, hi_a = min(a for sp in spans for a in sp), max(a for sp in spans for a in sp)
        _, sa = _scales(kit, view, shapely.box(min(starts), lo_a, max(ends), hi_a))
        for i, ((lo, hi), w) in enumerate(zip(spans, widths, strict=True)):
            _min_feature(id, f"stripe {i}", w * sa)
            if i < m - 1 and gaps[i] > 0:
                _min_feature(id, f"the gap after stripe {i}", gaps[i] * sa)
            pts = geom.slant(starts[i], ends[i], lo, hi, k)
            out.append(
                shape(kit, f"{id}.{i}.{view}", view, pts, colors[i], material, z, tags=("crosses_seam",))
            )
    return out


def chevrons(
    kit: Kit,
    id: str,
    view: str,
    u0: float,
    n: int,
    step: float,
    half: float,
    thickness: float,
    color: str,
    lean: float = 45.0,
    mid: float = 0.0,
    point: str = "front",
    through: Iterable[Piece] | None = None,
    material: Material = Material.gloss,
    z: int = 0,
) -> Element:
    """A lane of `n` chevrons along the car, `step` apart from u0: each points to the nose (`point="rear"`:
    the tail) from its apex on a = `mid` (v on the sides, w on top), its arms reaching `half` either side
    and leaning `lean` degrees as seen, `thickness` car lengths along the car. A chevron thinner than the
    lint's minimum feature, or one that can't be kept clear of a seam, is refused."""
    pieces = carspace.pieces(kit, view) if through is None else tuple(through)
    sweep = geom.rake(kit, lean, view) * half * (1 if point == "front" else -1)
    th = thickness if point == "front" else -thickness
    parts, snaps = [], []
    for i in range(n):
        u = u0 + i * step
        car = Polygon(
            [
                (u, mid),
                (u + sweep, mid + half),
                (u + sweep + th, mid + half),
                (u + th, mid),
                (u + sweep + th, mid - half),
                (u + sweep, mid - half),
            ]
        )
        if i == 0:
            scale = _scale(kit, view, car)
            _min_feature(id, "a chevron", thickness * scale, lean)
        foot = _clean(geom.footprint(kit, view, car, pieces))
        parts.append(_clear(kit, view, id, f"chevron {i + 1}", foot, snaps))
    return _element(id, view, {color: shapely.union_all(parts)}, material, z, ("crosses_seam",), snaps)


def scatter(
    kit: Kit,
    id: str,
    islands: str | Iterable[str],
    tiers: Sequence[tuple[float, float]],
    palette: Weights | Callable[[float, float], Weights],
    rng: np.random.Generator,
    inset_px: float | None = None,
    material: Material = Material.gloss,
    z: int = 0,
) -> Element:
    """Square pixels scattered evenly over islands no view maps (ids or parts' names), in texture px: for
    each (size_px, density) tier, that share of a size_px grid's squares, each wholly inside its island
    and `inset_px` clear of its outline (by default the lint's reach, so none runs along it). `palette`
    is colour weights, or a function of a square's texture centre (x, y) that returns them. Draws two
    numbers from `rng` per square, placed or not."""
    for size, density in tiers:
        _min_feature(id, f"a {size:g} px square", size)
        if not 0 <= density <= 1:
            raise PatternError(f"{id}: a tier's density is 0..1, not {density}")
    area = _islands(kit, id, islands)
    inner = area.buffer(-(_edges(kit).reach if inset_px is None else inset_px))
    shapely.prepare(inner)
    x0, y0, x1, y1 = area.bounds
    chosen: dict[str, list[BaseGeometry]] = {}
    taken = Polygon()
    for size, density in sorted(tiers, key=lambda tr: -tr[0]):
        placed = []
        for x in np.arange(x0, x1, size):
            for y in np.arange(y0, y1, size):
                a, b = rng.random(), rng.random()
                sq = shapely.box(x, y, x + size, y + size)
                if a >= density or not inner.contains(sq) or taken.intersects(sq):
                    continue
                weights = palette(x + size / 2, y + size / 2) if callable(palette) else palette
                color = _pick(weights, b)
                if color is not None:
                    chosen.setdefault(color, []).append(sq)
                    placed.append(sq)
        if placed:
            taken = shapely.union_all([taken, *placed]).buffer(-1e-9)
    return _element(id, None, {c: shapely.union_all(g) for c, g in chosen.items()}, material, z, ())
