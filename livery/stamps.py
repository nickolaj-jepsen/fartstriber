"""What build a preview shows. `preview flat` writes `build.json` into `flat/` (or `flat-sets/<key>/` for
`--set` values) and each sheet a `<sheet>.build.json`; each sim run writes its `manifest.json`
(`livery.simsession`). Each holds the build's digests and its design and toolchain hashes. A final review, an
approval and a critic pack take only previews whose stamp shows the checked build."""

import hashlib
import json
import time
from pathlib import Path

from livery import hashing
from livery.config import Workspace
from livery.errors import LiveryError

SHOWN = ("car.tga", "car_spec.tga")  # what reaches the car; every other output follows from them
STAMP = "build.json"


def digests(outputs: dict[str, bytes]) -> dict[str, str]:
    return {k: hashlib.sha256(outputs[k]).hexdigest() for k in SHOWN if k in outputs}


def make(ws: Workspace, name: str, outputs: dict[str, bytes], sets: dict[str, str] | None = None) -> dict:
    return {
        "design": name,
        "digests": digests(outputs),
        "design_hash": hashing.design_hash(ws, name),
        "toolchain_hash": hashing.toolchain_hash(),
        "sets": dict(sorted((sets or {}).items())),
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def write(path: Path, stamp: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stamp, indent=1), encoding="utf-8", newline="\n")


def read(path: Path) -> dict | None:
    try:
        stamp = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return stamp if isinstance(stamp, dict) else None


def shows(stamp: dict | None, built: dict[str, str]) -> bool:
    """Whether a preview stamped so shows exactly the build with these digests, no --set values on top."""
    if not stamp or stamp.get("sets") or not built.get("car.tga"):
        return False
    shown = stamp.get("digests") or {}
    return all(shown.get(k) == built.get(k) for k in SHOWN)


def sets_key(sets: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(sorted(sets.items())).encode()).hexdigest()[:10]


def flat_dir(ws: Workspace, name: str, sets: dict[str, str] | None = None) -> Path:
    """flat/ shows the script as it is; a --set variant goes to flat-sets/<key>/, never over it."""
    return ws.out(name) / ("flat-sets" / Path(sets_key(sets)) if sets else Path("flat"))


def baked_dir(ws: Workspace, name: str, sets: dict[str, str] | None = None) -> Path:
    """baked/ beside flat/: the build at the preview views in sim light, from the kit's capture."""
    return ws.out(name) / ("baked-sets" / Path(sets_key(sets)) if sets else Path("baked"))


def of_flat(ws: Workspace, name: str) -> dict | None:
    return read(flat_dir(ws, name) / STAMP)


def runs(ws: Workspace, name: str) -> list[Path]:
    """The design's whole sim runs, oldest first."""
    return sorted((ws.out(name) / "sim").glob("*/"))


def of_run(run: Path) -> dict | None:
    return read(run / "manifest.json")


def matching_run(ws: Workspace, name: str, built: dict[str, str]) -> Path | None:
    """The latest sim run of exactly the build with these digests."""
    return next((r for r in reversed(runs(ws, name)) if shows(of_run(r), built)), None)


def hashes(ws: Workspace, name: str) -> dict[str, str] | None:
    """The design and toolchain hashes as the files stand now; None for a script that can't be hashed."""
    try:
        return {"design_hash": hashing.design_hash(ws, name), "toolchain_hash": hashing.toolchain_hash()}
    except (LiveryError, SyntaxError):
        return None


def of_version(stamp: dict | None, now: dict[str, str] | None) -> bool:
    """Whether a preview stamped so shows the design and library at these hashes (an unchecked version
    has no build to match digests with)."""
    if not stamp or not now or stamp.get("sets"):
        return False
    return all(stamp.get(k) == v for k, v in now.items())


def latest_of_version(ws: Workspace, name: str, now: dict[str, str] | None) -> Path | None:
    return next((r for r in reversed(runs(ws, name)) if of_version(of_run(r), now)), None)
