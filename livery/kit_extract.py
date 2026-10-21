"""`livery kit extract`: draft a kit from an iRacing paint template PSD."""

from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from scipy import ndimage

from livery import kit as kits
from livery.config import Workspace
from livery.kit import DEFAULT_BLEED_PX, DEFAULT_SPEC, DEFAULT_VIEWS

HELPERS = "Turn Off Before Exporting TGA"
MIN_ISLAND_PX = 200
OUTLINE_GROW_PX = 3  # the template's island outlines are dashed; growing them closes the gaps
GAP_HALF_WIDTH_PX = 6  # the gap network between outlines is at most this far from its edge
# Hidden-layer colours in iRacing templates.
NUMBER_BLOCK = (236, 0, 140)
SPONSOR_BLOCKS = {(0, 166, 81): "series_sponsor", (242, 108, 79): "banner", (1, 255, 0): "series_small"}


def _layer(psd, *path: str):
    node = psd
    for name in path:
        node = next((c for c in node if c.name == name), None)
        if node is None:
            raise ValueError(f"template has no layer {'/'.join(path)!r}")
    return node


def _full_canvas(layer, size: tuple[int, int]) -> np.ndarray:
    """A layer's own RGBA pixels on the full canvas; works for hidden layers, unlike composite()."""
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    img = layer.topil()
    if img is not None:
        canvas.paste(img.convert("RGBA"), (layer.left, layer.top))
    return np.asarray(canvas)


def _colour(rgba: np.ndarray, rgb: tuple[int, int, int], tol: int = 12) -> np.ndarray:
    return (rgba[..., 3] > 127) & (np.abs(rgba[..., :3].astype(int) - rgb).max(axis=2) <= tol)


def islands(paintable: np.ndarray, outlines: np.ndarray) -> np.ndarray:
    """Island index per pixel (0 = none), numbered top to bottom, then left to right.

    Pieces enclosed by outlines are islands. The gaps between outlines form one thin network spanning the
    canvas; where an outline has a hole, an island leaks into it, so thick parts of the network (more than
    GAP_HALF_WIDTH_PX from its edge) are recovered as islands too.
    """
    lab, _ = ndimage.label(paintable & ~ndimage.binary_dilation(outlines, iterations=OUTLINE_GROW_PX))
    h, w = paintable.shape
    pieces: list[np.ndarray] = []
    for i, sl in enumerate(ndimage.find_objects(lab), start=1):
        m = lab == i
        if sl[0].stop - sl[0].start > 0.95 * h and sl[1].stop - sl[1].start > 0.95 * w:
            core = ndimage.distance_transform_edt(m) > GAP_HALF_WIDTH_PX
            core_lab, _ = ndimage.label(core)
            # Grow each core back out to the pixels of the network it covers.
            dist, (iy, ix) = ndimage.distance_transform_edt(core_lab == 0, return_indices=True)
            owner = np.where(m & (dist <= GAP_HALF_WIDTH_PX + 1), core_lab[iy, ix], 0)
            pieces += [owner == j for j in range(1, int(core_lab.max()) + 1)]
        else:
            pieces.append(m)
    pieces = [p for p in pieces if p.sum() >= MIN_ISLAND_PX]
    order = sorted(range(len(pieces)), key=lambda k: tuple(np.argwhere(pieces[k])[0]))
    out = np.zeros(paintable.shape, dtype=np.uint16)
    for new, k in enumerate(order, start=1):
        out[pieces[k]] = new
    return out


SHUT_LINE_MIN_PX = 60


def shut_lines(wire: np.ndarray) -> np.ndarray:
    """Panel gaps (door edges, side skirt, engine cover): the wireframe draws them thicker than mesh lines."""
    white = (wire[..., 3] > 127) & (wire[..., :3].min(axis=2) > 200)
    thick = ndimage.binary_opening(white, structure=np.ones((2, 2)))
    lab, _ = ndimage.label(thick, structure=np.ones((3, 3)))
    keep = np.zeros_like(thick)
    for i, sl in enumerate(ndimage.find_objects(lab), start=1):
        if max(sl[0].stop - sl[0].start, sl[1].stop - sl[1].start) >= SHUT_LINE_MIN_PX:
            keep[sl] |= lab[sl] == i
    return keep


def mirror_axis(paintable: np.ndarray) -> float:
    """The horizontal line the two side views mirror about, from the mask's own symmetry."""
    h = paintable.shape[0]
    rows = np.arange(h // 8, h // 2)
    best = (0.0, h / 2)
    for twice in range(h, int(h * 1.25)):
        other = twice - rows
        ok = other < h
        score = (paintable[rows[ok]] == paintable[other[ok]]).mean()
        best = max(best, (score, twice / 2))
    return best[1]


def _view(bbox: list[int], axis: float, canvas: int) -> str:
    """A first guess from position, refined by `kit fit`."""
    cy = (bbox[1] + bbox[3]) / 2
    band = canvas * 0.1
    if abs(cy - axis) < band:
        return "top"
    return "upper" if cy < axis else "lower"


def _contours(mask: np.ndarray) -> str:
    cs, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    paths = []
    for c in cs:
        pts = cv2.approxPolyDP(c, 0.75, True)[:, 0]
        if len(pts) >= 3:
            paths.append("M" + " L".join(f"{x},{y}" for x, y in pts) + " Z")
    return " ".join(paths)


def _boxes(mask: np.ndarray) -> list[list[int]]:
    lab, _ = ndimage.label(mask)
    return [[s[1].start, s[0].start, s[1].stop, s[0].stop] for s in ndimage.find_objects(lab)]


def extract(ws: Workspace, psd_path: Path, car: str, paint_folder: str) -> Path:
    """Writes the kit's template-derived files. Re-run on the same template, it keeps what later commands
    wrote; on another one, whose islands may be numbered differently, it drops the maps and the approval."""
    from psd_tools import PSDImage

    template = kits.fingerprint(Path(psd_path))
    model = kits.model_path(ws.ui_cars, paint_folder)
    psd = PSDImage.open(psd_path)
    if psd.width != psd.height:
        raise ValueError(f"expected a square template, got {psd.size}")
    size, canvas = psd.size, psd.width
    d = ws.kits / car
    ov = d / "overlays"
    ov.mkdir(parents=True, exist_ok=True)

    paintable = _full_canvas(_layer(psd, HELPERS, "Mask"), size)[..., 3] <= 127
    wire = _full_canvas(_layer(psd, HELPERS, "Wire"), size)
    numbers = _colour(_full_canvas(_layer(psd, HELPERS, "Number Blocks"), size), NUMBER_BLOCK)
    sponsor_rgba = _full_canvas(_layer(psd, HELPERS, "Sponsor Blocks"), size)

    Image.fromarray(wire).save(ov / "wireframe.png")
    Image.fromarray(np.where(shut_lines(wire), 255, 0).astype(np.uint8)).save(ov / "shutlines.png")
    isl = islands(paintable, _colour(wire, (1, 255, 0), tol=40))
    Image.fromarray(isl).save(ov / "islands.png")
    # What the car shows: the islands. Bleed grows paint outward from here (Phase 3 bleed test).
    Image.fromarray(np.where(isl > 0, 255, 0).astype(np.uint8)).save(ov / "mask.png")

    keep_clear = [{"kind": "number", "box": b} for b in _boxes(numbers)]
    clear_px = numbers.copy()
    for rgb, kind in SPONSOR_BLOCKS.items():
        m = _colour(sponsor_rgba, rgb)
        clear_px |= m
        keep_clear += [{"kind": kind, "box": b} for b in _boxes(m)]
    Image.fromarray(np.where(clear_px, 255, 0).astype(np.uint8)).save(ov / "keepclear.png")

    axis = mirror_axis(paintable)
    regions, paths = [], []
    for i, sl in enumerate(ndimage.find_objects(isl), start=1):
        m = isl[sl] == i
        bbox = [sl[1].start, sl[0].start, sl[1].stop, sl[0].stop]
        rid = f"i{i:03d}"
        regions.append({"id": rid, "view": _view(bbox, axis, canvas), "bbox": bbox, "area": int(m.sum())})
        full = np.zeros(paintable.shape, bool)
        full[sl] = m
        paths.append(f'<path data-id="{rid}" fill-rule="evenodd" d="{_contours(full)}"/>')
    (d / "regions.svg").write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas}" height="{canvas}">\n'
        + "\n".join(paths)
        + "\n</svg>\n",
        encoding="utf-8",
        newline="\n",
    )

    meta = kits.read_meta(d) if (d / kits.META).exists() else {}
    maps = kits.read_maps(d)
    was = meta.get("fingerprint", {}).get("template")
    if was and was["sha256"] != template["sha256"]:
        maps = {}
        (d / kits.APPROVAL).unlink(missing_ok=True)
    meta.setdefault("version", 0)
    meta.setdefault("views", list(DEFAULT_VIEWS))
    meta.setdefault("bleed_px", DEFAULT_BLEED_PX)
    meta.setdefault("spec_presets", {m.value: list(v) for m, v in DEFAULT_SPEC.items()})
    meta.update(
        fingerprint={"template": template, "model": kits.fingerprint(model) if model.exists() else None},
        canvas=canvas,
        paint_folder=paint_folder,
        mirror_axis_y=axis,
        keep_clear=keep_clear,
    )
    kits.write(d, meta=meta, maps={"regions": regions, **{k: v for k, v in maps.items() if k != "regions"}})
    return d
