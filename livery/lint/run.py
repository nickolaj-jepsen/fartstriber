"""Both lints for one design, as one report."""

import dataclasses
import io
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from livery import body, sponsors, visibility
from livery.config import ConfigError, Workspace, policy
from livery.designs import DesignError, build_livery, kit_name, script
from livery.kit import Kit
from livery.lint.output import LINT_SCALE, Finding, Thresholds, lint_output
from livery.lint.source import lint_source, source_warnings
from livery.model import Element, Livery
from livery.raster import rasterise


def thresholds(policy_file: Path | None = None) -> Thresholds:
    """The lint thresholds, from policy.toml [lint] only: a missing one is an error, not a default."""
    path = policy_file or Workspace().policy_file
    raw = policy(path).lint
    known = [f.name for f in dataclasses.fields(Thresholds)]
    missing = [k for k in known if k not in raw]
    if missing:
        raise ConfigError(f"{path} [lint] is missing {', '.join(missing)}")
    values: dict[str, Any] = {k: tuple(raw[k]) if isinstance(raw[k], list) else raw[k] for k in known}
    return Thresholds(**values)


def lint_design(
    ws: Workspace,
    name: str,
    sets: dict[str, str] | None = None,
    outputs: dict[str, bytes] | None = None,
    built: tuple[Livery, Kit] | None = None,
) -> dict:
    """Source lint first; the script only runs if that passes. Pass `outputs`, and `built` (what
    build_livery returned for them), to reuse a build. The report also lists every waiver, every edge the
    library snapped onto or clear of a near-tangent line, and the roster sponsor variants and race markings
    the design places (`sponsors.placed`)."""
    path = script(ws, name)
    errors = [dict(e, id="design.py") for e in lint_source(path)]
    parts = _island_parts(ws, path)
    warnings = [dict(w, id=f"design.py:{w['line']}") for w in source_warnings(path, parts)]
    waivers: list[dict] = []
    snaps: list[dict] = []
    placed: dict[str, list[str]] = {"sponsors": [], "marks": []}
    if not errors:
        livery, kit = built or build_livery(ws, name, sets)
        outputs = outputs or rasterise(livery, kit, ws)
        paint = np.asarray(Image.open(io.BytesIO(outputs["paint.png"])).convert("RGB"))
        vis = visibility.table(kit, LINT_SCALE, ws.policy.preview)
        found = lint_output(livery, kit, ws, paint, thresholds(ws.policy_file), vis)
        for f in found:
            if f.level != "waived":
                (errors if f.level == "error" else warnings).append(dataclasses.asdict(f))
        waivers = waiver_list(livery.elements, [f for f in found if f.level == "waived"])
        snaps = [{"id": e.id, **s} for e in livery.elements for s in e.snaps]
        placed = sponsors.placed(livery.elements)
    return {
        "design": name,
        "errors": errors,
        "warnings": warnings,
        "waivers": waivers,
        "snaps": snaps,
        **placed,
    }


def _island_parts(ws: Workspace, path: Path) -> dict[str, list[str]]:
    """The island ids the design's kit names as parts, read without running the design."""
    try:
        kit_dir = ws.kits / kit_name(path)
    except (DesignError, SyntaxError):
        return {}
    return body.island_parts(kit_dir) if kit_dir.is_dir() else {}


def waiver_list(elements: list[Element], waived: list[Finding]) -> list[dict]:
    """Each waiver as check, element and reason, with the findings it kept out of the report."""
    out = []
    for e in elements:
        reasons = dict(e.waivers)
        for tag in e.tags:
            if tag.startswith("allow:"):
                check = tag[len("allow:") :]
                hid = [f.message for f in waived if f.id == e.id and f.check == check]
                out.append({"check": check, "id": e.id, "reason": reasons.get(check), "hid": hid})
    return out
