"""kit fit against a car whose every texel is known (`tests.kitcar`): fitted from its rendered views, each
car-space point must land on the texel the sim shows there."""

import json
from dataclasses import dataclass

import numpy as np
import pytest

from livery import calibrate, carspace, config, kit_fit, primitives, state
from livery import kit as kits
from livery.config import REPO, Workspace
from livery.kit import Kit, Profile, View
from tests import kitcar
from tests.conftest import REPLAY, write_kit

BANK = 0.6  # the bank hides the left side's view below this share of the frame's height


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
def views():
    return kitcar.render_all(hide_left_below=BANK)


@pytest.fixture(scope="module")
def unbanked():
    return kitcar.render("side_l")


def _shots(views, tpl, names=None) -> dict[str, kit_fit.Shot]:
    return {v: kit_fit.shot(v, vm, tpl) for v, vm in views.items() if names is None or v in names}


def _write(kit: Kit, fitted: kit_fit.Fitted) -> None:
    maps = {**kits.read_maps(kit.dir), **fitted.maps}
    kits.write(kit.dir, meta=kits.read_meta(kit.dir) | {"views": kit_fit.map_views(maps)}, maps=maps)


@pytest.fixture(scope="module")
def synth(tmp_path_factory, views) -> Synth:
    """The fit from every view it reads: the five it needs, the faces and side_high."""
    ws = _kit(tmp_path_factory.mktemp("synth"))
    kit = kits.load(ws, "synth")
    tpl = kit_fit.template(kit)
    fitted = kit_fit.fit_maps(tpl, _shots(views, tpl))
    _write(kit, fitted)
    return Synth(ws, kits.load(ws, "synth"), tpl, fitted)


@pytest.fixture(scope="module")
def base(synth, views) -> kit_fit.Fitted:
    """The fit from the five views it needs alone."""
    return kit_fit.fit_maps(synth.tpl, _shots(views, synth.tpl, kit_fit.VIEWS))


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
    for view, vm in (("side_r", views["side_r"]), ("side_l", unbanked)):
        shot = _side_samples(synth, view, vm)
        u, v = kit_fit._uv(frames[view], shot)
        ids = kitcar.LEFT if view == "side_l" else kitcar.RIGHT
        for part in ("front", "door", "rear"):
            m = shot.ids == ids[part]
            miss = _misses(synth.kit, view, ids[part], np.stack([u[m], v[m]], 1), shot.tex(m))
            assert len(miss) > 0.9 * m.sum()  # the map holds over nearly all of what the view sees
            assert np.percentile(miss, 90) < 1.0


def test_what_a_bank_hides_of_one_side_is_mapped_from_the_other(synth, unbanked):
    shot = _side_samples(synth, "side_l", unbanked)
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
    apart = {}
    for (a, b), p in zip(ids.tolist(), pairs, strict=True):
        pa, pb = kitcar.where(a, p[None, :2] + 0.5), kitcar.where(b, p[None, 2:] + 0.5)
        apart.setdefault(tuple(sorted((a, b))), []).append(float(np.linalg.norm(pa - pb)) * kitcar.PX_PER_M)
    # Each point sits up to 3 px inside its island (more at a corner), and the round trip allows a few px more
    # along the car, so a pair is under about a dozen px apart on it; a wrong pairing (a folded map's second
    # solution, another island) would be tens.
    for pair, d in apart.items():
        assert np.median(d) < 8, pair
        assert np.percentile(d, 90) < 12, pair
        assert max(d) < 20, pair


def _car(kit: Kit, view: str, rid: str, texels: np.ndarray) -> np.ndarray:
    """The car-space points the kit's map of an island in a view puts at texels."""
    pc = next(p for p in carspace.pieces(kit, view) if p.island == rid)
    return np.array([carspace.invert(pc, t, carspace.start(view))[0] for t in texels])


def test_face_pixels_map_back_to_the_texels_they_show(synth):
    assert synth.kit.face_views == ("front", "rear")
    for face, rid in (("front", kitcar.LIP), ("rear", kitcar.REAR_FACE)):
        s = synth.fitted.evidence[(face, rid)]
        miss = _misses(synth.kit, face, rid, s.car, s.tex)
        assert len(miss) > 0.9 * len(s)
        assert np.percentile(miss, 90) < 1.0


def test_a_face_meets_the_sides_at_their_v_and_the_top_at_its_w(synth):
    side, rear, top, lip = (kitcar.surface(n) for n in ("side1", "rear", "top", "lip"))
    z = np.linspace(0.25, 0.85, 7)
    tail = np.full(len(z), -kitcar.HALF)
    y = side.point(tail, z)[:, 1]  # the left side's edge at the tail
    v_side = _car(synth.kit, "side_l", kitcar.LEFT["rear"], side.texel(tail, z))[:, 1]
    v_face = _car(synth.kit, "rear", kitcar.REAR_FACE, rear.texel(y, z))[:, 1]
    assert np.abs(v_side - v_face).max() < 0.02  # 1.5 cm on the car
    y = np.linspace(-0.6, 0.6, 7)
    for end, face, rid, texel in (
        (-kitcar.HALF, "rear", kitcar.REAR_FACE, rear.texel(y, np.full(len(y), kitcar.BELT + kitcar.RISE))),
        (kitcar.HALF, "front", kitcar.LIP, lip.texel(np.zeros(len(y)), y)),
    ):
        on_top = top.texel(np.full(len(y), end), y)
        w_top = _car(synth.kit, "top", synth.tpl.ids(*on_top.T)[0], on_top)[:, 1]
        w_face = _car(synth.kit, face, rid, texel)[:, 0]
        assert np.abs(w_top - w_face).max() < 0.03, face  # 2 cm on the car


def test_the_optional_views_leave_the_side_and_top_maps_where_they_were(synth, base):
    _, shapes = carspace.load_shapes(synth.kit)
    raw = kits.read_maps(synth.kit.dir)
    replaced = {r for r, v in synth.fitted.report.items() if isinstance(v, dict) and "replaced_by_face" in v}
    assert replaced == {kitcar.LIP}
    for view in ("side_l", "side_r", "top"):
        before = carspace.build_pieces({**raw, **base.maps}, shapes, view)
        for (v, rid), s in base.evidence.items():
            if v != view or rid in replaced or rid == "top-rest":
                continue
            was = kit_fit._piece_px(before, rid, s.car)
            now = kit_fit._piece_px(carspace.pieces(synth.kit, view), rid, s.car)
            assert was is not None, (view, rid)
            assert now is not None, (view, rid)
            assert np.percentile(np.linalg.norm(now - was, axis=1), 90) < 0.5, (view, rid)


def test_a_part_the_top_sees_edge_on_is_mapped_from_the_face_that_sees_it(synth, base):
    assert kitcar.LIP in base.maps["top_transform"]["sides"]  # the top's map, with its decode errors
    t = synth.fitted.maps["side_transforms"][kitcar.LIP]
    assert t["view"] == "front"
    assert kitcar.LIP not in synth.fitted.maps["top_transform"]["sides"]
    row = synth.fitted.report[kitcar.LIP]
    assert row["replaced_by_face"]["face"] == "front"
    assert row["replaced_by_face"]["top"]["dropped"] > kit_fit.MAX_TOP_DROPPED
    old = {**kits.read_maps(synth.kit.dir), **base.maps}
    assert kit_fit.compare(synth.kit, synth.tpl, old, synth.fitted)["owners"][kitcar.LIP] == ["top", "front"]


def test_side_high_maps_what_the_top_misses_of_the_shoulders(synth, views):
    """Something over the rear half of both shoulders hides them from the top view; the raised side view
    sees the left one, and the right one is its mirror image."""
    tpl = synth.tpl
    top = views["top"]
    ids = tpl.ids(*top.uv.T)
    behind = np.isin(ids, [kitcar.LEFT["shoulder"], kitcar.RIGHT["shoulder"]]) & (top.uv[:, 0] > 450)
    hide = np.zeros_like(top.pix)
    hide[top.pix] = behind
    seen = {**views, "top": kitcar.hidden(top, hide)}
    without = kit_fit.fit_maps(tpl, _shots(seen, tpl, kit_fit.VIEWS))
    full = kit_fit.fit_maps(tpl, _shots(seen, tpl))
    for rid in (kitcar.LEFT["shoulder"], kitcar.RIGHT["shoulder"]):
        tops = [f.maps["top_transform"]["sides"] for f in (without, full)]
        area = [carspace.region(t[rid]["domain"]).area if rid in t else 0.0 for t in tops]
        assert area[1] > 1.5 * area[0], rid
        # Where the top would have seen it, the raised view's map puts paint where the top's would, to the
        # couple of cm a carry from the cells around a part both views see holds.
        s = synth.fitted.evidence[("top", rid)]
        hid = s.tex[:, 0] > 450
        got = kit_fit._map_px(full.maps["top_transform"]["sides"][rid], s.car[hid])
        assert np.percentile(np.linalg.norm(got - s.tex[hid], axis=1), 90) < 4, rid
    assert full.report[kitcar.RIGHT["shoulder"]]["top"]["mirror_filled"] > 0


def test_a_band_reaching_an_end_covers_its_face(synth):
    tail = {e.id for e in primitives.band(synth.kit, "b", 0.8, 1.05, "#ff0000") if e.svg}
    assert tail == {"b.side_l", "b.side_r", "b.top", "b.rear"}
    middle = {e.id for e in primitives.band(synth.kit, "m", 0.3, 0.5, "#ff0000")}
    assert middle == {"m.side_l", "m.side_r", "m.top"}


def test_text_on_a_face_reads_forwards_as_seen(synth):
    from livery.lint import output

    for face, at in (("front", (0.0, 0.85)), ("rear", (0.0, 0.5))):
        el = primitives.text(synth.kit, f"t.{face}", face, "AB", at, 0.03, "#000000")
        kinds = {k for k, _ in output._orientation(el, output._nodes(el), synth.kit)}
        assert not kinds & {"mirrored", "backwards"}, face


def test_a_compare_with_the_same_maps_moves_nothing(synth):
    out = kit_fit.compare(synth.kit, synth.tpl, kits.read_maps(synth.kit.dir), synth.fitted)
    rows = out["islands"].values()
    assert rows
    assert all(r["moved_max_px"] == 0 for r in rows if "moved_max_px" in r)
    assert all(r["old_p90_px"] == r["p90_px"] for r in rows if r.get("old_p90_px") is not None)


# The command: from the kit's calibration capture to its files.


def _captured(ws: Workspace, kit: Kit, views) -> None:
    """The kit's current calibration capture: the rendered views, under the identity the kit has now."""
    prof = Profile(REPLAY, 100, {v: View(f"Preview {v}", ()) for v in views}, {})
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
    assert after.face_views == ("front", "rear")


def test_a_fit_reads_neither_close_ups_nor_second_light_views(tmp_path, views, base):
    ws = _kit(tmp_path)
    kit = kits.load(ws, "synth")
    _captured(
        ws, kit, {**{v: views[v] for v in kit_fit.VIEWS}, **{v: kitcar.render(v) for v in kitcar.IGNORED}}
    )
    kit_fit.fit(config.load(), ws, kit)
    after = kits.load(ws, "synth")
    assert after.views == kits.DEFAULT_VIEWS  # no face view without the views that see the faces
    maps = kits.read_maps(kit.dir)
    for part in ("side_transforms", "top_transform"):
        assert maps[part] == json.loads(json.dumps(base.maps[part]))


def test_a_fit_refuses_a_capture_shot_before_the_frame_moved(tmp_path, views):
    ws = _kit(tmp_path)
    kit = kits.load(ws, "synth")
    _captured(ws, kit, views)
    prof = kits.profile(kit)
    kits.write_profile(kit.dir, Profile(prof.replay, 200, prof.preview, {}))
    with pytest.raises(kit_fit.FitError):
        kit_fit.fit(config.load(), ws, kit, dry_run=True)


def test_keeping_an_island_puts_back_its_current_map(synth):
    old = kits.read_maps(synth.kit.dir)
    rid = kitcar.RIGHT["door"]
    mine = json.loads(json.dumps(old))
    mine["side_transforms"][rid]["coef"][0][0] += 50
    again = kit_fit.Fitted(
        json.loads(json.dumps(synth.fitted.maps)), synth.fitted.report, synth.fitted.evidence
    )
    assert kit_fit.keep_old(again, mine, {rid}) == [rid]
    assert again.maps["side_transforms"][rid] == mine["side_transforms"][rid]
