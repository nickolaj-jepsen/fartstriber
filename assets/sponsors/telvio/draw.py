"""Telvio, a telemetry company: lowercase Space Mono Bold, a mark of three rising bars.

The hand-made touches: the t's crossbar runs on into the e's shoulder, joining the pair; the i's dot is
a square data point; the v's right arm overshoots the x-height a little, a trace ticking up. The e is
rebuilt from the o with a wider eye and an open aperture, and the v's crotch is flattened, so the
counters stay open at sill size.

uv run python assets/sponsors/telvio/draw.py
"""

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
FONT = REPO / "assets" / "fonts" / "space-mono" / "SpaceMono-Bold.ttf"
TOP = 700  # ascender, in font units: t, l and the i's dot end here
ARM = 556  # the v's right arm, about an eighth above the x-height (496)


class _Flatten(BasePen):
    """A glyph's contours as point lists, curves sampled."""

    def __init__(self, glyphs):
        super().__init__(glyphs)
        self.rings, self.ring = [], []

    def _moveTo(self, p):
        self.ring = [p]

    def _lineTo(self, p):
        self.ring.append(p)

    def _qCurveToOne(self, c, p):
        (x0, y0), n = self.ring[-1], 12
        for i in range(1, n + 1):
            t = i / n
            self.ring.append(
                (
                    (1 - t) ** 2 * x0 + 2 * (1 - t) * t * c[0] + t * t * p[0],
                    (1 - t) ** 2 * y0 + 2 * (1 - t) * t * c[1] + t * t * p[1],
                )
            )

    def _curveToOne(self, c1, c2, p):
        (x0, y0), n = self.ring[-1], 16
        for i in range(1, n + 1):
            t, u = i / n, 1 - i / n
            self.ring.append(
                (
                    u**3 * x0 + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t**3 * p[0],
                    u**3 * y0 + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t**3 * p[1],
                )
            )

    def _closePath(self):
        self.rings.append(self.ring)


def glyph(ch: str) -> tuple[Polygon | MultiPolygon, list[Polygon]]:
    """A glyph in font units (y up), and each of its contours filled."""
    font = TTFont(str(FONT))
    pen = _Flatten(font.getGlyphSet())
    font.getGlyphSet()[font.getBestCmap()[ord(ch)]].draw(pen)
    rings = [Polygon(r).buffer(0) for r in pen.rings]
    shape = rings[0]
    for r in rings[1:]:
        shape = shape.symmetric_difference(r)
    return shape, rings


def letter_e():
    """The o with a crossbar: a taller eye than Space Mono's e and an aperture cut open below the bar."""
    _, (outer, counter) = glyph("o")
    bar_lo, bar_hi = 200, 296
    eye = counter.intersection(box(0, bar_hi, 612, 1000))
    lower = counter.intersection(box(0, -100, 612, bar_lo))
    aperture = Polygon([(300, bar_lo), (800, bar_lo), (800, 110 - 0.6 * 420), (300, 110 + 0.6 * 80)])
    # Rounding the cuts' corners (radius 40) fills the pinched tips where the counters meet the bar.
    cuts = [c.buffer(-40).buffer(40, quad_segs=8) for c in (eye, lower.union(aperture))]
    return outer.difference(unary_union(cuts))


def letter_v():
    """Space Mono's v with its right arm carried on above the x-height and a flat crotch."""
    v, _ = glyph("v")
    outer, inner = 425 + ARM * 142 / 496, 314 + (ARM - 60) * 117 / 436  # the arm's edges, run on up
    arm = Polygon([(425, 0), (outer, ARM), (inner, ARM), (314, 60)])
    return unary_union([v, arm, box(250, 0, 360, 100)]).difference(box(260, 100, 350, 300))


def letter_i():
    """The i's stem with a square dot on the top line."""
    _, rings = glyph("i")
    return unary_union([rings[1], box(258, TOP - 120, 378, TOP)])


def path_d(shape) -> str:
    """Shapely polygons (font units, y up) as SVG path data at size 100, y down."""
    shape = affinity.scale(shape, 0.1, -0.1, origin=(0, 0))
    parts = shape.geoms if isinstance(shape, MultiPolygon) else [shape]
    d = []
    for p in parts:
        p = orient(p, 1.0)
        for ring in [p.exterior, *p.interiors]:
            pts = list(ring.coords)[:-1]
            d.append("M" + " L".join(f"{x:.2f} {y:.2f}" for x, y in pts) + " Z")
    return " ".join(d)


def wordmark() -> str:
    letters = [glyph("t")[0], letter_e(), glyph("l")[0], letter_v(), letter_i(), glyph("o")[0]]
    # The t's crossbar carried on until its top edge meets the e's shoulder.
    shoulder = glyph("o")[1][0].boundary.intersection(box(0, 495, 306, 497)).bounds[0]
    join = box(500, 376, 612 + shoulder, 496)
    word = unary_union([affinity.translate(g, 612 * n) for n, g in enumerate(letters)] + [join])
    bounds = affinity.scale(word, 0.1, -0.1, origin=(0, 0)).bounds
    return sponsors.path_svg(path_d(word), bounds)


def mark() -> str:
    """Three bars whose tops lie on one rising line."""
    w, gap, low = 24, 14, 38  # a 100 x 100 square; the first bar's top starts at 38
    bars = []
    for n in range(3):
        x0 = n * (w + gap)
        x1 = x0 + w
        top = [low + (100 - low) * x / 100 for x in (x0, x1)]
        bars.append(Polygon([(x0, 0), (x1, 0), (x1, top[1]), (x0, top[0])]))
    # Rounding the corners by 2 blunts the sharp tip at each bar's top right.
    shape = affinity.scale(unary_union(bars).buffer(-2).buffer(2, quad_segs=6), 10, 10, origin=(0, 0))
    bounds = affinity.scale(shape, 0.1, -0.1, origin=(0, 0)).bounds
    return sponsors.path_svg(path_d(shape), bounds)


if __name__ == "__main__":
    word, sym = wordmark(), mark()
    lockup = sponsors.lockup_svg(sym, word, gap=0.35, mark_scale=1.0)
    print(sponsors.save(HERE, {"lockup": lockup, "wordmark": word, "mark": sym}))
