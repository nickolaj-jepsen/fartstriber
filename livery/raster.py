"""SVG -> pixels -> island bleed -> compensation for the finishes the sim darkens -> 24-bit TGA."""

import io
from functools import lru_cache
from pathlib import Path

import numpy as np
import resvg_py
from PIL import Image
from scipy import ndimage

from livery.compile import compile_svg, spec_svg
from livery.config import Workspace
from livery.kit import Kit
from livery.model import Livery


def render(svg: str, ws: Workspace) -> np.ndarray:
    """RGB uint8 array. Text comes only from the repo's fonts, so renders don't depend on the machine."""
    png = resvg_py.svg_to_bytes(svg_string=svg, skip_system_fonts=True, font_dirs=[str(ws.fonts)])
    return np.asarray(Image.open(io.BytesIO(bytes(png))).convert("RGB"))


BLEED_INSET_PX = 2  # an island's outermost pixels are antialiased against the base; source colour inside them


@lru_cache(maxsize=4)
def _bleed_map(mask_path: Path, mtime_ns: int, px: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For each pixel up to `px` outside the islands, and on each island's rim, the nearest interior pixel.

    Shapes are cut at island edges, so the rim is a blend of the shape and the base; copying it outward
    shows as a light hairline along seams in the sim. The rim is repainted from the interior instead.
    """
    paintable = np.asarray(Image.open(mask_path).convert("L")) > 127
    interior = ndimage.binary_erosion(paintable, iterations=BLEED_INSET_PX, border_value=1)
    dist, (iy, ix) = ndimage.distance_transform_edt(~interior, return_indices=True)
    ring = ~interior & (dist <= px + BLEED_INSET_PX)
    return ring, iy[ring], ix[ring]


def bleed(img: np.ndarray, mask_path: Path | None, px: int) -> np.ndarray:
    """Copy each island's edge colours `px` pixels outward, so mipmaps never blend in the background."""
    if mask_path is None or not mask_path.exists() or px <= 0:
        return img
    ring, iy, ix = _bleed_map(mask_path, mask_path.stat().st_mtime_ns, px)
    out = img.copy()
    out[ring] = img[iy, ix]
    return out


def _to_linear(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


_LINEAR = _to_linear(np.arange(256) / 255.0)


def _to_srgb(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def compensate(paint: np.ndarray, spec: np.ndarray, kit: Kit) -> np.ndarray:
    """Brighten paint under a finish the sim darkens (a preset with metal in it) by the kit's measured
    lookup for that preset, so it shows what gloss shows for the paint asked for, as far as the finish can.
    Each pixel takes the lookup of the preset nearest its spec's roughness and clearcoat, mixed in linear
    light by its metal channel, so antialiased edges blend."""
    under = spec[:, :, 0] > 0
    presets = [(m, kit.spec[m], lut) for m, lut in kit.compensation.items() if m in kit.spec]
    if not presets or not under.any():
        return paint
    px, sp = paint[under], spec[under].astype(np.float64)
    near = np.stack([np.abs(sp[:, 1:] - np.asarray(s[1:], np.float64)).sum(1) for _, s, _ in presets], 1)
    pick = near.argmin(1)
    looked = px.astype(np.float64)
    for k, (_, _, lut) in enumerate(presets):
        sel = pick == k
        target, wanted = np.asarray(lut["target"], np.float64), np.asarray(lut["paint"], np.float64)
        for c in range(3):
            looked[sel, c] = np.interp(px[sel, c], target, wanted[:, c])
    weight = sp[:, :1] / 255.0
    lin = _LINEAR[px] * (1 - weight) + _LINEAR[np.clip(np.rint(looked), 0, 255).astype(np.uint8)] * weight
    out = paint.copy()
    out[under] = np.rint(_to_srgb(lin) * 255).astype(np.uint8)
    return out


def png_bytes(img: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="PNG")
    return buf.getvalue()


def tga_bytes(img: np.ndarray) -> bytes:
    """Uncompressed 24-bit TGA: 32-bit with alpha can render all black in the sim."""
    buf = io.BytesIO()
    Image.fromarray(img).convert("RGB").save(buf, format="TGA", compression=None)
    return buf.getvalue()


def rasterise(livery: Livery, kit: Kit, ws: Workspace) -> dict[str, bytes]:
    """Every build output, by file name."""
    paint_svg = compile_svg(livery, kit)
    spec_doc = spec_svg(paint_svg, kit)
    spec = bleed(render(spec_doc, ws), kit.mask_path, kit.bleed_px)
    paint = bleed(render(paint_svg, ws), kit.mask_path, kit.bleed_px)
    paint = compensate(paint, spec, kit)
    return {
        "layers.svg": paint_svg.encode(),
        "spec.svg": spec_doc.encode(),
        "paint.png": png_bytes(paint),
        "spec.png": png_bytes(spec),
        "car.tga": tga_bytes(paint),
        "car_spec.tga": tga_bytes(spec),
    }
