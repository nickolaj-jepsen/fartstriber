"""Which texel the sim draws at each screen pixel, read from Gray-coded paints.

Each pattern holds three bits of the Gray codes of a texel's u (texture x) and v (texture y), one per colour
channel, and is shot with its inverse: a pixel's bit is whether it is brighter in the pattern, which cancels
shading and reflections. The sim block-compresses the paint, and a 4x4 block keeps only two colours exactly,
so each pattern holds one bit that flips inside the blocks of the mips the views sample and coarse bits
beside it (docs/sim.md, Rendering): all 11 bits in 8 patterns.

A white and a black probe give the car mask and each bit's confidence, its contrast as a share of white
minus black. The decode reads bits coarse to fine and allows one unsure bit; a plane fit over each pixel's
neighbours on the same panel gives its uv to a fraction of a texel and its footprint, the Jacobian.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

BITS = 11  # 2048 texels per axis
# Per pattern, the (axis, Gray bit) in R, G and B: one of bits 0-3 in each, the rest from bits 4-10.
SCHEME: tuple[tuple[tuple[str, int], ...], ...] = (
    (("u", 0), ("u", 10), ("v", 10)),
    (("u", 1), ("u", 9), ("v", 9)),
    (("u", 8), ("v", 8), ("u", 2)),
    (("u", 7), ("v", 0), ("v", 7)),
    (("u", 6), ("v", 1), ("v", 6)),
    (("u", 5), ("v", 5), ("v", 2)),
    (("u", 4), ("u", 3), ("v", 4)),
    (("v", 3),),
)
WHITE, BLACK = "white", "black"  # the probes' shot names
SURE = 0.25  # a bit is sure where pattern and inverse differ by this share of white minus black
CAR_CONTRAST = 30  # white minus black on the car, in levels of the brightest channel
MAX_LEVEL = 6  # a pixel whose finest sure bit is coarser than this is left out
RADIUS = 3  # the plane fit's window: (2 RADIUS + 1)^2 pixels
TOLERANCE = 8.0  # texels per pixel of distance a neighbour may differ by and still be on the same panel
MIN_NEIGHBOURS = 10


def names() -> list[str]:
    """The pattern shots, each followed by its inverse."""
    return [f"gray{k}{s}" for k in range(len(SCHEME)) for s in ("", "i")]


def pattern(name: str, canvas: int) -> np.ndarray:
    """The paint for a pattern shot of `names()`, (canvas, canvas, 3) uint8."""
    if canvas > 1 << BITS:
        raise ValueError(f"a {canvas} px canvas needs more than {BITS} bits")
    inverse = name.endswith("i")
    k = int(name.removeprefix("gray").removesuffix("i"))
    idx = np.arange(canvas)
    gray = idx ^ (idx >> 1)
    img = np.zeros((canvas, canvas, 3), np.uint8)
    for c, (axis, bit) in enumerate(SCHEME[k]):
        on = ((gray >> bit) & 1).astype(bool)
        on = on[None, :] if axis == "u" else on[:, None]
        img[:, :, c] = np.where(on != inverse, 255, 0)
    return img


def car_mask(white: np.ndarray, black: np.ndarray) -> np.ndarray:
    return (white.astype(np.int16) - black.astype(np.int16)).max(axis=2) > CAR_CONTRAST


def gray_to_bin(g: np.ndarray) -> np.ndarray:
    b = g.copy()
    shift = 1
    while shift < 32:
        b ^= b >> shift
        shift <<= 1
    return b


def centre(g: np.ndarray, level: np.ndarray) -> np.ndarray:
    """The middle of the texel interval that the Gray bits at and above `level` pin down."""
    low = (np.int32(1) << np.minimum(level, BITS).astype(np.int32)) - 1
    return (gray_to_bin(g) & ~low) + (low + 1) / 2


def decode_axis(
    planes: dict[int, tuple[np.ndarray, np.ndarray]], sure: float = SURE
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per bit (pattern minus inverse, confidence) -> (texel coordinate, finest sure bit, the weakest sure
    bit's confidence). NaN where no bit is sure.

    Coarse to fine, stopping at the second unsure bit. One unsure bit is allowed: a pixel on a Gray boundary
    of bit k is unsure in bit k only, and the finer bits read the same on both sides, so the two readings
    sit either side of the boundary and the pixel is at their midpoint.
    """
    shape = planes[0][0].shape
    g = np.zeros(shape, np.int32)
    active = np.ones(shape, bool)
    unsure = np.full(shape, -1, np.int32)
    level = np.full(shape, BITS, np.int32)
    weakest = np.full(shape, np.inf, np.float32)
    for k in range(BITS - 1, -1, -1):
        d, conf = planes[k]
        ok = conf >= sure
        take = active & ok
        g[take & (d > 0)] |= 1 << k
        level[take] = k
        weakest[take] = np.minimum(weakest[take], conf[take])
        bad = active & ~ok
        active &= ~(bad & (unsure >= 0))
        unsure[bad & (unsure < 0)] = k
    u = centre(g, level)
    two = (unsure >= 0) & (level < unsure)
    g1 = g | np.where(two, np.int32(1) << np.maximum(unsure, 0), 0)
    c1 = centre(g1, level)
    near = two & (np.abs(u - c1) <= 2.0 ** (level + 1))
    far = two & ~near
    u = np.where(near, (u + c1) / 2, u)
    level = np.where(far, unsure + 1, level)
    u = np.where(far, centre(g, level), u).astype(np.float32)
    u[level >= BITS] = np.nan
    return u, level.astype(np.int8), weakest


def _shifted(a: np.ndarray, dy: int, dx: int, r: int) -> np.ndarray:
    pad = [(r, r), (r, r)] + [(0, 0)] * (a.ndim - 2)
    p = np.pad(a, pad, constant_values=np.nan)
    h, w = a.shape[:2]
    return p[r + dy : r + dy + h, r + dx : r + dx + w]


def plane_fit(
    uv: np.ndarray, level: np.ndarray, r: int = RADIUS, tol: float = TOLERANCE, min_n: int = MIN_NEIGHBOURS
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Least-squares plane of uv over a (2r+1)^2 window, from neighbours on the same panel only.

    Returns the plane's uv at each pixel, its Jacobian d(u, v)/d(x, y) in texels per pixel (NaN where too
    few neighbours fit), and the neighbour count. A neighbour counts when it is within both pixels'
    quantisation plus `tol` texels per pixel of distance.
    """
    h, w = uv.shape[:2]
    q = np.where(np.isnan(uv[..., 0]), np.nan, 2.0 ** level.max(axis=2)).astype(np.float32)
    s = np.zeros((h, w, 3, 3), np.float32)
    rhs = np.zeros((h, w, 3, 2), np.float32)
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            nb, nq = _shifted(uv, dy, dx, r), _shifted(q, dy, dx, r)
            with np.errstate(invalid="ignore"):
                ok = np.abs(nb - uv).max(axis=2) <= q + nq + tol * max(abs(dx), abs(dy))
            vec = np.array([1, dx, dy], np.float32)
            s += ok[..., None, None] * np.outer(vec, vec)
            rhs += np.where(ok[..., None], nb, 0)[..., None, :] * vec[:, None]
    n = s[..., 0, 0]
    good = n >= min_n
    good[good] = np.abs(np.linalg.det(s[good])) > 1.0
    sol = np.linalg.solve(s[good], rhs[good])
    out = uv.copy()
    out[good] = sol[:, 0, :]
    jac = np.full((h, w, 2, 2), np.nan, np.float32)
    jac[good] = np.stack([sol[:, 1, :], sol[:, 2, :]], axis=-1)
    return out, jac, n.astype(np.int16)


@dataclass(frozen=True)
class ViewMap:
    """One view: which screen pixels show paint and where in the texture each samples. The per-pixel
    arrays list the `pix` pixels in row-major order."""

    car: np.ndarray  # (h, w) bool: the probes differ
    pix: np.ndarray  # (h, w) bool: car pixels with a decoded uv
    uv: np.ndarray  # (n, 2) float32: texels from the texture's top-left corner; texel i spans [i, i + 1)
    jac: np.ndarray  # (n, 2, 2) float32: d(u, v)/d(x, y), texels per pixel; NaN where no plane fit
    level: np.ndarray  # (n, 2) int8: the finest sure bit of u and v
    conf: np.ndarray  # (n,) float16: the weakest sure bit's confidence
    agree: np.ndarray  # (n,) bool: the plane fit is within the raw decode's interval

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, **{k: getattr(self, k) for k in self.__dataclass_fields__})

    @classmethod
    def load(cls, path: Path) -> "ViewMap":
        with np.load(path) as z:
            return cls(**{k: z[k] for k in cls.__dataclass_fields__})

    def stats(self) -> dict:
        """Its share of the car decoded, the plane fit's coverage and agreement, and the footprint."""
        fitted = np.isfinite(self.jac).all(axis=(1, 2))
        car = int(self.car.sum())
        out: dict = {
            "car_px": car,
            "decoded_pct": round(100 * float(self.pix.sum()) / max(car, 1), 2),
            "plane_fit_pct": round(100 * float(fitted.mean()), 2) if len(fitted) else 0.0,
            "plane_agree_pct": round(100 * float(self.agree[fitted].mean()), 2) if fitted.any() else 0.0,
            "finest_bit_pct": {
                int(k): round(100 * float((self.level.max(axis=1) == k).mean()), 2)
                for k in range(MAX_LEVEL + 1)
            },
        }
        if fitted.any():
            sv = np.linalg.svd(self.jac[fitted], compute_uv=False)
            out["texels_per_px"] = {
                "major_p10_50_90": np.percentile(sv[:, 0], [10, 50, 90]).round(2).tolist(),
                "minor_p10_50_90": np.percentile(sv[:, 1], [10, 50, 90]).round(2).tolist(),
            }
        return out


def decode(shot: Callable[[str], np.ndarray], sure: float = SURE) -> ViewMap:
    """Decode one view from its shots: `shot(name)` gives the (h, w, 3) frame of WHITE, BLACK or a name in
    `names()`."""
    white, black = shot(WHITE), shot(BLACK)
    car = car_mask(white, black)
    # Only the car and the fit's reach around it.
    ys, xs = np.nonzero(car) if car.any() else (np.array([0, car.shape[0]]), np.array([0, car.shape[1]]))
    r = RADIUS + 1
    box = np.s_[max(ys.min() - r, 0) : ys.max() + r + 1, max(xs.min() - r, 0) : xs.max() + r + 1]
    norm = np.maximum(white[box].astype(np.float32) - black[box].astype(np.float32), 1)
    planes: dict[tuple[str, int], tuple[np.ndarray, np.ndarray]] = {}
    for k, slots in enumerate(SCHEME):
        p, q = (shot(f"gray{k}{s}")[box].astype(np.float32) for s in ("", "i"))
        for c, key in enumerate(slots):
            d = p[..., c] - q[..., c]
            planes[key] = (d, np.abs(d) / norm[..., c])
    axes = [decode_axis({k: planes[(axis, k)] for k in range(BITS)}, sure) for axis in "uv"]
    raw = np.stack([a[0] for a in axes], axis=2)
    level = np.stack([a[1] for a in axes], axis=2)
    conf = np.minimum(axes[0][2], axes[1][2])
    raw[(level > MAX_LEVEL).any(axis=2)] = np.nan
    # Pixels just outside the mask (dark ones at the silhouette) still decode, and steady the fit of their
    # neighbours on the car; the background renders the same in every shot, so it hardly ever decodes.
    fit, jac, _ = plane_fit(raw, level)
    on = car[box] & ~np.isnan(raw[..., 0])
    pix = np.zeros_like(car)
    pix[box] = on
    with np.errstate(invalid="ignore"):
        agree = np.abs(raw - fit).max(axis=2) <= 2.0 ** level.max(axis=2)
    return ViewMap(
        car=car,
        pix=pix,
        uv=fit[on].astype(np.float32),
        jac=jac[on],
        level=level[on],
        conf=conf[on].astype(np.float16),
        agree=agree[on],
    )
