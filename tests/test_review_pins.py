"""Pins: boxes the owner draws on a final's pictures, resolved on Send to the elements under them, carried to
the builder and judged one by one by the critic."""

import json
from pathlib import Path

import numpy as np
import pytest

from livery import critic, graycode, prefs, state
from livery.compile import compile_svg
from livery.config import Workspace
from livery.designs import build_livery
from livery.review import apply as review_apply
from livery.review import items as review_items
from livery.review import pins
from tests.conftest import CANVAS, checked, sim_run
from tests.reviewpage import Review
from tests.test_review_critic import verdict

TWO_PANELS = """\
def build(kit, p):
    return Livery(base="#3060c0").add(
        svg('<rect x="0" y="0" width="16" height="64" fill="#ff0000"/>', id="front-panel"),
        svg('<rect x="16" y="0" width="16" height="64" fill="#00ff00"/>', id="rear-panel"),
    )
"""
LEFT, RIGHT, BOTH = [0.05, 0.2, 0.4, 0.8], [0.6, 0.2, 0.95, 0.8], [0.3, 0.2, 0.9, 0.8]
SKY = [0.0, 0.0, 0.1, 0.06]  # above the car: the capture decoded nothing there


def _capture(ws: Workspace) -> None:
    """A calibration capture of the tiny kit whose side_l sees the car's half of the texture across its
    whole frame below the top tenth: x runs along the texture's x, 0 to CANVAS/2, y along its y."""
    h, w = 45, 90  # the shape of the sim shots (900 x 450)
    ys, xs = np.mgrid[0:h, 0:w]
    pix = ys >= h // 10
    uv = np.stack([xs[pix] * (CANVAS / 2) / w, (ys[pix] - h // 10) * CANVAS / (h - h // 10)], 1)
    n = len(uv)
    vm = graycode.ViewMap(
        car=pix,
        pix=pix,
        uv=uv.astype(np.float32),
        jac=np.zeros((n, 2, 2), np.float32),
        level=np.zeros((n, 2), np.int8),
        conf=np.ones(n, np.float16),
        agree=np.ones(n, bool),
    )
    root = state.root() / "kit-capture" / "tiny"
    vm.save(root / "20260101-000000" / "side_l" / "geometry.npz")
    (root / "current").write_text("20260101-000000", encoding="utf-8")


def _final(ws: Workspace, capture: bool = True) -> dict:
    """final:d, two panels side by side, checked and shot in the sim, with its build's layers.svg."""
    check = checked(ws, "d", TWO_PANELS)
    livery, kit = build_livery(ws, "d")
    (ws.out("d") / "layers.svg").write_text(compile_svg(livery, kit), encoding="utf-8")
    sim_run(ws, "d", "20260101-000000", check["digests"])
    if capture:
        _capture(ws)
    (item,) = review_items.design_items(ws, ["d"])
    return item


def _pin(view: str, box: list[float], note: str) -> dict:
    return {"view": view, "box": box, "note": note}


def _sent(ws: Workspace, item: dict, decision: dict) -> dict:
    """The ledger line of the decision, applied as Send applies it."""
    review_apply.apply(ws, [item], {item["id"]: decision})
    return prefs.decisions(ws)[-1]


def test_a_pin_on_a_sim_shot_names_the_elements_under_it_through_the_capture(ws):
    item = _final(ws)
    line = _sent(
        ws,
        item,
        {
            "decision": "edit",
            "note": "",
            "pins": [
                _pin("side_l", LEFT, "darker"),
                _pin("side_l", RIGHT, "thinner"),
                _pin("side_l", BOTH, "join them"),
            ],
        },
    )

    left, right, both = line["pins"]
    assert left["elements"] == ["front-panel"]
    assert right["elements"] == ["rear-panel"]
    assert set(both["elements"]) == {"front-panel", "rear-panel"}
    assert both["elements"][0] == "rear-panel"  # the box covers more of it
    assert left["template"][2] <= 16 <= right["template"][0]
    for p in line["pins"]:
        assert (state.root() / p["crop"]).is_file()
    assert pins.line(left) == "on side_l, over front-panel: darker"


def test_a_pin_that_resolves_to_nothing_travels_as_view_box_and_crop(ws):
    item = _final(ws, capture=False)
    line = _sent(ws, item, {"decision": "edit", "note": "", "pins": [_pin("side_l", LEFT, "darker")]})

    (pin,) = line["pins"]
    assert "elements" not in pin
    assert "template" not in pin
    assert (pin["view"], pin["box"], pin["note"]) == ("side_l", LEFT, "darker")
    assert (state.root() / pin["crop"]).is_file()
    assert pins.line(pin).startswith("on side_l, ")
    assert pins.line(pin).endswith(": darker")


def test_a_pin_over_no_paint_names_no_element(ws):
    item = _final(ws)
    (pin,) = _sent(ws, item, {"decision": "edit", "note": "", "pins": [_pin("side_l", SKY, "sky")]})["pins"]
    assert "elements" not in pin


@pytest.mark.parametrize(
    "decision",
    [
        {"decision": "accept", "pins": [_pin("side_l", LEFT, "x")]},  # pins go with a change
        {"decision": "edit", "pins": [_pin("rear34", LEFT, "x")]},  # a view the final doesn't show
        {"decision": "edit", "pins": [_pin("side_l", [0.5, 0.2, 0.4, 0.8], "x")]},  # x0 > x1
        {"decision": "edit", "pins": [_pin("side_l", [0.1, 0.2, 1.4, 0.8], "x")]},  # off the picture
    ],
)
def test_the_page_refuses_pins_that_say_nothing_it_can_place(ws, decision):
    review = Review(ws, [_final(ws)])
    assert review.post("api/decide", {"id": "final:d", **decision}) == 400
    review.post("api/clear", {})


def test_pins_alone_say_what_to_change_and_reach_the_builder_with_their_crops(ws):
    review = Review(ws, [_final(ws)])
    unsaid = {"id": "final:d", "decision": "edit", "pins": [_pin("flat/views.png", LEFT, "")]}
    assert review.post("api/decide", unsaid) == 200
    assert review.post("api/apply", {}) == 409  # a pin without a note says nothing yet
    said = {"id": "final:d", "pins": [_pin("side_l", LEFT, "darker"), _pin("flat/views.png", RIGHT, "")]}
    assert review.post("api/decide", said) == 200

    (decision,) = review.apply()["decisions"]

    first, second = decision["pins"]
    assert first["line"] == "on side_l, over front-panel: darker"
    assert Path(first["path"]).is_file()
    assert Path(second["path"]).is_file()


def test_every_review_starts_without_pins_and_shows_the_last_ones_where_they_were(ws):
    item = _final(ws)
    _sent(ws, item, {"decision": "edit", "note": "", "pins": [_pin("side_l", LEFT, "darker")]})

    (again,) = review_items.design_items(ws, ["d"])

    assert [(p["view"], p["box"], p["note"]) for p in again["last_decision"]["pins"]] == [
        ("side_l", LEFT, "darker")
    ]
    (point,) = [p for p in again["timeline"] if p["who"] == "you"]
    assert point["pins"] == [{"view": "side_l", "box": LEFT, "note": "darker"}]
    assert not again.get("pins")


def _pinned_round(ws: Workspace) -> Path:
    item = _final(ws)
    _sent(ws, item, {"decision": "edit", "note": "", "pins": [_pin("side_l", LEFT, "darker")]})
    return critic.pack(ws, ["d"])


def test_a_round_pack_shows_each_pin_then_and_now(ws):
    folder = _pinned_round(ws)

    entry = json.loads((folder / "manifest.json").read_text())["designs"]["d"]
    (pin,) = json.loads((folder / entry["owner_note"]).read_text())["pins"]
    assert (pin["note"], pin["over"]) == ("darker", ["front-panel"])
    assert (folder / pin["then"]).is_file()
    assert (folder / pin["now"]).is_file()


@pytest.mark.parametrize("status", ["resolved", "partly"])
def test_the_ready_bar_needs_every_pin_judged_resolved(ws, status):
    _pinned_round(ws)
    with pytest.raises(critic.BadVerdict):
        critic.record(ws, "d", verdict())  # the pin isn't judged

    critic.record(ws, "d", verdict(pins=[{"status": status, "evidence": "pin-1-now.png"}]))

    out = critic.status(ws, "d")
    assert out["ready"] is (status == "resolved")
    assert any("darker" in h for h in out["holds"]) is (status != "resolved")
    (again,) = review_items.design_items(ws, ["d"])
    assert again["owner_note"]["judged"]["pins"] == [{"status": status, "evidence": "pin-1-now.png"}]
