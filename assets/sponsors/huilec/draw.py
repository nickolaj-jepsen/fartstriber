"""Huilec, lubricants (huile, oil). Barlow Black Italic caps as the font draws and kerns them, tracked open so
the gaps between letters hold as well as the E's arms; none is redrawn. The mark is an oil drop, a circle and
the tip it narrows to, leaning with the italic and cut by one speed line that tapers to a round end; its
corners are rounded on the radius Barlow rounds its own.

uv run python assets/sponsors/huilec/draw.py
"""

import math
from pathlib import Path

from fontTools.ttLib import TTFont
from shapely import affinity
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from livery import sponsors, typeset

HERE = Path(__file__).resolve().parent
FONT = sponsors.REPO / "assets" / "fonts" / "barlow" / "Barlow-BlackItalic.ttf"
SIZE = 100.0
TRACKING = 0.06  # of the size, between letters: their gaps as open as the E's arms
CAP = SIZE * TTFont(str(FONT))["OS/2"].sCapHeight / TTFont(str(FONT))["head"].unitsPerEm
LEAN = -TTFont(str(FONT))["post"].italicAngle  # degrees the font leans right
CORNER = 0.015 * SIZE  # the radius Barlow Black rounds its corners on (15 of 1000 units)


def wordmark():
    return typeset.text_shape("HUILEC", FONT, size=SIZE, tracking=TRACKING)


def mark():
    """The drop, y up on the baseline, as tall as the caps; the speed line crosses its widest part."""
    r = 0.4 * CAP
    drop = unary_union([Point(0, r).buffer(r, 128), Point(0, CAP)]).convex_hull
    drop = affinity.skew(drop, xs=LEAN, origin=(0, 0))
    x0, _, x1, _ = drop.bounds
    y, outer, end = 0.95 * r, 0.2 * CAP, 0.13 * CAP  # its height, its thickness outside, its round end
    x_end = x1 - 0.3 * (x1 - x0)
    lean = math.tan(math.radians(LEAN)) * outer / 2
    taper = Polygon(
        [
            (x0 - 10 - lean, y - outer / 2),
            (x_end, y - end / 2),
            (x_end, y + end / 2),
            (x0 - 10 + lean, y + outer / 2),
        ]
    )
    cut = drop.difference(taper.union(Point(x_end, y).buffer(end / 2, 64)))
    return cut.buffer(-CORNER, 32).buffer(CORNER, 32)  # rounds the tip and the cut's two corners, no more


if __name__ == "__main__":
    word, sign = sponsors.shape_svg(wordmark()), sponsors.shape_svg(mark())
    lockup = sponsors.lockup_svg(sign, word, gap=0.3, mark_scale=1.3)
    print(sponsors.save(HERE, {"lockup": lockup, "wordmark": word, "mark": sign}))
