"""Tow: a solid arrow pointing at the tow eye, with the eye knocked out of it as a ring. Square corners and a
right-angled point, so nothing on it is thinner than the ring. `right` points along the line the car's
lettering reads on, `left` against it, so the placement picks the one that points at the nose or the tail.

uv run python assets/marks/tow/draw.py
"""

from pathlib import Path

from shapely import affinity
from shapely.geometry import Point, Polygon, box

from livery import marks, sponsors

HERE = Path(__file__).resolve().parent
H = 60.0  # the arrow's height; its body is 100 long and its point half its height
EYE, RING = 0.3 * H, 0.14 * H  # the eye's outer radius and its ring's width

if __name__ == "__main__":
    arrow = box(0, 0, 100, H).union(Polygon([(100, 0), (100 + H / 2, H / 2), (100, H)]))
    eye = Point(50, H / 2).buffer(EYE, 64).difference(Point(50, H / 2).buffer(EYE - RING, 64))
    right = arrow.difference(eye)
    left = affinity.scale(right, -1, 1, origin=(50, 0))
    print(marks.save(HERE, {"right": sponsors.shape_svg(right), "left": sponsors.shape_svg(left)}))
