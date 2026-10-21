"""Writing into the owner's iRacing paint folder.

- `temporary`: sim runs. Before a file is first written, the original goes into a journal in the state dir
  (on disk, so it outlives a killed run); the block's end puts every file back and drops the journal, and
  `recover` replays the journal of a run that died.
- `install`: the final install. What was there before the first one is kept in the state dir, and `restore`
  puts it back.
"""

import os
import shutil
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from itertools import count
from pathlib import Path

from livery import state
from livery.config import REPO, Config

HOLD_S = 5.0  # how long another process may hold a paint file open before a write gives up
_runs = count()


def _retry(op: Callable[[], object]) -> None:
    """Run a file operation, retrying while another process holds the file. Something once held the paint
    TGA just after a preview run (not reproduced, holder unknown); a failed restore would leave our paint."""
    deadline = time.monotonic() + HOLD_S
    while True:
        try:
            op()
            return
        except PermissionError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.1)


def _write(path: Path, data: bytes) -> None:
    _retry(lambda: path.write_bytes(data))


def _copy(src: Path, dest: Path) -> None:
    _retry(lambda: shutil.copy2(src, dest))


def _unlink(path: Path) -> None:
    _retry(lambda: path.unlink(missing_ok=True))


def _durable(path: Path, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def paint_dir(cfg: Config, paint_folder: str) -> Path:
    return cfg.paint_root / paint_folder


def _with_mip(name: str) -> list[str]:
    """A spec TGA's .mip belongs to it: the sim regenerates it from the TGA."""
    return [name, str(Path(name).with_suffix(".mip"))] if name.startswith("car_spec_") else [name]


def journals() -> Path:
    return state.root() / "paint-journal"


@contextmanager
def temporary(cfg: Config, paint_folder: str, what: str = "") -> Iterator[Callable[[dict[str, bytes]], None]]:
    """Yields put(files); on exit every file touched (and its .mip) is as it was before the block.

    The journal holds manifest.json (the folder, the owner process and each file as 'saved' or 'absent')
    and the saved originals. The manifest names a file before that file is first written, so a journal
    whose owner died always holds what is needed to undo it.
    """
    target = paint_dir(cfg, paint_folder)
    run = journals() / f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{next(_runs)}"
    manifest = {"folder": str(target), "what": what, "since": time.time(), **state.me(), "files": {}}
    saved: dict[str, bytes | None] = {}

    def put(files: dict[str, bytes]) -> None:
        for name, data in files.items():
            new = [n for n in _with_mip(name) if n not in saved]
            if new:
                (run / "files").mkdir(parents=True, exist_ok=True)
                for n in new:
                    original = (target / n).read_bytes() if (target / n).exists() else None
                    if original is not None:
                        _durable(run / "files" / n, original)
                    saved[n] = original
                    manifest["files"][n] = "absent" if original is None else "saved"
                state.write_json(run / "manifest.json", manifest)
            _write(target / name, data)

    try:
        yield put
    finally:
        for name, data in saved.items():
            if data is None:
                _unlink(target / name)
            else:
                _write(target / name, data)
        shutil.rmtree(run, ignore_errors=True)  # only once every file is back


def _replay(run: Path, manifest: dict) -> list[str]:
    folder = Path(manifest["folder"])
    notes = []
    for name, kind in manifest["files"].items():
        if kind == "saved":
            _copy(run / "files" / name, folder / name)
            notes.append(f"put back {folder / name}")
        else:
            _unlink(folder / name)
            notes.append(f"removed {folder / name}")
    return notes


def stale() -> list[dict]:
    """Journals whose run died before it put the paint back."""
    out = []
    for run in sorted(journals().glob("*/")):
        manifest = state.read_json(run / "manifest.json")
        if manifest is None or not state.alive(manifest):
            out.append({"journal": str(run), **(manifest or {})})
    return out


def recover() -> list[str]:
    """Put back the paint of every run that died inside `temporary`. Returns what it did."""
    notes = []
    for entry in stale():
        run = Path(entry["journal"])
        if entry.get("files"):
            what = entry.get("what") or f"pid {entry.get('pid')}"
            notes += [f"{n} (after {what} died)" for n in _replay(run, entry)]
        # Without a manifest the run died before it wrote any paint.
        shutil.rmtree(run)
    return notes


def _backups() -> Path:
    return state.root() / "paint-backup"


def _old_backups() -> list[Path]:
    """Where install backups lived before the state dir: this checkout's .cache and, from a worktree, the
    main checkout's."""
    dirs = [REPO / ".cache" / "paint-backup"]
    dotgit = REPO / ".git"
    if dotgit.is_file():  # a worktree: 'gitdir: <main>/.git/worktrees/<name>'
        gitdir = Path(dotgit.read_text(encoding="utf-8").split(":", 1)[1].strip())
        dirs.append(gitdir.parent.parent.parent / ".cache" / "paint-backup")
    return dirs


def _backup_dir(paint_folder: str, log: Callable[[str], object] = print) -> Path:
    """The folder's install backup, moved into the state dir if it is still in a checkout's .cache."""
    d = _backups() / paint_folder
    if not d.exists():
        for old in _old_backups():
            if (old / paint_folder / "state.json").exists():
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(old / paint_folder, d)
                log(f"moved the install backup of {paint_folder} from {old} to {d.parent}")
                break
    return d


def backed_up(log: Callable[[str], object] = print) -> list[str]:
    """Paint folders with an install backup."""
    names = {p.name for old in _old_backups() if old.is_dir() for p in old.iterdir() if p.is_dir()}
    for name in names:
        _backup_dir(name, log)
    return sorted(p.name for p in _backups().glob("*/") if (p / "state.json").exists())


def install(cfg: Config, paint_folder: str, files: dict[str, bytes], log=print) -> list[Path]:
    """Write files (by name) into the paint folder. Whatever was there first is backed up once."""
    target = paint_dir(cfg, paint_folder)
    backup = _backup_dir(paint_folder, log)
    backup.mkdir(parents=True, exist_ok=True)
    state_file = backup / "state.json"
    record = state.read_json(state_file) or {"backed_up": [], "added": []}
    written = []
    for name, data in files.items():
        dest = target / name
        for n in _with_mip(name):  # a spec TGA's .mip is regenerated by the sim; keep the original too
            if n not in record["backed_up"] + record["added"]:
                if (target / n).exists():
                    _copy(target / n, backup / n)
                    record["backed_up"].append(n)
                else:
                    record["added"].append(n)
        state.write_json(state_file, record)
        _write(dest, data)
        written.append(dest)
    return written


def restore(cfg: Config, paint_folder: str, log=print) -> list[str]:
    """Put the paint folder back as it was before the first install."""
    target = paint_dir(cfg, paint_folder)
    backup = _backup_dir(paint_folder, log)
    record = state.read_json(backup / "state.json")
    if record is None:
        return []
    done = []
    for name in record["added"]:
        _unlink(target / name)
        done.append(f"removed {name}")
    for name in record["backed_up"]:
        _copy(backup / name, target / name)
        done.append(f"restored {name}")
    shutil.rmtree(backup)
    return done
