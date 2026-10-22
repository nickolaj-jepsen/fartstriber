from dataclasses import replace

import numpy as np
import pytest

from livery import kit as kits
from livery import kit_spec
from livery.kit import load
from livery.lint.output import _beyond_reach
from livery.model import Material


def test_a_finish_half_as_bright_as_gloss_needs_twice_the_paint_until_its_white_runs_out():
    levels = [0, 40, 80, 120, 160, 200, 255]
    gloss = np.array([[float(x)] * 3 for x in levels])

    c = kit_spec.lookup(levels, gloss, gloss / 2)

    assert [p[0] for p in c["paint"]] == pytest.approx([0, 80, 160, 240, 255, 255, 255], abs=0.2)
    assert c["reachable"] == pytest.approx([127.5] * 3, abs=0.2)


def test_only_presets_with_metal_in_them_are_brightened(ws):
    spec = {
        Material.gloss: (0, 30, 255),
        Material.matte: (0, 220, 0),
        Material.metallic: (255, 60, 255),
        Material.chrome: (128, 0, 255),
    }
    kit = replace(load(ws, "tiny"), spec=spec)

    assert set(kit_spec.compensated(kit)) == {Material.metallic, Material.chrome}


def test_a_colour_brighter_than_a_finish_shows_is_flagged(ws):
    kit = replace(load(ws, "tiny"), compensation={Material.metallic: {"reachable": [126, 126, 120]}})

    assert _beyond_reach(kit, Material.metallic, {"#7e7e78", "#202020"}) == []
    assert len(_beyond_reach(kit, Material.metallic, {"#7e7e7e", "#ffffff"})) == 2
    assert _beyond_reach(kit, Material.gloss, {"#ffffff"}) == []


def test_a_new_bleed_is_written_and_sends_the_kit_back_to_review(ws):
    kit = load(ws, "tiny")
    assert kits.approval(kit.dir) is not None

    assert kit_spec.write_bleed(kit, kit.bleed_px * 2)["approval"] == "cleared"

    assert load(ws, "tiny").bleed_px == kit.bleed_px * 2
    assert kits.approval(kit.dir) is None
    kits.approve(kit.dir, "Tester", "")
    assert kit_spec.write_bleed(kit, kit.bleed_px * 2)["approval"] == "unchanged"
    assert kits.approval(kit.dir) is not None
