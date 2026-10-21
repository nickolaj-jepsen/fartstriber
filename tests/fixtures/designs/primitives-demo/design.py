"""Every primitive once: the tests build and lint it on the real kit to check the library."""

from dataclasses import dataclass

from shapely.geometry import LineString, Polygon

from livery import geom, patterns
from livery.params import DesignParams, param
from livery.primitives import (
    band,
    base,
    clip_to,
    mirror_sides,
    panel,
    pattern_fill,
    shape,
    sponsor,
    stripe,
    svg,
    text,
)
from livery.typeset import Line, type_block

KIT = "mclaren-720s-gt3-evo"
ORANGE, INNER, CORE, INK, PAPER = "#f46200", "#ff8000", "#ffd500", "#1b1b1b", "#f2f0ea"
FLASH = ((0.05, 0.55), (0.2, 0.62), (0.05, 0.7))


@dataclass(frozen=True)
class Params(DesignParams):
    band_u: float = param(0.62, 0.3, 0.8)
    band_width: float = param(0.08, 0.02, 0.2)
    band_angle: float = param(12.0, -30.0, 30.0)
    stripe_v: float = param(0.25, 0.2, 0.45)
    lean: float = param(45.0, 0.0, 60.0)


def build(kit, p: Params):
    tile = f'<rect width="16" height="16" fill="{INK}"/><rect width="8" height="8" fill="{PAPER}"/>'
    k = geom.rake(kit, p.lean)
    burn = LineString([(0.5 + k * a, a) for a in (0.1, 1.1)])
    behind = Polygon([(0.5 - 0.2 * k, -0.2), (1.3, -0.2), (1.3, 1.2), (0.5 + 1.2 * k, 1.2)])
    tiers = [patterns.Tier(40, 0.6, 0.1), patterns.Tier(20, 0.8, 0.25)]
    return base(PAPER).add(
        band(kit, "band", p.band_u, p.band_u + p.band_width, ORANGE, angle=p.band_angle),
        stripe(kit, "sill", p.stripe_v - 0.05, p.stripe_v, INK),
        stripe(kit, "spine", -0.12, 0.12, INK, views="top", u0=0.06),
        panel(kit, "rear-deck", "i104", INNER, material="satin"),
        geom.outline(kit, "flash", "side_l", FLASH, [(16, INK)]),
        shape(kit, "flash", "side_l", list(FLASH), CORE),
        mirror_sides(text, kit, "name", "FIREPROOF", at=(0.3, 0.55), size=0.035, color=INK),
        mirror_sides(
            type_block,
            kit,
            "tag",
            [Line("TYPE 01", 0.026), Line("PRIMITIVES DEMO", 0.016, weight=700, tracking=0.06)],
            at=(0.5, 0.82),
            color=INK,
            lean=12.0,
            plate=CORE,
            pad=0.005,
        ),
        mirror_sides(
            sponsor, kit, "mark", "fireproof", at=(0.62, 0.82), color="own", height=0.05, variant="mark"
        ),
        sponsor(kit, "bonnet-mark", "top", "fireproof", (0.2, 0.3), "own", height=0.04, variant="mark"),
        pattern_fill(kit, "check", "i080", tile, 16, z=1),
        patterns.hazard(kit, "lip", "i083", (CORE, INK), 32, lean=30.0),
        patterns.dissolve(
            kit, "burn", "side_l", burn, tiers, {INK: 2, ORANGE: 1}, p.rng("burn"), field=behind
        ),
        patterns.dash_run(
            kit,
            "pin",
            "side_l",
            [(-0.2, 0.62), (1.2, 0.62), (1.2, 0.66), (-0.2, 0.66)],
            0.4,
            0.6,
            INK,
            0.05,
            lean=p.lean,
        ),
        patterns.stripe_bundle(
            kit,
            "bundle",
            0.3,
            [0.06, 0.04],
            [ORANGE, INNER],
            gap=0.04,
            u0=0.5,
            u1=[0.8, 0.75],
            views="side_r",
        ),
        patterns.chevrons(kit, "chevrons", "top", 0.15, 3, 0.05, 0.1, 0.015, ORANGE, lean=p.lean),
        clip_to(kit, "i128", svg('<circle cx="1302" cy="1684" r="110" fill="#3a6ea5"/>', id="disc", z=2)),
    )
