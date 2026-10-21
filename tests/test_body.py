"""The kit's body model: `kit label` checks and writes it, primitives take its part names, marks its
features, and the lints and the kit review use it. On a synthetic kit of two islands, and the real kit."""

import json
import shutil
import textwrap

import numpy as np
import pytest

from livery import body, carspace, cli, geom, patterns
from livery import kit as kits
from livery.config import REPO, Workspace
from livery.hashing import kit_hash
from livery.kit import KitError, load
from livery.lint.output import lint_output
from livery.lint.run import lint_design, thresholds
from livery.lint.source import findings
from livery.model import Livery
from livery.primitives import clip_out, clip_to, panel, shape, text
from livery.review.items import kit_items
from tests.conftest import TWO, drawn, two_islands

# The two-island kit: i001 is the front half of side_l (u < 0.5), i002 the rear half, both mapped by
# x = TWO u, y = TWO (1 - v).
HALF = (TWO / 2) ** 2  # a quarter of the canvas, in px²
UPPER_REAR = [(0.5, 0.5), (1.1, 0.5), (1.1, 1.1), (0.5, 1.1)]
LIGHT = [0.6, 0.6, 0.9, 0.9]  # on the upper rear
BLADE = [0.1, 0.1, 0.4, 0.4]  # on the front


def labels(**extra) -> tuple[dict, dict]:
    parts = {
        "parts": {
            "front": {"islands": ["i001"], "side": "left", "confidence": "sure"},
            "rear_upper": {
                "islands": ["i002"],
                "side": "left",
                "confidence": "likely",
                "cut": {"view": "side_l", "polygon": UPPER_REAR},
            },
            "rear_lower": {"islands": ["i002"], "side": "left", "confidence": "unsure", "rest": True},
        },
        "groups": {"side": ["front", "rear_upper", "rear_lower"]},
        "questions": ["Is the rear really two panels?"],
        "fit_problems": ["i002's map claims more than the sim shows."],
    }
    features = {
        "features": {
            "light": {"view": "side_l", "kind": "keep_clear", "box": LIGHT, "label": "the sim's light"},
            "lamp": {"same_as": "light"},
            "blade": {"view": "side_l", "kind": "occluder", "box": BLADE, "note": "hides it from above"},
            "crease": {"view": "side_l", "kind": "line", "line": [[0.1, 0.5], [0.9, 0.5]]},
            "tow": {"view": "side_l", "kind": "place", "point": [0.25, 0.75]},
        }
    }
    parts["parts"] |= extra.get("parts", {})
    features["features"] |= extra.get("features", {})
    return parts, features


@pytest.fixture
def ws(tmp_path) -> Workspace:
    ws = two_islands(tmp_path)
    kits.approve(ws.kits / "two", "Tester", "")
    return ws


@pytest.fixture
def kit(ws):
    k = load(ws, "two")
    body.label(k, *labels())
    return k


def test_labels_read_back_as_parts_groups_and_features(kit):
    assert kit.part("front").islands == ("i001",)
    assert kit.part("rear_upper").cut is not None
    assert kit.part("side").group
    assert kit.feature("lamp") == kit.feature("light")  # another name for the same feature
    assert kit.feature("tow").at == (0.25, 0.75)
    assert kit.feature("light").box == tuple(LIGHT)
    assert set(kit.parts()) == {"front", "rear_upper", "rear_lower", "side"}
    with pytest.raises(KitError, match="rear_upper"):
        kit.part("rear")


def test_a_cut_part_and_the_rest_of_its_island_share_it_and_a_group_paints_them_whole(kit):
    upper, lower = body.area(kit, "rear_upper"), body.area(kit, "rear_lower")
    assert upper.area == pytest.approx(HALF, rel=0.01)
    assert lower.area == pytest.approx(HALF, rel=0.01)
    assert upper.intersection(lower).area < 1
    assert body.area(kit, "side").area == pytest.approx(TWO * TWO, rel=0.01)


def test_labels_clear_the_kit_approval_and_review_notes_do_not(ws):
    k = load(ws, "two")
    assert kits.approval(k.dir) is not None
    report = body.label(k, *labels())
    assert report["approval"] == "cleared"
    assert kits.approval(k.dir) is None
    kits.approve(k.dir, "Tester", "")
    before = kit_hash(k.dir)
    parts, features = labels()
    parts["questions"] = ["Another question."]
    assert body.label(k, parts, features)["approval"] == "unchanged"
    assert kit_hash(k.dir) == before
    assert kits.approval(k.dir) is not None


def test_labels_that_dont_fit_the_kit_are_refused_with_every_problem(ws):
    k = load(ws, "two")
    bad = labels(
        parts={
            "ghost": {"islands": ["i999"]},
            "upper_too": {"islands": ["i002"], "cut": {"view": "side_l", "polygon": UPPER_REAR}},
        },
        features={
            "elsewhere": {"view": "side_r", "kind": "place", "box": [0.1, 0.1, 0.2, 0.2]},
            "backwards": {"view": "side_l", "kind": "zone", "box": [0.5, 0.1, 0.2, 0.2]},
            "odd": {"view": "side_l", "kind": "sticker", "point": [0.2, 0.2]},
            "nowhere": {"same_as": "missing"},
        },
    )
    with pytest.raises(KitError) as e:
        body.label(k, *bad)
    message = str(e.value)
    for named in ("i999", "upper_too", "elsewhere", "backwards", "odd", "nowhere"):
        assert named in message
    assert not (k.dir / body.FILE).exists()  # nothing is written


def test_regions_take_part_names_and_still_island_ids(kit):
    whole = drawn(panel(kit, "p", "rear_upper", "#ff0000"))
    assert whole.symmetric_difference(body.area(kit, "rear_upper")).area < 2
    assert drawn(panel(kit, "q", "i001", "#ff0000")).area == pytest.approx(HALF * 2, rel=0.01)
    el = shape(kit, "s", "side_l", [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)], "#00ff00")
    assert "clip-path" in clip_to(kit, "rear_lower", el).svg
    assert "clip-path" in clip_out(kit, ["front", "rear_upper"], el).svg
    tape = patterns.hazard(kit, "h", "rear_lower", ("#000000", "#ffff00"), 48)
    assert drawn(tape).within(body.area(kit, "rear_lower").buffer(1))
    dots = patterns.scatter(kit, "d", "front", [(12, 0.5)], {"#000000": 1.0}, np.random.default_rng(1))
    assert drawn(dots).within(body.area(kit, "front"))
    (trim,) = geom.outline(kit, "o", None, "rear_upper", [(16, "#ffffff")], place="inside")
    assert drawn(trim).within(body.area(kit, "rear_upper").buffer(1))
    with pytest.raises(KitError, match="no part"):
        panel(kit, "r", "bonnet", "#ff0000")
    with pytest.raises(patterns.PatternError):
        patterns.scatter(kit, "d", "i777", [(12, 0.5)], {"#000000": 1.0}, np.random.default_rng(1))


def _lint(ws, kit, *elements) -> dict[str, set[tuple[str, str]]]:
    livery = Livery("#808080").add(*elements)
    paint = np.full((kit.canvas, kit.canvas, 3), 128, np.uint8)
    found: dict[str, set[tuple[str, str]]] = {}
    for f in lint_output(livery, kit, ws, paint, thresholds()):
        found.setdefault(f.id, set()).add((f.level, f.check))
    return found


def test_what_the_sim_draws_over_the_paint_is_strict_for_type_and_an_occluder_warns_for_it(ws, kit):
    light = kit.feature("light")
    blade = kit.feature("blade")
    found = _lint(
        ws,
        kit,
        text(kit, "on-light", "side_l", "AB", light.at, 0.12, "#000000"),
        shape(kit, "under-light", "side_l", [(0.55, 0.55), (0.95, 0.55), (0.95, 0.95), (0.55, 0.95)], "#f00"),
        text(kit, "under-blade", "side_l", "AB", blade.at, 0.12, "#000000"),
        shape(
            kit,
            "paint-under-blade",
            "side_l",
            [(0.05, 0.05), (0.45, 0.05), (0.45, 0.45), (0.05, 0.45)],
            "#0f0",
        ),
    )
    assert ("error", "keep_clear") in found["on-light"]
    assert ("warning", "keep_clear") in found["under-light"]
    assert ("error", "keep_clear") not in found["under-light"]
    assert ("warning", "occluded") in found["under-blade"]
    assert not any(check == "occluded" for _, check in found.get("paint-under-blade", set()))


def test_the_design_lint_names_the_part_a_literal_island_id_is(ws, kit):
    d = ws.design_dir("ids")
    d.mkdir(parents=True)
    (d / "design.py").write_text(
        textwrap.dedent(
            """
            from livery.primitives import base, panel

            KIT = "two"


            def build(kit, p):
                return base("#808080").add(panel(kit, "a", "i002", "#ff0000"), panel(kit, "b", "front", "#00f"))
            """
        ),
        encoding="utf-8",
    )
    warned = [w for w in lint_design(ws, "ids")["warnings"] if "i002" in w["message"]]
    assert len(warned) == 1
    assert "rear_upper" in warned[0]["message"]
    assert "rear_lower" in warned[0]["message"]
    assert not [f for f in findings(d / "design.py") if f["level"] == "warning"]  # no kit, no warning


def test_the_kit_review_lists_the_unsure_names_the_questions_and_the_map_problems(ws, kit):
    from livery.kit_review import body_overlay

    (item,) = kit_items(ws, "two")
    assert "rear_lower" in item["body"]
    assert "Is the rear really two panels?" in item["body"]
    assert "i002's map claims more" in item["body"]
    pictures = body_overlay(kit, ws)
    assert [p.name for p in pictures] == ["parts.png", "body.png"]
    assert all(p.exists() for p in pictures)


def test_kit_label_is_a_command_that_prints_its_report(ws, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "Workspace", lambda: ws)
    parts, features = labels()
    (tmp_path / "parts.json").write_text(json.dumps(parts), encoding="utf-8")
    (tmp_path / "features.json").write_text(json.dumps(features), encoding="utf-8")
    with pytest.raises(SystemExit) as done:
        cli.app(["kit", "label", "two", str(tmp_path / "parts.json"), str(tmp_path / "features.json")])
    assert done.value.code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["parts"] == 3
    assert report["unsure"]
    assert report["unsure"][0].startswith("rear_lower")


# --- the real kit --------------------------------------------------------------------------------------

KIT = "mclaren-720s-gt3-evo"


@pytest.fixture(scope="module")
def mclaren(tmp_path_factory):
    root = tmp_path_factory.mktemp("ws")
    shutil.copytree(REPO / "kits" / KIT, root / "kits" / KIT)
    return load(Workspace(root=root, assets=REPO / "assets"), KIT)


def test_every_part_of_the_real_kit_paints_something_and_its_cuts_lint_clean(mclaren):
    for name, p in mclaren.parts().items():
        area = body.area(mclaren, name)
        assert area.area > 1, name
        if p.cut is not None or p.rest:  # a split part's edge in open paint follows a body line
            assert not geom.failing(mclaren, area), name


def test_the_real_kit_has_its_sim_overlays_on_both_sides(mclaren):
    for side in mclaren.side_views:
        zones = [(f, foot) for f, foot in body.zones(mclaren, "keep_clear") if f.view == side]
        assert zones
        for f, foot in zones:
            carspace.anchor(mclaren, side, f.at)  # its middle is on mapped paint, or this raises
            painted = [
                n
                for n, p in mclaren.parts().items()
                if not p.group and body.area(mclaren, n).intersects(foot)
            ]
            assert painted, f.name
