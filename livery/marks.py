"""Race markings: the technical marks a raced car carries (tow arrows, the electrical cut-off triangle, the
extinguisher E, jack points, fuel and air markers), drawn once into assets/marks/<mark>/ with the sponsor
helpers and approved by the owner like a brand (`livery review --marks`). `primitives.marks` places one.

A marking folder is a brand folder: one-colour SVG variants, a brand.toml with what drawing measured, and
its draw.py. A marking that points somewhere (a tow arrow) has a `right` and a `left` variant, its arrow
pointing along the line it reads on and against it; its letters read the same in both. Any other has one
`mark`.
"""

import ast
from collections.abc import Sequence
from pathlib import Path

from livery import sponsors, typeset
from livery.config import CACHE, REPO
from livery.kit import Kit
from livery.model import Element, Material

MARKS = REPO / "assets" / "marks"
VARIANTS = ("mark", "right", "left")


def load(name: str, root: Path | None = None) -> sponsors.Brand:
    root = root or MARKS  # looked up when called, so tests can point it elsewhere
    d = root / name
    if not (d / "brand.toml").exists():
        have = sorted(p.parent.name for p in root.glob("*/brand.toml"))
        raise sponsors.SponsorError(f"no race marking {name!r} in {root}; there are {', '.join(have)}")
    return sponsors.read(d, review="--marks")


def save(mark_dir: Path, variants: dict[str, str]) -> Path:
    """Writes a marking's variants (`mark`, or `right` and `left`) and its measures, for its draw.py."""
    return sponsors.save(mark_dir, variants, source="marking", names=VARIANTS)


def variant_for(view: str, toward: str | None) -> str:
    """The variant that points `toward` the nose or the tail on a view: lines read nose to tail on the
    left side and tail to nose on the right."""
    if toward is None:
        return "mark"
    if toward not in ("nose", "tail"):
        raise sponsors.SponsorError(f"a marking points toward the nose or the tail, not {toward!r}")
    if view == "top":
        raise sponsors.SponsorError("on top a line reads across the car: point markings on the sides")
    reads = "tail" if view == "side_l" else "nose"
    return "right" if toward == reads else "left"


def review_items(
    root: Path | None = None, out: Path = CACHE / "marks", only: list[str] | None = None
) -> list[dict]:
    """Each drawn marking with a variant not yet approved, as a review sheet: what `livery review --marks`
    shows. With `only` (the Library's Review again), those markings as they stand."""
    from livery.lint.run import thresholds

    root = root or MARKS
    min_stroke = thresholds().min_stroke_px
    items = []
    for toml in sorted(root.glob("*/brand.toml")):
        if only and toml.parent.name not in only:
            continue
        mark = load(toml.parent.name, root)
        pending = [n for n, v in mark.variants.items() if not v.approved]
        if not pending and not only:
            continue
        sheet = sponsors.sheet(mark, out / mark.id / "sheet.png", min_stroke)
        draw = mark.dir / "draw.py"
        doc = ast.get_docstring(ast.parse(draw.read_text(encoding="utf-8"))) if draw.exists() else None
        about = (doc or mark.id).split("\n\n")[0]
        items.append(
            {
                "id": f"mark:{mark.id}",
                "kind": "mark",
                "title": f"Race marking: {mark.id}",
                "body": f"{about}\n\n"
                + (f"To approve: {', '.join(pending)}. " if pending else "Approved. ")
                + "No leaves it unapproved, to be redrawn.",
                "images": [_rel(sheet)],
            }
        )
    return items


def _rel(p: Path) -> str:
    p = p.resolve()
    return p.relative_to(REPO).as_posix() if p.is_relative_to(REPO) else p.as_posix()


def apply_review(decisions: Sequence[dict], root: Path | None = None) -> list[str]:
    """Writes the markings' part of a review: an accepted marking's variants are approved; a rejected one
    stays unapproved for its draw.py to be reworked."""
    root = root or MARKS
    written = []
    for d in decisions:
        kind, _, name = d.get("id", "").partition(":")
        if kind != "mark":
            continue
        if d.get("decision") == "accept":
            written.append(
                f"marks/{name}: approved {', '.join(sponsors.approve(root / name)) or 'nothing new'}"
            )
        elif d.get("decision") == "reject":
            written.append(f"marks/{name}: rejected, left unapproved; rework assets/marks/{name}/draw.py")
    return written


# --- the driver's name ---------------------------------------------------------------------------------

BANNER = typeset.Line("", 0.022, font="Barlow Condensed", weight=800, tracking=0.12)


def driver_banner(
    kit: Kit,
    id: str,
    view: str,
    name: str,
    at: tuple[float, float],
    color: str,
    plate: str | None = None,
    size: float = BANNER.size,
    lean: float = 0.0,
    material: Material = Material.gloss,
    z: int = 0,
) -> list[Element]:
    """The driver's name as the window banner sets it: one line of widely tracked condensed caps, centred
    on `at`, on a plate padded by a third of its size if `plate` is given."""
    line = typeset.Line(name.upper(), size, font=BANNER.font, weight=BANNER.weight, tracking=BANNER.tracking)
    return typeset.type_block(
        kit, id, view, [line], at, color, lean=lean, shear=0.0, anchor=(0.5, 0.5),
        plate=plate, pad=size / 3, material=material, z=z,
    )  # fmt: skip
