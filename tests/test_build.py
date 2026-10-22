import json

import pytest
from PIL import Image

from livery import check
from livery.check import run_build, run_check
from tests.conftest import SPEC, checked, write_design

STEADY = """
def build(kit, p):
    return Livery(base="#202020").add(
        svg('<rect x="4" y="4" width="16" height="16" fill="#3060c0"/>', id="panel", material="metallic"),
        svg('<text x="2" y="60" font-family="Barlow" font-size="10" fill="#ffffff">AB</text>', id="label"),
    )
"""

# Iterating a set of strings follows the process's hash seed, so element order varies across processes.
# Built inside build(), where the design lint allows it: only the fresh-process build can catch it.
HASH_ORDER = """
def build(kit, p):
    names = {"alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet"}
    liv = Livery(base="#000000")
    for i, name in enumerate(names):
        liv.add(svg(f'<rect x="{i * 2}" y="0" width="2" height="8" fill="#{i * 20:02x}0000"/>', id=name))
    return liv
"""

BUILD_FILES = ("layers.svg", "spec.svg", "paint.png", "spec.png", "car.tga", "car_spec.tga")


def test_steady_design_passes_check_and_builds_24_bit_tgas(ws):
    write_design(ws, "steady", STEADY)
    result = run_build(ws, "steady")
    assert result["passed"], result["errors"]
    out = ws.out("steady")
    stored = json.loads((out / "check.json").read_text())
    assert check.current(ws, "steady") == stored
    for name in ("car.tga", "car_spec.tga"):
        raw = (out / name).read_bytes()
        assert raw[2] == 2
        assert raw[16] == 24
        assert Image.open(out / name).size == (64, 64)
    spec = Image.open(out / "car_spec.tga").convert("RGB")
    assert spec.getpixel((10, 10)) == tuple(SPEC["metallic"])
    assert spec.getpixel((28, 40)) == tuple(SPEC["gloss"])


def test_hash_order_design_fails_check_and_build_writes_nothing(ws):
    write_design(ws, "drift", HASH_ORDER)
    result = run_build(ws, "drift")
    assert not result["passed"]
    assert any("fresh process" in e for e in result["errors"])
    for name in BUILD_FILES:
        assert not (ws.out("drift") / name).exists()


def test_broken_design_fails_check(ws):
    write_design(ws, "broken", "def build(kit, p):\n    return 42\n")
    result, outputs = run_check(ws, "broken")
    assert not result["passed"]
    assert outputs is None


@pytest.mark.parametrize(
    "source",
    [
        'KIT = "tiny"\n\ndef build(kit, p)\n    return None\n',  # a syntax error
        'from livery.model import Livery\n\n\ndef build(kit, p):\n    return Livery(base="#000000")\n',  # no KIT
    ],
)
def test_a_script_that_cannot_be_hashed_fails_check_and_build(ws, source):
    ws.design_dir("bad").mkdir(parents=True)
    (ws.design_dir("bad") / "design.py").write_text(source, encoding="utf-8")
    result, outputs = run_check(ws, "bad")
    assert not result["passed"]
    assert result["errors"]
    assert outputs is None
    assert not run_build(ws, "bad")["passed"]
    with pytest.raises(check.NotChecked):
        check.checked_build(ws, "bad")


def test_build_reuses_a_matching_passed_check(ws, monkeypatch):
    checked(ws, "steady", STEADY, render=True)

    def no_recheck(*_):
        raise AssertionError("re-checked although hashes matched")

    monkeypatch.setattr(check, "run_check", no_recheck)
    assert run_build(ws, "steady")["passed"]

    write_design(ws, "steady", STEADY + "\n# edited\n")
    with pytest.raises(AssertionError):
        run_build(ws, "steady")


def test_approving_a_kit_keeps_its_designs_checked(ws):
    from livery import kit as kits
    from livery.designs import kit_name, script
    from livery.hashing import design_hash

    write_design(ws, "steady", STEADY)
    kit_dir = ws.kits / kit_name(script(ws, "steady"))
    before = design_hash(ws, "steady")

    kits.approve(kit_dir, "Tester", "")
    assert design_hash(ws, "steady") == before

    meta = kits.read_meta(kit_dir)
    kits.write(kit_dir, meta=meta | {"bleed_px": meta["bleed_px"] + 1})
    assert design_hash(ws, "steady") != before
    assert kits.approval(kit_dir) is None  # the approval was of the kit as it was
