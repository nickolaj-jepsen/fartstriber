"""NERV emergency: a black car whose hexagon display bursts out of the nose and burns down to the tail.

One system, three line weights. The hazard tape (black and orange slashes, 45 degrees) frames the car low
on the sides and runs as a chevron lane over the top. A red pinline traces the tape's top edge on the sides
and both edges of the lane on top; it ends in runs of raked dashes. Above it, and either side of the lane,
a honeycomb covers the body. Over the nose its cells are fused into solid orange; behind a hex-stepped edge
they break into large, then medium, then small cells along the 45 degree rake. They cool from orange through
burnt orange to charcoal and survive only in thinning raked streams, so the rear is black. The tape and
the chevrons cool along the same rake.

Type sits in the system's fields: EMERGENCY in a row of red cells, and under it, beside the number, a black
plate with an orange keyline and small orange labels. EMERGENCY also crosses the roof on a raked plate,
read from the front. Red is the accent: the pinlines, the number blocks, the
splitter lip, the front intakes, the mirrors, the wing endplates and the fender slats. No sponsor marks
(Nickolaj, 18 Oct).
"""

import math
from dataclasses import dataclass, replace

import numpy as np
import shapely
from shapely.geometry import Polygon

from livery import body, carspace, geom, patterns
from livery.geom import as_car, as_seen, clean, footprint, paint, rake, shows, slant
from livery.model import Element
from livery.params import DesignParams, param
from livery.primitives import base, panel, svg, waive
from livery.typeset import Line, measure, type_block

KIT = "mclaren-720s-gt3-evo"
BLACK, CHAR, BURNT, ORANGE = "#121212", "#5c5c5c", "#a33f00", "#f46200"
RED, WHITE = "#d7001e", "#ffffff"
SQ3 = 1.7320508075688772  # sqrt(3)
FONT = "Barlow Condensed"
# Texture px²: cell pieces smaller than this read as shards.
MIN_AREA = 300.0
SEAM = ("crosses_seam",)
# The car's features in the accent, by the kit's part names: the splitter (lip and top), the wing
# endplates, the mirror shells and the strips under them at the door's top front, the front intakes, the
# slats along the front fenders' crests (drawn red over the cells) and the small number panels.
ACCENTS = (
    "splitter",
    "wing_endplates",
    "mirrors",
    "mirror_base_l",
    "mirror_base_r",
    "intake_insert_l",
    "intake_insert_r",
    "fender_slats_l",
    "fender_slats_r",
    "number_panels",
)
NOSE_CORNERS = ("front_bumper_corner_l", "front_bumper_corner_r")  # no view maps them: texture-space tape
TAIL = "rear_face"  # no view maps it either
TITLE = "EMERGENCY"
# The door's secondary type: one black plate raked at the tape's lean, an orange keyline, orange labels,
# sharing one raked start edge and one line gap, the same padding on every side (`type_block`).
PLATE = ("BLOOD TYPE: BLUE", "UMBILICAL CABLE")
DASHES = 3  # a pinline's end run: raked dashes, each `dash_shrink` of the one before


@dataclass(frozen=True)
class Params(DesignParams):
    # The tape: slashes every `period` (in u), `duty` of it orange, leaning `lean` degrees as seen.
    period: float = param(0.052, 0.02, 0.12)
    duty: float = param(0.5, 0.3, 0.7)
    lean: float = param(45.0, 20.0, 60.0)
    phase: float = param(0.9, 0.0, 1.0)  # shifts the slashes along the car, in periods
    top_phase: float = param(0.45, 0.0, 1.0)  # and the chevrons on top
    corner_px: float = param(48.0, 30.0, 80.0)  # the nose corners' tape period, texture px
    # The lane breaks over the roof / engine cover shut line (`roof_cover_seam`).
    lane_gap: float = param(0.605, 0.55, 0.65)
    gap_w: float = param(0.04, 0.0, 0.08)
    # Side band, in v (sill 0, top of the side 1); below 0 it runs off the sill. Between the wheels its
    # top is band_v1, under the number; ahead of the front arch and behind the rear one it is end_v1. The
    # steps happen inside the arches (u), where nothing is painted.
    band_v0: float = param(-0.06, -0.1, 0.2)
    band_v1: float = param(0.23, 0.1, 0.3)
    end_v1: float = param(0.4, 0.23, 0.6)
    front_step: float = param(0.27, 0.2, 0.33)
    rear_step: float = param(0.86, 0.8, 0.94)
    # The red pinline over the tape: black gap and width (in v on the sides, w on top), and where its
    # solid run ends (u) before breaking into raked dashes towards each end of the car: the first
    # `dash_len` (u) long, `dash_gap` of that apart.
    pin_gap: float = param(0.025, 0.01, 0.06)
    pin_w: float = param(0.04, 0.02, 0.08)
    pin_u0: float = param(0.1, 0.0, 0.25)
    pin_a1: float = param(0.49, 0.45, 0.6)  # the front run ends before the side intake's top edge
    pin_b0: float = param(0.745, 0.7, 0.8)  # and the rear run starts behind it
    pin_u1: float = param(0.95, 0.85, 1.05)
    pin_top_u1: float = param(0.58, 0.4, 0.95)  # on top, ahead of the engine cover's shut lines
    dash_len: float = param(0.025, 0.01, 0.05)
    dash_shrink: float = param(0.75, 0.4, 0.9)
    dash_gap: float = param(0.7, 0.3, 1.2)
    # Top: the chevron lane's half-width |w|, and where it opens to the full width over the tail.
    spine_w: float = param(0.12, 0.05, 0.3)
    tail_u0: float = param(0.95, 0.9, 1.0)
    # The honeycomb: cell radius (centre to corner, in u as seen) for the large, medium and small cells,
    # the black gap between cells (u), and the black gap between the field and the pinline (v).
    r_l: float = param(0.04, 0.018, 0.05)
    r_m: float = param(0.024, 0.012, 0.03)
    r_t: float = param(0.02, 0.018, 0.03)  # the EMERGENCY row's cells, sized for its letters
    r_s: float = param(0.012, 0.008, 0.018)
    gap_share: float = param(0.2, 0.1, 0.35)  # black gap between cells, as a share of their radius
    hair: float = param(0.004, 0.003, 0.008)  # the thinnest gap, u as seen (about 6 texture px)
    field_gap: float = param(0.03, 0.01, 0.08)
    # Zones along the rake (c = u - v as seen at the tape's lean): large cells ahead of zone_l, medium up
    # to zone_m, small behind. Cells are all orange ahead of hot_c and fade to `cold` orange at cold_c.
    door_u: float = param(0.29, 0.25, 0.35)  # the door's front on the sides (where the fender ends)
    solid_c: float = param(0.2, -0.2, 0.3)  # the solid orange field ends here
    zone_l: float = param(0.22, 0.1, 0.4)
    zone_m: float = param(0.5, 0.4, 0.75)
    hot_c: float = param(0.34, 0.0, 0.5)
    cold_c: float = param(0.72, 0.5, 1.2)
    cold: float = param(0.0, 0.0, 0.3)
    cover: float = param(0.8, 0.0, 1.0)  # a cell the paint shows less of than this is left out
    holes: float = param(0.05, 0.0, 0.3)  # share of cells left black where the field has cooled
    # As the field cools, cells survive only in raked streams `stream_len` apart (u as seen), each down to
    # `stream_min` of that wide at the cold end.
    stream_len: float = param(0.09, 0.04, 0.15)
    stream_min: float = param(0.7, 0.2, 1.0)
    # The tape and the chevrons cool in steps: orange ahead of tape_hot, burnt orange up to tape_cold.
    tape_hot: float = param(0.4, 0.3, 0.7)
    tape_cold: float = param(0.55, 0.45, 0.9)
    # Lattice offsets (u as seen) per size, sides and top: slide a honeycomb along the car.
    ly0: float = param(0.68, 0.0, 1.0)  # a row of large cells on this v (sides)
    sy0: float = param(0.0, 0.0, 1.0)  # and of small ones
    du_l: float = param(0.02, 0.0, 0.06)
    du_m: float = param(0.03, 0.0, 0.04)
    du_s: float = param(0.015, 0.0, 0.03)
    tdu_l: float = param(0.01, 0.0, 0.06)
    tdu_m: float = param(0.01, 0.0, 0.04)
    tdu_s: float = param(0.0, 0.0, 0.03)
    # The EMERGENCY row: its centre line (v) and where its first cell may start (u).
    title_v: float = param(0.725, 0.6, 0.8)
    title_u: float = param(0.4, 0.3, 0.45)
    letter_size: float = param(0.031, 0.023, 0.05)
    # Red uprights either side of the door number, standing on the pinline.
    red_u0: float = param(0.385, 0.36, 0.42)
    red_u1: float = param(0.49, 0.48, 0.56)
    red_v1: float = param(0.53, 0.5, 0.65)
    hood_margin: float = param(12.0, 0.0, 40.0)  # texture px around the bonnet number kept cell-free
    tail_r: float = param(16.0, 10.0, 30.0)  # the rear face's cells: radius and gap, texture px
    tail_gap: float = param(7.0, 4.0, 12.0)
    mark_size: float = param(0.017, 0.016, 0.025)
    pad: float = param(0.0065, 0.004, 0.012)
    frame_px: float = param(14.0, 14.0, 24.0)  # the plate's orange keyline, texture px
    # The roof's EMERGENCY plate: its letters' em (car lengths), across the roof skin.
    roof_size: float = param(0.0266, 0.018, 0.036)


# --- geometry helpers ----------------------------------------------------------------------------------


def _starts(p: Params, lo: float, hi: float, phase: float | None = None) -> list[float]:
    phase = p.phase if phase is None else phase
    n0 = math.floor(lo / p.period - phase)
    n1 = math.ceil(hi / p.period - phase)
    return [(n + phase) * p.period for n in range(n0, n1 + 1)]


def _band(p: Params) -> Polygon:
    """The side tape's outline in car space: low between the wheels, higher at both ends."""
    v0, lo, hi, a, b = p.band_v0, p.band_v1, p.end_v1, p.front_step, p.rear_step
    return Polygon([(-0.3, v0), (1.3, v0), (1.3, hi), (b, hi), (b, lo), (a, lo), (a, hi), (-0.3, hi)])


def _tone(p: Params, c: float) -> str:
    """The colour of a tape slash or chevron at rake position c: the tape cools with the honeycomb."""
    return ORANGE if c < p.tape_hot else BURNT if c < p.tape_cold else CHAR


def _box(f) -> tuple[float, float, float, float]:
    return f.box


def _scale_at(kit, view: str, region) -> float:
    """Texture px per u where `patterns.hazard` measures a region's period: at its representative point, or
    the grid point nearest its middle that lands on a mapped island (the library's own order)."""
    x0, y0, x1, y1 = region.bounds
    gx, gy = np.meshgrid(np.linspace(x0, x1, 9), np.linspace(y0, y1, 9))
    grid = [shapely.Point(x, y) for x, y in zip(gx.ravel(), gy.ravel(), strict=True)]
    middle = sorted((q for q in grid if region.intersects(q)), key=region.centroid.distance)
    for q in (region.representative_point(), *middle):
        try:
            return carspace.px_per_unit(kit, view, (q.x, q.y))[0]
        except ValueError:
            continue
    raise ValueError(f"{view}: the tape lands on no mapped island")


# --- tape and pinlines ---------------------------------------------------------------------------------


def _side_tape(kit, view: str, p: Params) -> list[Element]:
    """The side tape: hazard tape in three regions, one per tone, split along the slashes' lean in the
    black gap between two slashes, so the tape cools slash by slash."""
    k, band, wid = rake(kit, p.lean, view), _band(p), p.duty * p.period
    _, a0, _, a1 = band.bounds
    tones: list[tuple[str, float]] = []  # (tone, where its region ends at v = 0)
    for s in _starts(p, -0.6, 1.4):
        tone = _tone(p, s + wid / 2)
        cut = s - (p.period - wid) / 2  # the middle of the gap ahead of this slash
        if not tones or tones[-1][0] != tone:
            if tones:
                tones[-1] = (tones[-1][0], cut)
            tones.append((tone, 9.0))
    out, start = [], -9.0
    for i, (tone, end) in enumerate(tones):
        region = band.intersection(Polygon(slant(start, end, a0, a1, k)))
        start = end
        if region.is_empty:
            continue
        # The tape's slashes are `period` long along the car in every region: hazard takes texture px at
        # the scale where its region sits.
        per_px = p.period * _scale_at(kit, view, region)
        out.append(
            patterns.hazard(
                kit,
                f"tape-{i}.{view}",
                region,
                (BLACK, tone),
                per_px,
                lean=p.lean,
                view=view,
                duty=p.duty,
                phase=p.phase,
            )
        )
    return out


def _side_pin(kit, view: str, p: Params) -> list[Element]:
    """The red pinline tracing the tape's top edge, with the door number standing between two uprights
    rising from it. It breaks over the side intake, whose top edge crosses it too shallowly to run across,
    and every run ends in raked dashes."""
    asp = carspace.side_aspect(kit)
    tape = as_seen(_band(p), asp)
    g, w = p.pin_gap * asp, p.pin_w * asp
    ring = tape.buffer(g + w, join_style="mitre").difference(tape.buffer(g, join_style="mitre"))
    ring = as_car(ring.intersection(shapely.box(-0.2, (p.band_v0 + 0.1) * asp, 1.2, 2)), asp)
    # The number sits on black between the uprights: the pinline opens under it.
    v0 = p.band_v1 + p.pin_gap
    du = p.pin_w * asp  # the uprights are as wide as the pinline is high, as seen
    gate = shapely.box(p.red_u0 + du, -1, p.red_u1 - du, 2)
    posts = shapely.box(p.red_u0, v0, p.red_u0 + du, p.red_v1).union(
        shapely.box(p.red_u1 - du, v0, p.red_u1, p.red_v1)
    )
    front = ring.intersection(shapely.box(-1, -1, p.pin_a1 + 0.3, 2)).difference(gate).union(posts)
    back = ring.intersection(shapely.box(p.pin_b0 - 0.3, -1, 2, 2))
    runs = (("pin-a", front, p.pin_u0, p.pin_a1, True), ("pin-b", back, p.pin_b0, p.pin_u1, False))
    return [
        patterns.dash_run(
            kit,
            f"{name}.{view}",
            view,
            area,
            u0,
            u1,
            RED,
            p.dash_len,
            n=DASHES,
            shrink=p.dash_shrink,
            gap=p.dash_gap,
            lean=p.lean,
            front=fr,
            z=1,
        )
        for name, area, u0, u1, fr in runs
    ]


def _top_pins(kit, p: Params) -> list[Element]:
    """Red pinlines along both edges of the lane, ending in raked dashes that lean back towards the car's
    sides like the chevrons."""
    a, b = p.spine_w + p.pin_gap, p.spine_w + p.pin_gap + p.pin_w
    out = []
    for side, sgn in (("l", -1), ("r", 1)):
        strip = shapely.box(-0.3, min(sgn * a, sgn * b), 1.3, max(sgn * a, sgn * b))
        out.append(
            patterns.dash_run(
                kit,
                f"pin-top-{side}",
                "top",
                strip,
                0.02,
                p.pin_top_u1,
                RED,
                p.dash_len,
                n=DASHES,
                shrink=p.dash_shrink,
                gap=p.dash_gap,
                lean=sgn * p.lean,
                z=1,
            )
        )
    return out


def _chevron(c: float, wid: float, sweep: float, w: float) -> Polygon:
    back = sweep * w
    return Polygon(
        [(c, 0), (c + back, w), (c + back + wid, w), (c + wid, 0), (c + back + wid, -w), (c + back, -w)]
    )


def _spine(kit, p: Params) -> list[Element]:
    """The chevron lane, opening out to the full width over the tail: one element per chevron, so each
    crosses its seams whole. Chevrons cool with the tape."""
    sweep, wid = rake(kit, p.lean, "top"), p.duty * p.period
    tail = shapely.box(p.tail_u0, -2, 2, 2)
    gap = shapely.box(p.lane_gap - p.gap_w / 2, -2, p.lane_gap + p.gap_w / 2, 2)
    splitter = body.region(kit, "splitter")
    out = []
    for i, c in enumerate(_starts(p, -0.1 - sweep * 1.2, 1.2, p.top_phase)):
        car = _chevron(c, wid, sweep, p.spine_w).union(_chevron(c, wid, sweep, 1.2).intersection(tail))
        foot = clean(footprint(kit, "top", car.difference(gap)), 40.0)
        # One that shows only under the red splitter would stop at the bonnet's seam with it: left out.
        if foot.difference(splitter).area < MIN_AREA:
            continue
        # On top the rake position continues the sides' at their top edge (v = 1).
        c_top = c + sweep * p.spine_w / 2 - rake(kit, p.lean)
        out.append(paint(f"spine-{i:02d}", foot, _tone(p, c_top), tags=SEAM))
    return out


# --- the honeycomb -------------------------------------------------------------------------------------


def _hexagon(cx: float, cy: float, r: float) -> Polygon:
    """A pointy-top hexagon, as seen."""
    return Polygon(
        [
            (cx + r * math.cos(math.radians(30 + 60 * k)), cy + r * math.sin(math.radians(30 + 60 * k)))
            for k in range(6)
        ]
    )


def _lattice(r: float, y0: float, du: float, box: tuple[float, float, float, float]):
    """Cell centres (u, y, row) of a honeycomb as seen, with a row on y0."""
    u_lo, y_lo, u_hi, y_hi = box
    dx, dy = SQ3 * r, 1.5 * r
    out = []
    for row in range(math.floor((y_lo - y0) / dy) - 1, math.ceil((y_hi - y0) / dy) + 2):
        y = y0 + row * dy
        off = (row % 2) * dx / 2
        for col in range(math.floor((u_lo - off - du) / dx) - 1, math.ceil((u_hi - off - du) / dx) + 2):
            out.append((col * dx + off + du, y, row))
    return out


def _stream(p: Params, c: float) -> bool:
    """Whether rake position c is inside one of the raked streams the cooling cells survive in."""
    t = float(np.clip((c - p.hot_c) / (p.cold_c - p.hot_c), 0.0, 1.0))
    return (c / p.stream_len) % 1.0 < 1.0 - t * (1.0 - p.stream_min)


def _heat(p: Params, c: float) -> float:
    """Share of orange cells at rake position c."""
    t = (c - p.hot_c) / (p.cold_c - p.hot_c)
    return float(np.clip(1 - t, p.cold, 1.0))


def _gap(p: Params, r: float) -> float:
    """The black gap around a cell of radius r: a share of it, never thinner than a hairline."""
    return max(p.gap_share * r, p.hair)


def _honeycomb(
    p: Params, rng, allowed, type_area, box, y0s, dus, slope: float, c_shift: float, fits, core_fit_u=9.0
):
    """Cells of the three sizes in the allowed area (as seen), by colour. A cell touching the type is left
    out; one overlapping a larger size's cells is cut back to them, so the sizes interlock. Each size also
    fills the gaps the larger ones left ahead of its zone, including where they didn't `fit` the paint."""
    sizes = ((p.r_l, -9.0, p.zone_l), (p.r_m, p.zone_l, p.zone_m), (p.r_s, p.zone_m, 9.0))
    keep_off = type_area.buffer(_gap(p, p.r_m) * 0.9)
    shapely.prepare(keep_off)
    # Ahead of solid_c every large cell is orange, whatever the paint does to it: the hot core of the display.
    solid = []
    rl = p.r_l - _gap(p, p.r_l) / SQ3
    for u, y, _ in _lattice(p.r_l, y0s[0], dus[0], box):
        if u - slope * y + c_shift < p.solid_c:
            part = _hexagon(u, y, rl).intersection(allowed).difference(keep_off)
            # On the door, where the window line cuts across, none cut part-way; the fenders keep them all.
            if not part.is_empty and (u < core_fit_u or fits(part)):
                solid.append(part)
    out = {"solid": solid, ORANGE: [], BURNT: [], CHAR: []}
    g = _gap(p, p.r_l)
    larger = shapely.union_all(solid).buffer(g, join_style="mitre") if solid else Polygon()
    shapely.prepare(larger)
    for (r, _, c1), y0, du in zip(sizes, y0s, dus, strict=True):
        g = _gap(p, r)
        rc = r - g / SQ3
        placed = []
        for u, y, _ in _lattice(r, y0, du, box):
            x = rng.random()  # drawn for every cell, so a rule change elsewhere never reshuffles
            c = u - slope * y + c_shift
            if c >= c1 or not _stream(p, c):
                continue
            cell = _hexagon(u, y, rc)
            if keep_off.intersects(cell):
                continue
            part = cell.intersection(allowed)
            if larger.intersects(part):
                part = part.difference(larger)
                if part.area < 0.75 * cell.area:
                    continue
            elif part.area < 0.6 * cell.area:
                continue
            if not fits(part):
                continue
            hot = _heat(p, c)
            burnt = 1.2 * hot * (1 - hot)
            if x < hot:
                out[ORANGE].append(part)
            elif x < hot + burnt:
                out[BURNT].append(part)
            elif x < 1 - p.holes * (1 - hot):
                out[CHAR].append(part)
            else:
                continue
            placed.append(part)
        if placed:
            larger = shapely.union_all([larger, *placed]).buffer(g, join_style="mitre")
            shapely.prepare(larger)
    return out


def _fits(kit, p: Params, view: str, k: float):
    """Whether the paint shows a cell (as seen) whole: one cut by glass, an arch or the edge of the view is
    left out, so cells clearly avoid those edges. Seams between painted islands don't cut it."""

    def fits(poly) -> bool:
        return shows(kit, view, as_car(poly, k), p.cover)

    return fits


def _whole(feet: list) -> list:
    """The cells whose footprint the cleaning keeps on every island it lands on: a cell whose sliver on one
    island would be opened away is left out whole, so none stops at a seam."""
    out = []
    for foot in feet:
        kept = clean(foot, MIN_AREA)
        if kept.area >= 0.9 * foot.area:
            out.append(kept)
    return out


def _prune(feet: list, lines) -> list:
    """Cells side by side can make a run along an outline that none makes alone: while the union runs
    alongside a line for the lint's run, the cells touching that stretch are left out."""
    while feet:
        union = shapely.union_all(feet)
        bad = [r for r in lines.runs(union) if r.length >= lines.run]
        if not bad:
            return feet
        near = shapely.union_all(
            [lines.b.lines[r.line].buffer(lines.reach + 1).intersection(_span(lines, r)) for r in bad]
        )
        keep = [f for f in feet if not (lines.touches(f) and f.intersects(near))]
        if len(keep) == len(feet):
            return keep
        feet = keep
    return feet


def _span(lines, r):
    """The texture near one run's stretch of line."""
    a = lines.b.at(r.line, r.s0)
    b = lines.b.at(r.line, r.s1)
    mid = lines.b.at(r.line, (r.s0 + r.s1) / 2)
    return shapely.MultiPoint([a, b, mid]).convex_hull.buffer(lines.reach + 4)


def _emit(kit, p: Params, view: str, cells: dict, k: float, lines, name: str, hole) -> list[Element]:
    """One element per colour. The core keeps its cells whole (snapped where an edge would run along an
    outline); other cells running alongside an outline are left out, not pulled back, which would cut only
    one island's copy and break it at the seam."""
    out = []
    for key, polys in cells.items():
        color, field = (ORANGE, True) if key == "solid" else (key, False)
        feet = []
        for poly in polys:
            foot = footprint(kit, view, as_car(poly, k))
            if foot.is_empty or foot.intersects(hole):
                continue
            if field or lines.clear(foot):
                feet.append(foot)
        if not field:
            feet = _prune(_whole(feet), lines)
        if not feet:
            continue
        g = clean(shapely.union_all(feet), MIN_AREA)
        snaps: tuple = ()
        if field:
            g, snaps = geom.snapped(kit, view, g)
        el = paint(f"{name}-{'solid' if field else color[1:]}.{view}", g, color, z=1, tags=SEAM)
        el = replace(el, snaps=snaps)
        if field and view == "side_r":
            # Its every broken seam pair is between the right front fender (or the door) and the slats.
            el = waive(el, "seam", "the core breaks only at the fender-crest slats, painted red over it")
        elif field and view == "top":
            # The sides' core passes its seams; the top's meets the side islands (fender, door shoulder,
            # mirror, splitter) on a lattice of its own, so its whole cells can't continue there.
            el = waive(
                el,
                "seam",
                "the top's whole nose cells are laid out on the top view's own honeycomb, so where the fenders, "
                "door shoulders, mirrors and splitter hand over to the side views (or are painted red) the "
                "cells don't continue across",
            )
        out.append(el)
    return out


# --- type ----------------------------------------------------------------------------------------------


def _plate(kit, view: str, p: Params) -> tuple[list[Element], Polygon]:
    """The door plate: BLOOD TYPE: BLUE over UMBILICAL CABLE on a black plate padded equally, raked at the
    tape's lean, an orange keyline outside it. It stands a pad behind the number's right upright, centred
    between the pinline and the EMERGENCY row. Returns its elements and its car-space outline with the
    keyline."""
    asp = carspace.side_aspect(kit)
    slope = math.tan(math.radians(p.lean))
    lines = [Line(t, p.mark_size, FONT, 700) for t in PLATE]
    ms = [measure(t, FONT, p.mark_size, 700) for t in PLATE]
    cap = ms[0].cap
    height = 2 * cap + p.pad + 2 * p.pad  # as seen: two cap heights, one line gap, a pad above and below
    width = max(m.width for m in ms) + 2 * p.pad * math.hypot(1.0, slope)
    pin_top = (p.band_v1 + p.pin_gap + p.pin_w) * asp
    title_bottom = p.title_v * asp - (p.r_t - _gap(p, p.r_t) / SQ3)
    yc = pin_top + (title_bottom - pin_top) / 2
    frame_u = p.frame_px / carspace.px_per_unit(kit, view, (p.red_u1, yc / asp))[0]
    ua = p.red_u1 + p.pad + frame_u  # the plate's bottom nose corner
    uc = ua + width / 2 + slope * height / 2
    els = type_block(
        kit,
        f"plate.{view}",
        view,
        lines,
        (uc, yc / asp),
        ORANGE,
        gap=p.pad,
        lean=p.lean,
        shear=0.0,
        anchor=(0.5, 0.5),
        plate=BLACK,
        pad=p.pad,
        z=2,
    )
    text = next(e for e in els if e.kind == "text")
    box = Polygon(text.car["polygon"])
    # The keyline goes outside the plate, so the padding inside stays equal; it is drawn before the plate.
    frame = geom.outline(kit, f"plate-frame.{view}", view, box, [(p.frame_px, ORANGE)], place="outside", z=2)
    grown = as_car(as_seen(box, asp).buffer(frame_u, join_style="mitre"), asp)
    return [*frame, *els], grown


def _title(kit, view: str, p: Params, cells) -> list[Element]:
    """EMERGENCY, one white letter centred in each red cell, reading forwards on both sides."""
    order = cells if view == "side_l" else cells[::-1]
    asp = carspace.side_aspect(kit)
    out = []
    for i, (ch, (u, y, _)) in enumerate(zip(TITLE, order, strict=True)):
        out += type_block(
            kit,
            f"title-{i}.{view}",
            view,
            [Line(ch, p.letter_size, FONT, 800)],
            (u, y / asp),
            WHITE,
            anchor=(0.5, 0.5),
            z=2,
        )
    return out


def _roof(kit, p: Params) -> list[Element]:
    """EMERGENCY on a red plate across the roof skin, reading from the front, its ends raked at 45 degrees
    like the side plates. The plate's long edges are the skin's own; it is as long as its text plus the same
    padding at the ends as above and below the letters."""
    roof = kit.part("roof")
    cut = roof.cut.islands if roof.cut else ()
    skin_id = next(i for i in roof.islands if i not in cut)  # the roof skin, whole
    skin = carspace.island_shapes(kit)[skin_id]
    piece = next(pc for pc in carspace.pieces(kit, "top") if pc.island == skin_id)
    x0, y0, x1, y1 = skin.bounds
    xu = carspace.px_per_unit(kit, "top")[0]
    yc = (y0 + y1) / 2
    across = skin.intersection(shapely.box(x0 - 1, yc - 0.5, x1 + 1, yc + 0.5)).bounds
    depth = (across[2] - across[0]) / xu  # u along the car at the middle
    car, _ = carspace.invert(piece, ((across[0] + across[2]) / 2, yc), (0.39, 0.0))
    uc, wc = float(car[0]), float(car[1])
    m = measure(TITLE, FONT, p.roof_size, 800)
    pad = (depth - m.cap) / 2
    k = carspace.aspect(kit, "top")
    half = (m.width / 2 + pad * math.sqrt(2)) / k  # w, the plate's half-length at its middle
    lo, hi = uc - depth, uc + depth
    # Raked 45 degrees as seen: the ends move across the car as fast as along it.
    plate = Polygon(
        [
            (lo, wc - half - (lo - uc) / k),
            (hi, wc - half - (hi - uc) / k),
            (hi, wc + half - (hi - uc) / k),
            (lo, wc + half - (lo - uc) / k),
        ]
    )
    band = clean(footprint(kit, "top", plate).intersection(skin), MIN_AREA)
    text = type_block(
        kit,
        "roof-type",
        "top",
        [Line(TITLE, p.roof_size, FONT, 800)],
        (uc, wc),
        WHITE,
        anchor=(0.5, 0.5),
        z=4,
    )
    return [paint("roof-bar", band, RED, z=3), *text]


# --- fields --------------------------------------------------------------------------------------------


def _side_field(kit, view: str, p: Params, lines) -> list[Element]:
    asp = carspace.side_aspect(kit)
    slope = math.tan(math.radians(p.lean))
    s = view[-1]
    tape = as_seen(_band(p), asp)
    low = tape.buffer((p.pin_gap + p.pin_w + p.field_gap) * asp, join_style="mitre")
    fg = p.field_gap * asp

    def seen_box(b):
        return shapely.box(b[0], b[1] * asp, b[2], b[3] * asp)

    block = shapely.box(p.red_u0, 0, p.red_u1, p.red_v1 * asp).buffer(fg, join_style="mitre")
    light = seen_box(_box(kit.feature(f"door_light_panel_{s}")))
    mirror = seen_box(_box(kit.feature(f"mirror_base_{s}"))).union(
        seen_box(_box(kit.feature(f"mirror_shell_{s}")))
    )
    plate_els, plate = _plate(kit, view, p)
    plate = as_seen(plate, asp)
    allowed = shapely.box(-0.1, -1, 1.2, 1.3 * asp).difference(low).difference(block)
    for zone in (light, plate, mirror):
        allowed = allowed.difference(zone.buffer(fg, join_style="mitre"))
    box = (-0.1, p.band_v1 * asp, 1.15, 1.25 * asp)
    y_title = p.title_v * asp
    # The type's cells come first, on the medium honeycomb.
    cells_m = _lattice(p.r_t, y_title, p.du_m, box)
    title = [c for c in cells_m if c[2] == 0 and c[0] >= p.title_u][: len(TITLE)]
    rc = p.r_t - _gap(p, p.r_t) / SQ3
    tcells = [_hexagon(u, y, rc) for u, y, _ in title]
    # Cells touching the type, the plate, the number or the light panel are left out whole; along the tape
    # they are cut straight, standing on its pinline.
    taken = shapely.union_all([*tcells, block, light, plate, mirror])
    cells = _honeycomb(
        p,
        p.rng("hex"),
        allowed,
        taken,
        box,
        (p.ly0 * asp, y_title, p.sy0 * asp),
        (p.du_l, p.du_m, p.du_s),
        slope,
        0.0,
        _fits(kit, p, view, asp),
        p.door_u,
    )
    out = _emit(kit, p, view, cells, asp, lines, "hex", Polygon())
    title_foot = clean(footprint(kit, view, as_car(shapely.union_all(tcells), asp)), MIN_AREA)
    out.append(paint(f"title.{view}", title_foot, RED, z=1, tags=SEAM))
    out += _title(kit, view, p, title)
    out += plate_els
    return out


def _top_field(kit, p: Params, lines) -> list[Element]:
    k = carspace.aspect(kit, "top")
    asp = carspace.side_aspect(kit)
    slope = math.tan(math.radians(p.lean))
    lane_w = (p.spine_w + p.pin_gap + p.pin_w + p.field_gap) * k
    lane = shapely.box(-1, -lane_w, 2, lane_w)
    hood = as_seen(shapely.box(0.04, 0.18, 0.19, 0.46), k)  # a black field around the bonnet number
    allowed = shapely.box(-0.1, -1.3 * k, p.tail_u0 - 0.01, 1.3 * k).difference(lane).difference(hood)
    box = (-0.1, -1.3 * k, 1.0, 1.3 * k)
    # On top the rake position continues the sides' at their top edge (v = 1), and runs along u only.
    cells = _honeycomb(
        p,
        p.rng("hex-top"),
        allowed,
        Polygon(),
        box,
        (0.0, 0.0, 0.0),
        (p.tdu_l, p.tdu_m, p.tdu_s),
        0.0,
        -slope * asp,
        _fits(kit, p, "top", k),
    )
    # The bonnet number's box, where the sim draws it: the core's cells touching it are left out.
    hole = body.footprint(kit, kit.feature("number_top")).buffer(p.hood_margin, join_style="mitre")
    return _emit(kit, p, "top", cells, k, lines, "hex", hole)


def _tail_hex(kit, p: Params) -> Element:
    """The coldest end of the honeycomb on the rear face, which no view maps: small charcoal cells on black,
    as a texture-space pattern (one lattice period: two columns by one row pair)."""
    r = p.tail_r
    rc = r - p.tail_gap / SQ3
    w, h = SQ3 * r, 3 * r
    paths = []
    for cx, cy in ((0, 0), (w, 0), (0, h), (w, h), (w / 2, h / 2)):
        pts = [
            (cx + rc * math.cos(math.radians(30 + 60 * k)), cy + rc * math.sin(math.radians(30 + 60 * k)))
            for k in range(6)
        ]
        paths.append("M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in pts) + " Z")
    d = carspace.path_d(body.region(kit, TAIL))
    # Its tile isn't square (a honeycomb's period is sqrt(3) to 3), which pattern_fill's tiles must be.
    raw = (
        f'<pattern id="pat-tail-hex" patternUnits="userSpaceOnUse" width="{w:.3f}" height="{h:.3f}">'
        f'<rect width="{w:.3f}" height="{h:.3f}" fill="{BLACK}"/><path d="{" ".join(paths)}" fill="{CHAR}"/>'
        f'</pattern><path fill-rule="evenodd" d="{d}" fill="url(#pat-tail-hex)"/>'
    )
    return svg(raw, "tail-hex", z=1)


def build(kit, p: Params):
    lines = geom.edges(kit, *geom.MARGINS)
    els = []
    for view in ("side_l", "side_r"):
        els += _side_tape(kit, view, p)
        els += _side_pin(kit, view, p)
        els += _side_field(kit, view, p, lines)
    els += [
        patterns.hazard(kit, f"tape-{name}", name, (BLACK, ORANGE), p.corner_px, lean=p.lean)
        for name in NOSE_CORNERS
    ]
    els += _spine(kit, p)
    els += _top_pins(kit, p)
    els += _top_field(kit, p, lines)
    els.append(_tail_hex(kit, p))
    els += _roof(kit, p)
    els += [panel(kit, f"accent-{name}", name, RED, z=2) for name in ACCENTS]
    return base(BLACK).add(*els)
