"""The taste table: what a person has cared about more than once, as checks. Builders check every live row
before a round; the critic passes or fails each in every round verdict, and a design ships only when all pass.

It lives in prefs/<person>/taste.json. A row rests on that person's words, each quote naming the decision it
came from (`of`, a ledger uid) where there is one; rows from the critic's recurring `lint_missed` say so in
`source`. Builders, the critic's pack and the retro read it as the markdown table `markdown` renders.
"""

import json
import re
from pathlib import Path

from livery import prefs
from livery.config import Workspace
from livery.errors import UsageError

FILE = "taste.json"
ROW_ID = re.compile(r"[a-z0-9][a-z0-9-]*")
MIN_WORDS = 2  # a row rests on at least this many of the person's notes that still stand


class BadRow(UsageError, ValueError):
    pass


def path(ws: Workspace) -> Path:
    return ws.prefs / FILE


def rows(ws: Workspace, retired: bool = False) -> list[dict]:
    p = path(ws)
    if not p.exists():
        return []
    out = json.loads(p.read_text(encoding="utf-8"))["rows"]
    return [r for r in out if retired or r.get("status", "live") == "live"]


def ids(ws: Workspace) -> list[str]:
    return [r["id"] for r in rows(ws)]


def _write(ws: Workspace, all_rows: list[dict]) -> Path:
    p = path(ws)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"rows": all_rows}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    return p


def check(row: dict) -> dict:
    """A row as `put` takes it: an id, the words it rests on, what to look for and where."""
    if not isinstance(row, dict) or not ROW_ID.fullmatch(str(row.get("id", ""))):
        raise BadRow("a row needs an id: lower case letters, digits and dashes")
    for key in ("look_for", "where"):
        if not isinstance(row.get(key), str) or not row[key].strip():
            raise BadRow(f"row {row['id']}: {key} is text")
    words = row.get("words")
    if (
        not isinstance(words, list)
        or not words
        or not all(isinstance(w, dict) and w.get("quote") for w in words)
    ):
        raise BadRow(f"row {row['id']}: words is a list of {{quote, of?, design?, at?}}")
    if row.get("source", "owner") not in ("owner", "critic"):
        raise BadRow(f"row {row['id']}: source is owner or critic")
    return row


def put(ws: Workspace, row: dict, by: dict | None = None) -> dict:
    """Adds a row, or replaces the one with its id, as live."""
    row = check(dict(row)) | {"status": "live"} | (by or prefs.agent_by())
    all_rows = rows(ws, retired=True)
    at = next((i for i, r in enumerate(all_rows) if r["id"] == row["id"]), None)
    if at is None:
        all_rows.append(row)
    else:
        all_rows[at] = row
    _write(ws, all_rows)
    return row


def retire(ws: Workspace, row_id: str, why: str, by: dict | None = None) -> dict:
    """A retired row stops gating designs; it comes back only by `put`, with new evidence."""
    all_rows = rows(ws, retired=True)
    row = next((r for r in all_rows if r["id"] == row_id), None)
    if row is None:
        raise BadRow(f"no taste row {row_id!r}")
    row["status"] = "retired"
    row["retired"] = {"at": prefs.now(), "why": why, **(by or prefs.agent_by())}
    _write(ws, all_rows)
    return row


def support(ws: Workspace, row: dict) -> list[dict]:
    """The words a row rests on that still count: owner quotes whose decision stands, or that name none."""
    standing = {prefs.uid(d) for d in prefs.decisions(ws)}
    return [w for w in row["words"] if not w.get("of") or w["of"] in standing]


def thin(ws: Workspace) -> list[dict]:
    """Live rows of the owner's words, written by an agent, resting on fewer than MIN_WORDS words that still
    count: undoing a note took one away. A row the owner kept as their own needs no support."""
    mine = prefs.owner_by()["by"]
    return [
        r
        for r in rows(ws)
        if r.get("source", "owner") == "owner" and r.get("by") != mine and len(support(ws, r)) < MIN_WORDS
    ]


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _words(row: dict) -> str:
    said = []
    for w in row["words"]:
        where = ", ".join(x for x in (w.get("at", "")[:10], w.get("design", "")) if x)
        said.append(f'"{w["quote"]}"' + (f" ({where})" if where else ""))
    text = "; ".join(said)
    return f"The critic's `lint_missed`: {text}" if row.get("source") == "critic" else text


def markdown(ws: Workspace) -> str:
    """The live rows as the table builders and the critic read."""
    out = [
        "# Taste table",
        "",
        "What the owner has cared about more than once, as checks. Builders check every row before a round; the "
        "critic passes or fails every row in every round verdict (`taste`), and a design ships only when all "
        "of them pass (`livery critic status`).",
        "",
        "| id | Their words | Look for | Where |",
        "|---|---|---|---|",
    ]
    out += [
        f"| `{r['id']}` | {_cell(_words(r))} | {_cell(r['look_for'])} | {_cell(r['where'])} |"
        for r in rows(ws)
    ]
    return "\n".join(out) + "\n"
