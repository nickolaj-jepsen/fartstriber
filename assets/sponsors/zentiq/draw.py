"""Zentiq, an energy drink: squared techno lowercase in Orbitron Black, acid green on black.

The hand-made touch is the z: not Orbitron's but the brand's bolt, a Z whose diagonal breaks into a
lightning jog. As the mark it stands alone, and in the lockup it stands in for the word's z, spanning
the i's dot to the q's tail; in the word it strikes down from the x-height to the q's
tail line, its foot tucked in towards the e. The e, i and q are Orbitron's, reopened so they hold at sill
size: the e restacked into five equal bands, the i's dot lifted clear of its stem, the q's tail shortened
to the z's foot.

uv run python assets/sponsors/zentiq/draw.py
"""

import math
from pathlib import Path

from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont
from shapely import affinity
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from livery import sponsors
from livery.config import REPO

HERE = Path(__file__).resolve().parent
FONT = REPO / "assets" / "fonts" / "orbitron" / "Orbitron-Black.ttf"
XH, ST = 580, 153  # Orbitron Black's x-height and stem, in font units
GAP = 125  # space between letters, font units
DROP = 200  # how far the z and q reach below the baseline


class _Flatten(BasePen):
    """Glyph contours as point lists, curves sampled."""

    def __init__(self, glyphs):
        super().__init__(glyphs)
        self.rings, self._ring = [], []

    def _moveTo(self, p):
        self._ring = [p]

    def _lineTo(self, p):
        self._ring.append(p)

    def _qCurveToOne(self, p1, p2):
        p0 = self._ring[-1]
        for i in range(1, 9):
            t = i / 8
            self._ring.append(
                tuple(
                    (1 - t) ** 2 * a + 2 * (1 - t) * t * b + t**2 * c
                    for a, b, c in zip(p0, p1, p2, strict=True)
                )
            )

    def _closePath(self):
        if len(self._ring) > 2:
            self.rings.append(self._ring)
        self._ring = []

    _endPath = _closePath


def glyph(ch: str):
    """One Orbitron Black glyph as a shapely shape, font units, y up."""
    f = TTFont(str(FONT))
    glyphs = f.getGlyphSet()
    pen = _Flatten(glyphs)
    glyphs[f.getBestCmap()[ord(ch)]].draw(pen)
    shape = Polygon()
    for ring in pen.rings:
        shape = shape.symmetric_difference(Polygon(ring).buffer(0))
    return shape


def bolt_z(height, bar, diag, lap, jog, reach, tail, angle=70.0):
    """A Z whose diagonal is a lightning bolt: two pieces `angle` degrees steep, overlapping `lap` either
    side of the middle, the lower one starting `jog` right of where the upper one ends. The top bar
    reaches `reach` left of the bolt's foot, the bottom bar `tail` right of its head. Font units, y up,
    from (0, 0)."""
    mid = height / 2
    travel = (mid - bar + lap) / math.tan(math.radians(angle))
    x_l = reach + travel
    x_u = x_l - jog
    right = x_u + travel + diag
    upper = Polygon(
        [(x_u, mid - lap), (x_u + diag, mid - lap), (right, height - bar), (right - diag, height - bar)]
    )
    lower = Polygon([(reach, bar), (reach + diag, bar), (x_l + diag, mid + lap), (x_l, mid + lap)])
    bars = [box(0, height - bar, right, height), box(reach, 0, right + tail, bar)]
    return unary_union([*bars, upper, lower])


def word(z: bool = True):
    """zentiq, or entiq for the lockup, each letter placed by hand GAP apart, the e tucked in over the
    z's foot."""
    bolt = affinity.translate(bolt_z(XH + DROP, ST, 185, 75, 230, 210, 150), 0, -DROP)
    filled = unary_union([glyph("e"), box(204, 153, 639, 213)])  # Orbitron's e, its aperture shut
    band = XH / 5
    e = filled.difference(box(204, 3 * band, 486, 4 * band)).difference(box(204, band, 700, 2 * band))
    i = unary_union([glyph("i").intersection(box(0, 0, 300, XH)), box(52, XH + 120, 205, XH + 240)])
    q = glyph("q").intersection(box(-100, -DROP, 1000, 1000))
    x, placed = 0.0, []
    letters = [(bolt, -60)] * z + [(e, 0), (glyph("n"), 0), (glyph("t"), 0), (i, 0), (q, 0)]
    for g, kern in letters:
        x0, _, x1, _ = g.bounds
        placed.append(affinity.translate(g, x - x0))
        x += x1 - x0 + GAP + kern
    return unary_union(placed)


def path_d(shape, scale: float = 0.1) -> str:
    """A shapely shape as SVG path data, y flipped down and scaled from font units."""
    shape = affinity.scale(shape, scale, -scale, origin=(0, 0))
    polys = shape.geoms if isinstance(shape, MultiPolygon) else [shape]
    out = []
    for p in polys:
        p = orient(p)
        for ring in [p.exterior, *p.interiors]:
            pts = list(ring.coords)[:-1]
            out.append("M" + " L".join(f"{x:.2f} {y:.2f}" for x, y in pts) + "Z")
    return "".join(out)


def svg(shape) -> str:
    """A shapely shape in font units as a one-colour SVG file, tight on its ink."""
    d = path_d(shape)
    x0, y0, x1, y1 = affinity.scale(shape, 0.1, -0.1, origin=(0, 0)).bounds
    return sponsors.path_svg(d, (x0, y0, x1, y1))


if __name__ == "__main__":
    wordmark = svg(word())
    mark = svg(bolt_z(1000, 170, 230, 90, 280, 200, 200))
    lockup = sponsors.lockup_svg(mark, svg(word(z=False)), gap=GAP / 1020, mark_scale=1.0)
    print(sponsors.save(HERE, {"lockup": lockup, "wordmark": wordmark, "mark": mark}))
