"""The critic's inputs and its verdicts. The critic itself is a subagent (.claude/agents/livery-critic.md).

`pack` gathers what the critic may see, never the design script: a round or an adversary check.
`pack_compare` puts this round's shots beside the last round's in both orders, and `pack_pairs` puts
concepts side by side two at a time in both orders, because a judge compares far more reliably than it
scores and favours whichever comes first. A pack shows only shots of the checked build: sim shots, or with
`baked` renders from the kit's calibration capture once it passed bake check (livery.bake). A round judged
on baked renders ships only where they stand in for sim shots: the check covered every finish the build
uses, and the capture and renderer are still the checked ones. The `record*` functions validate the
critic's JSON and append it to the design's verdicts.jsonl (a concept ranking to each concept's), the one
verdict store; `status` computes from those records whether the owner's asks are met (`ready`, where a
refinement goes back to them) and whether the critic's whole bar is (`ship`), so neither is the critic's
own word. `answer` stores the builder's reply to each fix, which the next round's pack shows the next
critic. The rubric's keys are read from its table in the livery-design skill, the one place they live.
"""

import hashlib
import itertools
import json
import re
import shutil
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PIL import Image

from livery import config, hashing, references, stamps, taste
from livery import kit as kits
from livery.config import Workspace
from livery.designs import kit_name, script
from livery.errors import LiveryError, UsageError

CALLS = ("iterate", "rework")  # the critic's call on a round that doesn't ship
RESOLVED = ("resolved", "partly", "not")
SIDES = ("A", "B")
# done: changed as asked; blocked: can't be done inside designs/<name>/ (a kit map, a lint window, a
# missing primitive), so it goes to the owner as a kit or library change; skipped: chose not to, and why.
ANSWERS = ("done", "blocked", "skipped")
DISTANCE_PX = 200  # what survives at distance and speed...
UPSCALE = 3  # ...shown three times larger, because Claude misreads images under 200 px
ANCHOR_PX = 1600  # the long edge of an anchor picture in a pack
ANCHOR_PICTURES = 4  # per favourite row: some showroom pages carry ten
FLAT = ("views.png", "car.png", "squint.png")  # what concept pairs are judged on, before any sim run
PAIR = "__"  # joins a pair's names, A first: the pack folder and its verdict file are A__B
PROVENANCE = ("design_hash", "shots", "rubric", "model", "baked")
TITLE_WORDS = 12  # a suggestion's title is a headline: about eight words, refused past this


class BadVerdict(LiveryError, ValueError):
    pass


class Unanswered(LiveryError, ValueError):
    pass


class NoShots(LiveryError):
    pass


class StaleShots(LiveryError):
    pass


def _quiet(message: str) -> None:
    pass


# Records


def _path(ws: Workspace, name: str) -> Path:
    return ws.design_dir(name) / "verdicts.jsonl"


def records(ws: Workspace, name: str) -> list[dict]:
    """Every record of a design, oldest first: round verdicts, compares and adversary checks."""
    path = _path(ws, name)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _kind(record: dict) -> str:
    return record.get("kind", "round")  # every record before v2 is a round


def history(ws: Workspace, name: str) -> list[dict]:
    """Every round verdict of a design, oldest first, each with the builder's answers or None."""
    return [r for r in records(ws, name) if _kind(r) == "round"]


def _write(ws: Workspace, name: str, recs: list[dict]) -> None:
    lines = "".join(json.dumps(r) + "\n" for r in recs)
    _path(ws, name).write_text(lines, encoding="utf-8", newline="\n")


def fixes(round_: dict) -> list[dict]:
    """A round's fixes, each with `text`, the whole suggestion on one line. A suggestion is {title, detail,
    where, optional}; before it, a fix was {text, optional}, and before v2 a plain string, when only what a
    ship verdict left was polish."""
    out = []
    for f in round_.get("fixes", []):
        if not isinstance(f, dict):
            f = {"text": f, "optional": round_.get("verdict") == "ship"}
        elif f.get("title"):
            where = f" ({f['where']})" if f.get("where") else ""
            f = f | {"text": f"{f['title']}{where}: {f['detail']}" if f.get("detail") else f["title"] + where}
        out.append(f)
    return out


def fix_title(fix: dict) -> str:
    """A fix as a status line names it: a suggestion's title, an older fix's text."""
    return fix.get("title") or fix["text"]


def owner_note(ws: Workspace, name: str) -> dict | None:
    """The owner's latest note on the design in the decision ledger, which every round judges like a fix,
    with the critic suggestions they endorsed in it and their pins, which every round judges too."""
    from livery.review.apply import latest_note

    line = latest_note(ws, name)
    if not line:
        return None
    note = {k: line.get(k) for k in ("note", "decision", "at")}
    return note | {k: line[k] for k in ("endorsed", "pins") if line.get(k)}


def _judged_by(ws: Workspace | None) -> tuple[Path, Path]:
    """The rubric (which designers read too) and the critic's own definition."""
    ws = ws or Workspace()
    return ws.skills / "livery-design" / "rubric.md", ws.agents / "agents" / "livery-critic.md"


def taste_ids(ws: Workspace | None = None) -> list[str]:
    """The live rows of the owner's taste table."""
    return taste.ids(ws or Workspace())


def rubric_keys(ws: Workspace | None = None) -> list[str]:
    """The key in the first cell of each row of the rubric's table, which every round scores."""
    text = _judged_by(ws)[0].read_text(encoding="utf-8")
    return re.findall(r"^\|\s*`([a-z][a-z0-9_]*)`\s*\|", text, re.M)


def rubric_version(ws: Workspace | None = None) -> str:
    """A hash of what the critic judges by: the rubric, the taste table and its own prompt."""
    h = hashlib.sha256()
    for p in _judged_by(ws):
        h.update(p.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    h.update(taste.markdown(ws or Workspace()).encode())
    return h.hexdigest()[:16]


def critic_model(ws: Workspace | None = None) -> str | None:
    """The model the critic's definition pins, so verdicts from different judges are told apart."""
    text = _judged_by(ws)[1].read_text(encoding="utf-8")
    m = re.search(r"^model:\s*(\S+)\s*$", text.split("---")[1], re.M)
    return m[1] if m else None


# Shots


def _rel(ws: Workspace, p: Path) -> str:
    return p.relative_to(ws.root).as_posix()


def _runs(ws: Workspace, name: str) -> list[Path]:
    return stamps.runs(ws, name)


def _shots(ws: Workspace, name: str, baked: bool = False) -> tuple[Path, dict[str, str]]:
    """The latest sim run of the checked build, and that build's digests. Shots of any other build, or from
    a run that recorded none, are refused: the critic judges what would be installed. With `baked`, a
    baked run of the checked build instead."""
    from livery.check import checked_build

    built, stored = checked_build(ws, name)
    if baked:
        return _baked(ws, name, built, stored), stored["digests"]
    run = stamps.matching_run(ws, name, stored["digests"])
    if run is None:
        runs = _runs(ws, name)
        if not runs:
            raise NoShots(f"{name} has no sim shots; run livery preview sim {name}")
        raise StaleShots(
            f"{name}'s sim shots (the latest is {runs[-1].name}) show another build than the checked one; "
            f"run livery preview sim {name}"
        )
    return run, stored["digests"]


def _baked(ws: Workspace, name: str, built: Path, stored: dict) -> Path:
    """A baked run of the checked build, from the kit's capture once it passed bake check."""
    from livery import bake

    outputs = {f: (built / f).read_bytes() for f in stamps.SHOWN}
    if stamps.digests(outputs) != {k: stored["digests"].get(k) for k in stamps.SHOWN}:
        raise StaleShots(f"{name}'s TGAs aren't the checked build's; run livery build {name}")
    kit = kits.load(ws, kit_name(script(ws, name)))
    return bake.baked_run(config.load(), ws, name, kit, outputs)


def _baked_why(ws: Workspace, name: str, shots: str | None) -> list[str]:
    """Why the baked run a round judged can't stand in for sim shots; empty when it can."""
    from livery import bake

    run = ws.root / shots if shots else None
    if run is None or not run.is_dir():
        return [f"its baked run {shots} is gone"]
    try:
        kit = kits.load(ws, kit_name(script(ws, name)))
    except LiveryError as e:
        return [str(e)]
    return bake.run_usable(config.load(), ws, kit, run)


def _previous_run(ws: Workspace, name: str, current: Path) -> Path:
    """The shots the last round was judged on; for rounds recorded without them, the sim run before."""
    for r in reversed(history(ws, name)):
        shots = r.get("shots")
        if shots and shots != _rel(ws, current) and (ws.root / shots).is_dir():
            return ws.root / shots
    earlier = [r for r in _runs(ws, name) if r.name < current.name]
    if not earlier:
        raise NoShots(f"{name} has no earlier round's shots to compare with")
    return earlier[-1]


def _design_hash(ws: Workspace, name: str) -> str | None:
    """The last build's: the shots show the build, which may be older than the script."""
    try:
        return json.loads((ws.out(name) / "check.json").read_text(encoding="utf-8")).get("design_hash")
    except (OSError, ValueError):
        return None


def _hash_now(ws: Workspace, name: str) -> str | None:
    """The design as its files stand, edits not yet built included; else the last build's."""
    try:
        return hashing.design_hash(ws, name)
    except (LiveryError, SyntaxError):
        return _design_hash(ws, name)


def _provenance(ws: Workspace, name: str, run: Path | None, baked: bool = False) -> dict:
    return {
        "design_hash": _design_hash(ws, name),
        "shots": _rel(ws, run) if run else None,
        "rubric": rubric_version(ws),
        "model": critic_model(ws),
        "baked": baked,
    }


# What a pack remembers for the record that follows it, in the design's build folder, out of the critic's
# sight: which pack, the shots it showed and what they were judged by.


def _keys(ws: Workspace, name: str) -> Path:
    return ws.out(name) / "critic.json"


def _remember(ws: Workspace, name: str, mode: str, key: dict) -> None:
    path = _keys(ws, name)
    keys = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(keys | {mode: key}, indent=1), encoding="utf-8", newline="\n")


def _recall(ws: Workspace, name: str, mode: str) -> dict | None:
    path = _keys(ws, name)
    return json.loads(path.read_text(encoding="utf-8")).get(mode) if path.exists() else None


def _forget(ws: Workspace, name: str, mode: str) -> None:
    path = _keys(ws, name)
    if path.exists():
        keys = json.loads(path.read_text(encoding="utf-8"))
        keys.pop(mode, None)
        path.write_text(json.dumps(keys, indent=1), encoding="utf-8", newline="\n")


# Packs


def _views(ws: Workspace) -> list[str]:
    """The views a preview shoots; a baked run also holds sheet.png, which a pack leaves out."""
    return list(ws.policy.preview)


def _is_baked(run: Path) -> bool:
    return bool((stamps.read(run / stamps.STAMP) or {}).get("baked"))


def _crops(ws: Workspace, name: str) -> dict[str, tuple]:
    """The close crops in the profile of the design's kit, which frames its car."""
    try:
        return kits.profile(kits.load(ws, kit_name(script(ws, name)))).crops
    except LiveryError:
        return {}


def _distance(img: Image.Image) -> Image.Image:
    small = img.resize(
        (DISTANCE_PX, max(1, round(img.height * DISTANCE_PX / img.width))), Image.Resampling.LANCZOS
    )
    return small.resize((small.width * UPSCALE, small.height * UPSCALE), Image.Resampling.LANCZOS)


def _new_folder(ws: Workspace, label: str) -> Path:
    base = ws.cache / "critic" / f"{time.strftime('%Y%m%d-%H%M%S')}-{label}"
    folder, n = base, 1
    while folder.exists():
        n += 1
        folder = base.with_name(f"{base.name}-{n}")
    folder.mkdir(parents=True)
    return folder


def _common(ws: Workspace, folder: Path, mode: str) -> dict:
    """The rubric, the taste table, the references and the owner's preference log, copied in, and the
    owner's name: a manifest points only at files inside its pack."""
    manifest: dict = {"mode": mode, "owner": config.owner()}
    rubric, _ = _judged_by(ws)
    (folder / "taste.md").write_text(taste.markdown(ws), encoding="utf-8", newline="\n")
    manifest["taste"] = "taste.md"
    for key, src, dest in (
        ("rubric", rubric, "rubric.md"),
        ("references", ws.references, "references.md"),
        ("prefs", ws.prefs / "log.md", "prefs-log.md"),
    ):
        if src.exists():
            shutil.copy2(src, folder / dest)
            manifest[key] = dest
    return manifest


def _copy(files: list[Path], dest: Path, folder: Path) -> list[str]:
    dest.mkdir(parents=True, exist_ok=True)
    for p in files:
        shutil.copy2(p, dest / p.name)
    return [(dest / p.name).relative_to(folder).as_posix() for p in files]


def _anchors(files: list[Path], dest: Path, folder: Path) -> list[str]:
    """Anchor pictures copied no larger than ANCHOR_PX: they show a system to match, not detail to
    judge, and a round shows a dozen of them."""
    dest.mkdir(parents=True, exist_ok=True)
    for p in files:
        img = Image.open(p)
        img.thumbnail((ANCHOR_PX, ANCHOR_PX), Image.Resampling.LANCZOS)
        img.convert("RGB").save(dest / p.name, quality=90)
    return [(dest / p.name).relative_to(folder).as_posix() for p in files]


def _shot_set(run: Path, folder: Path, d: Path, crops: dict, views: list[str] | None = None) -> dict:
    """A sim run's shots copied into d, each with its distance image, and the close crops."""
    d.mkdir(parents=True, exist_ok=True)
    rel = d.relative_to(folder).as_posix()
    entry: dict = {"shots": [], "distance": [], "crops": {}}
    for shot in sorted(run.glob("*.png")):
        if views is not None and shot.stem not in views:
            continue
        img = Image.open(shot).convert("RGB")
        img.save(d / shot.name)
        _distance(img).save(d / f"{shot.stem}-distance.png")
        entry["shots"].append(f"{rel}/{shot.name}")
        entry["distance"].append(f"{rel}/{shot.stem}-distance.png")
    for crop_name, (shot, *box) in crops.items():
        src = run / f"{shot}.png"
        if src.exists() and (views is None or shot in views):
            Image.open(src).convert("RGB").crop(tuple(box)).save(d / f"crop-{crop_name}.png")
            entry["crops"][crop_name] = f"{rel}/crop-{crop_name}.png"
    return entry


def _brief(ws: Workspace, name: str, folder: Path, d: Path) -> str | None:
    brief = ws.design_dir(name) / "brief.md"
    if not brief.exists():
        return None
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy2(brief, d / "brief.md")
    return (d / "brief.md").relative_to(folder).as_posix()


def _kit_notes(ws: Workspace, name: str, folder: Path, d: Path) -> str | None:
    """What the sim draws over the design's car and what hides its paint (`body.notes`), as kit.md."""
    from livery import body

    try:
        text = body.notes(kits.load(ws, kit_name(script(ws, name))))
    except LiveryError:
        return None
    if not text:
        return None
    d.mkdir(parents=True, exist_ok=True)
    (d / "kit.md").write_text(text, encoding="utf-8", newline="\n")
    return (d / "kit.md").relative_to(folder).as_posix()


def _favourites(ws: Workspace, name: str, folder: Path, d: Path, warn: Callable[[str], None]) -> dict:
    """The cached pictures of the reference rows the brief names: what the design is held against."""
    brief = ws.design_dir(name) / "brief.md"
    rows = references.brief_rows(brief.read_text(encoding="utf-8")) if brief.exists() else []
    if not rows:
        warn(f"{name}: the brief names no reference rows (a line like 'References: 25, 32')")
    found = {}
    for row in rows:
        if pics := references.pictures(ws, row):
            found[str(row)] = _anchors(pics[:ANCHOR_PICTURES], d / "anchors" / str(row), folder)
        else:
            warn(f"{name}: reference row {row} has no cached pictures; run livery references fetch")
    return found


def _images(entry: dict) -> list[str]:
    paths = [*entry["shots"], *entry["distance"], *entry["crops"].values(), entry.get("squint")]
    paths += [p.get(k) for p in entry.get("pins", []) for k in ("then", "now")]
    return [Path(p).name for p in paths if p]


def _pins(pins: list[dict], run: Path, flat: Path, folder: Path, d: Path) -> list[dict]:
    """Each of the owner's pins as the pack shows it: its view, note and the elements under it, the crop
    they pinned (`then`) and the same box on this round's picture of that view (`now`)."""
    from livery import state
    from livery.review import pins as review_pins

    out = []
    for n, pin in enumerate(pins, 1):
        entry = {"view": pin["view"], "note": pin["note"], "over": pin.get("elements") or []}
        then = state.root() / pin["crop"] if pin.get("crop") else None
        if then is not None and then.is_file():
            shutil.copy2(then, d / f"pin-{n}-then.png")
            entry["then"] = f"{d.relative_to(folder).as_posix()}/pin-{n}-then.png"
        view = pin["view"]
        src = (
            flat / view.removeprefix(review_pins.FLAT)
            if view.startswith(review_pins.FLAT)
            else run / f"{view}.png"
        )
        if src.is_file():
            review_pins.crop(src, pin["box"]).save(d / f"pin-{n}-now.png")
            entry["now"] = f"{d.relative_to(folder).as_posix()}/pin-{n}-now.png"
        out.append(entry)
    return out


def pack(
    ws: Workspace,
    name: str,
    mode: str = "round",
    warn: Callable[[str], None] = _quiet,
    baked: bool = False,
) -> Path:
    """A folder with everything the critic may look at for a round or an adversary check of one design,
    and a manifest naming each file's role. A round also carries the design's earlier verdicts with the
    builder's answers and the owner's latest note; an adversary check judges the shots alone. `baked` packs
    baked renders of the checked build in place of sim shots, and the manifest says so."""
    from livery import preview_flat

    if mode not in ("round", "adversary"):
        raise UsageError(f"a pack is a round or an adversary check, not {mode!r}")
    run, digests = _shots(ws, name, baked)
    rounds = history(ws, name) if mode == "round" else []
    if rounds and rounds[-1].get("fixes") and rounds[-1].get("answers") is None:
        raise Unanswered(f"{name}: answer the last verdict's fixes first (livery critic answer {name})")
    folder = _new_folder(ws, mode)
    manifest = _common(ws, folder, mode) | {"designs": {}, "baked": baked}
    if boring := references.boring(ws):
        manifest["boring"] = {
            n: _anchors(p, folder / "anchors" / "boring" / n, folder) for n, p in boring.items()
        }
    else:
        warn("no boring anchors are cached; run livery references fetch")
    d = folder / name
    entry = _shot_set(run, folder, d, _crops(ws, name), _views(ws) if baked else None)
    entry["baked"] = baked
    if brief := _brief(ws, name, folder, d):
        entry["brief"] = brief
    if notes := _kit_notes(ws, name, folder, d):
        entry["kit"] = notes
    if rounds:
        (d / "history.json").write_text(json.dumps(rounds, indent=1), encoding="utf-8", newline="\n")
        entry["history"] = f"{name}/history.json"
        entry["previous_fixes"] = [f["text"] for f in fixes(rounds[-1])]
    note = owner_note(ws, name) if mode == "round" else None
    flat = preview_flat.fresh(ws, name, digests)
    if note:
        shown = dict(note)
        if note.get("pins"):
            shown["pins"] = entry["pins"] = _pins(note["pins"], run, flat, folder, d)
        (d / "owner-note.json").write_text(json.dumps(shown, indent=1), encoding="utf-8", newline="\n")
        entry["owner_note"] = f"{name}/owner-note.json"
    shutil.copy2(flat / "squint.png", d / "squint.png")
    entry["squint"] = f"{name}/squint.png"
    if anchors := _favourites(ws, name, folder, d, warn):
        entry["anchors"] = anchors
    manifest["designs"][name] = entry
    key = {"pack": _rel(ws, folder), **_provenance(ws, name, run, baked), "images": _images(entry)}
    _remember(ws, name, mode, key | {"taste": taste_ids(ws), "scores": rubric_keys(ws), "owner_note": note})
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8", newline="\n")
    return folder


def pack_compare(ws: Workspace, name: str, baked: bool = False) -> tuple[Path, Path]:
    """This round's shots and the last round's as A and B, and again as B and A, in two packs for two
    fresh critics, so position bias cancels where they agree. Nothing in a pack says which is newer.
    `baked`: this round was judged on baked renders."""
    current, _ = _shots(ws, name, baked)
    previous = _previous_run(ws, name, current)
    shared = {p.stem for p in current.glob("*.png")} & {p.stem for p in previous.glob("*.png")}
    if _is_baked(current) or _is_baked(previous):
        shared &= set(_views(ws))
    views = sorted(shared)
    if not views:
        raise NoShots(f"{name}: this round's shots and the last round's share no view")
    folders = []
    for n, order in enumerate(((current, previous), (previous, current)), 1):
        folder = _new_folder(ws, f"compare-{n}")
        manifest = _common(ws, folder, "compare")
        manifest["baked"] = {side: _is_baked(run) for side, run in zip(SIDES, order, strict=True)}
        if brief := _brief(ws, name, folder, folder):
            manifest["brief"] = brief
        if notes := _kit_notes(ws, name, folder, folder):
            manifest["kit"] = notes
        for side, run in zip(SIDES, order, strict=True):
            manifest[side] = _shot_set(run, folder, folder / side, _crops(ws, name), views)
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8", newline="\n")
        folders.append(folder)
    key = {"packs": [_rel(ws, f) for f in folders], "against": _rel(ws, previous)}
    _remember(ws, name, "compare", key | _provenance(ws, name, current, baked))
    return folders[0], folders[1]


def pack_pairs(ws: Workspace, names: list[str]) -> Path:
    """One pack per ordered pair of concepts, each unordered pair in both orders, in folders named A__B:
    both concepts' brief, flat views, UI car view and squint. Concepts are judged before any sim run (and
    maybe before any check), so the flat previews must be of the scripts as they stand."""
    if len(names) < 2 or len(set(names)) != len(names):
        raise UsageError("pairs take two or more different concepts")
    if any(PAIR in n for n in names):
        raise UsageError(f"a concept's name can't contain {PAIR!r}, which joins a pair's names")
    for name in names:
        if missing := [f for f in FLAT if not (ws.out(name) / "flat" / f).exists()]:
            raise NoShots(f"{name} has no {', '.join(missing)}; run livery preview flat {name}")
        if not stamps.of_version(stamps.of_flat(ws, name), stamps.hashes(ws, name)):
            raise StaleShots(
                f"{name}'s flat preview isn't of the script as it stands; run livery preview flat {name}"
            )
    root = _new_folder(ws, "pairs")
    for pair in itertools.permutations(names, 2):
        folder = root / PAIR.join(pair)
        folder.mkdir()
        manifest = _common(ws, folder, "pair")
        for side, name in zip(SIDES, pair, strict=True):
            d, flat = folder / side, ws.out(name) / "flat"
            entry: dict = {
                k.removesuffix(".png"): v
                for k, v in zip(FLAT, _copy([flat / f for f in FLAT], d, folder), strict=True)
            }
            _distance(Image.open(flat / "car.png").convert("RGB")).save(d / "car-distance.png")
            entry["car_distance"] = f"{side}/car-distance.png"
            if brief := _brief(ws, name, folder, d):
                entry["brief"] = brief
            if notes := _kit_notes(ws, name, folder, d):
                entry["kit"] = notes
            manifest[side] = entry
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8", newline="\n")
    return root


# Round verdicts


def _text(v: object) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _strings(verdict: dict, key: str) -> None:
    v = verdict.get(key, [])
    if not isinstance(v, list) or not all(_text(s) for s in v):
        raise BadVerdict(f"{key} must be a list of strings")


def _resolution(entry: object, what: str, images: set[str] | None) -> dict:
    """A fix or note judged resolved, partly or not, naming the pack image that shows it."""
    if not isinstance(entry, dict) or entry.get("status") not in RESOLVED:
        raise BadVerdict(f"{what} needs a status, one of {RESOLVED}")
    if not _text(entry.get("evidence")):
        raise BadVerdict(f"{what} names the shot or crop that shows it (evidence)")
    if images is not None and Path(entry["evidence"]).name not in images:
        raise BadVerdict(f"evidence {entry['evidence']!r} is not an image in the pack")
    return entry


def validate(
    verdict: dict,
    last: dict | None = None,
    taste: list[str] | None = None,
    images: set[str] | None = None,
    note: dict | None = None,
    rubric: list[str] | None = None,
) -> dict:
    """A round verdict. `previous` marks each fix of the `last` round resolved, partly or not, naming the
    pack image that shows it, `owner_note` the owner's `note` the same way, `endorsed` each critic
    suggestion that note endorsed and `pins` each of its pins; `taste` passes or fails every taste row;
    scores 1-5 for every `rubric` key and overall 1-10 are logged only; fixes are suggestions {title,
    detail, where, optional}, the most important first, as many as it takes; the call is iterate or rework.
    `taste` and `rubric` default to the owner's taste table and the rubric as they stand."""
    before = fixes(last) if last else []
    previous = verdict.get("previous", [])
    if not isinstance(previous, list) or len(previous) != len(before):
        raise BadVerdict(f"previous needs one entry per fix of the last round, in order: {len(before)}")
    for p, f in zip(previous, before, strict=True):
        _resolution(p, "each previous entry", images)
        p["fix"] = f["text"]
    judged = verdict.get("owner_note")
    said = note is not None and _text(note.get("note"))
    if not said and judged is not None:
        raise BadVerdict("owner_note must be null: the pack carries no note from the owner")
    if note is not None and said:
        verdict["owner_note"] = (
            _resolution(judged, "owner_note, for the owner's note in the pack,", images) | note
        )
    asked, at = (note.get("endorsed") or [], note.get("at")) if note else ([], None)
    endorsed = verdict.get("endorsed", [])
    if not isinstance(endorsed, list) or len(endorsed) != len(asked):
        raise BadVerdict(
            f"endorsed needs one entry per critic suggestion the owner endorsed, in order: {len(asked)}"
        )
    for e, s in zip(endorsed, asked, strict=True):
        _resolution(e, "each endorsed entry", images).update(suggestion=s, at=at)
    pinned = (note.get("pins") or []) if note else []
    judged_pins = verdict.get("pins", [])
    if not isinstance(judged_pins, list) or len(judged_pins) != len(pinned):
        raise BadVerdict(f"pins needs one entry per pin of the owner's note, in order: {len(pinned)}")
    for j, p in zip(judged_pins, pinned, strict=True):
        _resolution(j, "each pins entry", images).update(view=p["view"], note=p["note"], at=at)
    ids = taste_ids() if taste is None else taste
    rows = verdict.get("taste")
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise BadVerdict(f"taste needs one entry per taste row: {ids}")
    if sorted(str(r.get("id")) for r in rows) != sorted(ids):
        raise BadVerdict(f"taste needs one entry per taste row, each once: {ids}")
    for r in rows:
        if not isinstance(r.get("pass"), bool) or not isinstance(r.get("where", ""), str):
            raise BadVerdict(f"taste row {r['id']}: pass is true or false, where is text")
        if not r["pass"] and not _text(r.get("where")):
            raise BadVerdict(f"taste row {r['id']} fails: say where")
    scores = verdict.get("scores", {})
    if missing := [k for k in (rubric_keys() if rubric is None else rubric) if k not in scores]:
        raise BadVerdict(f"missing rubric scores: {missing}")
    for k, v in scores.items():
        if not isinstance(v, int) or not 1 <= v <= 5:
            raise BadVerdict(f"score {k}={v!r} is not an integer 1-5")
    overall = verdict.get("overall")
    if not isinstance(overall, int) or not 1 <= overall <= 10:
        raise BadVerdict(f"overall={overall!r} is not an integer 1-10")
    if verdict.get("verdict") not in CALLS:
        raise BadVerdict(f"verdict is the critic's call, one of {CALLS}; ship is computed")
    fx = verdict.get("fixes", [])
    if not isinstance(fx, list):
        raise BadVerdict("fixes must be a list, the most important first")
    for f in fx:
        if not (
            isinstance(f, dict)
            and _text(f.get("title"))
            and isinstance(f.get("detail"), str)
            and isinstance(f.get("where"), str)
            and isinstance(f.get("optional"), bool)
        ):
            raise BadVerdict("each fix is {title, detail, where, optional}")
        if len(f["title"].split()) > TITLE_WORDS:
            raise BadVerdict(f"a fix's title is a headline of about eight words: {f['title']!r}")
    for key in ("lint_missed", "blocked"):
        _strings(verdict, key)
    return verdict


def _stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M")


def record(ws: Workspace, name: str, verdict: dict) -> dict:
    """Validate a round verdict against the last round and the pack it judged, the owner's note that pack
    carried included, and append it with that pack's provenance (design hash, shots, rubric version, critic
    model) to verdicts.jsonl."""
    rounds = history(ws, name)
    key = _recall(ws, name, "round")
    images = set(key["images"]) if key else None
    note = key.get("owner_note") if key else owner_note(ws, name)
    taste, rubric = (key["taste"], key.get("scores")) if key else (taste_ids(ws), rubric_keys(ws))
    validate(verdict, rounds[-1] if rounds else None, taste, images, note, rubric)
    runs = _runs(ws, name)
    prov = {k: key.get(k) for k in PROVENANCE} if key else _provenance(ws, name, runs[-1] if runs else None)
    entry = {
        "kind": "round",
        "version": 2,
        "round": len(rounds) + 1,
        "at": _stamp(),
        **prov,
        **verdict,
        "answers": None,
    }
    _write(ws, name, [*records(ws, name), entry])
    _forget(ws, name, "round")
    return entry


def answer(ws: Workspace, name: str, answers: list[dict]) -> list[dict]:
    """Store the builder's reply to each fix of the latest verdict, in order. Returns the blocked ones."""
    recs = records(ws, name)
    rounds = [r for r in recs if _kind(r) == "round"]
    if not rounds:
        raise BadVerdict(f"{name} has no recorded verdict to answer")
    fx = fixes(rounds[-1])
    if not isinstance(answers, list) or len(answers) != len(fx):
        raise BadVerdict(f"give one answer per fix: the last verdict has {len(fx)}")
    for a in answers:
        if not isinstance(a, dict) or a.get("status") not in ANSWERS:
            raise BadVerdict(f"each answer needs a status, one of {ANSWERS}")
        if not _text(a.get("note")):
            raise BadVerdict("each answer needs a note: what changed, or what blocks it")
    rounds[-1]["answers"] = answers  # the same dict as in recs
    _write(ws, name, recs)
    return [{"fix": f["text"], **a} for f, a in zip(fx, answers, strict=True) if a["status"] == "blocked"]


# Compare and adversary


def record_compare(ws: Workspace, name: str, first: dict, second: dict) -> dict:
    """Combine the answers to the two compare packs (this round is A in the first, B in the second) into
    better, worse or same for this round. Orders that disagree are 'same'."""
    key = _recall(ws, name, "compare")
    if not key:
        raise BadVerdict(f"{name} has no compare pack to answer; run livery critic pack {name} --compare")
    for v in (first, second):
        if v.get("better") not in (*SIDES, "same") or not isinstance(v.get("where", ""), str):
            raise BadVerdict("a compare answer is {better: A, B or same, where}")
    one = {"A": "better", "B": "worse", "same": "same"}[first["better"]]
    two = {"A": "worse", "B": "better", "same": "same"}[second["better"]]
    result = one if one == two else "same"
    entry = {
        "kind": "compare",
        "at": _stamp(),
        **{k: key.get(k) for k in PROVENANCE},
        "against": key["against"],
        "result": result,
        "orders": [first, second],
    }
    _write(ws, name, [*records(ws, name), entry])
    _forget(ws, name, "compare")
    return entry


def validate_adversary(verdict: dict) -> dict:
    """The adversary's findings, the most boring first, each in a view and marked optional or not."""
    findings = verdict.get("findings")
    if not isinstance(findings, list) or not 1 <= len(findings) <= 3:
        raise BadVerdict("findings: one to three, the most boring first")
    for f in findings:
        if not (isinstance(f, dict) and _text(f.get("text")) and _text(f.get("view"))):
            raise BadVerdict("each finding needs text and the view it is in")
        if not isinstance(f.get("optional"), bool):
            raise BadVerdict("each finding is optional or not")
    if not isinstance(verdict.get("closest"), str | int | None):
        raise BadVerdict("closest names the favourite's reference row, or is null")
    return verdict


def record_adversary(ws: Workspace, name: str, verdict: dict) -> dict:
    validate_adversary(verdict)
    key = _recall(ws, name, "adversary")
    runs = _runs(ws, name)
    prov = {k: key.get(k) for k in PROVENANCE} if key else _provenance(ws, name, runs[-1] if runs else None)
    entry = {"kind": "adversary", "at": _stamp(), **prov, **verdict}
    _write(ws, name, [*records(ws, name), entry])
    _forget(ws, name, "adversary")
    return entry


def _owner_holds(ws: Workspace, name: str, last: dict) -> list[str]:
    """What keeps the last round from going back to the owner: it was judged on baked renders that can't
    stand in for sim shots, the design changed since, their latest note, a pin of it or a suggestion they
    endorsed isn't judged resolved, something is blocked, or a taste row fails."""
    holds = []
    if last.get("baked"):
        holds += [
            f"judged on baked renders that can't stand in for sim shots ({w}): the round that ships is judged "
            f"on sim shots (livery preview sim {name}, then livery critic pack {name})"
            for w in _baked_why(ws, name, last.get("shots"))
        ]
    now = _hash_now(ws, name)
    if now and last.get("design_hash") and now != last["design_hash"]:
        holds.append("the design changed since the last round")
    owner = config.owner()
    if note := owner_note(ws, name):
        judged = last.get("owner_note") or {}
        if _text(note["note"]) and judged.get("at") != note["at"]:
            holds.append(f"no round has judged {owner}'s latest note yet: {note['note']}")
        elif _text(note["note"]) and judged.get("status") != "resolved":
            holds.append(f"{owner}'s note is {judged['status']} resolved: {note['note']}")
        asked = note.get("endorsed") or []
        done = [e for e in last.get("endorsed") or [] if e.get("at") == note["at"]]
        if asked and len(done) != len(asked):
            holds.append(f"no round has judged the critic's suggestions {owner} endorsed yet")
        holds += [
            f"a suggestion {owner} endorsed is {e['status']} resolved: {e['suggestion']}"
            for e in done
            if len(done) == len(asked) and e["status"] != "resolved"
        ]
        pinned = note.get("pins") or []
        seen = [p for p in last.get("pins") or [] if p.get("at") == note["at"]]
        if pinned and len(seen) != len(pinned):
            holds.append(f"no round has judged {owner}'s pins yet")
        holds += [
            f"{owner}'s pin on {p['view']} is {p['status']} resolved: {p['note']}"
            for p in seen
            if len(seen) == len(pinned) and p["status"] != "resolved"
        ]
    holds += [f"blocked, for {owner}: {b}" for b in last.get("blocked", [])]
    passed = {t["id"] for t in last["taste"] if t["pass"]}
    holds += [f"taste row fails or wasn't checked: {i}" for i in taste_ids(ws) if i not in passed]
    return holds


def status(ws: Workspace, name: str) -> dict:
    """Where the design stands, computed from the records. `ready`, where a refinement goes back to the
    owner, when the last round verdict:

    - is a v2 record
    - was judged on sim shots, or on baked renders that stand in for them (`livery.bake.run_usable`)
    - judged the design as its files stand: nothing changed since the round's design hash
    - judged the owner's latest note in the decision ledger, if it says anything, and found it resolved
    - judged every critic suggestion that note endorsed resolved, and every pin of it
    - lists nothing blocked
    - passes every row of the taste table as it stands

    `holds` lists what of that fails; the critic's own fixes never hold it back. `ship`, the critic's whole
    bar, when it is ready and also:

    - leaves none of its own fixes open that isn't optional
    - marks every fix of the round before that isn't optional resolved
    - if there was a round before, has a compare on the same shots, and the latest isn't worse
    - has an adversary check on the same shots whose top finding is optional

    Otherwise `why` lists everything that fails, `holds` first, and the critic's call (iterate or rework)
    stands. `plateau`: the last two compares both came out the same."""
    recs = records(ws, name)
    rounds = [r for r in recs if _kind(r) == "round"]
    results = [r["result"] for r in recs if _kind(r) == "compare"]
    out: dict = {
        "design": name,
        "round": len(rounds),
        "ready": False,
        "holds": [],
        "ship": False,
        "call": None,
        "why": [],
        "baked": False,
    }
    out["plateau"] = results[-2:] == ["same", "same"]
    why: list[str] = out["why"]
    if not rounds:
        out["holds"] = ["no round verdict yet"]
        why.append("no round verdict yet")
        return out
    last = rounds[-1]
    out["call"] = last.get("verdict")
    if last.get("version") != 2:
        out["holds"] = ["the last round predates critic v2; run a round"]
        why.append("the last round predates critic v2; run a round")
        return out
    out["baked"] = bool(last.get("baked"))
    out["holds"] = holds = _owner_holds(ws, name, last)
    out["ready"] = not holds
    why += holds
    why += [f"open fix: {fix_title(f)}" for f in fixes(last) if not f["optional"]]
    if len(rounds) > 1:
        earlier = zip(last["previous"], fixes(rounds[-2]), strict=True)
        why += [
            f"not resolved: {fix_title(f)}"
            for p, f in earlier
            if p["status"] != "resolved" and not f["optional"]
        ]
    compares = [r for r in recs if _kind(r) == "compare" and r.get("shots") == last.get("shots")]
    if len(rounds) > 1 and not compares:
        why.append(f"no compare with the last round (livery critic pack {name} --compare)")
    elif compares and compares[-1]["result"] == "worse":
        why.append(f"worse than the last round: {compares[-1]['orders'][0].get('where', '')}")
    adversaries = [r for r in recs if _kind(r) == "adversary" and r.get("shots") == last.get("shots")]
    if not adversaries:
        why.append(f"no adversary check of these shots (livery critic pack {name} --adversary)")
    elif not (top := adversaries[-1]["findings"][0])["optional"]:
        why.append(f"the adversary's top finding isn't optional ({top['view']}): {top['text']}")
    if not why:
        out |= {"ship": True, "call": "ship"}
    return out


# Concept rankings


def latest_ranking(ws: Workspace, names: list[str]) -> dict | None:
    """The last recorded critic ranking of exactly these concepts, or None. Rankings recorded before pairs
    were a single critic's, and have no `method`."""
    found = [
        r
        for name in names
        for r in records(ws, name)
        if _kind(r) == "ranking" and sorted(r.get("concepts", [])) == sorted(names)
    ]
    return max(found, key=lambda r: r["at"]) if found else None


def bradley_terry(
    names: list[str], games: list[tuple[str, str, float]], prior: float = 0.5
) -> dict[str, float]:
    """Log-strengths from games (a, b, a's score: 1 a win, 0.5 a tie, 0 a loss), fitted by Hunter's MM
    algorithm. Every pair also plays `prior` of a virtual tie, so a concept that never won stays finite."""
    idx = {n: i for i, n in enumerate(names)}
    k = len(names)
    won = prior / 2 * (1 - np.eye(k))  # won[i, j]: i's score against j
    for a, b, s in games:
        won[idx[a], idx[b]] += s
        won[idx[b], idx[a]] += 1 - s
    played = won + won.T
    wins = won.sum(axis=1)
    p = np.ones(k)
    for _ in range(10_000):
        new = wins / (played / (p[:, None] + p[None, :])).sum(axis=1)
        new /= np.exp(np.log(new).mean())
        done = np.abs(new - p).max() < 1e-12
        p = new
        if done:
            break
    return {n: float(np.log(p[i])) for n, i in idx.items()}


def read_pairs(folder: Path, names: list[str]) -> list[tuple[str, str, dict]]:
    """The critic's answer to every pair pack, each saved as <A>__<B>.json in folder: {winner: A, B or
    tie, why}."""
    games, missing = [], []
    for a, b in itertools.permutations(names, 2):
        path = folder / f"{a}{PAIR}{b}.json"
        if not path.exists():
            missing.append(path.name)
            continue
        try:
            v = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise BadVerdict(f"{path} is not JSON: {e}") from None
        if not isinstance(v, dict) or v.get("winner") not in (*SIDES, "tie") or not _text(v.get("why")):
            raise BadVerdict(f"{path.name}: a pair answer is {{winner: A, B or tie, why}}")
        games.append((a, b, v))
    if missing:
        raise BadVerdict(f"every pair is judged in both orders; {folder} has no {', '.join(missing)}")
    return games


def record_pairs(ws: Workspace, names: list[str], folder: Path) -> dict:
    """Rank concepts by Bradley-Terry over their pair verdicts, and append the ranking to each concept's
    verdicts.jsonl with its place and its flat preview's design hash (pairs are judged on flat views), for
    `livery review` to set beside the owner's pick."""
    games = read_pairs(folder, names)
    scored = [(a, b, {"A": 1.0, "B": 0.0, "tie": 0.5}[v["winner"]]) for a, b, v in games]
    strength = bradley_terry(names, scored)
    ranking = sorted(names, key=lambda n: (-strength[n], n))
    won = {n: sum(s if a == n else 1 - s for a, b, s in scored if n in (a, b)) for n in names}
    played = {n: sum(n in (a, b) for a, b, _ in scored) for n in names}
    entry = {
        "kind": "ranking",
        "at": _stamp(),
        "concepts": sorted(names),
        "ranking": ranking,
        "reasons": {
            n: f"won {won[n]:g} of {played[n]} pairs (Bradley-Terry {strength[n]:+.2f})" for n in names
        },
        "rubric": rubric_version(ws),
        "model": critic_model(ws),
        "method": "pairs",
        "strength": {n: round(s, 3) for n, s in strength.items()},
        "pairs": [[a, b, v["winner"]] for a, b, v in games],
    }
    for place, name in enumerate(ranking, 1):
        flat = (stamps.of_flat(ws, name) or {}).get("design_hash")
        _write(ws, name, [*records(ws, name), entry | {"place": place, "design_hash": flat}])
    return {"ranking": ranking, "reasons": entry["reasons"]}
