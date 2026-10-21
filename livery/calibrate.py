"""`livery kit capture`: one calibration capture per car, at its preview views (docs/architecture.md, Kits).

It shoots the Gray-code geometry (`livery.graycode`) under a matte spec and a grey ladder under each of the
kit's spec presets. Each paint is a sim run of its own (install, reload, shoot every view, put the paint
back), so other sim runs queue between paints rather than behind the whole capture.

A run shoots into `kit-capture/<kit>/<run>/jobs/<paint>/` in the machine state dir (`livery.state`), so every
worktree on the machine shares one capture per car. Once every paint is shot, it decodes each
view into:

- `<view>/geometry.npz`: a `graycode.ViewMap`, the texel each car pixel samples and its footprint
- `<view>/ladder.npz`: per material (L, n, 3) uint8, what each pixel shows at each `levels` grey, in the
  ViewMap's pixel order
- `<view>/background.png`: a frame for the pixels the paint doesn't reach

then writes the manifest with a summary, points `current` at the run and deletes the frames. A run that
stops is carried on with `--resume`, after its last finished paint. When a spec preset changes, `reshoot`
shoots that material's ladder again into the current capture (`livery kit spec --preset`).
"""

import configparser
import hashlib
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from livery import cameras, graycode, state
from livery import kit as kits
from livery.config import Config, Shot, Workspace
from livery.errors import LiveryError
from livery.hashing import kit_hash
from livery.kit import Kit
from livery.model import Material
from livery.raster import tga_bytes
from livery.simsession import SimSession, fresh_name

GEOMETRY_SPEC = (0, 255, 0)  # no metal, fully rough, no clearcoat: the fewest reflections to read through
LEVELS = (0, 40, 80, 120, 160, 200, 255)
FINISH_GREY = 80  # the ladder step a finish is told apart from gloss at
BACKGROUND = f"{Material.matte}_120"
MIN_DECODED_PCT = 80.0  # a view that decodes less of the car than this was shot wrong
CLIPPED = 254
HIGHLIGHT = 64  # black paint under gloss shows only reflections: brighter than this, a highlight
RENDERER_INI = "rendererDX11Monitor.ini"
RENDERER_SECTION = "Graphics Options"

Log = Callable[[str], object]


class CaptureFailed(LiveryError):
    pass


@dataclass(frozen=True)
class Job:
    name: str
    grey: int | None  # a flat grey paint, or None for a geometry pattern
    spec: tuple[int, int, int]


def jobs(kit: Kit) -> list[Job]:
    out = [Job(graycode.WHITE, 255, GEOMETRY_SPEC), Job(graycode.BLACK, 0, GEOMETRY_SPEC)]
    out += [Job(n, None, GEOMETRY_SPEC) for n in graycode.names()]
    return out + ladder_jobs(kit, list(kit.spec))


def ladder_jobs(kit: Kit, materials: list[Material]) -> list[Job]:
    return [Job(f"{m}_{lv}", lv, kit.spec[m]) for m in materials for lv in LEVELS]


def paint(job: Job, canvas: int) -> np.ndarray:
    if job.grey is None:
        return graycode.pattern(job.name, canvas)
    return np.full((canvas, canvas, 3), job.grey, np.uint8)


# What a capture depends on.


def renderer_settings(path: Path) -> dict[str, str] | None:
    """The sim's graphics options, without their comments."""
    if not path.exists():
        return None
    ini = configparser.ConfigParser(inline_comment_prefixes=(";",), interpolation=None, strict=False)
    ini.optionxform = str  # type: ignore[assignment]  # keep the keys' case
    ini.read_string(path.read_text(encoding="utf-8", errors="replace"))
    return dict(ini[RENDERER_SECTION]) if ini.has_section(RENDERER_SECTION) else None


def car_dat(cfg: Config, paint_folder: str) -> dict | None:
    """The car's file in the UI's httproot, which an iRacing update to the car rewrites."""
    dat = kits.model_path(cfg.ui_cars, paint_folder)
    if not dat.exists():
        return None
    st = dat.stat()
    return {"file": dat.name, "size": st.st_size, "mtime_ns": st.st_mtime_ns}


def _sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def identity(cfg: Config, ws: Workspace, kit: Kit, prof: kits.Profile) -> dict:
    """Everything the shots depend on."""
    r = cfg.replay
    return {
        "window": list(ws.policy.window),
        "kit": kit.name,
        "kit_hash": kit_hash(kit.dir),
        "canvas": kit.canvas,
        "paint_folder": kit.paint_folder,
        "frame": prof.frame,
        "views": {n: {"group": v.group, "pose": list(v.pose), "aim": v.aim} for n, v in prof.preview.items()},
        "replay": {
            "file": r.file,
            "sha256": r.sha256,
            "subsession": r.subsession,
            "track_name": r.track_name,
            "track": r.track,
        },
        "track_cameras": _sha(cameras.track_cam(cfg.documents, r.track)),
        "renderer": renderer_settings(cfg.documents / RENDERER_INI),
        "car_dat": car_dat(cfg, kit.paint_folder),
        "scheme": [[list(slot) for slot in p] for p in graycode.SCHEME],
        "jobs": [[j.name, j.grey, list(j.spec)] for j in jobs(kit)],
    }


def shot_with(manifest: dict) -> dict:
    """A capture's identity. Captures from before the window was part of it recorded the size their first
    paint was shot at beside it."""
    ident = manifest.get("identity", {})
    return ident if "window" in ident else {**ident, "window": manifest.get("window")}


# Runs.


def root(ws: Workspace, kit_name: str) -> Path:
    """Captures are of this machine's sim, not of a checkout: they live in the state dir."""
    return state.root() / "kit-capture" / kit_name


def current(ws: Workspace, kit_name: str) -> Path | None:
    """The last finished capture of the kit, or None."""
    pointer = root(ws, kit_name) / "current"
    return root(ws, kit_name) / pointer.read_text(encoding="utf-8").strip() if pointer.exists() else None


def _point_at(run: Path) -> None:
    tmp = run.parent / "current.tmp"
    tmp.write_text(run.name, encoding="utf-8", newline="\n")
    os.replace(tmp, run.parent / "current")


def _manifest(run: Path) -> dict:
    return state.read_json(run / "manifest.json") or {}


def _unfinished(ws: Workspace, kit_name: str) -> list[Path]:
    d = root(ws, kit_name)
    runs = sorted(p for p in d.iterdir() if (p / "manifest.json").exists()) if d.exists() else []
    return [p for p in runs if not _manifest(p).get("complete")]


def _resumable(ws: Workspace, kit_name: str, ident: dict) -> Path:
    runs = _unfinished(ws, kit_name)
    if not runs:
        raise CaptureFailed(
            f"no unfinished capture of {kit_name} to resume; run livery kit capture {kit_name}"
        )
    run = runs[-1]
    old = shot_with(_manifest(run))
    moved = sorted(k for k in ident.keys() | old.keys() if ident.get(k) != old.get(k))
    if moved:
        raise CaptureFailed(
            f"{run} was shot with another {', '.join(moved)}; start a fresh capture without --resume"
        )
    return run


def _done(run: Path, job: Job, views: dict[str, Shot]) -> bool:
    d = run / "jobs" / job.name
    m = state.read_json(d / "manifest.json")
    return m is not None and m.get("failure") is None and all((d / f"{v}.png").exists() for v in views)


def capture(
    cfg: Config, ws: Workspace, kit: Kit, *, resume: bool = False, keep_frames: bool = False, log: Log = print
) -> Path:
    """Shoot every paint not yet shot, decode, and point `current` at the run. About 15 minutes."""
    prof = kits.profile(kit)
    kits.require_replay(prof, cfg, kit)
    kits.check_model(kit, cfg.ui_cars)
    if not prof.preview:
        raise kits.KitError(f"{kit.name}'s profile has no preview views")
    views = prof.shots(prof.preview)
    ident = identity(cfg, ws, kit, prof)
    if resume:
        run = _resumable(ws, kit.name, ident)
    else:
        for old in _unfinished(ws, kit.name):
            log(f"an unfinished capture is in {old}; --resume carries it on")
        run = root(ws, kit.name) / fresh_name(root(ws, kit.name))
        state.write_json(run / "manifest.json", {"identity": ident, "complete": False, "started": _now()})
    all_jobs = jobs(kit)
    todo = [j for j in all_jobs if not _done(run, j, views)]
    for i, job in enumerate(todo, len(all_jobs) - len(todo) + 1):
        t = time.perf_counter()
        reload_s = _shoot(cfg, ws, kit, run, job, views, log)
        log(f"{i}/{len(all_jobs)} {job.name}: reload {reload_s:.1f} s, {time.perf_counter() - t:.0f} s")
    log("decoding")
    summary = _decode(run, kit, views)
    low = {v: s["decoded_pct"] for v, s in summary["views"].items() if s["decoded_pct"] < MIN_DECODED_PCT}
    if low:
        raise CaptureFailed(
            f"only {low} percent of the car decoded; look at the frames in {run / 'jobs'} (the replay UI or "
            "something else over the car?) before shooting again"
        )
    manifest = _manifest(run)
    if not keep_frames:
        for f in (run / "jobs").glob("*/*.png"):
            f.unlink()
    manifest.update(summary=summary, complete=True, finished=_now(), stored_bytes=_size(run))
    state.write_json(run / "manifest.json", manifest)
    _point_at(run)
    return run


def _size(run: Path) -> int:
    return sum(f.stat().st_size for f in run.rglob("*") if f.is_file())


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _shoot(
    cfg: Config, ws: Workspace, kit: Kit, run: Path, job: Job, views: dict[str, Shot], log: Log
) -> float:
    """One paint under the sim lock: install, reload, shoot every view, put the paint back."""
    data = tga_bytes(paint(job, kit.canvas))
    spec = tga_bytes(np.full((kit.canvas, kit.canvas, 3), job.spec, np.uint8))
    out = run / "jobs" / job.name
    groups = {s.group for s in views.values()}
    what = f"kit capture {kit.name}: {job.name}"
    with SimSession(
        cfg,
        what,
        paint_folder=kit.paint_folder,
        groups=groups,
        window=ws.policy.window,
        log=log,
        checkout=ws.root,
    ) as s:
        out.mkdir(parents=True, exist_ok=True)
        s.out = out
        s.manifest["job"] = {"name": job.name, "grey": job.grey, "spec": list(job.spec)}
        paint_name, spec_name = s.paint_names
        s.put({paint_name: data, spec_name: spec})
        reload = s.reload(next(iter(views.values())))
        for view, shot in views.items():
            Image.fromarray(s.shoot(view, shot, check_ui=True)).save(out / f"{view}.png", compress_level=1)
    return float(reload["total_s"] or 0.0)


# Decoding and the summary.


def _decode(run: Path, kit: Kit, views: dict[str, Shot]) -> dict:
    for view in views:

        def shot(name: str, view: str = view) -> np.ndarray:
            return np.asarray(Image.open(run / "jobs" / name / f"{view}.png").convert("RGB"))

        vm = graycode.decode(shot)
        vm.save(run / view / "geometry.npz")
        arrays: dict[str, Any] = {"levels": np.array(LEVELS)}
        for m in kit.spec:
            arrays[str(m)] = np.stack([shot(f"{m}_{lv}")[vm.pix] for lv in LEVELS])
        np.savez_compressed(run / view / "ladder.npz", **arrays)
        Image.fromarray(shot(BACKGROUND)).save(run / view / "background.png")
    return summarise(run, list(views), kit.spec)


def ladders(run: Path, view: str) -> tuple[list[int], dict[str, np.ndarray]]:
    """A view's ladder steps and each ladder, (L, n, 3), by `ladder_key`."""
    with np.load(run / view / "ladder.npz") as z:
        lad = {k: z[k] for k in z.files if not k.startswith("satin_")}  # an older capture's satin trial
    return lad.pop("levels").tolist(), lad


def summarise(run: Path, views: list[str], spec: dict[Material, tuple[int, int, int]]) -> dict:
    """Per view, how much of the car decoded and how well the plane fit agrees; per material, the ladder's
    response and whether white clips, the grey it needs to show what gloss shows, and how far it reads from
    gloss (and from matte) at FINISH_GREY, outside and inside highlights."""
    out: dict = {"views": {}, "materials": {}}
    responses: dict[str, dict[str, list[float]]] = {}
    levels: list[int] = list(LEVELS)
    for view in views:
        out["views"][view] = graycode.ViewMap.load(run / view / "geometry.npz").stats()
        levels, lad = ladders(run, view)
        for m, lut in lad.items():
            responses.setdefault(m, {})[view] = response(lut)
            row = out["materials"].setdefault(m, {"response": {}, "white_clipped_pct": {}})
            row["response"][view] = responses[m][view]
            clipped = (lut[-1] >= CLIPPED).any(axis=1)
            row["white_clipped_pct"][view] = round(100 * float(clipped.mean()), 2) if len(clipped) else 0.0
        gloss, matte = lad.get(Material.gloss), lad.get(Material.matte)
        if gloss is not None and FINISH_GREY in levels and 0 in levels:
            at = levels.index(FINISH_GREY)
            for m, lut in lad.items():
                if m != Material.gloss:
                    other = None if matte is None or m == Material.matte else matte[at]
                    row = against(lut[at], gloss[at], gloss[levels.index(0)], other)
                    out["materials"][m].setdefault("vs_gloss", {})[view] = row
    gloss = responses.get(Material.gloss)
    for m, by_view in responses.items():
        if gloss is None or m == Material.gloss:
            continue
        out["materials"][m]["compensation"] = {
            "paint": levels,
            "all_views": compensation(
                levels,
                np.median([gloss[v] for v in by_view], axis=0).tolist(),
                np.median(list(by_view.values()), axis=0).tolist(),
            ),
            "views": {v: compensation(levels, gloss[v], by_view[v]) for v in by_view},
        }
    return out


def response(lut: np.ndarray) -> list[float]:
    """The median output (the mean of R, G and B) at each ladder step."""
    if not lut.shape[1]:
        return [0.0] * len(lut)
    return [round(float(np.median(step.astype(np.float32).mean(axis=1))), 1) for step in lut]


def compensation(levels: list[int], gloss: list[float], material: list[float]) -> dict:
    """For each grey under gloss, the grey under the material whose median output matches it, None where the
    material's white doesn't reach it; and the brightest gloss grey it can match."""
    resp = np.maximum.accumulate(np.asarray(material, np.float64))
    lv = np.asarray(levels, np.float64)
    needs = [None if y > resp[-1] else round(float(np.interp(y, resp, lv)), 1) for y in gloss]
    reachable = float(np.interp(resp[-1], np.maximum.accumulate(np.asarray(gloss, np.float64)), lv))
    return {"needs": needs, "reachable": round(reachable, 1)}


def _spread(a: np.ndarray, b: np.ndarray, sel: np.ndarray | None = None) -> list[float]:
    """p50, p95 and p99 of the brightest channel's difference per pixel."""
    d = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=1)
    d = d if sel is None else d[sel]
    return [round(float(x), 1) for x in np.percentile(d, [50, 95, 99])] if len(d) else []


def against(shot: np.ndarray, gloss: np.ndarray, gloss_black: np.ndarray, matte: np.ndarray | None) -> dict:
    """How far a finish's shot is from gloss (and matte) at the same grey, outside and inside the highlights,
    and its mean brightness against gloss's."""
    highlight = gloss_black.astype(np.float32).mean(axis=1) > HIGHLIGHT
    out: dict = {"highlight_pct": round(100 * float(highlight.mean()), 2) if len(highlight) else 0.0}
    refs = {"gloss": gloss, **({"matte": matte} if matte is not None else {})}
    for name, ref in refs.items():
        for where, sel in (("outside", ~highlight), ("inside", highlight)):
            if sel.any():
                out[f"vs_{name}_{where}_p50_95_99"] = _spread(shot, ref, sel)
    if len(shot):
        out["mean_minus_gloss"] = round(
            float(shot.astype(np.float32).mean() - gloss.astype(np.float32).mean()), 1
        )
    return out


def report(summary: dict) -> list[str]:
    """The summary as text, a line per view and material."""
    lines = []
    for view, s in summary.get("views", {}).items():
        tpp = s.get("texels_per_px", {}).get("major_p10_50_90")
        lines.append(
            f"{view}: {s['decoded_pct']}% of {s['car_px']} car px decoded, plane fit on {s['plane_fit_pct']}%, "
            f"agreeing on {s['plane_agree_pct']}%; texels per px along the major axis p10/50/90 {tpp}"
        )
    for m, row in summary.get("materials", {}).items():
        for view, r in row["response"].items():
            lines.append(f"{m} at {view}: {r} (white clips on {row['white_clipped_pct'][view]}%)")
        comp = row.get("compensation")
        if comp:
            a = comp["all_views"]
            lines.append(f"{m} matches gloss {comp['paint']} with {a['needs']}, up to gloss {a['reachable']}")
        for view, s in row.get("vs_gloss", {}).items():
            lines.append(
                f"{m} at {view}, grey {FINISH_GREY}: vs gloss outside highlights p50/95/99 "
                f"{s.get('vs_gloss_outside_p50_95_99')}, mean {s.get('mean_minus_gloss')} brighter"
            )
    return lines


def ladder_key(manifest: dict, material: str, spec) -> str | None:
    """Where in ladder.npz the capture keeps `material`'s ladder shot under `spec`: under the material's name
    when the capture itself shot it so, else under `<material>@<spec>` once `reshoot` added it; None when it
    has none."""
    spec = [int(x) for x in spec]
    shot = {name: s for name, _grey, s in manifest.get("identity", {}).get("jobs", [])}
    if all(shot.get(f"{material}_{lv}") == spec for lv in LEVELS):
        return str(material)
    key = f"{material}@{'-'.join(str(x) for x in spec)}"
    return key if key in manifest.get("ladders", {}) else None


def reshoot(cfg: Config, ws: Workspace, kit: Kit, materials: list[Material], log: Log = print) -> Path:
    """Shoot the grey ladder of `materials` again into the kit's current capture, under the presets the kit
    has now (after a preset changed). The new ladder goes beside the capture's own, under `ladder_key`, so a
    checkout still on the old preset keeps reading the old one. The capture must otherwise be of the kit's
    car, frame and views as they are."""
    run = current(ws, kit.name)
    manifest = _manifest(run) if run else {}
    if run is None or not manifest.get("complete"):
        raise CaptureFailed(
            f"{kit.name} has no finished calibration capture; run livery kit capture {kit.name}"
        )
    prof = kits.profile(kit)
    views = prof.shots(prof.preview)
    ident = identity(cfg, ws, kit, prof)
    old = shot_with(manifest)
    moved = sorted(k for k in ident.keys() - {"jobs", "kit_hash"} if ident.get(k) != old.get(k))
    if moved:
        raise CaptureFailed(f"{run} was shot with another {', '.join(moved)}; shoot a fresh capture")
    keys = {m: f"{m}@{'-'.join(str(x) for x in kit.spec[m])}" for m in materials}
    todo = [Job(f"{keys[m]}_{lv}", lv, kit.spec[m]) for m in materials for lv in LEVELS]
    for i, job in enumerate(todo, 1):
        reload_s = _shoot(cfg, ws, kit, run, job, views, log)
        log(f"{i}/{len(todo)} {job.name}: reload {reload_s:.1f} s")
    for view in views:
        vm = graycode.ViewMap.load(run / view / "geometry.npz")
        with np.load(run / view / "ladder.npz") as z:
            arrays = {k: z[k] for k in z.files}
        for key in keys.values():
            frames = [
                np.asarray(Image.open(run / "jobs" / f"{key}_{lv}" / f"{view}.png").convert("RGB"))
                for lv in LEVELS
            ]
            arrays[key] = np.stack([f[vm.pix] for f in frames])
        np.savez_compressed(run / view / "ladder.npz", **arrays)
    for job in todo:
        for f in (run / "jobs" / job.name).glob("*.png"):
            f.unlink()
    manifest = _manifest(run)
    ladders = manifest.setdefault("ladders", {})
    for m, key in keys.items():
        ladders[key] = {"material": str(m), "spec": list(kit.spec[m]), "at": _now()}
    manifest.update(summary=summarise(run, list(views), kit.spec), stored_bytes=_size(run))
    state.write_json(run / "manifest.json", manifest)
    return run
