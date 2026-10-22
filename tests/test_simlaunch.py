from livery import simlaunch


def test_launch_task_serializes_parameters_as_a_query_string():
    creds = {
        "keysrv": "host%2Fgetkeys",
        "credentials": "c",
        "customerid": 123,
        "sess_cred": "s",
        "sess_cred_srv": "host%2Fchecksess",
    }
    task = simlaunch.launch_task(creds, "fartstriper.rpy")
    assert task["task"] == simlaunch.TASK_NAME
    p = task["parameters"]
    assert isinstance(p, str)
    assert "file=fartstriper.rpy" in p
    assert "keysrv=host%2Fgetkeys" in p  # already percent-encoded: kept as-is
    assert "customerid=123" in p
    assert "sess_cred=s" in p


def test_launch_task_drops_missing_credentials():
    task = simlaunch.launch_task({"keysrv": "k"}, "x.rpy")
    assert "sess_cred_srv" not in task["parameters"]
    assert "keysrv=k" in task["parameters"]


def test_decode_all_splits_concatenated_json():
    objs, rest = simlaunch.decode_all('{"a":1}{"b":2}')
    assert objs == [{"a": 1}, {"b": 2}]
    assert rest == ""


def test_decode_all_keeps_a_partial_object():
    objs, rest = simlaunch.decode_all('{"a":1}{"b"')
    assert objs == [{"a": 1}]
    assert rest == '{"b"'


def test_handle_answers_get_task_with_the_task():
    task = {"task": "legacyPlayReplay", "parameters": {"file": "x.rpy"}}
    reply, done = simlaunch.handle({"jsonrpc": "2.0", "id": 7, "method": "getTask"}, task)
    assert reply == {"jsonrpc": "2.0", "id": 7, "result": task}
    assert done is False


def test_handle_acknowledges_sim_ending():
    reply, done = simlaunch.handle({"id": 9, "method": "simEnding"}, {})
    assert reply is not None
    assert reply["result"] == {}
    assert done is False


def test_handle_stops_on_sim_ended():
    _, done = simlaunch.handle({"id": 10, "method": "simEnded"}, {})
    assert done is True


def test_handle_rejects_an_unknown_method():
    reply, done = simlaunch.handle({"id": 11, "method": "whoKnows"}, {})
    assert reply is not None
    assert reply["error"]["code"] == -32601
    assert done is False


REPLAY_LIST = (
    "var replaydata=\n{\n   replays:[\n"
    '     {\n       file:"fartstriper.rpy"\n      ,carID:188\n      ,trackID:192\n'
    "      ,subSessionID:84333072\n      ,allCustIDs:[\n         4294967295,654027,965736]\n     }\n"
    '     ,{\n       file:"other.rpy"\n      ,carID:1\n      ,trackID:2\n      ,allCustIDs:[5]\n     }]\n};\n'
)


def test_parse_replay_entry_pulls_track_car_and_participants():
    entry = simlaunch.parse_replay_entry(REPLAY_LIST, "fartstriper.rpy")
    # the 4294967295 empty-slot sentinel is dropped; real participants kept
    assert entry == {"track_id": 192, "car_id": 188, "sess_carids": [654027, 965736]}


def test_parse_replay_entry_missing_file_is_none():
    assert simlaunch.parse_replay_entry(REPLAY_LIST, "nope.rpy") is None


def test_sim_running_reads_the_status_flag(monkeypatch):
    monkeypatch.setattr(simlaunch, "_get", lambda path, timeout=5.0: (200, "var simStatus={\n running:1\n};"))
    assert simlaunch.sim_running() is True


def test_sim_not_running(monkeypatch):
    monkeypatch.setattr(simlaunch, "_get", lambda path, timeout=5.0: (200, "var simStatus={ running:0 };"))
    assert simlaunch.sim_running() is False


def test_sim_running_is_unknown_when_the_service_is_down(monkeypatch):
    def boom(path, timeout=5.0):
        raise OSError("connection refused")

    monkeypatch.setattr(simlaunch, "_get", boom)
    assert simlaunch.sim_running() is None
