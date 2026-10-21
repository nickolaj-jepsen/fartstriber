"""The calibration livery: bands at fixed car-space positions, drawn through any kit's maps. They must meet
across every seam and the beltline; `livery kit review` shoots it on the kit it reviews."""

from livery.carspace import polygon
from livery.kit import Kit
from livery.model import Livery
from livery.primitives import svg

ALLOW = ("allow:near_tangent",)  # the bands run along seams and panel edges on purpose
U_BANDS = ((0.2, "#e00000"), (0.4, "#0050e0"), (0.6, "#00a000"), (0.8, "#f0a000"))
WIDTH = 0.02
BELT = (0.55, 0.6)  # v on both sides
SPINE = 0.03  # half its width in w, along the top's middle
DARK = "#202020"


def calibration(kit: Kit) -> Livery:
    liv = Livery(base="#c8c8c8")
    sides = kit.side_views
    for u, colour in U_BANDS:
        a, b = u - WIDTH / 2, u + WIDTH / 2
        name = f"u{int(u * 100)}"
        for view in sides:
            band = [(a, -0.2), (b, -0.2), (b, 1.2), (a, 1.2)]
            liv.add(svg(polygon(kit, view, band, colour), id=f"{name}-{view}", tags=ALLOW))
        if "top" in kit.views:
            top = [(a, -1.2), (b, -1.2), (b, 1.2), (a, 1.2)]
            liv.add(svg(polygon(kit, "top", top, colour), id=f"{name}-top", tags=ALLOW))
    lo, hi = BELT
    for view in sides:
        belt = [(-0.1, lo), (1.1, lo), (1.1, hi), (-0.1, hi)]
        liv.add(svg(polygon(kit, view, belt, DARK), id=f"belt-{view}", z=1, tags=ALLOW))
    if "top" in kit.views:
        spine = [(-0.1, -SPINE), (1.1, -SPINE), (1.1, SPINE), (-0.1, SPINE)]
        liv.add(svg(polygon(kit, "top", spine, DARK), id="spine", z=1, tags=ALLOW))
    for view in kit.face_views:  # the belt runs on round the face, and the spine down it
        belt = [(-2.0, lo), (2.0, lo), (2.0, hi), (-2.0, hi)]
        liv.add(svg(polygon(kit, view, belt, DARK), id=f"belt-{view}", z=1, tags=ALLOW))
        spine = [(-SPINE, -1.0), (SPINE, -1.0), (SPINE, 2.0), (-SPINE, 2.0)]
        liv.add(svg(polygon(kit, view, spine, DARK), id=f"spine-{view}", z=1, tags=ALLOW))
    return liv
