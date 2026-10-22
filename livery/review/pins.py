"""Pins: boxes the owner draws on a Focus picture, each with a note, so a Change can say where.

A pin is `{view, box, note}`: the picture's key (a preview view such as `side_l`, `flat/views.png`, or a kit
picture's path) and the box as fractions of the picture, x0, y0, x1, y1. On Send each resolves to the template
pixels under it (a sim or baked shot through the kit's calibration capture, the flat views through the kit's
maps, the UI car through its own UV map) and the design's elements painted there, and keeps a crop of the
picture with the box drawn. A pin that resolves to nothing travels as its view, box and crop.
"""

import contextlib
import io
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import resvg_py
from PIL import Image, ImageDraw

from livery import kit as kits
from livery import stamps
from livery.compile import _recolour
from livery.config import Workspace
from livery.designs import kit_name, script
from livery.errors import LiveryError

MAX = 24  # pins on one item
FLAT = "flat/"  # a flat picture's view names its file: flat/views.png
SHARE = 0.03  # an element shows on at least this share of a pin's template pixels to be named
ID_PX = 768  # the longest side the element render of a pin's template box gets
CROP_MIN_PX = 48  # the least margin around a box in its crop...
CROP_MARGIN = 0.5  # ...else this share of the box's size
CROP_LONG_PX = 480  # a smaller crop is enlarged to this long edge: the critic misreads small images
BOX = (0, 230, 255)  # a crop's box: cyan inside black, which no livery colour hides


def refusal(pins: object, views: set[str]) -> str | None:
    """Why a decision's pins can't be stored, or None: each names a picture of its item, a box inside it
    with x0 < x1 and y0 < y1, and a note (maybe empty)."""
    if not isinstance(pins, list) or len(pins) > MAX:
        return f"pins are a list of at most {MAX}"
    for p in pins:
        if not isinstance(p, dict) or not isinstance(p.get("note"), str):
            return "each pin is {view, box, note}"
        if p.get("view") not in views:
            return f"a pin's view isn't a picture of this item: {p.get('view')!r}"
        box = p.get("box")
        if not (
            isinstance(box, list)
            and len(box) == 4
            and all(isinstance(v, int | float) and 0 <= v <= 1 for v in box)
            and box[0] < box[2]
            and box[1] < box[3]
        ):
            return "a pin's box is [x0, y0, x1, y1], fractions of its picture, x0 < x1 and y0 < y1"
    return None


def views_of(item: dict) -> dict[str, str]:
    """The pictures a Focus item may be pinned on, by the key a pin names them with: its preview views, its
    flat pictures (flat/<file>), else its own pictures by path. Paths are relative to its checkout."""
    out = dict(item.get("views") or {})
    out |= {FLAT + Path(p).name: p for p in item.get("flat") or []}
    if not out:
        out = {p: p for p in item.get("images") or []}
    return out


def _px_box(box: list[float], w: int, h: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = box
    return (
        int(np.floor(x0 * w)),
        int(np.floor(y0 * h)),
        max(int(np.ceil(x1 * w)), int(np.floor(x0 * w)) + 1),
        max(int(np.ceil(y1 * h)), int(np.floor(y0 * h)) + 1),
    )


def crop(src: Path, box: list[float]) -> Image.Image:
    """The picture around the box, with a margin and the box outlined, enlarged when small."""
    img = Image.open(src).convert("RGB")
    x0, y0, x1, y1 = _px_box(box, img.width, img.height)
    mx = max(CROP_MIN_PX, round((x1 - x0) * CROP_MARGIN))
    my = max(CROP_MIN_PX, round((y1 - y0) * CROP_MARGIN))
    left, top = max(x0 - mx, 0), max(y0 - my, 0)
    out = img.crop((left, top, min(x1 + mx, img.width), min(y1 + my, img.height)))
    scale = max(1.0, CROP_LONG_PX / max(out.width, out.height))
    if scale > 1:
        out = out.resize((round(out.width * scale), round(out.height * scale)), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(out)
    r = [(x0 - left) * scale, (y0 - top) * scale, (x1 - left) * scale - 1, (y1 - top) * scale - 1]
    w = max(2, round(scale * 2))
    draw.rectangle([r[0] - 1, r[1] - 1, r[2] + 1, r[3] + 1], outline=(0, 0, 0), width=w + 2)
    draw.rectangle(r, outline=BOX, width=w)
    return out


def _in_box(tx: np.ndarray, box: list[float]) -> np.ndarray:
    """The template pixels of a per-pixel texel map (h, w, 2; -1 for none) inside the box, (n, 2) x, y."""
    h, w = tx.shape[:2]
    x0, y0, x1, y1 = _px_box(box, w, h)
    sel = tx[y0:y1, x0:x1].reshape(-1, 2)
    return sel[sel[:, 0] >= 0]


def _shot_texels(kit: kits.Kit, view: str, picture: Path, box: list[float]) -> np.ndarray | None:
    """Through the kit's calibration capture: the texel the sim draws at each pixel of the view. A shot
    framed otherwise than the capture (another window shape) resolves to nothing."""
    from livery import graycode, visibility

    run = visibility.capture_dir(kit.name)
    path = run / view / "geometry.npz" if run is not None else None
    if path is None or not path.exists():
        return None
    vm = graycode.ViewMap.load(path)
    h, w = vm.pix.shape
    with Image.open(picture) as img:
        if abs(img.width / img.height - w / h) > 0.01:
            return None
    tx = np.full((h, w, 2), -1, np.int32)
    tx[vm.pix] = np.clip(np.floor(vm.uv), 0, kit.canvas - 1).astype(np.int32)
    return _in_box(tx, box)


def _flat_texels(ws: Workspace, kit: kits.Kit, file: str, box: list[float]) -> np.ndarray | None:
    from livery import preview_flat, ui_preview

    if file in ("views.png", "squint.png"):  # the squint is views.png shrunk
        try:
            tx = preview_flat.views_texels(kit)
        except (LiveryError, OSError, KeyError):  # a kit without maps
            return None
        return _in_box(tx, box)
    if file == "car.png":
        layers = ui_preview.load(ws, kit.paint_folder)
        return None if layers is None else _in_box(ui_preview.texels(layers, kit.canvas), box)
    return None


def elements(svg: str, texels: np.ndarray, fonts: Path) -> list[str]:
    """The elements of a build's layers.svg painted on the topmost layer at these template pixels (n, 2),
    each on at least SHARE of them, the most first. One render of the texels' bounding box, each element
    in a colour of its own, edges not antialiased, so each pixel names exactly one element."""
    root = ET.fromstring(svg)
    groups = [g for g in root if g.attrib.get("data-id")]
    for k, g in enumerate(groups, 1):
        colour = f"#{k:06x}"
        g.attrib["fill"] = colour
        _recolour(g, colour)
    (x0, y0), (x1, y1) = texels.min(0), texels.max(0) + 1
    step = max(1, -(-max(x1 - x0, y1 - y0) // ID_PX))
    gw, gh = -(-(x1 - x0) // step), -(-(y1 - y0) // step)
    root.attrib |= {
        "width": str(gw),
        "height": str(gh),
        "viewBox": f"{x0} {y0} {gw * step} {gh * step}",
        "shape-rendering": "crispEdges",
    }
    png = resvg_py.svg_to_bytes(
        svg_string=ET.tostring(root, encoding="unicode"), skip_system_fonts=True, font_dirs=[str(fonts)]
    )
    rgba = np.asarray(Image.open(io.BytesIO(bytes(png))).convert("RGBA")).astype(np.int64)
    ids = (rgba[..., 0] << 16) | (rgba[..., 1] << 8) | rgba[..., 2]
    ids[rgba[..., 3] < 128] = 0
    cells = np.unique((texels - (x0, y0)) // step, axis=0)
    under = ids[cells[:, 1], cells[:, 0]]
    under = under[(under > 0) & (under <= len(groups))]
    found, counts = np.unique(under, return_counts=True)
    order = np.argsort(-counts, kind="stable")
    return [groups[int(found[i]) - 1].attrib["data-id"] for i in order if counts[i] >= SHARE * len(cells)]


def _layers(ws: Workspace, name: str, item: dict) -> str | None:
    """The layers.svg of the build the item showed: a final's pinned build, else the design's last build."""
    from livery.check import checked_build

    path = ws.out(name) / "layers.svg"
    if not path.exists():
        return None
    if shown := (item.get("pin") or {}).get("digests"):
        try:
            _, stored = checked_build(ws, name)
        except LiveryError:
            return None
        if any(stored["digests"].get(k) != shown.get(k) for k in stamps.SHOWN):
            return None
    return path.read_text(encoding="utf-8")


def _where(ws: Workspace, name: str, item: dict, view: str, picture: Path, box: list[float]) -> dict:
    """The template pixels under a pin on a design's picture, as their bounding box, and the elements
    painted there; {} when the pin resolves to none."""
    try:
        kit = kits.load(ws, kit_name(script(ws, name)))
    except LiveryError:
        return {}
    if view in (item.get("views") or {}):
        texels = _shot_texels(kit, view, picture, box)
    elif view.startswith(FLAT):
        texels = _flat_texels(ws, kit, view.removeprefix(FLAT), box)
    else:
        texels = None
    if texels is None or not len(texels):
        return {}
    out: dict = {"template": [*map(int, texels.min(0)), *map(int, texels.max(0) + 1)]}
    if (svg := _layers(ws, name, item)) is not None:
        out["elements"] = elements(svg, texels, ws.fonts)
    return out


def resolve(ws: Workspace, item: dict, pins: list[dict], folder: Path, rel: str) -> list[dict]:
    """Each pin as the ledger keeps it: view, box, note, the template box and element ids under it (for a
    design's picture that resolves), and `crop`, its crop saved as pin-<n>.png in `folder` (`rel`, relative
    to the state dir)."""
    kind, _, name = str(item.get("id", "")).partition(":")
    pictures = views_of(item)
    out = []
    for n, pin in enumerate(pins, 1):
        entry = {"view": pin["view"], "box": [round(float(v), 4) for v in pin["box"]], "note": pin["note"]}
        picture = ws.root / pictures[pin["view"]] if pin["view"] in pictures else None
        if picture is not None and picture.is_file():
            if kind in ("final", "concept"):
                with contextlib.suppress(
                    Exception
                ):  # a pin that can't be placed still travels, and never stops a Send
                    entry |= _where(ws, name, item, pin["view"], picture, entry["box"])
            folder.mkdir(parents=True, exist_ok=True)
            crop(picture, entry["box"]).save(folder / f"pin-{n}.png")
            entry["crop"] = f"{rel}/pin-{n}.png"
        out.append(entry)
    return out


def for_builder(pin: dict) -> dict:
    """A kept pin with its line for the builder and the crop's path on this machine."""
    from livery import state

    return pin | {"line": line(pin)} | ({"path": str(state.root() / pin["crop"])} if pin.get("crop") else {})


def line(pin: dict) -> str:
    """A pin as the builder reads it: on <view>, over <element ids>: <note>. One that resolved to no
    element says where on the picture its box is."""
    if pin.get("elements"):
        where = f"over {', '.join(pin['elements'])}"
    else:
        x0, y0, x1, y1 = (round(100 * v) for v in pin["box"])
        where = f"in the box {x0}-{x1}% across, {y0}-{y1}% down"
    return f"on {pin['view']}, {where}: {pin['note'].strip() or '(see the note)'}"
