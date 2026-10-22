import numpy as np
import pytest

from livery import carspace
from livery import kit as kits
from livery.kit import load
from livery.kit_extract import islands
from tests.conftest import read_kit, write_kit


def test_islands_are_split_by_outlines_and_leaks_are_recovered():
    paintable = np.ones((120, 120), bool)
    outlines = np.zeros_like(paintable)
    # A closed outline around one square, and an open one (a dashed gap) around another.
    outlines[10, 10:40] = outlines[40, 10:40] = outlines[10:41, 10] = outlines[10:41, 40] = True
    outlines[70, 70:110] = outlines[110, 70:110] = outlines[70:111, 70] = True
    outlines[70:111:4, 110] = True
    # The gap network: a thin grid across the canvas.
    outlines[::6, :] |= np.arange(120)[None, :] % 1 == 0
    outlines[:, ::6] |= True
    outlines[12:39, 12:39] = outlines[72:109, 72:109] = False

    isl = islands(paintable, outlines)

    closed, leaky = isl[25, 25], isl[90, 90]
    assert closed
    assert leaky
    assert closed != leaky
    assert isl[3, 3] == 0  # the thin network itself is not an island


def test_side_shapes_stay_inside_where_the_island_was_measured(ws):
    kit_dir = ws.kits / "tiny"
    (kit_dir / "regions.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><path data-id="i001" d="M0,0 L32,0 L32,64 L0,64 Z"/></svg>'
    )
    raw = read_kit(kit_dir)
    # u, v in 0..1 -> x = 64u, y = 64(1 - v); measured only on the left half of the island.
    raw["side_transforms"] = {
        "i001": {
            "view": "side_l",
            "coef": [[0, 64, 0, 0, 0, 0], [64, 0, -64, 0, 0, 0]],
            "domain": [[0, 0], [16, 0], [16, 64], [0, 64]],
            "car_domain": [[-1, -1], [2, -1], [2, 2], [-1, 2]],
        }
    }
    write_kit(kit_dir, raw)
    kit = load(ws, "tiny")

    inside = carspace.polygon(kit, "side_l", [(0.05, 0.2), (0.15, 0.2), (0.15, 0.8), (0.05, 0.8)], "#f00")
    beyond = carspace.polygon(kit, "side_l", [(0.3, 0.2), (0.45, 0.2), (0.45, 0.8), (0.3, 0.8)], "#f00")

    assert 'fill="#f00"' in inside
    assert beyond == ""


def test_a_side_island_turning_over_the_top_has_one_view_per_pixel(ws):
    kit_dir = ws.kits / "tiny"
    (kit_dir / "regions.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><path data-id="i001" d="M0,0 L32,0 L32,64 L0,64 Z"/></svg>'
    )
    raw = read_kit(kit_dir)
    raw["regions"] = [{"id": "i001", "view": "lower", "area": 2048, "bbox": [0, 0, 32, 64]}]
    everywhere = [[-1, -2], [2, -2], [2, 2], [-1, 2]]
    # The side map was measured on the island's left half; the top map saw all of it from above.
    raw["side_transforms"] = {
        "i001": {
            "view": "side_l",
            "coef": [[0, 64, 0, 0, 0, 0], [64, 0, -64, 0, 0, 0]],
            "domain": [[0, 0], [16, 0], [16, 64], [0, 64]],
            "car_domain": everywhere,
        }
    }
    top_map = {
        "coef": [[0, 64, 0, 0, 0, 0], [32, 0, 32, 0, 0, 0]],
        "domain": [[0, 0], [32, 0], [32, 64], [0, 64]],
    }
    raw["top_transform"] = {"x0": 0, "x_per_u": 64, "axis_y": 32, "y_per_w": -32, "islands": {}}
    raw["top_transform"]["sides"] = {"i001": {**top_map, "view": "top", "car_domain": everywhere}}
    write_kit(kit_dir, raw)
    kit = load(ws, "tiny")

    side = carspace.project(kit, "side_l", [(-0.5, -1), (1.5, -1), (1.5, 2), (-0.5, 2)])
    top = carspace.project(kit, "top", [(-0.5, -1.5), (1.5, -1.5), (1.5, 1.5), (-0.5, 1.5)])

    assert side.area > 0
    assert top.area > 0
    assert side.intersection(top).area < 1  # a top-view shape never paints what the side view owns
    assert side.union(top).area > 32 * 64 - 8  # and together they cover the island


def test_residual_grid_takes_out_a_bend_the_quadratic_misses():
    from livery.carspace import grid_offset
    from livery.kit_fit import _design, _fit_map, _residual_grid

    rng = np.random.default_rng(0)
    u, v = rng.uniform(0.3, 0.95, 20000), rng.uniform(0, 1, 20000)
    bend = 6 * np.sin(3 * np.pi * u)  # a door-to-haunch body curve, in texture pixels
    target = np.stack([600 + 1500 * u, 1900 - 400 * v + bend], 1)
    coef = _fit_map(u, v, target)[0]
    resid = target - _design(u, v) @ coef

    grid = _residual_grid(u, v, resid)
    left = resid - grid_offset(grid, np.stack([u, v], 1))

    assert np.abs(resid[:, 1]).max() > 2
    assert np.abs(left).max() < np.abs(resid).max() / 2
    assert np.percentile(np.linalg.norm(left, axis=1), 95) < 0.5


def test_cells_two_shots_both_saw_pair_their_values():
    from livery.kit_fit import _common_cells

    a_keys, a_vals = np.array([5, 5, 7, 9]), np.array([[1.0], [3.0], [10.0], [4.0]])
    b_keys, b_vals = np.array([9, 5, 11]), np.array([[40.0], [20.0], [0.0]])

    a, b = _common_cells(a_keys, a_vals, b_keys, b_vals)

    assert a[:, 0].tolist() == [2.0, 4.0]  # cell 5 averaged over its two samples, then cell 9
    assert b[:, 0].tolist() == [20.0, 40.0]


def test_a_kit_made_from_another_build_of_the_car_is_refused(ws, tmp_path):
    dat = kits.model_path(tmp_path / "cars", "tiny")
    dat.parent.mkdir(parents=True)
    dat.write_bytes(b"the car's model")
    d = ws.kits / "tiny"
    kits.write(d, meta=kits.read_meta(d) | {"fingerprint": {"model": kits.fingerprint(dat)}})
    kit = load(ws, "tiny")
    kits.check_model(kit, tmp_path / "cars")

    dat.write_bytes(b"the car's model, updated")
    with pytest.raises(kits.KitError):
        kits.check_model(kit, tmp_path / "cars")


def test_a_kit_reads_back_what_the_kit_commands_wrote(ws):
    d = ws.kits / "tiny"
    maps: dict = {"regions": [{"id": "i001", "view": "top", "bbox": [0, 0, 32, 64], "area": 2048}]}
    maps["top_transform"] = {"x0": 0.1, "x_per_u": 1 / 3, "axis_y": 32, "y_per_w": -32, "islands": {}}
    kits.write(d, maps=maps)

    assert kits.read_maps(d) == maps
    assert carspace.kit_data(load(ws, "tiny"))["top_transform"]["x_per_u"] == 1 / 3
