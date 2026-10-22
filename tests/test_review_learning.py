"""The answers each kind takes, the decision ledger, Apply's commit and retro proposals."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from livery import approvals, config, hashing, prefs
from livery.config import Workspace
from livery.errors import LiveryError
from livery.review import apply as review_apply
from livery.review import items as review_items
from tests.conftest import OWNER, checked, sim_run, write_design
from tests.reviewpage import Review
from tests.reviewpage import item as _item

BLUE = 'def build(kit, p):\n    return Livery(base="#3060c0")\n'


def _final(ws: Workspace, name: str) -> dict:
    return _item(f"final:{name}", pin=approvals.pin_of(ws, name))


def _ledger(ws: Workspace) -> list[dict]:
    return review_apply.ledger(ws)


def test_each_kind_takes_its_own_answers(ws):
    review = Review(
        ws, [_item("final:a"), _item("concept:b"), _item("kit:tiny"), _item("concept:s", sketch=True)]
    )

    taken = [
        {"id": "final:a", "decision": "accept", "favourite": True},
        {"id": "final:a", "decision": "edit", "note": "the front", "strategy": "quick"},
        {"id": "final:a", "decision": "edit", "endorsed": ["the mirrors read as plain yellow"]},
        {"id": "final:a", "decision": "park"},
        {"id": "final:a", "decision": "stop"},
        {"id": "concept:s", "decision": "accept", "revived": True},
    ]
    refused = [
        {"id": "final:a", "decision": "reject"},
        {"id": "final:a", "decision": "park", "favourite": True},
        {"id": "final:a", "decision": "accept", "strategy": "quick"},
        {"id": "final:a", "decision": "edit", "strategy": "slow"},
        {"id": "final:a", "decision": "accept", "endorsed": ["a fix"]},
        {"id": "concept:b", "decision": "park"},
        {"id": "concept:b", "decision": "accept", "revived": True},
        {"id": "concept:b", "decision": "accept", "favourite": True},
        {"id": "kit:tiny", "decision": "park"},
        {"id": "concept:x", "decision": "accept"},
    ]
    assert [review.post("api/decide", body) for body in taken] == [200] * len(taken)
    assert [review.post("api/decide", body) for body in refused] == [400] * len(refused)


def test_a_change_is_kept_before_its_note_but_sent_only_with_one(ws):
    review = Review(ws, [_item("final:a")])

    assert review.post("api/decide", {"id": "final:a", "decision": "edit"}) == 200
    assert review.post("api/apply", {}) == 409
    assert review.post("api/decide", {"id": "final:a", "note": "a bigger, whiter number"}) == 200
    result = review.apply()

    (d,) = result["decisions"]
    assert (d["decision"], d["note"]) == ("edit", "a bigger, whiter number")


@pytest.mark.parametrize("answer", ["park", "stop"])
def test_a_parked_or_stopped_final_is_neither_approved_nor_installable(ws, private_metrics, answer):
    built = checked(ws, "d", BLUE)

    written = review_apply.apply(ws, [_final(ws, "d")], {"final:d": {"decision": answer}})

    assert not any("pinned" in w for w in written)
    assert approvals.load_approval(ws, "d") is None
    with pytest.raises(approvals.NotApproved):
        approvals.require(ws, "d", built)
    metric = json.loads(private_metrics.read_text().splitlines()[-1])
    assert metric["decision"] == answer


def _verdicts(ws: Workspace, name: str, rounds: list[dict]) -> None:
    (ws.design_dir(name) / "verdicts.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rounds))


def test_the_ledger_keeps_old_lines_the_note_verbatim_and_the_critic_on_the_same_design(ws):
    checked(ws, "d", BLUE)
    write_design(ws, "e", BLUE.replace("3060c0", "c03030"))
    h = hashing.design_hash(ws, "d")
    _verdicts(
        ws,
        "d",
        [
            {"design_hash": h, "overall": 7, "verdict": "iterate"},
            {"design_hash": "an-earlier-version", "overall": 9, "verdict": "ship"},
        ],
    )
    _verdicts(ws, "e", [{"design_hash": "an-earlier-version", "overall": 8, "verdict": "ship"}])
    old = {"at": "2026-10-18T12:50:46+02:00", "review": "x", "id": "kit:k", "decision": "accept", "note": ""}
    prefs.append(ws, [old])
    note = '  The back is nice,\nthe "front" is a bit boring '

    review_apply.apply(
        ws,
        [_final(ws, "d"), _item("concept:e")],
        {
            "final:d": {"decision": "edit", "note": note, "strategy": "full"},
            "concept:e": {"decision": "reject"},
        },
    )

    first, d, e = _ledger(ws)
    assert first == old
    assert (d["note"], d["strategy"], d["design_hash"], d["by"]) == (note, "full", h, OWNER)
    assert d["uid"] != e["uid"]
    assert (d["critic"]["overall"], d["critic"]["verdict"]) == (7, "iterate")
    assert e["design_hash"] == hashing.design_hash(ws, "e")
    assert "critic" not in e


def _shot(ws: Workspace, stamp: str, colour: str, build: bytes = b"round 1") -> Path:
    """A checked build of design d and a sim run of it in one colour."""
    outputs = {"car.tga": build + b" paint", "car_spec.tga": build + b" spec"}
    return sim_run(ws, "d", stamp, checked(ws, "d", outputs=outputs)["digests"], colour)


def test_the_ledger_records_the_sim_run_the_item_showed(ws):
    run = _shot(ws, "20261019-172200", "red")

    review_apply.apply(
        ws, review_items.design_items(ws, ["d"]), {"final:d": {"decision": "edit", "note": "boring front"}}
    )

    assert _ledger(ws)[-1]["runs"] == [run.name]


def _then_beside_now(ws: Workspace, item: dict) -> list[str]:
    """The item's pictures with the red run on the left and the blue one on the right."""
    found = []
    for rel in item["images"]:
        img = Image.open(ws.root / rel).convert("RGB")
        left, right = (img.getpixel((x * img.width // 4, img.height // 2)) for x in (1, 3))
        if (left, right) == ((255, 0, 0), (0, 0, 255)):
            found.append(rel)
    return found


def test_a_final_opens_with_the_last_decision_and_the_shots_then_beside_now(ws):
    then = _shot(ws, "20261019-172200", "red")
    (ws.design_dir("d") / "brief.md").write_text("The brief.\n", encoding="utf-8")
    note = '  The back design is nice,\nbut the "front" is a bit boring '
    review_apply.apply(
        ws, review_items.design_items(ws, ["d"]), {"final:d": {"decision": "edit", "note": note}}
    )
    _shot(ws, "20261019-210200", "blue", build=b"round 2")

    (item,) = review_items.design_items(ws, ["d"])

    assert item["body"].index(note) < item["body"].index("The brief.")
    assert item["images"][0] in _then_beside_now(ws, item)
    shutil.rmtree(then)
    (item,) = review_items.design_items(ws, ["d"])
    assert note in item["body"]
    assert not _then_beside_now(ws, item)


def _git(root, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(ws):
    """The workspace as a git checkout, with a design and an unrelated file committed."""
    _git(ws.root, "init", "-q")
    for key, value in (
        ("user.name", "Test"),
        ("user.email", "test@example.com"),
        ("commit.gpgsign", "false"),
    ):
        _git(ws.root, "config", key, value)
    (ws.root / ".gitignore").write_text(".cache/\nmetrics.jsonl\n")
    write_design(ws, "d", BLUE)
    (ws.root / "other.txt").write_text("before\n")
    _git(ws.root, "add", ".")
    _git(ws.root, "commit", "-q", "-m", "start")
    return ws.root


def test_apply_commits_what_it_wrote_and_the_design_folder_and_nothing_else(ws, repo):
    checked(ws, "d", BLUE)
    (ws.design_dir("d") / "notes.md").write_text("round 3\n")
    (repo / "other.txt").write_text("someone else's\n")
    _git(repo, "add", "other.txt")
    start = _git(repo, "rev-parse", "HEAD")
    item = _final(ws, "d")

    review_apply.apply(ws, [item], {"final:d": {"decision": "park"}})
    assert _git(repo, "rev-parse", "HEAD") == start  # tests and other callers never commit

    written = review_apply.apply(
        ws,
        [item],
        {"final:d": {"decision": "accept", "note": "good enough to race", "favourite": True}},
        commit=True,
    )

    assert any(w.startswith("committed") for w in written)
    files = set(_git(repo, "show", "--name-only", "--format=", "HEAD").split())
    assert files == {
        "designs/d/approved.json",
        "designs/d/notes.md",
        f"prefs/{config.person()}/decisions.jsonl",
    }
    assert "good enough to race" in _git(repo, "log", "-1", "--format=%B")
    assert _git(repo, "status", "--porcelain", "--", "other.txt").strip()


def test_apply_does_not_commit_a_file_that_had_other_changes(ws, repo):
    approval = ws.kits / "tiny" / "approved.json"
    approval.write_text("{}\n")  # someone else's, not yet committed
    start = _git(repo, "rev-parse", "HEAD")

    written = review_apply.apply(ws, [_item("kit:tiny")], {"kit:tiny": {"decision": "accept"}}, commit=True)

    assert any(w.startswith("not committed") and "kits/tiny/approved.json" in w for w in written)
    assert _git(repo, "rev-parse", "HEAD") == start


def test_a_decision_on_a_kind_apply_does_not_know_fails_before_anything_is_written(ws):
    approval = ws.kits / "tiny" / "approved.json"
    approval.write_text("{}\n")
    decisions = {"kit:tiny": {"decision": "accept"}, "poster:x": {"decision": "accept"}}
    with pytest.raises(LiveryError, match="poster:x"):
        review_apply.apply(ws, [_item("kit:tiny"), _item("poster:x")], decisions)
    assert not _ledger(ws)
    assert approval.read_text() == "{}\n"


def test_every_kind_the_page_takes_answers_for_is_one_apply_writes():
    assert set(review_items.ANSWERS) <= set(review_apply.KINDS)


def test_proposals_are_reviewed_and_the_accepted_ones_come_back(ws, tmp_path):
    path = tmp_path / "proposals.json"
    proposals = [
        {"title": "Taste row: no flat front", "body": "+ | the front carries a second layer |"},
        {"title": "Taste row: no flat front", "body": "another wording"},
        {"title": "Lint: sill sponsors", "body": "a check"},
    ]
    path.write_text(json.dumps(proposals))
    items = review_items.proposal_items(path)
    assert len({i["id"] for i in items}) == 3
    review = Review(ws, items)
    first, second, third = (i["id"] for i in items)

    assert review.post("api/decide", {"id": first, "decision": "park"}) == 400
    review.post("api/decide", {"id": first, "decision": "accept"})
    review.post("api/decide", {"id": second, "decision": "edit", "note": "shorter"})
    review.post("api/decide", {"id": third, "decision": "reject"})
    result = review.apply()

    assert [(p["body"], p["decision"]) for p in result["proposals"]] == [
        (proposals[0]["body"], "accept"),
        ("another wording", "edit"),
    ]
    assert {line["id"]: line["title"] for line in _ledger(ws)}[third] == "Lint: sill sponsors"


@pytest.mark.parametrize(
    "content", ["not json", "[]", '[{"title": "no body"}]', '{"title": "x", "body": "y"}']
)
def test_a_malformed_proposals_file_is_refused(tmp_path, content):
    path = tmp_path / "proposals.json"
    path.write_text(content)
    with pytest.raises(review_items.BadProposals):
        review_items.proposal_items(path)
