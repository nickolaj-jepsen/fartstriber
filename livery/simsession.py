"""One sim run. Every command that drives the sim or writes its paint folder goes through a `SimSession`.

Entering takes the sim lock, puts back the paint of a run that died, waits until the sim is ready (the
preview replay, the kit's car, the camera groups) and behind other windows, gives its window the run's client
size if it is a normal window that fits, and notes the camera and replay state. Leaving, on errors too, puts
the paint back, reloads so the sim shows it, puts the camera, UI, replay and window size back as they were,
writes the run's manifest and releases the lock. While it runs, every broadcast and
every frame first checks that the sim is still behind other windows: once the owner brings it forward, the
run stops rather than drive it under their hands.
"""

import hashlib
import io
import json
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Literal

import irsdk
import numpy as np
from PIL import Image

from livery import cameras, capture, paintfolder, presence, simhost, simlock, simrule, state
from livery import kit as kits
from livery.capture import CaptureError, SimInFront
from livery.config import Config, ConfigError, Replay, Shot, Workspace, sha256, track_path
from livery.iracing import Car, Sim, SimNotReady, sim_running, trading_paints_running, wait_for

READY_WAIT_S = 1800.0  # how long a run waits for the sim to be ready and behind other windows
POLL_S = 1.0
SETTLE_S = 0.6  # after a camera switch, before the scene is steady enough to compare frames
GREY_S = 8.0  # a reload turns the car grey within 0.6-0.8 s (docs/sim.md); slower while the sim is busy
PAINT_S = 5.0  # and shows the new paint 0.6-1.8 s later, about 3 s with a new spec map
LOAD_S = 3.0  # waited out when no grey shows
STEADY_S = 5.0
CHANGED = 0.5  # percent of pixels that changed clearly: a reload's step
SEEK_S = 15.0
# The replay UI: a session bar along the top and the driver and replay controls along the bottom, as shares
# of the window. A shot whose bands differ from the same view's clean shot has the UI over it.
UI_ROWS = ((0.02, 0.12), (0.87, 0.97))
UI_COLS = (0.12, 0.88)
UI_SHARE = 25.0  # percent of the bands' pixels that changed clearly
UI_RETRY_S = 3.0
CLOSING_POLL_S = 0.5  # how often the guard reads whether the agent-started sim is closing
PREVIEW_GROUPS_FIX = "run livery preview cameras and reopen the replay"


class UIOverCar(CaptureError):
    pass


class SimClosing(SimNotReady):
    """The agent-started sim has to close at once (a full-screen game in front, say): the run stops."""


def near_grey(tga: bytes | None) -> bool:
    """A paint close to the plain grey a reload passes through, which barely changes the frame."""
    if tga is None:
        return False
    a = np.asarray(Image.open(io.BytesIO(tga)).convert("RGB").reduce(32), np.float32)
    return float(a.std()) < 24 and float(np.abs(a - a.mean(axis=2, keepdims=True)).mean()) < 8


def ui_bands(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    x0, x1 = round(w * UI_COLS[0]), round(w * UI_COLS[1])
    return np.concatenate([img[round(h * a) : round(h * b), x0:x1] for a, b in UI_ROWS])


def fresh_name(*parents: Path) -> str:
    """A run folder name, by time, that none of `parents` holds yet."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name, n = stamp, 1
    while any((p / name).exists() for p in parents):
        n += 1
        name = f"{stamp}-{n}"
    return name


class SimSession:
    """`with SimSession(cfg, "preview x", paint_folder=..., groups=...) as s:`, then `s.put`, `s.reload`
    and `s.shoot`.

    `sim` says what the run needs: "replay" waits for `replay` (the user's preview replay unless given) with
    the kit's car and `groups`; "any-replay" for any replay with them; "optional" uses any session with the
    kit's car if one is open and behind other windows (else `sim` is None and `unavailable` says why); "none"
    only takes the lock and puts back a dead run's paint. A run that shoots names `window`, the client size
    every frame must have (policy.toml); one that only reloads leaves it None.
    """

    def __init__(
        self,
        cfg: Config,
        what: str,
        *,
        paint_folder: str | None = None,
        groups: Iterable[str] = (),
        groups_fix: str = PREVIEW_GROUPS_FIX,
        sim: Literal["replay", "any-replay", "optional", "none"] = "replay",
        replay: Replay | None = None,
        window: tuple[int, int] | None = None,
        log: Callable[[str], object] = print,
        lock_timeout: float = 1800,
        ready_timeout: float | None = None,
        checkout: Path | None = None,
    ):
        self.cfg, self.what, self.paint_folder, self.mode, self.log = cfg, what, paint_folder, sim, log
        self.replay = replay or cfg.replay
        self.groups, self.groups_fix = sorted(set(groups)), groups_fix
        self.size = window
        self.lock_timeout = lock_timeout
        self.ready_timeout = READY_WAIT_S if ready_timeout is None else ready_timeout
        self.out: Path | None = None  # where the manifest goes; set it once the sim is ours
        self.manifest: dict = {
            "what": what,
            "checkout": None if checkout is None else str(checkout),
            "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        self.timings: dict[str, float | None] = {}
        self.sim: Sim | None = None
        self.car: Car | None = None
        self.window: Any = None
        self.unavailable: str | None = None
        self._lock: Any = None
        self._temp: Any = None
        self._put: Callable[[dict[str, bytes]], None] | None = None
        self._capture: Any = None
        self._snapshot: dict | None = None
        self._groups: dict[str, int] = {}
        self._size: tuple[int, int] | None = None
        self._resized: tuple[Any, tuple[int, int]] | None = None  # the window this run resized, and its size
        self._touched = False  # sent a camera, replay or UI broadcast
        self._differs = False  # wrote paint other than what the folder held
        self._pending = False  # paint written since the last reload differs from what the sim shows
        self._paint: bytes | None = None  # the last paint TGA written
        self._camset: str | None = None
        self._want: Any = None
        self._agent = False  # the sim is one an agent started (`livery.simhost`)
        self._front_input: int | None = None  # the owner's last input, once an agent's sim in front is ok
        self._rules_read: tuple[simrule.Launch | None] | None = None
        self._closed_agent = False
        self._closing: tuple[float, tuple[str, bool] | None] = (0.0, None)

    # Entering.

    def __enter__(self) -> "SimSession":
        if self.mode in ("replay", "any-replay"):
            self._want = simhost.want(self.what)
            self._want.__enter__()
        try:
            self._lock = simlock.sim_lock(self.what, self.lock_timeout, self.log)
            held = self._lock.__enter__()
        except BaseException:
            if self._want is not None:
                self._want.__exit__(None, None, None)
            raise
        try:
            self.timings["lock_wait"] = held.waited
            if held.abandoned:
                self.log("the last sim run died holding the sim")
            recovered = paintfolder.recover()
            for note in recovered:
                self.log(note)
            if recovered:
                self.manifest["recovered"] = recovered
                self._differs = True  # the sim may still show the dead run's paint
            t = time.perf_counter()
            if self.mode != "none":
                self._connect()
            self.timings["connect"] = time.perf_counter() - t
        except BaseException:
            problems: list[str] = []
            self._close(problems)
            for p in problems:
                self.log(p)
            raise
        return self

    def _problem(self, sim: Sim) -> str | None:
        """What keeps the sim from this run, or None."""
        if self.mode in ("replay", "any-replay"):
            simhost.adopt()  # a dead host's sim still closes once unwanted
        replay = self.replay.label if self.mode != "any-replay" else f"a replay with {self.paint_folder}"
        if not sim.connected():
            return f"iRacing isn't running: start it and open {replay}"
        if self.mode in ("replay", "any-replay"):
            if not sim.is_replay():
                return f"the sim isn't in a replay: open {replay}"
            wrong = sim.wrong_replay(self.replay) if self.mode == "replay" else None
            if wrong:
                return wrong
        try:
            car = sim.car()
        except SimNotReady as e:
            return str(e)
        if self.paint_folder and car.path != self.paint_folder:
            return f"the session's car is {car.path}; this run is for {self.paint_folder}"
        missing = [g for g in self.groups if g not in sim.groups]
        if missing:
            return f"the replay has no camera group {', '.join(missing)}; {self.groups_fix}"
        self.window = capture.Window.find(self.cfg.process)
        if self.window is None:
            return f"no {self.cfg.process} window"
        if self.window.minimised():
            return "the sim window is minimised; restore it (behind other windows is fine)"
        self._agent = simhost.is_agent(getattr(self.window, "pid", None))
        if self._agent and (c := simhost.closing()) is not None:
            return f"the agent-started sim is closing: {c[0]}"
        if self.window.in_front() and not self._front_ok():
            return "in front"
        if self.size is not None and (now := self.window.client_size()) != self.size:
            return self._resize(now, self.size)
        return None

    def _resize(self, now: tuple[int, int], size: tuple[int, int]) -> str | None:
        """Give the sim window the run's client size, once a run, and only while it is a normal window behind
        other windows; else what keeps it from that size."""
        want = f"{size[0]}x{size[1]}"
        wrong = f"the sim window is {now[0]}x{now[1]}; previews need {want}"
        why = self.window.unsizable()
        if why is not None:
            return f"{wrong}, and it can't be resized: {why}"
        if self._resized is not None:
            return f"{wrong}, and it didn't take that size"
        self._resized = (self.window, now)
        self.window.set_client_size(*size)
        if self.window.client_size() != size:
            return f"{wrong}, and it didn't take that size"
        self.log(f"set the sim window to {want} for the run; it goes back to {now[0]}x{now[1]} after it")
        self.manifest["resized_from"] = list(now)
        return None

    def _rules(self) -> simrule.Launch | None:
        """The owner's [sim] settings, read once a run."""
        if self._rules_read is None:
            self._rules_read = (simrule.settings(),)
        return self._rules_read[0]

    def _launch(self, sim: Sim, problem: str) -> str:
        """While the sim isn't ready for this run: close an agent-started sim the host is waiting to close
        (it waits for the lock this run holds); and for a run on a replay with a file, while agents may start
        the sim (`livery.simhost`), start it when it isn't running, and switch an agent-started sim on another
        replay. Returns what the run waits for."""
        c = simhost.closing()
        if c is not None and not self._closed_agent:
            self._closed_agent = True
            sim.close()
            simhost.close_agent(self.cfg, c[0], self.log)
            return f"the agent-started sim closed: {c[0]}"
        if self.mode != "replay" or not self.replay.file or self._rules() is None:
            return problem
        h = simhost.host()
        if h is not None and h.get("phase") not in ("ready", "closing"):
            return f"the sim is starting for agents ({h.get('phase')})"
        if not sim_running(self.cfg):
            return simhost.ensure(self.cfg, self.replay.file)
        wrong = sim.connected() and sim.is_replay() and sim.wrong_replay(self.replay)
        if wrong and simhost.agent_sim() is not None:
            if simhost.launched_file() == self.replay.file:
                raise ConfigError(f"{self.replay.file} isn't the replay your settings name: {wrong}")
            sim.close()
            simhost.close_agent(self.cfg, f"this run needs {self.replay.label}", self.log)
            return f"switching the agent-started sim to {self.replay.label}"
        return problem

    def _front_ok(self) -> bool:
        """An agent-started sim may be in front while the owner is away: once they've had idle_minutes
        without input, until their next input."""
        launch = self._rules() if self._agent else None
        if launch is None or self.window is None:
            return False
        last = presence.last_input()
        if self._front_input is None:
            if presence.idle_s() < launch.idle_minutes * 60:
                return False
            self._front_input = last
        return last == self._front_input

    def _connect(self) -> None:
        sim = Sim(self.cfg, guard=self._guard)
        wait = self.ready_timeout if self.mode in ("replay", "any-replay") else 0.0
        t0, told = time.perf_counter(), set()
        while (problem := self._problem(sim)) is not None:
            if problem != "in front":
                problem = self._launch(sim, problem)
            if time.perf_counter() - t0 >= wait:
                sim.close()
                self.window = None
                if self.mode == "optional":
                    self.unavailable = "the sim is in front" if problem == "in front" else problem
                    return
                if problem == "in front":
                    raise SimInFront(
                        f"the sim stayed in front for {wait:.0f} s; switch away from it and run again"
                    )
                raise SimNotReady(problem)
            if problem not in told:
                front = problem == "in front"
                self.log(
                    "the sim is in front; waiting until you switch away" if front else f"waiting: {problem}"
                )
                told.add(problem)
            time.sleep(POLL_S)
        self.sim, self.car = sim, sim.car()
        self._groups = sim.groups
        self._snapshot = sim.snapshot()
        sub, track = sim.replay()
        self.manifest["replay"] = {"subsession": sub, "track": track}
        self.manifest["car"] = {"path": self.car.path, "paint": list(sim.paint_names())}

    # The guard.

    def _guard(self) -> None:
        if self.window is None:
            return
        if self._agent and (c := self._closing_now()) is not None and c[1]:
            raise SimClosing(f"the agent-started sim is closing at once: {c[0]}")
        if self.window.in_front() and not self._front_ok():
            raise SimInFront("the sim came to the front; stopping so as not to drive it under you")
        if self.window.minimised():
            raise CaptureError(
                "the sim window was minimised; it has to stay open (behind other windows is fine)"
            )

    def _closing_now(self) -> tuple[str, bool] | None:
        """`simhost.closing`, read at most every half second: the guard runs before every broadcast and
        frame."""
        at, seen = self._closing
        if time.monotonic() - at >= CLOSING_POLL_S:
            seen = simhost.closing()
            self._closing = (time.monotonic(), seen)
        return seen

    def _need(self) -> tuple[Sim, Car]:
        if self.sim is None or self.car is None:
            raise SimNotReady(self.unavailable or "this run has no sim")
        return self.sim, self.car

    # Paint.

    @property
    def paint_names(self) -> tuple[str, str]:
        return self._need()[0].paint_names()

    def put(self, files: dict[str, bytes]) -> None:
        """Write paint files for the run; they are journaled, and put back when the session ends."""
        if not self.paint_folder:
            raise SimNotReady("this run names no paint folder")
        put = self._put
        if put is None:
            self._temp = paintfolder.temporary(self.cfg, self.paint_folder, self.what)
            put = self._put = self._temp.__enter__()
        folder = paintfolder.paint_dir(self.cfg, self.paint_folder)
        for name, data in files.items():
            old = (folder / name).read_bytes() if (folder / name).exists() else None
            if old != data:
                self._differs = self._pending = True
        put(files)
        if self.sim is not None:
            self._paint = files.get(self.paint_names[0], self._paint)

    # Frames.

    def grab(self) -> np.ndarray:
        self._guard()
        if self._capture is None:
            self._capture = capture.Capture(self.window)
        img = self._capture.grab()
        self._guard()  # the sim may have come forward while the frame was on its way
        size = (img.shape[1], img.shape[0])
        if self._size is None:
            if self.size is not None and size != self.size:
                raise CaptureError(f"the sim's frames are {size}, not the run's {self.size}; stopping")
            self._size = size
            self.manifest["window"] = list(size)
        elif size != self._size:
            raise CaptureError(
                f"the sim window changed size during the run ({self._size} to {size}); stopping"
            )
        return img

    def steady(self, timeout: float = STEADY_S) -> np.ndarray:
        """Grab until two frames in a row match: after a seek the scene keeps loading for a moment."""
        prev = self.grab()
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < timeout:
            cur = self.grab()
            if capture.changed_share(prev, cur) < 0.05:
                return cur
            prev = cur
        return prev

    def seek(self, frame: int) -> None:
        """Pause the replay at a frame, without moving the camera: for reading telemetry there."""
        sim, _ = self._need()
        self._touched = True
        sim.play(0)
        sim.seek(frame)
        if wait_for(lambda: sim.ir["ReplayFrameNum"] == frame, SEEK_S) is None:
            raise SimNotReady(f"the replay didn't reach frame {frame} (at {sim.ir['ReplayFrameNum']})")

    def telemetry(self) -> dict:
        """The car's telemetry at the replay's frame (`Sim.telemetry`)."""
        sim, car = self._need()
        self._guard()
        return sim.telemetry(car.idx)

    def replay_frames(self) -> int:
        return self._need()[0].replay_frames()

    def frame(self, shot: Shot) -> None:
        """Pause at the shot's replay frame on its camera group, and let the scene settle.

        Frames, not session times: a session-time search lands up to 12 frames off (docs/sim.md).
        """
        sim, car = self._need()
        if shot.group not in self._groups:
            raise SimNotReady(f"no camera group {shot.group!r}; {self.groups_fix}")
        self._touched = True
        if not int(sim.ir["CamCameraState"] or 0) & irsdk.CameraState.ui_hidden:
            sim.set_ui_hidden(True)
        sim.play(0)
        sim.seek(shot.frame)
        if wait_for(lambda: sim.ir["ReplayFrameNum"] == shot.frame, SEEK_S) is None:
            raise SimNotReady(f"the replay didn't reach frame {shot.frame} (at {sim.ir['ReplayFrameNum']})")
        sim.camera(car.number, self._groups[shot.group], 0)
        wait_for(lambda: sim.ir["CamGroupNumber"] == self._groups[shot.group], 5)
        time.sleep(SETTLE_S)
        self.steady()

    def _await_paint(self, before: np.ndarray) -> dict[str, float | None]:
        """After a reload: old paint, plain grey, new paint. A paint near the grey may show neither step."""
        ref = {"img": before}

        def changed() -> bool:
            now = self.grab()
            if capture.changed_share(ref["img"], now) > CHANGED:
                ref["img"] = now
                return True
            return False

        grey = wait_for(changed, GREY_S, step=0.0)
        painted = wait_for(changed, PAINT_S, step=0.0) if grey is not None else None
        if grey is None:
            time.sleep(LOAD_S)
        return {"grey_s": grey, "painted_s": painted}

    def reload(self, at: Shot) -> dict[str, float | None]:
        """Reload the car's paint, framed at `at`, and wait until it shows.

        Without a grey step, the reload counts once the frame differs from the one before it, or when the
        new paint is near grey or the same as before; otherwise it failed.
        """
        sim, car = self._need()
        t0 = time.perf_counter()
        self.frame(at)
        before = self.steady()
        sim.reload(car.idx)
        result = self._await_paint(before)
        final = self.steady()
        shown = result["grey_s"] is not None or capture.changed_share(before, final) > CHANGED
        if not shown and self._pending and not near_grey(self._paint):
            raise SimNotReady("the reload didn't show: the car looks as it did before it")
        self._pending = False
        result["total_s"] = time.perf_counter() - t0
        self.manifest.setdefault("reloads", []).append(result)
        return result

    def reload_now(self) -> None:
        """Only the reload broadcast, for a session that doesn't watch the car (`livery install`)."""
        sim, car = self._need()
        sim.reload(car.idx)

    def shoot(self, name: str, shot: Shot, check_ui: bool = False) -> np.ndarray:
        """One shot. With `check_ui`, a shot with the replay UI over the car is taken again once, then
        refused."""
        t = time.perf_counter()
        self.frame(shot)
        img = self.grab()
        if check_ui:
            img = self._clear_of_ui(name, shot, img)
        self.manifest.setdefault("shots", {})[name] = [shot.frame, shot.group]
        self.timings[f"shot_{name}"] = time.perf_counter() - t
        return img

    def _reference(self, shot: Shot, img: np.ndarray) -> Path:
        """Where this view's UI bands from a clean shot are kept: the background behind them repeats on one
        replay frame and camera, whatever the paint."""
        if self._camset is None:
            track = self.replay.track
            if self.mode == "any-replay":
                track = track_path(self.manifest["replay"]["track"] or "")
            cam = cameras.track_cam(self.cfg.documents, track)
            self._camset = hashlib.sha256(cam.read_bytes()).hexdigest() if cam.exists() else ""
        key = json.dumps([self.manifest.get("replay"), shot.frame, shot.group, img.shape[:2], self._camset])
        return state.root() / "shot-bands" / f"{hashlib.sha256(key.encode()).hexdigest()[:16]}.png"

    def _clear_of_ui(self, name: str, shot: Shot, img: np.ndarray) -> np.ndarray:
        """The UI is up when the bands differ from this view's clean reference; without a reference yet,
        when the cursor is over the sim window. The first clean shot becomes the reference."""
        path = self._reference(shot, img)
        ref = np.asarray(Image.open(path).convert("RGB")) if path.exists() else None
        for retake in (False, True):
            if ref is not None:
                ui = capture.changed_share(ui_bands(img), ref) > UI_SHARE
            else:
                ui = bool(self.window.cursor_over())
            if not ui:
                if ref is None:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    Image.fromarray(ui_bands(img)).save(path)
                return img
            if not retake:
                self.log(
                    f"{name}: the replay UI looks to be over the car; taking it again in {UI_RETRY_S:.0f} s"
                )
                self.manifest.setdefault("ui_retakes", []).append(name)
                time.sleep(UI_RETRY_S)
                img = self.grab()
        raise UIOverCar(
            f"{name}: the replay UI is over the car, which the sim draws while the cursor is over its window; "
            "keep the cursor off the sim window and run again"
        )

    # Leaving.

    def __exit__(self, et, ev, tb) -> None:
        problems: list[str] = []
        self.manifest["failure"] = None if ev is None else f"{type(ev).__name__}: {ev}"
        try:
            if self._temp is not None:
                self._temp.__exit__(None, None, None)  # if this fails, the journal stays for the next run
            if self.sim is not None:
                self._leave_sim(problems)
        finally:
            self._close(problems)
        for p in problems:
            self.log(p)

    def _leave_sim(self, problems: list[str]) -> None:
        if not (self._differs or self._touched):
            return
        if self._agent and (c := simhost.closing()) is not None and c[1]:
            problems.append(
                f"the agent-started sim is closing at once ({c[0]}), so it was left as the run had it"
            )
            return
        if self.window is not None and self.window.in_front() and not self._front_ok():
            shows = "it shows the run's paint until its next reload, and " if self._differs else ""
            problems.append(
                f"the sim is in front, so it was left as the run had it: {shows}its camera and replay are where the run left them"
            )
            return
        sim, car = self._need()
        try:
            if self._differs:
                try:
                    before = self.steady()
                except CaptureError:
                    before = None
                sim.reload(car.idx)
                if before is None:
                    time.sleep(LOAD_S)
                else:
                    self._await_paint(before)
                    self.steady()
            if self._touched and self._snapshot is not None:
                self._restore(sim, self._snapshot)
        except Exception as e:
            problems.append(f"couldn't put the sim back as it was: {e}")

    def _restore(self, sim: Sim, snap: dict) -> None:
        now = sim.snapshot()
        if now["frame"] != snap["frame"]:
            sim.seek(snap["frame"])
            wait_for(lambda: sim.ir["ReplayFrameNum"] == snap["frame"], SEEK_S)
        if (now["car_idx"], now["group"], now["camera"]) != (snap["car_idx"], snap["group"], snap["camera"]):
            number = sim.number_of(snap["car_idx"])
            if number is not None:
                sim.camera(number, snap["group"], snap["camera"])
        hidden = bool(snap["state"] & irsdk.CameraState.ui_hidden)
        if bool(now["state"] & irsdk.CameraState.ui_hidden) != hidden:
            sim.set_ui_hidden(hidden)
        if (now["speed"], now["slow"]) != (snap["speed"], snap["slow"]):
            sim.play(snap["speed"], snap["slow"])

    def _put_size_back(self, problems: list[str]) -> None:
        """The window the size it had, unless the owner has it in front or resized it since."""
        if self._resized is None:
            return
        win, size = self._resized
        was = f"{size[0]}x{size[1]}"
        try:
            if win.client_size() != self.size:
                return
            if win.in_front():
                problems.append(
                    f"the sim is in front, so its window was left at the run's size; it was {was}"
                )
                return
            why = win.unsizable()
            if why is not None:
                problems.append(f"the sim window was left at the run's size, since {why}; it was {was}")
                return
            win.set_client_size(*size)
            if win.client_size() != size:
                problems.append(f"the sim window didn't go back to {was}")
        except Exception as e:
            problems.append(f"couldn't put the sim window back to {was}: {e}")

    def _close(self, problems: list[str]) -> None:
        try:
            self._put_size_back(problems)
            self.manifest["problems"] = problems
            self.manifest["timings"] = {
                k: None if v is None else round(v, 3) for k, v in self.timings.items()
            }
            self.manifest["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            if self.out is not None:
                state.write_json(self.out / "manifest.json", self.manifest)
            if self._capture is not None:
                self._capture.close()
            if self.sim is not None:
                self.sim.close()
        finally:
            try:
                self._lock.__exit__(None, None, None)
            finally:
                if self._want is not None:
                    self._want.__exit__(None, None, None)


def _profile(ws: Workspace, paint_folder: str) -> tuple[kits.Kit, kits.Profile] | None:
    """The kit for the car in this paint folder, with its sim profile, if there is one."""
    for meta in sorted(ws.kits.glob(f"*/{kits.META}")):
        try:
            kit = kits.load(ws, meta.parent.name)
            if kit.paint_folder == paint_folder:
                return kit, kits.profile(kit)
        except kits.KitError:
            continue
    return None


def _replay_file(cfg: Config) -> str:
    f = cfg.replay_file
    if not f.is_file():
        return f"replay file: {f} is missing"
    same = sha256(f) == cfg.replay.sha256
    return f"replay file: {f.name} {'is' if same else 'is not'} the one your settings' sha256 names"


def status(cfg: Config, ws: Workspace, preflight: bool = False) -> list[str]:
    """What the next sim run would find, read without sending the sim anything."""
    lines = [_replay_file(cfg)]
    sim = Sim(cfg)
    if not sim.connected():
        lines.append("sim: not running")
    else:
        sub, track = sim.replay()
        mode = sim.weekend.get("SimMode")
        wrong = sim.wrong_replay() if mode == "replay" else f"open {cfg.replay.file}"
        lines.append(f"sim: {mode}, subsession {sub} at {track}: {wrong or 'the preview replay'}")
        try:
            car = sim.car()
            paint, spec = sim.paint_names()
            lines.append(f"car: {car.path} #{car.number}, painted from {paint} and {spec}")
        except SimNotReady as e:
            car = None
            lines.append(f"car: {e}")
        found = _profile(ws, car.path) if car is not None else None
        if car is not None and found is None:
            lines.append(f"kit: none for {car.path} has a sim profile")
        if car is not None and found is not None:
            kit, prof = found
            frame = (
                f"frame {prof.frame}"
                if prof.frame is not None
                else f"none yet (livery sim pick-frame {kit.name})"
            )
            lines.append(f"{kit.name} frame: {frame} on replay {prof.replay}")
            missing = sorted({v.group for v in prof.preview.values()} - set(sim.groups))
            missing_text = "missing " + ", ".join(missing) if missing else "all there"
            lines.append(f"{kit.name} preview groups: {missing_text}")
        sim.close()
    holder = simlock.holder()
    if holder:
        age = (time.time() - float(holder.get("since", time.time()))) / 60
        where = f" in {holder['checkout']}" if holder.get("checkout") else ""
        lines.append(f"lock: held by {holder.get('what')} (pid {holder.get('pid')}{where}) for {age:.0f} min")
    else:
        lines.append("lock: free")
    stale = paintfolder.stale()
    left = ", ".join(str(s.get("what") or s["journal"]) for s in stale)
    lines.append(
        f"paint journals left by dead runs: {left + ' (the next sim run puts them back)' if stale else 'none'}"
    )
    running = trading_paints_running()
    lines.append(f"Trading Paints Downloader: {'running' if running else 'not running'}")
    w = capture.Window.find(cfg.process)
    if w is None:
        lines.append("window: none")
    else:
        where = "minimised" if w.minimised() else "in front" if w.in_front() else "behind other windows"
        cursor = ", the cursor over it (the sim draws its replay UI there)" if w.cursor_over() else ""
        (cw, ch), (pw, ph) = w.client_size(), ws.policy.window
        if (cw, ch) == (pw, ph):
            size = f"{cw}x{ch}"
        else:
            why = w.unsizable()
            size = (
                f"{cw}x{ch}, not the {pw}x{ph} runs shoot at ({why or 'a run resizes it and puts it back'})"
            )
        lines.append(f"window: {where}, {size}{cursor}")
    return lines + launch_status(cfg, preflight)


def launch_status(cfg: Config, preflight: bool = True) -> list[str]:
    """Whether agents start and close the sim, and what a launch would find; with `preflight`, everything a
    launch needs (it asks iRacing whether the session still signs in)."""
    from livery import simrule

    lc = simrule.settings()
    if lc is None:
        return ["launch: off (config.toml has no [sim] table; livery.example.toml describes it)"]
    lines = [
        f"launch: {lc.rule}, after {lc.idle_minutes:g} min idle; closes after {lc.close_after_minutes:g} min "
        f"unwanted; display {lc.display_mode}"
    ]
    o = simrule.override()
    if o is not None:
        lines.append(f"override: {o.describe()}")
    p = presence.read()
    why = simrule.blocked(lc, p, cfg.process, o)
    lines.append(f"owner idle {p.idle_s / 60:.1f} min; an agent launch now: {why or 'would start'}")
    h, agent = simhost.host(), simhost.agent_sim()
    if agent is not None:
        lines.append(f"sim: started by an agent (pid {agent['pid']})" + ("" if h else ", its host gone"))
    elif sim_running(cfg):
        lines.append("sim: opened by the owner; agents never close it")
    if h is not None:
        lines.append(f"host: pid {h['pid']}, {h.get('phase')}, on {h.get('file')}")
        if h.get("closing"):
            lines.append(f"host closing: {h['closing']}")
    wanted = simhost.demand()
    if wanted:
        lines.append("wanted by: " + ", ".join(str(d.get("what")) for d in wanted))
    failed = simhost.last_failure(cfg.replay.file)
    if failed is not None:
        lines.append(
            f"last launch failed {(time.time() - float(failed['at'])) / 60:.0f} min ago: {failed['reason']}"
        )
    if preflight:
        for what, problem in simhost.preflight(cfg, cfg.replay.file):
            lines.append(f"{what}: {problem or 'ok'}")
    return lines
