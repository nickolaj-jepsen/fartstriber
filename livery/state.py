"""Machine-wide state, shared by every checkout and worktree. It lives in %LOCALAPPDATA%\\fartstriber, or
wherever FARTSTRIBER_STATE points (the tests point it at a temp dir)."""

import ctypes
import ctypes.wintypes as wt
import json
import os
from pathlib import Path
from typing import IO

ENV = "FARTSTRIBER_STATE"
NO_FSYNC = "FARTSTRIBER_NO_FSYNC"  # set: writes stay atomic but skip fsync (the tests)

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.OpenProcess.restype = wt.HANDLE
_kernel32.OpenProcess.argtypes = (wt.DWORD, wt.BOOL, wt.DWORD)
_kernel32.GetProcessTimes.argtypes = (wt.HANDLE, *[ctypes.POINTER(wt.FILETIME)] * 4)
_kernel32.GetExitCodeProcess.argtypes = (wt.HANDLE, ctypes.POINTER(wt.DWORD))
_kernel32.CloseHandle.argtypes = (wt.HANDLE,)
QUERY = 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION
STILL_ACTIVE = 259


def root() -> Path:
    override = os.environ.get(ENV)
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "fartstriber"
    d = Path(override) if override else base
    d.mkdir(parents=True, exist_ok=True)
    return d


def started(pid: int) -> int | None:
    """When a running process started (a FILETIME), or None if it isn't running. With the pid it names the
    process for good: a pid is reused once its process is gone."""
    h = _kernel32.OpenProcess(QUERY, False, pid)
    if not h:
        return None
    try:
        code = wt.DWORD()
        if not _kernel32.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != STILL_ACTIVE:
            return None
        t = [wt.FILETIME() for _ in range(4)]
        if not _kernel32.GetProcessTimes(h, *[ctypes.byref(x) for x in t]):
            return None
        return (t[0].dwHighDateTime << 32) | t[0].dwLowDateTime
    finally:
        _kernel32.CloseHandle(h)


def me() -> dict:
    """This process, as `alive` recognises it."""
    return {"pid": os.getpid(), "started": started(os.getpid())}


def alive(owner: dict) -> bool:
    now = started(int(owner.get("pid", 0)))
    return now is not None and now == owner.get("started", now)


def sync(f: IO) -> None:
    """Puts an open file on disk before it is closed, unless NO_FSYNC is set."""
    f.flush()
    if not os.environ.get(NO_FSYNC):
        os.fsync(f.fileno())


def write_json(path: Path, data: object) -> None:
    """Written whole or not at all, and on disk before it returns: these files outlive a killed run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1)
        sync(f)
    os.replace(tmp, path)


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
