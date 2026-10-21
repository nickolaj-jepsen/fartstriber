"""Type as geometry: glyphs from the fonts in assets/fonts, set with fontTools into shapely outlines, measured,
and laid out in car space like any other shape, so a block of type crosses seams, takes outline strokes,
leans with the design and is judged glyph by glyph against what the kit maps.

Glyph geometry is y up with the baseline at 0, in units of the size it is set at (the em). Pairs are
kerned from the font's GPOS `kern` feature, as resvg renders text; there are no ligatures.
"""

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

import numpy as np
import shapely
import shapely.affinity
from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont
from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry

from livery import carspace, geom
from livery.carspace import Piece
from livery.config import REPO
from livery.errors import LiveryError
from livery.kit import Kit
from livery.model import Element, Material

FONTS = REPO / "assets" / "fonts"
CURVE_STEPS = 8  # segments per quadratic curve (12 per cubic): sub-pixel at the sizes type is set
SHOWN = 0.96  # a glyph the paint shows less of than this is cut by glass, an arch or an unmapped part
SAMPLES = 6  # points across a glyph the map's scale is averaged over


class TypesetError(LiveryError, ValueError):
    pass


# --- fonts ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Face:
    path: Path
    family: str
    weight: int
    italic: bool


@lru_cache(maxsize=4)
def faces(root: Path = FONTS) -> tuple[Face, ...]:
    """Every font file under `root`, by family, weight and slope."""
    out = []
    for p in sorted(root.rglob("*.ttf")):
        f = TTFont(str(p), lazy=True)
        name = f["name"]
        family = name.getDebugName(16) or name.getDebugName(1) or p.stem
        os2 = f["OS/2"]
        weight, italic = int(getattr(os2, "usWeightClass", 400)), bool(getattr(os2, "fsSelection", 0) & 1)
        out.append(Face(p, family, weight, italic))
    return tuple(out)


def face(font: str | Path, weight: int = 400, italic: bool = False) -> Path:
    """A font file: a path as given, or the family's file nearest the weight (as CSS matches fonts)."""
    if isinstance(font, Path) or font.endswith((".ttf", ".otf")):
        return Path(font)
    mine = [f for f in faces() if f.family == font]
    if not mine:
        families = sorted({f.family for f in faces()})
        raise TypesetError(f"no font family {font!r} in assets/fonts; there are {', '.join(families)}")
    slope = [f for f in mine if f.italic == italic] or mine
    return min(slope, key=lambda f: (abs(f.weight - weight), -f.weight)).path


class _Rings(BasePen):
    """A glyph's contours as point rings, curves flattened."""

    def __init__(self, glyphs):
        super().__init__(glyphs)
        self.rings: list[list[tuple[float, float]]] = []

    def _moveTo(self, pt):
        self.rings.append([pt])

    def _lineTo(self, pt):
        self.rings[-1].append(pt)

    def _qCurveToOne(self, pt1, pt2):
        (x0, y0), n = self.rings[-1][-1], CURVE_STEPS
        for t in np.arange(1, n + 1) / n:
            u = 1 - t
            self.rings[-1].append(
                (
                    u * u * x0 + 2 * u * t * pt1[0] + t * t * pt2[0],
                    u * u * y0 + 2 * u * t * pt1[1] + t * t * pt2[1],
                )
            )

    def _curveToOne(self, pt1, pt2, pt3):
        (x0, y0), n = self.rings[-1][-1], CURVE_STEPS * 3 // 2
        for t in np.arange(1, n + 1) / n:
            u = 1 - t
            a, b, c, d = u**3, 3 * u * u * t, 3 * u * t * t, t**3
            self.rings[-1].append(
                (a * x0 + b * pt1[0] + c * pt2[0] + d * pt3[0], a * y0 + b * pt1[1] + c * pt2[1] + d * pt3[1])
            )


def _winding(ring: np.ndarray, x: float, y: float) -> int:
    x0, y0 = ring[:, 0], ring[:, 1]
    x1, y1 = np.roll(x0, -1), np.roll(y0, -1)
    side = (x1 - x0) * (y - y0) - (x - x0) * (y1 - y0)
    up = (y0 <= y) & (y1 > y) & (side > 0)
    down = (y0 > y) & (y1 <= y) & (side < 0)
    return int(up.sum() - down.sum())


def _nonzero(rings: list[list[tuple[float, float]]]) -> BaseGeometry:
    """The area the rings fill under the nonzero rule, as TrueType renders overlapping contours."""
    rings = [r for r in rings if len(r) >= 3]
    if not rings:
        return Polygon()
    arrays = [np.asarray(r, float) for r in rings]
    noded = shapely.union_all([LineString([*r, r[0]]) for r in rings])
    filled = []
    for cell in shapely.get_parts(shapely.polygonize(shapely.get_parts(noded))):
        p = cell.representative_point()
        if sum(_winding(a, p.x, p.y) for a in arrays) != 0:
            filled.append(cell)
    return shapely.union_all(filled) if filled else Polygon()


@dataclass(frozen=True)
class _Font:
    path: Path
    upem: int
    cap: float  # cap height, in em
    ascender: float
    descender: float
    cmap: dict[int, str]
    kern: Callable[[str, str], float]


def _kerning(f: TTFont) -> Callable[[str, str], float]:
    """Pair adjustments (font units) from the GPOS kern feature: format 1 pairs and format 2 classes."""
    if "GPOS" not in f:
        return lambda a, b: 0.0
    table = f["GPOS"].table
    indices = sorted(
        {
            i
            for fr in table.FeatureList.FeatureRecord
            if fr.FeatureTag == "kern"
            for i in fr.Feature.LookupListIndex
        }
    )
    lookups = []
    for i in indices:
        lookup = table.LookupList.Lookup[i]
        subs = [s.ExtSubTable if lookup.LookupType == 9 else s for s in lookup.SubTable]
        subs = [s for s in subs if getattr(s, "LookupType", lookup.LookupType) == 2 or hasattr(s, "PairSet")]
        lookups.append(subs)

    def adjust(left: str, right: str) -> float:
        total = 0.0
        for subs in lookups:
            for s in subs:
                cov = s.Coverage.glyphs
                if left not in cov:
                    continue
                if s.Format == 1:
                    pair = next(
                        (r for r in s.PairSet[cov.index(left)].PairValueRecord if r.SecondGlyph == right),
                        None,
                    )
                    if pair is None:
                        continue
                    total += getattr(pair.Value1, "XAdvance", 0) or 0
                    break
                c1 = s.ClassDef1.classDefs.get(left, 0)
                c2 = s.ClassDef2.classDefs.get(right, 0)
                total += getattr(s.Class1Record[c1].Class2Record[c2].Value1, "XAdvance", 0) or 0
                break
        return float(total)

    return lru_cache(maxsize=4096)(adjust)


@lru_cache(maxsize=32)
def _tt(path: Path) -> TTFont:
    return TTFont(str(path))


@lru_cache(maxsize=32)
def _font(path: Path) -> _Font:
    f = _tt(path)
    upem = int(f["head"].unitsPerEm)  # pyrefly: ignore[missing-attribute]
    cmap = f.getBestCmap() or {}
    os2 = f["OS/2"]
    cap = getattr(os2, "sCapHeight", 0) or 0
    if not cap:
        cap = _glyph(path, cmap[ord("H")])[0].bounds[3]
    hhea = f["hhea"]
    return _Font(path, upem, cap / upem, hhea.ascent / upem, hhea.descent / upem, cmap, _kerning(f))


@lru_cache(maxsize=4096)
def _glyph(path: Path, name: str) -> tuple[BaseGeometry, float]:
    """A glyph in font units, y up, and its advance."""
    glyph_set = _tt(path).getGlyphSet()
    pen = _Rings(glyph_set)
    glyph_set[name].draw(pen)
    return _nonzero(pen.rings), float(glyph_set[name].width)


# --- setting a line ------------------------------------------------------------------------------------


def _set(
    text: str, font: str | Path, size: float, weight: int, italic: bool, tracking: float
) -> list[tuple[str, BaseGeometry, float, float]]:
    """(character, outline, origin x, advance) for each character, kerned and tracked."""
    fnt = _font(face(font, weight, italic))
    s = size / fnt.upem
    out, x, prev = [], 0.0, None
    for ch in text:
        name = fnt.cmap.get(ord(ch))
        if name is None:
            raise TypesetError(f"{fnt.path.name} has no {ch!r} (in {text!r})")
        if prev is not None:
            x += fnt.kern(prev, name) * s + tracking * size
        shape, advance = _glyph(fnt.path, name)
        placed = shape if shape.is_empty else shapely.affinity.affine_transform(shape, [s, 0, 0, s, x, 0])
        out.append((ch, placed, x, advance * s))
        x += advance * s
        prev = name
    return out


def glyphs(
    text: str,
    font: str | Path = "Barlow Condensed",
    size: float = 1.0,
    weight: int = 400,
    italic: bool = False,
    tracking: float = 0.0,
) -> list[tuple[str, BaseGeometry]]:
    """Each character of `text` set on one line, kerned, with `tracking` (a share of `size`) added between
    letters: (character, outline) pairs, y up, baseline at 0, the first origin at x = 0. A space has no
    outline. A character the font lacks is an error. `font` is a family in assets/fonts (the weight
    nearest `weight`), or a font file."""
    return [(ch, g) for ch, g, _, _ in _set(text, font, size, weight, italic, tracking)]


def text_shape(
    text: str,
    font: str | Path = "Barlow Condensed",
    size: float = 1.0,
    weight: int = 400,
    italic: bool = False,
    tracking: float = 0.0,
) -> BaseGeometry:
    """A line of text as one outline (as `glyphs` sets it): the letters a draw.py starts a brand from."""
    shapes = [g for _, g in glyphs(text, font, size, weight, italic, tracking) if not g.is_empty]
    return shapely.union_all(shapes) if shapes else Polygon()


@dataclass(frozen=True)
class Measure:
    advance: float  # from the first glyph's origin to past the last one
    ink: tuple[float, float, float, float]  # x0, y0, x1, y1 of what is drawn, y up
    cap: float  # the font's cap height at this size
    ascender: float
    descender: float  # negative: below the baseline

    @property
    def width(self) -> float:
        return self.ink[2] - self.ink[0]


def measure(
    text: str,
    font: str | Path = "Barlow Condensed",
    size: float = 1.0,
    weight: int = 400,
    italic: bool = False,
    tracking: float = 0.0,
) -> Measure:
    """How much room a line of text takes, in the units of `size`."""
    fnt = _font(face(font, weight, italic))
    set_ = _set(text, font, size, weight, italic, tracking)
    inked = [g for _, g, _, _ in set_ if not g.is_empty]
    ink = shapely.union_all(inked).bounds if inked else (0.0, 0.0, 0.0, 0.0)
    advance = set_[-1][2] + set_[-1][3] if set_ else 0.0
    return Measure(
        advance,
        (float(ink[0]), float(ink[1]), float(ink[2]), float(ink[3])),
        fnt.cap * size,
        fnt.ascender * size,
        fnt.descender * size,
    )


# --- blocks of type on the car -------------------------------------------------------------------------


@dataclass(frozen=True)
class Line:
    """One line of a type block. `size` is its em in car lengths, as `text` takes it; `tracking` is space
    added between letters as a share of it; `color` overrides the block's."""

    text: str
    size: float
    font: str = "Barlow Condensed"
    weight: int = 800
    italic: bool = False
    tracking: float = 0.0
    color: str | None = None


def _reading(kit: Kit, view: str, facing: str) -> np.ndarray:
    """Reading frame (x along the line, y up, both in car lengths as seen) to car space (u, v or w)."""
    k = carspace.aspect(kit, view)
    if view == "side_l":
        return np.array([[1.0, 0.0], [0.0, 1.0 / k]])
    if view == "side_r":
        return np.array([[-1.0, 0.0], [0.0, 1.0 / k]])
    if facing == "front":  # read from the front: x across to the car's left, up towards the tail
        return np.array([[0.0, 1.0], [-1.0 / k, 0.0]])
    return np.array([[0.0, -1.0], [1.0 / k, 0.0]])


def _slope(view: str, deg: float) -> float:
    """A lean as dx/dy in the reading frame: on the sides `deg` leans tops towards the tail, as `rake` does,
    which on the right side, read tail to nose, is a back-slant; on top it leans them along the line."""
    s = math.tan(math.radians(deg))
    return -s if view == "side_r" else s


def _shown(kit: Kit, view: str, shape: BaseGeometry, through) -> float:
    """The share of a car-space shape the paint shows: its footprint over its area at the map's scale,
    averaged over points inside it (a residual grid makes the scale vary by a few percent in a glyph)."""
    if shape.is_empty:
        return 1.0
    x0, y0, x1, y1 = shape.bounds
    gx, gy = np.meshgrid(np.linspace(x0, x1, SAMPLES + 2)[1:-1], np.linspace(y0, y1, SAMPLES + 2)[1:-1])
    inside = shapely.contains_xy(shape, gx.ravel(), gy.ravel())
    q = shape.representative_point()
    pts = [(q.x, q.y), *zip(gx.ravel()[inside], gy.ravel()[inside], strict=True)]
    scales = []
    for p in pts:
        try:
            scales.append(abs(float(np.linalg.det(carspace.anchor(kit, view, p)[2]))))
        except ValueError:
            continue  # off every map: the footprint shows what is lost
    if not scales:
        return 0.0
    return geom.footprint(kit, view, shape, through).area / (shape.area * float(np.mean(scales)))


def type_block(
    kit: Kit,
    id: str,
    view: str,
    lines: Sequence[Line],
    at: tuple[float, float],
    color: str,
    gap: float | None = None,
    lean: float = 0.0,
    shear: float | None = None,
    anchor: tuple[float, float] = (0.0, 0.0),
    plate: str | None = None,
    pad: float = 0.0,
    strokes: Sequence[tuple[float, str]] = (),
    facing: str = "front",
    through: Iterable[Piece] | None = None,
    material: Material = Material.gloss,
    z: int = 0,
) -> list[Element]:
    """A block of type set as glyph geometry: the first line is the designation, the rest small lines
    aligned to it. Lines stack down `gap` apart (car lengths from one baseline to the next cap top; by
    default 0.6 of the smallest cap height) and their first letters' ink shares one start edge, leaning
    `lean` degrees (on the sides, tops towards the tail, like `rake`). `shear` slants the letters
    (default: the lean; 0 for upright). The block's anchor point, `anchor` as shares of its ink box from
    the top start corner, lands on `at`. It reads like `text`: on the sides left to right as seen, on top
    from the front (`facing="rear"`: from behind).

    `plate` puts a field behind it with `pad` (car lengths) on every side, measured square to its raked
    ends; `strokes` outline the glyphs (as `geom.outline`). Glyphs are projected like shapes, so the block
    may cross seams; a glyph the paint doesn't show whole (glass, an arch, an unmapped part) is refused,
    by name and place, as is a line set below the lint's `min_text_px`.

    Elements in drawing order: `<id>.plate`, the glyph strokes, `<id>.text` (one path per colour). The text
    element's `car["polygon"]` is the plate (or the ink box) in car space, for other pieces to avoid."""
    from livery.lint.run import thresholds

    if not lines:
        raise TypesetError(f"{id}: a type block needs a line")
    s = _slope(view, lean)
    h = _slope(view, lean if shear is None else shear)
    sizes = [_font(face(ln.font, ln.weight, ln.italic)) for ln in lines]
    caps = [f.cap * ln.size for f, ln in zip(sizes, lines, strict=True)]
    gap = 0.6 * min(caps) if gap is None else gap

    # Each line in the reading frame: baselines stacked down from the block's top (y = 0), each shifted
    # so its ink starts on the edge x = s y.
    placed: list[tuple[Line, list[tuple[str, BaseGeometry]]]] = []
    y = 0.0
    for i, (ln, cap) in enumerate(zip(lines, caps, strict=True)):
        y -= cap if i == 0 else gap + cap
        set_ = [
            (ch, shapely.affinity.affine_transform(g, [1, h, 0, 1, 0, 0]))
            for ch, g in glyphs(ln.text, ln.font, ln.size, ln.weight, ln.italic, ln.tracking)
        ]
        ink = [np.asarray(p.exterior.coords) for _, g in set_ for p in geom.polygons(g)]
        if not ink:
            raise TypesetError(f"{id}: line {ln.text!r} draws nothing")
        pts = np.concatenate(ink)
        dx = s * y - float((pts[:, 0] - s * pts[:, 1]).min())
        placed.append((ln, [(ch, shapely.affinity.translate(g, dx, y)) for ch, g in set_]))

    every = np.concatenate(
        [np.asarray(p.exterior.coords) for _, set_ in placed for _, g in set_ for p in geom.polygons(g)]
    )
    end = float((every[:, 0] - s * every[:, 1]).max())
    top, bottom = max(0.0, float(every[:, 1].max())), float(every[:, 1].min())
    ay = top - anchor[1] * (top - bottom)
    origin = np.array([s * ay + anchor[0] * end, ay])
    m = _reading(kit, view, facing)
    off = np.asarray(at, float) - m @ origin

    def to_car(g: BaseGeometry) -> BaseGeometry:
        return shapely.affinity.affine_transform(g, [m[0, 0], m[0, 1], m[1, 0], m[1, 1], off[0], off[1]])

    def frame(x0: float, x1: float, y0: float, y1: float) -> Polygon:  # x along the lean
        return Polygon([(x0 + s * y0, y0), (x1 + s * y0, y0), (x1 + s * y1, y1), (x0 + s * y1, y1)])

    edge = pad * math.hypot(1.0, s)  # in x - s y: `pad` square to the raked ends
    box = frame(-edge, end + edge, bottom - pad, top + pad) if plate else frame(0.0, end, bottom, top)
    pieces = carspace.pieces(kit, view) if through is None else tuple(through)

    least = thresholds().min_text_px
    layers: dict[str, list[BaseGeometry]] = {}
    for ln, set_ in placed:
        cars = [(ch, to_car(g)) for ch, g in set_ if not g.is_empty]
        mid = shapely.union_all([g for _, g in cars]).representative_point()
        try:
            _, _, jac = carspace.anchor(kit, view, (mid.x, mid.y))
            px = ln.size * float(np.linalg.norm(jac @ m[:, 1]))
        except ValueError:
            px = math.inf  # off the map: the glyph check below names it
        for ch, g in cars:
            share = _shown(kit, view, g, pieces)
            if share < SHOWN:
                q = g.representative_point()
                raise TypesetError(
                    f"{id}: the {ch!r} of {ln.text!r} at ({q.x:.3f}, {q.y:.3f}) on {view} shows {share:.0%} on "
                    "the paint; glass, an arch or an unmapped part cuts it, so move or shrink the block"
                )
        if px < least:
            raise TypesetError(f"{id}: {ln.text!r} is {px:.0f} px high here, under the lint's {least:.0f} px")
        layers.setdefault(ln.color or color, []).extend(g for _, g in cars)

    out = []
    if plate:
        out.append(
            geom.fill(
                kit, f"{id}.plate", view, {plate: [to_car(box)]}, through=pieces, material=material, z=z
            )
        )
    if strokes:
        letters = shapely.union_all([g for gs in layers.values() for g in gs])
        out += geom.outline(kit, f"{id}.text", view, letters, strokes, through=pieces, material=material, z=z)
    text = geom.fill(kit, f"{id}.text", view, layers, through=pieces, material=material, z=z)
    car_box = [tuple(map(float, p)) for p in shapely.get_coordinates(to_car(box))[:-1]]
    text_car = {"view": view, "at": tuple(at), "lines": [ln.text for ln in lines], "polygon": car_box}
    out.append(replace(text, kind="text", car=text_car))
    return out
