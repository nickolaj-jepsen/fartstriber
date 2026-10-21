"""Telemetry: one event per livery command, and a line per sim run and review decision, in the machine
state dir (never in git), for `livery stats`. Outcomes worth keeping are committed elsewhere: verdicts in
each design's verdicts.jsonl, decisions in prefs/decisions.jsonl, approvals in approved.json.

`command` wraps a CLI command: what ran, on which design, with which hashes when the command knew them, how
it ended and how long its stages took. `log` lines written while a command runs carry its run id, and their
durations become its stages. The history from before, `runs/metrics.jsonl` in a checkout, is copied in the
first time a checkout that still has it runs a command, and only once per line.
"""

import hashlib
import json
import os
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from livery import state
from livery.config import REPO

FILE = "telemetry.jsonl"
IMPORTED = "telemetry-imported.json"  # hashes of the legacy lines already copied in
LEGACY = REPO / "runs" / "metrics.jsonl"
SESSION = ("CLAUDE_SESSION_ID", "CLAUDE_CODE_SESSION_ID")  # the first set names the Claude session

_event: dict | None = None  # the command running in this process, if any


def path() -> Path:
    return state.root() / FILE


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _durations(d: dict[str, float | None]) -> dict[str, float | None]:
    return {k: None if v is None else round(v, 3) for k, v in d.items()}


def _import_legacy(dest: Path) -> None:
    """Copies the lines of runs/metrics.jsonl not copied before; the file itself is left alone."""
    legacy = LEGACY  # looked up when called, so tests can point it elsewhere
    if not legacy.exists():
        return
    marker = dest.with_name(IMPORTED)
    seen = set((state.read_json(marker) or {}).get("lines", []))
    new = []
    for line in legacy.read_text(encoding="utf-8").splitlines():
        key = hashlib.sha256(line.strip().encode()).hexdigest()[:16]
        if not line.strip() or key in seen:
            continue
        row = json.loads(line)
        if isinstance(row.get("shots"), str):
            row["shots"] = row["shots"].replace("\\", "/")
        new.append(json.dumps(row | {"imported": "runs/metrics.jsonl"}) + "\n")
        seen.add(key)
    if new:
        with open(dest, "a", encoding="utf-8", newline="\n") as f:
            f.writelines(new)
        state.write_json(marker, {"lines": sorted(seen)})


def _append(line: dict, dest: Path | None = None) -> None:
    """Never fails the command it describes."""
    dest = dest or path()
    try:
        _import_legacy(dest)
        with open(dest, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(line) + "\n")
    except (OSError, ValueError):
        pass


def log(
    design: str, loop: str, durations: dict[str, float | None], path: Path | None = None, **extra
) -> dict:
    """Append one line about a loop (a sim run, a review decision, an install)."""
    line = {
        "design": design,
        "timestamp": _now(),
        "loop": loop,
        "durations_s": _durations(durations),
        **extra,
    }
    if _event is not None:
        line["run"] = _event["run"]
        _event["durations_s"].update({k: v for k, v in line["durations_s"].items() if k != "total"})
    _append(line, path)
    return line


def note(**fields) -> None:
    """Adds what the running command learnt (its design and toolchain hashes, say) to its event."""
    if _event is not None:
        _event.update(fields)


@contextmanager
def stage(name: str) -> Iterator[None]:
    """Times one stage of the running command."""
    t = time.perf_counter()
    try:
        yield
    finally:
        if _event is not None:
            _event["durations_s"][name] = round(time.perf_counter() - t, 3)


def _outcome(e: BaseException) -> tuple[str, str | None]:
    """ok; failed (it ran and reported a failure through its exit code, like a check that didn't pass); or
    error, with the exception's class."""
    import typer

    if isinstance(e, KeyboardInterrupt):
        return "interrupted", None
    if isinstance(e, typer.Exit):
        return ("ok" if e.exit_code == 0 else "failed"), None
    if isinstance(e, SystemExit):
        return ("ok" if e.code in (0, None) else "failed"), None
    return "error", type(e).__name__


@contextmanager
def command(
    name: str, args: list[str], design: str | list[str] | None = None, kit: str | None = None
) -> Iterator[dict]:
    """One event for one CLI command, written when it ends however it ends."""
    global _event
    event: dict = {
        "event": "command",
        "timestamp": _now(),
        "command": name,
        "args": args,
        "design": design,
        "kit": kit,
        "session": next((os.environ[k] for k in SESSION if os.environ.get(k)), None),
        "run": uuid.uuid4().hex[:12],
        "checkout": REPO.as_posix(),
        "outcome": "ok",
        "error": None,
        "durations_s": {},
    }
    outer, _event = _event, event
    t = time.perf_counter()
    try:
        yield event
    except BaseException as e:
        event["outcome"], event["error"] = _outcome(e)
        raise
    finally:
        event["durations_s"]["total"] = round(time.perf_counter() - t, 3)
        _event = outer
        _append(event)


def read(dest: Path | None = None) -> list[dict]:
    """Every event and line, oldest first by time."""
    dest = dest or path()
    try:
        _import_legacy(dest)
        text = dest.read_text(encoding="utf-8") if dest.exists() else ""
    except (OSError, ValueError):
        return []
    rows = []
    for line in text.splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue  # a line cut short by a killed process
    return sorted(rows, key=lambda r: _when(r.get("timestamp", "")))


def _when(stamp: str) -> float:
    try:
        return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S%z").timestamp()
    except ValueError:
        return 0.0
