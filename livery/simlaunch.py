"""Open a replay in the sim the way the iRacing UI does: spawn the sim through the anti-cheat wrapper with a
`-ui_port`, then answer the sim's `getTask` request over a local JSON-RPC socket with the replay task (the
.rpy file and the launch credentials). The service endpoint `/play_replay` is not used by the current UI;
only this spawn + RPC path launches a replay.

The sim holds the RPC connection until it exits. Closing it is a WM_CLOSE posted to its windows, which
needs no focus, and a kill if it doesn't exit; only an agent-started sim is ever closed (`livery.simhost`).
"""

import ctypes
import ctypes.wintypes as wt
import json
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from livery import state
from livery.errors import LiveryError

SERVICE = "http://127.0.0.1:32034"
SIM_STATUS = "/get_sim_status"
REPLAY_LIST = "/get_replay_list"
# The UI launches the sim through the Epic anti-cheat wrapper whenever the anti-cheat engine is EOS (the
# current one), even for a replay — the sim's own tamper check fails when started directly. A replay needs
# no access token, so the wrapper just passes the args through.
LAUNCHER = "start_protected_game.exe"
TASK_NAME = "legacyPlayReplay"
WM_CLOSE = 0x0010

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.OpenProcess.restype = wt.HANDLE
_kernel32.OpenProcess.argtypes = (wt.DWORD, wt.BOOL, wt.DWORD)
_kernel32.TerminateProcess.argtypes = (wt.HANDLE, wt.UINT)
_kernel32.CloseHandle.argtypes = (wt.HANDLE,)
_kernel32.TerminateProcess.restype = wt.BOOL
_kernel32.WaitForSingleObject.restype = wt.DWORD
_kernel32.WaitForSingleObject.argtypes = (wt.HANDLE, wt.DWORD)
_user32.PostMessageW.argtypes = (wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
_user32.GetWindowThreadProcessId.argtypes = (wt.HWND, ctypes.POINTER(wt.DWORD))
TERMINATE, SYNCHRONIZE, QUERY = 0x0001, 0x00100000, 0x1000
WAIT_OBJECT_0 = 0


class LaunchError(LiveryError):
    """The sim couldn't be started on the replay; the message says what was missing or failed."""


def _get(path: str, timeout: float = 5.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(SERVICE + path, timeout=timeout) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


def sim_running() -> bool | None:
    """Whether the service reports the sim running; None if the service can't be reached."""
    try:
        _, body = _get(SIM_STATUS)
    except OSError:
        return None
    return "running:1" in body.replace(" ", "")


def _num(block: str, key: str) -> int | None:
    m = re.search(rf"\b{key}:(-?\d+)", block)
    return int(m.group(1)) if m else None


def parse_replay_entry(body: str, file: str) -> dict | None:
    """Pull one saved replay's ids out of the service's replay list (a JS object literal
    `var replaydata={replays:[{file:"..",sessionID:..,trackID:..,carID:..,allCarIDs:[..]},..]}`): its session,
    track and car, and the car models in it."""
    for block in re.findall(r"\{[^{}]*\}", body):  # each replay entry has no nested braces, only [] arrays
        name = re.search(r'file:"([^"]+)"', block)
        if not name or name.group(1) != file:
            continue
        cars = re.search(r"allCarIDs:\[([^\]]*)\]", block)
        raw = [int(x) for x in cars.group(1).split(",") if x.strip().lstrip("-").isdigit()] if cars else []
        session = _num(block, "sessionID")
        return {
            "session_id": session if session and session > 0 else None,
            "track_id": _num(block, "trackID"),
            "car_id": _num(block, "carID"),
            "sess_carids": [i for i in raw if 0 < i < 0xFFFFFFFF],  # 0xFFFFFFFF marks an empty slot
        }
    return None


def request_args(entry: dict) -> dict:
    """How the replay is asked for, as the web app asks: a replay of an online session by its session id;
    else by track and car, with the car models when there are several."""
    from livery import cps

    if entry.get("session_id"):
        return cps.request_args(session_id=entry["session_id"])
    cars = entry.get("sess_carids") or []
    return cps.request_args(
        track_id=entry["track_id"], car_id=entry["car_id"], sess_carids=cars if len(cars) > 1 else None
    )


def replay_entry(file: str) -> dict | None:
    """Look up a saved replay's metadata by file name from the local service, or None if it can't be read."""
    try:
        status, body = _get(REPLAY_LIST)
    except OSError:
        return None
    return parse_replay_entry(body, file) if status == 200 else None


def launch_task(creds: dict, file: str) -> dict:
    """The task the sim asks for over `getTask`: open this replay file with these credentials.

    The web app serializes the fields into a plain `k=v&k=v` string (no url-encoding) as the task's
    `parameters`, in this order. keysrv and sess_cred_srv already arrive percent-encoded from CPS, so they
    are kept as-is.
    """
    fields = [
        ("file", file),
        ("keysrv", creds.get("keysrv")),
        ("credentials", creds.get("credentials")),
        ("customerid", creds.get("customerid")),
        ("sess_cred_srv", creds.get("sess_cred_srv")),
        ("sess_cred", creds.get("sess_cred")),
    ]
    params = "&".join(f"{k}={v}" for k, v in fields if v is not None)
    return {"task": TASK_NAME, "parameters": params}


def decode_all(buf: str) -> tuple[list[dict], str]:
    """The sim's messages are JSON objects concatenated on the socket with no framing. Decode every whole
    object at the front of `buf`, and return them with the unparsed remainder."""
    dec = json.JSONDecoder()
    objs: list[dict] = []
    while True:
        buf = buf.lstrip()
        if not buf:
            break
        try:
            obj, end = dec.raw_decode(buf)
        except json.JSONDecodeError:
            break  # a partial object: wait for more bytes
        objs.append(obj)
        buf = buf[end:]
    return objs, buf


def handle(obj: dict, task: dict) -> tuple[dict | None, bool]:
    """One JSON-RPC request from the sim: return (reply, done). The sim asks `getTask` (answered with the
    task), `simEnding` (acknowledged), and `simEnded` (ends the session)."""
    method, rid = obj.get("method"), obj.get("id")
    if method == "getTask":
        return {"jsonrpc": "2.0", "id": rid, "result": task}, False
    if method == "simEnding":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}, False
    if method == "simEnded":
        return None, True
    if rid is not None:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "method not found"}}, False
    return None, False


def serve(
    conn: socket.socket, task: dict, log: Callable[[str], None], stop: threading.Event | None = None
) -> str:
    """Answer the sim's JSON-RPC until it ends the session or closes the connection; returns how it ended."""
    conn.settimeout(1.0)
    buf = ""
    answered = False
    while stop is None or not stop.is_set():
        try:
            data = conn.recv(65536)
        except TimeoutError:
            continue
        except OSError:  # the sim reset the connection (e.g. on its own exit)
            return "the sim closed the connection"
        if not data:
            return "the sim closed the connection"
        objs, buf = decode_all(buf + data.decode(errors="replace"))
        for obj in objs:
            reply, done = handle(obj, task)
            if reply is not None:
                conn.sendall(json.dumps(reply).encode())
            if obj.get("method") == "getTask" and not answered:
                answered = True
                log("answered getTask: the sim is loading the replay")
            if done:
                return "the sim ended the session"
    return "stopped"


class Launch:
    """One sim start: a ui_port socket and the wrapper spawned on it. `accept` waits for the sim to connect;
    `serve` answers it."""

    def __init__(self, creds: dict, file: str, install: Path, display_mode: str):
        exe = install / LAUNCHER
        if not exe.is_file():
            raise LaunchError(f"the launcher isn't at {exe}")
        self.task = launch_task(creds, file)
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.at = time.time()
        self.proc = subprocess.Popen(
            [str(exe), f"-ui_port={self.port}", f"-display_mode={display_mode}"], cwd=str(install)
        )

    def accept(self, timeout: float) -> socket.socket | None:
        """The sim's connection, once it asks for its task; None if it hasn't within `timeout` seconds."""
        self.srv.settimeout(timeout)
        try:
            conn, _ = self.srv.accept()
        except TimeoutError:
            return None
        self.srv.close()
        return conn

    def give_up(self) -> None:
        self.srv.close()
        if self.proc.poll() is None:
            self.proc.terminate()


def launch_replay(
    creds: dict,
    file: str,
    install: Path,
    log: Callable[[str], None],
    display_mode: str = "monitor",
    connect_timeout: float = 300.0,
) -> dict:
    """Open `file` in the sim with `creds`, and serve it until it exits. Refuses if the sim already runs."""
    if sim_running():
        raise LaunchError("the sim is already running; close it first")
    launch = Launch(creds, file, install, display_mode)
    log(f"launched {LAUNCHER} (pid {launch.proc.pid}) on ui_port {launch.port}, display_mode {display_mode}")
    conn = launch.accept(connect_timeout)
    if conn is None:
        launch.give_up()
        raise LaunchError(f"the sim didn't ask for its replay within {connect_timeout:.0f} s")
    with conn:
        log(serve(conn, launch.task, log))
    return {"pid": launch.proc.pid, "port": launch.port}


def newest(pids: list[int], since: float) -> dict | None:
    """The process of `pids` that started last, at or after `since` (epoch seconds), as `state.alive` knows
    it."""
    best = None
    for pid in pids:
        started = state.started(pid)
        if started is None:
            continue
        epoch = started / 1e7 - 11644473600  # FILETIME: 100 ns ticks since 1601
        if epoch >= since - 5 and (best is None or started > best["started"]):
            best = {"pid": pid, "started": started}
    return best


def _windows(pid: int) -> list[int]:
    """The process's visible top-level windows, else all of them."""
    found: list[tuple[int, bool]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def visit(hwnd, _):
        owner = wt.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            found.append((hwnd, bool(_user32.IsWindowVisible(hwnd))))
        return True

    _user32.EnumWindows(visit, 0)
    visible = [h for h, shown in found if shown]
    return visible or [h for h, _ in found]


def _exited(handle: int, timeout: float) -> bool:
    return _kernel32.WaitForSingleObject(handle, int(timeout * 1000)) == WAIT_OBJECT_0


def close(sim: dict, log: Callable[[str], object], timeout: float = 90.0) -> tuple[bool, str]:
    """Close the sim `sim` ({pid, started}): WM_CLOSE to its windows, which neither focuses nor raises them,
    then, if it hasn't exited after `timeout` seconds, end its process. Returns (whether it ended, how). The
    process is held from the start, so a pid reused meanwhile is never touched."""
    h = _kernel32.OpenProcess(SYNCHRONIZE | TERMINATE | QUERY, False, sim["pid"])
    if not h:
        if not state.alive(sim):
            return True, "the sim had already exited"
        return False, f"can't open the sim's process (error {ctypes.get_last_error()})"
    try:
        if not state.alive(sim):  # the pid names another process now
            return True, "the sim had already exited"
        t0 = time.monotonic()
        for hwnd in _windows(sim["pid"]):
            _user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        if _exited(h, timeout):
            return True, f"the sim exited {time.monotonic() - t0:.0f} s after WM_CLOSE"
        log(f"the sim didn't exit {timeout:.0f} s after WM_CLOSE; ending its process")
        if not _kernel32.TerminateProcess(h, 1):
            return False, f"the sim wouldn't end (TerminateProcess error {ctypes.get_last_error()})"
        if _exited(h, 20):
            return True, "the sim's process was ended"
        return False, "the sim wouldn't end"
    finally:
        _kernel32.CloseHandle(h)
