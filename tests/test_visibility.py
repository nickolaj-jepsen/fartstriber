"""What the preview cameras see of each texel, from synthetic calibration captures of the two-island kit."""

import numpy as np
import pytest

from livery import calibrate, state, visibility
from livery.kit import load
from tests.conftest import FOLD, SQUEEZE, TWO, capture, two_islands, two_views

SCALE = 2
CELLS = TWO // SCALE


@pytest.fixture
def ws(tmp_path):
    return two_islands(tmp_path)


@pytest.fixture
def vis(ws) -> visibility.Visibility:
    capture("two", two_views())
    table = visibility.table(load(ws, "two"), SCALE)
    assert table is not None
    return table


def test_each_cell_is_seen_at_the_footprint_of_the_pixels_that_sample_it(vis):
    head, edge = vis.views.index("head-on"), vis.views.index("edge-on")
    left, beyond = np.s_[: FOLD // SCALE, : CELLS // 2], np.s_[FOLD // SCALE :, : CELLS // 2]
    squeezed, hidden = np.s_[:, CELLS // 2 : 3 * CELLS // 4], np.s_[:, 3 * CELLS // 4 :]
    assert vis.seen()[head][left].all()
    assert np.allclose(vis.major[head][left], 1)
    assert np.allclose(vis.minor[head][left], 1)
    assert np.allclose(vis.major[head][beyond], SQUEEZE)
    # A pixel four texels long covers both cells under it, so the squeezed band has no gaps.
    assert vis.seen()[edge][squeezed].all()
    assert np.allclose(vis.major[edge][squeezed], 4)
    assert np.allclose(vis.minor[edge][squeezed], 1)
    assert not vis.seen()[:, *hidden].any()
    assert not vis.well_seen(3.0).any(axis=0)[squeezed].any()
    assert vis.well_seen(3.0)[head][beyond].all()


def test_a_fold_is_a_crease_and_flat_paint_is_not(vis):
    crease = np.nan_to_num(vis.crease[vis.views.index("head-on")], nan=0.0)
    at_fold = crease[FOLD // SCALE - 1 : FOLD // SCALE + 1, 8 : CELLS // 2 - 8]
    flat = crease[8 : FOLD // SCALE - 8, 8 : CELLS // 2 - 8]
    assert (at_fold > 0.4).all()
    assert (flat < 0.05).all()


def test_a_mask_is_drawn_as_the_view_shows_it(vis):
    stripe = np.zeros((CELLS, CELLS), bool)
    stripe[10:50, 4:6] = True  # 4 texels wide, seen at one texel per pixel
    shown = vis.on_screen(vis.views.index("head-on"), stripe)
    assert shown is not None
    assert shown[0].shape == (80, 4)
    assert shown[1] == (20, 8)
    assert vis.widths(stripe, 8) == {"head-on": pytest.approx(4, abs=0.5)}
    band = np.zeros((CELLS, CELLS), bool)
    band[10:50, 70:78] = True  # 16 texels wide, four to a pixel
    assert vis.widths(band, 8) == {"edge-on": pytest.approx(4, abs=0.5)}
    hidden = np.zeros((CELLS, CELLS), bool)
    hidden[10:50, 110:120] = True
    assert vis.on_screen(0, hidden) is None
    assert vis.widths(hidden, 8) == {}


def cached(ws) -> list[str]:
    """The captures of the tables kept in the state dir."""
    files = [p for p in state.root().rglob("*.npz") if calibrate.root(ws, "two") not in p.parents]
    return [visibility.Visibility.load(p).capture for p in files]


def test_tables_are_cached_per_capture_and_built_again_for_a_new_one(ws, vis):
    kit = load(ws, "two")
    assert visibility.capture_dir("two") == calibrate.current(ws, "two")
    assert cached(ws) == [vis.capture]
    again = visibility.table(kit, SCALE)
    assert again is not None
    assert np.array_equal(again.major, vis.major)
    capture("two", {"head-on": two_views()["head-on"]}, run="run-2")
    fresh = visibility.table(kit, SCALE)
    assert fresh is not None
    assert (fresh.capture, fresh.views) == ("run-2", ("head-on",))
    assert cached(ws) == ["run-2"]


def test_a_kit_without_a_capture_has_no_tables(ws):
    assert visibility.table(load(ws, "two"), SCALE) is None
