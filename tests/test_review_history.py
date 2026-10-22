"""The owner's records: edits and undos of decisions, the taste table they feed, History on the page, and the
critic shown only once the owner has answered."""

import json

import numpy as np
from PIL import Image

from livery import prefs, state, taste
from livery.review import apply as review_apply
from livery.review import history
from tests.conftest import OWNER
from tests.reviewpage import Review, get, item, post

ROW = {"id": "type-designed", "look_for": "one type system per field", "where": "side shots"}


def _decided(ws, notes: dict[str, str]) -> dict[str, str]:
    """Edits on finals with these notes; their uids by design."""
    review_apply.apply(
        ws,
        [item(f"final:{k}") for k in notes],
        {f"final:{k}": {"decision": "edit", "note": n} for k, n in notes.items()},
    )
    return {d["id"].partition(":")[2]: prefs.uid(d) for d in prefs.decisions(ws)}


def test_an_edit_or_undo_is_a_line_of_its_own_and_the_decision_reads_as_it_stands(ws):
    uids = _decided(ws, {"a": "the side text is boring", "b": "the spacing is off"})

    prefs.change(ws, uids["a"], "edit", "the side text is plain")
    prefs.change(ws, uids["b"], "undo")

    standing = {d["id"]: d for d in prefs.decisions(ws)}
    assert standing["final:a"]["note"] == "the side text is plain"
    assert standing["final:a"]["first_note"] == "the side text is boring"
    assert "final:b" not in standing
    assert len(prefs.lines(ws)) == 4  # nothing rewritten
    assert review_apply.latest_note(ws, "b") is None
    prefs.change(ws, uids["b"], "redo")
    note = review_apply.latest_note(ws, "b")
    assert note is not None
    assert note["note"] == "the spacing is off"


def test_a_taste_row_left_on_one_note_asks_to_be_kept_or_retired(ws):
    uids = _decided(ws, {"a": "the side text is boring", "b": "the spacing is off"})
    words = [
        {"quote": "the side text is boring", "of": uids["a"]},
        {"quote": "the spacing is off", "of": uids["b"]},
    ]
    taste.put(ws, ROW | {"words": words})
    assert "type-designed" not in [r["id"] for r in taste.thin(ws)]

    out = history.change(ws, uids["b"], "undo")

    assert [r["id"] for r in out["thin"]] == ["type-designed"]
    history.keep_or_retire(ws, "type-designed", keep=True)
    assert "type-designed" not in [r["id"] for r in taste.thin(ws)]  # the owner's own row needs no support
    history.keep_or_retire(ws, "type-designed", keep=False, why="I like plain type now")
    assert "type-designed" not in taste.ids(ws)
    assert "type-designed" not in taste.markdown(ws)
    (row,) = [r for r in taste.rows(ws, retired=True) if r["id"] == "type-designed"]
    assert (row["retired"]["why"], row["retired"]["by"]) == ("I like plain type now", OWNER)


def test_what_an_agent_records_says_which_session_wrote_it(ws, monkeypatch):
    monkeypatch.setenv(prefs.SESSION_ENV, "abc123")
    monkeypatch.setenv(prefs.TRANSCRIPT_ENV, "C:/t/abc123.jsonl")

    row = taste.put(ws, ROW | {"words": [{"quote": "x"}]})

    assert (row["by"], row["transcript"]) == ("agent:abc123", "C:/t/abc123.jsonl")


def test_the_critic_shows_only_once_the_owner_has_answered(ws):
    critic_said = {"verdict": "iterate", "suggestions": [{"title": "the mirrors are plain", "detail": ""}]}
    timeline = [
        {"at": "2026-10-19T21:00", "who": "agents", "label": "Round 1", "views": {}, "critic": critic_said}
    ]
    review = Review(ws, [item("final:a", critic=critic_said, timeline=timeline)])

    (shown,) = review.state()["items"]
    assert "critic" not in shown
    assert "critic" not in shown["timeline"][0]
    review.post("api/decide", {"id": "final:a", "decision": "accept"})
    (shown,) = review.state()["items"]
    assert shown["critic"] == critic_said
    assert shown["timeline"][0]["critic"] == critic_said
    review.apply()


def test_history_lists_decisions_with_the_taste_they_fed_and_takes_edits(ws):
    uids = _decided(ws, {"a": "the side text is boring"})
    taste.put(ws, ROW | {"words": [{"quote": "the side text is boring", "of": uids["a"]}]})
    review = Review(ws, [item("final:z")])

    entries = json.loads(get(review.base + "api/history")[1])["entries"]
    decision = next(e for e in entries if e["uid"] == uids["a"])
    assert [t["id"] for t in decision["taste"]] == ["type-designed"]
    assert any(e["type"] == "taste" and e["key"] == "type-designed" for e in entries)
    assert post(review.base + "api/history/change", {"of": uids["a"], "kind": "edit", "note": "plain"}) == 200
    assert post(review.base + "api/history/change", {"of": "nope", "kind": "undo"}) == 400
    assert prefs.find(ws, uids["a"])["note"] == "plain"
    review.post("api/decide", {"id": "final:z", "decision": "park"})
    review.apply()


def test_a_design_decision_keeps_what_it_showed(ws):
    shot = ws.root / "front34.png"
    Image.new("RGB", (64, 36), "red").save(shot)

    review_apply.apply(
        ws, [item("final:a", views={"front34": "front34.png"})], {"final:a": {"decision": "accept"}}
    )

    (line,) = prefs.decisions(ws)
    kept = state.root() / line["shots"] / "front34.jpg"
    assert np.asarray(Image.open(kept).convert("RGB"))[..., 0].min() > 200
