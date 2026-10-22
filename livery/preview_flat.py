"""Flat previews: the design on the texture and on the car, without the sim, and baked: at the preview views
in sim light, from the kit's calibration capture (livery.bake). Each is stamped with the build it shows
(livery.stamps)."""

import io
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import shapely
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from livery import body, carspace, stamps, ui_preview
from livery.config import Workspace
from livery.designs import build_livery
from livery.errors import LiveryError
from livery.kit import Kit, view_kind
from livery.params import parse_range
from livery.raster import rasterise

if TYPE_CHECKING:
    from livery.bake import Bake

VIEW_WIDTH = 900
BAKED_TILE = 1000  # a baked sheet tile: the five views in two columns
BACKGROUND = (34, 34, 38)


class StalePreview(LiveryError):
    pass


def _render(
    ws: Workspace, name: str, sets: dict[str, str] | None
) -> tuple[np.ndarray, Kit, dict[str, bytes]]:
    livery, kit = build_livery(ws, name, sets)
    outputs = rasterise(livery, kit, ws)
    return np.asarray(Image.open(io.BytesIO(outputs["paint.png"])).convert("RGB")), kit, outputs


def car_view(paint: np.ndarray, kit: Kit, view: str, width: int = VIEW_WIDTH) -> Image.Image:
    """The paint seen flat on one view of the car: each car-space pixel looked up through the kit's maps.

    Sides are drawn as seen (left side nose left, right side nose right), the top from above, nose left,
    and a face as seen from ahead or behind, at the scale of the others (`width` px per car length).
    """
    tx = view_texels(kit, view, width)
    out = np.empty((*tx.shape[:2], 3), np.uint8)
    out[:] = BACKGROUND
    on = tx[..., 0] >= 0
    out[on] = paint[tx[on][:, 1], tx[on][:, 0]]
    return Image.fromarray(out)


def view_texels(kit: Kit, view: str, width: int = VIEW_WIDTH) -> np.ndarray:
    """Per pixel of `car_view`, the template pixel it shows as (x, y); -1 where it shows no paint."""
    islands = np.asarray(Image.open(kit.dir / "overlays" / "islands.png"))
    if view_kind(view) == "face":
        a0, a1, b0, b1 = extent(kit, view)
        ka, kb = carspace.unit_lengths(kit, view)
        height = max(1, int(width * (b1 - b0) * kb))
        width = max(1, int(width * (a1 - a0) * ka))
        w, v = np.meshgrid(np.linspace(a0, a1, width), np.linspace(b1, b0, height))
        pts = np.stack([w.ravel(), v.ravel()], 1)
    elif view == "top":
        t = carspace.kit_data(kit)["top_transform"]
        height = int(width * 2 * abs(t["y_per_w"]) / t["x_per_u"])
        u, w = np.meshgrid(np.linspace(0, 1, width), np.linspace(-1, 1, height))
        pts = np.stack([u.ravel(), w.ravel()], 1)
    else:
        height = int(width * carspace.side_aspect(kit))
        u, v = np.meshgrid(np.linspace(0, 1, width), np.linspace(1, 0, height))
        pts = np.stack([u.ravel(), v.ravel()], 1)
    out = np.full((len(pts), 2), -1, np.int32)
    done = np.zeros(len(pts), bool)
    for pc in carspace.pieces(kit, view):
        todo = ~done
        if pc.car_area is not None:
            todo &= shapely.contains_xy(pc.car_area, pts[:, 0], pts[:, 1])
        if not todo.any():
            continue
        xy = pc.to_px(pts[todo])
        x = np.clip(np.rint(xy[:, 0]).astype(int), 0, kit.canvas - 1)
        y = np.clip(np.rint(xy[:, 1]).astype(int), 0, kit.canvas - 1)
        hit = shapely.contains_xy(pc.area, xy[:, 0], xy[:, 1]) & (islands[y, x] > 0)
        idx = np.nonzero(todo)[0][hit]
        out[idx] = np.stack([x[hit], y[hit]], 1)
        done[idx] = True
    out = out.reshape(height, width, 2)
    return out[:, ::-1] if view in ("side_r", "front") else out


def extent(kit: Kit, view: str) -> tuple[float, float, float, float]:
    """The car-space box a car view shows, (a0, a1, b0, b1) of its two coordinates: on a face, where its
    maps were measured."""
    kind = view_kind(view)
    if kind != "face":
        return (0.0, 1.0, -1.0, 1.0) if kind == "top" else (0.0, 1.0, 0.0, 1.0)
    areas = [pc.car_area for pc in carspace.pieces(kit, view) if pc.car_area is not None]
    if not areas:
        return -1.0, 1.0, 0.0, 1.0
    a0, b0, a1, b1 = shapely.union_all(areas).bounds
    return a0, a1, b0, b1


FLAT_VIEWS = ("side_l", "top", "side_r")
PAD = 12


def _shrunk(a: np.ndarray, s: float) -> np.ndarray:
    h, w = a.shape[:2]
    nh, nw = max(1, int(h * s)), max(1, int(w * s))
    return a[(np.arange(nh) * h // nh)][:, (np.arange(nw) * w // nw)]


def _stacked(parts: list[np.ndarray], faces: list[np.ndarray], fill) -> np.ndarray:
    """Views as views.png lays them out, PAD apart and around: the stacked views, then the faces side by
    side below them, smaller where side by side they'd be wider than the car."""
    width = parts[0].shape[1]
    across = sum(f.shape[1] for f in faces) + PAD * (len(faces) - 1)
    if faces and across > width:
        faces = [_shrunk(f, width / across) for f in faces]
    rows = sum(p.shape[0] for p in parts) + (max(f.shape[0] for f in faces) + PAD if faces else 0)
    sheet = np.empty((rows + PAD * 4, width + 2 * PAD, parts[0].shape[2]), parts[0].dtype)
    sheet[:] = fill
    y = PAD
    for p in parts:
        sheet[y : y + p.shape[0], PAD : PAD + width] = p
        y += p.shape[0] + PAD
    x = PAD
    for f in faces:
        sheet[y : y + f.shape[0], x : x + f.shape[1]] = f
        x += f.shape[1] + PAD
    return sheet


def views(paint: np.ndarray, kit: Kit, width: int = VIEW_WIDTH) -> Image.Image:
    """Left side, top, right side, stacked, and below them the faces the kit maps side by side."""
    parts = [np.asarray(car_view(paint, kit, v, width)) for v in FLAT_VIEWS]
    faces = [np.asarray(car_view(paint, kit, v, width)) for v in kit.face_views]
    return Image.fromarray(_stacked(parts, faces, BACKGROUND))


def views_texels(kit: Kit, width: int = VIEW_WIDTH) -> np.ndarray:
    """Per pixel of `views` (views.png), the template pixel it shows as (x, y); -1 where it shows no paint."""
    parts = [view_texels(kit, v, width) for v in FLAT_VIEWS]
    return _stacked(parts, [view_texels(kit, v, width) for v in kit.face_views], -1)


def car(ws: Workspace, paint: np.ndarray, kit: Kit) -> Image.Image | None:
    """The UI's front three-quarter view of the right side, when the install has one for this car."""
    layers = ui_preview.load(ws, kit.paint_folder)
    return None if layers is None else ui_preview.render(paint, layers)


def views_and_car(ws: Workspace, paint: np.ndarray, kit: Kit, width: int = VIEW_WIDTH) -> Image.Image:
    """The flat views with the car view in a column beside them."""
    v = views(paint, kit, width)
    c = car(ws, paint, kit)
    if c is None:
        return v
    both = Image.new("RGB", (v.width + c.width, max(v.height, c.height)), BACKGROUND)
    both.paste(v, (0, 0))
    both.paste(c, (v.width, 0))
    return both


def squint(img: Image.Image, width: int = 256) -> Image.Image:
    """Small and slightly blurred: what still reads at a distance and at speed."""
    small = img.resize((width, int(img.height * width / img.width)), Image.LANCZOS)
    return small.filter(ImageFilter.GaussianBlur(1.2))


OVERLAY_COLOURS = {"keep_clear": (255, 140, 0), "occluder": (255, 230, 0)}


def overlay(paint: np.ndarray, kit: Kit) -> Image.Image:
    """The texture with the wireframe, keep-clear zones (magenta) and shut lines (cyan) over it, and the
    body model's sim overlays (orange) and occluders (yellow), outlined and named."""
    img = Image.fromarray(paint).convert("RGBA")
    img.alpha_composite(Image.open(kit.dir / "overlays" / "wireframe.png").convert("RGBA"))
    for name, colour in (("keepclear.png", (255, 0, 200)), ("shutlines.png", (0, 220, 255))):
        p = kit.dir / "overlays" / name
        if p.exists():
            m = np.asarray(Image.open(p).convert("L")) > 127
            layer = np.zeros((*m.shape, 4), np.uint8)
            layer[m] = (*colour, 150)
            img.alpha_composite(Image.fromarray(layer))
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    font = _font(max(12, kit.canvas // 90))
    for kind, colour in OVERLAY_COLOURS.items():
        for f, foot in body.zones(kit, kind):
            for poly in getattr(foot, "geoms", [foot]):
                if isinstance(poly, shapely.Polygon) and not poly.is_empty:
                    draw.polygon(
                        list(poly.exterior.coords), fill=(*colour, 110), outline=(*colour, 255), width=3
                    )
            x0, y0, _, _ = foot.bounds
            draw.text((x0, y0 - 4), f.name, font=font, fill=(*colour, 255), anchor="ls")
    img.alpha_composite(layer)
    return img.convert("RGB")


def _bake(ws: Workspace, kit: Kit) -> "Bake":
    """The kit's capture, refused while stale or before it passed bake check."""
    from livery import bake, config

    return bake.load_for(config.load(), ws, kit)


def baked(
    ws: Workspace,
    name: str,
    outputs: dict[str, bytes],
    kit: Kit,
    sets: dict[str, str] | None = None,
    with_bake: "Bake | None" = None,
) -> Path:
    """baked/ (or baked-sets/<key>/): the build at every preview view in sim light and a sheet of them,
    stamped with the build and the capture."""
    from livery import bake

    b = with_bake or _bake(ws, kit)
    stamp = stamps.make(ws, name, outputs, sets) | bake.stamp(b, outputs)
    return bake.write_views(b, b.render_tgas(outputs), stamps.baked_dir(ws, name, sets), stamp)


def flat(ws: Workspace, name: str, sets: dict[str, str] | None = None, with_baked: bool = False) -> Path:
    """flat/, or flat-sets/<key>/ with --set values, stamped with the build it shows; `with_baked` also
    writes baked/ (or baked-sets/<key>/) of the same build."""
    paint, kit, outputs = _render(ws, name, sets)
    bake = _bake(ws, kit) if with_baked else None  # refused before anything is written
    out = stamps.flat_dir(ws, name, sets)
    out.mkdir(parents=True, exist_ok=True)
    (out / stamps.STAMP).unlink(missing_ok=True)  # no stamp until every picture is the new build's
    v = views(paint, kit)
    v.save(out / "views.png")
    squint(v).save(out / "squint.png")
    overlay(paint, kit).resize((1024, 1024), Image.LANCZOS).save(out / "overlay.png")
    (out / "car.png").unlink(missing_ok=True)
    if (c := car(ws, paint, kit)) is not None:
        c.save(out / "car.png")
    stamps.write(out / stamps.STAMP, stamps.make(ws, name, outputs, sets))
    if bake is not None:
        baked(ws, name, outputs, kit, sets, bake)
    return out


def fresh(ws: Workspace, name: str, digests: dict[str, str]) -> Path:
    """flat/ as the build with these digests, rendered again unless its stamp already shows that build."""
    out = stamps.flat_dir(ws, name)
    if stamps.shows(stamps.read(out / stamps.STAMP), digests):
        return out
    flat(ws, name)
    if not stamps.shows(stamps.read(out / stamps.STAMP), digests):
        raise StalePreview(f"{name}'s script no longer renders the checked build; run livery build {name}")
    return out


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(Workspace().fonts / "barlow" / "Barlow-Bold.ttf"), size)


def _tile(images: list[tuple[str, Image.Image]], cols: int) -> Image.Image:
    w = max(i.width for _, i in images)
    h = max(i.height for _, i in images) + 30
    rows = -(-len(images) // cols)
    sheet = Image.new("RGB", (w * cols, h * rows), BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    for k, (label, img) in enumerate(images):
        x, y = (k % cols) * w, (k // cols) * h
        draw.text((x + 10, y + 4), label, font=_font(20), fill=(235, 235, 235))
        sheet.paste(img, (x, y + 30))
    return sheet


def _stamp_sheet(path: Path, stamp: dict) -> None:
    """A sheet's stamp sits beside it: <sheet>.build.json."""
    stamps.write(path.with_suffix(".build.json"), stamp)


def _range(
    ws: Workspace,
    name: str,
    key: str,
    values: list[str],
    sets: dict[str, str] | None,
    out: Path,
    with_baked: bool = False,
) -> Path:
    images, tiles = [], []
    bake, extra = None, {}
    for value in values:
        paint, kit, outputs = _render(ws, name, {**(sets or {}), key: value})
        if with_baked:
            from livery import bake as bakes

            bake = bake or _bake(ws, kit)
            extra = bakes.stamp(bake)
            images.append((f"{key}={value}", bakes.sheet(bake, bake.render_tgas(outputs), BAKED_TILE)))
        else:
            images.append((f"{key}={value}", views_and_car(ws, paint, kit, 600)))
        tiles.append({"set": {key: value}, "digests": stamps.digests(outputs)})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".build.json").unlink(missing_ok=True)
    _tile(images, cols=min(4, len(images))).save(out)
    _stamp_sheet(out, stamps.make(ws, name, {}, sets) | {"digests": None, "tiles": tiles} | extra)
    return out


def _sheet_name(stem: str, with_baked: bool) -> str:
    return f"{stem}-baked.png" if with_baked else f"{stem}.png"


def wedge(
    ws: Workspace, name: str, spec: str, sets: dict[str, str] | None = None, with_baked: bool = False
) -> Path:
    """One flat render per value of a param, or one baked render with `with_baked`: `spec` is
    `k=a..b..step`."""
    key, rng = spec.split("=", 1)
    out = ws.out(name) / _sheet_name(f"wedge-{key}", with_baked)
    return _range(ws, name, key, parse_range(rng), sets, out, with_baked)


def seeds(
    ws: Workspace, name: str, spec: str, sets: dict[str, str] | None = None, with_baked: bool = False
) -> Path:
    """One flat render per seed, or one baked render with `with_baked`: `spec` is `a..b`."""
    out = ws.out(name) / _sheet_name("seeds", with_baked)
    return _range(ws, name, "seed", parse_range(spec), sets, out, with_baked)


def contact_sheet(ws: Workspace, names: list[str]) -> Path:
    """Several designs side by side: flat views and the car view, and the side shot of the latest sim run of
    the same build, or of the latest run, labelled as an older build, when none is."""
    images, tiles = [], {}
    for name in names:
        paint, kit, outputs = _render(ws, name, None)
        img = views_and_car(ws, paint, kit, 600)
        shown = stamps.digests(outputs)
        run = stamps.matching_run(ws, name, shown)
        label = name
        if run is None and (older := [r for r in stamps.runs(ws, name) if (r / "side_l.png").exists()]):
            run, label = older[-1], f"{name} (sim: an older build)"
        if run is not None and (run / "side_l.png").exists():
            shot = Image.open(run / "side_l.png").convert("RGB")
            shot.thumbnail((img.width, 400))
            both = Image.new("RGB", (img.width, img.height + shot.height + 10), BACKGROUND)
            both.paste(img, (0, 0))
            both.paste(shot, (0, img.height + 10))
            img = both
        images.append((label, img))
        tiles[name] = stamps.make(ws, name, outputs) | {
            "sim": run.relative_to(ws.root).as_posix() if run else None
        }
    out = ws.root / ".cache" / "sheets"
    out.mkdir(parents=True, exist_ok=True)
    path = out / ("sheet-" + "-".join(names) + ".png")
    path.with_suffix(".build.json").unlink(missing_ok=True)
    _tile(images, cols=min(4, len(images))).save(path)
    _stamp_sheet(path, {"designs": tiles})
    return path
