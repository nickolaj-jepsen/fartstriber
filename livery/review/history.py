"""The page's History: every decision in the owner's ledger, newest first, with what they saw, the taste rows
it fed and who made it; and their edits and undos of them.

History reads and writes the main checkout (the repository's first worktree), where decisions from every
branch arrive once merged, and commits what it writes there. Union merges keep concurrent appends to a ledger
from conflicting (.gitattributes).
"""

import subprocess
from pathlib import Path

from livery import prefs, taste
from livery.config import Workspace
from livery.errors import UsageError
from livery.review import library


def main_root(root: Path) -> Path:
    """The repository's main checkout, from any of its worktrees; `root` itself outside git."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        ).stdout
    except OSError:
        return root
    first = next(
        (line[len("worktree ") :] for line in out.splitlines() if line.startswith("worktree ")), None
    )
    return Path(first) if first else root


def _picture(ws: Workspace, d: dict) -> str | None:
    """One picture for a decision: front34 (or the first view) of what it showed, a brand's or marking's
    sheet."""
    from livery.review.items import _shown

    kind, _, key = d.get("id", "").partition(":")
    if kind in ("concept", "final"):
        views = _shown(ws, key, d)
        return views.get("front34") or next(iter(views.values()), None)
    sheet = {
        "sponsor": ws.cache / "sponsors" / key / "sheet.png",
        "mark": ws.cache / "marks" / key / "sheet.png",
    }
    p = sheet.get(kind)
    return p.relative_to(ws.root).as_posix() if p and p.exists() else None


def _rows_citing(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows:
        for w in r["words"]:
            if w.get("of"):
                out.setdefault(w["of"], []).append({"id": r["id"], "look_for": r["look_for"]})
    return out


def entries(ws: Workspace) -> list[dict]:
    """Every decision (undone ones too) and every taste row, newest first, as the page lists them."""
    rows = taste.rows(ws, retired=True)
    citing = _rows_citing([r for r in rows if r.get("status", "live") == "live"])
    out = []
    for d in prefs.decisions(ws, undone=True):
        u = prefs.uid(d)
        kind, _, key = d.get("id", "").partition(":")
        out.append(
            {
                "uid": u,
                "type": "decision",
                "at": d.get("at", ""),
                "by": d.get("by") or "owner",
                "kind": kind,
                "key": key,
                "title": d.get("title"),
                "decision": d.get("decision"),
                "note": d.get("note", ""),
                "first_note": d.get("first_note"),
                "favourite": bool(d.get("favourite")),
                "undone": bool(d.get("undone")),
                "trail": d.get("trail", []),
                "via": d.get("via"),
                "moved": d.get("moved"),
                "held": d.get("held"),
                "still": bool(d.get("still")),
                "picture": _picture(ws, d),
                "taste": citing.get(u, []),
            }
        )
    for r in rows:
        at = r.get("at") or min((w.get("at", "") for w in r["words"]), default="")
        out.append(
            {
                "uid": f"taste:{r['id']}",
                "type": "taste",
                "at": at,
                "by": r.get("by", "agent"),
                "transcript": r.get("transcript"),
                "kind": "taste",
                "key": r["id"],
                "look_for": r["look_for"],
                "words": r["words"],
                "status": r.get("status", "live"),
                "retired": r.get("retired"),
                "support": len(taste.support(ws, r)),
            }
        )
    return sorted(out, key=lambda e: str(e["at"]), reverse=True)


def change(ws: Workspace, of: str, kind: str, note: str | None = None) -> dict:
    """The owner's edit, undo or redo of a decision, committed; an undo puts back what the decision changed
    (an approval revoked or carried, a brand dropped, a taste row retired, a design revived) and a redo does
    it again. With the live taste rows it left resting on too few of their notes, for them to keep or
    retire."""
    before = library.dirty(ws)
    line = prefs.change(ws, of, kind, note)
    files: list[Path] = []
    folders: list[Path] = []
    if kind in ("undo", "redo"):
        d = prefs.find(ws, of)
        try:
            files, folders = (library.undo if kind == "undo" else library.redo)(ws, d)
        except Exception:
            prefs.change(ws, of, "redo" if kind == "undo" else "undo")  # the ledger says what stands
            raise
    committed = library.commit(ws, before, files, f"History: {kind} {of}", folders)
    thin = [r for r in taste.thin(ws) if any(w.get("of") == of for w in r["words"])]
    return {
        "line": line,
        "committed": committed,
        "thin": [{"id": r["id"], "look_for": r["look_for"]} for r in thin],
    }


def keep_or_retire(ws: Workspace, row_id: str, keep: bool, why: str = "") -> dict:
    """A thin taste row kept as the owner's own, or retired with their reason (a ledger line, undoable)."""
    if not keep:
        return library.retire(ws, row_id, why)
    row = next((r for r in taste.rows(ws) if r["id"] == row_id), None)
    if row is None:
        raise UsageError(f"no live taste row {row_id!r}")
    before = library.dirty(ws)
    out = taste.put(ws, row, by=prefs.owner_by())
    committed = library.commit(ws, before, [taste.path(ws)], f"History: keep taste row {row_id}")
    return {"row": out, "committed": committed}
