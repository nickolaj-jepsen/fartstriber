"""Air: a ring round the air-jack inlet with a pointer up in it, the way the jacks lift the car. The ring is a
seventh of its diameter thick; the pointer is a bar with a right-angled point, as wide as the bar.

uv run python assets/marks/air/draw.py
"""

from pathlib import Path

from shapely.geometry import Point, Polygon, box

from livery import marks, sponsors

HERE = Path(__file__).resolve().parent
R = 50.0
RING = 2 * R / 7
HALF = 10.0  # the pointer's half width

if __name__ == "__main__":
    ring = Point(0, 0).buffer(R, 64).difference(Point(0, 0).buffer(R - RING, 64))
    pointer = box(-HALF, -24, HALF, 6).union(Polygon([(-HALF, 6), (HALF, 6), (0, 6 + HALF)]))
    print(marks.save(HERE, {"mark": sponsors.shape_svg(ring.union(pointer))}))
