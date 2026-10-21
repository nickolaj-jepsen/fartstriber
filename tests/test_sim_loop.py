import json
from dataclasses import replace

import pytest

from livery import config, metrics, paintfolder
from livery.check import NotChecked, checked_build, run_build
from tests.conftest import write_design


def test_temporary_writes_are_undone(tmp_path):
    cfg = replace(config.load(), documents=tmp_path)
    folder = cfg.paint_root / "car"
    folder.mkdir(parents=True)
    (folder / "car_1.tga").write_bytes(b"his")
    (folder / "car_spec_1.mip").write_bytes(b"his mip")

    with paintfolder.temporary(cfg, "car") as put:
        put({"car_1.tga": b"preview", "car_spec_1.tga": b"spec"})
        (folder / "car_spec_1.mip").write_bytes(b"rebuilt by the sim")

    assert (folder / "car_1.tga").read_bytes() == b"his"
    assert (folder / "car_spec_1.mip").read_bytes() == b"his mip"
    assert not (folder / "car_spec_1.tga").exists()


def test_sim_steps_refuse_a_build_that_did_not_pass_check(ws):
    write_design(ws, "d", 'def build(kit, p):\n    return Livery(base="#123456")\n')
    with pytest.raises(NotChecked):
        checked_build(ws, "d")
    assert run_build(ws, "d")["passed"]
    checked_build(ws, "d")
    (ws.out("d") / "car.tga").write_bytes(b"tampered")
    with pytest.raises(NotChecked):
        checked_build(ws, "d")


def test_metrics_lines_are_json(tmp_path):
    path = tmp_path / "metrics.jsonl"
    metrics.log("d", "sim", {"reload": 2.51234, "shot_side_l": None}, path=path, critic=None)
    metrics.log("d", "flat", {"total": 1.0}, path=path)
    lines = [json.loads(x) for x in path.read_text().splitlines()]
    assert [x["loop"] for x in lines] == ["sim", "flat"]
    assert lines[0]["durations_s"]["reload"] == 2.512
