"""Norvane, a helmet maker. The wordmark is Titillium Web Black leaned forward, cut through by one visor
slit at mid height. The letters are redrawn to meet it: the notches of the N and the V and the inside of a
barless A run into the slit, so the V and the A mirror each other across it, and the E's arms are moved
clear of it. The mark is a helmet in profile, its visor cut out of it.

uv run python assets/sponsors/norvane/draw.py [sheet.png]
"""

import sys
from pathlib import Path

from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont
from shapely import affinity
from shapely.geometry import MultiPoint, Point, Polygon, box
from shapely.ops import unary_union

from livery import sponsors
from livery.config import REPO

HERE = Path(__file__).resolve().parent
FONT = REPO / "assets" / "fonts" / "titillium-web" / "TitilliumWeb-Black.ttf"
SLANT = 0.22  # x per unit of height
GAP = 95  # font units between neighbouring letters, at their closest
SLIT = (290, 370)  # the visor slit, font units above the baseline (caps are 670)
OPEN = 76  # the narrowest a notch may get


class _Rings(BasePen):
    """A glyph flattened into closed rings."""

    def __init__(self, glyphs, steps=12):
        super().__init__(glyphs)
        self.rings, self.ring, self.steps = [], [], steps

    def _moveTo(self, p):
        self.ring = [p]

    def _lineTo(self, p):
        self.ring.append(p)

    def _curveToOne(self, a, b, c):
        p, n = self.ring[-1], self.steps
        for i in range(1, n + 1):
            t, u = i / n, 1 - i / n
            self.ring.append(
                tuple(u**3 * p[k] + 3 * u * u * t * a[k] + 3 * u * t * t * b[k] + t**3 * c[k] for k in (0, 1))
            )

    def _qCurveToOne(self, a, b):
        p, n = self.ring[-1], self.steps
        for i in range(1, n + 1):
            t, u = i / n, 1 - i / n
            self.ring.append(tuple(u * u * p[k] + 2 * u * t * a[k] + t * t * b[k] for k in (0, 1)))

    def _closePath(self):
        if len(self.ring) > 2:
            self.rings.append(self.ring)
        self.ring = []

    _endPath = _closePath


def glyph(ch):
    """A letter as a shapely shape in font units, y up, at its own origin."""
    f = TTFont(str(FONT))
    glyphs = f.getGlyphSet()
    pen = _Rings(glyphs)
    glyphs[f.getBestCmap()[ord(ch)]].draw(pen)
    shape = Polygon()
    for ring in pen.rings:
        shape = shape.symmetric_difference(Polygon(ring).buffer(0))
    return shape


def redrawn(ch):
    """The letters as Norvane draws them, before the slit and the slant. Notches and gaps open to at
    least OPEN; those of the N, the V and the A run on into the slit instead of pinching shut."""
    s = glyph(ch)
    lo, hi = SLIT
    if ch == "N":  # both notches widened along the diagonal
        s = s.union(box(250, 200, 440, 470))
        s = s.difference(Polygon([(262, -1), (428, -1), (242 + OPEN, lo + 10), (242, lo + 10)]))
        s = s.difference(Polygon([(259, 671), (426, 671), (446, hi - 10), (446 - OPEN, hi - 10)]))
    elif ch == "V":  # the slot widened
        s = s.union(box(230, 150, 370, 330))
        s = s.difference(
            Polygon([(239, 671), (357, 671), (298 + OPEN / 2, lo + 10), (298 - OPEN / 2, lo + 10)])
        )
    elif ch == "A":  # no bar
        s = s.union(box(250, 0, 400, 500))
        s = s.difference(
            Polygon([(222, -1), (412, -1), (320 + OPEN / 2, hi - 10), (320 - OPEN / 2, hi - 10)])
        )
    elif ch == "R":  # the notch under the bowl opened, the bowl's tail trimmed, the eye run into the slit
        s = s.difference(Polygon([(261, -1), (358, -1), (350, 170), (261, 170)]))
        s = s.difference(Polygon([(535, 200), (700, 200), (700, lo + 1), (509, lo + 1)]))
        s = s.difference(box(261, 330, 366, hi + 1))
    elif (
        ch == "E"
    ):  # arms moved clear of the slit: below it the foot, above it a short middle arm and the top
        s = s.union(box(262, 0, 532, 670)).difference(box(262, 175, 600, lo))
        s = s.difference(box(262, 460, 600, 545)).difference(box(472, hi, 600, 460))
    return s


def word(text):
    """The letters set with GAP between neighbours at their closest, slit and slanted."""
    placed = []
    for ch in text:
        s = affinity.affine_transform(redrawn(ch), [1, SLANT, 0, 1, 0, 0])
        if placed:
            prev = placed[-1]
            s = affinity.translate(s, prev.bounds[2] + GAP - s.bounds[0])
            while (d := prev.distance(s)) > GAP + 0.5:
                s = affinity.translate(s, GAP - d)
        placed.append(s)
    slit = affinity.affine_transform(box(-999, SLIT[0], 99999, SLIT[1]), [1, SLANT, 0, 1, 0, 0])
    return unary_union(placed).difference(slit)


def mark():
    """A full-face helmet facing forward: a round dome, a flat bottom and a chin bar raked like the letters,
    joined by straight tangents; the visor a window in the shell whose back edge leans the same way."""
    bottom, chin = -380, -20
    corners = MultiPoint([(-430, bottom), (520, bottom), (520 + SLANT * (chin - bottom), chin)])
    shell = unary_union([Point(0, 100).buffer(500, 128), corners]).convex_hull.buffer(-40).buffer(40)
    visor = shell.buffer(-115).intersection(Polygon([(-120, 330), (900, 330), (900, -60), (-200, -10)]))
    return shell.difference(visor.buffer(-60).buffer(60))


def doc(geom):
    """A shapely shape (y up) as a one-path SVG file, y down with the baseline at 0."""
    d = ""
    for poly in getattr(geom, "geoms", [geom]):
        for ring in (poly.exterior, *poly.interiors):
            d += "M" + "L".join(f"{x:.1f} {-y:.1f}" for x, y in list(ring.coords)[:-1]) + "Z"
    x0, y0, x1, y1 = geom.buffer(2).bounds  # a hair of margin: a corner on the very edge measures thin
    return sponsors.svg_doc(f'<path fill="#000000" fill-rule="evenodd" d="{d}"/>', (x0, -y1, x1, -y0))


if __name__ == "__main__":
    w, m = doc(word("NORVANE")), doc(mark())
    variants = {"lockup": sponsors.lockup_svg(m, w, gap=0.35, mark_scale=1.35), "wordmark": w, "mark": m}
    print(sponsors.save(HERE, variants))
    if len(sys.argv) > 1:
        print(sponsors.sheet(sponsors.load("norvane"), Path(sys.argv[1]), 3))
