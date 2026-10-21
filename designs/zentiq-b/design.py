"""Zentiq b: the block mosaic.

Paul Miller's dissolving blocks, transplanted: light grey at the nose, acid green through the door, black
from the haunch back and along the whole lower body. Every field is a mosaic of horizontal rectangles in
three sizes and in tones of its own colour, laid in overlapping layers; where two zones meet, their blocks
interleave, densest in the middle of the transition and thinning both ways. Every edge is horizontal or
vertical in car space: no diagonal, no curve, no clean line between two colours.
"""

from dataclasses import dataclass

import shapely
from shapely.geometry import Polygon

from livery import body, carspace, geom, typeset
from livery.params import DesignParams, param
from livery.primitives import base, clip_out, mirror_sides, panel, sponsor, sponsor_group, svg

KIT = "mclaren-720s-gt3-evo"

# Three hues in two tones each: light grey, acid green (Zentiq's), black.
GREY, GREY_T = "#d5d7d3", "#a3a6a2"
GREEN, GREEN_T = "#b6ff00", "#78b000"
BLACK, CHAR = "#111111", "#393a39"
ZONES = ((GREY, GREY_T), (GREEN, GREEN_T), (BLACK, CHAR))
SIDES = ("side_l", "side_r")

# Block tiers, as seen (car lengths): (height, how many candidates, density at a transition, density in a
# zone's body). Lengths run 2:1 to 6:1.
TIERS = (
    ("big", 0.075, 70, 0.85, 0.25),
    ("med", 0.034, 260, 0.9, 0.18),
    ("small", 0.015, 700, 0.75, 0.06),
)
GRID = 0.004  # every block edge on this grid, as seen, so no sliver between two blocks

# Parts painted whole.
LOWER = (
    "sill_l",
    "sill_r",
    "sill_strip_l",
    "sill_strip_r",
    "crash_opening",
    "grille_upright_l",
    "grille_upright_r",
    "grille_side_l",
    "grille_side_r",
    "intake_edge_l",
    "intake_edge_r",
    "rear_valance",
    "rear_deck_lip",
    "swan_neck_l",
    "swan_neck_r",
    "number_panels",
)
# Parts no view maps whole, painted as their ground cut with texture-rectangle blocks.
UNMAPPED = ("rear_face", "wing_endplates", "front_bumper_corner_l", "front_bumper_corner_r")
# Everything painted on its own, which the mosaic leaves.
WHOLE = (*LOWER, "splitter_top", "splitter_lip", "mirrors", "bonnet_vent", *UNMAPPED)


@dataclass(frozen=True)
class Params(DesignParams):
    seed: int = param(3, 0, 2**31 - 1)
    # Side transitions in u: grey into green, green into black; their half-width (reach) in u.
    t1: float = param(0.33, 0.2, 0.45)
    t2: float = param(0.68, 0.6, 0.85)
    reach: float = param(0.1, 0.03, 0.2)
    # The lower black band's top, and its transition's half-height (v).
    low_v: float = param(0.27, 0.1, 0.4)
    low_reach: float = param(0.09, 0.02, 0.2)
    # Top transitions in u (bonnet to roof, roof to engine cover), on the middle of the car.
    t1_top: float = param(0.24, 0.15, 0.35)
    t2_top: float = param(0.64, 0.55, 0.75)
    flank_w: float = param(0.5, 0.3, 0.8)  # outside |w| this the top follows the sides' transitions
    stray: float = param(0.25, 0.0, 0.6)  # share of zone-body small blocks in a foreign colour
    tone: float = param(0.6, 0.0, 1.0)  # share of zone-body blocks in the zone's tone
    black_tone: float = param(1.8, 1.0, 3.0)  # the black zone's body carries this many more blocks
    # Headline on the door.
    head_u: float = param(0.6, 0.5, 0.7)
    head_v: float = param(0.64, 0.5, 0.8)
    head_h: float = param(0.058, 0.03, 0.08)  # car lengths
    # The sill rows' centre line (v), under the side number's zone.
    sill_v: float = param(0.24, 0.2, 0.3)
    sill_h: float = param(0.05, 0.03, 0.06)  # the sill rows' largest height, car lengths
    # The front row's length in u (the rear's is 135/115 of it) and its start, behind the front arch.
    sill_len: float = param(0.09, 0.08, 0.12)
    sill_u: float = param(0.435, 0.4, 0.46)


def _snap(x: float) -> float:
    return round(x / GRID) * GRID


class Mosaic:
    """Rectangles in car space on one view, painted in order; `paint` gives what shows of each colour."""

    def __init__(self, k: float):
        self.k = k
        self.rects: list[tuple[Polygon, str]] = []

    def add(self, u0, a0, u1, a1, color):
        if u1 > u0 and a1 > a0:
            self.rects.append((shapely.box(u0, a0, u1, a1), color))

    def add_seen(self, u0, y0, u1, y1, color):
        """A block given as seen (y = a * k), snapped to the grid."""
        u0, u1, y0, y1 = _snap(u0), _snap(u1), _snap(y0), _snap(y1)
        self.add(u0, y0 / self.k, u1, y1 / self.k, color)

    def layers(self) -> dict[str, list[Polygon]]:
        covered = Polygon()
        out: dict[str, list] = {}
        for box, color in reversed(self.rects):
            vis = box.difference(covered)
            if not vis.is_empty:
                out.setdefault(color, []).append(vis)
            covered = covered.union(box)
        return {c: [shapely.union_all(g)] for c, g in out.items()}


def _texture_blocks(kit, part: str, ground: str, palette: dict[str, float], rng, n: int):
    """A part no view maps, in its ground colour cut with blocks as texture rectangles in three sizes,
    2:1 to 6:1, kept off the sim's number."""
    region = body.region(kit, part)
    x0, y0, x1, y1 = region.bounds
    edges = geom.edges(kit, *geom.MARGINS)  # the lint's lines at the library's own margins
    zones = shapely.union_all([z.buffer(16) for z in edges.zones])
    colors, weights = list(palette), list(palette.values())
    blocks: list[tuple[str, object]] = []
    for _ in range(n):
        cx, cy, pick, size, ratio = (
            rng.uniform(x0, x1),
            rng.uniform(y0, y1),
            rng.random(),
            rng.random(),
            rng.random(),
        )
        hh = (14.0, 28.0, 48.0)[int(size * 3)]
        ll = hh * (2 + 4 * ratio)
        acc, color = 0.0, colors[-1]
        for c, w in zip(colors, weights, strict=True):
            acc += w / sum(weights)
            if pick < acc:
                color = c
                break
        box = shapely.box(cx - ll / 2, cy - hh / 2, cx + ll / 2, cy + hh / 2)
        settled = edges.settle(box.intersection(region).difference(zones))  # clear of the lint's lines
        if settled is not None:
            blocks.append((color, settled))
    covered = Polygon()
    shown: dict[str, list] = {}
    for color, b in reversed(blocks):
        vis = b.difference(covered)
        if not vis.is_empty:
            shown.setdefault(color, []).append(vis)
        covered = covered.union(b)
    raw = ""
    for color, g in shown.items():
        d = carspace.path_d(geom.clean(shapely.union_all(g)))
        if d:
            raw += f'<path fill-rule="evenodd" d="{d}" fill="{color}"/>'
    return [panel(kit, f"ground-{part}", part, ground, z=2), svg(raw, f"blocks-{part}", z=3)]


def _mosaic_fill(kit, id: str, view: str, layers: dict[str, list[Polygon]], z: int):
    """The mosaic's layers on one view as one element, cut out of every part painted whole: their maps
    stop short of their outlines (sill strips, grille uprights, splitter top), so the mosaic would end
    inside them, just short of the line."""
    el = geom.fill(kit, id, view, layers, z=z, tags=("crosses_seam",))
    return clip_out(kit, WHOLE, el)


def _ramp(x: float, centre: float, reach: float) -> float:
    """0 well before a transition, 1 well past it, linear across it."""
    return min(1.0, max(0.0, (x - (centre - reach)) / (2 * reach)))


def build(kit, p: Params):
    els = []
    rng = p.rng("blocks")

    def zone_side(u, v, r1, r2, r3):
        """The zone a block at (u, v) takes: drawn across each transition, and whether it sits in one."""
        z = 0 if r1 >= _ramp(u, p.t1, p.reach) else (1 if r2 >= _ramp(u, p.t2, p.reach) else 2)
        if r3 < 1.0 - _ramp(v, p.low_v, p.low_reach):
            z = 2
        return z

    def near_side(u, v):
        d = min(abs(u - p.t1), abs(u - p.t2)) / p.reach
        if v > p.low_v - p.low_reach * 2:
            d = min(d, abs(v - p.low_v) / p.low_reach)
        return max(0.0, 1.0 - d)

    def t_top(w):
        return (p.t1_top, p.t2_top) if abs(w) < p.flank_w else (p.t1, p.t2)

    def zone_top(u, w, r1, r2, r3):
        a, b = t_top(w)
        return 0 if r1 >= _ramp(u, a, p.reach) else (1 if r2 >= _ramp(u, b, p.reach) else 2)

    def near_top(u, w):
        a, b = t_top(w)
        return max(0.0, 1.0 - min(abs(u - a), abs(u - b)) / p.reach)

    def mosaic(view, zone, near, a_lo, a_hi):
        k = carspace.aspect(kit, view)
        m = Mosaic(k)
        y_lo, y_hi = a_lo * k, a_hi * k
        # The ground: rows of the smallest block's height, each zone's boundary stepping row by row, out past
        # the wing's trailing edge so it ends at the outlines, not inside an island.
        row = TIERS[2][1] * 2
        y = y_lo
        while y < y_hi:
            a = (y + row / 2) / k
            for u0 in [x * 0.012 - 0.06 for x in range(110)]:
                z = zone(u0 + 0.006, a, 0.5, 0.5, 0.5)
                m.add_seen(u0, y, u0 + 0.012, y + row, ZONES[z][0])
            y += row
        # Blocks, largest first, smallest on top.
        for name, h, n, dens_t, dens_z in TIERS:
            for _ in range(n * (2 if view == "top" else 1)):
                u, y = rng.uniform(-0.06, 1.08), rng.uniform(y_lo, y_hi)
                hh = h * rng.uniform(0.75, 1.25)
                ll = hh * rng.uniform(2.0, 6.0)
                r = rng.random(5)
                a = y / k
                t = near(u, a)
                home = zone(u, a, 0.5, 0.5, 0.5)
                body_dens = dens_z * (p.black_tone if home == 2 else 1.0)
                dens = body_dens + (dens_t - body_dens) * t**1.5
                if r[0] >= dens:
                    continue
                z = zone(u, a, r[1], r[2], r[3])
                if t > 0.05:
                    color = ZONES[z][0] if r[4] < 0.6 else ZONES[z][1]
                else:
                    if name == "small" and r[4] < p.stray:
                        color = ZONES[(home + 1 + int(r[1] * 2)) % 3][0]  # a dead pixel
                    else:
                        color = ZONES[home][1] if r[4] < p.tone else ZONES[home][0]
                m.add_seen(u - ll / 2, y - hh / 2, u + ll / 2, y + hh / 2, color)
        return m

    # --- sides ---------------------------------------------------------------------------------------------
    for view in SIDES:
        k = carspace.aspect(kit, view)
        m = mosaic(view, zone_side, near_side, -0.1, 1.15)
        # The number in a black block at the front of the door, a charcoal block stepping out under it.
        m.add(0.384, 0.215, 0.5, 0.235, CHAR)
        m.add(0.392, 0.235, 0.488, 0.575, BLACK)
        # The headline's green block, and the sill rows' black blocks.
        hl, hv = p.head_h * 3.3266, p.head_h / k  # the wordmark's length in u, height in v
        m.add(
            _snap(p.head_u - hl / 2 - 0.012),
            p.head_v - hv * 0.75,
            _snap(p.head_u + hl / 2 + 0.012),
            p.head_v + hv * 0.75,
            GREEN,
        )
        # The tech labels' charcoal block on the haunch over the rear arch.
        m.add(0.78, 0.785, 0.95, 1.05, CHAR)
        m.add(0.40, 0.2, 0.525, 0.31, BLACK)
        m.add(0.632, 0.2, 0.776, 0.31, BLACK)
        # The plate stack behind the rear wheel.
        for (v0, v1), c in zip(((0.62, 0.75), (0.47, 0.6), (0.32, 0.45)), (GREEN, GREY, CHAR), strict=True):
            m.add(0.952, v0, 1.01, v1, c)
        # The sill strips' maps stop short of their outlines: they are painted whole, black, so the mosaic
        # leaves them.
        els.append(_mosaic_fill(kit, f"mosaic.{view}", view, m.layers(), z=1))

    # --- top -----------------------------------------------------------------------------------------------
    m = mosaic("top", zone_top, near_top, -1.05, 1.05)
    m.add(0.095, 0.235, 0.182, 0.45, BLACK)  # the top number's block
    m.add(0.01, -0.2, 0.09, 0.2, GREY)  # the bonnet lockup's block
    els.append(_mosaic_fill(kit, "mosaic.top", "top", m.layers(), z=1))

    # --- parts no view maps whole -------------------------------------------------------------------------
    els += [panel(kit, f"lower-{part}", part, BLACK, z=2) for part in LOWER]
    els.append(panel(kit, "splitter-top", "splitter_top", BLACK, z=2))
    els.append(panel(kit, "lip", "splitter_lip", GREEN, z=3))
    els.append(panel(kit, "mirrors", "mirrors", GREEN, z=3))
    els.append(panel(kit, "vent", "bonnet_vent", BLACK, z=2))
    # Parts no view maps: their ground, cut with blocks drawn as texture rectangles.
    for part, ground, palette, n in (
        ("rear_face", BLACK, {CHAR: 3.0, GREEN: 1.0}, 60),
        ("wing_endplates", GREY, {GREEN: 2.0, GREY_T: 1.0}, 50),
        ("front_bumper_corner_l", GREY, {GREY_T: 2.0, BLACK: 1.0, GREEN: 0.6}, 40),
        ("front_bumper_corner_r", GREY, {GREY_T: 2.0, BLACK: 1.0, GREEN: 0.6}, 40),
    ):
        els += _texture_blocks(kit, part, ground, palette, p.rng(f"blocks-{part}"), n)

    # --- type and sponsors ---------------------------------------------------------------------------------
    els += mirror_sides(
        sponsor,
        kit,
        "headline",
        "zentiq",
        at=(p.head_u, p.head_v),
        color=BLACK,
        height=p.head_h,
        variant="wordmark",
        z=6,
    )
    els.append(
        sponsor(kit, "bonnet", "top", "zentiq", (0.05, 0.0), BLACK, height=0.045, variant="lockup", z=6)
    )
    els.append(
        sponsor(
            kit,
            "roof",
            "top",
            "zentiq",
            (0.45, 0.0),
            BLACK,
            height=0.05,
            variant="wordmark",
            facing="rear",
            z=6,
        )
    )
    els.append(
        sponsor(
            kit,
            "tail",
            "top",
            "zentiq",
            (1.04, 0.0),
            GREEN,
            height=0.035,
            variant="wordmark",
            facing="rear",
            z=6,
        )
    )
    els += mirror_sides(
        sponsor_group,
        kit,
        "sill-f",
        [("huilec", "wordmark"), ("hexmoor", "mark")],
        start=(p.sill_u, p.sill_v),
        end=(p.sill_u + p.sill_len, p.sill_v),
        height=p.sill_h,
        color=GREY,
        z=6,
    )
    els += mirror_sides(
        sponsor_group,
        kit,
        "sill-r",
        [("drexa", "wordmark"), ("brandvik", "mark")],
        start=(0.637, p.sill_v),
        end=(0.637 + p.sill_len * 135 / 115, p.sill_v),
        height=p.sill_h,
        color=GREY,
        z=6,
    )
    els += mirror_sides(
        sponsor_group,
        kit,
        "nose",
        [("telvio", "mark"), ("avelor", "mark")],
        start=(-0.005, 0.33),
        end=(-0.005, 0.09),
        height=0.018,
        color=GREY,
        axis="column",
        z=6,
    )
    els += mirror_sides(
        sponsor, kit, "mirror", "altmere", at=(0.375, 0.995), height=0.014, color=BLACK, variant="mark", z=6
    )
    els += mirror_sides(
        sponsor, kit, "stack-0", "ostrand", at=(0.98, 0.685), height=0.025, color=BLACK, variant="mark", z=6
    )
    els += mirror_sides(
        sponsor, kit, "stack-1", "rigmount", at=(0.98, 0.535), height=0.025, color=BLACK, variant="mark", z=6
    )
    els += mirror_sides(
        sponsor, kit, "stack-2", "grivant", at=(0.98, 0.385), height=0.025, color=GREEN, variant="mark", z=6
    )
    els += sponsor_group(
        kit,
        "deck",
        "top",
        [("norvane", "wordmark"), "bellcrest"],
        start=(0.72, 0.0),
        end=(0.8, 0.0),
        axis="column",
        facing="rear",
        height=0.028,
        color=GREY,
        z=6,
    )
    # Tech labels in Space Mono on blocks: a part number on the haunch, a telemetry readout on the door.
    for view in SIDES:
        els += typeset.type_block(
            kit,
            f"tech.{view}",
            view,
            [
                typeset.Line("ZQ-720", 0.018, font="Space Mono", weight=700),
            ],
            at=(0.792, 0.885),
            color=GREEN,
            z=6,
        )
    return base(BLACK).add(*els)
