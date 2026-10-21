"""Hexmoor, a tools brand: HEXMOOR in Barlow Condensed Bold caps knocked out of a solid bar, and a hex
socket seen end on. The hand-made touch: both O's are drawn as sockets, a round outside with a hex
counter; the M's lower counter is squared off under its V, the crotches are blunted like ink traps and
the bar's corners are cut like a nut's.

uv run python assets/sponsors/hexmoor/draw.py
"""

import math
from pathlib import Path

from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont
from shapely import affinity
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

from livery import sponsors
from livery.config import REPO

HERE = Path(__file__).resolve().parent
FONT = REPO / "assets" / "fonts" / "barlow-condensed" / "BarlowCondensed-Bold.ttf"
SIZE = 100.0  # caps are 70 tall
GAP = 13.0  # bar between letters
PAD_X, PAD_Y = 15.0, 12.0  # bar round the caps
CHAMFER = 11.0
OPEN = 4.8  # blunts the bar's tips in the crotches, so they hold at sill size
NUT_RX, NUT_RY, NUT_ROUND = 10.5, 24.0, 3.0  # the O's hex counter


class FlattenPen(BasePen):
    """Glyph contours as lists of points, curves sampled."""

    def __init__(self, glyphs, steps=16):
        super().__init__(glyphs)
        self.contours, self.steps = [], steps

    def _moveTo(self, p):
        self.contours.append([p])

    def _lineTo(self, p):
        self.contours[-1].append(p)

    def _curveToOne(self, p1, p2, p3):
        p0 = self.contours[-1][-1]
        for i in range(1, self.steps + 1):
            t = i / self.steps
            u = 1 - t
            self.contours[-1].append(
                tuple(
                    u**3 * a + 3 * u * u * t * b + 3 * u * t * t * c + t**3 * d
                    for a, b, c, d in zip(p0, p1, p2, p3, strict=True)
                )
            )

    def _qCurveToOne(self, p1, p2):
        p0 = self.contours[-1][-1]
        for i in range(1, self.steps + 1):
            t = i / self.steps
            u = 1 - t
            self.contours[-1].append(
                tuple(u * u * a + 2 * u * t * b + t * t * c for a, b, c in zip(p0, p1, p2, strict=True))
            )


def glyph(ch: str):
    """A letter as a shapely shape, y down with the baseline at 0, and its advance."""
    font = TTFont(str(FONT))
    glyphs = font.getGlyphSet()
    g = glyphs[font.getBestCmap()[ord(ch)]]
    pen = FlattenPen(glyphs)
    g.draw(pen)
    shape = Polygon()
    for c in pen.contours:
        if len(c) >= 3:
            shape = shape.symmetric_difference(Polygon(c).buffer(0))
    s = SIZE / font["head"].unitsPerEm
    return affinity.scale(shape, s, -s, origin=(0, 0)), g.width * s


def hexagon(cx, cy, rx, ry, flat_top=False):
    turn = 0 if flat_top else math.pi / 2
    return Polygon(
        [
            (cx + rx * math.cos(turn + k * math.pi / 3), cy + ry * math.sin(turn + k * math.pi / 3))
            for k in range(6)
        ]
    )


def word():
    """The bar with the letters knocked out; each O knocked out whole, then its hex counter put back."""
    holes, nuts, x = [], [], 0.0
    for ch in "HEXMOOR":
        shape = glyph(ch)[0]
        if ch == "O":
            shape = Polygon(shape.exterior)
        if ch == "M":  # the ears of its lower counter can't hold at sill size: square it off under the V
            hull = shape.convex_hull
            notch, under = sorted(hull.difference(shape).geoms, key=lambda p: p.bounds[3])
            mid = (under.bounds[0] + under.bounds[2]) / 2
            vertex = under.intersection(LineString([(mid, -70), (mid, 0)])).bounds[1]
            ux0, _, ux1, _ = under.intersection(LineString([(0, vertex / 2), (100, vertex / 2)])).bounds
            shape = hull.difference(notch).difference(box(ux0, vertex, ux1, 0))
        x0, y0, x1, y1 = shape.bounds
        shape = affinity.translate(shape, x - x0)
        holes.append(shape)
        if ch == "O":
            nut = hexagon(x + (x1 - x0) / 2, (y0 + y1) / 2, NUT_RX, NUT_RY)
            nuts.append(nut.buffer(-NUT_ROUND).buffer(NUT_ROUND))
        x += x1 - x0 + GAP
    x -= GAP
    x0, y0, x1, y1 = -PAD_X, -70 - PAD_Y, x + PAD_X, PAD_Y
    c = CHAMFER
    bar = Polygon(
        [
            (x0 + c, y0),
            (x1 - c, y0),
            (x1, y0 + c),
            (x1, y1 - c),
            (x1 - c, y1),
            (x0 + c, y1),
            (x0, y1 - c),
            (x0, y0 + c),
        ]
    )
    return unary_union([bar.difference(unary_union(holes)).buffer(-OPEN).buffer(OPEN), *nuts])


def mark():
    disc = Polygon([(50 * math.cos(k * math.pi / 64), 50 * math.sin(k * math.pi / 64)) for k in range(128)])
    return disc.difference(hexagon(0, 0, 26, 26))


def path_d(shape) -> str:
    polys = shape.geoms if hasattr(shape, "geoms") else [shape]
    rings = [r for p in polys for r in (p.exterior, *p.interiors)]
    return "".join("M" + "L".join(f"{x:.2f} {y:.2f}" for x, y in r.coords[:-1]) + "Z" for r in rings)


def svg(shape) -> str:
    return sponsors.svg_doc(f'<path fill="#000000" fill-rule="evenodd" d="{path_d(shape)}"/>', shape.bounds)


if __name__ == "__main__":
    w, m = svg(word()), svg(mark())
    lockup = sponsors.lockup_svg(m, w, gap=0.3, mark_scale=1.0)
    print(sponsors.save(HERE, {"lockup": lockup, "wordmark": w, "mark": m}))
