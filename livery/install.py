"""`livery install`: the final paint into the paint folder, solo and team names, with spec .mip housekeeping.

The sim builds a spec TGA's .mip when it loads it; once that .mip exists, the TGA comes out of the folder
(otherwise the sim rebuilds the .mip every launch) and the .mip is archived, since Trading Paints takes the
.mip, not the TGA. The .mip appears only for the paint name the loaded session uses; the other name's spec
TGA stays until `livery install --finish` runs after a session of that kind.

Install, restore and finish each hold the sim lock throughout, so a sim run that is putting its own paint
back can't undo them.
"""

import shutil
import time
from pathlib import Path

from livery import approvals, config, metrics, paintfolder
from livery.check import checked_build
from livery.config import Config, Workspace
from livery.designs import kit_name, script
from livery.iracing import trading_paints_running, wait_for
from livery.kit import load
from livery.simsession import SimSession

TP_WARNING = (
    "Trading Paints Downloader is running: it may write its own paint over these files when a session loads"
)


def stems(cfg: Config) -> list[str]:
    """The paint names to install: solo (customer id) and, when configured, team."""
    out = [str(cfg.customer_id)]
    if cfg.team_id:
        out.append(f"team_{cfg.team_id}")
    return out


def install(cfg: Config, ws: Workspace, name: str, log=print, unapproved: bool = False) -> list[str]:
    """Installs the checked build, which must be the one the owner approved, placing only approved roster
    sponsor variants and race markings, unless `unapproved`."""
    built, stored = checked_build(ws, name)
    if unapproved:
        log(f"{name}: installing a build {config.owner()} has not approved")
        for placed in approvals.unapproved(ws, stored):
            log(f"{name}: it places {placed}, not approved either")
    else:
        approvals.require(ws, name, stored)
    kit = load(ws, kit_name(script(ws, name)))
    paint, spec = (built / "car.tga").read_bytes(), (built / "car_spec.tga").read_bytes()
    files = {}
    for stem in stems(cfg):
        files[f"car_{stem}.tga"] = paint
        files[f"car_spec_{stem}.tga"] = spec
    if trading_paints_running():
        log(TP_WARNING)
    t = time.perf_counter()
    folder = paintfolder.paint_dir(cfg, kit.paint_folder)
    with SimSession(cfg, f"install {name}", paint_folder=kit.paint_folder, sim="optional", log=log) as s:
        for p in paintfolder.install(cfg, kit.paint_folder, files, log=log):
            log(f"installed {p}")
        # Old .mips would be newer than nothing we can compare against; drop them so the sim rebuilds.
        for stem in stems(cfg):
            (folder / f"car_spec_{stem}.mip").unlink(missing_ok=True)
        if s.sim is not None:
            s.reload_now()
            _, spec_name = s.paint_names
            wait_for(lambda: (folder / spec_name).with_suffix(".mip").exists(), 20, step=0.25)
        else:
            log(
                f"sim not reloaded ({s.unavailable}); the .mip will be built when the sim next loads this paint"
            )
        notes = _finish(cfg, ws, name, kit.paint_folder, log)
    metrics.log(name, "install", {"total": time.perf_counter() - t})
    return notes


def finish(cfg: Config, ws: Workspace, name: str, log=print) -> list[str]:
    """For every installed spec with a fresh .mip: archive the .mip, take the TGA out of the folder."""
    kit = load(ws, kit_name(script(ws, name)))
    with SimSession(cfg, f"install {name} --finish", sim="none", log=log):
        return _finish(cfg, ws, name, kit.paint_folder, log)


def _finish(cfg: Config, ws: Workspace, name: str, paint_folder: str, log) -> list[str]:
    folder = paintfolder.paint_dir(cfg, paint_folder)
    archive = archive_dir(cfg, ws) / name / time.strftime("%Y%m%d-%H%M%S")
    notes = []
    for stem in stems(cfg):
        tga, mip = folder / f"car_spec_{stem}.tga", folder / f"car_spec_{stem}.mip"
        if mip.exists() and (not tga.exists() or mip.stat().st_mtime >= tga.stat().st_mtime):
            archive.mkdir(parents=True, exist_ok=True)
            shutil.copy2(mip, archive / mip.name)
            tga.unlink(missing_ok=True)
            notes.append(f"{mip.name}: archived to {archive}, spec TGA removed")
        elif tga.exists():
            notes.append(
                f"{tga.name}: kept until the sim builds its .mip (run livery install {name} --finish after)"
            )
    for n in notes:
        log(n)
    return notes


def restore(cfg: Config, log=print) -> list[str]:
    """Put every paint folder back as it was before the first install, and reload the car if it shows."""
    notes = []
    with SimSession(cfg, "install --restore", sim="optional", log=log) as s:
        folders = paintfolder.backed_up(log)
        for folder in folders:
            for line in paintfolder.restore(cfg, folder, log):
                notes.append(f"{folder}: {line}")
                log(notes[-1])
        if s.car is not None and s.car.path in folders:
            s.reload_now()
    return notes


def archive_dir(cfg: Config, ws: Workspace) -> Path:
    """Where final .mips go: the user's archive_dir, else under .cache."""
    return cfg.archive_dir or ws.root / ".cache" / "archive"
