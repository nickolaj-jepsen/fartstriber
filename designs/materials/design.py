"""Materials: every spec preset of the kit, for `livery bake check`.

The sides and the top are a 5 x 5 grid of cells. Down each column the finish steps through gloss, satin,
matte, metallic and chrome, and the colour through black, grey, white, red and blue, so every finish shows
every colour in every column, and so in every preview view. Each cell keeps a flat field and carries fine
detail in a contrasting colour of its own finish: three thin bars, two pinstripes and its finish's name.
"""

import shapely

from livery.model import Material
from livery.primitives import base, shape, stripe, text, waive

KIT = "mclaren-720s-gt3-evo"
ROLE = "fixture"

DARK, MID, LIGHT, RED, BLUE = "#202020", "#808080", "#e0e0e0", "#c81e1e", "#1e50c8"
COLOURS = (DARK, MID, LIGHT, RED, BLUE)
DETAIL = (LIGHT, DARK, DARK, LIGHT, LIGHT)  # each colour's detail ink
FINISHES = (Material.gloss, Material.satin, Material.matte, Material.metallic, Material.chrome)
LABELS = ("GLOSS", "SATIN", "MATTE", "METAL", "CHROME")
COLUMNS = (0.02, 0.21, 0.40, 0.59, 0.78, 0.98)  # u
SIDE_ROWS = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)  # v
TOP_ROWS = (-1.0, -0.6, -0.2, 0.2, 0.6, 1.0)  # w
BAR_U, BAR_W = (0.06, 0.075, 0.09), 0.006  # from the cell's start, in u; the field keeps the first 0.06
PIN_U, PIN = 0.11, 0.04  # where the pinstripes start in the cell, in u; their height, in v (w on top)
TYPE_U, TYPE = 0.15, 0.018  # the label's centre in the cell, and its em, in u
CLEAR = ("keep_clear", "zone")  # feature kinds a label stays off: the sim covers them
GRID = "a fixture grid: its cells meet other finishes and colours at seams and run near lines on purpose"


def label(kit, id, view, string, u, a, h, colour, finish):
    """The label at the first height in the cell that lands on a mapped island, off every keep-clear
    feature; none if there is no such spot."""
    for at in (0.35, 0.5, 0.2):
        y = a + at * h
        box = shapely.box(u - 0.04, y - 0.25 * h, u + 0.04, y + 0.25 * h)
        feats = (kit.feature(n) for n in kit.features())
        if any(f.view == view and f.kind in CLEAR and f.geometry().intersects(box) for f in feats):
            continue
        try:
            return text(kit, id, view, string, (u, y), TYPE, colour, material=finish, z=1)
        except ValueError:
            continue
    return None


def cell(kit, view, c, r):
    u0, u1 = COLUMNS[c], COLUMNS[c + 1]
    rows = TOP_ROWS if view == "top" else SIDE_ROWS
    a0, a1 = rows[r], rows[r + 1]
    h = a1 - a0
    f, k = (c + r) % 5, (c + 2 * r) % 5
    finish, colour, ink = FINISHES[f], COLOURS[k], DETAIL[k]
    tag = f"c{c}r{r}"
    out = stripe(kit, f"field.{tag}", a0, a1, colour, views=(view,), u0=u0, u1=u1, material=finish)
    for i, du in enumerate(BAR_U):
        x0, x1, y0, y1 = u0 + du, u0 + du + BAR_W, a0 + 0.15 * h, a1 - 0.15 * h
        pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        out.append(shape(kit, f"bar{i}.{tag}.{view}", view, pts, ink, finish, z=1))
    for i, at in enumerate((0.72, 0.86)):
        y = a0 + at * h
        pin = stripe(
            kit, f"pin{i}.{tag}", y, y + PIN * h / 0.2, ink, (view,), u0 + PIN_U, u1 - 0.01, finish, 1
        )
        out += pin
    out = [waive(waive(e, "near_tangent", GRID), "seam", GRID) for e in out]
    lab = label(kit, f"type.{tag}.{view}", view, LABELS[f], u0 + TYPE_U, a0, h, ink, finish)
    return out + ([lab] if lab else [])


def build(kit, p):
    return base(MID).add(
        *(e for view in kit.views for c in range(5) for r in range(5) for e in cell(kit, view, c, r))
    )
