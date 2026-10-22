"""kit fit against a car whose every texel is known (`tests.kitcar`): fitted from its rendered views, each
car-space point must land on the texel the sim shows there."""

import json
from dataclasses import dataclass, replace

import numpy as np
import pytest

from livery import calibrate, carspace, config, kit_fit, state
from livery import kit as kits
from livery.config import REPO, Workspace
from livery.kit import Kit, Profile, View
from tests import kitcar
from tests.conftest import REPLAY, write_kit

BANK = 0.6  # the bank hides the left side's view below this share of the frame's height
# One worker renders the synthetic car and fits it once for the whole module, under --dist loadgroup.
pytestmark = pytest.mark.xdist_group("kitfit-synth")


@dataclass
class Synth:
    ws: Workspace
    kit: Kit
    tpl: kit_fit.Template
    fitted: kit_fit.Fitted


def _kit(root) -> Workspace:
    ws = Workspace(root=root, assets=REPO / "assets")
    kitcar.write_template(ws.kits / "synth", write_kit)
    return ws


@pytest.fixture(scope="module")
def unbanked():
    return kitcar.render_all()


@pytest.fixture(scope="module")
def views(unbanked):
    return {**unbanked, "side_l": kitcar.hide(unbanked["side_l"], BANK)}


@pytest.fixture(scope="module")
def synth(tmp_path_factory, views) -> Synth:
    ws = _kit(tmp_path_factory.mktemp("synth"))
    kit = kits.load(ws, "synth")
    tpl = kit_fit.template(kit)
    fitted = kit_fit.fit_maps(tpl, {v: kit_fit.shot(v, vm, tpl) for v, vm in views.items()})
    kits.write(kit.dir, maps={**kits.read_maps(kit.dir), **fitted.maps})
    return Synth(ws, kits.load(ws, "synth"), tpl, fitted)


def _misses(kit: Kit, view: str, rid: str, car: np.ndarray, tex: np.ndarray) -> np.ndarray:
    """How far the kit's maps put car-space points from their texels, where the island's piece holds."""
    pc = next(p for p in carspace.pieces(kit, view) if p.island == rid)
    px = pc.to_px(car)
    held = carspace.shapely.contains_xy(pc.area, *px.T)
    if pc.car_area is not None:
        held &= carspace.shapely.contains_xy(pc.car_area, *car.T)
    return np.linalg.norm(px - tex, axis=1)[held]


def _side_samples(s: Synth, view: str, vm) -> kit_fit.Shot:
    return kit_fit.shot(view, vm, s.tpl)


def test_every_side_pixel_maps_back_to_the_texel_it_shows(synth, views, unbanked):
    frames = synth.fitted.maps["side_frames"]
    for view, vm in (("side_r", views["side_r"]), ("side_l", unbanked["side_l"])):
        shot = _side_samples(synth, view, vm)
        u, v = kit_fit._uv(frames[view], shot)
        ids = kitcar.LEFT if view == "side_l" else kitcar.RIGHT
        for part in ("front", "door", "rear"):
            m = shot.ids == ids[part]
            miss = _misses(synth.kit, view, ids[part], np.stack([u[m], v[m]], 1), shot.tex(m))
            assert len(miss) > 0.9 * m.sum()  # the map holds over nearly all of what the view sees
            assert np.percentile(miss, 90) < 1.0


def test_what_a_bank_hides_of_one_side_is_mapped_from_the_other(synth, unbanked):
    shot = _side_samples(synth, "side_l", unbanked["side_l"])
    hidden = shot.sy >= BANK * kitcar.H
    u, v = kit_fit._uv(synth.fitted.maps["side_frames"]["side_l"], shot)
    door = hidden & (shot.ids == kitcar.LEFT["door"])
    assert door.sum() > 500
    miss = _misses(synth.kit, "side_l", kitcar.LEFT["door"], np.stack([u[door], v[door]], 1), shot.tex(door))
    assert len(miss) > 0.9 * door.sum()
    assert np.percentile(miss, 90) < 1.0


def test_the_left_frame_matches_the_right_one_whatever_hides_its_sill(synth):
    left, right = (synth.fitted.maps["side_frames"][v] for v in ("side_l", "side_r"))
    height = right["sill_sy"] - right["top_sy"]
    assert abs((left["sill_sy"] - left["top_sy"]) - height) < 0.01 * height  # the cameras mirror each other


def test_partner_islands_agree_reflected(synth):
    agree = synth.fitted.report["sides_agree"]
    assert set(agree) >= {"i001/i004", "i002/i005", "i003/i006"}
    assert all(d["median_px"] < 1.0 for d in agree.values())


def test_the_top_and_the_up_facing_strips_map_back_to_their_texels(synth):
    louvres = [ids[f"louvre{k}"] for ids in (kitcar.LEFT, kitcar.RIGHT) for k in range(4)]
    for rid in (*kitcar.TOP.values(), kitcar.LEFT["shoulder"], kitcar.RIGHT["shoulder"], *louvres):
        s = synth.fitted.evidence[("top", rid)]
        miss = _misses(synth.kit, "top", rid, s.car, s.tex)
        assert len(miss) > 0.9 * len(s)
        assert np.percentile(miss, 90) < 1.0


def test_seams_pair_texels_on_the_same_spot_of_the_car(synth):
    pairs = np.asarray(kit_fit.seam_pairs(synth.kit), float)
    ids = synth.tpl.ids(pairs[:, [0, 2]].ravel(), pairs[:, [1, 3]].ravel()).reshape(-1, 2)
    found = {tuple(sorted(p)) for p in ids.tolist()}
    left, right, top = kitcar.LEFT, kitcar.RIGHT, kitcar.TOP
    for a, b in (
        (left["front"], left["door"]),
        (left["door"], left["rear"]),
        (right["front"], right["door"]),
        (right["door"], right["rear"]),
        (top["front"], top["door"]),
        (top["door"], top["rear"]),
        (left["door"], left["shoulder"]),
        (top["door"], left["shoulder"]),
        (top["door"], right["shoulder"]),
    ):
        assert tuple(sorted((a, b))) in found, (a, b)
    on_car = np.zeros((len(pairs), 2, 3))  # each pair's two points on the car
    for end in (0, 1):
        for rid in set(ids[:, end]):
            m = ids[:, end] == rid
            on_car[m, end] = kitcar.where(rid, pairs[m, 2 * end : 2 * end + 2] + 0.5)
    apart = {}
    for (a, b), d in zip(ids.tolist(), np.linalg.norm(on_car[:, 0] - on_car[:, 1], axis=1), strict=True):
        apart.setdefault(tuple(sorted((a, b))), []).append(float(d) * kitcar.PX_PER_M)
    # Each point sits up to 3 px inside its island (more at a corner), and the round trip allows a few px more
    # along the car, so a pair is under about a dozen px apart on it; a wrong pairing (a folded map's second
    # solution, another island) would be tens.
    for pair, d in apart.items():
        assert np.median(d) < 8, pair
        assert np.percentile(d, 90) < 12, pair
        assert max(d) < 20, pair


def test_a_compare_with_the_same_maps_moves_nothing(synth):
    out = kit_fit.compare(synth.kit, synth.tpl, kits.read_maps(synth.kit.dir), synth.fitted)
    rows = out["islands"].values()
    assert rows
    assert all(r["moved_max_px"] == 0 for r in rows if "moved_max_px" in r)
    assert all(r["old_p90_px"] == r["p90_px"] for r in rows if r.get("old_p90_px") is not None)


# The command: from the kit's calibration capture to its files.


def _captured(ws: Workspace, kit: Kit, views) -> None:
    """The kit's current calibration capture: the rendered views, under the identity the kit has now."""
    prof = Profile(REPLAY, 100, {v: View(f"Preview {v}", ()) for v in kit_fit.VIEWS}, {})
    kits.write_profile(kit.dir, prof)
    run = calibrate.root(ws, kit.name) / "run"
    for v, vm in views.items():
        vm.save(run / v / "geometry.npz")
    ident = calibrate.identity(config.load(), ws, kit, kits.profile(kit))
    state.write_json(run / "manifest.json", {"identity": ident, "complete": True})
    calibrate._point_at(run)


def test_a_fit_writes_maps_seams_and_its_report_and_sends_the_kit_back_to_review(tmp_path, views):
    ws = _kit(tmp_path)
    kit = kits.load(ws, "synth")
    _captured(ws, kit, views)
    kits.approve(kit.dir, "Tester", "")
    cfg = config.load()

    dry = kit_fit.fit(cfg, ws, kit, dry_run=True)
    assert not (kit.dir / kit_fit.REPORT).exists()
    assert kits.approval(kit.dir) is not None

    result = kit_fit.fit(cfg, ws, kit)
    after = kits.load(ws, "synth")
    assert after.version == kit.version + 1
    assert kits.approval(kit.dir) is None
    report = json.loads((kit.dir / kit_fit.REPORT).read_text(encoding="utf-8"))
    assert report["version"] == after.version
    assert report["summary"] == result["summary"] == dry["summary"]
    assert json.loads((kit.dir / "seams.json").read_text(encoding="utf-8"))
    assert kits.read_maps(kit.dir)["side_transforms"]


def test_a_fit_refuses_a_capture_shot_before_the_frame_moved(tmp_path, views):
    ws = _kit(tmp_path)
    kit = kits.load(ws, "synth")
    _captured(ws, kit, views)
    kits.write_profile(kit.dir, replace(kits.profile(kit), frame=200))
    with pytest.raises(kit_fit.FitError):
        kit_fit.fit(config.load(), ws, kit, dry_run=True)


def test_keeping_an_island_puts_back_its_current_map():
    rid = kitcar.RIGHT["door"]

    def maps(x0: float) -> dict:
        side = {"view": "side_r", "coef": [[x0, 900, 0, 0, 0, 0], [500, 0, -300, 0, 0, 0]]}
        top = {"sides": {kitcar.TOP["door"]: {"view": "top"}}, "islands": {}}
        return {"side_transforms": {rid: side}, "top_transform": top}

    mine = maps(90)
    again = kit_fit.Fitted(maps(40), {}, {})
    assert kit_fit.keep_old(again, mine, {rid}) == [rid]
    assert again.maps["side_transforms"][rid] == mine["side_transforms"][rid]
