"""The Library: where everything the owner decided stands, what they change there, History's undo of it, and
the approvals `build --all` carries or asks about."""

import json
import subprocess
import time

import pytest
from PIL import Image

from livery import approvals, hashing, prefs, sponsors, taste
from livery.config import Workspace
from livery.review import apply as review_apply
from livery.review import history, library, queue
from tests.conftest import CANVAS, OWNER, checked
from tests.reviewpage import Review, get, item, post

SQUARE = sponsors.path_svg("M0,0 h100 v100 h-100 Z", (0, 0, 100, 100))
ROSTER = (
    "| Brand | Name | Category | Voice | Mark | Colours | Checked | Status |\n"
    "|---|---|---|---|---|---|---|---|\n"
    "| acme | Acme | tyres | wide | a square | red | 2026-10-19 | yes |\n"
    "| fresh | Fresh | oil | slab | a square | blue | 2026-10-19 |  |\n"
)


@pytest.fixture
def lib(ws, tmp_path) -> Workspace:
    """The test workspace with a roster of its own: acme, approved, and fresh, drawn but not yet judged."""
    ws = Workspace(root=ws.root, assets=tmp_path / "assets", policy_file=ws.policy_file)
    for b in ("acme", "fresh"):
        sponsors.save(ws.sponsors / b, {"mark": SQUARE})
    sponsors.approve(ws.sponsors / "acme")
    (ws.sponsors / "roster.md").write_text(ROSTER, encoding="utf-8")
    return ws


def _paint(ws: Workspace, name: str, rgb: tuple[int, int, int]) -> None:
    for f in ("paint.png", "spec.png"):
        Image.new("RGB", (CANVAS, CANVAS), rgb).save(ws.out(name) / f)


def _approved(ws: Workspace, name: str, rgb=(48, 96, 192), places: list[str] | None = None) -> dict:
    """A design checked and approved as the owner accepts a final, placing these roster variants."""
    outputs = {"car.tga": f"{name} {rgb}".encode(), "car_spec.tga": f"{name} spec {rgb}".encode()}
    checked(ws, name, outputs=outputs)
    _paint(ws, name, rgb)
    shown = approvals.pin_of(ws, name) | {"sponsors": places or []}
    assert "pinned" in approvals.pin(ws, name, shown, "")
    review_apply._record(ws, {}, {f"final:{name}": {"decision": "accept"}}, "r", {})
    return shown


def _card(ws: Workspace, section: str, card_id: str) -> dict:
    return next(c for c in library.sections(ws)[section] if c["id"] == card_id)


def _last_uid(ws: Workspace) -> str:
    return prefs.uid(prefs.lines(ws)[-1])


def test_revoking_a_car_takes_its_approval_and_undo_in_history_gives_it_back(ws):
    _approved(ws, "d")
    assert _card(ws, "cars", "final:d")["state"] == "approved"

    out = library.revoke(ws, "d", "I'm bored of it")

    assert approvals.load_approval(ws, "d") is None
    assert (out["line"]["by"], out["line"]["note"]) == (OWNER, "I'm bored of it")
    assert _card(ws, "cars", "final:d")["state"] == "stopped"
    history.change(ws, _last_uid(ws), "undo")
    assert approvals.load_approval(ws, "d") is not None
    assert _card(ws, "cars", "final:d")["state"] == "approved"
    history.change(ws, out["line"]["uid"], "redo")
    assert approvals.load_approval(ws, "d") is None


def test_a_car_on_the_owners_car_says_so_and_revoking_it_offers_the_old_paint_back(ws, private_state):
    from livery import config

    _approved(ws, "d")
    cfg = config.load()
    folder = cfg.paint_root / "tiny"
    folder.mkdir(parents=True)
    (folder / f"car_{cfg.customer_id}.tga").write_bytes((ws.out("d") / "car.tga").read_bytes())

    assert _card(ws, "cars", "final:d")["state"] == "on your car"
    assert library.revoke(ws, "d")["on_car"]


def test_dropping_a_brand_holds_every_car_placing_it_until_undone(lib):
    _approved(lib, "d", places=["acme/mark"])
    assert _card(lib, "brands", "sponsor:acme")["used_by"] == ["d"]

    out = library.drop(lib, "sponsor", "acme", "it reads like an AI made it")

    assert out["held"] == ["d"]
    approval = approvals.load_approval(lib, "d")
    assert approval is not None
    assert approvals.unapproved(lib, approval)
    assert _card(lib, "cars", "final:d")["state"] == "held"
    assert _card(lib, "brands", "sponsor:acme")["state"] == "dropped"
    assert "acme" not in [r["brand"] for r in sponsors.roster(lib.sponsors / "roster.md")]
    history.change(lib, out["line"]["uid"], "undo")
    assert not approvals.unapproved(lib, approval)
    assert _card(lib, "brands", "sponsor:acme")["state"] == "approved"
    assert "acme" in [r["brand"] for r in sponsors.roster(lib.sponsors / "roster.md")]


def test_a_roster_row_is_judged_with_its_drawn_brand(lib):
    assert "fresh" in [r["brand"] for r in sponsors.roster(lib.sponsors / "roster.md")]
    items = [{"id": "sponsor:fresh", "kind": "sponsor"}]

    review_apply.apply(lib, items, {"sponsor:fresh": {"decision": "accept"}})

    assert {r["brand"]: r["status"] for r in sponsors.roster_rows(lib.sponsors / "roster.md")}[
        "fresh"
    ] == "yes"
    assert all(sponsors.approvals(lib.sponsors / "fresh").values())


def test_a_no_to_a_brand_keeps_its_drawing_and_history_can_undo_it(lib):
    review_apply.apply(
        lib, [{"id": "sponsor:acme", "kind": "sponsor"}], {"sponsor:acme": {"decision": "reject"}}
    )

    assert (lib.sponsors / "acme" / "brand.toml").exists()
    assert not any(sponsors.approvals(lib.sponsors / "acme").values())
    history.change(lib, _last_uid(lib), "undo")
    assert all(sponsors.approvals(lib.sponsors / "acme").values())


def test_retiring_a_taste_row_is_an_undoable_decision(ws):
    row = taste.ids(ws)[0]

    library.retire(ws, row, "plain is fine now")

    assert row not in taste.ids(ws)
    assert _card(ws, "taste", f"taste:{row}")["state"] == "retired"
    history.change(ws, _last_uid(ws), "undo")
    assert row in taste.ids(ws)


def test_reviving_a_stopped_car_puts_it_back_in_work_with_the_owners_words(ws):
    _approved(ws, "d")
    library.revoke(ws, "d")
    assert _card(ws, "cars", "final:d")["actions"] == ["revive"]

    library.revive(ws, "final:d", "try it with the new tyres")

    assert _card(ws, "cars", "final:d")["state"] == "revived"
    note = review_apply.latest_note(ws, "d")
    assert note is not None
    assert note["note"] == "try it with the new tyres"


def test_reviving_a_concept_whose_folder_was_deleted_brings_it_back_from_git(ws, monkeypatch):
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@t")
    git = lambda *a: subprocess.run(["git", "-C", str(ws.root), *a], check=True, capture_output=True)  # noqa: E731
    checked(ws, "sketch")
    git("init", "-q")
    git("add", "designs")
    git("commit", "-qm", "sketch")
    review_apply._record(ws, {}, {"concept:sketch": {"decision": "reject"}}, "r", {})
    git("rm", "-rq", "designs/sketch")
    git("commit", "-qm", "retire the sketch")
    assert _card(ws, "cars", "final:sketch")["actions"] == ["revive"]

    out = library.revive(ws, "concept:sketch")

    assert out["restored"]
    assert (ws.design_dir("sketch") / "design.py").exists()
    history.change(ws, out["line"]["uid"], "undo")
    assert not ws.design_dir("sketch").exists()


def _moved(ws: Workspace, monkeypatch, shift: int) -> dict:
    """An approved design that a library change (a new toolchain) rebuilt with every pixel `shift` brighter;
    its row as build --all reports it."""
    _approved(ws, "d")
    monkeypatch.setattr(hashing, "toolchain_hash", lambda: "a new library")
    now = checked(ws, "d", outputs={"car.tga": b"d moved", "car_spec.tga": b"d spec moved"})
    _paint(ws, "d", (48 + shift, 96, 192))
    return approvals.compare(ws, "d", now["digests"]) | {"role": "livery"}


def test_a_library_change_that_moves_no_pixel_past_tolerance_keeps_the_approval(ws, monkeypatch):
    row = _moved(ws, monkeypatch, approvals.TOLERANCE // 2)

    out = library.settle(ws, {"d": row})

    assert out["carried"] == ["d"]
    assert row["status"] == "carried"
    approval = approvals.load_approval(ws, "d")
    assert approval is not None
    assert approval["digests"]["car.tga"] == row_digest(ws)
    line = prefs.lines(ws)[-1]
    assert line["decision"] == "carry"
    assert line["by"].startswith("agent")
    history.change(ws, prefs.uid(line), "undo")
    approval = approvals.load_approval(ws, "d")
    assert approval is not None
    assert approval["digests"]["car.tga"] != row_digest(ws)


def row_digest(ws: Workspace) -> str:
    return json.loads((ws.out("d") / "check.json").read_text(encoding="utf-8"))["digests"]["car.tga"]


def test_a_change_past_tolerance_asks_the_owner_still_ok(ws, monkeypatch):
    row = _moved(ws, monkeypatch, 3 * approvals.TOLERANCE)

    out = library.settle(ws, {"d": row})

    assert out == {"carried": [], "asked": ["d"]}
    (group,) = queue.groups()
    assert group["status"] == "waiting"
    assert group["page"]
    assert [(i["id"], i["still"]) for i in group["items"]] == [("final:d", True)]


def test_a_still_ok_screen_takes_only_keep_change_or_stop(ws):
    review = Review(ws, [item("final:a", still=True)])

    assert review.post("api/decide", {"id": "final:a", "decision": "park"}) == 400
    assert review.post("api/decide", {"id": "final:a", "decision": "accept", "favourite": True}) == 400
    assert review.post("api/decide", {"id": "final:a", "decision": "stop"}) == 200
    review.apply()


def test_stopping_an_approved_car_takes_its_approval_back_until_undone(ws):
    _approved(ws, "d")

    review_apply.apply(ws, [item("final:d")], {"final:d": {"decision": "stop"}})

    assert approvals.load_approval(ws, "d") is None
    history.change(ws, _last_uid(ws), "undo")
    assert approvals.load_approval(ws, "d") is not None


def _until(check, wait: float = 20):
    deadline = time.monotonic() + wait
    while not (out := check()):
        assert time.monotonic() < deadline
        time.sleep(0.05)
    return out


def test_the_page_lists_the_library_and_applies_a_review_again_itself(ws):
    review = Review(ws, [item("final:z")])

    library_page = json.loads(get(review.base + "api/library")[1])
    assert [c["id"] for c in library_page["sections"]["kits"]] == ["kit:tiny"]
    assert post(review.base + "api/library/review", {"id": "kit:tiny"}) == 200
    (again,) = [g for g in queue.groups() if g.get("page")]
    assert (
        post(review.base + "api/decide", {"group": again["id"], "id": "kit:tiny", "decision": "accept"})
        == 200
    )
    assert post(review.base + "api/apply", {"group": again["id"]}) == 200

    _until(lambda: (queue.read(again["id"]) or {}).get("status") == "applied")
    line = prefs.decisions(ws)[-1]
    assert (line["id"], line["decision"], line.get("again")) == ("kit:tiny", "accept", True)
    review.post("api/decide", {"id": "final:z", "decision": "park"})
    review.apply()


def test_a_library_change_from_the_page_lands_in_history(ws):
    review = Review(ws, [item("final:z")])
    row = taste.ids(ws)[0]

    assert post(review.base + "api/library/retire", {"id": f"taste:{row}", "why": "not any more"}) == 200
    assert post(review.base + "api/library/drop", {"id": f"taste:{row}"}) == 400

    entries = json.loads(get(review.base + "api/history")[1])["entries"]
    assert any(e.get("decision") == "retire" and e["key"] == row and e["by"] == OWNER for e in entries)
    review.post("api/decide", {"id": "final:z", "decision": "park"})
    review.apply()
