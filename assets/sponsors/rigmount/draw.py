"""Rigmount, sim rigs: stencil caps in Big Shoulders Stencil Black; the mark is the end of a 20x20
aluminium profile, four T-slots and the bore.

The font's 5-unit stencil cuts are filled in and recut CUT wide, and the R, G, M and N are pulled open
where their counters pinch, so the word holds at sill size. The hand-made touch is the T: its bar runs
back over the whole word as a rail, the letters hung under it across a stencil gap.

uv run python assets/sponsors/rigmount/draw.py
"""

import itertools
from pathlib import Path

from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont
from shapely import affinity
from shapely.geometry import Point, Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from livery import sponsors
from livery.config import REPO

HERE = Path(__file__).resolve().parent
FONT = REPO / "assets" / "fonts" / "big-shoulders-stencil" / "BigShouldersStencil-Black.ttf"
CUT = 11.5  # stencil cuts, counters and letter gaps, in units of a 100-unit em (cap height 80)
RAIL = 13.0  # the T's bar, run back over the word


class _Rings(BasePen):
    """A glyph's contours flattened to point lists."""

    def __init__(self, glyphs):
        super().__init__(glyphs)
        self.rings, self.ring = [], []

    def _moveTo(self, p):
        self.ring = [p]

    def _lineTo(self, p):
        self.ring.append(p)

    def _curveToOne(self, *pts):
        self._flatten(pts, 12)

    def _qCurveToOne(self, *pts):
        self._flatten(pts, 8)

    def _flatten(self, pts, steps):
        """A Bezier from the last point through `pts`, as `steps` line segments (de Casteljau)."""
        ctrl = [self.ring[-1], *pts]
        for i in range(1, steps + 1):
            t, q = i / steps, ctrl
            while len(q) > 1:
                q = [((1 - t) * a[0] + t * b[0], (1 - t) * a[1] + t * b[1]) for a, b in itertools.pairwise(q)]
            self.ring.append(q[0])

    def _closePath(self):
        self.rings.append(self.ring)


def glyph(font: TTFont, ch: str):
    """The letter at a 100-unit em, y down, with the font's stencil cuts filled in."""
    glyphs, s = font.getGlyphSet(), 100 / font["head"].unitsPerEm
    pen = _Rings(glyphs)
    glyphs[font.getBestCmap()[ord(ch)]].draw(pen)
    shape = Polygon()
    for ring in pen.rings:
        shape = shape.symmetric_difference(Polygon([(x * s, -y * s) for x, y in ring]).buffer(0))
    return shape.buffer(2.6, join_style=2).buffer(-2.6, join_style=2)


def widen(shape, x: float, d: float):
    """`shape` pulled apart at the vertical line `x` by `d`, the slice at `x` stretched across the gap."""
    big = 1000
    left = shape.intersection(box(-big, -big, x, big))
    right = affinity.translate(shape.intersection(box(x, -big, big, big)), d)
    slice_ = shape.intersection(box(x - 0.05, -big, x + 0.05, big))
    mid = affinity.scale(slice_, d / 0.1, 1, origin=(x - 0.05, 0))
    return unary_union([left, right, affinity.translate(mid, 0.05)]).buffer(0.01).buffer(-0.01)


def slot(cx: float, y0: float, y1: float, w: float = CUT):
    return box(cx - w / 2, y0, cx + w / 2, y1)


def letters(font: TTFont) -> dict:
    g = {ch: glyph(font, ch) for ch in "RIGMOUN"}
    g["R"] = widen(g["R"], 20, CUT - 5.1)  # the leg leaves the stem 5 apart
    w = CUT - 1.9  # M and N: the wedges under their legs, 2 wide at the tip, opened to a cut
    g["M"] = widen(widen(g["M"], 55, w), 20, w).difference(slot(37.2 + w, -63, -37))  # the vertex, flat
    g["N"] = widen(widen(g["N"], 35, CUT - 2.6), 19, CUT - 2.9)
    g["G"] = widen(g["G"], 22, CUT - 7.3)  # the channel beside the spur
    for ch, cx in (("G", 24.9 + (CUT - 7.3) / 2), ("O", 24.9), ("U", 24.4)):
        g[ch] = g[ch].difference(slot(cx, 10, -14))
        if ch != "U":
            g[ch] = g[ch].difference(slot(cx, -90, -66))
    g["T"] = box(13.5, -81.2 - CUT - 1, 28.5, 0)  # the stem, up to the rail that is its bar
    return g


def wordmark() -> str:
    g = letters(TTFont(str(FONT)))
    x, parts = 0.0, []
    for ch in "RIGMOUNT":
        x0, _, x1, _ = g[ch].bounds
        parts.append(affinity.translate(g[ch], x - x0))
        x += x1 - x0 + CUT + 1
    stem_mid = (parts[-1].bounds[0] + parts[-1].bounds[2]) / 2
    rail_bottom = -81.2 - CUT
    parts.append(box(0, rail_bottom - RAIL, stem_mid + 20.4, rail_bottom))
    word = unary_union(parts)
    return sponsors.path_svg(path_d(word), word.bounds)


def path_d(shape) -> str:
    out = []
    for poly in getattr(shape, "geoms", [shape]):
        poly = orient(poly, 1.0)
        for ring in [poly.exterior, *poly.interiors]:
            pts = list(ring.coords)[:-1]
            out.append("M" + "L".join(f"{x:.2f} {y:.2f}" for x, y in pts) + "Z")
    return "".join(out)


def mark() -> str:
    """The end of a 20x20 profile: a T-slot in each face, the bore in the core."""
    s, r, lip, opening, floor = 100.0, 7.0, 14.0, 24.0, 31.0
    body = box(0, 0, s, s).buffer(-r, join_style=1).buffer(r, join_style=1)
    c = s / 2
    cavity = Polygon([(c - 21, lip), (c + 21, lip), (c + 10, floor), (c - 10, floor)])
    cavity = cavity.buffer(-3, join_style=1).buffer(3, join_style=1)
    one = unary_union([cavity, box(c - opening / 2, -1, c + opening / 2, lip + 1)])
    slots = [affinity.rotate(one, a, origin=(c, c)) for a in (0, 90, 180, 270)]
    shape = body.difference(unary_union([*slots, Point(c, c).buffer(9, 32)]))
    return sponsors.path_svg(path_d(shape), shape.bounds)


if __name__ == "__main__":
    word, sign = wordmark(), mark()
    lockup = sponsors.lockup_svg(sign, word, gap=0.3, mark_scale=1.0)
    print(sponsors.save(HERE, {"lockup": lockup, "wordmark": word, "mark": sign}))
