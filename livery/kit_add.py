"""`livery kit add <psd> --car <kit> --paint-folder <folder>`: a car from its paint template to the owner's
kit review in one command, which carries on where it stopped when run again.

Each step is skipped once it is done:

1. extract: the kit from the template, unless the kit was made from this one.
2. profile: the replay the car is shot on (the open replay if it shows the car, else your preview replay)
   and the preview views on camera groups of the kit's own, posed for a car as big as the McLaren.
3. cameras: every kit's preview cameras into that replay's track set. When camera.ini has to change it waits
   for the sim to close first (`cameras.armed`), so one close and reopen loads them.
4. frame: `pick-frame`, which waits until the reopened replay lists the cameras. If they don't fit the car
   it sizes them for it, and the step goes back to the cameras once.
5. capture: `kit capture`, the calibration capture at the preview views (about 15 minutes), unless the
   current one was shot of the kit as it is, on its frame and cameras; then `kit spec`, the finishes'
   compensation from its ladders.
6. fit: `kit fit`, from the capture.
7. review: `kit review`, the overlays (and the body model's pictures once `kit label` named its parts)
   and the calibration livery in the sim.

It stops with the next step wherever it needs the owner (closing or reopening the replay, saving the track's
camera set once, the review itself) or a step that can't run yet.
"""

from dataclasses import replace
from pathlib import Path

from livery import bake, calibrate, cameras, config, kit_fit, kit_review, kit_spec, pick_frame, simhost, state
from livery import kit as kits
from livery.config import Config, Workspace
from livery.errors import LiveryError
from livery.hashing import kit_hash
from livery.iracing import Sim, SimNotReady, sim_running
from livery.kit import Kit, View
from livery.kit_extract import extract

RESIZES = 1  # times the cameras are sized for the car again before it stops and asks


def open_replay(cfg: Config, paint_folder: str) -> tuple[int, str] | None:
    """The open replay's (SubSessionID, TrackName), if it shows your car in this paint folder. Reads only."""
    sim = Sim(cfg)
    try:
        if not (sim.connected() and sim.is_replay()) or sim.car().path != paint_folder:
            return None
        sub, track = sim.replay()
        return (int(sub), str(track)) if sub is not None and track else None
    except SimNotReady:
        return None
    finally:
        sim.close()


def _identity(cfg: Config, found: tuple[int, str]) -> tuple[int, str | None]:
    """A replay as a profile names it: by SubSessionID, and by track unless it is the preview replay."""
    return found[0], None if found[0] == cfg.replay.subsession else found[1]


def skeleton(cfg: Config, ws: Workspace, kit: Kit, log=print) -> kits.Profile:
    """A new kit's profile: its replay, no frame yet, and its preview views posed for a McLaren-sized car."""
    found = open_replay(cfg, kit.paint_folder)
    if found is None:
        sub, track = cfg.replay.subsession, None
        log(
            f"no open replay shows {kit.paint_folder}: framing {kit.name} on your preview replay ({cfg.replay.file})"
        )
    else:
        sub, track = _identity(cfg, found)
        log(f"framing {kit.name} on the open replay (subsession {found[0]} at {found[1]})")
    poses = cameras.preview_poses()
    names = list(ws.policy.preview)
    missing = [n for n in names if n not in poses]
    if missing:
        raise kits.KitError(f"there is no default camera for the preview view {', '.join(missing)}")
    preview = {n: View(cameras.group_name(kit.name, n, *poses[n]), *poses[n]) for n in names}
    return kits.Profile(sub, None, preview, {}, track_name=track)


def follow_open_replay(cfg: Config, kit: Kit, log=print) -> None:
    """A kit with no frame yet takes the open replay, if it shows the car, so its cameras go to that track."""
    prof = kits.profile(kit)
    found = open_replay(cfg, kit.paint_folder) if prof.frame is None else None
    if found is not None and _identity(cfg, found) != (prof.replay, prof.track_name):
        sub, track = _identity(cfg, found)
        kits.write_profile(kit.dir, replace(prof, replay=sub, track_name=track))
        log(f"framing {kit.name} on the open replay (subsession {found[0]} at {found[1]})")


def _extracted(d: Path, psd: Path) -> bool:
    if not (d / kits.META).exists():
        return False
    return kits.read_meta(d).get("fingerprint", {}).get("template") == kits.fingerprint(psd)


def install_cameras(cfg: Config, ws: Workspace, kit: Kit, log=print) -> bool:
    """Every kit's preview cameras into the track set of this kit's replay, if it doesn't hold this kit's as
    posed. Returns whether it wrote them, so the replay needs reopening."""
    track = kits.track_of(kits.profile(kit), cfg)
    mine = cameras.preview_views(kit, ws.policy.preview)
    if not cameras.previews_missing(cfg.documents, track, mine):
        return False
    views = cameras.track_views(ws, cfg, track, ws.policy.preview)
    close = lambda: simhost.close_agent_locked(cfg, "camera.ini has to change", log)  # noqa: E731
    running = cameras.armed(cfg.documents, track, lambda: sim_running(cfg), log, close=close)
    for note in cameras.install_previews(cfg.documents, track, views, running):
        log(note)
    return True


def captured(cfg: Config, ws: Workspace, kit: Kit) -> bool:
    """The kit's current calibration capture was shot of the kit as it is, on its frame and cameras."""
    run = calibrate.current(ws, kit.name)
    m = (state.read_json(run / "manifest.json") if run else None) or {}
    return bool(m.get("complete")) and run is not None and not bake.stale(cfg, kit, run)


def _reviewed(ws: Workspace, kit: Kit) -> bool:
    m = state.read_json(kit_review.out_dir(ws, kit) / "manifest.json")
    return bool(m) and m.get("kit_hash") == kit_hash(kit.dir) and not m.get("failure")


def add(cfg: Config, ws: Workspace, psd: Path, car: str, paint_folder: str, log=print) -> dict:
    """Run every step not yet done; returns what each did and the next step, if it stopped short."""
    again = f"livery kit add {psd} --car {car} --paint-folder {paint_folder}"
    result: dict = {"kit": car, "steps": {}, "next": None}
    steps = result["steps"]
    d = ws.kits / car

    def stop(next_step: str) -> dict:
        result["next"] = next_step
        log(f"next: {next_step}")
        return result

    if _extracted(d, psd):
        steps["extract"] = "done before"
    else:
        extract(ws, psd, car, paint_folder)
        steps["extract"] = f"extracted {d}"
    kit = kits.load(ws, car)
    if kit.paint_folder != paint_folder:
        raise kits.KitError(f"{car} is the kit for {kit.paint_folder}, not {paint_folder}")

    if (d / kits.PROFILE).exists():
        follow_open_replay(cfg, kit, log)
        steps["profile"] = "done before"
    else:
        kits.write_profile(d, skeleton(cfg, ws, kit, log))
        steps["profile"] = f"wrote {d / kits.PROFILE}"

    try:
        for attempt in range(RESIZES + 1):
            wrote = install_cameras(cfg, ws, kit, log)
            steps["cameras"] = "written: reopen the replay" if wrote else "the track's set holds them"
            if wrote:
                log("close and reopen the replay so the sim reads the cameras; the frame pick waits for it")
            if kits.profile(kit).frame is not None:
                steps["frame"] = "done before"
                break
            report = pick_frame.pick(cfg, ws, kit, log)
            if "resized" not in report:
                steps["frame"] = f"frame {report['frame']}"
                break
            steps["frame"] = f"cameras sized for a {report['resized']['length']:.2f} m car"
            if attempt == RESIZES:
                return stop(f"{report['next']}; then {again}")
    except (SimNotReady, cameras.CameraSetError, pick_frame.NoClearFrame) as e:
        return stop(f"{e}; then {again}")

    if kits.read_maps(d).get("side_transforms"):
        steps["capture"] = steps["fit"] = "done before"
    else:
        if captured(cfg, ws, kit):
            steps["capture"] = "done before"
        else:
            try:
                run = calibrate.capture(cfg, ws, kit, log=log)
            except (SimNotReady, calibrate.CaptureFailed) as e:
                return stop(f"{e}; then {again}")
            steps["capture"] = str(run)
            for line in calibrate.report((state.read_json(run / "manifest.json") or {}).get("summary") or {}):
                log(line)
        if not kits.read_meta(d).get("compensation"):
            kit_spec.spec(cfg, ws, kit, log=log)
            steps["spec"] = "compensation written"
            kit = kits.load(ws, car)
        try:
            report = kit_fit.fit(cfg, ws, kit)
        except (LiveryError, FileNotFoundError) as e:
            return stop(f"livery kit fit {car} can't fit the capture: {e}; then {again}")
        steps["fit"] = "fitted"
        result["fit"] = report["summary"]
        kit = kits.load(ws, car)

    if _reviewed(ws, kit):
        steps["review"] = "done before"
    else:
        kit_review.overlay(kit, ws)
        kit_review.body_overlay(kit, ws)
        try:
            steps["review"] = str(kit_review.shoot_calibration(cfg, kit, ws, log))
        except SimNotReady as e:
            return stop(f"{e}; then {again}")
    return stop(f"livery review --kit {car}: {config.owner()} accepts the kit or asks for edits")
