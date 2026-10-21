"""`livery kit review`: labelled overlays and the calibration livery in the sim, for kit approval."""

import colorsys
import os
from pathlib import Path
from typing import cast

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

from livery import body, carspace
from livery import kit as kits
from livery.calibration import calibration
from livery.check import digests
from livery.config import Config, Shot, Workspace
from livery.hashing import kit_hash
from livery.kit import Kit
from livery.raster import rasterise
from livery.simsession import SimSession, fresh_name

VIEW_COLOURS = {"side_l": (230, 80, 60), "side_r": (60, 120, 230), "top": (60, 180, 90)}
OTHER = (150, 150, 150)


def out_dir(ws: Workspace, kit: Kit) -> Path:
    d = ws.cache / "kit-review" / kit.name
    d.mkdir(parents=True, exist_ok=True)
    return d


def overlay(kit: Kit, ws: Workspace) -> Path:
    """Islands tinted by view (left red, right blue, top green, unmapped grey) with ids, over the wire."""
    raw = kits.read_meta(kit.dir) | kits.read_maps(kit.dir)
    islands = np.asarray(Image.open(kit.dir / "overlays" / "islands.png"))
    mapped = {rid: t["view"] for rid, t in raw.get("side_transforms", {}).items()}
    on_top = set(raw.get("top_transform", {}).get("sides", {}))  # parts only the top view maps
    lut = np.zeros((int(islands.max()) + 1, 3), np.uint8)
    for r in raw["regions"]:
        view = mapped.get(r["id"]) or ("top" if r["view"] == "top" or r["id"] in on_top else None)
        lut[int(r["id"][1:])] = VIEW_COLOURS.get(view, OTHER)
    img = Image.fromarray(lut[islands]).convert("RGBA")
    img.alpha_composite(Image.open(kit.dir / "overlays" / "wireframe.png").convert("RGBA"))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(str(ws.fonts / "barlow" / "Barlow-Bold.ttf"), 22)
    for r in raw["regions"]:
        if r["area"] >= 3000:
            x0, y0, x1, y1 = r["bbox"]
            draw.text(((x0 + x1) / 2, (y0 + y1) / 2), r["id"], font=font, fill=(0, 0, 0), anchor="mm")
    p = out_dir(ws, kit) / "overlay.png"
    img.convert("RGB").save(p)
    return p


# The body model's pictures: every part tinted and named, on the texture (which holds the parts no view maps)
# and on the car views (where a name is checked against the body), with the features drawn on both.

UNSURE = (255, 60, 60)  # an unsure part's or feature's name; the rest are in black or the feature's colour
FEATURE_COLOURS = {
    "keep_clear": (255, 140, 0),
    "occluder": (255, 230, 0),
    "opening": (0, 0, 0),
    "zone": (255, 0, 200),
    "line": (0, 200, 255),
    "place": (40, 40, 220),
}
NO_PART = (90, 90, 90)


def _font(ws: Workspace, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(ws.fonts / "barlow" / "Barlow-Bold.ttf"), size)


def _hues(n: int) -> np.ndarray:
    """n distinct light colours, neighbours in the list far apart in hue."""
    return np.array(
        [[round(255 * c) for c in colorsys.hsv_to_rgb((k * 0.618034) % 1.0, 0.45, 0.95)] for k in range(n)],
        np.uint8,
    )


def _part_index(kit: Kit) -> tuple[np.ndarray, list]:
    """Each texture pixel's part, as 1 + its index in the list (0: an island no part names, -1: no island)."""
    parts = [p for p in kit.parts().values() if not p.group]
    islands = np.asarray(Image.open(kit.dir / "overlays" / "islands.png"))
    idx = np.where(islands > 0, 0, -1).astype(np.int32)
    for k, p in enumerate(parts):
        img = Image.new("1", (kit.canvas, kit.canvas), 0)
        draw = ImageDraw.Draw(img)
        for poly in getattr(area := body.area(kit, p.name), "geoms", [area]):
            if hasattr(poly, "exterior") and not poly.is_empty:
                draw.polygon(list(poly.exterior.coords), fill=1)
                for hole in poly.interiors:
                    draw.polygon(list(hole.coords), fill=0)
        idx[np.asarray(img, bool)] = k + 1
    return idx, parts


def _label(draw: ImageDraw.ImageDraw, xy, text: str, font, fill) -> None:
    draw.text(xy, text, font=font, fill=fill, anchor="mm", stroke_width=2, stroke_fill=(255, 255, 255))


def _name(item) -> str:
    return item.name + ("?" if item.confidence == "unsure" else "")


def _texture_features(kit: Kit, draw: ImageDraw.ImageDraw, font) -> None:
    for f in kit.features().values():
        if f.kind not in FEATURE_COLOURS:
            continue
        colour = FEATURE_COLOURS[f.kind]
        if f.shape in ("point", "line"):
            px: list[tuple[str, tuple[float, float]] | None] = []
            for p in body.samples(f):
                try:
                    pc, xy, _ = carspace.anchor(kit, f.view, p)
                    px.append((pc.island, (float(xy[0]), float(xy[1]))))
                except ValueError:
                    px.append(None)
            runs: list[list[tuple[float, float]]] = [[]]
            last = None
            for q in px:  # a line drawn on one island at a time: it jumps in the texture at a seam
                if q is None or (last is not None and q[0] != last):
                    runs.append([])
                if q is not None:
                    runs[-1].append(q[1])
                last = None if q is None else q[0]
            for run in runs:
                if len(run) >= 2:
                    draw.line(run, fill=(*colour, 255), width=4)
                elif len(run) == 1:
                    x, y = run[0]
                    draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=(*colour, 255))
            on = [q[1] for q in px if q is not None]
            where = on[len(on) // 2] if on else None
        else:
            foot = body.footprint(kit, f)
            for poly in getattr(foot, "geoms", [foot]):
                if hasattr(poly, "exterior") and not poly.is_empty:
                    draw.polygon(list(poly.exterior.coords), outline=(*colour, 255), width=3)
            where = None if foot.is_empty else (foot.bounds[0], foot.bounds[1] - 12)
        if where is not None:
            draw.text(
                where, _name(f), font=font, fill=(*(UNSURE if f.confidence == "unsure" else colour), 255)
            )


def _texture_picture(kit: Kit, ws: Workspace, idx: np.ndarray, parts: list) -> Image.Image:
    lut = np.vstack([[NO_PART], _hues(len(parts))]).astype(np.uint8)
    rgb = np.zeros((*idx.shape, 3), np.uint8)
    rgb[idx >= 0] = lut[idx[idx >= 0]]
    img = Image.fromarray(rgb).convert("RGBA")
    if (wire := kit.dir / "overlays" / "wireframe.png").exists():
        img.alpha_composite(Image.open(wire).convert("RGBA"))
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    _texture_features(kit, draw, _font(ws, 18))
    img.alpha_composite(layer)
    draw = ImageDraw.Draw(img)
    for p in parts:
        a = body.area(kit, p.name)
        if a.is_empty:
            continue
        big = max(getattr(a, "geoms", [a]), key=lambda g: g.area)
        q = big.representative_point()
        size = int(np.clip(np.sqrt(big.area) / 6, 14, 30))
        _label(draw, (q.x, q.y), _name(p), _font(ws, size), UNSURE if p.confidence == "unsure" else (0, 0, 0))
    return img.convert("RGB")


def _view_picture(
    kit: Kit, ws: Workspace, idx: np.ndarray, parts: list, view: str, width: int
) -> Image.Image:
    """One car view tinted by part and named, with the view's features drawn in car space."""
    from livery import preview_flat

    code = np.zeros((*idx.shape, 3), np.uint8)  # each pixel's part through the car view's lookup
    code[..., 0], code[..., 1] = (idx.clip(0) >> 8) & 255, idx.clip(0) & 255
    code[..., 2] = np.where(idx >= 0, 255, 0)
    seen = np.asarray(preview_flat.car_view(code, kit, view, width))
    on = seen[..., 2] == 255
    which = np.where(on, seen[..., 0].astype(np.int32) * 256 + seen[..., 1], -1)
    lut = np.vstack([[NO_PART], _hues(len(parts))]).astype(np.uint8)
    rgb = np.full((*which.shape, 3), preview_flat.BACKGROUND, np.uint8)
    rgb[on] = lut[which[on]]
    img = Image.fromarray(rgb).convert("RGBA")
    h, w = which.shape
    flip = view == "side_r"

    def to_px(p) -> tuple[float, float]:
        u, a = p
        x = (1 - u if flip else u) * (w - 1)
        y = (a + 1) / 2 * (h - 1) if view == "top" else (1 - a) * (h - 1)
        return (x, y)

    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    font = _font(ws, 14)
    for f in kit.features().values():
        if f.view != view or f.kind not in FEATURE_COLOURS:
            continue
        colour = FEATURE_COLOURS[f.kind]
        g = f.geometry()
        if f.shape == "point":
            x, y = to_px(f.at)
            draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(*colour, 255))
        elif f.shape == "line":
            draw.line([to_px(p) for p in g.coords], fill=(*colour, 255), width=2)
        else:
            for poly in getattr(g, "geoms", [g]):
                draw.polygon([to_px(p) for p in poly.exterior.coords], outline=(*colour, 255), width=2)
        x, y = to_px(f.at)
        fill = UNSURE if f.confidence == "unsure" else colour
        draw.text((x, y - 9), _name(f), font=font, fill=(*fill, 255), anchor="mb")
    img.alpha_composite(layer)
    draw = ImageDraw.Draw(img)
    for k, p in enumerate(parts):
        mask = which == k + 1
        if mask.sum() < 40:
            continue
        lab, n = cast(tuple[np.ndarray, int], ndimage.label(mask))
        sizes = ndimage.sum(mask, lab, range(1, n + 1))
        biggest = lab == int(np.argmax(sizes)) + 1
        # The label's spot: the component's pixel farthest from its edge.
        dist = np.asarray(ndimage.distance_transform_edt(biggest))
        y, x = np.unravel_index(int(np.argmax(dist)), dist.shape)
        _label(draw, (x, y), _name(p), font, UNSURE if p.confidence == "unsure" else (0, 0, 0))
    return img.convert("RGB")


def body_overlay(kit: Kit, ws: Workspace, width: int = 1800) -> list[Path]:
    """The body model's two pictures, `parts.png` (the texture) and `body.png` (the car views): each part
    tinted and named, unsure names in red with a '?', and the features drawn in their kind's colour."""
    if not kit.parts() and not kit.features():
        return []
    idx, parts = _part_index(kit)
    d = out_dir(ws, kit)
    texture = d / "parts.png"
    _texture_picture(kit, ws, idx, parts).save(texture)
    shown = [v for v in ("side_l", "top", "side_r") if v in kit.views and carspace.pieces(kit, v)]
    views = [_view_picture(kit, ws, idx, parts, v, width) for v in shown]
    pad, head = 12, 26
    height = sum(v.height + head for v in views) + pad * 2 + head
    sheet = Image.new("RGB", (width + 2 * pad, height), (34, 34, 38))
    draw = ImageDraw.Draw(sheet)
    x = pad
    for kind, colour in (*FEATURE_COLOURS.items(), ("unsure ?", UNSURE)):
        draw.text((x, pad), kind, font=_font(ws, 18), fill=colour if colour != (0, 0, 0) else (200, 200, 200))
        x += int(draw.textlength(kind, font=_font(ws, 18))) + 24
    y = pad + head
    for name, v in zip(shown, views, strict=True):
        draw.text((pad, y), name, font=_font(ws, 20), fill=(235, 235, 235))
        sheet.paste(v, (pad, y + head))
        y += v.height + head
    cars = d / "body.png"
    sheet.save(cars)
    return [texture, cars]


def calibration_shots(ws: Workspace, kit: Kit) -> dict[str, Shot]:
    """The preview shots, which the designs are judged in; the side views see each side square-on, where a
    step across a seam shows best."""
    return kits.profile(kit).shots(ws.policy.preview)


def calibration_build(ws: Workspace, kit: Kit) -> dict[str, bytes]:
    """The calibration livery built on the kit: every build output, by file name."""
    if not kits.read_maps(kit.dir).get("side_transforms"):
        raise kits.KitError(
            f"{kit.name} has no fitted maps to draw the calibration through: livery kit fit {kit.name}"
        )
    return rasterise(calibration(kit), kit, ws)


def shoot_calibration(cfg: Config, kit: Kit, ws: Workspace, log=print) -> Path:
    """Build the calibration livery on the kit, install it as the replay car's paint, and capture every shot.

    It paints only the kit's car: the run waits for a replay whose car is the kit's, and refuses one that
    isn't. The shots replace the ones the kit review shows only once every one is taken.
    """
    prof = kits.profile(kit)
    replay = kits.require_replay(prof, cfg, kit)
    kits.check_model(kit, cfg.ui_cars)
    shots = calibration_shots(ws, kit)
    outputs = calibration_build(ws, kit)
    paint, spec = outputs["car.tga"], outputs["car_spec.tga"]
    d = out_dir(ws, kit)
    groups = {s.group for s in shots.values()}
    with SimSession(
        cfg,
        f"kit review {kit.name}",
        paint_folder=kit.paint_folder,
        groups=groups,
        replay=replay,
        log=log,
        window=ws.policy.window,
        checkout=ws.root,
    ) as s:
        s.out = d / f"run-{fresh_name(d)}"
        s.out.mkdir()
        s.manifest.update(
            kit_hash=kit_hash(kit.dir), digests=digests({"car.tga": paint, "car_spec.tga": spec})
        )
        paint_name, spec_name = s.paint_names
        s.put({paint_name: paint, spec_name: spec})
        log(f"reload: {s.reload(next(iter(shots.values())))}")
        for name, shot in shots.items():
            Image.fromarray(s.shoot(name, shot, check_ui=True)).save(s.out / f"calibration_{name}.png")
            log(f"shot {name}")
    for f in [*s.out.glob("calibration_*.png"), s.out / "manifest.json"]:
        os.replace(f, d / f.name)
    s.out.rmdir()
    return d
