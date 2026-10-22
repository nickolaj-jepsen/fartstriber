"""Sign in to a website in the owner's own Chrome or Edge, in a throwaway profile, and read the session
cookies back through the DevTools protocol.

No browser is bundled and the owner's real profile is never touched: a fresh `--user-data-dir` is made for
the sign-in and deleted afterwards. The password is only ever typed by the owner into the real login page;
this module only reads the cookies the browser received, over a localhost DevTools socket.
"""

import contextlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import websocket

from livery.errors import LiveryError

# Chrome and Edge, newest-looking first; both speak the same DevTools protocol.
_CANDIDATES = (
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
)
_PORT_WAIT_S = 20.0  # how long to wait for the browser to write DevToolsActivePort


class NoBrowser(LiveryError):
    """No Chrome or Edge was found to sign in with."""


def find_browser(candidates: tuple[str, ...] = _CANDIDATES) -> Path:
    """The first installed Chrome or Edge, with environment variables expanded."""
    for raw in candidates:
        p = Path(os.path.expandvars(raw))
        if p.is_file():
            return p
    raise NoBrowser("no Chrome or Edge found; install one, or sign in and copy the session yourself")


def _read_port(active: Path) -> tuple[int, str]:
    """The (port, ws path) a browser writes to DevToolsActivePort once its debugging socket is up."""
    lines = active.read_text(encoding="utf-8").splitlines()
    return int(lines[0]), lines[1]


class Browser:
    """A throwaway browser instance with a DevTools socket. Use as a context manager: it launches on enter
    and, on exit, closes the socket, ends the process and removes the temporary profile."""

    def __init__(self, exe: Path, url: str, log: Callable[[str], None] = lambda _m: None):
        self.exe = exe
        self.url = url
        self.log = log
        self.profile = Path(tempfile.mkdtemp(prefix="fartstriber-signin-"))
        self._proc: subprocess.Popen[bytes] | None = None
        self._ws: websocket.WebSocket | None = None
        self._id = 0

    def __enter__(self) -> "Browser":
        self._proc = subprocess.Popen(
            [
                str(self.exe),
                "--new-window",
                "--remote-debugging-port=0",  # the browser picks a free port and writes it out
                "--remote-allow-origins=*",  # modern Chrome refuses the DevTools socket otherwise
                f"--user-data-dir={self.profile}",
                "--no-first-run",
                "--no-default-browser-check",
                self.url,
            ]
        )
        active = self.profile / "DevToolsActivePort"
        t0 = time.monotonic()
        while time.monotonic() - t0 < _PORT_WAIT_S:
            if self.closed():
                raise LiveryError("the browser exited before it was ready")
            if active.is_file() and len(active.read_text(encoding="utf-8").splitlines()) >= 2:
                port, path = _read_port(active)
                self._ws = websocket.create_connection(f"ws://127.0.0.1:{port}{path}", timeout=15)
                return self
            time.sleep(0.1)
        raise LiveryError("the browser didn't open its DevTools port in time")

    def closed(self) -> bool:
        return self._proc is not None and self._proc.poll() is not None

    def _call(self, method: str, **params: Any) -> dict:
        assert self._ws is not None
        self._id += 1
        wanted = self._id
        self._ws.send(json.dumps({"id": wanted, "method": method, "params": params}))
        while True:
            msg = json.loads(self._ws.recv())
            if msg.get("id") == wanted:  # skip the protocol's event messages
                if "error" in msg:
                    raise LiveryError(f"DevTools {method} failed: {msg['error']}")
                return msg.get("result", {})

    def cookies(self) -> list[dict]:
        """Every cookie the browser holds, each a dict with name, value, domain, path, secure, httpOnly and
        (when set) an `expires` epoch. Read from the browser, so httpOnly cookies are included."""
        return self._call("Storage.getCookies").get("cookies", [])

    def page_urls(self) -> list[str]:
        """The URL of each open page (tab), so a caller can see where sign-in has reached."""
        targets = self._call("Target.getTargets").get("targetInfos", [])
        return [t.get("url", "") for t in targets if t.get("type") == "page"]

    def __exit__(self, *_exc: object) -> None:
        if self._ws is not None:
            with contextlib.suppress(OSError, websocket.WebSocketException):
                self._ws.close()
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        shutil.rmtree(self.profile, ignore_errors=True)


def for_domain(domain: str, suffix: str) -> bool:
    """Whether a cookie `domain` belongs to `suffix` (itself or a subdomain), not a lookalike like
    `notiracing.com` for `iracing.com`."""
    host = domain.lstrip(".")
    return host == suffix or host.endswith("." + suffix)


def iracing_cookies(cookies: list[dict]) -> list[dict]:
    """The cookies that belong to iRacing, by domain."""
    return [c for c in cookies if for_domain(c.get("domain", ""), "iracing.com")]


def cookie_header(cookies: list[dict]) -> str:
    """A `Cookie:` header value from stored cookies."""
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)


class SignInTarget(Protocol):
    """What `wait_for_signin` needs of a browser: a real `Browser`, or a test's stand-in."""

    def closed(self) -> bool: ...
    def cookies(self) -> list[dict]: ...
    def page_urls(self) -> list[str]: ...


def wait_for_signin(
    browser: SignInTarget,
    is_signed_in: Callable[[list[dict]], bool],
    log: Callable[[str], None],
    timeout: float,
    poll: float = 1.0,
) -> list[dict]:
    """Poll the browser until `is_signed_in` accepts its iRacing cookies, and return them.

    `is_signed_in` is called every poll (with an empty list before any cookie appears), so it can log what
    it sees. Each time the open pages change, their URLs are logged, to show where sign-in has reached.
    Stops if the owner closes the window, or when `timeout` seconds pass.
    """
    closed = LiveryError("the browser was closed before sign-in finished")
    t0 = time.monotonic()
    said = False
    last_urls: list[str] | None = None
    while time.monotonic() - t0 < timeout:
        if browser.closed():
            raise closed
        try:
            found = iracing_cookies(browser.cookies())
        except (OSError, websocket.WebSocketException):
            raise closed from None  # Chrome is going away mid-read
        try:  # the page URLs are only for the trace, so a failure to read them must not end sign-in
            urls = browser.page_urls()
        except (OSError, websocket.WebSocketException, LiveryError):
            urls = last_urls or []
        if urls != last_urls:
            log("page: " + ("; ".join(u for u in urls if u) or "(blank)"))
            last_urls = urls
        if is_signed_in(found):
            return found
        if not said:
            log("waiting for you to sign in (close the window to cancel)...")
            said = True
        time.sleep(poll)
    raise LiveryError("timed out waiting for sign-in")
