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
from livery.primitives import base, clip_out, marks, mirror_sides, panel, sponsor, sponsor_group, svg

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
HEAD_LEN = 3.3266  # the zentiq wordmark's length in its heights
SUB = "ENERGY RACING"  # the headline's small line
TELVIO_ASPECT = 4.986  # the Telvio wordmark's length in its heights (assets/sponsors/telvio/brand.toml)
DREXA_ASPECT = 4.64  # and Drexa's (assets/sponsors/drexa/brand.toml)
NUM_U = (0.392, 0.488)  # the side number's block in u: the partner plate on it shares its edges

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
    "rear_deck_lip",
    "swan_neck_l",
    "swan_neck_r",
    "number_panels",
)
# Parts no view maps whole, painted as their ground cut with texture-rectangle blocks.
UNMAPPED = ("rear_face", "rear_valance", "wing_endplates", "front_bumper_corner_l", "front_bumper_corner_r")
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
    flank_w: float = param(0.38, 0.3, 0.8)  # outside |w| this the top follows the sides' transitions
    # but the haunch tops break into black later than the sides, so the shoulder crease behind the door
    # window meets green blocks on green, not black on green along one clean line.
    flank_t2: float = param(0.76, 0.68, 0.85)
    shoulder_v: float = param(0.872, 0.84, 0.9)  # the green shoulder's foot behind the door window (v)
    stray: float = param(0.25, 0.0, 0.6)  # share of zone-body small blocks in a foreign colour
    tone: float = param(0.6, 0.0, 1.0)  # share of zone-body blocks in the zone's tone
    black_tone: float = param(1.8, 1.0, 3.0)  # the black zone's body carries this many more blocks
    grey_tone: float = param(1.8, 1.0, 3.0)  # and the grey nose's body this many
    green_tone: float = param(1.8, 1.0, 3.0)  # and the green door's body this many
    # The headline's plate is a mosaic of its own: this many blocks, this share of them the darker green.
    plate_n: int = param(40, 0, 120)
    plate_tone: float = param(0.55, 0.0, 1.0)
    # The partner plate on top of the side number's block: its padding round the wordmark (car lengths).
    np_pad: float = param(0.0055, 0.004, 0.006)  # Telvio's wordmark needs 0.0169 car lengths of height
    wing_w: float = param(0.45, 0.3, 0.6)
    wing_h: float = param(0.028, 0.02, 0.035)  # the wing-top partners' centres either side of the tail zentiq
    ext_v: float = param(0.81, 0.7, 0.86)  # the extinguisher E's centre, over the partner plate
    # Headline on the door.
    head_u: float = param(0.6, 0.5, 0.7)
    head_v: float = param(0.64, 0.5, 0.8)
    head_h: float = param(0.058, 0.03, 0.08)  # car lengths
    # The small line under the headline: its em (car lengths), its top's gap under the wordmark's ink (v),
    # and how far the green plate reaches down to hold it (v).
    sub_em: float = param(0.017, 0.012, 0.025)
    sub_gap: float = param(0.03, 0.0, 0.1)
    sub_drop: float = param(0.08, 0.0, 0.2)
    drexa_h: float = param(0.0135, 0.012, 0.016)  # the rear row, ending on the headline's tail edge
    drexa_v: float = param(0.247, 0.2, 0.3)


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
        return (p.t1_top, p.t2_top) if abs(w) < p.flank_w else (p.t1, p.flank_t2)

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
                body_dens = dens_z * (p.grey_tone, p.green_tone, p.black_tone)[home]
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
        m.add(0.392, 0.235, 0.488, 0.575, BLACK)
        # The headline's green block, and the sill rows' black blocks.
        hl, hv = p.head_h * HEAD_LEN, p.head_h / k  # the wordmark's length in u, height in v
        pu0, pu1 = _snap(p.head_u - hl / 2 - 0.012), _snap(p.head_u + hl / 2 + 0.012)
        pa0, pa1 = p.head_v - hv * 0.75 - p.sub_drop, p.head_v + hv * 0.75
        m.add(pu0, pa0, pu1, pa1, GREEN)
        # The plate is a mosaic of the green's own two tones (black letters read on both), so the door is
        # never one flat field: big and medium blocks, clipped to the plate's rectangle.
        prng = p.rng(f"plate.{view}")
        for _ in range(p.plate_n):
            h = (TIERS[0][1], TIERS[1][1], TIERS[1][1])[int(prng.random() * 3)] * prng.uniform(0.75, 1.25)
            ll = h * prng.uniform(2.0, 6.0)
            u, y = prng.uniform(pu0, pu1), prng.uniform(pa0 * k, pa1 * k)
            c = GREEN_T if prng.random() < p.plate_tone else GREEN
            u0, u1 = max(pu0, u - ll / 2), min(pu1, u + ll / 2)
            y0, y1 = max(pa0 * k, y - h / 2), min(pa1 * k, y + h / 2)
            m.add_seen(u0, y0, u1, y1, c)
        # A partner plate in grey on top of the number's block, its wordmark padded equally all round.
        np_v1 = 0.575 + (0.096 - 2 * p.np_pad) / TELVIO_ASPECT / k + 2 * p.np_pad / k
        m.add(0.392, 0.575, 0.488, np_v1, GREY)
        # The front fender's partner block behind the front wheel, from the arch to the door's front edge.
        m.add(0.29, 0.22, 0.372, 0.5, BLACK)
        # The shoulder from the door window's rear corner back to the first step is green, as the pillar
        # above it is on top, so no block meets the green along the crease there: a black stray on the
        # fold read as one clean raked line in the sim.
        m.add(0.5, p.shoulder_v, 0.622, 1.02, GREEN)
        # Two green blocks stepping up over the shoulder crease behind the door window, so the black there
        # meets the green side in steps, not along the crease in one raked line.
        m.add(0.622, 0.885, 0.662, 0.975, GREEN)
        m.add(0.672, 0.925, 0.706, 1.02, GREEN_T)
        # The tech labels' charcoal block on the haunch over the rear arch.
        m.add(0.78, 0.785, 0.95, 1.05, CHAR)
        # The sill rows' black blocks: the front one under the number's block, edge to edge with it; the rear
        # one from the intake crease to the headline's tail edge.
        m.add(NUM_U[0], 0.18, NUM_U[1], 0.31, BLACK)
        m.add(0.62, 0.18, _snap(p.head_u + hl / 2 + 0.012), 0.31, BLACK)
        # The plate stack behind the rear wheel.
        for (v0, v1), c in zip(((0.62, 0.75), (0.47, 0.6), (0.32, 0.45)), (GREEN, GREY, CHAR), strict=True):
            m.add(0.952, v0, 1.01, v1, c)
        # The sill strips' maps stop short of their outlines: they are painted whole, black, so the mosaic
        # leaves them.
        els.append(_mosaic_fill(kit, f"mosaic.{view}", view, m.layers(), z=1))

    # --- top -----------------------------------------------------------------------------------------------
    m = mosaic("top", zone_top, near_top, -1.05, 1.05)
    m.add(0.095, 0.235, 0.182, 0.45, BLACK)  # the top number's block
    m.add(0.095, -0.49, 0.182, -0.235, BLACK)  # and its twin across the green vent, for a partner
    m.add(0.01, -0.2, 0.09, 0.2, GREY)  # the bonnet lockup's block
    # The shoulder tops behind the door windows (the pillar and the haunch top beside the side intake) carry
    # the sides' two steps over the fold: green to the first step's tail edge, then its darker green, so each
    # step reads as one block wrapping the shoulder and the black comes in behind them.
    for s in (1, -1):
        w0, w1 = sorted((s * p.flank_w, s * 0.9))
        m.add(0.53, w0, 0.662, w1, GREEN)
        w0, w1 = sorted((s * (p.flank_w + 0.08), s * 0.9))
        m.add(0.662, w0, 0.706, w1, GREEN_T)
    # The roof wordmark's green block, as on the doors, so no black block runs into its letters.
    rl = 0.05 * HEAD_LEN / carspace.aspect(kit, "top") / 2 + 0.03
    m.add(_snap(0.45 - 0.05 * 0.8), -rl, _snap(0.45 + 0.05 * 0.8), rl, GREEN)
    els.append(_mosaic_fill(kit, "mosaic.top", "top", m.layers(), z=1))

    # --- parts no view maps whole -------------------------------------------------------------------------
    els += [panel(kit, f"lower-{part}", part, BLACK, z=2) for part in LOWER]
    els.append(panel(kit, "splitter-top", "splitter_top", BLACK, z=2))
    els.append(panel(kit, "lip", "splitter_lip", GREEN, z=3))
    els.append(panel(kit, "mirrors", "mirrors", GREEN, z=3))
    # The bonnet vent in the accent, so the black number blocks either side of it read as blocks of their own.
    els.append(panel(kit, "vent", "bonnet_vent", GREEN, z=2))
    # Parts no view maps: their ground, cut with blocks drawn as texture rectangles.
    for part, ground, palette, n in (
        ("rear_face", BLACK, {CHAR: 3.0, GREEN: 1.0}, 60),
        ("rear_valance", BLACK, {CHAR: 3.0, GREEN: 1.2}, 50),
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
    # The sill row (DREXA) ends on the headline's tail edge, where zentiq and ENERGY RACING end. The number
    # block's black foot carries nothing: the room there (u 0.455-0.488 under the number zone) only takes
    # marks at sill size, which read as two dots from the side camera.
    head_end = p.head_u + p.head_h * HEAD_LEN / 2
    drexa_len = p.drexa_h * DREXA_ASPECT
    els += mirror_sides(
        sponsor,
        kit,
        "sill-r",
        "drexa",
        at=(head_end - drexa_len / 2, p.drexa_v),
        color=GREY,
        height=p.drexa_h,
        variant="wordmark",
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
    for view in SIDES:
        # Telvio on the grey plate on top of the number's block, padded equally all round.
        k = carspace.aspect(kit, view)
        wl = NUM_U[1] - NUM_U[0] - 2 * p.np_pad
        els.append(
            sponsor(
                kit,
                f"numplate.{view}",
                view,
                "telvio",
                ((NUM_U[0] + NUM_U[1]) / 2, 0.575 + (wl / TELVIO_ASPECT / 2 + p.np_pad) / k),
                BLACK,
                height=wl / TELVIO_ASPECT,
                variant="wordmark",
                z=6,
            )
        )
    # Two partner marks stacked on the black block behind the front wheel.
    els += mirror_sides(
        sponsor_group,
        kit,
        "fender",
        [("grivant", "mark"), ("brandvik", "mark")],
        start=(0.331, 0.475),
        end=(0.331, 0.245),
        height=0.018,
        color=GREY,
        axis="column",
        z=6,
    )
    # The bonnet's second block carries Huilec, read from the front like the lockup.
    els.append(
        sponsor(kit, "bonnet-p", "top", "huilec", (0.1385, -0.37), GREY, height=0.03, variant="mark", z=6)
    )
    # The wing top carries a partner mark either side of the tail's zentiq, read from the chase cam: the
    # wing's map ends about |w| 0.68, too short for a wordmark at its minimum height beside the zentiq.
    for brand, w in (("drexa", -p.wing_w), ("grivant", p.wing_w)):
        els.append(
            sponsor(
                kit,
                f"wing-{brand}",
                "top",
                brand,
                (1.04, w),
                GREY,
                height=p.wing_h,
                variant="mark",
                facing="rear",
                z=6,
            )
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
    # The headline's small line, on the wordmark's left edge and spread to its width: one Orbitron
    # hierarchy on the door.
    hl, hv = p.head_h * HEAD_LEN, p.head_h / carspace.aspect(kit, "side_l")
    sub = typeset.measure(SUB, "Orbitron", 1.0, 700)
    track = (hl / p.sub_em - (sub.ink[2] - sub.ink[0])) / (len(SUB) - 1)
    for view in SIDES:
        els += typeset.type_block(
            kit,
            f"sub.{view}",
            view,
            [typeset.Line(SUB, p.sub_em, font="Orbitron", weight=700, tracking=track)],
            at=(p.head_u + (hl if view == "side_r" else -hl) / 2, p.head_v - hv / 2 - p.sub_gap),
            color=BLACK,
            z=6,
        )
    # Race markings: the extinguisher E by the mirror.
    for view in SIDES:
        els.append(marks(kit, f"ext.{view}", "extinguisher", (view, 0.44, p.ext_v), BLACK, 0.018, z=6))
    # Tech labels in Space Mono on blocks: a part number on the haunch.
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
