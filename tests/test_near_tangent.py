"""The near-tangent lint's lines and runs, and the library's snapping, on synthetic two-island kits.

Kit "two" (`conftest.two_islands`): i001 left of x = 128, i002 right of it, both mapped on side_l by
x = 256 u, y = 256 (1 - v), so they meet at x = 128 as one surface.
"""

import json
import textwrap

import numpy as np
import pytest
import shapely
import yaml
from PIL import Image, ImageDraw
from shapely.geometry import Polygon

from livery import geom
from livery.check import run_check
from livery.kit import load
from livery.lint.output import MEET_PX, lint_output
from livery.lint.run import lint_design, thresholds
from livery.model import Livery
from livery.primitives import shape, stripe
from tests.conftest import TWO, drawn, two_islands

H = TWO // 2
INK = "#000000"


@pytest.fixture
def ws(tmp_path):
    return two_islands(tmp_path)


def _kit(ws):
    return load(ws, "two")


def near_tangent(ws, *pieces) -> list[str]:
    """The ids of the pieces (texture shapes) the near-tangent lint fails."""
    livery = Livery("#ffffff").add(*(geom.paint(f"p{i}", p, INK) for i, p in enumerate(pieces)))
    found = lint_output(livery, _kit(ws), ws, np.zeros((TWO, TWO, 3), np.uint8), thresholds())
    return [f.id for f in found if f.check == "near_tangent"]


def pair_seam(ws, ys, left: float = H - 2, right: float = H + 1) -> None:
    """seams.json pairs across the islands' shared edge at x = 128, at the given heights."""
    (ws.kits / "two" / "seams.json").write_text(json.dumps([[left, y, right, y] for y in ys]))


def shut_line(ws, draw) -> None:
    """A shutlines.png drawn by `draw(ImageDraw)` in white on black."""
    img = Image.new("L", (TWO, TWO), 0)
    draw(ImageDraw.Draw(img))
    img.save(ws.kits / "two" / "overlays" / "shutlines.png")


def beside_seam(y0: float, y1: float) -> Polygon:
    """A piece on i002 whose left edge runs inside the reach of x = 128 without meeting it."""
    x = H + (MEET_PX + thresholds().near_tangent_px) / 2
    return shapely.box(x, y0, x + 60, y1)


# --- which lines ---------------------------------------------------------------------------------------


def test_an_outline_seams_json_pairs_is_no_line_to_keep_clear_of(ws):
    run = thresholds().near_tangent_run_px
    piece = beside_seam(30, 30 + 3 * run)
    assert near_tangent(ws, piece) == ["p0"]  # unpaired, the shared edge is a paint edge
    pair_seam(ws, range(4, TWO, 8))
    assert near_tangent(ws, piece) == []


def test_the_unpaired_stretch_of_an_outline_stays_a_line(ws):
    run = thresholds().near_tangent_run_px
    pair_seam(ws, range(4, H - 8, 8))  # the upper half only
    assert near_tangent(ws, beside_seam(30, 30 + 2 * run)) == []
    assert near_tangent(ws, beside_seam(H + 10, H + 10 + 2 * run)) == ["p0"]


def test_shut_lines_and_the_number_zone_stay_lines_where_the_outlines_are_paired(ws):
    pair_seam(ws, range(4, TWO, 8))
    shut_line(ws, lambda d: d.rectangle([180, 20, 182, TWO - 20], fill=255))
    assert near_tangent(ws, shapely.box(189, 40, 230, 200)) == ["p0"]
    raw = yaml.safe_load((ws.kits / "two" / "kit.yaml").read_text())
    raw["keep_clear"] = [{"kind": k, "box": [150, 60, 170, 200]} for k in thresholds().keep_clear_strict]
    (ws.kits / "two" / "kit.yaml").write_text(yaml.safe_dump(raw))
    (ws.kits / "two" / "overlays" / "shutlines.png").unlink()
    assert near_tangent(ws, shapely.box(H + 2, 50, 143, 210)) == ["p0"]


def test_an_edge_is_judged_only_against_its_own_islands_lines(ws):
    """i001 stops 6 px short of i002 in the texture; i001's edge there is paired, i002's is not."""
    d = ws.kits / "two"
    (d / "regions.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg">'
        f'<path data-id="i001" d="M0,0 L120,0 L120,{TWO} L0,{TWO} Z"/>'
        f'<path data-id="i002" d="M126,0 L{TWO},0 L{TWO},{TWO} L126,{TWO} Z"/></svg>'
    )
    piece = shapely.box(60, 40, 116, 200)  # 4 px inside i001, 10 px from i002's outline
    assert near_tangent(ws, piece) == ["p0"]  # i001's own outline, unpaired
    (d / "seams.json").write_text(json.dumps([[118, y, 300, y] for y in range(4, TWO, 8)]))
    assert near_tangent(ws, piece) == []


# --- how long a run ------------------------------------------------------------------------------------


def _along_diagonal(length: float, offset: float) -> Polygon:
    """A piece `length` px along the diagonal from (40, 40), its near edge `offset` px off it, 20 px wide."""
    t = np.array([1.0, 1.0]) / np.sqrt(2)
    n = np.array([1.0, -1.0]) / np.sqrt(2)
    a = np.array([60.0, 60.0])
    return Polygon(
        [
            a + n * offset,
            a + t * length + n * offset,
            a + t * length + n * (offset + 20),
            a + n * (offset + 20),
        ]
    )


def test_a_run_is_measured_along_the_line_not_by_its_bounding_box(ws):
    run = thresholds().near_tangent_run_px
    shut_line(ws, lambda d: d.line([(40, 40), (115, 115)], fill=255, width=3))
    reach = (MEET_PX + thresholds().near_tangent_px) / 2 + 1.5
    # Its bounding box is under the run across, its stretch along the line over it.
    long = _along_diagonal(1.15 * run, reach)
    assert max(np.ptp(np.asarray(long.exterior.coords)[:2], axis=0)) < run
    assert near_tangent(ws, long) == ["p0"]
    assert near_tangent(ws, _along_diagonal(0.7 * run, reach)) == []


# --- snapping ------------------------------------------------------------------------------------------


def _shut_across(ws) -> None:
    """A shut line across both islands at y = 127..129 (v = 0.5)."""
    shut_line(ws, lambda d: d.rectangle([20, H - 1, TWO - 20, H + 1], fill=255))


def _stripe_below(kit, gap: float, id: str = "pin"):
    """A side stripe whose top edge runs `gap` px below the shut line, 50 px tall."""
    top = H + 1 + gap
    return stripe(kit, id, 1 - (top + 50) / TWO, 1 - top / TWO, INK, views="side_l", u0=0.1, u1=0.9)[0]


def _lint(ws, *elements):
    found = lint_output(
        Livery("#ffffff").add(*elements), _kit(ws), ws, np.zeros((TWO, TWO, 3), np.uint8), thresholds()
    )
    return [f.id for f in found if f.check == "near_tangent"]


@pytest.mark.parametrize(("gap", "to"), [(6.0, "onto"), (10.0, "clear of")])
def test_an_edge_alongside_a_line_snaps_whichever_way_moves_it_less(ws, gap, to):
    _shut_across(ws)
    kit = _kit(ws)
    el = _stripe_below(kit, gap)
    assert _lint(ws, el) == []
    assert el.snaps  # one per island: the stripe's edge meets their shared edge between them
    for snap in el.snaps:
        assert snap["to"] == to
        assert snap["line"] == "shut line"
        assert 0 < snap["px"] <= thresholds().near_tangent_px + geom.CLEAR_PX
        assert snap["view"] == "side_l"
        assert abs(snap["at"][1] - H) < thresholds().near_tangent_px + geom.CLEAR_PX
    shut = shapely.box(20, H - 1, TWO - 20, H + 1)
    edge = drawn(el).boundary.intersection(shapely.box(60, 0, 200, TWO))  # away from the stripe's ends
    if to == "onto":
        assert edge.distance(shut) <= MEET_PX
    else:
        assert edge.distance(shut) > thresholds().near_tangent_px


def test_an_edge_far_from_every_line_is_left_as_it_is(ws):
    _shut_across(ws)
    el = _stripe_below(_kit(ws), 30.0)
    assert el.snaps == ()
    assert drawn(el).symmetric_difference(shapely.box(25.6, H + 31, 230.4, H + 81)).area < 1


def test_a_stretch_no_move_within_the_limit_fixes_is_left_for_the_lint(ws):
    _shut_across(ws)
    kit = _kit(ws)
    piece = shapely.box(25.6, H + 7, 230.4, H + 57)
    e = geom.edges(kit, geom.SNAP_MARGIN)
    out, moves, left = e.snap(piece, 1.0, lambda g: geom.failing(kit, g))
    assert moves == []
    assert left
    assert out.equals(piece)


def test_an_outline_follows_its_shape_where_the_shape_snapped(ws):
    _shut_across(ws)
    kit = _kit(ws)
    top = H + 11
    poly = [
        (0.1, 1 - (top + 50) / TWO),
        (0.9, 1 - (top + 50) / TWO),
        (0.9, 1 - top / TWO),
        (0.1, 1 - top / TWO),
    ]
    w = thresholds().min_outline_px
    (stroke,) = geom.outline(kit, "plate", "side_l", poly, [(w, "#ff0000")])
    plate = shape(kit, "plate", "side_l", poly, INK)
    assert plate.snaps
    body, ring = drawn(plate), drawn(stroke)
    gap = body.buffer(w - 2, join_style="mitre").difference(body).difference(ring)
    assert gap.area < 2 * w  # no base colour shows between the plate and its stroke


def test_every_snap_is_listed_in_the_lint_report_and_the_check(ws):
    _shut_across(ws)
    d = ws.design_dir("snapper")
    d.mkdir(parents=True)
    top = H + 7
    (d / "design.py").write_text(
        textwrap.dedent(
            f"""\
            from livery.primitives import base, stripe

            KIT = "two"


            def build(kit, p):
                return base("#ffffff").add(
                    stripe(kit, "pin", {1 - (top + 50) / TWO}, {1 - top / TWO}, "#000000", views="side_l", u0=0.1, u1=0.9)
                )
            """
        ),
        encoding="utf-8",
    )
    report = lint_design(ws, "snapper")
    assert not [e for e in report["errors"] if e["check"] == "near_tangent"]
    assert report["snaps"]
    for snap in report["snaps"]:
        assert snap["id"] == "pin.side_l"
        assert {"to", "line", "px", "at"} <= snap.keys()
    result, _ = run_check(ws, "snapper")
    assert result["passed"], result["errors"]
    assert result["snaps"] == report["snaps"]
