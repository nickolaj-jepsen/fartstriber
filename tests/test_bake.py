"""The bake: rendering a build from a calibration capture, refusing a stale capture, bake check against a
sim run, and the critic's baked middle rounds."""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from livery import bake, calibrate, config, critic, graycode, state
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


PASSED = {"passed": True, "worst": {"p95": 3.0}}


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
    view = bake.View("v", car, car, uv, *bake.footprint(jac), frame, levels, {}, bake._fill(car, car))
    pyr = bake.Pyramid(rows)

    assert bake.filtered(view, pyr, aniso=True)[:, 0].tolist() == [0, 1]
    assert bake.filtered(view, pyr, aniso=False)[:, 0].tolist() == [0.5, 0.5]  # one sample blurs across


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


@pytest.mark.parametrize(
    ("change", "named"),
    [
        (_moved_frame, "frame"),
        (_new_spec, "spec presets"),
        (_new_graphics, "graphics options"),
    ],
)
def test_a_capture_is_refused_once_what_its_shots_depend_on_moves(ws, change, named):
    run = capture(ws, PASSED)
    kit = kits.load(ws, "tiny")
    assert bake.capture(config.load(), ws, kit) == run

    change(ws)

    with pytest.raises(bake.StaleCapture, match=named):
        bake.capture(config.load(), ws, kits.load(ws, "tiny"))
    assert not bake.status(config.load(), ws, kits.load(ws, "tiny"))["checked"]


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


@pytest.fixture
def fake_sim(ws, monkeypatch):
    """`preview sim` that shoots the baked render of the build, `error` levels brighter in red on the car,
    and anything at all in the silhouette band."""
    from livery import check as gate
    from livery import preview_sim as sim_module

    shot = {"error": 0, "runs": 0}
    monkeypatch.setattr(gate, "run_build", lambda ws, name: {"passed": True})

    def preview_sim(cfg, ws, name, log=print):
        b = bake.load_for(cfg, ws, kits.load(ws, "tiny"), require_check=False)
        built = ws.out(name)
        outputs = {f: (built / f).read_bytes() for f in ("car.tga", "car_spec.tga")}
        run = built / "sim" / f"20260101-00000{shot['runs']}"
        shot["runs"] += 1
        run.mkdir(parents=True)
        for view, frame in b.render_tgas(outputs).items():
            frame = frame.astype(np.int16)
            frame[CAR] += (shot["error"], 0, 0)
            frame[4] = 0
            Image.fromarray(np.clip(frame, 0, 255).astype(np.uint8)).save(run / f"{view}.png")
        (run / "manifest.json").write_text(json.dumps({"window": [W, H]}), encoding="utf-8")
        return run

    monkeypatch.setattr(sim_module, "preview_sim", preview_sim)
    return shot


@pytest.mark.parametrize(("error", "passes"), [(5, True), (int(bake.MAX_P95) + 8, False)])
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


# The critic's middle rounds.


def _baked_design(ws: Workspace) -> None:
    capture(ws, PASSED)
    _built(ws, "d", halves((40, 40, 40), (200, 200, 200)), halves(SPEC["gloss"], SPEC["matte"]))


def test_a_baked_round_pack_shows_baked_renders_and_says_so(ws):
    _baked_design(ws)

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
    capture(ws, {"passed": False, "worst": {"p95": 30.0, "design": "calibration", "view": "rear34"}})
    _built(ws, "d", halves((40, 40, 40), (200, 200, 200)), halves(SPEC["gloss"], SPEC["matte"]))
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


@pytest.mark.parametrize("baked", [False, True])
def test_a_round_judged_on_baked_renders_never_ships(ws, baked):
    _baked_design(ws)

    _round_two(ws, baked)

    got = critic.status(ws, "d")
    assert got["ship"] is not baked
    assert got["baked"] is baked
    assert any("baked" in w for w in got["why"]) is baked
