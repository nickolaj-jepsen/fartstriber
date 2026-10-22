import sys
import time
from dataclasses import replace

import pytest

from livery import approvals, cli, config
from livery.export import export
from livery.review import apply as review_apply
from tests.conftest import checked


@pytest.fixture
def cfg(tmp_path):
    return replace(config.load(), customer_id=111, team_id=222, archive_dir=tmp_path / "archive")


def _archive(cfg, name: str, stamp: str, mip: str, data: bytes) -> None:
    folder = cfg.archive_dir / name / stamp
    folder.mkdir(parents=True)
    (folder / mip).write_bytes(data)


def test_export_refuses_a_build_he_has_not_approved(ws, cfg, tmp_path):
    checked(ws, "d")
    with pytest.raises(approvals.NotApproved):
        export(cfg, ws, "d", out=tmp_path / "out")
    assert not (tmp_path / "out").exists()

    out, steps = export(cfg, ws, "d", out=tmp_path / "out", unapproved=True)

    assert sorted(p.name for p in out.iterdir()) == ["car_111.tga", "car_spec_111.tga"]
    assert (out / "car_111.tga").read_bytes() == (ws.out("d") / "car.tga").read_bytes()
    assert (out / "car_spec_111.tga").read_bytes() == (ws.out("d") / "car_spec.tga").read_bytes()
    assert str(out) in steps


def test_export_takes_the_mip_archived_since_the_approval_under_the_upload_name(ws, cfg, tmp_path):
    checked(ws, "d")  # with a flat preview of its build, which a final is approved with
    _archive(cfg, "d", "20000101-000000", "car_spec_111.mip", b"from an earlier build")
    item = {"id": "final:d", "kind": "final", "title": "d", "pin": approvals.pin_of(ws, "d")}
    review_apply.apply(ws, [item], {"final:d": {"decision": "accept"}})

    out, _ = export(cfg, ws, "d", out=tmp_path / "out")
    assert not (out / "car_spec_111.mip").exists()

    later = time.strftime("%Y%m%d-%H%M%S", time.localtime(time.time() + 5))
    _archive(cfg, "d", later, "car_spec_team_222.mip", b"built from this spec")
    out, steps = export(cfg, ws, "d", out=tmp_path / "out")

    assert (out / "car_spec_111.mip").read_bytes() == b"built from this spec"
    assert "car_spec_111.mip" in steps


def test_the_export_command_refuses_without_approval(monkeypatch, ws, tmp_path):
    checked(ws, "d")
    monkeypatch.setattr(cli, "Workspace", lambda: ws)
    monkeypatch.setattr(sys, "argv", ["livery", "export", "d", "--out", str(tmp_path / "out")])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 1
