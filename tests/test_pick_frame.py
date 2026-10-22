"""`livery sim pick-frame` against a fake sim whose replay is one lap: straights and turns in its telemetry,
a fence in front of some cameras on some frames, and the car drawn at the size its cameras give it."""

import math
from dataclasses import replace

import irsdk
import numpy as np
import pytest

from livery import cameras, config, pick_frame
from livery import kit as kits
from livery.kit import load
from tests import fakesim
from tests.conftest import frame_kit
from tests.test_simsession import ALLOWED, STOCK, FakeIR, H, W, World

TOTAL = 3000  # frames in the replay: one lap
PITS = range(0, 300)
STRAIGHTS = (range(600, 900), range(1800, 2100))
# Of the frame the McLaren-sized car spans in a side view at its default pose.
SHARE = cameras.REF.length / (2 * cameras.D * math.tan(cameras.FOV / 2))
ORIGIN = (0.0, 0.0, 0.0)


class Lap(World):
    """A replay of one lap. The car fills `share` of the frame's width, centred; `fences` hide part of it
    from the camera groups whose names hold a word, on a range of frames."""

    def __init__(self, cfg: config.Config):
        super().__init__(cfg)
        self.vars["ReplayFrameNum"] = 400
        self.share = SHARE
        self.fences: list[tuple[str, range]] = []
        self.from_group: int | None = None  # the group the camera was on before the last switch
        self.order_matters = False  # the top view frames the car differently after another group
        self.yaw_known = True

    def group(self) -> str:
        num = self.vars["CamGroupNumber"]
        return next(g["GroupName"] for g in self.info["CameraInfo"]["Groups"] if g["GroupNum"] == num)

    def telemetry(self, key: str):
        f = self.vars["ReplayFrameNum"]
        on = f not in PITS
        straight = any(f in s for s in STRAIGHTS)
        per_car = {
            "CarIdxLapDistPct": f / TOTAL,
            "CarIdxTrackSurface": irsdk.TrkLoc.on_track if on else irsdk.TrkLoc.in_pit_stall,
            "CarIdxOnPitRoad": not on,
            "CarIdxSteer": 0.0 if straight else 0.2,
        }
        if key in per_car:
            values = [-1] * 64
            values[3] = per_car[key]
            return values
        return {
            "ReplayFrameNumEnd": TOTAL - f,
            "PlayerCarIdx": 3,
            "YawRate": (0.0 if straight else 0.4) if self.yaw_known else None,
            "Speed": 50.0,
        }[key]

    def render(self) -> np.ndarray:
        self.grabs += 1
        self.on_grab(self.grabs)
        img = np.zeros((H, W, 3), np.uint8)
        img[:] = (self.vars["CamGroupNumber"] * 20 % 256, 120, 200)
        img[:, ::7] = 30
        length = round(W * self.share)
        height = round(W * self.share * cameras.REF.height / cameras.REF.length)
        x0, y0 = (W - length) // 2, (H - height) // 2
        if self.order_matters and self.group() == "Top" and self.from_group != self.vars["CamGroupNumber"]:
            x0 += 3
        img[y0 : y0 + height, max(0, x0) : x0 + length] = (128, 128, 128) if self.grey_left else self.shown
        self.grey_left = max(0, self.grey_left - 1)
        for group, frames in self.fences:
            if group in self.group() and self.vars["ReplayFrameNum"] in frames:
                img[:, W // 2 - 4 : W // 2 + 4] = (90, 60, 40)  # a post in front of the car
        return img


class LapIR(FakeIR):
    TELEMETRY = (
        "CarIdxLapDistPct",
        "CarIdxTrackSurface",
        "CarIdxOnPitRoad",
        "CarIdxSteer",
        "ReplayFrameNumEnd",
        "PlayerCarIdx",
        "YawRate",
        "Speed",
    )

    def __getitem__(self, key):
        if key in self.TELEMETRY:
            assert isinstance(self.w, Lap)
            return self.w.telemetry(key)
        return super().__getitem__(key)

    def freeze_var_buffer_latest(self):
        pass

    def unfreeze_var_buffer_latest(self):
        pass

    def cam_switch_num(self, car_number, group, camera=0):
        assert isinstance(self.w, Lap)
        self.w.from_group = self.w.vars["CamGroupNumber"]
        super().cam_switch_num(car_number, group, camera)


@pytest.fixture
def lap(tmp_path, monkeypatch) -> Lap:
    cfg = replace(config.load(), documents=tmp_path / "docs", archive_dir=tmp_path / "archive")
    world = Lap(cfg)
    fakesim.install(monkeypatch, world, LapIR)
    return world


@pytest.fixture
def tiny(ws):
    """The tiny kit with a group per preview view, framed on no frame yet."""
    kit = load(ws, "tiny")
    frame_kit(ws, kit.dir, frame=None)
    return kit


def test_the_straightest_frame_every_camera_sees_clearly_is_written_with_the_replay(lap, ws, tiny):
    lap.fences.append(("Side", STRAIGHTS[1]))  # the second straight runs along a fence
    before = dict(lap.vars)

    report = pick_frame.pick(lap.cfg, ws, tiny, log=lap.logs.append)

    prof = kits.profile(tiny)
    assert prof.frame is not None
    assert prof.frame in STRAIGHTS[0]
    middle = (STRAIGHTS[0].start + STRAIGHTS[0].stop) / 2
    assert abs(prof.frame - middle) < len(STRAIGHTS[0]) / 4  # away from the turns at either end
    assert prof.replay == lap.cfg.replay.subsession
    assert prof.track_name is None  # the user's preview replay
    assert set(prof.crops) == set(pick_frame.CROPS)
    assert report["frame"] == prof.frame
    assert not list(lap.folder.iterdir())  # the paint folder as it was
    assert lap.vars == before  # the replay and camera put back
    assert {name for name, _ in lap.sent} <= ALLOWED
    assert lap.shown == STOCK  # and the sim shows the car's own paint again


def test_without_a_turn_rate_the_frame_checks_still_pick_a_clear_frame(lap, ws, tiny):
    lap.yaw_known = False
    lap.fences.append(("Top", range(0, 1500)))

    pick_frame.pick(lap.cfg, ws, tiny, log=lap.logs.append)

    frame = kits.profile(tiny).frame
    assert frame is not None
    assert frame >= 1500
    assert frame not in PITS


def test_a_frame_on_another_replay_records_that_replay_and_its_track(lap, ws, tiny):
    lap.info["WeekendInfo"].update(SubSessionID=999, TrackName="other track")

    pick_frame.pick(lap.cfg, ws, tiny, log=lap.logs.append)

    prof = kits.profile(tiny)
    assert (prof.replay, prof.track_name) == (999, "other track")
    replay = kits.require_replay(prof, lap.cfg, tiny)
    assert replay.track == "other\\track"
    assert kits.track_of(prof, lap.cfg) == "other\\track"


def test_with_no_clear_frame_nothing_is_written(lap, ws, tiny):
    lap.fences += [("Top", range(TOTAL // 2)), ("Side", range(TOTAL // 2, TOTAL))]
    cameras.preview_views(tiny, ws.policy.preview)  # the default poses, which any camera command fills in
    before = (tiny.dir / kits.PROFILE).read_text()

    with pytest.raises(pick_frame.NoClearFrame):
        pick_frame.pick(lap.cfg, ws, tiny, log=lap.logs.append)

    assert (tiny.dir / kits.PROFILE).read_text() == before
    assert not list(lap.folder.iterdir())


def test_a_smaller_car_gets_nearer_cameras_that_frame_it_as_the_mclarens_frame_it(lap, ws, tiny):
    lap.share = 0.6
    was = cameras.preview_views(tiny, ws.policy.preview)

    report = pick_frame.pick(lap.cfg, ws, tiny, log=lap.logs.append)

    prof = kits.profile(tiny)
    assert "resized" in report
    assert prof.frame is None  # picked once the sim shows the new cameras
    assert prof.car is not None
    side = prof.preview["side_l"]
    assert abs(side.pose[1]) < abs(was["side_l"].pose[1])
    assert side.group != was["side_l"].group  # a new group: the sim lists it only once it read the new set
    spans = prof.car.length / (2 * abs(side.pose[1]) * np.tan(side.pose[5] / 2))
    assert spans == pytest.approx(cameras.REF.length / (2 * cameras.D * np.tan(cameras.FOV / 2)), rel=0.05)


def test_a_car_that_runs_off_the_frame_gets_cameras_farther_out(lap, ws, tiny):
    lap.share = 0.99
    was = cameras.preview_views(tiny, ws.policy.preview)

    pick_frame.pick(lap.cfg, ws, tiny, log=lap.logs.append)

    prof = kits.profile(tiny)
    assert prof.frame is None
    assert all(
        math.dist(prof.preview[v].pose[:3], ORIGIN) > math.dist(was[v].pose[:3], ORIGIN)
        for v in ws.policy.preview
    )


def test_the_check_passes_when_every_view_repeats_whatever_came_before(lap, ws, tiny):
    kits.write_profile(tiny.dir, replace(kits.profile(tiny), frame=700))

    report = pick_frame.check(lap.cfg, ws, tiny, log=lap.logs.append)

    assert report["passed"]
    assert set(report["views"]) == set(ws.policy.preview)
    assert "reload_texture" not in {name for name, _ in lap.sent}  # it paints nothing


def test_the_check_fails_when_a_view_depends_on_the_one_before(lap, ws, tiny):
    kits.write_profile(tiny.dir, replace(kits.profile(tiny), frame=700))
    lap.order_matters = True

    report = pick_frame.check(lap.cfg, ws, tiny, log=lap.logs.append)

    assert not report["passed"]
    assert report["views"]["top"] > pick_frame.REPEAT_PCT
