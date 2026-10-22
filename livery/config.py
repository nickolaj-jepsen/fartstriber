"""Settings, from two files, and where everything lives.

The project's policy (`policy.toml` in the checkout: lint thresholds, the views a preview shoots and the
sim window's size) is committed; its `[lint]` table is part of every check's hash. The user's settings
(`config.toml` in the machine state dir, shared by every worktree: the owner's name, IDs, the iRacing
folders, the preview replay) are not; `livery.example.toml` documents every key.
"""

import hashlib
import re
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from livery import state
from livery.errors import LiveryError

REPO = Path(__file__).resolve().parent.parent
CACHE = REPO / ".cache"
USER_FILE = "config.toml"
EXAMPLE = "livery.example.toml"
NO_OWNER = "the owner"  # what the tool calls the owner while the settings name nobody


class ConfigError(LiveryError):
    """A settings file that is missing, or lacks a key; the message names which."""


@dataclass(frozen=True)
class Shot:
    frame: int  # replay frame from the start of the file: exact, unlike a session-time search
    group: str


@dataclass(frozen=True)
class Policy:
    lint: dict  # the output lint's thresholds by name; `lint.run.thresholds` checks and types them
    preview: tuple[str, ...]  # the views `livery preview sim` shoots, in order; the kit's profile frames each
    window: tuple[int, int]  # the sim window's client size every sim run shoots at


@dataclass(frozen=True)
class Replay:
    file: str  # under <documents>/replay; empty for a kit's own replay, whose file the SDK doesn't name
    sha256: str
    subsession: int  # as the SDK names it (WeekendInfo SubSessionID): sim runs refuse another replay
    track_name: str  # WeekendInfo TrackName
    track: str  # the track's path in camera.ini, e.g. 'daytona\2011\road': where its camera set lives

    @property
    def label(self) -> str:
        return self.file or f"the replay of subsession {self.subsession} at {self.track_name}"


def track_path(track_name: str) -> str:
    """The track's path in camera.ini and under cameras/tracks: its SDK name with backslashes for spaces,
    as a car's paint folder 'mx5 mx52016' is 'mx5\\mx52016' there."""
    return track_name.replace(" ", "\\")


@dataclass(frozen=True)
class Config:
    owner: str
    customer_id: int
    team_id: int | None
    archive_dir: Path | None
    documents: Path  # Documents\iRacing
    install: Path  # the iRacing install, for the UI's car previews
    process: str
    replay: Replay

    @property
    def paint_root(self) -> Path:
        return self.documents / "paint"

    @property
    def ui_cars(self) -> Path:
        return self.install / "httproot" / "cars"

    @property
    def replay_file(self) -> Path:
        return self.documents / "replay" / self.replay.file


def _get(raw: dict, key: str, where: Path) -> Any:
    node = raw
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            raise ConfigError(f"{where} has no {key}; {EXAMPLE} describes it")
        node = node[part]
    return node


def _path(value: str) -> Path:
    return Path(value).expanduser()


def _read(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from None


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


@lru_cache(maxsize=4)
def _policy(path: Path, _mtime: int) -> Policy:
    raw = _read(path)
    w, h = _get(raw, "preview.window", path)
    return Policy(
        lint=dict(_get(raw, "lint", path)),
        preview=tuple(_get(raw, "preview.views", path)),
        window=(int(w), int(h)),
    )


def policy(path: Path) -> Policy:
    if not path.exists():
        raise ConfigError(f"no policy file at {path}")
    return _policy(path, path.stat().st_mtime_ns)


def user_file() -> Path:
    return state.root() / USER_FILE


def _owner(raw: dict) -> str:
    name = raw.get("owner")
    return name.strip() if isinstance(name, str) and name.strip() else NO_OWNER


def owner() -> str:
    """The owner's name, for what the tool says and records: the `owner` setting, else 'the owner'. Reads
    the settings as they are, whether or not the rest is there."""
    try:
        return _owner(_read(user_file()))
    except (OSError, ConfigError):
        return NO_OWNER


def person(name: str | None = None) -> str:
    """A person's folder name under prefs/: their name (the owner's by default) in lower case, other
    characters as dashes."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or owner()).lower()).strip("-")
    return "owner" if name is None and owner() == NO_OWNER else slug or "owner"


def load(path: Path | None = None) -> Config:
    """The user's settings."""
    path = path or user_file()
    if not path.exists():
        raise ConfigError(
            f"no settings at {path}: copy {REPO / EXAMPLE} there and fill it in; `livery config show` then prints it"
        )
    raw = _read(path)
    archive = raw.get("archive_dir")
    return Config(
        owner=_owner(raw),
        customer_id=int(_get(raw, "customer_id", path)),
        team_id=int(raw["team_id"]) if raw.get("team_id") else None,
        archive_dir=_path(archive) if archive else None,
        documents=_path(_get(raw, "iracing.documents", path)),
        install=_path(_get(raw, "iracing.install", path)),
        process=str(_get(raw, "iracing.process", path)),
        replay=Replay(
            file=str(_get(raw, "replay.file", path)),
            sha256=str(_get(raw, "replay.sha256", path)),
            subsession=int(_get(raw, "replay.subsession", path)),
            track_name=str(_get(raw, "replay.track_name", path)),
            track=str(_get(raw, "replay.track", path)),
        ),
    )


@dataclass(frozen=True)
class Workspace:
    """Where designs, kits, assets and the project's policy live, and where output goes. Tests point it at a
    temp dir. The iRacing folders come from the user's settings."""

    root: Path = REPO
    assets: Path = REPO / "assets"
    policy_file: Path = REPO / "policy.toml"
    agents: Path = REPO / ".claude"  # the agent roles the critic's pack copies from
    skills: Path = REPO / ".agents" / "skills"  # the skills it copies from, in the shared location
    ui_cars_dir: Path | None = None  # the UI's car previews; None: under the user's iRacing install

    @property
    def designs(self) -> Path:
        return self.root / "designs"

    @property
    def kits(self) -> Path:
        return self.root / "kits"

    @property
    def fonts(self) -> Path:
        return self.assets / "fonts"

    @property
    def sponsors(self) -> Path:
        return self.assets / "sponsors"

    @property
    def marks(self) -> Path:
        return self.assets / "marks"

    @property
    def cache(self) -> Path:
        return self.root / ".cache"

    @property
    def references(self) -> Path:
        return self.root / "references.md"

    @property
    def prefs(self) -> Path:
        """The owner's records; each person who reviews has a folder of their own (livery.prefs)."""
        return self.root / "prefs" / person()

    @property
    def policy(self) -> Policy:
        return policy(self.policy_file)

    @property
    def ui_cars(self) -> Path:
        return self.ui_cars_dir or load().ui_cars

    def design_dir(self, name: str) -> Path:
        return self.designs / name

    def out(self, name: str) -> Path:
        return self.cache / "designs" / name
