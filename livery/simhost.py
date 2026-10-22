"""The sim host: the one process that starts the sim for agents, serves it while it runs, and closes it once no
run has wanted it for a while.

A run that finds no sim asks for one (`ensure`); the launch rule (`livery.simrule`) says whether an agent
may start it now. The host is spawned detached, so it outlives the command that asked, and a mutex keeps it
alone. It records the sim it started in the state dir as soon as the sim's process appears, which is how
anything tells an agent-started sim from one the owner opened: only an agent-started sim is ever closed,
and only with the sim lock held, so never under a run (but for a close at once, which gives the run
CLOSE_LOCK_S to stop). A sim the owner opened is never touched.

Every run says it wants the sim while it queues and runs (`want`), so the host can tell when none does. A
launch that fails, however it fails, is recorded, and no run asks again for RETRY_AFTER_S.
"""

import ctypes
import ctypes.wintypes as wt
import json
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
LOCK_SLICE_S = 5.0  # a close waits for the lock in slices, looking again at why between them
RETRY_AFTER_S = 1800.0  # a launch that failed isn't tried again this soon, except by `livery sim up`
LAUNCHING = ("starting", "launching", "connecting", "loading")

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


def _put(path: Path, data: dict) -> None:
    """`state.write_json`, retried: a run reading the record holds it open for a moment, and Windows won't
    replace a file that is open."""
    for attempt in range(20):
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data, indent=1), encoding="utf-8", newline="\n")
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))
    raise OSError(f"couldn't write {path}: it stayed open")


# What the state dir says.


def _record() -> dict:
    return state.read_json(_path(STATE_FILE)) or {}


def host() -> dict | None:
    """The host's record while its process lives: its phase, the replay, the sim it started."""
    info = state.read_json(_path(STATE_FILE))
    return info if info is not None and state.alive(info) else None


def agent_sim() -> dict | None:
    """The sim ({pid, started}) a host started, while it still runs, even if that host died."""
    sim = _record().get("sim")
    return sim if sim and state.alive(sim) else None


def launched_file() -> str | None:
    """The replay file the agent-started sim was launched on."""
    return _record().get("file") if agent_sim() is not None else None


def is_agent(pid: int | None) -> bool:
    sim = agent_sim()
    return sim is not None and pid is not None and sim["pid"] == pid


def closing() -> tuple[str, bool] | None:
    """(why, at once) while the agent-started sim is closing; at once, the run under way has to stop."""
    info = _record()
    if info.get("phase") != "closing" or agent_sim() is None:
        return None
    return str(info.get("closing")), bool(info.get("at_once"))


def last_failure(file: str) -> dict | None:
    """The last launch of `file` that failed, while it is recent enough to stop another try. A host that
    died while launching, before it could say so, counts."""
    f = state.read_json(_path(FAILED_FILE))
    if f and f.get("file") == file and time.time() - float(f.get("at", 0)) <= RETRY_AFTER_S:
        return f
    info = _record()
    since = float(info.get("since") or 0)
    if (
        info.get("phase") in LAUNCHING
        and info.get("file") == file
        and not state.alive(info)
        and agent_sim() is None
        and time.time() - since <= RETRY_AFTER_S
    ):
        return {"at": since, "file": file, "reason": f"the sim host died while {info['phase']}"}
    return None


def clear_failure() -> None:
    _path(FAILED_FILE).unlink(missing_ok=True)
    if host() is None and agent_sim() is None:
        _path(STATE_FILE).unlink(missing_ok=True)


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
    outlives the command that asked. Records it before returning, so the next look finds it starting; a
    dead host's sim, and how far it had got, carry over for the new host to adopt."""
    args = [sys.executable, "-m", "livery", "sim", "host", "--file", file]

    def start(flags: int) -> subprocess.Popen[bytes]:
        with open(_path(LOG_FILE), "ab") as out:
            return subprocess.Popen(
                args,
                cwd=str(state.root()),
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=out,
                creationflags=flags,
            )

    old, orphan = _record(), agent_sim()
    try:
        proc = start(DETACHED_PROCESS | NEW_GROUP | BREAKAWAY)
    except OSError:  # the job this process runs in doesn't allow breaking away
        proc = start(DETACHED_PROCESS | NEW_GROUP)
    record: dict[str, Any] = {"pid": proc.pid, "started": state.started(proc.pid), "file": file}
    record |= {"phase": "starting", "since": time.time(), "checkout": str(REPO)}
    if orphan is not None:
        record |= {"sim": orphan, "adopt_phase": old.get("phase"), "launched_at": old.get("launched_at")}
        record["file"] = old.get("file") or file
    _put(_path(STATE_FILE), record)
    return proc.pid


def adopt() -> bool:
    """Start a host for the agent-started sim a dead host left running, so it still closes when unwanted."""
    if agent_sim() is None or host() is not None:
        return False
    spawn(str(_record().get("file") or ""))
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
    why = simrule.blocked(simrule.settings(), presence.read(), cfg.process, simrule.override())
    if why is not None:
        return f"waiting to start the sim: {why}"
    spawn(file)
    return f"starting the sim on {file} (a launch takes a few minutes)"


def close_agent(cfg: Config, why: str, log: Callable[[str], object]) -> bool:
    """Close the agent-started sim, for a caller that holds the sim lock. False when the running sim isn't
    one an agent started (or none runs), or it wouldn't end."""
    sim = agent_sim()
    if sim is None:
        return False
    _mark_closing(why, at_once=False)
    log(f"closing the agent-started sim: {why}")
    ended, how = simlaunch.close(sim, log)
    log(how)
    return ended


def close_agent_locked(cfg: Config, why: str, log: Callable[[str], object]) -> bool:
    """`close_agent`, taking the sim lock first, so the run under way ends before the sim closes."""
    if agent_sim() is None:
        return False
    with simlock.sim_lock(f"close the agent-started sim: {why}", log=log):
        return close_agent(cfg, why, log)


def _mark_closing(why: str, at_once: bool) -> None:
    info = _record()
    if info:
        _put(_path(STATE_FILE), {**info, "phase": "closing", "closing": why, "at_once": at_once})


# What a launch needs.


def preflight(cfg: Config, file: str) -> list[tuple[str, str | None]]:
    """Everything an agent launch of `file` needs, as (what, problem or None), without starting anything."""
    from livery import members

    checks: list[tuple[str, str | None]] = []
    try:
        launch = simrule.settings()
        checks.append(("settings", None if launch else "config.toml has no [sim] table"))
    except LiveryError as e:
        checks.append(("settings", str(e)))
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
    _put(_path(STATE_FILE), record)


def _replay_open(cfg: Config) -> bool:
    from livery.iracing import Sim

    sim = Sim(cfg)
    try:
        return sim.connected() and sim.is_replay()
    finally:
        sim.close()


def _launch(cfg: Config, file: str, record: dict, log: Callable[[str], None]) -> threading.Event:
    """Credentials, the wrapper, the sim's connection. Records the sim in `record` as soon as its process
    appears, so even a launch that hangs leaves a sim the host can close. Returns the event that stops
    serving it."""
    from livery import cps, members

    problems = [f"{what}: {p}" for what, p in preflight(cfg, file) if p]
    if problems:
        raise LaunchError("; ".join(problems))
    launch_rules = simrule.settings()
    assert launch_rules is not None
    entry = simlaunch.replay_entry(file) or {}
    args = cps.request_args(
        track_id=entry["track_id"], car_id=entry["car_id"], sess_carids=entry["sess_carids"]
    )
    creds = cps.replay_credentials(members.fresh_session(), args)
    if not creds.get("success") or not all(members.credential_fields(creds).values()):
        raise LaunchError("iRacing didn't hand out the replay's credentials")
    launch = simlaunch.Launch(creds, file, cfg.install, launch_rules.display_mode)
    log(f"started {simlaunch.LAUNCHER} (pid {launch.proc.pid}) on ui_port {launch.port}")
    _write(record, phase="connecting", launched_at=launch.at)
    conn = None
    while conn is None:
        conn = launch.accept(POLL_S)
        if record.get("sim") is None:
            sim = simlaunch.newest(capture.pids(cfg.process), launch.at)
            if sim is not None:
                _write(record, sim=sim)
                log(f"the sim's process is up (pid {sim['pid']})")
        if conn is None and time.time() - launch.at > CONNECT_S:
            launch.give_up()
            raise LaunchError(f"the sim didn't ask for its replay within {CONNECT_S:.0f} s")
    if record.get("sim") is None:
        conn.close()
        raise LaunchError(f"the sim connected, but no {cfg.process} started")
    stop = threading.Event()

    def serve() -> None:
        with conn:
            log(simlaunch.serve(conn, launch.task, log, stop))

    threading.Thread(target=serve, daemon=True).start()
    _write(record, phase="loading", connected_s=round(time.time() - launch.at, 1))
    log(f"the sim asked for its replay {time.time() - launch.at:.0f} s after the launch")
    return stop


def _in_front(cfg: Config) -> bool:
    w = capture.Window.find(cfg.process)
    return w is not None and w.in_front()


def _wanted() -> bool:
    return bool(demand()) or simlock.holder() is not None


def _close(cfg: Config, record: dict, sim: dict, why: str, at_once: bool, log: Callable[[str], None]) -> bool:
    """Close the sim the launch rule says must go, with the sim lock held, so never under a run. The record
    says it's closing first, so no run starts on it. The lock is waited for in slices: between them, a close
    that has to happen at once after all is escalated (the run under way stops), and one whose reason has
    passed is dropped. At once, the run gets CLOSE_LOCK_S, then the sim closes regardless. Returns whether the
    sim is gone."""
    before = record.get("phase")
    _write(record, phase="closing", closing=why, at_once=at_once)
    log(f"closing the sim: {why}" + (" (at once)" if at_once else ""))
    t0 = time.monotonic()
    while state.alive(sim):
        try:
            with simlock.sim_lock(f"close the agent-started sim: {why}", LOCK_SLICE_S):
                ended, how = simlaunch.close(sim, log)
                log(how)
                return ended
        except simlock.SimBusy:
            pass
        if at_once and time.monotonic() - t0 >= CLOSE_LOCK_S:
            log("the run didn't stop in time; closing anyway")
            ended, how = simlaunch.close(sim, log)
            log(how)
            return ended
        now = simrule.must_close(simrule.settings(), presence.read(), cfg.process, simrule.override())
        if now is None:
            log("the reason to close has passed; the sim stays")
            break
        if now[1] and not at_once:
            why, at_once, t0 = now[0], True, time.monotonic()
            _write(record, closing=why, at_once=True)
            log(f"closing at once now: {why}")
    if not state.alive(sim):
        return True
    _write(record, phase=before, closing=None, at_once=None)
    return False


def _close_unwanted(record: dict, sim: dict, why: str, log: Callable[[str], None]) -> bool:
    """Close a sim no run wants, if the sim lock is free and no run asks for it meanwhile; the record says
    so only once the lock is held. Returns whether the sim is gone."""
    try:
        with simlock.sim_lock(f"close the agent-started sim: {why}", LOCK_SLICE_S):
            if demand():
                return False
            before = record.get("phase")
            _write(record, phase="closing", closing=why, at_once=False)
            log(f"closing the sim: {why}")
            ended, how = simlaunch.close(sim, log)
            log(how)
            if not ended:
                _write(record, phase=before, closing=None, at_once=None)
            return ended
    except simlock.SimBusy:
        return False


def _watch(cfg: Config, record: dict, sim: dict, log: Callable[[str], None]) -> str:
    """Until the sim exits or is closed: note when the replay is open, and close the sim when the launch
    rule says it must go, or when no run has wanted it for close_after_minutes. Returns why it ended."""
    launched = float(record.get("launched_at") or time.time())
    ready = record.get("phase") == "ready"
    quiet_since = time.time()
    backoff_until = 0.0
    while True:
        if not state.alive(sim):
            if _record().get("phase") == "closing":
                return f"closed: {_record().get('closing')}"
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
        try:
            launch_rules = simrule.settings()
        except LiveryError as e:
            log(f"the [sim] settings don't read ({e}); closing on the defaults")
            launch_rules = None
        p = presence.read()
        if _wanted():
            quiet_since = now
        if now >= backoff_until:
            must = simrule.must_close(launch_rules, p, cfg.process, simrule.override())
            if must is not None and _close(cfg, record, sim, must[0], must[1], log):
                return must[0]
            unwanted = not _wanted() and simrule.idle_close(
                launch_rules, p, now - quiet_since, _in_front(cfg)
            )
            if ready and unwanted:
                why = f"no run has wanted it for {(now - quiet_since) / 60:.0f} min"
                if _close_unwanted(record, sim, why, log):
                    return why
                quiet_since = time.time()
            if state.alive(sim) and _record().get("phase") == "closing":
                backoff_until = time.time() + 300  # a close that didn't take: try again in a while
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

    old = _record()
    orphan = agent_sim()
    record: dict[str, Any] = {**state.me(), "checkout": str(REPO), "file": file, "since": time.time()}
    t0 = time.time()
    stop: threading.Event | None = None
    ended = "failed"
    try:
        if orphan is not None:
            adopted = old.get("adopt_phase") or "ready"
            phase = adopted if adopted in ("loading", "ready") else "loading"
            launched_at = old.get("launched_at") or time.time()
            _write(record, phase=phase, sim=orphan, adopted=True, launched_at=launched_at)
            log(f"adopted the agent-started sim (pid {orphan['pid']}) its host left, {phase}")
        else:
            _write(record, phase="launching")
            if sim_running(cfg):
                raise LaunchError("the sim is already running, and no agent started it")
            log(f"launching the sim on {file}")
            stop = _launch(cfg, file, record, log)
        ended = _watch(cfg, record, record["sim"], log)
        _path(FAILED_FILE).unlink(missing_ok=True)
    except BaseException as e:
        reason = str(e) if isinstance(e, LiveryError) else f"{type(e).__name__}: {e}"
        log(f"the launch failed: {reason}")
        if record.get("phase") != "ready":  # recorded, so the next run doesn't try again at once
            state.write_json(_path(FAILED_FILE), {"at": time.time(), "file": file, "reason": reason})
            sim = record.get("sim")
            if sim is not None and state.alive(sim):  # not ready, so no run uses it
                log(simlaunch.close(sim, log)[1])
        if not isinstance(e, LiveryError):
            raise
    finally:
        if stop is not None:
            stop.set()
        sim = record.get("sim")
        info = _record()
        if info.get("pid") == os.getpid() and not (sim and state.alive(sim)):
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
