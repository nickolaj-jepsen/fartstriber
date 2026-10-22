"""SessionStart: the shared state a session would otherwise find by accident, in a few lines.

The branch and uncommitted paths, who holds the sim lock and this checkout's reviews on the shared review page
(both machine-wide, in the state dir, read through livery), and approved designs the last `livery build --all`
found changed.

It also puts the session's id and transcript in the environment of the session's commands, so what an agent
records through livery says which session wrote it (livery.prefs).
"""

import contextlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # this checkout's livery

SHOW_PATHS = 4
TAKEN_OVER = "the next `livery review` here takes the decisions over, `livery review --cancel` drops them"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=5, check=True
    ).stdout


def _ago(t: float) -> str:
    minutes = int((time.time() - t) / 60)
    return f"{minutes} min ago" if minutes < 120 else f"{minutes // 60} h ago"


def _lock() -> list[str]:
    from livery import simlock

    holder = simlock.holder()
    if holder is None:
        return []
    where = f" in {holder['checkout']}" if holder.get("checkout") else ""
    taken = f", taken {_ago(holder['since'])}" if holder.get("since") else ""
    return [f"sim lock: held by pid {holder['pid']}{where} for {holder.get('what')}{taken}"]


def _reviews(root: Path) -> list[str]:
    """This checkout's reviews not yet applied, and how many from other sessions wait on the page."""
    from livery.review import queue

    page = f"http://127.0.0.1:{queue.PORT}/"
    mine = queue.mine(root)
    lines = []
    for g in mine:
        counts = f"{len(g['decisions'])} of {len(g['items'])} decided"
        if g["status"] == "stale":
            lines.append(f"review {g['id']}: its process died, {counts}; {TAKEN_OVER}")
        elif g["status"] == "failed":
            lines.append(
                f"review {g['id']}: its Apply failed ({g['result']['error']}), {counts}; {TAKEN_OVER}"
            )
        else:
            lines.append(f"review {g['id']}: {g['status']} at {page}, {counts}")
    own = {g["id"] for g in mine}
    others = sum(g["id"] not in own and g["status"] in ("waiting", "applying") for g in queue.groups())
    if others:
        many = f"{others} reviews from other checkouts" if others > 1 else "1 review from another checkout"
        lines.append(f"{many} waiting at {page}")
    return lines


def _changed(root: Path) -> list[str]:
    path = root / ".cache" / "build-all.json"
    if not path.exists():
        return []
    report = json.loads(path.read_text(encoding="utf-8"))
    changed = [
        f"{design} (by {', '.join(row.get('changed_by', [])) or '?'})"
        for design, row in report.items()
        if row.get("status") == "changed"
    ]
    if not changed:
        return []
    return [f"approved but changed in the build --all of {_ago(path.stat().st_mtime)}: {', '.join(changed)}"]


def _remember_session(event: dict) -> None:
    """Exports the session for the commands that follow, through the file Claude Code reads them from."""
    from livery import prefs

    env_file = os.environ.get("CLAUDE_ENV_FILE")
    if not env_file or not event.get("session_id"):
        return
    lines = [f"export {prefs.SESSION_ENV}={event['session_id']}"]
    if event.get("transcript_path"):
        lines.append(f"export {prefs.TRANSCRIPT_ENV}='{event['transcript_path']}'")
    with open(env_file, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _safe(label: str, part: Callable[[], list[str]]) -> list[str]:
    """One unreadable file, or a livery that doesn't import, costs its own line, not the whole status."""
    try:
        return part()
    except Exception:
        return [f"{label}: unreadable"]


def main() -> None:
    event = json.load(sys.stdin)
    with contextlib.suppress(OSError, ImportError):  # the status still prints
        _remember_session(event)
    cwd = Path(event.get("cwd") or ".")
    top, common = _git(
        cwd, "rev-parse", "--show-toplevel", "--path-format=absolute", "--git-common-dir"
    ).splitlines()
    root, main_root = Path(top), Path(common).parent
    status = _git(root, "status", "--porcelain", "--branch").splitlines()
    worktree = f", a worktree of {main_root.as_posix()}" if root != main_root else ""
    lines = [f"Session status: branch {status[0].removeprefix('## ')}{worktree}"]
    paths = [s[3:] for s in status[1:]]
    more = f" and {len(paths) - SHOW_PATHS} more" if len(paths) > SHOW_PATHS else ""
    lines.append(
        f"{len(paths)} uncommitted: {', '.join(paths[:SHOW_PATHS])}{more}" if paths else "clean tree"
    )
    lines += _safe("sim lock", _lock)
    lines += _safe("review queue", partial(_reviews, root))
    lines += _safe("build-all.json", partial(_changed, root))
    print("\n".join(lines))


if __name__ == "__main__":
    try:
        main()
    except Exception:  # a broken hook must never block work
        sys.exit(0)
