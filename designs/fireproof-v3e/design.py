"""Fireproof v3e, LOD: the game can't render the car as fast as it goes.

50 m: a paper car with five black raked slashes, each with a terracotta trailing edge, from the nose to the
tail; they carry over the top as chevron bars across bonnet, roof, engine cover and rear deck.
10 m: the same slash at five levels of detail: a crisp vector bar on the nose (LOD 0), then each one
rasterised on a grid twice as coarse as the one in front, anti-aliased with half tones, down to a few huge
blocks at the tail that break off backwards. The black sill is one height and its top edge is rasterised
with the slash above it.
1 m: the half tones of the anti-aliasing, LOD labels, small sponsors.
"""

import itertools
import math
from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry import Polygon

from livery import carspace, geom
from livery.params import DesignParams, param
from livery.primitives import base, clip_out, panel, sponsor_group
from livery.typeset import Line, type_block

KIT = "mclaren-720s-gt3-evo"
FONT = "Space Mono"
PAPER, INK = "#DAD8CE", "#100F0F"
TC, TC_DK = "#CF6A4C", "#6B3528"
GREY_L, GREY = "#B7B5AC", "#878580"
SIDES = ("side_l", "side_r")
LO, HI = -0.4, 1.4  # how far past the maps (v on the sides, |w| on top) the shapes run
DOOR = (0.6, 0.5)  # where the side's texture scale is measured


@dataclass(frozen=True)
class Params(DesignParams):
    lean: float = param(30.0, 10.0, 50.0)  # every slash, degrees as seen, top towards the tail
    top_lean: float = param(15.0, 0.0, 30.0)  # the bars on top, outer ends towards the tail
    # LOD 1's pixel, car lengths; LOD n's is cell * ratio^(n-1). A ratio past 2 keeps the tail's blocks
    # reading bigger than the door's in the front three-quarter view, where perspective shrinks the tail.
    cell: float = param(0.009, 0.004, 0.02)
    ratio: float = param(2.3, 1.5, 3.0)
    pin_px: float = param(18.0, 14.0, 40.0)  # LOD 0's terracotta pinline, texture px
    trail: float = param(1.0, 0.5, 2.5)  # the terracotta trailing edge, in its LOD's pixels
    sill_v: float = param(0.17, 0.05, 0.3)
    # Each slash: its front edge where it crosses v 0.5 (s), and its width along u. Slash 1's bar on the
    # bonnet holds number_top, slash 2 number_l and number_r (both on solid black at these values).
    s1: float = param(0.11, 0.0, 0.2)
    w1: float = param(0.09, 0.04, 0.15)
    s2: float = param(0.385, 0.3, 0.45)
    w2: float = param(0.115, 0.06, 0.2)
    s3: float = param(0.62, 0.58, 0.7)
    w3: float = param(0.08, 0.05, 0.2)
    s4: float = param(0.79, 0.72, 0.9)
    w4: float = param(0.09, 0.05, 0.2)
    s5: float = param(0.95, 0.88, 1.05)
    w5: float = param(0.12, 0.05, 0.3)
    loose: float = param(0.55, 0.0, 1.0)  # how many blocks break off behind slashes 4 and 5
    reach: float = param(2.5, 1.0, 5.0)  # and how far, in their pixels
    # The LOD labels: em in car lengths, how high, and how far ahead of their slash.
    label_size: float = param(0.022, 0.016, 0.04)
    label_v: float = param(0.7, 0.4, 0.85)
    label_gap: float = param(0.012, 0.0, 0.05)
    top2_u: float = param(0.52, 0.47, 0.58)  # LOD 2 on the roof
    # Sponsors: the sill rows (centre v, height in car lengths).
    sill_text_v: float = param(0.095, 0.03, 0.15)
    sill_text_h: float = param(0.024, 0.01, 0.03)


# --- car-space shapes ------------------------------------------------------------------------------


def _slashes(p: Params) -> list[tuple[float, float]]:
    return [(p.s1, p.w1), (p.s2, p.w2), (p.s3, p.w3), (p.s4, p.w4), (p.s5, p.w5)]


def _cells(p: Params) -> list[float]:
    return [0.0] + [p.cell * p.ratio**n for n in range(4)]


def _trail(kit, p: Params, lod: int, view: str) -> float:
    """The terracotta trailing edge's width along u."""
    if lod == 0:
        sx = (
            carspace.px_per_unit(kit, "top")[0]
            if view == "top"
            else carspace.px_per_unit(kit, "side_l", DOOR)[0]
        )
        return p.pin_px / sx
    return p.trail * _cells(p)[lod]


def _band(kit, p: Params, view: str, a: float, b: float) -> Polygon:
    """The raked band between s = a and s = b: on a side u = s + k (v - 0.5); on top it is the band as
    it leaves the side (v 1) at |w| 1, a chevron pointing to the nose."""
    if view == "top":
        k = geom.rake(kit, p.lean)
        kt = geom.rake(kit, p.top_lean, "top")
        a, b = a + 0.5 * k, b + 0.5 * k
        ws = (-HI, 0.0, HI)
        front = [(a - kt * (1 - abs(w)), w) for w in ws]
        back = [(b - kt * (1 - abs(w)), w) for w in reversed(ws)]
        return Polygon(front + back)
    k = geom.rake(kit, p.lean)
    return Polygon(geom.slant(a - 0.5 * k, b - 0.5 * k, LO, HI, k))


def _zones(p: Params) -> list[tuple[float, float]]:
    """Each LOD's stretch of s, split halfway along the paper between one slash's trailing edge and the
    next slash."""
    cuts = [-1.0]
    for (s, w), (s2, _) in itertools.pairwise(_slashes(p)):
        cuts.append((s + w + s2) / 2 + 0.01)
    cuts.append(2.0)
    return list(itertools.pairwise(cuts))


# --- rasteriser ------------------------------------------------------------------------------------


def _classify(b: np.ndarray, t: np.ndarray) -> np.ndarray:
    """A cell's colour from how much of it is black (b) and terracotta (t): full cells take the field,
    partly covered ones a half tone, as an anti-aliased renderer would."""
    out = np.full(b.shape, "", dtype=object)
    out[b >= 0.14] = GREY_L
    out[b >= 0.38] = GREY
    out[(t >= 0.4) & (b < 0.2)] = TC
    out[(b + t >= 0.6) & (t >= 0.2) & (b >= 0.2)] = TC_DK
    out[t >= 0.6] = TC
    out[b >= 0.62] = INK
    return out


def _raster(black, tc, zone, behind, c: float, ky: float, loose: float, reach: float, rng) -> dict:
    """The zone's black and terracotta fields drawn on a grid of `c`-sized pixels (square as seen, where
    the second axis is scaled by `ky`), each pixel one colour; with `loose`, pixels break off into the
    paper `behind` the slash, fewer the farther they are."""
    b, t, z, bh = (geom.as_seen(g, ky) for g in (black, tc, zone, behind))
    x0, y0, x1, y1 = z.bounds
    y0, y1 = max(y0, LO * ky), min(y1, HI * ky)
    xs = np.arange(math.floor(x0 / c), math.ceil(x1 / c)) * c
    ys = np.arange(math.floor(y0 / c), math.ceil(y1 / c)) * c
    X, Y = (a.ravel() for a in np.meshgrid(xs, ys))
    boxes = shapely.box(X, Y, X + c, Y + c)
    hit = shapely.intersects(boxes, z)
    boxes, X, Y = boxes[hit], X[hit], Y[hit]
    area = c * c
    bc = shapely.area(shapely.intersection(boxes, b)) / area
    tcv = shapely.area(shapely.intersection(boxes, t)) / area
    col = _classify(bc, tcv)
    if loose > 0:
        centres = shapely.points(X + c / 2, Y + c / 2)
        d = shapely.distance(centres, shapely.union(b, t)) / c
        free = (col == "") & shapely.within(centres, bh)
        chance = loose * np.exp(-(d - 0.5) / reach) * free
        roll = rng.random(len(col))
        pick = rng.random(len(col))
        hit = roll < chance
        col[hit & (pick < 0.6)] = INK
        col[hit & (pick >= 0.6) & (pick < 0.85)] = TC
        col[hit & (pick >= 0.85)] = GREY
    out = {}
    for colour in (INK, TC, TC_DK, GREY, GREY_L):
        sel = col == colour
        if sel.any():
            g = shapely.union_all(boxes[sel]).intersection(z)
            out[colour] = geom.as_car(g, ky)
    return out


def _fields(kit, p: Params, view: str):
    """The black field (slashes and sill) and the terracotta trailing edges, in car space, per LOD."""
    out = []
    sill = Polygon([(-0.4, LO), (1.4, LO), (1.4, p.sill_v), (-0.4, p.sill_v)])
    for lod, (s, w) in enumerate(_slashes(p)):
        tw = _trail(kit, p, lod, view)
        black = _band(kit, p, view, s, s + w)
        tc = _band(kit, p, view, s + w, s + w + tw)
        if view != "top":
            black = black.union(sill)
            tc = tc.difference(sill)
        behind = _band(kit, p, view, s + w + tw, 3.0)
        out.append((black, tc, behind))
    return out


def _pixels(kit, p: Params, view: str):
    ky = carspace.aspect(kit, view)
    layers: dict[str, list] = {}
    cells = _cells(p)
    for lod, ((black, tc, behind), (z0, z1)) in enumerate(zip(_fields(kit, p, view), _zones(p), strict=True)):
        zone = _band(kit, p, view, z0, z1)
        if lod == 0:
            got = {INK: black.intersection(zone), TC: tc.intersection(zone)}
        else:
            loose = p.loose * (lod >= 3) * (0.5 if lod == 3 else 1.0)
            rng = p.rng(f"loose-{view if view == 'top' else 'side'}-{lod}")
            got = _raster(black, tc, zone, behind, cells[lod], ky, loose, p.reach, rng)
        for colour, g in got.items():
            if not g.is_empty:
                layers.setdefault(colour, []).append(g)
    return geom.fill(kit, f"pixels.{view}", view, {c: [shapely.union_all(gs)] for c, gs in layers.items()})


def _edge(kit, p: Params, s: float, v: float) -> float:
    """u of the raked edge through s (at v 0.5) at height v."""
    return s + geom.rake(kit, p.lean) * (v - 0.5)


def _label(kit, p: Params, lod: int, view: str, at, anchor) -> list:
    return type_block(
        kit,
        f"lod{lod}.{view}",
        view,
        [Line(f"LOD {lod}", p.label_size, FONT, 700)],
        at,
        GREY,
        lean=p.lean if view != "top" else 0.0,
        shear=0.0,
        anchor=anchor,
        facing="rear" if view == "top" else "front",  # the engine cover is read from the chase cam
        z=5,
    )


def _labels(kit, p: Params) -> list:
    """A renderer's debug overlay: LOD n in small mono type beside each slash on the paper. On the sides
    LOD 1 leads slash 2; on top LOD 2 leads the engine-cover bar from the roof. LOD 0, 3 and 4 have no
    clear paper beside them (notes.md)."""
    out = []
    u1 = _edge(kit, p, p.s2, p.label_v) - p.label_gap
    for view in SIDES:
        left = view == "side_l"
        out += _label(kit, p, 1, view, (u1, p.label_v), (1.0, 0.0) if left else (0.0, 0.0))
    out += _label(kit, p, 2, "top", (p.top2_u, 0.0), (0.5, 0.5))
    return out


def _sponsors(kit, p: Params) -> list:
    out = []
    for view in SIDES:
        out += sponsor_group(
            kit,
            f"sill-a.{view}",
            view,
            [("telvio", "mark"), ("avelor", "mark")],
            (0.32, p.sill_text_v),
            (0.395, p.sill_text_v),
            p.sill_text_h,
            PAPER,
            z=5,
        )
        out += sponsor_group(
            kit,
            f"sill-b.{view}",
            view,
            [("huilec", "wordmark"), ("drexa", "mark")],
            (0.665, p.sill_text_v),
            (0.765, p.sill_text_v),
            p.sill_text_h,
            PAPER,
            z=5,
        )
    return out


def build(kit, p: Params):
    els = [panel(kit, f"sill.{s}", f"sill_{s}", INK) for s in "lr"]
    els += [panel(kit, f"bumper.{s}", f"front_bumper_{s}", INK) for s in "lr"]
    # The rear face and the small number panels carry the sim's numbers: black under them for contrast.
    els += [panel(kit, "tail", "rear_face_all", INK), panel(kit, "number-panels", "number_panels", INK)]
    # The sill strips' maps stop short of their outlines: the sill panel paints them whole, so the pixels
    # leave them rather than end just inside their edge.
    els += [clip_out(kit, f"sill_strip_{v[-1]}", _pixels(kit, p, v)) for v in SIDES]
    els += [_pixels(kit, p, "top")]
    els += _labels(kit, p) + _sponsors(kit, p)
    els += [
        panel(kit, "splitter", "splitter_top", INK, z=1),
        panel(kit, "splitter-lip", "splitter_lip", TC, z=1),
        panel(kit, "mirrors", "mirrors", TC, z=1),
    ]
    return base(PAPER).add(*els)
