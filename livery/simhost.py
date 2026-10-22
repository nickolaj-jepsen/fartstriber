"""The sim host: the one process that starts the sim for agents, serves it while it runs, and closes it once no
run has wanted it for a while.

A run that finds no sim asks for one (`ensure`); the launch rule (`livery.simrule`) says whether an agent
may start it now. The host is spawned detached, so it outlives the command that asked, and a mutex keeps it
alone. It records the sim it started in the state dir, which is how anything tells an agent-started sim from
one the owner opened: only an agent-started sim is ever closed, and only with the sim lock held, so never
under a run. A sim the owner opened is never touched.

Every run says it wants the sim while it queues and runs (`want`), so the host can tell when none does.
"""

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from livery import capture, metrics, presence, simlaunch, simlock, simrule, state
from livery.config import REPO, Config
from livery.errors import LiveryError
from livery.simlaunch import LaunchError

MUTEX = "Local\\fartstriber-simhost"
STATE_FILE = "sim-host.json"
LOG_FILE = "sim-host.log"
FAILED_FILE = "sim-launch-failed.json"
DEMAND_DIR = "sim-demand"
POLL_S = 2.0
CONNECT_S = 300.0  # the wrapper and the sim's own start, until it asks for its task
READY_S = 900.0  # from the launch until the replay is open
CLOSE_LOCK_S = 10.0  # how long a run gets to stop when the sim has to close at once
RETRY_AFTER_S = 1800.0  # a launch that failed isn't tried again this soon, except by `livery sim up`

DETACHED_PROCESS, NEW_GROUP, BREAKAWAY = 0x00000008, 0x00000200, 0x01000000

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.restype = wt.HANDLE
_kernel32.CreateMutexW.argtypes = (wt.LPVOID, wt.BOOL, wt.LPCWSTR)
_kernel32.WaitForSingleObject.restype = wt.DWORD
_kernel32.WaitForSingleObject.argtypes = (wt.HANDLE, wt.DWORD)
_kernel32.ReleaseMutex.argtypes = (wt.HANDLE,)
_kernel32.CloseHandle.argtypes = (wt.HANDLE,)


def _path(name: str) -> Path:
    return state.root() / name


# What the state dir says.


def host() -> dict | None:
    """The host's record while its process lives: its phase, the replay, the sim it started."""
    info = state.read_json(_path(STATE_FILE))
    return info if info is not None and state.alive(info) else None


def agent_sim() -> dict | None:
    """The sim ({pid, started}) a host started, while it still runs, even if that host died."""
    info = state.read_json(_path(STATE_FILE))
    sim = (info or {}).get("sim")
    return sim if sim and state.alive(sim) else None


def is_agent(pid: int | None) -> bool:
    sim = agent_sim()
    return sim is not None and pid is not None and sim["pid"] == pid


def closing() -> tuple[str, bool] | None:
    """(why, at once) while the host is closing its sim; at once, the run under way has to stop."""
    h = host()
    if h is None or h.get("phase") != "closing":
        return None
    return str(h.get("closing")), bool(h.get("at_once"))


def last_failure(file: str) -> dict | None:
    """The last launch of `file` that failed, while it is recent enough to stop another try."""
    f = state.read_json(_path(FAILED_FILE))
    if not f or f.get("file") != file or time.time() - float(f.get("at", 0)) > RETRY_AFTER_S:
        return None
    return f


def clear_failure() -> None:
    _path(FAILED_FILE).unlink(missing_ok=True)


def wait_ready(log: Callable[[str], object], timeout: float = 0.0) -> dict:
    """Until the host says the replay is open, telling each phase once. Raises LaunchError with the
    host's reason when the launch fails."""
    t0 = time.time()
    timeout = timeout or CONNECT_S + READY_S + 60
    said = None
    while time.time() - t0 < timeout:
        h = host()
        if h is None:
            f = state.read_json(_path(FAILED_FILE))
            if f and float(f.get("at", 0)) >= t0 - 5:
                raise LaunchError(f"the launch failed: {f['reason']}")
            raise LaunchError(f"the sim host ended without opening the replay; {_path(LOG_FILE)} says why")
        if h.get("phase") != said:
            said = h.get("phase")
            log(f"sim host: {said}")
        if said == "ready":
            return {"file": h.get("file"), "ready_s": h.get("ready_s") or 0.0, "sim": h.get("sim")}
        time.sleep(POLL_S)
    raise LaunchError(f"the replay didn't open within {timeout / 60:.0f} min")


@contextmanager
def want(what: str) -> Iterator[None]:
    """While the block runs, this process wants the sim: the host doesn't close it as unwanted."""
    me = state.me()
    path = _path(DEMAND_DIR) / f"{me['pid']}.json"
    state.write_json(path, {**me, "what": what, "since": time.time()})
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def demand() -> list[dict]:
    """The runs that want the sim now; a dead run's file is removed."""
    found = []
    for f in sorted(_path(DEMAND_DIR).glob("*.json")):
        info = state.read_json(f)
        if info is not None and state.alive(info):
            found.append(info)
        else:
            f.unlink(missing_ok=True)
    return found


# Asking for the sim.


def spawn(file: str) -> int:
    """Start a host for `file`, detached from this process (and from its job, where the job allows it) so it
    outlives the command that asked. Records it before returning, so the next look finds it starting."""
    args = [sys.executable, "-m", "livery", "sim", "host", "--file", file]

    def start(flags: int) -> subprocess.Popen[bytes]:
        with open(_path(LOG_FILE), "ab") as out:
            return subprocess.Popen(
                args, cwd=str(REPO), stdin=subprocess.DEVNULL, stdout=out, stderr=out, creationflags=flags
            )

    try:
        proc = start(DETACHED_PROCESS | NEW_GROUP | BREAKAWAY)
    except OSError:  # the job this process runs in doesn't allow breaking away
        proc = start(DETACHED_PROCESS | NEW_GROUP)
    record = {"pid": proc.pid, "started": state.started(proc.pid), "file": file, "phase": "starting"}
    orphan = agent_sim()  # a dead host's sim, which the new host adopts
    record |= {"since": time.time(), "checkout": str(REPO), "sim": orphan}
    state.write_json(_path(STATE_FILE), record)
    return proc.pid


def adopt(cfg: Config) -> bool:
    """Start a host for the agent-started sim a dead host left running, so it still closes when unwanted."""
    info = state.read_json(_path(STATE_FILE)) or {}
    if agent_sim() is None or host() is not None:
        return False
    spawn(str(info.get("file") or cfg.replay.file))
    return True


def ensure(cfg: Config, file: str) -> str:
    """For a run that found no sim: start one if the launch rule allows it now. Says what happens next."""
    h = host()
    if h is not None and h.get("phase") == "closing":
        return "waiting for the agent-started sim to close"
    if h is not None:
        return f"the sim is starting for agents ({h.get('phase')})"
    failed = last_failure(file)
    if failed is not None:
        ago = (time.time() - float(failed["at"])) / 60
        raise LaunchError(
            f"the agent launch of {file} failed {ago:.0f} min ago: {failed['reason']}; "
            "`livery sim up` tries again now"
        )
    why = simrule.blocked(cfg.launch, presence.read(), cfg.process, simrule.override())
    if why is not None:
        return f"waiting to start the sim: {why}"
    spawn(file)
    return f"starting the sim on {file} (a launch takes a few minutes)"


def close_agent(cfg: Config, why: str, log: Callable[[str], object]) -> bool:
    """Close the agent-started sim, for a caller that holds the sim lock. False when the running sim isn't
    one an agent started (or none runs)."""
    sim = agent_sim()
    if sim is None:
        return False
    _mark_closing(why, at_once=False)
    log(f"closing the agent-started sim: {why}")
    log(simlaunch.close(sim, log))
    return True


def close_agent_locked(cfg: Config, why: str, log: Callable[[str], object]) -> bool:
    """`close_agent`, taking the sim lock first, so the run under way ends before the sim closes."""
    if agent_sim() is None:
        return False
    with simlock.sim_lock(f"close the agent-started sim: {why}", log=log):
        return close_agent(cfg, why, log)


def _mark_closing(why: str, at_once: bool) -> None:
    info = state.read_json(_path(STATE_FILE))
    if info is not None:
        state.write_json(_path(STATE_FILE), {**info, "phase": "closing", "closing": why, "at_once": at_once})


# What a launch needs.


def preflight(cfg: Config, file: str) -> list[tuple[str, str | None]]:
    """Everything an agent launch of `file` needs, as (what, problem or None), without starting anything."""
    from livery import members

    checks: list[tuple[str, str | None]] = []
    checks.append(("settings", None if cfg.launch else "config.toml has no [sim] table"))
    launcher = cfg.install / simlaunch.LAUNCHER
    checks.append(("launcher", None if launcher.is_file() else f"no {launcher}"))
    replay = cfg.documents / "replay" / file
    checks.append(("replay file", None if replay.is_file() else f"no {replay}"))
    entry = simlaunch.replay_entry(file)
    listed = entry is not None and entry.get("track_id") is not None
    checks.append(("replay list", None if listed else f"the iRacing service doesn't list {file}"))
    try:
        members.fresh_session()
        signed = None
    except LiveryError as e:
        signed = str(e)
    checks.append(("sign-in", signed))
    return checks


# The host.


def _log_to(path: Path) -> Callable[[str], None]:
    def log(msg: str) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{os.getpid()}] {msg}\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)

    return log


def _write(record: dict, **change) -> None:
    record.update(change)
    state.write_json(_path(STATE_FILE), record)


def _replay_open(cfg: Config) -> bool:
    from livery.iracing import Sim

    sim = Sim(cfg)
    try:
        return sim.connected() and sim.is_replay()
    finally:
        sim.close()


def _launch(cfg: Config, file: str, record: dict, log: Callable[[str], None]) -> tuple[dict, threading.Event]:
    """Credentials, the wrapper, the sim's connection: returns the sim and the event that stops serving it."""
    from livery import cps, members

    problems = [f"{what}: {p}" for what, p in preflight(cfg, file) if p]
    if problems:
        raise LaunchError("; ".join(problems))
    assert cfg.launch is not None
    entry = simlaunch.replay_entry(file) or {}
    args = cps.request_args(
        track_id=entry["track_id"], car_id=entry["car_id"], sess_carids=entry["sess_carids"]
    )
    creds = cps.replay_credentials(members.fresh_session(), args)
    if not creds.get("success") or not all(members.credential_fields(creds).values()):
        raise LaunchError("iRacing didn't hand out the replay's credentials")
    launch = simlaunch.Launch(creds, file, cfg.install, cfg.launch.display_mode)
    log(f"started {simlaunch.LAUNCHER} (pid {launch.proc.pid}) on ui_port {launch.port}")
    _write(record, phase="connecting", launched_at=launch.at)
    conn = launch.accept(CONNECT_S)
    sim = simlaunch.newest(capture.pids(cfg.process), launch.at)
    if sim is None:
        conn.close()
        raise LaunchError(f"the sim connected, but no {cfg.process} started")
    stop = threading.Event()

    def serve() -> None:
        with conn:
            log(simlaunch.serve(conn, launch.task, log, stop))

    threading.Thread(target=serve, daemon=True).start()
    _write(record, phase="loading", sim=sim, connected_s=round(time.time() - launch.at, 1))
    log(f"the sim (pid {sim['pid']}) connected {time.time() - launch.at:.0f} s after the launch")
    return sim, stop


def _in_front(cfg: Config) -> bool:
    w = capture.Window.find(cfg.process)
    return w is not None and w.in_front()


def _close(cfg: Config, record: dict, sim: dict, why: str, at_once: bool, log: Callable[[str], None]) -> bool:
    """Close the sim with the sim lock held, so never under a run: at once gives the run CLOSE_LOCK_S to
    stop, then closes regardless. False when an unwanted sim's lock was taken meanwhile: it stays."""
    _write(record, phase="closing", closing=why, at_once=at_once)
    log(f"closing the sim: {why}")
    timeout = CLOSE_LOCK_S if at_once else 3600.0
    try:
        with simlock.sim_lock(f"close the agent-started sim: {why}", timeout):
            log(simlaunch.close(sim, log))
    except simlock.SimBusy:
        if not at_once:
            _write(record, phase="ready", closing=None, at_once=None)
            return False
        log("the run didn't stop in time; closing anyway")
        log(simlaunch.close(sim, log))
    return True


def _watch(cfg: Config, record: dict, sim: dict, log: Callable[[str], None]) -> str:
    """Until the sim exits or is closed: note when the replay is open, and close the sim when the launch
    rule says it must go, or when no run has wanted it for close_after_minutes. Returns why it ended."""
    launched = float(record.get("launched_at") or time.time())
    ready = record.get("phase") == "ready"
    quiet_since = time.time()
    while True:
        if not state.alive(sim):
            if not ready:
                raise LaunchError("the sim exited before the replay opened")
            return "the sim exited"
        now = time.time()
        if not ready:
            if _replay_open(cfg):
                ready = True
                _write(record, phase="ready", ready_s=round(now - launched, 1))
                log(f"the replay is open, {now - launched:.0f} s after the launch")
            elif now - launched > READY_S:
                raise LaunchError(f"the replay didn't open within {READY_S / 60:.0f} min")
        p = presence.read()
        if demand() or simlock.holder() is not None:
            quiet_since = now
        must = simrule.must_close(cfg.launch, p, cfg.process, simrule.override())
        if must is not None and _close(cfg, record, sim, must[0], must[1], log):
            return must[0]
        if ready and simrule.idle_close(cfg.launch, p, now - quiet_since, _in_front(cfg)):
            why = f"no run has wanted it for {(now - quiet_since) / 60:.0f} min"
            if _close(cfg, record, sim, why, False, log):
                return why
            quiet_since = time.time()
        time.sleep(POLL_S)


def run(cfg: Config, file: str) -> None:
    """The host: launch the sim on `file` (or adopt the sim a dead host started), serve and watch it, and
    clear the record when it's gone. Exits at once if another host runs."""
    log = _log_to(_path(LOG_FILE))
    h = _kernel32.CreateMutexW(None, False, MUTEX)
    if not h:
        raise OSError(ctypes.get_last_error(), f"can't create {MUTEX}")
    try:
        if _kernel32.WaitForSingleObject(h, 0) not in (0, 0x80):
            log("another sim host runs; leaving it to that one")
            return
        try:
            _host(cfg, file, log)
        finally:
            _kernel32.ReleaseMutex(h)
    finally:
        _kernel32.CloseHandle(h)


def _host(cfg: Config, file: str, log: Callable[[str], None]) -> None:
    from livery.iracing import sim_running

    orphan = agent_sim()
    record: dict[str, Any] = {
        **state.me(),
        "checkout": str(REPO),
        "file": file,
        "phase": "launching",
        "since": time.time(),
    }
    t0 = time.time()
    sim: dict | None = None
    stop: threading.Event | None = None
    ended = "failed"
    try:
        if orphan is not None:
            sim = orphan
            _write(record, phase="ready", sim=sim, adopted=True, launched_at=time.time())
            log(f"adopted the agent-started sim (pid {sim['pid']}) its host left")
        else:
            _write(record)
            if sim_running(cfg):
                raise LaunchError("the sim is already running, and no agent started it")
            log(f"launching the sim on {file}")
            sim, stop = _launch(cfg, file, record, log)
        ended = _watch(cfg, record, sim, log)
        _path(FAILED_FILE).unlink(missing_ok=True)
    except LiveryError as e:  # recorded, so the next run doesn't try again at once
        log(f"the launch failed: {e}")
        state.write_json(_path(FAILED_FILE), {"at": time.time(), "file": file, "reason": str(e)})
        if sim is not None and state.alive(sim):  # not ready yet, so no run uses it
            log(simlaunch.close(sim, log))
    except Exception as e:  # a bug: the sim stays, and the record with it, for the next host to adopt
        log(f"the host failed: {type(e).__name__}: {e}")
        raise
    finally:
        if stop is not None:
            stop.set()
        info = state.read_json(_path(STATE_FILE))
        if info is not None and info.get("pid") == os.getpid() and not (sim and state.alive(sim)):
            _path(STATE_FILE).unlink(missing_ok=True)
        metrics.log(
            "",
            "sim-host",
            {
                "connect": record.get("connected_s"),
                "ready": record.get("ready_s"),
                "total": round(time.time() - t0, 1),
            },
            file=file,
            ended=ended,
            adopted=bool(record.get("adopted")),
        )
        log(f"host done: {ended}")
