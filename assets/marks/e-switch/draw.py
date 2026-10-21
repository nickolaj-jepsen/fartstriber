"""E-switch: the electrical cut-off sign, a spark in a triangle, in one colour. The triangle's outline is an
eleventh of its side thick, its corners rounded on true circles; the spark is one stroke of straight cuts
with square ends, set as large as it goes while the gaps round it stay open at its minimum size.

uv run python assets/marks/e-switch/draw.py
"""

import math
from pathlib import Path

from shapely import affinity
from shapely.geometry import LineString, Polygon

from livery import marks, sponsors

HERE = Path(__file__).resolve().parent
SIDE = 100.0
STROKE = SIDE / 11
CORNER = 0.06 * SIDE
SPARK = LineString([(0.8, 1.0), (0.3, 0.45), (0.7, 0.55), (0.2, 0.0)])  # top right to bottom left
OPEN = 0.09  # the narrowest gap round the spark, as a share of the sign's height


def sign(scale: float):
    tri = Polygon([(0, 0), (SIDE, 0), (SIDE / 2, SIDE * math.sqrt(3) / 2)])
    outer = tri.buffer(-CORNER, join_style="round").buffer(CORNER, join_style="round")
    inner = outer.buffer(-STROKE, join_style="round")
    line = affinity.scale(SPARK, scale, scale, origin=(0, 0))
    c = inner.buffer(-STROKE).centroid
    line = affinity.translate(line, c.x - line.centroid.x, c.y - line.centroid.y)
    spark = line.buffer(0.5 * STROKE, cap_style="flat", join_style="bevel")
    return outer.difference(inner).union(spark)


if __name__ == "__main__":
    scale = 40.0
    while sponsors.measure(sponsors.shape_svg(sign(scale)))["counter"] < OPEN:
        scale -= 1.0
    print(marks.save(HERE, {"mark": sponsors.shape_svg(sign(scale))}))
