"""One review page for every session: each review queues a group, whichever waits serves the page, and each
applies in its own checkout."""

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image

from livery.config import Workspace
from livery.review import apply as review_apply
from livery.review import items as review_items
from livery.review import queue, server
from tests.reviewpage import Review, free_port, get, item, post

REFS = [
    "| # | Livery | Car | Why | Yes/no |",
    "|---|---|---|---|---|",
    "| 1 | A | x | y |  |",
    "| 2 | B | x | y |  |",
]


def _checkout(root) -> Workspace:
    root.mkdir()
    (root / "references.md").write_text("\n".join(REFS) + "\n")
    Image.new("RGB", (4, 4)).save(root / "shot.png")
    return Workspace(root=root)


def test_review_returns_decisions_and_writes_references(tmp_path):
    ws = _checkout(tmp_path / "a")
    review = Review(ws, [*review_items.reference_items(ws.references), item("concept:x", mix=True)])

    assert [i["id"] for i in review.state()["items"]] == ["reference:1", "reference:2", "concept:x"]
    review.post("api/decide", {"id": "reference:1", "decision": "accept"})
    review.post("api/decide", {"id": "reference:2", "decision": "reject"})
    review.post("api/decide", {"id": "concept:x", "decision": "mix", "note": "the band, his colours"})
    result = review.apply()

    assert {d["id"]: d["decision"] for d in result["decisions"]} == {
        "reference:1": "accept",
        "reference:2": "reject",
        "concept:x": "mix",
    }
    assert [r[4] for r in review_items.reference_rows(ws.references)] == ["yes", "no"]


def test_two_sessions_share_one_page_and_each_applies_in_its_own_checkout(tmp_path):
    port = free_port()
    a, b = _checkout(tmp_path / "a"), _checkout(tmp_path / "b")
    first = Review(a, [*review_items.reference_items(a.references), item("concept:x", mix=True)], port=port)
    second = Review(b, review_items.reference_items(b.references), port=port)

    assert first.base == second.base
    page = first.page()
    assert [g["id"] for g in page["groups"]] == [first.group, second.group]
    assert [g["root"] for g in page["groups"]] == [str(a.root.resolve()), str(b.root.resolve())]
    # The same item in both reviews, decided apart.
    first.post("api/decide", {"id": "reference:1", "decision": "accept"})
    second.post("api/decide", {"id": "reference:1", "decision": "reject"})
    second.post("api/decide", {"id": "reference:2", "decision": "reject"})
    # A group's pictures come only from its own checkout.
    assert get(f"{first.base}img?g={first.group}&p=shot.png")[0] == 200
    assert get(f"{first.base}img?g={first.group}&p=../b/shot.png")[0] == 404

    one = first.apply()  # the review that served the page; the other takes the port over
    assert second.thread.is_alive()
    assert [g["id"] for g in second.page()["groups"] if g["status"] == "waiting"] == [second.group]
    two = second.apply()

    assert [r[4] for r in review_items.reference_rows(a.references)] == ["yes", ""]
    assert [r[4] for r in review_items.reference_rows(b.references)] == ["no", "no"]
    assert {(line["id"], line["decision"], line["review"]) for line in review_apply.ledger(a)} == {
        ("reference:1", "accept", one["review"])
    }
    assert {line["id"] for line in review_apply.ledger(b)} == {"reference:1", "reference:2"}
    assert one["undecided"] == ["reference:2", "concept:x"]
    assert two["undecided"] == []


DIES = """
import json, sys
from pathlib import Path
from livery.config import Workspace
from livery.review import server
server.serve(Workspace(root=Path(sys.argv[1])), json.loads(sys.argv[2]), port=0, on_ready=lambda m: print(m, flush=True))
"""


def test_a_review_whose_session_died_is_taken_over_by_the_next_from_its_checkout(tmp_path):
    ws = _checkout(tmp_path / "a")
    items = [item("concept:x", mix=True), item("concept:y", mix=True)]
    proc = subprocess.Popen(
        [sys.executable, "-c", DIES, str(ws.root), json.dumps(items)], stdout=subprocess.PIPE, text=True
    )
    assert proc.stdout is not None
    try:
        base, gid = proc.stdout.readline().split()[1].split("#")
        decide = {"group": gid, "id": "concept:x", "decision": "edit", "note": "the band lower"}
        assert post(base + "api/decide", decide) == 200
        with pytest.raises(queue.ReviewOpen):  # one waiting review per checkout
            server.serve(ws, items, port=0)
    finally:
        proc.kill()
        proc.wait()
    assert [g["status"] for g in queue.groups()] == ["stale"]

    review = Review(ws, [items[0], item("concept:z", mix=True)])

    assert [g["id"] for g in review.page()["groups"]] == [review.group]
    assert review.state()["decisions"]["concept:x"]["note"] == "the band lower"
    review.post("api/decide", {"id": "concept:z", "decision": "reject"})
    assert {d["id"] for d in review.apply()["decisions"]} == {"concept:x", "concept:z"}


def test_a_cancelled_review_leaves_the_page_and_its_session_stops_waiting(tmp_path):
    ws = _checkout(tmp_path / "a")
    review = Review(ws, [item("concept:x")])

    assert [g["id"] for g in queue.mine(ws.root)] == [review.group]
    assert queue.cancel(review.group)
    review.thread.join(timeout=10)

    assert isinstance(review.error, queue.Gone)
    assert queue.groups() == []


def test_a_port_held_by_a_page_that_does_not_queue_is_refused(tmp_path):
    class Old(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"items": [], "decisions": {}}).encode())

        def log_message(self, format: str, *args) -> None:
            pass

    class Exclusive(ThreadingHTTPServer):
        allow_reuse_address = False

    old = Exclusive(("127.0.0.1", 0), Old)
    threading.Thread(target=old.serve_forever, daemon=True).start()
    try:
        with pytest.raises(server.ReviewOpen):
            server.serve(_checkout(tmp_path / "a"), [item("concept:x")], port=old.server_address[1])
    finally:
        old.shutdown()
        old.server_close()
    assert queue.groups() == []
