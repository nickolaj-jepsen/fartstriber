"""Outline strokes, on a synthetic kit whose second island is mapped at twice the scale along the car."""

import pytest
import shapely
from shapely.geometry import LineString, Polygon

from livery import geom
from livery.compile import compile_svg
from livery.config import Workspace
from livery.errors import LiveryError
from livery.kit import load
from livery.lint.run import thresholds
from livery.model import Livery
from livery.raster import render
from tests.conftest import TWO, drawn, two_islands

C = TWO
H = C // 2


@pytest.fixture
def ws(tmp_path) -> Workspace:
    return two_islands(tmp_path, stretch=2.0)


@pytest.fixture
def kit(ws):
    return load(ws, "two")


def box(u0, v0, u1, v1):
    return [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]


PLATE = box(0.3, 0.3, 0.7, 0.7)  # across the seam: x 77..128 on i001, 128..230 on i002


def across(g, y=H) -> list[float]:
    """Lengths of the pieces of a texture shape on a horizontal line."""
    cut = g.intersection(LineString([(-1, y), (C + 1, y)]))
    return sorted(p.length for p in getattr(cut, "geoms", [cut]) if not p.is_empty)


def test_a_stroke_is_as_wide_on_a_stretched_island_and_skips_the_seam(kit):
    w = thresholds().min_outline_px + 2
    (stroke,) = geom.outline(kit, "plate", "side_l", PLATE, [(w, "#ff0000")])
    seen = drawn(stroke).difference(geom.footprint(kit, "side_l", PLATE))
    left, right = across(seen)
    assert left == pytest.approx(w, abs=0.5)
    assert right == pytest.approx(w, abs=0.5)
    assert drawn(stroke).intersection(shapely.box(H - 20, H - 20, H + 20, H + 20)).area == 0


def test_stacked_strokes_meet_each_other_and_the_shape(ws, kit):
    w = thresholds().min_outline_px
    strokes = geom.outline(kit, "plate", "side_l", PLATE, [(w, "#000000"), (w + 4, "#ff0000")])
    assert [s.id for s in strokes] == ["plate.stroke1", "plate.stroke0"]  # the outer one drawn first
    assert all(s.tags[0] == "stroke_of:plate" for s in strokes)
    body = geom.paint("plate", geom.footprint(kit, "side_l", PLATE), "#0000ff")
    img = render(compile_svg(Livery("#ffffff").add(*strokes, body), kit), ws)
    row = img[H, : int(0.3 * C) + 4]
    inside = row[int(0.3 * C) - 2 * w - 1 : int(0.3 * C) + 3]  # from inside the outer stroke into the plate
    assert inside[:, 1].max() < 40, inside  # no white base between strokes, or between stroke and plate
    assert drawn(strokes[0]).bounds[0] < drawn(strokes[1]).bounds[0]


def test_inside_and_centred_strokes_sit_where_asked(kit):
    w = thresholds().min_outline_px
    plate = geom.footprint(kit, "side_l", PLATE)
    (inner,) = geom.outline(kit, "plate", "side_l", PLATE, [(w, "#ff0000")], place="inside")
    assert drawn(inner).difference(plate).area < 1
    (mid,) = geom.outline(kit, "plate", "side_l", PLATE, [(2 * w, "#ff0000")], place="centred")
    ring = drawn(mid)
    for part in (ring.intersection(plate), ring.difference(plate)):
        assert across(part) == pytest.approx([w, w], abs=1.0)


def test_outer_sides_leave_holes_unstroked(kit):
    w = thresholds().min_outline_px
    holed = Polygon(box(0.05, 0.1, 0.45, 0.9), [box(0.2, 0.4, 0.3, 0.6)])
    hole = geom.footprint(kit, "side_l", Polygon(box(0.2, 0.4, 0.3, 0.6)))
    (every,) = geom.outline(kit, "holed", "side_l", holed, [(w, "#ff0000")])
    (outer,) = geom.outline(kit, "holed", "side_l", holed, [(w, "#ff0000")], sides="outer")
    assert drawn(every).intersection(hole).area > 0
    assert drawn(outer).intersection(hole).area == 0


def test_along_strokes_only_the_split_between_two_fields(kit):
    w = thresholds().min_outline_px
    a, b = box(0.05, 0.3, 0.4, 0.7), box(0.4, 0.3, 0.45, 0.7)
    (split,) = geom.outline(kit, "split", "side_l", a, [(w, "#ff0000")], place="centred", along=b)
    x0, _, x1, _ = drawn(split).bounds
    assert x0 >= 0.4 * C - w / 2 - 1.5
    assert x1 <= 0.4 * C + w / 2 + 0.5


def test_a_stroke_too_thin_to_show_in_the_sim_is_refused(kit):
    with pytest.raises(LiveryError):
        geom.outline(kit, "plate", "side_l", PLATE, [(thresholds().min_outline_px - 1, "#ff0000")])
    with pytest.raises(LiveryError):
        geom.outline(kit, "off", "side_l", box(5, 5, 6, 6), [(thresholds().min_outline_px, "#ff0000")])


def test_a_trim_follows_an_islands_own_edge_in_the_texture(kit):
    w = thresholds().min_outline_px
    (trim,) = geom.outline(kit, "trim", None, "i001", [(w, "#ff0000")], place="inside", sides="outer")
    assert across(drawn(trim)) == pytest.approx([w, w], abs=0.5)
    assert drawn(trim).bounds[2] <= H + 0.5  # inside the island
