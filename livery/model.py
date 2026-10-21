from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Material(StrEnum):
    gloss = "gloss"
    satin = "satin"
    matte = "matte"
    metallic = "metallic"
    chrome = "chrome"


@dataclass(frozen=True)
class Element:
    """One named thing on the car. `svg` is its markup in template pixels.

    `car` records where it sits in car space (view and polygon, or anchor) for the lints; raw SVG has none.
    `waivers` holds (check, reason) for each output-lint check `waive` turned off for it. `snaps` lists the
    edge moves the library made to keep it clear of the near-tangent lint (`geom.snapped`), for the report.
    """

    id: str
    kind: str
    svg: str
    material: Material = Material.gloss
    z: int = 0
    tags: tuple[str, ...] = ()
    car: dict[str, Any] = field(default_factory=dict, compare=False)
    waivers: tuple[tuple[str, str], ...] = ()
    snaps: tuple[dict[str, Any], ...] = field(default=(), compare=False)


@dataclass
class Livery:
    base: str
    base_material: Material = Material.gloss
    elements: list[Element] = field(default_factory=list)

    def add(self, *items: Element | Iterable[Element]) -> "Livery":
        """Add elements; primitives that draw on several views return lists, which are flattened."""
        for item in items:
            if isinstance(item, Element):
                self.elements.append(item)
            else:
                self.elements.extend(item)
        return self
