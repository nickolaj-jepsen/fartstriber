"""What the preview cameras see of the paint, per view and per texel, from the kit's calibration capture.

`kit capture` records which texel every car pixel of each preview view samples, and the pixel's footprint
(`livery.graycode.ViewMap`). Turned round, per cell of the texture (`scale` texels a side) and per view:

- `major`, `minor`: texels per screen pixel along the footprint's major and minor axes, the least of any
  pixel that samples the cell (each pixel counts for every cell its footprint covers); inf where no pixel
  of the view does: hidden, facing away, or under the sim's own hardware.
- `crease`: where a texel's neighbour `PROBE` texels on along the texture lands on screen, against the
  line through the texel and its neighbour the other way: the jump off that line as a share of the longer
  step. Flat or gently curved paint scores near 0; a fold the view sees across scores near 1. NaN where
  the three aren't seen on one island, or are seen too foreshortened to tell.

`cells` and `pixels` give each car pixel's cell, so a mask of cells can be drawn as a view shows it.
The tables are cached per capture in the machine state dir, beside the captures (`livery.calibrate`).
"""

import hashlib
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from scipy import ndimage

from livery import graycode, state
from livery.kit import CLOSE_VIEWS, Kit

PROBE = 8  # texels between the three points of the crease measure: a stroke's width, a letter's stem
MIN_STEP = 2.0  # screen px: a probe step shorter than this is too foreshortened to show a crease
MAX_TAPS = 8  # samples per pixel along each screen axis, so a footprint up to 8 cells across leaves no gaps
CODE = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()  # a change here rebuilds the cached tables
TMP = ".tmp.npz"


@dataclass(frozen=True)
class Visibility:
    capture: str  # the capture's id: its run folder's name
    views: tuple[str, ...]
    scale: int  # texels per cell side
    major: np.ndarray  # (views, h, w) float32, inf where unseen
    minor: np.ndarray  # (views, h, w) float32, inf where unseen
    crease: np.ndarray  # (views, h, w) float32, NaN where not measured
    cells: tuple[np.ndarray, ...]  # per view, (n,) int32: the cell each car pixel samples, row-major
    pixels: tuple[np.ndarray, ...]  # per view, (n, 2) int16: each car pixel's (y, x) on screen

    def seen(self) -> np.ndarray:
        """(views, h, w) bool: some pixel of the view samples the cell."""
        return np.isfinite(self.major)

    def well_seen(self, max_texels_per_px: float) -> np.ndarray:
        """(views, h, w) bool: seen no more foreshortened than `max_texels_per_px` along the major axis."""
        return self.major <= max_texels_per_px

    def on_screen(self, view: int, mask: np.ndarray) -> tuple[np.ndarray, tuple[int, int]] | None:
        """A cell mask as the view shows it: the screen crop holding its pixels and the crop's top-left
        (y, x), or None if no pixel of the view shows it."""
        hit = mask.ravel()[self.cells[view]]
        if not hit.any():
            return None
        yx = self.pixels[view][hit].astype(np.int32)
        y0, x0 = yx.min(axis=0)
        y1, x1 = yx.max(axis=0)
        img = np.zeros((y1 - y0 + 1, x1 - x0 + 1), bool)
        img[yx[:, 0] - y0, yx[:, 1] - x0] = True
        return img, (int(y0), int(x0))

    def widths(self, mask: np.ndarray, upto: float) -> dict[str, float]:
        """How many screen pixels across a cell mask is in each view that shows it (`width`)."""
        out = {}
        for i, v in enumerate(self.views):
            shown = self.on_screen(i, mask)
            if shown is not None:
                out[v] = width(shown[0], upto)
        return out

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        arrays: dict[str, Any] = {"capture": np.array(self.capture), "views": np.array(self.views)}
        arrays |= {"scale": np.array(self.scale), "major": self.major, "minor": self.minor}
        arrays["crease"] = self.crease
        for i, v in enumerate(self.views):
            arrays[f"cells_{v}"], arrays[f"pixels_{v}"] = self.cells[i], self.pixels[i]
        # Written whole or not at all: other worktrees may read it at the same time.
        tmp = path.with_name(f"{path.stem}.{os.getpid()}{TMP}")
        np.savez_compressed(tmp, **arrays)
        try:
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)

    @classmethod
    def load(cls, path: Path) -> "Visibility":
        with np.load(path) as z:
            views = tuple(str(v) for v in z["views"])
            return cls(
                capture=str(z["capture"]),
                views=views,
                scale=int(z["scale"]),
                major=z["major"],
                minor=z["minor"],
                crease=z["crease"],
                cells=tuple(z[f"cells_{v}"] for v in views),
                pixels=tuple(z[f"pixels_{v}"] for v in views),
            )


def width(mask: np.ndarray, upto: float) -> float:
    """A mask's typical width in pixels, to half a pixel, told apart up to `upto` (anything wider comes
    out at least that).

    Each pixel's width is that of the widest line through it: twice the largest distance to the outside
    near it, less the half pixel at each side between a pixel's centre and its edge; the median over its
    pixels weighs a field by its area, not by the slivers it leaves between letters."""
    r = int(np.ceil(upto / 2)) + 1
    dt = np.asarray(ndimage.distance_transform_edt(np.pad(mask, r)))
    near = ndimage.maximum_filter(dt, size=2 * r + 1)[dt > 0]
    return float(2 * np.median(near) - 0.5) if len(near) else 0.0


def capture_dir(kit_name: str) -> Path | None:
    """The kit's current calibration capture, as `livery.calibrate.current` finds it. Read here rather than
    imported, so the lint doesn't pull the sim's modules into a build's import closure."""
    root = state.root() / "kit-capture" / kit_name
    pointer = root / "current"
    run = root / pointer.read_text(encoding="utf-8").strip() if pointer.exists() else None
    return run if run is not None and run.is_dir() else None


def capture_views(run: Path) -> list[str]:
    """The views a capture decoded, in the order its manifest lists them, but the close-ups: they would count
    detail as seen well that every whole-car view blurs."""
    ident = (state.read_json(run / "manifest.json") or {}).get("identity", {})
    views = list(ident.get("views", {})) or sorted(p.parent.name for p in run.glob("*/geometry.npz"))
    return [v for v in views if (run / v / "geometry.npz").exists() and v not in CLOSE_VIEWS]


def islands(kit: Kit, scale: int) -> np.ndarray:
    """The kit's island ids per cell (0 off the islands), sampled as the output lint samples them."""
    path = kit.dir / "overlays" / "islands.png"
    ids = np.asarray(Image.open(path)) if path.exists() else np.ones((kit.canvas, kit.canvas), np.uint16)
    size = kit.canvas // scale
    return np.asarray(Image.fromarray(ids).resize((size, size), Image.Resampling.NEAREST)).astype(np.int32)


def table(kit: Kit, scale: int, shown: Sequence[str] | None = None) -> Visibility | None:
    """The kit's tables from its current capture, cached in the state dir; None if it has no capture. With
    `shown`, only those views count: what previews show, not what only calibrates the kit."""
    run = capture_dir(kit.name)
    if run is None:
        return None
    views = [v for v in capture_views(run) if shown is None or v in shown]
    if not views:
        return None
    ids = islands(kit, scale)
    key = hashlib.sha256(CODE.encode() + ids.tobytes() + repr((scale, views)).encode()).hexdigest()[:16]
    d = state.root() / "visibility" / kit.name
    path = d / f"{run.name}-{key}.npz"
    if path.exists():
        try:
            return Visibility.load(path)
        except (OSError, ValueError, KeyError):
            pass  # a damaged cache is built again
    vis = build(run, views, ids, scale)
    try:
        vis.save(path)
        for old in d.glob("*.npz"):
            if old != path and not old.name.endswith(TMP):
                old.unlink(missing_ok=True)
    except OSError:
        pass  # another lint holds the file: it is writing or reading these same tables
    return vis


def build(run: Path, views: list[str], ids: np.ndarray, scale: int) -> Visibility:
    """The tables from a capture's decoded views, on the cell grid of `ids` (island ids per cell)."""
    shape = ids.shape
    major, minor, crease, cells, pixels = [], [], [], [], []
    for view in views:
        vm = graycode.ViewMap.load(run / view / "geometry.npz")
        ys, xs = np.nonzero(vm.pix)  # the ViewMap lists its pixels in this order
        cx = np.clip(np.floor(vm.uv[:, 0] / scale), 0, shape[1] - 1).astype(np.int32)
        cy = np.clip(np.floor(vm.uv[:, 1] / scale), 0, shape[0] - 1).astype(np.int32)
        cells.append(cy * shape[1] + cx)
        pixels.append(np.stack([ys, xs], axis=1).astype(np.int16))
        mj, mn, sx, sy = splat(vm.uv, vm.jac, xs, ys, scale, shape)
        major.append(mj)
        minor.append(mn)
        crease.append(kink(sx, sy, ids, max(1, round(PROBE / scale))))
    return Visibility(
        capture=run.name,
        views=tuple(views),
        scale=scale,
        major=np.stack(major),
        minor=np.stack(minor),
        crease=np.stack(crease),
        cells=tuple(cells),
        pixels=tuple(pixels),
    )


def splat(
    uv: np.ndarray, jac: np.ndarray, xs: np.ndarray, ys: np.ndarray, scale: int, shape: tuple[int, int]
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Each pixel's footprint spread over the cells it covers: per cell the least major and minor texels
    per pixel (inf where none) and where on screen its centre is drawn (NaN where no pixel covers it).

    Each pixel places the centre of every cell it covers through its own Jacobian, and a cell's place is
    the mean of those, which a mean of the pixels' own centres would quantise to the pixel grid. A pixel
    without a plane fit has no footprint and is left out."""
    fitted = np.isfinite(jac).all(axis=(1, 2))
    uv, jac = uv[fitted].astype(np.float64), jac[fitted].astype(np.float64)
    screen = np.stack([xs[fitted], ys[fitted]], axis=1).astype(np.float64) + 0.5
    sv = np.linalg.svd(jac, compute_uv=False)
    inv = np.linalg.pinv(jac)
    # Texels per screen step along x and y: enough samples along each that none is a cell from the next.
    taps = np.clip(np.ceil(np.linalg.norm(jac, axis=1) / scale), 1, MAX_TAPS).astype(np.int32)
    idx, maj, mnr, px, py = [], [], [], [], []
    for kx, ky in {(int(a), int(b)) for a, b in taps}:
        sel = (taps[:, 0] == kx) & (taps[:, 1] == ky)
        ox, oy = np.meshgrid((np.arange(kx) + 0.5) / kx - 0.5, (np.arange(ky) + 0.5) / ky - 0.5)
        off = np.stack([ox.ravel(), oy.ravel()], axis=1)  # (taps, 2) in screen px
        at = uv[sel][:, None, :] + np.einsum("nij,tj->nti", jac[sel], off)
        c = np.floor(at / scale).astype(np.int64)
        inside = (c >= 0).all(axis=2) & (c[..., 0] < shape[1]) & (c[..., 1] < shape[0])
        idx.append((c[..., 1] * shape[1] + c[..., 0])[inside])
        n = len(off)
        maj.append(np.repeat(sv[sel, 0], n).reshape(-1, n)[inside])
        mnr.append(np.repeat(sv[sel, 1], n).reshape(-1, n)[inside])
        centre = (c + 0.5) * scale - uv[sel][:, None, :]  # from the pixel's texel to the cell's centre
        s = screen[sel][:, None, :] + np.einsum("nij,ntj->nti", inv[sel], centre)
        px.append(s[..., 0][inside])
        py.append(s[..., 1][inside])
    flat = int(np.prod(shape))
    out_major = np.full(flat, np.inf, np.float32)
    out_minor = np.full(flat, np.inf, np.float32)
    sx = np.full(flat, np.nan, np.float32)
    sy = np.full(flat, np.nan, np.float32)
    if idx:
        i, mj, mn = np.concatenate(idx), np.concatenate(maj), np.concatenate(mnr)
        order = np.lexsort((mj, i))
        first = np.unique(i[order], return_index=True)[1]
        out_major[i[order][first]] = mj[order][first]
        out_minor[i[order][first]] = mn[order][first]
        count = np.bincount(i, minlength=flat)
        hit = count > 0
        sx[hit] = (np.bincount(i, np.concatenate(px), flat)[hit] / count[hit]).astype(np.float32)
        sy[hit] = (np.bincount(i, np.concatenate(py), flat)[hit] / count[hit]).astype(np.float32)
    return (out_major.reshape(shape), out_minor.reshape(shape), sx.reshape(shape), sy.reshape(shape))


def _shift(a: np.ndarray, k: int, axis: int, fill: float) -> np.ndarray:
    """b[c] = a[c + k] along `axis`, `fill` past the edge."""
    out = np.full_like(a, fill)
    n = a.shape[axis]
    src = [slice(None)] * a.ndim
    dst = [slice(None)] * a.ndim
    src[axis] = slice(max(k, 0), n + min(k, 0))
    dst[axis] = slice(max(-k, 0), n - max(k, 0))
    out[tuple(dst)] = a[tuple(src)]
    return out


def kink(sx: np.ndarray, sy: np.ndarray, ids: np.ndarray, d: int) -> np.ndarray:
    """Per cell, the crease measure along the texture's x and y, whichever is larger (see the module)."""
    out = np.full(sx.shape, np.nan, np.float32)
    for axis in (0, 1):
        s = np.stack([sx, sy])
        prev = np.stack([_shift(sx, -d, axis, np.nan), _shift(sy, -d, axis, np.nan)])
        nxt = np.stack([_shift(sx, d, axis, np.nan), _shift(sy, d, axis, np.nan)])
        same = (ids > 0) & (_shift(ids, -d, axis, 0) == ids) & (_shift(ids, d, axis, 0) == ids)
        a, b = s - prev, nxt - s
        step = np.maximum(np.hypot(*a), np.hypot(*b))
        with np.errstate(invalid="ignore", divide="ignore"):
            k = np.hypot(*(b - a)) / step
        k[~same | ~(step >= MIN_STEP)] = np.nan
        out = np.fmax(out, k.astype(np.float32))
    return out
