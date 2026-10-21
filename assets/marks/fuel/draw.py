"""Fuel: FUEL knocked out of a plate with its corners cut at 45 degrees, beside the refuelling coupler. The
word is Orbitron Black as the font draws it, tracked open so the plate between letters holds; the plate
is padded a third of a cap height.

uv run python assets/marks/fuel/draw.py
"""

from pathlib import Path

from shapely.geometry import Polygon

from livery import marks, sponsors, typeset

HERE = Path(__file__).resolve().parent
FONT, WEIGHT, TRACK = "Orbitron", 900, 0.12

if __name__ == "__main__":
    word = typeset.text_shape("FUEL", FONT, 100, WEIGHT, tracking=TRACK)
    cap = typeset.measure("FUEL", FONT, 100, WEIGHT, tracking=TRACK).cap
    x0, _, x1, _ = word.bounds
    pad = cap / 3
    a0, b0, a1, b1, c = x0 - pad, -pad, x1 + pad, cap + pad, pad
    plate = Polygon(
        [
            (a0 + c, b0),
            (a1 - c, b0),
            (a1, b0 + c),
            (a1, b1 - c),
            (a1 - c, b1),
            (a0 + c, b1),
            (a0, b1 - c),
            (a0, b0 + c),
        ]
    )
    print(marks.save(HERE, {"mark": sponsors.shape_svg(plate.difference(word))}))
