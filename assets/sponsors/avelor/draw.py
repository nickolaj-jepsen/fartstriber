"""Avelor, a fuel brand: Jost Medium caps set wide, and a mark of a sun cut by the horizon. The hand-made
touch: the O is cut by the same horizon as the mark, and the A and V are redrawn flat-pointed (points cut
off and stretched back to cap height, the notch between their strokes filled flat), which also keeps
their sharp wedges from closing up at sill size.

uv run python assets/sponsors/avelor/draw.py
"""

from pathlib import Path

from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont
from shapely import affinity
from shapely.geometry import Point, Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from livery import sponsors

HERE = Path(__file__).resolve().parent
FONT = HERE.parents[2] / "assets" / "fonts" / "jost" / "Jost-Medium.ttf"
CAP = 700  # Jost's cap height in font units
TRACK = 190  # letter spacing, font units
KERN = {"AV": -40, "LO": -110}  # pairs that look too far apart at even tracking
# The A and V (font units, y down): where the point is cut flat, and the band filled at the notch.
POINTS = {"A": (-640, (-520, -440)), "V": (-60, (-275, -195))}
HORIZON = 0.6  # the horizon's centre, as a share of the height down from the top
CUT = 0.13  # the horizon cut's thickness, as a share of the height


class _Flatten(BasePen):
    """A glyph's contours as point lists, curves sampled into short lines."""

    def __init__(self, glyphs, steps: int = 16):
        super().__init__(glyphs)
        self.rings, self.steps = [], steps

    def _moveTo(self, p):
        self.rings.append([p])

    def _lineTo(self, p):
        self.rings[-1].append(p)

    def _sample(self, *ctrl):
        p0, n = self.rings[-1][-1], len(ctrl)
        w = [(1, 2, 1), (1, 3, 3, 1)][n - 2]  # Bernstein weights for a quadratic or a cubic
        for t in (i / self.steps for i in range(1, self.steps + 1)):
            k = [w[j] * (1 - t) ** (n - j) * t**j for j in range(n + 1)]
            pts = (p0, *ctrl)
            self.rings[-1].append(
                tuple(sum(kj * p[axis] for kj, p in zip(k, pts, strict=True)) for axis in (0, 1))
            )

    def _curveToOne(self, p1, p2, p3):
        self._sample(p1, p2, p3)

    def _qCurveToOne(self, p1, p2):
        self._sample(p1, p2)


def glyph(ch: str):
    """One letter as a shapely shape in font units, y down with the baseline at 0, and its advance."""
    font = TTFont(str(FONT))
    glyphs = font.getGlyphSet()
    g = glyphs[font.getBestCmap()[ord(ch)]]
    pen = _Flatten(glyphs)
    g.draw(pen)
    # Static Jost keeps overlapping contours: fill those wound like the largest and cut the others out.
    rings = [Polygon(r) for r in pen.rings if len(r) > 2]
    fill = max(rings, key=lambda r: r.buffer(0).area).exterior.is_ccw
    outer = unary_union([r.buffer(0) for r in rings if r.exterior.is_ccw == fill])
    shape = outer.difference(unary_union([r.buffer(0) for r in rings if r.exterior.is_ccw != fill]))
    return affinity.scale(shape, 1, -1, origin=(0, 0)), g.width


def flat_point(shape, point: float, notch: tuple[float, float]):
    """An A or V with its notch filled flat and its point cut off at `point`, stretched back to cap
    height."""
    shape = shape.union(box(-100, notch[0], 1000, notch[1]).intersection(shape.convex_hull))
    if point < -CAP / 2:  # the A points up
        return affinity.scale(shape.intersection(box(-100, point, 1000, 0)), 1, CAP / -point, origin=(0, 0))
    shape = shape.intersection(box(-100, -CAP, 1000, point))
    return affinity.scale(shape, 1, CAP / (CAP + point), origin=(0, -CAP))


def horizon_cut(shape):
    """`shape` with a horizontal band taken out at the horizon."""
    x0, top, x1, bottom = shape.bounds
    y, half = top + HORIZON * (bottom - top), CUT * (bottom - top) / 2
    return shape.difference(box(x0 - 1, y - half, x1 + 1, y + half))


def to_path(shape) -> str:
    """A shapely shape as SVG path data, rings oriented so the default fill rule keeps the holes."""
    parts = []
    for poly in getattr(shape, "geoms", [shape]):
        if poly.geom_type == "Polygon":
            poly = orient(poly, 1.0)
            for ring in [poly.exterior, *poly.interiors]:
                parts.append("M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in ring.coords[:-1]) + "Z")
    return "".join(parts)


def wordmark():
    letters, x, prev = [], 0.0, ""
    for ch in "AVELOR":
        shape, advance = glyph(ch)
        if ch in POINTS:
            shape = flat_point(shape, *POINTS[ch])
        if ch == "R":
            shape = shape.intersection(box(-100, -CAP, 560, 0))  # the leg's sharp toe cut upright
        if ch == "O":
            shape = horizon_cut(shape)
        x += KERN.get(prev + ch, 0)
        letters.append(affinity.translate(shape, x))
        x += advance + TRACK
        prev = ch
    return unary_union(letters)


if __name__ == "__main__":
    word, sun = wordmark(), horizon_cut(Point(0, 0).buffer(500, quad_segs=64))
    word_svg = sponsors.path_svg(to_path(word), word.bounds)
    mark_svg = sponsors.path_svg(to_path(sun), sun.bounds)
    lockup = sponsors.lockup_svg(mark_svg, word_svg, gap=0.45, mark_scale=1.35)
    print(sponsors.save(HERE, {"lockup": lockup, "wordmark": word_svg, "mark": mark_svg}))
