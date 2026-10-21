"""`livery review`: one localhost page for the owner's decisions, from every session on the machine. Blocks
until they press Send on this review's screen.

Each review queues its items as one screen (livery.review.queue) and waits. Whichever waiting review holds the
port serves the page for all of them from the queue on disk, so when it exits the next one takes the port over
and the page carries on. The page only stores decisions: Send on a screen is applied by that screen's own
process, in its own checkout, so its commit lands on that checkout's branch. History reads and writes the main
checkout (livery.review.history).
"""

import contextlib
import hashlib
import json
import mimetypes
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, urlparse

from livery import config, notify, state
from livery.config import Workspace
from livery.errors import LiveryError
from livery.review import history, queue
from livery.review.apply import SHOTS, apply, proposals
from livery.review.items import ANSWERS, HIDDEN, STRATEGIES, layout
from livery.review.queue import ReviewOpen

PAGE = Path(__file__).with_name("page.html")
PORT = 8765
POLL = 0.25  # seconds between a waiting review's looks at its group, and its tries for the port
PICTURES = (".png", ".jpg", ".svg")

__all__ = ["ReviewOpen", "serve"]


class _Server(ThreadingHTTPServer):
    """One page per port. Windows lets a second server bind a port that is in use unless the bind is
    exclusive; the owner's decisions would then land on whichever of the two answered."""

    allow_reuse_address = False
    seen = 0.0  # when the page last read the queue
    root: Path = Path(".")  # the checkout of the review serving the page, for History's main checkout

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


FIELDS = {
    "decision": None,
    "note": "",
    "favourite": False,
    "strategy": None,
    "endorsed": [],
    "revived": False,
}


def _merged(old: dict, body: dict) -> dict:
    """A decide call's fields over the stored decision's, so a call that sends only the note keeps the rest.
    A new answer keeps only the note: a favourite or a strategy belonged to the answer before."""
    if "decision" in body and body["decision"] != old.get("decision"):
        old = {"note": old.get("note", "")}
    return {k: body[k] if k in body else old.get(k, default) for k, default in FIELDS.items()}


def _refusal(item: dict | None, d: dict) -> str | None:
    """Why a decision can't be stored, or None. Each kind takes its own answers. A change may be stored
    before its note (so the critic shows as soon as the owner picks it); Send needs one (`_unsaid`)."""
    if item is None:
        return "unknown item"
    choice, note = d["decision"], d["note"]
    if choice not in ANSWERS.get(item["kind"], ()):
        return "bad decision"
    if not isinstance(note, str):
        return "bad note"
    endorsed = d["endorsed"]
    if not isinstance(endorsed, list) or not all(isinstance(e, str) for e in endorsed):
        return "bad endorsed"
    if d["favourite"] and not (item["kind"] == "final" and choice == "accept"):
        return "only a raced final is a favourite"
    if d["strategy"] is not None and not (choice == "edit" and d["strategy"] in STRATEGIES):
        return "bad strategy"
    if endorsed and choice != "edit":
        return "ticked suggestions go with a change"
    if d["revived"] and not (item.get("sketch") and choice == "accept"):
        return "only a sketch is revived"
    return None


def _unsaid(group: dict) -> list[str]:
    """Changes that don't yet say what to change: no note and no ticked critic suggestion."""
    return [
        k
        for k, d in group["decisions"].items()
        if d.get("decision") == "edit" and not str(d.get("note", "")).strip() and not d.get("endorsed")
    ]


def _shown(group: dict) -> dict:
    """A group as the page gets it: its layout, and each item without what it may show only once answered."""
    decided = group["decisions"]
    items = [
        {k: v for k, v in i.items() if k not in HIDDEN or i["id"] in decided}
        | {"timeline": [_point(p, i["id"] in decided) for p in i.get("timeline", [])]}
        for i in group["items"]
    ]
    return {**group, "items": items, "layout": layout(group["items"])}


def _point(p: dict, answered: bool) -> dict:
    return p if answered else {k: v for k, v in p.items() if k not in HIDDEN}


class _Handler(BaseHTTPRequestHandler):
    lock = threading.Lock()  # around every read and write of a group's decisions, and of History

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

    def _picture(self, root: Path, rel: str) -> None:
        """A picture from inside `root`, or one a decision kept in the state dir (`state:`)."""
        if rel.startswith("state:"):
            root, rel = (state.root() / SHOTS).resolve(), rel[len("state:") :].removeprefix(f"{SHOTS}/")
        root = root.resolve()
        path = (root / rel).resolve()
        if root not in path.parents or not path.is_file() or path.suffix not in PICTURES:
            return self._send(404, b"not found", "text/plain")
        self._send(200, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "application/octet-stream")

    def _main(self) -> Workspace:
        return Workspace(root=history.main_root(cast(_Server, self.server).root))

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        arg = lambda k: query.get(k, [""])[0]  # noqa: E731
        if url.path == "/":
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif url.path == "/api/state":
            at = time.time()
            with self.lock:
                groups = queue.groups(prune=True)
            cast(_Server, self.server).seen = at
            self._json({"groups": [_shown(g) for g in groups], "owner": config.owner()})
        elif url.path == "/img":
            group = queue.read(arg("g"))
            if group is None:
                return self._send(404, b"not found", "text/plain")
            self._picture(Path(group["root"]), arg("p"))  # a group's pictures come only from its own checkout
        elif url.path == "/api/history":
            ws = self._main()
            with self.lock:
                self._json({"entries": history.entries(ws), "root": str(ws.root), "owner": config.owner()})
        elif url.path == "/history-img":
            self._picture(self._main().root, arg("p"))
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path.startswith("/api/history/"):
            return self._history(body)
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
            elif self.path == "/api/undecide":
                if status in ("applying", "applied"):
                    return self._json({"error": f"this review is {status}"}, 409)
                self._write(lambda: queue.undecide(gid, str(body.get("id", ""))))
            elif self.path == "/api/apply":
                if status != "waiting":
                    why = "its session stopped waiting: it applies once that session runs livery review again"
                    return self._json(
                        {"error": why if status in ("stale", "failed") else f"it is {status}"}, 409
                    )
                left = [i["id"] for i in group["items"] if i["id"] not in group["decisions"]]
                if left and layout(group["items"]) != "compare":
                    return self._json({"error": f"answer every item first: {', '.join(left)}"}, 409)
                if unsaid := _unsaid(group):
                    return self._json({"error": f"say what to change: {', '.join(unsaid)}"}, 409)
                self._write(lambda: queue.request_apply(gid))
            elif self.path == "/api/clear":
                if group["alive"] and status != "applied":
                    return self._json({"error": "its session is still waiting for it"}, 409)
                queue.cancel(gid)
                self._json({"ok": True})
            else:
                self._json({"error": "not found"}, 404)

    def _history(self, body: dict) -> None:
        ws = self._main()
        with self.lock:
            try:
                if self.path == "/api/history/change":
                    kind = str(body.get("kind", ""))
                    note = body.get("note") if kind == "edit" else None
                    self._json(history.change(ws, str(body.get("of", "")), kind, note))
                elif self.path == "/api/history/taste":
                    keep = bool(body.get("keep"))
                    self._json(
                        history.keep_or_retire(ws, str(body.get("id", "")), keep, str(body.get("why", "")))
                    )
                else:
                    self._json({"error": "not found"}, 404)
            except LiveryError as e:
                self._json({"error": str(e)}, 400)

    def _write(self, write) -> None:
        try:
            write()
        except queue.Gone as e:
            return self._json({"error": str(e)}, 404)
        self._json({"ok": True})


def _claimed(port: int, root: Path) -> _Server | None:
    try:
        server = _Server(("127.0.0.1", port), _Handler)
    except OSError:
        return None
    server.root = root
    return server


def _shared(port: int) -> bool | None:
    """Whether the page on `port` shows the queue: None when nothing answers there."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=5) as r:
            return "groups" in json.loads(r.read())
    except urllib.error.HTTPError:
        return False
    except (OSError, ValueError):
        return None


def _claim(port: int, root: Path) -> _Server | None:
    """This process's server if it got the port; None while another review's serves the queue."""
    deadline = time.monotonic() + 10
    while True:
        server = _claimed(port, root)
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
    """Hands the port over; after a Send, once an open page has read what it wrote (or a few seconds
    passed), since no review may be left to serve it."""
    deadline = time.monotonic() + 3
    open_page = server.seen > applied - 10
    while applied and open_page and server.seen < applied and time.monotonic() < deadline:
        time.sleep(0.05)
    server.shutdown()
    server.server_close()


@contextlib.contextmanager
def _checkout_lock(root: Path) -> Iterator[None]:
    """One Apply at a time per checkout: several screens from one checkout may wait at once, and two
    commits at once would trip over git's index lock."""
    name = hashlib.sha1(os.path.normcase(str(root.resolve())).encode()).hexdigest()[:12]
    path = queue.folder() / f"apply-{name}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                holder = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                holder = None
            if holder is not None and not state.alive(holder):
                with contextlib.suppress(OSError):
                    path.unlink()
            time.sleep(POLL)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state.me(), f)
        break
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            path.unlink()


def _apply(ws: Workspace, group: dict, commit: bool) -> dict:
    review = time.strftime("%Y%m%d-%H%M%S")
    items, decisions = group["items"], dict(group["decisions"])
    if layout(items) == "compare":  # a concept not picked, or a sketch not revived, is a no
        for i in items:
            decisions.setdefault(i["id"], {"decision": "reject", "note": "", "at": time.time()})
    try:
        with _checkout_lock(ws.root):
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


def _what(items: list[dict]) -> str:
    kinds = [i["kind"] for i in items]
    if "final" in kinds:
        return f"{items[kinds.index('final')]['title']} is ready: final"
    if "concept" in kinds:
        return f"{sum(not i.get('sketch') for i in items)} concepts to pick from"
    if "kit" in kinds:
        return f"{items[0]['title']} is ready to check"
    return f"{len(items)} {kinds[0]}{'s' if len(items) > 1 else ''} to decide"


def serve(
    ws: Workspace,
    items: list[dict],
    port: int = PORT,
    on_ready=print,
    commit: bool = False,
    notify_owner: bool | None = None,
) -> dict:
    """Queues the review and blocks until the owner sends it. `commit` lets Apply commit what it wrote: the
    CLI's review, never a test's; it also sends a Windows notification unless `notify_owner` is False. Port 0
    serves a page of its own on a free port."""
    server = _claim(port, ws.root)
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
        url = f"http://127.0.0.1:{port}/#{gid}"
        on_ready(f"review: {url}  ({len(items)} items{waiting})")
        if commit if notify_owner is None else notify_owner:
            notify.toast("Fartstriber review", _what(items), url)
        while True:
            group = queue.read(gid)
            if group is None:
                raise queue.Gone(f"review {gid} was taken off the page (livery review --cancel)")
            if group["apply"]:
                result = _apply(ws, group, commit)
                applied = time.time()
                return result
            if server is None and (server := _claimed(port, ws.root)) is not None:
                _start(server)  # the review that served the page has finished
            time.sleep(POLL)
    finally:
        if server is not None:
            _stop(server, applied)
