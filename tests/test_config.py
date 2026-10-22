from pathlib import Path

import pytest

from livery import config
from tests.conftest import OWNER, SETTINGS


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


def test_without_settings_the_first_run_says_where_they_go(tmp_path):
    missing = tmp_path / "new" / "config.toml"
    with pytest.raises(config.ConfigError) as e:
        config.load(missing)
    assert str(missing) in str(e.value)
    assert not missing.exists()
