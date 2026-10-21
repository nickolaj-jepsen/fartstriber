"""`livery kit capture` against the fake sim of test_simsession, whose car shows the loaded paint through a
fixed uv field."""

import hashlib
import io
import json
import threading
from dataclasses import replace

import irsdk
import numpy as np
import pytest
from PIL import Image

from livery import calibrate, capture, config, graycode, simlock, simsession
from livery import kit as kits
from livery.capture import SimInFront
from livery.config import Workspace
from livery.errors import LiveryError
from livery.iracing import SimNotReady
from livery.kit import Profile, View
from livery.model import Material
from livery.raster import tga_bytes
from tests.conftest import REPLAY, write_policy
from tests.test_simsession import FakeCapture, FakeIR, FakeWindow, H, W, World

Y0, Y1, X0, X1 = 30, 60, 40, 120  # the car, in the window
VIEWS = {"side": View("Side", ()), "top": View("Top", ())}


def field() -> tuple[np.ndarray, np.ndarray]:
    """The texel the car shows at each of its pixels, as floats: the fake samples the nearest."""
    y, x = np.mgrid[Y0:Y1, X0:X1].astype(np.float64)
    return 0.75 * (x - X0) + 0.3, 0.9 * (y - Y0) + 2.2


class Mapped(World):
    """The car shows the whole paint texture, not one colour."""

    def __init__(self, cfg: config.Config, canvas: int):
        self.canvas = canvas
        super().__init__(cfg)
        self.texture = self.read_texture()

    def read_texture(self) -> np.ndarray:
        f = self.folder / f"car_{self.stem}.tga"
        if not f.exists():
            return np.full((self.canvas, self.canvas, 3), 60, np.uint8)
        return np.asarray(Image.open(io.BytesIO(f.read_bytes())).convert("RGB"))

    def reload(self) -> None:
        super().reload()
        if self.reload_shows:
            self.texture = self.read_texture()

    def render(self) -> np.ndarray:
        grey = self.grey_left  # frames of the reload's grey step still to show
        img = super().render()
        if not grey:
            u, v = field()
            img[Y0:Y1, X0:X1] = self.texture[v.astype(int), u.astype(int)]
        return img


@pytest.fixture
def world(tmp_path, monkeypatch, ws: Workspace) -> Mapped:
    kit = kits.load(ws, "tiny")
    kits.write_profile(kit.dir, Profile(REPLAY, 100, VIEWS, {}))
    cfg = replace(config.load(), documents=tmp_path / "docs")
    folder = cfg.paint_root / "tiny"
    folder.mkdir(parents=True)
    (folder / f"car_team_{cfg.team_id}.tga").write_bytes(
        tga_bytes(np.full((kit.canvas, kit.canvas, 3), (200, 0, 0), np.uint8))
    )
    w = Mapped(cfg, kit.canvas)
    monkeypatch.setattr(irsdk, "IRSDK", lambda: FakeIR(w))
    monkeypatch.setattr(capture.Window, "find", staticmethod(lambda process: FakeWindow(w)))
    monkeypatch.setattr(capture, "Capture", FakeCapture)
    monkeypatch.setattr(capture, "processes", lambda: ["explorer.exe"])
    quick = {"POLL_S": 0.01, "SETTLE_S": 0.0, "GREY_S": 0.2, "PAINT_S": 0.05, "LOAD_S": 0.0, "STEADY_S": 0.5}
    for name, value in quick.items():
        monkeypatch.setattr(simsession, name, value)
    return w


def folder(w: World) -> dict[str, str]:
    return {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in w.folder.iterdir()}


def click_in(w: World, job: int) -> None:
    """The owner clicks into the sim while that paint (counted from 0) reloads: each paint reloads once, then
    once more to put the paint back."""
    reloads = lambda: sum(name == "reload_texture" for name, _ in w.sent)  # noqa: E731
    w.on_grab = lambda n: setattr(w, "front", w.front or reloads() == 2 * job + 1)


def capture_run(w: World, ws: Workspace, **kw):
    return calibrate.capture(w.cfg, ws, kits.load(ws, "tiny"), log=w.logs.append, **kw)


def test_a_capture_decodes_the_car_and_its_ladders_and_leaves_the_folder_as_it_was(world, ws):
    before = folder(world)

    run = capture_run(world, ws)

    assert calibrate.current(ws, "tiny") == run
    assert folder(world) == before
    assert list(run.glob("jobs/*/*.png")) == []  # only the decoded arrays are kept
    u, v = field()
    truth = np.zeros((H, W, 2))
    truth[Y0:Y1, X0:X1] = np.stack([u, v], axis=2)
    spec = kits.load(ws, "tiny").spec
    for view in VIEWS:
        vm = graycode.ViewMap.load(run / view / "geometry.npz")
        assert vm.car[Y0:Y1, X0:X1].all()
        assert vm.car.sum() == vm.pix.sum() == u.size
        assert np.percentile(np.abs(vm.uv - truth[vm.pix]).max(axis=1), 95) < 0.5
        with np.load(run / view / "ladder.npz") as z:
            for m in spec:
                assert (z[m] == z["levels"][:, None, None]).all()
    manifest = json.loads((run / "manifest.json").read_text())
    assert manifest["identity"]["window"] == [W, H]
    assert manifest["summary"]["views"]["side"]["decoded_pct"] == 100
    assert set(manifest["summary"]["materials"]["satin"]["vs_gloss"]) == set(VIEWS)


def test_a_finish_shot_again_under_a_new_preset_brings_the_capture_up_to_date(world, ws):
    from livery import bake

    run = capture_run(world, ws)
    meta = kits.read_meta(ws.kits / "tiny")
    meta["spec_presets"]["satin"] = [0, 110, 64]
    kits.write(ws.kits / "tiny", meta=meta)
    kit = kits.load(ws, "tiny")
    assert bake.stale(world.cfg, ws, kit, run)  # the capture shot satin under the old preset
    before = folder(world)

    assert calibrate.reshoot(world.cfg, ws, kit, [Material.satin], log=world.logs.append) == run

    assert folder(world) == before
    assert bake.stale(world.cfg, ws, kit, run) == []
    assert list(run.glob("jobs/*/*.png")) == []
    manifest = json.loads((run / "manifest.json").read_text())
    new = calibrate.ladder_key(manifest, Material.satin, (0, 110, 64))
    old = calibrate.ladder_key(manifest, Material.satin, (0, 110, 255))
    assert new is not None
    assert old is not None
    assert new != old  # a checkout still on the old preset keeps reading its own ladder
    with np.load(run / "side" / "ladder.npz") as z:
        assert (z[new] == z["levels"][:, None, None]).all()


def test_other_sim_runs_get_the_sim_between_paints(world, ws):
    got: dict = {}

    def other():
        with simlock.sim_lock("another run", timeout=60):
            got["at"] = world.grabs

    world.on_grab = lambda n: threading.Thread(target=other).start() if n == 3 else None

    capture_run(world, ws)

    assert 3 < got["at"] < world.grabs


def test_a_stopped_capture_carries_on_after_its_last_finished_paint(world, ws, monkeypatch):
    shot: list[str] = []
    real = calibrate.SimSession
    monkeypatch.setattr(
        calibrate, "SimSession", lambda cfg, what, **kw: shot.append(what) or real(cfg, what, **kw)
    )
    names = [j.name for j in calibrate.jobs(kits.load(ws, "tiny"))]
    jobs = lambda: [n for n in names if any(x.endswith(f": {n}") for x in shot)]  # noqa: E731
    click_in(world, job=8)
    before = folder(world)

    with pytest.raises(SimInFront):
        capture_run(world, ws)

    assert folder(world) == before
    assert calibrate.current(ws, "tiny") is None
    assert jobs() == names[:9]
    shot.clear()
    world.front, world.on_grab = False, lambda n: None

    run = capture_run(world, ws, resume=True)

    assert calibrate.current(ws, "tiny") == run
    assert jobs() == names[8:]  # the paint it stopped in, and the rest


def test_every_paint_is_shot_at_the_policys_window_whatever_the_owner_sets(world, ws, monkeypatch):
    before = folder(world)
    owners = (W + 10, H)
    monkeypatch.setattr(simsession, "READY_WAIT_S", 0.0)
    real = calibrate.SimSession

    def session(cfg, what, **kw):
        if what.endswith(f": {graycode.names()[3]}"):
            world.size, world.unsizable = owners, "it is maximised"
        return real(cfg, what, **kw)

    monkeypatch.setattr(calibrate, "SimSession", session)

    with pytest.raises(SimNotReady):
        capture_run(world, ws)

    assert folder(world) == before
    world.unsizable = None
    monkeypatch.setattr(calibrate, "SimSession", real)
    run = capture_run(world, ws, resume=True)
    assert json.loads((run / "manifest.json").read_text())["summary"]["views"]["side"]["decoded_pct"] == 100
    assert world.size == owners


def test_a_capture_is_stale_once_the_policy_window_changes(world, ws, tmp_path):
    from livery import bake

    kit = kits.load(ws, "tiny")
    run = capture_run(world, ws)
    assert bake.stale(world.cfg, ws, kit, run) == []
    write_policy(tmp_path / "wider.toml", (W + 10, H))

    assert bake.stale(world.cfg, replace(ws, policy_file=tmp_path / "wider.toml"), kit, run)


def test_a_capture_from_before_the_window_was_in_its_identity_holds_at_the_size_it_was_shot_at(world, ws):
    from livery import bake

    kit = kits.load(ws, "tiny")
    run = capture_run(world, ws)
    manifest = json.loads((run / "manifest.json").read_text())
    del manifest["identity"]["window"]
    for size, holds in (([W, H], True), ([W + 10, H], False)):
        (run / "manifest.json").write_text(json.dumps(manifest | {"window": size}), encoding="utf-8")
        assert (bake.stale(world.cfg, ws, kit, run) == []) == holds


def test_a_capture_only_resumes_on_the_same_shots(world, ws):
    click_in(world, job=1)
    with pytest.raises(SimInFront):
        capture_run(world, ws)
    world.front, world.on_grab = False, lambda n: None
    kits.write_profile(ws.kits / "tiny", Profile(REPLAY, 200, VIEWS, {}))

    with pytest.raises(LiveryError):
        capture_run(world, ws, resume=True)


def test_compensation_is_the_grey_a_darker_finish_needs_until_its_white_runs_out():
    levels = [0, 40, 80, 120, 160, 200, 255]
    gloss = [float(x) for x in levels]
    half = [x / 2 for x in gloss]

    c = calibrate.compensation(levels, gloss, half)

    assert c["needs"] == [0, 80, 160, 240, None, None, None]
    assert c["reachable"] == pytest.approx(127.5)
