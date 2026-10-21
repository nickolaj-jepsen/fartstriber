import math
import struct
from dataclasses import replace

import pytest

from livery import cameras, config
from livery import kit as kits
from livery.config import REPO, Workspace
from livery.kit import CarSize, Profile, View
from tests.conftest import write_kit

TRACK = "daytona\\2011\\road"
ORIGIN = (0.0, 0.0, 0.0)


def preview(groups: dict[str, str]) -> dict[str, View]:
    return {v: View(g, *cameras.PREVIEW[v]) for v, g in groups.items()}


def test_a_profile_without_poses_is_filled_with_the_defaults(ws):
    kit = kits.load(ws, "tiny")

    views = cameras.preview_views(kit, ws.policy.preview)

    assert all(v.pose for v in views.values())
    assert {n: kits.profile(kit).preview[n] for n in views} == views
    moved = View("Side", (0.0, 20.0, 0.5, *views["side_l"].pose[3:]), views["side_l"].aim)
    prof = kits.profile(kit)
    kits.write_profile(kit.dir, replace(prof, preview=prof.preview | {"side_l": moved}))
    assert cameras.preview_views(kit, ws.policy.preview)["side_l"] == moved  # a pose the profile has is used


def track_camera_set(groups: dict[str, list[tuple[str, int]]]) -> bytes:
    """A track set with these groups, each a list of (camera name, type)."""
    recs = []
    for cams in groups.values():
        for i, (name, kind) in enumerate(cams):
            head = name.encode().ljust(32, b"\0") + struct.pack("<4I", kind, 0, 1, 0)
            recs.append(head + struct.pack("<I33f", 0, *[float(i + 1)] * 33))
    return cameras.build_track_set(recs, [(0, g, [n for n, _ in cams]) for g, cams in groups.items()])


STOCK_TRACK = {"TV1": [("CamTV1_00", 0), ("CamTV1_01", 0)], "Chase": [("CamChase", 3)]}


def test_preview_views_are_added_as_groups_of_their_own_beside_the_stock_cameras():
    stock = track_camera_set(STOCK_TRACK)

    out = cameras.preview_set(stock, preview({"side_l": "Preview Side L", "top": "Preview Top"}))

    recs, groups = cameras.track_set(out)
    stock_recs, stock_groups = cameras.track_set(stock)
    assert recs[: len(stock_recs)] == stock_recs
    assert groups[: len(stock_groups)] == stock_groups
    added = {g: cams for _f, g, cams in groups[len(stock_groups) :]}
    assert set(added) == {"Preview Side L", "Preview Top"}
    by_name = {cameras.name_of(r): r for r in recs}
    side = by_name[added["Preview Side L"][0]]
    x, y, _z = struct.unpack_from("<3f", side, cameras.NAME + 4)
    assert y > 10
    assert abs(x) < 1
    assert struct.unpack_from("<I", side, 32)[0] == struct.unpack_from("<I", by_name["CamChase"], 32)[0]


@pytest.mark.parametrize(
    ("stock", "groups"),
    [
        (STOCK_TRACK, {"side_l": "TV1"}),  # a stock group would be shadowed
        (STOCK_TRACK, {"side_l": "P", "top": "P"}),  # one camera can't shoot two views
        ({"TV1": [("CamTV1_00", 0)]}, {"side_l": "P"}),  # no car-relative camera to copy
    ],
)
def test_preview_sets_the_sim_would_not_shoot_as_meant_are_refused(stock, groups):
    with pytest.raises(cameras.CameraSetError):
        cameras.preview_set(track_camera_set(stock), preview(groups))


def test_preview_install_and_restore_round_trip_and_never_keep_a_preview_set_as_stock(tmp_path):
    views = preview({"side_l": "Preview Side L", "rear34": "Preview Rear34"})
    cam = cameras.track_cam(tmp_path, TRACK)
    cam.parent.mkdir(parents=True)
    stock = track_camera_set(STOCK_TRACK)
    cam.write_bytes(stock)
    ini = tmp_path / "camera.ini"
    ini.write_text("[tracks\\daytona\\2011\\road]\nLastCameraGroup=TV1\nLastTrackSet=\n", encoding="utf-8")

    with pytest.raises(cameras.CameraSetError):  # the pointer needs setting, which the running sim would undo
        cameras.install_previews(tmp_path, TRACK, views, sim_running=True)
    cameras.install_previews(tmp_path, TRACK, views, sim_running=False)

    assert {g for _f, g, _c in cameras.track_set(cam.read_bytes())[1]} >= {"Preview Side L", "Preview Rear34"}
    assert "LastTrackSet=tracks\\daytona\\2011\\road\\track.cam" in ini.read_text()
    cameras.install_previews(tmp_path, TRACK, views, sim_running=True)  # once read, views can change live

    cameras.restore_previews(tmp_path, TRACK, sim_running=False)

    assert cam.read_bytes() == stock
    assert "LastTrackSet=\n" in ini.read_text()
    assert "LastCameraGroup=TV1" in ini.read_text()

    cameras.install_previews(tmp_path, TRACK, views, sim_running=False)
    cam.with_name("track.cam.stock").unlink()  # the kept stock set is lost while the previews are in
    with pytest.raises(cameras.CameraSetError):
        cameras.install_previews(tmp_path, TRACK, views, sim_running=False)


# Poses sized to the car.


def test_a_car_as_big_as_the_mclaren_gets_the_mclarens_cameras():
    mclaren = kits.profile(kits.load(Workspace(), "mclaren-720s-gt3-evo"))
    size = cameras.car_size(mclaren)

    assert {v: (view.pose, view.aim) for v, view in mclaren.preview.items()} == cameras.preview_poses(size)


def _spans(car: CarSize, pose: tuple[float, ...]) -> tuple[float, float]:
    """How much of a side view's frame width the car's length and height take, at the car's origin."""
    across = 2 * abs(pose[1]) * math.tan(pose[5] / 2)
    return car.length / across, car.height / across


@pytest.mark.parametrize("grow", [(1.25, 1.0), (1.0, 1.4), (0.8, 0.9), (1.2, 1.3)])
def test_the_car_takes_the_frame_the_mclaren_takes_whatever_its_size(grow):
    car = CarSize(cameras.REF.length * grow[0], cameras.REF.height * grow[1])
    ref, now = cameras.preview_poses(), cameras.preview_poses(car)

    length, height = _spans(car, now["side_l"][0])
    ref_length, ref_height = _spans(cameras.REF, ref["side_l"][0])
    assert length <= ref_length * (1 + 1e-9)  # it never runs off where the McLaren wouldn't
    assert height <= ref_height * (1 + 1e-9)
    assert max(length / ref_length, height / ref_height) == pytest.approx(1)  # nor shrinks to a speck
    farther = cameras.scale(car) > 1
    for v in ref:
        assert (math.dist(now[v][0][:3], ORIGIN) > math.dist(ref[v][0][:3], ORIGIN)) == farther
        assert now[v][0][5] == ref[v][0][5]
    taller = grow[1] > 1
    assert (now["side_l"][0][2] > ref["side_l"][0][2]) == taller  # level with the middle of its side
    assert (now["side_l"][1] > ref["side_l"][1]) == taller  # aimed to centre it


def test_a_kits_group_names_fit_the_camera_set_and_change_with_the_pose():
    pose, aim = cameras.preview_poses()["front34"]
    long_name = "a-kit-with-a-rather-long-name-indeed"
    name = cameras.group_name(long_name, "front34", pose, aim)
    bigger = cameras.preview_poses(CarSize(5.0, 1.2))["front34"][0]

    out = cameras.preview_set(track_camera_set(STOCK_TRACK), {"front34": View(name, pose, aim)})

    assert cameras.group_name(long_name, "front34", bigger, aim) != name
    recs, groups = cameras.track_set(out)
    assert name in {g for _f, g, _c in groups}
    assert f"Cam{name}" in {cameras.name_of(r) for r in recs}


def _kit(ws: Workspace, name: str, views: dict[str, View], track_name: str | None = None) -> kits.Kit:
    d = ws.kits / name
    write_kit(d, {"canvas": 64, "paint_folder": name, "bleed_px": 0})
    kits.write_profile(d, Profile(4242, 100, views, {}, track_name=track_name))
    return kits.load(ws, name)


def _views(kit: str, names, size: CarSize = cameras.REF) -> dict[str, View]:
    poses = cameras.preview_poses(size)
    return {n: View(cameras.group_name(kit, n, *poses[n]), *poses[n]) for n in names}


def test_adding_a_kits_cameras_keeps_every_other_kits_on_the_track(tmp_path):
    ws = Workspace(root=tmp_path / "w", assets=REPO / "assets")
    cfg = config.load()
    names = ws.policy.preview
    mine = {"a": _views("a", names), "b": _views("b", names, CarSize(5.0, 1.1))}
    for kit, views in mine.items():
        _kit(ws, kit, views)
    _kit(ws, "elsewhere", _views("elsewhere", names), track_name="another track")
    cam = cameras.track_cam(tmp_path, cfg.replay.track)
    cam.parent.mkdir(parents=True)
    cam.write_bytes(track_camera_set(STOCK_TRACK))
    (tmp_path / "camera.ini").write_text("", encoding="utf-8")

    views = cameras.track_views(ws, cfg, cfg.replay.track, names)
    cameras.install_previews(tmp_path, cfg.replay.track, views, sim_running=False)

    assert {k.split()[0] for k in views} == {"a", "b"}
    for views in mine.values():
        assert cameras.previews_missing(tmp_path, cfg.replay.track, views) == []
    moved = _views("a", names, CarSize(4.0, 1.0))
    assert cameras.previews_missing(tmp_path, cfg.replay.track, moved) == sorted(names)


def test_two_kits_may_not_put_one_group_in_two_places(tmp_path):
    ws = Workspace(root=tmp_path / "w", assets=REPO / "assets")
    cfg = config.load()
    pose, aim = cameras.preview_poses()["side_l"]
    _kit(ws, "a", {"side_l": View("Shared", pose, aim)})
    _kit(ws, "b", {"side_l": View("Shared", (0.0, 30.0, *pose[2:]), aim)})

    with pytest.raises(cameras.CameraSetError):
        cameras.track_views(ws, cfg, cfg.replay.track, ["side_l"])


def test_arming_waits_for_the_sim_to_close_only_while_camera_ini_must_change(tmp_path):
    cam = cameras.track_cam(tmp_path, TRACK)
    ini = tmp_path / "camera.ini"
    ini.write_text("[tracks\\daytona\\2011\\road]\nLastTrackSet=\n", encoding="utf-8")
    with pytest.raises(cameras.CameraSetError):  # no set saved from the sim's camera tool yet
        cameras.armed(tmp_path, TRACK, lambda: False)
    cam.parent.mkdir(parents=True)
    cam.write_bytes(track_camera_set(STOCK_TRACK))
    polls, logs = iter(range(3)), []  # the sim runs for three polls, then the owner closes it

    assert (
        cameras.armed(tmp_path, TRACK, lambda: next(polls, None) is not None, logs.append, poll_s=0) is False
    )
    assert len(logs) == 1

    pointed = "[tracks\\daytona\\2011\\road]\nLastTrackSet=tracks\\daytona\\2011\\road\\track.cam\n"
    ini.write_text(pointed, encoding="utf-8")
    assert cameras.armed(tmp_path, TRACK, lambda: True, logs.append, poll_s=0) is True  # it may write now


def test_the_kit_commands_write_back_a_profile_as_they_read_it(tmp_path):
    src = REPO / "kits" / "mclaren-720s-gt3-evo" / kits.PROFILE
    d = tmp_path / "k"
    d.mkdir()
    (d / kits.PROFILE).write_bytes(src.read_bytes())
    kit = kits.Kit("k", d, 2048, "x", 16, {})

    kits.write_profile(d, kits.profile(kit))
    assert (d / kits.PROFILE).read_bytes() == src.read_bytes().replace(b"\r\n", b"\n")

    new = replace(kits.profile(kit), frame=None, track_name="some track", car=CarSize(4.6, 1.3))
    kits.write_profile(d, new)
    assert kits.profile(kit) == new
    with pytest.raises(kits.KitError):  # a profile without a frame shoots nothing yet
        new.shot("side_l")
