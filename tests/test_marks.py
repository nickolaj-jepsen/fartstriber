"""Race markings: the drawn set, their placement at a car-space anchor, and their review."""

import re
import shutil

import pytest

from livery import approvals, marks, sponsors
from livery.config import Workspace
from livery.lint.run import thresholds
from livery.primitives import marks as place

DOOR = 0.6, 0.6  # on the door skin, both sides


def arrow(point_right: bool) -> str:
    """A stand-in pointing marking: a block with a square notch on the end it points away from."""
    d = "M0,0 h80 l40,50 l-40,50 h-80 Z" if point_right else "M40,0 h80 v100 h-80 l-40,-50 Z"
    return sponsors.path_svg(d, (0, 0, 120, 100))


@pytest.fixture(scope="module")
def drawn(tmp_path_factory):
    """The stand-in markings, drawn and measured once; each test gets its own unapproved copy."""
    r = tmp_path_factory.mktemp("marks")
    marks.save(r / "tow", {"right": arrow(True), "left": arrow(False)})
    marks.save(r / "e", {"mark": sponsors.path_svg("M0,0 h100 v100 h-100 Z", (0, 0, 100, 100))})
    return r


@pytest.fixture
def root(drawn, tmp_path, monkeypatch):
    r = tmp_path / "marks"
    shutil.copytree(drawn, r)
    monkeypatch.setattr(marks, "MARKS", r)
    return r


@pytest.fixture
def kit(real_kit):
    return real_kit


def test_every_drawn_marking_is_one_colour_and_measured():
    folders = sorted(p.parent for p in marks.MARKS.glob("*/brand.toml"))
    assert folders
    for d in folders:
        m = marks.load(d.name)
        assert m.variants
        assert set(m.variants) <= set(marks.VARIANTS)
        for v in m.variants.values():
            svg = v.path.read_text(encoding="utf-8")
            assert len(set(re.findall(r'fill="(#[0-9a-fA-F]+)"', svg))) == 1
            assert 0 < v.ink < 1
            assert v.min_height_px(thresholds().min_stroke_px) <= sponsors.SILL_PX  # holds in a sill row


def test_a_marking_not_yet_approved_is_placed_and_listed_for_the_final_review(kit, root):
    el = place(kit, "e", "e", ("side_l", *DOOR), "#ffffff", 0.03)
    assert el.kind == "logo"
    assert el.car["mark"] == "e"
    placed = sponsors.placed([el])
    assert placed == {"sponsors": [], "marks": ["e/mark"]}
    ws = Workspace(root=root.parent, assets=root.parent)
    assert approvals.unapproved(ws, placed)
    sponsors.approve(root / "e")
    assert not approvals.unapproved(ws, placed)


def test_a_marking_is_anchored_at_a_feature_of_the_body_by_name(kit, root):
    name, f = next((n, f) for n, f in kit.features().items() if f.shape == "box" and f.view == "side_l")
    by_name = place(kit, "e", "e", name, "#ffffff", 0.03)
    assert by_name.svg == place(kit, "e", "e", (f.view, *f.at), "#ffffff", 0.03).svg
    assert by_name.car["view"] == "side_l"


def test_a_pointing_marking_points_at_the_end_asked_for_on_each_side(kit, root):
    picked = {
        (view, toward): place(kit, "t", "tow", (view, *DOOR), "#000000", 0.03, toward=toward).car["variant"]
        for view in ("side_l", "side_r")
        for toward in ("nose", "tail")
    }
    # Lines read nose to tail on the left side and tail to nose on the right.
    assert picked == {
        ("side_l", "nose"): "left",
        ("side_l", "tail"): "right",
        ("side_r", "nose"): "right",
        ("side_r", "tail"): "left",
    }
    with pytest.raises(sponsors.SponsorError):
        place(kit, "t", "tow", ("side_l", *DOOR), "#000000", 0.03)  # it only comes pointing
    with pytest.raises(sponsors.SponsorError):
        place(kit, "t", "tow", ("side_l", *DOOR), "#000000", 0.001, toward="nose")  # below its minimum


def test_review_lists_unapproved_markings_until_approved(root, tmp_path):
    items = marks.review_items(root, out=tmp_path / "sheets")
    assert {i["id"] for i in items} == {"mark:tow", "mark:e"}
    assert all((tmp_path / "sheets" / i["id"].split(":")[1] / "sheet.png").exists() for i in items)
    marks.approve("tow", root)
    assert all(v.approved for v in marks.load("tow").variants.values())
    assert not any(v.approved for v in marks.load("e").variants.values())
    assert [i["id"] for i in marks.review_items(root, out=tmp_path / "sheets")] == ["mark:e"]


def test_the_driver_banner_is_one_line_on_a_plate(kit):
    plate, text = marks.driver_banner(kit, "name", "side_l", "n. driver", DOOR, "#ffffff", plate="#000000")
    assert plate.id == "name.plate"
    assert text.kind == "text"
    assert text.car["lines"] == ["N. DRIVER"]
