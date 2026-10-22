"""Review decisions written back to the repo: what review owns, then every decision in the
prefs/decisions.jsonl ledger, committed with what it decided. The owner's taste goes to prefs/log.md, by
the agent, in their words."""

import json
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from livery import approvals, config, critic, hashing, metrics, sponsors
from livery import kit as kits
from livery import marks as race_marks
from livery.config import Workspace
from livery.errors import LiveryError
from livery.review.items import RATINGS

LEDGER = Path("prefs") / "decisions.jsonl"  # under the workspace root
DESIGNS = ("concept", "final")


@dataclass
class _Writes:
    """What Apply has written, the files and folders it commits, and the table marks it writes last."""

    ws: Workspace
    written: list[str] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)
    folders: list[Path] = field(default_factory=list)
    tables: dict[Path, dict[str, str]] = field(default_factory=dict)

    def mark(self, table: Path, key: str, choice: str | None) -> None:
        """An accept or reject on a table row: its yes/no column."""
        if choice in ("accept", "reject"):
            self.tables.setdefault(table, {})[key] = "yes" if choice == "accept" else "no"


def _mark_table(path: Path, marks: dict[str, str]) -> None:
    """Sets the last cell of the table rows whose first cell is a key of `marks`."""
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(\|\s*([^|]+?)\s*\|.+\|)\s*(yes|no|)\s*\|\s*$", line)
        if m and m[2] in marks:
            lines[i] = f"{m[1]} {marks[m[2]]} |"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


# Each handler writes what review owns for one decision; `item` is None for a decision on an item not queued.


def _design(w: _Writes, key: str, d: dict, item: dict | None) -> None:
    if d.get("decision") != "skip":
        w.folders.append(w.ws.design_dir(key))


def _final(w: _Writes, key: str, d: dict, item: dict | None) -> None:
    """Accepting pins the build shown."""
    _design(w, key, d, item)
    if d.get("decision") == "accept" and item and item.get("pin"):
        w.written.append(approvals.pin(w.ws, key, item["pin"], d.get("note", "")))


def _kit(w: _Writes, key: str, d: dict, item: dict | None) -> None:
    """Approves the kit as it was shown; one that changed while under review goes back to review."""
    if d.get("decision") != "accept":
        return
    shown = (item or {}).get("kit_hash")
    if shown is not None and hashing.kit_hash(w.ws.kits / key) != shown:
        w.written.append(
            f"kits/{key}: not approved, the kit changed while it was under review; review it again"
        )
    else:
        path = kits.approve(w.ws.kits / key, config.owner(), d.get("note", ""))
        w.written.append(f"{path.relative_to(w.ws.root).as_posix()}: approved")
    w.files.append(w.ws.kits / key / kits.APPROVAL)


def _sponsor(w: _Writes, key: str, d: dict, item: dict | None) -> None:
    """Accepting approves every variant; rejecting drops the brand and marks its roster row no."""
    choice, root = d.get("decision"), w.ws.sponsors
    if choice not in ("accept", "reject") or item is None:
        return
    if choice == "accept":
        w.written.append(f"{key}: approved {', '.join(sponsors.approve(root / key)) or 'nothing new'}")
    else:
        shutil.rmtree(root / key)
        _mark_table(root / "roster.md", {key: "no"})
        w.written.append(f"{key}: dropped")
    w.folders.append(root / key)
    if choice == "reject":
        w.files.append(root / "roster.md")


def _mark(w: _Writes, key: str, d: dict, item: dict | None) -> None:
    done = race_marks.apply_decision(key, d.get("decision"), w.ws.marks)
    if done:
        w.written.append(done)
        if w.ws.marks not in w.folders:
            w.folders.append(w.ws.marks)


def _roster(w: _Writes, key: str, d: dict, item: dict | None) -> None:
    w.mark(w.ws.sponsors / "roster.md", key, d.get("decision"))


def _reference(w: _Writes, key: str, d: dict, item: dict | None) -> None:
    w.mark(w.ws.references, str(int(key)), d.get("decision"))


def _proposal(w: _Writes, key: str, d: dict, item: dict | None) -> None:
    """Nothing: the accepted ones go back to the agent (`proposals`)."""


def _no_fields(ws: Workspace, key: str, item: dict) -> dict:
    return {}


def _design_fields(ws: Workspace, key: str, item: dict) -> dict:
    return {"runs": item.get("runs", []), **_design_record(ws, key, item)}


def _proposal_fields(ws: Workspace, key: str, item: dict) -> dict:
    return {"title": item.get("title")}


@dataclass(frozen=True)
class Kind:
    """What Apply does with a decision on one kind of review item (the id's prefix)."""

    apply: Callable[[_Writes, str, dict, dict | None], None]
    flag: str | None = None  # the `livery review` option that queues it; designs are named instead
    first: bool = False  # applied before every other kind
    ledger: Callable[[Workspace, str, dict], dict] = _no_fields  # the fields its ledger lines add
    metrics: bool = True  # a metrics line per decision


KINDS = {
    "concept": Kind(_design, ledger=_design_fields),
    "final": Kind(_final, ledger=_design_fields),
    "kit": Kind(_kit, "--kit"),
    "roster": Kind(_roster, "--roster"),
    # Brands and race markings first, so a final placing one accepted in this review can be pinned.
    "sponsor": Kind(_sponsor, "--sponsors", first=True),
    "mark": Kind(_mark, "--marks", first=True),
    "reference": Kind(_reference, "--references"),
    "proposal": Kind(_proposal, "--proposals", ledger=_proposal_fields, metrics=False),
}


def _log(ws: Workspace, items: list[dict], decisions: dict[str, dict], written: list[str]) -> None:
    """One metrics line per decision, with the critic's last word on a design beside the owner's, so
    critic agreement is measured on every review, not only when someone remembers to log it."""
    concepts = [k for kind, _, k in (i["id"].partition(":") for i in items) if kind == "concept"]
    ranking = critic.latest_ranking(ws, concepts) if concepts else None
    for item_id, d in decisions.items():
        kind, _, key = item_id.partition(":")
        if not KINDS[kind].metrics:
            continue
        extra: dict = {"item": kind, "decision": d.get("decision"), "note": bool(d.get("note"))}
        if d.get("mix_of"):
            extra["mix_of"] = d["mix_of"]
        if d.get("rating"):
            extra["rating"] = d["rating"]
        if kind in DESIGNS:
            rounds = critic.history(ws, key)
            if rounds:
                extra["critic"] = {"overall": rounds[-1]["overall"], "verdict": rounds[-1]["verdict"]}
            if kind == "concept" and ranking:
                extra["critic_rank"] = ranking["ranking"].index(key) + 1
            if kind == "final":
                extra["pinned"] = any(w.startswith(f"designs/{key}/approved.json: pinned") for w in written)
        metrics.log(key, "review", {}, **extra)


def ledger(ws: Workspace) -> list[dict]:
    """Every decision in prefs/decisions.jsonl, oldest first."""
    path = ws.root / LEDGER
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def decisions_on(ws: Workspace, name: str) -> list[dict]:
    """The ledger's decisions on a design, as a concept or a final, oldest first. A skip without a note
    decided nothing."""
    ids = (f"concept:{name}", f"final:{name}")
    return [
        line
        for line in ledger(ws)
        if line.get("id") in ids and (line.get("decision") != "skip" or line.get("note", "").strip())
    ]


def latest_note(ws: Workspace, name: str) -> dict | None:
    """The ledger line with the owner's latest note on a design, which every round after it answers to;
    None once they have accepted the final since."""
    for line in reversed(decisions_on(ws, name)):
        if line["id"].startswith("final:") and line.get("decision") == "accept":
            return None
        if line.get("note", "").strip():
            return line
    return None


def _design_record(ws: Workspace, name: str, item: dict) -> dict:
    """The design hash the owner decided on (a final's is the build shown) and the critic's latest verdict
    on that same hash: a verdict on an earlier version would set the decision beside a different car."""
    h = item.get("pin", {}).get("design_hash")
    if h is None:
        try:
            h = hashing.design_hash(ws, name)
        except LiveryError:
            return {"design_hash": None}
    out: dict = {"design_hash": h}
    same = [r for r in critic.history(ws, name) if r.get("design_hash") == h]
    if same:
        out["critic"] = {k: same[-1][k] for k in ("overall", "verdict", "round", "at") if k in same[-1]}
    return out


def _record(ws: Workspace, by_id: dict[str, dict], decisions: dict[str, dict], review: str) -> str:
    """Appends every decision to the ledger, the owner's note verbatim, and for a design the sim runs its
    item showed."""
    lines = []
    for item_id, d in decisions.items():
        kind, _, key = item_id.partition(":")
        at = datetime.fromtimestamp(d.get("at") or time.time()).astimezone()
        line = {
            "at": at.isoformat(timespec="seconds"),
            "review": review,
            "id": item_id,
            "decision": d.get("decision"),
            "note": d.get("note", ""),
            "mix_of": d.get("mix_of", []),
            "rating": d.get("rating"),
        }
        line |= KINDS[kind].ledger(ws, key, by_id.get(item_id, {}))
        lines.append(json.dumps(line) + "\n")
    path = ws.root / LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.writelines(lines)
    return f"{LEDGER.as_posix()}: {len(lines)} decisions"


def proposals(items: list[dict], decisions: dict[str, dict]) -> list[dict]:
    """The proposals the owner said yes to, for the agent to make: accepted as written, or edited as the
    note says."""
    return [
        {"id": i["id"], "title": i["title"], "body": i["body"], **decisions[i["id"]]}
        for i in items
        if i["kind"] == "proposal" and decisions.get(i["id"], {}).get("decision") in ("accept", "edit")
    ]


def _message(decisions: dict[str, dict], review: str) -> str:
    """A subject naming the decisions, then each one with its rating and the owner's note verbatim."""
    subject = "Review: " + ", ".join(f"{d.get('decision')} {i}" for i, d in decisions.items())
    if len(subject) > 72:
        subject = f"Review {review}: {len(decisions)} decisions"
    rows = []
    for item_id, d in decisions.items():
        row = f"{item_id}: {d.get('decision')}"
        if d.get("rating"):
            row += f", rated {d['rating']} ({RATINGS[d['rating']]})"
        if d.get("mix_of"):
            row += f", mixed with {', '.join(d['mix_of'])}"
        rows.append(row + "".join(f"\n    {n}" for n in d.get("note", "").strip().splitlines()))
    return subject + "\n\n" + "\n".join(rows) + "\n"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _dirty(root: Path) -> set[str] | None:
    """Repo-relative paths with uncommitted changes, untracked ones included; None unless `root` is the top
    of a git checkout."""
    try:
        top = _git(root, "rev-parse", "--show-toplevel")
    except OSError:  # no git
        return None
    if top.returncode or Path(top.stdout.strip()).resolve() != root.resolve():
        return None
    paths = set()
    entries = iter(_git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout.split("\0"))
    for e in entries:
        if len(e) > 3:
            paths.add(e[3:])
            if e[0] in "RC":
                paths.add(next(entries, ""))  # a rename's source
    return paths


def _last_line(p: subprocess.CompletedProcess[str]) -> str:
    lines = [line for line in (p.stderr + p.stdout).splitlines() if line.strip()]
    return lines[-1] if lines else f"git exited {p.returncode}"


def _commit(root: Path, before: set[str], files: list[Path], folders: list[Path], message: str) -> str | None:
    """Commits exactly the files Apply wrote and the decided folders, nothing else that is staged, and never
    pushes. Refuses when a file it wrote already had changes Apply didn't make: those weren't the owner's
    decision. Only Apply writes the ledger, so uncommitted lines there are earlier decisions and go along."""
    rel = []
    for p in (*files, *folders):
        try:
            rel.append(p.resolve().relative_to(root.resolve()).as_posix())
        except ValueError:
            return f"not committed: {p} is outside the repo"
    inside = rel[len(files) :]
    stale = sorted(
        f
        for f in rel[: len(files)]
        if f in before and f != LEDGER.as_posix() and not any(f.startswith(d + "/") for d in inside)
    )
    if stale:
        return f"not committed: {', '.join(stale)} had changes Apply didn't make; commit them with yours"
    paths = [p for p in dict.fromkeys(rel) if (root / p).exists() or _git(root, "ls-files", "--", p).stdout]
    added = _git(root, "add", "--all", "--", *paths)
    if added.returncode:
        return f"not committed: {_last_line(added)}"
    if _git(root, "diff", "--cached", "--quiet", "--", *paths).returncode == 0:
        return None  # nothing new
    done = _git(root, "commit", "--only", "--quiet", "-m", message, "--", *paths)
    if done.returncode:
        return f"not committed: {_last_line(done)}"
    sha = _git(root, "rev-parse", "--short", "HEAD").stdout.strip()
    return f"committed {sha}: {message.splitlines()[0]}"


def apply(
    ws: Workspace,
    items: list[dict],
    decisions: dict[str, dict],
    review: str | None = None,
    commit: bool = False,
) -> list[str]:
    """Writes what review owns, each decision by its kind's handler (`KINDS`), then appends every decision
    to the ledger and logs it; with `commit` (the CLI's review, never a test's) it commits what it wrote and
    each decided design's folder. A decision on a kind with no entry fails before anything is written."""
    unknown = sorted(i for i in decisions if i.partition(":")[0] not in KINDS)
    if unknown:
        raise LiveryError(f"Apply has no handler for {', '.join(unknown)}: kinds are {', '.join(KINDS)}")
    before = _dirty(ws.root) if commit else None
    review = review or time.strftime("%Y%m%d-%H%M%S")
    w = _Writes(ws, files=[ws.root / LEDGER])
    by_id = {i["id"]: i for i in items}
    for first in (True, False):
        for item_id, d in decisions.items():
            kind, _, key = item_id.partition(":")
            if KINDS[kind].first == first:
                KINDS[kind].apply(w, key, d, by_id.get(item_id))
    for table, marks in w.tables.items():
        _mark_table(table, marks)
        w.written.append(f"{table.name}: {len(marks)} marked")
        w.files.append(table)
    written = w.written
    if decisions:
        written.append(_record(ws, by_id, decisions, review))
    _log(ws, items, decisions, written)
    if before is not None and decisions:
        done = _commit(ws.root, before, w.files, w.folders, _message(decisions, review))
        if done:
            written.append(done)
    return written
