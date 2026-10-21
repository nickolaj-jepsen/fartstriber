"""The refine workflow's logic, run under Node with scripted agents: which agents it starts and where it
stops. Nothing here runs a builder, a critic or the sim."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
REFINE = ROOT / ".claude" / "workflows" / "livery-refine.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="the workflow scripts run under Node")

NOTE = "the pixels are missing from the front"
ENDORSED = "Mirror marks don't read from any camera"


def _status(n: int, ready: bool = False, **more) -> dict:
    holds = [] if ready else [f"{NOTE}: partly resolved"]
    return {
        "design": "d",
        "round": n,
        "ready": ready,
        "holds": holds,
        "ship": False,
        "call": "iterate",
        "why": [*holds, "open fix: the critic's own"],  # the critic's own fixes never hold it back
        "plateau": False,
        "baked": False,
    } | more


VERDICT = {
    "previous": [],
    "owner_note": {"status": "partly", "evidence": "front34.png"},
    "endorsed": [],
    "taste": [],
    "scores": {},
    "overall": 7,
    "verdict": "iterate",
    "fixes": [{"title": "the critic's own", "detail": "", "where": "", "optional": False}],
    "lint_missed": [],
    "blocked": [],
}
BUILT = {
    "design": "d",
    "checked": True,
    "sim_run": ".cache/designs/d/sim/20260101-000000",
    "blocked": [],
    "kit": [],
    "character": "",
    "sim": "",
    "constraints": [],
    "summary": "worked the note",
}


def _run(args: dict, statuses: list[dict], *, compare: str = "better", **answers) -> dict:
    """The workflow with `args`, where round n's critic status is statuses[n - 1] (and its compare's)."""
    scripted = [[f"^{k}$", v] for k, v in answers.items()]
    scripted += [
        ["^bake status$", {"ok": True, "checked": False, "usable": False}],
        ["^r\\d build", BUILT],
        ["^r\\d pack$", {"ok": True, "folder": "pack"}],
        ["^r\\d critic$", VERDICT],
        ["^r\\d compare packs$", {"ok": True, "folders": ["a", "b"]}],
        ["^r\\d compare \\d$", {"better": "A", "where": "front34"}],
    ]
    for n, st in enumerate(statuses, 1):
        scripted += [
            [f"^r{n} record$", {"ok": True, "status": st}],
            [f"^r{n} compare$", {"ok": True, "result": compare, "status": st}],
        ]
    scenario = {"script": str(REFINE), "args": {"design": "d"} | args, "answers": scripted}
    done = subprocess.run(
        [str(NODE), str(Path(__file__).parent / "workflow_harness.js")],
        input=json.dumps(scenario),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(done.stdout)


def _labels(out: dict, pattern: str = "") -> list[str]:
    return [c["label"] for c in out["calls"] if pattern in c["label"]]


@pytest.mark.parametrize("ready", [True, False])
def test_a_quick_fix_is_one_round_on_the_owners_note_then_back_to_them(ready):
    out = _run(
        {"strategy": "quick", "note": NOTE, "endorsed": [ENDORSED]},
        [_status(5, ready), _status(6, True)],  # round 5 of the design: earlier refines ran before
    )

    result = out["result"]
    assert result["stop"] == ("ready" if ready else "quick")
    assert result["rounds"] == 1
    assert result["review"]  # back to the owner either way
    assert not _labels(out, "compare")
    assert not _labels(out, "adversary")
    (build,) = [c for c in out["calls"] if c["agentType"] == "livery-builder"]
    assert NOTE in build["prompt"]
    assert ENDORSED in build["prompt"]


def test_a_full_round_goes_back_to_the_owner_once_their_asks_are_met_though_the_critic_has_more():
    out = _run({"strategy": "full", "note": NOTE}, [_status(1), _status(2), _status(3, True), _status(4)])

    result = out["result"]
    assert result["stop"] == "ready"
    assert result["rounds"] == 3
    assert result["review"]
    assert _labels(out, "compare packs") == ["r2 compare packs"]  # round 3 was ready: no compare needed
    assert not _labels(out, "adversary")
    builds = [c for c in out["calls"] if c["agentType"] == "livery-builder"]
    assert all(NOTE in b["prompt"] for b in builds)  # every round answers to the owner's note


def test_a_full_round_stops_at_its_budget_and_goes_back_to_the_owner():
    out = _run({"note": NOTE}, [_status(n) for n in range(1, 6)])

    assert out["result"]["stop"] == "budget"
    assert out["result"]["rounds"] == 4
    assert out["result"]["review"]


def test_a_plateau_no_variant_breaks_goes_back_to_the_owner():
    variant = {"design": "d-alt", "rendered": False, "summary": "", "problems": []}
    out = _run(
        {"note": NOTE},
        [_status(1), _status(2, plateau=True), _status(3)],
        compare="same",
        **{"d-alt\\d": variant},
    )

    assert out["result"]["stop"] == "plateau"
    assert out["result"]["rounds"] == 2
    assert out["result"]["review"]


def test_a_blocked_fix_goes_to_the_owner_while_the_checked_build_is_still_judged():
    out = _run(
        {"strategy": "quick", "note": NOTE},
        [_status(1)],
        **{"r1 build": BUILT | {"blocked": ["the kit map"]}},
    )

    assert out["result"]["stop"] == "quick"
    assert any("the kit map" in line for line in out["result"]["for_owner"])
    assert [c for c in out["calls"] if c["agentType"] == "livery-critic"]


def test_a_blocked_fix_on_a_build_that_cant_be_judged_stops_before_any_critic():
    stuck = BUILT | {"blocked": ["the kit map"], "checked": False}
    out = _run({"strategy": "quick", "note": NOTE}, [_status(1, True)], **{"r1 build": stuck})

    assert out["result"]["stop"] == "blocked"
    assert out["result"]["review"] is None
    assert not [c for c in out["calls"] if c["agentType"] == "livery-critic"]


def test_a_full_round_stops_on_what_the_critic_sees_blocked_once_no_required_fix_is_left():
    stuck = VERDICT | {"blocked": ["the beltline steps at the door"], "fixes": []}
    out = _run({"note": NOTE}, [_status(1)], **{"r1 critic": stuck})

    assert out["result"]["stop"] == "blocked"
    assert out["result"]["rounds"] == 1


def test_a_baked_round_that_only_its_bake_held_back_is_judged_again_on_sim_shots():
    baked_only = _status(
        1, baked=True, holds=["judged on baked renders that can't stand in for sim shots (x)"]
    )
    out = _run(
        {"note": NOTE},
        [baked_only, _status(2, True)],
        **{"bake status": {"ok": True, "checked": True, "usable": False}},
    )

    assert out["result"]["stop"] == "ready"
    assert _labels(out, "build") == ["r1 build (baked)", "r2 build"]


@pytest.mark.parametrize(
    "args", [{"strategy": "variants"}, {"endorsed": "one string"}, {"design": "Not A Name"}]
)
def test_bad_arguments_are_refused_before_any_agent(args):
    out = _run(args, [])
    assert out["error"]
    assert not out["calls"]
