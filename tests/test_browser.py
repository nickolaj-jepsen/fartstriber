import pytest

from livery import browser
from livery.errors import LiveryError


def test_find_browser_takes_the_first_installed(tmp_path):
    chrome = tmp_path / "chrome.exe"
    edge = tmp_path / "edge.exe"
    edge.write_bytes(b"")  # only the second candidate exists
    assert browser.find_browser((str(chrome), str(edge))) == edge


def test_find_browser_without_any_is_an_error(tmp_path):
    with pytest.raises(browser.NoBrowser):
        browser.find_browser((str(tmp_path / "none.exe"),))


@pytest.mark.parametrize(
    ("domain", "belongs"),
    [
        ("members-ng.iracing.com", True),
        (".iracing.com", True),
        ("iracing.com", True),
        ("notiracing.com", False),
        ("iracing.com.evil.com", False),
        ("example.com", False),
    ],
)
def test_cookie_domain_matching_rejects_lookalikes(domain, belongs):
    assert browser.for_domain(domain, "iracing.com") is belongs


def test_iracing_cookies_keeps_only_iracing():
    cookies = [
        {"name": "a", "value": "1", "domain": ".iracing.com"},
        {"name": "b", "value": "2", "domain": "members-ng.iracing.com"},
        {"name": "c", "value": "3", "domain": "google.com"},
    ]
    assert {c["name"] for c in browser.iracing_cookies(cookies)} == {"a", "b"}


def test_cookie_header_joins_name_value_pairs():
    cookies = [{"name": "a", "value": "1"}, {"name": "b", "value": "2"}]
    assert browser.cookie_header(cookies) == "a=1; b=2"


def test_read_port_parses_the_devtools_file(tmp_path):
    f = tmp_path / "DevToolsActivePort"
    f.write_text("54321\n/devtools/browser/abc\n", encoding="utf-8")
    assert browser._read_port(f) == (54321, "/devtools/browser/abc")


class _FakeBrowser:
    def __init__(self, cookie_rounds, closed=False):
        self._rounds = list(cookie_rounds)
        self._closed = closed

    def closed(self):
        return self._closed

    def cookies(self):
        return self._rounds.pop(0) if self._rounds else []

    def page_urls(self):
        return ["https://members-ng.iracing.com/"]


def _has_auth(cookies):
    return any(c["name"] == "authtoken" for c in cookies)


def test_wait_for_signin_returns_once_signed_in():
    rounds = [
        [{"name": "consent", "value": "x", "domain": ".iracing.com"}],  # anonymous first
        [{"name": "authtoken", "value": "y", "domain": ".iracing.com"}],  # then signed in
    ]
    found = browser.wait_for_signin(_FakeBrowser(rounds), _has_auth, lambda _m: None, timeout=5, poll=0.0)
    assert [c["name"] for c in found] == ["authtoken"]


def test_wait_for_signin_stops_when_the_window_closes():
    with pytest.raises(LiveryError):
        browser.wait_for_signin(
            _FakeBrowser([], closed=True), _has_auth, lambda _m: None, timeout=5, poll=0.0
        )


def test_wait_for_signin_times_out():
    never = _FakeBrowser([[{"name": "x", "value": "1", "domain": ".iracing.com"}]] * 100)
    with pytest.raises(LiveryError):
        browser.wait_for_signin(never, _has_auth, lambda _m: None, timeout=0.05, poll=0.0)
