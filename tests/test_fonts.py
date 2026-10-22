import pytest
from PIL import ImageFont

from livery.config import Workspace
from livery.raster import render

WS = Workspace()
FAMILIES = sorted({ImageFont.truetype(str(p), 10).getname()[0] for p in WS.fonts.rglob("*.ttf")})


def ink(family: str) -> int:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="80">'
        '<rect width="400" height="80" fill="#fff"/>'
        f'<text x="10" y="60" font-family="{family}" font-weight="700" font-size="48">FIREPROOF</text></svg>'
    )
    return int((render(svg, WS) != 255).any(axis=2).sum())


def test_repo_ships_fonts_with_licences():
    assert FAMILIES
    for ttf in WS.fonts.rglob("*.ttf"):
        assert (ttf.parent / "OFL.txt").exists(), ttf


@pytest.mark.parametrize("family", FAMILIES)
def test_each_font_renders_without_system_fonts(family):
    assert ink(family) > 0


def test_system_fonts_are_not_used():
    assert ink("Arial") == 0
