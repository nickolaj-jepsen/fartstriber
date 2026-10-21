import io
import os
import struct

import numpy as np
from PIL import Image
from zstandard import ZstdCompressor

from livery import ui_preview
from livery.config import Workspace

W, H = 8, 4  # frame size; the UV map is twice that
BODY = slice(2, 6)  # frame columns the paint shows on
GREY = (40, 40, 40)
COLOURS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)]


def _tga(a: np.ndarray) -> bytes:
    b = io.BytesIO()
    Image.fromarray(a).save(b, format="TGA")
    return b.getvalue()


def _size_set(w: int, h: int, body_colours) -> list[bytes]:
    """Pattern frames that differ on the body, ten fixed layers, a UV map and a descriptor."""
    body = slice(w // 4, 3 * w // 4)
    frames = []
    for colour in body_colours:
        f = np.full((h, w, 3), GREY, np.uint8)
        f[:, body] = colour
        frames.append(f)
    fixed = [np.full((h, w, 3), 90, np.uint8) for _ in range(ui_preview.FIXED)]
    fixed[ui_preview.SHADE][:] = 200
    fixed[ui_preview.ALPHA][:, -1] = 0
    fixed[ui_preview.ALPHA][:, :-1] = 255
    # Body texel (row, col) of a 4x4 texture, one per frame pixel; 0 off the body.
    uv = np.zeros((2 * h, 2 * w, 4), np.uint8)
    for y in range(2 * h):
        for x in range(2 * w):
            col = x // 2 - body.start
            if 0 <= col < body.stop - body.start:
                u16 = int((col + 0.5) / 4 * 65536)
                v16 = int((y // 2 + 0.5) / 4 * 65536)
                uv[y, x] = (v16 & 255, u16 >> 8, u16 & 255, v16 >> 8)
    return [_tga(f) for f in frames + fixed] + [_tga(uv), b"WebUVDescriptor\x01\x02\x03"]


def _write_dat(path, body_colours=((255, 0, 0), (0, 0, 255))) -> None:
    out = bytearray(ui_preview.FILE_HEADER)
    for chunk in _size_set(W // 2, H // 2, body_colours) + _size_set(W, H, body_colours):
        z = ZstdCompressor().compress(chunk)
        out += bytes(ui_preview.RECORD_PREFIX)
        out += struct.pack("<IIII", 0, ui_preview.BLOCK, 0, len(z)) + z
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))


def _ws(tmp_path) -> Workspace:
    return Workspace(root=tmp_path, ui_cars_dir=tmp_path / "httproot")


def _paint(colour_of) -> np.ndarray:
    paint = np.zeros((4, 4, 3), np.uint8)
    for r in range(4):
        for c in range(4):
            paint[r, c] = colour_of(r, c)
    return paint


def test_no_preview_for_the_car_gives_none(tmp_path):
    assert ui_preview.load(_ws(tmp_path), "nocar") is None


def test_paint_lands_where_the_uv_map_points(tmp_path):
    ws = _ws(tmp_path)
    _write_dat(ws.ui_cars / "car" / "car.dat")
    img = np.asarray(ui_preview.render(_paint(lambda r, c: COLOURS[(r + c) % 4]), ui_preview.load(ws, "car")))
    for r in range(H):
        for c in range(4):
            px = img[r, BODY.start + c].astype(int)
            want = COLOURS[(r + c) % 4]
            if want != (255, 255, 255):
                assert px.argmax() == int(np.argmax(want))


def test_unpainted_parts_and_background_keep_their_colour(tmp_path):
    ws = _ws(tmp_path)
    _write_dat(ws.ui_cars / "car" / "car.dat")
    img = np.asarray(ui_preview.render(_paint(lambda r, c: (0, 0, 255)), ui_preview.load(ws, "car")))
    assert (img[:, : BODY.start] == GREY).all()
    assert (img[:, BODY.stop : -1] == GREY).all()
    assert (img[:, -1] == ui_preview.BACKGROUND).all()


def test_body_pixels_the_uv_map_misses_take_their_neighbours_paint(tmp_path):
    ws = _ws(tmp_path)
    _write_dat(ws.ui_cars / "car" / "car.dat")
    layers = ui_preview.load(ws, "car")
    uv = layers.uv.copy()
    uv[:, 2 * (BODY.start + 2) : 2 * (BODY.start + 3)] = 0  # as at real panel edges
    layers = ui_preview.Layers(**{**layers.__dict__, "uv": uv})
    paint = _paint(lambda r, c: (255, 0, 0) if (r, c) == (0, 0) else (0, 0, 255))
    img = np.asarray(ui_preview.render(paint, layers)).astype(int)
    blue = img[:, BODY.start + 1 : BODY.stop]
    assert (blue[..., 0] <= blue[..., 2]).all()


def test_a_new_install_version_is_extracted_again(tmp_path):
    ws = _ws(tmp_path)
    dat = ws.ui_cars / "car" / "car.dat"
    _write_dat(dat)
    first = ui_preview.load(ws, "car")
    assert np.array_equal(ui_preview.load(ws, "car").base, first.base)
    _write_dat(dat, body_colours=((0, 255, 0), (0, 0, 255)))
    os.utime(dat, ns=(dat.stat().st_atime_ns, dat.stat().st_mtime_ns + 10**9))
    assert not np.array_equal(ui_preview.load(ws, "car").base, first.base)
