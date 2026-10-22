"""The owner's iRacing membership session: signing in through a throwaway browser and keeping the session
cookies in the machine state dir, plus the cheap check that the session still authenticates.

The replay-credentials request itself goes over CPS (`livery.cps`); this module owns the session the owner
signs into. The session cookies are the owner's; they live in the machine state dir (never the checkout)
and are never printed.
"""

import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from livery import browser, state
from livery.errors import LiveryError

BASE = "https://members-ng.iracing.com"
# The web app's authenticated HTTP proxy: the session cookie plus a constant client-id header (clientId is
# static config in the web bundle, not a per-user secret). Used here only for the benign `sessions` check.
BFF = BASE + "/bff/pub/proxy/api"
IR_CLIENT_ID = "iracing_ui"
SESSION_COOKIE = "iracing"  # the website session cookie; present only once signed in
VERIFY_ENDPOINT = "sessions"  # a benign proxy call, to check the session still authenticates
SIGNIN_URL = BASE + "/"
SIGNIN_TIMEOUT_S = 600.0
# A browser-like User-Agent and the SPA's own Referer/Origin: the members-ng proxy (behind a bot filter)
# refuses an obviously-scripted request even with valid cookies.
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)
# What a replay launch needs from the credentials response.
CRED_FIELDS = ("keysrv", "credentials", "customerid", "sess_cred", "sess_cred_srv")


class NotSignedIn(LiveryError):
    """No saved iRacing session, or it has expired: run `livery sim login`."""


def store_path() -> Path:
    return state.root() / "iracing-session.json"


def save_session(cookies: list[dict]) -> None:
    state.write_json(store_path(), {"cookies": cookies})


def load_session() -> list[dict] | None:
    data = state.read_json(store_path())
    return data.get("cookies") if isinstance(data, dict) else None


def _get(path: str, cookies: list[dict]) -> tuple[int, bytes]:
    """A GET to the web app's authenticated proxy, as the owner: the session cookie and the constant
    IR-Client-ID header. Returns (status, body)."""
    headers = {
        "Cookie": browser.cookie_header(cookies),
        "IR-Client-ID": IR_CLIENT_ID,
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Referer": BASE + "/",
        "Origin": BASE,
    }
    req = urllib.request.Request(f"{BFF}/{path}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def has_session(cookies: list[dict]) -> bool:
    """Whether the owner is signed in: the website session cookie is present."""
    return any(c.get("name") == SESSION_COOKIE for c in cookies)


def verify(cookies: list[dict]) -> int:
    """Confirm the saved session still authenticates, with a benign proxy call. Returns the HTTP status
    (200 when signed in)."""
    return _get(VERIFY_ENDPOINT, cookies)[0]


def signin_probe(log: Callable[[str], None]) -> Callable[[list[dict]], bool]:
    """A `wait_for_signin` predicate that logs the iRacing cookie names as they change, and returns True
    once the session cookie appears."""
    seen: dict[str, object] = {"names": None}

    def probe(cookies: list[dict]) -> bool:
        names = sorted(c.get("name", "") for c in cookies)
        if names != seen["names"]:
            log(f"iRacing cookies: {', '.join(names) or 'none yet'}")
            seen["names"] = names
        return has_session(cookies)

    return probe


def login(log: Callable[[str], None], timeout: float = SIGNIN_TIMEOUT_S) -> dict:
    """Open an iRacing sign-in in a throwaway browser profile and keep the session when it succeeds."""
    exe = browser.find_browser()
    log(f"opening {exe.name} for iRacing sign-in; this uses a throwaway profile, not your normal one")
    with browser.Browser(exe, SIGNIN_URL, log) as b:
        cookies = browser.wait_for_signin(b, signin_probe(log), log, timeout)
        log("signed in — closing the browser")  # inside the block, so it prints before the window closes
    save_session(cookies)
    return {"store": str(store_path()), "cookies": len(cookies)}


def session_or_raise() -> list[dict]:
    cookies = load_session()
    if not cookies:
        raise NotSignedIn("no saved iRacing session: run `livery sim login`")
    return cookies


def fresh_session() -> list[dict]:
    """The saved session, once a benign call proves it still authenticates. An expired one needs the owner:
    iRacing's remember-me cookie only reaches its sign-in page, never a new session."""
    cookies = session_or_raise()
    try:
        status = verify(cookies)
    except OSError as e:
        raise LiveryError(f"couldn't reach iRacing to check the session: {e}") from None
    if status != 200:
        raise NotSignedIn(f"the saved iRacing session has expired (HTTP {status}): run `livery sim login`")
    return cookies


def credential_fields(result: dict) -> dict[str, bool]:
    """Which expected credential fields came back and are non-empty — for reporting, without the values."""
    return {k: bool(result.get(k)) for k in CRED_FIELDS}
