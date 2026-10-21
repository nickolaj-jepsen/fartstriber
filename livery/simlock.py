"""One sim, many sessions: a named mutex lets one sim run drive iRacing at a time; the others queue.

The kernel frees the mutex when its holder dies, so a killed run never leaves the sim locked; a run that was
waiting at that moment learns that it died (`Held.abandoned`). Nobody else does, since a mutex nobody holds
open is gone, so a dead run's paint is found from the journal, not from the lock. A small file in the state dir
names the holder, for the 'waiting for' message and `livery sim status`.
"""

import ctypes
import ctypes.wintypes as wt
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from livery import state
from livery.config import REPO
from livery.errors import LiveryError

MUTEX = "Local\\fartstriber-sim"
POLL_MS = 500
WAIT_OBJECT_0, WAIT_ABANDONED, WAIT_TIMEOUT = 0, 0x80, 0x102

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.restype = wt.HANDLE
_kernel32.CreateMutexW.argtypes = (wt.LPVOID, wt.BOOL, wt.LPCWSTR)
_kernel32.WaitForSingleObject.restype = wt.DWORD
_kernel32.WaitForSingleObject.argtypes = (wt.HANDLE, wt.DWORD)
_kernel32.ReleaseMutex.argtypes = (wt.HANDLE,)
_kernel32.CloseHandle.argtypes = (wt.HANDLE,)


class SimBusy(LiveryError, TimeoutError):
    pass


@dataclass(frozen=True)
class Held:
    waited: float  # seconds spent queueing
    abandoned: bool  # the last holder died holding it


def _info(name: str | None) -> Path:
    """'Local\\fartstriber-sim' -> <state>/fartstriber-sim.json"""
    return state.root() / ((name or MUTEX).rsplit("\\", 1)[-1] + ".json")


def holder(name: str | None = None) -> dict | None:
    """Who holds the lock, read without touching the mutex. None when it's free (a dead holder's file
    counts as free: the kernel freed its mutex)."""
    info = state.read_json(_info(name))
    return info if info is not None and state.alive(info) else None


def _describe(info: dict | None) -> str:
    if not info:
        return "another run"
    where = f" in {info['checkout']}" if info.get("checkout") else ""
    return f"{info.get('what')} (pid {info.get('pid')}{where})"


@contextmanager
def sim_lock(
    what: str, timeout: float = 1800, log: Callable[[str], object] | None = None, name: str | None = None
) -> Iterator[Held]:
    """Hold the sim lock for the block. `name` is the mutex's, MUTEX unless a test needs its own."""
    name = name or MUTEX
    h = _kernel32.CreateMutexW(None, False, name)
    if not h:
        raise OSError(ctypes.get_last_error(), f"can't create the sim lock {name}")
    t0 = time.perf_counter()
    told = False
    try:
        while True:
            r = _kernel32.WaitForSingleObject(h, POLL_MS)
            if r in (WAIT_OBJECT_0, WAIT_ABANDONED):
                break
            if r != WAIT_TIMEOUT:
                raise OSError(ctypes.get_last_error(), f"waiting for the sim lock {name} failed")
            if not told and log:
                log(f"waiting for the sim: {_describe(state.read_json(_info(name)))} has it")
                told = True
            if time.perf_counter() - t0 > timeout:
                raise SimBusy(f"the sim stayed busy for {timeout:.0f} s: {_describe(holder(name))}")
        try:
            state.write_json(
                _info(name), {**state.me(), "what": what, "since": time.time(), "checkout": str(REPO)}
            )
            yield Held(time.perf_counter() - t0, r == WAIT_ABANDONED)
        finally:
            info = state.read_json(_info(name))
            if info and info.get("pid") == os.getpid():
                _info(name).unlink(missing_ok=True)
            _kernel32.ReleaseMutex(h)
    finally:
        _kernel32.CloseHandle(h)
