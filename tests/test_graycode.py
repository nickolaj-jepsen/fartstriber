"""The Gray-code decode against synthetic renders: a known uv field, drawn through the patterns the way the
sim samples a texture (trilinear between mips), with shading, a highlight and dither."""

import numpy as np
import pytest

from livery import graycode

CANVAS = 2048
H, W = 120, 220
SEAM = 110  # two panels meet here, far apart in the texture
BORDER = 6  # background around the car


def field() -> tuple[np.ndarray, np.ndarray]:
    """uv (h, w, 2) at each pixel and its Jacobian (h, w, 2, 2), about 1.2 texels per pixel. The left panel
    crosses u = v = 1024, where bit 10 flips, and the right one v = 1536, where bit 9 does."""
    y, x = np.mgrid[0:H, 0:W].astype(np.float64)
    left = x < SEAM
    xr = x - SEAM
    u = np.where(left, 960 + 1.2 * x + 0.1 * y + 2e-4 * x * x, 300 + 1.15 * xr - 0.08 * y)
    v = np.where(left, 990 - 0.05 * x + 1.05 * y + 1e-4 * y * y, 1500 + 0.06 * xr + 1.1 * y)
    jac = np.empty((H, W, 2, 2))
    jac[..., 0, 0] = np.where(left, 1.2 + 4e-4 * x, 1.15)
    jac[..., 0, 1] = np.where(left, 0.1, -0.08)
    jac[..., 1, 0] = np.where(left, -0.05, 0.06)
    jac[..., 1, 1] = np.where(left, 1.05 + 2e-4 * y, 1.1)
    return np.stack([u, v], axis=2), jac


def _bilinear(tex: np.ndarray, m: int, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    n = tex.shape[0]
    x, y = u / 2**m - 0.5, v / 2**m - 0.5
    x0, y0 = np.floor(x), np.floor(y)
    fx, fy = (x - x0)[..., None], (y - y0)[..., None]
    xa, xb = np.clip(x0, 0, n - 1).astype(int), np.clip(x0 + 1, 0, n - 1).astype(int)
    ya, yb = np.clip(y0, 0, n - 1).astype(int), np.clip(y0 + 1, 0, n - 1).astype(int)
    top = tex[ya, xa] * (1 - fx) + tex[ya, xb] * fx
    return top * (1 - fy) + (tex[yb, xa] * (1 - fx) + tex[yb, xb] * fx) * fy


def sample(paint: np.ndarray, uv: np.ndarray, jac: np.ndarray) -> np.ndarray:
    """What a GPU shows: trilinear between mip 0 and mip 1 at the footprint's level of detail."""
    t0 = paint.astype(np.float32) / 255
    t1 = (t0[0::2, 0::2] + t0[1::2, 0::2] + t0[0::2, 1::2] + t0[1::2, 1::2]) / 4
    lod = np.log2(np.linalg.norm(jac, axis=2).max(axis=2))
    assert ((lod >= 0) & (lod < 1)).all()
    u, v = uv[..., 0], uv[..., 1]
    f = lod[..., None]
    return _bilinear(t0, 0, u, v) * (1 - f) + _bilinear(t1, 1, u, v) * f


def renderer(seed: int = 0):
    uv, jac = field()
    y, x = np.mgrid[0:H, 0:W].astype(np.float64)
    car = np.zeros((H, W), bool)
    car[BORDER:-BORDER, BORDER:-BORDER] = True
    white = (180 + 30 * np.sin(x / 23) + 8 * np.cos(y / 17))[..., None] * np.array([1.0, 0.95, 0.9])
    black = (18 + 6 * np.cos(x / 31))[..., None] * np.ones(3)
    highlight = (25 * np.exp(-((x - 60) ** 2 + (y - 40) ** 2) / 400))[..., None]
    rng = np.random.default_rng(seed)

    def shot(name: str) -> np.ndarray:
        if name == graycode.WHITE:
            albedo = np.ones((H, W, 3))
        elif name == graycode.BLACK:
            albedo = np.zeros((H, W, 3))
        else:
            albedo = sample(graycode.pattern(name, CANVAS), uv, jac)
        img = black + (white - black) * albedo + highlight + rng.integers(-1, 2, (H, W, 3))
        img = np.where(car[..., None], img, 70 + rng.integers(-1, 2, (H, W, 3)))
        return np.clip(np.round(img), 0, 255).astype(np.uint8)

    return shot, uv, jac, car


@pytest.fixture(scope="module")
def decoded():
    shot, uv, jac, car = renderer()
    return graycode.decode(shot), uv, jac, car


def test_a_known_uv_field_decodes_to_within_a_fifth_of_a_texel(decoded):
    vm, uv, jac, car = decoded

    assert np.array_equal(vm.car, car)
    assert vm.pix.sum() / car.sum() > 0.99
    err = np.abs(vm.uv - uv[vm.pix]).max(axis=1)
    assert np.percentile(err, 99) <= 0.2
    # Where the fit's window lies on one panel; at a panel's edge it is one-sided.
    r = graycode.RADIUS
    inner = np.zeros((H, W), bool)
    inner[BORDER + r : -BORDER - r, BORDER + r : -BORDER - r] = True
    inner[:, SEAM - r : SEAM + r] = False
    assert err[inner[vm.pix]].max() <= 0.2
    fitted = np.isfinite(vm.jac).all(axis=(1, 2))
    assert fitted.mean() > 0.99
    assert np.percentile(np.abs(vm.jac[fitted] - jac[vm.pix][fitted]), 95) < 0.05


def test_a_view_map_survives_a_save(decoded, tmp_path):
    vm = decoded[0]

    vm.save(tmp_path / "geometry.npz")
    back = graycode.ViewMap.load(tmp_path / "geometry.npz")

    for k in graycode.ViewMap.__dataclass_fields__:
        assert np.array_equal(getattr(back, k), getattr(vm, k), equal_nan=k in ("uv", "jac"))
    assert back.stats() == vm.stats()


def test_a_pixel_on_a_gray_boundary_decodes_to_the_boundary():
    # 1023 and 1024 differ only in Gray bit 10: a pixel that sees both reads bit 10 as unsure.
    code = 1023 ^ (1023 >> 1)
    planes = {}
    for k in range(graycode.BITS):
        on = (code >> k) & 1
        planes[k] = (
            np.array([0.0 if k == 10 else (100.0 if on else -100.0)]),
            np.array([0.0 if k == 10 else 1.0]),
        )

    u, level, _ = graycode.decode_axis(planes)

    assert u[0] == pytest.approx(1024.0)
    assert level[0] == 0


def test_every_texel_has_its_own_code_and_each_pattern_one_fine_bit():
    canvas = 1 << graycode.BITS
    codes = {axis: np.zeros(canvas, np.int64) for axis in "uv"}
    for name in graycode.names()[::2]:
        img = graycode.pattern(name, canvas)
        inv = graycode.pattern(name + "i", canvas)
        k = int(name.removeprefix("gray"))
        used = len(graycode.SCHEME[k])
        assert np.array_equal(img[..., :used], 255 - inv[..., :used])
        fine = [bit for _, bit in graycode.SCHEME[k] if bit <= 3]
        assert len(fine) == 1
        for c, (axis, bit) in enumerate(graycode.SCHEME[k]):
            row = img[0, :, c] if axis == "u" else img[:, 0, c]
            codes[axis] |= (row > 127).astype(np.int64) << bit
    for axis in "uv":
        assert np.array_equal(graycode.gray_to_bin(codes[axis]), np.arange(canvas))
