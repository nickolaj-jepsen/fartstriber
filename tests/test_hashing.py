import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import replace

from livery import check, hashing, sponsors
from livery.config import REPO
from livery.hashing import strip_source
from tests.conftest import checked, write_design

CODE = '''
def f(x):
    """Doubles."""
    return x * 2  # twice
'''

PLAIN = 'def build(kit, p):\n    return Livery(base="#202020")\n'


def test_prose_changes_do_not_change_the_library_hash():
    reworded = CODE.replace("Doubles.", "Multiplies by two.").replace("# twice", "# 2x")
    assert strip_source(reworded) == strip_source(CODE)


def test_code_changes_do():
    assert strip_source(CODE.replace("x * 2", "x * 3")) != strip_source(CODE)


def test_only_code_a_build_can_import_is_in_the_toolchain_hash(tmp_path, monkeypatch):
    lib = tmp_path / "livery"
    shutil.copytree(hashing.LIBRARY, lib, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(hashing, "LIBRARY", lib)
    before = hashing.toolchain_hash()

    for name in ("cli.py", "critic.py", "simsession.py", "review/server.py"):
        (lib / name).write_text((lib / name).read_text(encoding="utf-8") + "\nX = 1\n", encoding="utf-8")
    assert hashing.toolchain_hash() == before

    (lib / "raster.py").write_text((lib / "raster.py").read_text(encoding="utf-8") + "\nX = 1\n")
    assert hashing.toolchain_hash() != before


IMPORTED = """
import json, sys
from pathlib import Path
import livery.geom, livery.marks, livery.patterns, livery.primitives, livery.typeset  # what designs import
from livery.check import digests
from livery.config import Workspace
from livery.designs import render_outputs
from livery.lint.run import lint_design
ws = Workspace(Path(sys.argv[1]))
digests(render_outputs(ws, "d"))
lint_design(ws, "d")
print(json.dumps(sorted(m for m in sys.modules if m == "livery" or m.startswith("livery."))))
"""


def test_a_build_imports_nothing_outside_the_toolchain_hash(ws):
    write_design(ws, "d", PLAIN)
    r = subprocess.run(
        [sys.executable, "-c", IMPORTED, str(ws.root)], capture_output=True, text=True, check=True
    )
    assert set(json.loads(r.stdout.splitlines()[-1])) <= set(hashing.build_modules())


def test_a_lint_threshold_change_sends_a_design_back_to_check(ws, tmp_path):
    policy = tmp_path / "policy.toml"
    shutil.copy(REPO / "policy.toml", policy)
    ws = replace(ws, policy_file=policy)
    checked(ws, "d", PLAIN)
    assert check.current(ws, "d")

    text = policy.read_text()
    policy.write_text(re.sub(r"min_text_px = (\d+)", lambda m: f"min_text_px = {int(m[1]) + 6}", text))
    assert check.current(ws, "d") is None


def test_a_designs_own_brand_is_a_build_input_and_its_drawing_script_is_not(ws):
    brand = ws.design_dir("d") / "sponsors" / "acme"
    sponsors.save(brand, {"mark": sponsors.path_svg("M0,0 h100 v100 h-100 Z", (0, 0, 100, 100))})
    (brand / "draw.py").write_text("# draws acme\n")
    checked(ws, "d", PLAIN)

    (brand / "draw.py").write_text("# draws acme, rounder\n")
    assert check.current(ws, "d")

    sponsors.save(brand, {"mark": sponsors.path_svg("M0,0 h100 v90 h-100 Z", (0, 0, 100, 90))})
    assert check.current(ws, "d") is None


def test_only_what_a_build_can_load_from_the_assets_is_hashed(ws, tmp_path):
    assets = tmp_path / "assets"
    brand = assets / "sponsors" / "acme"
    sponsors.save(brand, {"mark": sponsors.path_svg("M0,0 h100 v100 h-100 Z", (0, 0, 100, 100))})
    (brand / "draw.py").write_text("# draws acme\n")
    (assets / "sponsors" / "roster.md").write_text("| Brand |\n")
    (assets / "fonts" / "f").mkdir(parents=True)
    (assets / "fonts" / "f" / "F.ttf").write_bytes(b"glyphs")
    (assets / "fonts" / "f" / "OFL.txt").write_text("licence\n")
    ws = replace(ws, assets=assets)
    write_design(ws, "d", PLAIN)
    before = hashing.design_hash(ws, "d")

    for p in (brand / "draw.py", assets / "sponsors" / "roster.md", assets / "fonts" / "f" / "OFL.txt"):
        p.write_text(p.read_text() + "more\n")
    assert hashing.design_hash(ws, "d") == before

    (assets / "fonts" / "f" / "F.ttf").write_bytes(b"other glyphs")
    assert hashing.design_hash(ws, "d") != before


def test_an_edit_changes_the_hash_in_the_same_process_however_soon_it_follows(ws, tmp_path):
    font = tmp_path / "assets" / "fonts" / "F.ttf"
    font.parent.mkdir(parents=True)
    ws = replace(ws, assets=tmp_path / "assets")
    write_design(ws, "d", PLAIN)
    now = time.time_ns()
    seen = []
    # One size, so only the content tells them apart: an old file, a fresh one, then two writes in one
    # mtime tick.
    for glyphs, mtime in (
        (b"glyphs-a", now - 3600 * 10**9),
        (b"glyphs-b", None),
        (b"glyphs-c", now),
        (b"glyphs-d", now),
    ):
        font.write_bytes(glyphs)
        if mtime is not None:
            os.utime(font, ns=(mtime, mtime))
        seen.append(hashing.design_hash(ws, "d"))
        assert hashing.design_hash(ws, "d") == seen[-1]
    assert len(set(seen)) == len(seen)
