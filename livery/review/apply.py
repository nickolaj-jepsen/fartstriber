"""Review decisions written back to the repo: what review owns, then every decision in the owner's ledger
(prefs/<person>/decisions.jsonl, livery.prefs), committed with what it decided. The owner's taste goes to
their log.md, by the agent, in their words."""

import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PIL import Image

from livery import approvals, config, critic, hashing, metrics, prefs, sponsors, state
from livery import kit as kits
from livery import marks as race_marks
from livery.config import Workspace
from livery.errors import LiveryError
from livery.review import pins

DESIGNS = ("concept", "final")
SHOTS = (
    "review-shots"  # in the state dir: what each design decision showed, shrunk, so history outlives .cache
)
SHOT_PX = 1400
EXTRAS = ("favourite", "strategy", "endorsed", "revived")  # what a decision may carry besides its note


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


def _final(w: _Writes, key: str, d: dict, item: dict | None) -> dict | None:
    """Accepting pins the build shown; stopping takes back an approval."""
    from livery.review import library

    _design(w, key, d, item)
    choice = d.get("decision")
    if choice == "stop" and approvals.load_approval(w.ws, key):
        was = library.unpin(w.ws, key)
        w.written.append(f"designs/{key}/approved.json: revoked")
        return {"was": was}
    if choice == "accept" and item and item.get("pin"):
        w.written.append(approvals.pin(w.ws, key, item["pin"], d.get("note", "")))
    return None


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


def _drop(w: _Writes, kind: str, key: str) -> dict:
    """A no to a brand or a marking: unapproved, and a brand's roster row turned down; its folder stays for
    a redraw."""
    from livery.review import library

    was, touched = library.drop_brand(w.ws, kind, key)
    # As folders, not files: a brand just drawn goes along with its review, committed or not.
    w.folders += [p.parent if p.name == "brand.toml" else p for p in touched]
    w.written.append(f"{kind} {key}: dropped")
    return {"was": was}


def _sponsor(w: _Writes, key: str, d: dict, item: dict | None) -> dict | None:
    """Accepting approves every variant and makes its roster row a yes, since the owner judges the row with
    its drawing."""
    choice, root = d.get("decision"), w.ws.sponsors
    if item is None:
        return None
    if choice == "reject":
        return _drop(w, "sponsor", key)
    if choice == "accept":
        w.written.append(f"{key}: approved {', '.join(sponsors.approve(root / key)) or 'nothing new'}")
        if any(r["brand"] == key and r["status"] != "yes" for r in sponsors.roster_rows(root / "roster.md")):
            _mark_table(root / "roster.md", {key: "yes"})
        w.folders += [root / key, root / "roster.md"]
    return None


def _mark(w: _Writes, key: str, d: dict, item: dict | None) -> dict | None:
    choice = d.get("decision")
    if choice == "reject" and item is not None:
        return _drop(w, "mark", key)
    if choice == "accept":
        w.written.append(race_marks.approve(key, w.ws.marks))
        if w.ws.marks not in w.folders:
            w.folders.append(w.ws.marks)
    return None


def _roster(w: _Writes, key: str, d: dict, item: dict | None) -> None:
    w.mark(w.ws.sponsors / "roster.md", key, d.get("decision"))


def _reference(w: _Writes, key: str, d: dict, item: dict | None) -> None:
    w.mark(w.ws.references, str(int(key)), d.get("decision"))


def _proposal(w: _Writes, key: str, d: dict, item: dict | None) -> None:
    """Nothing: the accepted ones go back to the agent (`proposals`)."""


def _no_fields(ws: Workspace, key: str, item: dict, uid: str) -> dict:
    return {}


def _design_fields(ws: Workspace, key: str, item: dict, uid: str) -> dict:
    """The sim runs (or the baked run) its item showed, a kept copy of its shots, and what was decided on."""
    out: dict = {"runs": item.get("runs", [])}
    if baked := item.get("baked_run"):
        out["baked_run"] = baked
    if kept := _keep_shots(ws, item, uid):
        out["shots"] = kept
    return out | _design_record(ws, key, item)


def _proposal_fields(ws: Workspace, key: str, item: dict, uid: str) -> dict:
    return {"title": item.get("title")}


@dataclass(frozen=True)
class Kind:
    """What Apply does with a decision on one kind of review item (the id's prefix)."""

    # Writes what review owns; returns what the decision replaced, for History's undo.
    apply: Callable[[_Writes, str, dict, dict | None], dict | None]
    flag: str | None = None  # the `livery review` option that queues it; designs are named instead
    first: bool = False  # applied before every other kind
    ledger: Callable[[Workspace, str, dict, str], dict] = _no_fields  # the fields its ledger lines add
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
        for k in EXTRAS:
            if d.get(k):
                extra[k] = d[k]
        if d.get("pins"):
            extra["pins"] = len(d["pins"])
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
    """Every decision in the owner's ledger as it stands, oldest first: edits folded in, undone ones out."""
    return prefs.decisions(ws)


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
    """The ledger line with the owner's latest note on a design, their pins, or critic suggestions they
    endorsed, which every round after it answers to; None once they have accepted the final since."""
    for line in reversed(decisions_on(ws, name)):
        if line["id"].startswith("final:") and line.get("decision") == "accept":
            return None
        if line.get("note", "").strip() or line.get("endorsed") or line.get("pins"):
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


def _keep_shots(ws: Workspace, item: dict, uid: str) -> str | None:
    """A shrunk copy of each view the item showed, under the state dir; the folder, relative to it."""
    views = {v: ws.root / rel for v, rel in (item.get("views") or {}).items() if (ws.root / rel).is_file()}
    if not views:
        return None
    out = state.root() / SHOTS / uid
    out.mkdir(parents=True, exist_ok=True)
    for view, src in views.items():
        img = Image.open(src).convert("RGB")
        img.thumbnail((SHOT_PX, SHOT_PX), Image.Resampling.LANCZOS)
        img.save(out / f"{view}.jpg", quality=82)
    return f"{SHOTS}/{uid}"


def _record(
    ws: Workspace, by_id: dict[str, dict], decisions: dict[str, dict], review: str, effects: dict[str, dict]
) -> str:
    """Appends every decision to the ledger, by the owner, their note verbatim, what it replaced
    (`effects`), its pins, and its kind's own fields (`Kind.ledger`)."""
    lines = []
    for item_id, d in decisions.items():
        kind, _, key = item_id.partition(":")
        at = datetime.fromtimestamp(d.get("at") or time.time()).astimezone()
        uid = prefs.new_uid()
        line = {
            "uid": uid,
            "at": at.isoformat(timespec="seconds"),
            "review": review,
            "id": item_id,
            "decision": d.get("decision"),
            "note": d.get("note", ""),
            **prefs.owner_by(),
        }
        item = by_id.get(item_id, {})
        line |= {k: d[k] for k in EXTRAS if d.get(k)}
        line |= effects.get(item_id, {})
        line |= {k: True for k in ("still", "again") if item.get(k)}
        if d.get("pins"):
            line["pins"] = pins.resolve(ws, item, d["pins"], state.root() / SHOTS / uid, f"{SHOTS}/{uid}")
        line |= KINDS[kind].ledger(ws, key, item, uid)
        lines.append(line)
    path = prefs.append(ws, lines)
    return f"{path.relative_to(ws.root).as_posix()}: {len(lines)} decisions"


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
        if d.get("favourite"):
            row += ", a favourite"
        if d.get("strategy"):
            row += f", as a {d['strategy']} round"
        said = [*d.get("note", "").strip().splitlines()]
        said += [f"pin on {p['view']}: {p['note'].strip()}" for p in d.get("pins") or []]
        rows.append(row + "".join(f"\n    {n}" for n in said))
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


def _commit(
    root: Path, before: set[str], files: list[Path], folders: list[Path], message: str, ledger: Path
) -> str | None:
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
    ledger_rel = ledger.resolve().relative_to(root.resolve()).as_posix()
    stale = sorted(
        f
        for f in rel[: len(files)]
        if f in before and f != ledger_rel and not any(f.startswith(d + "/") for d in inside)
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
    w = _Writes(ws, files=[prefs.ledger_path(ws)])
    by_id = {i["id"]: i for i in items}
    effects: dict[str, dict] = {}  # what a decision replaced, for History's undo
    for first in (True, False):
        for item_id, d in decisions.items():
            kind, _, key = item_id.partition(":")
            if KINDS[kind].first == first and (was := KINDS[kind].apply(w, key, d, by_id.get(item_id))):
                effects[item_id] = was
    for table, marks in w.tables.items():
        _mark_table(table, marks)
        w.written.append(f"{table.name}: {len(marks)} marked")
        w.files.append(table)
    written = w.written
    if decisions:
        written.append(_record(ws, by_id, decisions, review, effects))
    _log(ws, items, decisions, written)
    if before is not None and decisions:
        done = _commit(
            ws.root, before, w.files, w.folders, _message(decisions, review), prefs.ledger_path(ws)
        )
        if done:
            written.append(done)
    return written
