"""Livery + kit -> layered SVG, and the matching spec-map SVG."""

import re
import xml.etree.ElementTree as ET

from livery.kit import Kit
from livery.model import Livery, Material

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)

# Painting inside these defines shape or coverage, not colour, so the spec pass leaves it alone.
_NOT_COLOUR = {f"{{{SVG_NS}}}clipPath", f"{{{SVG_NS}}}mask"}
_STYLE_PAINT = re.compile(r"\b(fill|stroke|stop-color)\s*:\s*([^;]+)")


def compile_svg(livery: Livery, kit: Kit) -> str:
    ids = [e.id for e in livery.elements]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if "base" in ids or dupes:
        raise ValueError(f"element ids must be unique and not 'base': {dupes or ['base']}")
    c = kit.canvas
    parts = [
        f'<svg xmlns="{SVG_NS}" xmlns:xlink="{XLINK_NS}" width="{c}" height="{c}" viewBox="0 0 {c} {c}">',
        f'<g data-id="base" data-kind="base" data-material="{Material(livery.base_material)}">'
        f'<rect width="{c}" height="{c}" fill="{livery.base}"/></g>',
    ]
    order = sorted(range(len(livery.elements)), key=lambda i: (livery.elements[i].z, i))
    for i in order:
        e = livery.elements[i]
        attrs = f'data-id="{e.id}" data-kind="{e.kind}" data-material="{Material(e.material)}"'
        if e.tags:
            attrs += f' data-tags="{" ".join(e.tags)}"'
        parts.append(f"<g {attrs}>{e.svg}</g>")
    parts.append("</svg>")
    return "\n".join(parts)


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _recolour(el: ET.Element, colour: str) -> None:
    if el.tag in _NOT_COLOUR:
        return
    for attr in ("fill", "stroke", "stop-color"):
        if attr in el.attrib and el.attrib[attr].strip() not in ("none", "transparent"):
            el.attrib[attr] = colour
    if "style" in el.attrib:

        def paint(m: re.Match) -> str:
            return m.group(0) if m.group(2).strip() in ("none", "transparent") else f"{m.group(1)}:{colour}"

        el.attrib["style"] = _STYLE_PAINT.sub(paint, el.attrib["style"])
    if el.tag == f"{{{SVG_NS}}}image":
        # An image (which the lint refuses) takes its element's material over its whole area.
        href = [k for k in el.attrib if k.endswith("href")]
        for k in href:
            del el.attrib[k]
        el.tag = f"{{{SVG_NS}}}rect"
        el.attrib["fill"] = colour
    for child in el:
        _recolour(child, colour)


def spec_svg(paint_svg: str, kit: Kit) -> str:
    """The paint SVG with every element painted in its material's spec colour."""
    root = ET.fromstring(paint_svg)
    for g in root:
        material = g.attrib.get("data-material")
        if material is None:
            continue
        colour = _hex(kit.spec[Material(material)])
        g.attrib["fill"] = colour  # children that inherit their fill
        _recolour(g, colour)
    return ET.tostring(root, encoding="unicode")
