"""The hashes a stored check is trusted by. A build whose design, toolchain and policy hashes match a passed
check needs no re-check; each covers what a build reads, and nothing it doesn't.

- Design hash: the script, its kit's build inputs, and the assets a build can load (fonts, and the SVGs and
  brand.toml, less its approval dates, of the roster's brands, the race markings and the design's own
  brands; not draw.py, roster.md or licences).
- Toolchain hash: the livery modules a build can import, without comments or docstrings, the runtime
  dependencies' versions and Python's.
- Policy hash: policy.toml's [lint], which the lints and the library's minimum sizes read.

A tree's hash is kept for the process, keyed on every file's stat, since check, status and review ask for
the same hashes hundreds of times.
"""

import ast
import hashlib
import json
import os
import sys
import time
import tomllib
from collections.abc import Callable, Iterable
from functools import cache, lru_cache
from importlib import metadata
from pathlib import Path

from livery.config import Workspace
from livery.designs import kit_name, script
from livery.kit import NOT_INPUTS

LIBRARY = Path(__file__).resolve().parent
RUNTIME_DEPS = ("resvg-py", "pillow", "numpy", "scipy", "pyyaml", "shapely", "fonttools", "opencv-python")
# What a build runs: the gate (check, and its fresh-process child) and the library designs draw with.
ENTRY = ("livery.check", "livery.child", "livery.primitives", "livery.geom", "livery.typeset")
ENTRY += ("livery.patterns", "livery.marks", "livery.calibration")
TEXT = frozenset({".py", ".svg", ".toml", ".yaml", ".yml", ".json", ".md", ".txt"})
FONTS = frozenset({".ttf", ".otf"})
# A file written this recently could be rewritten within the same mtime tick (2 s on the coarsest
# filesystems) and stat the same, so a tree holding one is hashed afresh every time.
RACY_NS = 2 * 10**9

_kept: dict[str, tuple[tuple, str]] = {}


def _stamp(files: Iterable[Path]) -> tuple | None:
    """Each file's path, size, times and file id; None if one changed too recently to tell the next change
    from this one. os.stat, not the directory listing's: NTFS updates a listing's entry lazily."""
    since = time.time_ns() - RACY_NS
    out = []
    for p in files:
        st = os.stat(p)
        if st.st_mtime_ns >= since:
            return None
        out.append((str(p), st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino))
    return tuple(out)


def _kept_or(key: str, files: list[Path], compute: Callable[[], str]) -> str:
    """`compute()`, or what it returned last for `key` while every one of `files` stats the same."""
    stamp = _stamp(sorted(files))
    if stamp is None:
        return compute()
    hit = _kept.get(key)
    if hit is None or hit[0] != stamp:
        hit = _kept[key] = (stamp, compute())
    return hit[1]


def _files(root: Path, skip: frozenset[str] = frozenset()) -> list[Path]:
    """Every file under root, not following links into directories, without descending into `skip`."""
    out, todo = [], [root]
    while todo:
        try:
            entries = list(os.scandir(todo.pop()))
        except (FileNotFoundError, NotADirectoryError):
            continue
        for e in entries:
            if e.is_dir(follow_symlinks=False):
                if e.name not in skip:
                    todo.append(Path(e.path))
            elif e.is_file():
                out.append(Path(e.path))
    return out


@lru_cache(maxsize=256)
def strip_source(src: str) -> str:
    """Source without comments, docstrings or formatting, so editing prose doesn't re-check every design.
    Cached by the source itself: parsing the library takes longer than anything else in a hash."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (
            isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def _bytes(p: Path) -> bytes:
    """A brand's approval dates record the owner's review, not what a build draws, so approving a brand
    doesn't send every design that places it back through check. Text is hashed with LF line ends, so a
    CRLF checkout or editor doesn't either."""
    if p.name == "brand.toml":
        raw = tomllib.loads(p.read_text(encoding="utf-8"))
        for variant in raw.get("variants", {}).values():
            variant.pop("approved", None)
        return json.dumps(raw, sort_keys=True).encode()
    data = p.read_bytes()
    return data.replace(b"\r\n", b"\n") if p.suffix.lower() in TEXT else data


def _sha(root: Path, files) -> str:
    h = hashlib.sha256()
    for p in sorted(files):
        h.update(p.relative_to(root).as_posix().encode() + b"\0" + _bytes(p) + b"\0")
    return h.hexdigest()


def _module_file(name: str) -> Path | None:
    parts = name.split(".")
    if parts[0] != "livery":
        return None
    base = LIBRARY.joinpath(*parts[1:])
    return next((p for p in (base.with_suffix(".py"), base / "__init__.py") if p.is_file()), None)


@lru_cache(maxsize=256)
def _imports(src: str) -> tuple[str, ...]:
    """What a module's source imports by absolute name, and each name it imports from a module."""
    found: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found += [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
    return tuple(found)


def build_modules() -> dict[str, Path]:
    """The livery modules a build can import: the import closure of ENTRY, lazy imports and packages
    included, read from the source."""
    found: dict[str, Path] = {}
    todo = list(ENTRY)
    while todo:
        name = todo.pop()
        path = _module_file(name)
        if name in found or path is None:
            continue
        found[name] = path
        todo += [name.rsplit(".", 1)[0]] if "." in name else []
        todo += _imports(path.read_text(encoding="utf-8"))
    return found


@cache
def _runtime() -> bytes:
    """The runtime dependencies' versions and Python's, which can't change under a running process."""
    return (
        "".join(f"{dep}=={metadata.version(dep)}\0" for dep in RUNTIME_DEPS).encode() + sys.version.encode()
    )


def _toolchain() -> str:
    h = hashlib.sha256()
    for name, p in sorted(build_modules().items()):
        h.update(name.encode() + b"\0" + strip_source(p.read_text(encoding="utf-8")).encode() + b"\0")
    h.update(_runtime())
    return h.hexdigest()


def toolchain_hash() -> str:
    """Kept while no module of the library changes: which ones a build imports is read from them all."""
    sources = [p for p in _files(LIBRARY, frozenset({"__pycache__"})) if p.suffix == ".py"]
    return _kept_or(f"toolchain {LIBRARY}", sources, _toolchain)


def policy_hash(ws: Workspace) -> str:
    return hashlib.sha256(json.dumps(ws.policy.lint, sort_keys=True).encode()).hexdigest()


def kit_files(kit_dir: Path) -> list[Path]:
    """The kit's build inputs: everything but how the sim shows the car and the owner's approval."""
    return [p for p in _files(kit_dir) if p.relative_to(kit_dir).as_posix() not in NOT_INPUTS]


def _tree_hash(root: Path, files: list[Path]) -> str:
    return _kept_or(f"tree {root}", files, lambda: _sha(root, files))


def kit_hash(kit_dir: Path) -> str:
    return _tree_hash(kit_dir, kit_files(kit_dir))


def asset_files(root: Path) -> list[Path]:
    """What a build can load from an assets tree or a design's own brands: fonts, variant SVGs, brand.toml."""
    return [
        p
        for p in _files(root)
        if p.suffix.lower() in FONTS or p.suffix.lower() == ".svg" or p.name == "brand.toml"
    ]


def input_hashes(ws: Workspace, name: str) -> dict[str, str]:
    """The design hash's parts, so a changed build can say what changed it."""
    path = script(ws, name)
    own = ws.design_dir(name) / "sponsors"
    kit = _kept_or(f"kit of {path}", [path], lambda: kit_name(path))  # parsing the script takes longest
    return {
        "script": hashlib.sha256(_bytes(path)).hexdigest(),
        "kit": kit_hash(ws.kits / kit),
        "assets": _tree_hash(ws.assets, asset_files(ws.assets)),
        "design_sponsors": _tree_hash(own, asset_files(own)),
    }


def design_hash(ws: Workspace, name: str) -> str:
    return hashlib.sha256(json.dumps(input_hashes(ws, name), sort_keys=True).encode()).hexdigest()
