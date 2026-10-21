"""Loading and building designs/<name>/design.py."""

import ast
import contextlib
import importlib.util
import sys
from pathlib import Path

from livery import kit as kits
from livery import sponsors
from livery.config import Workspace
from livery.errors import LiveryError, UsageError
from livery.lint.source import literal
from livery.model import Livery
from livery.params import DesignParams, with_sets
from livery.raster import rasterise


class DesignNotFound(UsageError):
    pass


class DesignError(LiveryError):
    """design.py can't be run as a design."""


def script(ws: Workspace, name: str) -> Path:
    p = ws.design_dir(name) / "design.py"
    if not p.exists():
        raise DesignNotFound(f"no design at {p}")
    return p


def _literal(path: Path, name: str) -> str | None:
    return literal(ast.parse(path.read_text(encoding="utf-8")), name)


def kit_name(path: Path) -> str:
    """The design's `KIT = "..."`, read without running the script."""
    kit = _literal(path, "KIT")
    if kit is None:
        raise DesignError(f'{path} needs a module-level KIT = "<kit name>"')
    return kit


def role(path: Path) -> str:
    """`ROLE = "fixture"` marks a design that checks a kit or the library and is never raced; anything else
    is a livery."""
    try:
        return "fixture" if _literal(path, "ROLE") == "fixture" else "livery"
    except SyntaxError:
        return "livery"


def build_livery(ws: Workspace, name: str, sets: dict[str, str] | None = None) -> tuple[Livery, kits.Kit]:
    """Runs the script in a fresh module every time, so state can't leak between builds.

    `sets` overrides params by name (`--set`, wedges, seeds) without touching the script. What the script
    prints goes to stderr, so it can't mix with a command's output.
    """
    path = script(ws, name)
    spec = importlib.util.spec_from_file_location(f"livery_design_{name.replace('-', '_')}", path)
    if spec is None or spec.loader is None:
        raise DesignError(f"{path}: not a loadable Python file")
    module = importlib.util.module_from_spec(spec)
    with contextlib.redirect_stdout(sys.stderr):
        try:
            # Compiled from the source, never from __pycache__: a cached .pyc is trusted by the source's
            # size and mtime in whole seconds, so an edit that keeps the length could run the old script.
            exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
        except SyntaxError as e:
            raise DesignError(f"{path}:{e.lineno}: syntax error: {e.msg}") from None
        kit = kits.load(ws, kit_name(path))
        params = module.Params() if hasattr(module, "Params") else DesignParams()
        if not isinstance(params, DesignParams):
            raise DesignError("Params must subclass livery.params.DesignParams")
        with sponsors.designing(ws.design_dir(name), ws.sponsors):
            livery = module.build(kit, with_sets(params, sets or {}))
    if not isinstance(livery, Livery):
        raise DesignError(f"build() returned {type(livery).__name__}, not Livery")
    return livery, kit


def render_outputs(ws: Workspace, name: str, sets: dict[str, str] | None = None) -> dict[str, bytes]:
    livery, kit = build_livery(ws, name, sets)
    return rasterise(livery, kit, ws)
