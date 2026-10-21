"""Brandvik Freight, a fictional logistics sponsor: Zilla Slab Bold caps and a crate with an arrow through it.

The hand-made touch: the foot serifs are joined into one flatbed that the letters ride on, running out past
the K into an arrowhead. The A stands solid on it under a lowered crossbar, the K's top serifs are one slab,
and acute crotches are filled round so no gap closes up when small.

uv run python assets/sponsors/brandvik/draw.py
"""

from pathlib import Path

from fontTools.pens.basePen import BasePen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from shapely import affinity
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from livery import sponsors
from livery.config import REPO

HERE = Path(__file__).resolve().parent
FONT = REPO / "assets" / "fonts" / "zilla-slab" / "ZillaSlab-Bold.ttf"
SIZE = 100.0  # cap height 65
GAP = 11.0  # the space between letters, above the flatbed
EXTRA = {"AN": -0.5, "DV": -0.5}  # optical adjustments to GAP, per pair
BED = 11.6  # flatbed thickness: just over the foot serifs, which it swallows
FILLET = 4.4  # crotches are filled round to this radius


class _Polys(BasePen):
    """Glyph contours flattened to point lists."""

    def __init__(self, glyphs):
        super().__init__(glyphs)
        self.contours, self.pt = [], None

    def _moveTo(self, p):
        self.contours.append([p])
        self.pt = p

    def _lineTo(self, p):
        self.contours[-1].append(p)
        self.pt = p

    def _curveToOne(self, a, b, c):
        (x0, y0), n = self.pt, 12
        for i in range(1, n + 1):
            t = i / n
            u = 1 - t
            self.contours[-1].append(
                (
                    u**3 * x0 + 3 * u * u * t * a[0] + 3 * u * t * t * b[0] + t**3 * c[0],
                    u**3 * y0 + 3 * u * u * t * a[1] + 3 * u * t * t * b[1] + t**3 * c[1],
                )
            )
        self.pt = c

    def _qCurveToOne(self, a, b):
        (x0, y0), n = self.pt, 10
        for i in range(1, n + 1):
            t = i / n
            u = 1 - t
            self.contours[-1].append(
                (u * u * x0 + 2 * u * t * a[0] + t * t * b[0], u * u * y0 + 2 * u * t * a[1] + t * t * b[1])
            )
        self.pt = b

    def _closePath(self):
        self.pt = None


def glyph(ch: str):
    """One letter as a shapely shape, y down, baseline at 0, left edge of its ink at 0."""
    f = TTFont(str(FONT))
    glyphs = f.getGlyphSet()
    pen = _Polys(glyphs)
    s = SIZE / f["head"].unitsPerEm
    glyphs[f.getBestCmap()[ord(ch)]].draw(TransformPen(pen, (s, 0, 0, -s, 0, 0)))
    shape = Polygon()
    for c in pen.contours:
        shape = shape.symmetric_difference(Polygon(c).buffer(0))
    if ch == "K":  # its stem and arm share one top slab, so the notch between them can't close to a dot
        shape = shape.union(box(24.0, -65.0, 37.0, -53.5))
    if ch == "A":  # solid below a lowered crossbar: a bigger eye, and no slot shut in by the flatbed
        shape = Polygon(shape.exterior)
        shape = shape.union(shape.intersection(box(-1e3, -22.0, 1e3, -BED)).convex_hull)
        shape = shape.difference(Polygon([(33.0, -46.2), (34.2, -46.2), (43.6, -21.0), (22.9, -21.0)]))
    return affinity.translate(shape, -shape.bounds[0], 0)


def arrow(tail: float, base: float, tip: float, half: float, shaft: float, y: float):
    """A right-pointing arrow on the line `y`, its barbs and point rounded so they survive small."""
    shape = unary_union(
        [
            box(tail, y - shaft / 2, base + 1, y + shaft / 2),
            Polygon([(base, y - half), (tip, y), (base, y + half)]),
        ]
    )
    return shape.buffer(-2.5).buffer(2.5)


def word(text: str):
    """The letters spaced GAP apart above the flatbed (nearest points, not boxes), riding on it."""
    above = box(-1e3, -1e3, 1e3, -BED - 0.5)
    placed = []
    for i, ch in enumerate(text):
        g = glyph(ch)
        if placed:
            prev = placed[-1].intersection(above)
            want = GAP + EXTRA.get(text[i - 1 : i + 1], 0.0)
            lo, hi = prev.bounds[0], prev.bounds[2] + 50
            for _ in range(40):  # the shift whose nearest distance above the bed is `want`
                mid = (lo + hi) / 2
                d = affinity.translate(g, mid, 0).intersection(above).distance(prev)
                lo, hi = (mid, hi) if d < want else (lo, mid)
            g = affinity.translate(g, hi, 0)
        placed.append(g)
    ink = unary_union(placed)
    x0, _, x1, _ = ink.bounds
    # The flatbed runs out past the K into an arrowhead.
    bed = arrow(x0 + 2.5, x1 + 9, x1 + 35, 16, BED, -BED / 2)
    ink = unary_union([ink, bed, box(x0, -BED, x0 + 5, 0)])  # its tail stays square
    ink = ink.buffer(FILLET, quad_segs=16).buffer(-FILLET, quad_segs=16).simplify(0.1)
    return ink.intersection(box(-1e3, -65.0, 1e3, 1e3))  # flat on the cap line where pieces join


def mark():
    """A crate, wider than tall, with an arrow run straight through it."""
    w, h, wall = 125.0, 90.0, 14.0
    crate = box(0, 0, w, h).difference(box(wall, wall, w - wall, h - wall))
    return unary_union([crate, arrow(-30, w + 14, w + 60, 31, 16, h / 2)])


def path_d(shape) -> str:
    polys = shape.geoms if isinstance(shape, MultiPolygon) else [shape]
    rings = []
    for p in polys:
        p = orient(p, 1.0)
        for ring in [p.exterior, *p.interiors]:
            pts = list(ring.coords)[:-1]
            rings.append("M" + " L".join(f"{x:.2f} {y:.2f}" for x, y in pts) + " Z")
    return " ".join(rings)


def svg(shape) -> str:
    return sponsors.path_svg(path_d(shape), shape.bounds)


if __name__ == "__main__":
    m, w = svg(mark()), svg(word("BRANDVIK"))
    lockup = sponsors.lockup_svg(m, w, gap=0.35, mark_scale=1.0)
    print(sponsors.save(HERE, {"lockup": lockup, "wordmark": w, "mark": m}))
