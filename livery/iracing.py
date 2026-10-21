"""Thin pyirsdk wrapper. Only SDK reads and the broadcasts below: no keystrokes, no mouse, no focus changes.

Every broadcast first calls `guard`, which `SimSession` uses to stop a run once the sim is in front.
"""

import configparser
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import irsdk

from livery import capture
from livery.config import Config, Replay
from livery.errors import LiveryError

TRADING_PAINTS = ("tradingpaints", "trading paints")
RENDERER_INI = "rendererDX11Monitor.ini"
# The ini's sections that shape a replay's picture: the options, and those a replay overrides them with.
RENDERER_SECTIONS = ("Graphics Options", "Replay Graphics")


def renderer_settings(documents: Path) -> dict[str, dict[str, str]] | None:
    """The sim's graphics settings by section, without their comments; None without the ini."""
    path = documents / RENDERER_INI
    if not path.exists():
        return None
    ini = configparser.ConfigParser(inline_comment_prefixes=(";",), interpolation=None, strict=False)
    ini.optionxform = str  # type: ignore[assignment]  # keep the keys' case
    ini.read_string(path.read_text(encoding="utf-8", errors="replace"))
    return {s: {k: v.strip() for k, v in ini[s].items()} for s in RENDERER_SECTIONS if ini.has_section(s)}


def renderer_differs(documents: Path, required: dict[str, dict[str, str]]) -> list[str]:
    """Each setting the policy requires that the sim's ini holds otherwise, as '[section] key is x, needs y'."""
    if not required:
        return []
    have = renderer_settings(documents) or {}
    return [
        f"[{section}] {key} is {have.get(section, {}).get(key, 'unset')}, needs {want}"
        for section, keys in required.items()
        for key, want in keys.items()
        if have.get(section, {}).get(key) != str(want)
    ]


class SimNotReady(LiveryError, RuntimeError):
    pass


@dataclass(frozen=True)
class Car:
    idx: int
    number: str
    path: str
    team_id: int
    team_racing: bool

    @property
    def paint_stem(self) -> str | None:
        """Team sessions paint from car_team_<TeamID>; others from car_<customerID>."""
        return f"team_{self.team_id}" if self.team_racing else None


def wait_for(pred: Callable[[], bool], timeout: float, step: float = 0.05) -> float | None:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        if pred():
            return time.perf_counter() - t0
        time.sleep(step)
    return None


def trading_paints_running() -> bool:
    """Trading Paints Downloader writes paint files when a session loads."""
    return any(any(t in p.lower() for t in TRADING_PAINTS) for p in capture.processes())


def sim_running(cfg: Config) -> bool:
    """The sim's process runs, with a window or not: it rewrites camera.ini when it exits."""
    return any(p.lower() == cfg.process.lower() for p in capture.processes())


class Sim:
    def __init__(self, cfg: Config, ir: Any = None, guard: Callable[[], None] = lambda: None):
        self.cfg = cfg
        self.ir: Any = ir if ir is not None else irsdk.IRSDK()
        self.guard = guard

    def connected(self) -> bool:
        return bool(self.ir.startup() and self.ir.is_connected)

    def close(self) -> None:
        self.ir.shutdown()

    @property
    def weekend(self) -> dict:
        return self.ir["WeekendInfo"] or {}

    def is_replay(self) -> bool:
        return self.weekend.get("SimMode") == "replay"

    def replay(self) -> tuple[int | None, str | None]:
        """The open session's (SubSessionID, TrackName)."""
        w = self.weekend
        return w.get("SubSessionID"), w.get("TrackName")

    def wrong_replay(self, replay: Replay | None = None) -> str | None:
        """Why the open replay isn't `replay` (the user's preview replay by default), if it isn't."""
        replay = replay or self.cfg.replay
        sub, track = self.replay()
        if sub != replay.subsession or track != replay.track_name:
            return f"the sim has another replay open (subsession {sub} at {track}); open {replay.label}"
        return None

    def replay_frames(self) -> int:
        """How many frames the open replay holds."""
        return int(self.ir["ReplayFrameNum"] or 0) + int(self.ir["ReplayFrameNumEnd"] or 0)

    def telemetry(self, car_idx: int) -> dict:
        """Where the car is and how it moves at the replay's frame, read from one tick: its share of the lap,
        track surface (irsdk.TrkLoc), whether it is on pit road, its steering (rad), and for the player's car
        its yaw rate (rad/s), speed (m/s) and roll (rad, the track's banking with it). What the sim doesn't
        give is None."""
        ir = self.ir
        ir.freeze_var_buffer_latest()
        try:

            def of_car(name: str):
                v = ir[name]
                return v[car_idx] if isinstance(v, list | tuple) and car_idx < len(v) else None

            player = ir["PlayerCarIdx"] == car_idx
            return {
                "frame": ir["ReplayFrameNum"],
                "lap": of_car("CarIdxLapDistPct"),
                "surface": of_car("CarIdxTrackSurface"),
                "pit": of_car("CarIdxOnPitRoad"),
                "steer": of_car("CarIdxSteer"),
                "yaw_rate": ir["YawRate"] if player else None,
                "speed": ir["Speed"] if player else None,
                "roll": ir["Roll"] if player else None,
            }
        finally:
            ir.unfreeze_var_buffer_latest()

    @property
    def groups(self) -> dict[str, int]:
        return {g["GroupName"]: g["GroupNum"] for g in (self.ir["CameraInfo"] or {}).get("Groups", [])}

    def drivers(self) -> list[dict]:
        return (self.ir["DriverInfo"] or {}).get("Drivers", [])

    def car(self) -> Car:
        for d in self.drivers():
            if d["UserID"] == self.cfg.customer_id:
                return Car(
                    idx=d["CarIdx"],
                    number=d["CarNumber"],
                    path=d["CarPath"],
                    team_id=int(d.get("TeamID") or 0),
                    team_racing=bool(self.weekend.get("TeamRacing")),
                )
        raise SimNotReady(f"customer {self.cfg.customer_id} isn't in this session")

    def paint_names(self) -> tuple[str, str]:
        """(paint, spec) file names the sim reads for this car in this session."""
        car = self.car()
        stem = car.paint_stem or str(self.cfg.customer_id)
        return f"car_{stem}.tga", f"car_spec_{stem}.tga"

    def number_of(self, car_idx: int) -> str | None:
        return next((d["CarNumber"] for d in self.drivers() if d["CarIdx"] == car_idx), None)

    def snapshot(self) -> dict:
        """What a run changes: the camera, the UI flag, the replay's place and speed."""
        ir = self.ir
        return {
            "state": int(ir["CamCameraState"] or 0),
            "car_idx": ir["CamCarIdx"],
            "group": ir["CamGroupNumber"],
            "camera": ir["CamCameraNumber"],
            "frame": ir["ReplayFrameNum"],
            "speed": ir["ReplayPlaySpeed"],
            "slow": bool(ir["ReplayPlaySlowMotion"]),
        }

    # Broadcasts.

    def set_ui_hidden(self, hidden: bool) -> None:
        state = int(self.ir["CamCameraState"] or 0)
        flag = irsdk.CameraState.ui_hidden
        self.guard()
        self.ir.cam_set_state(state | flag if hidden else state & ~flag)

    def play(self, speed: int, slow: bool = False) -> None:
        self.guard()
        self.ir.replay_set_play_speed(speed, slow)

    def seek(self, frame: int) -> None:
        self.guard()
        self.ir.replay_set_play_position(irsdk.RpyPosMode.begin, frame)

    def camera(self, car_number: str, group: int, camera: int = 0) -> None:
        self.guard()
        self.ir.cam_switch_num(car_number, group, camera)

    def reload(self, car_idx: int) -> None:
        self.guard()
        self.ir.reload_texture(car_idx)
