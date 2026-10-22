"""`livery kit add` from a synthetic paint template to the kit review, against the fake sim of
test_pick_frame, which reads the track's camera set whenever the replay is asked for its groups (as if
the owner reopened it at once). The calibration capture and the fit have tests of their own; here they are
stand-ins that record being called."""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from psd_tools import PSDImage
from psd_tools.api.layers import Group

from livery import calibrate, cameras, config, kit_add, state
from livery import kit as kits
from livery.config import REPO, Workspace, track_path
from livery.hashing import kit_hash
from tests import fakesim
from tests.conftest import OWNER
from tests.test_cameras import STOCK_TRACK, track_camera_set
from tests.test_pick_frame import Lap, LapIR

N = 128
CAR, FOLDER = "synth", "synthcar"
ISLANDS = ((8, 40), (52, 76), (88, 120))  # rows of three islands, each across the canvas


def template(path: Path) -> Path:
    """A paint template with the layers kit extract reads: three islands, a number block and a sponsor
    block."""
    psd = PSDImage.new("RGB", (N, N), color=(83, 71, 65))
    helpers = Group.new(psd, name="Turn Off Before Exporting TGA")
    mask = np.zeros((N, N, 4), np.uint8)
    mask[...] = (83, 71, 65, 255)
    wire = np.zeros((N, N, 4), np.uint8)
    for y0, y1 in ISLANDS:
        mask[y0:y1, 8:120, 3] = 0
        for edge in (
            wire[y0 - 1, 7:121],
            wire[y1, 7:121],
            wire[y0 - 1 : y1 + 1, 7],
            wire[y0 - 1 : y1 + 1, 120],
        ):
            edge[...] = (1, 255, 0, 255)
    numbers = np.zeros((N, N, 4), np.uint8)
    numbers[20:30, 50:70] = (236, 0, 140, 255)
    sponsors = np.zeros((N, N, 4), np.uint8)
    sponsors[100:110, 20:40] = (0, 166, 81, 255)
    for name, layer in (
        ("Mask", mask),
        ("Wire", wire),
        ("Number Blocks", numbers),
        ("Sponsor Blocks", sponsors),
    ):
        helpers.create_pixel_layer_from_array(layer, name=name)
    psd.save(path)
    return path


class ReopenedIR(LapIR):
    """The replay's camera groups are those of its track's set on disk."""

    def __getitem__(self, key):
        if key == "CameraInfo":
            track = track_path(self.w.info["WeekendInfo"]["TrackName"])
            _recs, groups = cameras.track_set(cameras.track_cam(self.w.cfg.documents, track).read_bytes())
            self.w.info["CameraInfo"] = {
                "Groups": [{"GroupName": g, "GroupNum": i} for i, (_f, g, _c) in enumerate(groups, start=1)]
            }
        return super().__getitem__(key)


def saved_set(docs: Path, track: str) -> None:
    """The track's camera set as the owner saves it from the sim, which then points camera.ini at it."""
    cam = cameras.track_cam(docs, track)
    cam.parent.mkdir(parents=True)
    cam.write_bytes(track_camera_set(STOCK_TRACK))
    ini = docs / "camera.ini"
    was = ini.read_text(encoding="utf-8") if ini.exists() else ""
    ini.write_text(f"{was}[tracks\\{track}]\nLastTrackSet=tracks\\{track}\\track.cam\n", encoding="utf-8")


@pytest.fixture
def captures(monkeypatch) -> list[str]:
    """A stand-in for the calibration capture: a finished run of the kit as it is."""
    shot: list[str] = []

    def capture(cfg, ws, kit, *, resume=False, keep_frames=False, log=print):
        run = calibrate.root(ws, kit.name) / f"run{len(shot)}"
        ident = calibrate.identity(cfg, kit, kits.profile(kit))
        state.write_json(run / "manifest.json", {"identity": ident, "complete": True})
        (run.parent / "current").write_text(run.name, encoding="utf-8")
        shot.append(kit.name)
        return run

    monkeypatch.setattr(kit_add.calibrate, "capture", capture)
    monkeypatch.setattr(kit_add.kit_spec, "spec", lambda cfg, ws, kit, log=print: {})
    return shot


@pytest.fixture
def setup(tmp_path, monkeypatch, captures) -> tuple[Workspace, Lap, Path]:
    cfg = replace(config.load(), documents=tmp_path / "docs", archive_dir=tmp_path / "archive")
    saved_set(cfg.documents, cfg.replay.track)
    world = Lap(cfg)
    fakesim.install(monkeypatch, world, ReopenedIR)
    fakesim.drive(world, FOLDER)
    ws = Workspace(root=tmp_path / "w", assets=REPO / "assets", ui_cars_dir=tmp_path / "ui")
    return ws, world, template(tmp_path / "car.psd")


def test_a_new_car_goes_from_its_template_to_its_capture_once_and_stops_where_the_fit_cant_follow(
    setup, captures
):
    ws, world, psd = setup

    result = kit_add.add(world.cfg, ws, psd, CAR, FOLDER, log=world.logs.append)

    kit = kits.load(ws, CAR)
    assert len(kits.read_maps(kit.dir)["regions"]) == len(ISLANDS)
    prof = kits.profile(kit)
    assert prof.frame is not None
    assert prof.replay == world.cfg.replay.subsession
    assert cameras.previews_missing(world.cfg.documents, world.cfg.replay.track, prof.preview) == []
    assert all(
        v.group.startswith(CAR) for v in prof.preview.values()
    )  # groups of its own, beside the stock ones
    assert captures == [CAR]
    assert "kit fit" in result["next"]  # the fake capture has no geometry to fit from
    assert not list(world.folder.iterdir())

    sent = len(world.sent)
    again = kit_add.add(world.cfg, ws, psd, CAR, FOLDER, log=world.logs.append)

    assert {again["steps"][s] for s in ("extract", "profile", "frame", "capture")} == {"done before"}
    assert again["next"] == result["next"]
    assert len(world.sent) == sent  # nothing shot again
    assert captures == [CAR]


def test_a_new_car_on_an_open_replay_of_its_own_is_framed_there(setup):
    ws, world, psd = setup
    world.info["WeekendInfo"].update(SubSessionID=777, TrackName="test track")  # its own replay, same track

    kit_add.add(world.cfg, ws, psd, CAR, FOLDER, log=world.logs.append)

    prof = kits.profile(kits.load(ws, CAR))
    assert (prof.replay, prof.track_name) == (777, "test track")


def test_a_car_not_framed_yet_follows_the_replay_now_open_and_its_track(setup):
    ws, world, psd = setup
    world.fences += [("Top", range(1500)), ("Side", range(1500, 3000))]
    kit_add.add(world.cfg, ws, psd, CAR, FOLDER, log=world.logs.append)
    assert kits.profile(kits.load(ws, CAR)).frame is None  # no frame shows the car clear of the fences
    world.fences.clear()
    world.info["WeekendInfo"].update(SubSessionID=888, TrackName="other track")
    saved_set(world.cfg.documents, "other\\track")

    kit_add.add(world.cfg, ws, psd, CAR, FOLDER, log=world.logs.append)

    prof = kits.profile(kits.load(ws, CAR))
    assert (prof.replay, prof.track_name) == (888, "other track")
    assert prof.frame is not None
    assert cameras.previews_missing(world.cfg.documents, "other\\track", prof.preview) == []


def test_once_measured_it_fits_and_shoots_the_kit_review_then_waits_for_the_owner(setup, monkeypatch):
    ws, world, psd = setup
    shot = []

    def fit(cfg, ws, kit):
        maps = kits.read_maps(kit.dir)
        kits.write(kit.dir, maps={**maps, "side_transforms": {"i001": {"view": "side_l"}}})
        return {"summary": {}}

    def review(cfg, kit, ws, log=print):
        shot.append(kit.name)
        out = ws.cache / "kit-review" / kit.name
        state.write_json(out / "manifest.json", {"kit_hash": kit_hash(kit.dir), "failure": None})
        return out

    monkeypatch.setattr(kit_add.kit_fit, "fit", fit)
    monkeypatch.setattr(kit_add.kit_review, "shoot_calibration", review)

    result = kit_add.add(world.cfg, ws, psd, CAR, FOLDER, log=world.logs.append)

    assert result["steps"]["fit"] == "fitted"
    assert shot == [CAR]
    assert "livery review --kit" in result["next"]
    assert OWNER in result["next"]
    assert (
        kit_add.add(world.cfg, ws, psd, CAR, FOLDER, log=world.logs.append)["steps"]["review"]
        == "done before"
    )
    assert shot == [CAR]
