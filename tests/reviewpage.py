"""A `livery review` waiting in a thread, and the owner's page talking to it."""

import json
import re
import socket
import threading
import time
import urllib.error
import urllib.request

from livery.config import Workspace
from livery.review import server


def item(item_id: str, **extra) -> dict:
    kind, _, title = item_id.partition(":")
    return {"id": item_id, "kind": kind, "title": title, "body": "", "images": [], "mix": False, **extra}


def announced(message: str) -> str:
    """The page's address in what a review says once it waits."""
    found = re.search(r"http://\S+", message)
    assert found, message
    return found.group()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(url: str, wait: float = 10) -> tuple[int, bytes]:
    """Retries while nothing answers: the page does, while one review hands the port to the next."""
    deadline = time.monotonic() + wait
    while True:
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except urllib.error.URLError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.05)


def post(url: str, body: dict) -> int:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


class Review:
    def __init__(self, ws: Workspace, items: list[dict], port: int = 0):
        self.result: dict = {}
        self.error: BaseException | None = None
        self.url = ""
        ready = threading.Event()

        def told(message: str) -> None:
            self.url = announced(message)
            ready.set()

        def run() -> None:
            try:
                self.result.update(server.serve(ws, items, port=port, on_ready=told))
            except Exception as e:
                self.error = e
            finally:
                ready.set()

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        ready.wait(60)
        if self.error:
            raise self.error
        self.base, self.group = self.url.split("#")

    def post(self, path: str, body: dict) -> int:
        return post(self.base + path, {"group": self.group, **body})

    def page(self) -> dict:
        return json.loads(get(self.base + "api/state")[1])

    def state(self) -> dict:
        """This review's group as the page sees it, with the ratings the page offers."""
        page = self.page()
        return {**next(g for g in page["groups"] if g["id"] == self.group), "ratings": page["ratings"]}

    def apply(self) -> dict:
        assert self.post("api/apply", {}) == 200
        while self.state()["status"] == "applying":  # the page waits for what Apply wrote
            time.sleep(0.05)
        self.thread.join(timeout=30)
        if self.error:
            raise self.error
        return self.result
