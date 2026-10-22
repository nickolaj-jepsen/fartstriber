"""When agents may start the sim, and when an agent-started sim has to go: the owner's `[sim]` settings, what
they are doing at the PC (`livery.presence`), and their own hold or allow (`livery sim hold`, `sim allow`),
which outlasts any one run until it expires or is lifted."""

import time
from dataclasses import dataclass

from livery import config, state
from livery.config import Launch
from livery.presence import Presence

OVERRIDE_FILE = "sim-override.json"
UI_EXE = "iRacingUI.exe"
UI_ACTIVE_S = 60.0  # input this recent, with the iRacing UI in front, means the owner is using it


@dataclass(frozen=True)
class Override:
    mode: str  # "hold": no launches, and an agent-started sim closes; "allow": launch while the owner is here
    until: float | None  # epoch seconds; None until lifted

    def describe(self) -> str:
        until = time.strftime("%H:%M", time.localtime(self.until)) if self.until else "lifted"
        return f"{self.mode} until {until}"


def _override_path():
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


def blocked(launch: Launch | None, p: Presence, sim_exe: str, o: Override | None) -> str | None:
    """Why agents may not start the sim now, or None when they may."""
    owner = config.owner()
    if launch is None:
        return f"agents don't start the sim: {config.USER_FILE} has no [sim] table ({config.EXAMPLE} describes it)"
    if o is not None and o.mode == "hold":
        return f"{owner} holds the sim ({o.describe()}; `livery sim hold --off` lifts it)"
    if launch.full_screen_stops and (what := p.full_screen(sim_exe)):
        return f"{owner} has {what} in front"
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


def must_close(
    launch: Launch | None, p: Presence, sim_exe: str, o: Override | None
) -> tuple[str, bool] | None:
    """Why an agent-started sim has to close now, whatever the runs want: (reason, at once). At once stops
    the run under way; otherwise the sim closes once it ends."""
    owner = config.owner()
    if launch is not None and launch.full_screen_stops and (what := p.full_screen(sim_exe)):
        return f"{owner} has {what} in front", True
    if o is not None and o.mode == "hold":
        return f"{owner} holds the sim ({o.describe()})", False
    if (
        launch is not None
        and launch.ui_closes
        and (p.foreground or "").lower() == UI_EXE.lower()
        and p.idle_s < UI_ACTIVE_S
    ):
        return f"{owner} is in the iRacing UI", False
    return None


def idle_close(launch: Launch | None, p: Presence, quiet_s: float, in_front: bool) -> bool:
    """Whether an agent-started sim no run has wanted for `quiet_s` seconds can close: after
    close_after_minutes, and, while it is in front, only once the owner has been away idle_minutes."""
    after = (launch.close_after_minutes if launch else 30.0) * 60
    if quiet_s < after:
        return False
    return not in_front or p.idle_s >= (launch.idle_minutes if launch else 5.0) * 60
