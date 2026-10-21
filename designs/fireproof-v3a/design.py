"""fireproof-v3a: a black nose turns into a paper tail only through stacked rectangles, with a terracotta
core where they meet (Paul Miller's block collage, in the Fireproof desktop theme)."""

import math
from dataclasses import dataclass

import shapely
from shapely.geometry import LineString, Polygon

from livery import body, carspace, geom, patterns, typeset
from livery.params import DesignParams, param
from livery.primitives import (
    base,
    clip_out,
    clip_to,
    marks,
    mirror_sides,
    panel,
    sponsor,
    sponsor_group,
    svg,
)

KIT = "mclaren-720s-gt3-evo"

BLACK = "#100f0f"
BLACK_T = "#282726"
TERRA = "#cf6a4c"
TERRA_D = "#6b3528"
PAPER = "#dad8ce"
PAPER_D = "#b7b5ac"
PAPER_L = "#f2f0e5"
MUTED = "#878580"

# Car-space extents drawn past on each view (u, then v or w), so every edge of the car is covered.
U_LO, U_HI = -0.06, 1.06
SIDE_A = (-0.12, 1.12)
TOP_A = (-1.15, 1.15)


@dataclass(frozen=True)
class Params(DesignParams):
    lean: float = param(50.0, 0.0, 60.0)  # degrees as seen, tops towards the tail
    core_front: float = param(0.30, 0.18, 0.45)  # black / terracotta on the side at mid-height (u)
    core_rear: float = param(0.56, 0.45, 0.72)  # terracotta / paper on the side at mid-height (u)
    top_front: float = param(0.42, 0.25, 0.55)  # black / terracotta on top, on the centre line
    top_rear: float = param(0.62, 0.5, 0.75)
    top_lean: float = param(-25.0, -50.0, 50.0)  # on top: positive leans the outer ends towards the tail
    row_jitter: float = param(0.5, 0.0, 2.0)  # how far a row's step moves, in big blocks
    big_px: float = param(96.0, 64.0, 160.0)
    mid_px: float = param(40.0, 24.0, 64.0)
    dust_px: float = param(16.0, 8.0, 24.0)
    big_density: float = param(0.65, 0.0, 1.0)  # big blocks at a split
    big_reach: float = param(0.18, 0.03, 0.4)  # car lengths from a split where they thin to nothing
    field_density: float = param(0.3, 0.0, 0.6)  # big tone blocks anywhere in the paper
    black_density: float = param(0.6, 0.0, 0.8)  # big tone blocks and embers anywhere in the black
    core_fill: float = param(0.25, 0.0, 0.8)  # big and mid tone blocks anywhere in the core
    mid_density: float = param(0.45, 0.0, 1.0)
    mid_reach: float = param(0.12, 0.02, 0.4)
    mid_floor: float = param(0.05, 0.0, 0.2)  # mid strays anywhere
    dust_density: float = param(0.35, 0.0, 1.0)
    dust_reach: float = param(0.07, 0.01, 0.2)
    sill_top: float = param(0.14, 0.06, 0.25)  # the sill band's top (v)
    sill_u0: float = param(0.44, 0.42, 0.46)  # the door island's sill, ahead of the side blade's strip
    sill_v: float = param(0.07, 0.03, 0.12)
    sill_h: float = param(0.018, 0.012, 0.024)
    tech_u: float = param(0.625, 0.6, 0.7)
    tech_v: float = param(0.8, 0.5, 0.9)
    hair_px: float = param(6.0, 6.0, 12.0)
    hair_period_px: float = param(36.0, 16.0, 80.0)
    rear_number_pad: float = param(20.0, 6.0, 32.0)  # texture px round the rear number box
    edge_margin_px: float = param(2.0, 0.0, 6.0)  # how much stricter than the lint the block clearance is


def _snap(x: float, q: float) -> float:
    return round(x / q) * q


def _pick(x: float, weights: dict[str, float]) -> str | None:
    total = sum(weights.values())
    if total <= 0:
        return None
    acc = 0.0
    for c, w in weights.items():
        acc += w / total
        if x < acc:
            return c
    return list(weights)[-1]


def _falloff(d: float, reach: float) -> float:
    return max(0.0, 1.0 - d / reach) ** 1.5


def _collage(kit, view: str, p: Params) -> list:
    """The view's whole paint as seen-space (u, a * aspect) rectangles in drawing order, flattened to what
    shows of each colour, back in car space."""
    k = carspace.aspect(kit, view)
    side = view != "top"
    ref = (0.45, 0.4) if side else (0.4, 0.0)
    S = carspace.px_per_unit(kit, view, ref)[0]  # texture px per car length, about the same both ways seen
    q = 8.0 / S  # every edge sits on an 8 px grid, so no sliver is thinner than that
    B, M, D = (_snap(px / S, q) for px in (p.big_px, p.mid_px, p.dust_px))
    a_lo, a_hi = SIDE_A if side else TOP_A
    y_lo, y_hi = a_lo * k, a_hi * k
    rng = p.rng(f"rows.{view}")

    if side:
        t, y_mid = math.tan(math.radians(p.lean)), 0.5 * k
        f0, f1 = p.core_front, p.core_rear

        def lean_at(y: float) -> float:
            return t * (y - y_mid)
    else:
        t = math.tan(math.radians(p.top_lean))
        f0, f1 = p.top_front, p.top_rear

        def lean_at(y: float) -> float:
            return t * abs(y)

    # Rows of varied height, each with its own step in both splits: the splits are stacked rectangles.
    rows = []
    y = _snap(y_lo, q)
    while y < y_hi:
        h = _snap(B * rng.choice([0.5, 1.0, 1.0, 1.5]), q)
        jit = rng.uniform(-1, 1, size=2) * p.row_jitter * B
        ym = y + h / 2
        b1 = _snap(f0 + lean_at(ym) + jit[0], q)
        b2 = max(_snap(f1 + lean_at(ym) + jit[1], q), b1 + 2 * B)
        rows.append((y, y + h, b1, b2))
        y += h

    def row_at(yy: float):
        for r in rows:
            if r[0] <= yy < r[1]:
                return r
        return rows[-1] if yy >= rows[-1][1] else rows[0]

    # The near-tangent lint's lines: a block or a stretch of hairline whose edge would run alongside a seam,
    # shut line or outline without meeting it is left out, so every edge clearly crosses or avoids them.
    lines = geom.edges(kit, margin=p.edge_margin_px)

    def clear(g: Polygon) -> bool:
        return lines.clear(geom.footprint(kit, view, geom.as_car(g, k)))

    items: list[tuple[Polygon, str]] = []
    for y0, y1, b1, b2 in rows:
        items.append((shapely.box(b1, y0, b2, y1), TERRA))
        items.append((shapely.box(b2, y0, U_HI, y1), PAPER))

    # Hairlines through the black and the paper, under every block, in stretches of a big block so one
    # running alongside a line drops out on its own.
    hp, hw = p.hair_period_px / S, _snap(p.hair_px / S, q / 2)
    yy = _snap(y_lo, q)
    while yy < y_hi:
        r = row_at(yy + hw / 2)
        for a, b, c in ((U_LO, r[2], BLACK_T), (r[3], U_HI, PAPER_D)):
            x = a
            while x < b - 1e-9:
                seg = shapely.box(x, yy, min(b, x + B), yy + hw)
                if clear(seg):
                    items.append((seg, c))
                x += B
        yy += hp

    # The number blocks: solid, and no block crosses them.
    if side:
        u0, v0, u1, v1 = kit.feature("number_l" if view == "side_l" else "number_r").box
        pad = 3 * q
        num = shapely.box(
            _snap(u0, q) - pad, _snap(v0 * k, q) - pad, _snap(u1, q) + pad, _snap(v1 * k, q) + pad
        )
        solid = [(num, TERRA_D)]
    else:
        u0, w0, u1, w1 = kit.feature("number_top").box
        pad = 3 * q
        num = shapely.box(
            _snap(u0, q) - pad, _snap(w0 * k, q) - pad, _snap(u1, q) + pad, _snap(w1 * k, q) + pad
        )
        solid = [(num, BLACK)]
    keep_out = shapely.union_all([g for g, _ in solid])

    def place(cx: float, cy: float):
        """Which field a point is in (0 black, 1 core, 2 paper), how far from the nearest split, and whether
        that split is the rear one."""
        _, _, b1, b2 = row_at(cy)
        f = 0 if cx < b1 else (1 if cx < b2 else 2)
        d1, d2 = abs(cx - b1), abs(cx - b2)
        return f, min(d1, d2), d2 < d1

    def colours(tier: str, f: int, d: float, rear: bool, near: float) -> dict[str, float]:
        if f == 0:  # the black: its tone, and terracotta embers reaching forward from the core
            if tier == "field":
                return {BLACK_T: 0.5, TERRA_D: 0.35, TERRA: 0.15}
            return {BLACK_T: 0.3, TERRA: 0.25 + 0.5 * near, TERRA_D: 0.35}
        if f == 2:  # the paper: its two tones, and terracotta reaching back
            if tier == "field":
                return {PAPER_D: 0.5, PAPER_L: 0.4, TERRA: 0.1}
            return {PAPER_D: 0.35, PAPER_L: 0.3, TERRA: 0.2 + 0.5 * near, TERRA_D: 0.1 * near}
        if tier == "field":  # the core's own big tone blocks
            return {TERRA_D: 1.0}
        # the core: its dark tone, and the neighbour on the near side reaching in
        out = {
            TERRA_D: 0.5,
            TERRA: 0.15,
            BLACK: 0.0 if rear else 0.45 * near,
            PAPER: 0.45 * near if rear else 0.0,
        }
        if tier == "dust":
            out[PAPER_L if rear else BLACK_T] = 0.25
        return out

    # (name, size, widths and heights in sizes, chance at d)
    tiers = [
        (
            "field",
            B,
            [1.0, 1.5, 2.0, 3.0],
            [0.5, 1.0, 1.0],
            lambda f, d: (p.black_density, p.core_fill, p.field_density)[f],
        ),
        (
            "big",
            B,
            [1.0, 1.5, 2.0, 2.0, 3.0],
            [0.5, 1.0, 1.0, 1.5],
            lambda f, d: p.big_density * _falloff(d, p.big_reach),
        ),
        (
            "mid",
            M,
            [1.0, 1.0, 2.0, 3.0],
            [1.0, 1.0, 2.0],
            lambda f, d: (p.core_fill if f == 1 else p.mid_floor) + p.mid_density * _falloff(d, p.mid_reach),
        ),
        (
            "dust",
            D,
            [1.0, 1.0, 2.0],
            [1.0],
            lambda f, d: p.dust_density * _falloff(d, p.dust_reach) if f == 1 else 0.0,
        ),
    ]
    for name, T, wm, hm, chance in tiers:
        tr = p.rng(f"tier.{name}.{view}")
        nx = math.ceil((U_HI - U_LO) / T)
        ny = math.ceil((y_hi - y_lo) / T)
        ox, oy = _snap(U_LO, q), _snap(y_lo, q)
        for j in range(ny):
            for i in range(nx):
                r = tr.random(4)  # four draws per cell, placed or not
                x0, y0 = ox + i * T, oy + j * T
                w = _snap(T * wm[int(r[2] * len(wm))], q)
                h = _snap(T * hm[int(r[3] * len(hm))], q)
                f, d, rear = place(x0 + w / 2, y0 + h / 2)
                if r[0] >= chance(f, d):
                    continue
                rect = shapely.box(x0, y0, x0 + w, y0 + h)
                if rect.intersects(keep_out):
                    continue
                reach = {"big": p.big_reach, "mid": p.mid_reach, "dust": p.dust_reach}.get(name, 1.0)
                c = _pick(r[1], colours(name, f, d, rear, max(0.0, 1.0 - d / reach)))
                if c is not None and clear(rect):
                    items.append((rect, c))

    # The sill band, stepping up and down, over every block; the number blocks on top.
    sill_top = None
    if side:
        srng = p.rng(f"sill.{view}")
        pts = [(U_LO, y_lo)]
        u = U_LO
        while u < U_HI:
            top = _snap(p.sill_top * k + srng.choice([-2, -1, 0, 0, 1, 2]) * q, q)
            nxt = min(U_HI, u + _snap(B * srng.choice([1.0, 2.0, 3.0, 4.0]), q))
            pts += [(u, top), (nxt, top)]
            u = nxt
        pts.append((U_HI, y_lo))
        items.append((Polygon(pts), BLACK))
        sill_top = geom.as_car(LineString(pts[1:-1]), k)
    items += solid

    # What shows of each colour: later items cover earlier ones.
    covered = Polygon()
    shows: dict[str, list] = {}
    for g, c in reversed(items):
        vis = g.difference(covered)
        if not vis.is_empty:
            shows.setdefault(c, []).append(vis)
        covered = covered.union(g)
    layers = {c: [geom.as_car(shapely.union_all(gs), k)] for c, gs in shows.items()}
    return layers, sill_top


def _detail(kit, p: Params) -> list:
    """The raced-car layer: small roster sponsors one colour per field, race markings, tech labels."""
    out = []
    # Sills: paper on the black band, ahead of the carbon side blade.
    out += mirror_sides(
        sponsor,
        kit,
        "sill_door",
        "telvio",
        (p.sill_u0, p.sill_v),
        PAPER,
        height=p.sill_h,
        variant="mark",
        z=7,
    )
    out += mirror_sides(
        sponsor_group,
        kit,
        "sill_rear_sponsors",
        [("huilec", "wordmark")],
        start=(0.665, p.sill_v),
        end=(0.765, p.sill_v),
        height=p.sill_h,
        color=PAPER,
        z=7,
    )
    out += mirror_sides(
        sponsor_group,
        kit,
        "sill_front_sponsors",
        [("grivant", "mark")],
        start=(0.33, p.sill_v),
        end=(0.38, p.sill_v),
        height=p.sill_h,
        color=PAPER,
        z=7,
    )
    # Behind the rear wheel: black on the paper.
    out += mirror_sides(
        sponsor_group,
        kit,
        "tail_sponsors",
        [("ostrand", "mark"), ("rigmount", "mark")],
        start=(0.975, 0.68),
        end=(0.975, 0.27),
        height=0.024,
        color=BLACK,
        z=7,
        axis="column",
    )
    # Bonnet front: paper on the black; rear deck: black on the paper.
    out += sponsor_group(
        kit,
        "bonnet_sponsors",
        "top",
        [("hexmoor", "mark"), ("avelor", "wordmark"), ("zentiq", "mark")],
        start=(0.045, -0.3),
        end=(0.045, 0.3),
        height=0.026,
        color=PAPER,
        z=7,
    )
    out += sponsor_group(
        kit,
        "deck_sponsors",
        "top",
        [("drexa", "wordmark"), ("brandvik", "wordmark")],
        start=(0.978, -0.36),
        end=(0.978, 0.36),
        height=0.022,
        color=BLACK,
        z=7,
    )
    # Tech labels on the paper haunch, in the muted grey, one start edge.
    for view in kit.side_views:
        out += typeset.type_block(
            kit,
            f"tech.{view}",
            view,
            [
                typeset.Line("FP-03", 0.022, font="Space Mono", weight=700),
                typeset.Line("GT3 EVO", 0.016, font="Space Mono", weight=700),
                typeset.Line("T-2048", 0.016, font="Space Mono", weight=700),
            ],
            at=(p.tech_u, p.tech_v),
            color=MUTED,
            plate=PAPER_L,
            pad=0.008,
            z=7,
        )
    # Race markings in each field's contrast colour.
    for view in kit.side_views:
        out.append(marks(kit, f"tow_f.{view}", "tow", (view, 0.12, 0.765), PAPER, 0.02, toward="nose", z=7))
        out.append(marks(kit, f"ext.{view}", "extinguisher", (view, 0.36, 0.62), PAPER, 0.022, z=7))
    return out


def _rear_number_block(kit, p: Params) -> list:
    """A solid #6B3528 block under the sim's rear number on the paper rear face, one more block of the
    collage. No view maps the rear face, so it is drawn in template pixels round the kit's number box."""
    area = body.region(kit, "rear_face")
    out = []
    for zone in carspace.kit_data(kit).get("keep_clear", []):
        if zone["kind"] != "number":
            continue
        x0, y0, x1, y1 = zone["box"]
        if not area.contains(shapely.Point((x0 + x1) / 2, (y0 + y1) / 2)):
            continue
        r = p.rear_number_pad
        raw = f'<rect x="{x0 - r}" y="{y0 - r}" width="{x1 - x0 + 2 * r}" height="{y1 - y0 + 2 * r}" fill="{TERRA_D}"/>'
        out.append(clip_to(kit, "rear_face", svg(raw, "rear_number_block", z=1)))
    return out


def build(kit, p: Params):
    els = [
        # Parts no view maps: the rear face and the wing in the paper, with the collage's tones.
        panel(kit, "rear_face", "rear_face_all", PAPER, z=0),
        patterns.scatter(
            kit,
            "rear_face_blocks",
            "rear_face_all",
            [(40.0, 0.25), (16.0, 0.1)],
            {PAPER_D: 0.5, PAPER_L: 0.3, TERRA: 0.2},
            p.rng("rear_face"),
            z=0,
        ),
        panel(kit, "wing", "wing", PAPER, z=0),
        patterns.scatter(
            kit,
            "wing_blocks",
            "wing_top",
            [(96.0, 0.2), (40.0, 0.2)],
            {PAPER_D: 0.5, PAPER_L: 0.4, TERRA: 0.1},
            p.rng("wing"),
            z=0,
        ),
    ]
    # Parts whose maps stop short of their outlines are painted whole on their own, and the collage cut off
    # them, so its edge is the island's: the sill strips (black, under the sill band) and the splitter top.
    whole = {"side_l": ["sill_strip_l"], "side_r": ["sill_strip_r"], "top": ["splitter_top"]}
    els += [
        panel(kit, "sill_strips_l", "sill_strip_l", BLACK, z=1),
        panel(kit, "sill_strips_r", "sill_strip_r", BLACK, z=1),
        panel(kit, "splitter_top", "splitter_top", BLACK, z=1),
    ]
    for view in kit.views:
        layers, sill_top = _collage(kit, view, p)
        collage = geom.fill(kit, f"collage.{view}", view, layers, z=1)
        els.append(clip_out(kit, whole[view], collage) if view in whole else collage)
        if sill_top is not None:
            # The terracotta pinline on the sill band's stepped top edge.
            sill = Polygon([(U_LO, -0.5), *sill_top.coords, (U_HI, -0.5)])
            els += geom.outline(kit, f"sill_pin.{view}", view, sill, [(14.0, TERRA)], along=sill_top, z=2)
    els += _rear_number_block(kit, p)
    els += _detail(kit, p)
    els += [
        panel(kit, "mirrors", "mirrors", TERRA, z=5),
        panel(kit, "lip", "splitter_lip", TERRA, z=5),
        panel(kit, "strakes", "intake_strakes", TERRA, z=5),
        panel(kit, "endplates", "wing_endplates", BLACK, z=5),
        patterns.scatter(
            kit,
            "endplate_blocks",
            "wing_endplates",
            [(40.0, 0.35), (16.0, 0.2)],
            {PAPER: 0.6, PAPER_L: 0.4},
            p.rng("endplates"),
            z=6,
        ),
    ]
    return base(BLACK).add(*els)
