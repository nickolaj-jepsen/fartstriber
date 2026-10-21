"""The design library. Geometry is in car space (see `livery.carspace`) unless a primitive says otherwise.

Views: "side_l", "side_r", "top", and the faces ("front", "rear") the kit maps. Primitives that cover
several views return one element per view, with ids suffixed by the view, so lints and the critic can name
each piece.
"""

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from dataclasses import replace
from pathlib import Path

import numpy as np
import shapely

from livery import carspace, config, sponsors
from livery.errors import LiveryError
from livery.kit import Kit, view_kind
from livery.model import Element, Livery, Material

FACE_W = 2.0  # past either edge of a face (w), so a shape across it covers it whole
FACE_V = (-1.0, 2.0)  # below and above a face (v)

_SVG_NS = "{http://www.w3.org/2000/svg}"


def base(color: str, material: Material = Material.gloss) -> Livery:
    """The colour (and finish) under everything."""
    return Livery(base=color, base_material=Material(material))


def _views(kit: Kit, views: str | Iterable[str]) -> tuple[str, ...]:
    names = {"sides": kit.side_views, "all": kit.views}
    if isinstance(views, str):
        return names.get(views, (views,))
    return tuple(v for name in views for v in names.get(name, (name,)))


def shape(
    kit: Kit,
    id: str,
    view: str,
    points: list[tuple[float, float]],
    color: str,
    material: Material = Material.gloss,
    z: int = 0,
    tags: tuple[str, ...] = (),
) -> Element:
    """A car-space polygon on one view, continued across every island it lands on. An edge that would run
    alongside a line the near-tangent lint judges is snapped onto it or clear of it (`geom.snapped`), and
    each move is listed in the lint report."""
    from livery import geom

    foot, snaps = geom.snapped(kit, view, carspace.project(kit, view, points))
    d = carspace.path_d(foot)
    return Element(
        id=id,
        kind="shape",
        svg=f'<path fill-rule="evenodd" d="{d}" fill="{color}"/>' if d else "",
        material=Material(material),
        z=z,
        tags=tags,
        car={"view": view, "polygon": [tuple(map(float, p)) for p in points]},
        snaps=snaps,
    )


def band(
    kit: Kit,
    id: str,
    u0: float,
    u1: float,
    color: str,
    views: str | Iterable[str] = "all",
    angle: float = 0.0,
    v0: float = -0.05,
    v1: float = 1.05,
    material: Material = Material.gloss,
    z: int = 0,
) -> list[Element]:
    """A band across the car between u0 and u1 (nose 0, tail 1), up the sides and over the top.

    `angle` leans it in degrees, measured on the side as seen: positive leans the top towards the tail.
    Over the top it continues from where it leaves the sides (v = 1), so it stays one band. A band that
    reaches the nose (an edge at u <= 0) or the tail (u >= 1) covers that face across, between v0 and v1, or
    to the face's bottom and top where it reaches the sill and the top of the side.
    """
    lean = np.tan(np.radians(angle)) * carspace.side_aspect(kit)  # u per unit of v
    out = []
    for view in _views(kit, views):
        if view_kind(view) == "face":
            if not _reaches(view, u0 + lean * v0, u0 + lean * v1, u1 + lean * v0, u1 + lean * v1):
                continue
            lo, hi = FACE_V[0] if v0 <= 0 else v0, FACE_V[1] if v1 >= 1 else v1
            pts = [(-FACE_W, lo), (FACE_W, lo), (FACE_W, hi), (-FACE_W, hi)]
        elif view == "top":
            a, b = u0 + lean, u1 + lean
            pts = [(a, -1.2), (b, -1.2), (b, 1.2), (a, 1.2)]
        else:
            pts = [(u0 + lean * v0, v0), (u1 + lean * v0, v0), (u1 + lean * v1, v1), (u0 + lean * v1, v1)]
        out.append(shape(kit, f"{id}.{view}", view, pts, color, material, z, tags=("crosses_seam",)))
    return out


def stripe(
    kit: Kit,
    id: str,
    a0: float,
    a1: float,
    color: str,
    views: str | Iterable[str] = "sides",
    u0: float = -0.05,
    u1: float = 1.05,
    material: Material = Material.gloss,
    z: int = 0,
) -> list[Element]:
    """A stripe along the car from u0 to u1: between v = a0..a1 on the sides, w = a0..a1 on top. On a face
    it runs across between v = a0..a1, where u0..u1 reaches that end (u0 <= 0 the nose, u1 >= 1 the tail)."""
    out = []
    for v in _views(kit, views):
        if view_kind(v) == "face":
            if not _reaches(v, u0, u1):
                continue
            pts = [(-FACE_W, a0), (FACE_W, a0), (FACE_W, a1), (-FACE_W, a1)]
        else:
            pts = [(u0, a0), (u1, a0), (u1, a1), (u0, a1)]
        out.append(shape(kit, f"{id}.{v}", v, pts, color, material, z, tags=("crosses_seam",)))
    return out


def _reaches(face: str, *u: float) -> bool:
    """Whether a shape whose edges lie at these u reaches the face's end of the car."""
    return min(u) <= 0 if face == "front" else max(u) >= 1


def _region_geom(kit: Kit, region: str):
    """A part or group of the kit's body model (`kit.parts()`), an island id, or a view name for every
    island mapped in that view."""
    from livery import body

    return body.region(kit, region)


def panel(
    kit: Kit, id: str, region: str, color: str, material: Material = Material.gloss, z: int = 0
) -> Element:
    """A whole region (a part or group, an island or a view) in one colour."""
    d = carspace.path_d(_region_geom(kit, region))
    return Element(
        id=id,
        kind="panel",
        svg=f'<path fill-rule="evenodd" d="{d}" fill="{color}"/>',
        material=Material(material),
        z=z,
        car={"region": region},
    )


def _frame(
    kit: Kit, view: str, at: tuple[float, float], facing: str = "front"
) -> tuple[np.ndarray, np.ndarray, str]:
    """Texture position and 2x2 matrix for a text frame (x reading direction, y down) at a car-space point.

    One frame unit is one unit of u (car length). On the sides, text reads left to right as seen, so on
    the right side it runs nose-wards; on a face too, seen from ahead or behind; on top it reads from the
    front (or from behind, `facing="rear"`).
    """
    piece, xy, jac = carspace.anchor(kit, view, at)
    a, b = carspace.unit_lengths(kit, view)
    if view == "side_l" or view == "rear":  # seen from behind, the car's right (w = 1) is on the right
        f = np.array([[1.0 / a, 0.0], [0.0, -1.0 / b]])
    elif view == "side_r" or view == "front":
        f = np.array([[-1.0 / a, 0.0], [0.0, -1.0 / b]])
    else:
        t = carspace.kit_data(kit)["top_transform"]
        w_per_u = t["x_per_u"] / abs(t["y_per_w"])  # top-view texture is laid out at one scale
        f = (
            np.array([[0.0, -1.0], [-w_per_u, 0.0]])
            if facing == "front"
            else np.array([[0.0, 1.0], [w_per_u, 0.0]])
        )
    return xy, jac @ f, piece.island


def _clipped(id: str, d: str, inner: str) -> str:
    return (
        f'<clipPath id="clip-{id}"><path fill-rule="evenodd" d="{d}"/></clipPath>'
        f'<g clip-path="url(#clip-{id})">{inner}</g>'
    )


def _clip(kit: Kit, id: str, view: str, island: str, inner: str) -> str:
    area = next(pc.area for pc in carspace.pieces(kit, view) if pc.island == island)
    return _clipped(id, carspace.path_d(area), inner)


def _matrix(xy: np.ndarray, m: np.ndarray) -> str:
    return f"matrix({m[0, 0]:.5f},{m[1, 0]:.5f},{m[0, 1]:.5f},{m[1, 1]:.5f},{xy[0]:.2f},{xy[1]:.2f})"


def text(
    kit: Kit,
    id: str,
    view: str,
    string: str,
    at: tuple[float, float],
    size: float,
    color: str,
    font: str = "Barlow",
    weight: int = 800,
    anchor: str = "middle",
    facing: str = "front",
    material: Material = Material.gloss,
    z: int = 0,
) -> Element:
    """Text centred (or started/ended, per `anchor`) at a car-space point; `size` is the em height in u.

    It is laid out flat in the anchor's island and clipped to that island, so keep it inside one panel.
    """
    xy, m, island = _frame(kit, view, at, facing)
    esc = string.replace("&", "&amp;").replace("<", "&lt;")
    inner = (
        f'<text transform="{_matrix(xy, m)}" font-family="{font}" font-weight="{weight}" '
        f'font-size="{size:.5f}" text-anchor="{anchor}" dominant-baseline="central" fill="{color}">'
        f"{esc}</text>"
    )
    return Element(
        id=id,
        kind="text",
        svg=_clip(kit, id, view, island, inner),
        material=Material(material),
        z=z,
        car={"view": view, "at": tuple(at), "size": size, "island": island, "string": string},
    )


def _place_svg(
    kit: Kit,
    id: str,
    view: str,
    path: Path,
    at: tuple[float, float],
    height: float,
    facing: str,
    color: str | None,
    material: Material,
    z: int,
    car: dict,
) -> Element:
    root = ET.fromstring(path.read_text(encoding="utf-8"))
    vb = [float(x) for x in re.split(r"[\s,]+", root.attrib["viewBox"].strip())]
    scale = height / vb[3]
    xy, m, island = _frame(kit, view, at, facing)
    inner_m = m @ np.array([[scale, 0], [0, scale]])
    offset = xy - inner_m @ np.array([vb[0] + vb[2] / 2, vb[1] + vb[3] / 2])
    body = "".join(ET.tostring(child, encoding="unicode") for child in root)
    # Serialised children carry namespace prefixes and declarations; inside our document they need neither.
    body = re.sub(r'\sxmlns(:\w+)?="[^"]*"', "", body)
    body = re.sub(r"<(/?)\w+:", r"<\1", body)
    if color:
        body = re.sub(r'\b(fill|stroke)="(?!none)[^"]*"', lambda g: f'{g.group(1)}="{color}"', body)
    attrs = " ".join(f'{k}="{v}"' for k, v in root.attrib.items() if k in ("shape-rendering",))
    inner = f'<g transform="{_matrix(offset, inner_m)}" {attrs}>{body}</g>'
    return Element(
        id=id,
        kind="logo",
        svg=_clip(kit, id, view, island, inner),
        material=Material(material),
        z=z,
        car={"view": view, "at": tuple(at), "height": height, "island": island, **car},
    )


def _px_per_unit(kit: Kit, view: str, at: tuple[float, float], facing: str) -> float:
    """Texture px per car length, across the reading direction at a point."""
    return float(np.linalg.norm(_frame(kit, view, at, facing)[1][:, 1]))


def _need(b: sponsors.Brand, v: sponsors.Variant) -> tuple[float, str]:
    """The height a variant needs here, in texture px, and why."""
    t = _thresholds()
    need = b.min_height_px(v, t.min_stroke_px, t.large_min_px)
    return need, "strokes and counters" if need == v.min_height_px(t.min_stroke_px) else "large-only brand"


def _too_small(name: str, variant: str, px: float, need: tuple[float, str], height: float) -> str:
    return (
        f"{name} {variant} is {px:.0f} px high here and needs {need[0]:.0f} px for its {need[1]} "
        f"(at least {height * need[0] / px:.4f} car lengths)"
    )


def _thresholds():
    from livery.lint.run import thresholds

    return thresholds()


def sponsor(
    kit: Kit,
    id: str,
    view: str,
    brand: str,
    at: tuple[float, float],
    color: str,
    fit: tuple[float, float] | None = None,
    height: float | None = None,
    variant: str | None = None,
    facing: str = "front",
    material: Material = Material.gloss,
    z: int = 0,
) -> Element:
    """A sponsor brand centred at a car-space point: the design's own (designs/<name>/sponsors) or the
    roster's (assets/sponsors). `fit=(w, h)` in car lengths scales it to fill that box; `height` sets its
    height instead. `color` is one colour for the whole mark, or "own" for the brand's file colours.
    `variant` is lockup, wordmark or mark; by default the lockup if the brand has one. A roster variant not
    yet approved is placed, and keeps the build from a final review's pin until it is."""
    if (fit is None) == (height is None):
        raise sponsors.SponsorError(f"{id}: give fit=(w, h) or height, not both")
    b = sponsors.load(brand)
    v = b.variant(variant)
    h = height if fit is None else min(fit[1], fit[0] / v.aspect)
    px = h * _px_per_unit(kit, view, at, facing)
    need = _need(b, v)
    if px < need[0]:
        raise sponsors.SponsorError(f"{id}: {_too_small(brand, v.path.stem, px, need, h)}")
    recolour = None if color == "own" else color
    car = {"file": v.path.name, "brand": brand, "variant": v.path.stem, "own": b.local}
    return _place_svg(kit, id, view, v.path, at, h, facing, recolour, material, z, car)


# A group's marks are sized between equal heights (0) and equal ink areas (0.5): a long thin wordmark next
# to a compact mark comes out a little shorter, as a sponsor row is balanced by eye.
OPTICAL = 0.25


def _length(kit: Kit, view: str, a: tuple[float, float], b: tuple[float, float]) -> float:
    """Car lengths between two car-space points of one view, as seen."""
    ka, kb = carspace.unit_lengths(kit, view)
    return float(np.hypot((b[0] - a[0]) * ka, (b[1] - a[1]) * kb))


def sponsor_group(
    kit: Kit,
    id: str,
    view: str,
    brands: Iterable[str | tuple[str, str]],
    start: tuple[float, float],
    end: tuple[float, float],
    height: float,
    color: str,
    axis: str = "row",
    gap: float = 0.3,
    facing: str = "front",
    material: Material = Material.gloss,
    z: int = 0,
) -> list[Element]:
    """Sponsors spread evenly along the line from `start` to `end`, in the order given: side by side
    (`axis="row"`) or stacked (`axis="column"`). A brand is a name, or (name, variant).

    The tallest mark is `height` (car lengths) and the others are sized to balance it; gaps are at least
    `gap` of that height. A group that doesn't fit shrinks, and fails rather than going below a mark's
    minimum size: drop a brand or lengthen the field.
    """
    picks = [(b, None) if isinstance(b, str) else tuple(b) for b in brands]
    names = [b for b, _ in picks]
    if len(set(names)) < len(names):
        raise sponsors.SponsorError(f"{id}: a brand appears twice in one group: {names}")
    loaded = [sponsors.load(b) for b, _ in picks]
    marks = [b.variant(v) for b, (_, v) in zip(loaded, picks, strict=True)]
    rel = np.array([(v.ink * v.aspect) ** -OPTICAL for v in marks])
    rel /= rel.max()
    extent = rel * (np.array([v.aspect for v in marks]) if axis == "row" else 1.0)
    length = _length(kit, view, start, end)
    natural = height * (extent.sum() + gap * (len(marks) - 1))
    s = min(1.0, length / natural)
    if len(marks) > 1:
        step = s * height * gap + (length - s * natural) / (len(marks) - 1)  # spare length goes in the gaps
        offsets = np.cumsum(np.r_[0.0, s * height * extent[:-1] + step]) + s * height * extent / 2
    else:
        offsets = np.array([length / 2])
    try:
        start_px = _px_per_unit(kit, view, start, facing)
    except ValueError:
        raise sponsors.SponsorError(f"{id}: the field starts at {start}, off every mapped island") from None
    placed, short, off = [], [], []
    for (brand, _), b, v, rh, d in zip(picks, loaded, marks, rel, offsets, strict=True):
        t = d / length if length else 0.5
        at = (start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t)
        h = s * height * rh
        try:
            px = h * _px_per_unit(kit, view, at, facing)
        except ValueError:  # judged at the start's scale; reported once the sizes are right
            px = h * start_px
            off.append(f"{brand} lands at ({at[0]:.3f}, {at[1]:.3f}), off every mapped island")
        need = _need(b, v)
        if px < need[0]:
            short.append(_too_small(brand, v.path.stem, px, need, h))
        placed.append((brand, v.path.stem, at, h))
    if short:
        raise sponsors.SponsorError(
            f"{id}: {len(marks)} marks don't fit in {length:.3f} car lengths at their minimum sizes: "
            + "; ".join(short)
            + ". Drop a brand or lengthen the field"
        )
    if off:
        raise sponsors.SponsorError(f"{id}: " + "; ".join(off) + ". Move the field")
    return [
        sponsor(kit, f"{id}.{brand}", view, brand, at, color, None, h, variant, facing, material, z)
        for brand, variant, at, h in placed
    ]


def marks(
    kit: Kit,
    id: str,
    name: str,
    at: tuple[str, float, float] | str,
    color: str,
    height: float,
    toward: str | None = None,
    facing: str = "front",
    material: Material = Material.gloss,
    z: int = 0,
) -> Element:
    """A race marking from assets/marks (`livery.marks`), `height` car lengths tall in one colour, centred
    on an anchor `at` = (view, u, v or w), or on a feature of the kit's body model by name (its view, and
    its point, a line's middle or an area's centre). One that points, like a tow arrow, points `toward` the
    nose or the tail: the variant drawn that way is placed, so its letters still read. It is refused below
    its minimum height; one not yet approved (`livery review --marks`) keeps the build from a final
    review's pin."""
    from livery.marks import load as load_mark
    from livery.marks import variant_for

    if isinstance(at, str):
        f = kit.feature(at)
        view, (u, a) = f.view, f.at
    else:
        view, u, a = at
    m = load_mark(name)
    v = m.variant(variant_for(view, toward))
    px = height * _px_per_unit(kit, view, (u, a), facing)
    need = _need(m, v)
    if px < need[0]:
        raise sponsors.SponsorError(f"{id}: {_too_small(name, v.path.stem, px, need, height)}")
    car = {"file": v.path.name, "mark": name, "variant": v.path.stem}
    return _place_svg(kit, id, view, v.path, (u, a), height, facing, color, material, z, car)


def pattern_fill(
    kit: Kit, id: str, region: str, tile: str, tile_px: float, material: Material = Material.gloss, z: int = 0
) -> Element:
    """A region filled with a repeating tile: `tile` is SVG for one square tile, `tile_px` texture px wide."""
    d = carspace.path_d(_region_geom(kit, region))
    svg_ = (
        f'<pattern id="pat-{id}" patternUnits="userSpaceOnUse" width="{tile_px}" height="{tile_px}">'
        f"{tile}</pattern>"
        f'<path fill-rule="evenodd" d="{d}" fill="url(#pat-{id})"/>'
    )
    return Element(id=id, kind="pattern", svg=svg_, material=Material(material), z=z, car={"region": region})


def mirror_sides(
    primitive: Callable[..., Element | list[Element]], kit: Kit, id: str, *args, **kwargs
) -> list[Element]:
    """The same placement on both sides. Text and logos are laid out afresh on each, so both read forwards;
    a sponsor group keeps its order nose to tail on both."""
    out = []
    for view in kit.side_views:
        made = primitive(kit, f"{id}.{view}", view, *args, **kwargs)
        for el in [made] if isinstance(made, Element) else made:
            out.append(replace(el, tags=(*el.tags, "mirror_pair")))
    return out


def _regions_geom(kit: Kit, regions: str | Iterable[str]):
    from livery import body

    return body.regions(kit, regions)


def clip_to(kit: Kit, region: str | Iterable[str], element: Element) -> Element:
    """The element, cut to a region (a part or group, an island or a view), or to several."""
    geom = _regions_geom(kit, region)
    return replace(element, svg=_clipped(element.id, carspace.path_d(geom), element.svg))


def clip_out(kit: Kit, regions: str | Iterable[str], element: Element, margin: float = 0.0) -> Element:
    """The element with regions (parts or groups, islands or views) cut out of it, grown by `margin`
    texture px: for parts painted on their own."""
    cut = _regions_geom(kit, regions)
    if margin:
        cut = cut.buffer(margin)
    keep = shapely.box(0, 0, kit.canvas, kit.canvas).difference(cut)
    return replace(element, svg=_clipped(element.id, carspace.path_d(keep), element.svg))


def waive(element: Element, check: str, reason: str) -> Element:
    """The element with one output-lint check waived, visibly, with the reason the owner will read: check.json
    and a final review's pin list every waiver and what it kept out of the lint report."""
    from livery.lint.output import CHECKS

    if check not in CHECKS:
        raise LiveryError(
            f"{element.id}: there is no lint check {check!r}; the checks are {', '.join(CHECKS)}"
        )
    if not reason.strip():
        raise LiveryError(f"{element.id}: waiving {check} needs a reason {config.owner()} would accept")
    return replace(
        element, tags=(*element.tags, f"allow:{check}"), waivers=(*element.waivers, (check, reason.strip()))
    )


def svg(
    raw: str, id: str, material: Material = Material.gloss, z: int = 0, tags: tuple[str, ...] = ()
) -> Element:
    """Raw SVG in template pixels: the escape hatch for anything the library can't draw yet."""
    return Element(id=id, kind="svg", svg=raw, material=Material(material), z=z, tags=tags)
