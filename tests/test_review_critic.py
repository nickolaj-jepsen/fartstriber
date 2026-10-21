import json

import pytest

from livery import critic
from livery.config import Workspace
from tests.conftest import TASTE_IDS, checked, sim_run

FIX = "the orange band stops short of the tail light"


def suggestion(title: str, optional: bool = False, **more) -> dict:
    """A critic suggestion as a round records it."""
    return {"title": title, "detail": "", "where": "", "optional": optional} | more


def verdict(**change) -> dict:
    """A well-formed round verdict on a first round: every taste row passes, one fix that matters."""
    return {
        "previous": [],
        "taste": [{"id": i, "pass": True, "where": ""} for i in TASTE_IDS],
        "scores": dict.fromkeys(critic.RUBRIC, 4),
        "overall": 7,
        "verdict": "iterate",
        "fixes": [suggestion(FIX)],
        "lint_missed": [],
        "blocked": [],
    } | change


def test_a_well_formed_verdict_passes():
    assert critic.validate(verdict(), taste=TASTE_IDS) == verdict()


def _taste(fail: str, where: str = "") -> list[dict]:
    return [{"id": i, "pass": i != fail, "where": where} for i in TASTE_IDS]


@pytest.mark.parametrize(
    "change",
    [
        {"verdict": "ship"},  # ship is computed, never the critic's call
        {"overall": 11},
        {"scores": dict.fromkeys(critic.RUBRIC[:-1], 3)},
        {"fixes": ["a plain string"]},
        {"fixes": [{"text": "a fix as it was before suggestions", "optional": False}]},
        {"fixes": [{"title": "a", "detail": "b", "optional": False}]},  # where is text, maybe empty
        {"fixes": [suggestion("a headline that runs on " + "and on " * 8)]},
        {"taste": _taste("boring")[1:]},  # a row left out
        {"taste": _taste("boring")},  # failing without saying where
        {"previous": [{"status": "resolved", "evidence": "side_l.png"}]},  # there was no last round
        {"owner_note": {"status": "resolved", "evidence": "side_l.png"}},  # and no note from the owner
    ],
)
def test_malformed_verdicts_are_refused(change):
    with pytest.raises(critic.BadVerdict):
        critic.validate(verdict(**change), taste=TASTE_IDS)


def test_a_round_suggests_as_many_fixes_as_it_takes_and_names_each_in_one_line():
    many = [suggestion(f"fix {i}", detail=f"why {i}", where="front34: the nose") for i in range(6)]
    last = critic.validate(verdict(fixes=many), taste=TASTE_IDS)

    lines = [f["text"] for f in critic.fixes(last)]
    assert len(set(lines)) == 6
    assert all("fix" in t and "why" in t and "front34" in t for t in lines)


def test_a_failing_taste_row_says_where():
    critic.validate(
        verdict(taste=_taste("boring", where="front34: the bumper is one plain field")), taste=TASTE_IDS
    )


@pytest.mark.parametrize(
    "previous",
    [
        [],  # one per fix of the last round
        [{"status": "fixed", "evidence": "side_l.png"}],
        [{"status": "not", "evidence": ""}],
        [{"status": "not", "evidence": "elsewhere.png"}],  # not an image in the pack
    ],
)
def test_the_last_rounds_fixes_are_each_marked_with_evidence(previous):
    last = verdict()
    with pytest.raises(critic.BadVerdict):
        critic.validate(verdict(previous=previous), last, TASTE_IDS, images={"side_l.png"})
    marked = critic.validate(
        verdict(previous=[{"status": "partly", "evidence": "x/side_l.png"}]), last, TASTE_IDS
    )
    assert marked["previous"][0]["fix"] == FIX


def test_concept_ranking_must_cover_every_concept():
    critic.validate_ranking({"ranking": ["a", "b"], "reasons": {"a": "x", "b": "y"}}, ["a", "b"])
    with pytest.raises(critic.BadVerdict):
        critic.validate_ranking({"ranking": ["a"], "reasons": {"a": "x"}}, ["a", "b"])


def _shot(ws: Workspace, name: str) -> None:
    """A sim run of a checked build."""
    sim_run(ws, name, "20260101-000000", checked(ws, name)["digests"])


def test_the_next_round_sees_earlier_verdicts_and_the_builders_answers(ws):
    _shot(ws, "d")
    critic.record(ws, "d", verdict())
    blocked = critic.answer(ws, "d", [{"status": "blocked", "note": "the number zone spans two maps"}])

    assert [b["fix"] for b in blocked] == [FIX]
    folder = critic.pack(ws, ["d"])
    entry = json.loads((folder / "manifest.json").read_text())["designs"]["d"]
    rounds = json.loads((folder / entry["history"]).read_text())
    assert rounds[-1]["overall"] == 7
    assert rounds[-1]["answers"][0]["status"] == "blocked"
    assert entry["previous_fixes"] == [FIX]


def test_a_verdict_records_what_it_judged(ws):
    _shot(ws, "d")
    critic.pack(ws, ["d"])
    recorded = critic.record(ws, "d", verdict())

    assert recorded["shots"] == ".cache/designs/d/sim/20260101-000000"
    assert recorded["design_hash"] == json.loads((ws.out("d") / "check.json").read_text())["design_hash"]
    assert recorded["round"] == 1
    assert recorded["rubric"] == critic.rubric_version(ws)
    assert recorded["model"] == critic.critic_model()
    assert critic.history(ws, "d")[-1]["fixes"] == verdict()["fixes"]
    assert not (ws.design_dir("d") / "notes.md").exists()  # verdicts.jsonl is the one store


def test_verdicts_from_before_v2_are_still_read(ws):
    _shot(ws, "d")
    old = {"at": "2026-10-19 21:45", "scores": {}, "overall": 8, "verdict": "ship", "fixes": ["polish"]}
    critic._path(ws, "d").write_text(json.dumps(old | {"answers": None}) + "\n", encoding="utf-8")

    assert critic.history(ws, "d")[0]["overall"] == 8
    assert critic.fixes(critic.history(ws, "d")[0]) == [{"text": "polish", "optional": True}]
    assert critic.answer(ws, "d", [{"status": "skipped", "note": "polish"}]) == []
    assert not critic.status(ws, "d")["ship"]
    with pytest.raises(critic.BadVerdict):  # the next round marks the old round's fix too
        critic.record(ws, "d", verdict())
    critic.record(ws, "d", verdict(previous=[{"status": "resolved", "evidence": "side_l.png"}]))
    assert len(critic.history(ws, "d")) == 2


def test_a_round_is_not_packed_until_the_last_fixes_are_answered(ws):
    _shot(ws, "d")
    critic.record(ws, "d", verdict())
    with pytest.raises(critic.Unanswered):
        critic.pack(ws, ["d"])


@pytest.mark.parametrize(
    "answers",
    [
        [],  # one per fix
        [{"status": "maybe", "note": "x"}],
        [{"status": "done", "note": " "}],
    ],
)
def test_malformed_answers_are_refused(ws, answers):
    _shot(ws, "d")
    critic.record(ws, "d", verdict())
    with pytest.raises(critic.BadVerdict):
        critic.answer(ws, "d", answers)
