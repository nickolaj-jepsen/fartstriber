"""The sim-lit preview without the sim (docs/architecture.md, Previews): a build's paint and spec rendered at the
kit's preview views from its calibration capture (`livery.calibrate`), by `livery.bake_render`.

A capture is used only while what its shots depend on still holds (`stale`), and only once `bake check` has
held this renderer's output against one real preview sim run of the same build (`checked`). A design's baked
views stand in for its sim shots (`usable`) only where that check also covered every finish the build uses.
Every later `preview sim` of such a build measures the bake against the sim again (`drift`).
"""

import hashlib
import json
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from livery import bake_render as render
from livery import calibrate, state
from livery import kit as kits
from livery.bake_render import ANISO, View
from livery.config import Config, Workspace
from livery.errors import LiveryError
from livery.kit import Kit

# The gate (docs/sim.md, The offline bake, has what it was set against). Almost all the error sits along
# paint edges, where a texel of registration noise lands on the other colour, so a view is judged apart
# there and in the rest.
SIL_PX = 2.0  # left out along the silhouette
EDGE_PX = 1.5  # the band along paint edges
EDGE_LEVELS = 24  # neighbouring texels this far apart in any channel, or in another finish, are an edge
MAX_P95 = 12.0  # per channel, per view, away from paint edges
MAX_EDGE_P95 = 24.0  # per channel, per view, in the edge band
USE_PX = 50  # a finish on fewer pixels than this over every view is the spec's antialiasing, not a use
COVER_PX = 2000  # a check covers a finish its passing designs show on at least this many pixels in all
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
    "sim": "the sim's build (an iRacing update)",
    "track_files": "the track's files (an iRacing update)",
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
    shot = calibrate.shot_identity(manifest)
    now = calibrate.identity(cfg, kit, kits.profile(kit))
    moved = [
        what
        for key, what in WATCHED.items()
        if (key in shot or key not in calibrate.VOUCHED) and now.get(key) != shot.get(key)
    ]
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


def renderer_hash() -> str:
    """The renderer's code without comments, so a check names the renderer it held."""
    from livery.hashing import strip_source

    src = Path(render.__file__).read_text(encoding="utf-8")
    return hashlib.sha256(strip_source(src).encode()).hexdigest()[:16]


def within(s: dict) -> bool:
    """Whether one view's statistics pass the gate."""
    rest, edge = s.get("rest") or {}, s.get("edge") or {}
    return max(rest.get("p95") or [0.0]) <= MAX_P95 and max(edge.get("p95") or [0.0]) <= MAX_EDGE_P95


def _views_pass(designs: dict) -> bool:
    return all(within(s) for r in designs.values() for s in r["views"].values())


def checked(cfg: Config, ws: Workspace, kit: Kit) -> Path:
    """The kit's current capture, once bake check with this renderer passed this gate on some design."""
    run = capture(cfg, ws, kit)
    report = check_of(run)
    if not report:
        raise Unchecked(f"{kit.name}'s capture {run.name} hasn't been through livery bake check {kit.name}")
    if report.get("renderer") != renderer_hash():
        raise Unchecked(
            f"{kit.name}'s capture {run.name} was checked with another renderer; run livery bake check "
            f"{kit.name} again"
        )
    if not covered(report):
        worst = report.get("worst") or {}
        raise Unchecked(
            f"{kit.name}'s capture {run.name} failed bake check on every design (p95 {worst.get('p95')}, along "
            f"edges {worst.get('edge_p95')}, at {worst.get('design')} {worst.get('view')}); use sim shots until "
            "it passes"
        )
    return run


# A capture loaded for rendering.


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
        views_ = {v: View.load(run, v, keys) for v in have}
        return cls(run, views_, presets, render.curve(run, have))

    def render(self, paint: np.ndarray, spec: np.ndarray, aniso: bool = ANISO) -> dict[str, np.ndarray]:
        pyrs = render.pyramids(paint, spec, self.presets)
        try:
            return {name: render.render_view(v, pyrs, self.curve, aniso) for name, v in self.views.items()}
        except KeyError as e:
            raise StaleCapture(
                f"the capture has no ladder for {e.args[0]}; run livery kit capture again"
            ) from e

    def render_tgas(self, outputs: dict[str, bytes], aniso: bool = ANISO) -> dict[str, np.ndarray]:
        """A build's car.tga and car_spec.tga, as the sim would show them."""
        paint, spec = (render.rgb(outputs[f]) for f in ("car.tga", "car_spec.tga"))
        return self.render(paint, spec, aniso)

    def finish_map(self, view: View, idx: np.ndarray) -> np.ndarray:
        """Per pixel of a view, the index into `presets` of the finish its texel has (`idx`, per texel), -1
        off the decoded pixels."""
        at = np.full(view.car.shape, -1, np.int64)
        tx = view.texels(idx.shape[0])
        at[view.pix] = idx[tx[:, 1], tx[:, 0]]
        return at

    def finishes(self, spec: np.ndarray) -> dict[str, int]:
        """Per spec preset, the pixels over every view that show it (at each pixel's texel)."""
        idx = render.materials(spec, self.presets)
        names = list(self.presets)
        counts = np.zeros(len(names), np.int64)
        for view in self.views.values():
            counts += np.bincount(self.finish_map(view, idx)[view.pix], minlength=len(names))
        return {m: int(n) for m, n in zip(names, counts, strict=True) if n}

    @property
    def id(self) -> str:
        return self.run.name


def load_for(cfg: Config, ws: Workspace, kit: Kit, *, require_check: bool = True) -> Bake:
    """The kit's capture at the policy's preview views, refused while stale, or before it passed bake check
    unless `require_check` is off."""
    run = checked(cfg, ws, kit) if require_check else capture(cfg, ws, kit)
    return Bake.load(run, kit, list(ws.policy.preview))


def used(finishes: dict[str, int]) -> list[str]:
    """The finishes a build uses, from `Bake.finishes`."""
    return sorted(m for m, n in finishes.items() if n >= USE_PX)


def covered(report: dict | None) -> list[str]:
    """The finishes a bake check vouches for: those compared on enough pixels over the designs that passed
    the gate in every view. Not per finish: a metal's glare spreads over the cells around it whatever their
    finish (docs/sim.md, The offline bake), so a design passes or fails with the finishes it puts together."""
    total: dict[str, int] = {}
    for r in (report or {}).get("designs", {}).values():
        if all(within(s) for s in (r.get("views") or {}).values()):
            for m, n in (r.get("finishes") or {}).items():
                total[m] = total.get(m, 0) + n
    return sorted(m for m, n in total.items() if n >= COVER_PX)


def usable(cfg: Config, ws: Workspace, kit: Kit, finishes: list[str]) -> list[str]:
    """Why a build using these finishes can't be judged on baked views instead of sim shots; empty when it
    can: the capture holds, passed bake check with this renderer, and that check covered every finish."""
    try:
        run = checked(cfg, ws, kit)
    except LiveryError as e:
        return [str(e)]
    if missing := sorted(set(finishes) - set(covered(check_of(run)))):
        return [f"bake check hasn't covered {', '.join(missing)} on {kit.name}"]
    return []


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


def stamp(bake: Bake, outputs: dict[str, bytes] | None = None, aniso: bool = ANISO) -> dict:
    """What a baked picture adds to a build's stamp: which capture, how it was rendered and, given the
    build's TGAs, the finishes it uses."""
    out: dict = {"baked": True, "capture": bake.id, "aniso": aniso, "renderer": renderer_hash()}
    if outputs is not None:
        out["finishes"] = used(bake.finishes(render.rgb(outputs["car_spec.tga"])))
    return out


# Baked runs: like sim runs, one folder per render, never written over, for the critic and the review.


RUNS = "baked-runs"


def runs(ws: Workspace, name: str) -> list[Path]:
    return sorted((ws.out(name) / RUNS).glob("*/"))


def baked_run(cfg: Config, ws: Workspace, name: str, kit: Kit, outputs: dict[str, bytes]) -> Path:
    """A baked run of the checked build (its TGAs in `outputs`) with the kit's checked capture and this
    renderer: the latest one there is, or a new one."""
    from livery import stamps
    from livery.simsession import fresh_name

    bake = load_for(cfg, ws, kit)
    built = stamps.digests(outputs)
    for run in reversed(runs(ws, name)):
        st = stamps.read(run / stamps.STAMP)
        if (
            stamps.shows(st, built)
            and st
            and st.get("capture") == bake.id
            and st.get("renderer") == renderer_hash()
            and "finishes" in st
        ):
            return run
    parent = ws.out(name) / RUNS
    parent.mkdir(parents=True, exist_ok=True)
    run = parent / fresh_name(parent)
    write_views(bake, bake.render_tgas(outputs), run, stamps.make(ws, name, outputs) | stamp(bake, outputs))
    return run


def run_usable(cfg: Config, ws: Workspace, kit: Kit, run: Path) -> list[str]:
    """Why a baked run can't stand in for sim shots of its build; empty when it can: rendered by this
    renderer from the kit's checked capture, of finishes that check covered."""
    from livery import stamps

    st = stamps.read(run / stamps.STAMP) or {}
    if "finishes" not in st:
        return [f"{run.name} doesn't record the finishes it shows"]
    if why := usable(cfg, ws, kit, st["finishes"]):
        return why
    now = calibrate.current(ws, kit.name)
    if st.get("capture") != (now.name if now else None) or st.get("renderer") != renderer_hash():
        return [f"{run.name} was rendered from another capture or renderer than the checked one"]
    return []


# bake check: a render against one real preview sim run of the same build.


def edges(view: View, paint: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """The view's pixels within EDGE_PX of a paint edge, (h, w) bool: where the texel a decoded pixel shows
    differs from a neighbour's by more than EDGE_LEVELS in any channel, or in finish (`idx`, per texel)."""
    h, w = view.car.shape
    tx = view.texels(paint.shape[0])
    img = np.zeros((h, w, 4), np.int32)
    img[view.pix] = np.concatenate([paint[tx[:, 1], tx[:, 0]], 1000 * idx[tx[:, 1], tx[:, 0], None]], axis=1)
    edge = np.zeros((h, w), bool)
    for dy, dx in ((0, 1), (1, 0)):
        a, b = img[: h - dy, : w - dx], img[dy:, dx:]
        both = view.pix[: h - dy, : w - dx] & view.pix[dy:, dx:]
        step = both & (np.abs(a - b) > EDGE_LEVELS).any(axis=2)
        edge[: h - dy, : w - dx] |= step
        edge[dy:, dx:] |= step
    if not edge.any():
        return edge
    return np.asarray(ndimage.distance_transform_edt(~edge)) <= EDGE_PX


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


def view_stats(
    baked: np.ndarray, sim: np.ndarray, view: View, band: np.ndarray, at: np.ndarray, names: list[str]
) -> dict:
    """One view's statistics: over every pixel compared, in the edge band and in the rest; and the same per
    finish (`at`: each pixel's index into `names`), with its pixel count."""
    e, keep = errors(baked, sim, view)
    near, fin = band[keep], at[keep]
    out = stats(e) | {"edge": stats(e[near]), "rest": stats(e[~near]), "finishes": {}}
    for i, m in enumerate(names):
        if (sel := fin == i).any():
            out["finishes"][m] = {
                "n": int(sel.sum()),
                "edge": stats(e[sel & near]),
                "rest": stats(e[sel & ~near]),
            }
    return out


def worst(report: dict) -> dict:
    """The view closest to failing the gate (or furthest past it) over every design checked."""
    found: dict = {"p95": None, "edge_p95": None, "margin": -1.0}
    for design, r in report.items():
        for view, s in r["views"].items():
            p95 = max((s.get("rest") or {}).get("p95") or [0.0])
            edge = max((s.get("edge") or {}).get("p95") or [0.0])
            margin = max(p95 / MAX_P95, edge / MAX_EDGE_P95)
            if margin > found["margin"]:
                found = {
                    "design": design,
                    "view": view,
                    "p95": p95,
                    "edge_p95": edge,
                    "margin": round(margin, 3),
                }
    return found


def _label(img: Image.Image, text: str) -> Image.Image:
    ImageDraw.Draw(img).text((6, 4), text, fill=(255, 70, 70))
    return img


def pictures(
    bake: Bake,
    baked: dict[str, np.ndarray],
    sim: dict[str, np.ndarray],
    bands: dict[str, np.ndarray],
    out: Path,
) -> dict:
    """side_by_side.png (baked | sim, a row per view) and error.png (the error per pixel, brightest
    channel, 0 black to 32 and over white, red in the edge band; the silhouette band left out in blue)."""
    rows, errs = [], []
    for v, frame in baked.items():
        view = bake.views[v]
        b, s = crop(view, frame), crop(view, sim[v])
        gap = np.full((b.shape[0], 6, 3), 255, np.uint8)
        rows.append(_label(Image.fromarray(np.concatenate([b, gap, s], axis=1)), f"{v}: baked | sim"))
        _, keep = errors(frame, sim[v], view)
        e = np.abs(frame.astype(np.int16) - sim[v].astype(np.int16)).max(axis=2)
        grey = np.where(keep, np.clip(e * 8, 0, 255), 0).astype(np.uint8)
        pic = np.repeat(grey[..., None], 3, axis=2)
        pic[keep & bands[v], 1:] //= 3
        pic[view.car & ~keep] = (40, 70, 160)
        errs.append(_label(Image.fromarray(crop(view, pic)), f"{v}: error x8, edge band red"))
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


def _shots(bake: Bake, baked: dict[str, np.ndarray], shots: Path) -> dict[str, np.ndarray]:
    out = {}
    for v, frame in baked.items():
        sim = np.asarray(Image.open(shots / f"{v}.png").convert("RGB"))
        if sim.shape != frame.shape:
            raise BakeCheckFailed(
                f"{shots / f'{v}.png'} is {sim.shape[1]}x{sim.shape[0]}, but the capture was shot at "
                f"{frame.shape[1]}x{frame.shape[0]}: put the sim window back to that size"
            )
        out[v] = sim
    return out


def compare(
    bake: Bake, outputs: dict[str, bytes], baked: dict[str, np.ndarray], shots: Path
) -> tuple[dict, dict[str, np.ndarray], dict[str, np.ndarray], dict[str, int]]:
    """A render of a build against a sim run's shots of it: per view, the error statistics (`view_stats`);
    the shots, the edge bands, and the pixels compared on per finish over every view."""
    sim = _shots(bake, baked, shots)
    paint, spec = (render.rgb(outputs[f]) for f in ("car.tga", "car_spec.tga"))
    idx = render.materials(spec, bake.presets)
    names = list(bake.presets)
    bands = {v: edges(bake.views[v], paint, idx) for v in baked}
    views = {
        v: view_stats(frame, sim[v], bake.views[v], bands[v], bake.finish_map(bake.views[v], idx), names)
        for v, frame in baked.items()
    }
    finishes: dict[str, int] = {}
    for s in views.values():
        for m, f in s["finishes"].items():
            finishes[m] = finishes.get(m, 0) + f["n"]
    return views, sim, bands, finishes


def check(
    cfg: Config, ws: Workspace, kit: Kit, designs: list[str], *, aniso: bool = ANISO, log: Log = print
) -> dict:
    """Render each design's checked build baked, shoot it with one real preview sim run, and compare them
    outside the silhouette band. The result goes into the capture's manifest as its check, with the
    finishes it covers (`covered`) and the sim's build it held against, beside the earlier check's other
    designs while that check had this renderer and sim build; it fails when any view fails the gate."""
    from livery.check import run_build
    from livery.preview_sim import preview_sim

    run = capture(cfg, ws, kit)
    bake = Bake.load(run, kit, list(ws.policy.preview))
    window = (state.read_json(run / "manifest.json") or {}).get("window")
    now = calibrate.identity(cfg, kit, kits.profile(kit))
    vouched = {k: now.get(k) for k in calibrate.VOUCHED}
    before = check_of(run) or {}
    same = before.get("renderer") == renderer_hash() and before.get("identity") == vouched
    report: dict = {d: r for d, r in (before.get("designs") or {}).items() if same and d not in designs}
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
        shots = preview_sim(cfg, ws, name, log=log, drift=False)
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
        views, sim, bands, finishes = compare(bake, outputs, baked, shots)
        report[name] = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "digests": json.loads((built / "check.json").read_text(encoding="utf-8"))["digests"],
            "sim": str(shots),
            "render_s": round(render_s, 1),
            "finishes": finishes,
            "views": views,
            "pictures": pictures(bake, baked, sim, bands, dest),
        }
    result: dict = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "passed": _views_pass(report),
        "gate": {
            "max_p95": MAX_P95,
            "max_edge_p95": MAX_EDGE_P95,
            "edge_px": EDGE_PX,
            "silhouette_px": SIL_PX,
        },
        "aniso": aniso,
        "renderer": renderer_hash(),
        "curve": dict(zip(("g", "k"), bake.curve, strict=True)),
        "identity": vouched,
        "worst": worst(report),
        "designs": report,
    }
    result["covered"] = covered(result)
    manifest = state.read_json(run / "manifest.json") or {}
    manifest["check"] = result
    state.write_json(run / "manifest.json", manifest)
    return result | {"capture": str(run)}


# The sim against the bake, after every preview sim run of a build the bake stands in for.


def drift(cfg: Config, ws: Workspace, kit: Kit, outputs: dict[str, bytes], shots: Path) -> dict:
    """The bake against a sim run of a build: `usable` false (and why) when the bake can't stand in for
    this build's sim shots, else the error statistics per view, whether they pass the gate and the worst."""
    from livery import stamps

    try:
        bake = load_for(cfg, ws, kit)
        finishes = used(bake.finishes(render.rgb(outputs["car_spec.tga"])))
    except LiveryError as e:
        return {"usable": False, "why": [str(e)]}
    if why := usable(cfg, ws, kit, finishes):
        return {"usable": False, "why": why}
    baked = bake.render_tgas(outputs)
    try:
        views = compare(bake, outputs, baked, shots)[0]
    except BakeCheckFailed as e:
        return {"usable": True, "passed": False, "why": [str(e)]}
    report = {"run": {"views": views}}
    top = worst(report)
    top.pop("design", None)
    return {
        "usable": True,
        "capture": bake.id,
        "renderer": renderer_hash(),
        "digests": stamps.digests(outputs),
        "passed": _views_pass(report),
        "worst": top,
        "views": views,
    }


def status(cfg: Config, ws: Workspace, kit: Kit) -> dict:
    """The kit's capture, what moved since it was shot, its bake check, and the finishes that covered."""
    run = calibrate.current(ws, kit.name)
    if run is None:
        return {"kit": kit.name, "capture": None, "stale": [], "check": None, "checked": False, "covered": []}
    moved = stale(cfg, kit, run)
    report = check_of(run)
    try:
        checked(cfg, ws, kit)
        ok, why = True, None
    except LiveryError as e:
        ok, why = False, str(e)
    out = {"kit": kit.name, "capture": str(run), "stale": moved, "check": report, "checked": ok}
    return out | {"why": why, "covered": covered(report) if ok else []}


def design_status(cfg: Config, ws: Workspace, name: str) -> dict:
    """`status` for a design's kit, and whether its checked build's baked views stand in for sim shots."""
    from livery.check import checked_build
    from livery.designs import kit_name, script

    kit = kits.load(ws, kit_name(script(ws, name)))
    out = status(cfg, ws, kit) | {"design": name, "finishes": None, "usable": False}
    if not out["checked"]:
        return out | {"usable_why": [out["why"]]}
    try:
        built, _ = checked_build(ws, name)
    except LiveryError as e:
        return out | {"usable_why": [str(e)]}
    spec = render.rgb((built / "car_spec.tga").read_bytes())
    finishes = used(load_for(cfg, ws, kit).finishes(spec))
    why = usable(cfg, ws, kit, finishes)
    return out | {"finishes": finishes, "usable": not why, "usable_why": why}
