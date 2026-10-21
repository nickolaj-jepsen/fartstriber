"""Altmere, an insurer, in Jost Bold's geometric lowercase redrawn from its parts: true circles, straight
stems, two stroke weights. Each bowl and arch is a circle round a counter of half circles joined by straight
sides, the counter set on the stem it meets, so every crotch opens at the angle the circles give it; every cut
is level or plumb. The hand-made touch is the e: its aperture is a level slot as tall as its bar, ending at a
plumb terminal. The mark is the same a in a disc centred on its bowl.

uv run python assets/sponsors/altmere/draw.py
"""

from pathlib import Path

from shapely.affinity import translate
from shapely.geometry import Point, box
from shapely.ops import unary_union

from livery import sponsors

HERE = Path(__file__).resolve().parent
# Lengths in Jost's units at 100 px, y down, baseline at 0.
STROKE = 14.0  # stems, and the sides of bowls and arches
THIN = 9.5  # the tops and bottoms of bowls and arches, and the e's bar
R = 24.5  # a and e bowls: outer radius; they overshoot the x-height and baseline by 1
XH = 2 * R - 2  # x-height: stem tops
ASCENDER, T_TOP = 78.0, 62.0  # l and t
CROSSBAR = 10.0  # the t's
COUNTER = 12.6  # between the stems of m and r's arch
GAP = 10.0  # between letters, straight to straight
ROUND_GAP = 8.5  # where a round side faces its neighbour
DISC = 1.8  # the mark's radius, in bowl radii


def disc(x, y, r):
    return Point(x, y).buffer(r, quad_segs=128)


def bowl():
    """A circle round a counter of two half circles joined by straight sides, so the top and bottom are THIN
    and the sides STROKE; and its centre."""
    cx, cy, inner, half = R, -(R - 1), R - STROKE, STROKE - THIN
    counter = disc(cx, cy - half, inner).union(disc(cx, cy + half, inner))
    return (
        disc(cx, cy, R).difference(counter.union(box(cx - inner, cy - half, cx + inner, cy + half))),
        cx,
        cy,
    )


def a():
    ring, cx, _ = bowl()
    return ring.union(box(cx + R - STROKE, -XH, cx + R, 0))  # the stem: on the counter, flush with the bowl


def ell():
    return box(0, -ASCENDER, STROKE, 0)


def t():
    arm = 8.0  # each side of the stem
    return box(arm, -T_TOP, arm + STROKE, 0).union(box(0, -XH, 2 * arm + STROKE, -XH + CROSSBAR))


def arches(n: int):
    """A stem with n arches (2 for m, 1 for r's before its arm is cut). Each arch is a circle round a counter
    with straight sides on the stems either side, flush with the stems' outer edges."""
    inner, half = COUNTER / 2, STROKE - THIN
    outer = inner + STROKE
    cy = -XH + outer
    pitch = STROKE + COUNTER
    shape = box(0, -XH, outer, cy).union(box(0, cy, n * pitch + STROKE, 0))
    for i in range(n):
        shape = shape.union(disc(outer + i * pitch, cy, outer))
    for i in range(n):
        cx = outer + i * pitch
        shape = shape.difference(disc(cx, cy - half, inner).union(box(cx - inner, cy - half, cx + inner, 1)))
    return shape, cy


def m():
    return arches(2)[0]


def r():
    """One arch, its arm cut level with the arch's centre."""
    shape, cy = arches(1)
    return shape.difference(box(STROKE, cy, 99, 1))


def e():
    """The bowl with a bar across its counter's straight sides; the aperture is a level slot as tall as the
    bar, its terminal cut plumb where it is still STROKE - 4 thick."""
    ring, cx, cy = bowl()
    half = STROKE - THIN
    bar = 2 * half
    bar_top = cy - half
    ring = ring.union(box(cx - R, bar_top, cx + R, bar_top + bar).intersection(disc(cx, cy, R)))
    floor = bar_top + 2 * bar  # the slot's
    end = cx + (R**2 - (floor + STROKE - 4 - cy) ** 2) ** 0.5
    return ring.difference(box(cx, bar_top + bar, cx + R + 1, floor)).difference(
        box(end, floor, cx + R + 1, 2)
    )


LETTERS = {"a": (a, True, False), "l": (ell, False, False), "t": (t, False, False)}
LETTERS |= {"m": (m, False, False), "e": (e, True, True), "r": (r, False, True)}  # (draw, round left, right)


def word(text: str = "altmere"):
    shapes, x, prev_round = [], 0.0, False
    for i, ch in enumerate(text):
        draw, round_left, round_right = LETTERS[ch]
        if i:
            x += ROUND_GAP if prev_round or round_left else GAP
        shape = draw()
        shapes.append(translate(shape, x - shape.bounds[0], 0))
        x += shape.bounds[2] - shape.bounds[0]
        prev_round = round_right
    return unary_union(shapes)


def mark():
    cx, cy = R, -(R - 1)
    return disc(cx, cy, DISC * R).difference(a())


def to_svg(shape) -> str:
    d = [
        "M" + " L".join(f"{x:.2f} {y:.2f}" for x, y in ring.coords[:-1]) + " Z"
        for poly in getattr(shape, "geoms", [shape])
        for ring in (poly.exterior, *poly.interiors)
    ]
    return sponsors.svg_doc(f'<path fill="#000000" fill-rule="evenodd" d="{" ".join(d)}"/>', shape.bounds)


if __name__ == "__main__":
    wordmark, symbol = to_svg(word()), to_svg(mark())
    k = 2 * DISC * R / (ASCENDER + 1)  # the mark's a as big as the word's
    lockup = sponsors.lockup_svg(symbol, wordmark, gap=0.3, mark_scale=k)
    print(sponsors.save(HERE, {"lockup": lockup, "wordmark": wordmark, "mark": symbol}))
