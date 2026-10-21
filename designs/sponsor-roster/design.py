"""Sponsor roster: every roster brand on the McLaren, in the groups a race car carries them.

A light car with a black lower band. Black marks on the light body, light marks on the black band: one
colour per field. Each mark is clipped to the island its centre lands on, so every group sits inside one
mapped panel (the door and lower door are one island on each side, u 0.33-0.76).
"""

from dataclasses import dataclass, replace

import shapely

from livery import body, geom
from livery.params import DesignParams, param
from livery.primitives import base, mirror_sides, sponsor, sponsor_group

KIT = "mclaren-720s-gt3-evo"
ROLE = "fixture"
BODY, BAND = "#ecebe6", "#151515"
# Parts wholly inside the band whose maps stop short of their outlines, painted whole: each side's sill
# group (sill strip and side intake strip) and grille upright, which carries the nose marks.
WHOLE = (("side_l", ("sill_l", "grille_upright_l")), ("side_r", ("sill_r", "grille_upright_r")))


@dataclass(frozen=True)
class Params(DesignParams):
    band_v: float = param(0.36, 0.2, 0.5)  # the black lower band's top
    # The headline on the door and the row under it.
    head_u: float = param(0.62, 0.4, 0.7)
    head_v: float = param(0.76, 0.5, 0.8)
    head_h: float = param(0.042, 0.03, 0.08)
    door_v: float = param(0.585, 0.38, 0.7)
    door_h: float = param(0.02, 0.015, 0.04)
    # The row along the lower door, on the black band.
    sill_v: float = param(0.23, 0.08, 0.3)
    sill_h: float = param(0.022, 0.015, 0.03)
    # The stack on the rear bumper's corner, behind the rear wheel.
    rear_u: float = param(0.965, 0.94, 0.99)
    rear_h: float = param(0.026, 0.015, 0.04)
    # Top: bonnet, roof, engine cover.
    bonnet_u: float = param(0.05, 0.01, 0.08)
    bonnet_h: float = param(0.03, 0.015, 0.04)
    roof_u: float = param(0.51, 0.45, 0.57)
    roof_h: float = param(0.046, 0.03, 0.06)
    deck_u: float = param(0.72, 0.64, 0.8)
    deck_h: float = param(0.032, 0.015, 0.04)
    # The marks stacked on each grille upright, on the band.
    nose_u: float = param(-0.005, -0.03, 0.01)


def _band(kit, view: str, whole: tuple[str, ...], top: float):
    """The black lower band on one side, nose to tail under `top`, with the side's WHOLE parts, as one
    element: its edges snapped clear of the lines the near-tangent lint judges."""
    rect = geom.footprint(kit, view, [(-0.2, -0.3), (1.2, -0.3), (1.2, top), (-0.2, top)])
    foot, snaps = geom.snapped(kit, view, shapely.union_all([rect, body.regions(kit, whole)]))
    return replace(geom.paint(f"band.{view}", foot, BAND, tags=("crosses_seam",)), snaps=snaps)


def build(kit, p: Params):
    els = [_band(kit, view, whole, p.band_v) for view, whole in WHOLE]
    els += mirror_sides(
        sponsor,
        kit,
        "headline",
        "fireproof",
        at=(p.head_u, p.head_v),
        height=p.head_h,
        color=BAND,
        variant="lockup",
    )
    els += mirror_sides(
        sponsor_group,
        kit,
        "door",
        ["grivant", "telvio"],
        start=(0.35, p.door_v),
        end=(0.7, p.door_v),
        height=p.door_h,
        color=BAND,
    )
    els += mirror_sides(
        sponsor_group,
        kit,
        "sill",
        [("huilec", "wordmark"), ("hexmoor", "wordmark"), ("drexa", "wordmark"), ("brandvik", "mark")],
        start=(0.41, p.sill_v),
        end=(0.775, p.sill_v),
        gap=0.22,
        height=p.sill_h,
        color=BODY,
    )
    els += mirror_sides(
        sponsor_group,
        kit,
        "rear",
        [("ostrand", "mark"), ("rigmount", "mark")],
        start=(p.rear_u, 0.78),
        end=(p.rear_u, 0.42),
        height=p.rear_h,
        color=BAND,
        axis="column",
    )
    els += mirror_sides(
        sponsor_group,
        kit,
        "nose",
        [("hexmoor", "mark"), ("zentiq", "mark")],
        start=(p.nose_u, 0.33),
        end=(p.nose_u, 0.09),
        height=0.018,
        color=BODY,
        axis="column",
    )
    els += mirror_sides(
        sponsor, kit, "mirror", "altmere", at=(0.375, 0.995), height=0.014, color=BAND, variant="mark"
    )
    els.append(sponsor(kit, "bonnet", "top", "avelor", (p.bonnet_u, 0.0), BAND, height=p.bonnet_h))
    els.append(sponsor(kit, "roof", "top", "corbaux", (p.roof_u, 0.0), BAND, height=p.roof_h, facing="rear"))
    els += sponsor_group(
        kit,
        "deck",
        "top",
        [("norvane", "wordmark"), "bellcrest"],
        start=(p.deck_u - 0.08, 0.0),
        end=(p.deck_u + 0.08, 0.0),
        axis="column",
        facing="rear",
        height=p.deck_h,
        color=BAND,
    )
    return base(BODY).add(*els)
