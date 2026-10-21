"""Jack: JACK over a pointer as wide as the word, pointing down at the jacking point on the sill. The word
is Orbitron Black as the font draws it (its squared letters hold at sill size); the pointer's corners
are all square or open, so none of it is thinner than the letters.

uv run python assets/marks/jack/draw.py
"""

import math
from pathlib import Path

from shapely.geometry import Polygon

from livery import marks, sponsors, typeset

HERE = Path(__file__).resolve().parent
FONT, WEIGHT, TRACK = "Orbitron", 900, 0.08
GAP, BAR, SLOPE = (
    0.3,
    0.3,
    20.0,
)  # in cap heights: word to pointer, the pointer's straight part; its point's slope

if __name__ == "__main__":
    word = typeset.text_shape("JACK", FONT, 100, WEIGHT, tracking=TRACK)
    cap = typeset.measure("JACK", FONT, 100, WEIGHT, tracking=TRACK).cap
    x0, y0, x1, _ = word.bounds
    top = min(y0, 0.0) - GAP * cap
    low = top - BAR * cap
    tip = low - (x1 - x0) / 2 * math.tan(math.radians(SLOPE))
    pointer = Polygon([(x0, top), (x1, top), (x1, low), ((x0 + x1) / 2, tip), (x0, low)])
    print(marks.save(HERE, {"mark": sponsors.shape_svg(word.union(pointer))}))
