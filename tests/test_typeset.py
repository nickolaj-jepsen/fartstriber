"""Type as geometry: glyphs against resvg's own rendering, and type blocks laid out in car space."""

import io
import math

import numpy as np
import pytest
import resvg_py
import shapely
import shapely.affinity
from PIL import Image
from shapely.geometry import Polygon

from livery import carspace, typeset
from livery.config import REPO
from livery.kit import load
from livery.lint.run import thresholds
from tests.conftest import TWO, drawn, two_islands

FONTS = REPO / "assets" / "fonts"


def _alpha(body: str, w: int, h: int, fonts: bool) -> np.ndarray:
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">{body}</svg>'
    kw = {"skip_system_fonts": True, "font_dirs": [str(FONTS)]} if fonts else {}
    png = resvg_py.svg_to_bytes(svg_string=svg, **kw)
    return np.asarray(Image.open(io.BytesIO(bytes(png))).convert("RGBA"))[:, :, 3] > 127


@pytest.mark.parametrize(("family", "weight"), [("Barlow Condensed", 800), ("Playfair Display", 700)])
def test_glyph_geometry_matches_how_resvg_sets_the_same_text(family, weight):
    text, size, w, h = "AVATAR Wy 451", 80, 700, 130
    theirs = _alpha(
        f'<text x="10" y="100" font-family="{family}" font-weight="{weight}" font-size="{size}">{text}</text>',
        w,
        h,
        fonts=True,
    )
    shape = shapely.affinity.affine_transform(
        typeset.text_shape(text, family, size, weight), [1, 0, 0, -1, 10, 100]
    )
    ours = _alpha(f'<path fill-rule="evenodd" d="{carspace.path_d(shape)}"/>', w, h, fonts=False)
    assert (ours & theirs).sum() / (ours | theirs).sum() > 0.98


def test_pairs_are_kerned_and_tracking_and_size_scale_a_line():
    a, v, av = (typeset.measure(t, "Barlow", 1.0, 700) for t in ("A", "V", "AV"))
    assert av.advance < a.advance + v.advance
    plain = typeset.measure("TOW", size=1.0)
    assert typeset.measure("TOW", size=1.0, tracking=0.1).width > plain.width
    assert typeset.measure("TOW", size=2.0).width == pytest.approx(2 * plain.width)


def test_overlapping_contours_fill_by_the_nonzero_rule():
    sq = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]
    shifted = [(x + 1, y) for x, y in sq]
    assert typeset._nonzero([sq, shifted]).area == pytest.approx(6.0)  # one direction: their union
    inner = [(0.5, 0.5), (0.5, 1.5), (1.5, 1.5), (1.5, 0.5)]  # turning the other way: a counter
    assert typeset._nonzero([sq, inner]).area == pytest.approx(3.0)


# --- type blocks on a synthetic kit, where the reading frame is the texture scaled ----------------------


@pytest.fixture
def kit(tmp_path):
    return load(two_islands(tmp_path), "two")


def _reading(g):
    """The two-island kit's texture back to side_l's reading frame (x = u, y up = v times the aspect)."""
    return shapely.affinity.affine_transform(g, [1 / TWO, 0, 0, -0.5 / TWO, 0, 0.5])


LINES = [
    typeset.Line("EVA-01", 0.09, color="#000001"),
    typeset.Line("NERV", 0.06, color="#000002"),
    typeset.Line("TYPE 7", 0.06, color="#000003"),
]


def _leaned(g, s):
    pts = np.concatenate([np.asarray(p.exterior.coords) for p in getattr(g, "geoms", [g])])
    return pts[:, 0] - s * pts[:, 1], pts[:, 1]


def test_lines_share_a_start_edge_on_the_lean_and_one_gap(kit):
    lean, gap = 20.0, 0.02
    (text,) = typeset.type_block(kit, "tag", "side_l", LINES, (0.12, 0.85), "#000000", gap=gap, lean=lean)
    s = math.tan(math.radians(lean))
    starts, tops, bottoms = [], [], []
    for ln in LINES:
        x, y = _leaned(_reading(drawn(text, ln.color)), s)
        starts.append(x.min())
        tops.append(y.max())
        bottoms.append(y.min())
    assert max(starts) - min(starts) < 1.5 / TWO  # within a texture pixel or so
    caps = [typeset.measure(ln.text, ln.font, ln.size, ln.weight).cap for ln in LINES]
    baselines = [tops[0] - caps[0]]
    for cap in caps[1:]:
        baselines.append(baselines[-1] - gap - cap)
    assert bottoms[1] == pytest.approx(baselines[1], abs=1.5 / TWO)  # NERV sits on its baseline
    assert tops[2] == pytest.approx(baselines[1] - gap, abs=1.5 / TWO)  # TYPE 7's caps hang a gap below


def test_a_plate_has_the_same_padding_on_every_side(kit):
    lean, pad = 20.0, 0.015
    plate, text = typeset.type_block(
        kit, "tag", "side_l", LINES, (0.12, 0.85), "#000000", lean=lean, plate="#ffffff", pad=pad
    )
    s = math.tan(math.radians(lean))
    ink = _reading(drawn(text))
    field = _reading(drawn(plate))
    x, y = _leaned(ink, s)
    fx, fy = _leaned(field, s)
    square = math.cos(math.atan(s))  # from x - s y to the distance square to a raked edge
    assert (x.min() - fx.min()) * square == pytest.approx(pad, abs=1.5 / TWO)
    assert (fx.max() - x.max()) * square == pytest.approx(pad, abs=1.5 / TWO)
    assert fy.max() - y.max() == pytest.approx(pad, abs=1.5 / TWO)
    assert y.min() - fy.min() == pytest.approx(pad, abs=1.5 / TWO)


def test_a_block_crosses_a_seam_and_its_strokes_follow(kit):
    w = thresholds().min_outline_px
    els = typeset.type_block(
        kit, "big", "side_l", [typeset.Line("0101", 0.2)], (0.5, 0.5), "#000000", anchor=(0.5, 0.5),
        strokes=[(w, "#ff0000")],
    )  # fmt: skip
    stroke, text = els
    assert stroke.tags[0] == f"stroke_of:{text.id}"
    ink = drawn(text)
    assert ink.intersection(shapely.box(0, 0, TWO / 2, TWO)).area > 0
    assert ink.intersection(shapely.box(TWO / 2, 0, TWO, TWO)).area > 0
    assert text.kind == "text"


def test_glyphs_the_paint_would_cut_are_refused_and_small_type_too(kit):
    with pytest.raises(typeset.TypesetError):  # runs off the view's mapped area at the tail
        typeset.type_block(kit, "off", "side_l", [typeset.Line("TAILGATE", 0.1)], (0.8, 0.5), "#000000")
    small = 0.8 * thresholds().min_text_px / (2 * TWO)  # an em this many car lengths is that many px here
    with pytest.raises(typeset.TypesetError):
        typeset.type_block(kit, "tiny", "side_l", [typeset.Line("TINY", small)], (0.2, 0.5), "#000000")


# --- on the real kit -----------------------------------------------------------------------------------


def test_a_block_reads_nose_to_tail_on_the_left_and_tail_to_nose_on_the_right(real_kit):
    lines = [typeset.Line("TEST TYPE", 0.03)]
    for view, sign in (("side_l", 1), ("side_r", -1)):
        (text,) = typeset.type_block(real_kit, "t", view, lines, (0.6, 0.7), "#000000")
        u = [p[0] for p in text.car["polygon"]]
        assert sign * (np.mean(u) - 0.6) > 0
        assert Polygon(text.car["polygon"]).area > 0
