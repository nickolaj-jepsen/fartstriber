"""`livery sim pick-frame <kit>`: the replay frame a kit's car is shot at, picked on the replay that is open.

A frame needs the car on a straight, where the car-locked cameras sit square and centred, with nothing
between any preview camera and the car (docs/sim.md, Camera sets). The scan seeks across the replay and reads
the car's telemetry at each stop (no camera switch, no paint): where it is on the lap, whether it is on track,
and how fast it turns where the sim says. It keeps on-track frames whose neighbours turn least, spread around
the lap. Each candidate is then shot from every preview group with the car painted white, then black, under a
matte spec: the pixels that differ are the car's paint, whatever lies behind it. A candidate passes a view when
the car is whole in the frame with a margin, about centred, and shows as much paint as on the clearest
candidate, so no fence or wall covers part of it. The straightest candidate that passes every view becomes the
profile's frame, with the replay's identity.

The side views also measure the car (`cameras.size_seen`). When the profile's cameras don't fit it (it runs off
the frame, or they would be sized more than a tenth differently for it), the profile gets cameras sized for the
car instead of a frame: install them, reopen the replay and pick again.

`check` shoots every preview group at the profile's frame in two orders and compares the shots: a car-locked
camera frames the car the same whichever view came before.
"""

import json
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import cast

import numpy as np
from PIL import Image
from scipy import ndimage

from livery import cameras
from livery import kit as kits
from livery.config import Config, Shot, Workspace
from livery.errors import LiveryError
from livery.kit import CarSize, Kit, View
from livery.raster import tga_bytes
from livery.simsession import SimSession, fresh_name

ON_TRACK = 3  # irsdk.TrkLoc.on_track
MAX_SAMPLES = 400
MIN_STRIDE = 30  # replay frames between samples: half a second
NEIGHBOURS = 2  # samples either side that count towards how much a candidate turns
TURN_SLACK = 0.02  # rad/s (or rad of steering) a straight may wander by
CANDIDATES = 6
SEPARATION = 0.03  # of a lap between candidates, so they see different backgrounds
MATTE = (0, 255, 0)  # no metal, fully rough: the fewest reflections between the two paints
PAINT_DIFF = 40  # a channel moving this much between the white and the black car is paint
MIN_PART = 0.005  # paint pieces smaller than this share of the largest are foliage moving, not car
CLEAR = 0.97  # of the paint the clearest candidate shows: less, and something covers the car
MARGIN = 0.02  # of the frame, between the car and each edge
CENTRE = 0.1  # of the frame, the car's centre may sit off the frame's
SIZE_TOL = 0.1  # cameras sized this much differently for the measured car get resized
GROW = 1.25  # a car cut off by the frame is at least this much bigger than it looks
REPEAT_DIFF = 8  # a channel moving more than this between the two orders differs
REPEAT_PCT = 0.5  # percent of a shot that may differ: animated foliage, the window border
# The critic's close crops as shares of the car's box in a view: the beltline along the left side and the
# nose in the front three-quarter view, as they sit on the McLaren.
CROPS = {"beltline": ("side_l", (0.26, 0.22, 0.83, 0.80)), "nose": ("front34", (-0.03, 0.34, 0.47, 0.98))}


class NoClearFrame(LiveryError):
    pass


@dataclass(frozen=True)
class Sample:
    frame: int
    lap: float | None  # share of the lap
    on_track: bool
    turn: float | None  # |yaw rate| in rad/s, or |steering| in rad where the sim gives no yaw rate


def _turn(t: dict) -> float | None:
    for key in ("yaw_rate", "steer"):
        if t.get(key) is not None:
            return abs(float(t[key]))
    return None


def scan(s: SimSession, log=print) -> list[Sample]:
    """The car's telemetry at frames across the whole replay."""
    total = s.replay_frames()
    stride = max(MIN_STRIDE, total // MAX_SAMPLES)
    log(f"scanning {total} replay frames, every {stride}")
    out = []
    for f in range(stride // 2, total, stride):
        s.seek(f)
        t = s.telemetry()
        surface = t.get("surface")
        on = (surface is None or int(surface) == ON_TRACK) and not t.get("pit")
        lap = t.get("lap")
        out.append(Sample(f, None if lap is None or lap < 0 else float(lap), on, _turn(t)))
    return out


def _apart(a: Sample, b: Sample, stride: int) -> bool:
    if a.lap is not None and b.lap is not None:
        d = abs(a.lap - b.lap)
        return min(d, 1 - d) >= SEPARATION
    return abs(a.frame - b.frame) >= 10 * stride


def candidates(samples: list[Sample], n: int = CANDIDATES) -> list[tuple[Sample, float]]:
    """On-track samples whose neighbours turn least, each with how much they turn, spread around the lap.

    A sim that gives no turn rate (or only zeros) leaves the order to the frame checks.
    """
    known = any(x.turn for x in samples)
    stride = samples[1].frame - samples[0].frame if len(samples) > 1 else MIN_STRIDE

    def score(i: int) -> float:
        around = samples[max(0, i - NEIGHBOURS) : i + NEIGHBOURS + 1]
        if not all(x.on_track for x in around):
            return float("inf")
        return max((x.turn or 0.0) for x in around) if known else 0.0

    def room(i: int, most: float) -> int:
        """Samples to the nearer end of the stretch around i that turns no more than `most`: the middle of a
        straight beats its ends."""
        ends = []
        for step in (-1, 1):
            j = i
            while 0 <= j + step < len(samples) and samples[j + step].on_track:
                if known and (samples[j + step].turn or 0.0) > most + TURN_SLACK:
                    break
                j += step
            ends.append(abs(j - i))
        return min(ends)

    scores = {i: score(i) for i, x in enumerate(samples) if x.on_track}
    ranked = sorted((sc, -room(i, sc), samples[i].frame, i) for i, sc in scores.items())
    picked: list[tuple[Sample, float]] = []
    for sc, _room, _f, i in ranked:
        if sc == float("inf") and picked:
            break
        if all(_apart(samples[i], p, stride) for p, _ in picked):
            picked.append((samples[i], sc))
        if len(picked) == n:
            break
    return picked


# What the paint shows in a shot.


def paint_mask(white: np.ndarray, black: np.ndarray) -> np.ndarray:
    """The pixels that show the car's paint: what changed between the white and the black car."""
    changed = np.abs(white.astype(np.int16) - black.astype(np.int16)).max(axis=2) > PAINT_DIFF
    m: np.ndarray = np.asarray(ndimage.binary_opening(changed), bool)
    lab, n = cast(tuple[np.ndarray, int], ndimage.label(m))  # the labels and their count
    if n == 0:
        return m
    sizes = np.asarray(ndimage.sum(m, lab, range(1, n + 1)))
    keep = np.flatnonzero(sizes >= sizes.max() * MIN_PART) + 1
    return np.isin(lab, keep)


@dataclass(frozen=True)
class Seen:
    """How a view shows the car on one frame."""

    box: tuple[int, int, int, int] | None  # x0, y0, x1, y1 of the paint, inclusive
    area: int
    width: int
    height: int

    @property
    def margin(self) -> float:
        """The car's least distance to an edge, as a share of the frame across that edge."""
        if self.box is None:
            return -1.0
        x0, y0, x1, y1 = self.box
        w, h = self.width, self.height
        return min(x0 / w, (w - 1 - x1) / w, y0 / h, (h - 1 - y1) / h)

    @property
    def off_centre(self) -> float:
        """How far the car's centre sits from the frame's, as a share of the frame (the larger axis)."""
        if self.box is None:
            return 1.0
        x0, y0, x1, y1 = self.box
        return max(
            abs((x0 + x1) / 2 - self.width / 2) / self.width,
            abs((y0 + y1) / 2 - self.height / 2) / self.height,
        )


def seen(mask: np.ndarray) -> Seen:
    h, w = mask.shape
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return Seen(None, 0, w, h)
    return Seen((int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())), int(mask.sum()), w, h)


def judge(views: dict[str, dict[int, Seen]]) -> dict[int, dict[str, str | None]]:
    """Per candidate frame and view, what is wrong with the shot, or None."""
    out: dict[int, dict[str, str | None]] = {}
    for view, by_frame in views.items():
        clearest = max(x.area for x in by_frame.values())
        for frame, x in by_frame.items():
            if x.box is None or clearest == 0:
                problem = "the car isn't in the shot"
            elif x.margin < MARGIN:
                problem = "the car runs off the frame"
            elif x.off_centre > CENTRE:
                problem = "the car is off-centre"
            elif x.area < CLEAR * clearest:
                problem = f"something covers {100 * (1 - x.area / clearest):.0f}% of the car"
            else:
                problem = None
            out.setdefault(frame, {})[view] = problem
    return out


def measured(views: dict[str, View], seen_by_view: dict[str, list[Seen]]) -> CarSize | None:
    """The car's size from the side views' shots, the median over the frames given."""
    sizes = [
        cameras.size_seen(x.box[2] - x.box[0] + 1, x.box[3] - x.box[1] + 1, x.width, views[v].pose)
        for v, xs in seen_by_view.items()
        if v in views and v.startswith("side_")
        for x in xs
        if x.box is not None
    ]
    if not sizes:
        return None
    return CarSize(float(np.median([z.length for z in sizes])), float(np.median([z.height for z in sizes])))


def _crops(seen_at: dict[str, Seen]) -> dict[str, tuple]:
    out = {}
    for name, (view, (a, b, c, d)) in CROPS.items():
        x = seen_at.get(view)
        if x is None or x.box is None:
            continue
        x0, y0, x1, y1 = x.box
        w, h = x1 - x0, y1 - y0
        box = [x0 + a * w, y0 + b * h, x0 + c * w, y0 + d * h]
        lim = [x.width, x.height, x.width, x.height]
        out[name] = (view, *(round(min(max(v, 0), m)) for v, m in zip(box, lim, strict=True)))
    return out


def _same(v: View, pose: tuple[float, ...], aim: float) -> bool:
    return (
        len(v.pose) == len(pose)
        and all(abs(a - b) < 1e-6 for a, b in zip(v.pose, pose, strict=True))
        and (abs(v.aim - aim) < 1e-6)
    )


def resized(kit: Kit, prof: kits.Profile, size: CarSize) -> kits.Profile:
    """The profile with cameras sized for the car: each view still on its default pose gets the default for
    `size`, under a group name of its own, so the sim shows the new cameras only once it has read them."""
    old_preview, new_preview = cameras.preview_poses(cameras.car_size(prof)), cameras.preview_poses(size)
    preview = {}
    for name, v in prof.preview.items():
        if name in old_preview and (not v.pose or _same(v, *old_preview[name])):
            pose, aim = new_preview[name]
            preview[name] = View(cameras.group_name(kit.name, name, pose, aim), pose, aim)
        else:
            preview[name] = v
    return replace(prof, preview=preview, car=size)


def _out(ws: Workspace, kit: Kit, prefix: str = "") -> Path:
    root = ws.cache / "pick-frame" / kit.name
    root.mkdir(parents=True, exist_ok=True)
    d = root / f"{prefix}{fresh_name(root)}"
    d.mkdir()
    return d


def _half(img: np.ndarray) -> Image.Image:
    im = Image.fromarray(img)
    return im.resize((max(1, im.width // 2), max(1, im.height // 2)), Image.Resampling.LANCZOS)


def pick(cfg: Config, ws: Workspace, kit: Kit, log=print, write: bool = True) -> dict:
    """Scan the open replay, shoot the candidates, and write the chosen frame (or resized cameras) into the
    kit's profile unless `write` is False. Returns the report, also saved as report.json in the run folder."""
    names = list(ws.policy.preview)
    views = cameras.preview_views(kit, names)
    prof = kits.profile(kit)
    groups = {v.group for v in views.values()}
    fix = f"run livery preview cameras {kit.name} --arm and reopen the replay"
    canvas = kit.canvas
    paint = {c: tga_bytes(np.full((canvas, canvas, 3), c, np.uint8)) for c in (255, 0)}
    spec = tga_bytes(np.full((canvas, canvas, 3), MATTE, np.uint8))
    report: dict = {"kit": kit.name, "views": names}
    with SimSession(
        cfg,
        f"pick frame {kit.name}",
        paint_folder=kit.paint_folder,
        groups=groups,
        groups_fix=fix,
        sim="any-replay",
        log=log,
        checkout=ws.root,
    ) as s:
        s.out = _out(ws, kit)
        sub, track = s.manifest["replay"]["subsession"], s.manifest["replay"]["track"]
        report["replay"] = {"subsession": sub, "track": track}
        samples = scan(s, log)
        picked = candidates(samples)
        report["samples"] = len(samples)
        report["candidates"] = [{**asdict(x), "score": sc} for x, sc in picked]
        if not picked:
            raise NoClearFrame("the car is never on track in this replay")
        log(f"candidates: {', '.join(str(x.frame) for x, _ in picked)}")
        paint_name, spec_name = s.paint_names
        white: dict[tuple[int, str], np.ndarray] = {}
        by_view: dict[str, dict[int, Seen]] = {n: {} for n in names}
        for colour, data in paint.items():
            s.put({paint_name: data, spec_name: spec})
            s.reload(Shot(picked[0][0].frame, views[names[0]].group))
            for x, _ in picked:
                for name in names:
                    img = s.shoot(f"{x.frame}_{name}", Shot(x.frame, views[name].group), check_ui=True)
                    if colour:
                        white[x.frame, name] = img
                        continue
                    lit = white.pop((x.frame, name))
                    mask = paint_mask(lit, img)
                    by_view[name][x.frame] = seen(mask)
                    _half(lit).save(s.out / f"{x.frame}_{name}.png")
                    Image.fromarray(mask).save(s.out / f"{x.frame}_{name}_paint.png")
            log(f"shot the {'white' if colour else 'black'} car")
    problems = judge(by_view)
    report["frames"] = {
        str(x.frame): {
            "score": sc,
            "views": {n: {"problem": problems[x.frame][n], **asdict(by_view[n][x.frame])} for n in names},
        }
        for x, sc in picked
    }
    passing = [(x, sc) for x, sc in picked if not any(problems[x.frame].values())]
    sides = [n for n in names if n.startswith("side_")]
    size = measured(views, {n: [by_view[n][x.frame] for x, _ in (passing or picked)] for n in sides})
    cut = any(all(problems[x.frame][n] == "the car runs off the frame" for x, _ in picked) for n in names)
    current = cameras.car_size(prof)
    report["car"] = asdict(size) if size else None
    if size is not None and (cut or abs(cameras.scale(size) / cameras.scale(current) - 1) > SIZE_TOL):
        if cut:
            size = CarSize(max(size.length, current.length) * GROW, max(size.height, current.height) * GROW)
        report["resized"] = asdict(size)
        report["next"] = (
            f"livery preview cameras {kit.name} --arm, reopen the replay, then livery sim pick-frame {kit.name}"
        )
        log(
            f"the cameras don't fit the car ({size.length:.2f} m by {size.height:.2f} m); "
            f"sized them for it: {report['next']}"
        )
        if write:
            kits.write_profile(kit.dir, resized(kit, kits.profile(kit), size))
    elif not passing:
        _save(s.out, report)
        reasons = "; ".join(
            f"{x.frame}: " + ", ".join(f"{n} {p}" for n, p in problems[x.frame].items() if p)
            for x, _ in picked
        )
        raise NoClearFrame(f"no candidate frame shows the car clearly from every camera ({reasons}); {s.out}")
    else:
        best, _ = min(passing, key=lambda c: (c[1], sum(by_view[n][c[0].frame].off_centre for n in sides)))
        report["frame"] = best.frame
        log(f"frame {best.frame} on replay {sub} at {track}")
        if write:
            prof = kits.profile(kit)
            crops = prof.crops or _crops({n: by_view[n][best.frame] for n in names})
            kits.write_profile(
                kit.dir,
                replace(
                    prof,
                    replay=int(sub),
                    frame=best.frame,
                    track_name=None if sub == cfg.replay.subsession else track,
                    crops=crops,
                ),
            )
            report["written"] = str(kit.dir / kits.PROFILE)
    report["out"] = str(s.out)
    _save(s.out, report)
    return report


def _save(out: Path, report: dict) -> None:
    (out / "report.json").write_text(
        json.dumps(report, indent=1, default=str), encoding="utf-8", newline="\n"
    )


def differing(a: np.ndarray, b: np.ndarray) -> float:
    """Percent of pixels that differ between two shots of one view."""
    if a.shape != b.shape:
        return 100.0
    return float((np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2) > REPEAT_DIFF).mean() * 100)


def check(cfg: Config, ws: Workspace, kit: Kit, log=print) -> dict:
    """Shoot every preview group at the profile's frame in the policy's order and then the other way round,
    and compare each view's two shots. Paints nothing."""
    names = list(ws.policy.preview)
    views = cameras.preview_views(kit, names)
    prof = kits.profile(kit)
    replay = kits.require_replay(prof, cfg, kit)
    shots = prof.shots(names)
    with SimSession(
        cfg,
        f"pick frame check {kit.name}",
        paint_folder=kit.paint_folder,
        groups={v.group for v in views.values()},
        groups_fix=f"run livery preview cameras {kit.name} --arm and reopen the replay",
        replay=replay,
        log=log,
        checkout=ws.root,
    ) as s:
        s.out = _out(ws, kit, "check-")
        first = {n: s.shoot(f"a_{n}", shots[n], check_ui=True) for n in names}
        second = {n: s.shoot(f"b_{n}", shots[n], check_ui=True) for n in reversed(names)}
    report: dict = {"kit": kit.name, "frame": prof.frame, "limit_pct": REPEAT_PCT, "views": {}}
    for n in names:
        _half(first[n]).save(s.out / f"a_{n}.png")
        _half(second[n]).save(s.out / f"b_{n}.png")
        report["views"][n] = round(differing(first[n], second[n]), 3)
    report["passed"] = all(v <= REPEAT_PCT for v in report["views"].values())
    report["out"] = str(s.out)
    report["at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    _save(s.out, report)
    return report
