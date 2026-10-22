"""The bake's renderer (docs/architecture.md, Previews): a build's paint and spec at a capture's views. A bake
check names this module's code (`livery.bake.renderer_hash`), so changing it asks for a new check, and
nothing else lives here.

Each car pixel of a view samples the paint and spec at its texel from mips, filtered along its footprint
as a GPU's anisotropic filter does (or with `aniso` off, one sample at the mip of its long axis). Mips are
box-filtered and sampled in the paint's own sRGB values, as the sim filters its textures (docs/sim.md, The
offline bake). Each material the spec puts there is looked up in that material's grey ladder at that
pixel, interpolated between ladder steps in a curve fitted to the gloss ladder, in which the sim's output
is nearly linear in the paint. Materials are mixed in linear light, over the view's background frame.
"""

import io
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from livery import calibrate, graycode, state
from livery.model import Material

GAMMA = 2.2  # materials mix in linear light
ANISO_MAX, ANISO_TAPS = 16, 8
ANISO = True  # the default filter; docs/sim.md (The offline bake) says why
CURVE_FILE = "bake.json"  # beside a capture's manifest: the fitted curve, once per capture
CURVE_PX = 6000  # gloss ladder pixels the curve is fitted on, spread over every view
CROP_MARGIN = 12


# The curve: out^g is nearly linear in albedo^k between ladder steps.


def fit_curve(white: np.ndarray, black: np.ndarray, steps: dict[int, np.ndarray]) -> tuple[float, float]:
    """(g, k) for which out^g = black^g + (white^g - black^g) (grey/255)^k fits the inner ladder steps best,
    all (n, 3) in 0..1: a coarse grid, then a fine one around its best."""

    def best(gs: np.ndarray, ks: np.ndarray) -> tuple[float, float]:
        found = (np.inf, 1.0, 1.0)
        for g in gs:
            wg, bg = np.power(white, g), np.power(black, g)
            for k in ks:
                e = 0.0
                for lv, s in steps.items():
                    pred = np.power(np.clip(bg + (wg - bg) * (lv / 255) ** k, 0, 1), 1 / g)
                    e += float(np.abs(pred - s).mean())
                if e < found[0]:
                    found = (e, float(g), float(k))
        return found[1], found[2]

    g, k = best(np.arange(0.3, 3.01, 0.1), np.arange(0.3, 3.01, 0.1))
    fine = np.arange(-0.1, 0.101, 0.02)
    g, k = best(np.clip(g + fine, 0.3, 3.0), np.clip(k + fine, 0.3, 3.0))
    return round(g, 3), round(k, 3)


def curve(run: Path, views: list[str]) -> tuple[float, float]:
    """The capture's curve, fitted on its gloss ladder once and kept beside its manifest."""
    cached = state.read_json(run / CURVE_FILE)
    if cached and "g" in cached and "k" in cached:
        return float(cached["g"]), float(cached["k"])
    picked: list[np.ndarray] = []
    levels: list[int] = list(calibrate.LEVELS)
    for view in views:
        with np.load(run / view / "ladder.npz") as z:
            levels = z["levels"].tolist()
            gloss = z[str(Material.gloss)]
        stride = max(1, gloss.shape[1] * len(views) // CURVE_PX)
        picked.append(gloss[:, ::stride].astype(np.float32) / 255)
    lad = np.concatenate(picked, axis=1)
    lo, hi = levels.index(min(levels)), levels.index(max(levels))
    inner = {lv: lad[i] for i, lv in enumerate(levels) if i not in (lo, hi)}
    g, k = fit_curve(lad[hi], lad[lo], inner)
    state.write_json(run / CURVE_FILE, {"g": g, "k": k, "px": int(lad.shape[1])})
    return g, k


# Mips.


class Pyramid:
    """Box-filtered mips of a float texture, sampled trilinearly in level-0 texel coordinates (texel i spans
    [i, i + 1), as `graycode.ViewMap.uv`)."""

    def __init__(self, tex: np.ndarray) -> None:
        self.levels = [tex]
        while max(self.levels[-1].shape[:2]) > 1:
            t = self.levels[-1]
            if t.shape[0] % 2 or t.shape[1] % 2:
                t = np.pad(t, ((0, t.shape[0] % 2), (0, t.shape[1] % 2), (0, 0)), mode="edge")
            self.levels.append((t[0::2, 0::2] + t[1::2, 0::2] + t[0::2, 1::2] + t[1::2, 1::2]) / 4)

    def bilinear(self, m: int, u: np.ndarray, v: np.ndarray) -> np.ndarray:
        t = self.levels[m]
        h, w = t.shape[:2]
        x, y = u / 2**m - 0.5, v / 2**m - 0.5
        x0, y0 = np.floor(x), np.floor(y)
        fx, fy = (x - x0)[:, None], (y - y0)[:, None]
        x0, y0 = x0.astype(np.int32), y0.astype(np.int32)
        xa, xb = np.clip(x0, 0, w - 1), np.clip(x0 + 1, 0, w - 1)
        ya, yb = np.clip(y0, 0, h - 1), np.clip(y0 + 1, 0, h - 1)
        top = t[ya, xa] * (1 - fx) + t[ya, xb] * fx
        bot = t[yb, xa] * (1 - fx) + t[yb, xb] * fx
        return top * (1 - fy) + bot * fy

    def sample(self, u: np.ndarray, v: np.ndarray, lod: np.ndarray) -> np.ndarray:
        top = len(self.levels) - 1
        lod = np.clip(lod, 0, top)
        m0 = np.floor(lod).astype(np.int32)
        f = (lod - m0)[:, None]
        out = np.empty((len(u), self.levels[0].shape[2]), np.float32)
        for m in np.unique(m0):
            i = m0 == m
            a = self.bilinear(int(m), u[i], v[i])
            b = self.bilinear(min(int(m) + 1, top), u[i], v[i])
            out[i] = a * (1 - f[i]) + b * f[i]
        return out


def materials(spec: np.ndarray, presets: dict[str, tuple[int, ...]]) -> np.ndarray:
    """Per texel, the index of the preset nearest its spec colour."""
    pres = np.array(list(presets.values()), np.int32)
    s = spec.astype(np.int32)
    dist = np.stack([np.abs(s - p).sum(axis=2) for p in pres])
    return dist.argmin(axis=0)


def pyramids(paint: np.ndarray, spec: np.ndarray, presets: dict[str, tuple[int, ...]]) -> dict:
    """Per material the spec uses: mips of (coverage, coverage x albedo), the albedo in sRGB."""
    idx = materials(spec, presets)
    srgb = paint.astype(np.float32) / 255
    out = {}
    for i, m in enumerate(presets):
        w = (idx == i).astype(np.float32)
        if w.any():
            out[m] = Pyramid(np.concatenate([w[..., None], srgb * w[..., None]], axis=2))
    return out


# Views.


def footprint(jac: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per pixel: its footprint's major and minor axis in texels, and the major axis as a texel vector. A
    pixel without a plane fit takes the view's median."""
    has = np.isfinite(jac).all(axis=(1, 2))
    sv = np.full((len(jac), 2), np.nan, np.float32)
    dirs = np.zeros((len(jac), 2), np.float32)
    if has.any():
        uu, ss, _ = np.linalg.svd(jac[has])
        sv[has] = ss
        dirs[has] = uu[:, :, 0] * ss[:, :1]
        sv[~has] = np.nanmedian(sv[has], axis=0)
    else:
        sv[:] = 1.0
    return np.maximum(sv[:, 0], 1e-3), np.maximum(sv[:, 1], 1e-3), dirs


@dataclass
class View:
    """One view of the capture: which pixels show paint, where they sample, and each material's ladder
    there; the per-pixel arrays list `pix` in row-major order."""

    name: str
    car: np.ndarray  # (h, w) bool
    pix: np.ndarray  # (h, w) bool
    uv: np.ndarray  # (n, 2)
    major: np.ndarray
    minor: np.ndarray
    dirs: np.ndarray
    background: np.ndarray  # (h, w, 3) uint8
    levels: np.ndarray  # (L,) the ladder's greys
    ladder: dict[str, np.ndarray]  # material -> (L, n, 3) uint8
    fill: tuple[np.ndarray, np.ndarray]  # car pixels without a uv (flat index) and the pixel they copy (0..n)

    @classmethod
    def load(cls, run: Path, name: str, keys: dict[str, str]) -> "View":
        """`keys`: material -> where ladder.npz keeps its ladder under the kit's preset (`ladder_key`)."""
        vm = graycode.ViewMap.load(run / name / "geometry.npz")
        major, minor, dirs = footprint(vm.jac)
        with np.load(run / name / "ladder.npz") as z:
            levels = z["levels"].astype(np.float32)
            ladder = {m: z[k] for m, k in keys.items() if k in z.files}
        order = np.argsort(levels)
        ladder = {k: v[order] for k, v in ladder.items()}
        background = np.asarray(Image.open(run / name / "background.png").convert("RGB"))
        holes = fill(vm.car, vm.pix)
        return cls(name, vm.car, vm.pix, vm.uv, major, minor, dirs, background, levels[order], ladder, holes)

    @property
    def box(self) -> tuple[int, int, int, int]:
        """The car's bounding box with a margin: x0, y0, x1, y1."""
        ys, xs = np.nonzero(self.car)
        h, w = self.car.shape
        if not len(ys):
            return 0, 0, w, h
        m = CROP_MARGIN
        x0, y0, x1, y1 = (int(c) for c in (xs.min() - m, ys.min() - m, xs.max() + m + 1, ys.max() + m + 1))
        return max(x0, 0), max(y0, 0), min(x1, w), min(y1, h)

    def texels(self, canvas: int) -> np.ndarray:
        """The level-0 texel each decoded pixel's uv falls in, (n, 2) as x, y."""
        return np.clip(np.floor(self.uv).astype(np.int32), 0, canvas - 1)


def fill(car: np.ndarray, pix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Car pixels the geometry didn't decode copy the nearest one it did."""
    holes = car & ~pix
    if not holes.any() or not pix.any():
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    iy, ix = np.asarray(ndimage.distance_transform_edt(~pix, return_distances=False, return_indices=True))
    order = np.full(pix.shape, -1, np.int64)
    order[pix] = np.arange(int(pix.sum()))
    dst = np.flatnonzero(holes)
    return dst, order[iy.ravel()[dst], ix.ravel()[dst]]


def lookup(view: View, m: str, idx: np.ndarray, albedo: np.ndarray, g: float, k: float) -> np.ndarray:
    """What material m shows at pixels idx for an albedo (m, 3) in 0..1, as linear light: the ladder
    interpolated in the curve between its two steps either side."""
    lut = view.ladder[m]
    x = np.power(view.levels / 255, k)
    ak = np.power(albedo, k)
    i = np.clip(np.searchsorted(x, ak, side="right") - 1, 0, len(x) - 2)
    t = np.clip((ak - x[i]) / (x[i + 1] - x[i]), 0, 1)
    enc = np.power(np.arange(256, dtype=np.float32) / 255, g)
    c = np.arange(3)[None, :]
    lo, hi = enc[lut[i, idx[:, None], c]], enc[lut[i + 1, idx[:, None], c]]
    y = np.clip(lo * (1 - t) + hi * t, 0, 1)
    return np.power(y, GAMMA / g)


def filtered(view: View, pyr: Pyramid, aniso: bool) -> np.ndarray:
    """Each pixel's sample of a pyramid. Isotropic: one trilinear sample at the mip of the footprint's long
    axis. Anisotropic, as a GPU filters: at the mip of the short axis (no finer than the long one over
    ANISO_MAX), averaged over as many taps along the long axis as it is times longer, up to ANISO_TAPS."""
    u, v = view.uv[:, 0], view.uv[:, 1]
    if not aniso:
        return pyr.sample(u, v, np.log2(view.major))
    eff = np.maximum(view.minor, view.major / ANISO_MAX)
    lod = np.log2(eff)
    span = np.clip(1 - eff / view.major, 0, 1)[:, None] * view.dirs
    taps = np.clip(np.rint(view.major / eff), 1, ANISO_TAPS).astype(np.int32)
    acc = np.zeros((len(u), pyr.levels[0].shape[2]), np.float32)
    for k in np.unique(taps):
        idx = np.flatnonzero(taps == k)
        for i in range(k):
            t = (i + 0.5) / k - 0.5
            acc[idx] += pyr.sample(u[idx] + t * span[idx, 0], v[idx] + t * span[idx, 1], lod[idx])
        acc[idx] /= k
    return acc


def render_view(view: View, pyrs: dict, curve_gk: tuple[float, float], aniso: bool = ANISO) -> np.ndarray:
    """One view's frame, uint8. A material the view has no ladder for raises KeyError naming it."""
    out = np.zeros((len(view.uv), 3), np.float32)
    for m, pyr in pyrs.items():
        if m not in view.ladder:
            raise KeyError(m)
        acc = filtered(view, pyr, aniso)
        w = acc[:, 0]
        idx = np.flatnonzero(w > 1e-6)
        albedo = np.clip(acc[idx, 1:] / w[idx, None], 0, 1)
        out[idx] += w[idx, None] * lookup(view, m, idx, albedo, *curve_gk)
    shown = np.round(np.power(np.clip(out, 0, 1), 1 / GAMMA) * 255).astype(np.uint8)
    frame = view.background.copy()
    frame[view.pix] = shown
    dst, src = view.fill
    frame.reshape(-1, 3)[dst] = shown[src]
    return frame


def rgb(data: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))
