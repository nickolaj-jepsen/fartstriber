import xml.etree.ElementTree as ET

import pytest

from livery.compile import SVG_NS, compile_svg, spec_svg
from livery.kit import load
from livery.model import Livery, Material
from livery.primitives import svg

N = f"{{{SVG_NS}}}"


def groups(doc: str) -> dict[str, ET.Element]:
    return {g.attrib["data-id"]: g for g in ET.fromstring(doc)}


def test_elements_compile_to_groups_in_z_order(ws):
    kit = load(ws, "tiny")
    liv = Livery(base="#000").add(
        svg('<rect width="4" height="4" fill="#f00"/>', id="top", z=2),
        svg('<rect width="4" height="4" fill="#0f0"/>', id="bottom", material=Material.matte),
    )
    g = groups(compile_svg(liv, kit))
    assert list(g) == ["base", "bottom", "top"]
    assert g["bottom"].attrib["data-material"] == "matte"


def test_duplicate_ids_are_rejected(ws):
    liv = Livery(base="#000").add(svg("<g/>", id="a"), svg("<g/>", id="a"))
    with pytest.raises(ValueError):
        compile_svg(liv, load(ws, "tiny"))


def test_spec_pass_paints_each_element_in_its_material(ws):
    kit = load(ws, "tiny")
    liv = Livery(base="#000").add(
        svg(
            '<rect width="4" height="4" fill="#123456"/>'
            '<path d="M0,0 L4,4" fill="none" stroke="#abcdef"/>'
            '<text style="fill:#ff0000;stroke:none">x</text>'
            '<image x="1" y="2" width="3" height="4" href="data:image/png;base64,AAAA"/>'
            '<clipPath id="c"><rect width="2" height="2" fill="#ffffff"/></clipPath>',
            id="m",
            material=Material.metallic,
        )
    )
    metal = "#ff3cff"
    g = groups(spec_svg(compile_svg(liv, kit), kit))["m"]
    assert g.find(f"{N}rect").attrib["fill"] == metal
    path = g.find(f"{N}path")
    assert path.attrib["fill"] == "none"
    assert path.attrib["stroke"] == metal
    assert g.find(f"{N}text").attrib["style"] == f"fill:{metal};stroke:none"
    assert g.find(f"{N}image") is None
    assert g.find(f"{N}clipPath/{N}rect").attrib["fill"] == "#ffffff"
