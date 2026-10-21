"""Fireproof v3b: 8-bit linework. A warm black McLaren covered in panels of pixel linework in Flexoki
terracotta, after SPIRIT's geometric panels (references.md 34), with every line drawn as hard pixels.

Each view is cut into raked panels by recursive splits in the view as seen (v or w scaled into units of u,
so a pixel is square). Panels are largest, and full terracotta, along one band raked with the lean (low on
the front fender, up over the door top and haunch, over the roof); towards the lower nose and the lower
tail they get smaller, dim and quiet. Every panel is rasterised onto one pixel grid in SPIRIT's nested
frames: a stepped frame in its own ink, black, a border row, black, an inner line, black, and a core
pattern; the hottest panels are solid terracotta with black stair lines cut in. The black between panels,
the lower body under the intake crease, the number windows and the yellow hardware are the only solid
colours.
"""

import math
from dataclasses import dataclass, replace

import numpy as np
import shapely
import shapely.ops
from shapely.geometry import Polygon

from livery import body, carspace, geom
from livery.params import DesignParams, param
from livery.primitives import base, mirror_sides, panel, sponsor_group, svg

KIT = "mclaren-720s-gt3-evo"

BLACK = "#100F0F"
WEAVE = "#1C1B1A"
TERRA = "#CF6A4C"
DIM = "#6B3528"
PAPER = "#DAD8CE"
# The contrast colour, from the theme (the owner, 21 Oct: no cyan; try these side by side and pick).
CONTRASTS = ("#D0A215", "#F2F0E5", "#8B7EC8", "#CE5D97")  # yellow, bright paper, purple, magenta

PATTERNS = ("hatch", "saw", "diamond", "dots", "chevron", "embers")
QUIET = ("dots", "embers", "diamond")  # the dim panels' cores
BANDS = ("rope", "ladder", "teeth")


@dataclass(frozen=True)
class Params(DesignParams):
    cell: float = param(0.0085, 0.005, 0.014)  # one pixel, in units of u as seen
    lean: float = param(28.0, 0.0, 60.0)  # the raked cuts, degrees from upright, tops towards the tail
    band_v0: float = param(0.44, 0.2, 0.7)  # the hot band's centre at the nose (v)
    band_dv: float = param(0.46, 0.0, 0.8)  # how far it climbs to the tail (v)
    band_w: float = param(0.17, 0.08, 0.5)  # its half width (v)
    top_u: float = param(0.47, 0.3, 0.7)  # on top, the band's centre over the roof (u)
    top_sweep: float = param(0.35, 0.0, 0.8)  # how far back it sweeps at the outer edge (u per w)
    top_w: float = param(0.22, 0.05, 0.5)  # its half width on top (u)
    big: float = param(0.05, 0.005, 0.12)  # a panel's area at the band's heart (u² as seen)
    small: float = param(0.014, 0.001, 0.04)  # its area far from the band
    hot: float = param(0.55, 0.1, 0.9)  # heat above which a panel is terracotta, else #6B3528
    solid: float = param(0.75, 0.3, 1.01)  # heat above which a panel may be solid with a fine pattern
    solid_share: float = param(1.0, 0.0, 1.0)  # of the panels hot enough, how many are solid
    contrast: int = param(0, 0, 3)  # which theme colour is the contrast (CONTRASTS)
    accent_share: float = param(0.5, 0.0, 1.0)  # of nose and tail panels with an inner line in the contrast
    gap: float = param(2.0, 0.6, 4.0)  # black between panels, in cells
    window_pad: float = param(1.0, 0.5, 4.0)  # black round the number, in cells
    window_lean: float = param(14.0, 0.0, 40.0)  # the number windows' rake, degrees
    bracket: float = param(4.5, 2.0, 12.0)  # the number windows' corner brackets, arm length in cells
    sill_v: float = param(0.24, 0.1, 0.35)  # the lower body's top on the door (v)
    crease_pad: float = param(0.06, 0.0, 0.15)  # the lower body's top above the intake crease (v)
    mark_v: float = param(0.14, 0.05, 0.2)  # the door sill marks' centre line (v)
    mark_u1: float = param(0.49, 0.47, 0.52)  # where their row ends (u)


# --- the panel layout ------------------------------------------------------------------------------------


def _half_plane(p0, d, side: int, size: float = 10.0) -> Polygon:
    """The half plane left (side 1) or right (-1) of the line through p0 along d."""
    dx, dy = d
    n = math.hypot(dx, dy)
    dx, dy = dx / n, dy / n
    nx, ny = -dy * side, dx * side
    x, y = p0
    a = (x - dx * size, y - dy * size)
    b = (x + dx * size, y + dy * size)
    return Polygon([a, b, (b[0] + nx * size, b[1] + ny * size), (a[0] + nx * size, a[1] + ny * size)])


def _split(poly: Polygon, heat, p: Params, rng, dirs, depth: int = 0) -> list[Polygon]:
    """Cut a region into panels: while a piece is larger than its heat allows, cut it once, raked, along
    the band or corner to corner, near its middle."""
    if poly.is_empty or poly.area <= 0:
        return []
    c = poly.centroid
    h = heat(c.x, c.y)
    target = p.small + (p.big - p.small) * h**1.5
    r1, r2, r3 = rng.random(), rng.random(), rng.random()
    if poly.area < target or depth > 12:
        return [poly]
    x0, y0, x1, y1 = poly.bounds
    w, t = x1 - x0, y1 - y0
    raked, along = dirs(c.y)
    if w > 2.4 * t:
        d = raked
    elif t > 1.3 * w:
        d = along
    elif r1 < 0.45:
        d = raked
    elif r1 < 0.8:
        d = along
    else:
        d = (w, t if c.y >= 0 else -t)  # corner to corner: stepped triangles
    f = 0.35 + 0.3 * r2
    at = (x0 + w * (f if d is not raked else 0.5 + (r3 - 0.5) * 0.4), y0 + t * f)
    out = []
    for side in (1, -1):
        part = poly.intersection(_half_plane(at, d, side))
        for g in geom.polygons(part):
            out += _split(g, heat, p, rng, dirs, depth + 1)
    return out


# --- rasterising ------------------------------------------------------------------------------------------


def _grid(poly, c: float):
    x0, y0, x1, y1 = poly.bounds
    i0, j0 = math.floor(x0 / c) - 1, math.floor(y0 / c) - 1
    i1, j1 = math.ceil(x1 / c) + 1, math.ceil(y1 / c) + 1
    ii, jj = np.meshgrid(np.arange(i0, i1), np.arange(j0, j1))
    mask = shapely.contains_xy(poly, (ii + 0.5) * c, (jj + 0.5) * c)
    return mask, i0, j0


def _erode(m: np.ndarray) -> np.ndarray:
    out = m.copy()
    out[1:, :] &= m[:-1, :]
    out[:-1, :] &= m[1:, :]
    out[:, 1:] &= m[:, :-1]
    out[:, :-1] &= m[:, 1:]
    out[0, :] = out[-1, :] = False
    out[:, 0] = out[:, -1] = False
    return out


def _boxes(mask: np.ndarray, i0: int, j0: int, c: float) -> list[Polygon]:
    """The cells of a mask as row runs, as seen."""
    out = []
    for r, row in enumerate(mask):
        if not row.any():
            continue
        edge = np.diff(np.r_[0, row.astype(int), 0])
        for a, b in zip(np.nonzero(edge == 1)[0], np.nonzero(edge == -1)[0], strict=True):
            out.append(shapely.box((i0 + a) * c, (j0 + r) * c, (i0 + b) * c, (j0 + r + 1) * c))
    return out


def _pattern(kind: str, a: np.ndarray, b: np.ndarray, rng) -> np.ndarray:
    """One 8-bit core pattern on local cell indices: every stair climbs towards the tail."""
    if kind == "hatch":  # staircase hatching
        s, r = (1, 4) if rng.random() < 0.5 else (2, 4)
        return (b - a // s) % r == 0
    if kind == "saw":  # stair-step sawtooth rows, climbing then dropping
        s, steps, rows = 2, 3, 6
        y = (a % (s * steps)) // s
        line = (b - y) % rows == 0
        drop = ((a % (s * steps)) == s * steps - 1) & ((b % rows) < steps)
        return line | drop
    if kind == "diamond":  # concentric stepped diamonds
        ac, bc = a.max() / 2, b.max() / 2
        return (np.round(np.abs(a - ac) + np.abs(b - bc)).astype(int)) % 4 == 0
    if kind == "dots":  # a sparse grid of single pixels, staggered
        return (a % 3 == 0) & ((b + (a // 3) % 2 * 2) % 4 == 0)
    if kind == "chevron":  # rows of pixel chevrons pointing back
        hgt = 7
        d = np.abs((b % hgt) - hgt // 2)
        return ((a + d) % 5 == 0) & ((b % hgt) != hgt - 1)
    # embers: a grid of 2x2 squares, some burnt out
    on = (a % 5 < 2) & (b % 5 < 2)
    keep = rng.random(a.shape) < 0.8
    return on & keep


def _band(kind: str, a: np.ndarray, b: np.ndarray, d: np.ndarray) -> np.ndarray:
    """A three-cell band running round inside a panel's frame (`d` 0-2 across it), SPIRIT's border rows
    as pixels: a rope of stair steps, a ladder of ticks, or a row of stepped teeth."""
    if kind == "rope":
        return (a + b) % 3 == 0
    if kind == "ladder":  # two rails and rungs
        return (d == 0) | (d == 2) | (a % 3 == 0)
    # teeth: stepped triangles standing on the band's outer edge
    return (d == 0) | ((d == 1) & ((a + b) % 4 != 0)) | ((d == 2) & ((a + b) % 4 == 1))


def _depth(mask: np.ndarray) -> np.ndarray:
    """Each cell's ring index from the mask's edge inward (0 on the edge)."""
    d = np.full(mask.shape, -1)
    cur, k = mask.copy(), 0
    while cur.any():
        d[cur] = k
        cur = _erode(cur)
        k += 1
    return d


def _rasterise(panels, heat, accent_at, p: Params, rng) -> dict[str, list[Polygon]]:
    """Each panel as cells by colour, as seen, in SPIRIT's nested frames: a solid stepped frame, black, a
    patterned band round the inside, black, an inner line (in the contrast colour on some nose and tail
    panels), black, and a core pattern. The hottest panels are solid ink with black linework cut in."""
    c = p.cell
    layers: dict[str, list[Polygon]] = {TERRA: [], DIM: [], "accent": []}
    for poly in panels:
        shrunk = poly.buffer(-p.gap * c / 2, join_style="mitre")
        if shrunk.is_empty:
            continue
        mask, i0, j0 = _grid(shrunk, c)
        if mask.sum() < 24:
            continue
        cen = poly.centroid
        h = heat(cen.x, cen.y)
        ink = TERRA if h >= p.hot else DIM
        r_kind, r_band, r_acc, r_phase = rng.random(), rng.random(), rng.random(), int(rng.integers(0, 8))
        d = _depth(mask)
        deep = int(d.max())
        jj, ii = np.nonzero(mask)
        a = np.zeros(mask.shape, int)
        b = np.zeros(mask.shape, int)
        a[jj, ii] = ii - ii.min() + r_phase
        b[jj, ii] = (jj - jj.min()) if cen.y >= 0 else (jj.max() - jj)
        fw = 2 if deep >= 9 else 1  # the frame's width
        on: dict[str, np.ndarray] = {ink: mask & (d < fw), "accent": np.zeros_like(mask)}
        core0 = fw + 1
        if ink == DIM:  # the dim panels stay quiet: a frame, an inner line and a sparse core
            if deep >= fw + 5:
                on[ink] |= mask & (d == fw + 2)
                core0 = fw + 4
            core = mask & (d >= core0)
            on[ink] |= core & _pattern(QUIET[int(r_kind * len(QUIET))], a, b, rng)
            for k, m in on.items():
                layers[k] += _boxes(m, i0, j0, c)
            continue
        if deep >= fw + 9:  # room for the band and the inner line
            band = mask & (d >= fw + 1) & (d <= fw + 3)
            on[ink] |= band & _band(BANDS[int(r_band * len(BANDS))], a, b, d - fw - 1)
            line = mask & (d == fw + 5)
            acc = accent_at(cen.x, cen.y) and r_acc < p.accent_share
            on["accent" if acc else ink] |= line
            core0 = fw + 7
        core = mask & (d >= core0)
        if h >= p.solid:  # solid ink with black stair lines cut in
            on[ink] |= core & ((b - a // 2) % 5 != 0)
        else:
            on[ink] |= core & _pattern(PATTERNS[int(r_kind * len(PATTERNS))], a, b, rng)
        for k, m in on.items():
            layers[k] += _boxes(m, i0, j0, c)
    return layers


def _paint(kit, id: str, view: str, shape, color: str, z: int):
    """A car-space shape on one view as one element, its edges snapped onto or clear of any line the
    near-tangent lint would fail them against (`geom.snapped`), the moves listed in the lint report."""
    foot = geom.footprint(kit, view, shape)
    if foot.is_empty:
        return None
    foot, snaps = geom.snapped(kit, view, foot)
    # A stretch no snap clears (pixel steps meet the line and leave it again): the pixels within the lint's
    # reach of that stretch of line are left out, so the panel's edge there stops clear of it.
    e = geom.edges(kit, *geom.MARGINS)
    for _ in range(4):
        left = geom.failing(kit, foot)
        if not left:
            break
        cut = [
            shapely.ops.substring(e.b.lines[r.line], r.s0 - e.reach, r.s1 + e.reach).buffer(
                e.reach + 2, cap_style="flat"
            )
            for r in left
        ]
        foot = geom.clean(foot.difference(shapely.union_all(cut)))
    return replace(geom.paint(id, foot, color, z=z), car={"view": view}, snaps=snaps)


def _emit(kit, name: str, view: str, layers: dict[str, list[Polygon]], k: float, z: int, accent: str) -> list:
    out = []
    for key, boxes in layers.items():
        if not boxes:
            continue
        ink = accent if key == "accent" else key
        seen = shapely.union_all(boxes)
        el = _paint(kit, f"{name}-{key.strip('#')}.{view}", view, geom.as_car(seen, k), ink, z)
        if el is not None:
            out.append(el)
    return out


def _window(box, k: float, pad: float, lean: float) -> Polygon:
    """A number window as seen: the zone grown by `pad`, raked as a parallelogram."""
    u0, a0, u1, a1 = box
    y0, y1 = a0 * k - pad, a1 * k + pad
    u0, u1 = u0 - pad, u1 + pad
    s = math.tan(math.radians(lean))
    mid = (y0 + y1) / 2
    return Polygon(
        [
            (u0 + s * (y0 - mid) - s * (y1 - y0) / 2, y0),
            (u1 + s * (y0 - mid) + s * (y1 - y0) / 2, y0),
            (u1 + s * (y1 - mid) + s * (y1 - y0) / 2, y1),
            (u0 + s * (y1 - mid) - s * (y1 - y0) / 2, y1),
        ]
    )


def _brackets(win: Polygon, c: float, arm: float) -> list[Polygon]:
    """Stepped one-cell corner brackets round a window: the window's ring kept within `arm` cells of each
    corner, so the window reads as framed without a heavy box."""
    grown = win.buffer(c, join_style="mitre")
    outer, i0, j0 = _grid(grown, c)
    xx, yy = np.meshgrid(
        (np.arange(outer.shape[1]) + i0 + 0.5) * c, (np.arange(outer.shape[0]) + j0 + 0.5) * c
    )
    inner = shapely.contains_xy(win, xx, yy)
    near = np.zeros_like(outer)
    for x, y in list(grown.exterior.coords)[:-1]:
        near |= np.hypot(xx - x, yy - y) < arm * c
    return _boxes(outer & ~inner & near, i0, j0, c)


# --- views -------------------------------------------------------------------------------------------------


def _side(kit, view: str, p: Params) -> list:
    k = carspace.side_aspect(kit)
    s = view[-1]
    slope = math.tan(math.radians(p.lean))

    def vc(u):
        return p.band_v0 + p.band_dv * u

    def heat(x, y):
        return math.exp(-((((y / k) - vc(x)) / p.band_w) ** 2))

    def dirs(_y):
        return (slope, 1.0), (1.0, p.band_dv * k)

    def accent_at(x, y):
        return x < 0.16 or x > 0.9

    # The lower body: under the door's sill line, then under the intake crease, rising to the tail.
    crease = kit.feature(f"intake_crease_{s}").coords
    (cu0, _), (cu1, cv1) = crease[0], crease[-1]
    lower = Polygon(
        [
            (0.13, -0.2),
            (0.13, p.sill_v),
            (cu0, p.sill_v),
            (cu1, cv1 + p.crease_pad),
            (0.95, cv1 + p.crease_pad),
            (1.1, 0.40),
            (1.1, -0.2),
        ]
    )
    win = _window(kit.feature(f"number_{s}").box, k, p.window_pad * p.cell, p.window_lean)
    light = shapely.box(*kit.feature(f"door_light_panel_{s}").box)
    mirror = (
        shapely.box(*kit.feature(f"mirror_base_{s}").box)
        .buffer(0.01)
        .union(shapely.box(*kit.feature(f"mirror_shell_{s}").box))
    )
    mirror = shapely.box(mirror.bounds[0], mirror.bounds[1], mirror.bounds[2], 1.2)
    keep = shapely.union_all(
        [
            geom.as_seen(lower, k),
            win.buffer(2 * p.cell, join_style="mitre"),
            geom.as_seen(light, k).buffer(p.cell, join_style="mitre"),
            geom.as_seen(mirror, k),
        ]
    )
    region = shapely.box(-0.08, -0.02, 1.08, 1.1 * k).difference(keep)
    panels = []
    for g in geom.polygons(region):
        # Both sides draw from one stream, so they carry the same layout.
        panels += _split(g, heat, p, p.rng("split-r"), dirs)
    acc = CONTRASTS[p.contrast]
    layers = _rasterise(panels, heat, accent_at, p, p.rng("cells-r"))
    out = _emit(kit, "panels", view, layers, k, 3, acc)
    # The solid fields: the lower body and the number window in black over the weave, corner brackets in
    # the contrast colour.
    out += [
        el
        for el in (
            _paint(kit, f"lower.{view}", view, lower, BLACK, z=2),
            _paint(kit, f"window.{view}", view, geom.as_car(win, k), BLACK, z=2),
        )
        if el is not None
    ]
    out += _emit(kit, "bracket", view, {"accent": _brackets(win, p.cell, p.bracket)}, k, 3, acc)
    return out


def _top(kit, p: Params) -> list:
    k = carspace.aspect(kit, "top")
    slope = math.tan(math.radians(p.lean))

    def heat(x, y):
        w = abs(y) / k
        return math.exp(-(((x - (p.top_u + p.top_sweep * w)) / p.top_w) ** 2))

    def dirs(y):
        sg = 1.0 if y >= 0 else -1.0
        return (slope, sg), (1.0, 0.0)

    def accent_at(x, _y):
        return x < 0.12 or x > 0.92

    acc = CONTRASTS[p.contrast]
    win = _window(kit.feature("number_top").box, k, p.window_pad * p.cell, 0.0)
    region = shapely.box(-0.08, -1.05 * k, 1.13, 1.05 * k).difference(
        win.buffer(2 * p.cell, join_style="mitre")
    )
    panels = []
    for g in geom.polygons(region):
        panels += _split(g, heat, p, p.rng("split-top"), dirs)
    layers = _rasterise(panels, heat, accent_at, p, p.rng("cells-top"))
    out = _emit(kit, "panels", "top", layers, k, 3, acc)
    out += [el for el in (_paint(kit, "window.top", "top", geom.as_car(win, k), BLACK, z=2),) if el]
    out += _emit(kit, "bracket", "top", {"accent": _brackets(win, p.cell, p.bracket)}, k, 3, acc)
    return out


def _tile(color: str, px: float, cells: list[tuple[int, int]], n: int) -> str:
    c = px / n
    return "".join(
        f'<rect x="{i * c:.2f}" y="{j * c:.2f}" width="{c:.2f}" height="{c:.2f}" fill="{color}"/>'
        for i, j in cells
    )


def _tiled(kit, id: str, region: str, tile: str, tile_px: float, z: int):
    """A region filled with a texture-space tile, kept out of the lint's reach of every paint edge, shut
    line and number zone (`geom.edges`, with the library's own margins), and off the number zones: the
    tile's pixels then either run on across a seam or stop clear of the line, never alongside it."""
    e = geom.edges(kit, *geom.MARGINS)
    cut = shapely.union_all([e.near, *[z_.buffer(e.reach) for z_ in e.zones]])
    area = body.region(kit, region).difference(cut)
    d = carspace.path_d(area)
    raw = (
        f'<pattern id="pat-{id}" patternUnits="userSpaceOnUse" width="{tile_px}" height="{tile_px}">'
        f'{tile}</pattern><path fill-rule="evenodd" d="{d}" fill="url(#pat-{id})"/>'
    )
    return svg(raw, id, z=z)


def build(kit, p: Params):
    cpx = p.cell * carspace.px_per_unit(kit, "top")[0]  # one cell in texture px
    weave = _tile(WEAVE, cpx * 2, [(0, 0), (1, 0), (2, 2), (2, 3)], 4)
    stairs = _tile(DIM, cpx * 2, [(0, 0), (1, 1), (2, 2), (3, 3)], 4)
    els = [_tiled(kit, f"weave-{v}", v, weave, cpx * 2, z=1) for v in ("side_l", "side_r", "top")]
    els += [
        _tiled(kit, f"tail-{r}", r, stairs, cpx * 2, z=1)
        for r in ("rear_face_all", "front_bumper_corner_l", "front_bumper_corner_r")
    ]
    for view in ("side_l", "side_r"):
        els += _side(kit, view, p)
    els += _top(kit, p)
    els += [
        panel(kit, "strakes", "intake_strakes", TERRA, z=5),
        panel(kit, "lip", "splitter_lip", CONTRASTS[p.contrast], z=5),
        panel(kit, "mirrors", "mirrors", CONTRASTS[p.contrast], z=5),
        panel(kit, "endplates", "wing_endplates", CONTRASTS[p.contrast], z=5),
    ]
    els += mirror_sides(
        sponsor_group,
        kit,
        "sill",
        [("grivant", "mark"), ("huilec", "mark"), ("telvio", "mark")],
        start=(0.42, p.mark_v),
        end=(p.mark_u1, p.mark_v),
        height=0.02,
        color=PAPER,
        z=6,
    )
    els += mirror_sides(
        sponsor_group,
        kit,
        "duct",
        [("ostrand", "mark"), ("rigmount", "mark"), ("hexmoor", "mark")],
        start=(0.668, 0.13),
        end=(0.765, 0.13),
        height=0.022,
        color=PAPER,
        z=6,
    )
    return base(BLACK).add(*els)
