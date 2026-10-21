"""Bellcrest, a coffee roaster: a brush script in Kaushan Script, built to stay legible on a sill.

Kaushan's hairlines are thickened and its tight notches filled, so every stroke and gap is at least a
tenth of the word's height. Its e and s close up at that weight, so they are redrawn by hand as brush
strokes with open eyes, and the t's crossbar runs on into a rising curl, a wisp of steam off the cup.

uv run python assets/sponsors/bellcrest/draw.py
"""

from pathlib import Path

from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont
from shapely import affinity
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from livery import sponsors
from livery.config import REPO

HERE = Path(__file__).resolve().parent
FONT = REPO / "assets" / "fonts" / "kaushan-script" / "KaushanScript-Regular.ttf"
SIZE = 100  # font size the word is set at; the word comes out about 94 tall
R = 4.9  # half the thinnest stroke and narrowest gap allowed: 0.1 of the height, plus a little
LOWER = 1.22  # x-height letters are set larger, so their eyes and gaps survive the weight
LEAN = 0.3  # the drawn letters' slant, matched to Kaushan's

# Hand-drawn letters, as (x, y, width) points along the brush, at SIZE, upright before LEAN.
E = [(6, -15, 8.4), (20, -19, 8.6), (32, -27, 9.2), (36, -38, 10), (30, -49, 10.5), (17, -48, 11),
     (8, -36, 12), (5, -20, 12.5), (10, -5, 12), (21, 1, 11), (33, -1, 10), (41, -9, 9)]  # fmt: skip
S = [(33, -42, 9), (29, -48, 9.5), (20, -49, 10), (12, -43, 11), (13, -34, 12), (21, -26, 12.5),
     (28, -17, 12.5), (26, -5, 11.5), (16, 1, 10.5), (6, -1, 9.5), (1, -7, 9)]  # fmt: skip
DRAWN = {"e": (E, 44), "s": (S, 44)}  # points and advance
# The t's crossbar carried on to the right and curled up, in the t's own coordinates.
CURL = [(40, -43, 10), (58, -44, 10), (72, -49, 10), (80, -59, 9.8), (76, -70, 9.6), (66, -73, 9.4)]
# Each letter, its scale and the space before it.
WORD = [("B", 1, 0), ("e", LOWER, 12), ("l", 1, 4), ("l", 1, 8), ("c", LOWER, 8), ("r", LOWER, 6),
        ("e", LOWER, 13), ("s", LOWER, 12), ("t", 1, 6)]  # fmt: skip


class _Flatten(BasePen):
    """A glyph's contours as point lists (quadratics arrive here as cubics)."""

    def __init__(self, glyphs, steps=16):
        super().__init__(glyphs)
        self.rings, self.ring, self.steps = [], [], steps

    def _moveTo(self, p):
        self.ring = [p]

    def _lineTo(self, p):
        self.ring.append(p)

    def _curveToOne(self, a, b, c):
        p = self.ring[-1]
        for i in range(1, self.steps + 1):
            t = i / self.steps
            u = 1 - t
            self.ring.append(
                tuple(u**3 * p[k] + 3 * u * u * t * a[k] + 3 * u * t * t * b[k] + t**3 * c[k] for k in (0, 1))
            )

    def _closePath(self):
        if len(self.ring) > 2:
            self.rings.append(self.ring)
        self.ring = []

    _endPath = _closePath


def glyph(ch):
    """A Kaushan letter as a shape, y down with the baseline at 0, and its advance."""
    font = TTFont(str(FONT))
    glyphs = font.getGlyphSet()
    g = glyphs[font.getBestCmap()[ord(ch)]]
    pen = _Flatten(glyphs)
    g.draw(pen)
    outer, holes = [], []
    for ring in pen.rings:
        poly = Polygon(ring)  # TrueType winds outlines clockwise and counters the other way
        (holes if poly.exterior.is_ccw else outer).append(poly.buffer(0))
    shape = unary_union(outer).difference(unary_union(holes))
    s = SIZE / font["head"].unitsPerEm
    return affinity.scale(shape, s, -s, origin=(0, 0)), g.width * s


def brush(points):
    """A round brush along a Catmull-Rom curve through (x, y, width) points."""
    p = [points[0], *points, points[-1]]
    dabs = []
    for i in range(1, len(p) - 2):
        a, b, c, d = p[i - 1], p[i], p[i + 1], p[i + 2]
        for j in range(10):
            t = j / 10
            x, y, w = (
                0.5 * (2 * b[k] + (c[k] - a[k]) * t + (2 * a[k] - 5 * b[k] + 4 * c[k] - d[k]) * t * t
                       + (3 * b[k] - a[k] - 3 * c[k] + d[k]) * t**3)
                for k in range(3)
            )  # fmt: skip
            dabs.append(Point(x, y).buffer(w / 2, 24))
    dabs.append(Point(points[-1][:2]).buffer(points[-1][2] / 2, 24))
    return unary_union([dabs[i].union(dabs[i + 1]).convex_hull for i in range(len(dabs) - 1)])


def thicken(ink):
    """Grows every part of `ink` thinner than 2R until none is left (corner slivers aside)."""
    for _ in range(40):
        thin = ink.difference(ink.buffer(-R).buffer(R))
        parts = [
            p for p in getattr(thin, "geoms", [thin]) if p.area > 0.5 and not p.buffer(-0.35 * R).is_empty
        ]
        if not parts:
            break
        ink = ink.union(unary_union(parts).buffer(0.8))
    return ink


def wordmark():
    x, kaushan, drawn = 0.0, [], []
    for ch, k, gap in WORD:
        x += gap
        if ch in DRAWN:
            points, advance = DRAWN[ch]
            shape = brush([(px - LEAN * py, py, w) for px, py, w in points])
        else:
            shape, advance = glyph(ch)
        shape = affinity.translate(affinity.scale(shape, k, k, origin=(0, 0)), x, 0)
        if ch in DRAWN:
            drawn.append(shape)
        else:
            kaushan.append(shape.buffer(0.9 if k > 1 else 1.6))
        if ch == "t":
            drawn.append(affinity.translate(brush(CURL), x, 0))
        x += advance * k
    ink = thicken(unary_union(kaushan)).union(unary_union(drawn))
    for _ in range(3):  # fill gaps narrower than 2R, drop ink thinner, until both hold
        ink = ink.buffer(R).buffer(-R).buffer(-R).buffer(R)
    ink = ink.simplify(0.03)
    d = "".join(
        "M" + "L".join(f"{px:.2f} {py:.2f}" for px, py in ring.coords[:-1]) + "Z"
        for poly in getattr(ink, "geoms", [ink])
        for ring in (poly.exterior, *poly.interiors)
    )
    return sponsors.svg_doc(f'<path fill="#000000" fill-rule="evenodd" d="{d}"/>', ink.bounds)


if __name__ == "__main__":
    print(sponsors.save(HERE, {"wordmark": wordmark()}))
