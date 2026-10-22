"""The car as iRacing's UI previews it: one front three-quarter view of the right side, from the preview
bake the UI ships for every car (docs/sim.md). A layout check, not a judge: no spec map, number or sim light.

The layers are read from the local install and cached under `.cache/ui-preview/`, never committed.
"""

import io
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import zstandard
from PIL import Image
from scipy import ndimage

from livery.config import Workspace

FILE_HEADER = 50  # version u16 + 48 bytes
RECORD_PREFIX = 32  # per-record hash
BLOCK = 0x20000

# Roles of the frames in a size set. Their count varies with the car's stock liveries, so the fixed layers
# are counted from the end. Before them come stock liveries and pattern masks: body pixels change between
# those, unpainted parts (wheels, glass, interior) don't.
BASE = 0  # flat red pattern, unpainted parts rendered in
FIXED = 10  # lighting, masks and shadow layers at the end
SHADE = -4  # grey car under the preview light
ALPHA = -1  # silhouette with soft ground shadow
# Unpainted parts vary by under 5 levels between the patterns. Edge pixels down to a few percent body
# count as body, or the red pattern shows through as a fringe.
BODY_RANGE = 12

BACKGROUND = (58, 60, 64)


@dataclass(frozen=True)
class Layers:
    base: np.ndarray  # (h, w, 3) uint8
    shade: np.ndarray  # (h, w) uint8, luminance
    alpha: np.ndarray  # (h, w) uint8
    body: np.ndarray  # (h, w) bool: where the paint shows
    uv: np.ndarray  # (2h, 2w, 4) uint8: u is G·256+B, v is A·256+R, in 65536ths, top-left origin; 0 off it


def _read_stream(dat: Path) -> tuple[bytes, list[int]]:
    b = dat.read_bytes()
    pos, parts = FILE_HEADER, []
    d = zstandard.ZstdDecompressor()
    while pos + RECORD_PREFIX + 16 <= len(b):
        _, block, zero, n = struct.unpack_from("<IIII", b, pos + RECORD_PREFIX)
        if block != BLOCK or zero:
            break
        start = pos + RECORD_PREFIX + 16
        parts.append(d.decompressobj().decompress(b[start : start + n]))
        pos = start + n
    return b"".join(parts), [len(p) for p in parts]


def _tgas(stream: bytes, sizes: list[int]) -> list[Image.Image]:
    """The stream is a run of TGAs and WebUVDescriptor blobs; a blob runs to the next TGA's record."""
    starts = np.cumsum([0, *sizes[:-1]])

    def is_tga(o: int) -> bool:
        return stream[o + 1] == 0 and stream[o + 2] in (2, 10) and stream[o + 16] in (24, 32)

    out, o = [], 0
    while o < len(stream):
        if is_tga(o):
            w, h, bpp = struct.unpack_from("<HHB", stream, o + 12)
            n = 18 + w * h * bpp // 8
            if stream[o + n + 8 : o + n + 26] == b"TRUEVISION-XFILE.\x00":
                n += 26
            out.append(Image.open(io.BytesIO(stream[o : o + n])))
        else:
            n = next((int(s) for s in starts if s > o and is_tga(int(s))), len(stream)) - o
        o += n
    return out


def extract(dat: Path) -> Layers:
    """Size sets are frames, a 32-bit UV map at twice their size, and a descriptor; the largest is last."""
    tgas = _tgas(*_read_stream(dat))
    uv_maps = [i for i, im in enumerate(tgas) if im.mode == "RGBA"]
    if len(uv_maps) < 2:
        raise ValueError(f"{dat}: no size sets found; the preview format has changed")
    frames, uv = tgas[uv_maps[-2] + 1 : uv_maps[-1]], tgas[uv_maps[-1]]
    size = frames[0].size
    if (
        len(frames) <= FIXED + 1
        or any(f.size != size for f in frames)
        or uv.size != (2 * size[0], 2 * size[1])
    ):
        raise ValueError(
            f"{dat}: layout differs from the cars it was checked on; look before trusting the roles"
        )
    rgb = [np.asarray(f.convert("RGB")) for f in frames]
    pats = np.stack(rgb[:-FIXED]).astype(np.int16)
    return Layers(
        base=rgb[BASE],
        shade=np.asarray(frames[SHADE].convert("L")),
        alpha=np.asarray(frames[ALPHA].convert("L")),
        body=(pats.max(0) - pats.min(0)).max(-1) > BODY_RANGE,
        uv=np.asarray(uv),
    )


def load(ws: Workspace, car: str) -> Layers | None:
    """The car's layers, extracted once per install version. None when the install has no preview for it."""
    dat = ws.ui_cars / car / f"{car}.dat"
    if not dat.exists():
        return None
    st = dat.stat()
    stamp = np.array([st.st_size, st.st_mtime_ns], np.int64)
    cache = ws.root / ".cache" / "ui-preview" / f"{car}.npz"
    if cache.exists():
        with np.load(cache) as z:
            if np.array_equal(z["stamp"], stamp):
                return Layers(**{k: z[k] for k in Layers.__dataclass_fields__})
    layers = extract(dat)
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache, stamp=stamp, **layers.__dict__)
    return layers


def _uv_px(layers: Layers, tw: int, th: int) -> tuple[np.ndarray, np.ndarray]:
    """The texture pixel (x, y) each UV sub-pixel samples, (2h, 2w) each."""
    uvm = layers.uv.astype(np.int64)
    has = uvm.any(-1)
    # Body edges the UV map misses take the nearest mapped sub-pixel, not the red base.
    iy, ix = ndimage.distance_transform_edt(~has, return_distances=False, return_indices=True)
    uvm = uvm[iy, ix]
    x = np.clip((((uvm[..., 1] << 8) | uvm[..., 2]) * tw) >> 16, 0, tw - 1)
    y = np.clip((((uvm[..., 3] << 8) | uvm[..., 0]) * th) >> 16, 0, th - 1)
    return x, y


def texels(layers: Layers, canvas: int) -> np.ndarray:
    """Per pixel of `render`, the template pixel it shows as (x, y), from its first UV sub-pixel; -1 off the
    body."""
    x, y = _uv_px(layers, canvas, canvas)
    out = np.stack([x[::2, ::2], y[::2, ::2]], -1).astype(np.int32)
    out[~layers.body] = -1
    return out


def render(paint: np.ndarray, layers: Layers, background=BACKGROUND) -> Image.Image:
    """The paint (top-left origin RGB) on the car under the preview light, over a flat background."""
    th, tw = paint.shape[:2]
    x, y = _uv_px(layers, tw, th)
    lin = (paint.astype(np.float32) / 255) ** 2.2
    h, w = layers.body.shape
    # Four UV sub-pixels per frame pixel: average in linear light.
    col = lin[y, x].reshape(h, 2, w, 2, 3).mean((1, 3))

    body = layers.body
    shade = layers.shade.astype(np.float32) / 255
    ref = np.percentile(shade[body], 97) if body.any() else 1.0
    diffuse = np.clip(shade / ref, 0, 1) * 0.85 + 0.15
    spec = np.clip((shade - ref * 0.8) / (ref * 0.2), 0, 1)[..., None] * 0.35
    car = np.clip((col * diffuse[..., None]) ** (1 / 2.2) + spec, 0, 1)
    car = np.where(body[..., None], car, layers.base.astype(np.float32) / 255)

    a = layers.alpha.astype(np.float32)[..., None] / 255
    img = car * a + np.array(background, np.float32) / 255 * (1 - a)
    return Image.fromarray(np.rint(img * 255).astype(np.uint8))
