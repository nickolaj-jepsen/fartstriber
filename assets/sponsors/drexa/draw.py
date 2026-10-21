"""Drexa, wheels. The letters are drawn, no font: rectangles and quarter circles on a 100-unit cap height.
The hand-made touch is the X, two rim arcs set back to back, and the mark is a five-spoke wheel.

uv run python assets/sponsors/drexa/draw.py
"""

from math import cos, pi, sin
from pathlib import Path

from shapely import affinity, ops
from shapely.geometry import Point, Polygon, box

from livery import sponsors

HERE = Path(__file__).resolve().parent
S = 22  # stroke
GAP = 16  # between letters


def rrect(x0, y0, x1, y1, tl=0.0, tr=0.0, br=0.0, bl=0.0):
    """A rectangle (y down) with each corner rounded on a quarter circle of its own radius."""
    pts = []
    for cx, cy, r, a0 in (
        (x0 + tl, y0 + tl, tl, 180),
        (x1 - tr, y0 + tr, tr, 270),
        (x1 - br, y1 - br, br, 0),
        (x0 + bl, y1 - bl, bl, 90),
    ):
        angles = [pi * (a0 + 90 * i / 24) / 180 for i in range(25)] if r else [0.0]
        pts += [(cx + r * cos(a), cy + r * sin(a)) for a in angles]
    return Polygon(pts)


def letters():
    d = rrect(0, 0, 84, 100, tr=50, br=50).difference(rrect(S, S, 84 - S, 100 - S, tr=28, br=28))
    bowl = rrect(0, 0, 80, 62, tr=31, br=31).difference(rrect(S, S, 80 - S, 62 - S, tr=9, br=9))
    r = ops.unary_union([bowl, box(0, 0, S, 100), box(46, 50, 46 + S, 100)])
    e = ops.unary_union([box(0, 0, S, 100), box(0, 0, 64, S), box(0, 39, 54, 61), box(0, 100 - S, 64, 100)])
    # X: a ")" and a "(" of rim, each the half of a ring that faces the other, overlapping at the waist.
    ring = Point(0, 50).buffer(50, 96).difference(Point(0, 50).buffer(50 - S, 96))
    half = ring.intersection(box(0, 0, 50, 100))
    x = half.union(affinity.translate(affinity.scale(half, -1, 1, origin=(0, 0)), 88))
    a = rrect(0, 0, 84, 100, tl=42, tr=42).difference(
        ops.unary_union([rrect(S, S, 84 - S, 54, tl=20, tr=20), box(S, 54 + S, 84 - S, 101)])
    )
    shapes, cursor = [], 0.0
    for g in (d, r, e, x, a):
        x0, _, x1, _ = g.bounds
        shapes.append(affinity.translate(g, cursor - x0))
        cursor += x1 - x0 + GAP
    return ops.unary_union(shapes)


def wheel():
    """A rim, a hub with a centre bore and five spokes; the windows between them are the counters."""
    rim = Point(0, 0).buffer(50, 128).difference(Point(0, 0).buffer(39, 128))
    spoke = box(-8, -45, 8, 0)
    spokes = [affinity.rotate(spoke, 72 * i, origin=(0, 0)) for i in range(5)]
    hub = Point(0, 0).buffer(17, 96)
    return ops.unary_union([rim, hub, *spokes]).difference(Point(0, 0).buffer(6, 64))


def path_d(geom):
    polys = getattr(geom, "geoms", [geom])
    rings = [ring for p in polys for ring in (p.exterior, *p.interiors)]
    return " ".join("M" + " L".join(f"{x:.2f} {y:.2f}" for x, y in ring.coords[:-1]) + " Z" for ring in rings)


def svg(geom):
    body = f'<path fill="#000000" fill-rule="evenodd" d="{path_d(geom)}"/>'
    return sponsors.svg_doc(body, geom.bounds)


if __name__ == "__main__":
    word, mark = svg(letters()), svg(wheel())
    lockup = sponsors.lockup_svg(mark, word, gap=0.3, mark_scale=1.3)
    print(sponsors.save(HERE, {"lockup": lockup, "wordmark": word, "mark": mark}))
