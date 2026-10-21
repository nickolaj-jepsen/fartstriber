"""Corbaux, a watchmaker: Playfair Display Bold caps, widely tracked, gold on black.

The letters are Playfair's own, thickened by one uniform offset, and every crotch sharper than a right
angle (the A's apex, the waists of R and B) is cut off straight by one rule. The hand-made touch: the O
is a pocket-watch case, with a square winding crown on a short stem, straight-edged like the serifs.

A didone lives on its hairlines, so this is a large-placement brand (bonnet, doors, wing): drawn thick
enough for a sill it would stop being a didone.

uv run python assets/sponsors/corbaux/draw.py
"""

from pathlib import Path

from fontTools.pens.basePen import BasePen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from livery import sponsors

HERE = Path(__file__).resolve().parent
FONT = HERE.parents[1] / "fonts" / "playfair-display" / "PlayfairDisplay-Bold.ttf"
WORD = "CORBAUX"
CAP = 100.0  # cap height, in drawing units
OFFSET = 1.5  # every edge moves out this much
TRACK = 34.0  # space added after each letter
KERN = {"BA": -8.0, "AU": -8.0}  # pairs, added to the tracking
CROWN = (16.0, 6.0, 7.0, 3.5)  # the O's winding crown: knob width and height, then its stem's
CROTCH = 6.0  # crotches are cut off where they are this wide
MITRE = {"join_style": "mitre", "mitre_limit": 2.0}


class FlatPen(BasePen):
    """Glyph contours as point lists, curves flattened finely enough that offsetting them stays smooth."""

    def __init__(self, steps: int = 24):
        super().__init__(None)
        self.contours: list[list[tuple[float, float]]] = []
        self.steps = steps

    def _moveTo(self, p):
        self.contours.append([p])

    def _lineTo(self, p):
        self.contours[-1].append(p)

    def _curveToOne(self, p1, p2, p3):
        p0 = self.contours[-1][-1]
        for i in range(1, self.steps + 1):
            t = i / self.steps
            u = 1 - t
            self.contours[-1].append(
                tuple(
                    u**3 * a + 3 * u * u * t * b + 3 * u * t * t * c + t**3 * d
                    for a, b, c, d in zip(p0, p1, p2, p3, strict=True)
                )
            )

    def _qCurveToOne(self, p1, p2):
        p0 = self.contours[-1][-1]
        for i in range(1, self.steps + 1):
            t = i / self.steps
            u = 1 - t
            self.contours[-1].append(
                tuple(u * u * a + 2 * u * t * b + t * t * c for a, b, c in zip(p0, p1, p2, strict=True))
            )


def letter(font: TTFont, ch: str, x: float) -> Polygon | MultiPolygon:
    """One letter at `x`, y down with the baseline at 0."""
    s = CAP / font["OS/2"].sCapHeight
    pen = FlatPen()
    font.getGlyphSet()[font.getBestCmap()[ord(ch)]].draw(TransformPen(pen, (s, 0, 0, -s, x, 0)))
    rings = [Polygon(c) for c in pen.contours if len(c) > 2]
    # y is flipped, so outer contours (clockwise in the font) come out anticlockwise
    outer = unary_union([r.buffer(0) for r in rings if r.exterior.is_ccw])
    holes = unary_union([r.buffer(0) for r in rings if not r.exterior.is_ccw])
    return outer.difference(holes)


def blunt(shape: Polygon | MultiPolygon) -> Polygon | MultiPolygon:
    """Every crotch sharper than a right angle (the A's apex, where R's and B's bowls meet) is cut off
    straight where it narrows to CROTCH: the space round the letters is opened with bevelled corners."""
    frame = box(*shape.buffer(4 * CROTCH).bounds)
    space = frame.difference(shape)
    space = space.buffer(-CROTCH / 2, join_style="mitre", mitre_limit=1.5).buffer(
        CROTCH / 2, join_style="mitre", mitre_limit=1.5
    )
    return frame.difference(space)


def to_path(shape: Polygon | MultiPolygon) -> str:
    d = []
    for p in shape.geoms if isinstance(shape, MultiPolygon) else [shape]:
        p = orient(p)  # outer rings anticlockwise, holes clockwise: holes under the nonzero rule
        for ring in [p.exterior, *p.interiors]:
            d.append("M" + "L".join(f"{x:.2f} {y:.2f}" for x, y in list(ring.coords)[:-1]) + "Z")
    return "".join(d)


def wordmark() -> str:
    font = TTFont(str(FONT))
    s = CAP / font["OS/2"].sCapHeight
    x, letters = 0.0, []
    for i, ch in enumerate(WORD):
        g = letter(font, ch, x).buffer(OFFSET, **MITRE)
        if ch == "O":
            x0, top, x1, _ = g.bounds
            mid, (w, h, neck_w, neck_h) = (x0 + x1) / 2, CROWN
            neck = box(mid - neck_w / 2, top - neck_h, mid + neck_w / 2, top + 2.0)
            knob = box(mid - w / 2, top - neck_h - h, mid + w / 2, top - neck_h)
            g = unary_union([g, neck, knob])
        letters.append(g)
        x += (
            font.getGlyphSet()[font.getBestCmap()[ord(ch)]].width * s + TRACK + KERN.get(WORD[i : i + 2], 0.0)
        )
    shape = blunt(unary_union(letters))
    return sponsors.path_svg(to_path(shape), shape.bounds)


if __name__ == "__main__":
    print(sponsors.save(HERE, {"wordmark": wordmark()}, large_only=True))
