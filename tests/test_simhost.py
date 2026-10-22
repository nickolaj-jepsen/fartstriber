"""The sim host's records: who wants the sim, which sim an agent started, a failed launch, and what a run
that finds no sim is told."""

import subprocess
import sys
import time
from dataclasses import replace

import pytest

from livery import config, presence, simhost, state
from livery.config import Launch
from livery.presence import Presence
from livery.simlaunch import LaunchError

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
    record(**me, phase="closing", closing="a full-screen game", at_once=True, sim=me)
    assert simhost.closing() == ("a full-screen game", True)


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
    cfg = replace(config.load(), launch=RULES)
    monkeypatch.setattr(presence, "read", lambda: HERE)
    assert "at the PC" in simhost.ensure(cfg, "a.rpy")
    assert spawned == []
    monkeypatch.setattr(presence, "read", lambda: AWAY)
    simhost.ensure(cfg, "a.rpy")
    assert spawned == ["a.rpy"]


def test_a_run_doesnt_start_a_second_host(monkeypatch, spawned):
    cfg = replace(config.load(), launch=RULES)
    monkeypatch.setattr(presence, "read", lambda: AWAY)
    record(**state.me(), phase="loading")
    simhost.ensure(cfg, "a.rpy")
    assert spawned == []


def test_a_run_after_a_failed_launch_is_told_why_and_starts_nothing(monkeypatch, spawned):
    cfg = replace(config.load(), launch=RULES)
    monkeypatch.setattr(presence, "read", lambda: AWAY)
    state.write_json(state.root() / simhost.FAILED_FILE, {"at": time.time(), "file": "a.rpy", "reason": "x"})
    with pytest.raises(LaunchError):
        simhost.ensure(cfg, "a.rpy")
    assert spawned == []
