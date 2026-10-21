"""Ostrand, brakes. Condensed bold caps in Barlow Condensed ExtraBold; the mark is a quarter of a slotted
disc (hat and bore, a gap, a slotted friction ring). The letters are the font's; the A and N are only
widened so their counters hold at sill size, and the narrowest crotches (N, R) are closed by one flat cut,
as the font itself closes the R's. The hand-made touch: the T's bar runs on into the R as one top rail.

uv run python assets/sponsors/ostrand/draw.py
"""

import math
from pathlib import Path

import shapely
from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont
from shapely import affinity
from shapely.geometry import LinearRing, LineString, MultiPolygon, Point, Polygon, box
from shapely.ops import unary_union

from livery import sponsors

HERE = Path(__file__).resolve().parent
FONT = HERE.parents[2] / "assets" / "fonts" / "barlow-condensed" / "BarlowCondensed-ExtraBold.ttf"
SIZE = 100.0  # cap height is 70
TRACKING = 8.0  # between letters, opened up so the gaps hold at sill size
WIDEN = 6.0  # how much wider the A and N are drawn than the font's, so their counters stay open
GAP = 3.3  # half the narrowest gap kept, just under the font's S counter
TILT = 35.0  # the disc's slots, degrees off the radius


class _Rings(BasePen):
    """A glyph's contours flattened into point rings."""

    def __init__(self, glyphs):
        super().__init__(glyphs)
        self.rings, self.ring = [], []

    def _moveTo(self, p):
        self.ring = [p]

    def _lineTo(self, p):
        self.ring.append(p)

    def _curveToOne(self, p1, p2, p3):
        p0, steps = self.ring[-1], 12
        for i in range(1, steps + 1):
            t, u = i / steps, 1 - i / steps
            pts = zip(p0, p1, p2, p3, strict=True)
            self.ring.append(
                tuple(u**3 * a + 3 * u * u * t * b + 3 * u * t * t * c + t**3 * d for a, b, c, d in pts)
            )

    def _qCurveToOne(self, p1, p2):
        p0, steps = self.ring[-1], 8
        for i in range(1, steps + 1):
            t, u = i / steps, 1 - i / steps
            self.ring.append(
                tuple(u * u * a + 2 * u * t * b + t * t * c for a, b, c in zip(p0, p1, p2, strict=True))
            )

    def _closePath(self):
        if len(self.ring) > 2:
            self.rings.append(self.ring)
        self.ring = []

    _endPath = _closePath


def glyph(ch: str) -> tuple[Polygon, float]:
    """A letter as a shape (y down, baseline 0, left at its side bearing) and its advance."""
    font = TTFont(str(FONT))
    glyphs = font.getGlyphSet()
    g = glyphs[font.getBestCmap()[ord(ch)]]
    pen = _Rings(glyphs)
    g.draw(pen)
    # Non-zero winding: the font overlaps its strokes, so solids (clockwise, y up) are joined, then holed.
    solid = unary_union([Polygon(r).buffer(0) for r in pen.rings if not LinearRing(r).is_ccw])
    shape = solid.difference(unary_union([Polygon(r).buffer(0) for r in pen.rings if LinearRing(r).is_ccw]))
    s = SIZE / font["head"].unitsPerEm
    return affinity.affine_transform(shape, (s, 0, 0, -s, 0, 0)), g.width * s


def widened_a(delta: float) -> tuple[Polygon, float]:
    """The font's A cut down its middle and the halves set `delta` apart, the bar and apex bridged straight:
    same legs, same bar, a counter that stays open."""
    a, advance = glyph("A")
    x0, y0, x1, y1 = a.bounds
    c = min(a.interiors[0].coords, key=lambda pt: pt[1])[0]  # through the counter's apex
    left, right = a.intersection(box(x0 - 1, y0, c, y1)), a.intersection(box(c, y0, x1 + 1, y1))
    spans = a.intersection(LineString([(c, y0 - 1), (c, y1 + 1)]))
    bridges = [
        box(c - 0.01, g.bounds[1], c + delta + 0.01, g.bounds[3]) for g in getattr(spans, "geoms", [spans])
    ]
    return unary_union([left, affinity.translate(right, delta), *bridges]), advance + delta


def widened_n(delta: float) -> tuple[Polygon, float]:
    """The font's N with its right stem moved `delta` out and the diagonal leaned to meet it, its width and
    its ends as the font draws them."""
    n, advance = glyph("N")
    left, right = n.intersection(box(0, -71, 19.9, 1)), n.intersection(box(32.3, -71, 60, 1))
    # The diagonal's two edges, read off the font's outline, sheared so the foot moves `delta`.
    lo = (20.2 + 0.2865 * (-70 + 35.9), 20.2 + 0.2865 * 35.9)
    hi = (31.8 + 0.288 * (-70 + 34.0), 31.8 + 0.288 * 34.0)
    slash = Polygon([(lo[0], -70), (hi[0], -70), (hi[1] + delta, 0), (lo[1] + delta, 0)])
    return unary_union([left, slash, affinity.translate(right, delta)]), advance + delta


def close_wedges(shape, r: float):
    """Closes the tip of every acute gap narrower than 2r with one horizontal cut, where it has narrowed to
    2r. Corners and gaps the measure would let pass (slivers under SOLID_SHARE of r) are left as drawn."""
    frame = box(*shape.bounds)
    room = frame.buffer(4 * r, join_style="mitre").difference(shape)
    kept = room.buffer(-r, join_style="round").buffer(r, join_style="round")
    lost = room.difference(kept).intersection(frame)
    cuts = []
    for piece in getattr(lost, "geoms", [lost]):
        if shapely.maximum_inscribed_circle(piece).length < sponsors.SOLID_SHARE * r:
            continue
        ring = piece.exterior.coords[:-1]
        off = [shape.distance(Point(pt)) > 0.05 * r for pt in ring]
        start = next(i for i in range(len(ring)) if off[i] and not off[i - 1])
        arc = []
        for i in range(start, start + len(ring)):
            if not off[i % len(ring)]:
                break
            arc.append(ring[i % len(ring)])
        (_, py), (_, qy), (_, my) = arc[0], arc[-1], arc[len(arc) // 2]
        y = (py + qy) / 2  # cut flat across, where the gap has narrowed to 2r; the tip is past the arc
        x0, _, x1, _ = piece.bounds
        side = box(x0 - r, y, x1 + r, y + 10 * r) if my > y else box(x0 - r, y - 10 * r, x1 + r, y)
        cuts.append(room.intersection(frame).intersection(side).intersection(piece.buffer(0.5 * r)))
    return unary_union([shape, *cuts]).buffer(0)


def wordmark():
    drawn = {"A": lambda: widened_a(WIDEN), "N": lambda: widened_n(WIDEN)}
    x, parts = 0.0, []
    for ch in "OSTRAND":
        shape, advance = drawn[ch]() if ch in drawn else glyph(ch)
        parts.append(affinity.translate(shape, x))
        x += advance + TRACKING
    rail = box(parts[2].bounds[2] - 1, -70, parts[3].bounds[0] + 3, -55.8)  # T's bar on into the R's top
    return close_wedges(unary_union([*parts, rail]), GAP)


def slot(cx: float, cy: float, length: float, width: float, angle: float) -> Polygon:
    """A round-ended slot centred on (cx, cy), `angle` degrees from horizontal."""
    line = affinity.rotate(box(-length / 2, 0, length / 2, 0).exterior, angle, origin=(0, 0))
    return affinity.translate(line.buffer(width / 2, 32), cx, cy)


def mark():
    """A quarter of a slotted disc, its centre at bottom left: the hat with its bore, a gap, then the
    friction ring with three slots swept the same way."""
    bore, hat, ring, rim = 17.0, 40.0, 53.0, 100.0

    def quarter(r: float) -> Polygon:
        return Point(0, 0).buffer(r, 128).intersection(box(0, -r, r, 0))

    shape = quarter(rim).difference(quarter(ring)).union(quarter(hat).difference(quarter(bore)))
    mid = (ring + rim) / 2
    for deg in (19, 45, 71):
        a = math.radians(deg)
        shape = shape.difference(slot(mid * math.cos(a), -mid * math.sin(a), 24, 10.5, -deg - TILT))
    return shape


def path_d(shape) -> str:
    parts = []
    for poly in shape.geoms if isinstance(shape, MultiPolygon) else [shape]:
        for ring in (poly.exterior, *poly.interiors):
            parts.append("M" + "L".join(f"{x:.2f} {y:.2f}" for x, y in list(ring.coords)[:-1]) + "Z")
    return "".join(parts)


def svg(shape) -> str:
    return sponsors.svg_doc(f'<path fill="#000000" fill-rule="evenodd" d="{path_d(shape)}"/>', shape.bounds)


if __name__ == "__main__":
    word, sign = svg(wordmark()), svg(mark())
    lockup = sponsors.lockup_svg(sign, word, gap=0.3, mark_scale=1.15)
    print(sponsors.save(HERE, {"lockup": lockup, "wordmark": word, "mark": sign}))
