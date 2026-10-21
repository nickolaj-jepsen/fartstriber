"""Car-locked preview cameras: `livery preview cameras` adds them to the track's camera set. The file format is
here; how the sim treats a camera set is in docs/sim.md (Camera sets).

The sim loads `Documents/iRacing/cameras/tracks/<track>/track.cam` when a session opens, if `camera.ini`
points at it. Each preview view is a group of its own holding one chase camera, posed by the kit's profile;
a pose the profile lacks is the default `preview_poses` sizes for the car.
"""

import hashlib
import json
import math
import shutil
import struct
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

from livery import kit as kits
from livery.config import Config, Workspace
from livery.errors import LiveryError
from livery.kit import CarSize, Kit, View

REC = 184
NAME = 48
NAME_LEN = 31  # the name block's byte 31 is the camera's type

# The poses, sized for a car as big as the McLaren 720S GT3, whose paint seen from 25 m at 0.2 rad spans
# 1507 by 360 px of the 1782 px window (docs/sim.md): 4.24 by 1.01 m by `size_seen`.
REF = CarSize(4.24, 1.01)
D = 25.0  # metres out: far enough that perspective is mild, near enough for the car's full model
Z = 0.5  # metres above the car's origin: about the middle of its side
AIM = 0.27  # field 9 that centres the side views
FOV = 0.2
TILT = math.radians(5)  # aimed straight down the roll is free, and the car turns with its heading on track
GROUP_NAME = NAME_LEN - len("Cam") - 1  # its camera, "Cam<group>", ends inside the record's name block


class CameraSetError(LiveryError):
    """A camera set or camera.ini this tool can't change safely, with what to do about it."""


def scale(size: CarSize) -> float:
    """How much farther out the cameras go than for the McLaren: the frame grows with whichever of the car's
    length and height outgrows the McLaren's more, so the car never runs off it."""
    return max(size.length / REF.length, size.height / REF.height)


def car_size(prof: kits.Profile) -> CarSize:
    """The car the kit's default poses are sized for: as `pick-frame` measured it, else as big as the McLaren."""
    return prof.car or REF


def _rig(size: CarSize) -> tuple[float, float, float]:
    """The cameras' distance and height, and the side views' aim, for a car this size: the distance with the
    frame, the height and the aim with the car's height."""
    up = size.height / REF.height
    return D * scale(size), Z * up, AIM * up


def _around(az_deg: float, el_deg: float, fov: float, d: float, z: float) -> tuple[float, ...]:
    """d out at azimuth az (0 ahead, 90 left) and elevation el, looking at the car's origin."""
    az, el = math.radians(az_deg), math.radians(el_deg)
    x, y = d * math.cos(el) * math.cos(az), d * math.cos(el) * math.sin(az)
    return (x, y, z + d * math.sin(el), math.atan2(-y, -x), el, fov)


def preview_poses(size: CarSize = REF) -> dict[str, tuple[tuple[float, ...], float]]:
    """view: ((x, y, z, yaw, pitch, fov), aim). The sides look square-on at each side, aimed to centre the
    car. The top view leans to the right, so the car lies along the screen, nose right; front34 is low from the front
    left, rear34 high from the rear right."""
    d, z, aim = _rig(size)
    s, up = scale(size), size.height / REF.height
    return {
        "side_l": ((0.0, d, z, -math.pi / 2, 0.0, FOV), aim),
        "side_r": ((0.0, -d, z, math.pi / 2, 0.0, FOV), aim),
        "top": (
            (0.0, -d * math.sin(TILT), z + d * math.cos(TILT), math.pi / 2, math.pi / 2 - TILT, FOV),
            0.0,
        ),
        "front34": (_around(40, 8, 0.24, d, z), 0.0),
        "rear34": (_around(-140, 30, 0.24, d, z), 0.0),
        "side_high": (_around(90, 25, 0.188, d, z), 0.1227 * up),
        "side_r_lit": ((0.0, -d, z, math.pi / 2, 0.0, FOV), aim),
        **{name: (_scaled(pose, s), a * up) for name, (pose, a) in NEAR.items()},
    }


# Views nearer than D, posed on the McLaren's geometry from its capture (docs/sim.md, Camera sets): behind
# and ahead at a driver's eye height, and close-ups of the left door and the nose. Their aims sit between
# the field's two readings (a raised aim point, or a tilt), so either frames the car; rear_chase's is raised
# by what centred the car in its first shots (it sat 115 px high at 0.17).
NEAR = {
    "rear_chase": ((-12.0, 0.0, 0.737, 0.0, 0.0613, 0.2157), 0.344),
    "front": ((12.0, 0.0, 0.587, -math.pi, 0.0489, 0.2114), 0.179),
    "door_close": ((2.2627, 4.9381, 0.5972, -2.0005, 0.1095, 0.4367), 0.0),
    "nose_close": ((6.45, 0.0, 0.237, -math.pi, 0.0367, 0.4581), -0.031),
}


def _scaled(pose: tuple[float, ...], s: float) -> tuple[float, ...]:
    return (*(c * s for c in pose[:3]), *pose[3:])


PREVIEW = preview_poses()


def size_seen(length_px: float, height_px: float, frame_px: int, pose: tuple[float, ...]) -> CarSize:
    """The car's size from how far its paint spans a side shot from `pose`, `frame_px` wide. The frame spans
    2 d tan(fov / 2) at the car's origin, d out; that takes the FOV across the frame's width, as REF does."""
    across = 2 * math.dist(pose[:3], (0.0, 0.0, 0.0)) * math.tan(pose[5] / 2)
    return CarSize(length_px / frame_px * across, height_px / frame_px * across)


def group_name(kit: str, view: str, pose: tuple[float, ...], aim: float) -> str:
    """A camera group of the kit's own for one view, tagged with its pose: new poses are new groups, so a
    sim run can tell from the group list whether the sim has read the set that holds them."""
    tag = hashlib.sha256(json.dumps([round(x, 6) for x in (*pose, aim)]).encode()).hexdigest()[:4]
    label = view.replace("_", " ").title()
    return f"{kit[: GROUP_NAME - len(label) - len(tag) - 2]} {label} {tag}"


def _filled(views: dict[str, View], defaults: dict[str, tuple[tuple[float, ...], float]]) -> dict[str, View]:
    """The views, each one without a pose given the default pose and aim."""
    out = {}
    for name, v in views.items():
        if v.pose:
            out[name] = v
        elif name in defaults:
            out[name] = replace(v, pose=defaults[name][0], aim=defaults[name][1])
        else:
            raise CameraSetError(f"the {name} view has no pose, and there is no default one")
    return out


def name_of(rec: bytes) -> str:
    return rec[:NAME_LEN].split(b"\0")[0].decode()


def placed(rec: bytes, pose: tuple[float, ...]) -> bytes:
    x, y, z, yaw, pitch, fov = pose
    fields = bytearray(rec[NAME:])
    struct.pack_into("<I6ff", fields, 0, 0, x, y, z, yaw, pitch, 0.0, fov)
    return rec[:NAME] + bytes(fields)


def _section(lines: list[str], section: str, key: str) -> tuple[int | None, int | None]:
    """Index of the section's header in camera.ini and of its `key` line, if there."""
    header = f"[{section}]".lower()
    starts = [i for i, ln in enumerate(lines) if ln.strip().lower() == header]
    if not starts:
        return None, None
    i = starts[0]
    for j in range(i + 1, len(lines)):
        if lines[j].strip().startswith("["):
            break
        if lines[j].split("=", 1)[0].strip() == key:
            return i, j
    return i, None


def _get(ini: str, section: str, key: str) -> str | None:
    lines = ini.splitlines()
    _, j = _section(lines, section, key)
    return None if j is None else lines[j].split("=", 1)[1].strip()


def _set(ini: str, section: str, key: str, value: str) -> str:
    lines = ini.splitlines()
    line = f"{key}={value}"
    i, j = _section(lines, section, key)
    if i is None:
        lines += ["", f"[{section}]", line]
    elif j is None:
        lines.insert(i + 1, line)
    else:
        lines[j] = line
    return "\n".join(lines) + "\n"


# Preview cameras: one group each in the track's camera set, which takes new groups, so previews keep every
# car camera. The set is 'MACT', version 18, the data size and the camera count; the camera records as
# above, whose type is the int at byte 32 (0 fixed on the track, 1 blimp, 2 chopper, 3 chase: placed in the
# car's frame and aimed at the car, so yaw and pitch are ignored, and field 9 raises the aim point, about
# 500 px per unit at 25 m and 0.2 rad); then the group count and a fixed block per group: flags (2 marks a
# scenic group), a 32-byte name, the camera count and 512 slots of 32-byte camera names.
TRACK_VERSION = 18
GROUP_SLOTS = 512
GROUP = 40 + 32 * GROUP_SLOTS
CHASE = 3


def _track_section(track: str) -> str:
    return f"tracks\\{track}"


def track_cam(docs: Path, track: str) -> Path:
    """The track's set; `track` is its path in camera.ini, e.g. 'daytona\\2011\\road'."""
    return docs / "cameras" / "tracks" / Path(*track.split("\\")) / "track.cam"


def track_set(data: bytes) -> tuple[list[bytes], list[tuple[int, str, list[str]]]]:
    """(camera records, groups as (flags, name, camera names))."""
    magic, version, size, n = struct.unpack_from("<4sIII", data)
    if magic != b"MACT" or size != len(data) - 12:
        raise CameraSetError("not a track camera set this tool knows")
    if version != TRACK_VERSION:
        raise CameraSetError(f"track camera set version {version}; only {TRACK_VERSION} has been checked")
    recs = [data[16 + i * REC : 16 + (i + 1) * REC] for i in range(n)]
    o = 16 + n * REC
    (ng,) = struct.unpack_from("<I", data, o)
    if o + 4 + ng * GROUP != len(data):
        raise CameraSetError("the track camera set's groups don't fill the file")
    groups = []
    for g in range(ng):
        p = o + 4 + g * GROUP
        flags, name, count = struct.unpack_from("<I32sI", data, p)
        cams = [data[p + 40 + 32 * k : p + 72 + 32 * k].split(b"\0")[0].decode() for k in range(count)]
        groups.append((flags, name.split(b"\0")[0].decode(), cams))
    return recs, groups


def build_track_set(recs: list[bytes], groups: list[tuple[int, str, list[str]]]) -> bytes:
    body = b"".join(recs) + struct.pack("<I", len(groups))
    for flags, name, cams in groups:
        slots = b"".join(c.encode().ljust(32, b"\0") for c in cams).ljust(32 * GROUP_SLOTS, b"\0")
        body += struct.pack("<I32sI", flags, name.encode(), len(cams)) + slots
    return struct.pack("<4sIII", b"MACT", TRACK_VERSION, len(body) + 4, len(recs)) + body


def _type(rec: bytes) -> int:
    return struct.unpack_from("<I", rec, 32)[0]


def _is_preview(rec: bytes, views: dict[str, View]) -> bool:
    x, y, z, _yaw, _pitch, _roll, fov = struct.unpack("<7f", rec[NAME + 4 : NAME + 32])
    return _type(rec) == CHASE and any(
        all(abs(a - b) < 1e-3 for a, b in zip((x, y, z, fov), (p[0], p[1], p[2], p[5]), strict=True))
        for p in (v.pose for v in views.values())
    )


def preview_set(stock_set: bytes, views: dict[str, View]) -> bytes:
    """The stock track set plus a group per preview view, named by its group, holding one chase camera."""
    groups = {name: v.group for name, v in views.items()}
    recs, stock_groups = track_set(stock_set)
    taken = {g for _f, g, _c in stock_groups} | {name_of(r) for r in recs}
    clash = sorted(g for g in groups.values() if g in taken or f"Cam{g}" in taken)
    if clash:
        raise CameraSetError(f"the track's set already has group(s) or camera(s) named {', '.join(clash)}")
    if len(set(groups.values())) < len(groups):
        raise CameraSetError(f"two preview views share a camera group: {groups}")
    chase = [r for r in recs if _type(r) == CHASE]
    # The record the preview poses were measured with; Chase frames alike apart from field 9.
    template = next((r for r in chase if name_of(r) == "CamFar Chase"), chase[0] if chase else None)
    if template is None:
        raise CameraSetError("the track's set has no chase camera to copy")
    out_groups = list(stock_groups)
    for v in views.values():
        rec = bytearray(placed(template, v.pose))
        rec[:32] = f"Cam{v.group}".encode().ljust(32, b"\0")
        struct.pack_into("<f", rec, NAME + 36, v.aim)  # field 9
        recs.append(bytes(rec))
        out_groups.append((0, v.group, [f"Cam{v.group}"]))
    return build_track_set(recs, out_groups)


def _track_files(docs: Path, track: str) -> tuple[Path, Path, Path]:
    """The track's set, the stock set kept beside it, and camera.ini's pointer as it was before."""
    cam = track_cam(docs, track)
    return cam, cam.with_name("track.cam.stock"), cam.with_name("track.cam.pointer")


def install_previews(docs: Path, track: str, views: dict[str, View], sim_running: bool) -> list[str]:
    """Add the preview cameras to the track's set, keeping the stock set aside first. Returns notes."""
    if not views:
        raise CameraSetError("no preview views to add")
    cam, kept, saved = _track_files(docs, track)
    ini = docs / "camera.ini"
    text = ini.read_text(encoding="utf-8-sig")
    section, value = _track_section(track), f"tracks\\{track}\\track.cam"
    was = _get(text, section, "LastTrackSet")
    if (was or "").lower() != value.lower() and sim_running:
        raise CameraSetError(f"{ini} doesn't load the track's set; close the sim and run this again")
    if not kept.exists():
        if not cam.exists():
            raise CameraSetError(
                f"{cam} doesn't exist; save the track's camera set once from the sim's camera tool (Ctrl+F12)"
            )
        if any(_is_preview(r, views) for r in track_set(cam.read_bytes())[0]):
            raise CameraSetError(f"{cam} already holds preview cameras and no stock set is kept beside it")
        shutil.copy2(cam, kept)
    cam.write_bytes(preview_set(kept.read_bytes(), views))
    notes = [
        f"wrote {', '.join(f'{n} -> {v.group}' for n, v in views.items())} into {cam}; stock set in {kept}"
    ]
    if (was or "").lower() != value.lower():
        if not saved.exists():
            saved.write_text("" if was is None else was, encoding="utf-8")
        ini.write_text(_set(text, section, "LastTrackSet", value), encoding="utf-8")
        notes.append(f"pointed {ini} at the track's set")
    notes.append("open the preview replay again: the sim reads camera sets when a session opens")
    return notes


def restore_previews(docs: Path, track: str, sim_running: bool) -> list[str]:
    """Put the track's stock set back, and its camera.ini pointer as it was before `install_previews`."""
    cam, kept, saved = _track_files(docs, track)
    if not kept.exists():
        raise CameraSetError(f"no stock set kept at {kept}")
    if saved.exists() and sim_running:
        raise CameraSetError("camera.ini's pointer needs putting back; close the sim and run this again")
    notes = []
    if saved.exists():  # first, so a failure here leaves the stock set kept for another try
        ini = docs / "camera.ini"
        text = ini.read_text(encoding="utf-8-sig")
        was = saved.read_text(encoding="utf-8")
        ini.write_text(_set(text, _track_section(track), "LastTrackSet", was), encoding="utf-8")
        saved.unlink()
        notes.append(f"put {ini}'s pointer back")
    shutil.copy2(kept, cam)
    kept.unlink()
    notes.append(f"restored {cam}; open the replay again to use it")
    return notes


def _track_pointed(docs: Path, track: str) -> bool:
    ini = docs / "camera.ini"
    text = ini.read_text(encoding="utf-8-sig") if ini.exists() else ""
    was = _get(text, _track_section(track), "LastTrackSet") or ""
    return was.lower() == f"tracks\\{track}\\track.cam".lower()


def previews_missing(docs: Path, track: str, views: dict[str, View]) -> list[str]:
    """Preview views the track's set doesn't hold as posed, or all of them while camera.ini doesn't point the
    sim at that set."""
    cam = track_cam(docs, track)
    if not cam.exists() or not _track_pointed(docs, track):
        return sorted(views)
    recs, groups = track_set(cam.read_bytes())
    by_name = {name_of(r): r for r in recs}
    named = {g for _f, g, _c in groups}

    def held(v: View) -> bool:
        rec = by_name.get(f"Cam{v.group}")
        if rec is None or v.group not in named or not v.pose:
            return False
        x, y, z, _yaw, _pitch, _roll, fov = struct.unpack("<7f", rec[NAME + 4 : NAME + 32])
        (aim,) = struct.unpack_from("<f", rec, NAME + 36)
        return all(
            abs(a - b) < 1e-3
            for a, b in zip((x, y, z, fov, aim), (*v.pose[:3], v.pose[5], v.aim), strict=True)
        )

    return sorted(name for name, v in views.items() if not held(v))


def track_views(ws: Workspace, cfg: Config, track: str, names: Sequence[str]) -> dict[str, View]:
    """The preview views of every kit shot on this track, as '<kit> <view>', so adding one kit's cameras keeps
    the others'. Kits may share a group only on the same camera."""
    out: dict[str, View] = {}
    by_group: dict[str, tuple[str, View]] = {}
    for meta in sorted(ws.kits.glob(f"*/{kits.META}")):
        try:
            kit = kits.load(ws, meta.parent.name)
            prof = kits.profile(kit)
        except kits.KitError:
            continue
        if kits.track_of(prof, cfg) != track:
            continue
        for name, v in preview_views(kit, names).items():
            other = by_group.get(v.group)
            if other is None:
                by_group[v.group] = (f"{kit.name} {name}", v)
                out[f"{kit.name} {name}"] = v
            elif (other[1].pose, other[1].aim) != (v.pose, v.aim):
                raise CameraSetError(
                    f"{other[0]} and {kit.name} {name} put the camera group {v.group!r} in different places"
                )
    return out


def armed(docs: Path, track: str, running: Callable[[], bool], log=print, poll_s: float = 1.0) -> bool:
    """Whether the sim runs, once it may: camera.ini changes only while the sim is closed (it rewrites the file
    when it exits), so while it would have to, this waits until the owner closes the sim. One close and reopen
    then loads the cameras."""
    cam, kept, _ = _track_files(docs, track)
    if not (cam.exists() or kept.exists()):
        raise CameraSetError(
            f"{cam} doesn't exist; save the track's camera set once from the sim's camera tool (Ctrl+F12)"
        )
    if running() and not _track_pointed(docs, track):
        log("waiting for the sim to close: camera.ini can point at the track's set only while it is closed")
        while running():
            time.sleep(poll_s)
    return running()


# The poses a kit's profile holds: what it gives, else the defaults sized for its car, written into it.


def reset_views(kit: Kit, names: Sequence[str]) -> None:
    """Pose the kit's views `names` at their defaults again, each in a group of its own, keeping any frame."""
    prof = kits.profile(kit)
    defaults = preview_poses(car_size(prof))
    unknown = [n for n in names if n not in defaults]
    if unknown:
        raise kits.KitError(f"no default pose for {', '.join(unknown)}")
    was = {n: prof.preview.get(n) or View("", ()) for n in names}
    preview = {
        n: replace(
            was[n], group=group_name(kit.name, n, *defaults[n]), pose=defaults[n][0], aim=defaults[n][1]
        )
        for n in names
    }
    kits.write_profile(kit.dir, replace(prof, preview=prof.preview | preview))


def preview_views(kit: Kit, names: Sequence[str]) -> dict[str, View]:
    """The kit's preview views `names` (the policy's), each with its pose and aim."""
    prof = kits.profile(kit)
    defaults = preview_poses(car_size(prof))
    missing = [n for n in names if n not in prof.preview and n not in defaults]
    if missing:
        raise kits.KitError(
            f"{kit.name}'s profile has no preview view {', '.join(missing)}, nor a default one"
        )
    new = {
        n: View(group_name(kit.name, n, *defaults[n]), *defaults[n]) for n in names if n not in prof.preview
    }
    views = _filled({n: prof.preview.get(n) or new[n] for n in names}, defaults)
    if any(views[n] != prof.preview.get(n) for n in names):
        kits.write_profile(kit.dir, replace(prof, preview=prof.preview | views))
    return views
