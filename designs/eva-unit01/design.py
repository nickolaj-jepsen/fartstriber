"""Evangelion Unit-01: a violet car in angular acid-green armour, layered in tones, strokes and detail.

50 m: violet with green armour plates (front fenders, bonnet centre, a lightning-cut door plate, the rear
haunches, the engine cover) and an orange-and-black hazard band along the lower body, arch to arch.
10 m: a darker violet in large facets (the lower body, the segment between the door and haunch plates,
the bonnet, the bumper sides, the roof, the rear deck, the wing); every green plate is stroked
near-black then orange, and so is the bonnet where it meets the green fenders; behind the fender's raked
end a run of thin orange slashes climbs the door, each shorter; an orange pinline traces the dark lower
body; the hazard runs across the nose through the grille; the car's features in the accent (orange
mirrors).
1 m: a tone-on-tone hairline inside the door plate, and small tactical markings in groups.
Every slash and cut leans the same way, top towards the tail. No logos.
"""

import math
from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon

from livery import body, carspace, geom, patterns
from livery.geom import grow, rake, segment
from livery.model import Element
from livery.params import DesignParams, param
from livery.primitives import base, clip_to, panel, shape, svg, text
from livery.typeset import Line, type_block

KIT = "mclaren-720s-gt3-evo"
VIOLET, VIOLET_DK = "#4b2a7b", "#1d0a3a"
GREEN, GREEN_DK = "#8ee53f", "#5c9e2a"
ORANGE, INK = "#f46200", "#16121c"
FONT = "Barlow Condensed"
REAR_ARCH = 0.88  # u inside the rear wheel arch, where the hazard band ends
DOOR = (0.6, 0.5)  # where on the left door skin car-space widths are measured, for both sides
ROOF = "i102"  # the roof's front panel: the part `roof` also takes i078's pillars and window frames
# The front bumper's sides and the grille uprights, dark; the corners carry a green trim.
BUMPER = ("front_bumper_corner_l", "front_bumper_corner_r", "grille_upright_l", "grille_upright_r")
# The number plates: the part each of the sim's number boxes sits on, and whether it gets a keyline.
NUMBERS = (("door_l", True), ("door_r", True), ("rear_face", False))


@dataclass(frozen=True)
class Params(DesignParams):
    # Strokes, template px: a near-black keyline then an orange pinline around every green plate.
    keyline: float = param(16.0, 14.0, 24.0)
    pin: float = param(14.0, 14.0, 20.0)
    hair: float = param(10.0, 4.0, 12.0)  # tone-on-tone hairlines: thinner ones are under 8 px on screen
    rule_px: float = param(10.0, 6.0, 16.0)  # the rule under the door's EVA-01
    # Bonnet: the green centre plate, in |w| from the spine, wide at the nose and narrower at the hump.
    plate_w0: float = param(0.12, 0.05, 0.4)
    plate_w1: float = param(0.08, 0.05, 0.4)
    plate_tip: float = param(0.01, -0.01, 0.06)
    plate_point: float = param(0.03, 0.0, 0.08)
    plate_u1: float = param(0.16, 0.1, 0.45)
    echo_gap: float = param(22.0, 10.0, 40.0)  # px between the plate's stroke and its green echo
    echo: int = param(0, choices=(0, 1))
    # The bonnet's dark outer facets: their inner edge runs from |w| facet_w0 at the nose to facet_w1.
    facet_w0: float = param(0.27, 0.15, 0.4)
    facet_w1: float = param(0.17, 0.1, 0.35)
    # Where the bonnet's outline meets the fenders: inside u seam_u, beyond |w| seam_w on top.
    seam_u0: float = param(-0.05, -0.1, 0.1)
    seam_u1: float = param(0.3, 0.2, 0.4)
    seam_w: float = param(0.2, 0.0, 0.4)
    rim_px: float = param(24.0, 14.0, 40.0)  # the engine cover's orange trim
    rim_key: float = param(24.0, 14.0, 40.0)  # and the near-black outside it
    trim_gap: float = param(14.0, 14.0, 30.0)  # px of dark between a bumper corner's edge and its trim
    trim_px: float = param(18.0, 14.0, 30.0)  # and the trim's width
    # The hazard band along each bumper corner's foot, texture px tall (0: none), the trim above it.
    bump_haz: float = param(50.0, 0.0, 90.0)
    bump_lean: float = param(-30.0, -45.0, 45.0)
    strake_tone: int = param(2, choices=(0, 1, 2))  # the intake strakes: violet, dark violet, near-black
    wing_h: float = param(0.13, 0.08, 0.3)  # the wing stripe's half height, a share of the wing's chord
    # The lean shared by every slash: the haunch edge's angle on the side, top towards the tail.
    lean_deg: float = param(25.0, 10.0, 40.0)
    # Rear haunch plate.
    haunch_u0: float = param(0.76, 0.6, 0.9)
    haunch_v0: float = param(0.55, 0.3, 0.8)
    line_gap: float = param(0.025, 0.005, 0.05)  # dark between the door plate and its orange line
    line_w: float = param(0.018, 0.005, 0.04)
    line_v0: float = param(0.75, 0.2, 0.85)  # where the orange line's pointed lower end finishes
    # Door plate: a green band across the door, kinked forward below door_kink_v like a lightning cut.
    door_u0: float = param(0.56, 0.5, 0.68)
    door_w: float = param(0.08, 0.02, 0.12)
    door_kink_v: float = param(0.36, 0.2, 0.6)
    door_kink: float = param(0.03, 0.0, 0.08)
    # The fender: green from a raked cut through (blade_u0, blade_v0) back to a raked end through
    # (blade_u1, blade_v0).
    blade_u0: float = param(-0.2, -0.3, 0.12)
    blade_v0: float = param(0.58, 0.4, 0.8)
    blade_u1: float = param(0.22, 0.15, 0.32)
    # The slash run behind the fender's raked end, between the front arch and the number plate: dash_n
    # thin solid slashes (u at haunch_v0, as `_lean` takes it), their tops on dash_v1, each one's foot
    # dash_rise higher than the one before, so each is shorter.
    dash_u0: float = param(0.31, 0.28, 0.36)
    dash_w: float = param(0.009, 0.006, 0.02)
    dash_gap: float = param(0.014, 0.006, 0.03)
    dash_n: int = param(5, choices=(3, 4, 5, 6))
    dash_v0: float = param(0.46, 0.4, 0.6)
    dash_v1: float = param(0.78, 0.7, 0.8)
    dash_rise: float = param(0.05, 0.0, 0.1)
    dash_tone: int = param(1, choices=(0, 1))  # green, orange
    # The dark lower body ahead of the door plate.
    skirt_v: float = param(0.4, 0.25, 0.45)
    hazard_v: float = param(0.2, 0.1, 0.3)  # the hazard band's top
    hazard_u0: float = param(0.12, -0.1, 0.12)  # its front end: ahead of 0.12 it runs onto the bumper
    hazard_px: float = param(44.0, 20.0, 80.0)  # stripe period, template px
    # Engine cover chevrons, between the scoop slot (its rear edge about u 0.69) and EVA-01: chev_n of
    # them pointing to the tail, apexes from chev_u0.
    chev_u0: float = param(0.746, 0.56, 0.78)
    chev_step: float = param(0.024, 0.015, 0.06)
    chev_w: float = param(0.1, 0.05, 0.25)
    chev_n: int = param(2, choices=(1, 2, 3))
    chev_t: float = param(0.014, 0.008, 0.03)
    chev_point: int = param(1, choices=(0, 1))  # the chevrons point to the nose, or to the tail
    chev_arm: float = param(0.025, 0.015, 0.05)  # how far along the car each arm sweeps
    cover_tag_u: float = param(0.796, 0.74, 0.82)  # EVA-01 on the cover
    # The grille opening's side walls behind the mesh: violet, near-black or hazard; and the uprights in
    # the hazard too.
    grille_tone: int = param(2, choices=(0, 1, 2))
    upright_haz: int = param(1, choices=(0, 1))
    # Roof chevrons (green).
    roof_u0: float = param(0.43, 0.25, 0.55)
    roof_step: float = param(0.04, 0.015, 0.06)
    roof_w: float = param(0.16, 0.05, 0.3)
    # The rear deck's dark facets start here.
    deck_u0: float = param(0.6, 0.5, 0.7)
    # Number plates: near-black field with an orange frame, template px.
    number_pad: float = param(16.0, 13.0, 40.0)
    tail_pad: float = param(24.0, 13.0, 40.0)
    number_frame: float = param(10.0, 4.0, 20.0)
    roof_number_px: float = param(118.0, 60.0, 140.0)  # the roof's 01, em in template px
    # Side type: the door block's front edge is the plate's front edge plus tag_margin.
    tag_margin: float = param(0.012, 0.0, 0.03)
    tag_size: float = param(0.024, 0.016, 0.04)
    tag_v: float = param(0.76, 0.6, 0.8)  # top of the door's EVA-01
    stack_gap: float = param(0.07, 0.05, 0.1)  # v from one small line's top to the next
    type_size: float = param(0.0165, 0.016, 0.03)
    small_size: float = param(0.0165, 0.016, 0.03)
    # The 01 group, ahead of the sim's door light panel: its raked front edge (u where it crosses
    # haunch_v0), the 01's top, and TYPE-01 under it.
    unit_size: float = param(0.038, 0.03, 0.08)
    unit_u: float = param(0.677, 0.64, 0.7)
    unit_v: float = param(0.66, 0.45, 0.75)
    unit_gap: float = param(0.075, 0.0, 0.12)  # v between the 01's ink and TYPE-01, past its outline


# --- geometry helpers --------------------------------------------------------------------------------


def _scale(kit, view: str) -> tuple[float, float]:
    """Template px per unit of each car-space axis: measured on the door skin for the sides."""
    return carspace.px_per_unit(kit, "top") if view == "top" else carspace.px_per_unit(kit, "side_l", DOOR)


def _lean(kit, p: Params, through: tuple[float, float] | None = None):
    """u at height v for an edge leaning with the rake that crosses haunch_v0 at u; with `through`, for
    the edge through that point, offset by u."""
    k = rake(kit, p.lean_deg)
    if through:
        return lambda u, v: through[0] + u + k * (v - through[1])
    return lambda u, v: u + k * (v - p.haunch_v0)


def _sill_islands(kit) -> set[str]:
    return set(kit.part("sill_l").islands) | set(kit.part("sill_r").islands)


def _pieces(kit, view: str):
    """The view's maps for side shapes: none on the sill strips, which are only partly mapped and are
    painted whole in hazard."""
    if view == "top":
        return None
    sills = _sill_islands(kit)
    return tuple(pc for pc in carspace.pieces(kit, view) if pc.island not in sills)


def _plate(kit, id: str, view: str, polys, color: str, z: int = 0) -> Element:
    """Car-space polygons on a view as one element, through `_pieces`, snapped clear of the near-tangent
    lint's lines as `shape` snaps them (so `geom.outline` strokes follow the same edge)."""
    if isinstance(polys, shapely.Geometry):
        union = polys
    else:
        if polys and isinstance(polys[0][0], (int, float)):
            polys = [polys]
        union = shapely.union_all([Polygon(q) for q in polys])
    foot, snaps = geom.snapped(kit, view, geom.footprint(kit, view, union, _pieces(kit, view)))
    return Element(
        id=id,
        kind="shape",
        svg=f'<path fill-rule="evenodd" d="{carspace.path_d(foot)}" fill="{color}"/>',
        z=z,
        car={"view": view},
        snaps=snaps,
    )


def _stroked(kit, id, view, polys, color, p: Params, islands=None, z: int = 2, key=None, pin=None):
    """Plates with their stroke: a near-black keyline then an orange pinline outside it, each as wide in
    the texture on every island (`geom.outline`)."""
    key = p.keyline if key is None else key
    pin = p.pin if pin is None else pin
    if isinstance(polys[0][0], (int, float)):
        polys = [polys]
    union = shapely.union_all([Polygon(q) for q in polys])
    strokes = [(w, c) for w, c in ((key, INK), (pin, ORANGE)) if w]
    out = geom.outline(kit, id, view, union, strokes, through=_pieces(kit, view), z=z)
    out.append(_plate(kit, id, view, polys, color, z))
    return [clip_to(kit, islands, e) for e in out] if islands else out


def _onto_top(kit, id, view, poly, color, p: Params, stroked=False, z: int = 2) -> list[Element]:
    """A side shape's continuation on the top view, over the up-facing part of the side skin
    (`side_*_skin`): its span where it leaves the side (v 1), straight across to the centre line."""
    cut = Polygon(poly).intersection(LineString([(-2.0, 1.0), (3.0, 1.0)]))
    if cut.is_empty:
        return []
    u0, _, u1, _ = cut.bounds
    w = -1.3 if view == "side_l" else 1.3
    rect = [(u0, 0.0), (u1, 0.0), (u1, w), (u0, w)]
    skin = f"side_{view[-1]}_skin"
    if stroked:
        return _stroked(kit, f"{id}.top", "top", rect, color, p, islands=skin, z=z)
    return [clip_to(kit, skin, _plate(kit, f"{id}.top", "top", rect, color, z))]


def _hair(kit, id, view, line, color, px: float, z: int = 3) -> Element:
    """A straight line `px` wide between two car-space points."""
    return _plate(kit, id, view, segment(*line, px, _scale(kit, view)), color, z)


def _chevron_lean(kit, half: float, arm: float = 0.035) -> float:
    """The lean (degrees as seen on top) that sweeps a chevron's arm `arm` u back over `half` w."""
    return math.degrees(math.atan(arm / half / rake(kit, 45.0, "top")))


def _chevrons(kit, u0: float, step: float, half: float, t: float = 0.012, n: int = 3):
    """The car-space polygons `patterns.chevrons` draws, for their strokes."""
    sweep = rake(kit, _chevron_lean(kit, half), "top") * half
    out = []
    for i in range(n):
        u = u0 + i * step
        out.append(
            [
                (u, 0.0),
                (u + sweep, half),
                (u + sweep + t, half),
                (u + t, 0.0),
                (u + sweep + t, -half),
                (u + sweep, -half),
            ]
        )
    return out


# --- parts --------------------------------------------------------------------------------------------


def _block(kit, id, view, string, front_top, size, color, p: Params, strokes=()) -> list[Element]:
    """One line of type raked with the shared lean, its nose-side top corner at a car-space point on both
    sides (`type_block` reads left to right as seen, so on the right side the nose is its end)."""
    anchor = (0.0, 0.0) if view == "side_l" else (1.0, 0.0)
    return type_block(
        kit,
        id,
        view,
        [Line(string, size, FONT, 800)],
        front_top,
        color,
        lean=p.lean_deg,
        anchor=anchor,
        strokes=strokes,
        z=6,
    )


def _number_plates(kit, p: Params) -> list[Element]:
    """A framed field under each of the sim's number boxes on a door and the tail: near-black, an orange
    frame and (on the doors) a near-black keyline, square in the texture as the sim draws the number."""
    out = []
    zones = [z for z in carspace.kit_data(kit).get("keep_clear", []) if z["kind"] == "number"]
    for part, keyline in NUMBERS:
        area = body.region(kit, part)
        for zone in zones:
            x0, y0, x1, y1 = zone["box"]
            if not area.contains(Point((x0 + x1) / 2, (y0 + y1) / 2)):
                continue
            pad = p.number_pad if keyline else p.tail_pad
            rings = [(pad + p.number_frame, ORANGE), (pad, INK)]
            if keyline:
                rings.insert(0, (pad + p.keyline + p.number_frame, INK))
            raw = "".join(
                f'<rect x="{x0 - r}" y="{y0 - r}" width="{x1 - x0 + 2 * r}" height="{y1 - y0 + 2 * r}" '
                f'fill="{c}"/>'
                for r, c in rings
            )
            out.append(clip_to(kit, part, svg(raw, f"number-{part}", z=4)))
    return out


def _bonnet_seam(kit, p: Params) -> list[Element]:
    """The keyline and pin between the green fenders and the bonnet's dark facets, inside the bonnet's own
    outline (its edge in the texture) where it meets each fender: the part of the outline inside a
    car-space box on each side of the top view, carried into the texture through the bonnet's top map."""
    island = kit.part("bonnet_skin").islands[0]
    pc = next(pc for pc in carspace.pieces(kit, "top") if pc.island == island)
    u0, u1 = p.seam_u0, p.seam_u1
    sel = []
    for sign in (-1, 1):
        box = shapely.box(u0, sign * p.seam_w, u1, sign * 1.0).segmentize(0.01)
        sel.append(Polygon(pc.to_px(np.asarray(box.exterior.coords))))
    strokes = [(p.keyline, INK), (p.pin, ORANGE)]
    return geom.outline(
        kit,
        "bonnet-seam",
        None,
        "bonnet_skin",
        strokes,
        "inside",
        sides="outer",
        along=shapely.union_all(sel),
        z=3,
    )


def _wing_stripe(kit, p: Params) -> list[Element]:
    """A green stripe along each wing face's middle, keylined and pinned, in the texture: the wing is
    long along its islands' x."""
    out = []
    for part in ("wing_top", "wing_bottom"):
        area = body.region(kit, part)
        x0, y0, x1, y1 = area.bounds
        cy, h = (y0 + y1) / 2, p.wing_h * (y1 - y0)
        for k, (hh, c) in enumerate(((h + p.keyline + p.pin, ORANGE), (h + p.keyline, INK), (h, GREEN))):
            band = area.intersection(shapely.box(x0, cy - hh, x1, cy + hh))
            out.append(geom.paint(f"wing-stripe-{part}.{k}", band, c, z=1))
    return out


def _side(kit, view: str, p: Params) -> list[Element]:
    at = _lean(kit, p)
    s = view[-1]  # the parts' side suffix, l or r
    pieces = _pieces(kit, view)
    els: list[Element] = []
    a, b, k, kv = p.door_u0, p.door_u0 + p.door_w, p.door_kink, p.door_kink_v
    # Dark lower body ahead of the door plate, its top traced by an orange pinline.
    skirt = [(0.12, -0.3), (at(a, -0.3), -0.3), (at(a, p.skirt_v), p.skirt_v), (0.12, p.skirt_v)]
    els += geom.outline(kit, f"skirt-pin.{view}", view, Polygon(skirt), [(p.pin, ORANGE)], through=pieces)
    els.append(_plate(kit, f"skirt.{view}", view, skirt, VIOLET_DK))
    # The hazard band, front arch to rear arch, one steady height, drawn over the plates' feet.
    h0 = p.hazard_u0
    band = [(h0, -0.3), (REAR_ARCH, -0.3), (REAR_ARCH, p.hazard_v), (h0, p.hazard_v)]
    els.append(
        patterns.hazard(
            kit,
            f"hazard-band.{view}",
            band,
            (ORANGE, INK),
            p.hazard_px,
            lean=p.lean_deg,
            view=view,
            duty=0.45,
            z=3,
        )
    )
    # Dark behind the door plate: the segment up to the haunch plate, and everything under the haunch.
    hu, hv0 = p.haunch_u0, p.haunch_v0
    rear = [
        (at(a, -0.3), -0.3),
        (1.3, -0.3),
        (1.3, hv0),
        (at(hu, hv0), hv0),
        (at(hu, 1.3), 1.3),
        (at(a, 1.3), 1.3),
    ]
    els.append(_plate(kit, f"rear-dark.{view}", view, rear, VIOLET_DK))

    # Green plates, stroked: the door's lightning-cut band and the rear haunch.
    front = [(at(a - k, -0.3), -0.3), (at(a - k, kv), kv), (at(a, kv + 0.08), kv + 0.08), (at(a, 1.3), 1.3)]
    back = [(at(b, 1.3), 1.3), (at(b, kv + 0.08), kv + 0.08), (at(b - k, kv), kv), (at(b - k, -0.3), -0.3)]
    els += _stroked(kit, f"door-plate.{view}", view, front + back, GREEN, p)
    haunch = [(at(hu, hv0), hv0), (1.3, hv0), (1.3, 1.3), (at(hu, 1.3), 1.3)]
    els += _stroked(kit, f"haunch.{view}", view, haunch, GREEN, p)
    # An orange line in the dark segment behind the door plate, its lower end pointed above the 01.
    u0 = b + p.line_gap
    u1, lo = u0 + p.line_w, p.line_v0
    line = [(at(u0, lo + 0.1), lo + 0.1), (at(u1, lo), lo), (at(u1, 1.3), 1.3), (at(u0, 1.3), 1.3)]
    els.append(_plate(kit, f"seg-line.{view}", view, line, ORANGE, z=1))
    # The skin's up-facing top (the haunch shoulder, the top of the dark segment) is mapped from the top
    # view: each of these carries on over it from where it leaves the side.
    els += _onto_top(kit, f"rear-dark.{view}", view, rear, VIOLET_DK, p, z=0)
    els += _onto_top(kit, f"door-plate.{view}", view, front + back, GREEN, p, stroked=True)
    els += _onto_top(kit, f"haunch.{view}", view, haunch, GREEN, p, stroked=True)
    els += _onto_top(kit, f"seg-line.{view}", view, line, ORANGE, p, z=1)

    # The fender (front_panel_*: skin, nose piece and louvre slats) is one green plate: green as whole
    # islands, so the crest the side view doesn't map is green too and no map edge shows; on the side,
    # dark behind a raked end short of the door shut line (and ahead of a raked cut at the nose, which
    # sits ahead of the car), the end stroked like every plate. Its top is the bonnet seam, stroked on
    # the bonnet's side (`_bonnet_seam`).
    fender = f"front_panel_{s}"
    els.append(panel(kit, f"fender.{view}", fender, GREEN, z=1))
    nose = _lean(kit, p, through=(p.blade_u0, p.blade_v0))
    tail = _lean(kit, p, through=(p.blade_u1, p.blade_v0))
    t = 1.3  # above the car
    blade = [(nose(0, 0.3), 0.3), (tail(0, 0.3), 0.3), (tail(0, t), t), (nose(0, t), t)]
    dark = shapely.box(-0.5, -0.5, 0.8, 1.6).difference(Polygon(blade))
    els.append(clip_to(kit, fender, _plate(kit, f"fender-dark.{view}", view, dark, VIOLET_DK, z=1)))
    els += _stroked(kit, f"fender-blade.{view}", view, blade, GREEN, p, islands=fender)
    # Behind the fender's raked end, between the front arch and the number plate, a run of thin solid
    # slashes leaning with the rake, their tops on one line under the window, each shorter towards the
    # tail: the reference's beltline dashes, tall enough here for their lean to read.
    slashes = []
    for i in range(p.dash_n):
        u = p.dash_u0 + i * (p.dash_w + p.dash_gap)
        lo, hi = p.dash_v0 + i * p.dash_rise, p.dash_v1
        slashes.append(
            [(at(u, lo), lo), (at(u + p.dash_w, lo), lo), (at(u + p.dash_w, hi), hi), (at(u, hi), hi)]
        )
    els.append(_plate(kit, f"dash.{view}", view, slashes, (GREEN, ORANGE)[p.dash_tone], z=2))

    # A hairline, green on green inside the door plate.
    du = b - k - 0.018
    hair = [(at(du, 0.24), 0.24), (at(du, kv - 0.02), kv - 0.02)]
    els.append(_hair(kit, f"door-hair.{view}", view, hair, GREEN_DK, p.hair))

    # Side type, wholly on the vertical side face and leaning with the rake. Ahead of the sim's door
    # light panel: a big green 01 outlined in orange in the dark segment, TYPE-01 under it on the same
    # raked front edge. On the door plate, from its front edge: EVA-01 over a rule, NERV and TEST TYPE.
    v = p.unit_v
    els += _block(
        kit, f"tag-unit.{view}", view, "01", (at(p.unit_u, v), v), p.unit_size, GREEN, p, [(p.pin, ORANGE)]
    )
    cap = 0.7 * p.unit_size / carspace.side_aspect(kit)  # the digits' cap height, in v
    vt = v - cap - p.unit_gap
    els += _block(kit, f"tag-type.{view}", view, "TYPE-01", (at(p.unit_u, vt), vt), p.small_size, ORANGE, p)
    m = p.door_u0 + p.tag_margin
    v0 = p.tag_v
    els += _block(kit, f"tag-door-eva.{view}", view, "EVA-01", (at(m, v0), v0), p.tag_size, INK, p)
    rule = v0 - 0.7 * p.tag_size / carspace.side_aspect(kit) - 0.026
    rule_line = [(at(m, rule), rule), (at(m + 0.06, rule), rule)]
    els.append(_hair(kit, f"door-rule.{view}", view, rule_line, INK, p.rule_px, z=6))
    for j, string in enumerate(("NERV", "TEST TYPE")):
        vv = rule - 0.044 - j * p.stack_gap
        els += _block(kit, f"tag-door-{j}.{view}", view, string, (at(m, vv), vv), p.type_size, VIOLET_DK, p)
    return els


def build(kit, p: Params):
    els: list[Element] = []
    # Whole parts: dark bumper sides with violet strakes (bright ones read as stepped blocks from the
    # front), orange mirrors, dark endplates, a dark wing with a stroked green stripe, near-black swan
    # necks, a dark rear valance.
    els += [panel(kit, f"bumper-{part}", part, VIOLET_DK) for part in BUMPER]
    els.append(panel(kit, "strakes", "intake_strakes", (VIOLET, VIOLET_DK, INK)[p.strake_tone], z=1))
    # Each bumper corner (mapped by no view, so drawn in its own texture, where y runs down the car):
    # a hazard band along its foot, over the splitter's, and a green trim inside the rest of its outline
    # (its intake, and the band's top), set in from the edge by a band of the corner's own dark.
    for part in BUMPER[:2]:
        area = body.region(kit, part)
        x0, _, x1, y1 = area.bounds
        upper = area
        if p.bump_haz:
            els.append(
                patterns.hazard(
                    kit, f"hazard-{part}", part, (ORANGE, INK), p.hazard_px, p.bump_lean, duty=0.45, z=1
                )
            )
            upper = area.difference(shapely.box(x0 - 1, y1 - p.bump_haz, x1 + 1, y1 + 1))
            els.append(geom.paint(f"bumper-upper-{part}", upper, VIOLET_DK, z=2))
        inner = upper.buffer(-p.trim_gap, join_style="mitre")
        ring = geom.clean(inner.difference(inner.buffer(-p.trim_px, join_style="mitre")))
        els.append(geom.paint(f"bumper-line-{part}", ring, GREEN, z=2))
    # The grille opening's side walls, seen through the mesh from the front: violet, near-black or hazard.
    grille = ["grille_side_l", "grille_side_r"] + (
        ["grille_upright_l", "grille_upright_r"] if p.upright_haz else []
    )
    if p.grille_tone == 1:
        els.append(panel(kit, "grille-inside", grille, INK, z=1))
    elif p.grille_tone == 2:
        els.append(
            patterns.hazard(
                kit, "hazard-grille", grille, (ORANGE, INK), p.hazard_px, p.bump_lean, duty=0.45, z=1
            )
        )
    els.append(panel(kit, "mirrors", "mirrors", ORANGE, z=5))
    # The vent over the front arch sits in the fender's green: green too, so it doesn't float.
    els.append(panel(kit, "arch-vent", "fender_arch_vent", GREEN, z=2))
    els.append(panel(kit, "endplates", "wing_endplates", VIOLET_DK))
    els += [panel(kit, f"swan-{s}", f"swan_neck_{s}", INK) for s in ("l", "r")]
    els.append(panel(kit, "rear-valance", "rear_valance", VIOLET_DK))
    els.append(panel(kit, "wing", "wing", VIOLET_DK))
    els += _wing_stripe(kit, p)

    # Hazard: the sills (leaning with the rake on each side), the lip and the rear face. Whole parts no
    # view maps whole, so the slashes lean from the texture's vertical, which runs the other way on the
    # right side's islands.
    for s, lean in (("l", p.lean_deg), ("r", -p.lean_deg)):
        els.append(
            patterns.hazard(
                kit, f"hazard-sill-{s}", f"sill_{s}", (ORANGE, INK), p.hazard_px, lean, duty=0.45, z=1
            )
        )
    for part in ("splitter", "rear_face"):
        els.append(
            patterns.hazard(kit, f"hazard-{part}", part, (ORANGE, INK), p.hazard_px, -30.0, duty=0.45, z=1)
        )

    # Top: the bonnet's dark outer facets, the stroked green centre plate, the roof.
    for s, sign in (("l", -1), ("r", 1)):
        facet = [(-0.1, sign * p.facet_w0), (0.3, sign * p.facet_w1), (0.3, sign * 1.2), (-0.1, sign * 1.2)]
        els.append(clip_to(kit, "bonnet_skin", shape(kit, f"bonnet-facet-{s}", "top", facet, VIOLET_DK)))
    els += _bonnet_seam(kit, p)
    c = p.plate_tip + p.plate_point
    plate = [
        (p.plate_tip, 0.0),
        (c, p.plate_w0),
        (p.plate_u1, p.plate_w1),
        (p.plate_u1, -p.plate_w1),
        (c, -p.plate_w0),
    ]
    els += _stroked(kit, "bonnet-plate", "top", plate, GREEN, p, islands="bonnet_skin")
    # A green outline echoes the plate's point across the bonnet, like the reference's converging
    # chevrons.
    if p.echo:
        echo = grow(plate, p.keyline + p.pin + p.echo_gap, _scale(kit, "top"))
        els += [
            clip_to(kit, "bonnet_skin", e)
            for e in geom.outline(kit, "bonnet-echo", "top", echo, [(p.pin, GREEN)], z=2)
        ]
    # The bonnet pins (one island drawn at every pin, the engine cover's too) in the dark tone.
    els.append(panel(kit, "pins", "bonnet_pins", VIOLET_DK, z=2))
    els.append(panel(kit, "roof", ROOF, VIOLET_DK))
    # A big outlined 01 across the roof, reading from the front (the chase camera doesn't see the roof).
    centre = carspace.island_shapes(kit)[ROOF].centroid
    roof_at = carspace.invert(
        next(pc for pc in carspace.pieces(kit, "top") if pc.island == ROOF), (centre.x, centre.y), (0.4, 0.0)
    )[0]
    em = p.roof_number_px / _scale(kit, "top")[0]
    els += [
        clip_to(kit, ROOF, e)
        for e in type_block(
            kit,
            "roof-number",
            "top",
            [Line("01", em, FONT, 800)],
            (float(roof_at[0]), float(roof_at[1])),
            VIOLET_DK,
            anchor=(0.5, 0.5),
            strokes=[(p.pin, GREEN), (p.keyline, INK)],
            z=4,
        )
    ]
    roof = _chevrons(kit, p.roof_u0, p.roof_step, p.roof_w)
    els += geom.outline(
        kit,
        "roof-chevron",
        "top",
        shapely.union_all([Polygon(q) for q in roof]),
        [(p.keyline, INK), (p.pin, ORANGE)],
        z=2,
    )
    lean = _chevron_lean(kit, p.roof_w)
    els.append(
        patterns.chevrons(
            kit, "roof-chevron", "top", p.roof_u0, 3, p.roof_step, p.roof_w, 0.012, GREEN, lean, z=2
        )
    )
    # Rear deck: dark over the buttresses behind deck_u0, around the cover.
    deck = [(p.deck_u0 + 0.06, -1.2), (p.deck_u0, 0.0), (p.deck_u0 + 0.06, 1.2), (1.2, 1.2), (1.2, -1.2)]
    els.append(clip_to(kit, ("buttress_l", "buttress_r"), shape(kit, "deck", "top", deck, VIOLET_DK)))
    # Engine cover: green, ringed near-black, orange, near-black from its edge in; near-black chevrons.
    els.append(panel(kit, "cover-plate", "engine_cover", GREEN, z=1))
    # The outer keyline is rim_key wide: the roof/cover shut line runs about 10 px inside the cover's
    # front edge, and a narrower one would run along it.
    rim = [(p.rim_key, INK), (p.rim_px, ORANGE), (p.keyline, INK)]
    els += geom.outline(kit, "cover-rim", None, "engine_cover", rim, "inside", sides="outer", z=1)
    # The scoop slot on the cover's centreline, green with the plate, so it doesn't sit as a violet block
    # among the chevrons.
    els.append(panel(kit, "cover-scoop", "engine_cover_scoop", GREEN, z=1))
    lean = _chevron_lean(kit, p.chev_w, p.chev_arm)
    els.append(
        patterns.chevrons(
            kit,
            "chevron",
            "top",
            p.chev_u0,
            p.chev_n,
            p.chev_step,
            p.chev_w,
            p.chev_t,
            INK,
            lean,
            point=("front", "rear")[p.chev_point],
            z=2,
        )
    )

    for view in kit.side_views:
        els += _side(kit, view, p)
    els += _number_plates(kit, p)
    # EVA-01 on the engine cover, reading from behind. (The bonnet's outer facets carry no marks: from
    # front34 they bent over the fender crown and were cut, and read only from straight above.)
    els.append(
        text(
            kit,
            "tag-cover",
            "top",
            "EVA-01",
            (p.cover_tag_u, 0.0),
            0.035,
            INK,
            font=FONT,
            weight=800,
            facing="rear",
            z=6,
        )
    )
    return base(VIOLET).add(*els)
