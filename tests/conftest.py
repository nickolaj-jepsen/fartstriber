import hashlib
import json
import re
import textwrap
import uuid
from pathlib import Path

import numpy as np
import pytest
import shapely
from PIL import Image
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from livery import kit as kits
from livery.config import REPO, Workspace
from livery.kit import Profile, View
from livery.model import Element

FIXTURES = Path(__file__).parent / "fixtures"  # laid out like a workspace: designs/<name>/design.py
CANVAS = 64
SPEC = {
    "gloss": [0, 30, 255],
    "satin": [0, 110, 255],
    "matte": [0, 220, 0],
    "metallic": [255, 60, 255],
    "chrome": [255, 0, 255],
}


@pytest.fixture(autouse=True)
def private_metrics(private_state, tmp_path, monkeypatch):
    """Telemetry goes to the test's own state dir, and no checkout's old runs/metrics.jsonl is copied in."""
    from livery import metrics

    monkeypatch.setattr(metrics, "LEGACY", tmp_path / "no-legacy" / "metrics.jsonl")
    return metrics.path()


REPLAY = 4242  # the fixture settings' preview replay, which the tiny kit's profile frames
OWNER = "Ingrid"  # the fixture settings' owner: a name no message has of its own
SETTINGS = """
owner = "{owner}"
customer_id = 111
team_id = 222
archive_dir = "{root}/archive"

[iracing]
documents = "{root}/iracing-docs"
install = "{root}/iracing"
process = "test-sim.exe"

[replay]
file = "test.rpy"
sha256 = "{sha}"
subsession = {replay}
track_name = "test track"
track = 'test\\track'
"""


@pytest.fixture(autouse=True)
def private_state(tmp_path, monkeypatch):
    """Tests never touch the machine's state dir, the user's settings or a checkout's old install backups,
    and never take the sim lock a real run would queue on. The settings are a fixture's."""
    from livery import paintfolder, simlock

    state = tmp_path / "state"
    state.mkdir()
    settings = SETTINGS.format(root=tmp_path.as_posix(), sha="0" * 64, replay=REPLAY, owner=OWNER)
    (state / "config.toml").write_text(settings, encoding="utf-8")
    monkeypatch.setenv("FARTSTRIBER_STATE", str(state))
    monkeypatch.setattr(paintfolder, "REPO", tmp_path / "checkout")
    monkeypatch.setattr(simlock, "MUTEX", f"Local\\fartstriber-test-{uuid.uuid4().hex}")
    return tmp_path / "state"


@pytest.fixture
def ws(tmp_path) -> Workspace:
    """A workspace with one tiny kit (a 64 px canvas whose left half is the car) and the repo's fonts."""
    kit = tmp_path / "kits" / "tiny"
    (kit / "overlays").mkdir(parents=True)
    mask = np.zeros((CANVAS, CANVAS), np.uint8)
    mask[:, : CANVAS // 2] = 255
    Image.fromarray(mask).save(kit / "overlays" / "mask.png")
    write_kit(kit, {"canvas": CANVAS, "paint_folder": "tiny", "bleed_px": 4})
    ws = Workspace(root=tmp_path, assets=REPO / "assets")
    preview = {v: View("Top" if v == "top" else "Side", ()) for v in ws.policy.preview}
    kits.write_profile(kit, Profile(REPLAY, 100, preview, {}))
    kits.approve(kit, "Tester", "")
    return ws


MAPS = ("regions", "side_frames", "side_transforms", "top_transform")


def write_kit(d: Path, raw: dict) -> None:
    """A kit's files from one dict, as the kit commands write them: the maps to maps.json, the rest to
    kit.yaml."""
    meta = {"version": 1, "views": list(kits.DEFAULT_VIEWS), "spec_presets": SPEC} | {
        k: v for k, v in raw.items() if k not in MAPS
    }
    kits.write(d, meta=meta, maps={k: raw[k] for k in MAPS if k in raw})


def read_kit(d: Path) -> dict:
    return kits.read_meta(d) | kits.read_maps(d)


def write_design(ws: Workspace, name: str, body: str) -> None:
    d = ws.design_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    header = (
        'from livery.model import Livery, Material\nfrom livery.primitives import svg\n\nKIT = "tiny"\n\n'
    )
    (d / "design.py").write_text(header + textwrap.dedent(body), encoding="utf-8")


@pytest.fixture
def cheap_flat(monkeypatch):
    """`preview flat` on the tiny kit: the build and its stamp are real, the pictures are placeholders (the
    tiny kit has no maps to draw car views through)."""
    from livery import preview_flat

    def picture(*args, **kwargs) -> Image.Image:
        return Image.new("RGB", (40, 20), "grey")

    monkeypatch.setattr(preview_flat, "views", picture)
    monkeypatch.setattr(preview_flat, "overlay", picture)
    monkeypatch.setattr(preview_flat, "car", picture)


def checked(
    ws: Workspace,
    name: str,
    body: str = 'def build(kit, p):\n    return Livery(base="#3060c0")\n',
    outputs: dict[str, bytes] | None = None,
) -> dict:
    """A design whose check passed with today's hashes and whose TGAs (placeholder bytes, or `outputs`)
    and flat preview are that build's, without running the build: for tests about what a pack or review
    accepts, not about building."""
    from livery import hashing, stamps

    write_design(ws, name, body)
    out = ws.out(name)
    out.mkdir(parents=True, exist_ok=True)
    outputs = outputs or {"car.tga": f"{name} paint".encode(), "car_spec.tga": f"{name} spec".encode()}
    for f, data in outputs.items():
        (out / f).write_bytes(data)
    digests = {f: hashlib.sha256(data).hexdigest() for f, data in outputs.items()}
    check = {
        "design": name,
        "design_hash": hashing.design_hash(ws, name),
        "toolchain_hash": hashing.toolchain_hash(),
        "policy_hash": hashing.policy_hash(ws),
        "passed": True,
        "errors": [],
        "warnings": [],
        "waivers": [],
        "digests": digests,
    }
    (out / "check.json").write_text(json.dumps(check), encoding="utf-8")
    flat = out / "flat"
    flat.mkdir(exist_ok=True)
    for f in ("views.png", "car.png", "squint.png"):
        Image.new("RGB", (300, 150), "grey").save(flat / f)
    stamps.write(flat / stamps.STAMP, stamps.make(ws, name, outputs))
    return check


def sim_run(ws: Workspace, name: str, stamp: str, digests: dict | None, colour: str = "purple") -> Path:
    """A whole sim run of side_l and front34, its manifest naming the build it shot (none: from before
    runs recorded one)."""
    run = ws.out(name) / "sim" / stamp
    run.mkdir(parents=True)
    for view in ("side_l", "front34"):
        Image.new("RGB", (900, 450), colour).save(run / f"{view}.png")
    if digests is not None:
        (run / "manifest.json").write_text(json.dumps({"digests": digests}), encoding="utf-8")
    return run


def drawn(el: Element, color: str | None = None) -> BaseGeometry:
    """What an element's paths cover, as texture geometry (each path even-odd); only `color`'s if given."""
    out = []
    for d, fill in re.findall(r'<path [^>]*?d="([^"]*)"[^>]*?fill="([^"]*)"', el.svg):
        if color is not None and fill != color:
            continue
        g = Polygon()
        for sub in d.split("Z"):
            pts = re.findall(r"(-?[\d.]+),(-?[\d.]+)", sub)
            if len(pts) >= 3:
                ring = shapely.make_valid(Polygon([(float(x), float(y)) for x, y in pts]))
                g = g.symmetric_difference(ring)
        out.append(g)
    return shapely.union_all(out) if out else Polygon()


TWO = 256  # the two-island kit's canvas


def two_islands(root: Path, stretch: float = 1.0) -> Workspace:
    """A kit "two" of two side_l islands meeting at x = TWO/2 (u = 0.5): i001 maps x = TWO u, i002
    x = TWO/2 + stretch TWO (u - 1/2), so with a stretch it is mapped at that scale along the car. Both
    map y = TWO (1 - v), and the side is half as tall as the car is long."""
    c, h = TWO, TWO // 2
    d = root / "kits" / "two"
    (d / "overlays").mkdir(parents=True)
    Image.fromarray(np.full((c, c), 255, np.uint8)).save(d / "overlays" / "mask.png")
    ids = np.ones((c, c), np.uint8)
    ids[:, h:] = 2
    Image.fromarray(ids).save(d / "overlays" / "islands.png")
    (d / "regions.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg">'
        f'<path data-id="i001" d="M0,0 L{h},0 L{h},{c} L0,{c} Z"/>'
        f'<path data-id="i002" d="M{h},0 L{c},0 L{c},{c} L{h},{c} Z"/></svg>'
    )
    y = [c, 0, -c, 0, 0, 0]
    raw = {
        "canvas": c,
        "paint_folder": "two",
        "bleed_px": 0,
        "spec_presets": SPEC,
        "regions": [{"id": "i001", "view": "side_l"}, {"id": "i002", "view": "side_l"}],
        "side_frames": {"side_l": {"sill_sy": 300, "top_sy": 100, "nose_sx": 0, "tail_sx": 400}},
        "side_transforms": {
            "i001": {
                "view": "side_l",
                "coef": [[0, c, 0, 0, 0, 0], y],
                "domain": [[0, 0], [h, 0], [h, c], [0, c]],
                "car_domain": [[-1, -1], [0.5, -1], [0.5, 2], [-1, 2]],
            },
            "i002": {
                "view": "side_l",
                "coef": [[h - stretch * h, stretch * c, 0, 0, 0, 0], y],
                "domain": [[h, 0], [c, 0], [c, c], [h, c]],
                "car_domain": [[0.5, -1], [2, -1], [2, 2], [0.5, 2]],
            },
        },
    }
    write_kit(d, raw)
    return Workspace(root=root, assets=REPO / "assets")


# Per screen pixel: the texel's u and v (NaN off the car) and the Jacobian d(u, v)/d(x, y).
CapturedView = tuple[np.ndarray, np.ndarray, np.ndarray]


def capture(kit: str, views: dict[str, CapturedView], run: str = "run-1") -> Path:
    """A finished calibration capture of `kit` in the test's state dir, laid out as `kit capture` leaves
    one, and pointed at as its current one: per view, which texel each screen pixel samples and its
    footprint, d(u, v)/d(x, y)."""
    from livery import graycode, state

    d = state.root() / "kit-capture" / kit / run
    for name, (u, v, jac) in views.items():
        car = np.isfinite(u)
        n = int(car.sum())
        graycode.ViewMap(
            car=car,
            pix=car,
            uv=np.stack([u[car], v[car]], axis=1).astype(np.float32),
            jac=jac[car].astype(np.float32),
            level=np.zeros((n, 2), np.int8),
            conf=np.ones(n, np.float16),
            agree=np.ones(n, bool),
        ).save(d / name / "geometry.npz")
    state.write_json(d / "manifest.json", {"identity": {"views": {v: {} for v in views}}, "complete": True})
    (d.parent / "current").write_text(run, encoding="utf-8")
    return d


FOLD, SQUEEZE = TWO // 2, 2.5  # where view "head-on" folds, and how foreshortened the face beyond it is


def two_views() -> dict[str, CapturedView]:
    """Two views of the two-island kit. "head-on" sees the left island (x < TWO/2) at one texel per pixel
    down to v = FOLD, where the body folds into a face it sees at SQUEEZE texels per pixel along v.
    "edge-on" sees x from TWO/2 to 3/4 TWO at 4 texels per pixel along x. Nothing sees the last quarter."""
    ys, xs = np.mgrid[0:TWO, 0 : TWO // 2].astype(np.float64)
    beyond = ys >= FOLD
    v = np.where(beyond, FOLD + SQUEEZE * (ys - FOLD + 0.5), ys + 0.5)
    u = np.where(v < TWO, xs + 0.5, np.nan)
    jac = np.zeros((*u.shape, 2, 2))
    jac[..., 0, 0] = 1
    jac[..., 1, 1] = np.where(beyond, SQUEEZE, 1)
    head_on = (u, np.where(v < TWO, v, np.nan), jac)
    ys, xs = np.mgrid[0:TWO, 0 : TWO // 16].astype(np.float64)
    jac = np.zeros((*xs.shape, 2, 2))
    jac[..., 0, 0], jac[..., 1, 1] = 4, 1
    edge_on = (TWO // 2 + 4 * (xs + 0.5), ys + 0.5, jac)
    return {"head-on": head_on, "edge-on": edge_on}
