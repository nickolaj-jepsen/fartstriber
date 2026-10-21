"""The Library: where everything the owner has decided stands now (cars, brands, race markings, kits and
taste rows), and what they change there: review again, revoke a car, drop a brand or a marking, retire a
taste row, revive a car or a concept.

Each change is a ledger line by the owner carrying what it replaced (`was`), so History's Undo puts it back
and Redo does it again (`undo`, `redo`). The page works on the main checkout, as History does, and commits
there. `build --all` carries approvals through library and kit changes that moved no pixel (`settle`).
"""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from livery import approvals, config, prefs, sponsors, stamps, state, taste
from livery import kit as kits
from livery import marks as race_marks
from livery.config import Workspace
from livery.designs import kit_name, role, script
from livery.errors import LiveryError, UsageError

SECTIONS = ("cars", "brands", "marks", "kits", "taste")
DESIGNS = ("concept", "final")
VIA = "library"  # what a ledger line the Library wrote says it came from


class NotHere(UsageError, LookupError):
    pass


# --- where things stand -----------------------------------------------------------------------------------


def _lines_by_id(ws: Workspace) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for d in prefs.decisions(ws):
        out.setdefault(d.get("id", ""), []).append(d)
    return out


def _last(lines: list[dict]) -> dict | None:
    if not lines:
        return None
    d = lines[-1]
    return {k: d.get(k) for k in ("at", "decision", "note", "by", "uid")} | {
        "kind": d["id"].partition(":")[0]
    }


_digests: dict[tuple, str] = {}


def _digest(path: Path) -> str | None:
    """A file's sha256, cached by its size and modification time: paint TGAs are megabytes."""
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_size, st.st_mtime_ns)
    if key not in _digests:
        _digests[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    return _digests[key]


def installed(ws: Workspace, names: list[str]) -> set[str]:
    """The designs whose approved paint is the one on the owner's car now (their customer paint name)."""
    try:
        cfg = config.load()
    except LiveryError:
        return set()
    out = set()
    for name in names:
        a = approvals.load_approval(ws, name)
        if a is None:
            continue
        try:
            folder = kits.read_meta(ws.kits / kit_name(script(ws, name)))["paint_folder"]
        except (LiveryError, OSError, KeyError):
            continue
        tga = cfg.paint_root / folder / f"car_{cfg.customer_id}.tga"
        if _digest(tga) == a["digests"].get("car.tga"):
            out.add(name)
    return out


def _gone_at(ws: Workspace, name: str) -> str | None:
    """The commit that deleted a design's folder, so a revive can bring it back; None if git never had it."""
    from livery.review.apply import _git

    try:
        out = _git(ws.root, "rev-list", "-n", "1", "HEAD", "--", f"designs/{name}/design.py")
    except OSError:
        return None
    return (out.stdout.strip() or None) if out.returncode == 0 else None


def _picture(ws: Workspace, name: str, lines: list[dict], approval: dict | None) -> str | None:
    """front34 (or the first view) of the last review that showed the car, else the approved build's flat
    car, else the design's flat car; a `state:` path or one inside the checkout."""
    from livery.review.items import _shown

    for d in reversed(lines):
        views = _shown(ws, name, d)
        if views:
            return views.get("front34") or next(iter(views.values()))
    candidates = []
    if approval:
        candidates.append(ws.cache / "approved" / name / approval["digests"]["car.tga"][:12] / "car.png")
    candidates.append(stamps.flat_dir(ws, name) / "car.png")
    for p in candidates:
        if p.exists():
            return p.relative_to(ws.root).as_posix()
    return None


def _car_state(d: dict | None) -> str:
    if d is None:
        return "new"
    kind, decision = d["kind"], d["decision"]
    if decision == "revive":
        return "revived"
    if kind == "concept" and decision == "reject":
        return "not picked"
    if kind == "final" and decision == "park":
        return "parked"
    if kind == "final" and decision in ("stop", "revoke"):
        return "stopped"
    return "in work"


def _cars(ws: Workspace, by_id: dict[str, list[dict]]) -> list[dict]:
    names = {p.parent.name for p in ws.designs.glob("*/design.py") if role(p) == "livery"}
    names |= {k.partition(":")[2] for k in by_id if k.partition(":")[0] in DESIGNS}
    names -= {p.parent.name for p in ws.designs.glob("*/design.py") if role(p) != "livery"}
    on_car = installed(ws, sorted(names))
    cards = []
    for name in sorted(names):
        lines = sorted(
            by_id.get(f"concept:{name}", []) + by_id.get(f"final:{name}", []), key=lambda d: d["at"]
        )
        mine = [d for d in lines if not str(d.get("by", "")).startswith("agent")]
        last = _last(mine)
        approval = approvals.load_approval(ws, name) if (ws.design_dir(name) / "design.py").exists() else None
        here = (ws.design_dir(name) / "design.py").exists()
        held = approvals.unapproved(ws, approval) if approval else []
        if approval:
            st = "on your car" if name in on_car else "held" if held else "approved"
        else:
            st = _car_state(last)
        if st in ("on your car", "approved", "held"):
            actions = ["review", "revoke"]
        elif st in ("parked", "stopped", "not picked"):
            actions = ["revive"] if here or _gone_at(ws, name) else []
        else:
            actions = ["review"] if here else []
        cards.append(
            {
                "id": f"final:{name}",
                "key": name,
                "title": name,
                "state": st,
                "last": last,
                "approved": approval and {k: approval.get(k) for k in ("date", "by", "note")},
                "held": held,
                "here": here,
                "picture": _picture(ws, name, lines, approval),
                "actions": actions,
            }
        )
    return cards


def _placing(ws: Workspace, field: str, key: str) -> list[str]:
    """The approved designs whose approved build places this roster brand or marking."""
    out = []
    for p in sorted(ws.designs.glob("*/approved.json")):
        a = json.loads(p.read_text(encoding="utf-8"))
        if any(x.partition("/")[0] == key for x in a.get(field, [])):
            out.append(p.parent.name)
    return out


def _brand_cards(ws: Workspace, kind: str, by_id: dict[str, list[dict]]) -> list[dict]:
    root, field = (ws.sponsors, "sponsors") if kind == "sponsor" else (ws.marks, "marks")
    rows = {r["brand"]: r for r in sponsors.roster_rows(root / "roster.md")} if kind == "sponsor" else {}
    keys = sorted({p.parent.name for p in root.glob("*/brand.toml")} | set(rows))
    on_car = set()
    cards = []
    for key in keys:
        drawn = (root / key / "brand.toml").exists()
        dates = sponsors.approvals(root / key) if drawn else {}
        last = _last(by_id.get(f"{kind}:{key}", []))
        status = rows.get(key, {}).get("status", "")
        if status == "no" or (not all(dates.values()) and last and last["decision"] in ("drop", "reject")):
            st = "dropped"
        elif not drawn:
            st = "to draw"
        elif dates and all(dates.values()):
            st = "approved"
        else:
            st = "to review"
        used = _placing(ws, field, key)
        on_car |= set(used)
        cards.append(
            {
                "id": f"{kind}:{key}",
                "key": key,
                "title": rows[key]["name"] if key in rows else key,
                "about": rows[key]["category"] if key in rows else "",
                "state": st,
                "last": last,
                "approved": max((d for d in dates.values() if d), default=None),
                "used_by": used,
                "picture": f"sheet:{kind}:{key}" if drawn else None,
                "actions": (["review", "drop"] if st == "approved" else ["review"]) if drawn else [],
            }
        )
    mine = installed(ws, sorted(on_car))
    for c in cards:
        c["on_car"] = [n for n in _placing(ws, field, str(c["key"])) if n in mine]
    return cards


def _kits(ws: Workspace, by_id: dict[str, list[dict]]) -> list[dict]:
    cards = []
    for d in sorted(p.parent for p in ws.kits.glob("*/kit.yaml")):
        record = kits.approval(d)
        stored = d / kits.APPROVAL
        st = "approved" if record else "changed" if stored.exists() else "to review"
        review = ws.cache / "kit-review" / d.name
        pics = [review / "calibration_review.png", review / "overlay.png", d / "overlays" / "islands.png"]
        pic = next((p for p in pics if p.exists()), None)
        cards.append(
            {
                "id": f"kit:{d.name}",
                "key": d.name,
                "title": d.name,
                "state": st,
                "last": _last(by_id.get(f"kit:{d.name}", [])),
                "approved": record and record.get("date"),
                "picture": pic.relative_to(ws.root).as_posix() if pic else None,
                "actions": ["review"],
            }
        )
    return cards


def _taste(ws: Workspace) -> list[dict]:
    return [
        {
            "id": f"taste:{r['id']}",
            "key": r["id"],
            "title": r["id"],
            "state": "live" if r.get("status", "live") == "live" else "retired",
            "look_for": r["look_for"],
            "where": r.get("where", ""),
            "support": len(taste.support(ws, r)),
            "words": len(r["words"]),
            "by": r.get("by", "agent"),
            "retired": r.get("retired"),
            "last": {
                "at": (r.get("retired") or {}).get("at")
                or max((w.get("at", "") for w in r["words"]), default="")
            },
            "actions": ["retire"] if r.get("status", "live") == "live" else [],
        }
        for r in taste.rows(ws, retired=True)
    ]


ORDER = ["on your car", "approved", "held", "live", "in work", "revived", "to review", "to draw", "changed"]


def sections(ws: Workspace) -> dict[str, list[dict]]:
    """Every card, by section, most alive first and then the latest decided."""
    by_id = _lines_by_id(ws)
    out = {
        "cars": _cars(ws, by_id),
        "brands": _brand_cards(ws, "sponsor", by_id),
        "marks": _brand_cards(ws, "mark", by_id),
        "kits": _kits(ws, by_id),
        "taste": _taste(ws),
    }
    rank = {s: i for i, s in enumerate(ORDER)}
    for cards in out.values():
        cards.sort(key=lambda c: str((c.get("last") or {}).get("at") or ""), reverse=True)
        cards.sort(key=lambda c: rank.get(c["state"], len(ORDER)))
    return out


def sheet(ws: Workspace, kind: str, key: str) -> Path:
    """A brand's or marking's review sheet in the checkout's .cache, drawn again when its brand.toml is newer."""
    from livery.lint.run import thresholds

    root = ws.sponsors if kind == "sponsor" else ws.marks
    if kind not in ("sponsor", "mark") or not (root / key / "brand.toml").exists():
        raise NotHere(f"no {kind} {key!r}")
    out = ws.cache / ("sponsors" if kind == "sponsor" else "marks") / key / "sheet.png"
    if not out.exists() or out.stat().st_mtime < (root / key / "brand.toml").stat().st_mtime:
        brand = sponsors.load(key, root) if kind == "sponsor" else race_marks.load(key, root)
        sponsors.sheet(brand, out, thresholds(ws.policy_file).min_stroke_px, ws.fonts)
    return out


# --- changes: each a ledger line by the owner, undone by History ----------------------------------------


def _line(item_id: str, decision: str, note: str, **extra) -> dict:
    return {
        "uid": prefs.new_uid(),
        "at": prefs.now(),
        "id": item_id,
        "decision": decision,
        "note": note,
        "via": VIA,
        **prefs.owner_by(),
        **extra,
    }


def _commit(ws: Workspace, files: list[Path], message: str, folders: list[Path] | None = None) -> str | None:
    from livery.review.apply import _commit as commit
    from livery.review.apply import _dirty

    before = _dirty(ws.root)
    if before is None:
        return None
    return commit(
        ws.root, before, [prefs.ledger_path(ws), *files], folders or [], message, prefs.ledger_path(ws)
    )


def _approval_path(ws: Workspace, name: str) -> Path:
    return ws.design_dir(name) / "approved.json"


def _write_approval(ws: Workspace, name: str, record: dict) -> None:
    _approval_path(ws, name).write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8", newline="\n")


def unpin(ws: Workspace, name: str) -> dict | None:
    """Removes a design's approval; what it was."""
    record = approvals.load_approval(ws, name)
    if record is not None:
        _approval_path(ws, name).unlink()
    return record


def revoke(ws: Workspace, name: str, why: str = "") -> dict:
    """Takes back a car's approval, so it installs no more until a new final. Says whether it is on the car
    now, for the owner to put back what was there before (`restore_paint`)."""
    on_car = name in installed(ws, [name])
    was = unpin(ws, name)
    if was is None:
        raise NotHere(f"{name} has no approval to revoke")
    line = _line(f"final:{name}", "revoke", why, was=was)
    prefs.append(ws, [line])
    committed = _commit(ws, [_approval_path(ws, name)], f"Library: revoke {name}")
    return {"line": line, "committed": committed, "on_car": on_car}


def drop_brand(ws: Workspace, kind: str, key: str) -> tuple[dict, list[Path]]:
    """Unapproves a roster brand (its roster row turned down) or a race marking: every car that places it is
    held until a new final without it, and designs stop placing a brand. What it was, and the files."""
    root = ws.sponsors if kind == "sponsor" else ws.marks
    if not (root / key / "brand.toml").exists():
        raise NotHere(f"no {kind} {key!r}")
    dates = sponsors.approvals(root / key)
    sponsors.set_approvals(root / key, dict.fromkeys(dates))
    was: dict = {"approved": dates}
    files = [root / key / "brand.toml"]
    if kind == "sponsor":
        status = next(
            (r["status"] for r in sponsors.roster_rows(root / "roster.md") if r["brand"] == key), None
        )
        if status is not None:
            was["status"] = status
            _roster(root, key, "no")
            files.append(root / "roster.md")
    return was, files


def _roster(root: Path, key: str, status: str) -> None:
    from livery.review.apply import _mark_table

    if (root / "roster.md").exists():
        _mark_table(root / "roster.md", {key: status})


def drop(ws: Workspace, kind: str, key: str, why: str = "") -> dict:
    if kind not in ("sponsor", "mark"):
        raise UsageError(f"only a brand or a marking is dropped, not a {kind}")
    held = _placing(ws, "sponsors" if kind == "sponsor" else "marks", key)
    was, files = drop_brand(ws, kind, key)
    line = _line(f"{kind}:{key}", "drop", why, was=was, held=held)
    prefs.append(ws, [line])
    committed = _commit(ws, files, f"Library: drop {kind} {key}")
    return {"line": line, "committed": committed, "held": held}


def retire(ws: Workspace, row_id: str, why: str = "") -> dict:
    row = taste.retire(ws, row_id, why, by=prefs.owner_by())
    line = _line(f"taste:{row_id}", "retire", why)
    prefs.append(ws, [line])
    committed = _commit(ws, [taste.path(ws)], f"Library: retire taste row {row_id}")
    return {"line": line, "row": row, "committed": committed}


def _restore_folder(ws: Workspace, name: str) -> bool:
    """Brings a design's folder back from the commit before the one that deleted it."""
    from livery.review.apply import _git

    sha = _gone_at(ws, name)
    if sha is None:
        return False
    done = _git(ws.root, "checkout", f"{sha}^", "--", f"designs/{name}")
    if done.returncode:
        raise LiveryError(f"could not bring designs/{name} back: {done.stderr.strip()}")
    _git(ws.root, "reset", "--quiet", "--", f"designs/{name}")  # left unstaged, as Apply finds files
    return True


def revive(ws: Workspace, item_id: str, why: str = "") -> dict:
    """A parked or stopped car, or a concept not picked, back in work: the next design session on it starts
    from the owner's words here. A design whose folder was deleted comes back from git."""
    kind, _, name = item_id.partition(":")
    if kind not in DESIGNS:
        raise UsageError(f"only a car or a concept is revived, not {item_id!r}")
    restored = False
    if not (ws.design_dir(name) / "design.py").exists():
        restored = _restore_folder(ws, name)
        if not restored:
            raise NotHere(f"designs/{name} is gone and git has no copy to bring back")
    line = _line(item_id, "revive", why, restored=restored)
    prefs.append(ws, [line])
    committed = _commit(ws, [], f"Library: revive {name}", folders=[ws.design_dir(name)])
    return {"line": line, "committed": committed, "restored": restored}


def _relative(ws: Workspace, items: list[dict]) -> list[dict]:
    """Pictures inside the checkout as the page fetches them, relative to it."""
    root = ws.root.resolve()
    for i in items:
        rel = []
        for p in i.get("images", []):
            path = Path(p)
            rel.append(
                path.resolve().relative_to(root).as_posix()
                if path.is_absolute() and path.resolve().is_relative_to(root)
                else p
            )
        i["images"] = rel
    return items


def items_for(ws: Workspace, item_id: str) -> list[dict]:
    """What Review again queues for one card, as it stands now. A car is built first if its check isn't
    current."""
    from livery.check import run_build
    from livery.review import items as ri

    kind, _, key = item_id.partition(":")
    if kind in DESIGNS:
        if not (ws.design_dir(key) / "design.py").exists():
            raise NotHere(f"designs/{key} is gone: revive it first")
        built = run_build(ws, key)
        if not built["passed"]:
            raise LiveryError(f"{key} doesn't build: {'; '.join(built['errors'])}")
        return ri.design_items(ws, [key])
    if kind == "sponsor":
        return ri.sponsor_items(ws, only=[key], again=True)
    if kind == "mark":
        return _relative(ws, race_marks.review_items(ws.marks, ws.cache / "marks", only=[key]))
    if kind == "kit":
        return ri.kit_items(ws, key)
    raise UsageError(f"nothing to review again for {item_id!r}")


def review_again(ws: Workspace, item_id: str) -> dict:
    """Queues the card as a screen of its own, which the page applies in this checkout on Send."""
    from livery.review import queue

    items = items_for(ws, item_id)
    if not items:
        raise NotHere(f"nothing to review for {item_id!r}")
    for i in items:
        i["again"] = True
    return {"group": queue.enqueue(ws.root, items, page=True)}


def restore_paint(ws: Workspace) -> dict:
    """Starts `livery install --restore` on its own, since it may wait for the sim lock; its log is in the
    state dir."""
    import os
    import sys

    log = state.root() / "library-restore.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    with open(log, "w", encoding="utf-8") as out:
        subprocess.Popen(
            [sys.executable, "-c", "from livery.cli import app; app()", "install", "--restore"],
            cwd=ws.root,
            stdout=out,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
            env=os.environ.copy(),
        )
    return {"log": str(log)}


# --- History's undo and redo of what a line changed -------------------------------------------------------


def _reverse(ws: Workspace, d: dict, undo: bool) -> tuple[list[Path], list[Path]]:
    """Puts back what a ledger line changed (undo) or does it again (redo); the files and folders touched.
    A line that changed nothing but the ledger touches none."""
    kind, _, key = d.get("id", "").partition(":")
    decision = d.get("decision")
    if kind == "final" and "was" in d and decision in ("revoke", "stop", "carry"):
        if undo:
            _write_approval(ws, key, d["was"])
        elif decision == "carry":
            _write_approval(ws, key, d["record"])
        else:
            unpin(ws, key)
        return [_approval_path(ws, key)], []
    if kind in ("sponsor", "mark") and "was" in d and decision in ("drop", "reject"):
        root = ws.sponsors if kind == "sponsor" else ws.marks
        if not (root / key / "brand.toml").exists():
            raise NotHere(f"{kind} {key} is no longer drawn")
        if undo:
            sponsors.set_approvals(root / key, d["was"]["approved"])
            if "status" in d["was"]:
                _roster(root, key, d["was"]["status"])
            return [root / key / "brand.toml", root / "roster.md"], []
        return drop_brand(ws, kind, key)[1], []
    if kind == "taste" and decision == "retire":
        if undo:
            taste.unretire(ws, key)
        else:
            taste.retire(ws, key, d.get("note", ""), by=prefs.owner_by())
        return [taste.path(ws)], []
    if kind in DESIGNS and decision == "revive" and d.get("restored"):
        if undo:
            shutil.rmtree(ws.design_dir(key), ignore_errors=True)
        else:
            _restore_folder(ws, key)
        return [], [ws.design_dir(key)]
    return [], []


def undo(ws: Workspace, d: dict) -> tuple[list[Path], list[Path]]:
    return _reverse(ws, d, undo=True)


def redo(ws: Workspace, d: dict) -> tuple[list[Path], list[Path]]:
    return _reverse(ws, d, undo=False)


# --- build --all ------------------------------------------------------------------------------------------


def settle(ws: Workspace, report: dict[str, dict]) -> dict[str, list[str]]:
    """After `build --all`: each approved design a library or kit change moved without moving a pixel past
    approvals.TOLERANCE keeps its approval, as a ledger line by this agent that History can undo; each one
    moved further is queued as a "still OK?" screen on the page. The designs carried and asked; each row
    says so too."""
    from livery.review import items as ri
    from livery.review import queue

    lines: list[dict] = []
    asked: list[str] = []
    for name, row in report.items():
        if row["status"] != "changed":
            continue
        try:
            kits.require_approved(ws, kit_name(script(ws, name)))
        except LiveryError as e:
            row["not_asked"] = str(e)
            continue
        if approvals.carries(row):
            was, record = approvals.carry(ws, name)
            moved = {k: row[k] for k in ("changed_by", "paint_changed", "spec_changed") if k in row}
            lines.append(
                {
                    "uid": prefs.new_uid(),
                    "at": prefs.now(),
                    "id": f"final:{name}",
                    "decision": "carry",
                    "note": "",
                    "moved": moved,
                    "was": was,
                    "record": record,
                    **prefs.agent_by(),
                }
            )
            row["status"] = "carried"
            continue
        try:
            items = [i | {"still": True} for i in ri.design_items(ws, [name])]
            row["asked"] = queue.enqueue(ws.root, items, page=True)
        except LiveryError as e:
            row["not_asked"] = str(e)
            continue
        asked.append(name)
    if lines:
        prefs.append(ws, lines)
    return {"carried": [str(d["id"]).partition(":")[2] for d in lines], "asked": asked}
