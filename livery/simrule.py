"""When agents may start the sim, and when an agent-started sim has to go: the owner's `[sim]` settings, what
they are doing at the PC (`livery.presence`), and their own hold or allow (`livery sim hold`, `sim allow`),
which outlasts any one run until it expires or is lifted.

The `[sim]` table lives in the owner's config.toml with the other settings, and is checked the same way: a
missing key or a value outside its choices is a ConfigError that names it. It is read here rather than by
`livery.config`, which builds import, so that changing it never touches a build's toolchain hash.
"""

import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from livery import config, state
from livery.config import ConfigError
from livery.presence import FULL_SCREEN, Presence

OVERRIDE_FILE = "sim-override.json"
UI_EXE = "iRacingUI.exe"
UI_ACTIVE_S = 60.0  # input this recent, with the iRacing UI in front, means the owner is using it
RULES = ("idle", "anytime", "never")
DISPLAY_MODES = ("monitor", "oculus", "openvr", "openxr")


@dataclass(frozen=True)
class Launch:
    """The `[sim]` settings: when agents may start the sim themselves, and when they close it."""

    rule: str  # RULES: "idle" waits until the owner has been away idle_minutes
    idle_minutes: float
    close_after_minutes: float  # closed once no run has wanted it this long
    display_mode: str
    full_screen_stops: bool  # a full-screen app or game in front: no launch, and an agent-started sim closes
    battery_stops: bool  # on battery: no launch
    ui_closes: bool  # the owner in the iRacing UI: no launch, and an agent-started sim closes after the run


def _value(raw: dict, key: str, where: Path) -> Any:
    table = raw.get("sim", {})
    if key not in table:
        raise ConfigError(f"{where} has no sim.{key}; {config.EXAMPLE} describes it")
    return table[key]


def _choice(raw: dict, key: str, where: Path, choices: tuple[str, ...]) -> str:
    value = _value(raw, key, where)
    if value not in choices:
        raise ConfigError(f"{where}: sim.{key} is {value!r}; it takes {', '.join(choices)}")
    return value


def _minutes(raw: dict, key: str, where: Path, least: float) -> float:
    value = _value(raw, key, where)
    if isinstance(value, bool) or not isinstance(value, int | float) or value < least:
        raise ConfigError(f"{where}: sim.{key} is {value!r}; it takes minutes, {least:g} or more")
    return float(value)


def _flag(raw: dict, key: str, where: Path) -> bool:
    value = _value(raw, key, where)
    if not isinstance(value, bool):
        raise ConfigError(f"{where}: sim.{key} is {value!r}; it takes true or false")
    return value


def settings(path: Path | None = None) -> Launch | None:
    """The owner's `[sim]` settings, or None without the table: then agents never start the sim."""
    where = path or config.user_file()
    try:
        raw = tomllib.loads(where.read_text(encoding="utf-8"))
    except OSError:
        return None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{where}: {e}") from None
    if "sim" not in raw:
        return None
    return Launch(
        rule=_choice(raw, "launch", where, RULES),
        idle_minutes=_minutes(raw, "idle_minutes", where, 0),
        close_after_minutes=_minutes(raw, "close_after_minutes", where, 1),
        display_mode=_choice(raw, "display_mode", where, DISPLAY_MODES),
        full_screen_stops=_flag(raw, "full_screen_stops", where),
        battery_stops=_flag(raw, "battery_stops", where),
        ui_closes=_flag(raw, "ui_closes", where),
    )


@dataclass(frozen=True)
class Override:
    mode: str  # "hold": no launches, and an agent-started sim closes; "allow": launch while the owner is here
    until: float | None  # epoch seconds; None until lifted

    def describe(self) -> str:
        until = time.strftime("%H:%M", time.localtime(self.until)) if self.until else "lifted"
        return f"{self.mode} until {until}"


def _override_path() -> Path:
    return state.root() / OVERRIDE_FILE


def override(now: float | None = None) -> Override | None:
    """The owner's hold or allow, while it lasts."""
    raw = state.read_json(_override_path())
    if not raw or raw.get("mode") not in ("hold", "allow"):
        return None
    o = Override(raw["mode"], raw.get("until"))
    if o.until is not None and o.until <= (now if now is not None else time.time()):
        return None
    return o


def set_override(mode: str | None, seconds: float | None = None) -> Override | None:
    """Hold or allow for `seconds` (until lifted when None); `mode` None lifts either."""
    if mode is None:
        _override_path().unlink(missing_ok=True)
        return None
    o = Override(mode, time.time() + seconds if seconds else None)
    state.write_json(_override_path(), {"mode": o.mode, "until": o.until})
    return o


def paused(launch: Launch | None, p: Presence, sim_exe: str) -> str | None:
    """Why runs have to stop at once and no sim may start: a full-screen app or game in front. The sim isn't
    closed then: its exit brings the iRacing UI to the front, over the game (docs/sim.md)."""
    if launch is not None and launch.full_screen_stops and (what := p.full_screen(sim_exe)):
        return f"{config.owner()} has {what} in front"
    return None


def must_close(launch: Launch | None, p: Presence, sim_exe: str, o: Override | None) -> str | None:
    """Why an agent-started sim has to close once the run under way ends, whatever the runs want."""
    owner = config.owner()
    if paused(launch, p, sim_exe):
        return None  # not while a game is in front; once it's gone, the reason below or an idle close
    if o is not None and o.mode == "hold":
        return f"{owner} holds the sim ({o.describe()})"
    if (
        launch is not None
        and launch.ui_closes
        and (p.foreground or "").lower() == UI_EXE.lower()
        and p.idle_s < UI_ACTIVE_S
    ):
        return f"{owner} is in the iRacing UI"
    return None


def blocked(launch: Launch | None, p: Presence, sim_exe: str, o: Override | None) -> str | None:
    """Why agents may not start the sim now, or None when they may. Whatever would pause the runs or close
    an agent-started sim also stops its launch."""
    owner = config.owner()
    if launch is None:
        return f"agents don't start the sim: {config.USER_FILE} has no [sim] table ({config.EXAMPLE} describes it)"
    if (why := paused(launch, p, sim_exe) or must_close(launch, p, sim_exe, o)) is not None:
        return why
    if launch.battery_stops and p.battery:
        return "the PC is on battery"
    if o is not None and o.mode == "allow":
        return None
    if launch.rule == "never":
        return 'agents never start the sim ([sim] launch = "never")'
    if launch.rule == "idle" and p.idle_s < launch.idle_minutes * 60:
        return (
            f"{owner} is at the PC: an agent starts the sim after {launch.idle_minutes:g} min without input "
            "(`livery sim allow` lets it start now)"
        )
    return None


def idle_close(launch: Launch | None, p: Presence, quiet_s: float) -> bool:
    """Whether an agent-started sim no run has wanted for `quiet_s` seconds can close: after
    close_after_minutes, once the owner has been away idle_minutes (its exit brings the iRacing UI to the
    front), and not while a full-screen game is in front."""
    after = (launch.close_after_minutes if launch else 30.0) * 60
    away = (launch.idle_minutes if launch else 5.0) * 60
    return quiet_s >= after and p.idle_s >= away and p.notification not in FULL_SCREEN
