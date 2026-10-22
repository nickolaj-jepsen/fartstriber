"""The calibration livery on any kit, and `livery kit review` shooting it on the kit's own car."""

import json
from dataclasses import replace

import pytest

from livery import config
from livery import kit as kits
from livery.calibration import BELT, U_BANDS, calibration
from livery.check import digests
from livery.iracing import SimNotReady
from livery.kit import Profile, View
from livery.kit_review import calibration_build, shoot_calibration
from tests import fakesim
from tests.conftest import REPLAY, TWO, drawn, two_islands
from tests.test_simsession import World


@pytest.fixture
def two(tmp_path):
    """The two-island kit, framed on the fixture replay."""
    ws = two_islands(tmp_path)
    kit = kits.load(ws, "two")
    preview = {v: View("Top" if v == "top" else "Side", ()) for v in ws.policy.cameras}
    kits.write_profile(kit.dir, Profile(REPLAY, 100, preview, {}))
    return ws, kit


def test_the_calibration_bands_sit_at_their_place_on_the_car_through_the_kits_maps(two):
    _ws, kit = two

    liv = calibration(kit)

    by_id = {e.id: e for e in liv.elements}
    for u, colour in U_BANDS:
        band = drawn(by_id[f"u{int(u * 100)}-side_l"], colour)
        assert band.centroid.x == pytest.approx(TWO * u, abs=1)  # both islands map x = TWO u
        assert band.bounds[3] - band.bounds[1] == pytest.approx(TWO, abs=1)  # sill to the top of the side
    belt = drawn(by_id["belt-side_l"]).bounds
    assert (belt[1], belt[3]) == pytest.approx((TWO * (1 - BELT[1]), TWO * (1 - BELT[0])), abs=1)
    assert drawn(by_id["u20-side_r"]).is_empty  # the kit maps no right side


def test_a_kit_without_maps_has_no_calibration_to_shoot(ws):
    with pytest.raises(kits.KitError):
        calibration_build(ws, kits.load(ws, "tiny"))


@pytest.fixture
def sim(tmp_path, monkeypatch) -> World:
    cfg = replace(config.load(), documents=tmp_path / "docs", archive_dir=tmp_path / "archive")
    return fakesim.install(monkeypatch, World(cfg))


def test_kit_review_shoots_the_calibration_built_for_the_kit(two, sim):
    ws, kit = two
    fakesim.drive(sim, kit.paint_folder)

    out = shoot_calibration(sim.cfg, kit, ws, log=sim.logs.append)

    assert {p.name for p in out.glob("calibration_*.png")} == {
        f"calibration_{v}.png" for v in ws.policy.cameras
    }
    built = calibration_build(ws, kit)
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["digests"] == digests({k: built[k] for k in ("car.tga", "car_spec.tga")})
    assert ("reload_texture", (3,)) in sim.sent
    assert not list(sim.folder.iterdir())  # the paint folder put back


def test_kit_review_never_paints_a_replay_whose_car_is_another(two, sim):
    ws, kit = two  # the sim's car is the tiny kit's, not this one's
    folder = sim.cfg.paint_root / kit.paint_folder

    with pytest.raises(SimNotReady):
        shoot_calibration(sim.cfg, kit, ws, log=sim.logs.append)

    assert sim.sent == []
    assert not folder.exists() or not list(folder.iterdir())
    assert not list((ws.cache / "kit-review" / kit.name).glob("calibration_*.png"))
