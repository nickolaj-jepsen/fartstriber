"""Sponsor brands, placed by `primitives.sponsor` and `primitives.sponsor_group`.

A brand is a folder: one-colour SVG variants (wordmark, lockup, mark), a brand.toml with what drawing
measured, and the draw.py that made them. The roster lives in assets/sponsors (indexed by roster.md); a
design's own themed brands live in designs/<name>/sponsors and are looked up first. Builds read only the
SVGs, approved or not: a final review and install refuse a build that places a roster variant the owner
hasn't approved (`placed`). Drawing helpers here are for draw.py and never run in a build.
"""

import contextlib
import contextvars
import io
import re
import time
import tomllib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import resvg_py
import shapely
import shapely.affinity
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage
from shapely.geometry.base import BaseGeometry

from livery import carspace
from livery.config import Workspace
from livery.errors import LiveryError
from livery.model import Element

VARIANTS = ("lockup", "wordmark", "mark")  # a placement's default is the first one the brand has
MEASURE_PX = 300  # variants are measured rendered this tall
# An opening that removes a piece this thick (px, or this share of its radius) has removed a stroke or
# counter, not just rounded a corner (a corner's sliver is about 0.2 of the radius thick).
SOLID_MIN_PX, SOLID_SHARE = 1.5, 0.4
SILL_PX = 40  # how tall the review sheet shows a variant at sill size

_design: contextvars.ContextVar[Path | None] = contextvars.ContextVar("design", default=None)
_roster: contextvars.ContextVar[Path | None] = contextvars.ContextVar("roster", default=None)


class SponsorError(LiveryError, ValueError):
    pass


@contextlib.contextmanager
def designing(design_dir: Path | None, roster: Path | None = None) -> Iterator[None]:
    """While a design builds, its own sponsors folder is searched before the roster: `roster`, else the one
    already in use."""
    tokens = _design.set(design_dir), _roster.set(roster or _roster.get())
    try:
        yield
    finally:
        _design.reset(tokens[0])
        _roster.reset(tokens[1])


def roster_dir() -> Path:
    """The roster in use: the building design's workspace's, else the checkout's."""
    return _roster.get() or Workspace().sponsors


@dataclass(frozen=True)
class Variant:
    path: Path
    aspect: float  # width over height
    stroke: float  # thinnest stroke, as a share of the height
    counter: float  # narrowest gap inside or between letters, as a share of the height
    ink: float  # inked share of the box
    approved: str | None

    def min_height_px(self, min_stroke_px: float) -> float:
        """The smallest height, in texture px, at which its strokes and counters stay above the lint's
        minimum stroke."""
        return min_stroke_px / min(self.stroke, self.counter)


@dataclass(frozen=True)
class Brand:
    id: str
    dir: Path
    source: str  # fictional, owned (Fireproof) or supplied (a real logo the owner gives us)
    large_only: bool  # placed only at headline sizes (roof, bonnet, doors), never in a sill row
    local: bool  # one of a design's own
    variants: dict[str, Variant]
    review: str = "--sponsors"  # the review flag that approves it

    def min_height_px(self, v: Variant, min_stroke_px: float, large_min_px: float) -> float:
        """The smallest height, in texture px, a variant may be placed at: where its strokes and counters
        hold, and for a large-only brand at least the large minimum."""
        return max(v.min_height_px(min_stroke_px), large_min_px if self.large_only else 0.0)

    def variant(self, name: str | None = None) -> Variant:
        """A variant, approved or not; by default the first of VARIANTS the brand has."""
        name = name or next((v for v in VARIANTS if v in self.variants), next(iter(self.variants), "mark"))
        if name not in self.variants:
            raise SponsorError(f"{self.id} has no {name}; it has {', '.join(self.variants)}")
        return self.variants[name]


def find(brand: str, root: Path | None = None) -> Path:
    root = root or roster_dir()
    design = _design.get()
    for d in ([design / "sponsors" / brand] if design else []) + [root / brand]:
        if (d / "brand.toml").exists():
            return d
    raise SponsorError(f"no sponsor {brand!r} in the design's sponsors folder or the roster ({root})")


def load(brand: str, root: Path | None = None) -> Brand:
    root = root or roster_dir()
    d = find(brand, root)
    return read(d, local=d.parent != root)


def read(d: Path, local: bool = False, review: str = "--sponsors") -> Brand:
    """The brand (or race marking) in a folder, as its brand.toml describes it."""
    raw = tomllib.loads((d / "brand.toml").read_text(encoding="utf-8"))
    return Brand(
        id=d.name,
        dir=d,
        source=raw.get("source", "fictional"),
        large_only=raw.get("large_only", False),
        local=local,
        variants={
            name: Variant(
                d / f"{name}.svg", v["aspect"], v["stroke"], v["counter"], v["ink"], v.get("approved")
            )
            for name, v in raw.get("variants", {}).items()
        },
        review=review,
    )


# --- the roster index ----------------------------------------------------------------------------------

ROSTER_COLUMNS = ("brand", "name", "category", "voice", "mark", "colours", "checked", "status")


def roster_rows(path: Path | None = None) -> list[dict]:
    """roster.md's table, one dict per brand."""
    path = path or roster_dir() / "roster.md"
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == len(ROSTER_COLUMNS) and cells[0] not in ("Brand", "") and not set(cells[0]) <= {"-"}:
            rows.append(dict(zip(ROSTER_COLUMNS, cells, strict=True)))
    return rows


def roster(path: Path | None = None) -> tuple[dict, ...]:
    """The brands a design can place: drawn, and not turned down in roster.md (the owner judges a row with its
    drawing, in `livery review --sponsors`). Each row also lists its variants, so a design can pick a mix of
    categories, and those not approved yet, which place and build but keep a final review from pinning the
    build until the owner approves them."""
    path = path or roster_dir() / "roster.md"
    out = []
    for row in roster_rows(path):
        if row["status"] == "no" or not (path.parent / row["brand"] / "brand.toml").exists():
            continue
        brand = load(row["brand"], path.parent)
        pending = tuple(n for n, v in brand.variants.items() if not v.approved)
        out.append({**row, "variants": tuple(brand.variants), "unapproved": pending})
    return tuple(out)


def placed(elements: Iterable[Element]) -> dict[str, list[str]]:
    """The roster brands' variants and the race markings elements place, each as "<name>/<variant>": what
    a final review and install need approved. A design's own brands need no approval and aren't listed."""
    found: dict[str, set[str]] = {"sponsors": set(), "marks": set()}
    for e in elements:
        car = e.car or {}
        if car.get("brand") and not car.get("own"):
            found["sponsors"].add(f"{car['brand']}/{car['variant']}")
        elif car.get("mark"):
            found["marks"].add(f"{car['mark']}/{car['variant']}")
    return {kind: sorted(names) for kind, names in found.items()}


# --- drawing (draw.py only) ----------------------------------------------------------------------------


def svg_doc(body: str, bounds: tuple[float, float, float, float], attrs: str = "") -> str:
    """An SVG file around `body`, its viewBox tight on `bounds`."""
    x0, y0, x1, y1 = bounds
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0:.2f} {y0:.2f} {x1 - x0:.2f} {y1 - y0:.2f}"'
        f"{' ' + attrs if attrs else ''}>{body}</svg>\n"
    )


def path_svg(d: str, bounds: tuple[float, float, float, float]) -> str:
    """One black path as an SVG file."""
    return svg_doc(f'<path fill="#000000" d="{d}"/>', bounds)


def shape_svg(shape: BaseGeometry) -> str:
    """A shapely shape, y up (as `livery.typeset` sets text), as a one-path black SVG file."""
    flipped = shapely.affinity.scale(shape, 1.0, -1.0, origin=(0, 0))
    body = f'<path fill="#000000" fill-rule="evenodd" d="{carspace.path_d(flipped)}"/>'
    return svg_doc(body, flipped.bounds)


def _viewbox(svg: str) -> tuple[float, float, float, float]:
    m = re.search(r'viewBox="([^"]+)"', svg)
    x, y, w, h = (float(v) for v in re.split(r"[\s,]+", m[1].strip()))
    return x, y, w, h


def _inner(svg: str) -> tuple[str, str]:
    """An SVG file's body and its shape-rendering attribute, if any."""
    body = re.sub(r"^.*?<svg[^>]*>|</svg>\s*$", "", svg.strip(), flags=re.S)
    m = re.search(r'<svg[^>]*\sshape-rendering="([^"]+)"', svg)
    return body, f' shape-rendering="{m[1]}"' if m else ""


def lockup_svg(mark: str, word: str, gap: float = 0.35, mark_scale: float = 1.0) -> str:
    """The mark left of the wordmark, both as given (never redrawn). The mark is `mark_scale` times the
    wordmark's height, the two centred on one line, `gap` wordmark heights apart."""
    mx, my, mw, mh = _viewbox(mark)
    wx, wy, ww, wh = _viewbox(word)
    k = mark_scale * wh / mh
    height = max(wh, mh * k)
    mark_body, mark_attr = _inner(mark)
    word_body, word_attr = _inner(word)
    m_dx, w_dx = -mx * k, mw * k + gap * wh - wx
    m_dy, w_dy = (height - mh * k) / 2 - my * k, (height - wh) / 2 - wy
    body = (
        f'<g transform="translate({m_dx:.3f},{m_dy:.3f}) scale({k:.5f})"{mark_attr}>{mark_body}</g>'
        f'<g transform="translate({w_dx:.3f},{w_dy:.3f})"{word_attr}>{word_body}</g>'
    )
    return svg_doc(body, (0, 0, mw * k + gap * wh + ww, height))


def render(svg: str, height: int, color: str | None = None) -> np.ndarray:
    """An SVG file rendered `height` px tall, as RGBA; `color` recolours it as `logo(color=)` does."""
    if color:
        svg = re.sub(r'\b(fill|stroke)="(?!none)[^"]*"', lambda g: f'{g.group(1)}="{color}"', svg)
    _, _, w, h = _viewbox(svg)
    png = resvg_py.svg_to_bytes(svg_string=svg, width=max(1, round(height * w / h)), height=height)
    return np.asarray(Image.open(io.BytesIO(bytes(png))).convert("RGBA"))


def _narrowest(mask: np.ndarray) -> int:
    """Half the width of the narrowest feature of `mask`, in px: the largest radius whose opening removes
    no solid piece of it. An opening also rounds off corners, but those slivers stay thin."""
    inside = ndimage.distance_transform_edt(mask)

    def keeps(r: int) -> bool:
        lost = mask & ~(ndimage.distance_transform_edt(~(inside > r)) <= r)
        return not lost.any() or ndimage.distance_transform_edt(lost).max() < max(
            SOLID_MIN_PX, SOLID_SHARE * r
        )

    # The first radius that removes something, found growing by half each step, then narrowed down. A
    # larger radius can pass again (its sliver allowance grows), so this is not a search over all radii.
    lo, hi, top = 0, 1, int(inside.max()) + 1
    while hi < top and keeps(hi):
        lo, hi = hi, max(hi + 1, int(hi * 1.5))
    hi = min(hi, top)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if keeps(mid) else (lo, mid)
    return lo


def measure(svg: str) -> dict[str, float]:
    """Aspect, thinnest stroke, narrowest counter and ink share of a variant, rendered MEASURE_PX tall."""
    ink = render(svg, MEASURE_PX)[:, :, 3] > 127
    if not ink.any():
        raise SponsorError("the variant draws nothing")
    # Counters are the narrow parts of the space between strokes; padding keeps the space round the
    # variant wide, so only gaps inside and between letters can close up.
    gaps = np.pad(~ink, MEASURE_PX // 2, constant_values=True)
    _, _, w, h = _viewbox(svg)
    return {
        "aspect": round(w / h, 4),
        "stroke": round(2 * _narrowest(ink) / MEASURE_PX, 4),
        "counter": round(2 * _narrowest(gaps) / MEASURE_PX, 4),
        "ink": round(float(ink.mean()), 4),
    }


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return f'"{v}"' if isinstance(v, str) else repr(v)


def _write_brand(path: Path, raw: dict) -> None:
    lines = [f"{k} = {_toml_value(v)}" for k, v in raw.items() if k != "variants"]
    for name, entry in raw.get("variants", {}).items():
        lines += ["", f"[variants.{name}]", *(f"{k} = {_toml_value(v)}" for k, v in entry.items())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def save(
    brand_dir: Path,
    variants: dict[str, str],
    source: str = "fictional",
    large_only: bool = False,
    names: tuple[str, ...] = VARIANTS,
) -> Path:
    """Writes a brand's variant SVGs and its brand.toml. An unchanged file keeps its approval; a redrawn
    one goes back to review. `names` are the variants this kind of folder has (a race marking's differ)."""
    unknown = set(variants) - set(names)
    if unknown or not variants or (names == VARIANTS and not variants.keys() & {"wordmark", "mark"}):
        raise SponsorError(
            f"variants are {', '.join(names)}, with a wordmark or a mark; got {list(variants)}"
        )
    path = brand_dir / "brand.toml"
    old = tomllib.loads(path.read_text(encoding="utf-8")).get("variants", {}) if path.exists() else {}
    raw: dict = {"source": source, "large_only": large_only, "variants": {}}
    brand_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        file = brand_dir / f"{name}.svg"
        if name not in variants:
            file.unlink(missing_ok=True)
            continue
        same = file.exists() and file.read_bytes() == variants[name].encode("utf-8")
        file.write_bytes(variants[name].encode("utf-8"))  # as given: a supplied mark stays byte for byte
        entry: dict = measure(variants[name])
        if same and old.get(name, {}).get("approved"):
            entry["approved"] = old[name]["approved"]
        raw["variants"][name] = entry
    _write_brand(path, raw)
    return path


def approve(brand_dir: Path, date: str | None = None) -> list[str]:
    """Marks every variant not yet approved as approved today; returns their names."""
    path = brand_dir / "brand.toml"
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    done = []
    for name, entry in raw.get("variants", {}).items():
        if not entry.get("approved"):
            entry["approved"] = date or time.strftime("%Y-%m-%d")
            done.append(name)
    _write_brand(path, raw)
    return done


def approvals(brand_dir: Path) -> dict[str, str | None]:
    """Each variant's approval date, None where it has none."""
    raw = tomllib.loads((brand_dir / "brand.toml").read_text(encoding="utf-8"))
    return {name: entry.get("approved") for name, entry in raw.get("variants", {}).items()}


def set_approvals(brand_dir: Path, dates: dict[str, str | None]) -> None:
    """Sets the named variants' approval dates as given (None: not approved), as dropping a brand and undoing
    the drop do."""
    path = brand_dir / "brand.toml"
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    for name, entry in raw.get("variants", {}).items():
        if name in dates:
            entry.pop("approved", None)
            if dates[name]:
                entry["approved"] = dates[name]
    _write_brand(path, raw)


def sheet(brand: Brand, out: Path, min_stroke_px: float, fonts: Path | None = None) -> Path:
    """The review sheet: each variant black on white and white on black, large and at sill size, with its
    minimum height."""
    font = ImageFont.truetype(str((fonts or Workspace().fonts) / "barlow" / "Barlow-Bold.ttf"), 18)
    big, pad = 120, 24
    rows = []
    for name, v in brand.variants.items():
        svg = v.path.read_text(encoding="utf-8")
        tiles = [
            (render(svg, h, ink), bg)
            for h in (big, SILL_PX)
            for ink, bg in (("#000000", (255, 255, 255)), ("#ffffff", (20, 20, 20)))
        ]
        label = (
            f"{name}   aspect {v.aspect:.2f}   stroke {v.stroke:.3f}   counter {v.counter:.3f}   "
            f"min height {v.min_height_px(min_stroke_px):.0f} px"
            + ("   approved " + v.approved if v.approved else "")
        )
        rows.append((label, tiles))
    width = max(sum(t.shape[1] + 2 * pad for t, _ in tiles) for _, tiles in rows) + pad
    height = sum(30 + big + 2 * pad for _ in rows)
    img = Image.new("RGB", (max(width, 900), height), (235, 235, 235))
    draw = ImageDraw.Draw(img)
    y = 0
    for label, tiles in rows:
        draw.text((pad, y + 6), label, fill=(30, 30, 30), font=font)
        x = pad
        for tile, bg in tiles:
            box = Image.new("RGB", (tile.shape[1] + 2 * pad, big + 2 * pad), bg)
            box.paste(Image.fromarray(tile), (pad, pad + (big - tile.shape[0]) // 2), Image.fromarray(tile))
            img.paste(box, (x, y + 30))
            x += box.width
        y += 30 + big + 2 * pad
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out
