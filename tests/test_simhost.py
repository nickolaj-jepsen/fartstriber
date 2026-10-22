"""The sim host's records: who wants the sim, which sim an agent started, a failed launch, and what a run
that finds no sim is told."""

import subprocess
import sys
import time

import pytest

from livery import config, presence, simhost, simrule, state
from livery.presence import Presence
from livery.simlaunch import LaunchError
from livery.simrule import Launch

RULES = Launch(
    rule="idle",
    idle_minutes=5,
    close_after_minutes=30,
    display_mode="monitor",
    full_screen_stops=True,
    battery_stops=True,
    ui_closes=True,
)
AWAY = Presence(idle_s=3600, notification=presence.ACCEPTS, foreground="explorer.exe", battery=False)
HERE = Presence(idle_s=2, notification=presence.ACCEPTS, foreground="Code.exe", battery=False)


@pytest.fixture
def dead() -> dict:
    """A process that has exited, as `state.me` would have named it."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    started = state.started(p.pid)
    p.wait()
    return {"pid": p.pid, "started": started}


def record(**fields) -> None:
    state.write_json(state.root() / simhost.STATE_FILE, fields)


def test_a_run_wants_the_sim_only_while_it_runs():
    assert simhost.demand() == []
    with simhost.want("preview x"):
        assert [d["what"] for d in simhost.demand()] == ["preview x"]
    assert simhost.demand() == []


def test_a_dead_runs_want_is_dropped(dead):
    state.write_json(state.root() / simhost.DEMAND_DIR / "1.json", {**dead, "what": "preview x"})
    assert simhost.demand() == []
    assert not list((state.root() / simhost.DEMAND_DIR).iterdir())


def test_an_agent_sim_is_the_running_one_a_host_recorded(dead):
    me = state.me()
    record(**dead, phase="ready", sim=me)  # the host died; the sim it started still runs
    assert simhost.host() is None
    assert simhost.agent_sim() == me
    assert simhost.is_agent(me["pid"])
    record(**me, phase="ready", sim=dead)
    assert simhost.agent_sim() is None
    assert not simhost.is_agent(dead["pid"])


def test_closing_is_told_only_while_the_host_closes_its_sim():
    me = state.me()
    record(**me, phase="ready", sim=me)
    assert simhost.closing() is None
    record(**me, phase="closing", closing="a hold", sim=me)
    assert simhost.closing() == "a hold"


def test_a_failed_launch_holds_off_a_retry_of_that_replay_for_a_while():
    state.write_json(state.root() / simhost.FAILED_FILE, {"at": time.time(), "file": "a.rpy", "reason": "x"})
    assert simhost.last_failure("a.rpy") is not None
    assert simhost.last_failure("b.rpy") is None
    old = time.time() - simhost.RETRY_AFTER_S - 1
    state.write_json(state.root() / simhost.FAILED_FILE, {"at": old, "file": "a.rpy", "reason": "x"})
    assert simhost.last_failure("a.rpy") is None


@pytest.fixture
def spawned(monkeypatch) -> list[str]:
    started: list[str] = []
    monkeypatch.setattr(simhost, "spawn", lambda file: started.append(file) or 1)
    return started


def test_a_run_that_finds_no_sim_starts_one_once_the_owner_is_away(monkeypatch, spawned):
    cfg = config.load()
    monkeypatch.setattr(simrule, "settings", lambda path=None: RULES)
    monkeypatch.setattr(presence, "read", lambda: HERE)
    simhost.ensure(cfg, "a.rpy")
    assert spawned == []
    monkeypatch.setattr(presence, "read", lambda: AWAY)
    simhost.ensure(cfg, "a.rpy")
    assert spawned == ["a.rpy"]


def test_a_run_doesnt_start_a_second_host(monkeypatch, spawned):
    cfg = config.load()
    monkeypatch.setattr(simrule, "settings", lambda path=None: RULES)
    monkeypatch.setattr(presence, "read", lambda: AWAY)
    record(**state.me(), phase="loading")
    simhost.ensure(cfg, "a.rpy")
    assert spawned == []


def test_a_run_after_a_failed_launch_is_told_why_and_starts_nothing(monkeypatch, spawned):
    cfg = config.load()
    monkeypatch.setattr(simrule, "settings", lambda path=None: RULES)
    monkeypatch.setattr(presence, "read", lambda: AWAY)
    state.write_json(state.root() / simhost.FAILED_FILE, {"at": time.time(), "file": "a.rpy", "reason": "x"})
    with pytest.raises(LaunchError):
        simhost.ensure(cfg, "a.rpy")
    assert spawned == []


def test_a_host_that_died_while_launching_holds_off_the_next_try(dead, monkeypatch, spawned):
    cfg = config.load()
    monkeypatch.setattr(simrule, "settings", lambda path=None: RULES)
    monkeypatch.setattr(presence, "read", lambda: AWAY)
    record(**dead, phase="connecting", file="a.rpy", since=time.time())
    with pytest.raises(LaunchError):
        simhost.ensure(cfg, "a.rpy")
    assert spawned == []


# Closing: only an agent's sim, only under the sim lock, and never one a run wants.


@pytest.fixture
def closes(monkeypatch) -> list[dict]:
    """Every sim `simlaunch.close` is asked to close; the sim 'ends' when it is."""
    asked: list[dict] = []
    monkeypatch.setattr(
        simhost.simlaunch, "close", lambda sim, log, timeout=90.0: (asked.append(sim), (True, "closed"))[1]
    )
    monkeypatch.setattr(simhost, "LOCK_SLICE_S", 0.2)
    return asked


@pytest.fixture
def held():
    """The sim lock, held by a run on another thread until the test lets go."""
    import threading

    from livery import simlock

    go, taken = threading.Event(), threading.Event()

    def hold():
        with simlock.sim_lock("a run", timeout=5):
            taken.set()
            go.wait(30)

    t = threading.Thread(target=hold)
    t.start()
    taken.wait(5)
    yield go
    go.set()
    t.join(10)


def test_closing_without_an_agents_sim_closes_nothing(closes):
    assert simhost.agent_sim() is None
    assert simhost.close_agent(config.load(), "x", print) is False
    assert simhost.close_agent_locked(config.load(), "x", print) is False
    assert closes == []


def test_an_unwanted_sim_stays_while_a_run_wants_it(closes):
    me = state.me()
    rec = {**me, "phase": "ready", "sim": me}
    with simhost.want("a queued run"):
        assert simhost._close_unwanted(rec, me, "unwanted", print) is False
    assert closes == []
    assert simhost._close_unwanted(rec, me, "unwanted", print) is True
    assert closes == [me]


def test_an_unwanted_sim_stays_while_a_run_holds_the_lock(closes, held):
    me = state.me()
    assert simhost._close_unwanted({**me, "phase": "ready", "sim": me}, me, "unwanted", print) is False
    assert closes == []


def test_a_close_waits_for_the_run_under_way(closes, held, monkeypatch):
    import threading

    me = state.me()
    monkeypatch.setattr(simrule, "must_close", lambda *a: "a hold")
    rec = {**me, "phase": "ready", "sim": me}
    threading.Timer(1.0, held.set).start()  # the run ends
    t0 = time.monotonic()
    assert simhost._close(config.load(), rec, me, "a hold", print) is True
    assert time.monotonic() - t0 >= 1.0
    assert closes == [me]


def test_a_close_after_the_run_is_dropped_once_its_reason_passes(closes, held, monkeypatch):
    me = state.me()
    monkeypatch.setattr(simrule, "must_close", lambda *a: None)
    rec = {**me, "phase": "ready", "sim": me}
    assert simhost._close(config.load(), rec, me, "a hold", print) is False
    assert closes == []
    assert rec["phase"] == "ready"


def test_a_launch_that_fails_any_way_is_recorded(monkeypatch, closes):
    def boom(*a):
        raise RuntimeError("socket trouble")

    monkeypatch.setattr(simhost, "_launch", boom)
    monkeypatch.setattr(simhost.metrics, "log", lambda *a, **k: None)
    monkeypatch.setattr("livery.iracing.sim_running", lambda cfg: False)
    with pytest.raises(RuntimeError):
        simhost._host(config.load(), "a.rpy", print)
    assert simhost.last_failure("a.rpy") is not None
