"""What goes into a review: designs, a kit, roster.md brands, drawn sponsors, references.md entries, and
the edits a retro proposes."""

import json
import re
from pathlib import Path

from PIL import Image

from livery import config, sponsors, stamps
from livery import kit as kits
from livery.config import Workspace
from livery.designs import kit_name, script
from livery.errors import LiveryError, UsageError

# The owner's optional rating of a concept or final, stored with their decision.
RATINGS = {1: "done with it", 2: "fine", 3: "would race", 4: "love it"}


class BadProposals(UsageError):
    pass


def _rel(ws: Workspace, p: Path) -> str:
    return p.resolve().relative_to(ws.root.resolve()).as_posix()


def _baked(ws: Workspace, name: str, built: dict[str, str] | None) -> Path | None:
    """A baked run of the design's checked build (the one with these digests, for a final), where baked
    views stand in for its sim shots (`livery.bake.run_usable`); else None."""
    from livery import bake
    from livery.check import checked_build

    try:
        out, stored = checked_build(ws, name)
        if built and any(stored["digests"].get(k) != built.get(k) for k in stamps.SHOWN):
            return None
        kit = kits.load(ws, kit_name(script(ws, name)))
        cfg = config.load()
        if bake.usable(cfg, ws, kit, []):
            return None
        outputs = {f: (out / f).read_bytes() for f in stamps.SHOWN}
        run = bake.baked_run(cfg, ws, name, kit, outputs)
        return None if bake.run_usable(cfg, ws, kit, run) else run
    except LiveryError:
        return None


def _previews(
    ws: Workspace, name: str, built: dict[str, str] | None
) -> tuple[dict[Path, str], str, Path | None, Path | None]:
    """The shots, in policy.toml's view order, then the flat pictures, of the build with these digests (a
    final) or of the design as it stands (a concept, maybe unchecked), each with its caption; a line for the
    body when the shots are of something else; and the sim run and the baked run shown. Sim shots of the
    build come first; without them, baked views where they stand in for sim shots, captioned as baked. A
    stale flat preview is rendered again; sim shots can't be, so they are shown and marked."""
    from livery import preview_flat

    if built:
        folder = preview_flat.fresh(ws, name, built)
        run = stamps.matching_run(ws, name, built)
    else:
        now = stamps.hashes(ws, name)
        folder = stamps.flat_dir(ws, name)
        if not stamps.of_version(stamps.read(folder / stamps.STAMP), now):
            preview_flat.flat(ws, name)
        run = stamps.latest_of_version(ws, name, now)
    baked = _baked(ws, name, built) if run is None else None
    note = ""
    if baked is not None:
        note = (
            f"The car shots are baked from {kit_name(script(ws, name))}'s calibration capture, not shot in "
            f"the sim; livery preview sim {name} shoots this build.\n\n"
        )
    elif run is None and (runs := stamps.runs(ws, name)):
        run = runs[-1]
        note = (
            f"The sim shots ({run.name}) are of another build than this one; livery preview sim {name} "
            "shoots this one.\n\n"
        )
    views = ws.policy.preview
    shown = baked or run
    shots = sorted(
        (p for p in shown.glob("*.png") if not baked or p.stem in views) if shown else [],
        key=lambda p: (views.index(p.stem) if p.stem in views else len(views), p.stem),
    )
    kind = "baked" if baked else "sim"
    flat = {"views.png": "flat views", "car.png": "iRacing UI car", "squint.png": "flat squint"}
    pictures = {p: f"{p.stem} · {kind}" for p in shots} | {folder / f: c for f, c in flat.items()}
    return pictures, note, run, baked


THEN_AND_NOW = Path("diff") / "reviewed-vs-now.png"  # in the design's .cache folder
JUDGED = {"resolved": "resolved", "partly": "partly resolved", "not": "not resolved"}


def _when(at: str) -> str:
    return at[:16].replace("T", " ")


def _then_and_now(ws: Workspace, name: str, then: Path, now: Path) -> Path | None:
    """The shots of run `then` beside run `now`'s (sim or baked) at half size, a row per view both shot."""
    views = [v for v in ws.policy.preview if (then / f"{v}.png").exists() and (now / f"{v}.png").exists()]
    if not views:
        return None
    rows = []
    for view in views:
        old = Image.open(then / f"{view}.png").convert("RGB")
        size = (old.width // 2, old.height // 2)
        new = Image.open(now / f"{view}.png").convert("RGB")
        rows.append([img.resize(size, Image.Resampling.LANCZOS) for img in (old, new)])
    sheet = Image.new("RGB", (max(2 * a.width for a, _ in rows), sum(a.height for a, _ in rows)))
    y = 0
    for old, new in rows:
        sheet.paste(old, (0, y))
        sheet.paste(new, (old.width, y))
        y += old.height
    out = ws.out(name) / THEN_AND_NOW
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out


def _judgement(ws: Workspace, name: str, note: dict, design_hash: str | None) -> dict | None:
    """The latest round's judgement of the owner's note, never the critic's scores or call: the ledger
    measures how often the critic agrees with the owner, and its call would anchor them."""
    from livery import critic

    for r in reversed(critic.history(ws, name)):
        judged = r.get("owner_note") or {}
        if judged.get("at") == note["at"]:
            return {
                "status": judged["status"],
                "evidence": judged["evidence"],
                "round": r.get("round"),
                "this_build": design_hash is not None and r.get("design_hash") == design_hash,
            }
    return None


def _last_time(
    ws: Workspace, name: str, run: Path | None, design_hash: str | None
) -> tuple[str, Path | None, dict]:
    """What opens a design's item when the ledger has a decision on it: that decision (date, decision, the
    note verbatim), their latest note if it had none, the critic's judgement of that note, and the car
    shots (sim or baked) they saw then beside the ones shown now (none once the old run is gone from
    .cache)."""
    from livery.review.apply import decisions_on, latest_note

    lines = decisions_on(ws, name)
    if not lines:
        return "", None, {}
    last, note = lines[-1], latest_note(ws, name)
    said = f", with the note:\n{last['note']}" if last.get("note", "").strip() else ", with no note"
    text = f"{config.owner()}'s last decision, {_when(last['at'])}: {last['decision']}{said}\n\n"
    fields: dict = {
        "last_decision": {k: last.get(k) for k in ("at", "decision", "note", "runs", "baked_run")}
    }
    if note:
        if note["at"] != last["at"]:
            text += f"Their latest note, {_when(note['at'])} ({note['decision']}):\n{note['note']}\n\n"
        judged = _judgement(ws, name, note, design_hash)
        if judged:
            on = "this build" if judged["this_build"] else "an earlier build"
            text += (
                f"The critic judged that note {JUDGED[judged['status']]} in round {judged['round']}, on {on} "
                f"({judged['evidence']}).\n\n"
            )
        else:
            text += "No critic round has judged that note yet.\n\n"
        fields["owner_note"] = {k: note.get(k) for k in ("at", "decision", "note")} | {"judged": judged}
    then = next((ws.out(name) / "sim" / r for r in last.get("runs") or []), None)
    if then is None and last.get("baked_run"):
        then = ws.out(name) / "baked-runs" / last["baked_run"]
    picture = None
    if run is not None and then is not None and then.is_dir():
        if then == run:
            text += "The car shots are the ones they saw then.\n\n"
        elif picture := _then_and_now(ws, name, then, run):
            text += "The first picture is the car shots they saw then | now, a view per row.\n\n"
    return text, picture, fields


def design_items(ws: Workspace, names: list[str], pin: bool = True) -> list[dict]:
    """One item per design: concepts when there are several (a pick, or a mix), else a final approval.

    A final is pinned to the checked build on disk (NotChecked if there is none) on an approved kit
    (KitNotApproved): accepting it approves exactly that build, and its previews are that build's. If the
    design was approved before and has moved since, the item shows the diff. One that places roster
    sponsor variants or race markings not yet approved says so: accepting it pins nothing until they are,
    by --sponsors or --marks in this review or an earlier one. Every item carries the sim runs it shows,
    for the ledger, and opens with the owner's last decision on the design, if there is one (`_last_time`).
    """
    from livery import approvals

    kind = "concept" if len(names) > 1 else "final"
    items = []
    for name in names:
        if kind == "final" and pin:
            kits.require_approved(ws, kit_name(script(ws, name)))
        shown = approvals.pin_of(ws, name) if kind == "final" and pin else None
        pictures, note, run, baked = _previews(ws, name, shown["digests"] if shown else None)
        brief = ws.design_dir(name) / "brief.md"
        body = note + (brief.read_text(encoding="utf-8") if brief.exists() else "")
        if shown:
            moved = approvals.compare(ws, name, shown["digests"])
            if moved["status"] == "changed":
                body = (
                    f"Changed since {config.owner()} approved it on {moved['approved']}, by "
                    f"{', '.join(moved['changed_by']) or 'nothing recorded'}: "
                    f"{moved.get('paint_changed', '?')} of the car's paint pixels. "
                    "The last picture is approved | now | changed.\n\n" + body
                )
                if "diff" in moved:
                    pictures[Path(moved["diff"])] = "approved | now | changed"
            left = approvals.unapproved(ws, shown)
            if left:
                body = (
                    f"Places {', '.join(left)}, not approved yet: accepting it pins nothing until they "
                    "are.\n\n" + body
                )
            waived = shown.get("waivers") or []
            if waived:
                lines = [
                    f"- {w['check']} on {w['id']} ({len(w.get('hid') or [])} findings hidden): {w['reason']}"
                    for w in waived
                ]
                body = (
                    f"Waivers ({len(waived)}), each a lint finding the design accepts:\n"
                    + "\n".join(lines)
                    + "\n\n"
                    + body
                )
        design_hash = shown["design_hash"] if shown else (stamps.hashes(ws, name) or {}).get("design_hash")
        since, picture, fields = _last_time(ws, name, baked or run, design_hash)
        if picture:
            pictures = {picture: "then | now"} | pictures
        captions = {_rel(ws, p): c for p, c in pictures.items() if p.exists()}
        item = {
            "id": f"{kind}:{name}",
            "kind": kind,
            "title": name,
            "body": since + body,
            "images": list(captions),
            "captions": captions,
            "mix": kind == "concept",
            "park": True,
            "rate": True,
            "runs": [run.name] if run else [],
            "baked_run": baked.name if baked else None,
            **fields,
        }
        if shown:
            item["pin"] = shown
        items.append(item)
    return items


def _body_text(kit_dir: Path) -> str:
    """The body model's part of a kit item: what to check in its pictures, the names the labelling wasn't
    sure of, its open questions and the map problems left for the next fit."""
    from livery import body

    model = body.read(kit_dir)
    if not model.parts and not model.features:
        return ""
    notes = body.review_notes(kit_dir)
    out = [
        "\n\nParts and features by name, on the texture and on the car views (unsure names in red with a ?). "
        "Accept if each name is the part it paints and each feature sits where the car has it."
    ]
    for head, rows in (
        ("Unsure", body.unsure(model)),
        ("Open questions", notes.get("questions", [])),
        ("Map problems for the next kit fit", notes.get("fit_problems", [])),
    ):
        if rows:
            out.append(f"\n\n{head}:\n" + "\n".join(f"- {r}" for r in rows))
    return "".join(out)


def kit_items(ws: Workspace, car: str) -> list[dict]:
    """The kit as it is now: accepting it approves exactly these files (`kit_hash`)."""
    from livery.hashing import kit_hash
    from livery.kit_review import calibration_shots

    review = ws.cache / "kit-review" / car
    try:
        shots = calibration_shots(ws, kits.load(ws, car))
    except LiveryError:  # a kit without a sim profile has no calibration shots yet
        shots = {}
    images = [
        review / "overlay.png",
        review / "parts.png",
        review / "body.png",
        review / "calibration_review.png",
    ]
    images += [review / f"calibration_{shot}.png" for shot in shots]
    text = (
        "Islands by view (left red, right blue, top green, grey unmapped) and the calibration livery in the "
        "sim. Accept if the bands meet across seams and the beltline."
    )
    return [
        {
            "id": f"kit:{car}",
            "kind": "kit",
            "title": f"Kit: {car}",
            "body": text + _body_text(ws.kits / car),
            "images": list(dict.fromkeys(_rel(ws, p) for p in images if p.exists())),
            "mix": False,
            "kit_hash": kit_hash(ws.kits / car),
        }
    ]


def _brand_text(row: dict) -> str:
    return (
        f"{row['name']}: {row['category']}\n\nVoice: {row['voice']}\n\nMark: {row['mark']}\n\n"
        f"Own colours: {row['colours']}\n\nName checked: {row['checked']}"
    )


def roster_items(path: Path | None = None) -> list[dict]:
    """roster.md rows not yet accepted or rejected: the line-up, judged before anything is drawn."""
    rows = sponsors.roster_rows(path)
    return [
        {
            "id": f"roster:{row['brand']}",
            "kind": "roster",
            "title": row["name"],
            "body": _brand_text(row) + "\n\nAccept = draw it. Reject = drop it. Edit = rework the row first.",
            "images": [],
            "mix": False,
            "bulk": True,  # one key accepts every undecided item of its kind
        }
        for row in rows
        if row["status"] not in ("yes", "no")
    ]


ROSTER_CAR = "sponsor-roster"  # the test design that puts every brand on a car


def sponsor_items(ws: Workspace, root: Path | None = None, only: list[str] | None = None) -> list[dict]:
    """Each drawn brand (or each of `only`) with a variant not yet approved, as a sheet; then the roster on
    a car, if that design exists."""
    from livery.lint.run import thresholds

    root = root or ws.sponsors
    rows = {r["brand"]: r for r in sponsors.roster_rows(root / "roster.md")}
    min_stroke = thresholds(ws.policy_file).min_stroke_px
    items = []
    for toml in sorted(root.glob("*/brand.toml")):
        if only and toml.parent.name not in only:
            continue
        brand = sponsors.load(toml.parent.name, root)
        pending = [n for n, v in brand.variants.items() if not v.approved]
        if not pending:
            continue
        sheet = sponsors.sheet(brand, ws.cache / "sponsors" / brand.id / "sheet.png", min_stroke, ws.fonts)
        row = rows.get(brand.id)
        items.append(
            {
                "id": f"sponsor:{brand.id}",
                "kind": "sponsor",
                "title": row["name"] if row else brand.id,
                "body": (_brand_text(row) + "\n\n" if row else "")
                + f"To approve: {', '.join(pending)}. Accept approves every variant; reject drops the brand.",
                "images": [_rel(ws, sheet)],
                "mix": False,
                "bulk": True,
            }
        )
    if items and (ws.design_dir(ROSTER_CAR) / "design.py").exists():
        for car in design_items(ws, [ROSTER_CAR], pin=False):  # a test car for the brands, not a livery
            car["body"] = (
                "The brands above on a car. Accepting it pins nothing: its build is approved by "
                f"`livery review {ROSTER_CAR}` once the brands are.\n\n" + car["body"]
            )
            items.append(car)
    return items


REF_ROW = re.compile(r"^\|\s*(\d+)\s*\|(.+?)\|(.+?)\|(.+?)\|\s*(yes|no|)\s*\|\s*$")


def reference_rows(path: Path) -> list[tuple[int, str, str, str, str]]:
    """(number, livery, car and era, what makes it work, yes/no) for each table row."""
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = REF_ROW.match(line)
        if m:
            rows.append((int(m[1]), m[2].strip(), m[3].strip(), m[4].strip(), m[5].strip()))
    return rows


def reference_items(path: Path) -> list[dict]:
    return [
        {
            "id": f"reference:{n}",
            "kind": "reference",
            "title": f"{n}. {name}",
            "body": f"{car}\n\n{why}\n\nAccept = yes, a taste anchor. Reject = no.",
            "images": [],
            "mix": False,
            "bulk": True,
            "current": mark,
        }
        for n, name, car, why, mark in reference_rows(path)
    ]


def proposal_items(path: Path) -> list[dict]:
    """A retro's proposed edits, from a JSON list of {title, body}: the body says where the edit goes and
    gives its text or diff. Repo-relative `images` are shown too. Apply hands back the accepted ones."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise BadProposals(f"{path}: {e}") from None
    ok = isinstance(raw, list) and all(
        isinstance(p, dict) and isinstance(p.get("title"), str) and isinstance(p.get("body"), str)
        for p in raw
    )
    if not ok or not raw:
        raise BadProposals(f"{path} must be a JSON list of {{title, body}}, one per proposed edit")
    items, seen = [], set()
    for p in raw:
        # Ids from the title, not the position, so a review resumed on a rewritten file keeps its decisions.
        slug = re.sub(r"[^a-z0-9]+", "-", p["title"].lower()).strip("-")[:48] or "proposal"
        key, n = slug, 1
        while key in seen:
            n += 1
            key = f"{slug}-{n}"
        seen.add(key)
        items.append(
            {
                "id": f"proposal:{key}",
                "kind": "proposal",
                "title": p["title"],
                "body": p["body"],
                "images": [i for i in p.get("images", []) if isinstance(i, str)],
                "mix": False,
            }
        )
    return items
