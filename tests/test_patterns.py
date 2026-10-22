"""The 10 m layer's patterns: what they draw on a synthetic kit, and that they pass the lint on the McLaren."""

import itertools
import shutil

import numpy as np
import pytest
import shapely
from shapely.geometry import LineString, Polygon

from livery import geom, patterns
from livery.config import REPO, Workspace
from livery.kit import load
from livery.lint.output import lint_output, opening_px
from livery.lint.run import thresholds
from livery.model import Livery
from tests.conftest import TWO, drawn, two_islands

KIT = "mclaren-720s-gt3-evo"
A, B = "#ff0000", "#0000ff"


@pytest.fixture
def kit(tmp_path):
    return load(two_islands(tmp_path), "two")


def box(u0, v0, u1, v1):
    return [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]


def pieces_across(g, y):
    """(start, length) of a texture shape's pieces on a horizontal line, left to right."""
    cut = g.intersection(LineString([(-1, y), (TWO + 1, y)]))
    parts = [p for p in getattr(cut, "geoms", [cut]) if not p.is_empty]
    return sorted((p.bounds[0], p.length) for p in parts)


def test_hazard_slashes_repeat_at_their_period_over_the_whole_region(kit):
    period = 3 * opening_px(thresholds())
    region = box(0.1, 0.3, 0.9, 0.7)
    el = patterns.hazard(kit, "tape", region, (A, B), period, lean=0.0, view="side_l")
    assert drawn(el).symmetric_difference(geom.footprint(kit, "side_l", region)).area < 1
    slashes = pieces_across(drawn(el, B), TWO / 2)
    starts = np.diff([s for s, _ in slashes[1:-1]])
    assert starts == pytest.approx(period, abs=1.0)
    assert [w for _, w in slashes[1:-1]] == pytest.approx([period / 2] * (len(slashes) - 2), abs=1.0)


def test_hazard_on_islands_fills_them_and_refuses_slashes_the_lint_would_eat(kit):
    el = patterns.hazard(kit, "lip", "i001", (A, B), 40, lean=30.0)
    assert drawn(el).area == pytest.approx(TWO * TWO / 2, rel=0.01)
    with pytest.raises(patterns.PatternError):
        patterns.hazard(kit, "thin", "i001", (A, B), opening_px(thresholds()), lean=30.0)


SPLIT = LineString([(0.3, -0.1), (0.3, 1.1)])
BEHIND = Polygon(box(0.3, -0.1, 1.1, 1.1))
TIERS = [patterns.Tier(24, 0.5, 0.1), patterns.Tier(12, 0.8, 0.2)]


def test_a_dissolve_is_densest_at_its_line_and_stays_in_its_field(kit):
    el = patterns.dissolve(
        kit, "embers", "side_l", SPLIT, TIERS, {A: 1}, np.random.default_rng(1), field=BEHIND
    )
    cells = drawn(el)
    x = 0.3 * TWO
    near = cells.intersection(shapely.box(x, 0, x + 0.05 * TWO, TWO)).area
    far = cells.intersection(shapely.box(x + 0.12 * TWO, 0, x + 0.17 * TWO, TWO)).area
    assert near > 2 * far > 0
    assert cells.bounds[0] >= x - 0.5  # nothing ahead of the line
    assert cells.bounds[2] <= x + 0.2 * TWO + 0.5  # nothing past the longest reach
    again = patterns.dissolve(
        kit, "embers", "side_l", SPLIT, TIERS, {A: 1}, np.random.default_rng(1), field=BEHIND
    )
    assert again.svg == el.svg


def test_a_dissolves_palette_goes_by_zone_and_can_leave_cells_out(kit):
    def zones(u: float, v: float, d: float) -> patterns.Weights:
        return {A: 1.0} if v > 0.5 else {B: 1.0, None: 1.0}

    el = patterns.dissolve(
        kit, "zones", "side_l", SPLIT, TIERS, zones, np.random.default_rng(2), field=BEHIND
    )
    upper, lower = drawn(el, A), drawn(el, B)
    assert upper.bounds[3] <= TWO / 2 + 0.5  # v > 0.5 is the texture's upper half
    assert lower.bounds[1] >= TWO / 2 - 0.5
    assert lower.area < upper.area  # half the lower cells were left out


def test_dissolve_cells_too_small_for_the_lint_are_refused(kit):
    small = patterns.Tier(opening_px(thresholds()) - 1, 0.5, 0.1)
    with pytest.raises(patterns.PatternError):
        patterns.dissolve(kit, "dust", "side_l", SPLIT, [small], {A: 1}, np.random.default_rng(3))
    hexes = patterns.Tier(opening_px(thresholds()) + 1, 0.5, 0.1)  # holds the square only as a square
    with pytest.raises(patterns.PatternError):
        patterns.dissolve(kit, "dust", "side_l", SPLIT, [hexes], {A: 1}, np.random.default_rng(3), cell="hex")


def test_a_dash_run_ends_a_band_in_dashes_that_get_shorter(kit):
    band = box(-0.2, 0.45, 1.2, 0.55)
    el = patterns.dash_run(kit, "pin", "side_l", band, 0.1, 0.3, A, length=0.15, n=3, rear=True, front=False)
    parts = pieces_across(drawn(el), TWO / 2)
    assert parts[0][0] == pytest.approx(0.1 * TWO, abs=1)
    assert parts[0][1] == pytest.approx(0.2 * TWO, abs=1)
    dashes = [w for _, w in parts[1:]]
    assert len(dashes) == 3
    assert dashes == sorted(dashes, reverse=True)
    with pytest.raises(patterns.PatternError):
        patterns.dash_run(kit, "pin", "side_l", band, 0.1, 0.3, A, length=0.04, n=4, shrink=0.5)


def test_a_stripe_bundle_stacks_its_stripes_with_graded_lengths(kit):
    els = patterns.stripe_bundle(
        kit,
        "bundle",
        0.3,
        [0.1, 0.08, 0.06],
        [A, B, A],
        gap=0.05,
        u0=0.05,
        u1=[0.45, 0.4, 0.35],
        views="side_l",
    )
    assert [e.id for e in els] == ["bundle.0.side_l", "bundle.1.side_l", "bundle.2.side_l"]
    shapes = [drawn(e) for e in els]
    rights = [s.bounds[2] for s in shapes]
    assert rights == sorted(rights, reverse=True)
    for lower, upper in itertools.pairwise(shapes):
        assert lower.bounds[1] > upper.bounds[3]  # texture y grows down the side: each one higher
    with pytest.raises(patterns.PatternError):
        patterns.stripe_bundle(kit, "thin", 0.3, [0.01, 0.1], [A, B], views="side_l")


def test_chevrons_point_where_asked_and_must_be_thick_enough(kit):
    el = patterns.chevrons(kit, "chev", "side_l", 0.1, 3, 0.12, 0.2, 0.07, A, lean=30.0, mid=0.5)
    first = drawn(el).intersection(shapely.box(0, 0, 0.17 * TWO, TWO))
    apex_x = first.intersection(LineString([(0, TWO / 2), (TWO, TWO / 2)])).bounds[0]
    assert apex_x == pytest.approx(0.1 * TWO, abs=1)  # the point leads, at u0 on the middle line
    assert first.bounds[0] == pytest.approx(apex_x, abs=1)
    back = patterns.chevrons(
        kit, "chev", "side_l", 0.3, 1, 0.08, 0.2, 0.07, A, lean=30.0, mid=0.5, point="rear"
    )
    tip = drawn(back).intersection(LineString([(0, TWO / 2), (TWO, TWO / 2)])).bounds[2]
    assert tip == pytest.approx(0.3 * TWO, abs=1)
    assert drawn(back).bounds[2] == pytest.approx(tip, abs=1)
    with pytest.raises(patterns.PatternError):
        patterns.chevrons(kit, "chev", "side_l", 0.1, 3, 0.08, 0.2, 0.02, A, lean=45.0, mid=0.5)


# --- the real kit --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mclaren(tmp_path_factory):
    root = tmp_path_factory.mktemp("ws")
    shutil.copytree(REPO / "kits" / KIT, root / "kits" / KIT)
    ws = Workspace(root=root, assets=REPO / "assets")
    return ws, load(ws, KIT)


def test_pieces_pass_the_lint_on_the_real_kit(mclaren):
    ws, kit = mclaren
    els = []
    for view in ("side_l", "side_r"):
        k = geom.rake(kit, 58.0, view)
        line = LineString([(0.52 + k * a, a) for a in (0.1, 1.1)])
        behind = Polygon([(0.52 - 0.2 * k, -0.2), (1.3, -0.2), (1.3, 1.2), (0.52 + 1.2 * k, 1.2)])
        tiers = [patterns.Tier(40, 0.5, 0.1), patterns.Tier(20, 0.7, 0.25), patterns.Tier(12, 0.6, 0.35)]
        els.append(
            patterns.dissolve(
                kit,
                f"embers.{view}",
                view,
                line,
                tiers,
                {A: 0.5, B: 0.5},
                np.random.default_rng(7),
                field=behind,
            )
        )
    found = lint_output(
        Livery("#808080").add(*els), kit, ws, np.zeros((kit.canvas, kit.canvas, 3), np.uint8), thresholds()
    )
    mine = [f for f in found if f.check in ("near_tangent", "min_size") and "embers" in f.id]
    assert mine == []
    assert not [f for f in found if f.level == "error" and f.id.startswith("embers")]


def test_the_patterns_edges_include_the_shut_lines_the_lint_judges(kit):
    from PIL import Image

    beside = shapely.box(66, 40, 90, 200)
    assert not geom.edges(kit).touches(beside)
    shut = np.zeros((TWO, TWO), np.uint8)
    shut[20:-20, 60:63] = 255
    Image.fromarray(shut).save(kit.dir / "overlays" / "shutlines.png")
    assert geom.edges(kit).touches(beside)


def test_a_dash_run_can_taper_in_height_instead(kit):
    band = box(-0.2, 0.3, 1.2, 0.7)
    el = patterns.dash_run(
        kit, "slash", "side_l", band, 0.05, 0.1, A, length=0.06, n=3, taper="height", front=False
    )
    dashes = [g for g in geom.polygons(drawn(el)) if g.bounds[0] > 0.11 * TWO]
    heights = [g.bounds[3] - g.bounds[1] for g in sorted(dashes, key=lambda g: g.bounds[0])]
    assert len(heights) == 3
    assert heights == sorted(heights, reverse=True)
    mids = [(g.bounds[1] + g.bounds[3]) / 2 for g in dashes]
    assert mids == pytest.approx([TWO / 2] * 3, abs=1)  # on the band's middle


def test_scatter_keeps_its_squares_inside_an_island_and_clear_of_its_edge(kit):
    tiers = [(24, 0.3), (12, 0.4)]
    el = patterns.scatter(kit, "embers", "i001", tiers, {A: 1, B: 1}, np.random.default_rng(4), inset_px=8)
    squares = drawn(el)
    assert squares.area > 0
    assert squares.within(shapely.box(8 - 0.5, 8 - 0.5, TWO / 2 - 8 + 0.5, TWO - 8 + 0.5))
    again = patterns.scatter(kit, "embers", "i001", tiers, {A: 1, B: 1}, np.random.default_rng(4), inset_px=8)
    assert again.svg == el.svg
