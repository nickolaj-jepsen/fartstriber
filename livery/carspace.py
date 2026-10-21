"""Car space -> template pixels, through the kit's fitted transforms.

Side views: (u, v), u nose 0 -> tail 1, v sill 0 -> top of the side 1. Top view: (u, w), w left -1 -> right 1.
A view is a set of pieces, each a map and the texture area it is valid on. A shape is mapped through every
piece and cut to its area, so it continues across seams; the cut is geometric, so the SVG carries only the
visible pieces and no clip paths.
"""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import Point, Polygon
from shapely.geometry.base import BaseGeometry

from livery import kit as kits
from livery.kit import Kit


@dataclass(frozen=True)
class Piece:
    island: str  # an island id, or "top-rest" for the top islands without a map of their own
    to_px: Callable[[np.ndarray], np.ndarray]  # car-space points (n, 2) -> texture pixels (n, 2)
    area: BaseGeometry  # where in the texture this map holds
    car_area: BaseGeometry | None = None  # where in car space it was measured; None: everywhere

    def covers_car(self, p) -> bool:
        return self.car_area is None or self.car_area.contains(Point(p[0], p[1]))


@lru_cache(maxsize=4)
def _load(kit_dir: Path, stamp: tuple[int, ...]) -> tuple[dict, dict[str, BaseGeometry]]:
    raw = kits.read_meta(kit_dir) | kits.read_maps(kit_dir)
    regions = kit_dir / "regions.svg"  # a kit not yet split into islands has none
    svg = regions.read_text(encoding="utf-8") if regions.exists() else ""
    shapes = {}
    for rid, d in re.findall(r'data-id="(i\d+)"[^>]*\sd="([^"]*)"', svg):
        rings = [
            Polygon(np.array(re.findall(r"(-?[\d.]+),(-?[\d.]+)", sub), float))
            for sub in d.split("Z")
            if sub.strip()
        ]
        # Simplified outlines of thin islands can self-intersect; repair them, never drop them (a lost
        # hole turns a ring-shaped island into a filled one).
        rings = [r if r.is_valid else r.buffer(0) for r in rings]
        rings = [r for r in rings if not r.is_empty]
        shape = rings[0]
        for r in rings[1:]:
            shape = shape.symmetric_difference(r)  # even-odd: holes cancel out
        shapes[rid] = shape
    return raw, shapes


def _mtime(f: Path) -> int:
    try:
        return f.stat().st_mtime_ns
    except OSError:
        return 0


def _stamp(kit: Kit) -> tuple[int, ...]:
    """When the kit's files last changed: kit.yaml, maps.json and regions.svg. Every projection asks, so
    it is one stat per file (a stat costs a file open on Windows)."""
    return tuple(_mtime(kit.dir / f) for f in (kits.META, kits.MAPS, "regions.svg"))


def _kit(kit: Kit) -> tuple[dict, dict[str, BaseGeometry]]:
    return _load(kit.dir, _stamp(kit))


def load_shapes(kit: Kit) -> tuple[dict, dict[str, BaseGeometry]]:
    """The kit's maps as read, and its island shapes."""
    return _kit(kit)


def kit_data(kit: Kit) -> dict:
    return _kit(kit)[0]


def island_shapes(kit: Kit) -> dict[str, BaseGeometry]:
    return _kit(kit)[1]


def densify(points, step: float = 0.01) -> np.ndarray:
    """Closed polygon with extra vertices, so straight car-space edges follow curved UV maps."""
    pts = np.asarray(points, float)
    out = []
    for a, b in zip(pts, np.roll(pts, -1, axis=0), strict=True):
        n = max(1, int(np.ceil(np.linalg.norm(b - a) / step)))
        out.extend(a + (b - a) * t for t in np.arange(n) / n)
    return np.asarray(out)


def _quad(coef: np.ndarray, p: np.ndarray) -> np.ndarray:
    u, v = p[:, 0], p[:, 1]
    return np.stack([np.ones_like(u), u, v, u * u, u * v, v * v], axis=1) @ coef.T


def grid_offset(grid: dict, p: np.ndarray) -> np.ndarray:
    """Bilinear lookup in a side map's residual grid (texture pixels), clamped at its edges."""
    d = np.asarray(grid["d"], float)
    (u0, u1), (v0, v1) = grid["u"], grid["v"]
    nu, nv = d.shape[:2]
    fu = np.clip((p[:, 0] - u0) / (u1 - u0) * (nu - 1), 0, nu - 1)
    fv = np.clip((p[:, 1] - v0) / (v1 - v0) * (nv - 1), 0, nv - 1)
    a, b = np.minimum(fu.astype(int), nu - 2), np.minimum(fv.astype(int), nv - 2)
    ta, tb = (fu - a)[:, None], (fv - b)[:, None]
    return (
        d[a, b] * (1 - ta) * (1 - tb)
        + d[a + 1, b] * ta * (1 - tb)
        + d[a, b + 1] * (1 - ta) * tb
        + d[a + 1, b + 1] * ta * tb
    )


def _array_grid(grid: dict) -> dict:
    """A residual grid with its offsets as one array, made once per map rather than on every lookup."""
    return grid | {"d": np.asarray(grid["d"], float)}


def _side_map(t: dict) -> Callable[[np.ndarray], np.ndarray]:
    coef, grid = np.asarray(t["coef"]), t.get("grid")
    if grid is None:
        return lambda p: _quad(coef, p)
    grid = _array_grid(grid)
    return lambda p: _quad(coef, p) + grid_offset(grid, p)


def region(d: list) -> BaseGeometry:
    """A map's measured area as `kit fit` writes it: a list of rings, or (from older fits) one ring."""
    if d and isinstance(d[0][0], list | tuple):
        return shapely.union_all([Polygon(r) for r in d])
    return Polygon(d)


def build_pieces(raw: dict, shapes: dict[str, BaseGeometry], view: str) -> tuple[Piece, ...]:
    """A view's pieces from a kit's maps (`maps.json` as read) and island shapes."""
    if view != "top":
        out = []
        for rid, t in sorted(raw.get("side_transforms", {}).items()):
            if t["view"] == view:
                area = shapes[rid].intersection(region(t["domain"]))
                out.append(Piece(rid, _side_map(t), area, region(t["car_domain"])))
        return tuple(out)
    if "top_transform" not in raw:  # a kit not yet fitted maps nothing
        return ()
    t = raw["top_transform"]

    def y_of(p: np.ndarray) -> np.ndarray:
        return t["axis_y"] + t["y_per_w"] * p[:, 1]

    own = t.get("islands", {})
    out = []
    for rid in sorted(own):
        coef, grid = np.asarray([own[rid]["coef"]]), own[rid].get("grid")
        grid = _array_grid(grid) if grid else grid

        def x_of(p: np.ndarray, c=coef, g=grid) -> np.ndarray:
            return _quad(c, p)[:, 0] + (grid_offset(g, p)[:, 0] if g else 0)

        out.append(
            Piece(
                rid,
                lambda p, x=x_of: np.stack([x(p), y_of(p)], 1),
                shapes[rid],
                shapely.box(own[rid]["u"][0], -10, own[rid]["u"][1], 10),
            )
        )
    # Top islands with a map of their own, and side islands that turn over into the top, for the part the
    # side view can't see. Each texture pixel has one owner: the side map keeps what it was measured on.
    sides = raw.get("side_transforms", {})
    for rid, st in sorted(t.get("sides", {}).items()):
        area = shapes[rid].intersection(region(st["domain"]))
        if rid in sides:
            area = area.difference(region(sides[rid]["domain"]))
        if not area.is_empty:
            out.append(Piece(rid, _side_map(st), area, region(st["car_domain"])))
    mapped = own.keys() | t.get("sides", {}).keys() | sides.keys()
    rest = shapely.union_all(
        [shapes[r["id"]] for r in raw["regions"] if r["view"] == "top" and r["id"] not in mapped]
    )
    if "rest_domain" in t:  # only where some view sees it
        rest = rest.intersection(region(t["rest_domain"]))
    out.append(Piece("top-rest", lambda p: np.stack([t["x0"] + t["x_per_u"] * p[:, 0], y_of(p)], 1), rest))
    return tuple(out)


@lru_cache(maxsize=16)
def _pieces(kit_dir: Path, stamp: tuple[int, ...], views: tuple[str, ...], view: str) -> tuple[Piece, ...]:
    raw, shapes = _load(kit_dir, stamp)
    if view not in views:
        raise ValueError(f"unknown view {view!r}; the kit's views are {views}")
    return build_pieces(raw, shapes, view)


def pieces(kit: Kit, view: str) -> tuple[Piece, ...]:
    return _pieces(kit.dir, _stamp(kit), kit.views, view)


def side_aspect(kit: Kit) -> float:
    """How long one unit of v is, in units of u (the side's height over the car's length)."""
    f = kit_data(kit)["side_frames"]["side_l"]
    return abs(f["sill_sy"] - f["top_sy"]) / abs(f["tail_sx"] - f["nose_sx"])


def aspect(kit: Kit, view: str) -> float:
    """How long one unit of v (sides) or w (top) is as seen, in units of u."""
    if view == "top":
        t = kit_data(kit)["top_transform"]
        return abs(t["y_per_w"]) / t["x_per_u"]
    return side_aspect(kit)


def px_per_unit(kit: Kit, view: str, at: tuple[float, float] | None = None) -> tuple[float, float]:
    """Texture px per unit of u and of v (w on top): the local map's at a car-space point, or on top
    without one, the top view's linear scale. A side has no single scale, so it needs a point."""
    if at is None:
        if view != "top":
            raise ValueError(f"{view} has no single scale: give a point")
        t = kit_data(kit)["top_transform"]
        return abs(t["x_per_u"]), abs(t["y_per_w"])
    _, _, jac = anchor(kit, view, at)
    return float(np.linalg.norm(jac[:, 0])), float(np.linalg.norm(jac[:, 1]))


def path_d(geom: BaseGeometry) -> str:
    parts = []
    for poly in getattr(geom, "geoms", [geom]):
        if not isinstance(poly, Polygon) or poly.is_empty:
            continue
        for ring in [poly.exterior, *poly.interiors]:
            parts.append("M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in ring.coords[:-1]) + " Z")
    return " ".join(parts)


def _cut(px: np.ndarray, area: BaseGeometry) -> BaseGeometry | None:
    shape = Polygon(px)
    if not shape.is_valid:  # a fit can fold far outside its island; keep what is valid
        shape = shapely.make_valid(shape)
    if not shape.intersects(area):
        return None
    return shape.intersection(area)


def project(kit: Kit, view: str, points, through: Iterable[Piece] | None = None) -> BaseGeometry:
    """A car-space polygon's footprint in the texture: one piece per island it lands on. `through` replaces
    the view's pieces, for a design that must leave out or stretch one."""
    poly = Polygon(points)
    parts = []
    for pc in pieces(kit, view) if through is None else through:
        seen = poly if pc.car_area is None else poly.intersection(pc.car_area)
        for part in getattr(seen, "geoms", [seen]):
            if isinstance(part, Polygon) and not part.is_empty:
                cut = _cut(pc.to_px(densify(part.exterior.coords[:-1])), pc.area)
                if cut is not None and not cut.is_empty:
                    parts.append(cut)
    return shapely.union_all(parts) if parts else Polygon()


def polygon(kit: Kit, view: str, points, fill: str) -> str:
    """SVG for a car-space polygon in one view."""
    d = path_d(project(kit, view, points))
    return f'<path fill-rule="evenodd" d="{d}" fill="{fill}"/>' if d else ""


EDGE_SLACK_PX = 4.0  # of a 2048 px template: a fit puts an island's edge on the car to within this


def anchor(kit: Kit, view: str, at: tuple[float, float]) -> tuple[Piece, np.ndarray, np.ndarray]:
    """The piece a car-space point lands on, its texture position, and the local slope (px per unit, 2x2).
    A point a few pixels past an island's edge (on a seam, where two maps leave a gap of a pixel or two, or
    where a refit moved the edge) takes the nearest island."""
    p = np.asarray([at], float)
    near: list[tuple[float, Piece, np.ndarray]] = []
    for pc in pieces(kit, view):
        if not pc.covers_car(at):
            continue
        xy = pc.to_px(p)[0]
        gap = pc.area.distance(Point(xy))
        if gap == 0:
            return pc, xy, _slope(pc, p)
        near.append((gap, pc, xy))
    gap, pc, xy = min(near, key=lambda n: n[0], default=(np.inf, None, None))
    if pc is None or xy is None or gap > EDGE_SLACK_PX * kit.canvas / 2048:
        raise ValueError(f"{view} point {at} doesn't land on any mapped island")
    return pc, xy, _slope(pc, p)


def _slope(pc: Piece, p: np.ndarray) -> np.ndarray:
    h = 1e-3
    return np.stack(
        [
            (pc.to_px(p + [[h, 0]])[0] - pc.to_px(p - [[h, 0]])[0]) / (2 * h),  # noqa: RUF005 - numpy
            (pc.to_px(p + [[0, h]])[0] - pc.to_px(p - [[0, h]])[0]) / (2 * h),  # noqa: RUF005 - numpy
        ],
        axis=1,
    )


def invert(pc: Piece, xy, start=(0.5, 0.5)) -> tuple[np.ndarray, np.ndarray]:
    """The car-space point a piece maps to texture pixel `xy`, and the slope there (Newton's method)."""
    car, target, h = np.asarray(start, float), np.asarray(xy, float), 1e-4
    for _ in range(30):
        f = pc.to_px(car[None])[0]
        jac = np.stack(
            [(pc.to_px((car + [h, 0])[None])[0] - f) / h, (pc.to_px((car + [0, h])[None])[0] - f) / h],  # noqa: RUF005 - numpy
            1,
        )
        step = np.linalg.solve(jac, target - f)
        car = car + step
        if np.abs(step).max() < 1e-6:
            break
    return car, jac


def to_car(kit: Kit, xy: tuple[float, float]) -> tuple[str, np.ndarray, np.ndarray] | None:
    """Inverse: the view, car-space point and slope for a texture pixel, if a mapped island covers it."""
    pt = Point(xy)
    for view in kit.views:
        for pc in pieces(kit, view):
            if pc.area.contains(pt):
                car, jac = invert(pc, xy, (0.5, 0.5 if view != "top" else 0.0))
                return view, car, jac
    return None
