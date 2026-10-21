"""The design lint: what design.py may import, call and keep at module level, read without running it."""

import ast
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

ALLOWED_IMPORTS = ("math", "dataclasses", "typing", "itertools", "numpy", "shapely", "livery")
BANNED_MODULES = ("random", "numpy.random", "time", "datetime")
BANNED_CALLS = ("hash", "id", "open", "eval", "exec", "__import__", "compile", "globals", "vars", "input")
IMMUTABLE_CALLS = ("frozenset", "tuple", "str", "int", "float", "bool")
# Reading back what a primitive drew ties a design to how the library serialises SVG and builds elements:
# a library change then breaks it silently. Warnings, so a design can still be built while it moves off.
SVG_TEXT_METHODS = ("index", "replace")
ISLAND = re.compile(r"i\d{3}")


def _names(parts: Sequence[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def literal(tree: ast.Module, name: str) -> str | None:
    """A module-level `NAME = "..."`."""
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and [getattr(t, "id", None) for t in node.targets] == [name]
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return node.value.value
    return None


def _allowed(module: str) -> bool:
    return not any(module == b or module.startswith(b + ".") for b in BANNED_MODULES) and any(
        module == a or module.startswith(a + ".") for a in ALLOWED_IMPORTS
    )


def _mutable(value: ast.expr) -> bool:
    """A module-level value that could be changed and carry state from one build to the next."""
    if isinstance(value, ast.List | ast.Dict | ast.Set | ast.ListComp | ast.DictComp | ast.SetComp):
        return True
    if isinstance(value, ast.Call):
        name = value.func.id if isinstance(value.func, ast.Name) else getattr(value.func, "attr", "")
        return name not in IMMUTABLE_CALLS
    if isinstance(value, ast.Tuple):
        return any(_mutable(e) for e in value.elts)
    return False


def findings(path: Path, parts: Mapping[str, Sequence[str]] | None = None) -> list[dict]:
    """Errors and warnings in a design script, each with a level, a line number and a message. `parts`
    maps each island id the kit's body model names to its parts, so a literal id there is named."""
    found: list[dict] = []

    def add(level: str, node: ast.AST, msg: str) -> None:
        found.append({"level": level, "check": "design", "line": getattr(node, "lineno", 0), "message": msg})

    def err(node: ast.AST, msg: str) -> None:
        add("error", node, msg)

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as e:
        return [
            {"level": "error", "check": "design", "line": e.lineno or 0, "message": f"syntax error: {e.msg}"}
        ]

    aliases: dict[str, str] = {}
    flagged_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                aliases[a.asname or a.name.split(".")[0]] = a.name
                if not _allowed(a.name):
                    err(
                        node,
                        f"import {a.name} is not allowed; designs may import {', '.join(ALLOWED_IMPORTS)}",
                    )
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for a in node.names:
                full = f"{mod}.{a.name}"
                if not _allowed(mod) or (not _allowed(full) and full.split(".")[0] != "livery"):
                    err(node, f"from {mod} import {a.name} is not allowed")
                elif any(full == b for b in BANNED_MODULES):
                    err(node, f"from {mod} import {a.name} is not allowed; use p.rng(name) for randomness")
        elif isinstance(node, ast.Attribute):
            chain = []
            n: ast.expr = node
            while isinstance(n, ast.Attribute):
                chain.append(n.attr)
                n = n.value
            if isinstance(n, ast.Name):
                dotted = ".".join([aliases.get(n.id, n.id), *reversed(chain)])
                banned = any(dotted == b or dotted.startswith(b + ".") for b in BANNED_MODULES)
                if banned and node.lineno not in flagged_lines:  # np.random.rand also contains np.random
                    flagged_lines.add(node.lineno)
                    err(node, f"{dotted} is not allowed; use p.rng(name) for randomness")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in BANNED_CALLS:
            err(node, f"{node.func.id}() is not allowed in a design")
        elif isinstance(node, ast.Global | ast.Nonlocal):
            err(node, "global and nonlocal are not allowed: a design keeps no state")
        if (
            parts
            and isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and ISLAND.fullmatch(node.value)
            and node.value in parts
        ):
            names = list(parts[node.value])
            what = f"the part {names[0]}" if len(names) == 1 else f"in the parts {_names(names)}"
            add(
                "warning",
                node,
                f'"{node.value}" is {what} of the kit: name the part, which stays right when the kit is '
                "refitted or relabelled",
            )
        if isinstance(node, ast.Attribute) and node.attr == "__dict__":
            add("warning", node, "__dict__ copies an element's fields: use dataclasses.replace")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in SVG_TEXT_METHODS
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "svg"
        ):
            add(
                "warning",
                node,
                f".svg.{node.func.attr}() edits the SVG a primitive drew: draw it with the primitive's "
                "parameters, livery.geom or livery.typeset instead",
            )

    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        if targets and _mutable(value):
            names = ", ".join(t.id for t in targets if isinstance(t, ast.Name))
            err(node, f"module-level {names} is mutable; use a tuple, a constant, or build it inside build()")

    if literal(tree, "KIT") is None:
        err(tree, 'a design needs a module-level KIT = "<kit name>"')
    if not any(isinstance(n, ast.FunctionDef) and n.name == "build" for n in tree.body):
        err(tree, "a design needs def build(kit, p)")
    return sorted(found, key=lambda e: e["line"])


def lint_source(path: Path) -> list[dict]:
    """The errors in a design script: a script with any is never hashed or run."""
    return [{k: v for k, v in f.items() if k != "level"} for f in findings(path) if f["level"] == "error"]


def source_warnings(path: Path, parts: Mapping[str, Sequence[str]] | None = None) -> list[dict]:
    return [
        {k: v for k, v in f.items() if k != "level"} for f in findings(path, parts) if f["level"] == "warning"
    ]
