"""`livery kit spec`: the kit's finishes, from its calibration capture's grey ladders (docs/plan.md, decision
21).

The sim renders paint under a metal finish darker than under gloss (docs/sim.md, Rendering). For each
preset with metal in it, the build brightens the paint by a lookup measured on the ladders: for each grey a
design asks for, the paint value under that finish whose median output, per colour channel and over the
preview views, matches what gloss shows for that grey. Above what the finish can show at all (its white
matches gloss only up to a grey well short of white), the lookup gives white, and the output lint warns.

`--preset m=R,G,B` sets a preset first and shoots that finish's ladder again into the current capture, so the
bake and the lookup see the finish as it is now.
"""

import numpy as np

from livery import bake, calibrate
from livery import kit as kits
from livery.config import Config, Workspace
from livery.kit import Kit
from livery.model import Material


def compensated(kit: Kit) -> list[Material]:
    """The presets the build compensates: those with metal in them, which the sim darkens."""
    return [m for m, s in kit.spec.items() if s[0] > 0 and m != Material.gloss]


def _responses(run, views: list[str], key: str) -> np.ndarray:
    """(L, 3): per ladder step and channel, the median output over each view's pixels, then over the views."""
    per_view = []
    for view in views:
        _, lad = calibrate.ladders(run, view)
        per_view.append(np.median(lad[key].astype(np.float32), axis=1))
    return np.median(np.stack(per_view), axis=0)


def lookup(levels: list[int], gloss: np.ndarray, finish: np.ndarray) -> dict:
    """Per channel, the paint value under the finish that shows what gloss shows for each ladder grey, and the
    brightest gloss grey the finish can match (its white's output)."""
    lv = np.asarray(levels, np.float64)
    paint, reach = np.zeros((len(lv), 3)), np.zeros(3)
    for c in range(3):
        # Non-decreasing, and strictly so: the darkest steps of a metal sit on the same reflection floor.
        resp = np.maximum.accumulate(finish[:, c].astype(np.float64)) + lv * 1e-6
        g = np.maximum.accumulate(gloss[:, c].astype(np.float64)) + lv * 1e-6
        paint[:, c] = np.interp(np.minimum(g, resp[-1]), resp, lv)
        reach[c] = np.interp(resp[-1], g, lv)
    return {
        "target": [int(x) for x in lv],
        "paint": np.round(paint, 1).tolist(),
        "reachable": np.round(reach, 1).tolist(),
    }


def lookups(run, kit: Kit) -> dict:
    views = list(kits.profile(kit).preview)
    levels, _ = calibrate.ladders(run, views[0])
    manifest = calibrate.state.read_json(run / "manifest.json") or {}

    def key(m: Material) -> str:
        return calibrate.ladder_key(manifest, m, kit.spec[m]) or str(m)

    gloss = _responses(run, views, key(Material.gloss))
    return {str(m): lookup(levels, gloss, _responses(run, views, key(m))) for m in compensated(kit)}


def spec(
    cfg: Config,
    ws: Workspace,
    kit: Kit,
    presets: dict[Material, tuple[int, int, int]] | None = None,
    log=print,
) -> dict:
    """Set any `presets` (shooting their ladders again), then write the compensation lookups into kit.yaml.
    Returns them, with how far each finish reads from gloss in the capture."""
    if presets:
        meta = kits.read_meta(kit.dir)
        meta["spec_presets"] = {**meta["spec_presets"], **{str(m): list(v) for m, v in presets.items()}}
        kits.write(kit.dir, meta=meta)
        kit = kits.load(ws, kit.name)
        calibrate.reshoot(cfg, ws, kit, list(presets), log)
    run = bake.capture(cfg, ws, kit)
    comp = lookups(run, kit)
    meta = kits.read_meta(kit.dir)
    meta.pop("metallic_compensation", None)
    meta["compensation"] = comp
    kits.write(kit.dir, meta=meta)
    manifest = calibrate.state.read_json(run / "manifest.json") or {}
    rows = manifest.get("summary", {}).get("materials", {})
    keys = {str(m): calibrate.ladder_key(manifest, m, s) for m, s in kit.spec.items() if m != Material.gloss}
    finishes = {m: rows.get(k, {}).get("vs_gloss", {}) for m, k in keys.items() if k}
    return {"kit": kit.name, "capture": run.name, "compensation": comp, "vs_gloss": finishes}


def lines(result: dict) -> list[str]:
    out = []
    for m, c in result["compensation"].items():
        paint = ", ".join(
            f"{t}->{'/'.join(str(round(x)) for x in p)}" for t, p in zip(c["target"], c["paint"], strict=True)
        )
        out.append(f"{m}: paint for each grey gloss shows {paint}; shows at most {c['reachable']} (R/G/B)")
    for m, by_view in result["vs_gloss"].items():
        for view, r in by_view.items():
            out.append(
                f"{m} at {view}: vs gloss outside highlights p50/95/99 {r.get('vs_gloss_outside_p50_95_99')}, "
                f"mean {r.get('mean_minus_gloss')} brighter"
            )
    return out
