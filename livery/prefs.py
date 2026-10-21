"""A person's records, under prefs/<person>/: decisions.jsonl (every review decision, and every later edit or
undo of one), log.md (their words, written by the agent), taste.json (what they've cared about more than once,
as checks) and retro.json.

Nothing in the ledger is rewritten. An edit or an undo is a line of its own that names the decision it
changes by uid; `decisions` folds them in. Every line says who wrote it: the person, or an agent and its
Claude session (the SessionStart hook puts the session's id and transcript in the environment).
"""

import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

from livery import config
from livery.config import Workspace
from livery.errors import UsageError

LEDGER = "decisions.jsonl"
SESSION_ENV, TRANSCRIPT_ENV = "FARTSTRIBER_SESSION", "FARTSTRIBER_TRANSCRIPT"
CHANGES = ("edit", "undo", "redo")  # the kinds of line that change an earlier decision


class NoSuchDecision(UsageError, LookupError):
    pass


def ledger_path(ws: Workspace) -> Path:
    return ws.prefs / LEDGER


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def owner_by() -> dict:
    """Who a decision made on the review page is by."""
    return {"by": config.owner()}


def agent_by() -> dict:
    """Who a record an agent writes is by: the agent, with its session when the hook gave one."""
    session = os.environ.get(SESSION_ENV)
    out: dict = {"by": f"agent:{session}" if session else "agent"}
    if transcript := os.environ.get(TRANSCRIPT_ENV):
        out["transcript"] = transcript
    return out


def new_uid() -> str:
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def uid(line: dict) -> str:
    """A decision's id. Lines from before uids are named by their review and item."""
    return line.get("uid") or f"{line.get('review')}/{line.get('id')}"


def lines(ws: Workspace) -> list[dict]:
    """Every line of the ledger, oldest first."""
    path = ledger_path(ws)
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def append(ws: Workspace, new: list[dict]) -> Path:
    path = ledger_path(ws)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.writelines(json.dumps(x) + "\n" for x in new)
    return path


def decisions(ws: Workspace, undone: bool = False) -> list[dict]:
    """The decisions as they stand, oldest first: each edit's note folded into its decision (the first note
    kept as `first_note`, the changes as `trail`); an undone one left out, or kept with `undone` set."""
    out: dict[str, dict] = {}
    for line in lines(ws):
        kind = line.get("kind")
        if kind not in CHANGES:
            out[uid(line)] = dict(line)
            continue
        d = out.get(line.get("of", ""))
        if d is None:
            continue
        d.setdefault("trail", []).append(line)
        if kind == "edit":
            d.setdefault("first_note", d.get("note", ""))
            d["note"] = line.get("note", "")
        else:
            d["undone"] = kind == "undo"
    return [d for d in out.values() if undone or not d.get("undone")]


def find(ws: Workspace, of: str) -> dict:
    d = next((x for x in decisions(ws, undone=True) if uid(x) == of), None)
    if d is None:
        raise NoSuchDecision(f"no decision {of!r} in {ledger_path(ws).relative_to(ws.root).as_posix()}")
    return d


def change(ws: Workspace, of: str, kind: str, note: str | None = None, by: dict | None = None) -> dict:
    """Appends an edit of a decision's note, an undo, or a redo of an undone one."""
    d = find(ws, of)
    if kind == "edit" and (note is None or d.get("undone")):
        raise UsageError("an edit needs a note, on a decision that stands")
    if (kind == "undo" and d.get("undone")) or (kind == "redo" and not d.get("undone")):
        raise UsageError(f"decision {of} is {'undone' if d.get('undone') else 'standing'} already")
    if kind not in CHANGES:
        raise UsageError(f"no such change {kind!r}: {', '.join(CHANGES)}")
    line = {"uid": new_uid(), "at": now(), "kind": kind, "of": of, **(by or owner_by())}
    if kind == "edit":
        line["note"] = note
    append(ws, [line])
    return line
