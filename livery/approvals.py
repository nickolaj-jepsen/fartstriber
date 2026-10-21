"""Approvals pinned to the build the owner saw.

Accepting a final in `livery review` writes `designs/<name>/approved.json` with that build's digests and
inputs, and keeps its pictures in `.cache/approved/<name>/<digest>/`. Install needs the build on disk to be
the approved one; `livery build --all` reports every design whose build has moved since its approval, and
what moved it. Neither pins nor installs a build that places a roster sponsor variant or a race marking
the owner hasn't approved.
"""

import json
import shutil
import time
from pathlib import Path

import numpy as np
from PIL import Image

from livery import config, hashing, marks, sponsors, stamps
from livery.check import checked_build, run_build
from livery.config import Workspace
from livery.designs import kit_name, role, script
from livery.errors import LiveryError
from livery.kit import load, require_approved

INSTALLED = ("car.tga", "car_spec.tga")  # what reaches the car; the other outputs follow from them
PICTURES = ("paint.png", "spec.png", "flat/views.png", "flat/car.png")
TOLERANCE = 8  # a channel moving more than this is a change, not antialiasing or rounding


class NotApproved(LiveryError, RuntimeError):
    pass


def _path(ws: Workspace, name: str) -> Path:
    return ws.design_dir(name) / "approved.json"


def load_approval(ws: Workspace, name: str) -> dict | None:
    p = _path(ws, name)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _key(digests: dict[str, str]) -> str:
    return digests["car.tga"][:12]


def _pictures(ws: Workspace, name: str, digests: dict[str, str]) -> Path:
    return ws.root / ".cache" / "approved" / name / _key(digests)


def unapproved(ws: Workspace, check: dict) -> list[str]:
    """The roster sponsor variants and race markings a checked build places that the owner hasn't approved,
    each with the review that approves it. Read from their folders now, so an approval counts at once."""
    left = []
    for kind, load_brand, root in (("sponsors", sponsors.load, ws.sponsors), ("marks", marks.load, ws.marks)):
        for placed in check.get(kind, []):
            brand_id, _, variant = placed.partition("/")
            try:
                brand = load_brand(brand_id, root)
            except sponsors.SponsorError:
                left.append(f"{placed} (no longer drawn)")
                continue
            v = brand.variants.get(variant)
            if v is None or not v.approved:
                left.append(f"{placed} (livery review {brand.review})")
    return left


def pin_of(ws: Workspace, name: str) -> dict:
    """What a final review shows: the checked build on disk, by digest, with each lint check its elements
    waive and why, and the roster sponsor variants and race markings it places. Raises NotChecked
    otherwise."""
    _, stored = checked_build(ws, name)
    return {
        "design_hash": stored["design_hash"],
        "toolchain_hash": stored["toolchain_hash"],
        "policy_hash": stored.get("policy_hash"),
        "inputs": hashing.input_hashes(ws, name),
        "digests": stored["digests"],
        "waivers": stored.get("waivers", []),
        "sponsors": stored.get("sponsors", []),
        "marks": stored.get("marks", []),
    }


def pin(ws: Workspace, name: str, shown: dict, note: str) -> str:
    """Records the owner's accept of the build they were shown, if it is still the build on disk, the flat
    pictures kept with it show that build, and everything it places from the roster and the race markings
    is approved."""
    try:
        now = pin_of(ws, name)
    except LiveryError as e:
        return f"{name}: not pinned, {e}"
    if now["digests"] != shown["digests"]:
        return f"{name}: not pinned, the build changed while it was under review; review it again"
    left = unapproved(ws, now)
    if left:
        return (
            f"{name}: not pinned, it places {', '.join(left)}, not approved yet: approve them, then review it"
        )
    if not stamps.shows(stamps.of_flat(ws, name), shown["digests"]):
        return f"{name}: not pinned, its flat preview shows another build; review it again"
    record = {"by": config.owner(), "date": time.strftime("%Y-%m-%d"), "note": note, **shown}
    keep = _pictures(ws, name, shown["digests"])
    keep.mkdir(parents=True, exist_ok=True)
    for rel in PICTURES:
        src = ws.out(name) / rel
        if src.exists():
            shutil.copy2(src, keep / Path(rel).name)
    _path(ws, name).write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8", newline="\n")
    return f"designs/{name}/approved.json: pinned {_key(shown['digests'])}"


def require(ws: Workspace, name: str, check: dict) -> dict:
    """The approval of exactly this checked build on an approved kit, with everything it places from the
    roster and the race markings approved too; or NotApproved (KitNotApproved)."""
    require_approved(ws, kit_name(script(ws, name)))
    left = unapproved(ws, check)
    if left:
        raise NotApproved(
            f"{name} places {', '.join(left)}, not approved yet: approve them, or install --unapproved"
        )
    a = load_approval(ws, name)
    if a is None:
        raise NotApproved(f"{name} has no approval: livery review {name}, or install --unapproved")
    digests = check["digests"]
    if any(a["digests"].get(k) != digests.get(k) for k in INSTALLED):
        raise NotApproved(
            f"{name}'s build is not the one {config.owner()} approved on {a['date']} (livery build --all "
            f"shows the difference): livery review {name}, or install --unapproved"
        )
    return a


def _changed(before: np.ndarray, after: np.ndarray) -> np.ndarray:
    return np.abs(before.astype(np.int16) - after.astype(np.int16)).max(axis=2) > TOLERANCE


def _diff_picture(before: np.ndarray, after: np.ndarray, changed: np.ndarray, out: Path) -> None:
    """Approved, now, and now dimmed with the changed pixels in magenta, side by side at half size."""
    marked = (after * 0.35).astype(np.uint8)
    marked[changed] = (255, 0, 255)
    h, w = after.shape[:2]
    tiles = [Image.fromarray(a).resize((w // 2, h // 2), Image.LANCZOS) for a in (before, after)]
    tiles.append(Image.fromarray(marked).resize((w // 2, h // 2), Image.NEAREST))
    sheet = Image.new("RGB", (3 * (w // 2), h // 2))
    for i, t in enumerate(tiles):
        sheet.paste(t, (i * (w // 2), 0))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)


def compare(ws: Workspace, name: str, digests: dict[str, str]) -> dict:
    """How the build with `digests` stands against the design's approval."""
    a = load_approval(ws, name)
    if a is None:
        return {"status": "unapproved"}
    if all(a["digests"].get(k) == digests.get(k) for k in INSTALLED):
        return {"status": "approved", "approved": a["date"]}
    now = hashing.input_hashes(ws, name)
    moved = [k for k, v in now.items() if a.get("inputs", {}).get(k) != v]
    if a.get("toolchain_hash") != hashing.toolchain_hash():
        moved.append("toolchain")
    if a.get("policy_hash") != hashing.policy_hash(ws):
        moved.append("policy")
    result = {"status": "changed", "approved": a["date"], "changed_by": moved}
    kept = _pictures(ws, name, a["digests"])
    kit = load(ws, kit_name(script(ws, name)))
    car = np.asarray(Image.open(kit.mask_path).convert("L")) > 127 if kit.mask_path.exists() else None
    for picture in ("paint.png", "spec.png"):
        old, new = kept / picture, ws.out(name) / picture
        if not (old.exists() and new.exists()):
            result["pictures"] = "the approved pictures are gone from .cache; only the digests are compared"
            continue
        before = np.asarray(Image.open(old).convert("RGB"))
        after = np.asarray(Image.open(new).convert("RGB"))
        changed = _changed(before, after)
        area = car if car is not None and car.shape == changed.shape else np.ones_like(changed)
        result[f"{picture.split('.')[0]}_changed"] = round(float(changed[area].mean()), 4)
        result[f"{picture.split('.')[0]}_moved_px"] = int(changed[area].sum())
        if picture == "paint.png":
            diff = ws.out(name) / "diff" / "approved-vs-now.png"
            _diff_picture(before, after, changed & area, diff)
            result["diff"] = diff.as_posix()
    return result


def standings(ws: Workspace) -> dict[str, dict]:
    """Builds every design and says how each stands: a livery against its approval; a fixture the same
    way when it has one, else only whether it built. A build that places roster sponsor variants or race
    markings not yet approved lists them, since its final review can't pin it until they are."""
    report = {}
    for name in sorted(p.parent.name for p in ws.designs.glob("*/design.py")):
        row: dict = {"role": role(script(ws, name))}
        try:
            result = run_build(ws, name)
        except LiveryError as e:
            result = {"passed": False, "errors": [str(e)]}
        if not result["passed"]:
            row |= {"status": "failed", "errors": result["errors"]}
        else:
            row |= compare(ws, name, result["digests"])
            if row["role"] == "fixture" and row["status"] == "unapproved":
                row["status"] = "built"
            left = unapproved(ws, result)
            if left:
                row["needs_approval"] = left
        report[name] = row
    return report


CARRIED_BY = {
    "kit",
    "assets",
    "toolchain",
    "policy",
}  # a library or kit change; the design's own files aren't


def carries(row: dict) -> bool:
    """Whether a design `standings` reports as changed keeps its approval: only a library or kit change moved
    it, no channel of any car pixel in its paint or spec moved more than TOLERANCE, and it places nothing
    unapproved."""
    return (
        row.get("status") == "changed"
        and set(row.get("changed_by") or ["?"]) <= CARRIED_BY
        and row.get("paint_moved_px") == 0
        and row.get("spec_moved_px") == 0
        and not row.get("needs_approval")
    )


def carry(ws: Workspace, name: str) -> tuple[dict, dict]:
    """Moves the design's approval onto its checked build on disk, keeping who approved it, when and their
    note; the approval before and after."""
    old = load_approval(ws, name)
    if old is None:
        raise NotApproved(f"{name} has no approval to carry")
    record = old | pin_of(ws, name)
    keep = _pictures(ws, name, record["digests"])
    keep.mkdir(parents=True, exist_ok=True)
    fresh = stamps.shows(stamps.of_flat(ws, name), record["digests"])
    for rel in PICTURES:
        src = ws.out(name) / rel
        if src.exists() and (fresh or not rel.startswith("flat/")):
            shutil.copy2(src, keep / Path(rel).name)
    _path(ws, name).write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8", newline="\n")
    return old, record


def failing(report: dict[str, dict]) -> list[str]:
    """The designs that fail `build --all`: those that failed, and approved ones whose build moved."""
    return [name for name, row in report.items() if row["status"] in ("failed", "changed")]
