"""`livery review`: one localhost page for the owner's decisions, from every session on the machine. Blocks
until they press Apply on this review's group.

Each review queues its items (livery.review.queue) and waits. Whichever waiting review holds the port serves
the page for all of them from the queue on disk, so when it exits the next one takes the port over and the page
carries on. The page only stores decisions: Apply on a group is run by that group's own process, in its own
checkout, so its commit lands on that checkout's branch.
"""

import json
import mimetypes
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, urlparse

from livery.config import Workspace
from livery.review import queue
from livery.review.apply import apply, proposals
from livery.review.items import RATINGS
from livery.review.queue import ReviewOpen

PAGE = Path(__file__).with_name("page.html")
PORT = 8765
POLL = 0.25  # seconds between a waiting review's looks at its group, and its tries for the port
# park: stop iterating on a design without approving it, so accept always means good enough to race.
CHOICES = ("accept", "edit", "reject", "skip", "mix", "park")

__all__ = ["ReviewOpen", "serve"]


class _Server(ThreadingHTTPServer):
    """One page per port. Windows lets a second server bind a port that is in use unless the bind is
    exclusive; the owner's decisions would then land on whichever of the two answered."""

    allow_reuse_address = False
    seen = 0.0  # when the page last read the queue

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def _merged(old: dict, body: dict) -> dict:
    """A decide call's fields over the stored decision's, so a call that sends only the note keeps the rest."""
    fields = {"decision": None, "note": "", "mix_of": [], "rating": None}
    return {k: body[k] if k in body else old.get(k, default) for k, default in fields.items()}


def _refusal(item: dict | None, decision: dict) -> str | None:
    """Why a decision can't be stored, or None. Mix, park and a rating go only on items that offer them;
    edit and mix say what to change, so they need a note."""
    if item is None:
        return "unknown item"
    choice, note, rating = decision["decision"], decision["note"], decision["rating"]
    if choice not in CHOICES or (choice in ("mix", "park") and not item.get(choice)):
        return "bad decision"
    if not isinstance(note, str):
        return "bad note"
    if choice in ("edit", "mix") and not note.strip():
        return f"{choice} needs a note"
    if rating is not None and not (item.get("rate") and type(rating) is int and rating in RATINGS):
        return "bad rating"
    return None


class _Handler(BaseHTTPRequestHandler):
    lock = threading.Lock()  # around every read and write of a group's decisions

    def log_message(self, format: str, *args) -> None:  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path == "/":
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif url.path == "/api/state":
            at = time.time()
            with self.lock:
                groups = queue.groups(prune=True)
            cast(_Server, self.server).seen = at
            self._json({"groups": groups, "ratings": RATINGS})
        elif url.path == "/img":
            group = queue.read(query.get("g", [""])[0])
            if group is None:
                return self._send(404, b"not found", "text/plain")
            root = Path(group["root"]).resolve()  # a group's pictures come only from its own checkout
            path = (root / query.get("p", [""])[0]).resolve()
            if root not in path.parents or not path.is_file() or path.suffix not in (".png", ".jpg", ".svg"):
                return self._send(404, b"not found", "text/plain")
            self._send(
                200, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            )
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        gid = str(body.get("group", ""))
        with self.lock:
            group = queue.read(gid) if gid else None
            if group is None:
                return self._json({"error": "no such review"}, 404)
            status = group["status"]
            if self.path == "/api/decide":
                if status in ("applying", "applied"):
                    return self._json({"error": f"this review is {status}"}, 409)
                item = next((i for i in group["items"] if i["id"] == body.get("id")), None)
                decision = _merged(group["decisions"].get(item["id"], {}) if item else {}, body)
                refused = _refusal(item, decision)
                if refused:
                    return self._json({"error": refused}, 400)
                stored = decision | {"at": time.time()}
                self._write(lambda: queue.decide(gid, body["id"], stored))
            elif self.path == "/api/apply":
                if status != "waiting":
                    why = "its session stopped waiting: it applies once that session runs livery review again"
                    return self._json(
                        {"error": why if status in ("stale", "failed") else f"it is {status}"}, 409
                    )
                self._write(lambda: queue.request_apply(gid))
            elif self.path == "/api/clear":
                if group["alive"] and status != "applied":
                    return self._json({"error": "its session is still waiting for it"}, 409)
                queue.cancel(gid)
                self._json({"ok": True})
            else:
                self._json({"error": "not found"}, 404)

    def _write(self, write) -> None:
        try:
            write()
        except queue.Gone as e:
            return self._json({"error": str(e)}, 404)
        self._json({"ok": True})


def _claimed(port: int) -> _Server | None:
    try:
        return _Server(("127.0.0.1", port), _Handler)
    except OSError:
        return None


def _shared(port: int) -> bool | None:
    """Whether the page on `port` shows the queue: None when nothing answers there."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=5) as r:
            return "groups" in json.loads(r.read())
    except urllib.error.HTTPError:
        return False
    except (OSError, ValueError):
        return None


def _claim(port: int) -> _Server | None:
    """This process's server if it got the port; None while another review's serves the queue."""
    deadline = time.monotonic() + 10
    while True:
        server = _claimed(port)
        if server is not None:
            return server
        shared = _shared(port)
        if shared:
            return None
        if shared is False or time.monotonic() > deadline:
            raise ReviewOpen(
                f"port {port} is in use, and not by a review page that every session shares: a review "
                f"from before it may still be open at http://127.0.0.1:{port}/; apply it or stop it first"
            )
        time.sleep(POLL)  # the review that served it is handing it over


def _start(server: _Server) -> _Server:
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _stop(server: _Server, applied: float) -> None:
    """Hands the port over; after an Apply, once an open page has read what it wrote (or a few seconds
    passed), since no review may be left to serve it."""
    deadline = time.monotonic() + 3
    open_page = server.seen > applied - 10
    while applied and open_page and server.seen < applied and time.monotonic() < deadline:
        time.sleep(0.05)
    server.shutdown()
    server.server_close()


def _apply(ws: Workspace, group: dict, commit: bool) -> dict:
    review = time.strftime("%Y%m%d-%H%M%S")
    items, decisions = group["items"], group["decisions"]
    try:
        written = apply(ws, items, decisions, review=review, commit=commit)
    except Exception as e:
        queue.finish(group["id"], {"error": f"{type(e).__name__}: {e}"})
        raise
    result = {
        "review": review,
        "decisions": [{"id": k, **v} for k, v in decisions.items()],
        "undecided": [i["id"] for i in items if i["id"] not in decisions],
        "written": written,
        "proposals": proposals(items, decisions),
    }
    out = ws.cache / "review" / f"result-{review}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1), encoding="utf-8", newline="\n")
    queue.finish(group["id"], {"written": written})
    return result


def serve(ws: Workspace, items: list[dict], port: int = PORT, on_ready=print, commit: bool = False) -> dict:
    """Queues the review and blocks until the owner applies it. `commit` lets Apply commit what it wrote: the
    CLI's review, never a test's. Port 0 serves a page of its own on a free port."""
    server = _claim(port)
    try:
        gid = queue.enqueue(ws.root, items)
    except BaseException:
        if server is not None:
            server.server_close()
        raise
    applied = 0.0
    try:
        if server is not None:
            _start(server)
            port = server.server_address[1]
        others = sum(g["status"] != "applied" and g["id"] != gid for g in queue.groups())
        waiting = f"; {others} other review{'s' if others > 1 else ''} on the page" if others else ""
        on_ready(f"review: http://127.0.0.1:{port}/#{gid}  ({len(items)} items{waiting})")
        while True:
            group = queue.read(gid)
            if group is None:
                raise queue.Gone(f"review {gid} was taken off the page (livery review --cancel)")
            if group["apply"]:
                result = _apply(ws, group, commit)
                applied = time.time()
                return result
            if server is None and (server := _claimed(port)) is not None:
                _start(server)  # the review that served the page has finished
            time.sleep(POLL)
    finally:
        if server is not None:
            _stop(server, applied)
