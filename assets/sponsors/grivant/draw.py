"""Grivant, a fictional tyre maker: wide heavy caps in Michroma, emboldened, and a chevron of three tread
blocks. The hand-made touch is the A, drawn as Michroma's V turned over (no crossbar), so V and A make one
zigzag tread groove in the middle of the word. The G's spur is cut back to open its mouth, acute corners
are filled so no wedge is thinner than a stroke, and the letters are spaced by hand on an even gap.

uv run python assets/sponsors/grivant/draw.py
"""

import math
from pathlib import Path

from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont
from shapely import affinity
from shapely.geometry import LineString, MultiPolygon, Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from livery import sponsors
from livery.config import REPO

HERE = Path(__file__).resolve().parent
FONT = REPO / "assets" / "fonts" / "michroma" / "Michroma-Regular.ttf"
CAP = 75.0  # Michroma's cap height at size 100
BOLD = 0.035 * CAP  # outline grown this much each side: Michroma is wide but light
GAP = 0.2 * CAP  # nearest distance between neighbouring letters
FILLET = 0.065 * CAP  # acute corners rounded this much: the thinnest wedge must stay legible at sill size
ZIG = 0.17 * CAP  # the V-A groove, a little tighter so the pair reads as one zigzag


class _Rings(BasePen):
    """Glyph contours as point rings, curves flattened."""

    def __init__(self, glyphs, steps=10):
        super().__init__(glyphs)
        self.rings, self.steps = [], steps

    def _moveTo(self, p):
        self.rings.append([p])

    def _lineTo(self, p):
        self.rings[-1].append(p)

    def _qCurveToOne(self, p1, p2):
        p0 = self.rings[-1][-1]
        for i in range(1, self.steps + 1):
            t = i / self.steps
            self.rings[-1].append(
                tuple(
                    (1 - t) ** 2 * a + 2 * (1 - t) * t * b + t**2 * c
                    for a, b, c in zip(p0, p1, p2, strict=True)
                )
            )

    def _curveToOne(self, p1, p2, p3):
        p0 = self.rings[-1][-1]
        for i in range(1, self.steps + 1):
            t = i / self.steps
            self.rings[-1].append(
                tuple(
                    (1 - t) ** 3 * a + 3 * (1 - t) ** 2 * t * b + 3 * (1 - t) * t**2 * c + t**3 * d
                    for a, b, c, d in zip(p0, p1, p2, p3, strict=True)
                )
            )


def glyph(ch):
    """One letter at size 100, y down, baseline at 0, emboldened."""
    f = TTFont(str(FONT))
    glyphs = f.getGlyphSet()
    pen = _Rings(glyphs)
    glyphs[f.getBestCmap()[ord(ch)]].draw(pen)
    shape = Polygon()
    for ring in pen.rings:
        shape = shape.symmetric_difference(Polygon(ring))  # even-odd: holes cut out
    s = 100 / f["head"].unitsPerEm
    shape = affinity.scale(shape, s, -s, origin=(0, 0))
    return shape.buffer(BOLD, join_style="mitre", mitre_limit=2.0)


def set_word(letters, gaps):
    """Letters placed left to right, each `gaps[i]` from everything set before it."""
    word = letters[0]
    for shape, gap in zip(letters[1:], gaps, strict=True):
        shape = affinity.translate(shape, word.bounds[2] - shape.bounds[0] + gap)
        lo, hi = -shape.bounds[2] + shape.bounds[0], gap  # shift left until the nearest gap is right
        for _ in range(40):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if affinity.translate(shape, mid).distance(word) < gap else (lo, mid)
        word = word.union(affinity.translate(shape, hi))
    return word


def blunt(shape, r):
    """Acute corners filled in (counters) and cut off (strokes) with radius `r` fillets, so no wedge is
    narrower than the rest of the word; square and obtuse corners stay sharp."""

    def thick(pieces):
        polys = getattr(pieces, "geoms", [pieces])
        return unary_union([p for p in polys if not p.buffer(-0.25 * r).is_empty])

    fill = thick(shape.buffer(r).buffer(-r).difference(shape))
    shape = shape.union(fill.buffer(0.05 * r))  # overlapped a hair, or the seam leaves a slit
    return shape.difference(thick(shape.difference(shape.buffer(-r).buffer(r))))


def path_d(shape):
    polys = shape.geoms if isinstance(shape, MultiPolygon) else [shape]
    out = []
    for p in polys:
        p = orient(p, 1.0)  # outer rings one way, holes the other: nonzero fill keeps the holes
        for ring in [p.exterior, *p.interiors]:
            pts = list(ring.coords)[:-1]
            out.append("M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in pts) + "Z")
    return "".join(out)


def wordmark():
    v = glyph("V")
    a = affinity.scale(v, 1, -1, origin=(0, -CAP / 2))  # V turned over: an A without its crossbar
    g = glyph("G")
    x1 = g.bounds[2]
    g = g.difference(box(x1 - 15, -55, x1 + 1, -45))  # the G's spur cut back: a wider mouth
    r = blunt(glyph("R"), 0.4 * FILLET)  # a softer fillet keeps the leg's foot square-looking
    # G is not blunted: filling its corners would close its mouth
    letters = [g, r] + [blunt(c, FILLET) for c in (glyph("I"), v, a, glyph("N"), glyph("T"))]
    return set_word(letters, [GAP, GAP, GAP, ZIG, GAP, GAP])


def mark():
    """A chevron pointing up, flat at the apex and feet like Michroma's V, cut into three tread blocks."""
    h, w, t, cut, along = 100.0, 170.0, 42.0, 14.0, 0.48
    arm = LineString([(-w / 4, 1.5 * h), (w / 2, 0), (1.25 * w, 1.5 * h)])
    chevron = arm.buffer(t / 2, cap_style="flat", join_style="mitre").intersection(box(-w, 0, 2 * w, h))
    slots = []
    slope = math.degrees(math.atan2(h, w / 2))
    for sign in (1, -1):
        # a slot across each arm, square to it, `along` of the way from the apex to the foot
        cx, cy = w / 2 - sign * along * w / 2, along * h
        slot = box(-t, -cut / 2, t, cut / 2)
        slots.append(affinity.translate(affinity.rotate(slot, sign * (90 - slope)), cx, cy))
    return chevron.difference(unary_union(slots))


if __name__ == "__main__":
    word_shape, mark_shape = wordmark(), mark()
    word = sponsors.path_svg(path_d(word_shape), word_shape.bounds)
    sym = sponsors.path_svg(path_d(mark_shape), mark_shape.bounds)
    lockup = sponsors.lockup_svg(sym, word, gap=0.45, mark_scale=1.25)
    print(sponsors.save(HERE, {"lockup": lockup, "wordmark": word, "mark": sym}))
