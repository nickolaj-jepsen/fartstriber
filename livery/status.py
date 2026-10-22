"""`livery status`: where each design stands, read from its files and its build folder without building
anything: whether its check is current, its approval and its kit's, whether its previews show the checked
build, and the critic's last round with the fixes still open."""

import hashlib
import json
from pathlib import Path

import yaml

from livery import approvals, critic, hashing, stamps
from livery.config import Workspace
from livery.designs import kit_name, role, script
from livery.errors import LiveryError

THIS, OTHER, UNKNOWN = "this build", "another build", "build unchecked"


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def kit_approval(ws: Workspace, kit: str) -> dict | None:
    """The kit's approval entry in kit.yaml, or None while a fit waits for the owner."""
    try:
        raw = yaml.safe_load((ws.kits / kit / "kit.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    return raw.get("approved") if isinstance(raw, dict) else None


def _built(ws: Workspace, name: str, digests: dict[str, str]) -> bool:
    """Whether the TGAs on disk are the ones the check passed."""
    files = [ws.out(name) / f for f in stamps.SHOWN]
    return all(
        f.exists() and hashlib.sha256(f.read_bytes()).hexdigest() == digests.get(f.name) for f in files
    )


def _check(ws: Workspace, name: str, stored: dict | None, now: dict[str, str] | None) -> dict:
    """none, failed, stale (with the hashes that moved since it ran) or current; and whether the TGAs on
    disk are the ones it checked."""
    if stored is None:
        return {"state": "none"}
    moved = [k.removesuffix("_hash") for k, v in (now or {}).items() if stored.get(k) != v]
    if now is None:
        moved = ["design"]  # the script can't be hashed: it doesn't parse
    if not stored.get("passed"):
        return {"state": "failed", "errors": stored.get("errors", [])[:3]}
    if moved:
        return {"state": "stale", "moved": moved}
    return {"state": "current", "built": _built(ws, name, stored.get("digests") or {})}


def _approval(ws: Workspace, name: str, digests: dict[str, str] | None) -> dict:
    """Whether the approved build is the checked one; unknown while no current check says what the build is."""
    a = approvals.load_approval(ws, name)
    if a is None:
        return {"state": "none"}
    if digests is None:
        return {"state": UNKNOWN, "date": a.get("date")}
    same = all(a["digests"].get(k) == digests.get(k) for k in approvals.INSTALLED)
    return {"state": THIS if same else OTHER, "date": a.get("date")}


def _previews(ws: Workspace, name: str, digests: dict[str, str] | None, now: dict[str, str] | None) -> dict:
    """Whether flat/ and the sim runs show the checked build; without one, the design as it stands."""
    flat, runs = stamps.of_flat(ws, name), stamps.runs(ws, name)
    if digests:
        flat_state = THIS if stamps.shows(flat, digests) else OTHER
        match = stamps.matching_run(ws, name, digests)
    else:
        flat_state = "this version, unchecked" if stamps.of_version(flat, now) else "an older version"
        match = stamps.latest_of_version(ws, name, now)
    if flat is None:
        flat_state = "unstamped" if (stamps.flat_dir(ws, name) / "views.png").exists() else "none"
    out: dict = {"flat": {"state": flat_state}, "sim": {"state": "none"}}
    if runs:
        out["sim"] = {
            "state": (THIS if digests else "this version, unchecked") if match else OTHER,
            "run": (match or runs[-1]).relative_to(ws.root).as_posix(),
            "runs": len(runs),
        }
    return out


def _critic(ws: Workspace, name: str, design_hash: str | None) -> dict:
    rounds = critic.history(ws, name)
    if not rounds:
        return {"rounds": 0}
    last = rounds[-1]
    answers = last.get("answers") or []
    open_fixes = []
    for i, f in enumerate(critic.fixes(last)):
        if not f["optional"]:
            answer = answers[i].get("status") if i < len(answers) else None
            open_fixes.append({"text": critic.fix_title(f), "answer": answer})
    return {
        "rounds": len(rounds),
        "round": last.get("round", len(rounds)),
        "at": last.get("at"),
        "call": last.get("verdict"),
        "overall": last.get("overall"),
        # None: the round recorded no design hash (it predates critic v2)
        "on_this_version": None if not last.get("design_hash") else last["design_hash"] == design_hash,
        "open_fixes": open_fixes,
        "blocked": last.get("blocked", []),
    }


def design(ws: Workspace, name: str, toolchain: str | None = None, kits: dict | None = None) -> dict:
    path = script(ws, name)
    kits = {} if kits is None else kits
    try:
        now = {
            "design_hash": hashing.design_hash(ws, name),
            "toolchain_hash": toolchain or hashing.toolchain_hash(),
        }
    except (LiveryError, SyntaxError):
        now = None
    stored = _read(ws.out(name) / "check.json")
    check = _check(ws, name, stored, now)
    digests = (stored or {}).get("digests") if check.get("built") else None
    try:
        kit = kit_name(path)
    except LiveryError:
        kit = None
    if kit and kit not in kits:
        kits[kit] = kit_approval(ws, kit)
    return {
        "design": name,
        "role": role(path),
        "kit": {"name": kit, "approved": (kits.get(kit) or {}).get("date")},
        "check": check,
        "approval": _approval(ws, name, digests),
        **_previews(ws, name, digests, now),
        "critic": _critic(ws, name, now["design_hash"] if now else None),
    }


def every(ws: Workspace, names: list[str] | None = None) -> list[dict]:
    """Each named design, or every design, hashing the library and reading each kit once."""
    names = names or sorted(p.parent.name for p in ws.designs.glob("*/design.py"))
    toolchain, kits = hashing.toolchain_hash(), {}
    return [design(ws, n, toolchain, kits) for n in names]


def lines(row: dict) -> list[str]:
    """One design's report as text."""
    name, kit, c, a, sim, cr = (row[k] for k in ("design", "kit", "check", "approval", "sim", "critic"))
    kit_line = f"kit {kit['name']}, " + (f"approved {kit['approved']}" if kit["approved"] else "not approved")
    check = c["state"]
    if c.get("moved"):
        check += f": {' and '.join(c['moved'])} moved since it ran (livery build {name})"
    elif c.get("errors"):
        check += f": {c['errors'][0]}"
    elif c["state"] == "current" and not c["built"]:
        check += f", but the TGAs on disk aren't the checked ones (livery build {name})"
    approval = a["state"] if a["state"] == "none" else f"{a['date']}, {a['state']}"
    sim_line = sim["state"] if sim["state"] == "none" else f"{sim['state']} ({Path(sim['run']).name})"
    if cr["rounds"]:
        on = {True: "this version", False: "an older version", None: "a version it didn't record"}
        critic_line = f"round {cr['round']}, {cr['at']}: {cr['call']} {cr['overall']}/10, on {on[cr['on_this_version']]}"
    else:
        critic_line = "no round yet"
    out = [
        f"{name} ({row['role']}, {kit_line})",
        f"  check     {check}",
        f"  approval  {approval}",
        f"  flat      {row['flat']['state']}",
        f"  sim       {sim_line}",
        f"  critic    {critic_line}",
    ]
    for f in cr.get("open_fixes", []):
        text = f["text"] if len(f["text"]) <= 100 else f["text"][:97] + "..."
        out.append(f"  open fix  {text}" + (f" [{f['answer']}]" if f["answer"] else ""))
    out += [f"  blocked   {b}" for b in cr.get("blocked", [])]
    return out
