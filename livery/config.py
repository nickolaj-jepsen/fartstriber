"""Settings, from two files, and where everything lives.

The project's policy (`policy.toml` in the checkout: lint thresholds, the views a preview shoots and the
sim window's size) is committed; its `[lint]` table is part of every check's hash. The user's settings
(`config.toml` in the machine state dir, shared by every worktree: the owner's name, IDs, the iRacing
folders, the preview replay) are not; `livery.example.toml` documents every key.
"""

import hashlib
import json
import subprocess
import sys
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
    the settings as they are, without the first run's migration and whether or not the rest is there."""
    try:
        return _owner(_read(user_file()))
    except (OSError, ConfigError):
        return NO_OWNER


def load(path: Path | None = None) -> Config:
    """The user's settings. The first run writes them from a checkout's old livery.toml, if there is one."""
    path = path or user_file()
    if not path.exists():
        migrate(path, REPO)
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


# The first run: the user's settings came from the checkout's livery.toml until it was split.


def _git(root: Path, *args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30)
    except OSError:
        return ""
    return r.stdout if r.returncode == 0 else ""


def _legacy(root: Path) -> str | None:
    """The old livery.toml: in the checkout, or the last version git has of it."""
    old = root / "livery.toml"
    if old.exists():
        return old.read_text(encoding="utf-8")
    deleted = _git(root, "log", "-1", "--format=%H", "--diff-filter=D", "--", "livery.toml").strip()
    return _git(root, "show", f"{deleted}^:livery.toml" if deleted else "HEAD:livery.toml") or None


def _toml(value) -> str:
    return json.dumps(value) if isinstance(value, str) else str(value)


def migrate(path: Path, root: Path) -> None:
    """Write the user's settings from the old livery.toml, or say where they go."""
    text = _legacy(root)
    if text is None:
        raise ConfigError(f"no settings at {path}: copy {root / EXAMPLE} there and fill it in")
    old = tomllib.loads(text)
    sim = old.get("sim", {})
    documents = _path(old.get("paint_root", "~/Documents/iRacing/paint")).parent
    replay = documents / "replay" / sim.get("replay", "")
    name = _git(root, "config", "user.name").split()
    values = {
        "owner": name[0] if name else None,
        "customer_id": old.get("customer_id"),
        "team_id": old.get("team_id"),
        "archive_dir": old.get("archive_dir"),
        "iracing": {
            "documents": str(documents),
            "install": r"C:\Program Files (x86)\iRacing",
            "process": sim.get("process"),
        },
        "replay": {
            "file": sim.get("replay"),
            "sha256": sha256(replay) if replay.is_file() else None,
            "subsession": sim.get("subsession"),
            "track_name": sim.get("track_name"),
            "track": sim.get("track"),
        },
    }
    lines = [f"# Written from the old livery.toml; {EXAMPLE} in the repo describes every key."]
    for k, v in values.items():
        if isinstance(v, dict):
            lines += ["", f"[{k}]", *(f"{kk} = {_toml(vv)}" for kk, vv in v.items() if vv is not None)]
        elif v is not None:
            lines.append(f"{k} = {_toml(v)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote your settings to {path} from the old livery.toml", file=sys.stderr)


@dataclass(frozen=True)
class Workspace:
    """Where designs, kits, assets and the project's policy live, and where output goes. Tests point it at a
    temp dir. The iRacing folders come from the user's settings."""

    root: Path = REPO
    assets: Path = REPO / "assets"
    policy_file: Path = REPO / "policy.toml"
    agents: Path = REPO / ".claude"  # the agent definitions and skills the critic's pack copies from
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
        return self.root / "prefs"

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
