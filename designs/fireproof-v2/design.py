"""Fireproof v2: the flame car burning into a black tail through a pixel dissolve.

The front is the v1 car: orange, a yellow core band on the sides and a yellow spine on top, edged in black.
Behind the door a raked line turns the car black, and the line is drawn as hard square pixels in three
sizes: black eats into the orange ahead of it, and orange and yellow embers fly back over the black behind
it, thinning out. Stepped flame tongues in the mark's lighter orange rise from the black sill into the door.
"""

import math
from dataclasses import dataclass, replace

import shapely
from shapely.geometry import LineString, Point, Polygon

from livery import carspace, geom, patterns
from livery.params import DesignParams, param
from livery.primitives import base, clip_out, mirror_sides, panel, sponsor, sponsor_group, stripe, waive

KIT = "mclaren-720s-gt3-evo"
# The Fireproof mark's three tones (field, flame, core), black and ash.
ORANGE, FLAME, CORE, BLACK, ASH = "#f46200", "#ff8000", "#ffd500", "#141414", "#474747"
SIDES = ("side_l", "side_r")
# The kit's body model by name (kits/<kit>/body.yaml), per side where a part has one.
# The nose's sides under the headlights, which the band's car-space shape only partly reaches: whole yellow.
FENDER_NOSE = ("fender_nose_l", "fender_nose_r")
# The strip on the door under each mirror: its map covers only part of it, so the band skips it and it is
# painted whole.
MIRROR_BASES = ("mirror_base_l", "mirror_base_r")
# The sill's up-facing strips and the strip over the side intake, per side: painted whole, in the sill's own
# element, since their maps stop short of their outlines.
SILLS = ("sill_l", "sill_r")
# The black tail's parts no view maps whole: the wing, its uprights, the rear face (with the rear number),
# the valance, the rear deck and its lip, the rear number panels.
TAIL = (
    "wing",
    "swan_neck_l",
    "swan_neck_r",
    "rear_face",
    "rear_valance",
    "rear_deck",
    "rear_deck_lip",
    "number_panels",
)
# The car's features in the accent: the mirror shells, the wing endplates, the strakes and inserts of the
# bumper's side intakes, the bar over the right intake (the left one is part of its bumper corner) and the
# header over the crash-structure opening.
ACCENT = (
    "mirrors",
    "wing_endplates",
    "intake_strakes",
    "intake_insert_l",
    "intake_insert_r",
    "intake_bar_r",
    "grille_header",
)
# Black, so the sill reaches the nose: the front bumper's corners, the crash-structure opening, and the
# grille's uprights and side faces and the intakes' inner edges round it (in the base orange, they read
# as unpainted paint inside the black bumper from the front).
BUMPER = (
    "front_bumper_corner_l",
    "front_bumper_corner_r",
    "crash_opening",
    "grille_upright_l",
    "grille_upright_r",
    "grille_side_l",
    "grille_side_r",
    "intake_edge_l",
    "intake_edge_r",
)
SPLITTER = "splitter"  # lip and top: the hazard tape
STOPS_AT_SPLITTER = (
    "It ends where the splitter's top begins, which carries its own hazard tape; the seam check counts that "
    "designed end at the bonnet-to-splitter seam as a break."
)
VENT = "bonnet_vent"  # the duct ramp ahead of the windscreen and its walls: black with embers
LIGHT_PANEL = ("door_light_panel_l", "door_light_panel_r")  # the sim draws over it

# Flame tongues on the door, rising from the sill: per tongue, the cell rows from the sill up, each as the
# [first, last) cell index along u (u = index * cell_u). Every row steps back with the car's rake. The
# number box (u 0.41-0.47, v 0.29-0.53) sits in the gap between the two groups. The first tongue runs on to
# u 0.4 under the door's front, which no view sees, so it ends where the rear fender does.
TONGUES = (
    ((19, 25), (21, 25)),
    ((31, 35), (32, 36), (34, 37)),
    ((35, 38), (36, 39), (38, 41), (40, 42)),
)
# Dissolve cells in three sizes (in cells), each with its density at the line and how far out it reaches
# (as a share of the reach): big blocks only close to the line, small ones furthest out.
SIZES = ((2.0, 0.45, 0.3), (1.0, 0.7, 0.75), (0.5, 0.55, 1.0))
# Roster sponsors in cream (Fireproof's "proof" tone) on the black sill and haunch. Each mark is clipped to
# its anchor's island, so each sill group stays inside one sill island: at v 0.09, the door's (u 0.37-0.485)
# and the haunch's (0.635-0.775); the side intake strip between them sits under the carbon side blade.
PROOF = "#DAD8CE"
SILL_GROUPS = (  # brands, the field's ends in u, the height
    ((("hexmoor", "mark"), ("huilec", "mark"), ("telvio", "mark")), 0.425, 0.48, 0.017),
    ((("grivant", "wordmark"),), 0.648, 0.768, 0.0152),
)
MIRROR_MARK, MIRROR_AT = "grivant", (0.36, 1.0)
BONNET_GROUP = (("rigmount", "mark"), ("norvane", "mark"))
HAUNCH = "zentiq"  # on the black haunch, in a box the embers keep out of
# Behind the rear wheel, on the tail quarter (i160 left, i053 right: about u 0.96-0.99, v 0.25-0.5), in a
# box the embers keep out of. Only one mark fits there at its minimum size.
REAR_MARK = "ostrand"


@dataclass(frozen=True)
class Params(DesignParams):
    seed: int = param(3, 0, 2**31 - 1)
    # Side band, in v: black pinline, yellow core, black pinline.
    side_v0: float = param(0.615, 0.3, 0.9)
    side_v1: float = param(0.92, 0.4, 1.0)
    # The lower pinline straddles the door's crease (about v 0.59), wide enough to show on both faces, and
    # clears the side number zones (top at v 0.522 left, 0.531 right) by the near-tangent reach.
    pin_v0: float = param(0.572, 0.3, 0.9)
    pin_v1: float = param(0.94, 0.4, 1.1)
    sill_v: float = param(0.18, 0.0, 0.3)
    # Top spine, in |w|: yellow core, its black pinline out to gap_w, and a row of flame pixels outside it.
    core_w: float = param(0.1, 0.02, 0.3)
    gap_w: float = param(0.14, 0.02, 0.4)
    row_gap: float = param(0.012, 0.0, 0.1)
    # The burn: where the raked line meets v 0 and its lean, where it crosses the top, the pixel size and
    # the dissolve's reach (in u, along the car).
    burn_u: float = param(0.525, 0.4, 0.85)
    burn_angle: float = param(58.0, 0.0, 70.0)
    top_u: float = param(0.78, 0.55, 0.9)  # over the engine cover, behind the roof
    top_rake: float = param(0.5, 0.0, 1.0)  # u per unit of |w| the top line comes forward over the haunches
    top_flat_w: float = param(0.35, 0.0, 0.5)  # the top line runs straight across |w| up to this
    cell_u: float = param(0.016, 0.008, 0.03)
    top_phase: float = param(0.0, 0.0, 1.0)  # shifts the top's pixel rows in w, as a share of a cell
    char_reach: float = param(0.1, 0.0, 0.15)  # how far ahead of the line black pixels reach
    top_char_reach: float = param(0.06, 0.0, 0.15)
    ember_reach: float = param(0.44, 0.0, 0.6)  # how far behind it embers fly
    ember_density: float = param(1.0, 0.0, 1.0)  # at the line
    ash_density: float = param(0.2, 0.0, 0.4)
    # The front burns too, lighter: black pixels break from the front arch back over the door's front and
    # from the nose plate back over the bonnet, and yellow (orange on the band) embers rise further back.
    # The front fender's side is a mesh of small islands where no pixel clears the near-tangent lint.
    front_u: float = param(0.28, -0.3, 0.5)  # where the front line meets v 0, behind the front arch
    front_reach: float = param(0.18, 0.0, 0.4)
    front_density: float = param(0.85, 0.0, 1.0)
    front_ember_reach: float = param(0.24, 0.0, 0.5)
    front_ember_density: float = param(0.55, 0.0, 1.0)
    bonnet_line: float = param(0.09, -0.05, 0.2)  # the top's front line on the flanks, beside the vent
    bonnet_density: float = param(1.0, 0.0, 1.0)
    bonnet_reach: float = param(0.3, 0.0, 0.4)
    bonnet_ember_reach: float = param(0.32, 0.0, 0.5)
    bonnet_ember_density: float = param(0.7, 0.0, 1.0)
    duct_density: float = param(0.12, 0.0, 0.5)
    # Three hairlines ahead of the line: the first one's distance in u, the gap, the width.
    hair_at: float = param(0.085, 0.03, 0.3)
    hair_gap: float = param(0.015, 0.005, 0.06)
    hair_u: float = param(0.005, 0.002, 0.012)  # tone-on-tone: under min_feature across the rake (notes.md)
    # A black plate across the nose under the mark, framed in yellow, down to the splitter.
    plate_u1: float = param(0.1, 0.02, 0.15)
    plate_w: float = param(0.22, 0.1, 0.4)
    frame_w: float = param(0.03, 0.025, 0.1)  # its frame, in w (at least the lint's outline width)
    tail_u: float = param(1.04, 0.8, 1.06)  # on the wing, towards its trailing edge, clear of the swan necks
    tail_h: float = param(0.03, 0.01, 0.06)  # small: about 1.1x the wordmark's minimum here
    # The sponsors: the sill row's height and the haunch mark's place.
    spons_v: float = param(0.09, 0.0, 0.2)
    bonnet_mark_u: float = param(0.135, 0.08, 0.18)  # on the bonnet's left flank, across from the 451
    bonnet_mark_w: float = param(0.27, 0.2, 0.45)
    haunch_u: float = param(0.728, 0.6, 0.95)
    haunch_v: float = param(0.33, 0.2, 0.9)  # below the sim's door light panel (u 0.71-0.77, v 0.45-0.7)
    # One small mark on the black quarter behind the rear wheel.
    rear_u: float = param(0.975, 0.9, 0.99)
    rear_v: float = param(0.37, 0.2, 0.5)
    rear_h: float = param(0.023, 0.01, 0.03)


def _cell(u, a, du, da) -> Polygon:
    return shapely.box(u, a, u + du, a + da)


def _stairs(line, a_lo: float, a_hi: float, da: float, du: float, end: float = 1.3):
    """Everything behind `line(a)` (a = v or w; towards the tail) as a staircase of rows `da` high, each
    starting on the `du` grid."""
    rows = []
    for j in range(math.ceil((a_hi - a_lo) / da)):
        a0 = a_lo + j * da
        rows.append(shapely.box(math.floor(line(a0 + da / 2) / du) * du, a0, end, a0 + da))
    return shapely.union_all(rows)


def _dissolve(kit, p: Params, id: str, view: str, line, field, tiers, palette, rng, origin, falloff, **kw):
    """`patterns.dissolve` with cells of `size * cell_u` (as seen), so they sit on the staircase's grid:
    `tiers` is (size in cells, density, reach). Texture px per car length is taken where the dissolve
    measures it, at the middle of the area its cells can fill."""
    k = carspace.aspect(kit, view)
    reach = max(r for _, _, r in tiers)
    if view == "top":
        scale = carspace.px_per_unit(kit, "top")[0]
    else:
        region = geom.as_car(geom.as_seen(line, k).buffer(reach).intersection(geom.as_seen(field, k)), k)
        x0, y0, x1, y1 = region.bounds
        grid = [Point(x0 + (x1 - x0) * i / 8, y0 + (y1 - y0) * j / 8) for j in range(9) for i in range(9)]
        middle = sorted((q for q in grid if region.intersects(q)), key=region.centroid.distance)
        for q in (region.representative_point(), *middle):
            try:
                scale = carspace.px_per_unit(kit, view, (q.x, q.y))[0]
                break
            except ValueError:
                continue
    cells = [patterns.Tier(s * p.cell_u * scale, min(d, 1.0), r) for s, d, r in tiers]
    return patterns.dissolve(
        kit, id, view, line, cells, palette, rng, field=field, origin=origin, falloff=falloff, **kw
    )


def _reach(reach: float, cos: float, cell: float):
    """The dissolve's tiers for a reach along the car (u), measured square to a line leaning by `cos`:
    each tier's reach out to its cells' far side, plus a cell for where the staircase steps off the line."""
    return [(s, d, (reach * share + (s + 1) * cell) * cos) for s, d, share in SIZES]


def build(kit, p: Params):
    els = []
    k_side, k_top = carspace.aspect(kit, "side_l"), carspace.aspect(kit, "top")
    x_per_u, y_per_w = carspace.px_per_unit(kit, "top")
    # Pixels are square as seen: a cell is cell_u long and as tall in v (or as wide in w).
    dv, dw = p.cell_u / k_side, p.cell_u / k_top
    lean = geom.rake(kit, p.burn_angle)
    cos_burn = math.cos(math.radians(p.burn_angle))  # distances square to the line, from distances along u

    # The v1 stripe set: black pinlines under a yellow core on the sides; on top a yellow core with a black
    # pinline traced round it.
    side = stripe(kit, "side-pin", p.pin_v0, p.pin_v1, BLACK, views="sides", z=1)
    side += stripe(kit, "side-core", p.side_v0, p.side_v1, CORE, views="sides", z=2)
    els += [clip_out(kit, MIRROR_BASES, e, margin=2) for e in side]
    els += [panel(kit, f"side-core-{part}", part, CORE, z=2) for part in MIRROR_BASES]
    els += [panel(kit, f"side-core-{part}", part, CORE) for part in FENDER_NOSE]
    # The nose's sides and bumper face below the band are black, with the bumper corners. Under the right
    # headlight it meets the splitter's top, which carries the hazard tape: there it stops.
    for e in stripe(kit, "side-nose", -0.3, p.pin_v0, BLACK, views="sides", u1=0.17, z=1):
        els.append(waive(e, "seam", STOPS_AT_SPLITTER) if e.id.endswith("side_r") else e)
    # The sill.
    for e in stripe(kit, "sill", -0.3, p.sill_v, BLACK, views="sides", z=3):
        sill = SILLS[SIDES.index(e.id.rsplit(".", 1)[1])]
        els.append(replace(e, svg=clip_out(kit, sill, e, margin=2).svg + panel(kit, "-", sill, BLACK).svg))
    spine = [(-0.3, -p.core_w), (1.3, -p.core_w), (1.3, p.core_w), (-0.3, p.core_w)]
    pin_px = (p.gap_w - p.core_w) * y_per_w
    top = geom.outline(kit, "top-core", "top", spine, [(pin_px, BLACK)], z=1)
    top += stripe(kit, "top-core", -p.core_w, p.core_w, CORE, views="top", u0=-0.3, u1=1.3, z=2)
    els += [clip_out(kit, SPLITTER, e, margin=2) for e in top]

    # A row of flame-tone pixels along each side of the spine, nose to burn (one cell every two).
    row = [
        _cell(
            k * 2 * p.cell_u, sgn * (p.gap_w + p.row_gap) - (sgn < 0) * dw * 0.75, p.cell_u * 0.75, dw * 0.75
        )
        for k in range(-3, int(p.top_u / (2 * p.cell_u)))
        for sgn in (1, -1)
    ]
    els.append(geom.fill(kit, "top-row", "top", {FLAME: row}, z=2))

    # Flame tongues rising from the sill into the door, in the flame tone. Their first row starts a row
    # down, under the sill, so they don't stop where the door meets the side intake's strip.
    tongues = [
        _cell(c0 * p.cell_u, p.sill_v + (r - (r == 0)) * dv, (c1 - c0) * p.cell_u, dv * (1 + (r == 0)))
        for rows in TONGUES
        for r, (c0, c1) in enumerate(rows)
    ]
    for view in SIDES:
        els.append(geom.fill(kit, f"tongues.{view}", view, {FLAME: tongues}, z=2, tags=("crosses_seam",)))

    # Hairlines in the flame tone across the door, parallel to the burn line ahead of it, from the sill to
    # the band's pinline.
    hair = [
        Polygon(geom.slant(p.burn_u - o, p.burn_u - o + p.hair_u, p.sill_v, p.pin_v0, lean))
        for o in (p.hair_at, p.hair_at + p.hair_gap, p.hair_at + 2 * p.hair_gap)
    ]
    for view in SIDES:
        els.append(geom.fill(kit, f"hairlines.{view}", view, {FLAME: hair}, z=3, tags=("crosses_seam",)))

    # The burn: black behind the raked line as a staircase of cells, black pieces ahead of it, embers and
    # ash behind it.
    def side_colour(u, v, d):
        return {CORE: 1.0} if p.side_v0 <= v <= p.side_v1 else {FLAME: 0.4, ORANGE: 0.6}

    def top_colour(u, w, d):
        return {CORE: 1.0} if abs(w) <= p.core_w else {FLAME: 0.4, ORANGE: 0.6}

    def top_line(w):
        return p.top_u - p.top_rake * max(0.0, abs(w) - p.top_flat_w)

    # The haunch's sponsor box, the sim's door light panel and the tail's wordmark keep clear of embers.
    hu0, hv0, hu1, hv1 = p.haunch_u - 0.07, p.haunch_v - 0.12, p.haunch_u + 0.05, p.haunch_v + 0.12
    deck = kit.part("rear_deck").islands
    keep_out = {
        view: shapely.union_all(
            [
                shapely.box(hu0, hv0, hu1, hv1),
                shapely.box(p.rear_u - 0.03, p.rear_v - 0.12, p.rear_u + 0.03, p.rear_v + 0.12),
                kit.feature(light).geometry(),
            ]
        )
        for view, light in zip(SIDES, LIGHT_PANEL, strict=True)
    }
    wing_mark = shapely.box(p.tail_u - p.tail_h, -0.34, p.tail_u + p.tail_h, 0.34)
    keep_out["top"] = shapely.union_all(
        [pc.car_area for pc in carspace.pieces(kit, "top") if pc.island in deck and pc.car_area is not None]
        + [wing_mark]
    )
    burns = {}
    for view in SIDES:
        burns[view] = (
            _stairs(lambda v: p.burn_u + lean * v, p.sill_v, 1.1, dv, p.cell_u),
            LineString([(p.burn_u + lean * v, v) for v in (p.sill_v - 0.3, 1.3)]),
            (0.0, p.sill_v * k_side),
            shapely.box(-0.3, p.sill_v, 1.3, 1.1),
            side_colour,
            p.char_reach,
            cos_burn,
        )
    w0 = -1.1 - p.top_phase * dw
    ws = [-1.2, -p.top_flat_w, p.top_flat_w, 1.2]
    burns["top"] = (
        _stairs(top_line, w0, 1.1, dw, p.cell_u),
        LineString([(top_line(w), w) for w in ws]),
        (0.0, w0 * k_top),
        shapely.box(-0.3, w0, 1.3, 1.1),
        top_colour,
        p.top_char_reach,
        1.0,
    )
    for view, (black, line, origin, area, colour, char_reach, cos) in burns.items():
        burn = geom.fill(kit, f"burn.{view}", view, {BLACK: [black]}, z=4, tags=("crosses_seam",))
        els.append(clip_out(kit, SILLS[SIDES.index(view)], burn, margin=2) if view in SIDES else burn)
        ahead = area.difference(black)
        chars = [(s, d, r) for s, d, r in _reach(char_reach, cos, p.cell_u)]
        embers = [(s, p.ember_density * d / 0.7, r) for s, d, r in _reach(p.ember_reach, cos, p.cell_u)]
        common = dict(origin=origin)
        els.append(
            _dissolve(
                kit,
                p,
                f"char.{view}",
                view,
                line,
                ahead,
                chars,
                {BLACK: 1.0},
                p.rng(f"char-{view}"),
                falloff=1.5,
                z=4,
                **common,
            )
        )
        els.append(
            _dissolve(
                kit,
                p,
                f"embers.{view}",
                view,
                line,
                black,
                embers,
                colour,
                p.rng(f"embers-{view}"),
                falloff=2.0,
                avoid=keep_out[view],
                z=5,
                **common,
            )
        )
        els.append(
            _dissolve(
                kit,
                p,
                f"ash.{view}",
                view,
                line,
                black,
                [(1.0, p.ash_density, 2.0)],
                {ASH: 1.0},
                p.rng(f"ash-{view}"),
                falloff=0.0,
                avoid=keep_out[view],
                z=5,
                **common,
            )
        )

    # The front burn: on the sides, black pieces and embers behind a line from behind the front arch; on top,
    # behind the nose plate's frame and, outside it, a black corner field. None on the plate, the spine or
    # the bonnet's sponsors (nor, as in every dissolve, on the numbers).
    k_front = 0.1  # u per unit of v the front line leans
    cos_front = math.cos(math.atan(k_front / k_side))
    for view in SIDES:
        for id, shift, reach, dens, palette, z in (
            ("front-char", 0.0, p.front_reach, p.front_density, {BLACK: 1.0}, 4),
            ("front-embers", 0.03, p.front_ember_reach, p.front_ember_density, None, 5),
        ):
            u0 = p.front_u + shift
            line = LineString([(u0 + k_front * v, v) for v in (p.sill_v - 0.3, 1.3)])
            behind = Polygon(
                [(u0 + k_front * p.sill_v, p.sill_v), (1.3, p.sill_v), (1.3, 1.1), (u0 + k_front * 1.1, 1.1)]
            )

            def lit(u, v, d):
                return {ORANGE: 1.0} if p.side_v0 <= v <= p.side_v1 else {CORE: 1.0}

            tiers = [(s, dens * d / 0.7, r) for s, d, r in _reach(reach, cos_front, 0.0)]
            els.append(
                _dissolve(
                    kit,
                    p,
                    f"{id}.{view}",
                    view,
                    line,
                    behind,
                    tiers,
                    palette or lit,
                    p.rng(f"{id}-{view}"),
                    (0.0, p.sill_v * k_side),
                    2.0,
                    z=z,
                )
            )
    plate = p.plate_w + p.frame_w
    frame_px = p.frame_w * y_per_w
    front = p.plate_u1 + frame_px / x_per_u
    marks = (p.bonnet_mark_u - 0.03, -p.bonnet_mark_w - 0.2, p.bonnet_mark_u + 0.03, -p.bonnet_mark_w + 0.05)
    skip = shapely.union_all([shapely.box(-0.3, -0.19, 1.0, 0.19), shapely.box(*marks)])
    # Outside the plate the black reaches back to the line, over the bonnet's corners and the fender tops.
    corners = [shapely.box(-0.3, plate, p.bonnet_line, 1.2), shapely.box(-0.3, -1.2, p.bonnet_line, -plate)]
    els.append(geom.fill(kit, "front-burn.top", "top", {BLACK: corners}, z=3, tags=("crosses_seam",)))
    for id, shift, reach, dens, palette, z in (
        ("front-char", 0.0, p.bonnet_reach, p.bonnet_density, {BLACK: 1.0}, 4),
        ("front-embers", 0.02, p.bonnet_ember_reach, p.bonnet_ember_density, {CORE: 1.0}, 5),
    ):
        b, f = p.bonnet_line + shift, front + shift
        steps = [(b, -0.6), (b, -plate), (f, -plate), (f, plate), (b, plate), (b, 0.6)]
        behind = Polygon([*steps, (1.0, 0.6), (1.0, -0.6)])
        tiers = [(s, dens * d / 0.7, r) for s, d, r in _reach(reach, 1.0, 0.0)]
        els.append(
            _dissolve(
                kit,
                p,
                f"{id}.top",
                "top",
                LineString(steps),
                behind,
                tiers,
                palette,
                p.rng(f"{id}-top"),
                (0.0, w0 * k_top),
                2.0,
                avoid=skip,
                z=z,
            )
        )

    # The bonnet's vent, which no view maps: black with embers, the tail's pattern on the nose.
    els.append(panel(kit, "vent", VENT, BLACK, z=3))
    els.append(
        patterns.scatter(
            kit,
            "vent-embers",
            VENT,
            [(26.0, p.duct_density), (13.0, p.duct_density * 1.5)],
            {CORE: 2.0, ORANGE: 1.0, FLAME: 1.0},
            p.rng("vent"),
            z=5,
        )
    )

    els += [panel(kit, f"tail-{part}", part, BLACK) for part in TAIL]
    els += [panel(kit, f"bumper-{part}", part, BLACK) for part in BUMPER]
    els += [panel(kit, f"accent-{part}", part, CORE, z=6) for part in ACCENT]
    # The nose: a black plate framed in yellow, the spine's yellow core running on through it, down to the
    # splitter, which carries its own hazard tape.
    plate_shape = [(-0.3, -p.plate_w), (p.plate_u1, -p.plate_w), (p.plate_u1, p.plate_w), (-0.3, p.plate_w)]
    nose = geom.outline(kit, "nose-plate", "top", plate_shape, [(frame_px, CORE)], z=3)
    nose += stripe(kit, "nose-plate", -p.plate_w, p.plate_w, BLACK, views="top", u0=-0.3, u1=p.plate_u1, z=3)
    nose += stripe(kit, "nose-core", -p.core_w, p.core_w, CORE, views="top", u0=-0.3, u1=p.plate_u1, z=3)
    els += [clip_out(kit, SPLITTER, e, margin=2) for e in nose[:1]]
    els += [waive(clip_out(kit, SPLITTER, e, margin=2), "seam", STOPS_AT_SPLITTER) for e in nose[1:]]
    # The splitter's lip and top in hazard tape.
    els.append(
        patterns.hazard(
            kit, "splitter", SPLITTER, (CORE, BLACK), 32.0, lean=math.degrees(math.atan(0.5)), z=3
        )
    )

    # One small wordmark on the wing's top, between the swan necks; the spine runs plainly through the nose.
    els.append(
        sponsor(
            kit,
            "wordmark-tail",
            "top",
            "fireproof",
            variant="wordmark",
            at=(p.tail_u, 0.0),
            height=p.tail_h,
            facing="rear",
            color=CORE,
            z=7,
        )
    )

    # The sponsor set.
    for i, (brands, u0, u1, h) in enumerate(SILL_GROUPS):
        for view in SIDES:
            els += sponsor_group(
                kit, f"sill-{i}.{view}", view, brands, (u0, p.spons_v), (u1, p.spons_v), h, PROOF, z=7
            )
    els += mirror_sides(
        sponsor,
        kit,
        "sill-mark",
        "fireproof",
        at=(0.33, p.spons_v),
        color="own",
        height=0.03,
        variant="mark",
        z=7,
    )
    els += mirror_sides(
        sponsor,
        kit,
        "haunch",
        HAUNCH,
        at=(p.haunch_u, p.haunch_v),
        color=PROOF,
        height=0.0205,
        variant="wordmark",
        z=7,
    )
    els += mirror_sides(
        sponsor,
        kit,
        "rear",
        REAR_MARK,
        at=(p.rear_u, p.rear_v),
        color=PROOF,
        height=p.rear_h,
        variant="mark",
        z=7,
    )
    # Small marks on the yellow mirrors (mapped on the sides) and on the bonnet's left flank.
    els += mirror_sides(
        sponsor, kit, "mirror", MIRROR_MARK, at=MIRROR_AT, color=BLACK, height=0.015, variant="mark", z=7
    )
    els += sponsor_group(
        kit,
        "bonnet",
        "top",
        BONNET_GROUP,
        (p.bonnet_mark_u, -p.bonnet_mark_w),
        (p.bonnet_mark_u, -p.bonnet_mark_w - 0.15),
        0.027,
        BLACK,
        axis="column",
        z=7,
    )
    return base(ORANGE).add(*els)
