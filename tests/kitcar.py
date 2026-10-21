"""A synthetic car for kit fit's tests: an analytic body, its paint texture laid out the way an iRacing
template lays one out, and the preview views rendered into the calibration capture's per-pixel format
(`graycode.ViewMap`), so a fit can be checked against where each texel really is on the car.

The body is a slab 4 m long: each side a panel that bulges a little (front, door and rear islands), a
shoulder strip turning from the side over into the top, which the side views see nearly edge-on (one island,
and four small louvres at the front), and the top (bonnet, roof, rear deck) with a hump in the middle. A
bank in front of the car can hide the bottom of one side. The left side's islands sit below the mirror axis in the texture, the right side's are their
mirror images above it, the top's are in the middle with w linear in texture y. Texture x runs nose to tail,
with a gap between islands that meet on the car, and a little bend so the maps aren't plain affine.
"""

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

from livery import graycode

CANVAS = 1024
AXIS = 511.5  # texture y the sides mirror about: y <-> 2 AXIS - y
W, H = 890, 496  # a view's frame
HALF = 2.0  # half the car's length, m
SILL, BELT = 0.15, 0.9  # the side panel's height range, m
TOP_HALF = 0.75  # the top's half width, m
RISE = 0.02  # m the shoulder rises over its 13 cm: the side views see it nearly edge-on
PX_PER_M = 220  # texture px per metre along the car
SPACING = 0.002  # m between the points a surface is drawn with: under half a pixel in every view

# Island ids, numbered as kit extract numbers them: by band, then nose to tail.
LEFT = {"front": "i001", "door": "i002", "rear": "i003", "shoulder": "i007"}
RIGHT = {"front": "i004", "door": "i005", "rear": "i006", "shoulder": "i008"}
TOP = {"front": "i009", "door": "i010", "rear": "i011"}  # bonnet, roof, rear deck
LOUVRES = 1.2  # m: forward of this the shoulder is four small islands, 0.2 m each
LEFT |= {f"louvre{k}": f"i{12 + k:03d}" for k in range(4)}
RIGHT |= {f"louvre{k}": f"i{16 + k:03d}" for k in range(4)}
BAND = {**dict.fromkeys(LEFT.values(), "lower"), **dict.fromkeys(RIGHT.values(), "upper")}
BAND |= dict.fromkeys(TOP.values(), "top")
GAP = {"front": 0.0, "door": 12.0, "rear": 24.0}  # texture px between islands that meet on the car


def segment(x: np.ndarray) -> np.ndarray:
    return np.where(x >= 0.8, "front", np.where(x >= -0.8, "door", "rear"))


def _gap(x: np.ndarray) -> np.ndarray:
    return np.select([x >= 0.8, x >= -0.8], [GAP["front"], GAP["door"]], GAP["rear"])


def _tx(x: np.ndarray, bend: np.ndarray) -> np.ndarray:
    return 40 + PX_PER_M * (HALF - x) + bend


@dataclass(frozen=True)
class Surface:
    """A piece of the body as a function of two parameters: points, and texels on the left side's layout."""

    name: str
    a: tuple[float, float]  # the parameters' ranges
    b: tuple[float, float]
    point: Callable[[np.ndarray, np.ndarray], np.ndarray]  # (n,), (n,) -> (n, 3): x forward, y left, z up
    texel: Callable[[np.ndarray, np.ndarray], np.ndarray]  # -> (n, 2)
    island: Callable[[np.ndarray, np.ndarray], np.ndarray]  # -> (n,) ids


def _side(sign: int) -> Surface:
    ids = LEFT if sign > 0 else RIGHT

    def point(x, z):
        return np.stack([x, sign * (0.88 + 0.04 * np.sin(np.pi * (z - SILL) / (BELT - SILL))), z], 1)

    def texel(x, z):
        t = (z - SILL) / (BELT - SILL)
        tx = _tx(x, _gap(x) + 6 * np.sin(np.pi * t) * x / HALF)
        ty = 990 - 268 * t + 4 * np.cos(np.pi * x / 4)
        return np.stack([tx, ty if sign > 0 else 2 * AXIS - ty], 1)

    return Surface(
        f"side{sign}", (-HALF, HALF), (SILL, BELT), point, texel, lambda x, z: _ids(ids, segment(x))
    )


def _louvre(x: np.ndarray) -> np.ndarray:
    """Which louvre each x is on, -1 behind them."""
    return np.where(x >= LOUVRES, np.clip(np.floor((x - LOUVRES) / 0.2), 0, 3), -1).astype(int)


def _shoulder(sign: int) -> Surface:
    ids = LEFT if sign > 0 else RIGHT

    def point(x, s):
        return np.stack([x, sign * (0.88 - 0.13 * s), BELT + RISE * np.sin(np.pi * s / 2)], 1)

    def texel(x, s):
        ty = 706 - 24 * s
        return np.stack([_tx(x, -6.0 * (_louvre(x) + 1)), ty if sign > 0 else 2 * AXIS - ty], 1)

    def island(x, s):
        k = _louvre(x)
        out = np.full(len(x), ids["shoulder"], dtype=object)
        for i in range(4):
            out[k == i] = ids[f"louvre{i}"]
        return out

    return Surface(f"shoulder{sign}", (-HALF, HALF), (0.0, 1.0), point, texel, island)


def top_z(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return BELT + RISE + 0.22 * np.cos(np.pi * x / (2 * HALF)) ** 2 * (1 - (y / TOP_HALF) ** 2)


def _top() -> Surface:
    def point(x, y):
        return np.stack([x, y, top_z(x, y)], 1)

    def texel(x, y):
        w = -y / TOP_HALF  # left is -1
        return np.stack([_tx(x, _gap(x) + 5 * w * w * np.cos(np.pi * x / 4)), AXIS - 150 * w], 1)

    return Surface(
        "top", (-HALF, HALF), (-TOP_HALF, TOP_HALF), point, texel, lambda x, y: _ids(TOP, segment(x))
    )


def _ids(ids: dict[str, str], seg: np.ndarray) -> np.ndarray:
    out = np.empty(len(seg), dtype=object)
    for k, v in ids.items():
        out[seg == k] = v
    return out


SURFACES = (_side(1), _side(-1), _shoulder(1), _shoulder(-1), _top())


def _grid(s: Surface, spacing: float = SPACING) -> tuple[np.ndarray, np.ndarray]:
    na = int((s.a[1] - s.a[0]) / spacing) + 1
    # The second parameter's metres per unit: the shoulder's runs 0-1 over about 15 cm.
    span = float(
        np.linalg.norm(
            s.point(np.array([0.0]), np.array([s.b[1]])) - s.point(np.array([0.0]), np.array([s.b[0]]))
        )
    )
    nb = int(span / spacing) + 2
    a, b = np.meshgrid(np.linspace(*s.a, na), np.linspace(*s.b, nb), indexing="ij")
    return a.ravel(), b.ravel()


# Cameras: the profile's preview poses, aimed at the car.


@dataclass(frozen=True)
class Camera:
    at: tuple[float, float, float]
    look: tuple[float, float, float]
    fov: float
    up: tuple[float, float, float] = (0.0, 0.0, 1.0)

    def basis(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        f = np.subtract(self.look, self.at)
        f = f / np.linalg.norm(f)
        r = np.cross(f, self.up)
        r = r / np.linalg.norm(r)
        return f, r, np.cross(r, f)

    def project(self, p: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        f, r, u = self.basis()
        d = p - np.asarray(self.at)
        depth = d @ f
        k = (W / 2) / np.tan(self.fov / 2)
        return W / 2 + k * (d @ r) / depth, H / 2 - k * (d @ u) / depth, depth


CAMERAS = {
    "side_l": Camera((0.0, 25.0, 0.5), (0.0, 0.0, 0.5), 0.2),
    "side_r": Camera((0.0, -25.0, 0.5), (0.0, 0.0, 0.5), 0.2),
    "front34": Camera((18.96, 15.91, 3.98), (0.0, 0.0, 0.4), 0.24),
    "rear34": Camera((-16.59, -13.92, 13.0), (0.0, 0.0, 0.4), 0.24),
    "top": Camera((0.0, -2.18, 25.4), (0.0, 0.0, 0.5), 0.2, up=(0.0, 1.0, 0.0)),
}


def _normals(s: Surface, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    h = 1e-4
    da = s.point(a + h, b) - s.point(a - h, b)
    db = s.point(a, b + h) - s.point(a, b - h)
    n = np.cross(da, db)
    n /= np.linalg.norm(n, axis=1, keepdims=True)
    # Outward: away from the car's centre line and up.
    out = s.point(a, b) * [0, 1, 0] + [0, 0, 0.3]
    return np.where(((n * out).sum(1) < 0)[:, None], -n, n)


def render(view: str, hide_below: float | None = None) -> graycode.ViewMap:
    """A view as the capture keeps it: per car pixel, the texel the sim draws there. Pixels lower on the
    screen than `hide_below` (a share of the frame's height) are hidden, as a bank in front of the car hides
    them."""
    cam = CAMERAS[view]
    keys, depths, txs, tys, ids = [], [], [], [], []
    for s in SURFACES:
        a, b = _grid(s)
        p = s.point(a, b)
        facing = (_normals(s, a, b) * (np.asarray(cam.at) - p)).sum(1) > 0
        a, b, p = a[facing], b[facing], p[facing]
        sx, sy, depth = cam.project(p)
        ix, iy = np.floor(sx).astype(int), np.floor(sy).astype(int)
        ok = (ix >= 0) & (ix < W) & (iy >= 0) & (iy < H)
        t = s.texel(a[ok], b[ok])
        keys.append(iy[ok] * W + ix[ok])
        depths.append(depth[ok])
        txs.append(t[:, 0])
        tys.append(t[:, 1])
        ids.append(s.island(a[ok], b[ok]))
    key, depth, tx, ty = (np.concatenate(x) for x in (keys, depths, txs, tys))
    island = np.concatenate(ids)
    order = np.lexsort((depth, key))
    key, depth, tx, ty, island = key[order], depth[order], tx[order], ty[order], island[order]
    first = np.r_[True, key[1:] != key[:-1]]
    start = np.maximum.accumulate(np.where(first, np.arange(len(key)), 0))
    # A pixel shows the nearest point's island; its texel is the mean over that island's points in it.
    same = (island == island[start]) & (depth < depth[start] + 0.02)
    k, inv = np.unique(key[same], return_inverse=True)
    n = np.bincount(inv)
    u = np.bincount(inv, tx[same]) / n
    v = np.bincount(inv, ty[same]) / n
    car = np.zeros(H * W, bool)
    car[k] = True
    car = car.reshape(H, W)
    if hide_below is not None:
        car[int(hide_below * H) :] = False
    uv = np.full((H * W, 2), np.nan, np.float32)
    uv[k] = np.stack([u, v], 1)
    uv = uv.reshape(H, W, 2)
    on = car & np.isfinite(uv[..., 0])
    m = int(on.sum())
    return graycode.ViewMap(
        car=car,
        pix=on,
        uv=uv[on],
        jac=np.tile(np.eye(2, dtype=np.float32), (m, 1, 1)),
        level=np.zeros((m, 2), np.int8),
        conf=np.ones(m, np.float16),
        agree=np.ones(m, bool),
    )


def render_all(hide_left_below: float | None = None) -> dict[str, graycode.ViewMap]:
    return {v: render(v, hide_left_below if v == "side_l" else None) for v in CAMERAS}


# The texture: islands, as kit extract writes them.


def outline(s: Surface, island: str, n: int = 60) -> np.ndarray:
    """An island's outline in the texture: its parameter range's edge, mapped."""
    a0, a1, b0, b1 = *s.a, *s.b
    a = np.linspace(a0, a1, 4000)
    keep = s.island(a, np.full(len(a), b0)) == island
    a0, a1 = float(a[keep].min()), float(a[keep].max())
    edge = [
        (np.linspace(a0, a1, n), np.full(n, b0)),
        (np.full(n, a1), np.linspace(b0, b1, n)),
        (np.linspace(a1, a0, n), np.full(n, b1)),
        (np.full(n, a0), np.linspace(b1, b0, n)),
    ]
    return np.concatenate([s.texel(x, y) for x, y in edge])


def islands() -> dict[str, np.ndarray]:
    """Every island's outline."""
    out = {}
    for s in SURFACES:
        for rid in sorted(set(s.island(*_grid(s, 0.05)))):
            out[rid] = outline(s, rid)
    return out


def write_template(kit_dir, write_kit) -> None:
    """islands.png, mask.png, regions.svg and the regions, for a kit at `kit_dir`."""
    shapes = islands()
    img = Image.new("L", (CANVAS, CANVAS), 0)
    draw = ImageDraw.Draw(img)
    regions, paths = [], []
    for rid in sorted(shapes):
        pts = [(float(x), float(y)) for x, y in shapes[rid]]
        draw.polygon(pts, fill=int(rid[1:]))
        ring = " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        paths.append(f'<path data-id="{rid}" d="M{ring} Z"/>')
    (kit_dir / "overlays").mkdir(parents=True, exist_ok=True)
    img.save(kit_dir / "overlays" / "islands.png")
    arr = np.asarray(img)
    Image.fromarray(np.where(arr > 0, 255, 0).astype(np.uint8)).save(kit_dir / "overlays" / "mask.png")
    for rid in sorted(shapes):
        ys, xs = np.nonzero(arr == int(rid[1:]))
        bbox = [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]
        regions.append({"id": rid, "view": BAND[rid], "bbox": bbox, "area": len(xs)})
    svg = '<svg xmlns="http://www.w3.org/2000/svg">' + "".join(paths) + "</svg>"
    (kit_dir / "regions.svg").write_text(svg, encoding="utf-8")
    write_kit(
        kit_dir,
        {
            "canvas": CANVAS,
            "paint_folder": "synth",
            "bleed_px": 4,
            "mirror_axis_y": AXIS,
            "regions": regions,
        },
    )


def where(rid: str, texel: np.ndarray) -> np.ndarray:
    """The 3D points an island's texels are painted on: the texture layout inverted by Newton's method."""
    s = next(s for s in SURFACES if rid in set(s.island(*_grid(s, 0.05))))
    a = np.full(len(texel), np.mean(s.a))
    b = np.full(len(texel), np.mean(s.b))
    # Start from the island's own segment, so a texel isn't pulled across a gap onto the next island.
    grid_a, grid_b = _grid(s, 0.02)
    mine = s.island(grid_a, grid_b) == rid
    ga, gb = grid_a[mine], grid_b[mine]
    gt = s.texel(ga, gb)
    nearest = np.argmin(((gt[None] - texel[:, None]) ** 2).sum(-1), axis=1)
    a, b = ga[nearest], gb[nearest]
    h = 1e-5
    for _ in range(20):
        t = s.texel(a, b)
        ja = (s.texel(a + h, b) - t) / h
        jb = (s.texel(a, b + h) - t) / h
        jac = np.stack([ja, jb], 2)
        step = np.linalg.solve(jac, (texel - t)[..., None])[..., 0]
        a, b = a + step[:, 0], b + step[:, 1]
    return s.point(a, b)
