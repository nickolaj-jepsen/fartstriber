import json
from dataclasses import replace
from pathlib import Path

import pytest

from livery import config, paintfolder, state


@pytest.fixture
def cfg(tmp_path) -> config.Config:
    cfg = replace(config.load(), documents=tmp_path)
    (cfg.paint_root / "car").mkdir(parents=True)
    return cfg


def test_restore_puts_the_folder_back_as_it_was(cfg):
    folder = cfg.paint_root / "car"
    (folder / "car_1.tga").write_bytes(b"theirs")
    (folder / "other.tga").write_bytes(b"untouched")

    paintfolder.install(cfg, "car", {"car_1.tga": b"ours", "car_spec_1.tga": b"spec"})
    paintfolder.install(cfg, "car", {"car_1.tga": b"ours again"})
    (folder / "car_spec_1.mip").write_bytes(b"made by the sim")

    assert paintfolder.backed_up() == ["car"]
    paintfolder.restore(cfg, "car")

    assert (folder / "car_1.tga").read_bytes() == b"theirs"
    assert not (folder / "car_spec_1.tga").exists()
    assert not (folder / "car_spec_1.mip").exists()
    assert (folder / "other.tga").read_bytes() == b"untouched"
    assert paintfolder.backed_up() == []


def test_a_backup_left_in_a_checkout_moves_to_the_state_dir(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(paintfolder, "REPO", tmp_path / "checkout")
    old = tmp_path / "checkout" / ".cache" / "paint-backup" / "car"
    old.mkdir(parents=True)
    (old / "car_1.tga").write_bytes(b"theirs")
    (old / "state.json").write_text(json.dumps({"backed_up": ["car_1.tga"], "added": []}))
    (cfg.paint_root / "car" / "car_1.tga").write_bytes(b"ours")

    assert paintfolder.backed_up(log=lambda _m: None) == ["car"]
    assert not old.exists()  # moved, so a second restore can't put an old file back
    paintfolder.restore(cfg, "car")

    assert (cfg.paint_root / "car" / "car_1.tga").read_bytes() == b"theirs"


def test_the_originals_are_on_disk_before_the_first_write(cfg):
    folder = cfg.paint_root / "car"
    (folder / "car_1.tga").write_bytes(b"his")

    with paintfolder.temporary(cfg, "car", "test") as put:
        put({"car_1.tga": b"preview", "car_spec_1.tga": b"spec"})
        [run] = paintfolder.journals().glob("*/")
        manifest = json.loads((run / "manifest.json").read_text())
        assert manifest["files"] == {
            "car_1.tga": "saved",
            "car_spec_1.tga": "absent",
            "car_spec_1.mip": "absent",
        }
        assert (run / "files" / "car_1.tga").read_bytes() == b"his"
        assert paintfolder.stale() == []  # its owner is alive: not for recovery

    assert list(paintfolder.journals().glob("*/")) == []


def test_a_dead_runs_journal_is_replayed(cfg):
    folder = cfg.paint_root / "car"
    (folder / "car_1.tga").write_bytes(b"his")
    run = paintfolder.journals() / "dead"
    (run / "files").mkdir(parents=True)
    (run / "files" / "car_1.tga").write_bytes(b"his")
    files = {"car_1.tga": "saved", "car_spec_1.tga": "absent"}
    state.write_json(run / "manifest.json", {"folder": str(folder), "pid": 2**31 - 2, "files": files})
    (folder / "car_1.tga").write_bytes(b"preview")
    (folder / "car_spec_1.tga").write_bytes(b"spec")

    assert paintfolder.recover()

    assert (folder / "car_1.tga").read_bytes() == b"his"
    assert not (folder / "car_spec_1.tga").exists()
    assert not run.exists()


def test_a_file_held_for_a_moment_is_written_once_it_is_free(cfg, monkeypatch):
    (cfg.paint_root / "car" / "car_1.tga").write_bytes(b"theirs")
    real, held = Path.write_bytes, {"n": 2}

    def write_bytes(self, data):
        if held["n"]:
            held["n"] -= 1
            raise PermissionError(32, "in use by another process")
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", write_bytes)
    with paintfolder.temporary(cfg, "car") as put:
        put({"car_1.tga": b"ours"})
        assert (cfg.paint_root / "car" / "car_1.tga").read_bytes() == b"ours"
        held["n"] = 2

    assert (cfg.paint_root / "car" / "car_1.tga").read_bytes() == b"theirs"


def test_a_file_held_for_good_still_fails_and_its_journal_stays(cfg, monkeypatch):
    (cfg.paint_root / "car" / "car_1.tga").write_bytes(b"theirs")
    monkeypatch.setattr(paintfolder, "HOLD_S", 0.2)

    def write_bytes(self, data):
        raise PermissionError(32, "in use by another process")

    def preview():
        with paintfolder.temporary(cfg, "car") as put:
            put({"car_1.tga": b"ours"})
            monkeypatch.setattr(Path, "write_bytes", write_bytes)  # held from here on

    with pytest.raises(PermissionError):
        preview()

    [run] = paintfolder.journals().glob("*/")
    assert (run / "files" / "car_1.tga").read_bytes() == b"theirs"  # for the next run to put back
