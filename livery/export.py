"""`livery export`: the approved build under Trading Paints' upload names, for a manual upload.

Trading Paints takes the paint as `car_<customerID>.tga` and the spec map only as the .mip the sim builds
from `car_spec_<customerID>.tga` (help.tradingpaints.com, spec maps). The .mip is the one `livery install`
archived after the approval; an older one was built from a different spec.
"""

import shutil
from datetime import datetime
from pathlib import Path

from livery import approvals
from livery.check import checked_build
from livery.config import Config, Workspace
from livery.designs import kit_name, script
from livery.install import archive_dir
from livery.kit import load
from livery.review.apply import ledger

EXPORT = Path.home() / "Documents" / "Fartstriber" / "export"
UPLOAD = "tradingpaints.com, My Paints"


def _approved_at(ws: Workspace, name: str, approval: dict) -> str:
    """When the owner accepted the approved build, as an archive folder's stamp: the ledger's time, or the
    start of the approval's day for an approval the ledger doesn't hold."""
    accepts = [
        d["at"]
        for d in ledger(ws)
        if d["id"] == f"final:{name}" and d["decision"] == "accept" and d["at"][:10] == approval["date"]
    ]
    if accepts:
        return datetime.fromisoformat(accepts[-1]).astimezone().strftime("%Y%m%d-%H%M%S")
    return approval["date"].replace("-", "") + "-000000"


def _mip(cfg: Config, ws: Workspace, name: str, since: str) -> Path | None:
    """The newest .mip archived for the design since `since`; solo and team .mips come from the same spec."""
    runs = sorted(d for d in (archive_dir(cfg, ws) / name).glob("*") if d.is_dir() and d.name >= since)
    for run in reversed(runs):
        mips = sorted(run.glob("car_spec_*.mip"), key=lambda p: p.name != f"car_spec_{cfg.customer_id}.mip")
        if mips:
            return mips[0]
    return None


def export(
    cfg: Config, ws: Workspace, name: str, out: Path | None = None, unapproved: bool = False
) -> tuple[Path, str]:
    """Copies the checked build's paint and spec TGAs, and its .mip if one was archived, into `out` under
    the upload names. Needs the build the owner approved unless `unapproved`. Returns the folder and what to
    upload."""
    built, stored = checked_build(ws, name)
    try:
        approval = approvals.require(ws, name, stored)
    except approvals.NotApproved:
        if not unapproved:
            raise
        approval = None
    out = out or EXPORT / name
    out.mkdir(parents=True, exist_ok=True)
    cid = cfg.customer_id
    paint, spec_tga, spec_mip = f"car_{cid}.tga", f"car_spec_{cid}.tga", f"car_spec_{cid}.mip"
    shutil.copyfile(built / "car.tga", out / paint)
    shutil.copyfile(built / "car_spec.tga", out / spec_tga)
    mip = _mip(cfg, ws, name, _approved_at(ws, name, approval)) if approval else None
    if mip:
        shutil.copyfile(mip, out / spec_mip)
    else:
        (out / spec_mip).unlink(missing_ok=True)  # one from an earlier export belongs to another build
    car = load(ws, kit_name(script(ws, name))).paint_folder
    if mip:
        steps = f"upload {paint} as the paint and {spec_mip} as its spec map for {car} at {UPLOAD}"
    else:
        why = "the build isn't approved" if approval is None else "none was archived since the approval"
        steps = (
            f"upload {paint} as the paint for {car} at {UPLOAD}; no spec .mip ({why}): Trading Paints takes "
            f"the spec only as a .mip, which `livery install {name}` archives once the sim has loaded it"
        )
    return out, f"{out}: {steps}"
