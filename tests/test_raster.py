from dataclasses import replace

import numpy as np
from PIL import Image

from livery.kit import load
from livery.model import Material
from livery.raster import bleed, compensate


def test_bleed_copies_island_colours_outward_up_to_the_width(tmp_path):
    mask = np.zeros((20, 20), np.uint8)
    mask[:, :10] = 255
    path = tmp_path / "mask.png"
    Image.fromarray(mask).save(path)
    img = np.zeros((20, 20, 3), np.uint8)
    img[:, :10] = (200, 50, 10)

    out = bleed(img, path, 3)

    assert (out[:, 10:13] == (200, 50, 10)).all()
    assert (out[:, 13:] == 0).all()
    assert (out[:, :10] == img[:, :10]).all()


def test_bleed_repaints_the_antialiased_rim_from_the_interior(tmp_path):
    mask = np.zeros((20, 20), np.uint8)
    mask[:, :10] = 255
    path = tmp_path / "mask.png"
    Image.fromarray(mask).save(path)
    img = np.zeros((20, 20, 3), np.uint8)
    img[:, :10] = (200, 50, 10)
    img[:, 9] = (230, 200, 190)  # shape blended with a white base at the island edge

    out = bleed(img, path, 3)

    assert (out[:, 8:13] == (200, 50, 10)).all()


def test_paint_under_a_metal_finish_takes_its_measured_lookup_and_the_rest_is_left_alone(ws):
    kit = load(ws, "tiny")
    # Metallic shows half as bright: to show 100 it needs 200, and it can't show more than 127.
    lut = {"target": [0, 100, 255], "paint": [[0, 0, 0], [200, 200, 200], [255, 255, 255]]}
    kit = replace(kit, compensation={Material.metallic: {**lut, "reachable": [127, 127, 127]}})
    paint = np.full((6, 4, 3), 100, np.uint8)
    spec = np.zeros((6, 4, 3), np.uint8)
    spec[:2] = kit.spec[Material.metallic]
    spec[2:4] = kit.spec[Material.chrome]  # no lookup of its own: the nearest metal finish's
    spec[4:, :, 1:] = kit.spec[Material.gloss][1:]

    out = compensate(paint, spec, kit)

    assert (out[:4] == 200).all()
    assert (out[4:] == 100).all()
