"""Car kits: what the build needs to know about one car's paint template, and how the sim shows the car.

A kit is `kits/<kit>/`, written only by the `livery kit` commands: `kit.yaml` (the template's canvas and
paint folder, the views it maps, bleed, spec presets and their compensation, keep-clear boxes, its version
and the fingerprint of the files it came from), `maps.json` (the islands and the maps `kit fit` measured),
`body.yaml` (named parts and features, `livery.body`), `regions.svg`, `seams.json` and `overlays/`. Four more
files are not build inputs: `profile.toml`, how the sim shows the car on the preview replay,
`fit-report.json`, the last fit's report, `review.yaml`, what the kit review asks the owner, and
`approved.json`, the owner's accept of the kit as it is.
"""

import json
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from livery.config import Config, Replay, Shot, Workspace, sha256, track_path
from livery.errors import LiveryError
from livery.model import Material

if TYPE_CHECKING:
    from livery.body import Feature, Part

# (metallic, roughness, clearcoat) per material; provisional until judged in the sim (how they render:
# docs/sim.md, Rendering).
DEFAULT_SPEC = {
    Material.gloss: (0, 30, 255),
    Material.satin: (0, 110, 64),  # a thin clearcoat: with a full one satin reads as gloss (docs/sim.md)
    Material.matte: (0, 220, 0),
    Material.metallic: (255, 60, 255),
    Material.chrome: (255, 0, 255),
}
DEFAULT_BLEED_PX = 16  # seam lines show at 0 px, faintly at 8, not at 16 (docs/sim.md)
DEFAULT_VIEWS = ("side_l", "side_r", "top")  # the plan view is "top"; every other view is a side
META = "kit.yaml"
MAPS = "maps.json"
PROFILE = "profile.toml"
APPROVAL = "approved.json"
REVIEW = "review.yaml"
FIT_REPORT = "fit-report.json"
NOT_INPUTS = (
    PROFILE,
    REVIEW,
    APPROVAL,
    FIT_REPORT,
)  # what the sim, fit and review keep in a kit; a build reads none
LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
META_ORDER = ("version", "fingerprint", "canvas", "paint_folder", "mirror_axis_y", "views", "keep_clear")


class KitError(LiveryError):
    """A kit missing what a command needs, or made from other files than the sim now has."""


@dataclass(frozen=True)
class Kit:
    name: str
    dir: Path
    canvas: int
    paint_folder: str
    bleed_px: int
    spec: dict[Material, tuple[int, int, int]]
    views: tuple[str, ...] = DEFAULT_VIEWS
    version: int = 0
    # Per preset the build brightens (`livery kit spec`): the paint value, per channel, that shows each
    # `target` grey as gloss shows it, and the brightest grey the finish can show at all.
    compensation: dict[Material, dict] = field(default_factory=dict)

    @property
    def mask_path(self) -> Path:
        """White on the islands, the texture the car shows. May not exist for a kit without one."""
        return self.dir / "overlays" / "mask.png"

    @property
    def side_views(self) -> tuple[str, ...]:
        return tuple(v for v in self.views if v != "top")

    # The body model (`livery.body`, from body.yaml): parts by name, and features of the body.

    def part(self, name: str) -> "Part":
        """A part or group by name: its islands, side, how sure the labelling was, and its cut."""
        from livery import body

        return body.part(self, name)

    def parts(self) -> "dict[str, Part]":
        from livery import body

        return dict(body.read(self.dir).parts)

    def feature(self, name: str) -> "Feature":
        """A feature by name (or another name for it): its view, kind and car-space geometry."""
        from livery import body

        return body.feature(self, name)

    def features(self) -> "dict[str, Feature]":
        from livery import body

        return dict(body.read(self.dir).features)


def read_meta(kit_dir: Path) -> dict:
    return yaml.load((kit_dir / META).read_text(encoding="utf-8"), Loader=LOADER) or {}


def read_maps(kit_dir: Path) -> dict:
    """The islands (`regions`) and fitted maps; empty for a kit not yet extracted."""
    p = kit_dir / MAPS
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def load(ws: Workspace, name: str) -> Kit:
    d = ws.kits / name
    if not (d / META).exists():
        raise KitError(f"no kit at {d}")
    raw = read_meta(d)
    try:
        return Kit(
            name=name,
            dir=d,
            canvas=int(raw["canvas"]),
            paint_folder=raw["paint_folder"],
            bleed_px=int(raw["bleed_px"]),
            spec={Material(k): tuple(v) for k, v in raw["spec_presets"].items()},
            views=tuple(raw["views"]),
            version=int(raw["version"]),
            compensation={Material(k): v for k, v in (raw.get("compensation") or {}).items()},
        )
    except KeyError as e:
        raise KitError(f"{d / META} has no {e.args[0]}") from None


def dumps(value, depth: int = 0) -> str:
    """JSON with one line per entry for the first two levels, so a refit shows per island in a diff."""
    if depth >= 2 or not isinstance(value, dict | list) or not value:
        return json.dumps(value, separators=(",", ":"))
    pad = "\n" + " " * (depth + 1)
    if isinstance(value, dict):
        items = [f"{json.dumps(k)}: {dumps(v, depth + 1)}" for k, v in value.items()]
    else:
        items = [dumps(v, depth + 1) for v in value]
    o, c = "{}" if isinstance(value, dict) else "[]"
    return o + pad + ("," + pad).join(items) + "\n" + " " * depth + c


def write(kit_dir: Path, meta: dict | None = None, maps: dict | None = None) -> None:
    """Writes kit.yaml and maps.json, whichever is given. Only the kit commands call it."""
    kit_dir.mkdir(parents=True, exist_ok=True)
    if meta is not None:
        ordered = {k: meta[k] for k in META_ORDER if k in meta} | {k: v for k, v in meta.items()}
        text = yaml.safe_dump(ordered, sort_keys=False, default_flow_style=None, width=200)
        (kit_dir / META).write_text(text, encoding="utf-8", newline="\n")
    if maps is not None:
        (kit_dir / MAPS).write_text(dumps(maps) + "\n", encoding="utf-8", newline="\n")


# Fingerprints: the template and the car model a kit was made from. iRacing updates either with a new
# build of the car; a kit made from the old ones maps paint to the wrong places.


def fingerprint(path: Path) -> dict:
    return {"file": path.name, "size": path.stat().st_size, "sha256": sha256(path)}


def model_path(ui_cars: Path, paint_folder: str) -> Path:
    """The car's model in the UI's httproot (`livery.ui_preview` reads it), from the same build of the car as
    the sim's."""
    return ui_cars / paint_folder / f"{paint_folder}.dat"


def check_model(kit: Kit, ui_cars: Path) -> None:
    """Refuse a kit made from another build of the car than the one installed."""
    was = read_meta(kit.dir).get("fingerprint", {}).get("model")
    if not was:
        return
    dat = model_path(ui_cars, kit.paint_folder)
    if not dat.exists():
        raise KitError(f"the car's model {dat} is missing; is the car installed?")
    if dat.stat().st_size != was["size"] or fingerprint(dat)["sha256"] != was["sha256"]:
        raise KitError(
            f"iRacing updated the {kit.paint_folder} model since {kit.name} was made ({dat}); "
            f"extract the kit from the new template, then capture and fit it again"
        )


# The sim profile: how the preview replay shows this car.


@dataclass(frozen=True)
class View:
    """A camera view of the car in the sim: its camera group and the camera's pose in the car's frame."""

    group: str
    pose: tuple[float, ...]  # x forward, y left, z up (metres from the car's origin), yaw, pitch, FOV (rad)
    aim: float = 0.0  # a preview camera's field 9: how far its aim point is raised


@dataclass(frozen=True)
class CarSize:
    """How long and tall the car's paint is, seen square-on from its side, in metres: what a frame holds."""

    length: float
    height: float


@dataclass(frozen=True)
class Profile:
    replay: int  # the SubSessionID of the replay its frame belongs to
    frame: int | None  # the replay frame every view is shot at: on a straight, with a clear line from each
    # camera; None until `livery sim pick-frame` picks one
    preview: dict[str, View]
    crops: dict[str, tuple]  # the critic's close crops: view, x0, y0, x1, y1 in the shot's pixels
    track_name: str | None = None  # the replay's WeekendInfo TrackName, unless it is your preview replay
    car: CarSize | None = None  # the car as `pick-frame` measured it, when its cameras are sized for it

    def shot(self, view: str) -> Shot:
        if view not in self.preview:
            raise KitError(f"the kit's profile has no preview view {view}")
        if self.frame is None:
            raise KitError("the kit's profile has no frame yet: livery sim pick-frame <kit> picks one")
        return Shot(self.frame, self.preview[view].group)

    def shots(self, views) -> dict[str, Shot]:
        return {v: self.shot(v) for v in views}


def _views(raw: dict) -> dict[str, View]:
    """Views by name; one without a pose gets the default one `preview cameras` sizes for the car."""
    return {
        name: View(str(v["group"]), tuple(float(x) for x in v.get("pose", ())), float(v.get("aim", 0.0)))
        for name, v in raw.items()
    }


def profile(kit: Kit) -> Profile:
    p = kit.dir / PROFILE
    if not p.exists():
        raise KitError(f"{kit.name} has no sim profile ({p})")
    raw = tomllib.loads(p.read_text(encoding="utf-8"))
    try:
        car = raw.get("car")
        return Profile(
            replay=int(raw["replay"]),
            frame=int(raw["frame"]) if "frame" in raw else None,
            preview=_views(raw.get("preview", {})),
            crops={k: tuple(v) for k, v in raw.get("crops", {}).items()},
            track_name=str(raw["track_name"]) if "track_name" in raw else None,
            car=CarSize(float(car["length"]), float(car["height"])) if car else None,
        )
    except KeyError as e:
        raise KitError(f"{p} has no {e.args[0]}") from None


def require_replay(prof: Profile, cfg: Config, kit: Kit) -> Replay:
    """The replay the profile's frame is on: your preview replay, or the one whose track it names."""
    if prof.replay == cfg.replay.subsession:
        return cfg.replay
    if prof.track_name:
        return Replay("", "", prof.replay, prof.track_name, track_path(prof.track_name))
    raise KitError(
        f"{kit.name}'s profile frames replay {prof.replay}, not your preview replay "
        f"{cfg.replay.subsession} ({cfg.replay.file}): open yours and run livery sim pick-frame {kit.name}"
    )


def track_of(prof: Profile, cfg: Config) -> str:
    """The camera.ini path of the track the kit's car is shot at: its own replay's, else your preview
    replay's, where a kit framed on someone else's replay gets its frame picked again."""
    return track_path(prof.track_name) if prof.track_name else cfg.replay.track


def _toml(v) -> str:
    if isinstance(v, str):
        return json.dumps(v)
    if isinstance(v, list | tuple):
        return "[" + ", ".join(_toml(x) for x in v) + "]"
    return repr(v)


PROFILE_HEAD = """# How the sim shows this car on the preview replay; written by the kit commands. Each view is a camera
# group and its camera's pose in the car's frame: x forward, y left, z up in metres from the car's origin,
# yaw, pitch (down positive) and FOV in radians; a preview camera's aim raises its aim point (field 9).
"""


def write_profile(kit_dir: Path, prof: Profile) -> None:
    lines = [PROFILE_HEAD, f"replay = {prof.replay}"]
    if prof.track_name:
        lines.append(f"track_name = {_toml(prof.track_name)}")
    if prof.frame is not None:
        lines.append(f"frame = {prof.frame}")
    if prof.car:
        lines += ["", "[car]", f"length = {_toml(prof.car.length)}", f"height = {_toml(prof.car.height)}"]
    for name, v in prof.preview.items():
        lines += ["", f"[preview.{name}]", f"group = {_toml(v.group)}"]
        if v.pose:
            lines += [f"pose = {_toml(list(v.pose))}", f"aim = {_toml(v.aim)}"]
    if prof.crops:
        lines += ["", "[crops]", *(f"{k} = {_toml(list(v))}" for k, v in prof.crops.items())]
    (kit_dir / PROFILE).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


# Approval: the owner's accept of the kit as it is. Any change to its build inputs undoes it.


class KitNotApproved(KitError):
    pass


def approval(kit_dir: Path) -> dict | None:
    """The kit's approval, if its files are still the ones approved."""
    from livery.hashing import kit_hash

    p = kit_dir / APPROVAL
    if not p.exists():
        return None
    record = json.loads(p.read_text(encoding="utf-8"))
    return record if record.get("kit_hash") == kit_hash(kit_dir) else None


def approve(kit_dir: Path, by: str, note: str, date: str | None = None) -> Path:
    from livery.hashing import kit_hash

    record = {
        "by": by,
        "date": date or time.strftime("%Y-%m-%d"),
        "note": note,
        "version": int(read_meta(kit_dir).get("version", 0)),
        "kit_hash": kit_hash(kit_dir),
    }
    p = kit_dir / APPROVAL
    p.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8", newline="\n")
    return p


def require_approved(ws: Workspace, name: str) -> dict:
    record = approval(ws.kits / name)
    if record is None:
        raise KitNotApproved(
            f"the {name} kit isn't approved as it is now: livery kit review {name}, then livery review --kit {name}"
        )
    return record
