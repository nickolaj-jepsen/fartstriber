import pytest

from livery import cps


def test_request_args_online_and_offline():
    assert cps.request_args(session_id=99) == {"session_id": 99}
    assert cps.request_args(track_id=1, car_id=2) == {"track_id": 1, "car_id": 2}


def test_request_args_includes_participants_when_present():
    args = cps.request_args(track_id=1, car_id=2, sess_carids=[10, 20])
    assert args == {"track_id": 1, "car_id": 2, "sess_carids": [10, 20]}


def test_request_args_drops_empty_participants():
    assert cps.request_args(track_id=1, car_id=2, sess_carids=[]) == {"track_id": 1, "car_id": 2}


def test_request_args_needs_something():
    with pytest.raises(cps.CpsError):
        cps.request_args()


def test_parse_ack_returns_the_inner_data():
    ack = [None, {"type": "registration_process_replay_request", "data": {"success": True, "keysrv": "k"}}]
    assert cps.parse_ack(ack) == {"success": True, "keysrv": "k"}


def test_parse_ack_raises_on_an_error():
    with pytest.raises(cps.CpsError):
        cps.parse_ack(["something went wrong", None])


def test_parse_ack_falls_back_to_a_bare_response():
    assert cps.parse_ack({"data": {"x": 1}}) == {"x": 1}
    assert cps.parse_ack({"success": True}) == {"success": True}
