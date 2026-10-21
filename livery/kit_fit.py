"""`livery kit fit`: car-space -> texture maps from the kit's calibration capture (`livery kit capture`).

The capture holds, per preview view, the texel the sim draws at each car pixel, to a fraction of one
(`livery.graycode`). The fit needs five of them: each side square-on, whose screen axes are the car's; the
three-quarter view that sees each side's up-facing strips (front34 the left, rear34 the right); and the top,
5 degrees off vertical. It also reads, when the capture has them, the views straight ahead and behind (the
faces) and the left side from above (side_high). It runs in stages, each a function below:

1. Side frames: u nose 0 -> tail 1 along a side view's screen x, v sill 0 -> top of the side 1 up it.
2. Side maps: a quadratic per large island with a residual grid, fitted to its side's samples.
3. The three-quarter views carried into each side's (u, v), for the strips the sides see edge-on.
4. The top frame: u from the top view's screen, w (left -1 -> right 1) linear in texture y.
5. Small side islands and top strips: affine maps from side and carried samples.
6. Mirror: an island neither side mapped takes its partner's map reflected; of partners that disagree, the
   side that saw its island far more keeps its map, else the one that fits the other side better.
7. Top maps: (u, w) -> (x, y) for the top islands and the up-facing parts of side islands the sides miss,
   with side_high carried into (u, w) where the top shot missed a shoulder.
8. The top-rest model for top islands too little seen for a map of their own.
9. Faces: a homography from each face view's screen to (w, v), anchored on the cells it shares with the
   side panels (v) and the top shot (w) near its end; a face map (w, v) -> (x, y) for each island no side
   maps and the top maps badly or not at all.

The seams come last, over every view.

Each map holds only where it was measured: in the texture, where its samples lie, and in car space, where
their car coordinates lie, each grown by a margin. Thresholds are in texture px of a 2048 px template and
scale with the kit's canvas.
"""

import json
import shutil
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import shapely
import shapely.ops
from PIL import Image
from scipy import ndimage
from scipy.spatial import cKDTree

from livery import calibrate, carspace, graycode, state
from livery import kit as kits
from livery.carspace import grid_offset
from livery.config import Config, Workspace
from livery.errors import LiveryError
from livery.kit import Kit

REF_CANVAS = 2048
SIDES = {"side_l": ("lower", "front34"), "side_r": ("upper", "rear34")}  # view -> texture band, its 3/4 view
PLAN = "top"
VIEWS = (*SIDES, *(h for _, h in SIDES.values()), PLAN)
# Views a capture may also have, each read when it is there: the face views (car-space face -> the view
# that sees it straight on) and the left side from above, for the door shoulders' up-facing parts.
FACES = {"front": "front", "rear": "rear_chase"}
HIGH_SIDE = "side_high"
OPTIONAL = (*FACES.values(), HIGH_SIDE)
REPORT = kits.FIT_REPORT
# What a fit depends on in the capture's identity: not the spec presets or the sim's graphics options.
WATCHED = {
    "window": "the sim window's size (policy.toml)",
    "canvas": "the kit's canvas",
    "paint_folder": "the kit's paint folder",
    "frame": "the profile's frame",
    "views": "the profile's preview views",
    "replay": "the preview replay",
    "track_cameras": "the track camera set",
    "car_dat": "the car's model (.dat)",
    "scheme": "the Gray-code scheme",
}

MIN_SAMPLES = 150  # screen pixels
MIN_SMALL_SAMPLES = 40
MIN_MIRROR_IOU = 0.5
CELL_PX = 2  # texture cells two shots are matched on
SEEN_PX = 8  # texture cells a shot counts as having seen
# Texture px (areas in px^2) of a 2048 px template.
MAX_SMALL_AREA_PX = 5000
MAX_SMALL_P90_PX = 8.0
MAX_TOP_RMS = 6.5
MIN_PX_PER_UNIT = 100  # a small island's map spanning less in any direction can't be inverted reliably
MIN_LARGE_TOP_AREA = 20000
ROBUST_FLOOR_PX = 3.0  # misses a fit always keeps
GRID_MAX_RESIDUAL_PX = 25  # decode errors, left out of the grid
DOMAIN_MARGIN_PX = 8  # a map holds this far past the texels it was measured on
CAR_CELL = 0.005  # car-space units: the grid a map's measured car area is drawn on
CAR_MARGIN = 0.03
# Partner islands both sides mapped themselves should agree on a symmetric car. Past this median miss one
# side saw its island badly (a thin strip nearly edge-on): of the two maps, the one that, reflected, misses
# the other side's own samples by more is replaced by the other, reflected.
MAX_SIDES_OFF_PX = 6.0
SEEN_MORE = 2.0  # one side saw an island this many times more than the other: its map is the better one
# The template files some fender-top strips with the top view, where its model (w linear in texture y) puts
# them near the centreline. The top shot shows where they really are: a small top island whose w is further
# than this from where the large top islands put its screen position is mapped with the sides instead.
MAX_TOP_W_OFF = 0.2
# A three-quarter view's screen is carried into a side's (u, v) tile by tile, through a quadratic on the
# cells it shares with the side shot in and around the tile (screen pixels / 1000): over the whole car a
# quadratic can't follow a camera looking down at the car. A point further than HIGH_NEAR from any shared
# cell isn't carried.
HIGH_TILE = 0.04
HIGH_NEAR = 0.08
HIGH_MIN_CELLS = 30
MIN_QUADRATIC_SPREAD = 0.1  # car-space units; thinner islands get an affine map
CAR_FLOOR = 0.004  # car-space units (u, v, w): about the 6 texture px below which a car-space fit keeps all
GRID_CELL = 0.025  # car-space units
GRID_MIN_SAMPLES = 1000  # islands with fewer keep the plain quadratic
GRID_MIN_CELL_SAMPLES = 8
EDGE_PX = 8  # a shot whose car comes this close to the screen edge has lost part of it
# A face's frame is anchored on the side and top samples within this much u of its end: further from the
# face, a part sits at another depth, and so at another scale, in the face view's perspective.
FACE_END = 0.15
MIN_FACE_CELLS = 30  # of each kind of anchor
MAX_TOP_DROPPED = 0.2  # a top map that drops more of its samples than this is replaced by a face's


class FitError(LiveryError):
    """The capture can't be fitted as it is."""


# The template: islands, bands and mirror partners.


@dataclass(frozen=True)
class Template:
    canvas: int
    islands: np.ndarray  # (canvas, canvas) island index per texel, 0 off the islands
    names: np.ndarray  # island index -> id ("" for 0), object dtype: ids come out as Python strings
    band: dict[str, str]  # the template's band per island: "upper" (right side), "lower" (left), "top"
    area: dict[str, int]
    centre_y: dict[str, float]
    axis: float  # texture y the two sides mirror about
    partners: dict[str, str] = field(default_factory=dict)  # mirror partner, for islands that have one
    iou: dict[str, float] = field(default_factory=dict)

    @property
    def scale(self) -> float:
        return self.canvas / REF_CANVAS

    def px(self, x: float) -> float:
        return x * self.scale

    def area_px(self, a: float) -> float:
        return a * self.scale**2

    def partner(self, rid: str) -> str | None:
        return self.partners.get(rid)

    def ids(self, tx: np.ndarray, ty: np.ndarray) -> np.ndarray:
        x = np.clip(np.floor(tx).astype(int), 0, self.canvas - 1)
        y = np.clip(np.floor(ty).astype(int), 0, self.canvas - 1)
        return self.names[self.islands[y, x]]

    def cells(self, tx: np.ndarray, ty: np.ndarray, size: float = CELL_PX) -> np.ndarray:
        c = max(1, round(self.px(size)))
        n = -(-self.canvas // c)
        return np.floor(ty / c).astype(np.int64) * n + np.floor(tx / c).astype(np.int64)

    def grown(self, cells: np.ndarray, size: float = SEEN_PX) -> np.ndarray:
        """Texture cells (of `size`) with their eight neighbours."""
        n = -(-self.canvas // max(1, round(self.px(size))))
        return np.unique((cells[:, None] + [d * n + e for d in (-1, 0, 1) for e in (-1, 0, 1)]).ravel())

    def of_band(self, band: str) -> set[str]:
        return {r for r, b in self.band.items() if b == band}

    def x_centre(self, rid: str) -> float | None:
        """The texture x an island mirrors itself about, for one laid out across the car along texture x (a
        rear face): its middle, if it overlaps itself reflected there by MIN_MIRROR_IOU."""
        ys, xs = np.nonzero(self.islands == int(rid[1:]))
        if not len(xs):
            return None
        x0, x1, y0 = int(xs.min()), int(xs.max()), int(ys.min())
        mask = np.zeros((int(ys.max()) - y0 + 1, x1 - x0 + 1), bool)
        mask[ys - y0, xs - x0] = True
        both = (mask & mask[:, ::-1]).sum()
        return (x0 + x1 + 1) / 2 if both / (2 * mask.sum() - both) >= MIN_MIRROR_IOU else None


def template(kit: Kit) -> Template:
    raw = kits.read_meta(kit.dir) | kits.read_maps(kit.dir)
    islands = np.asarray(Image.open(kit.dir / "overlays" / "islands.png"))
    n = int(islands.max())
    names = np.array(["", *(f"i{i:03d}" for i in range(1, n + 1))], dtype=object)
    axis = float(raw["mirror_axis_y"])
    partners, iou = _partners(islands, axis, names)
    return Template(
        canvas=kit.canvas,
        islands=islands,
        names=names,
        band={r["id"]: r["view"] for r in raw["regions"]},
        area={r["id"]: r["area"] for r in raw["regions"]},
        centre_y={r["id"]: (r["bbox"][1] + r["bbox"][3]) / 2 for r in raw["regions"]},
        axis=axis,
        partners=partners,
        iou=iou,
    )


def _partners(islands: np.ndarray, axis: float, names: np.ndarray) -> tuple[dict, dict]:
    """Each island's mirror partner: the island that best overlaps it reflected about y = axis, if the two
    overlap by MIN_MIRROR_IOU; and every island's best overlap."""
    n = int(islands.max())
    h = islands.shape[0]
    rows = np.rint(2 * axis - np.arange(h)).astype(int)
    ok = (rows >= 0) & (rows < h)
    mirrored = np.zeros_like(islands)
    mirrored[ok] = islands[rows[ok]]
    a, b = islands.ravel().astype(np.int64), mirrored.ravel().astype(np.int64)
    both = (a > 0) & (b > 0)
    counts = np.bincount(a[both] * (n + 1) + b[both], minlength=(n + 1) ** 2).reshape(n + 1, n + 1)
    sizes = np.bincount(a, minlength=n + 1)
    partners, iou = {}, {}
    for i in range(1, n + 1):
        j = int(counts[i].argmax())
        inter = int(counts[i, j])
        if not inter:
            continue
        iou[names[i]] = inter / (sizes[i] + sizes[j] - inter)
        if iou[names[i]] >= MIN_MIRROR_IOU:
            partners[names[i]] = names[j]
    return partners, iou


# The capture's shots.


@dataclass(frozen=True)
class Shot:
    """One view's decoded car pixels that land on an island: screen position, the texel the sim draws
    there, its island and its texture cell."""

    view: str
    sx: np.ndarray
    sy: np.ndarray
    tx: np.ndarray
    ty: np.ndarray
    ids: np.ndarray
    key: np.ndarray
    shape: tuple[int, int]

    def __len__(self) -> int:
        return len(self.sx)

    def screen(self) -> np.ndarray:
        return np.stack([self.sx, self.sy], 1) / 1000  # keeps quadratic terms well scaled

    def tex(self, m: np.ndarray | slice = slice(None)) -> np.ndarray:
        return np.stack([self.tx[m], self.ty[m]], 1)


def shot(view: str, vm: graycode.ViewMap, tpl: Template) -> Shot:
    sy, sx = np.nonzero(vm.pix)  # row-major, as the ViewMap lists its pixels
    tx, ty = vm.uv[:, 0].astype(np.float64), vm.uv[:, 1].astype(np.float64)
    ok = np.isfinite(tx) & np.isfinite(ty) & vm.agree
    ids = tpl.ids(np.where(ok, tx, 0), np.where(ok, ty, 0))
    ok &= ids != ""
    return Shot(
        view,
        sx[ok].astype(np.float64),
        sy[ok].astype(np.float64),
        tx[ok],
        ty[ok],
        ids[ok],
        tpl.cells(tx[ok], ty[ok]),
        (int(vm.car.shape[0]), int(vm.car.shape[1])),
    )


def capture_shots(cfg: Config, ws: Workspace, kit: Kit, tpl: Template) -> tuple[dict[str, Shot], Path]:
    """The current capture's shots of the views the fit reads; refused once what they depend on moved."""
    run = calibrate.current(ws, kit.name)
    if run is None or not (run / "manifest.json").exists():
        raise FitError(f"{kit.name} has no calibration capture; run livery kit capture {kit.name}")
    shot_with = calibrate.shot_with(state.read_json(run / "manifest.json") or {})
    now = calibrate.identity(cfg, ws, kit, kits.profile(kit))
    moved = [what for key, what in WATCHED.items() if now.get(key) != shot_with.get(key)]
    if moved:
        raise FitError(
            f"{kit.name}'s capture {run.name} was shot before a change to {', '.join(moved)}; "
            f"run livery kit capture {kit.name} again"
        )
    missing = [v for v in VIEWS if not (run / v / "geometry.npz").exists()]
    if missing:
        raise FitError(f"{kit.name}'s capture has no {', '.join(missing)} view; the fit needs {VIEWS}")
    read = [*VIEWS, *(v for v in OPTIONAL if (run / v / "geometry.npz").exists())]
    return {v: shot(v, graycode.ViewMap.load(run / v / "geometry.npz"), tpl) for v in read}, run


# Fitting helpers.


def _design(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    return np.stack([np.ones_like(u), u, v, u * u, u * v, v * v], axis=1)


def _robust_fit(
    a: np.ndarray, b: np.ndarray, rounds: int = 3, floor: float = ROBUST_FLOOR_PX
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Least squares, dropping samples far off the fit (decode errors at panel edges). Misses under `floor`
    (in the target's units) are always kept. Returns the coefficients, every sample's miss and which samples
    were kept."""
    keep = np.ones(len(a), bool)
    for _ in range(rounds):
        coef, *_ = np.linalg.lstsq(a[keep], b[keep], rcond=None)
        err = np.linalg.norm(a @ coef - b, axis=1)
        keep = err <= max(3 * np.median(err[keep]), floor)
    return coef, err, keep


def _fit_map(
    a: np.ndarray, b: np.ndarray, target: np.ndarray, floor: float = ROBUST_FLOOR_PX
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Quadratic map from (a, b) to texture pixels, or affine when the island is thin in either direction:
    a quadratic fitted over a narrow range folds over itself just outside it. Always 6 coefficients."""
    if min(np.ptp(a), np.ptp(b)) >= MIN_QUADRATIC_SPREAD:
        return _robust_fit(_design(a, b), target, floor=floor)
    coef, err, keep = _robust_fit(_design(a, b)[:, :3], target, floor=floor)
    return np.vstack([coef, np.zeros((3, coef.shape[1]))]), err, keep


def _residual_grid(u: np.ndarray, v: np.ndarray, resid: np.ndarray) -> dict:
    """What the quadratic misses, on a coarse car-space grid: the median residual per cell, empty cells
    from their nearest neighbour, smoothed. A large island bends more than a quadratic follows, and the
    error piles up at its edges, where it meets the next island across a seam."""
    u0, u1, v0, v1 = float(u.min()), float(u.max()), float(v.min()), float(v.max())
    nu = max(2, int(np.ceil((u1 - u0) / GRID_CELL)) + 1)
    nv = max(2, int(np.ceil((v1 - v0) / GRID_CELL)) + 1)
    iu = np.clip(np.rint((u - u0) / (u1 - u0) * (nu - 1)).astype(int), 0, nu - 1)
    iv = np.clip(np.rint((v - v0) / (v1 - v0) * (nv - 1)).astype(int), 0, nv - 1)
    d = np.full((nu, nv, 2), np.nan)
    cell = iu * nv + iv
    for c in np.unique(cell):
        m = cell == c
        if m.sum() >= GRID_MIN_CELL_SAMPLES:
            d[c // nv, c % nv] = np.median(resid[m], axis=0)
    _, (ia, ib) = ndimage.distance_transform_edt(np.isnan(d[..., 0]), return_indices=True)
    d = d[ia, ib]
    d = np.stack([ndimage.gaussian_filter(d[..., k], 1.0, mode="nearest") for k in range(2)], -1)
    return {
        "u": [round(u0, 4), round(u1, 4)],
        "v": [round(v0, 4), round(v1, 4)],
        "d": np.round(d, 2).tolist(),
    }


def _rings(points: np.ndarray, cell: float, margin: float, digits: int) -> list[list[list[float]]]:
    """Where points lie: the cells of size `cell` they fall in, closed over small gaps and with holes filled,
    grown by `margin`, as rings."""
    if not len(points):
        return []
    grow = int(np.ceil(margin / cell)) + 2
    lo = np.floor(points.min(0) / cell) - grow
    idx = (np.floor(points / cell) - lo).astype(int)
    grid = np.zeros((idx[:, 1].max() + grow + 1, idx[:, 0].max() + grow + 1), bool)
    grid[idx[:, 1], idx[:, 0]] = True
    grid = np.asarray(ndimage.binary_fill_holes(ndimage.binary_closing(grid, iterations=2)), bool)
    grid = np.asarray(ndimage.distance_transform_edt(~grid)) <= margin / cell
    grid = np.asarray(ndimage.binary_fill_holes(grid), bool)
    boxes = []
    for r in np.nonzero(grid.any(axis=1))[0]:
        row = np.concatenate([[False], grid[r], [False]])
        starts, ends = (
            np.nonzero(np.diff(row.astype(np.int8)) == 1)[0],
            np.nonzero(np.diff(row.astype(np.int8)) == -1)[0],
        )
        boxes += [shapely.box(s, r, e, r + 1) for s, e in zip(starts, ends, strict=True)]
    shape = shapely.union_all(boxes).simplify(0.5)
    out = []
    for poly in getattr(shape, "geoms", [shape]):
        if isinstance(poly, shapely.Polygon) and not poly.is_empty:
            ring = (np.asarray(poly.exterior.coords[:-1]) + lo) * cell
            out.append([[round(float(x), digits), round(float(y), digits)] for x, y in ring])
    return out


def _domain(tpl: Template, tx: np.ndarray, ty: np.ndarray) -> list[list[list[float]]]:
    """Where in the texture a map was measured: its samples' texels, grown by a margin."""
    return _rings(np.stack([tx, ty], 1), max(1.0, tpl.px(4)), tpl.px(DOMAIN_MARGIN_PX), 1)


def _car_domain(car: np.ndarray) -> list[list[list[float]]]:
    """Where in car space a map was measured, grown by a margin a design can anchor in at an island's edge;
    it is not used outside. Paint lands only on the texels its map was measured on (`_domain`), so the margin
    doesn't paint what the sim shows of another island."""
    return _rings(car, CAR_CELL, CAR_MARGIN, 4)


def _cell_means(key: np.ndarray, vals: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    cells, inv = np.unique(key, return_inverse=True)
    n = np.bincount(inv)
    return cells, np.stack([np.bincount(inv, vals[:, j]) / n for j in range(vals.shape[1])], 1)


def _common_cells(ka, va, kb, vb) -> tuple[np.ndarray, np.ndarray]:
    """For each texture cell two shots both saw: the mean of each shot's values there."""
    ca, ma = _cell_means(ka, va)
    cb, mb = _cell_means(kb, vb)
    _, ia, ib = np.intersect1d(ca, cb, assume_unique=True, return_indices=True)
    return ma[ia], mb[ib]


def _in_frame(s: Shot, sel: np.ndarray | None = None) -> None:
    """Refuse a shot whose car comes up to the screen edge: part of it is cut off."""
    sx, sy = (s.sx, s.sy) if sel is None else (s.sx[sel], s.sy[sel])
    (x0, x1), (y0, y1) = np.percentile(sx, [0.05, 99.95]), np.percentile(sy, [0.05, 99.95])
    h, w = s.shape
    if min(x0, y0) < EDGE_PX or x1 > w - EDGE_PX or y1 > h - EDGE_PX:
        raise FitError(f"{s.view}: the car runs off the shot; widen the camera's FOV in the kit's profile")


def _jacobian(coef: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """A quadratic map's (6, 2) Jacobian at each point, (n, 2, 2): texture px per unit of u and of v."""
    c = coef.T
    du = c[:, 1, None] + 2 * c[:, 3, None] * u + c[:, 4, None] * v
    dv = c[:, 2, None] + c[:, 4, None] * u + 2 * c[:, 5, None] * v
    return np.stack([du.T, dv.T], axis=2)


def _folds(coef: np.ndarray, u: np.ndarray, v: np.ndarray) -> bool:
    """Whether a quadratic map turns over inside the samples: its Jacobian changes sign there."""
    det = np.linalg.det(_jacobian(coef, u, v))
    return min((det > 0).mean(), (det < 0).mean()) > 0.01


def _min_scale(coef: np.ndarray, u: np.ndarray, v: np.ndarray) -> float:
    """The fewest texture px a quadratic map moves per car-space unit, in any direction, at its samples."""
    return float(np.linalg.svd(_jacobian(coef, u, v), compute_uv=False).min())


def _map_px(t: dict, p: np.ndarray) -> np.ndarray:
    px = _design(p[:, 0], p[:, 1]) @ np.asarray(t["coef"]).T
    return px + grid_offset(t["grid"], p) if "grid" in t else px


@dataclass
class Samples:
    """What a map was fitted on: car-space points and the texels the sim showed there."""

    car: np.ndarray  # (n, 2)
    tex: np.ndarray  # (n, 2)

    @classmethod
    def of(cls, u, v, tx, ty) -> "Samples":
        return cls(np.stack([u, v], 1).astype(float), np.stack([tx, ty], 1).astype(float))

    @classmethod
    def empty(cls) -> "Samples":
        return cls(np.zeros((0, 2)), np.zeros((0, 2)))

    def __add__(self, o: "Samples") -> "Samples":
        return Samples(np.concatenate([self.car, o.car]), np.concatenate([self.tex, o.tex]))

    def __len__(self) -> int:
        return len(self.car)


def _large_map(tpl: Template, view: str, s: Samples) -> tuple[dict, dict]:
    """A large island's map with its residual grid, and its report."""
    floor = tpl.px(ROBUST_FLOOR_PX)
    u, v = s.car.T
    coef, _, keep = _fit_map(u, v, s.tex, floor)
    folded = _folds(coef, u, v)
    if folded:  # an affine map can't turn over
        coef, _, keep = _robust_fit(_design(u, v)[:, :3], s.tex, floor=floor)
        coef = np.vstack([coef, np.zeros((3, 2))])
    t: dict = {"view": view, "coef": np.round(coef.T, 4).tolist()}
    rep: dict = {"view": view, "samples": len(u)}
    if folded:
        rep["affine"] = "folded"
    resid = s.tex - _design(u, v) @ coef
    if len(u) >= GRID_MIN_SAMPLES:
        ok = np.linalg.norm(resid, axis=1) <= tpl.px(GRID_MAX_RESIDUAL_PX)
        t["grid"] = _residual_grid(u[ok], v[ok], resid[ok])
        resid = resid - grid_offset(t["grid"], s.car)
    miss = np.linalg.norm(resid, axis=1)
    # The rms counts only the samples the robust fit kept; a map that can't follow its island to the edge
    # drops the edge samples and still reports a low rms. p90 and dropped see every sample.
    rep["rms_px"] = round(float(np.sqrt((miss[keep] ** 2).mean())), 2)
    rep["p90_px"] = round(float(np.percentile(miss, 90)), 1)
    rep["dropped"] = round(1 - keep.mean(), 3)
    inside = miss <= max(floor, 3 * float(np.median(miss)))
    t["domain"] = _domain(tpl, *s.tex[inside].T)
    t["car_domain"] = _car_domain(s.car[inside])
    return t, rep


def _small_map(tpl: Template, view: str, s: Samples) -> tuple[dict, dict] | None:
    """An affine map for a small island, or None when it doesn't fit to MAX_SMALL_P90_PX."""
    u, v = s.car.T
    a = _design(u, v)[:, :3]
    coef, miss, keep = _robust_fit(a, s.tex, floor=tpl.px(ROBUST_FLOOR_PX))
    if np.percentile(miss, 90) > tpl.px(MAX_SMALL_P90_PX):
        return None
    if np.linalg.svd(coef[1:3], compute_uv=False).min() < tpl.px(MIN_PX_PER_UNIT):
        return None  # its samples lie along a line in car space or in the texture
    t = {
        "view": view,
        "coef": np.round(np.vstack([coef, np.zeros((3, 2))]).T, 4).tolist(),
        "domain": _domain(tpl, *s.tex[keep].T),
        "car_domain": _car_domain(s.car[keep]),
    }
    rep = {
        "view": view,
        "samples": len(u),
        "rms_px": round(float(np.sqrt((miss[keep] ** 2).mean())), 2),
        "p90_px": round(float(np.percentile(miss, 90)), 1),
        "dropped": round(1 - keep.mean(), 3),
        "affine": True,
    }
    return t, rep


def _mirrored(t: dict, rid: str, axis: float) -> dict:
    """A map reflected onto its island's partner, about the template's mirror axis: a side's onto the other
    side, a face's onto the partner across the car's centre line (which shows at w what it shows at -w)."""
    coef = np.asarray(t["coef"])
    face = kits.view_kind(t["view"]) == "face"
    if face:
        coef = coef * [1, -1, 1, 1, -1, 1]
    mirrored = np.vstack([coef[0], -coef[1]])
    mirrored[1, 0] += 2 * axis
    car_domain = t["car_domain"]
    if face:
        car_domain = [[[-a, b] for a, b in ring] for ring in _as_rings(car_domain)]
    out = {
        "view": t["view"] if face else "side_r" if t["view"] == "side_l" else "side_l",
        "coef": np.round(mirrored, 4).tolist(),
        "domain": [[[x, round(2 * axis - y, 1)] for x, y in ring] for ring in _as_rings(t["domain"])],
        "car_domain": car_domain,
        "mirror_of": rid,
    }
    if "grid" in t:  # reflected in y like the map
        g = t["grid"]
        d = np.asarray(g["d"]) * [1, -1]
        out["grid"] = {**g, "d": np.round(d, 2).tolist()}
        if face:
            (a0, a1) = g["u"]
            out["grid"] |= {"u": [-a1, -a0], "d": np.round(d[::-1], 2).tolist()}
    return out


def _as_rings(d: list) -> list:
    return d if d and isinstance(d[0][0], list | tuple) else [d]


def _sides_agree(transforms: dict, tpl: Template) -> dict:
    """For partner islands both sides mapped themselves: how far apart the two maps put the same (u, v),
    one reflected about the mirror axis. The sides are measured apart; on a symmetric car they agree."""
    out = {}
    for rid, t in sorted(transforms.items()):
        pid = tpl.partner(rid)
        o = transforms.get(pid) if pid else None
        if t["view"] != "side_l" or "mirror_of" in t or not o or "mirror_of" in o:
            continue
        both = carspace.region(t["car_domain"]).intersection(carspace.region(o["car_domain"]))
        if both.is_empty:
            continue
        u0, v0, u1, v1 = both.bounds
        grid = np.stack(np.meshgrid(np.linspace(u0, u1, 40), np.linspace(v0, v1, 40)), -1).reshape(-1, 2)
        grid = grid[shapely.contains_xy(both, grid[:, 0], grid[:, 1])]
        if len(grid) < 10:
            continue
        a, b = _map_px(t, grid), _map_px(o, grid)
        b[:, 1] = 2 * tpl.axis - b[:, 1]
        d = np.linalg.norm(a - b, axis=1)
        out[f"{rid}/{pid}"] = {
            "median_px": round(float(np.median(d)), 1),
            "p90_px": round(float(np.percentile(d, 90)), 1),
        }
    return out


# Stage 1: side frames.


def _extent(s: Shot, side: np.ndarray, view: str) -> dict:
    """A side view's frame from the extent of its side panels in the shot."""
    lo_x, hi_x = np.percentile(s.sx[side], [0.2, 99.8])
    lo_y, hi_y = np.percentile(s.sy[side], [0.2, 99.8])
    # Which end is the nose: on the left side the car faces left in its shot, on the right side right.
    nose_x, tail_x = (lo_x, hi_x) if view == "side_l" else (hi_x, lo_x)
    return {"nose_sx": float(nose_x), "tail_sx": float(tail_x), "sill_sy": float(hi_y), "top_sy": float(lo_y)}


def _uv(frame: dict, s: Shot) -> tuple[np.ndarray, np.ndarray]:
    u = (s.sx - frame["nose_sx"]) / (frame["tail_sx"] - frame["nose_sx"])
    v = (frame["sill_sy"] - s.sy) / (frame["sill_sy"] - frame["top_sy"])
    return u, v


def _matched(tpl: Template, ref: Shot, ref_side: np.ndarray, ref_frame: dict, s: Shot, side: np.ndarray):
    """A side's frame that puts each of its texels at the (u, v) where the other side shows the mirrored
    texel: least squares on the texture cells both sides saw, and how far the cells miss it (screen px)."""
    u, v = _uv(ref_frame, ref)
    mirrored = tpl.cells(ref.tx[ref_side], 2 * tpl.axis - ref.ty[ref_side])
    uv, xy = _common_cells(
        mirrored, np.stack([u[ref_side], v[ref_side]], 1), s.key[side], np.stack([s.sx[side], s.sy[side]], 1)
    )
    if len(uv) < MIN_SAMPLES:
        return None, {}
    one = np.ones(len(uv))
    cx, ex, _ = _robust_fit(np.stack([one, uv[:, 0]], 1), xy[:, :1], floor=1.0)
    cy, ey, _ = _robust_fit(np.stack([one, uv[:, 1]], 1), xy[:, 1:], floor=1.0)
    frame = {
        "nose_sx": float(cx[0, 0]),
        "tail_sx": float(cx[0, 0] + cx[1, 0]),
        "sill_sy": float(cy[0, 0]),
        "top_sy": float(cy[0, 0] + cy[1, 0]),
    }
    rep = {
        "cells": len(uv),
        "miss_p50_90_px": [round(float(x), 2) for x in np.percentile(np.hypot(ex, ey), [50, 90])],
    }
    return frame, rep


def side_frames(tpl: Template, shots: dict[str, Shot]) -> tuple[dict[str, dict], dict[str, np.ndarray], dict]:
    """Each side's frame, and which of its samples are on side panels (islands of its band with a mirror
    partner: the bands also hold parts like the wing and splitter that one camera sees and the other
    doesn't, which would skew the frame). The side with more panel samples is framed by their extent, the
    other by matching it texel for mirrored texel, so something hiding part of one side (a bank below the
    car) doesn't move its frame."""
    panels = {}
    for view, (band, _) in SIDES.items():
        s = shots[view]
        wanted = [r for r in tpl.of_band(band) if tpl.partner(r)]
        panels[view] = np.isin(s.ids, wanted)
        if panels[view].sum() < MIN_SAMPLES:
            raise FitError(f"{view}: only {panels[view].sum()} side samples decoded; shoot the capture again")
        _in_frame(s, panels[view])
    ref, other = sorted(SIDES, key=lambda v: -int(panels[v].sum()))
    frames = {ref: _extent(shots[ref], panels[ref], ref)}
    report: dict = {"reference": ref}
    matched, rep = _matched(tpl, shots[ref], panels[ref], frames[ref], shots[other], panels[other])
    if matched is None:
        frames[other] = _extent(shots[other], panels[other], other)
        report[other] = {"from": "extent"}
    else:
        frames[other] = matched
        report[other] = {"from": f"mirror of {ref}", **rep}
    return {v: {k: round(x, 2) for k, x in frames[v].items()} for v in SIDES}, panels, report


# Stages 2 and 3: side maps, and the three-quarter views carried into the sides.


def _carry(points: np.ndarray, cxy: np.ndarray, cuv: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(u, v) for three-quarter-view screen points, and which points could be carried."""
    out = np.full((len(points), cuv.shape[1]), np.nan)
    if len(cxy) < HIGH_MIN_CELLS or not len(points):
        return out, np.zeros(len(points), bool)
    tiles = np.floor(points / HIGH_TILE).astype(int)
    for t in np.unique(tiles, axis=0):
        m = (tiles == t).all(axis=1)
        lo, hi = t * HIGH_TILE - HIGH_NEAR, (t + 1) * HIGH_TILE + HIGH_NEAR
        near = ((cxy >= lo) & (cxy <= hi)).all(axis=1)
        if near.sum() >= HIGH_MIN_CELLS:
            coef, _, _ = _robust_fit(_design(*cxy[near].T), cuv[near], floor=CAR_FLOOR)
            out[m] = _design(*points[m].T) @ coef
    reach = cKDTree(cxy).query(points)[0] <= HIGH_NEAR
    return out, reach & ~np.isnan(out[:, 0])


@dataclass
class Side:
    """One side's shot in its own car space, and its three-quarter view carried into it."""

    view: str
    band: str
    shot: Shot
    u: np.ndarray
    v: np.ndarray
    panels: np.ndarray
    high: Shot
    huv: np.ndarray
    hok: np.ndarray

    def samples(self, m: np.ndarray) -> Samples:
        return Samples.of(self.u[m], self.v[m], self.shot.tx[m], self.shot.ty[m])

    def carried(self, m: np.ndarray) -> Samples:
        m = m & self.hok
        return Samples.of(self.huv[m, 0], self.huv[m, 1], self.high.tx[m], self.high.ty[m])


def sides(tpl: Template, shots: dict[str, Shot], frames: dict, panels: dict, report: dict) -> dict[str, Side]:
    out = {}
    for view, (band, high_view) in SIDES.items():
        s, h = shots[view], shots[high_view]
        u, v = _uv(frames[view], s)
        _in_frame(h)
        side = panels[view]
        cuv, cxy = _common_cells(s.key[side], np.stack([u[side], v[side]], 1), h.key, h.screen())
        report[high_view] = {"cells": len(cuv)}
        huv, hok = _carry(h.screen(), cxy, cuv)
        out[view] = Side(view, band, s, u, v, side, h, huv, hok)
    return out


def _mirror_fill(tpl: Template, rid: str, own: Side, other: Side) -> Samples:
    """The partner's samples reflected onto island `rid`, at texture cells its own side didn't see: what
    something hid from one side (a bank below the car), the other side shows."""
    pid = tpl.partner(rid)
    if pid is None:
        return Samples.empty()
    m = other.shot.ids == pid
    ty = 2 * tpl.axis - other.shot.ty[m]
    tx = other.shot.tx[m]
    on = tpl.ids(tx, ty) == rid
    mine = own.shot.ids == rid
    seen = np.unique(tpl.cells(own.shot.tx[mine], own.shot.ty[mine], SEEN_PX))
    new = on & ~np.isin(tpl.cells(tx, ty, SEEN_PX), seen)
    return Samples.of(other.u[m][new], other.v[m][new], tx[new], ty[new])


def _reflected(tpl: Template, rid: str, s: Samples, col: int = 1, about_x: float | None = None) -> Samples:
    """A centre island's samples reflected across the car's centre line (w, car-space column `col`, -> -w),
    at texture cells the island's own samples don't reach: a bank or the camera's tilt hides one half from
    the views that see the other. The texture mirrors about the template's axis, or for an island laid out
    along texture x, about x = `about_x`."""
    if about_x is None:
        tx, ty = s.tex[:, 0], 2 * tpl.axis - s.tex[:, 1]
    else:
        tx, ty = 2 * about_x - s.tex[:, 0], s.tex[:, 1]
    seen = np.unique(tpl.cells(s.tex[:, 0], s.tex[:, 1], SEEN_PX))
    new = (tpl.ids(tx, ty) == rid) & ~np.isin(tpl.cells(tx, ty, SEEN_PX), seen)
    car = s.car[new].copy()
    car[:, col] *= -1
    return Samples(car, np.stack([tx[new], ty[new]], 1))


def side_maps(tpl: Template, sd: dict[str, Side]) -> tuple[dict, dict, dict]:
    """Every side panel a side shot sees enough of gets a large map, from its side's samples and, where the
    shot didn't see it, its partner's reflected."""
    transforms, fitted, report = {}, {}, {}
    for view, side in sd.items():
        other = next(s for v, s in sd.items() if v != view)
        s = side.shot
        for rid in sorted(set(s.ids[side.panels])):
            m = s.ids == rid
            if m.sum() < MIN_SAMPLES:
                continue
            own = side.samples(m)
            fill = _mirror_fill(tpl, rid, side, other)
            transforms[rid], report[rid] = _large_map(tpl, view, own + fill)
            fitted[rid] = own
            if len(fill):
                report[rid]["mirror_filled"] = len(fill)
    return transforms, fitted, report


# Stage 4: the top frame.


@dataclass
class Top:
    shot: Shot
    u: np.ndarray  # every sample's u, from its screen position
    w: np.ndarray  # w as the texture's top model puts each texel (only meaningful on top islands)
    w_seen: np.ndarray  # w where the large top islands put each sample's screen position
    y_per_w: float


def top_frame(tpl: Template, shots: dict[str, Shot], sd: dict[str, Side], report: dict) -> Top:
    """u from the top shot's screen, an affine map fitted on the texture cells it shares with the side
    panels, where a shape crosses from a side onto the top (parts further from a side camera than its panels,
    like the roof, sit at another scale in its perspective); w linear in texture y about the mirror axis, the
    top islands spanning -1 to 1."""
    p = shots[PLAN]
    _in_frame(p)
    seen_u, seen_xy = [], []
    for side in sd.values():
        m = side.panels
        cu, cxy = _common_cells(side.shot.key[m], side.u[m, None], p.key, p.screen())
        seen_u.append(cu)
        seen_xy.append(cxy)
    cu, cxy = np.concatenate(seen_u), np.concatenate(seen_xy)
    if len(cu) < MIN_SAMPLES:
        raise FitError(f"only {len(cu)} texture cells seen by both the top and side shots")
    a = np.column_stack([np.ones(len(cxy)), cxy])
    ucoef, uerr, ukeep = _robust_fit(a, cu, floor=CAR_FLOOR)
    pu = np.column_stack([np.ones(len(p)), p.screen()]) @ ucoef[:, 0]
    report["top"] = {
        "u_cells": len(cu),
        "u_rms": round(float(np.sqrt((uerr[ukeep] ** 2).mean())), 4),
        "u_p90": round(float(np.percentile(uerr, 90)), 4),
        "u_dropped": round(1 - ukeep.mean(), 3),
    }
    top_ids = [int(r[1:]) for r in tpl.of_band("top")]
    rows = np.nonzero(np.isin(tpl.islands, top_ids).any(axis=1))[0]
    half = float(max(abs(rows.min() - tpl.axis), abs(rows.max() - tpl.axis)))
    y_per_w = -half  # left (w = -1) is towards larger y: the left side's islands sit below the axis
    pw = (p.ty - tpl.axis) / y_per_w
    on_top = np.isin(p.ids, list(tpl.of_band("top")))
    large = on_top & np.isin(p.ids, [r for r in tpl.area if tpl.area[r] >= tpl.area_px(MIN_LARGE_TOP_AREA)])
    if large.sum() < MIN_SAMPLES:
        raise FitError(f"the top shot decoded only {large.sum()} samples on the large top islands")
    xy = p.screen()
    wcoef, _, _ = _robust_fit(_design(*xy[large].T), pw[large, None], floor=CAR_FLOOR)
    # The top islands' texture doesn't follow one smooth function of the tilted camera's screen where they
    # curve down (docs/sim.md, Fitting): there a shape crossing onto a side island's top part can jog.
    w_seen = _design(*xy.T) @ wcoef[:, 0]  # w where the large top islands put each place
    return Top(p, pu, pw, w_seen, y_per_w)


def top_strips(tpl: Template, top: Top, report: dict) -> set[str]:
    """Strips the template files with the top view whose w the top shot puts far from where the top model
    has them: fender tops, mapped with the sides."""
    p = top.shot
    w_off = np.abs(top.w - top.w_seen)
    strips = set()
    for rid in sorted(set(p.ids) & tpl.of_band("top")):
        m = p.ids == rid
        if tpl.area[rid] <= tpl.area_px(MAX_SMALL_AREA_PX) and m.sum() >= MIN_SMALL_SAMPLES:
            off = float(np.median(w_off[m]))
            report.setdefault(rid, {"view": "top"})["w_off"] = round(off, 3)
            if off > MAX_TOP_W_OFF:
                strips.add(rid)
    return strips


# Stage 5: small side islands and the strips.


def small_maps(
    tpl: Template, sd: dict[str, Side], strips: set[str], transforms: dict, fitted: dict, report: dict
):
    """Small islands on each side, and the strips: samples from the side shot and from its three-quarter
    view, carried into (u, v) through the cells both saw around the island."""
    small: dict[str, list] = {}
    for view, side in sd.items():
        s, h = side.shot, side.high
        for rid in sorted((set(s.ids) | set(h.ids)) - set(transforms)):
            paired = tpl.band[rid] == side.band and tpl.partner(rid)
            # A strip belongs to the side whose half of the texture it sits in: the left side's islands lie
            # below the mirror axis. From up high, the far side's fender top can show over the bonnet.
            near_side = (tpl.centre_y[rid] > tpl.axis) == (side.band == "lower")
            if tpl.area[rid] > tpl.area_px(MAX_SMALL_AREA_PX) or not (
                paired or (rid in strips and near_side)
            ):
                continue
            m, mh = s.ids == rid, (h.ids == rid) & side.hok
            if m.sum() + mh.sum() < MIN_SMALL_SAMPLES:
                continue
            samples = side.samples(m) + side.carried(h.ids == rid)
            got = _small_map(tpl, view, samples)
            if got:
                got[1].update(side_samples=int(m.sum()), high_samples=int(mh.sum()))
                small.setdefault(rid, []).append((*got, samples))
    for rid, fits in small.items():  # a strip both sides saw goes to the one that saw more of it
        t, rep, fitted[rid] = max(fits, key=lambda f: f[1]["samples"])
        transforms[rid] = t
        if tpl.band[rid] == "top":
            rep["moved_from"] = "top"
        report[rid] = {**report.get(rid, {}), **rep}


# Stage 6: mirror.


def mirror(tpl: Template, transforms: dict, fitted: dict, report: dict) -> None:
    """An island neither side mapped takes its mirror partner's map, reflected about the mirror axis: the
    template lays the sides out as mirror images. Of partners that disagree, the map whose reflection misses
    the other side's samples by more is replaced by the other's reflection."""
    for rid, t in list(transforms.items()):
        pid = tpl.partner(rid)
        if pid is None or pid in transforms:
            report[rid]["mirror"] = None
            continue
        transforms[pid] = _mirrored(t, rid, tpl.axis)
        report[rid]["mirror"] = {"island": pid, "iou": round(tpl.iou[rid], 3)}
    replaced: set[str] = set()
    for pair, d in _sides_agree(transforms, tpl).items():
        a, b = pair.split("/")
        if d["median_px"] <= tpl.px(MAX_SIDES_OFF_PX) or replaced & {a, b}:
            continue
        miss = {}
        for r, o in ((a, b), (b, a)):  # r's map, reflected, against the samples o's map was fitted on
            px = _map_px(_mirrored(transforms[r], r, tpl.axis), fitted[o].car)
            miss[r] = float(np.median(np.linalg.norm(px - fitted[o].tex, axis=1)))
        if min(len(fitted[a]), len(fitted[b])) < MIN_SMALL_SAMPLES:
            report[a]["disagrees_with"] = report[b]["disagrees_with"] = {"pair": pair, **d}
            continue
        keep, drop = sorted((a, b), key=lambda r: miss[r])
        # A side that saw its island far less (a bank, or nearly edge-on) was filled from the other side's
        # samples, which its map then fits: the side that saw more keeps its own.
        more, less = sorted((a, b), key=lambda r: -len(fitted[r]))
        if len(fitted[more]) >= SEEN_MORE * len(fitted[less]):
            keep, drop = more, less
        transforms[drop] = _mirrored(transforms[keep], keep, tpl.axis)
        replaced |= {a, b}
        report[drop] = {
            "view": transforms[drop]["view"],
            "replaced_by_mirror_of": {"island": keep, "miss_px": {r: round(x, 1) for r, x in miss.items()}},
        }
    report["sides_agree"] = _sides_agree(transforms, tpl)


# Stages 7 and 8: the top view.


@dataclass
class High:
    """The left side seen from above (`HIGH_SIDE`), carried into the top's (u, w)."""

    shot: Shot
    uw: np.ndarray
    ok: np.ndarray


def high_side(tpl: Template, top: Top, shots: dict[str, Shot], report: dict) -> High | None:
    """The raised side view carried into the top's (u, w) through the cells it shares with the top shot,
    as the three-quarter views are carried into the sides."""
    h = shots.get(HIGH_SIDE)
    if h is None:
        return None
    p = top.shot
    cuw, cxy = _common_cells(p.key, np.stack([top.u, top.w_seen], 1), h.key, h.screen())
    uw, ok = _carry(h.screen(), cxy, cuw)
    report[HIGH_SIDE] = {"cells": len(cuw), "carried": int(ok.sum())}
    return High(h, uw, ok)


def _high_fill(tpl: Template, rid: str, top: Top, high: High | None, out: np.ndarray | None) -> tuple:
    """Samples of island `rid` from the raised side view at texture cells the top shot didn't see on it,
    nor next to one it saw (a rim the top's pixels just miss is no part it can't see), and its partner's
    there, reflected (the raised view sees only the left side); none inside `out` (the side map's area).
    Returns both, as Samples."""
    if high is None:
        return Samples.empty(), Samples.empty()
    p, h = top.shot, high.shot
    mine = p.ids == rid
    seen = tpl.grown(np.unique(tpl.cells(p.tx[mine], p.ty[mine], SEEN_PX)))

    def fresh(tx: np.ndarray, ty: np.ndarray) -> np.ndarray:
        keep = ~np.isin(tpl.cells(tx, ty, SEEN_PX), seen)
        return keep & ~shapely.contains_xy(out, tx, ty) if out is not None else keep

    # It sees the far side's shoulder too, over the top, but edge-on: only the left half's islands (below
    # the mirror axis in the texture) take its own samples.
    m = (h.ids == rid) & high.ok & (tpl.centre_y[rid] > tpl.axis)
    m[m] = fresh(h.tx[m], h.ty[m])
    own = Samples.of(high.uw[m, 0], high.uw[m, 1], h.tx[m], h.ty[m])
    seen = np.union1d(seen, tpl.cells(h.tx[m], h.ty[m], SEEN_PX))
    pid = tpl.partner(rid)
    if pid is None or pid == rid:
        return own, Samples.empty()
    # The top camera's tilt sets the two sides' w a little apart (a shoulder sits lower than the top islands
    # its w comes from): a partner's place reflected is where the top shot puts the mirror image of its
    # texels, measured on the cells it saw of both, along the car.
    pm = p.ids == pid
    a, b = _common_cells(
        p.key[mine],
        np.stack([top.u[mine], top.w_seen[mine]], 1),
        tpl.cells(p.tx[pm], 2 * tpl.axis - p.ty[pm]),
        np.stack([top.u[pm], top.w_seen[pm]], 1),
    )
    if len(a) < HIGH_MIN_CELLS:
        return own, Samples.empty()
    line = np.column_stack([np.ones(len(b)), b[:, 0]])
    off, _, _ = _robust_fit(line, np.stack([a[:, 0] - b[:, 0], a[:, 1] + b[:, 1]], 1), floor=CAR_FLOOR)
    m = (h.ids == pid) & high.ok
    tx, ty = h.tx[m], 2 * tpl.axis - h.ty[m]
    on = (tpl.ids(tx, ty) == rid) & fresh(tx, ty)
    uw = high.uw[m][on]
    d = np.column_stack([np.ones(len(uw)), uw[:, 0]]) @ off
    return own, Samples.of(uw[:, 0] + d[:, 0], d[:, 1] - uw[:, 1], tx[on], ty[on])


def over_top(
    tpl: Template,
    top: Top,
    sd: dict[str, Side],
    transforms: dict,
    report: dict,
    evidence: dict,
    high: High | None = None,
) -> dict:
    """Side panels that turn over into the top (fender and haunch tops, door shoulders) face the top camera,
    not the side one, and so do centre parts the template files with the sides (the bonnet duct ramp). The
    part of such an island the side shots missed gets a top-view map (u, w) -> (x, y) of its own, with w
    where the large top islands put that place in the top shot, and where the top shot missed it too, from
    the raised side view (`high_side`)."""
    p = top.shot
    out = {}
    candidates = (set(transforms) | {r for r in set(p.ids) if tpl.band[r] != "top"}) & set(p.ids)
    for rid in sorted(candidates):
        m = p.ids == rid
        side_area = None
        if rid in transforms:  # what the side map holds stays the side's
            side_area = carspace.region(transforms[rid]["domain"])
            m &= ~shapely.contains_xy(side_area, p.tx, p.ty)
        raised, mirrored = _high_fill(tpl, rid, top, high, side_area)
        if m.sum() + len(raised) + len(mirrored) < MIN_SAMPLES:
            continue
        samples = Samples.of(top.u[m], top.w_seen[m], p.tx[m], p.ty[m]) + raised + mirrored
        t, rep = _large_map(tpl, "top", samples)
        rep.pop("view")
        if len(raised) + len(mirrored):
            rep |= {"side_high_samples": len(raised), "mirror_filled": len(mirrored)}
        # A part seen edge-on from above (a wing endplate) spans the texture but almost no car space.
        scale = _min_scale(np.asarray(t["coef"]).T, *samples.car.T)
        if scale < tpl.px(MIN_PX_PER_UNIT) or rep["p90_px"] > tpl.px(MAX_SMALL_P90_PX):
            rep["map"] = None
        else:
            out[rid] = t
            evidence[("top", rid)] = samples
        report.setdefault(rid, {"view": tpl.band[rid]})["top"] = rep
    return out


def top_maps(
    tpl: Template,
    top: Top,
    sd: dict[str, Side],
    transforms: dict,
    strips: set[str],
    report: dict,
    evidence: dict,
):
    """The top-rest model, x = x0 + x_per_u u over the top islands without a map, and a map (u, w) -> (x, y)
    per top island that fits, with w linear in texture y: from the top shot, and where it hardly sees an
    island (steep faces at the nose and tail) from the side shots, with u from their frames."""
    p = top.shot
    on_top = np.isin(p.ids, list(tpl.of_band("top")))
    rest = on_top & ~np.isin(p.ids, list(transforms))
    if rest.sum() < MIN_SAMPLES:
        raise FitError(f"the top shot decoded only {rest.sum()} samples on the top islands")
    coef, _, _ = _robust_fit(np.stack([np.ones(rest.sum()), top.u[rest]], 1), p.tx[rest, None])
    x0, x_per_u = coef[:, 0]
    own = {}
    for rid in sorted((tpl.of_band("top") - set(transforms)) - strips):  # the top model misplaces a strip
        m = p.ids == rid
        src, samples = "top", Samples.of(top.u[m], top.w[m], p.tx[m], p.ty[m])
        if m.sum() < MIN_SAMPLES:
            src, samples = "side", Samples.empty()
            for side in sd.values():
                ms = side.shot.ids == rid
                w = (side.shot.ty[ms] - tpl.axis) / top.y_per_w
                samples = samples + Samples.of(side.u[ms], w, side.shot.tx[ms], side.shot.ty[ms])
        if len(samples) < MIN_SAMPLES:
            if tpl.area[rid] > tpl.area_px(MAX_SMALL_AREA_PX):
                report.setdefault(rid, {"view": "top"})["map"] = "top-rest"
            continue
        own_samples = samples
        if tpl.partner(rid) == rid:  # an island across the centre line: each half shows the other's
            samples = samples + _reflected(tpl, rid, samples)
        t, rep = _large_map(tpl, "top", samples)
        rep.pop("view")
        if len(samples) > len(own_samples):
            rep["mirror_filled"] = len(samples) - len(own_samples)
        rep_ = report.setdefault(rid, {"view": "top"})
        rep_.update(source=src, **rep)
        # A quadratic fitted on part of an island can turn back over the rest of it: x must keep moving
        # the way the overall top scale does, over its samples.
        c = np.asarray(t["coef"])[0]
        u, w = samples.car.T
        slope = c[1] + 2 * c[3] * u + c[4] * w
        if rep["rms_px"] <= tpl.px(MAX_TOP_RMS) and (slope * x_per_u).min() <= 0:
            rep_.update(folds=True, map="top-rest")
        elif rep["rms_px"] <= tpl.px(MAX_TOP_RMS):
            own[rid] = t
            evidence[("top", rid)] = samples
        elif tpl.area[rid] > tpl.area_px(MAX_SMALL_AREA_PX):
            rep_["map"] = "top-rest"
    for rid in strips - set(transforms):  # the sides couldn't map it, and the top model misplaces it
        report[rid]["map"] = "top-rest"
    out: dict = {
        "x0": round(float(x0), 2),
        "x_per_u": round(float(x_per_u), 2),
        "axis_y": tpl.axis,
        "y_per_w": round(top.y_per_w, 2),
    }
    return out, own


def rest_domain(tpl: Template, shots: dict[str, Shot], rest: set[str]) -> list:
    """Where some view sees each top island the top-rest model maps: only there (an island no view sees,
    like a vent's floor or the glass, isn't painted through it)."""
    out = []
    for rid in sorted(rest):
        tx = np.concatenate([s.tx[s.ids == rid] for s in shots.values()])
        ty = np.concatenate([s.ty[s.ids == rid] for s in shots.values()])
        out += _domain(tpl, tx, ty)
    return out


# Stage 9: the faces.


@dataclass
class Face:
    """A face view's shot in the face's car space (w, v)."""

    shot: Shot
    w: np.ndarray
    v: np.ndarray


def _homography(xy_w, w, xy_v, v) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Screen -> (w, v), w = (h0 x + h1 y + h2) / d and v = (h3 x + h4 y + h5) / d with d = h6 x + h7 y + 1,
    fitted linearly on points where only w is known and points where only v is: robust, and with the
    perspective terms held near zero unless the anchors need them (a face seen straight on is near affine,
    and the anchors, along its edges, hardly fix them). Returns h and each anchor's miss."""
    rows_w = np.column_stack([xy_w, np.ones(len(w)), np.zeros((len(w), 3)), -w[:, None] * xy_w])
    rows_v = np.column_stack([np.zeros((len(v), 3)), xy_v, np.ones(len(v)), -v[:, None] * xy_v])
    a, b = np.vstack([rows_w, rows_v]), np.concatenate([w, v])
    ridge = np.sqrt(len(b)) * np.eye(8)[6:]
    keep = np.ones(len(b), bool)
    for _ in range(3):
        h, *_ = np.linalg.lstsq(np.vstack([a[keep], ridge]), np.concatenate([b[keep], [0, 0]]), rcond=None)
        miss_w = np.abs(_project(h, xy_w)[:, 0] - w)
        miss_v = np.abs(_project(h, xy_v)[:, 1] - v)
        err = np.concatenate([miss_w, miss_v])
        keep = err <= max(3 * np.median(err[keep]), CAR_FLOOR)
    return h, miss_w, miss_v


def _project(h: np.ndarray, xy: np.ndarray) -> np.ndarray:
    d = h[6] * xy[:, 0] + h[7] * xy[:, 1] + 1
    return (
        np.stack([h[0] * xy[:, 0] + h[1] * xy[:, 1] + h[2], h[3] * xy[:, 0] + h[4] * xy[:, 1] + h[5]], 1)
        / d[:, None]
    )


def face_frames(tpl: Template, shots: dict[str, Shot], sd: dict[str, Side], top: Top, report: dict) -> dict:
    """Each face view's frame: v from the texture cells it shares with the side panels near its end, w from
    those it shares with the top shot near its end (w where the large top islands put them). A face whose
    view has too few of either is left out, with the reason in the report."""
    out = {}
    for face, view in FACES.items():
        f = shots.get(view)
        if f is None:
            continue
        rep: dict = {"from": view}
        report.setdefault("frames", {})[face] = rep

        def near(u: np.ndarray, end: str = face) -> np.ndarray:
            return u > 1 - FACE_END if end == "rear" else u < FACE_END

        keys = np.concatenate([s.shot.key[s.panels & near(s.u)] for s in sd.values()])
        vs = np.concatenate([s.v[s.panels & near(s.u)] for s in sd.values()])
        centre = f.screen().mean(axis=0)
        xy = f.screen() - centre
        cv, xy_v = _common_cells(keys, vs[:, None], f.key, xy)
        m = near(top.u)
        cw, xy_w = _common_cells(top.shot.key[m], top.w_seen[m, None], f.key, xy)
        rep |= {"w_cells": len(cw), "v_cells": len(cv)}
        if min(len(cw), len(cv)) < MIN_FACE_CELLS:
            rep["skipped"] = (
                f"too few cells shared with the {'top' if len(cw) < MIN_FACE_CELLS else 'side'} shots"
            )
            continue
        h, miss_w, miss_v = _homography(xy_w, cw[:, 0], xy_v, cv[:, 0])
        wv = _project(h, xy)
        rep |= {
            "w_p90": round(float(np.percentile(miss_w, 90)), 4),
            "v_p90": round(float(np.percentile(miss_v, 90)), 4),
        }
        # On a symmetric car a texel and its mirror image sit at -w of each other.
        a, b = _common_cells(f.key, wv[:, :1], tpl.cells(f.tx, 2 * tpl.axis - f.ty), wv[:, :1])
        rep["mirror_w_p90"] = (
            round(float(np.percentile(np.abs(a + b), 90)), 4) if len(a) >= MIN_FACE_CELLS else None
        )
        out[face] = Face(f, wv[:, 0], wv[:, 1])
    return out


def _top_fails(rep: dict, tpl: Template) -> bool:
    return rep.get("p90_px", 0) > tpl.px(MAX_SMALL_P90_PX) or rep.get("dropped", 0) > MAX_TOP_DROPPED


def face_maps(
    tpl: Template,
    faces: dict[str, Face],
    transforms: dict,
    tops: dict,
    rest: set[str],
    report: dict,
    evidence: dict,
) -> dict:
    """A face map (w, v) -> (x, y) for each island a face view sees enough of that neither side maps and the
    top maps badly or not at all (`tops`: the top's own maps; `rest`: the islands only its top-rest model
    holds). Of two faces, the one that saw more of it. The replaced top maps leave `tops`."""
    fits: dict[str, list] = {}
    for face, fc in faces.items():
        s = fc.shot
        for rid in sorted(set(s.ids) - set(transforms)):
            if rid in tops and not _top_fails(_top_rep(report, rid), tpl):
                continue
            m = s.ids == rid
            small = tpl.area[rid] <= tpl.area_px(MAX_SMALL_AREA_PX)
            if m.sum() < (MIN_SMALL_SAMPLES if small else MIN_SAMPLES):
                continue
            own = Samples.of(fc.w[m], fc.v[m], s.tx[m], s.ty[m])
            samples = own + _face_reflected(tpl, rid, own)
            if m.sum() >= MIN_SAMPLES:
                t, rep = _large_map(tpl, face, samples)
                scale = _min_scale(np.asarray(t["coef"]).T, *samples.car.T)
                if scale < tpl.px(MIN_PX_PER_UNIT) or rep["p90_px"] > tpl.px(MAX_SMALL_P90_PX):
                    report.setdefault(rid, {"view": tpl.band[rid]}).setdefault("faces", {})[face] = {
                        "samples": int(m.sum()),
                        "p90_px": rep["p90_px"],
                        "map": None,
                    }
                    continue
            else:
                got = _small_map(tpl, face, samples)
                if got is None:
                    continue
                t, rep = got
            if len(samples) > len(own):
                rep["mirror_filled"] = len(samples) - len(own)
            fits.setdefault(rid, []).append((int(m.sum()), face, t, rep, samples))
    out: dict = {}
    for rid, cands in sorted(fits.items()):
        n, face, t, rep, samples = max(cands, key=lambda c: c[0])
        out[rid] = t
        evidence[(face, rid)] = samples
        row = report.setdefault(rid, {"view": tpl.band[rid]})
        if rid in tops:  # the top map's report moves under what replaced it
            was = row.pop("top") if isinstance(row.get("top"), dict) else None
            if was is None:
                was = {k: row.pop(k) for k in list(row) if k not in ("view", "w_off")}
            row["replaced_by_face"] = {"face": face, "top": was}
            tops.pop(rid)
            evidence.pop(("top", rid), None)
        elif rid in rest:
            row["replaced_by_face"] = {"face": face, "top": "top-rest"}
        row["face"] = {**rep, "samples": n}
    for rid, t in list(out.items()):  # a partner no view maps takes the face map, reflected
        pid = tpl.partner(rid)
        if pid is None or pid == rid or pid in out or pid in transforms or pid in tops:
            continue
        out[pid] = _mirrored(t, rid, tpl.axis)
        report[rid]["face"]["mirror"] = pid
    return out


def _top_rep(report: dict, rid: str) -> dict:
    """The report of an island's top map: over_top's under "top", top_maps' the island's own."""
    row = report.get(rid, {})
    return row["top"] if isinstance(row.get("top"), dict) else row


def _face_reflected(tpl: Template, rid: str, s: Samples) -> Samples:
    """A face island's samples reflected across the car's centre line, when it mirrors itself in the
    texture: about the template's axis (laid out like the top) or across its own middle in x."""
    if tpl.partner(rid) == rid:
        return _reflected(tpl, rid, s, col=0)
    cx = tpl.x_centre(rid)
    return _reflected(tpl, rid, s, col=0, about_x=cx) if cx is not None else Samples.empty()


def map_views(maps: dict) -> list[str]:
    """The car-space views a kit's maps hold: the sides, the top, and the faces that have maps."""
    faces = {t["view"] for t in maps.get("side_transforms", {}).values()}
    return [*SIDES, PLAN, *(f for f in kits.FACE_VIEWS if f in faces)]


# The fit.


@dataclass
class Fitted:
    maps: dict  # side_frames, side_transforms, top_transform
    report: dict
    evidence: dict[tuple[str, str], Samples]  # (view, island) -> the samples its map was fitted on


def fit_maps(tpl: Template, shots: dict[str, Shot]) -> Fitted:
    """Every stage, from the shots to the maps. Only the fit's views are read: a capture's close-ups and
    second-light views don't feed it."""
    shots = {v: s for v, s in shots.items() if v in VIEWS or v in OPTIONAL}
    frames, panels, frame_report = side_frames(tpl, shots)
    report: dict = {"frames": frame_report}
    sd = sides(tpl, shots, frames, panels, report)
    transforms, fitted, rep = side_maps(tpl, sd)
    report |= rep
    top = top_frame(tpl, shots, sd, report)
    strips = top_strips(tpl, top, report)
    small_maps(tpl, sd, strips, transforms, fitted, report)
    mirror(tpl, transforms, fitted, report)
    evidence = {(t["view"], rid): fitted[rid] for rid, t in transforms.items() if rid in fitted}
    over = over_top(tpl, top, sd, transforms, report, evidence, high_side(tpl, top, shots, report))
    top_t, own = top_maps(tpl, top, sd, transforms, strips, report, evidence)
    top_t["islands"] = {}
    tops = {**over, **own}
    rest_ids = tpl.of_band("top") - set(transforms) - set(own)
    faces = face_frames(tpl, shots, sd, top, report)
    transforms |= face_maps(tpl, faces, transforms, tops, rest_ids, report, evidence)
    top_t["sides"] = tops
    rest_ids -= set(transforms)
    top_t["rest_domain"] = rest_domain(tpl, shots, rest_ids)
    rest = np.isin(top.shot.ids, list(rest_ids))
    evidence[("top", "top-rest")] = Samples.of(top.u[rest], top.w[rest], top.shot.tx[rest], top.shot.ty[rest])
    return Fitted(
        {"side_frames": frames, "side_transforms": transforms, "top_transform": top_t}, report, evidence
    )


# Seams.

SEAM_STEP_PX = 8
SEAM_REACH_PX = 10
SEAM_ROUND_TRIP = 0.01  # car-space units, of a 2048 px template: a texel is more of the car on a smaller one


def seam_pairs(kit: Kit) -> list[list[int]]:
    return seam_pairs_of({view: carspace.pieces(kit, view) for view in kit.views}, kit.canvas)


def seam_pairs_of(pieces: dict[str, tuple[carspace.Piece, ...]], canvas: int) -> list[list[int]]:
    """Pairs of texture points on two islands that are the same spot on the car, along every seam.

    Walk each island's edge, map the point to car space through that island, and back through every other
    island of the view: where it lands within SEAM_REACH_PX of that island, the two points face each other
    across a seam. The output lint compares paint at each pair.
    """
    pairs = set()
    trip = SEAM_ROUND_TRIP * REF_CANVAS / canvas
    for view, every in pieces.items():
        # The top view's overall scale is too rough to pair seams with; only fitted islands take part.
        pcs = [pc for pc in every if pc.island != "top-rest"]
        start = carspace.start(view)
        for a in pcs:
            for ring in getattr(a.area, "geoms", [a.area]):
                if ring.is_empty or not hasattr(ring, "exterior"):
                    continue
                edge = ring.exterior
                for d in np.arange(0, edge.length, SEAM_STEP_PX):
                    pa = edge.interpolate(d)
                    inside = shapely.Point(pa.x, pa.y).buffer(3).intersection(a.area).centroid
                    if inside.is_empty:
                        continue
                    car, _ = carspace.invert(a, (inside.x, inside.y), start)
                    if not a.covers_car(car):
                        continue
                    for b in pcs:
                        if b is a or not b.covers_car(car):
                            continue
                        xy = b.to_px(car[None])[0]
                        if b.area.distance(shapely.Point(xy)) > SEAM_REACH_PX:
                            continue
                        near = shapely.ops.nearest_points(b.area, shapely.Point(xy))[0]
                        nb = shapely.Point(near.x, near.y).buffer(3).intersection(b.area).centroid
                        if nb.is_empty:
                            continue
                        # Keep the pair only if b's own inverse lands on the same car-space point, from the
                        # middle of where b was measured too: a map can fold back near its island far from
                        # where it was measured, and a folded map has a second car point that lands here.
                        back, _ = carspace.invert(b, (nb.x, nb.y), car)
                        mid = b.car_area.centroid if b.car_area is not None else None
                        neutral, _ = carspace.invert(b, (nb.x, nb.y), (mid.x, mid.y) if mid else start)
                        if max(np.abs(back - car).max(), np.abs(neutral - car).max()) <= trip:
                            pairs.add((round(inside.x), round(inside.y), round(nb.x), round(nb.y)))
    return sorted([list(p) for p in pairs])


# Against the kit's current maps.


def _piece_px(pcs: tuple[carspace.Piece, ...], rid: str, car: np.ndarray) -> np.ndarray | None:
    """Where a view's maps put car-space points of island `rid` (or of the top-rest model)."""
    for pc in pcs:
        if pc.island == rid:
            return pc.to_px(car)
    return None


def _reframed(frame: np.ndarray, car: np.ndarray, view: str) -> np.ndarray:
    v = car[:, 1] if view == PLAN else frame[2] + frame[3] * car[:, 1]
    return np.stack([frame[0] + frame[1] * car[:, 0], v], 1)


def old_frame(old: tuple[carspace.Piece, ...], new: Fitted, view: str) -> np.ndarray:
    """The old maps' car space on the capture's screen: (a_u, b_u, a_v, b_v), u_old = a_u + b_u u and
    v_old = a_v + b_v v (on the top only u: w is the texture's), fitted so the old maps miss this view's
    large islands least. Two fits frame the car a little differently; this compares their maps."""
    from scipy.optimize import least_squares

    runs = []
    for (v, rid), s in new.evidence.items():
        piece = next((pc for pc in old if pc.island == rid), None)
        if v == view and piece is not None and len(s) >= GRID_MIN_SAMPLES:
            step = max(1, len(s) // 2000)
            runs.append((s.car[::step], s.tex[::step], piece))
    if not runs:
        return np.array([0.0, 1.0, 0.0, 1.0])

    def resid(x: np.ndarray) -> np.ndarray:
        return np.concatenate([(pc.to_px(_reframed(x, car, view)) - tex).ravel() for car, tex, pc in runs])

    return least_squares(resid, [0.0, 1.0, 0.0, 1.0], loss="soft_l1", f_scale=2.0).x


def compare(kit: Kit, tpl: Template, old_maps: dict, new: Fitted) -> dict:
    """Per island and view, on the samples the new map was fitted on: each map's miss at its 90th
    percentile (the old one in the car space that fits it best, `old_frames`) and how far paint at the same
    car-space point moves from the old map to the new (p90 and most), and the islands whose view changes."""
    _, shapes = carspace.load_shapes(kit)
    new_raw = {**old_maps, **new.maps}
    out: dict = {"old_frames": {}, "islands": {}, "owners": {}}
    was_by, now_by = owners(old_maps), owners(new_raw)
    for rid in sorted(was_by.keys() | now_by.keys()):
        if was_by.get(rid) != now_by.get(rid):
            out["owners"][rid] = [was_by.get(rid), now_by.get(rid)]
    views = map_views(old_maps) if "top_transform" in old_maps else []
    for view in [*views, *(v for v in map_views(new_raw) if v not in views)]:
        old_pcs = carspace.build_pieces(old_maps, shapes, view) if "top_transform" in old_maps else ()
        new_pcs = carspace.build_pieces(new_raw, shapes, view)
        frame = old_frame(old_pcs, new, view)
        out["old_frames"][view] = [round(float(x), 4) for x in frame]
        for (v, rid), s in sorted(new.evidence.items()):
            now = _piece_px(new_pcs, rid, s.car) if v == view and len(s) else None
            if now is None:
                continue
            row: dict = {"samples": len(s), "p90_px": _p90(now - s.tex)}
            was = _piece_px(old_pcs, rid, s.car)
            if was is not None:
                best = _piece_px(old_pcs, rid, _reframed(frame, s.car, view))
                row["old_p90_px"] = _p90(best - s.tex) if best is not None else None
                moved = np.linalg.norm(now - was, axis=1)
                row["moved_p90_px"] = round(float(np.percentile(moved, 90)), 1)
                row["moved_max_px"] = round(float(moved.max()), 1)
            row["main"] = rid == "top-rest" or tpl.area.get(rid, 0) > tpl.area_px(MAX_SMALL_AREA_PX)
            out["islands"][f"{view}/{rid}"] = row
    out["old_sides_agree"] = _sides_agree(old_maps.get("side_transforms", {}), tpl)
    out["sides_agree"] = new.report.get("sides_agree", {})
    return out


def owners(maps: dict) -> dict[str, str]:
    """The view whose map paints each island: its side or face map's, else "top" for a map of the top's."""
    top = maps.get("top_transform", {})
    out = {rid: "top" for part in ("sides", "islands") for rid in top.get(part, {})}
    return out | {rid: t["view"] for rid, t in maps.get("side_transforms", {}).items()}


def _p90(d: np.ndarray) -> float:
    return round(float(np.percentile(np.linalg.norm(d, axis=1), 90)), 1)


def keep_old(new: Fitted, old: dict, keep: set[str]) -> list[str]:
    """Put back the current maps of the islands in `keep`, wherever the old fit mapped them."""
    kept = []
    sides, top = new.maps["side_transforms"], new.maps["top_transform"]
    old_top = old.get("top_transform", {})
    for rid in sorted(keep):
        found = False
        if rid in old.get("side_transforms", {}):
            sides[rid] = old["side_transforms"][rid]
            found = True
        for part in ("sides", "islands"):
            if rid in old_top.get(part, {}):
                top.setdefault(part, {})[rid] = old_top[part][rid]
                found = True
        if found:
            kept.append(rid)
            if rid not in old_top.get("sides", {}):
                top["sides"].pop(rid, None)
            if rid not in old.get("side_transforms", {}):
                sides.pop(rid, None)
    return kept


def design_moves(ws: Workspace, kit: Kit, maps: dict, seams: list) -> dict[str, float | str]:
    """For each approved design on the kit: the share of the car's pixels whose paint the new maps move by
    more than the approvals' tolerance, or why it doesn't build on them."""
    import io

    from livery import approvals
    from livery.designs import kit_name, render_outputs

    names = sorted(
        p.parent.name
        for p in ws.designs.glob("*/design.py")
        if (p.parent / "approved.json").exists() and kit_name(p) == kit.name
    )
    if not names:
        return {}
    car = np.asarray(Image.open(kit.mask_path).convert("L")) > 127
    out: dict[str, float | str] = {}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        shutil.copytree(kit.dir, root / "kits" / kit.name)
        meta = kits.read_meta(kit.dir) | {"views": map_views(maps)}
        kits.write(root / "kits" / kit.name, meta=meta, maps=maps)
        (root / "kits" / kit.name / "seams.json").write_text(
            json.dumps(seams), encoding="utf-8", newline="\n"
        )
        for n in names:
            shutil.copytree(ws.design_dir(n), root / "designs" / n)
        other = replace(ws, root=root)

        def paint(w: Workspace, name: str) -> np.ndarray:
            return np.asarray(Image.open(io.BytesIO(render_outputs(w, name)["paint.png"])).convert("RGB"))

        for n in names:
            before = paint(ws, n)
            try:
                after = paint(other, n)
            except (LiveryError, ValueError) as e:
                out[n] = f"doesn't build: {e}"
                continue
            moved = np.abs(before.astype(np.int16) - after.astype(np.int16)).max(axis=2) > approvals.TOLERANCE
            out[n] = round(float(moved[car].mean()), 4)
    return out


# The fit's report, and the command.


def summary(report: dict, seams: list) -> dict:
    """The fit's report in brief: maps by kind, the worst islands and the seams."""
    rows = {r: v for r, v in report.items() if r.startswith("i") and isinstance(v, dict)}
    p90 = sorted(((v["p90_px"], r) for r, v in rows.items() if "p90_px" in v), reverse=True)
    agree = report.get("sides_agree", {})
    faces = {r: v["face"] for r, v in rows.items() if "face" in v}
    return {
        "islands_fitted": sum("p90_px" in v for v in rows.values()) + len(faces),
        "mirrored": sorted(v["mirror"]["island"] for v in rows.values() if v.get("mirror")),
        "replaced_by_mirror": sorted(r for r, v in rows.items() if "replaced_by_mirror_of" in v),
        "faces": {r: f["view"] for r, f in sorted(faces.items())},
        "replaced_by_face": sorted(r for r, v in rows.items() if "replaced_by_face" in v),
        "worst_p90_px": [[r, x] for x, r in p90[:8]],
        "sides_agree_worst_median_px": max((d["median_px"] for d in agree.values()), default=None),
        "seam_pairs": len(seams),
    }


def lines(result: dict) -> list[str]:
    """A fit's result as text: the summary, each island's miss against the current maps' and how far it
    moves, how the sides agree, and how much of each approved design moves."""
    s = result["summary"]
    out = [
        f"capture {result['capture']}: {s['islands_fitted']} island maps, {s['seam_pairs']} seam pairs; "
        f"mirrored {', '.join(s['mirrored']) or 'none'}; replaced by a mirror {', '.join(s['replaced_by_mirror']) or 'none'}",
        "worst p90: " + ", ".join(f"{r} {x} px" for r, x in s["worst_p90_px"]),
    ]
    frames = result.get("fit", {}).get("frames", {})
    for face in kits.FACE_VIEWS:
        if face in frames:
            out.append(f"{face} face frame: " + ", ".join(f"{k} {_num(v)}" for k, v in frames[face].items()))
    if s.get("faces"):
        out.append(
            "face maps: "
            + ", ".join(f"{r} {f}" for r, f in s["faces"].items())
            + f"; replacing top maps of {', '.join(s['replaced_by_face']) or 'none'}"
        )
    if result["kept"]:
        out.append(f"kept the current maps of {', '.join(result['kept'])}")
    cmp = result.get("compare")
    if cmp:
        out.append("view/island        samples  p90 now  p90 new  moved p90  moved max")
        rows = sorted(cmp["islands"].items(), key=lambda kv: (not kv[1]["main"], -kv[1]["samples"]))
        for key, r in rows:
            out.append(
                f"{key:18}{'*' if r['main'] else ' '}{r['samples']:7d}  {_num(r.get('old_p90_px')):>7}  "
                f"{_num(r['p90_px']):>7}  {_num(r.get('moved_p90_px')):>9}  {_num(r.get('moved_max_px')):>9}"
            )
        out.append("(* a main island; 'now' is the current maps, in the car space that fits them best)")
        if cmp.get("owners"):
            out.append(
                "islands changing view: "
                + ", ".join(f"{r} {_num(a)} -> {_num(b)}" for r, (a, b) in cmp["owners"].items())
            )
        pairs = sorted(cmp["sides_agree"].keys() | cmp["old_sides_agree"].keys())
        out.append(
            "sides agree, median px now -> new: "
            + ", ".join(
                f"{p} {_num(cmp['old_sides_agree'].get(p, {}).get('median_px'))} -> "
                f"{_num(cmp['sides_agree'].get(p, {}).get('median_px'))}"
                for p in pairs
            )
        )
    for name, share in result.get("designs_moved", {}).items():
        out.append(
            f"{name}: "
            + (share if isinstance(share, str) else f"{100 * share:.1f}% of the car's pixels move")
        )
    return out


def _num(x) -> str:
    return "-" if x is None else str(x)


def fit(
    cfg: Config,
    ws: Workspace,
    kit: Kit,
    *,
    dry_run: bool = False,
    keep: set[str] | frozenset[str] = frozenset(),
    designs: bool = False,
) -> dict:
    """Fit the kit's maps from its calibration capture. Unless `dry_run`, writes maps.json, seams.json and
    fit-report.json, raises the kit's version and clears its approval. `keep` puts back the current maps of
    those islands; `designs` also says how much of each approved design the new maps would move."""
    kits.check_model(kit, cfg.ui_cars)
    tpl = template(kit)
    shots, run = capture_shots(cfg, ws, kit, tpl)
    new = fit_maps(tpl, shots)
    old = kits.read_maps(kit.dir)
    kept = keep_old(new, old, set(keep))
    maps = {**old, **new.maps}
    _, shapes = carspace.load_shapes(kit)
    views = map_views(maps)
    seams = seam_pairs_of({view: carspace.build_pieces(maps, shapes, view) for view in views}, kit.canvas)
    result = {"capture": run.name, "summary": summary(new.report, seams), "kept": kept, "fit": new.report}
    if old.get("side_transforms"):
        result["compare"] = compare(kit, tpl, old, new)
    if designs:
        result["designs_moved"] = design_moves(ws, kit, maps, seams)
    if dry_run:
        return result
    kits.write(kit.dir, maps=maps)
    (kit.dir / "seams.json").write_text(json.dumps(seams), encoding="utf-8", newline="\n")
    version = kit.version + 1
    kits.write(kit.dir, meta=kits.read_meta(kit.dir) | {"version": version, "views": views})
    (kit.dir / kits.APPROVAL).unlink(missing_ok=True)  # new maps need the owner's review again
    report = {"version": version, **{k: v for k, v in result.items() if k != "designs_moved"}}
    (kit.dir / REPORT).write_text(kits.dumps(report) + "\n", encoding="utf-8", newline="\n")
    return result
