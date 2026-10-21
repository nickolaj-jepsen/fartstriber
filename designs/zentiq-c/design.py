"""Zentiq GT3, concept C: the bolt split.

WRT #46's system (row 25) on the McLaren: two fields, black and white, that swap sides, an acid-green
pinline tracing the split and the window line, hairlines across both fields, hazard chevrons, green
hardware and a big outlined designation across the roof. The move: the split on each side is the Zentiq
bolt, the brand's Z with its lightning jog, drawn the length of the car: back along the shoulder from the
nose, down through the door just behind the number in one steep jogged diagonal, back along the lower door
to the tail. Left: black ahead of and below the bolt, white above and behind. Right: the same bolt, the
colours swapped. On top the split runs down the centreline and swaps at the roof / cover seam, so the
front reads black-left / white-right and the tail white-left / black-right, as each side's ends do.
"""

from dataclasses import dataclass

import shapely
from shapely.geometry import LineString, Point, Polygon

from livery import body, carspace, geom, patterns, sponsors
from livery.model import Element
from livery.params import DesignParams, param
from livery.primitives import base, clip_out, clip_to, marks, panel, shape, sponsor, sponsor_group, svg
from livery.typeset import Line, type_block

KIT = "mclaren-720s-gt3-evo"
BLACK, WHITE, GREEN = "#111214", "#efefeb", "#b6ff00"
GREY, DGREY = "#bdbfbc", "#3a3d42"  # each field's hairlines, in the other field's tone
MONO = "Space Mono"
# The top view's central panels ahead of the roof / engine cover seam: split on the centreline, the left
# half black (the left nose's colour), the right white.
FRONT_TOP = ("bonnet_skin", "bonnet_vent", "windscreen_frame", "roof", "roof_fin")
# Behind the seam: the left half white (the left tail's colour), the right black.
REAR_TOP = ("engine_cover", "engine_cover_scoop", "rear_deck", "rear_deck_lip", "buttress_l", "buttress_r")
# The front's own parts, white on the right: kept out of the top view's right-half black.
NOSE_R = ("front_bumper_r", "splitter", "grille_header", "crash_opening")
SEAM_U = 0.605  # the roof / engine cover seam (`roof_cover_seam`)
# The Zentiq mark (assets/sponsors/zentiq/mark.svg), its outline points in the file's units (x right, y
# down, 65.57 x 100): drawn here as geometry because the roof carries it outlined and counter-filled,
# which `sponsor` can't do.
MARK = (
    (65.57, 0.0),
    (20.0, 0.0),
    (20.0, -17.0),
    (28.74, -41.0),
    (7.29, -41.0),
    (22.57, -83.0),
    (0.0, -83.0),
    (0.0, -100.0),
    (45.57, -100.0),
    (45.57, -83.0),
    (36.84, -59.0),
    (58.29, -59.0),
    (43.0, -17.0),
    (65.57, -17.0),
)
# The sponsor rows, nose to tail, one colour per field.
SILL = (("huilec", "wordmark"), ("grivant", "wordmark"))  # under the number
DUCT = (("telvio", "mark"), ("ostrand", "mark"))  # behind the side intake
ARCH = (("drexa", "mark"), ("hexmoor", "mark"))
REAR = (("rigmount", "mark"), ("avelor", "mark"))
NOSE = (("brandvik", "mark"), ("altmere", "mark"))


@dataclass(frozen=True)
class Params(DesignParams):
    lean: float = param(20.0, 5.0, 35.0)  # the bolt's diagonal, degrees from upright, top to the tail
    shoulder_v: float = param(0.72, 0.55, 0.75)  # the Z's top bar
    sill_v: float = param(0.26, 0.12, 0.32)  # the Z's bottom bar, above the side intake
    diag_u: float = param(0.515, 0.47, 0.56)  # the upper diagonal's u at jog_v
    jog_v: float = param(0.44, 0.3, 0.55)
    jog: float = param(0.04, 0.0, 0.06)  # the lower diagonal starts this far towards the tail
    pin: float = param(18.0, 14.0, 30.0)  # the green pinline, texture px
    # Hairlines: parallel to the diagonal, the first `hair_g0` from it, each gap `hair_r` times the last.
    hair: float = param(9.0, 6.0, 14.0)
    hair_g0: float = param(0.012, 0.006, 0.03)
    hair_r: float = param(1.15, 1.0, 1.6)
    hair_n: int = param(16, 4, 24)
    hair_bow: float = param(0.02, -0.15, 0.15)  # u the lines bow back by at sill and window
    chin_px: float = param(90.0, 0.0, 120.0)  # the black chin along each bumper corner's foot, texture px
    haz_px: float = param(40.0, 24.0, 80.0)  # its green hazard slashes' period
    hair_gap: float = param(14.0, 8.0, 30.0)  # px clear of the pin
    # Chevrons on each black front fender; the left's lane behind the arch, ahead of the door's front edge.
    chev_u0: float = param(0.31, 0.28, 0.36)
    chev_mid: float = param(0.33, 0.2, 0.6)
    chev_half: float = param(0.08, 0.04, 0.12)
    chev_step: float = param(0.022, 0.015, 0.05)
    chev_n: int = param(3, 1, 5)
    chev_t: float = param(0.016, 0.006, 0.03)
    # Type and marks.
    code_gap: float = param(0.05, 0.02, 0.12)  # v from the wordmark's foot to the code's top
    code_size: float = param(0.024, 0.017, 0.04)
    head_u: float = param(0.635, 0.6, 0.7)  # zentiq on the rear door and haunch, centre
    head_v: float = param(0.66, 0.45, 0.8)
    head_h: float = param(0.038, 0.02, 0.05)
    sill_row_v: float = param(0.15, 0.08, 0.22)
    sill_h: float = param(0.02, 0.014, 0.03)
    sill_gap: float = param(0.01, 0.004, 0.03)  # u the fender's sill wordmark stays off the door's front edge
    # u the door's stays behind it: the panel ahead stands proud of the door and hides its start from front34.
    sill_back: float = param(0.015, 0.004, 0.03)
    roof_h: float = param(0.18, 0.1, 0.26)  # the roof mark's height along the car
    roof_u: float = param(0.495, 0.42, 0.55)
    cover_u: float = param(0.8, 0.7, 0.85)


# --- the bolt -----------------------------------------------------------------------------------------


def _bolt(kit, p: Params) -> Polygon:
    """The field ahead of and below the Z on a side, in car space."""
    k = geom.rake(kit, p.lean)

    def up(v):
        return p.diag_u + k * (v - p.jog_v)

    def lo(v):
        return p.diag_u + p.jog + k * (v - p.jog_v)

    return Polygon(
        [
            (-0.3, -0.3),
            (-0.3, p.shoulder_v),
            (up(p.shoulder_v), p.shoulder_v),
            (up(p.jog_v), p.jog_v),
            (lo(p.jog_v), p.jog_v),
            (lo(p.sill_v), p.sill_v),
            (1.3, p.sill_v),
            (1.3, -0.3),
        ]
    )


def _scaled(geom_, sx: float, sy: float):
    return shapely.affinity.scale(geom_, sx, sy, origin=(0, 0))


def _line(pts, px: float, scale) -> Polygon:
    """A polyline `px` texture px wide, in car space at `scale` (px per unit u, v)."""
    sx, sy = scale
    seen = LineString([(u * sx, v * sy) for u, v in pts]).buffer(px / 2, cap_style="flat")
    return _scaled(seen, 1 / sx, 1 / sy)


def _hairlines(kit, view: str, front: Polygon, number, p: Params) -> list[Element]:
    """WRT's hairlines: parallel to the diagonal, densest at it and opening out towards each end, bowing
    back at the sill and window like contour lines; on the black field in dark grey, on the white in grey,
    stopping clear of the pin and the number."""
    scale = carspace.px_per_unit(kit, view, (0.5, 0.5))
    k = geom.rake(kit, p.lean)
    clear = (p.pin / 2 + p.hair_gap) / scale[0]
    split = front.boundary.buffer(clear)
    black = front if view == "side_l" else shapely.box(-0.3, -0.3, 1.3, 1.3).difference(front)
    white = shapely.box(-0.3, -0.3, 1.3, 1.3).difference(black)
    keep_out = split.union(number.buffer(0.02))
    offsets, d, g = [], 0.0, p.hair_g0
    for _ in range(p.hair_n):
        d += g
        offsets.append(d)
        g *= p.hair_r
    lines = []
    for sign in (-1, 1):
        for d in offsets:
            u0 = p.diag_u + (p.jog if sign > 0 else 0.0) + sign * d
            pts = [
                (u0 + k * (v - p.jog_v) + sign * p.hair_bow * (v - 0.5) ** 2, v)
                for v in [x / 20 for x in range(-2, 23)]
            ]
            lines.append(_line(pts, p.hair, scale))
    # Stricter than the lint, which judges the rendered mask: the hairlines run close to the door's rear
    # shut line's lean.
    edges = geom.edges(kit, margin=3.0, run_margin=10.0)
    layers = {
        DGREY: [ln.intersection(black).difference(keep_out) for ln in lines],
        GREY: [ln.intersection(white).difference(keep_out) for ln in lines],
    }
    layers = {c: [g for g in gs if not g.is_empty] for c, gs in layers.items()}
    return [geom.fill(kit, f"hair.{view}", view, layers, edges=edges, z=2)]


def _window_pin(kit, view: str, p: Params) -> Element:
    """A green strip `pin` px tall under the side window's lower edge, nose to tail."""
    line = kit.feature(f"window_line_{view[-1]}").geometry()

    # `pin` px at each point of the line, at the door's texture scale there (the door ends at u 0.6).
    def d(u: float, v: float) -> float:
        return p.pin / carspace.px_per_unit(kit, view, (min(u, 0.6), min(v, 0.93) - 0.03))[1]

    pts = [(u, v - d(u, v)) for u, v in line.coords]
    pts = [(0.27, pts[0][1]), *pts, (1.3, pts[-1][1]), (1.3, 1.4), (0.27, 1.4)]
    return shape(kit, f"window-pin.{view}", view, pts, GREEN, z=3)


def _number_box(kit, view: str) -> Polygon:
    return shapely.box(*kit.feature(f"number_{view[-1]}").box)


def _plates(kit, p: Params) -> list[Element]:
    """A black plate keyed in green round each number the sim draws on white: the right door's and the
    bonnet's; and one round the rear face's, on the black tail, keyed the same."""
    out = []
    zones = [z["box"] for z in carspace.kit_data(kit).get("keep_clear", []) if z["kind"] == "number"]
    for part in ("door_r", "bonnet_skin", "rear_face"):
        area = body.region(kit, part)
        for x0, y0, x1, y1 in zones:
            if not area.contains(Point((x0 + x1) / 2, (y0 + y1) / 2)):
                continue
            pad, key = 10.0, 14.0
            raw = "".join(
                f'<rect x="{x0 - r}" y="{y0 - r}" width="{x1 - x0 + 2 * r}" height="{y1 - y0 + 2 * r}" '
                f'fill="{c}"/>'
                for r, c in ((pad + key, GREEN), (pad, BLACK))
            )
            out.append(clip_to(kit, part, svg(raw, f"plate-{part}", z=5)))
    return out


def _side(kit, view: str, p: Params) -> list[Element]:
    left = view == "side_l"
    front = _bolt(kit, p)
    black = front if left else shapely.box(-0.3, -0.3, 1.3, 1.3).difference(front)
    ink_front = WHITE if left else BLACK  # marks in the field ahead of and below the bolt
    ink_back = BLACK if left else WHITE
    els = geom.outline(kit, f"bolt-pin.{view}", view, front, [(p.pin, GREEN)], place="centred", z=3)
    els.append(shape(kit, f"field.{view}", view, list(black.exterior.coords)[:-1], BLACK, z=1))
    els.append(_window_pin(kit, view, p))
    els += _hairlines(kit, view, front, _number_box(kit, view), p)

    # Green chevrons on the black front fender: behind the arch on the left (below the shoulder), ahead
    # of it in the shoulder strip on the right.
    mid = p.chev_mid if left else 0.745
    half = p.chev_half if left else 0.045
    els.append(
        patterns.chevrons(
            kit,
            f"chev.{view}",
            view,
            p.chev_u0 if left else 0.3,
            p.chev_n,
            p.chev_step,
            half,
            p.chev_t,
            GREEN,
            p.lean * 2,
            mid=mid,
            z=4,
        )
    )

    # One type system on the rear door and haunch, in the field's opposite colour: zentiq, the designation,
    # and under it the code lines in Space Mono on its start edge (its nose end on the left, as read; its
    # tail end on the right, where the side reads tail to nose).
    els.append(
        sponsor(
            kit,
            f"head.{view}",
            view,
            "zentiq",
            (p.head_u, p.head_v),
            ink_back,
            height=p.head_h,
            variant="wordmark",
            z=6,
        )
    )
    half_w = p.head_h * sponsors.load("zentiq").variant("wordmark").aspect / 2
    cap = p.head_h / carspace.side_aspect(kit) / 2  # the wordmark's half height, in v
    els += type_block(
        kit,
        f"code.{view}",
        view,
        [Line("ZQ-26", p.code_size, MONO, 700), Line("720S GT3 EVO", 0.0165, MONO, 700)],
        (p.head_u - half_w if left else p.head_u + half_w, p.head_v - cap - p.code_gap),
        ink_back,
        shear=12.0,
        z=6,
    )
    # The sill row under the number, in the black (left) or white (right) lower field: one wordmark on the
    # panel behind the front wheel, one on the door, the gap between them on the door's front edge, which
    # would cut a letter.
    edge = kit.feature(f"door_front_edge_{view[-1]}").geometry()
    seam_u = edge.intersection(LineString([(0.0, p.sill_row_v), (1.0, p.sill_row_v)])).centroid.x
    for (brand, variant), (u0, u1) in zip(
        SILL, ((0.31, seam_u - p.sill_gap), (seam_u + p.sill_back, 0.515)), strict=True
    ):
        els += sponsor_group(
            kit,
            f"sill-{brand}.{view}",
            view,
            [(brand, variant)],
            (u0, p.sill_row_v),
            (u1, p.sill_row_v),
            p.sill_h,
            ink_front,
            z=6,
        )
    els += sponsor_group(
        kit,
        f"duct.{view}",
        view,
        DUCT,
        (0.655, p.sill_row_v),
        (0.77, p.sill_row_v),
        0.024,
        ink_front,
        gap=0.25,
        z=6,
    )
    # A stack behind the front arch, and the rear bumper's corner.
    if not left:
        els += sponsor_group(
            kit, f"arch.{view}", view, ARCH, (0.33, 0.6), (0.365, 0.3), 0.026, ink_front, axis="column", z=6
        )
    els += sponsor_group(
        kit, f"rear.{view}", view, REAR, (0.968, 0.72), (0.968, 0.45), 0.022, ink_back, axis="column", z=6
    )
    els += sponsor_group(
        kit,
        f"nose.{view}",
        view,
        NOSE,
        (-0.005, 0.33),
        (-0.005, 0.09),
        0.02,
        ink_front,
        axis="column",
        z=6,
    )
    els.append(sponsor(kit, f"mirror.{view}", view, "bellcrest", (0.374, 1.0), BLACK, height=0.012, z=6))
    # Race markings: the tow arrow at the nose, the extinguisher E by the mirror.
    els.append(marks(kit, f"tow.{view}", "tow", (view, 0.968, 0.33), ink_back, 0.016, toward="tail", z=6))
    els.append(marks(kit, f"ext.{view}", "extinguisher", (view, 0.43, 0.75), ink_back, 0.018, z=6))
    return els


# --- the top ------------------------------------------------------------------------------------------


def _mark_poly(kit, p: Params) -> Polygon:
    """The mark on top, `roof_h` long, reading from the front: its up towards the tail, its right (as
    the viewer at the nose sees it) on the car's left."""
    su, sw = carspace.px_per_unit(kit, "top")
    s = p.roof_h / 100.0
    return Polygon([(p.roof_u + (-y - 50.0) * s, -(x - 32.785) * s * su / sw) for x, y in MARK])


def _roof_mark(kit, p: Params) -> list[Element]:
    """The Zentiq mark across the roof, reading from the front, as WRT draws its 46: its counter-fill
    swaps the roof's halves (white on the black left, black on the white right) inside a green outline."""
    mark = _mark_poly(kit, p)
    els = geom.outline(kit, "roof-mark", "top", mark, [(p.pin, GREEN)], z=4)
    lhalf = mark.intersection(shapely.box(-1, -2, 2, 0))
    rhalf = mark.intersection(shapely.box(-1, 0, 2, 2))
    els.append(
        clip_to(kit, "roof", geom.fill(kit, "roof-mark-fill", "top", {WHITE: [lhalf], BLACK: [rhalf]}, z=4))
    )
    return els


def _top_hairlines(kit, keep_out, p: Params) -> Element:
    """The sides' hairlines on top: along the car, densest at the centreline split and opening out
    towards each side, in the other field's tone, swapping with the fields at the roof / cover seam."""
    su, sw = carspace.px_per_unit(kit, "top")
    black = shapely.union_all([shapely.box(-0.3, -1.3, SEAM_U, 0.0), shapely.box(SEAM_U, 0.0, 1.3, 1.3)])
    white = shapely.box(-0.3, -1.3, 1.3, 1.3).difference(black)
    lines, d, g = [], (p.pin / 2 + p.hair_gap) / sw, p.hair_g0 * 3
    for _ in range(p.hair_n):
        d += g
        g *= p.hair_r
        for sign in (-1, 1):
            w = sign * d
            lines.append(_line([(-0.3, w), (1.3, w)], p.hair, (su, sw)))
    layers = {
        DGREY: [g for ln in lines if not (g := ln.intersection(black).difference(keep_out)).is_empty],
        GREY: [g for ln in lines if not (g := ln.intersection(white).difference(keep_out)).is_empty],
    }
    el = geom.fill(kit, "hair.top", "top", layers, edges=geom.edges(kit), z=2)
    return clip_to(kit, FRONT_TOP + REAR_TOP, el)


def _top(kit, p: Params) -> list[Element]:
    """The top: split on the centreline, black left and white right ahead of the roof / cover seam,
    swapped behind it; the right side's skin, black above its bolt, black from above too."""
    els = []
    left = [(-0.3, -1.3), (SEAM_U, -1.3), (SEAM_U, 0.0), (-0.3, 0.0)]
    els.append(clip_to(kit, FRONT_TOP, shape(kit, "top-front", "top", left, BLACK, z=0)))
    right = [(-0.3, 0.0), (1.3, 0.0), (1.3, 1.3), (-0.3, 1.3)]
    els.append(clip_out(kit, FRONT_TOP + NOSE_R, shape(kit, "top-right", "top", right, BLACK, z=0)))
    # The pinline: down the centreline, across the seam, and round the bonnet's outline.
    su, sw = carspace.px_per_unit(kit, "top")
    h = p.pin / 2 / sw
    spine = [(-0.3, -h), (1.3, -h), (1.3, h), (-0.3, h)]
    els.append(clip_to(kit, FRONT_TOP + REAR_TOP, shape(kit, "spine", "top", spine, GREEN, z=5)))
    seam = [(SEAM_U - p.pin / su, -1.0), (SEAM_U, -1.0), (SEAM_U, 1.0), (SEAM_U - p.pin / su, 1.0)]
    els.append(clip_to(kit, "roof", shape(kit, "seam-pin", "top", seam, GREEN, z=3)))
    els += geom.outline(
        kit, "bonnet-ring", None, "bonnet_skin", [(p.pin, GREEN)], "inside", sides="outer", z=3
    )
    els += _roof_mark(kit, p)
    # The lockup across the bonnet's nose on a black plate.
    nose_plate = [(0.012, -0.3), (0.09, -0.3), (0.09, 0.3), (0.012, 0.3)]
    els += [
        clip_to(kit, "bonnet_skin", e)
        for e in geom.outline(kit, "bonnet-plate", "top", nose_plate, [(p.pin, GREEN)], z=5)
    ]
    els.append(clip_to(kit, "bonnet_skin", shape(kit, "bonnet-plate", "top", nose_plate, BLACK, z=5)))
    els.append(sponsor(kit, "bonnet-lockup", "top", "zentiq", (0.051, 0.0), GREEN, height=0.028, z=6))
    # zentiq for the chase cam, reading from behind, on a black plate across the cover.
    u0, u1 = p.cover_u - 0.025, p.cover_u + 0.025
    plate = [(u0, -0.3), (u1, -0.3), (u1, 0.3), (u0, 0.3)]
    els += [
        clip_to(kit, "engine_cover", e)
        for e in geom.outline(kit, "cover-plate", "top", plate, [(p.pin, GREEN)], z=5)
    ]
    els.append(clip_to(kit, "engine_cover", shape(kit, "cover-plate", "top", plate, BLACK, z=5)))
    els.append(
        sponsor(
            kit,
            "cover",
            "top",
            "zentiq",
            (p.cover_u, 0.0),
            GREEN,
            height=0.026,
            variant="wordmark",
            facing="rear",
            z=6,
        )
    )
    keep_out = shapely.union_all(
        [
            Polygon(nose_plate).buffer(0.02),
            Polygon(plate).buffer(0.02),
            _mark_poly(kit, p).buffer(0.02),
            shapely.box(SEAM_U - 0.03, -2.0, SEAM_U + 0.015, 2.0),
        ]
    )
    els.append(_top_hairlines(kit, keep_out, p))
    return els


def build(kit, p: Params):
    els: list[Element] = []
    for view in kit.side_views:
        els += _side(kit, view, p)
    els += _top(kit, p)
    els += _plates(kit, p)
    # Hardware: black lower bumper and sills both sides, green splitter, mirrors, endplates, intake edges.
    # The nose: each bumper corner (mapped by no view, so drawn in its own texture, its foot at the
    # bottom) in its side's nose colour over a chin of black-and-green hazard along its foot, so the black
    # lower body reaches the splitter on both sides.
    for part, upper in (("front_bumper_corner_l", BLACK), ("front_bumper_corner_r", WHITE)):
        area = body.region(kit, part)
        x0, _, x1, y1 = area.bounds
        els.append(patterns.hazard(kit, f"chin-{part}", part, (BLACK, GREEN), p.haz_px, -30.0, duty=0.4, z=1))
        top = area.difference(shapely.box(x0 - 1, y1 - p.chin_px, x1 + 1, y1 + 1))
        els.append(geom.paint(f"bumper-{part}", top, upper, z=2))
        rim = area.intersection(shapely.box(x0 - 1, y1 - p.chin_px - p.pin, x1 + 1, y1 - p.chin_px))
        els.append(geom.paint(f"chin-pin-{part}", geom.clean(rim), GREEN, z=2))
    for part, color, z in (
        ("front_bumper_l", BLACK, 0),
        ("sill_l", BLACK, 2),
        ("sill_r", BLACK, 2),
        ("intake_strakes", BLACK, 2),
        ("splitter", GREEN, 4),
        ("mirrors", GREEN, 4),
        ("wing_endplates", GREEN, 4),
        ("intake_edge_l", GREEN, 4),
        ("intake_edge_r", GREEN, 4),
        ("rear_face", BLACK, 1),
        ("rear_valance", BLACK, 1),
        ("number_panels", BLACK, 1),  # never seen; black so the sim's white numbers there have contrast
    ):
        els.append(panel(kit, f"part-{part}", part, color, z=z))
    els += geom.outline(kit, "tail-ring", None, "rear_face", [(p.pin, GREEN)], "inside", sides="outer", z=2)
    els.append(panel(kit, "wing", "wing", BLACK, z=1))
    return base(WHITE).add(*els)
