"""Open a replay in the sim the way the iRacing UI does: spawn the sim through the anti-cheat wrapper with a
`-ui_port`, then answer the sim's `getTask` request over a local JSON-RPC socket with the replay task (the
.rpy file and the launch credentials). The service endpoint `/play_replay` is not used by the current UI;
only this spawn + RPC path launches a replay.

The sim holds the RPC connection until it exits, so a launch blocks, serving the sim, for the whole session.
"""

import json
import re
import socket
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from livery.errors import LiveryError

SERVICE = "http://127.0.0.1:32034"
SIM_STATUS = "/get_sim_status"
REPLAY_LIST = "/get_replay_list"
SIM_EXE = "iRacingSim64DX11.exe"  # the sim process that connects back to the ui_port
# The UI launches the sim through the Epic anti-cheat wrapper whenever the anti-cheat engine is EOS (the
# current one), even for a replay — the sim's own tamper check fails when started directly. A replay needs
# no access token, so the wrapper just passes the args through.
LAUNCHER = "start_protected_game.exe"
TASK_NAME = "legacyPlayReplay"


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
    """Pull one saved replay's track, car and participant ids out of the service's replay list (a JS object
    literal `var replaydata={replays:[{file:"..",trackID:..,carID:..,allCustIDs:[..]},..]}`)."""
    for block in re.findall(r"\{[^{}]*\}", body):  # each replay entry has no nested braces, only [] arrays
        name = re.search(r'file:"([^"]+)"', block)
        if not name or name.group(1) != file:
            continue
        custs = re.search(r"allCustIDs:\[([^\]]*)\]", block)
        raw = [int(x) for x in custs.group(1).split(",") if x.strip().lstrip("-").isdigit()] if custs else []
        # 4294967295 (0xFFFFFFFF) is the empty-grid-slot sentinel, not a participant; drop it and non-ids
        ids = [i for i in raw if 0 < i < 0xFFFFFFFF]
        return {"track_id": _num(block, "trackID"), "car_id": _num(block, "carID"), "sess_carids": ids}
    return None


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


def _serve(conn: socket.socket, task: dict, log: Callable[[str], None]) -> None:
    """Answer the sim's JSON-RPC until it ends or closes the connection."""
    conn.settimeout(1.0)
    buf = ""
    answered = False
    while True:
        try:
            data = conn.recv(65536)
        except TimeoutError:
            continue
        except OSError:  # the sim reset the connection (e.g. on its own exit)
            log("the sim closed the connection")
            return
        if not data:
            log("the sim closed the connection")
            return
        objs, buf = decode_all(buf + data.decode(errors="replace"))
        for obj in objs:
            reply, done = handle(obj, task)
            if reply is not None:
                conn.sendall(json.dumps(reply).encode())
            if obj.get("method") == "getTask" and not answered:
                answered = True
                log("answered getTask: the sim is loading the replay")
            if done:
                log("the sim ended the session")
                return


# The display modes the UI accepts; the sim is told which so it doesn't fall back to the VR headset path.
DISPLAY_MODES = ("monitor", "oculus", "openvr", "openxr")


def launch_replay(
    creds: dict,
    file: str,
    install: Path,
    log: Callable[[str], None],
    display_mode: str = "monitor",
    connect_timeout: float = 90.0,
) -> dict:
    """Open `file` in the sim with `creds`: spawn the sim on a fresh ui_port, telling it the display mode,
    and serve its getTask over a local socket. Blocks, serving the sim, until it exits. Refuses if the sim
    is already running."""
    if sim_running():
        raise LiveryError("the sim is already running; close it first")
    exe = install / LAUNCHER
    if not exe.is_file():
        raise LiveryError(f"the launcher isn't at {exe}")
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    srv.settimeout(connect_timeout)
    port = srv.getsockname()[1]
    task = launch_task(creds, file)
    proc = subprocess.Popen([str(exe), f"-ui_port={port}", f"-display_mode={display_mode}"])
    log(f"launched {LAUNCHER} (pid {proc.pid}) on ui_port {port}, display_mode {display_mode}")
    try:
        conn, _ = srv.accept()
    except TimeoutError:
        proc.terminate()
        raise LiveryError(f"the sim didn't connect within {connect_timeout:.0f}s") from None
    finally:
        srv.close()
    with conn:
        _serve(conn, task, log)
    return {"pid": proc.pid, "port": port}
