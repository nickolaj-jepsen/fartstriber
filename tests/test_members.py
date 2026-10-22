import pytest

from livery import members


def test_session_round_trips_through_the_state_dir(private_state):
    cookies = [{"name": "iracing", "value": "secret", "domain": ".iracing.com"}]
    members.save_session(cookies)
    assert members.load_session() == cookies
    assert members.store_path().parent == private_state


def test_no_session_reads_as_none(private_state):
    assert members.load_session() is None


def test_session_or_raise_without_a_session(private_state):
    with pytest.raises(members.NotSignedIn):
        members.session_or_raise()


def test_credential_fields_report_presence_not_values():
    result = {"keysrv": "k", "credentials": "", "customerid": 7, "sess_cred": "s", "sess_cred_srv": None}
    assert members.credential_fields(result) == {
        "keysrv": True,
        "credentials": False,
        "customerid": True,
        "sess_cred": True,
        "sess_cred_srv": False,
    }


def test_has_session_detects_the_session_cookie():
    assert members.has_session([{"name": "iracing", "value": "x"}]) is True
    assert members.has_session([{"name": "_ga", "value": "x"}]) is False
    assert members.has_session([]) is False


def test_signin_probe_returns_true_once_the_session_cookie_appears():
    logged = []
    probe = members.signin_probe(logged.append)
    assert probe([{"name": "_ga", "value": "x", "domain": ".iracing.com"}]) is False
    assert probe([{"name": "iracing", "value": "s", "domain": ".iracing.com"}]) is True
    assert any("iRacing cookies" in line for line in logged)


def _fake_urlopen(monkeypatch, status=200, body=b"{}"):
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return body

    _Resp.status = status

    def fake(req, timeout=0):
        seen["url"] = req.full_url
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        return _Resp()

    monkeypatch.setattr(members.urllib.request, "urlopen", fake)
    return seen


def test_get_sends_the_client_id_header_and_cookie(monkeypatch):
    seen = _fake_urlopen(monkeypatch)
    members._get("sessions", [{"name": "iracing", "value": "s"}])
    assert seen["url"] == f"{members.BFF}/sessions"
    assert seen["headers"]["ir-client-id"] == members.IR_CLIENT_ID
    assert "iracing=s" in seen["headers"]["cookie"]


def test_verify_returns_the_proxy_status(monkeypatch):
    _fake_urlopen(monkeypatch, status=200)
    assert members.verify([]) == 200
