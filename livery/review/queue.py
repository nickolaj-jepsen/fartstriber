"""Every waiting `livery review` on the machine, so one page shows them all: a folder per review (a group) under
the state dir's review/. The CLI writes group.json (its checkout, branch, process and items) and, after
Apply, result.json; the page's server writes decisions.json (the decisions and the request to apply). Each file
has one writer and is replaced whole."""

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from livery import state
from livery.errors import LiveryError

GROUP, DECISIONS, RESULT = "group.json", "decisions.json", "result.json"
APPLIED_FOR = 300  # seconds an applied review stays on the page, saying what it wrote


class ReviewOpen(LiveryError, RuntimeError):
    pass


class Gone(LiveryError, LookupError):
    """The group is no longer queued: it was cancelled."""


def folder() -> Path:
    return state.root() / "review"


def _dir(gid: str) -> Path:
    """A group's folder; an id from the page that isn't one names none."""
    if not re.fullmatch(r"[\w-]+", gid):
        raise Gone(f"no review {gid!r}")
    return folder() / gid


def _checkout(root: Path) -> str:
    return os.path.normcase(str(root.resolve()))


def _read(path: Path) -> dict | None:
    for _ in range(100):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except PermissionError:  # Windows: its writer is replacing it
            time.sleep(0.01)
        except ValueError:
            return None
    raise PermissionError(f"{path} stayed locked")


def _write(path: Path, data: object) -> None:
    """Never creates the group's folder: a write to a cancelled group fails (Gone) instead of reviving it."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
    except FileNotFoundError:
        raise Gone(f"review {path.parent.name} is no longer queued") from None
    for _ in range(100):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # Windows: a reader has it open
            time.sleep(0.01)
    os.replace(tmp, path)


def _branch(root: Path) -> str | None:
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "branch", "--show-current"], capture_output=True, text=True, timeout=30
        )
    except OSError:
        return None
    return (r.stdout.strip() or None) if r.returncode == 0 else None


def read(gid: str) -> dict | None:
    """A group with its decisions, apply request, result and status (waiting, applying, applied, failed or
    stale: its CLI died before Apply ran); None once it is gone."""
    try:
        d = _dir(gid)
    except Gone:
        return None
    group = _read(d / GROUP)
    if group is None:
        return None
    decided = _read(d / DECISIONS) or {}
    result = _read(d / RESULT)
    alive = state.alive(group["owner"])
    if result is not None:
        status = "failed" if "error" in result else "applied"
    elif not alive:
        status = "stale"
    else:
        status = "applying" if decided.get("apply") else "waiting"
    return {
        **group,
        "decisions": decided.get("decisions", {}),
        "apply": decided.get("apply"),
        "result": result,
        "alive": alive,
        "status": status,
    }


def groups(prune: bool = False) -> list[dict]:
    """Every queued group, oldest first. With `prune`, drops applied ones once they have shown what they
    wrote for a while, and folders a group never finished writing."""
    out = []
    for d in [p for p in folder().iterdir() if p.is_dir()] if folder().exists() else []:
        g = read(d.name)
        if g is None:
            if prune and time.time() - d.stat().st_mtime > 60:
                shutil.rmtree(d, ignore_errors=True)
            continue
        if prune and g["status"] == "applied" and time.time() - g["result"]["at"] > APPLIED_FOR:
            shutil.rmtree(d, ignore_errors=True)
            continue
        out.append(g)
    return sorted(out, key=lambda g: g["created"])


def enqueue(root: Path, items: list[dict]) -> str:
    """Queues a review from the checkout at `root`. A stale or failed one from the same checkout is taken
    over: its decisions on items still in the review are kept. One that is still waiting is refused, since
    two Applies in one checkout would commit over each other."""
    here = _checkout(root)
    ids = {i["id"] for i in items}
    kept: dict[str, dict] = {}
    for g in groups():
        if _checkout(Path(g["root"])) != here or g["status"] == "applied":
            continue
        if g["alive"] and g["status"] != "failed":
            raise ReviewOpen(
                f"a review from this checkout is still waiting ({g['id']}: "
                f"{', '.join(i['title'] for i in g['items'])}): apply it, or take it off the page with "
                "livery review --cancel"
            )
        kept |= {k: v for k, v in g["decisions"].items() if k in ids}
        cancel(g["id"])
    gid = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    d = folder() / gid
    d.mkdir(parents=True)
    _write(d / DECISIONS, {"decisions": kept, "apply": None})
    group = {
        "id": gid,
        "root": str(root.resolve()),
        "branch": _branch(root),
        "owner": state.me(),
        "created": time.time(),
        "items": items,
    }
    _write(d / GROUP, group)  # last: a group is listed once this exists
    return gid


def decide(gid: str, item_id: str, decision: dict) -> None:
    """The server's write; it holds one lock around every read and write of decisions.json."""
    d = _dir(gid)
    decided = _read(d / DECISIONS) or {"decisions": {}, "apply": None}
    decided["decisions"][item_id] = decision
    _write(d / DECISIONS, decided)


def request_apply(gid: str) -> None:
    d = _dir(gid)
    decided = _read(d / DECISIONS) or {"decisions": {}, "apply": None}
    decided["apply"] = {"at": time.time()}
    _write(d / DECISIONS, decided)


def finish(gid: str, result: dict) -> None:
    """The CLI's record of its Apply: what it wrote, or the error that stopped it."""
    _write(_dir(gid) / RESULT, {"at": time.time(), **result})


def cancel(gid: str) -> bool:
    d = _dir(gid)
    for _ in range(100):
        if not d.exists():
            return False
        try:
            shutil.rmtree(d)
            return True
        except OSError:  # Windows: the server or the CLI has a file in it open
            time.sleep(0.02)
    shutil.rmtree(d)
    return True


def mine(root: Path) -> list[dict]:
    """The groups queued from the checkout at `root` that haven't applied."""
    here = _checkout(root)
    return [g for g in groups() if _checkout(Path(g["root"])) == here and g["status"] != "applied"]


def summary(g: dict) -> dict:
    """A group as `livery review --list` shows it."""
    return {
        "id": g["id"],
        "status": g["status"],
        "branch": g["branch"],
        "root": g["root"],
        "items": [i["id"] for i in g["items"]],
        "decided": len(g["decisions"]),
        "pid": g["owner"].get("pid"),
    }
