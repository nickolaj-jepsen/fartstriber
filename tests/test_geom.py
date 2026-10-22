"""The design geometry layer (livery.geom, and carspace's scale helpers), on a small synthetic kit."""

import numpy as np
import pytest
import shapely
from shapely.geometry import GeometryCollection, LineString, Point, Polygon

from livery import carspace, geom
from livery.compile import compile_svg
from livery.config import Workspace
from livery.kit import load
from livery.lint.output import MEET_PX, lint_output
from livery.lint.run import thresholds
from livery.model import Livery
from livery.primitives import clip_out, clip_to, svg
from livery.raster import render
from tests.conftest import TWO, read_kit, two_islands, write_kit

C = TWO  # canvas px


@pytest.fixture
def ws(tmp_path) -> Workspace:
    """The two-island kit (`conftest.two_islands`), i001 left of x = C/2 and i002 right of it, both mapped
    on side_l by x = C u, y = C (1 - v), and a top view laid out at a quarter of that across the car."""
    ws = two_islands(tmp_path)
    d = ws.kits / "two"
    raw = read_kit(d)
    raw["top_transform"] = {"x0": 0, "x_per_u": C, "axis_y": C // 2, "y_per_w": -C / 4, "islands": {}}
    write_kit(d, raw)
    return ws


@pytest.fixture
def kit(ws):
    return load(ws, "two")


def box(u0, v0, u1, v1):
    return [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]


def pixels(ws, kit, *elements):
    return render(compile_svg(Livery("#ffffff").add(*elements), kit), ws)


# --- scale and rake ------------------------------------------------------------------------------------


def test_scale_is_the_local_map_on_a_side_and_the_linear_map_on_top(kit):
    assert carspace.px_per_unit(kit, "side_l", (0.3, 0.5)) == pytest.approx((C, C))
    assert carspace.px_per_unit(kit, "top") == pytest.approx((C, C / 4))
    with pytest.raises(ValueError, match="point"):
        carspace.px_per_unit(kit, "side_l")


def test_aspect_and_rake_make_a_45_degree_lean_look_45_degrees(kit):
    for view in ("side_l", "top"):
        k = carspace.aspect(kit, view)
        seen = geom.as_seen(Polygon(geom.slant(0.2, 0.3, 0.0, 1.0, geom.rake(kit, 45.0, view))), k)
        assert isinstance(seen, Polygon)
        (u0, y0), (u1, y1) = seen.exterior.coords[0], seen.exterior.coords[3]
        assert u1 - u0 == pytest.approx(y1 - y0)
    assert geom.rake(kit, 0.0) == 0.0


def test_seen_and_car_space_round_trip():
    shape = Polygon(box(0.1, 0.2, 0.4, 0.9))
    back = geom.as_car(geom.as_seen(shape, 0.37), 0.37)
    assert back.symmetric_difference(shape).area < 1e-12


def test_grow_and_segment_measure_in_texture_px():
    scale = (200.0, 50.0)
    grown = Polygon(geom.grow(box(0.4, 0.4, 0.6, 0.6), 10, scale))
    u0, v0, u1, v1 = grown.bounds
    assert (u1 - u0) * scale[0] == pytest.approx(0.2 * scale[0] + 20)
    assert (v1 - v0) * scale[1] == pytest.approx(0.2 * scale[1] + 20)
    line = Polygon(geom.segment((0.2, 0.5), (0.8, 0.9), 8, scale))
    px = shapely.affinity.scale(line, scale[0], scale[1], origin=(0, 0))
    assert px.area == pytest.approx(np.hypot(0.6 * scale[0], 0.4 * scale[1]) * 8)


# --- projection ----------------------------------------------------------------------------------------


def test_a_footprint_crosses_islands_and_keeps_holes(kit):
    whole = geom.footprint(kit, "side_l", box(0.25, 0.25, 0.75, 0.75))
    assert whole.area == pytest.approx((C / 2) ** 2, rel=1e-3)
    ring = Polygon(box(0.25, 0.25, 0.75, 0.75), [box(0.4, 0.4, 0.6, 0.6)])
    assert geom.footprint(kit, "side_l", ring).area == pytest.approx(whole.area * (1 - 0.04 / 0.25), rel=1e-3)


def test_a_footprint_through_other_pieces_skips_what_they_leave_out(kit):
    left = [pc for pc in carspace.pieces(kit, "side_l") if pc.island == "i001"]
    foot = geom.footprint(kit, "side_l", box(0.25, 0.25, 0.75, 0.75), left)
    assert foot.area == pytest.approx((C / 2) ** 2 / 2, rel=1e-3)
    assert foot.bounds[2] <= C / 2


def test_fill_is_one_element_with_a_path_per_colour_and_drops_what_it_must_avoid(ws, kit):
    avoid = shapely.box(0, 0, 40, 40)  # texture px, under the first black piece
    el = geom.fill(
        kit,
        "dots",
        "side_l",
        {
            "#000000": [box(0.05, 0.85, 0.1, 0.9), box(0.3, 0.3, 0.7, 0.4)],
            "#ff0000": [box(0.3, 0.6, 0.7, 0.7)],
        },
        avoid=avoid,
    )
    assert el.svg.count("<path") == 2
    img = pixels(ws, kit, el)
    assert tuple(img[int(C * 0.65), C // 2]) == (0, 0, 0)  # across both islands
    assert tuple(img[int(C * 0.35), C // 2]) == (255, 0, 0)
    assert tuple(img[int(C * 0.12), int(C * 0.07)]) == (255, 255, 255)  # the avoided piece


def test_shows_tells_a_shape_on_the_paint_from_one_cut_by_its_edge(kit):
    inside = Polygon(box(0.3, 0.3, 0.5, 0.5))
    half_off = Polygon(box(0.85, 0.3, 1.05, 0.5))
    assert geom.shows(kit, "side_l", inside, 0.99)
    assert not geom.shows(kit, "side_l", half_off, 0.9)
    assert geom.shows(kit, "side_l", half_off, 0.3)
    assert not geom.shows(kit, "side_l", Polygon(box(1.5, 0.3, 1.7, 0.5)), 0.1)


def test_clip_to_several_islands_and_clip_out(ws, kit):
    cover = svg(f'<rect width="{C}" height="{C}" fill="#000000"/>', "cover")
    on = pixels(ws, kit, clip_to(kit, ("i001", "i002"), cover))
    assert (on[8:-8, 8:-8] == 0).all()
    out = pixels(ws, kit, clip_out(kit, "i001", cover, margin=4))
    assert (out[:, : C // 2] == 255).all()
    assert (out[:, C // 2 + 8 :] == 0).all()


# --- clean-up ------------------------------------------------------------------------------------------


def test_polygons_and_crumbs():
    mixed = GeometryCollection(
        [Point(0, 0), LineString([(0, 0), (1, 1)]), shapely.box(0, 0, 9, 9), Polygon()]
    )
    assert [p.area for p in geom.polygons(mixed)] == [81]
    two = shapely.box(0, 0, 50, 50).union(shapely.box(100, 100, 102, 102))
    assert geom.drop_crumbs(two, 10).area == 2500


def test_clean_opens_away_features_thinner_than_the_lint_allows():
    thin = thresholds().min_feature_px - 2
    plate = shapely.box(0, 0, 100, 100).union(shapely.box(100, 40, 200, 40 + thin))
    speck = shapely.box(300, 300, 312, 312)
    cleaned = geom.clean(plate.union(speck), min_area=12 * 12 + 1)
    assert cleaned.area == pytest.approx(100 * 100)
    assert cleaned.bounds[2] == pytest.approx(100)


# --- clearance -----------------------------------------------------------------------------------------


def _lint_near_tangent(ws, kit, piece) -> bool:
    livery = Livery("#ffffff").add(geom.paint("piece", piece, "#000000"))
    found = lint_output(livery, kit, ws, np.zeros((C, C, 3), np.uint8), thresholds())
    return any(f.check == "near_tangent" for f in found)


def test_edges_agree_with_the_near_tangent_lint(ws, kit):
    t, lines = thresholds(), geom.edges(kit)
    x = C / 2 + (MEET_PX + t.near_tangent_px) / 2  # beside the seam at x = C/2, inside its reach
    run = 3 * t.near_tangent_run_px
    beside = shapely.box(x, 40, x + 60, 40 + run)
    across = shapely.box(C / 2 - 30, 40, C / 2 + 60, 40 + run)
    far = shapely.box(C / 2 + 2 * t.near_tangent_px, 40, C / 2 + 80, 40 + run)

    assert not lines.clear(beside)
    assert lines.longest(beside) >= run - 1
    assert _lint_near_tangent(ws, kit, beside)
    assert lines.clear(across)
    assert not _lint_near_tangent(ws, kit, across)
    assert not lines.touches(far)
    assert lines.alongside(far).is_empty


def test_a_piece_settled_with_a_margin_passes_the_lint(ws, kit):
    t = thresholds()
    x = C / 2 + (MEET_PX + t.near_tangent_px) / 2
    beside = shapely.box(x, 40, x + 60, 40 + 3 * t.near_tangent_run_px)
    wide = geom.edges(kit, margin=4)
    settled = wide.settle(beside)
    assert settled is not None
    assert 0 < settled.area < beside.area
    assert not geom.edges(kit).touches(settled)
    assert not _lint_near_tangent(ws, kit, settled)
    # A piece wholly inside the reach has nothing left once pulled back; it may grow onto the line instead.
    sliver = shapely.box(C / 2 + MEET_PX, 20, C / 2 + wide.reach - 1, C - 20)
    assert wide.settle(sliver) is None
    grown = wide.settle(sliver, grow=True)
    assert grown is not None
    assert grown.intersects(LineString([(C / 2, 0), (C / 2, C)]))
    assert not _lint_near_tangent(ws, kit, grown)


def test_edges_include_the_strict_keep_clear_zones(ws, kit):
    raw = read_kit(kit.dir)
    raw["keep_clear"] = [{"kind": k, "box": [40, 40, 90, 90]} for k in thresholds().keep_clear_strict]
    raw["keep_clear"].append({"kind": "not-strict", "box": [150, 150, 200, 200]})
    write_kit(kit.dir, raw)
    lines = geom.edges(load(ws, "two"))
    assert len(lines.zones) == len(thresholds().keep_clear_strict)
    assert lines.touches(shapely.box(45, 45, 85, 85))
    assert not lines.touches(shapely.box(155, 155, 195, 195))
