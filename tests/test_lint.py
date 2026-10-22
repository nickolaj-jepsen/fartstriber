"""The linters catch each kind of seeded error, on the element that has it, against the real McLaren kit."""

import shutil
import textwrap

import numpy as np
import pytest
from PIL import Image
from shapely.geometry import Point

from livery import carspace, kit_fit
from livery.config import REPO, Workspace
from livery.designs import render_outputs
from livery.errors import LiveryError
from livery.kit import load
from livery.lint.run import lint_design, thresholds
from livery.lint.source import lint_source, source_warnings
from livery.primitives import svg, waive
from tests.conftest import FIXTURES

KIT = "mclaren-720s-gt3-evo"


@pytest.fixture(scope="module")
def real_ws(tmp_path_factory) -> Workspace:
    """The real kit, with the designs only the tests build."""
    root = tmp_path_factory.mktemp("ws")
    shutil.copytree(REPO / "kits" / KIT, root / "kits" / KIT)
    shutil.copytree(FIXTURES / "designs", root / "designs")
    return Workspace(root=root, assets=REPO / "assets")


def write(ws: Workspace, name: str, body: str) -> None:
    d = ws.design_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    (d / "design.py").write_text(textwrap.dedent(body), encoding="utf-8")


def _door_shut_line(ws: Workspace) -> list[tuple[int, int]]:
    """Points down the left door's rear shut line, from the kit's overlay."""
    ov = ws.kits / KIT / "overlays"
    shut = np.asarray(Image.open(ov / "shutlines.png").convert("L")) > 127
    door = np.asarray(Image.open(ov / "islands.png")) == 128
    pts = []
    for y in range(1660, 1840, 12):
        xs = np.nonzero(shut[y] & door[y])[0]
        if xs.size:
            pts.append((int(xs.mean()), y))
    return pts


def _seam_on(kit, island: str, view: str) -> float:
    """u of a seam the island shares with another in the view, from the kit's seam pairs."""
    shapes = carspace.island_shapes(kit)
    for x1, y1, x2, y2 in kit_fit.seam_pairs(kit):
        here = shapes[island].contains(Point(x1, y1))
        if here and not shapes[island].contains(Point(x2, y2)):
            found = carspace.to_car(kit, (x1, y1))
            if found is not None and found[0] == view and 0.1 < found[1][0] < 0.9:
                return float(found[1][0])
    raise AssertionError(f"{island} shares no seam in {view}")


@pytest.fixture(scope="module")
def seeded_report(real_ws) -> dict:
    kit = load(real_ws, KIT)
    number = next(
        z["box"] for z in carspace.kit_data(kit)["keep_clear"] if z["kind"] == "number" and z["box"][1] > 1600
    )
    view, at, _ = carspace.to_car(kit, ((number[0] + number[2]) / 2, (number[1] + number[3]) / 2))
    light = next(f for f in kit.features().values() if f.kind == "keep_clear" and f.view == "side_l")
    cut_u = _seam_on(kit, "i128", "side_l")
    line = _door_shut_line(real_ws)
    beside = [(x + 8, y) for x, y in line] + [(x + 70, y) for x, y in reversed(line)]
    beside_d = "M" + " L".join(f"{x},{y}" for x, y in beside) + " Z"
    write(
        real_ws,
        "seeded",
        f'''
        from livery.primitives import base, clip_to, shape, svg, text, waive

        KIT = "{KIT}"
        MIRRORED = (
            '<text transform="translate(1300,1720) scale(-1,1)" font-family="Barlow" font-size="60">'
            "MIRROR</text>"
        )
        CUT = (({cut_u - 0.025:.4f}, -0.1), ({cut_u + 0.025:.4f}, -0.1), ({cut_u + 0.025:.4f}, 1.1), ({cut_u - 0.025:.4f}, 1.1))
        BLURRED = (
            '<filter id="f"><feGaussianBlur stdDeviation="3"/></filter>'
            '<rect x="1250" y="1600" width="120" height="60" filter="url(#f)"/>'
        )
        IMAGE = '<image x="1250" y="1400" width="40" height="40" href="data:image/png;base64,AAAA"/>'


        def build(kit, p):
            return base("#e8e8e8").add(
                svg(MIRRORED, id="mirrored"),
                text(kit, "on-number", "{view}", "ACME", at=({at[0]:.4f}, {at[1]:.4f}), size=0.03,
                     color="#111"),
                text(kit, "on-light", "side_l", "ACME", at={light.at}, size=0.03, color="#111"),
                svg('<rect x="640" y="1040" width="60" height="60" fill="#111"/>', id="off-car"),
                clip_to(kit, "i128", shape(kit, "cut", "side_l", CUT, "#c00", tags=("crosses_seam",))),
                svg('<path d="{beside_d}" fill="#06c"/>', id="hugs-shut-line"),
                svg('<rect x="1250" y="1500" width="120" height="60" opacity="0.5"/>', id="see-through"),
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


@pytest.mark.parametrize(
    ("element", "check"),
    [
        ("mirrored", "orientation"),
        ("on-number", "keep_clear"),
        ("on-light", "keep_clear"),
        ("off-car", "off_car"),
        ("cut", "seam"),
        ("hugs-shut-line", "near_tangent"),
        ("see-through", "forbidden_svg"),
        ("blurred", "forbidden_svg"),
        ("image", "forbidden_svg"),
    ],
)
def test_output_lint_catches_seeded_error(seeded, element, check):
    assert check in seeded.get(element, set()), seeded


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
    assert lint_source(p)


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


def test_every_primitive_builds_and_lints_on_the_real_kit(real_ws):
    outputs = render_outputs(real_ws, "primitives-demo")
    assert {"car.tga", "car_spec.tga"} <= outputs.keys()
    report = lint_design(real_ws, "primitives-demo", outputs=outputs)
    assert report["errors"] == []
    assert report["waivers"] == []
