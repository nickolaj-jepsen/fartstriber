import hashlib
import subprocess
import tomllib
from pathlib import Path

import pytest

from livery import config
from tests.conftest import OWNER, SETTINGS

OLD = """
customer_id = 7
team_id = 8
paint_root = "{docs}/paint"
archive_dir = "~/mips"

[sim]
process = "x.exe"
replay = "r.rpy"
track = 'road\\track'
subsession = 99
track_name = "road track"

[lint]
min_text_px = 24
"""


def settings(tmp_path: Path, **change: str) -> Path:
    """The fixture settings, with the line of each key in `change` replaced."""
    text = SETTINGS.format(root=tmp_path.as_posix(), sha="0" * 64, replay=1, owner=OWNER)
    for key, line in change.items():
        text = "\n".join(line if ln.startswith(f"{key} =") else ln for ln in text.splitlines())
    p = tmp_path / "settings.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_home_paths_expand(tmp_path):
    cfg = config.load(settings(tmp_path, archive_dir='archive_dir = "~/mips"'))
    assert cfg.archive_dir is not None
    assert cfg.archive_dir.is_absolute()


def test_empty_archive_dir_means_unset(tmp_path):
    assert config.load(settings(tmp_path, archive_dir='archive_dir = ""')).archive_dir is None


def test_the_owner_is_named_by_the_settings(private_state):
    assert config.owner() == config.load().owner == OWNER


def test_without_an_owner_setting_the_owner_is_the_owner(tmp_path, private_state):
    no_owner = settings(tmp_path, owner="")
    assert config.load(no_owner).owner == config.NO_OWNER
    no_owner.replace(config.user_file())
    assert config.owner() == config.NO_OWNER
    config.user_file().unlink()
    assert config.owner() == config.NO_OWNER


def test_a_missing_setting_is_an_error_that_names_it(tmp_path):
    with pytest.raises(config.ConfigError) as e:
        config.load(settings(tmp_path, sha256=""))
    assert "replay.sha256" in str(e.value)


def test_the_first_run_writes_the_users_settings_from_the_old_livery_toml(tmp_path):
    docs = tmp_path / "docs"
    (docs / "replay").mkdir(parents=True)
    (docs / "replay" / "r.rpy").write_bytes(b"a replay")
    (tmp_path / "livery.toml").write_text(OLD.format(docs=docs.as_posix()), encoding="utf-8")
    target = tmp_path / "new" / "config.toml"

    config.migrate(target, tmp_path)

    written = tomllib.loads(target.read_text(encoding="utf-8"))
    assert (written["customer_id"], written["team_id"]) == (7, 8)
    assert Path(written["iracing"]["documents"]) == docs
    assert written["replay"] == {
        "file": "r.rpy",
        "sha256": hashlib.sha256(b"a replay").hexdigest(),
        "subsession": 99,
        "track_name": "road track",
        "track": "road\\track",
    }
    assert "lint" not in written  # policy stays in the checkout


def test_after_the_split_the_first_run_finds_the_old_livery_toml_in_git(tmp_path):
    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *args], capture_output=True, check=True)

    git("init", "-q")
    for key, value in (
        ("user.name", "Test Person"),
        ("user.email", "t@example.com"),
        ("commit.gpgsign", "false"),
    ):
        git("config", key, value)
    old = tmp_path / "livery.toml"
    old.write_text(OLD.format(docs=tmp_path.as_posix()).replace("customer_id = 7", "customer_id = 1"))
    git("add", "livery.toml")
    git("commit", "-q", "-m", "settings")
    old.write_text(OLD.format(docs=tmp_path.as_posix()))
    git("commit", "-q", "-am", "the last settings")
    git("rm", "-q", "livery.toml")
    git("commit", "-q", "-m", "split")
    target = tmp_path / "new" / "config.toml"

    config.migrate(target, tmp_path)

    written = tomllib.loads(target.read_text(encoding="utf-8"))
    assert written["customer_id"] == 7
    assert written["owner"] == "Test"


def test_without_old_settings_the_first_run_says_where_they_go(tmp_path):
    with pytest.raises(config.ConfigError):
        config.migrate(tmp_path / "new" / "config.toml", tmp_path)
    assert not (tmp_path / "new" / "config.toml").exists()


def test_calibration_views_get_cameras_but_previews_shoot_only_their_own(tmp_path):
    p = tmp_path / "policy.toml"
    p.write_text(
        '[lint]\n[preview]\nviews = ["a", "b"]\ncalibrate = ["c", "b"]\nwindow = [16, 9]\n', encoding="utf-8"
    )

    policy = config.policy(p)

    assert policy.preview == ("a", "b")
    assert policy.cameras == ("a", "b", "c")  # each view once, the previews' first
