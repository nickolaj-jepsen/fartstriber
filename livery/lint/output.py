"""The output lint: checks a built livery against the kit. Errors and warnings name the element by id.

Thresholds are numbers in policy.toml [lint] (texture pixels at the kit's canvas size unless noted).
"""

import io
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import cast

import cv2
import numpy as np
import resvg_py
import shapely
from PIL import Image, ImageDraw
from scipy import ndimage
from shapely.geometry.base import BaseGeometry

from livery import body, carspace, visibility
from livery.body import Feature
from livery.compile import SVG_NS
from livery.config import Workspace
from livery.kit import Kit
from livery.lint.edges import MEET_PX, Boundary, Run, boundary, polygons, runs
from livery.model import Element, Livery, Material
from livery.visibility import Visibility

LINT_SCALE = 2  # element masks are rendered at canvas / LINT_SCALE
TRANSPARENCY_ATTRS = ("opacity", "fill-opacity", "stroke-opacity")
N = f"{{{SVG_NS}}}"


@dataclass(frozen=True)
class Thresholds:
    """The [lint] values in policy.toml, which is their only source."""

    off_car_px: int  # texture px of an element allowed off the islands (antialiasing, bleed)
    keep_clear_px: int
    keep_clear_strict: tuple[str, ...]
    near_tangent_px: float
    near_tangent_run_px: float
    min_feature_px: float
    min_stroke_px: float
    min_outline_px: float  # outline strokes (keylines, pinlines) thinner than this vanish in the sim
    min_text_px: float
    large_min_px: float  # a large-only brand is never placed smaller (headline sizes, not a sill row)
    max_colours: int
    seam_min_breaks: int
    seam_break_share: float  # of the seam points an element reaches; its own edges account for some
    number_colour: str
    min_number_contrast: float
    max_texels_per_px: float  # more foreshortened than this in every preview view is not seen well
    max_unseen_share: float
    min_screen_px: float  # screen px, at the capture's window size
    crease_kink: float
    crease_share: float
    max_hidden_detail_share: float


# Every check the output lint runs, by the name its findings carry and `waive` takes.
CHECKS = (
    "forbidden_svg",
    "min_size",
    "orientation",
    "text_size",
    "empty",
    "off_car",
    "keep_clear",
    "occluded",
    "near_tangent",
    "seam",
    "palette",
    "finish_reach",
    "number_contrast",
    "unseen",
    "screen_width",
    "crease",
    "hidden_detail",
)
DETAIL_PIECES = 4  # an element in this many separate pieces or more is detail, like a pattern
CREASE_ACROSS = 0.5  # a crease line counts where it runs at least this share of the way across a mark


@dataclass
class Finding:
    level: str  # "error" or "warning", or "waived" when the element's waiver turned it off
    check: str
    id: str
    message: str


def _render_alpha(svg: str, ws: Workspace, size: int) -> np.ndarray:
    png = resvg_py.svg_to_bytes(svg_string=svg, skip_system_fonts=True, font_dirs=[str(ws.fonts)], width=size)
    return np.asarray(Image.open(io.BytesIO(bytes(png))).convert("RGBA"))[:, :, 3] > 127


def _element_doc(e: Element, kit: Kit) -> str:
    c = kit.canvas
    return (  # the viewBox makes a smaller render scale the drawing instead of cropping it
        f'<svg xmlns="{SVG_NS}" xmlns:xlink="http://www.w3.org/1999/xlink" width="{c}" height="{c}" '
        f'viewBox="0 0 {c} {c}">{e.svg}</svg>'
    )


def _downscale(mask: np.ndarray, size: int) -> np.ndarray:
    return np.asarray(Image.fromarray(mask.astype(np.uint8) * 255).resize((size, size), Image.BOX)) > 127


# --- SVG structure -------------------------------------------------------------------------------------


def _parse_transform(t: str) -> np.ndarray:
    m = np.eye(3)
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", t or ""):
        v = [float(x) for x in re.split(r"[\s,]+", args.strip()) if x]
        if name == "matrix":
            step = np.array([[v[0], v[2], v[4]], [v[1], v[3], v[5]], [0, 0, 1]])
        elif name == "translate":
            step = np.array([[1, 0, v[0]], [0, 1, v[1] if len(v) > 1 else 0], [0, 0, 1]])
        elif name == "scale":
            sx = v[0]
            sy = v[1] if len(v) > 1 else v[0]
            step = np.diag([sx, sy, 1.0])
        elif name == "rotate":
            a = np.radians(v[0])
            step = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
            if len(v) == 3:
                step = (
                    np.array([[1, 0, v[1]], [0, 1, v[2]], [0, 0, 1]])
                    @ step
                    @ np.array([[1, 0, -v[1]], [0, 1, -v[2]], [0, 0, 1]])
                )
        else:
            continue
        m = m @ step
    return m


def _walk(el: ET.Element, m: np.ndarray, out: list) -> None:
    here = m @ _parse_transform(el.attrib.get("transform", ""))
    out.append((el, here))
    for child in el:
        _walk(child, here, out)


def _nodes(e: Element) -> list[tuple[ET.Element, np.ndarray]]:
    root = ET.fromstring(f'<g xmlns="{SVG_NS}">{e.svg}</g>')
    out: list = []
    _walk(root, np.eye(3), out)
    return out


def _forbidden(e: Element, nodes) -> list[str]:
    """Transparency and filters: a half-transparent element has no single material for the spec map."""
    found = []
    for el, _ in nodes:
        tag = el.tag.replace(N, "")
        style = el.attrib.get("style", "")
        for attr in TRANSPARENCY_ATTRS:
            val = (
                el.attrib.get(attr)
                or (re.search(rf"(?<![-\w]){attr}\s*:\s*([^;]+)", style) or [None, None])[1]
            )
            if val is not None and float(val.strip().rstrip("%")) < (100 if "%" in val else 1):
                found.append(f"{attr}={val.strip()} on <{tag}>")
        if tag in ("filter", "feGaussianBlur", "mask") or "filter" in el.attrib or "filter:" in style:
            found.append(f"<{tag}> uses a filter or mask")
        if tag == "image":
            found.append("<image>: paint is vector, every pixel one colour and one material")
    return found


def _strokes(nodes) -> list[float]:
    widths = []
    for el, m in nodes:
        stroke = el.attrib.get("stroke")
        if stroke and stroke != "none":
            w = float(el.attrib.get("stroke-width", "1"))
            widths.append(w * float(np.sqrt(abs(np.linalg.det(m[:2, :2])))))
    return widths


def _colours(nodes) -> set[str]:
    out = set()
    for el, _ in nodes:
        for attr in ("fill", "stroke"):
            v = el.attrib.get(attr, "").strip().lower()
            if v.startswith("#"):
                out.add(_norm_hex(v))
    return out


def _beyond_reach(kit: Kit, material: str, colours: set[str]) -> list[str]:
    """Colours brighter than the sim shows under this finish (the kit's measured lookup, `kit spec`): they
    render darker than asked, however the build brightens them."""
    lut = kit.compensation.get(Material(material))
    if not lut:
        return []
    reach = np.asarray(lut["reachable"], float)
    out = []
    for c in sorted(colours):
        rgb = np.array([int(c[i : i + 2], 16) for i in (1, 3, 5)], float)
        if (rgb > reach + 0.5).any():
            out.append(
                f"{c} under {material} asks for more than the sim shows: it reaches only "
                f"{'/'.join(str(round(x)) for x in reach)} (R/G/B), so it renders darker"
            )
    return out


def _norm_hex(v: str) -> str:
    v = v.lstrip("#")
    return "#" + ("".join(ch * 2 for ch in v) if len(v) == 3 else v[:6])


# --- orientation ---------------------------------------------------------------------------------------


def _screen(view: str, kit: Kit) -> np.ndarray:
    """Car space -> screen (x right, y down) as each view is looked at; text there must read normally."""
    asp = carspace.side_aspect(kit)
    return {
        "side_l": np.array([[1.0, 0.0], [0.0, -asp]]),
        "side_r": np.array([[-1.0, 0.0], [0.0, -asp]]),
        "top": np.array([[0.0, -1.0], [-1.0, 0.0]]),  # seen from the front: car right on the left
    }[view]


def _orientation(e: Element, nodes, kit: Kit) -> list[tuple[str, str]]:
    """Mirrored or backwards text and logos, judged on the car, not in the texture."""
    out = []
    for el, m in nodes:
        tag = el.tag.replace(N, "")
        is_logo = tag == "g" and e.kind == "logo" and "matrix" in el.attrib.get("transform", "")
        if tag != "text" and not is_logo:
            continue
        if "view" in e.car and "at" in e.car:  # placed by a primitive: judge it where it was anchored
            view = e.car["view"]
            _, _, jac = carspace.anchor(kit, view, e.car["at"])
        else:
            x, y = float(el.attrib.get("x", 0)), float(el.attrib.get("y", 0))
            pos = m @ np.array([x, y, 1.0])
            found = carspace.to_car(kit, (pos[0], pos[1]))
            if found is None:
                continue
            view, _, jac = found
        frame = _screen(view, kit) @ np.linalg.inv(jac) @ m[:2, :2]
        what = f"text {el.text!r}" if tag == "text" else "logo"
        if np.linalg.det(frame) < 0:
            out.append(("mirrored", f"{what} reads mirrored on the {view}"))
        elif view != "top" and frame[0, 0] < 0:
            out.append(("backwards", f"{what} reads backwards or upside down on the {view}"))
        if tag == "text":
            size = float(el.attrib.get("font-size", "16")) * float(np.sqrt(abs(np.linalg.det(m[:2, :2]))))
            out.append(("text_size", f"{size:.1f}"))
    return out


# --- geometry ------------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Lines:
    """The near-tangent lines rasterised at the lint's scale. For every pixel: the distance to the nearest
    line or outline of any island (`meet`), and to the nearest kept line of its own island (`dist`), both
    in lint pixels, with that line and how far along it the nearest point is (texture px)."""

    b: Boundary
    meet: np.ndarray
    dist: np.ndarray
    line: np.ndarray
    pos: np.ndarray


def _points(ln: BaseGeometry) -> tuple[np.ndarray, np.ndarray]:
    """A line's points every half lint pixel, in lint pixels, and how far along it each is (texture px)."""
    xy = shapely.get_coordinates(shapely.segmentize(ln, LINT_SCALE / 2))
    return xy / LINT_SCALE, np.r_[0.0, np.cumsum(np.hypot(*np.diff(xy, axis=0).T))]


def _cells(xy: np.ndarray, x0: int, y0: int, w: int, h: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The pixel rows and columns of points (lint pixels) in a w x h window at (x0, y0), and which points
    those are."""
    c = xy.astype(np.intp) - np.array([x0, y0], np.intp)
    ok = (c[:, 0] >= 0) & (c[:, 1] >= 0) & (c[:, 0] < w) & (c[:, 1] < h)
    return c[ok, 1], c[ok, 0], ok


def _labels(b: Boundary, size: int) -> np.ndarray:
    """Each lint pixel's island (index + 1), a pixel between islands taking the nearest."""
    img = Image.new("I", (size, size), 0)
    draw = ImageDraw.Draw(img)
    for k in sorted(range(len(b.islands)), key=lambda k: -b.islands[k].area):  # holes may hold islands
        for poly in polygons(b.islands[k]):
            draw.polygon([(x / LINT_SCALE, y / LINT_SCALE) for x, y in poly.exterior.coords], fill=k + 1)
            for hole in poly.interiors:
                draw.polygon([(x / LINT_SCALE, y / LINT_SCALE) for x, y in hole.coords], fill=0)
    labels = np.asarray(img, np.int32)
    if not labels.any():
        return labels
    near = ndimage.distance_transform_edt(labels == 0, return_distances=False, return_indices=True)
    iy, ix = np.asarray(near)
    return labels[iy, ix]


def _rasterise(b: Boundary, canvas: int, reach: float) -> Lines:
    size = canvas // LINT_SCALE
    pts = [_points(ln) for ln in b.lines]
    every = np.zeros((size, size), bool)
    for xy, _ in [*pts, *(_points(ln) for ln in b.seams)]:
        rows, cols, _ok = _cells(xy, 0, 0, size, size)
        every[rows, cols] = True
    meet = np.asarray(ndimage.distance_transform_edt(~every))
    labels = _labels(b, size)
    boxes = ndimage.find_objects(labels)
    dist = np.full((size, size), np.inf)
    line = np.full((size, size), -1, np.int32)
    pos = np.zeros((size, size))
    pad = int(np.ceil(reach / LINT_SCALE)) + 2
    for k, mine in sorted(b.owned().items()):  # each island against its own lines only
        box = boxes[k] if k < len(boxes) else None
        if box is None:
            continue
        y0, x0 = max(box[0].start - pad, 0), max(box[1].start - pad, 0)
        y1, x1 = min(box[0].stop + pad, size), min(box[1].stop + pad, size)
        on = np.zeros((y1 - y0, x1 - x0), bool)
        which = np.full(on.shape, -1, np.int32)
        where = np.zeros(on.shape)
        for i in mine:
            xy, s = pts[i]
            rows, cols, ok = _cells(xy, x0, y0, x1 - x0, y1 - y0)
            on[rows, cols] = True
            which[rows, cols] = i
            where[rows, cols] = s[ok]
        if not on.any():
            continue
        found = cast(tuple[np.ndarray, np.ndarray], ndimage.distance_transform_edt(~on, return_indices=True))
        d, (iy, ix) = found
        sel = labels[y0:y1, x0:x1] == k + 1
        dist[y0:y1, x0:x1][sel] = np.asarray(d)[sel]
        line[y0:y1, x0:x1][sel] = which[iy, ix][sel]
        pos[y0:y1, x0:x1][sel] = where[iy, ix][sel]
    return Lines(b, meet, dist, line, pos)


_lines_cache: dict[tuple, Lines] = {}


def near_lines(kit: Kit, t: Thresholds) -> Lines:
    """The kit's near-tangent lines (`lint.edges.boundary`) as the lint judges them, built once per kit
    state and thresholds."""
    b = boundary(kit, t.keep_clear_strict)
    key = (id(b), kit.canvas, t.near_tangent_px)
    if key not in _lines_cache:
        _lines_cache.clear()
        _lines_cache[key] = _rasterise(b, kit.canvas, t.near_tangent_px)
    return _lines_cache[key]


def path_mask(d: str, canvas: int) -> np.ndarray:
    """A texture path (SVG path data) rendered as the lint renders an element of it."""
    doc = (
        f'<svg xmlns="{SVG_NS}" width="{canvas}" height="{canvas}" viewBox="0 0 {canvas} {canvas}">'
        f'<path fill-rule="evenodd" d="{d}" fill="#000"/></svg>'
    )
    png = resvg_py.svg_to_bytes(svg_string=doc, skip_system_fonts=True, width=canvas // LINT_SCALE)
    return np.asarray(Image.open(io.BytesIO(bytes(png))).convert("RGBA"))[:, :, 3] > 127


def near_tangent(mask: np.ndarray, lines: Lines, t: Thresholds) -> list[Run]:
    """The stretches of line an element's free edge runs alongside within reach, each measured along its
    line. A free edge meets no line or island outline."""
    edge = mask & ~ndimage.binary_erosion(mask)
    near = edge & (lines.meet > MEET_PX / LINT_SCALE) & (lines.dist <= t.near_tangent_px / LINT_SCALE)
    if not near.any():
        return []
    return runs(lines.b, lines.line[near], lines.pos[near])


def opening_px(t: Thresholds) -> int:
    """The side, in texture px, of the square the min_size check opens an element with: a piece too
    narrow to hold it loses its pixels (a band leaning at an angle needs it to fit across)."""
    return (2 * max(1, round(t.min_feature_px / LINT_SCALE / 2)) + 1) * LINT_SCALE


def _features(kit: Kit, kind: str) -> list[tuple[Feature, np.ndarray]]:
    """The body model's features of one kind (`livery.body`), each as the mask the lint compares
    elements with."""
    out = []
    for f, foot in body.zones(kit, kind):
        d = carspace.path_d(foot)
        if d:
            out.append((f, path_mask(d, kit.canvas)))
    return out


# --- what the preview cameras see ----------------------------------------------------------------------


@dataclass(frozen=True)
class Cameras:
    """The kit's visibility tables (`livery.visibility`) as the checks read them, at the lint's scale."""

    vis: Visibility
    seen: np.ndarray  # (h, w): some view samples the cell
    well: np.ndarray  # (views, h, w): the view sees it no more foreshortened than max_texels_per_px
    crease: np.ndarray  # (views, h, w): the view sees a crease there, and sees it well

    @classmethod
    def of(cls, vis: Visibility, t: Thresholds) -> "Cameras":
        well = vis.well_seen(t.max_texels_per_px)
        # A view that sees a surface edge-on makes a gentle turn in it look like a fold.
        crease = well & (np.nan_to_num(vis.crease, nan=0.0) >= t.crease_kink)
        return cls(vis, vis.seen().any(axis=0), well, crease)


def _pct(share: float) -> str:
    return f"{round(100 * share)}%"


def _px(width: float) -> str:
    return f"{np.floor(width * 10) / 10:g}"  # rounded down, so 7.96 under a limit of 8 never reads 8


def _seen_by(views: tuple[str, ...], shares: np.ndarray) -> str:
    named = [f"{views[i]} {_pct(float(shares[i]))}" for i in np.argsort(-shares) if shares[i] >= 0.05]
    return ", ".join(named)


def _pieces(mask: np.ndarray) -> tuple[np.ndarray, int]:
    """The mask's 8-connected pieces, labelled, and how many there are."""
    return cast(tuple[np.ndarray, int], ndimage.label(mask, structure=np.ones((3, 3))))


def _hull(mask: np.ndarray) -> np.ndarray:
    ys, xs = np.nonzero(mask)
    hull = cv2.convexHull(np.stack([xs, ys], axis=1).astype(np.int32))
    out = np.zeros(mask.shape, np.uint8)
    cv2.fillPoly(out, [hull.reshape(-1, 2)], 1)
    return out.astype(bool) | mask


def _straddle(mask: np.ndarray, crease: np.ndarray) -> tuple[float, np.ndarray | None]:
    """The largest share of a mark beyond a crease line that runs across it, and that line's cells.

    Each run of crease cells inside the mark's hull that crosses at least CREASE_ACROSS of the hull is
    taken as a straight line (its principal axis); the mark's share on the smaller side is how much of it
    folds away."""
    hull = _hull(mask)
    lab, n = _pieces(crease & hull)
    if not n:
        return 0.0, None
    hy, hx = np.nonzero(hull)
    my, mx = np.nonzero(mask)
    hull_pts = np.stack([hx, hy], axis=1).astype(float)
    pts = np.stack([mx, my], axis=1).astype(float)
    best, line = 0.0, None
    for k, sl in enumerate(ndimage.find_objects(lab), 1):
        cy, cx = np.nonzero(lab[sl] == k)
        if len(cy) < 3:
            continue
        c = np.stack([cx + sl[1].start, cy + sl[0].start], axis=1).astype(float)
        mid = c.mean(axis=0)
        along, across = np.linalg.svd(c - mid, full_matrices=False)[2]
        if np.ptp((c - mid) @ along) < CREASE_ACROSS * np.ptp((hull_pts - mid) @ along):
            continue
        side = (pts - mid) @ across
        share = min(float((side > 1).mean()), float((side < -1).mean()))
        if share > best:
            best, line = share, lab == k
    return best, line


def _camera_checks(
    e: Element, box: tuple[slice, slice], m: np.ndarray, cams: Cameras, t: Thresholds, add
) -> None:
    """Warnings from what the preview cameras see of what shows of one element: `m`, the cells of `box`
    on the visibility tables' grid that it paints and nothing drawn after it covers."""
    area = float(m.sum())
    views = cams.vis.views
    well = cams.well[(slice(None), *box)]
    by_view = (well & m).sum(axis=(1, 2)) / area
    hidden = float((m & ~cams.seen[box]).sum()) / area
    lost = float((m & ~well.any(axis=0)).sum()) / area
    major = cams.vis.major[(slice(None), *box)]
    squeezed = [
        f"{views[i]} {float(np.median(major[i][s])):.1f}"
        for i in range(len(views))
        if (s := m & np.isfinite(major[i]) & ~well[i]).sum() >= 0.05 * area
    ]
    parts = [f"{_pct(hidden)} is hidden from every view"] if hidden >= 0.005 else []
    if lost - hidden >= 0.005:
        parts.append(
            f"{_pct(lost - hidden)} is seen only more foreshortened than {t.max_texels_per_px:g} texels per px"
            + (f" ({', '.join(squeezed)})" if squeezed else "")
        )
    seen_by = _seen_by(views, by_view)
    unclear = "; ".join(parts) + (f"; seen well by {seen_by}" if seen_by else "")
    if e.kind in ("text", "logo"):  # text, sponsors and race marks; their sizes have checks of their own
        if lost > t.max_unseen_share:
            add("warning", "unseen", e.id, f"no preview view sees {_pct(lost)} of it well: {unclear}")
        crease = cams.crease[(slice(None), *box)]
        share, line = _straddle(m, crease.any(axis=0))
        if line is not None and share >= t.crease_share:
            on = (crease & line).sum(axis=(1, 2)) / line.sum()
            shown = [views[i] for i in np.argsort(-on) if on[i] >= 0.25] or ["a view"]
            verb = "shows" if len(shown) == 1 else "show"
            add(
                "warning",
                "crease",
                e.id,
                f"straddles a crease in the body that {' and '.join(shown)} {verb}: {_pct(share)} of it is "
                "beyond the fold, so it reads bent or cut",
            )
        return
    if e.kind == "panel":  # a whole island's colour, not a line or a piece of detail
        return
    pieces = _pieces(m)[1]
    if (e.kind == "pattern" or pieces >= DETAIL_PIECES) and lost > t.max_hidden_detail_share:
        add(
            "warning",
            "hidden_detail",
            e.id,
            f"{_pct(lost)} of its area ({pieces} piece{'' if pieces == 1 else 's'}) is where no preview view "
            f"sees it well: {unclear}",
        )
        return
    # Only lines and pieces narrow enough in the texture to be thin on screen where they are seen well.
    line = t.min_screen_px * t.max_texels_per_px / LINT_SCALE  # cells: wider is wide enough where seen well
    if visibility.width(m, line) < line:
        full = np.zeros(cams.seen.shape, bool)
        full[box] = m
        across = cams.vis.widths(full, t.min_screen_px)
        if across and max(across.values()) < t.min_screen_px:
            each = ", ".join(f"{v} {_px(w)}" for v, w in across.items())
            add(
                "warning",
                "screen_width",
                e.id,
                f"at most {_px(max(across.values()))} px across on screen ({each}), under "
                f"{t.min_screen_px:g} px in every view that sees it, so it hardly shows",
            )


def _camera_pass(livery: Livery, drawn: dict[int, tuple], cams: Cameras, t: Thresholds, add) -> None:
    """The camera checks on what shows of each element once everything drawn after it is painted over
    (compile's order: z, then insertion), so a pinline under a plate is judged by the strip it leaves."""
    covered = np.zeros(cams.seen.shape, bool)
    for i in sorted(drawn, key=lambda i: (livery.elements[i].z, i), reverse=True):
        box, mask = drawn[i]
        shows = mask & ~covered[box]
        covered[box] |= mask
        if shows.any():
            _camera_checks(livery.elements[i], box, shows, cams, t, add)


def _luminance(rgb: np.ndarray) -> float:
    c = rgb / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return float(0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2])


def lint_output(
    livery: Livery, kit: Kit, ws: Workspace, paint: np.ndarray, t: Thresholds, vis: Visibility | None = None
) -> list[Finding]:
    """`vis` is what the preview cameras see of the kit (`visibility.table` at LINT_SCALE); without it the
    checks that need it are skipped, with one note."""
    findings: list[Finding] = []
    size = kit.canvas // LINT_SCALE

    # An element can waive a check with tags=("allow:<check>",): visible in the script, for kit tools.
    waived = {e.id: {t[len("allow:") :] for t in e.tags if t.startswith("allow:")} for e in livery.elements}

    def add(level: str, check: str, id: str, message: str) -> None:
        waives = check in waived.get(id, set())
        findings.append(Finding("waived" if waives else level, check, id, message))

    islands = np.asarray(Image.open(kit.mask_path).convert("L")) > 127
    on_car = _downscale(ndimage.binary_dilation(islands, iterations=kit.bleed_px), size)
    kc_path = kit.dir / "overlays" / "keepclear.png"
    keepclear = (
        _downscale(np.asarray(Image.open(kc_path).convert("L")) > 127, size)
        if kc_path.exists()
        else np.zeros((size, size), bool)
    )
    # Zones of the strict kinds (the sim's number) must stay clear; others (series sponsor and banner
    # blocks, which the owner's races don't stamp) only warn, and are not edges a design must respect.
    strict_full = np.zeros((kit.canvas, kit.canvas), bool)
    for zone in carspace.kit_data(kit).get("keep_clear", []):
        if zone["kind"] in t.keep_clear_strict:
            x0, y0, x1, y1 = zone["box"]
            strict_full[y0:y1, x0:x1] = True
    strict = _downscale(strict_full, size)
    loose = keepclear & ~strict
    # What the sim draws over the paint (the door light panel) is strict for what must read, and hides
    # other paint; an occluder (the carbon side blade) hides what is under it from some views.
    overlays, occluders = _features(kit, "keep_clear"), _features(kit, "occluder")
    lines = near_lines(kit, t)
    seams = json.loads((kit.dir / "seams.json").read_text()) if (kit.dir / "seams.json").exists() else []

    cams = Cameras.of(vis, t) if vis is not None else None
    if cams is None:
        add(
            "warning",
            "no_capture",
            "livery",
            f"what the preview cameras see isn't checked: this machine has no calibration capture of {kit.name} "
            f"(livery kit capture {kit.name})",
        )
    palette = {_norm_hex(livery.base)}
    drawn: dict[int, tuple] = {}  # each element's mask, cropped to its box, for the camera checks
    for i, e in enumerate(livery.elements):
        nodes = _nodes(e)
        for problem in _forbidden(e, nodes):
            add("error", "forbidden_svg", e.id, problem)
        for w in _strokes(nodes):
            if w < t.min_stroke_px:
                add("error", "min_size", e.id, f"stroke {w:.1f} px is thinner than {t.min_stroke_px} px")
        for kind, msg in _orientation(e, nodes, kit):
            if kind == "text_size":
                if float(msg) < t.min_text_px:
                    add(
                        "error",
                        "text_size",
                        e.id,
                        f"text {float(msg):.0f} px high, below {t.min_text_px:.0f} px",
                    )
            else:
                add("error", "orientation", e.id, msg)
        if e.kind != "logo":
            palette |= _colours(nodes)

        mask = _render_alpha(_element_doc(e, kit), ws, size)
        if not mask.any():
            add("warning", "empty", e.id, "draws nothing")
            continue
        ys, xs = np.nonzero(mask)
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        box = np.s_[y0:y1, x0:x1]
        # Morphology on the element's neighbourhood, not the whole canvas: the same pixels, many times faster.
        side = opening_px(t) // LINT_SCALE
        pad = side + 1
        around = np.s_[max(y0 - pad, 0) : y1 + pad, max(x0 - pad, 0) : x1 + pad]
        off = int((mask & ~on_car).sum()) * LINT_SCALE**2
        if off > t.off_car_px:
            add("error", "off_car", e.id, f"{off} px lie off the car")
        overlap = int((mask & strict).sum()) * LINT_SCALE**2
        if overlap > t.keep_clear_px:
            level = "error" if e.kind in ("text", "logo") else "warning"
            add(level, "keep_clear", e.id, f"overlaps the sim's number zone ({overlap} px)")
        overlap = int((mask & loose).sum()) * LINT_SCALE**2
        if overlap > t.keep_clear_px and e.kind in ("text", "logo"):
            add("warning", "keep_clear", e.id, f"sits on a series sponsor or banner block ({overlap} px)")
        reads = e.kind in ("text", "logo")
        for f, zone in overlays:
            overlap = int((mask & zone).sum()) * LINT_SCALE**2
            if overlap > t.keep_clear_px and reads:
                add(
                    "error",
                    "keep_clear",
                    e.id,
                    f"lies under {f.title} ({f.name}), which covers the paint in the sim ({overlap} px)",
                )
            elif overlap > t.keep_clear_px:
                add(
                    "warning",
                    "keep_clear",
                    e.id,
                    f"{overlap} px lie under {f.title} ({f.name}), which covers them in the sim",
                )
        for f, zone in occluders if reads else ():
            overlap = int((mask & zone).sum()) * LINT_SCALE**2
            if overlap > t.keep_clear_px:
                add(
                    "warning",
                    "occluded",
                    e.id,
                    f"{overlap} px are hidden under {f.title} ({f.name}): {f.note}",
                )
        # Glyphs and logos are clipped to one panel and their small edges brush seams by nature.
        found = [] if e.kind in ("text", "logo") else near_tangent(mask, lines, t)
        run = max(found, key=lambda r: r.length, default=None)
        if run is not None and run.length >= t.near_tangent_run_px:
            x, y = lines.b.at(run.line, (run.s0 + run.s1) / 2)
            add(
                "error",
                "near_tangent",
                e.id,
                f"an edge runs within {t.near_tangent_px:.0f} px of a {lines.b.kinds[run.line]} for "
                f"{run.length:.0f} px along it, around texture ({x:.0f}, {y:.0f}), without meeting it; make it "
                "cross or stay clear",
            )
        drawn[i] = (box, mask[box])
        opened = ndimage.binary_opening(mask[around], structure=np.ones((side, side)))
        lost = int((mask[around] & ~opened).sum()) * LINT_SCALE**2
        if e.kind not in ("text", "logo", "pattern") and lost > 40 * t.min_feature_px**2:
            add(
                "warning",
                "min_size",
                e.id,
                f"{lost} px are in features thinner than {t.min_feature_px:.0f} px",
            )

        if "crosses_seam" in e.tags and seams:
            big = np.asarray(
                Image.fromarray(mask.astype(np.uint8)).resize((kit.canvas, kit.canvas), Image.NEAREST)
            )
            # The element itself must be on both sides of a seam pair or on neither; what other elements
            # paint on top is their business.
            touched = breaks = 0
            for ax, ay, bx, by in seams:
                if big[ay, ax] or big[by, bx]:
                    touched += 1
                    breaks += int(big[ay, ax] != big[by, bx])
            if breaks >= t.seam_min_breaks and breaks > t.seam_break_share * touched:
                add("error", "seam", e.id, f"stops at {breaks} of the {touched} seam points it reaches")

    if cams is not None:
        _camera_pass(livery, drawn, cams, t, add)
    for id, material, colours in [
        ("livery", livery.base_material, {_norm_hex(livery.base)}),
        *((e.id, e.material, _colours(_nodes(e))) for e in livery.elements),
    ]:
        for message in _beyond_reach(kit, material, colours):
            add("warning", "finish_reach", id, message)
    if len(palette) > t.max_colours:
        add(
            "warning",
            "palette",
            "livery",
            f"{len(palette)} colours ({', '.join(sorted(palette))}); aim for {t.max_colours} or fewer",
        )

    number = np.array([int(t.number_colour[i : i + 2], 16) for i in (1, 3, 5)], float)
    for zone in carspace.kit_data(kit).get("keep_clear", []):
        if zone["kind"] != "number":
            continue
        x0, y0, x1, y1 = zone["box"]
        under = paint[y0:y1, x0:x1].reshape(-1, 3).mean(0)
        hi, lo = sorted((_luminance(number), _luminance(under)), reverse=True)
        ratio = (hi + 0.05) / (lo + 0.05)
        if ratio < t.min_number_contrast:
            add(
                "warning",
                "number_contrast",
                f"number@{x0},{y0}",
                f"number contrast {ratio:.1f}:1 with the paint under it; aim for {t.min_number_contrast}:1",
            )
    return findings
