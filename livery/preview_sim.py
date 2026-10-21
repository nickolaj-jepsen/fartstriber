"""`livery preview sim`: the design in the sim, from every shot preset, on a checked build only."""

import hashlib
import os
import time
from pathlib import Path

from PIL import Image

from livery import metrics
from livery.check import NotChecked, checked_build
from livery.config import Config, Workspace
from livery.designs import kit_name, script
from livery.kit import load, profile, require_replay
from livery.simsession import SimSession, fresh_name


def preview_sim(cfg: Config, ws: Workspace, name: str, log=print) -> Path:
    """Install, reload, shoot every preset, put the paint folder back. Logs a metrics line.

    The shots go to sim/<time>/ only once every one is taken; a run that stops leaves its shots and manifest
    in sim-partial/<time>/.
    """
    t_all = time.perf_counter()
    built, stored = checked_build(ws, name)
    kit = load(ws, kit_name(script(ws, name)))
    prof = profile(kit)
    replay = require_replay(prof, cfg, kit)
    shots = prof.shots(ws.policy.preview)
    # Read once, before the wait for the sim: another session may rebuild the design meanwhile.
    paint, spec = (built / "car.tga").read_bytes(), (built / "car_spec.tga").read_bytes()
    for fname, data in (("car.tga", paint), ("car_spec.tga", spec)):
        if hashlib.sha256(data).hexdigest() != stored["digests"].get(fname):
            raise NotChecked(
                f"{name}'s {fname} changed while it was read; run livery preview sim {name} again"
            )
    groups = {s.group for s in shots.values()}
    what = f"preview {name}"
    with SimSession(
        cfg,
        what,
        paint_folder=kit.paint_folder,
        groups=groups,
        replay=replay,
        window=ws.policy.window,
        renderer=ws.policy.renderer,
        log=log,
        checkout=ws.root,
    ) as s:
        # Named once the sim is ours, so runs sort in shooting order.
        stamp = fresh_name(built / "sim", built / "sim-partial")
        s.out = built / "sim-partial" / stamp
        s.out.mkdir(parents=True)
        s.manifest.update(
            design=name,
            digests={k: stored["digests"][k] for k in ("car.tga", "car_spec.tga")},
            design_hash=stored["design_hash"],
            toolchain_hash=stored["toolchain_hash"],
        )
        paint_name, spec_name = s.paint_names
        t = time.perf_counter()
        s.put({paint_name: paint, spec_name: spec})
        s.timings["install"] = time.perf_counter() - t
        t = time.perf_counter()
        s.reload(next(iter(shots.values())))
        s.timings["reload"] = time.perf_counter() - t
        for shot_name, shot in shots.items():
            Image.fromarray(s.shoot(shot_name, shot, check_ui=True)).save(s.out / f"{shot_name}.png")
            log(f"shot {shot_name}")
    shots_dir = built / "sim" / stamp
    shots_dir.parent.mkdir(parents=True, exist_ok=True)
    os.replace(s.out, shots_dir)
    d = dict(s.timings, total=time.perf_counter() - t_all)
    metrics.log(
        name,
        "sim",
        d,
        shots=str(shots_dir.relative_to(ws.root)),
        lint_errors=0,  # it passed check
        lint_warnings=len(stored.get("warnings", [])),
        critic=None,
    )
    return shots_dir
