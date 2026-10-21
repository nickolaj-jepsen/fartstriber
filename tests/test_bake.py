"""The bake: rendering a build from a calibration capture, refusing a stale capture, bake check against a
sim run (its gate, the finishes it covers, the renderer it held), the drift check after a sim run, and baked
views standing in for sim shots in the critic's rounds and the review."""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from livery import bake, calibrate, config, critic, graycode, state
from livery import bake_render as render
from livery import kit as kits
from livery.config import Workspace
from livery.kit import Profile
from livery.raster import tga_bytes
from tests.conftest import CANVAS, SPEC, checked
from tests.test_review_critic import verdict

H, W = 24, 40  # a view's frame
CAR = np.s_[4:20, 5:35]  # the car in it
LEFT = CANVAS // 2  # texels left of this are one half of the test paint, the rest the other
HOLE = (12, 20)  # a car pixel the geometry didn't decode


def _response(m: int, levels: np.ndarray, n: int) -> np.ndarray:
    """A ladder for material m: per pixel and channel its own monotonic response, (L, n, 3) uint8."""
    gain = 0.4 + 0.1 * m + 0.2 * np.arange(3)[None, None, :]
    lift = (np.arange(n) % 7)[None, :, None] * 3 + 5 * m
    return np.clip(lift + gain * levels[:, None, None], 0, 255).astype(np.uint8)


def capture(ws: Workspace, check: dict | None = None) -> Path:
    """A finished capture of the tiny kit at every preview view, shot as things stand: each car pixel sees
    one texel a pixel, along a row of the texture that crosses LEFT, and each material its own ladder."""
    kit = kits.load(ws, "tiny")
    run = calibrate.root(ws, "tiny") / "20260101-000000"
    car = np.zeros((H, W), bool)
    car[CAR] = True
    pix = car.copy()
    pix[HOLE] = False
    ys, xs = np.nonzero(pix)
    n = len(ys)
    uv = np.stack([xs - 5 + 12.5, ys - 4 + 20.5], axis=1).astype(np.float32)
    levels = np.array(calibrate.LEVELS)
    for view in ws.policy.preview:
        jac = np.tile(np.eye(2, dtype=np.float32), (n, 1, 1))
        level = np.zeros((n, 2), np.int8)
        vm = graycode.ViewMap(car, pix, uv, jac, level, np.ones(n, np.float16), np.ones(n, bool))
        vm.save(run / view / "geometry.npz")
        arrays: dict[str, Any] = {
            "levels": levels,
            "satin_grey": np.array(80),
            "satin_roughness": np.zeros(0, np.int64),
            "satin_ladder": np.zeros((0, n, 3), np.uint8),
        }
        arrays |= {m: _response(i, levels.astype(np.float64), n) for i, m in enumerate(SPEC)}
        np.savez_compressed(run / view / "ladder.npz", **arrays)
        Image.fromarray(np.full((H, W, 3), (10, 90, 30), np.uint8)).save(run / view / "background.png")
    ident = calibrate.identity(config.load(), kit, kits.profile(kit))
    manifest = {"identity": ident, "complete": True, "window": [W, H]} | ({"check": check} if check else {})
    state.write_json(run / "manifest.json", manifest)
    (run.parent / "current").write_text(run.name, encoding="utf-8")
    return run


def halves(left: Sequence[int], right: Sequence[int]) -> np.ndarray:
    img = np.zeros((CANVAS, CANVAS, 3), np.uint8)
    img[:, :LEFT], img[:, LEFT:] = left, right
    return img


def passed(*finishes: str) -> dict:
    """A bake check with this renderer that passed and compared on enough pixels of each finish."""
    return {
        "passed": True,
        "renderer": bake.renderer_hash(),
        "worst": {"p95": 3.0},
        "designs": {"fixture": {"finishes": dict.fromkeys(finishes, bake.COVER_PX), "views": {}}},
    }


def failed(*finishes: str) -> dict:
    """A bake check with this renderer whose one design, showing these finishes, failed the gate."""
    out = passed(*finishes)
    out["designs"]["fixture"]["views"] = {"rear34": {"rest": {"p95": [bake.MAX_P95 + 1] * 3}}}
    return out | {"passed": False, "worst": {"p95": bake.MAX_P95 + 1, "design": "fixture", "view": "rear34"}}


PASSED = passed("gloss")


def test_a_flat_grey_on_a_ladder_step_renders_as_that_step_at_every_pixel(ws):
    run = capture(ws)
    b = bake.Bake.load(run, kits.load(ws, "tiny"), list(ws.policy.preview))
    gloss = list(SPEC).index("gloss")
    at = calibrate.LEVELS.index(80)

    frames = b.render(np.full((CANVAS, CANVAS, 3), 80, np.uint8), np.full((CANVAS, CANVAS, 3), SPEC["gloss"]))

    view = b.views["side_l"]
    want = _response(gloss, np.array(calibrate.LEVELS, np.float64), int(view.pix.sum()))[at]
    for frame in frames.values():
        assert (frame[view.pix] == want).all()
        assert (frame[~view.car] == (10, 90, 30)).all()  # off the car, the background frame
        assert (frame[HOLE] == frame[HOLE[0], HOLE[1] - 1]).all()  # an undecoded pixel copies a neighbour


def test_each_pixel_shows_the_texel_its_uv_names_under_that_texels_material(ws):
    run = capture(ws)
    b = bake.Bake.load(run, kits.load(ws, "tiny"), list(ws.policy.preview))
    paint, spec = halves((40, 40, 40), (200, 200, 200)), halves(SPEC["gloss"], SPEC["matte"])

    frame = b.render(paint, spec)["top"]

    view = b.views["top"]
    n = int(view.pix.sum())
    levels = np.array(calibrate.LEVELS, np.float64)
    names = list(SPEC)
    dark = _response(names.index("gloss"), levels, n)[calibrate.LEVELS.index(40)]
    light = _response(names.index("matte"), levels, n)[calibrate.LEVELS.index(200)]
    left = view.uv[:, 0] < LEFT
    assert left.any()
    assert (~left).any()
    shown = frame[view.pix]
    assert (shown[left] == dark[left]).all()
    assert (shown[~left] == light[~left]).all()


def test_a_footprint_long_along_u_is_averaged_along_u_and_keeps_its_detail_across(ws):
    rows = np.zeros((CANVAS, CANVAS, 1), np.float32)
    rows[1::2] = 1  # alternate texture rows dark and light
    uv = np.array([[20.5, 10.5], [20.5, 11.5]], np.float32)  # one pixel on a dark row, one on a light
    jac = np.tile(np.array([[4, 0], [0, 1]], np.float32), (2, 1, 1))  # 4 texels along u per pixel, 1 along v
    car = np.ones((1, 2), bool)
    frame, levels = np.zeros((1, 2, 3), np.uint8), np.array(calibrate.LEVELS, np.float32)
    view = render.View("v", car, car, uv, *render.footprint(jac), frame, levels, {}, render.fill(car, car))
    pyr = render.Pyramid(rows)

    assert render.filtered(view, pyr, aniso=True)[:, 0].tolist() == [0, 1]
    assert render.filtered(view, pyr, aniso=False)[:, 0].tolist() == [0.5, 0.5]  # one sample blurs across


def test_mips_average_the_paint_as_stored_not_in_linear_light():
    """The sim filters its textures in their own sRGB values: a mip of black and white texels is the
    middle value, not the linear-light mean."""
    paint = np.zeros((2, 2, 3), np.uint8)
    paint[0] = 255
    spec = np.full((2, 2, 3), SPEC["gloss"], np.uint8)

    pyr = render.pyramids(paint, spec, {m: tuple(s) for m, s in SPEC.items()})["gloss"]

    coverage, albedo = pyr.levels[1][0, 0, 0], pyr.levels[1][0, 0, 1:]
    assert np.allclose(albedo / coverage, 0.5)


# What a capture depends on.


def _install(ws: Workspace, sim: bytes = b"sim v1", track: bytes = b"track v1") -> None:
    """The sim's executable and signature, and the replay's track, in the fixture settings' install."""
    cfg = config.load()
    exe = cfg.install / cfg.process
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(sim)
    exe.with_suffix(".sgn").write_bytes(b"signed " + sim)
    folder = cfg.install.joinpath("tracks", *cfg.replay.track.split("\\"))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "track.dat").write_bytes(track)


def _moved_frame(ws: Workspace) -> None:
    kit = kits.load(ws, "tiny")
    p = kits.profile(kit)
    assert p.frame is not None
    kits.write_profile(kit.dir, Profile(p.replay, p.frame + 1, p.preview, p.crops))


def _new_spec(ws: Workspace) -> None:
    meta = kits.read_meta(ws.kits / "tiny")
    presets = dict(meta["spec_presets"])
    presets["satin"] = [0, 150, 255]
    kits.write(ws.kits / "tiny", meta=meta | {"spec_presets": presets})


def _new_graphics(ws: Workspace) -> None:
    ini = config.load().documents / calibrate.RENDERER_INI
    ini.parent.mkdir(parents=True, exist_ok=True)
    ini.write_text("[Graphics Options]\nMSAASamples=4\n", encoding="utf-8")


def _sim_update(ws: Workspace) -> None:
    _install(ws, sim=b"sim v2, a little longer")


def _track_update(ws: Workspace) -> None:
    _install(ws, track=b"track v2, a little longer")


@pytest.mark.parametrize(
    ("change", "named"),
    [
        (_moved_frame, "frame"),
        (_new_spec, "spec presets"),
        (_new_graphics, "graphics options"),
        (_sim_update, "sim's build"),
        (_track_update, "track's files"),
    ],
)
def test_a_capture_is_refused_once_what_its_shots_depend_on_moves(ws, change, named):
    _install(ws)
    run = capture(ws, PASSED)
    kit = kits.load(ws, "tiny")
    assert bake.capture(config.load(), ws, kit) == run

    change(ws)

    with pytest.raises(bake.StaleCapture, match=named):
        bake.capture(config.load(), ws, kits.load(ws, "tiny"))
    assert not bake.status(config.load(), ws, kits.load(ws, "tiny"))["checked"]


def test_a_capture_shot_before_the_sims_build_was_recorded_holds_against_its_bake_checks_record(ws):
    _install(ws)
    run = capture(ws)
    manifest = state.read_json(run / "manifest.json") or {}
    then = {k: manifest["identity"].pop(k) for k in calibrate.VOUCHED}
    state.write_json(run / "manifest.json", manifest | {"check": PASSED | {"identity": then}})
    kit = kits.load(ws, "tiny")
    assert bake.stale(config.load(), kit, run) == []

    _sim_update(ws)

    assert bake.stale(config.load(), kit, run)


def test_a_refit_keeps_the_capture(ws):
    """A refit moves where car space lands in the texture, not which texel the sim shows where."""
    run = capture(ws, PASSED)
    kits.write(ws.kits / "tiny", maps=kits.read_maps(ws.kits / "tiny") | {"refit": 1})

    assert bake.capture(config.load(), ws, kits.load(ws, "tiny")) == run


def test_no_capture_and_an_unchecked_capture_are_refused(ws):
    kit = kits.load(ws, "tiny")
    with pytest.raises(bake.NoCapture):
        bake.load_for(config.load(), ws, kit)
    capture(ws)
    with pytest.raises(bake.Unchecked):
        bake.load_for(config.load(), ws, kit)
    assert bake.load_for(config.load(), ws, kit, require_check=False).views


def test_a_check_done_with_another_renderer_asks_for_a_new_one(ws):
    capture(ws, PASSED | {"renderer": "another"})
    kit = kits.load(ws, "tiny")

    with pytest.raises(bake.Unchecked):
        bake.checked(config.load(), ws, kit)
    assert not bake.status(config.load(), ws, kit)["checked"]


# bake check.


def test_error_statistics_are_per_channel_and_leave_out_the_silhouette_band(ws):
    run = capture(ws)
    view = bake.Bake.load(run, kits.load(ws, "tiny"), ["side_l"]).views["side_l"]
    baked = np.full((H, W, 3), 100, np.uint8)
    sim = baked.copy()
    sim[CAR] += np.array([4, 0, 9], np.uint8)
    sim[CAR][:2] = 255  # the two rows along the car's top edge

    s = bake.stats(bake.errors(baked, sim, view)[0])

    assert s["median"] == [4, 0, 9]
    assert s["p95"] == [4, 0, 9]


def _built(ws: Workspace, name: str, paint: np.ndarray, spec: np.ndarray) -> dict:
    return checked(ws, name, outputs={"car.tga": tga_bytes(paint), "car_spec.tga": tga_bytes(spec)})


def _shoot(b: bake.Bake, outputs: dict[str, bytes], run: Path, error: int, where: dict | None = None) -> Path:
    """A sim run of the build: its baked render, `error` levels brighter in red on the car (or on `where`'s
    pixels, per view), and anything at all in the silhouette band."""
    run.mkdir(parents=True)
    for view, frame in b.render_tgas(outputs).items():
        frame = frame.astype(np.int16)
        if where is None:
            frame[CAR] += (error, 0, 0)
        else:
            frame[where[view]] += (error, 0, 0)
        frame[4] = 0
        Image.fromarray(np.clip(frame, 0, 255).astype(np.uint8)).save(run / f"{view}.png")
    (run / "manifest.json").write_text(json.dumps({"window": [W, H]}), encoding="utf-8")
    return run


@pytest.fixture
def fake_sim(ws, monkeypatch):
    """`preview sim` that shoots `_shoot` with the `error` and `where` set here. The tiny capture has few
    pixels, so a check covers a finish it compared on any."""
    from livery import check as gate
    from livery import preview_sim as sim_module

    monkeypatch.setattr(bake, "COVER_PX", 1)
    shot: dict = {"error": 0, "where": None, "runs": 0}
    monkeypatch.setattr(gate, "run_build", lambda ws, name: {"passed": True})

    def preview_sim(cfg, ws, name, log=print, drift=True):
        b = bake.load_for(cfg, ws, kits.load(ws, "tiny"), require_check=False)
        built = ws.out(name)
        outputs = {f: (built / f).read_bytes() for f in ("car.tga", "car_spec.tga")}
        run = built / "sim" / f"20260101-00000{shot['runs']}"
        shot["runs"] += 1
        return _shoot(b, outputs, run, shot["error"], shot["where"])

    monkeypatch.setattr(sim_module, "preview_sim", preview_sim)
    return shot


@pytest.mark.parametrize(("error", "passes"), [(5, True), (int(bake.MAX_EDGE_P95) + 8, False)])
def test_bake_check_records_its_statistics_in_the_capture_and_passes_under_the_target(
    ws, fake_sim, error, passes
):
    run = capture(ws)
    _built(ws, "fixture", halves((40, 40, 40), (200, 120, 0)), halves(SPEC["gloss"], SPEC["metallic"]))
    fake_sim["error"] = error
    kit = kits.load(ws, "tiny")

    result = bake.check(config.load(), ws, kit, ["fixture"], log=lambda m: None)

    assert result["passed"] is passes
    views = result["designs"]["fixture"]["views"]
    assert set(views) == set(ws.policy.preview)
    assert all(s["median"] == [error, 0, 0] for s in views.values())
    assert all(Path(p).is_file() for p in result["designs"]["fixture"]["pictures"].values())
    recorded = bake.check_of(run)
    assert recorded
    assert recorded["passed"] is passes
    if passes:
        assert bake.checked(config.load(), ws, kit) == run
    else:
        with pytest.raises(bake.Unchecked):
            bake.checked(config.load(), ws, kit)


@pytest.mark.parametrize(("along_edges", "passes"), [(True, True), (False, False)])
def test_the_gate_is_looser_along_paint_edges_than_away_from_them(ws, fake_sim, along_edges, passes):
    """The same error on the same number of pixels passes in the band along a paint edge, where a texel of
    registration noise lands on the other colour, and fails away from it."""
    run = capture(ws)
    paint, spec = halves((40, 40, 40), (200, 200, 200)), halves(SPEC["gloss"], SPEC["gloss"])
    _built(ws, "fixture", paint, spec)
    kit = kits.load(ws, "tiny")
    b = bake.Bake.load(run, kit, list(ws.policy.preview))
    idx = render.materials(spec, b.presets)
    where = {}
    for name, view in b.views.items():
        _, keep = bake.errors(np.zeros((H, W, 3)), np.zeros((H, W, 3)), view)
        band = bake.edges(view, paint, idx) & keep
        away = np.zeros_like(band)
        away.flat[np.flatnonzero(keep & ~band)[: int(band.sum())]] = True
        where[name] = band if along_edges else away
    fake_sim["error"] = int((bake.MAX_P95 + bake.MAX_EDGE_P95) / 2)
    fake_sim["where"] = where

    result = bake.check(config.load(), ws, kit, ["fixture"], log=lambda m: None)

    assert result["passed"] is passes


def test_bake_check_records_the_finishes_it_compared_on(ws, fake_sim):
    capture(ws)
    _built(ws, "fixture", halves((40, 40, 40), (200, 120, 0)), halves(SPEC["gloss"], SPEC["metallic"]))
    kit = kits.load(ws, "tiny")

    result = bake.check(config.load(), ws, kit, ["fixture"], log=lambda m: None)

    assert result["covered"] == ["gloss", "metallic"]
    assert bake.usable(config.load(), ws, kit, ["gloss", "metallic"]) == []
    assert bake.usable(config.load(), ws, kit, ["gloss", "matte"])


def _metal(b: bake.Bake, spec: np.ndarray) -> dict[str, np.ndarray]:
    """Per view, the pixels whose texel is metallic."""
    idx = render.materials(spec, b.presets)
    return {v: b.finish_map(view, idx) == list(b.presets).index("metallic") for v, view in b.views.items()}


def test_a_design_that_fails_covers_none_of_its_finishes_and_leaves_the_passing_ones_covered(ws, fake_sim):
    """Metal glare spreads over the cells around it whatever their finish, so a design whose metal fails
    vouches for none of its finishes; a design that passed still vouches for its own."""
    run = capture(ws)
    spec = halves(SPEC["gloss"], SPEC["metallic"])
    _built(ws, "glossy", halves((40, 40, 40), (200, 120, 0)), halves(SPEC["gloss"], SPEC["gloss"]))
    _built(ws, "fixture", halves((40, 40, 40), (200, 120, 0)), spec)
    kit = kits.load(ws, "tiny")
    bake.check(config.load(), ws, kit, ["glossy"], log=lambda m: None)
    fake_sim["error"] = int(bake.MAX_EDGE_P95) + 8
    fake_sim["where"] = _metal(bake.Bake.load(run, kit, list(ws.policy.preview)), spec)

    result = bake.check(config.load(), ws, kit, ["fixture"], log=lambda m: None)

    assert not result["passed"]
    assert "gloss" in result["designs"]["fixture"]["finishes"]
    assert result["covered"] == ["gloss"]
    assert bake.checked(config.load(), ws, kit) == run
    assert bake.usable(config.load(), ws, kit, ["gloss"]) == []
    assert bake.usable(config.load(), ws, kit, ["gloss", "metallic"])


def test_a_check_of_another_design_adds_to_the_finishes_the_last_one_covered(ws, fake_sim):
    capture(ws)
    _built(ws, "glossy", halves((40, 40, 40), (200, 120, 0)), halves(SPEC["gloss"], SPEC["gloss"]))
    _built(ws, "metal", halves((40, 40, 40), (200, 120, 0)), halves(SPEC["metallic"], SPEC["metallic"]))
    kit = kits.load(ws, "tiny")
    bake.check(config.load(), ws, kit, ["glossy"], log=lambda m: None)

    result = bake.check(config.load(), ws, kit, ["metal"], log=lambda m: None)

    assert set(result["designs"]) == {"glossy", "metal"}
    assert result["covered"] == ["gloss", "metallic"]


@pytest.mark.parametrize(("right", "stands_in"), [("gloss", True), ("matte", False)])
def test_baked_views_stand_in_for_a_design_only_where_the_check_covered_every_finish_it_uses(
    ws, right, stands_in
):
    capture(ws, PASSED)
    _built(ws, "d", halves((40, 40, 40), (200, 200, 200)), halves(SPEC["gloss"], SPEC[right]))

    found = bake.design_status(config.load(), ws, "d")

    assert found["checked"]
    assert found["usable"] is stands_in
    assert bool(found["usable_why"]) is not stands_in


def test_baked_previews_are_stamped_with_the_build_and_the_capture(ws, cheap_flat):
    from livery import preview_flat, stamps

    capture(ws, PASSED)
    checked(ws, "d")  # the design script; flat renders it afresh

    out = preview_flat.flat(ws, "d", with_baked=True)

    folder = stamps.baked_dir(ws, "d")
    stamp = stamps.read(folder / stamps.STAMP)
    flat = stamps.read(out / stamps.STAMP)
    assert stamp
    assert flat
    assert stamp["baked"]
    assert stamp["capture"] == "20260101-000000"
    assert stamp["digests"] == flat["digests"]
    assert {p.stem for p in folder.glob("*.png")} == {*ws.policy.preview, "sheet"}


# The bake against every later sim run of a build it stands in for.


@pytest.mark.parametrize(("error", "passes"), [(2, True), (int(bake.MAX_EDGE_P95) + 8, False)])
def test_a_sim_run_of_a_build_the_bake_stands_in_for_measures_the_bake_and_warns_when_it_drifted(
    ws, error, passes
):
    from livery import preview_sim

    capture(ws, PASSED)
    _built(ws, "d", halves((40, 40, 40), (200, 200, 200)), halves(SPEC["gloss"], SPEC["gloss"]))
    kit = kits.load(ws, "tiny")
    outputs = {f: (ws.out("d") / f).read_bytes() for f in ("car.tga", "car_spec.tga")}
    shots = _shoot(bake.load_for(config.load(), ws, kit), outputs, ws.out("d") / "sim" / "run", error)
    said: list[str] = []

    found = preview_sim._drift(config.load(), ws, kit, outputs, shots, said.append)

    recorded = (state.read_json(shots / "manifest.json") or {})["bake"]
    assert recorded["usable"]
    assert recorded["passed"] is passes
    assert set(recorded["views"]) == set(ws.policy.preview)
    assert found == recorded
    assert bool(said) is not passes


def test_a_sim_run_of_a_build_with_a_finish_the_check_didnt_cover_leaves_the_bake_alone(ws):
    from livery import preview_sim

    capture(ws, PASSED)
    _built(ws, "d", halves((40, 40, 40), (200, 200, 200)), halves(SPEC["gloss"], SPEC["matte"]))
    kit = kits.load(ws, "tiny")
    outputs = {f: (ws.out("d") / f).read_bytes() for f in ("car.tga", "car_spec.tga")}
    shots = _shoot(bake.load_for(config.load(), ws, kit), outputs, ws.out("d") / "sim" / "run", 0)

    found = preview_sim._drift(config.load(), ws, kit, outputs, shots, print)

    assert not found["usable"]
    assert "views" not in found


# The critic's rounds and the review.


def _baked_design(ws: Workspace, check: dict) -> None:
    capture(ws, check)
    _built(ws, "d", halves((40, 40, 40), (200, 200, 200)), halves(SPEC["gloss"], SPEC["matte"]))


def test_a_baked_round_pack_shows_baked_renders_and_says_so(ws):
    _baked_design(ws, PASSED)

    folder = critic.pack(ws, ["d"], baked=True)

    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    entry = manifest["designs"]["d"]
    assert manifest["baked"]
    assert entry["baked"]
    assert sorted(Path(p).stem for p in entry["shots"]) == sorted(ws.policy.preview)
    recorded = critic.record(ws, "d", verdict())
    assert recorded["baked"] is True
    assert "baked-runs" in recorded["shots"]


def test_baked_renders_are_refused_before_the_capture_passed_bake_check(ws):
    _baked_design(ws, failed("gloss"))
    with pytest.raises(bake.Unchecked, match="failed bake check"):
        critic.pack(ws, ["d"], baked=True)


def _round_two(ws: Workspace, baked: bool) -> None:
    """Round one on sim shots, answered; round two, everything passing, judged on sim shots or baked."""
    from tests.conftest import sim_run

    digests = json.loads((ws.out("d") / "check.json").read_text(encoding="utf-8"))["digests"]
    sim_run(ws, "d", "20260101-000000", digests)
    critic.record(ws, "d", verdict())
    critic.answer(ws, "d", [{"status": "done", "note": "done"}])
    sim_run(ws, "d", "20260102-000000", digests)
    critic.pack(ws, ["d"], baked=baked)
    resolved = [{"status": "resolved", "evidence": "front34.png"}]
    critic.record(ws, "d", verdict(previous=resolved, fixes=[]))
    critic.pack_compare(ws, "d", baked=baked)
    critic.record_compare(ws, "d", {"better": "A"}, {"better": "B"})
    critic.pack(ws, ["d"], mode="adversary", baked=baked)
    finding = {"text": "the mirrors are plain", "view": "front34", "optional": True}
    critic.record_adversary(ws, "d", {"closest": None, "findings": [finding]})


@pytest.mark.parametrize(
    ("baked", "covered", "ships"),
    [
        (False, ("gloss",), True),
        (True, ("gloss", "matte"), True),  # the check covered both finishes the design uses
        (True, ("gloss",), False),  # it never compared matte
    ],
)
def test_a_round_judged_on_baked_renders_ships_only_where_they_stand_in_for_sim_shots(
    ws, baked, covered, ships
):
    _baked_design(ws, passed(*covered))

    _round_two(ws, baked)

    got = critic.status(ws, "d")
    assert got["ship"] is ships
    assert got["baked"] is baked
    assert any("baked" in w for w in got["why"]) is not ships


def test_a_baked_round_stops_shipping_once_the_capture_goes_stale(ws):
    _install(ws)
    _baked_design(ws, passed("gloss", "matte"))
    _round_two(ws, baked=True)
    assert critic.status(ws, "d")["ship"]

    _sim_update(ws)

    assert not critic.status(ws, "d")["ship"]


@pytest.mark.parametrize(
    ("sim_shots", "covered"), [(False, ("gloss", "matte")), (True, ("gloss", "matte")), (False, ("gloss",))]
)
def test_a_final_review_shows_sim_shots_first_and_baked_views_only_where_they_stand_in(
    ws, cheap_flat, sim_shots, covered
):
    from livery.review.items import design_items
    from tests.conftest import sim_run

    _baked_design(ws, passed(*covered))
    if sim_shots:
        digests = json.loads((ws.out("d") / "check.json").read_text(encoding="utf-8"))["digests"]
        sim_run(ws, "d", "20260101-000000", digests)

    (item,) = design_items(ws, ["d"])

    kinds = {c.rsplit(" · ", 1)[-1] for c in item["captions"].values() if " · " in c}
    stands_in = not sim_shots and covered == ("gloss", "matte")
    assert kinds == ({"baked"} if stands_in else {"sim"} if sim_shots else set())
    assert bool(item["baked_run"]) is stands_in
    assert bool(item["runs"]) is sim_shots
