"""Whether the owner is at the PC, read without touching anything: how long since their last keyboard or
mouse input, whether a full-screen app or presentation is in front, which app has the foreground, and
whether the PC runs on battery."""

import ctypes
import ctypes.wintypes as wt
from dataclasses import dataclass

from livery import capture

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
_kernel32.GetTickCount.restype = wt.DWORD
_user32.GetForegroundWindow.restype = wt.HWND


class _LastInput(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


class _Power(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", wt.DWORD),
        ("BatteryFullLifeTime", wt.DWORD),
    ]


# SHQueryUserNotificationState: what Windows itself checks before it shows a notification.
NOT_PRESENT, BUSY, D3D_FULL_SCREEN, PRESENTATION, ACCEPTS, QUIET_TIME, APP = range(1, 8)
FULL_SCREEN = {
    BUSY: "a full-screen app",
    D3D_FULL_SCREEN: "a full-screen game",
    PRESENTATION: "a presentation",
}


def last_input() -> int:
    """The tick (ms since boot, 32-bit) of the owner's last keyboard or mouse input."""
    info = _LastInput(ctypes.sizeof(_LastInput), 0)
    if not _user32.GetLastInputInfo(ctypes.byref(info)):
        return int(_kernel32.GetTickCount())
    return int(info.dwTime)


def idle_s() -> float:
    """Seconds since the owner's last keyboard or mouse input."""
    return ((int(_kernel32.GetTickCount()) - last_input()) & 0xFFFFFFFF) / 1000


def notification_state() -> int:
    state = ctypes.c_int(ACCEPTS)
    if _shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
        return ACCEPTS
    return state.value


def foreground_exe() -> str | None:
    """The exe name of the app in front, or None when nothing is."""
    fg = _user32.GetForegroundWindow()
    if not fg:
        return None
    return capture.process_name(capture.window_pid(fg)) or None


def on_battery() -> bool:
    p = _Power()
    return bool(_kernel32.GetSystemPowerStatus(ctypes.byref(p))) and p.ACLineStatus == 0


@dataclass(frozen=True)
class Presence:
    """One reading of what the owner is doing."""

    idle_s: float
    notification: int
    foreground: str | None
    battery: bool

    def full_screen(self, sim_exe: str) -> str | None:
        """What full-screen thing the owner has in front, if anything: the sim itself doesn't count."""
        if self.notification not in FULL_SCREEN:
            return None
        if self.foreground and self.foreground.lower() == sim_exe.lower():
            return None
        return f"{FULL_SCREEN[self.notification]} ({self.foreground or 'unknown'})"


def read() -> Presence:
    return Presence(idle_s(), notification_state(), foreground_exe(), on_battery())
