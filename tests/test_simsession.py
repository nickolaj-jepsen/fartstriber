"""The sim rules (CLAUDE.md) against a fake sim: a dict-backed SDK and a capture that draws the car in the
colour of the paint file the last reload loaded."""

import io
import json
import os
import subprocess
import sys
import threading
from dataclasses import replace

import numpy as np
import pytest
from PIL import Image

from livery import capture, config, install, paintfolder, simlock, simsession
from livery.capture import SimInFront
from livery.config import Shot
from livery.iracing import SimNotReady
from livery.preview_sim import preview_sim
from livery.raster import tga_bytes
from tests.conftest import SIM_WINDOW, checked

ALLOWED = {
    "cam_set_state",
    "cam_switch_num",
    "replay_set_play_speed",
    "replay_set_play_position",
    "reload_texture",
}
FOLDER = "tiny"  # the conftest kit's paint folder
SIDE, TOP = Shot(100, "Side"), Shot(100, "Top")
W, H = SIM_WINDOW
STOCK = (60, 60, 60)  # what the car shows without a paint file


def tga(colour) -> bytes:
    return tga_bytes(np.full((8, 8, 3), colour, np.uint8))


class World:
    """The sim: telemetry, session info, what the car shows and the window's state."""

    def __init__(self, cfg: config.Config):
        self.cfg = cfg
        self.folder = cfg.paint_root / FOLDER
        self.stem = f"team_{cfg.team_id}"
        self.vars = {
            "CamCameraState": 0,
            "CamCarIdx": 5,
            "CamGroupNumber": 1,
            "CamCameraNumber": 2,
            "ReplayFrameNum": 400,
            "ReplayPlaySpeed": 1,
            "ReplayPlaySlowMotion": 0,
        }
        groups = [("TV1", 1), ("Side", 10), ("Top", 11)]
        self.info = {
            "WeekendInfo": {
                "SimMode": "replay",
                "SubSessionID": cfg.replay.subsession,
                "TrackName": cfg.replay.track_name,
                "TeamRacing": 1,
            },
            "DriverInfo": {
                "Drivers": [
                    {
                        "UserID": cfg.customer_id,
                        "CarIdx": 3,
                        "CarNumber": "451",
                        "CarPath": FOLDER,
                        "TeamID": cfg.team_id,
                    },
                    {"UserID": 1, "CarIdx": 5, "CarNumber": "9", "CarPath": FOLDER, "TeamID": 0},
                ]
            },
            "CameraInfo": {"Groups": [{"GroupName": g, "GroupNum": n} for g, n in groups]},
        }
        self.running = True
        self.front = self.minimised = self.cursor = self.ui = False
        self.grey_frames = 2  # frames of plain grey a reload passes through
        self.reload_shows = True
        self.sent: list[tuple[str, tuple]] = []
        self.violations: list[str] = []  # broadcasts sent while the sim was in front
        self.logs: list[str] = []
        self.grabs = 0
        self.size = (W, H)  # the window's client size, which the frames have
        self.unsizable: str | None = None  # why the window can't be resized: maximised, fullscreen
        self.takes_size = True
        self.resizes: list[tuple[int, int]] = []
        self.on_grab = lambda n: None
        self.grey_left = 0
        self.shown = self.on_disk()

    def on_disk(self) -> tuple:
        f = self.folder / f"car_{self.stem}.tga"
        if not f.exists():
            return STOCK
        return tuple(int(c) for c in np.asarray(Image.open(io.BytesIO(f.read_bytes())).convert("RGB"))[0, 0])

    def send(self, name: str, *args) -> None:
        if self.front:
            self.violations.append(name)
        self.sent.append((name, args))

    def reload(self) -> None:
        if not self.reload_shows:
            return
        self.grey_left = self.grey_frames
        self.shown = self.on_disk()
        spec = self.folder / f"car_spec_{self.stem}.tga"
        if spec.exists():
            spec.with_suffix(".mip").write_bytes(b"built by the sim")

    def render(self) -> np.ndarray:
        self.grabs += 1
        self.on_grab(self.grabs)
        img = np.zeros((self.size[1], self.size[0], 3), np.uint8)
        img[:] = (self.vars["CamGroupNumber"] * 20 % 256, 120, 200)  # what each camera sees around the car
        img[:, ::7] = 30
        img[30:60, 40:120] = (128, 128, 128) if self.grey_left else self.shown
        self.grey_left = max(0, self.grey_left - 1)
        if self.ui:
            for a, b in simsession.UI_ROWS:
                img[round(H * a) : round(H * b), round(W * 0.1) : round(W * 0.9)] = (25, 27, 36)
        return img


class FakeIR:
    """pyirsdk's reads and only the broadcasts a run may send: anything else is an AttributeError."""

    def __init__(self, world: World):
        self.w = world

    def startup(self):
        return self.w.running

    @property
    def is_connected(self):
        return self.w.running

    def shutdown(self):
        pass

    def __getitem__(self, key):
        return self.w.vars[key] if key in self.w.vars else self.w.info.get(key)

    def cam_set_state(self, camera_state):
        self.w.send("cam_set_state", camera_state)
        self.w.vars["CamCameraState"] = camera_state

    def cam_switch_num(self, car_number, group, camera=0):
        self.w.send("cam_switch_num", car_number, group, camera)
        drivers = self.w.info["DriverInfo"]["Drivers"]
        self.w.vars["CamCarIdx"] = next(d["CarIdx"] for d in drivers if d["CarNumber"] == car_number)
        self.w.vars["CamGroupNumber"], self.w.vars["CamCameraNumber"] = group, camera or 1

    def replay_set_play_speed(self, speed=0, slow_motion=False):
        self.w.send("replay_set_play_speed", speed, slow_motion)
        self.w.vars["ReplayPlaySpeed"], self.w.vars["ReplayPlaySlowMotion"] = speed, int(slow_motion)

    def replay_set_play_position(self, pos_mode, frame_num=0):
        self.w.send("replay_set_play_position", pos_mode, frame_num)
        self.w.vars["ReplayFrameNum"] = frame_num

    def reload_texture(self, car_idx=0):
        self.w.send("reload_texture", car_idx)
        self.w.reload()


class FakeWindow:
    def __init__(self, world: World):
        self.world = world

    def in_front(self):
        return self.world.front

    def minimised(self):
        return self.world.minimised

    def cursor_over(self):
        return self.world.cursor

    def client_size(self):
        return self.world.size

    def unsizable(self):
        return self.world.unsizable

    def set_client_size(self, width, height):
        if self.world.front:
            self.world.violations.append("resize")
        self.world.resizes.append((width, height))
        if self.world.takes_size:
            self.world.size = (width, height)


class FakeCapture:
    def __init__(self, window: FakeWindow):
        self.world = window.world

    def grab(self):
        return self.world.render()

    def close(self):
        pass


@pytest.fixture
def world(tmp_path, monkeypatch) -> World:
    from tests import fakesim  # it imports this module's fakes

    cfg = replace(config.load(), documents=tmp_path / "docs", archive_dir=tmp_path / "archive")
    return fakesim.install(monkeypatch, World(cfg))


def session(w: World, **kw) -> simsession.SimSession:
    kw = {"paint_folder": FOLDER, "groups": ["Side", "Top"], "log": w.logs.append, "ready_timeout": 0, **kw}
    return simsession.SimSession(w.cfg, "test", **kw)


def run(w: World, body=lambda s: None, **kw) -> None:
    with session(w, **kw) as s:
        body(s)


def paint(w: World, colour) -> dict[str, bytes]:
    return {f"car_{w.stem}.tga": tga(colour), f"car_spec_{w.stem}.tga": tga((0, 30, 255))}


def test_the_paint_is_put_back_and_shown_when_a_run_fails(world):
    (world.folder / f"car_{world.stem}.tga").write_bytes(tga((200, 0, 0)))
    world.shown = world.on_disk()

    def body(s):
        s.put(paint(world, (0, 0, 255)))
        s.reload(SIDE)
        assert world.shown == (0, 0, 255)
        raise KeyError("a bug mid-run")

    with pytest.raises(KeyError):
        run(world, body)

    assert world.on_disk() == (200, 0, 0)
    assert not (world.folder / f"car_spec_{world.stem}.tga").exists()
    assert world.shown == (200, 0, 0)  # reloaded, so the sim shows it too
    assert list(paintfolder.journals().glob("*/")) == []


KILLED = """
import sys, time
from dataclasses import replace
from pathlib import Path
from livery import config, paintfolder
cfg = replace(config.load(), documents=Path(sys.argv[1]).parent)
with paintfolder.temporary(cfg, sys.argv[2], "a run that gets killed") as put:
    put({sys.argv[3]: b"preview"})
    print("written", flush=True)
    time.sleep(60)
"""


def test_a_killed_runs_paint_is_put_back_by_the_next_session(world):
    name = f"car_{world.stem}.tga"
    (world.folder / name).write_bytes(tga((200, 0, 0)))
    args = [sys.executable, "-c", KILLED, str(world.cfg.paint_root), FOLDER, name]
    p = subprocess.Popen(args, stdout=subprocess.PIPE, text=True)
    assert p.stdout is not None
    assert p.stdout.readline().strip() == "written"
    p.kill()
    p.wait(timeout=30)
    assert (world.folder / name).read_bytes() == b"preview"

    run(world, sim="none")

    assert world.on_disk() == (200, 0, 0)
    assert list(paintfolder.journals().glob("*/")) == []


# A run in its own process: it says when it queues and when it holds the lock, and holds it until told
# to let go (a line on its stdin).
HOLD = """
import json, sys, time
from livery.simlock import sim_lock
queued = lambda _message: print(json.dumps({"queued": time.time()}), flush=True)
with sim_lock(sys.argv[2], name=sys.argv[1], log=queued) as held:
    start = time.time()
    print("held", flush=True)
    sys.stdin.readline()
    print(json.dumps({"start": start, "end": time.time(), "waited": held.waited}), flush=True)
"""


def _hold(what: str) -> subprocess.Popen:
    args = [sys.executable, "-c", HOLD, simlock.MUTEX, what]
    return subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)


def _said(p: subprocess.Popen) -> str:
    assert p.stdout is not None
    return p.stdout.readline().strip()


def _let_go(p: subprocess.Popen) -> dict:
    return json.loads(p.communicate("\n", timeout=60)[0].splitlines()[-1])


def test_two_runs_at_once_queue_on_the_lock():
    a = _hold("a")
    assert _said(a) == "held"
    b = _hold("b")
    queued = json.loads(_said(b))["queued"]  # b waits for a, whatever the machine's load
    first = _let_go(a)
    assert _said(b) == "held"
    second = _let_go(b)
    assert second["start"] >= first["end"]  # no overlap
    assert second["waited"] >= first["end"] - queued  # b waited from before it queued until a let go


def test_the_lock_is_freed_when_its_holder_dies():
    p = _hold("a run that gets killed")
    assert _said(p) == "held"
    holder = simlock.holder()
    assert holder is not None
    assert holder["what"] == "a run that gets killed"

    def take():
        with simlock.sim_lock("me", timeout=0.3):
            pass

    with pytest.raises(simlock.SimBusy):
        take()

    queued, got = threading.Event(), {}

    def wait():
        with simlock.sim_lock("me", timeout=10, log=lambda _m: queued.set()) as held:
            got.update(abandoned=held.abandoned, holder=simlock.holder())

    waiter = threading.Thread(target=wait)
    waiter.start()
    assert queued.wait(10)
    p.kill()
    p.wait(timeout=30)
    waiter.join(timeout=15)

    assert got["abandoned"]  # a waiter learns that the holder died
    assert got["holder"]["pid"] == os.getpid()
    assert simlock.holder() is None


@pytest.mark.parametrize(
    "change",
    [
        lambda w: w.info["DriverInfo"]["Drivers"][0].update(CarPath="othercar"),
        lambda w: w.info["CameraInfo"]["Groups"].pop(),
        lambda w: w.info["WeekendInfo"].update(SubSessionID=1),
        lambda w: w.info["WeekendInfo"].update(SimMode="full"),
        lambda w: setattr(w, "minimised", True),
        lambda w: setattr(w, "running", False),
    ],
    ids=["another car", "a group missing", "another replay", "not a replay", "minimised", "not running"],
)
def test_a_sim_that_is_not_ready_is_refused_and_left_alone(world, change):
    change(world)

    with pytest.raises(SimNotReady):
        run(world)

    assert world.sent == []
    with simlock.sim_lock("next", timeout=1):  # and the lock is free again
        pass


def test_a_run_waits_for_the_sim_to_get_ready_and_says_what_it_needs_once(world):
    world.running = False
    threading.Timer(0.3, lambda: setattr(world, "running", True)).start()

    run(world, ready_timeout=10)

    assert len(world.logs) == 1


def test_a_run_stops_once_the_sim_comes_to_the_front(world):
    (world.folder / f"car_{world.stem}.tga").write_bytes(tga((200, 0, 0)))

    def body(s):
        s.put(paint(world, (0, 0, 255)))
        s.reload(SIDE)
        world.on_grab = lambda n: setattr(world, "front", True)  # the owner clicks into the sim
        s.shoot("side", SIDE)

    with pytest.raises(SimInFront):
        run(world, body)

    assert world.violations == []  # nothing was sent while the owner had the sim, not even to clean up
    assert world.on_disk() == (200, 0, 0)  # the files are back all the same


def test_a_run_waits_while_the_sim_is_in_front_at_its_start(world):
    world.front = True
    threading.Timer(0.3, lambda: setattr(world, "front", False)).start()

    run(world, lambda s: s.shoot("side", SIDE), ready_timeout=10)

    assert world.violations == []
    assert len(world.logs) == 1  # said once, not once a poll


def test_a_sim_that_stays_in_front_is_never_driven(world):
    world.front = True

    with pytest.raises(SimInFront):
        run(world, ready_timeout=0.1)

    assert world.sent == []


OTHER = (W + 20, H)  # the owner's window size, not the run's


def test_a_run_gives_the_sim_window_its_size_and_puts_the_owners_back(world):
    world.size = OTHER
    shots = []

    run(world, lambda s: shots.append(s.shoot("side", SIDE)), window=(W, H))

    assert shots[0].shape[:2] == (H, W)
    assert world.size == OTHER
    assert world.violations == []


def test_the_owners_window_size_comes_back_when_a_run_fails(world):
    world.size = OTHER

    def body(s):
        s.shoot("side", SIDE)
        raise RuntimeError("the run broke")

    with pytest.raises(RuntimeError):
        run(world, body, window=(W, H))

    assert world.size == OTHER


def test_a_window_that_cant_be_resized_is_refused_and_left_alone(world):
    world.size, world.unsizable = OTHER, "it is maximised"

    with pytest.raises(SimNotReady, match=f"{W}x{H}"):
        run(world, window=(W, H))

    assert world.resizes == []
    assert world.sent == []


def test_a_window_that_doesnt_take_the_size_is_tried_once_and_waited_for(world):
    world.size, world.takes_size = OTHER, False

    with pytest.raises(SimNotReady):
        run(world, window=(W, H), ready_timeout=0.1)

    assert world.resizes == [(W, H)]
    assert world.sent == []


def test_a_window_the_owner_brought_forward_keeps_the_runs_size(world):
    world.size = OTHER

    def body(s):
        s.shoot("side", SIDE)
        world.front = True

    run(world, body, window=(W, H))

    assert world.size == (W, H)
    assert world.violations == []


def test_a_window_resized_during_a_run_stops_it_and_keeps_the_new_size(world):
    world.size = OTHER

    def body(s):
        s.shoot("side", SIDE)
        world.size = (W + 40, H)
        s.shoot("top", TOP)

    with pytest.raises(capture.CaptureError):
        run(world, body, window=(W, H))

    assert world.size == (W + 40, H)
    assert world.resizes == [(W, H)]


def test_a_preview_never_shoots_at_another_window_size(world, ws):
    built(ws)
    world.size, world.unsizable = OTHER, "it is fullscreen"

    with pytest.raises(SimNotReady):
        preview_sim(world.cfg, ws, "d", log=world.logs.append)

    assert world.grabs == 0
    assert list(world.folder.iterdir()) == []


def test_the_sim_is_left_as_it_was_found(world):
    before = dict(world.vars)

    def body(s):
        s.put(paint(world, (0, 0, 255)))
        s.reload(SIDE)
        s.shoot("side", SIDE)
        s.shoot("top", TOP)

    run(world, body)

    assert world.vars == before
    assert {name for name, _ in world.sent} <= ALLOWED
    assert world.shown == STOCK


def test_a_reload_without_a_grey_step_counts_once_the_car_changed(world):
    world.grey_frames = 0

    run(world, lambda s: (s.put(paint(world, (0, 0, 255))), s.reload(SIDE)))


def test_a_reload_that_never_shows_fails(world):
    world.reload_shows = False

    with pytest.raises(SimNotReady):
        run(world, lambda s: (s.put(paint(world, (0, 0, 255))), s.reload(SIDE)))


def test_a_paint_near_the_loading_grey_needs_no_visible_change(world):
    world.reload_shows = False

    run(world, lambda s: (s.put(paint(world, (128, 128, 128))), s.reload(SIDE)))


def test_a_shot_with_the_replay_ui_over_the_car_is_taken_again_then_refused(world):
    run(world, lambda s: s.shoot("side", SIDE, check_ui=True))  # clean: the view's reference
    world.ui = True

    with pytest.raises(simsession.UIOverCar):
        run(world, lambda s: s.shoot("side", SIDE, check_ui=True))

    def cursor_leaves(_message):
        world.ui = False

    run(world, lambda s: s.shoot("side", SIDE, check_ui=True), log=cursor_leaves)  # gone by the retake


def test_without_a_reference_a_shot_is_refused_while_the_cursor_is_over_the_sim(world):
    world.cursor = True

    with pytest.raises(simsession.UIOverCar):
        run(world, lambda s: s.shoot("side", SIDE, check_ui=True))

    world.cursor = False
    run(world, lambda s: s.shoot("side", SIDE, check_ui=True))
    world.ui = world.cursor = True
    with pytest.raises(simsession.UIOverCar):  # the clean shot is now the reference
        run(world, lambda s: s.shoot("side", SIDE, check_ui=True))


def built(ws) -> dict:
    """Design d with a passed check, without running the build."""
    return checked(ws, "d", outputs={"car.tga": tga((18, 52, 86)), "car_spec.tga": tga((0, 30, 255))})


def test_preview_shoots_every_preset_and_records_the_build(world, ws):
    check = built(ws)
    before = dict(world.vars)

    out = preview_sim(world.cfg, ws, "d", log=world.logs.append)

    assert sorted(p.name for p in out.glob("*.png")) == sorted(f"{n}.png" for n in ws.policy.preview)
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["digests"]["car.tga"] == check["digests"]["car.tga"]
    assert manifest["failure"] is None
    assert list(world.folder.iterdir()) == []
    assert world.vars == before


def test_a_preview_that_stops_leaves_no_run_where_the_critic_looks(world, ws):
    built(ws)
    world.on_grab = lambda n: setattr(world, "front", n > 12)

    with pytest.raises(SimInFront):
        preview_sim(world.cfg, ws, "d", log=world.logs.append)

    assert not (ws.out("d") / "sim").exists()
    [partial] = (ws.out("d") / "sim-partial").iterdir()
    assert json.loads((partial / "manifest.json").read_text())["failure"]


def test_install_and_restore_write_the_folder_only_under_the_lock(world, ws, monkeypatch):
    built(ws)
    held = []
    for fn in ("install", "restore"):
        real = getattr(paintfolder, fn)
        monkeypatch.setattr(
            paintfolder, fn, lambda *a, real=real, **k: (held.append(simlock.holder()), real(*a, **k))[1]
        )
    monkeypatch.setattr(capture, "processes", lambda: ["Trading Paints.exe"])

    install.install(world.cfg, ws, "d", log=world.logs.append, unapproved=True)

    assert install.TP_WARNING in world.logs
    assert ("reload_texture", (3,)) in world.sent
    assert (world.folder / f"car_{world.stem}.tga").exists()
    assert not (world.folder / f"car_spec_{world.stem}.tga").exists()  # its .mip was built and archived
    assert list(world.cfg.archive_dir.rglob("*.mip"))

    install.restore(world.cfg, log=world.logs.append)

    assert list(world.folder.iterdir()) == []
    assert len(held) == 2
    assert [h and h["pid"] for h in held] == [os.getpid()] * 2


def test_status_only_reads(world, ws):
    assert simsession.status(world.cfg, ws)
    assert world.sent == []


class Recorder:
    def __init__(self):
        self.calls: list[str] = []

    def __getattr__(self, name):
        def call(*_args):
            self.calls.append(name)
            return 0

        return call


def test_the_window_is_only_ever_read(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(capture, "user32", rec)
    monkeypatch.setattr(capture, "dwmapi", rec)
    w = capture.Window(1234)

    w.in_front()
    w.minimised()
    w.cursor_over()
    w.client_box(100, 50)
    w.unsizable()

    reads = {
        "IsZoomed",
        "GetWindowLongW",
        "MonitorFromWindow",
        "GetMonitorInfoW",
        "GetForegroundWindow",
        "GetWindowThreadProcessId",
        "IsIconic",
        "GetCursorPos",
        "WindowFromPoint",
        "GetClientRect",
        "ClientToScreen",
        "GetWindowRect",
        "DwmGetWindowAttribute",
    }
    assert rec.calls
    assert set(rec.calls) <= reads


class Desktop:
    """user32 and dwmapi for one window on a 2560x1440 monitor, with a frame 8 px wide and a 31 px title bar."""

    FRAME = (8, 31, 8, 8)  # left, top, right, bottom

    def __init__(self, x=100, y=100, w=1600, h=900, style=capture.WS_CAPTION, zoomed=False):
        self.pos, self.client, self.style, self.zoomed = (x, y), (w, h), style, zoomed
        self.flags: list[int] = []

    @staticmethod
    def _fill(rect, l, t, r, b):  # noqa: E741
        rect.left, rect.top, rect.right, rect.bottom = l, t, r, b

    def _outer(self, ref):
        (x, y), (w, h), (fl, ft, fr, fb) = self.pos, self.client, self.FRAME
        self._fill(ref._obj, x, y, x + fl + w + fr, y + ft + h + fb)
        return 1

    def GetClientRect(self, hwnd, ref):
        self._fill(ref._obj, 0, 0, *self.client)
        return 1

    def GetWindowRect(self, hwnd, ref):
        return self._outer(ref)

    def DwmGetWindowAttribute(self, hwnd, attr, ref, size):
        self._outer(ref)
        return 0

    def SetWindowPos(self, hwnd, after, x, y, cx, cy, flags):
        self.flags.append(flags)
        fl, ft, fr, fb = self.FRAME
        self.client = (cx - fl - fr, cy - ft - fb)
        return 1

    def GetWindowThreadProcessId(self, hwnd, ref):
        return 1

    def IsIconic(self, hwnd):
        return 0

    def IsZoomed(self, hwnd):
        return int(self.zoomed)

    def GetWindowLongW(self, hwnd, index):
        return self.style

    def MonitorFromWindow(self, hwnd, flags):
        return 1

    def GetMonitorInfoW(self, monitor, ref):
        self._fill(ref._obj.rcMonitor, 0, 0, 2560, 1440)
        return 1


def on_desktop(monkeypatch, **kw) -> tuple[Desktop, capture.Window]:
    d = Desktop(**kw)
    monkeypatch.setattr(capture, "user32", d)
    monkeypatch.setattr(capture, "dwmapi", d)
    return d, capture.Window(1)


def test_a_resize_keeps_the_window_where_it_is_and_behind_other_windows(monkeypatch):
    d, w = on_desktop(monkeypatch)

    assert w.unsizable() is None
    w.set_client_size(1780, 992)

    assert w.client_size() == (1780, 992)
    assert d.pos == (100, 100)
    no_move, no_zorder, no_activate, no_owner_zorder = 0x0002, 0x0004, 0x0010, 0x0200
    assert d.flags
    assert all(f & no_move and f & no_zorder and f & no_activate and f & no_owner_zorder for f in d.flags)


@pytest.mark.parametrize(
    "kw",
    [{"zoomed": True}, {"style": 0}, {"x": 0, "y": 0, "w": 2544, "h": 1401}],
    ids=["maximised", "borderless", "fullscreen"],
)
def test_only_a_normal_window_may_be_resized(monkeypatch, kw):
    _d, w = on_desktop(monkeypatch, **kw)

    assert w.unsizable()


def test_a_window_may_grow_past_its_monitor(monkeypatch):
    d, w = on_desktop(monkeypatch, x=1500, y=800)

    assert w.unsizable() is None
    w.set_client_size(1920, 1080)

    assert w.client_size() == (1920, 1080)
    assert d.pos == (1500, 800)
