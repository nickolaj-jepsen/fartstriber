"""The linters catch each kind of seeded error, on the element that has it, against the real McLaren kit."""

import contextlib
import shutil
import textwrap

import numpy as np
import pytest
from PIL import Image
from scipy import ndimage
from shapely.geometry import LineString

from livery import body, carspace
from livery.config import REPO, Workspace
from livery.designs import build_livery
from livery.errors import LiveryError
from livery.kit import Kit
from livery.lint.run import lint_design, thresholds
from livery.lint.source import lint_source, source_warnings
from livery.primitives import svg, waive
from livery.raster import rasterise
from tests.conftest import FIXTURES


def write(ws: Workspace, name: str, body: str) -> None:
    d = ws.design_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    (d / "design.py").write_text(textwrap.dedent(body), encoding="utf-8")


def _door_shut_line(kit: Kit, island: str) -> list[tuple[int, int]]:
    """Points down the left door's rear shut line every 12 rows: the kit's shut-line overlay on the door's
    island, beside where the body model's line maps."""
    ov = kit.dir / "overlays"
    shut = np.asarray(Image.open(ov / "shutlines.png").convert("L")) > 127
    shut &= np.asarray(Image.open(ov / "islands.png")) == int(
        island[1:]
    )  # an island's number is its value there
    f = kit.feature("door_shut_line_l")
    line = LineString(f.coords)
    mapped = []
    for t in np.linspace(0, 1, 50):
        p = line.interpolate(t, normalized=True)
        with contextlib.suppress(ValueError):  # where it runs past the mapped paint
            mapped.append(carspace.anchor(kit, f.view, (p.x, p.y))[1])
    xs, ys = np.array(mapped).T
    order = np.argsort(ys)
    pts = []
    for y in range(int(ys.min()), int(ys.max()), 12):
        x = int(np.interp(y, ys[order], xs[order]))
        near = np.nonzero(shut[y, x - 10 : x + 11])[0]
        if near.size:
            pts.append((x - 10 + int(near.mean()), y))
    return pts


@pytest.fixture(scope="module")
def seeded_report(real_ws, real_kit) -> dict:
    kit = real_kit
    numbers = [z["box"] for z in carspace.kit_data(kit)["keep_clear"] if z["kind"] == "number"]
    on_car = [carspace.to_car(kit, ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)) for b in numbers]
    view, at, _ = next(c for c in on_car if c is not None and c[0] in kit.side_views)
    light = next(f for f in kit.features().values() if f.kind == "keep_clear" and f.view == "side_l")
    (island,) = kit.parts()["door_l"].islands
    door = body.area(kit, "door_l").representative_point()
    dx, dy = round(door.x), round(door.y)
    off = np.asarray(
        ndimage.distance_transform_edt(np.asarray(Image.open(kit.mask_path).convert("L")) <= 127)
    )
    inner = off[30:-30, 30:-30]  # a 60 px square centred there is on the canvas, as far off the car as can be
    oy, ox = np.unravel_index(np.argmax(inner), inner.shape)
    line = _door_shut_line(kit, island)
    u0, _, u1, _ = kit.feature("door_shut_line_l").box  # a strip down the door's rear edge
    beside = [(x + 8, y) for x, y in line] + [(x + 70, y) for x, y in reversed(line)]
    beside_d = "M" + " L".join(f"{x},{y}" for x, y in beside) + " Z"
    write(
        real_ws,
        "seeded",
        f'''
        from livery.primitives import base, clip_to, shape, svg, text, waive

        KIT = "{kit.name}"
        MIRRORED = (
            '<text transform="translate({dx},{dy}) scale(-1,1)" font-family="Barlow" font-size="60">'
            "MIRROR</text>"
        )
        CUT = (({u0:.4f}, -0.1), ({u1:.4f}, -0.1), ({u1:.4f}, 1.1), ({u0:.4f}, 1.1))
        BLURRED = (
            '<filter id="f"><feGaussianBlur stdDeviation="3"/></filter>'
            '<rect x="{dx}" y="{dy}" width="120" height="60" filter="url(#f)"/>'
        )
        IMAGE = '<image x="{dx}" y="{dy}" width="40" height="40" href="data:image/png;base64,AAAA"/>'


        def build(kit, p):
            return base("#e8e8e8").add(
                svg(MIRRORED, id="mirrored"),
                text(kit, "on-number", "{view}", "ACME", at=({at[0]:.4f}, {at[1]:.4f}), size=0.03,
                     color="#111"),
                text(kit, "on-light", "side_l", "ACME", at={light.at}, size=0.03, color="#111"),
                svg('<rect x="{ox}" y="{oy}" width="60" height="60" fill="#111"/>', id="off-car"),
                clip_to(kit, "{island}", shape(kit, "cut", "side_l", CUT, "#c00", tags=("crosses_seam",))),
                svg('<path d="{beside_d}" fill="#06c"/>', id="hugs-shut-line"),
                svg('<rect x="{dx}" y="{dy}" width="120" height="60" opacity="0.5"/>', id="see-through"),
                svg(BLURRED, id="blurred"),
                svg(IMAGE, id="image"),
                waive(svg('<path d="{beside_d}" fill="#06c"/>', id="waived", z=1), "near_tangent", "a test"),
            )
        ''',
    )
    return lint_design(real_ws, "seeded")


@pytest.fixture(scope="module")
def seeded(seeded_report) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for e in seeded_report["errors"]:
        found.setdefault(e["id"], set()).add(e["check"])
    return found


SEEDED = [
    ("mirrored", "orientation"),
    ("on-number", "keep_clear"),
    ("on-light", "keep_clear"),
    ("off-car", "off_car"),
    ("cut", "seam"),
    ("hugs-shut-line", "near_tangent"),
    ("see-through", "forbidden_svg"),
    ("blurred", "forbidden_svg"),
    ("image", "forbidden_svg"),
]


def test_output_lint_catches_each_seeded_error(seeded):
    # One test, not one per row: under xdist every worker that takes a row re-lints the seeded design.
    missed = [(element, check) for element, check in SEEDED if check not in seeded.get(element, set())]
    assert missed == [], seeded


def test_a_waiver_keeps_its_check_out_of_the_report_and_lists_what_it_hid(seeded, seeded_report):
    assert "waived" not in seeded
    (waiver,) = [w for w in seeded_report["waivers"] if w["id"] == "waived"]
    assert waiver["check"] == "near_tangent"
    assert waiver["reason"]
    assert waiver["hid"]


def test_a_waiver_needs_a_reason_and_a_real_check():
    el = svg("<g/>", "x")
    with pytest.raises(LiveryError):
        waive(el, "near_tangent", " ")
    with pytest.raises(LiveryError):
        waive(el, "near_tangnet", "a typo")
    assert waive(el, "seam", "why").waivers == (("seam", "why"),)


def test_design_lint_catches_import_random(tmp_path):
    p = tmp_path / "design.py"
    p.write_text('import random\nKIT = "x"\n\ndef build(kit, p):\n    return random.random()\n')
    assert 1 in {e["line"] for e in lint_source(p)}


def test_design_lint_warns_on_reading_back_a_primitives_svg(tmp_path):
    p = tmp_path / "design.py"
    p.write_text(
        textwrap.dedent(
            """\
            from dataclasses import replace
            from livery.primitives import base, svg
            KIT = "x"

            def build(kit, p):
                el = svg("<g/>", "a")
                i = el.svg.index("g")
                el = replace(el, svg=el.svg.replace("g", "path"))
                return base(el.__dict__["id"])
            """
        )
    )
    assert lint_source(p) == []
    assert sorted(w["line"] for w in source_warnings(p)) == [7, 8, 9]


def test_lint_thresholds_come_only_from_the_policy(tmp_path):
    full = (REPO / "policy.toml").read_text(encoding="utf-8")
    cut = tmp_path / "policy.toml"
    cut.write_text(full.replace("min_feature_px", "# min_feature_px"), encoding="utf-8")
    with pytest.raises(LiveryError):
        thresholds(cut)
    assert thresholds(REPO / "policy.toml").min_feature_px > 0


def test_a_clean_design_passes_the_design_lint():
    assert lint_source(FIXTURES / "designs" / "primitives-demo" / "design.py") == []


# Checks of where an element sits against the kit's lines, seams and zones: a refit moves those under the
# demo's places, and the seeded design shows each one catches what it should.
PLACEMENT = {"keep_clear", "near_tangent", "seam"}


def test_every_primitive_builds_on_the_real_kit_and_draws_nothing_the_lint_faults(real_ws):
    shutil.copytree(FIXTURES / "designs", real_ws.designs, dirs_exist_ok=True)
    built = build_livery(real_ws, "primitives-demo")
    outputs = rasterise(*built, real_ws)
    assert {"car.tga", "car_spec.tga"} <= outputs.keys()
    report = lint_design(real_ws, "primitives-demo", outputs=outputs, built=built)
    assert [e for e in report["errors"] if e["check"] not in PLACEMENT] == []
    assert report["waivers"] == []
