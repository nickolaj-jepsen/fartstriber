"""The sim's window, read through calls that never focus, raise or restore it, and one Windows Graphics
Capture session per run that hands out the next frame on request."""

import contextlib
import ctypes
import ctypes.wintypes as wt
import threading

import numpy as np
from windows_capture import Frame, InternalCaptureControl, WindowsCapture

from livery.errors import LiveryError

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
dwmapi = ctypes.windll.dwmapi
EXTENDED_FRAME_BOUNDS = 9  # DWMWA_: the window as the capture sees it, without its invisible resize border
FRAME_TIMEOUT_S = 5.0
GWL_STYLE, WS_CAPTION = -16, 0x00C00000
MONITOR_DEFAULTTONEAREST = 2
# SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOOWNERZORDER: a resize in place, behind other windows.
RESIZE_ONLY = 0x0002 | 0x0004 | 0x0010 | 0x0200


class _MonitorInfo(ctypes.Structure):
    _fields_ = (("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD))


class CaptureError(LiveryError, RuntimeError):
    pass


class SimInFront(CaptureError):
    pass


def process_name(pid: int) -> str:
    h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(len(buf))
        kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        return buf.value.rsplit("\\", 1)[-1]
    finally:
        kernel32.CloseHandle(h)


def window_pid(hwnd: int) -> int:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def find_window(process_exe: str) -> int | None:
    """The largest visible top-level window of `process_exe`."""
    best: tuple[int, int] | None = None

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def visit(hwnd, _):
        nonlocal best
        if user32.IsWindowVisible(hwnd) and process_name(window_pid(hwnd)).lower() == process_exe.lower():
            r = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            area = (r.right - r.left) * (r.bottom - r.top)
            if best is None or area > best[1]:
                best = (hwnd, area)
        return True

    user32.EnumWindows(visit, 0)
    return best[0] if best else None


class _Entry(ctypes.Structure):
    _fields_ = (
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wt.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    )


def _snapshot() -> list[tuple[int, str]]:
    """(pid, exe name) of every running process."""
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateToolhelp32Snapshot.restype = wt.HANDLE
    snap = k.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if snap in (None, wt.HANDLE(-1).value):
        return []
    found = []
    try:
        e = _Entry()
        e.dwSize = ctypes.sizeof(_Entry)
        ok = k.Process32FirstW(wt.HANDLE(snap), ctypes.byref(e))
        while ok:
            found.append((int(e.th32ProcessID), e.szExeFile))
            ok = k.Process32NextW(wt.HANDLE(snap), ctypes.byref(e))
    finally:
        k.CloseHandle(wt.HANDLE(snap))
    return found


def processes() -> list[str]:
    """The exe names of every running process."""
    return [name for _, name in _snapshot()]


def pids(process_exe: str) -> list[int]:
    """The pids of every running `process_exe`."""
    return [pid for pid, name in _snapshot() if name.lower() == process_exe.lower()]


class Window:
    """The sim's top-level window."""

    def __init__(self, hwnd: int):
        self.hwnd = hwnd
        self.pid = window_pid(hwnd)

    @classmethod
    def find(cls, process_exe: str) -> "Window | None":
        hwnd = find_window(process_exe)
        return cls(hwnd) if hwnd else None

    def in_front(self) -> bool:
        """The sim has the foreground: the owner is using it, so a run must not drive it."""
        fg = user32.GetForegroundWindow()
        return bool(fg) and window_pid(fg) == self.pid

    def minimised(self) -> bool:
        return bool(user32.IsIconic(self.hwnd))

    def cursor_over(self) -> bool:
        """The cursor is over a visible part of the sim's window, where the sim draws its replay UI."""
        p = wt.POINT()
        if not user32.GetCursorPos(ctypes.byref(p)):
            return False
        h = user32.WindowFromPoint(p)
        return bool(h) and window_pid(h) == self.pid

    def client_size(self) -> tuple[int, int]:
        c = wt.RECT()
        user32.GetClientRect(self.hwnd, ctypes.byref(c))
        return c.right, c.bottom

    def _bounds(self) -> wt.RECT:
        """The window as it shows on screen, without its invisible resize border."""
        e = wt.RECT()
        if dwmapi.DwmGetWindowAttribute(self.hwnd, EXTENDED_FRAME_BOUNDS, ctypes.byref(e), ctypes.sizeof(e)):
            user32.GetWindowRect(self.hwnd, ctypes.byref(e))
        return e

    def unsizable(self) -> str | None:
        """Why the window can't be resized with a resize alone, or None. A maximised window would need a
        restore, which takes focus; a fullscreen one has no frame to size. A window may grow past its monitor
        and the desktop: the sim renders and the capture sees all of it (docs/sim.md)."""
        if user32.IsIconic(self.hwnd):
            return "it is minimised"
        if user32.IsZoomed(self.hwnd):
            return "it is maximised"
        if not user32.GetWindowLongW(self.hwnd, GWL_STYLE) & WS_CAPTION:
            return "it is fullscreen"
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        user32.GetMonitorInfoW(
            user32.MonitorFromWindow(self.hwnd, MONITOR_DEFAULTTONEAREST), ctypes.byref(info)
        )
        m, e = info.rcMonitor, self._bounds()
        if (e.left, e.top, e.right, e.bottom) == (m.left, m.top, m.right, m.bottom):
            return "it is fullscreen"
        return None

    def set_client_size(self, width: int, height: int) -> None:
        """Resize the window, where it is, to this client size: never moved, raised, activated or restored.
        The frame's size is read from the window rather than worked out from its style."""
        for _ in range(2):
            w, h = self.client_size()
            if (w, h) == (width, height):
                return
            r = wt.RECT()
            user32.GetWindowRect(self.hwnd, ctypes.byref(r))
            user32.SetWindowPos(
                self.hwnd, 0, 0, 0, r.right - r.left + width - w, r.bottom - r.top + height - h, RESIZE_ONLY
            )

    def client_box(self, frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
        """(x0, y0, x1, y1) of the client area in a captured frame, which also holds the title bar and the
        window border."""
        e = self._bounds()
        w, h = self.client_size()
        p = wt.POINT(0, 0)
        user32.ClientToScreen(self.hwnd, ctypes.byref(p))
        # The frame is in physical pixels; the rects may be scaled for this process's DPI awareness.
        sx = frame_w / max(1, e.right - e.left)
        sy = frame_h / max(1, e.bottom - e.top)
        x0, y0 = round((p.x - e.left) * sx), round((p.y - e.top) * sy)
        x0, y0 = min(max(x0, 0), frame_w), min(max(y0, 0), frame_h)
        return x0, y0, min(frame_w, x0 + round(w * sx)), min(frame_h, y0 + round(h * sy))


class Capture:
    """One capture session for a run: starting one costs 0.5-1 s, and a running one hands out frames as the
    sim draws them."""

    def __init__(self, window: Window):
        self.window = window
        self._want, self._got = threading.Event(), threading.Event()
        self._frame: np.ndarray | None = None
        self._closed = False
        cap = WindowsCapture(cursor_capture=False, draw_border=False, window_hwnd=window.hwnd)

        @cap.event
        def on_frame_arrived(frame: Frame, control: InternalCaptureControl):
            if self._want.is_set():
                self._frame = frame.frame_buffer[:, :, 2::-1].copy()  # BGRA -> RGB
                self._want.clear()
                self._got.set()

        @cap.event
        def on_closed():
            self._closed = True
            self._got.set()

        self._control = cap.start_free_threaded()

    def grab(self) -> np.ndarray:
        """The next frame's client area. Raises if none comes or it is blank."""
        self._frame = None
        self._got.clear()
        self._want.set()
        if not self._got.wait(FRAME_TIMEOUT_S) or self._frame is None:
            self._want.clear()
            raise CaptureError("no frame captured" + (": the capture closed" if self._closed else ""))
        frame = self._frame
        x0, y0, x1, y1 = self.window.client_box(frame.shape[1], frame.shape[0])
        img = frame[y0:y1, x0:x1]
        if img.size == 0 or img.std() < 1.0:
            raise CaptureError("blank capture; stopping instead of bringing the window forward")
        return img

    def close(self) -> None:
        with contextlib.suppress(Exception):  # already closed with the window
            self._control.stop()


def changed_share(a: np.ndarray, b: np.ndarray) -> float:
    """Percentage of pixels that changed clearly; a mean over the frame misses a small car."""
    if a.shape != b.shape:
        return 100.0
    return float((np.abs(a.astype(np.int16) - b).max(axis=2) > 40).mean() * 100)
