"""SessionStart: the shared state a session would otherwise find by accident, in a few lines.

The branch and uncommitted paths, who holds the sim lock and this checkout's reviews on the shared review page
(both machine-wide, in the state dir), and approved designs the last `livery build --all` found changed.
"""

import ctypes
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path

REVIEW_PORT = 8765
SHOW_PATHS = 4


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=5, check=True
    ).stdout


def _alive(pid: int) -> bool:
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    code = ctypes.c_ulong()
    kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    kernel32.CloseHandle(handle)
    return code.value == 259  # STILL_ACTIVE


def _ago(t: float) -> str:
    minutes = int((time.time() - t) / 60)
    return f"{minutes} min ago" if minutes < 120 else f"{minutes // 60} h ago"


def _state() -> Path:
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(os.environ.get("FARTSTRIBER_STATE") or Path(local) / "fartstriber")


def _lock() -> str | None:
    """livery.simlock's holder file; a dead holder's is stale, since the kernel freed its mutex."""
    path = _state() / "fartstriber-sim.json"
    if not path.exists():
        return None
    holder = json.loads(path.read_text(encoding="utf-8"))
    pid = int(holder["pid"])
    if not _alive(pid):
        return None
    where = f" in {holder['checkout']}" if holder.get("checkout") else ""
    return f"sim lock: held by pid {pid}{where} for {holder.get('what')}, taken {_ago(holder['since'])}"


def _reviews(root: Path) -> list[str]:
    """livery.review.queue's groups from this checkout not yet applied, and how many other sessions' wait."""
    here = os.path.normcase(str(root.resolve()))
    lines, others = [], 0
    queue = _state() / "review"
    for d in sorted(queue.iterdir()) if queue.exists() else []:
        if not (d / "group.json").exists() or (d / "result.json").exists():
            continue
        group = json.loads((d / "group.json").read_text(encoding="utf-8"))
        if os.path.normcase(group["root"]) != here:
            others += 1
            continue
        decided = json.loads((d / "decisions.json").read_text(encoding="utf-8")).get("decisions", {})
        counts = f"{len(decided)} of {len(group['items'])} decided"
        if _alive(int(group["owner"]["pid"])):
            lines.append(f"review {group['id']}: waiting at http://127.0.0.1:{REVIEW_PORT}/, {counts}")
        else:
            lines.append(
                f"review {group['id']}: its process died, {counts}; the next `livery review` here takes the "
                "decisions over, `livery review --cancel` drops them"
            )
    if others:
        many = f"{others} reviews from other checkouts" if others > 1 else "1 review from another checkout"
        lines.append(f"{many} waiting at http://127.0.0.1:{REVIEW_PORT}/")
    return lines


def _changed(root: Path) -> str | None:
    path = root / ".cache" / "build-all.json"
    if not path.exists():
        return None
    report = json.loads(path.read_text(encoding="utf-8"))
    changed = [
        f"{design} (by {', '.join(row.get('changed_by', [])) or '?'})"
        for design, row in report.items()
        if row.get("status") == "changed"
    ]
    if not changed:
        return None
    return f"approved but changed in the build --all of {_ago(path.stat().st_mtime)}: {', '.join(changed)}"


def _safe(label: str, part: Callable[[], str | None]) -> list[str]:
    """One unreadable file costs its own line, not the whole status."""
    try:
        line = part()
    except Exception:
        line = f"{label}: unreadable"
    return [line] if line else []


def main() -> None:
    event = json.load(sys.stdin)
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
    try:
        lines += _reviews(root)
    except Exception:
        lines.append("review queue: unreadable")
    lines += _safe("build-all.json", partial(_changed, root))
    print("\n".join(lines))


if __name__ == "__main__":
    try:
        main()
    except Exception:  # a broken hook must never block work
        sys.exit(0)
