"""Zentiq: the acid-green mamba wrapped once round a black car.

One animal, one body: a single ribbon of constant width from the fangs at the splitter to the tail tip on
the right wing endplate. The head is the nose; the neck leaves it over the left front fender, comes down
behind the left front arch, runs low along the left door under the number, rises over the left haunch,
crosses the engine cover and roof diagonally to the right, comes down behind the right front arch, runs
back low, climbs over the right rear arch and ends on the right wing endplate.

Across its width the body is, from one edge to the other: a light-grey belly band crossed by black belly
plates, a field of diamond scales (acid kites on the darker green, the biggest down the spine, small ones
in the notches towards the edges, all pointing to the tail), a black pinline and an acid lip. The black
car round it carries faint charcoal scales near the body, like shed skin.
"""

import math
from dataclasses import dataclass
from itertools import pairwise

import shapely
from shapely.geometry import LineString, Polygon

from livery import carspace, geom
from livery.params import DesignParams, param
from livery.primitives import (
    base,
    clip_out,
    clip_to,
    marks,
    mirror_sides,
    panel,
    shape,
    sponsor,
    sponsor_group,
    waive,
)
from livery.typeset import Line, type_block

KIT = "mclaren-720s-gt3-evo"
BLACK, CHAR = "#0c0d0c", "#202420"
ACID, DEEP = "#b6ff00", "#3f6600"
BELLY = "#cfd3c8"
PX_PER_U = 1500.0  # texture px per car length as seen, about, on the sides and the top

# The body's spine on each view in car space (u, v on the sides, u, w on top), head end first. Each piece
# runs past its view's edge where the next view picks it up, and crosses that edge square (at one u), so
# the two views meet whatever w the side's top edge lands at.
NECK_TOP = ((0.02, -0.42), (0.1, -0.53), (0.2, -0.55), (0.262, -0.72), (0.27, -1.25))
SIDE_L = (
    (-0.06, 0.95),
    (0.10, 0.955),
    (0.20, 0.96),
    (0.262, 0.90),
    (0.30, 0.66),
    (0.325, 0.40),
    (0.35, 0.18),
    (0.40, 0.085),
    (0.47, 0.08),
    (0.53, 0.16),
    (0.57, 0.36),
    (0.61, 0.60),
    (0.645, 0.80),
    (0.655, 0.95),
    (0.656, 1.25),
)
# The coil over the engine cover and roof: it leaves the left haunch square at u 0.656 and arrives on the
# right square at u 0.315, and between the two it is one S of changing curvature (see `_coil_top`) with no
# straight stretch, so it moves like the dips and rises on the sides.
COIL_FROM, COIL_TO = (0.656, -0.5), (0.315, 0.5)  # where the S leaves and joins the square crossings
SIDE_R = (
    (0.315, 1.25),
    (0.315, 0.92),
    (0.33, 0.66),
    (0.35, 0.40),
    (0.37, 0.18),
    (0.42, 0.085),
    (0.48, 0.09),
    (0.53, 0.18),
    (0.57, 0.38),
    (0.615, 0.62),
    (0.66, 0.80),
    (0.72, 0.90),
    (0.80, 0.94),
    (0.90, 0.95),
    (1.02, 0.94),
    (1.1, 0.935),
)
# Which edge carries the belly, as the sign of the offset left of travel in each view's car space: the
# body's front edge where it crosses from a side onto the top, so the same edge all the way round.
BELLY_SIDE = (("side_l", 1), ("top", 1), ("side_r", -1))
# Where the body carries ZENTIQ in black on a plain acid stretch of its scale field: per view, the stretch
# along the spine, from where to where it passes in u.
WORDS = (("side_l", (0.385, 0.505)), ("side_r", (0.72, 0.84)))
# Why the coil's scales and belly plates on top break at the roof's seams: reported to the owner as a kit fault.
ROOF_SEAMS = (
    "The kit's top maps of the roof disagree at its seams: i102 and i078 put the same seam pairs 0.005 u apart "
    "(u 0.419-0.420 against 0.424-0.427, about 8 px), i078 and i104 the roof / engine cover seam 0.007 u apart "
    "(u 0.598-0.601 against 0.606-0.608), so a scale pattern with 7 px gaps can't land on both sides; the rest "
    "are kite edges within a pixel of a seam point."
)

# The tail beyond the right rear quarter, and the head's parts on the nose.
TAIL_PARTS = ("rear_bumper_side_r", "haunch_corner_r")
HEAD_PARTS = (
    "front_bumper_corner_l",
    "front_bumper_corner_r",
    "grille_upright_l",
    "grille_upright_r",
    "intake_bar_r",
)
# The fender's nose pieces round each headlight: the headlight surrounds, green with the head.
LIDS = ("fender_nose_l", "fender_nose_r")
ENDPLATES = ("wing_endplate_l", "wing_endplate_r")
# The head's scale rows on the bonnet: |w| of the row, a kite's length (u) and its width (w).
HEAD_ROWS = ((0.0, 0.034, 0.12), (0.12, 0.026, 0.09), (0.215, 0.02, 0.07))

# Partner marks, one colour per field: grey on the black sills behind the rising body, black on the green
# mirror shells, bumper corners and endplates.
SILL_L = (("hexmoor", "mark"), ("huilec", "mark"), ("telvio", "mark"))
SILL_R = (("grivant", "mark"), ("ostrand", "mark"), ("drexa", "mark"))
DOOR_R = (("rigmount", "wordmark"), ("norvane", "mark"))
WING = (("avelor", -0.4), ("brandvik", 0.4))  # marks either side of ZENTIQ on the wing, by w


@dataclass(frozen=True)
class Params(DesignParams):
    seed: int = param(5, 0, 2**31 - 1)
    # The body's profile across, belly edge first, in texture px.
    belly_px: float = param(20.0, 14.0, 40.0)
    field_px: float = param(48.0, 20.0, 60.0)
    pin_px: float = param(14.0, 14.0, 24.0)
    lip_px: float = param(14.0, 14.0, 24.0)
    gap_px: float = param(7.0, 6.0, 12.0)  # the darker green round each scale
    plate_px: float = param(7.0, 6.0, 14.0)  # a belly plate's width
    plate_every: float = param(1.4, 0.8, 3.0)  # belly plates' spacing, in belly widths
    kite: float = param(1.8, 1.2, 3.0)  # a spine scale's length over its height
    word_h: float = param(0.024, 0.02, 0.04)  # ZENTIQ along the body, car lengths
    word_lift: float = param(0.004, 0.0, 0.01)  # car lengths towards the belly, on the left
    # Shed skin: charcoal scales in rows outside the body, thinning out over `shed_rows` rows.
    shed_rows: int = param(4, 0, 8)
    shed_density: float = param(0.75, 0.0, 1.0)
    # The head on the bonnet.
    head_u: float = param(0.095, 0.05, 0.2)  # the back of the head on the centre line
    eye_u: float = param(0.062, 0.03, 0.1)
    eye_w: float = param(0.235, 0.2, 0.55)  # inboard of this the bonnet's map holds the whole eye
    eye_len: float = param(0.07, 0.03, 0.1)  # car lengths as seen
    eye_rake: float = param(32.0, 0.0, 60.0)  # degrees the eye's axis turns outwards towards the tail
    fang_w: float = param(0.42, 0.3, 0.6)
    # The lockup in black on the head's brow, between the eyes.
    lockup_u: float = param(0.028, 0.01, 0.08)
    lockup_w: float = param(0.0, -0.1, 0.1)
    lockup_h: float = param(0.02, 0.015, 0.03)
    # The coil's S on top: how long its ends run on square across the car before turning forward, as a
    # share of the distance between them (more: tighter bends and a middle that runs more along the
    # car), and how much of that goes to the left (haunch) end rather than the right.
    coil_ease: float = param(0.58, 0.0, 1.2)
    coil_skew: float = param(0.0, -0.6, 0.6)


def _coil_top(kit, p: Params):
    """The coil's spine on top: square across the car at both sides, and between `COIL_FROM` and
    `COIL_TO` a cubic Bezier leaving and arriving square across, so its curvature never stops changing."""
    k = carspace.aspect(kit, "top")
    (u0, w0), (u1, w1) = COIL_FROM, COIL_TO
    a = p.coil_ease * math.hypot(u1 - u0, (w1 - w0) * k) / k  # in w
    c = ((u0, w0), (u0, w0 + a * (1 + p.coil_skew)), (u1, w1 - a * (1 - p.coil_skew)), (u1, w1))
    pts = []
    for i in range(17):
        t = i / 16
        b = ((1 - t) ** 3, 3 * t * (1 - t) ** 2, 3 * t**2 * (1 - t), t**3)
        pts.append(
            (
                sum(f * q[0] for f, q in zip(b, c, strict=True)),
                sum(f * q[1] for f, q in zip(b, c, strict=True)),
            )
        )
    return ((u0, -1.25), *pts, (u1, 1.25))


def _smooth(pts, n: int = 4):
    """Chaikin corner cutting, keeping both ends."""
    pts = [tuple(q) for q in pts]
    for _ in range(n):
        out = [pts[0]]
        for a, b in pairwise(pts):
            out += [(0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1])]
            out += [(0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1])]
        out.append(pts[-1])
        pts = out
    return pts


class Body:
    """The ribbon on one view: its spine as seen (v or w scaled into car lengths), and offsets across it
    measured from the belly edge (0) to the lip edge (`width`), in car lengths as seen."""

    def __init__(self, kit, view: str, spine, width: float):
        self.k = carspace.aspect(kit, view)
        self.line = LineString([(u, a * self.k) for u, a in _smooth(spine)])
        self.width = width
        self.sign = dict(BELLY_SIDE)[view]
        self.length = self.line.length

    def s_at(self, u: float) -> float:
        """The length along the spine where it passes `u` (the spines run one way in u)."""
        cut = self.line.intersection(LineString([(u, -5.0), (u, 5.0)]))
        return self.line.project(cut if cut.geom_type == "Point" else cut.geoms[0])

    def at(self, s: float, t: float):
        """The car-space point `t` across from the belly edge at length `s` along the spine."""
        s = min(max(s, 0.0), self.length)
        d = 1e-4
        a = self.line.interpolate(max(s - d, 0.0))
        b = self.line.interpolate(min(s + d, self.length))
        dx, dy = b.x - a.x, b.y - a.y
        n = math.hypot(dx, dy)
        off = self.sign * (self.width / 2 - t)  # left of travel
        p = self.line.interpolate(s)
        return (p.x - dy / n * off, (p.y + dx / n * off) / self.k)

    def strip(self, t0: float, t1: float, s0: float = 0.0, s1: float | None = None, step: float = 0.003):
        s1 = self.length if s1 is None else s1
        n = max(2, math.ceil((s1 - s0) / step))
        ss = [s0 + (s1 - s0) * i / n for i in range(n + 1)]
        return [self.at(s, t0) for s in ss] + [self.at(s, t1) for s in reversed(ss)]

    def kite(self, s: float, t: float, length: float, height: float, tail: float = 0.6):
        """A scale centred at (s, t), its tail-ward point `tail` of its length behind its widest place."""
        front, back = length * (1 - tail), length * tail
        return Polygon(
            [
                self.at(s - front, t),
                self.at(s, t - height / 2),
                self.at(s + back, t),
                self.at(s, t + height / 2),
            ]
        )


def _profile(p: Params):
    px = 1.0 / PX_PER_U
    belly, field = p.belly_px * px, p.field_px * px
    pin, lip = p.pin_px * px, p.lip_px * px
    return px, belly, field, pin, lip, belly + field + pin + lip


def _body(kit, p: Params, id: str, view: str, spine) -> tuple[list, Body]:
    """One view's piece of the body: the belly with its plates, the scale field, the pinline and the lip;
    and the shed skin beside it."""
    px, belly, field, pin, _, width = _profile(p)
    b = Body(kit, view, spine, width)
    f0, f1 = belly, belly + field
    word = dict(WORDS).get(view) if id == "body" else None
    w0, w1 = (b.s_at(word[0]), b.s_at(word[1])) if word else (-1.0, -1.0)
    els = [
        shape(kit, f"{id}-belly.{view}", view, b.strip(0.0, belly), BELLY, z=2),
        shape(kit, f"{id}-field.{view}", view, b.strip(f0, f1), DEEP, z=2),
        shape(kit, f"{id}-pin.{view}", view, b.strip(f1, f1 + pin), BLACK, z=2),
        shape(kit, f"{id}-lip.{view}", view, b.strip(f1 + pin, width), ACID, z=2),
    ]
    if word:
        els.append(shape(kit, f"{id}-word.{view}", view, b.strip(f0, f1, w0, w1), ACID, z=3))
    # Belly plates: black bars across the belly band.
    step = p.plate_every * belly
    plates = [
        Polygon(b.strip(-0.3 * px, belly + 0.3 * px, s, s + p.plate_px * px, step=p.plate_px * px / 2))
        for s in [i * step for i in range(1, int(b.length / step))]
    ]
    els.append(
        _roof(id, geom.fill(kit, f"{id}-plates.{view}", view, {BLACK: plates}, z=3, tags=("crosses_seam",)))
    )
    # Scales: a row of big kites down the spine, small ones in the notches between them at each edge.
    g = p.gap_px * px
    mid = (f0 + f1) / 2
    big_h = field - g
    big_l = big_h * p.kite
    small_h = (field - g) / 2 - g
    small_l = small_h * p.kite * 1.2
    scales = []
    for i in range(int(b.length / big_l) + 2):
        s = i * big_l
        if w0 - big_l < s < w1 + big_l * 0.5:
            continue
        scales.append(b.kite(s, mid, big_l - g * 1.4, big_h))
        for t in (f0 + g / 2 + small_h / 2, f1 - g / 2 - small_h / 2):
            scales.append(b.kite(s + big_l / 2, t, small_l, small_h))
    scales_el = geom.fill(
        kit,
        f"{id}-scales.{view}",
        view,
        {ACID: scales},
        # On the right the rise runs beside the door's rear shut line: its scales keep clear of it.
        edges=geom.edges(kit) if view == "side_r" else None,
        z=3,
        tags=("crosses_seam",),
    )
    els.append(_roof(id, scales_el))
    # Shed skin: rows of charcoal kites outside both edges, staggered, thinning out row by row.
    rng = p.rng(f"shed-{id}-{view}")
    h = small_h * 1.5
    ln = h * p.kite
    shed = []
    for j in range(p.shed_rows):
        chance = p.shed_density * (1 - j / p.shed_rows) ** 1.5
        for t in (-g - h / 2 - j * (h / 2 + g / 2), width + g + h / 2 + j * (h / 2 + g / 2)):
            off = (j % 2) * ln / 2
            for i in range(int(b.length / ln) + 1):
                if rng.random() < chance:
                    shed.append(b.kite(i * ln + off, t, ln - g, h))
    avoid = shapely.union_all([geom.footprint(kit, view, z.buffer(0.01)) for z in _numbers(kit, view)])
    els.append(
        geom.fill(kit, f"{id}-shed.{view}", view, {CHAR: shed}, avoid=avoid, edges=geom.edges(kit), z=1)
    )
    return els, b


def _roof(id: str, el):
    """The coil's scales and plates across the roof, with the seam check waived for the kit's roof maps."""
    return waive(el, "seam", ROOF_SEAMS) if id == "coil" else el


def _numbers(kit, view: str):
    names = {"side_l": ("number_l",), "side_r": ("number_r",), "top": ("number_top",)}[view]
    return [kit.feature(n).geometry() for n in names]


def _word(kit, p: Params, b: Body, view: str):
    """ZENTIQ in black on the plain stretch of the body's scale field."""
    _, belly, field, _, _, _ = _profile(p)
    u0, u1 = dict(WORDS)[view]
    # On the left the stretch runs under the number, near the sill: the word sits towards the belly.
    off = p.word_lift if view == "side_l" else 0.0
    at = b.at((b.s_at(u0) + b.s_at(u1)) / 2, belly + field / 2 - off)
    return sponsor(
        kit, f"word.{view}", view, "zentiq", at=at, color=BLACK, height=p.word_h, variant="wordmark", z=4
    )


def _almond(c, length: float, height: float, angle: float, k: float, n: int = 24):
    """A pointed oval centred at car-space `c`, `length` along an axis turned `angle` degrees from u."""
    ca, sa = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    pts = []
    for i in range(n):
        th = 2 * math.pi * i / n
        x = length / 2 * math.cos(th)
        y = height / 2 * math.sin(th) * abs(math.sin(th)) ** 0.35
        pts.append((c[0] + x * ca - y * sa, c[1] + (x * sa + y * ca) / k))
    return Polygon(pts)


def _head(kit, p: Params) -> list:
    """The head on the bonnet and bumper: acid, with slit-pupil eyes and darker lids over the headlights,
    head plates between them, and grey fangs down the splitter's front corners."""
    k = carspace.aspect(kit, "top")
    hu = p.head_u
    head = [(-0.3, -1.3), (hu - 0.035, -1.3), (hu - 0.035, -0.5), (hu, -0.25)]
    head += [(hu, 0.25), (hu - 0.035, 0.5), (hu - 0.035, 1.3), (-0.3, 1.3)]
    # The splitter is the mouth, painted black on its own: the head stops at it.
    els = [clip_out(kit, "splitter", shape(kit, "head.top", "top", head, ACID, z=1))]
    els += [panel(kit, f"head-{part}", part, ACID, z=1) for part in HEAD_PARTS]
    els += [panel(kit, f"head-{part}", part, ACID, z=1) for part in LIDS]
    lids, eyes, irises, pupils, plates = [], [], [], [], []
    for side in (-1, 1):
        ang = side * p.eye_rake
        c = (p.eye_u, side * p.eye_w)
        L, H = p.eye_len, p.eye_len * 0.42
        eye = _almond(c, L, H, ang, k)
        ca, sa = math.cos(math.radians(ang)), math.sin(math.radians(ang))
        # The lid: a heavier almond over the eye's inner side, towards the centre line.
        lc = (c[0] + 0.45 * H * sa * side, c[1] - 0.45 * H * ca * side / k)
        lids.append(_almond(lc, L * 1.45, H * 1.6, ang, k).difference(eye.buffer(0.0045)))
        eyes.append(eye)
        irises.append(_almond(c, L * 0.62, H * 0.72, ang, k, 32))
        # A vertical slit, square to the eye's axis.
        sw, sh = 0.0055, H * 0.62
        pupils.append(
            Polygon(
                [
                    (c[0] + x * ca - y * sa, c[1] + (x * sa + y * ca) / k)
                    for x, y in ((-sw / 2, 0), (0, -sh / 2), (sw / 2, 0), (0, sh / 2))
                ]
            )
        )
    # Head scales: the body's kites in the darker green, pointing back, biggest down the centre line and
    # smaller towards the eyes, in staggered rows.
    brow = shapely.box(p.lockup_u - 0.015, -0.13, p.lockup_u + 0.015, 0.13)
    keep_out = shapely.union_all([e.buffer(0.012) for e in lids + eyes] + [brow])
    for j, (w, ln, ht) in enumerate(HEAD_ROWS):
        for side in (-1, 1) if w else (1,):
            u = 0.004 + (j % 2) * ln / 2
            while u + ln * 0.6 < p.head_u - 0.012 - 0.02 * (w > 0.1):
                q = Polygon(
                    [
                        (u - ln * 0.4, side * w),
                        (u, side * w - ht / 2),
                        (u + ln * 0.6, side * w),
                        (u, side * w + ht / 2),
                    ]
                )
                if not q.intersects(keep_out):
                    plates.append(q)
                u += ln + 0.006
    els.append(geom.fill(kit, "head-plates.top", "top", {DEEP: plates}, z=2))
    els.append(geom.fill(kit, "lids.top", "top", {BLACK: lids}, z=2))
    els.append(geom.fill(kit, "eyes.top", "top", {BLACK: eyes}, z=3))
    els.append(geom.fill(kit, "iris.top", "top", {BELLY: irises}, z=4))
    els.append(geom.fill(kit, "pupil.top", "top", {BLACK: pupils}, z=5))
    # The mouth: the splitter black, with a fang and two smaller teeth at each front corner.
    els.append(panel(kit, "mouth", "splitter", BLACK, z=2))
    fangs = []
    for side in (-1, 1):
        for dw, half, tip in ((0.0, 0.06, -0.07), (0.12, 0.035, -0.045), (-0.11, 0.035, -0.045)):
            w = side * (p.fang_w + dw)
            fangs.append(Polygon([(0.012, w - half), (0.012, w + half), (tip, w)]))
    els.append(clip_to(kit, "splitter", geom.fill(kit, "fangs.top", "top", {BELLY: fangs}, z=3)))
    return els


def _tail(kit, p: Params) -> list:
    """The tail: the right rear quarter in acid, and its tip on the right wing endplate; the left endplate
    in acid too, both cut with the body's scales."""
    els = [panel(kit, f"tail-{part}", part, ACID, z=1) for part in TAIL_PARTS]
    els += [panel(kit, f"endplate-{part}", part, ACID, z=1) for part in ENDPLATES]
    k = carspace.aspect(kit, "top")
    kites = []
    for side in (-1, 1):
        for i in range(8):
            for j in range(5):
                u = 0.94 + i * 0.03 + (j % 2) * 0.015
                w = side * (0.5 + j * 0.09)
                dl, dh = 0.012, 0.05
                kites.append(Polygon([(u - dl, w), (u, w - dh / 2), (u + dl * 1.4, w), (u, w + dh / 2)]))
    del k
    scales = geom.fill(kit, "endplate-scales.top", "top", {DEEP: kites}, z=2)
    els.append(clip_to(kit, list(ENDPLATES), scales))
    return els


def _marks(kit, p: Params) -> list:
    """Fifteen-odd partner marks in groups, the lockup on the bonnet, ZENTIQ on the wing, the tech label
    on the door and the race markings."""
    els = []
    els += sponsor_group(kit, "sill.side_l", "side_l", SILL_L, (0.665, 0.09), (0.77, 0.09), 0.022, BELLY, z=6)
    els += sponsor_group(kit, "sill.side_r", "side_r", SILL_R, (0.77, 0.09), (0.665, 0.09), 0.022, BELLY, z=6)
    els += mirror_sides(
        sponsor, kit, "mirror", "altmere", at=(0.374, 1.0), color=BLACK, height=0.016, variant="mark", z=6
    )
    els.append(
        sponsor(
            kit,
            "lockup.top",
            "top",
            "zentiq",
            at=(p.lockup_u, -p.lockup_w),
            color=BLACK,
            height=p.lockup_h,
            variant="lockup",
            z=6,
        )
    )
    els.append(
        sponsor(
            kit,
            "wing.top",
            "top",
            "zentiq",
            at=(1.04, 0.0),
            color=ACID,
            height=0.03,
            variant="wordmark",
            facing="rear",
            z=6,
        )
    )
    els += type_block(
        kit,
        "label.side_l",
        "side_l",
        [
            Line("DENDROASPIS ZENTIQ", 0.0165, "Space Mono", 700),
            Line("ZQ-720 / 07-417", 0.0165, "Space Mono", 700),
        ],
        (0.398, 0.785),
        BELLY,
        z=6,
    )
    els.append(marks(kit, "extinguisher", "extinguisher", ("side_l", 0.70, 0.30), BELLY, 0.022, z=6))
    # The right door's top, inside the coil, and the wing's top read from behind.
    els += sponsor_group(kit, "door.side_r", "side_r", DOOR_R, (0.56, 0.72), (0.40, 0.72), 0.025, BELLY, z=6)
    for i, (brand, w) in enumerate(WING):
        els.append(
            sponsor(
                kit,
                f"wing-{i}.top",
                "top",
                brand,
                at=(1.04, w),
                color=BELLY,
                height=0.022,
                variant="mark",
                facing="rear",
                z=6,
            )
        )
    return els


def build(kit, p: Params):
    els = []
    bodies = {}
    for id, view, spine in (
        ("neck", "top", NECK_TOP),
        ("body", "side_l", SIDE_L),
        ("coil", "top", _coil_top(kit, p)),
        ("body", "side_r", SIDE_R),
    ):
        parts, b = _body(kit, p, id, view, spine)
        els += parts
        if id == "body":
            bodies[view] = b
    els += [_word(kit, p, b, view) for view, b in bodies.items()]
    els += _head(kit, p)
    els += _tail(kit, p)
    els += [panel(kit, f"mirror-{part}", part, ACID, z=1) for part in ("mirror_shell_l", "mirror_shell_r")]
    els += _marks(kit, p)
    return base(BLACK).add(*els)
