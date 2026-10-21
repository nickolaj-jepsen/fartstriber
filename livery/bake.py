"""The sim-lit preview without the sim (docs/plan.md, decision 19): a build's paint and spec rendered at the
kit's preview views from its calibration capture (`livery.calibrate`).

Each car pixel of a view samples the paint and spec at its texel from mips, filtered along its footprint
as a GPU's anisotropic filter does (or with `aniso` off, one sample at the mip of its long axis). Each
material the spec puts there is looked up in that material's grey ladder at that pixel, interpolated
between ladder steps in a curve fitted to the gloss ladder, in which the sim's output is nearly linear in
the paint. The materials and mip samples are mixed in linear light, over the view's background frame.

A capture is used only while what its shots depend on still holds (`stale`), and a builder or critic uses
it only once `bake check` has held a render against one real preview sim run of the same build.
"""

import hashlib
import io
import json
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from livery import calibrate, graycode, state
from livery import kit as kits
from livery.config import Config, Workspace
from livery.errors import LiveryError
from livery.kit import Kit
from livery.model import Material

GAMMA = 2.2  # texture filtering and material mixing happen in linear light
ANISO_MAX, ANISO_TAPS = 16, 8
ANISO = True  # the default filter; docs/sim.md (The offline bake) says why
CURVE_FILE = "bake.json"  # beside a capture's manifest: the fitted curve, once per capture
CURVE_PX = 6000  # gloss ladder pixels the curve is fitted on, spread over every view
SIL_PX = 2.0  # bake check leaves out this band along the silhouette
MAX_P95 = 12.0  # the S5 target: per channel, per view
CROP_MARGIN = 12
# What a bake depends on, in its capture manifest's identity, and how a refusal names each, besides a
# ladder for each of the kit's spec presets. Not the kit's maps: a refit changes where car space lands in the
# texture, not which texel the sim shows where.
WATCHED = {
    "scheme": "the Gray-code scheme",
    "canvas": "the kit's canvas",
    "paint_folder": "the kit's paint folder",
    "frame": "the profile's frame",
    "views": "the profile's preview views",
    "replay": "the preview replay",
    "track_cameras": "the track camera set",
    "renderer": "the sim's graphics options",
    "car_dat": "the car's model (.dat)",
}

Log = Callable[[str], object]


class NoCapture(LiveryError):
    pass


class StaleCapture(LiveryError):
    pass


class Unchecked(LiveryError):
    pass


class BakeCheckFailed(LiveryError):
    pass


# The capture, and whether it still holds.


def stale(cfg: Config, kit: Kit, run: Path) -> list[str]:
    """What moved since the capture was shot, as WATCHED names it; empty while it holds."""
    manifest = state.read_json(run / "manifest.json") or {}
    shot = manifest.get("identity", {})
    now = calibrate.identity(cfg, kit, kits.profile(kit))
    moved = [what for key, what in WATCHED.items() if now.get(key) != shot.get(key)]
    unshot = [
        f"{m} at {list(s)}" for m, s in kit.spec.items() if calibrate.ladder_key(manifest, m, s) is None
    ]
    return moved + ([f"the kit's spec presets ({', '.join(unshot)})"] if unshot else [])


def capture(cfg: Config, ws: Workspace, kit: Kit) -> Path:
    """The kit's current capture, refused if anything it depends on moved since."""
    run = calibrate.current(ws, kit.name)
    if run is None or not (run / "manifest.json").exists():
        raise NoCapture(f"{kit.name} has no calibration capture; run livery kit capture {kit.name}")
    if moved := stale(cfg, kit, run):
        raise StaleCapture(
            f"{kit.name}'s capture {run.name} was shot before a change to {', '.join(moved)}; "
            f"run livery kit capture {kit.name} again"
        )
    return run


def check_of(run: Path) -> dict | None:
    return (state.read_json(run / "manifest.json") or {}).get("check")


def checked(cfg: Config, ws: Workspace, kit: Kit) -> Path:
    """The kit's current capture, once it passed bake check."""
    run = capture(cfg, ws, kit)
    report = check_of(run)
    if not report:
        raise Unchecked(f"{kit.name}'s capture {run.name} hasn't been through livery bake check {kit.name}")
    if not report.get("passed"):
        worst = report.get("worst") or {}
        raise Unchecked(
            f"{kit.name}'s capture {run.name} failed bake check (p95 {worst.get('p95')} at "
            f"{worst.get('design')} {worst.get('view')}); use sim shots until it passes"
        )
    return run


def renderer_hash() -> str:
    """This module's code without comments, so a check names the renderer it held."""
    from livery.hashing import strip_source

    return hashlib.sha256(strip_source(Path(__file__).read_text(encoding="utf-8")).encode()).hexdigest()[:16]


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
    """Per material the spec uses: mips of (coverage, coverage x linear albedo)."""
    idx = materials(spec, presets)
    lin = np.power(paint.astype(np.float32) / 255, GAMMA)
    out = {}
    for i, m in enumerate(presets):
        w = (idx == i).astype(np.float32)
        if w.any():
            out[m] = Pyramid(np.concatenate([w[..., None], lin * w[..., None]], axis=2))
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
        fill = _fill(vm.car, vm.pix)
        return cls(name, vm.car, vm.pix, vm.uv, major, minor, dirs, background, levels[order], ladder, fill)

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


def _fill(car: np.ndarray, pix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Car pixels the geometry didn't decode copy the nearest one it did."""
    holes = car & ~pix
    if not holes.any() or not pix.any():
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    iy, ix = np.asarray(ndimage.distance_transform_edt(~pix, return_distances=False, return_indices=True))
    order = np.full(pix.shape, -1, np.int64)
    order[pix] = np.arange(int(pix.sum()))
    dst = np.flatnonzero(holes)
    return dst, order[iy.ravel()[dst], ix.ravel()[dst]]


def _lookup(view: View, m: str, idx: np.ndarray, albedo: np.ndarray, g: float, k: float) -> np.ndarray:
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
    """One view's frame, uint8."""
    out = np.zeros((len(view.uv), 3), np.float32)
    for m, pyr in pyrs.items():
        if m not in view.ladder:
            raise StaleCapture(f"the capture has no ladder for {m}; run livery kit capture again")
        acc = filtered(view, pyr, aniso)
        w = acc[:, 0]
        idx = np.flatnonzero(w > 1e-6)
        lin = acc[idx, 1:] / w[idx, None]
        albedo = np.power(np.clip(lin, 0, 1), 1 / GAMMA)
        out[idx] += w[idx, None] * _lookup(view, m, idx, albedo, *curve_gk)
    shown = np.round(np.power(np.clip(out, 0, 1), 1 / GAMMA) * 255).astype(np.uint8)
    frame = view.background.copy()
    frame[view.pix] = shown
    dst, src = view.fill
    frame.reshape(-1, 3)[dst] = shown[src]
    return frame


@dataclass
class Bake:
    """A capture loaded for rendering: its views, the kit's spec presets and the curve."""

    run: Path
    views: dict[str, View]
    presets: dict[str, tuple[int, ...]]
    curve: tuple[float, float]

    @classmethod
    def load(cls, run: Path, kit: Kit, views: list[str]) -> "Bake":
        have = [v for v in views if (run / v / "geometry.npz").exists()]
        if missing := [v for v in views if v not in have]:
            raise StaleCapture(f"the capture {run.name} has no {', '.join(missing)}; run livery kit capture")
        presets = {str(m): tuple(int(x) for x in s) for m, s in kit.spec.items()}
        manifest = state.read_json(run / "manifest.json") or {}
        keys = {str(m): calibrate.ladder_key(manifest, m, s) or str(m) for m, s in kit.spec.items()}
        return cls(run, {v: View.load(run, v, keys) for v in have}, presets, curve(run, have))

    def render(self, paint: np.ndarray, spec: np.ndarray, aniso: bool = ANISO) -> dict[str, np.ndarray]:
        pyrs = pyramids(paint, spec, self.presets)
        return {name: render_view(v, pyrs, self.curve, aniso) for name, v in self.views.items()}

    def render_tgas(self, outputs: dict[str, bytes], aniso: bool = ANISO) -> dict[str, np.ndarray]:
        """A build's car.tga and car_spec.tga, as the sim would show them."""
        paint, spec = (_rgb(outputs[f]) for f in ("car.tga", "car_spec.tga"))
        return self.render(paint, spec, aniso)

    @property
    def id(self) -> str:
        return self.run.name


def _rgb(data: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))


def load_for(cfg: Config, ws: Workspace, kit: Kit, *, require_check: bool = True) -> Bake:
    """The kit's capture at the policy's preview views, refused while stale, or before it passed bake check
    unless `require_check` is off."""
    run = checked(cfg, ws, kit) if require_check else capture(cfg, ws, kit)
    return Bake.load(run, kit, list(ws.policy.preview))


# Pictures.


def crop(view: View, frame: np.ndarray) -> np.ndarray:
    x0, y0, x1, y1 = view.box
    return frame[y0:y1, x0:x1]


def sheet(bake: Bake, frames: dict[str, np.ndarray], width: int = 1600) -> Image.Image:
    """Every view cropped to the car, in two columns `width` wide, each view under the shorter column."""
    col = width // 2
    heights, placed = [0, 0], []
    for v, f in frames.items():
        t = Image.fromarray(crop(bake.views[v], f))
        t = t.resize((col, max(1, round(t.height * col / t.width))), Image.Resampling.LANCZOS)
        j = heights.index(min(heights))
        placed.append((t, j * col, heights[j]))
        heights[j] += t.height
    out = Image.new("RGB", (width, max(heights)), (34, 34, 38))
    for t, x, y in placed:
        out.paste(t, (x, y))
    return out


def write_views(bake: Bake, frames: dict[str, np.ndarray], out: Path, stamp: dict) -> Path:
    """Each view's frame as <view>.png, a sheet.png of them, and build.json last: no stamp until every
    picture is the new build's."""
    from livery import stamps

    out.mkdir(parents=True, exist_ok=True)
    (out / stamps.STAMP).unlink(missing_ok=True)
    for view, frame in frames.items():
        Image.fromarray(frame).save(out / f"{view}.png", compress_level=1)
    sheet(bake, frames).save(out / "sheet.png")
    stamps.write(out / stamps.STAMP, stamp)
    return out


def stamp(bake: Bake, aniso: bool = ANISO) -> dict:
    """What a baked picture adds to a build's stamp: which capture and how it was rendered."""
    return {"baked": True, "capture": bake.id, "aniso": aniso, "renderer": renderer_hash()}


# The critic's baked rounds: like sim runs, one folder per render, never written over.


RUNS = "baked-runs"


def runs(ws: Workspace, name: str) -> list[Path]:
    return sorted((ws.out(name) / RUNS).glob("*/"))


def critic_run(cfg: Config, ws: Workspace, name: str, kit: Kit, outputs: dict[str, bytes]) -> Path:
    """A baked run of the checked build (its TGAs in `outputs`) with the kit's checked capture: the latest
    one there is, or a new one."""
    from livery import stamps
    from livery.simsession import fresh_name

    bake = load_for(cfg, ws, kit)
    built = stamps.digests(outputs)
    for run in reversed(runs(ws, name)):
        st = stamps.read(run / stamps.STAMP)
        if stamps.shows(st, built) and st and st.get("capture") == bake.id:
            return run
    parent = ws.out(name) / RUNS
    parent.mkdir(parents=True, exist_ok=True)
    run = parent / fresh_name(parent)
    write_views(bake, bake.render_tgas(outputs), run, stamps.make(ws, name, outputs) | stamp(bake))
    return run


# bake check: a render against one real preview sim run of the same build.


def errors(baked: np.ndarray, sim: np.ndarray, view: View) -> tuple[np.ndarray, np.ndarray]:
    """Per pixel compared (decoded car pixels more than SIL_PX inside the silhouette): the absolute error
    per channel, and the mask of those pixels."""
    inside = np.asarray(ndimage.distance_transform_edt(view.car)) > SIL_PX
    keep = view.pix & inside
    return np.abs(baked.astype(np.int16) - sim.astype(np.int16))[keep], keep


def stats(e: np.ndarray) -> dict:
    """Median and 95th percentile per channel of (n, 3) errors."""
    if not len(e):
        return {"n": 0, "median": None, "p95": None}
    return {
        "n": len(e),
        "median": [round(float(x), 2) for x in np.median(e, axis=0)],
        "p95": [round(float(x), 2) for x in np.percentile(e, 95, axis=0)],
    }


def worst(report: dict) -> dict:
    """The view with the highest p95 over every design checked."""
    found: dict = {"p95": -1.0}
    for design, r in report.items():
        for view, s in r["views"].items():
            p95 = max(s["p95"] or [-1.0])
            if p95 > found["p95"]:
                found = {"design": design, "view": view, "p95": p95}
    return found


def _label(img: Image.Image, text: str) -> Image.Image:
    ImageDraw.Draw(img).text((6, 4), text, fill=(255, 70, 70))
    return img


def pictures(bake: Bake, baked: dict[str, np.ndarray], sim: dict[str, np.ndarray], out: Path) -> dict:
    """side_by_side.png (baked | sim, a row per view) and error.png (the error per pixel, brightest
    channel, 0 black to 32 and over white; the silhouette band left out in blue)."""
    rows, errs = [], []
    for v, frame in baked.items():
        view = bake.views[v]
        b, s = crop(view, frame), crop(view, sim[v])
        gap = np.full((b.shape[0], 6, 3), 255, np.uint8)
        rows.append(_label(Image.fromarray(np.concatenate([b, gap, s], axis=1)), f"{v}: baked | sim"))
        _, keep = errors(frame, sim[v], view)
        e = np.abs(frame.astype(np.int16) - sim[v].astype(np.int16)).max(axis=2)
        grey = np.clip(e * 8, 0, 255).astype(np.uint8)
        pic = np.repeat(np.where(keep, grey, 0)[..., None], 3, axis=2)
        pic[view.car & ~keep] = (40, 70, 160)
        errs.append(_label(Image.fromarray(crop(view, pic)), f"{v}: error x8"))
    paths = {"side_by_side": out / "side_by_side.png", "error": out / "error.png"}
    for key, tiles in (("side_by_side", rows), ("error", errs)):
        width = max(t.width for t in tiles)
        img = Image.new("RGB", (width, sum(t.height + 6 for t in tiles)), (255, 255, 255))
        y = 0
        for t in tiles:
            img.paste(t, (0, y))
            y += t.height + 6
        img.save(paths[key])
    return {k: str(p) for k, p in paths.items()}


def compare(bake: Bake, baked: dict[str, np.ndarray], shots: Path) -> dict:
    """Per view, the error statistics of a render against a sim run's shots of the same build."""
    out = {}
    for v, frame in baked.items():
        sim = np.asarray(Image.open(shots / f"{v}.png").convert("RGB"))
        if sim.shape != frame.shape:
            raise BakeCheckFailed(
                f"{shots / f'{v}.png'} is {sim.shape[1]}x{sim.shape[0]}, but the capture was shot at "
                f"{frame.shape[1]}x{frame.shape[0]}: put the sim window back to that size"
            )
        out[v] = stats(errors(frame, sim, bake.views[v])[0])
    return out


def check(
    cfg: Config, ws: Workspace, kit: Kit, designs: list[str], *, aniso: bool = ANISO, log: Log = print
) -> dict:
    """Render each design's checked build baked, shoot it with one real preview sim run, and compare them
    outside the silhouette band. The result goes into the capture's manifest as its check; it fails when
    any view's p95 in any channel is over MAX_P95."""
    from livery.check import run_build
    from livery.preview_sim import preview_sim

    run = capture(cfg, ws, kit)
    bake = Bake.load(run, kit, list(ws.policy.preview))
    window = (state.read_json(run / "manifest.json") or {}).get("window")
    report: dict = {}
    for name in designs:
        result = run_build(ws, name)
        if not result["passed"]:
            raise BakeCheckFailed(f"{name} doesn't pass livery check: {result['errors'][:3]}")
        built = ws.out(name)
        outputs = {f: (built / f).read_bytes() for f in ("car.tga", "car_spec.tga")}
        t = time.perf_counter()
        baked = bake.render_tgas(outputs, aniso)
        render_s = time.perf_counter() - t
        log(f"{name}: baked {len(baked)} views in {render_s:.1f} s; shooting it in the sim")
        shots = preview_sim(cfg, ws, name, log=log)
        shot_window = (state.read_json(shots / "manifest.json") or {}).get("window")
        if window and shot_window and list(shot_window) != list(window):
            raise BakeCheckFailed(
                f"the sim window was {shot_window} for {shots}, but the capture was shot at {window}: "
                "put the window back to that size and run bake check again"
            )
        dest = run / "check" / name
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        sim = {v: np.asarray(Image.open(shots / f"{v}.png").convert("RGB")) for v in baked}
        views = compare(bake, baked, shots)
        report[name] = {
            "digests": json.loads((built / "check.json").read_text(encoding="utf-8"))["digests"],
            "sim": str(shots),
            "render_s": round(render_s, 1),
            "views": views,
            "pictures": pictures(bake, baked, sim, dest),
        }
    top = worst(report)
    result = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "passed": top["p95"] <= MAX_P95,
        "max_p95": MAX_P95,
        "silhouette_px": SIL_PX,
        "aniso": aniso,
        "renderer": renderer_hash(),
        "curve": dict(zip(("g", "k"), bake.curve, strict=True)),
        "worst": top,
        "designs": report,
    }
    manifest = state.read_json(run / "manifest.json") or {}
    manifest["check"] = result
    state.write_json(run / "manifest.json", manifest)
    return result | {"capture": str(run)}


def status(cfg: Config, ws: Workspace, kit: Kit) -> dict:
    """The kit's capture, what moved since it was shot, and its bake check."""
    run = calibrate.current(ws, kit.name)
    if run is None:
        return {"kit": kit.name, "capture": None, "stale": [], "check": None, "checked": False}
    moved = stale(cfg, kit, run)
    report = check_of(run)
    ok = not moved and bool(report and report.get("passed"))
    return {"kit": kit.name, "capture": str(run), "stale": moved, "check": report, "checked": ok}
