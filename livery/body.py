"""A kit's body model: named parts and features, in `body.yaml`, written only by `livery kit label`.

A part is paint: whole islands, an island cut by a car-space polygon on one view (`cut`), or what is left
of its islands once the other parts' cuts are taken out (`rest`). A group names parts painted together;
`Kit.part` returns either, and a primitive that takes an island id takes a part's name. A feature is a
place on the body, car-space geometry on one view: a point, a line, a box, a polygon or several boxes.
Two kinds count for the output lint: `keep_clear`, something the sim draws over the paint (the door
light panel), and `occluder`, a part of the car that hides the paint under it from some views (the carbon
side blade). Each part and feature says how sure the labelling was; the owner checks the names in the kit
review, with `review.yaml`'s open questions and the map problems left for the next fit.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import shapely
import yaml
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry

from livery import carspace
from livery import kit as kits
from livery.kit import Kit, KitError, view_kind

FILE = "body.yaml"
SIDES = ("left", "right", "centre", "both", "unknown")
CONFIDENCE = ("sure", "likely", "unsure")
KINDS = {
    "keep_clear": "the sim draws it over the paint",
    "occluder": "a part of the car that hides the paint under it from some views",
    "opening": "a hole or vent, no paint",
    "unseen": "paint the square-on view never sees",
    "zone": "a template keep-clear box, in car space",
    "line": "a body line: an arch, a shut line, a crease, the window's edge",
    "place": "a spot or area on the body to anchor to",
}
SHAPES = ("point", "line", "box", "polygon", "boxes")
NAME = re.compile(r"[a-z][a-z0-9_]*")
ISLAND = re.compile(r"i\d{3}")
# Two parts' cuts on one island may share an edge, not texture: more than this (px²) is an overlap.
OVERLAP_PX2 = 50.0
# A feature's samples count as on the view's paint within this (texture px) of a mapped island.
ON_PAINT_PX = 3.0
SAMPLE_STEP = 0.005  # car units between a line's samples
GRID = 15  # samples across a box or polygon


@dataclass(frozen=True)
class Cut:
    """A car-space polygon on one view that cuts a part out of `islands`; the part's other islands are
    whole."""

    view: str
    polygon: tuple[tuple[float, float], ...]
    islands: tuple[str, ...]


@dataclass(frozen=True)
class Part:
    """Paint on the body, by name. A group's `members` are the parts it paints together; its islands are
    theirs."""

    name: str
    islands: tuple[str, ...]
    side: str = "unknown"
    confidence: str = "unsure"
    note: str = ""
    evidence: str = ""
    cut: Cut | None = None
    rest: bool = False  # what is left of its islands once the other parts' cuts on them are taken out
    members: tuple[str, ...] = ()

    @property
    def group(self) -> bool:
        return bool(self.members)


@dataclass(frozen=True)
class Feature:
    """A place on the body: car-space geometry on one view (u, v on a side; u, w on top; w, v on a face)."""

    name: str
    view: str
    kind: str
    shape: str  # point, line, box, polygon or boxes
    coords: tuple
    label: str = ""  # how a lint message names it: "the sim's door light panel"
    confidence: str = "sure"
    note: str = ""
    evidence: str = ""

    def geometry(self) -> BaseGeometry:
        """Car-space shapely geometry: a Point, LineString, Polygon or MultiPolygon."""
        c = self.coords
        if self.shape == "point":
            return Point(c)
        if self.shape == "line":
            return LineString(c)
        if self.shape == "box":
            return shapely.box(*c)
        if self.shape == "polygon":
            return Polygon(c)
        return MultiPolygon([shapely.box(*b) for b in c])

    @property
    def at(self) -> tuple[float, float]:
        """Where something placed on it is centred: the point, a line's middle along it, an area's
        centre (its largest box's, for several)."""
        g = self.geometry()
        if self.shape == "line":
            p = g.interpolate(0.5, normalized=True)
        elif self.shape == "boxes":
            p = max(getattr(g, "geoms", [g]), key=lambda b: b.area).centroid
        else:
            p = g.centroid
        return (round(float(p.x), 6), round(float(p.y), 6))

    @property
    def box(self) -> tuple[float, float, float, float]:
        """Its bounds in its view's two coordinates: (u0, v0, u1, v1) on a side."""
        x0, y0, x1, y1 = self.geometry().bounds
        return (float(x0), float(y0), float(x1), float(y1))

    @property
    def title(self) -> str:
        return self.label or self.name.replace("_", " ")


@dataclass(frozen=True)
class Body:
    parts: dict[str, Part]  # parts and groups, by name
    features: dict[str, Feature]
    aliases: dict[str, str] = field(default_factory=dict)  # a feature's other name -> its name
    unnamed: dict[str, str] = field(default_factory=dict)  # an island no part names -> what it is


EMPTY = Body({}, {})


# --- reading -------------------------------------------------------------------------------------------


def _tuple(v: Any) -> Any:
    """JSON or YAML numbers as nested tuples of floats."""
    return tuple(_tuple(x) for x in v) if isinstance(v, list | tuple) else float(v)


def _cut(raw: dict | None, islands: tuple[str, ...]) -> Cut | None:
    if not raw:
        return None
    pts = tuple((float(u), float(a)) for u, a in raw["polygon"])
    return Cut(str(raw["view"]), pts, tuple(raw.get("islands") or islands))


def _as_cut(raw: dict, island: str) -> Cut:
    return Cut(str(raw["view"]), tuple((float(u), float(a)) for u, a in raw["polygon"]), (island,))


def _part(name: str, raw: dict) -> Part:
    islands = tuple(raw.get("islands", ()))
    return Part(
        name=name,
        islands=islands,
        side=raw.get("side", "unknown"),
        confidence=raw.get("confidence", "unsure"),
        note=raw.get("note", ""),
        evidence=raw.get("evidence", ""),
        cut=_cut(raw.get("cut"), islands),
        rest=bool(raw.get("rest", False)),
    )


def _least_sure(parts: Iterable[Part]) -> str:
    return max((p.confidence for p in parts), key=CONFIDENCE.index, default="unsure")


def _group(name: str, members: list[str], parts: dict[str, Part]) -> Part:
    mine = [parts[m] for m in members]
    sides = {p.side for p in mine}
    return Part(
        name=name,
        islands=tuple(dict.fromkeys(i for p in mine for i in p.islands)),
        side=sides.pop() if len(sides) == 1 else "both",
        confidence=_least_sure(mine),
        members=tuple(members),
    )


def _feature(name: str, raw: dict) -> Feature:
    shape = next(s for s in SHAPES if s in raw)
    return Feature(
        name=name,
        view=str(raw["view"]),
        kind=raw["kind"],
        shape=shape,
        coords=_tuple(raw[shape]),
        label=raw.get("label", ""),
        confidence=raw.get("confidence", "sure"),
        note=raw.get("note", ""),
        evidence=raw.get("evidence", ""),
    )


def parse(raw: dict) -> Body:
    """A body from body.yaml's sections (or `kit label`'s input once it is valid)."""
    parts = {n: _part(n, p) for n, p in (raw.get("parts") or {}).items()}
    groups = {n: _group(n, list(m), parts) for n, m in (raw.get("groups") or {}).items()}
    features, aliases = {}, {}
    for n, f in (raw.get("features") or {}).items():
        if "same_as" in f:
            aliases[n] = f["same_as"]
        else:
            features[n] = _feature(n, f)
    return Body(parts | groups, features, aliases, dict(raw.get("unnamed") or {}))


@lru_cache(maxsize=8)
def _read(path: Path, stamp: int) -> Body:
    raw = yaml.load(path.read_text(encoding="utf-8"), Loader=kits.LOADER) or {}
    return parse(raw)


def read(kit_dir: Path) -> Body:
    """The kit's body model; empty for a kit not yet labelled."""
    p = kit_dir / FILE
    return _read(p, p.stat().st_mtime_ns) if p.exists() else EMPTY


def _unknown(what: str, name: str, have: Iterable[str]) -> KitError:
    have = sorted(have)
    close = [h for h in have if name in h or h in name][:6]
    hint = f"; did you mean {', '.join(close)}?" if close else (f"; it has {len(have)}" if have else "")
    return KitError(f"the kit has no {what} {name!r}{hint}")


def part(kit: Kit, name: str) -> Part:
    body = read(kit.dir)
    if name not in body.parts:
        raise _unknown("part or group", name, body.parts)
    return body.parts[name]


def feature(kit: Kit, name: str) -> Feature:
    body = read(kit.dir)
    name = body.aliases.get(name, name)
    if name not in body.features:
        raise _unknown("feature", name, [*body.features, *body.aliases])
    return body.features[name]


def island_parts(kit_dir: Path) -> dict[str, list[str]]:
    """Each island a part names, with the parts (not groups) that name it."""
    out: dict[str, list[str]] = {}
    for p in read(kit_dir).parts.values():
        if not p.group:
            for i in p.islands:
                out.setdefault(i, []).append(p.name)
    return out


# --- texture areas -------------------------------------------------------------------------------------


def _stamp(kit: Kit) -> tuple[int, ...]:
    files = (kit.dir / FILE, kit.dir / kits.META, kit.dir / kits.MAPS, kit.dir / "regions.svg")
    return tuple(f.stat().st_mtime_ns if f.exists() else 0 for f in files)


def _cut_area(kit: Kit, cut: Cut, island: str) -> BaseGeometry:
    return carspace.island_shapes(kit)[island].intersection(carspace.project(kit, cut.view, cut.polygon))


_areas: dict[tuple, BaseGeometry] = {}


def _area(kit: Kit, stamp: tuple[int, ...], name: str) -> BaseGeometry:
    key = (kit.dir, stamp, name)
    if key not in _areas:
        if len(_areas) >= 1024:
            _areas.clear()
        _areas[key] = _part_area(kit, stamp, name)
    return _areas[key]


def _part_area(kit: Kit, stamp: tuple[int, ...], name: str) -> BaseGeometry:
    body = read(kit.dir)
    p = body.parts[name]
    if p.group:
        return shapely.union_all([_area(kit, stamp, m) for m in p.members])
    shapes = carspace.island_shapes(kit)
    pieces = []
    for island in p.islands:
        if p.cut is not None and island in p.cut.islands:
            pieces.append(_cut_area(kit, p.cut, island))
        elif p.rest:
            taken = [
                _cut_area(kit, o.cut, island)
                for o in body.parts.values()
                if o.cut is not None and o.name != name and island in o.cut.islands
            ]
            pieces.append(shapes[island].difference(shapely.union_all(taken)) if taken else shapes[island])
        else:
            pieces.append(shapes[island])
    return shapely.union_all(pieces) if pieces else Polygon()


def area(kit: Kit, name: str) -> BaseGeometry:
    """A part's or group's texture area, in template pixels."""
    part(kit, name)
    return _area(kit, _stamp(kit), name)


def region(kit: Kit, name: str) -> BaseGeometry:
    """An island id, a part or group, or a view (every island mapped in it), as texture geometry."""
    shapes = carspace.island_shapes(kit)
    if name in shapes:
        return shapes[name]
    if name in read(kit.dir).parts:
        return area(kit, name)
    if name in kit.views:
        return shapely.union_all([pc.area for pc in carspace.pieces(kit, name)])
    if ISLAND.fullmatch(name):
        raise KitError(f"the kit has no island {name}")
    raise _unknown("part, group, island or view", name, [*read(kit.dir).parts, *kit.views])


def regions(kit: Kit, names: str | Iterable[str]) -> BaseGeometry:
    """One or several regions (`region`) as one texture geometry."""
    names = [names] if isinstance(names, str) else list(names)
    return shapely.union_all([region(kit, n) for n in names])


def footprint(kit: Kit, f: Feature) -> BaseGeometry:
    """A feature's area in the texture, on every island of its view it lands on (a point or line: none)."""
    if f.shape in ("point", "line"):
        return Polygon()
    g = f.geometry()
    parts = [carspace.project(kit, f.view, list(p.exterior.coords[:-1])) for p in getattr(g, "geoms", [g])]
    return shapely.union_all(parts)


def zones(kit: Kit, kind: str) -> list[tuple[Feature, BaseGeometry]]:
    """The features of one kind with their texture footprints, those that land on paint."""
    out = []
    for f in read(kit.dir).features.values():
        if f.kind == kind and f.view in kit.views:
            foot = footprint(kit, f)
            if not foot.is_empty:
                out.append((f, foot))
    return out


def notes(kit: Kit) -> str:
    """What the sim draws over this car and what hides its paint, where: the critic pack's kit.md."""
    rows = []
    for kind, head in (("keep_clear", "The sim draws over the paint"), ("occluder", "Hides the paint")):
        found = [f for f in read(kit.dir).features.values() if f.kind == kind]
        if found:
            rows.append(f"## {head}\n")
            for f in found:
                u0, a0, u1, a1 = f.box
                first, second = {"top": ("u", "w"), "face": ("w", "v")}.get(view_kind(f.view), ("u", "v"))
                rows.append(
                    f"- {f.title} ({f.name}), {f.view} {first} {u0:.3f}-{u1:.3f}, {second} {a0:.3f}-{a1:.3f}: "
                    f"{f.note}"
                )
            rows.append("")
    return f"# {kit.name}: the car under the paint\n\n" + "\n".join(rows) if rows else ""


# --- labelling -----------------------------------------------------------------------------------------


def samples(f: Feature) -> list[tuple[float, float]]:
    """Car-space points over a feature: the point, along a line, or a grid over an area."""
    g = f.geometry()
    if f.shape == "point":
        return [f.at]
    if f.shape == "line":
        n = max(2, int(g.length / SAMPLE_STEP) + 1)
        return [(p.x, p.y) for p in (g.interpolate(t, normalized=True) for t in np.linspace(0, 1, n))]
    out = []
    for poly in getattr(g, "geoms", [g]):
        x0, y0, x1, y1 = poly.bounds
        for i in range(GRID):
            for j in range(GRID):
                p = Point(x0 + (x1 - x0) * (i + 0.5) / GRID, y0 + (y1 - y0) * (j + 0.5) / GRID)
                if poly.contains(p):
                    out.append((p.x, p.y))
    return out


def _on_paint(kit: Kit, view: str, at: tuple[float, float]) -> bool:
    p = np.asarray([at], float)
    for pc in carspace.pieces(kit, view):
        if pc.covers_car(at) and pc.area.distance(Point(pc.to_px(p)[0])) <= ON_PAINT_PX:
            return True
    return False


def mapped_share(kit: Kit, f: Feature) -> float:
    """How much of a feature lands on its view's mapped paint, by samples over it."""
    pts = samples(f)
    return sum(_on_paint(kit, f.view, p) for p in pts) / len(pts) if pts else 0.0


def _check_coords(name: str, shape: str, c) -> str | None:
    try:
        c = _tuple(c)
    except (TypeError, ValueError):
        return f"feature {name}: its {shape} isn't numbers"
    pairs = [c] if shape == "point" else list(c)
    if shape in ("box", "boxes"):
        boxes = [c] if shape == "box" else list(c)
        if not boxes or any(len(b) != 4 or not (b[0] < b[2] and b[1] < b[3]) for b in boxes):
            return f"feature {name}: a box is [u0, v0, u1, v1] with u0 < u1 and v0 < v1"
        return None
    if any(not isinstance(p, tuple) or len(p) != 2 for p in pairs):
        return f"feature {name}: a {shape} is (u, v) points"
    least = {"point": 1, "line": 2, "polygon": 3}[shape]
    if len(pairs) < least:
        return f"feature {name}: a {shape} needs {least} points or more"
    if shape == "polygon" and not Polygon(pairs).is_valid:
        return f"feature {name}: its polygon crosses itself"
    return None


def _check_parts(kit: Kit, raw: dict, problems: list[str], warnings: list[str]) -> None:
    ids = set(carspace.island_shapes(kit))
    parts = raw.get("parts") or {}
    for name, p in parts.items():
        if not NAME.fullmatch(name):
            problems.append(f"part {name!r}: names are lower case letters, digits and _")
        islands = p.get("islands") or []
        if not islands:
            problems.append(f"part {name}: names no island")
        problems += [f"part {name}: the kit has no island {i}" for i in islands if i not in ids]
        if p.get("side", "unknown") not in SIDES:
            problems.append(f"part {name}: side is one of {', '.join(SIDES)}")
        if p.get("confidence", "unsure") not in CONFIDENCE:
            problems.append(f"part {name}: confidence is one of {', '.join(CONFIDENCE)}")
        cut = p.get("cut")
        if cut and p.get("rest"):
            problems.append(f"part {name}: a part is cut or the rest of its islands, not both")
        if cut:
            if cut.get("view") not in kit.views:
                problems.append(f"part {name}: its cut's view {cut.get('view')!r} isn't one of {kit.views}")
                continue
            pts = cut.get("polygon") or []
            if len(pts) < 3 or not Polygon(pts).is_valid:
                problems.append(
                    f"part {name}: its cut needs a polygon of 3 points or more that doesn't cross"
                )
                continue
            for i in cut.get("islands") or islands:
                if i not in islands:
                    problems.append(f"part {name}: its cut names {i}, which isn't one of its islands")
                elif i in ids and _cut_area(kit, _as_cut(cut, i), i).area < 1.0:
                    problems.append(f"part {name}: its cut on {cut['view']} takes nothing of {i}")
    for name, p in parts.items():
        if not p.get("rest"):
            continue
        for i in p.get("islands") or []:
            if not any(
                o.get("cut") and i in (o["cut"].get("islands") or o.get("islands", []))
                for n, o in parts.items()
                if n != name
            ):
                problems.append(f"part {name}: the rest of {i}, but no other part cuts {i}")
    # Two cuts on one island may share an edge, not paint; two whole parts on one island paint it twice.
    by_island: dict[str, list[str]] = {}
    for name, p in parts.items():
        for i in p.get("islands") or []:
            by_island.setdefault(i, []).append(name)
    for i, names in sorted(by_island.items()):
        if len(names) < 2 or i not in ids:
            continue
        cuts = [n for n in names if parts[n].get("cut") and i in (parts[n]["cut"].get("islands") or [i])]
        whole = [n for n in names if n not in cuts and not parts[n].get("rest")]
        if len(whole) > 1:
            warnings.append(f"{i} is whole in {', '.join(whole)}")
        for a in range(len(cuts)):
            for b in range(a + 1, len(cuts)):
                ca, cb = _as_cut(parts[cuts[a]]["cut"], i), _as_cut(parts[cuts[b]]["cut"], i)
                if ca.view not in kit.views or cb.view not in kit.views:
                    continue
                shared = _cut_area(kit, ca, i).intersection(_cut_area(kit, cb, i)).area
                if shared > OVERLAP_PX2:
                    problems.append(f"the cuts of {cuts[a]} and {cuts[b]} overlap on {i} ({shared:.0f} px²)")
    for name, members in (raw.get("groups") or {}).items():
        if not NAME.fullmatch(name):
            problems.append(f"group {name!r}: names are lower case letters, digits and _")
        if name in parts:
            problems.append(f"group {name}: a part has that name")
        if not members:
            problems.append(f"group {name}: has no parts")
        problems += [f"group {name}: no part {m}" for m in members if m not in parts]
    for i in raw.get("unnamed") or {}:
        if i not in ids:
            problems.append(f"unnamed: the kit has no island {i}")
        elif i in by_island:
            warnings.append(f"{i} is under unnamed but the parts {', '.join(by_island[i])} name it")


def _check_features(kit: Kit, raw: dict, problems: list[str], shares: dict[str, float]) -> None:
    features = raw.get("features") or {}
    for name, f in features.items():
        if not NAME.fullmatch(name):
            problems.append(f"feature {name!r}: names are lower case letters, digits and _")
        if "same_as" in f:
            target = features.get(f["same_as"])
            if target is None or "same_as" in target:
                problems.append(f"feature {name}: same_as {f['same_as']!r} is no feature of its own")
            continue
        if f.get("view") not in kit.views:
            problems.append(f"feature {name}: its view {f.get('view')!r} isn't one of {kit.views}")
            continue
        if f.get("kind") not in KINDS:
            problems.append(f"feature {name}: kind is one of {', '.join(KINDS)}")
        if f.get("confidence", "sure") not in CONFIDENCE:
            problems.append(f"feature {name}: confidence is one of {', '.join(CONFIDENCE)}")
        shapes = [s for s in SHAPES if s in f]
        if len(shapes) != 1:
            problems.append(f"feature {name}: give one of {', '.join(SHAPES)}")
            continue
        if bad := _check_coords(name, shapes[0], f[shapes[0]]):
            problems.append(bad)
            continue
        share = mapped_share(kit, _feature(name, f))
        shares[name] = round(share, 3)
        if share == 0:
            problems.append(f"feature {name}: nothing of it lands on the paint {f['view']} maps")


def _clean(raw: dict, keys: tuple[str, ...]) -> dict:
    return {k: raw[k] for k in keys if raw.get(k) not in (None, "", [], {}, False)}


PART_KEYS = ("islands", "side", "confidence", "cut", "rest", "note", "evidence")
FEATURE_KEYS = ("same_as", "view", "kind", *SHAPES, "label", "confidence", "note", "evidence")

HEAD = """\
# The kit's body model, written by `livery kit label` (never by hand); docs/architecture.md (Kits) says
# what each part and feature is for. Parts: whole islands, an island cut by a car-space polygon on one
# view (cut), or the rest of their islands once the other parts' cuts are out (rest). Groups paint parts
# together. Features: car-space geometry on one view; keep_clear and occluder count for the output lint.
"""


def _dump(data: dict, flow: bool | None = None) -> str:
    """YAML as the kit's other files are written: island lists and points on one line (`flow=None`)."""
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=flow, width=110, allow_unicode=True)


def label(kit: Kit, parts_in: dict, features_in: dict) -> dict:
    """Validates labelled parts and features against the kit and writes body.yaml and review.yaml. A
    changed body clears the kit's approval: the labels go to the owner's kit review."""
    problems: list[str] = []
    warnings: list[str] = []
    shares: dict[str, float] = {}
    _check_parts(kit, parts_in, problems, warnings)
    _check_features(kit, features_in, problems, shares)
    if problems:
        raise KitError(f"{kit.name}: the labels don't fit the kit:\n- " + "\n- ".join(problems))
    body = {
        "parts": {n: _clean(p, PART_KEYS) for n, p in (parts_in.get("parts") or {}).items()},
        "groups": {n: list(m) for n, m in (parts_in.get("groups") or {}).items()},
        "features": {n: _clean(f, FEATURE_KEYS) for n, f in (features_in.get("features") or {}).items()},
        "unnamed": dict(parts_in.get("unnamed") or {}),
    }
    text = HEAD + _dump({k: v for k, v in body.items() if v})
    path = kit.dir / FILE
    changed = not path.exists() or path.read_text(encoding="utf-8") != text
    if changed:
        path.write_text(text, encoding="utf-8", newline="\n")
        (kit.dir / kits.APPROVAL).unlink(missing_ok=True)  # new labels need the owner's review again
    questions = [*(parts_in.get("questions") or []), *(features_in.get("questions") or [])]
    review = {"questions": questions, "fit_problems": list(parts_in.get("fit_problems") or [])}
    (kit.dir / kits.REVIEW).write_text(
        _dump({k: v for k, v in review.items() if v}, flow=False), "utf-8", newline="\n"
    )
    parsed = parse(body)
    named = {i for p in parsed.parts.values() for i in p.islands}
    ids = set(carspace.island_shapes(kit))
    return {
        "kit": kit.name,
        "body": str(path),
        "changed": changed,
        "approval": "cleared" if changed else "unchanged",
        "parts": sum(not p.group for p in parsed.parts.values()),
        "groups": sum(p.group for p in parsed.parts.values()),
        "features": len(parsed.features),
        "islands_named": len(named),
        "islands_unnamed": len(ids - named),
        "unlabelled": sorted(ids - named - set(parsed.unnamed)),  # neither named nor said what they are
        "unsure": unsure(parsed),
        "mapped_share": {n: s for n, s in shares.items() if s < 1.0},
        "warnings": warnings,
    }


def unsure(body: Body) -> list[str]:
    """The parts and features the labelling wasn't sure of, as `name (what)`, for the kit review."""
    out = [
        f"{p.name} ({', '.join(p.islands)}): {p.note or p.evidence.split('. ')[0]}".rstrip(": ")
        for p in body.parts.values()
        if not p.group and p.confidence == "unsure"
    ]
    out += [
        f"{f.name} ({f.view}): {f.note}".rstrip(": ")
        for f in body.features.values()
        if f.confidence == "unsure"
    ]
    return out


def review_notes(kit_dir: Path) -> dict:
    """review.yaml: the open questions and the map problems for the next fit."""
    p = kit_dir / kits.REVIEW
    return (yaml.load(p.read_text(encoding="utf-8"), Loader=kits.LOADER) or {}) if p.exists() else {}
