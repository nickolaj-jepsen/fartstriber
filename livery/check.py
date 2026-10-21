"""The gate: a design passes both lints and builds byte-identically across processes."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from livery import hashing
from livery.config import Workspace
from livery.designs import build_livery, render_outputs, script
from livery.errors import LiveryError
from livery.lint.run import lint_design
from livery.lint.source import lint_source
from livery.raster import rasterise


def digests(outputs: dict[str, bytes]) -> dict[str, str]:
    return {k: hashlib.sha256(v).hexdigest() for k, v in sorted(outputs.items())}


def _child_seed() -> str:
    """A hash seed other than this process's, so set and dict iteration order can differ."""
    mine = os.environ.get("PYTHONHASHSEED")
    return str(int(mine) + 1) if mine and mine.isdigit() else "4242"


@contextmanager
def fresh_process_build(ws: Workspace, name: str) -> Iterator[Callable[[], dict[str, str]]]:
    """Starts the build in a fresh interpreter and gives what waits for its digests, so it runs while this
    process builds and lints. Leaving early stops it."""
    env = os.environ | {"PYTHONHASHSEED": _child_seed(), "PYTHONUTF8": "1"}
    with tempfile.TemporaryDirectory() as tmp:
        out, log = Path(tmp) / "digests.json", Path(tmp) / "log.txt"
        cmd = [sys.executable, "-m", "livery.child", "--root", str(ws.root), "--assets", str(ws.assets)]
        cmd += ["--out", str(out), name]
        with open(log, "wb") as f:  # a file, not a pipe: a design that prints a lot can't stall it
            child = subprocess.Popen(cmd, env=env, stdout=f, stderr=f)

        def result() -> dict[str, str]:
            if child.wait(timeout=600) != 0:
                printed = log.read_text(encoding="utf-8", errors="replace").strip()
                raise RuntimeError(f"fresh-process build failed:\n{printed}")
            return json.loads(out.read_text(encoding="utf-8"))

        try:
            yield result
        finally:
            if child.poll() is None:
                child.kill()
            child.wait()


def _compare(errors: list[str], a: dict[str, str], b: dict[str, str], what: str) -> None:
    for k in sorted(a.keys() | b.keys()):
        if a.get(k) != b.get(k):
            errors.append(f"{k} differs {what}")


def run_check(ws: Workspace, name: str) -> tuple[dict, dict[str, bytes] | None]:
    """Writes out/check.json; returns it and the first build's outputs (None if the build failed)."""
    result = {
        "design": name,
        "design_hash": None,
        "toolchain_hash": None,
        "policy_hash": None,
        "passed": False,
        "errors": [],
        "warnings": [],
        "waivers": [],
        "snaps": [],
        "sponsors": [],  # the roster variants and race markings it places, which pinning it needs approved
        "marks": [],
    }
    errors: list[str] = result["errors"]
    outputs = None
    # The design lint reads the script without running it, and passes only a script that can be hashed.
    errors += [f"design.py:{e['line']}: {e['message']}" for e in lint_source(script(ws, name))]
    if not errors:
        result["design_hash"] = hashing.design_hash(ws, name)
        result["toolchain_hash"] = hashing.toolchain_hash()
        result["policy_hash"] = hashing.policy_hash(ws)
        try:
            with fresh_process_build(ws, name) as fresh:
                built = build_livery(ws, name)
                outputs = rasterise(*built, ws)
                first = digests(outputs)
                _compare(
                    errors, first, digests(render_outputs(ws, name)), "between two builds in one process"
                )
                report = lint_design(ws, name, outputs=outputs, built=built)
                _compare(errors, first, fresh(), "in a fresh process with another hash seed")
            result["digests"] = first
            errors += [f"{e['check']} {e['id']}: {e['message']}" for e in report["errors"]]
            result["warnings"] = [f"{w['check']} {w['id']}: {w['message']}" for w in report["warnings"]]
            result["waivers"] = report["waivers"]
            result["snaps"] = report["snaps"]
            result["sponsors"], result["marks"] = report["sponsors"], report["marks"]
        except Exception as e:
            errors.append(f"build failed: {type(e).__name__}: {e}")
    result["passed"] = not errors
    out = ws.out(name)
    out.mkdir(parents=True, exist_ok=True)
    (out / "check.json").write_text(json.dumps(result, indent=1), encoding="utf-8", newline="\n")
    return result, outputs if result["passed"] else None


def current(ws: Workspace, name: str) -> dict | None:
    """The stored check, if it passed and still matches the design, toolchain and policy."""
    p = ws.out(name) / "check.json"
    if not p.exists() or lint_source(script(ws, name)):
        return None
    stored = json.loads(p.read_text(encoding="utf-8"))
    ok = (
        stored.get("passed")
        and stored.get("design_hash") == hashing.design_hash(ws, name)
        and stored.get("toolchain_hash") == hashing.toolchain_hash()
        and stored.get("policy_hash") == hashing.policy_hash(ws)
    )
    return stored if ok else None


def run_build(ws: Workspace, name: str) -> dict:
    """Check (unless a matching check already passed), then write the outputs. Writes nothing on failure."""
    result = current(ws, name)
    if result is None:
        result, outputs = run_check(ws, name)
        if outputs is None:
            return result
    else:
        outputs = render_outputs(ws, name)
        if digests(outputs) != result["digests"]:
            raise NotChecked(f"{name}'s build differs from its passed check; run livery check {name}")
    out = ws.out(name)
    for fname, data in outputs.items():
        (out / fname).write_bytes(data)
    return result


class NotChecked(LiveryError, RuntimeError):
    pass


def checked_build(ws: Workspace, name: str) -> tuple[Path, dict]:
    """The build directory and its check, if the build on disk is exactly what passed check."""
    stored = current(ws, name)
    if stored is None:
        raise NotChecked(
            f"{name} has no passed check for its current design and toolchain; run livery build {name}"
        )
    out = ws.out(name)
    for fname in ("car.tga", "car_spec.tga"):
        f = out / fname
        if not f.exists() or hashlib.sha256(f.read_bytes()).hexdigest() != stored["digests"].get(fname):
            raise NotChecked(f"{f} is missing or not what passed check; run livery build {name}")
    return out, stored
