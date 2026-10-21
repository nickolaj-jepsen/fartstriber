import io

import numpy as np
import pytest
import resvg_py
from PIL import Image, ImageFont

from livery.config import REPO

FONT_DIR = REPO / "assets" / "fonts"
FAMILIES = sorted({ImageFont.truetype(str(p), 10).getname()[0] for p in FONT_DIR.rglob("*.ttf")})


def ink(family: str) -> int:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="80">'
        f'<text x="10" y="60" font-family="{family}" font-weight="700" font-size="48">FIREPROOF</text></svg>'
    )
    png = resvg_py.svg_to_bytes(svg_string=svg, skip_system_fonts=True, font_dirs=[str(FONT_DIR)])
    alpha = np.asarray(Image.open(io.BytesIO(bytes(png))).convert("RGBA"))[:, :, 3]
    return int((alpha > 0).sum())


def test_repo_ships_fonts_with_licences():
    assert FAMILIES
    for ttf in FONT_DIR.rglob("*.ttf"):
        assert (ttf.parent / "OFL.txt").exists(), ttf


@pytest.mark.parametrize("family", FAMILIES)
def test_each_font_renders_without_system_fonts(family):
    assert ink(family) > 0


def test_system_fonts_are_not_used():
    assert ink("Arial") == 0
