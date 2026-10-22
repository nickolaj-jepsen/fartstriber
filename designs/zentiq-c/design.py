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

import numpy as np
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
NOSE_R = ("front_bumper_r", "splitter", "grille_header", "crash_opening", "front_panel_r")
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
SILL = (("huilec", "wordmark"),)  # under the number, ahead of the door's front edge
DOOR = (("ostrand", "mark"),)  # and behind it
DUCT = (
    ("grivant", "wordmark"),
)  # behind the side intake, by the rear wheel: one wordmark is all 0.115 u holds
ARCH = (("drexa", "mark"), ("hexmoor", "mark"))
REAR = (("rigmount", "mark"), ("avelor", "mark"))
HAUNCH = (("altmere", "wordmark"),)  # over the rear arch: one wordmark is what the haunch's curve holds
SPLITTER = "hexmoor"  # its mark on each splitter corner


@dataclass(frozen=True)
class Params(DesignParams):
    lean: float = param(20.0, 5.0, 35.0)  # the bolt's diagonal, degrees from upright, top to the tail
    shoulder_v: float = param(0.75, 0.55, 0.77)  # the Z's top bar
    sill_v: float = param(0.26, 0.12, 0.32)  # the Z's bottom bar, above the side intake
    diag_u: float = param(0.515, 0.47, 0.56)  # the upper diagonal's u at jog_v
    jog_v: float = param(0.44, 0.3, 0.55)
    jog: float = param(0.04, 0.0, 0.06)  # the lower diagonal starts this far towards the tail
    pin: float = param(18.0, 14.0, 30.0)  # the green pinline, texture px
    # Hairlines: parallel to the diagonal, the first `hair_g0` from it, each gap `hair_r` times the last.
    hair: float = param(11.0, 6.0, 14.0)
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
    sill_h: float = param(0.023, 0.014, 0.03)
    sill_gap: float = param(0.01, 0.004, 0.03)  # u the fender's sill wordmark stays off the door's front edge
    # u the door's stays behind it: the panel ahead stands proud of the door and hides its start from front34.
    sill_back: float = param(0.03, 0.004, 0.05)
    door_end: float = param(0.49, 0.47, 0.515)  # the door's marks end ahead of the crease above the blade
    roof_h: float = param(0.18, 0.1, 0.26)  # the roof mark's height along the car
    roof_u: float = param(0.495, 0.42, 0.55)
    cover_u: float = param(0.8, 0.7, 0.85)
    arch_u: float = param(0.322, 0.31, 0.335)  # the left's mark above the chevrons
    arch_v: float = param(0.5, 0.44, 0.56)
    mirror_u: float = param(0.374, 0.36, 0.39)
    mirror_h: float = param(0.012, 0.008, 0.018)
    wing_u: float = param(1.04, 0.98, 1.1)  # zentiq across the wing's top, reading from behind
    wing_h: float = param(0.05, 0.03, 0.08)
    edge_over: float = param(
        5.0, 0.0, 12.0
    )  # px the pin up the fender's rear edge reaches over the shut line
    # The row over the left rear arch, between the haunch's crease (lint `crease`) and the arch.
    over_u0: float = param(0.745, 0.72, 0.83)
    over_u1: float = param(0.845, 0.82, 0.95)
    over_v: float = param(0.81, 0.76, 0.88)
    over_h: float = param(0.019, 0.012, 0.025)
    tow_v: float = param(0.33, 0.25, 0.4)
    splitter_u: float = param(0.0125, 0.005, 0.02)
    splitter_h: float = param(0.011, 0.008, 0.014)
    arch_gap: float = param(0.02, 0.005, 0.04)  # u the sill wordmark stays behind the front arch
    haunch_u: float = param(0.6, 0.55, 0.64)  # behind here the window pin follows the side map's top
    haunch_px: float = param(8.0, 0.0, 20.0)  # how far under it, texture px
    haunch_k: float = param(1.2, 1.0, 1.8)  # the pin's height there, times its height on the door
    pinch_u: float = param(0.555, 0.5, 0.6)  # where that starts, at the B-pillar's foot
    # Hairlines on each front fender's top, as the top view maps it: raked like the door's, densest at
    # the fender's rear edge and opening out towards the headlight.
    fh_g0: float = param(0.014, 0.008, 0.03)  # u from the rear edge to the first line, at the crest
    fh_n: int = param(10, 3, 14)
    fh_px: float = param(14.0, 8.0, 20.0)  # their width, texture px: the fender top is foreshortened
    fh_lean: float = param(-30.0, -60.0, 0.0)  # degrees as seen from above; negative: the outer end forward


# --- the bolt -----------------------------------------------------------------------------------------


def _fender_edge(kit, view: str):
    """u of the front fender's rear edge (the shut line to the door) at v, the feature's line extended."""
    (u0, v0), (u1, v1) = list(kit.feature(f"fender_rear_edge_{view[-1]}").geometry().coords)[:2]
    return lambda v: u0 + (u1 - u0) * (v - v0) / (v1 - v0)


def _bolt(kit, p: Params, view: str = "side_l") -> Polygon:
    """The field ahead of and below the Z on a side, in car space. The whole front fender is in it: the Z's
    top bar starts at the fender's rear edge, half a pin behind the shut line, so the centred pin there
    runs from the shut line back onto the door. On the fender crest the side and top views share the
    texels in a stepped boundary, and a field edge or pin there tore (sim runs 20261021-200211 to -212041)."""
    k = geom.rake(kit, p.lean)
    edge = _fender_edge(kit, view)
    half = p.pin / 2 / carspace.px_per_unit(kit, view, (0.29, p.shoulder_v))[0]

    def up(v):
        return p.diag_u + k * (v - p.jog_v)

    def lo(v):
        return p.diag_u + p.jog + k * (v - p.jog_v)

    return Polygon(
        [
            (-0.3, -0.3),
            (-0.3, 1.3),
            (edge(1.3) + half, 1.3),
            (edge(p.shoulder_v) + half, p.shoulder_v),
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


def _side_top(kit, view: str, us) -> list[float | None]:
    """At each u, the highest v the side's own map paints on the door / haunch skin: above it the top
    view owns the texels."""
    isl = body.part(kit, f"door_{view[-1]}").islands
    pcs = [pc for pc in carspace.pieces(kit, view) if pc.island in isl]

    def painted(u: float, v: float) -> bool:
        return any(
            pc.covers_car((u, v)) and pc.area.contains(Point(*pc.to_px(np.array([[u, v]]))[0])) for pc in pcs
        )

    return [next((float(v) for v in np.arange(1.05, 0.7, -0.0025) if painted(u, v)), None) for u in us]


def _lower_hull(us: list[float], vs: list[float]) -> list[float]:
    """The lower convex hull of the points, sampled at `us`: a curve under every point with no dips."""
    hull: list[int] = []
    for i in range(len(us)):
        while len(hull) >= 2:
            a, b = hull[-2], hull[-1]
            cross = (us[b] - us[a]) * (vs[i] - vs[a]) - (vs[b] - vs[a]) * (us[i] - us[a])
            if cross > 0:
                break
            hull.pop()
        hull.append(i)
    return [float(np.interp(u, [us[i] for i in hull], [vs[i] for i in hull])) for u in us]


def _window_pin(kit, view: str, p: Params) -> list[tuple[float, float]]:
    """A green strip `pin` px tall under the side window's lower edge, nose to tail, in car space. Behind
    the window (from `haunch_u`) it runs along the haunch `haunch_px` under the side map's upper edge, a
    smoothed lower envelope of it: there the top view owns the haunch tops, and a strip on the boundary
    showed from above as green fragments."""
    line = kit.feature(f"window_line_{view[-1]}").geometry()

    # `pin` px at each point of the line, at the door's texture scale there (the door ends at u 0.6).
    def d(u: float, v: float) -> float:
        return p.pin / carspace.px_per_unit(kit, view, (min(u, 0.6), min(v, 0.93) - 0.03))[1]

    def line_v(u: float) -> float:
        cut = line.intersection(LineString([(u, -1.0), (u, 2.0)]))
        return 1.0 if cut.is_empty else cut.centroid.y

    us = [p.haunch_u + 0.01 * i for i in range(int((1.0 - p.haunch_u) / 0.01))]
    reach = _side_top(kit, view, us)
    n = next((i for i, r in enumerate(reach) if r is None), len(reach))
    us, reach = us[:n], [float(r) for r in reach[:n] if r is not None]
    # A narrow minimum, so the line only falls where the haunch does (behind u 0.88), not ahead of it.
    env = [min(reach[max(0, i - 1) : i + 2]) for i in range(n)]
    # Rising to the haunch's crown, the line runs on the lower convex hull of the map's edge: the left's
    # edge dips at u 0.64-0.68 (0.03 v), and a line following it wobbled there (sim run 20261021-221929).
    # Behind the crown it falls with the tail, smoothed.
    crown = int(np.argmax(env))
    fall = [float(np.mean(env[max(crown, i - 2) : i + 3])) for i in range(crown, n)]
    curve = _lower_hull(us[: crown + 1], env[: crown + 1])[:-1] + fall
    curve = [float(np.mean(curve[max(0, i - 1) : i + 2])) for i in range(n)]
    curve = [min(c, e) for c, e in zip(curve, env, strict=True)]

    # haunch_px in v: d is `pin` px in v there
    # The offset ramps in from a quarter at `haunch_u`: at the B-pillar's foot the full one left a white
    # sliver between the pin and the black intake above it.
    def off(u: float) -> float:
        return p.haunch_px * min(1.0, max(0.25, (u - p.haunch_u) / 0.05))

    haunch = [(u, min(line_v(u), e - d(u, e) * off(u) / p.pin)) for u, e in zip(us, curve, strict=True)]
    ahead = [(u, v) for u, v in line.coords if u < p.haunch_u]
    top = [(0.27, ahead[0][1]), *ahead, *haunch]

    # From the B-pillar's foot back the pin is `haunch_k` times as tall, ramping in from `pinch_u` over
    # 0.03 u: at one height in car space it showed two thirds as wide on screen there as on the door
    # (8-10 px against 13-16 px in side_l, sim runs 20261021-221929 and -224139), on both sides.
    def k(u: float) -> float:
        return 1.0 + (p.haunch_k - 1.0) * min(1.0, max(0.0, (u - p.pinch_u) / 0.03))

    return top + [(u, v - d(u, v) * k(u)) for u, v in reversed(top)]


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
    front = _bolt(kit, p, view)
    black = front if left else shapely.box(-0.3, -0.3, 1.3, 1.3).difference(front)
    ink_front = WHITE if left else BLACK  # marks in the field ahead of and below the bolt
    ink_back = BLACK if left else WHITE
    panel_ = f"front_panel_{view[-1]}"  # painted whole in its field's colour (`build`): no pin on it
    els = [
        clip_out(kit, panel_, e)
        for e in geom.outline(kit, f"bolt-pin.{view}", view, front, [(p.pin, GREEN)], place="centred", z=3)
    ]
    # The pin up the fender's rear edge reaches a little over the shut line onto the fender, where the
    # front panel's cut takes it back to the island's own edge: no sliver of field between edge and pin.
    edge = _fender_edge(kit, view)
    scale = carspace.px_per_unit(kit, view, (0.29, p.shoulder_v))[0]
    a, b = p.edge_over / scale, p.pin / 2 / scale
    vs = (p.shoulder_v, 1.2)
    over = [
        (edge(vs[0]) - a, vs[0]),
        (edge(vs[0]) + b, vs[0]),
        (edge(vs[1]) + b, vs[1]),
        (edge(vs[1]) - a, vs[1]),
    ]
    els.append(clip_out(kit, panel_, shape(kit, f"edge-pin.{view}", view, over, GREEN, z=3)))
    els.append(shape(kit, f"field.{view}", view, list(black.exterior.coords)[:-1], BLACK, z=1))
    els.append(
        clip_out(kit, panel_, shape(kit, f"window-pin.{view}", view, _window_pin(kit, view, p), GREEN, z=3))
    )
    els += _hairlines(kit, view, front, _number_box(kit, view), p)

    # Green chevrons on the left's black panel behind the front wheel. The right's black above the bar
    # is only the thin shoulder strip on the door, between two pins: no room for them there.
    if left:
        els.append(
            patterns.chevrons(
                kit,
                f"chev.{view}",
                view,
                p.chev_u0,
                p.chev_n,
                p.chev_step,
                p.chev_half,
                p.chev_t,
                GREEN,
                p.lean * 2,
                mid=p.chev_mid,
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
    # The sill row under the number, in the black (left) or white (right) lower field: a wordmark on the
    # panel behind the front wheel, two marks on the door, the gap between them on the door's front edge,
    # which would cut a letter. The panel ahead stands proud of the door and hides its first 0.01 u from
    # front34, so the door's marks start `sill_back` behind the edge.
    edge = kit.feature(f"door_front_edge_{view[-1]}").geometry()
    seam_u = edge.intersection(LineString([(0.0, p.sill_row_v), (1.0, p.sill_row_v)])).centroid.x
    # The front arch's rear edge beside the row: the sill wordmark starts `arch_gap` behind it, clear of
    # the wheel and the arch's flare, which side_r showed cutting its last letter at 0.008.
    arch = kit.feature(f"front_arch_{view[-1]}").geometry()
    arch_u = max(u for u, v in arch.coords if v <= p.sill_row_v + 0.1)
    for i, (group, (u0, u1)) in enumerate(
        zip(
            (SILL, DOOR),
            ((arch_u + p.arch_gap, seam_u - p.sill_gap), (seam_u + p.sill_back, p.door_end)),
            strict=True,
        )
    ):
        els += sponsor_group(
            kit,
            f"sill{i}.{view}",
            view,
            group,
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
    else:  # above the chevrons, where the door's front edge has leaned back to u 0.346
        els.append(
            sponsor(
                kit,
                f"arch.{view}",
                view,
                "telvio",
                (p.arch_u, p.arch_v),
                WHITE,
                height=0.022,
                variant="mark",
                z=6,
            )
        )
    els += sponsor_group(
        kit, f"rear.{view}", view, REAR, (0.968, 0.72), (0.968, 0.45), 0.022, ink_back, axis="column", z=6
    )
    # A partner over the left rear arch, between the haunch's crease and the arch. The right haunch has no
    # such place: its crease sits lower (lint `crease` 33-37% at u 0.78-0.85), and behind u 0.86 the
    # haunch pin falls with the tail onto the letters.
    if left:
        els += sponsor_group(
            kit,
            f"haunch.{view}",
            view,
            HAUNCH,
            (p.over_u0, p.over_v),
            (p.over_u1, p.over_v),
            p.over_h,
            ink_back,
            z=6,
        )
    els.append(
        sponsor(
            kit,
            f"mirror.{view}",
            view,
            "norvane",
            (p.mirror_u, 1.0),
            BLACK,
            height=p.mirror_h,
            variant="mark",
            z=6,
        )
    )
    # Race markings: the tow arrow at the nose, the extinguisher E by the mirror.
    els.append(marks(kit, f"tow.{view}", "tow", (view, 0.968, p.tow_v), ink_back, 0.016, toward="tail", z=6))
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


def _fender_hairlines(kit, p: Params) -> list[Element]:
    """The door's hairlines carried onto each front fender's top, which the top view maps (the side maps
    only a strip over the arch, where they were too short to keep): raked, the first `fh_g0` ahead of the
    fender's rear edge and each gap `hair_r` times the last, so they open out towards the headlight as
    the door's open out from the bolt. In the other field's tone: dark grey on the left's black, grey on
    the right's white."""
    su, sw = carspace.px_per_unit(kit, "top")
    kk = geom.rake(kit, p.fh_lean, "top")
    out = []
    for side, color in (("l", DGREY), ("r", GREY)):
        sign = -1.0 if side == "l" else 1.0
        rear = min(u for u, _ in kit.feature(f"fender_rear_edge_{side}").geometry().coords)
        lines, d, g = [], 0.0, p.fh_g0
        for _ in range(p.fh_n):
            d += g
            g *= p.hair_r
            c = rear - d  # the line's u at |w| 0.5
            pts = [(c + kk * (a - 0.5), sign * a) for a in (0.15, 0.8)]
            lines.append(_line(pts, p.fh_px, (su, sw)))
        el = geom.fill(kit, f"hair.fender_{side}", "top", {color: lines}, edges=geom.edges(kit), z=2)
        out.append(clip_to(kit, f"front_panel_{side}", el))
    return out


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
    # And across the wing's top, as WRT carries GEN there, for the chase cam and the overhead shots.
    els.append(
        clip_to(
            kit,
            "wing_top",
            sponsor(
                kit,
                "wing-word",
                "top",
                "zentiq",
                (p.wing_u, 0.0),
                GREEN,
                height=p.wing_h,
                variant="wordmark",
                facing="rear",
                z=6,
            ),
        )
    )
    # A partner on the green splitter, black, reading from the front. The top view sees the splitter's upper face only at its corners (|w| 0.3-0.48; the bonnet hides
    # the rest), so the front's partner is a mark on each corner.
    for side in (-1, 1):
        els.append(
            clip_to(
                kit,
                "splitter_top",
                sponsor(
                    kit,
                    f"splitter{side}",
                    "top",
                    SPLITTER,
                    (p.splitter_u, side * 0.42),
                    BLACK,
                    height=p.splitter_h,
                    variant="mark",
                    z=6,
                ),
            )
        )
    keep_out = shapely.union_all(
        [
            Polygon(nose_plate).buffer(0.02),
            Polygon(plate).buffer(0.02),
            _mark_poly(kit, p).buffer(0.02),
            shapely.box(SEAM_U - 0.03, -2.0, SEAM_U + 0.015, 2.0),
            shapely.box(-0.3, 0.0, 0.2, 2.0),  # the bonnet's right half, black as a whole (`build`)
        ]
    )
    els.append(_top_hairlines(kit, keep_out, p))
    els += _fender_hairlines(kit, p)
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
        # Each front fender (skin, nose piece, slats) wholly in its side's nose colour, from the side and
        # from above alike: the side and top views share its crest, and an edge there tore.
        ("front_panel_l", BLACK, 1),
        ("front_panel_r", WHITE, 1),
        # The vent is an opening: black as a whole, so its walls, which no view maps, don't show white in
        # stepped pieces beside the bonnet's black half.
        ("bonnet_vent", BLACK, 1),
        # The bonnet's right half is the number's black plate but for a sliver at its rear corner, whose
        # white met the plate in a stepped edge in the nose crop: the bonnet is black as a whole.
        ("bonnet_skin", BLACK, 0),
        # One island drawn on both sides (u 0.131-0.143, v 0.63-0.70, under the shoulder bar), so it can't
        # take each side's field: black as the vent it is, never white in the left's black field.
        ("fender_arch_vent", BLACK, 2),  # under the shoulder pin, which crosses its top
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
