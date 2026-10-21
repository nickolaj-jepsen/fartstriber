"""Extinguisher: the letter E knocked out of a disc, as the extinguisher's pull handle is marked. The E is
Barlow Condensed ExtraBold as the font draws it, its cap height a little over half the disc.

uv run python assets/marks/extinguisher/draw.py
"""

from pathlib import Path

from shapely import affinity
from shapely.geometry import Point

from livery import marks, sponsors, typeset

HERE = Path(__file__).resolve().parent
FONT, WEIGHT = "Barlow Condensed", 800
R = 50.0
CAP = 0.56 * 2 * R

if __name__ == "__main__":
    size = CAP / typeset.measure("E", FONT, 1.0, WEIGHT).cap
    e = typeset.text_shape("E", FONT, size, WEIGHT)
    x0, _, x1, _ = e.bounds
    e = affinity.translate(e, -(x0 + x1) / 2, -CAP / 2)
    disc = Point(0, 0).buffer(R, 64)
    print(marks.save(HERE, {"mark": sponsors.shape_svg(disc.difference(e))}))
