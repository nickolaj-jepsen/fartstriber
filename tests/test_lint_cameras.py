"""The output lint's warnings about what the preview cameras see, on a synthetic capture of the two-island
kit (`tests.conftest.two_views`): the left island head-on with a fold at v = FOLD, the next quarter seen
only edge-on, the last quarter by no view."""

import numpy as np
import pytest

from livery import body, visibility
from livery.kit import load
from livery.lint.output import LINT_SCALE, lint_output
from livery.lint.run import thresholds
from livery.model import Element, Livery
from livery.primitives import waive
from tests.conftest import FOLD, TWO, capture, two_islands, two_views

CAMERA_CHECKS = {"unseen", "screen_width", "crease", "hidden_detail"}


def rect(x0: float, y0: float, x1: float, y1: float) -> str:
    return f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" fill="#111111"/>'


def squares(x0: float, y0: float, n: int = 6, size: float = 10) -> str:
    return "".join(
        rect(x0 + 16 * (i % 3), y0 + 16 * (i // 3), x0 + 16 * (i % 3) + size, y0 + 16 * (i // 3) + size)
        for i in range(n)
    )


ELEMENTS = [
    Element("clear", "text", rect(8, 8, 56, 48)),
    Element("squeezed", "text", rect(140, 30, 180, 90)),
    Element("hidden", "logo", rect(200, 30, 250, 90)),
    Element("over-fold", "logo", rect(8, FOLD - 22, 60, FOLD + 20)),
    Element("thin", "svg", rect(70, 8, 74, 110)),
    Element("wide", "svg", rect(90, 8, 106, 110)),
    Element("dots", "svg", squares(8, 60)),
    Element("lost-dots", "svg", squares(200, 150)),
    Element("pin", "svg", rect(10, 170, 60, 240)),  # under the plate: shows as a 4 px ring
    Element("plate", "svg", rect(14, 174, 56, 236), z=1),
]


@pytest.fixture
def ws(tmp_path):
    return two_islands(tmp_path)


def lint(ws, elements, with_capture: bool = True):
    kit = load(ws, "two")
    if with_capture:
        capture("two", two_views())
    vis = visibility.table(kit, LINT_SCALE)
    return lint_output(
        Livery("#808080", elements=list(elements)),
        kit,
        ws,
        np.zeros((TWO, TWO, 3), np.uint8),
        thresholds(),
        vis,
    )


@pytest.fixture
def found(ws) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for f in lint(ws, ELEMENTS):
        if f.check in CAMERA_CHECKS:
            assert f.level == "warning", f
            out.setdefault(f.id, set()).add(f.check)
    return out


@pytest.mark.parametrize(
    ("element", "checks"),
    [
        ("clear", set()),
        ("squeezed", {"unseen"}),
        ("hidden", {"unseen"}),
        ("over-fold", {"crease"}),
        ("thin", {"screen_width"}),
        ("wide", set()),
        ("dots", set()),
        ("lost-dots", {"hidden_detail"}),
        ("pin", {"screen_width"}),
        ("plate", set()),
    ],
)
def test_the_lint_warns_about_what_the_cameras_hide_shrink_or_bend(found, element, checks):
    assert found.get(element, set()) == checks


def test_a_warning_names_the_views(ws):
    (msg,) = [f.message for f in lint(ws, ELEMENTS) if f.id == "squeezed" and f.check == "unseen"]
    assert "edge-on" in msg
    (msg,) = [f.message for f in lint(ws, ELEMENTS) if f.id == "over-fold"]
    assert "head-on" in msg


def test_a_camera_check_can_be_waived(ws):
    waived = waive(ELEMENTS[2], "unseen", "a test")
    (f,) = [f for f in lint(ws, [waived]) if f.check == "unseen"]
    assert f.level == "waived"


def test_without_a_capture_the_camera_checks_are_skipped_with_one_note(ws):
    found = lint(ws, ELEMENTS, with_capture=False)
    assert not [f for f in found if f.check in CAMERA_CHECKS]
    assert [(f.level, f.check) for f in found if f.check == "no_capture"] == [("warning", "no_capture")]


def test_one_report_holds_the_camera_checks_and_the_body_models(ws):
    body.label(
        load(ws, "two"),
        {"parts": {"front": {"islands": ["i001"], "side": "left", "confidence": "sure"}}},
        {"features": {"light": {"view": "side_l", "kind": "keep_clear", "box": [0.6, 0.6, 0.9, 0.9]}}},
    )
    found = {(f.level, f.check) for f in lint(ws, ELEMENTS) if f.id == "hidden"}
    assert {("error", "keep_clear"), ("warning", "unseen")} <= found
