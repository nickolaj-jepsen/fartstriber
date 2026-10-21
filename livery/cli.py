import dataclasses
import functools
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer
from typer.core import TyperGroup

from livery.config import Workspace

OWNER = re.compile(r"\b[Tt]he owner\b")  # how help texts name the owner


def _name_owner(command: Any, name: str) -> None:
    """Writes `name` where the help of `command` and of everything under it says 'the owner'."""
    for item in (command, *command.params):
        for attr in ("help", "short_help"):
            text = getattr(item, attr, None)
            if isinstance(text, str):
                setattr(item, attr, OWNER.sub(name, text))
    for sub in getattr(command, "commands", {}).values():
        _name_owner(sub, name)


class _Root(TyperGroup):
    """The root command: each run names the owner from the settings in the help texts."""

    def main(self, *args: Any, **kwargs: Any) -> Any:
        from livery import config

        name = config.owner()
        if name != config.NO_OWNER:
            _name_owner(self, name)
        return super().main(*args, **kwargs)


app = typer.Typer(cls=_Root, no_args_is_help=True, help="Fartstriber: iRacing liveries designed as code.")
kit_app = typer.Typer(no_args_is_help=True, help="Car kits: add, extract, capture, fit, label, review, spec.")
preview_app = typer.Typer(no_args_is_help=True, help="Flat and in-sim previews.")
app.add_typer(kit_app, name="kit")
app.add_typer(preview_app, name="preview")
critic_app = typer.Typer(no_args_is_help=True, help="The critic's inputs and its recorded verdicts.")
app.add_typer(critic_app, name="critic")
sim_app = typer.Typer(no_args_is_help=True, help="The sim, as the next sim run would find it.")
app.add_typer(sim_app, name="sim")
references_app = typer.Typer(no_args_is_help=True, help="The pictures behind references.md.")
app.add_typer(references_app, name="references")
bake_app = typer.Typer(
    no_args_is_help=True, help="Sim-lit previews without the sim, from a kit's calibration capture."
)
app.add_typer(bake_app, name="bake")
config_app = typer.Typer(no_args_is_help=True, help="Your settings: config.toml in the state dir.")
app.add_typer(config_app, name="config")

Design = Annotated[str, typer.Argument(help="Design name under designs/.")]
Sets = Annotated[list[str] | None, typer.Option("--set", help="Try a param value without saving it: k=v.")]
Json = Annotated[Path, typer.Argument(exists=True, dir_okay=False)]
AsJson = Annotated[
    bool, typer.Option("--json", help="Print the result as one JSON object; progress goes to stderr.")
]
Baked = Annotated[
    bool,
    typer.Option(
        "--baked",
        help="Also render the preview views in sim light from the kit's calibration capture, once it passed "
        "livery bake check.",
    ),
]


def main() -> None:
    """Entry point: an error that says what to do is a message and an exit code, not a traceback."""
    from livery.errors import LiveryError

    try:
        app()
    except LiveryError as e:
        typer.echo(f"error: {e}", err=True)
        raise SystemExit(getattr(e, "exit_code", 1)) from None


def _json(path: Path) -> Any:
    """A verdict, ranking or answers file."""
    from livery.critic import BadVerdict

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise BadVerdict(f"{path} is not JSON: {e}") from None


def _emit(as_json: bool, data: Any, text: str | list[str] | None = None) -> None:
    """A command's result: as JSON with --json (or when it has no text form), else as text."""
    if as_json or text is None:
        typer.echo(json.dumps(data, indent=1, default=str))
    else:
        typer.echo(text if isinstance(text, str) else "\n".join(text))


def _progress(as_json: bool) -> Callable[[str], None]:
    """Where a long command says what it is doing: stderr under --json, so stdout is the JSON alone."""
    return functools.partial(typer.echo, err=as_json)


def _snap_line(s: dict) -> str:
    """One edge the library moved, as a line of the check and lint reports."""
    piece = f" ({s['piece']})" if "piece" in s else ""
    car = f", car {tuple(s['car'])}" if "car" in s else ""
    return (
        f"{s['id']}{piece}: {s['run']} px of edge moved {s['px']} px {s['to']} a {s['line']} at texture "
        f"{tuple(s['at'])}{car}"
    )


def _report(result: dict) -> dict:
    shown = {k: result.get(k) for k in ("design", "passed", "errors", "warnings")}
    shown["snaps"] = [_snap_line(s) for s in result.get("snaps", [])]
    return shown


def _built(result: dict) -> None:
    """The hashes a check or build ran on, for its telemetry event."""
    from livery import metrics

    metrics.note(design_hash=result.get("design_hash"), toolchain_hash=result.get("toolchain_hash"))


@kit_app.command("extract")
def kit_extract(
    psd: Path,
    car: Annotated[str, typer.Option(help="Kit name, e.g. mclaren-720s-gt3-evo.")],
    paint_folder: Annotated[str, typer.Option(help="The car's folder under Documents/iRacing/paint.")],
    as_json: AsJson = False,
) -> None:
    """Draft a kit from the paint template PSD."""
    from livery.kit_extract import extract

    out = extract(Workspace(), psd, car, paint_folder)
    _emit(as_json, {"kit": car, "out": str(out)}, str(out))


@kit_app.command("add")
def kit_add(
    psd: Path,
    car: Annotated[str, typer.Option(help="Kit name, e.g. mclaren-720s-gt3-evo.")],
    paint_folder: Annotated[str, typer.Option(help="The car's folder under Documents/iRacing/paint.")],
    as_json: AsJson = False,
) -> None:
    """Add a car: extract, profile, preview cameras, frame, calibration capture, fit and kit review.

    Each step runs once: run it again after it stops and it carries on where it stopped. It stops with the
    next step wherever it needs the owner (closing or reopening the replay, saving the track's camera set,
    the review) or a step can't run yet. Waits for the sim to close before camera.ini changes, and for the
    reopened replay before it picks the frame; the capture takes about 15 minutes. Run it in the background.
    """
    from livery import config
    from livery.kit_add import add

    result = add(config.load(), Workspace(), psd, car, paint_folder, log=_progress(as_json))
    text = [f"{step}: {what}" for step, what in result["steps"].items()]
    _emit(as_json, result, [*text, f"next: {result['next']}"] if result["next"] else text)


@kit_app.command("capture")
def kit_capture(
    car: str,
    resume: Annotated[
        bool, typer.Option(help="Carry on the last unfinished capture after its last finished paint.")
    ] = False,
    keep_frames: Annotated[bool, typer.Option(help="Keep the raw frames once they are decoded.")] = False,
    as_json: AsJson = False,
) -> None:
    """Shoot the car's calibration capture at its preview views: the Gray-code geometry and a grey ladder
    under each spec preset, decoded into kit-capture/<kit>/ in the machine state dir.

    Takes the sim lock per paint, so other sim runs queue between paints. About 12 minutes; never takes
    focus. Needs the preview replay loaded, not minimised.
    """
    from livery import calibrate, config, kit, state

    ws = Workspace()
    log = _progress(as_json)
    run = calibrate.capture(
        config.load(), ws, kit.load(ws, car), resume=resume, keep_frames=keep_frames, log=log
    )
    m = state.read_json(run / "manifest.json") or {}
    mb = round(m.get("stored_bytes", 0) / 1e6, 1)
    result = {"kit": car, "run": str(run), "stored_mb": mb, "summary": m.get("summary")}
    _emit(as_json, result, [str(run), f"stored {mb} MB", *calibrate.report(m.get("summary") or {})])


@kit_app.command("spec")
def kit_spec(
    car: str,
    preset: Annotated[
        list[str] | None,
        typer.Option(
            help="Set a spec preset first, material=R,G,B (metallic, roughness, clearcoat), and shoot its grey "
            "ladder again into the calibration capture. Needs the sim, like kit capture."
        ),
    ] = None,
    as_json: AsJson = False,
) -> None:
    """Write the kit's finish compensation from its calibration capture: per preset with metal in it, the
    paint each grey needs to show what gloss shows, per colour channel over the preview views."""
    from livery import config, kit
    from livery.kit_spec import lines, spec
    from livery.model import Material

    presets: dict[Material, tuple[int, int, int]] = {}
    for item in preset or []:
        name, _, value = item.partition("=")
        try:
            r, g, b = (int(x) for x in value.split(","))
            presets[Material(name.strip())] = (r, g, b)
        except ValueError:
            raise typer.BadParameter(f"--preset {item}: give material=R,G,B") from None
    ws = Workspace()
    result = spec(config.load(), ws, kit.load(ws, car), presets, log=_progress(as_json))
    _emit(as_json, result, lines(result))


@kit_app.command("fit")
def kit_fit(
    car: str,
    compare: Annotated[
        bool,
        typer.Option(
            help="Write nothing: say how the new maps compare with the current ones, island by island, and how "
            "much of each approved design they would move."
        ),
    ] = False,
    keep: Annotated[
        str | None, typer.Option(help="Keep the current maps of these islands (ids, comma-separated).")
    ] = None,
    as_json: AsJson = False,
) -> None:
    """Fit car-space maps from the kit's calibration capture and write maps.json, seams.json and
    fit-report.json. A fit raises the kit's version and clears its approval: new maps go back to review."""
    from livery import config, kit
    from livery.kit_fit import fit, lines

    ws = Workspace()
    keep_ids = {i.strip() for i in keep.split(",") if i.strip()} if keep else set()
    result = fit(config.load(), ws, kit.load(ws, car), dry_run=compare, keep=keep_ids, designs=compare)
    _emit(as_json, result, lines(result))


@kit_app.command("label")
def kit_label(
    car: str,
    parts: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="Parts, groups, notes (JSON).")],
    features: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="Features (JSON).")],
    as_json: AsJson = False,
) -> None:
    """Name the kit's parts and features: checks every island against the kit and every feature against its
    view's maps, and writes body.yaml. A change clears the kit's approval. Prints JSON."""
    from livery import body, kit

    ws = Workspace()
    _emit(as_json, body.label(kit.load(ws, car), _json(parts), _json(features)))


@kit_app.command("review")
def kit_review(
    car: str,
    sim: Annotated[bool, typer.Option(help="Also shoot the calibration livery in the sim.")] = True,
    as_json: AsJson = False,
) -> None:
    """Render the labelled overlays (islands by view; parts and features by name) and shoot the calibration
    livery in the sim, for the owner's review."""
    from livery import config, kit
    from livery.kit_review import body_overlay, overlay, shoot_calibration

    ws = Workspace()
    k = kit.load(ws, car)
    result: dict = {"kit": car, "overlay": str(overlay(k, ws)), "calibration": None}
    result["body"] = [str(p) for p in body_overlay(k, ws)]
    if not as_json:
        typer.echo("\n".join([result["overlay"], *result["body"]]))
    if sim:
        result["calibration"] = str(shoot_calibration(config.load(), k, ws, log=_progress(as_json)))
        if not as_json:
            typer.echo(result["calibration"])
    if as_json:
        _emit(True, result)


@app.command()
def check(design: Design, as_json: AsJson = False) -> None:
    """Lint the source, build, lint the output, and rebuild in a fresh process to prove it deterministic.

    Prints JSON.
    """
    from livery.check import run_check

    result = run_check(Workspace(), design)[0]
    _built(result)
    _emit(as_json, _report(result))
    if not result["passed"]:
        raise typer.Exit(1)


def _standing(row: dict) -> str:
    waits = f"; places {', '.join(row['needs_approval'])}, not approved" if row.get("needs_approval") else ""
    if row["status"] == "changed":
        moved = ", ".join(row["changed_by"]) or "?"
        paint, spec = row.get("paint_changed", "?"), row.get("spec_changed", "?")
        diff = row.get("diff", row.get("pictures"))
        return f" by {moved}: paint {paint}, spec {spec} of the car; {diff}{waits}"
    if row["status"] == "failed":
        return f": {row['errors'][0]}"
    return waits


@app.command()
def build(
    design: Annotated[str | None, typer.Argument(help="Design name under designs/.")] = None,
    all_designs: Annotated[
        bool, typer.Option("--all", help="Every design, each compared with its approval.")
    ] = False,
    as_json: AsJson = False,
) -> None:
    """Run check, then compile and rasterise. Writes nothing if check fails.

    With --all, builds every design and reports each one's standing against its approval: approved,
    changed since approval (by what, how much of the car, and a diff picture), unapproved or failed.
    Fixtures are listed apart and are 'built' rather than unapproved. It fails if a design failed or an
    approved one changed. Run it after any kit or library change. The report also goes to
    .cache/build-all.json.
    """
    from livery.check import run_build

    ws = Workspace()
    if not all_designs:
        if not design:
            raise typer.BadParameter("name a design, or pass --all")
        result = run_build(ws, design)
        _built(result)
        report = _report(result)
        if as_json:
            _emit(True, report | {"out": str(ws.out(design))})
        else:
            _emit(False, report)
            typer.echo(ws.out(design))
        if not result["passed"]:
            raise typer.Exit(1)
        return
    from livery import approvals

    report = approvals.standings(ws)
    text = []
    for role, heading in (("livery", "liveries"), ("fixture", "fixtures")):
        rows = {name: row for name, row in report.items() if row["role"] == role}
        if rows:
            text.append(heading)
        for name, row in rows.items():
            text.append(f"  {name:24} {row['status']}{_standing(row)}")
    out = ws.root / ".cache" / "build-all.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1), encoding="utf-8", newline="\n")
    _emit(as_json, report, text)
    if approvals.failing(report):
        raise typer.Exit(1)


@app.command()
def lint(design: Design, sets: Sets = None, as_json: AsJson = False) -> None:
    """Run every lint check without the determinism pass, and write lint.json."""
    from livery.lint.run import lint_design
    from livery.params import parse_sets

    ws = Workspace()
    report = lint_design(ws, design, parse_sets(sets))
    out = ws.out(design)
    out.mkdir(parents=True, exist_ok=True)
    (out / "lint.json").write_text(json.dumps(report, indent=1), encoding="utf-8", newline="\n")
    text = [
        f"{level[:-1]:7} {f['check']:15} {f['id']}: {f['message']}"
        for level in ("errors", "warnings")
        for f in report[level]
    ]
    text += [f"snap    {'near_tangent':15} {_snap_line(s)}" for s in report["snaps"]]
    text.append(
        f"{len(report['errors'])} errors, {len(report['warnings'])} warnings, {len(report['snaps'])} snaps "
        f"-> {out / 'lint.json'}"
    )
    _emit(as_json, report | {"out": str(out / "lint.json")}, text)
    if report["errors"]:
        raise typer.Exit(1)


@preview_app.command("flat")
def preview_flat(design: Design, sets: Sets = None, baked: Baked = False, as_json: AsJson = False) -> None:
    """Render the car views, the UI's 3/4 car view, a squint thumbnail and the texture overlay into flat/.

    The car views are the sides and the top, unwrapped through the kit. With --baked, also every preview
    view as the sim would show it, from the kit's calibration capture, into baked/ (<view>.png and a
    sheet.png of them). With --set the pictures go to flat-sets/<key>/ (and baked-sets/<key>/) instead, so
    flat/ and baked/ always show the script as it is. Each folder's build.json says which build it shows.
    """
    from livery import metrics, stamps
    from livery.params import parse_sets
    from livery.preview_flat import flat

    ws = Workspace()
    parsed = parse_sets(sets)
    out = flat(ws, design, parsed, with_baked=baked)
    stamp = stamps.read(out / stamps.STAMP) or {}
    metrics.note(design_hash=stamp.get("design_hash"), toolchain_hash=stamp.get("toolchain_hash"))
    result: dict = {"design": design, "out": str(out), "stamp": stamp}
    text = [str(out)]
    if baked:
        result["baked"] = str(stamps.baked_dir(ws, design, parsed))
        text.append(result["baked"])
    _emit(as_json, result, text)


@preview_app.command("sim")
def preview_sim(design: Design, as_json: AsJson = False) -> None:
    """Install, reload and shoot every preset in the sim, then put the paint back. Needs a checked build.

    Queues behind any other sim run. Never takes focus; needs the preview replay loaded, not minimised.
    """
    from livery import config, metrics, stamps
    from livery.preview_sim import preview_sim as run

    shots = run(config.load(), Workspace(), design, log=_progress(as_json))
    manifest = stamps.of_run(shots) or {}
    metrics.note(design_hash=manifest.get("design_hash"), toolchain_hash=manifest.get("toolchain_hash"))
    _emit(as_json, {"design": design, "shots": str(shots), "manifest": manifest}, str(shots))


@preview_app.command("cameras")
def preview_cameras(
    car: Annotated[str | None, typer.Argument(help="The kit whose car the preview replay shows.")] = None,
    restore: Annotated[bool, typer.Option(help="Put the track's stock camera set back.")] = False,
    arm: Annotated[
        bool, typer.Option(help="If camera.ini has to change, wait for the sim to close, then write.")
    ] = False,
    reset: Annotated[
        list[str] | None,
        typer.Option(
            help="Pose this view of the kit at its default again, in a new camera group (repeatable)."
        ),
    ] = None,
    as_json: AsJson = False,
) -> None:
    """Add the car-locked preview cameras to the track camera set of the kit's replay in the sim's documents.

    The set gets the preview cameras of every kit shot on that track, so adding one kit keeps the others'.
    The poses come from each kit's profile; one it lacks, or one --reset names, gets the default sized for
    its car.

    The sim reads the set when a session opens: open the replay again afterwards. With --arm it waits, when
    camera.ini has to change, until the sim is closed, so one close and reopen loads the cameras.
    """
    from livery import cameras, config, kit
    from livery.iracing import sim_running

    cfg = config.load()
    ws = Workspace()
    track = kit.track_of(kit.profile(kit.load(ws, car)), cfg) if car else cfg.replay.track
    log = _progress(as_json)
    if restore:
        notes = cameras.restore_previews(cfg.documents, track, sim_running(cfg))
    elif not car:
        raise typer.BadParameter("name the kit, or pass --restore")
    else:
        if reset:
            cameras.reset_views(kit.load(ws, car), reset)
        views = cameras.track_views(ws, cfg, track, ws.policy.preview)
        running = (
            cameras.armed(cfg.documents, track, lambda: sim_running(cfg), log) if arm else sim_running(cfg)
        )
        notes = cameras.install_previews(cfg.documents, track, views, running)
    _emit(as_json, {"notes": notes}, notes)


@app.command()
def sheet(
    designs: Annotated[list[str], typer.Argument(help="One or more design names.")],
    wedge: Annotated[str | None, typer.Option(help="k=a..b..step")] = None,
    seeds: Annotated[str | None, typer.Option(help="a..b")] = None,
    sets: Sets = None,
    baked: Annotated[
        bool,
        typer.Option(
            "--baked",
            help="With --wedge or --seeds: tile the preview views in sim light, from the kit's calibration "
            "capture, instead of the flat views.",
        ),
    ] = False,
    as_json: AsJson = False,
) -> None:
    """Tile several designs on one sheet, or one design at each value of a wedge or seed range.

    With --baked, a wedge or seed sheet shows each value at the preview views as the sim would light it
    (<sheet>-baked.png). Each sheet's <sheet>.build.json says which build each tile shows.
    """
    from livery import preview_flat
    from livery.params import parse_sets, parse_wedge

    ws = Workspace()
    sheets = []
    if wedge or seeds:
        if len(designs) != 1:
            raise typer.BadParameter("--wedge and --seeds take exactly one design")
        if wedge:
            parse_wedge(wedge)  # a malformed spec fails here, before anything renders
            sheets.append(preview_flat.wedge(ws, designs[0], wedge, parse_sets(sets), with_baked=baked))
        if seeds:
            sheets.append(preview_flat.seeds(ws, designs[0], seeds, parse_sets(sets), with_baked=baked))
    elif baked:
        raise typer.BadParameter("--baked takes --wedge or --seeds")
    else:
        sheets.append(preview_flat.contact_sheet(ws, designs))
    _emit(as_json, {"designs": designs, "sheets": [str(s) for s in sheets]}, [str(s) for s in sheets])


@app.command()
def review(
    designs: Annotated[
        list[str] | None,
        typer.Argument(
            help="Designs: several are concepts, one is a final. With --cancel, review ids from --list."
        ),
    ] = None,
    kit: Annotated[str | None, typer.Option(help="Review a kit's overlay and calibration shots.")] = None,
    roster: Annotated[bool, typer.Option(help="Accept or reject proposed brands in roster.md.")] = False,
    sponsors: Annotated[bool, typer.Option(help="Review drawn brands not yet approved.")] = False,
    brand: Annotated[list[str] | None, typer.Option(help="With --sponsors: only these brands.")] = None,
    marks: Annotated[bool, typer.Option(help="Review drawn race markings not yet approved.")] = False,
    references: Annotated[bool, typer.Option(help="Mark references.md entries yes or no.")] = False,
    proposals: Annotated[
        Path | None,
        typer.Option(
            exists=True, dir_okay=False, help="A retro's proposed edits: a JSON list of {title, body}."
        ),
    ] = None,
    list_: Annotated[
        bool, typer.Option("--list", help="List every review on the page: waiting, stale or applied.")
    ] = False,
    cancel: Annotated[
        bool,
        typer.Option(
            "--cancel",
            help="Take reviews off the page (those named, else this checkout's); one still waiting exits.",
        ),
    ] = False,
    port: int = 8765,
    as_json: AsJson = False,
) -> None:
    """Queue a review on the page every session shares, block until the owner presses Apply on it, print the
    decisions as JSON.

    A final shows the checked build: its flat preview is rendered again when it shows another build, and
    sim shots of another build are marked. Apply writes what review owns in this checkout, appends every
    decision to prefs/decisions.jsonl and commits what it wrote with each decided design's folder. The JSON
    lists the proposals the owner accepted, for the agent to make.
    """
    from livery import marks as race_marks
    from livery.review import items as ri
    from livery.review import queue as rq
    from livery.review.server import serve

    ws = Workspace()
    if list_:
        rows = [rq.summary(g) for g in rq.groups()]
        lines = [
            f"{r['id']}  {r['status']}  {r['branch'] or r['root']}  {r['decided']}/{len(r['items'])} decided: "
            + ", ".join(r["items"])
            for r in rows
        ]
        return _emit(as_json, rows, lines or ["no reviews on the page"])
    if cancel:
        ids = designs or [g["id"] for g in rq.mine(ws.root)]
        gone = [gid for gid in ids if rq.cancel(gid)]
        return _emit(
            as_json, {"cancelled": gone}, [f"cancelled {gid}" for gid in gone] or ["nothing to cancel"]
        )
    queue = []
    if kit:
        queue += ri.kit_items(ws, kit)
    if roster:
        queue += ri.roster_items()
    if sponsors:
        queue += ri.sponsor_items(ws, only=brand)
    if marks:
        queue += race_marks.review_items()
    if designs:
        queue += ri.design_items(ws, designs)
    if references:
        queue += ri.reference_items(ws.references)
    if proposals:
        queue += ri.proposal_items(proposals)
    if not queue:
        raise typer.BadParameter(
            "nothing to review: name designs or pass --kit, --roster, --sponsors, --marks, --references or"
            " --proposals"
        )
    result = serve(ws, queue, port=port, on_ready=lambda m: typer.echo(m, err=True), commit=True)
    _emit(as_json, result)


@app.command()
def install(
    design: Annotated[str | None, typer.Argument()] = None,
    restore: Annotated[bool, typer.Option("--restore", help="Put back the pre-install paint files.")] = False,
    finish: Annotated[
        bool, typer.Option("--finish", help="Only archive fresh .mips and remove their TGAs.")
    ] = False,
    unapproved: Annotated[
        bool, typer.Option("--unapproved", help="Install a build the owner hasn't approved.")
    ] = False,
    as_json: AsJson = False,
) -> None:
    """Install the final paint into the paint folder, under the solo and the team name.

    Needs a checked build, and the one the owner approved in review, placing only approved roster sponsor
    variants and race markings, unless --unapproved. Holds the sim lock throughout, as --restore and --finish
    do; warns while Trading Paints Downloader runs.
    """
    from livery import config
    from livery import install as inst

    cfg = config.load()
    log = _progress(as_json)
    if restore:
        notes = inst.restore(cfg, log=log)
    elif not design:
        raise typer.BadParameter("name a design, or pass --restore")
    elif finish:
        notes = inst.finish(cfg, Workspace(), design, log=log)
    else:
        notes = inst.install(cfg, Workspace(), design, log=log, unapproved=unapproved)
    if as_json:
        _emit(True, {"design": design, "notes": notes})


@sim_app.command("status")
def sim_status(as_json: AsJson = False) -> None:
    """What the next sim run would find, read without sending the sim anything.

    The replay file against its sha256, the SDK connection, the replay against your settings, the car and
    its paint names, its kit's frame and its preview and measurement camera groups, the sim lock's holder,
    paint left by a run that died, Trading Paints Downloader, and the sim window (in front, minimised, the
    cursor over it).
    """
    from livery import config
    from livery.simsession import status

    found = status(config.load(), Workspace())
    _emit(as_json, {"status": found}, found)


@sim_app.command("pick-frame")
def sim_pick_frame(
    car: Annotated[str, typer.Argument(help="The kit whose car the open replay shows.")],
    check: Annotated[
        bool, typer.Option("--check", help="Only shoot every preview view in two orders and compare.")
    ] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Pick, but leave the profile alone.")] = False,
    as_json: AsJson = False,
) -> None:
    """Pick the replay frame the kit's car is shot at, on the open replay, and write it into the kit's profile.

    Scans the replay's telemetry for the car on a straight, shoots the candidates from every preview camera
    with the car painted white and black, and keeps the straightest frame where the car is whole, centred
    and uncovered in every view. Cameras that don't fit the car get resized in the profile instead: install
    them and reopen the replay, then pick again. --check paints nothing: it shoots every preview view at the
    profile's frame in two orders and fails if they differ. Queues behind other sim runs; never takes focus.
    """
    from livery import config, kit, pick_frame

    ws = Workspace()
    k = kit.load(ws, car)
    log = _progress(as_json)
    if check:
        report = pick_frame.check(config.load(), ws, k, log=log)
        text = [f"{v}: {pct}% differs" for v, pct in report["views"].items()]
        _emit(as_json, report, [*text, "passed" if report["passed"] else "failed", report["out"]])
        if not report["passed"]:
            raise typer.Exit(1)
        return
    report = pick_frame.pick(config.load(), ws, k, log=log, write=not dry_run)
    if "resized" in report:
        text = [f"resized the cameras; next: {report['next']}"]
    else:
        text = [f"frame {report['frame']}" + ("" if dry_run else f" -> {report.get('written')}")]
    _emit(as_json, report, [*text, report["out"]])


@config_app.command("show")
def config_show(as_json: AsJson = False) -> None:
    """Print the settings every worktree reads, the owner's name among them, and the file they are in."""
    from livery import config

    settings = {"file": config.user_file(), **dataclasses.asdict(config.load())}
    text = []
    for key, value in settings.items():
        if isinstance(value, dict):
            text += [f"{key}.{k}: {v}" for k, v in value.items()]
        else:
            text.append(f"{key}: {value}")
    _emit(as_json, settings, text)


@app.command()
def status(
    designs: Annotated[list[str] | None, typer.Argument(help="Designs [default: every design].")] = None,
    as_json: AsJson = False,
) -> None:
    """Where each design stands, without building anything.

    Whether its check is current (or which hash moved since), its approval and its kit's, whether its flat
    preview and sim shots show the checked build, and the critic's last round with its open fixes.
    """
    from livery import status as st

    rows = st.every(Workspace(), designs)
    _emit(as_json, rows, [line for row in rows for line in st.lines(row)])


@app.command()
def stats(as_json: AsJson = False) -> None:
    """The loop's numbers: sim runs and rounds per design, the critic against the owner's decisions, what
    the lint missed by kind, time per stage and waits for the sim lock.

    From the committed verdicts and decisions, and this machine's telemetry in the state dir.
    """
    from livery import stats as st

    report = st.stats(Workspace())
    _emit(as_json, report, st.lines(report))


@app.command()
def export(
    design: Design,
    out: Annotated[
        Path | None,
        typer.Option(help="Folder to write into [default: ~/Documents/Fartstriber/export/<design>]"),
    ] = None,
    unapproved: Annotated[
        bool, typer.Option("--unapproved", help="Export a build the owner hasn't approved.")
    ] = False,
    as_json: AsJson = False,
) -> None:
    """Write the approved build's paint and spec under Trading Paints' upload names, for a manual upload.

    The spec .mip comes from the archive `livery install` keeps; prints the folder and what to upload.
    """
    from livery import config
    from livery.export import export as run

    folder, message = run(config.load(), Workspace(), design, out=out, unapproved=unapproved)
    _emit(as_json, {"design": design, "out": str(folder), "message": message}, message)


@critic_app.command("pack")
def critic_pack(
    designs: Annotated[
        list[str], typer.Argument(help="One design for a round, several for concept ranking.")
    ],
    compare: Annotated[
        bool, typer.Option("--compare", help="This round's shots beside the last round's, in both orders.")
    ] = False,
    adversary: Annotated[
        bool, typer.Option("--adversary", help="This round's shots for the adversary check.")
    ] = False,
    pairs: Annotated[
        bool, typer.Option("--pairs", help="The concepts two at a time, every pair in both orders.")
    ] = False,
    baked: Annotated[
        bool,
        typer.Option(
            "--baked",
            help="A middle round: baked renders of the checked build in place of sim shots. Never ships.",
        ),
    ] = False,
    as_json: AsJson = False,
) -> None:
    """Gather what the critic may see into a folder, never the script, and print its path.

    A round: the checked build's sim shots with their distance images (shots of another build are
    refused), the close crops, the squint, the brief and the pictures of the reference rows it names, the
    boring anchors, the rubric, taste table, references and prefs, the earlier verdicts with the builder's
    answers and the owner's latest note. --adversary packs the same without the history or the note. --compare prints two packs: pass
    the critics' answers to `critic compare` in the order printed. --pairs prints a folder of packs named
    A__B (flat views, car view, squint): save each answer as A__B.json in one folder for `critic rank
    --pairs`. Several designs and no flag is the legacy concept ranking on sim shots.

    --baked, with a round, --compare or --adversary, packs baked renders of the checked build from the kit's
    calibration capture (once it passed livery bake check) in place of sim shots, marked as baked in the
    manifest and in the verdict: a middle round of the autonomous loop. `critic status` never ships a round
    judged on them.
    """
    from livery import critic

    ws = Workspace()
    if compare + adversary + pairs > 1:
        raise typer.BadParameter("pass at most one of --compare, --adversary and --pairs")
    if (compare or adversary) and len(designs) != 1:
        raise typer.BadParameter("--compare and --adversary take exactly one design")
    if baked and (pairs or len(designs) != 1):
        raise typer.BadParameter("--baked takes one design: a round, --compare or --adversary")
    if compare:
        packs = list(critic.pack_compare(ws, designs[0], baked=baked))
        mode = "compare"
    elif pairs:
        packs, mode = [critic.pack_pairs(ws, designs)], "pairs"
    else:
        mode = "adversary" if adversary else "concepts" if len(designs) > 1 else "round"
        warn = functools.partial(typer.echo, err=True)
        packs = [critic.pack(ws, designs, mode=mode, warn=lambda m: warn(f"warning: {m}"), baked=baked)]
    _emit(
        as_json,
        {"designs": designs, "mode": mode, "baked": baked, "packs": [str(p) for p in packs]},
        [str(p) for p in packs],
    )


@critic_app.command("record")
def critic_record(design: Design, verdict: Json, as_json: AsJson = False) -> None:
    """Validate a round verdict against the last round and the pack it judged, and record it.

    It goes to the design's verdicts.jsonl with its round, the pack's design hash, shots, rubric version
    and critic model. Ship is computed afterwards, by `critic status`.
    """
    from livery import metrics
    from livery.critic import record

    result = record(Workspace(), design, _json(verdict))
    metrics.note(design_hash=result.get("design_hash"))
    open_fixes = sum(not f["optional"] for f in result["fixes"])
    failed = [t["id"] for t in result["taste"] if not t["pass"]]
    text = f"{design}: round {result['round']}, {result['verdict']}, {open_fixes} open fixes, taste failing: {failed or 'none'}"
    _emit(as_json, result, text)


@critic_app.command("compare")
def critic_compare(design: Design, first: Json, second: Json, as_json: AsJson = False) -> None:
    """Record the critics' answers to the two --compare packs, in the order the pack printed them.

    This round is better, worse or the same as the last; orders that disagree count as the same.
    """
    from livery.critic import record_compare

    result = record_compare(Workspace(), design, _json(first), _json(second))
    _emit(as_json, result, f"{design}: {result['result']} than the last round")


@critic_app.command("adversary")
def critic_adversary(design: Design, verdict: Json, as_json: AsJson = False) -> None:
    """Record the adversary critic's findings on this round's shots, the most boring first."""
    from livery.critic import record_adversary

    result = record_adversary(Workspace(), design, _json(verdict))
    top = result["findings"][0]
    text = f"{design}: top finding {'optional' if top['optional'] else 'not optional'} ({top['view']})"
    _emit(as_json, result, text)


@critic_app.command("status")
def critic_status(design: Design, as_json: AsJson = False) -> None:
    """Print whether the design ships, and why not, as JSON.

    Ship: the last round has no open fix that isn't optional, nothing blocked, every taste row passing
    and the owner's latest note resolved, it isn't worse than the round before, and the adversary's top
    finding is optional.
    Otherwise the critic's call (iterate or rework) stands.
    """
    from livery.critic import status

    _emit(as_json, status(Workspace(), design))


@critic_app.command("rank")
def critic_rank(
    designs: Annotated[list[str], typer.Argument(help="The concepts the critic ranked.")],
    verdict: Annotated[
        Path | None,
        typer.Option(exists=True, dir_okay=False, help="The critic's legacy concept-mode JSON."),
    ] = None,
    pairs: Annotated[
        Path | None,
        typer.Option(exists=True, file_okay=False, help="A folder of pair answers, one A__B.json per pack."),
    ] = None,
    as_json: AsJson = False,
) -> None:
    """Record a concept ranking in each concept's verdicts.jsonl, for comparison with the owner's pick.

    With --pairs, the ranking is a Bradley-Terry fit to the pair answers.
    """
    from livery.critic import record_pairs, record_ranking

    ws = Workspace()
    if pairs and not verdict:
        result = record_pairs(ws, designs, pairs)
    elif verdict and not pairs:
        result = record_ranking(ws, designs, _json(verdict))
    else:
        raise typer.BadParameter("pass --verdict or --pairs")
    _emit(as_json, result, " > ".join(result["ranking"]))


@critic_app.command("answer")
def critic_answer(design: Design, answers: Json, as_json: AsJson = False) -> None:
    """Record the builder's reply to each fix of the last verdict, and print the blocked ones as JSON.

    The answers file is a JSON list of {status, note}, one per fix, each status done, blocked or skipped.
    Blocked fixes go to the owner.
    """
    from livery.critic import answer

    blocked = answer(Workspace(), design, _json(answers))
    _emit(as_json, {"design": design, "blocked": blocked})


@references_app.command("fetch")
def references_fetch(as_json: AsJson = False) -> None:
    """Cache the yes rows' Trading Paints showroom pictures and the boring anchors in .cache/references.

    Pictures already there are skipped. A failed page or picture is reported, and the rest carry on.
    Prints JSON.
    """
    from livery.references import fetch

    report = fetch(Workspace(), log=lambda m: typer.echo(m, err=True))
    _emit(as_json, report)


@bake_app.command("check")
def bake_check(
    car: Annotated[str, typer.Argument(help="The kit whose calibration capture is checked.")],
    design: Annotated[
        str | None, typer.Argument(help="A design to check as well as the kit's calibration fixture.")
    ] = None,
    as_json: AsJson = False,
) -> None:
    """Hold the bake against the sim: render the calibration fixture (and a design) baked, shoot each build
    with one real preview sim run, and compare them outside a 2 px band along the silhouette.

    Prints the median and p95 per channel at each view, writes a side by side and an error picture per
    design into the capture's check/ folder, and records the result in the capture's manifest. Fails if any
    view's p95 is over 12. Builders and middle critic rounds use baked views only after a check passed.
    Queues behind any other sim run; never takes focus.
    """
    from livery import bake, config, kit
    from livery.designs import kit_name, script

    ws = Workspace()
    k = kit.load(ws, car)
    fixture = "calibration"  # designs/calibration: the calibration livery, for the kit it names
    designs = (
        [fixture]
        if (ws.design_dir(fixture) / "design.py").exists() and kit_name(script(ws, fixture)) == car
        else []
    )
    designs += [design] if design and design not in designs else []
    if not designs:
        raise typer.BadParameter(f"the calibration fixture is drawn for another kit: name a design on {car}")
    result = bake.check(config.load(), ws, k, designs, log=_progress(as_json))
    text = [
        f"{name} {view}: median {s['median']}, p95 {s['p95']}"
        for name, r in result["designs"].items()
        for view, s in r["views"].items()
    ]
    text += [f"pictures: {r['pictures']['side_by_side']}" for r in result["designs"].values()]
    worst = result["worst"]
    verdict = "passed" if result["passed"] else "failed"
    text.append(f"{verdict}: the worst p95 is {worst['p95']} ({worst.get('design')} {worst.get('view')})")
    _emit(as_json, result, text)
    if not result["passed"]:
        raise typer.Exit(1)


@bake_app.command("status")
def bake_status(
    car: Annotated[str | None, typer.Argument(help="The kit.")] = None,
    design: Annotated[str | None, typer.Option(help="The kit this design is drawn for.")] = None,
    as_json: AsJson = False,
) -> None:
    """Whether the kit's calibration capture can bake previews: what changed since it was shot, and its last
    bake check. `checked` is true when baked views may be used."""
    from livery import bake, config, kit
    from livery.designs import kit_name, script

    ws = Workspace()
    if (car is None) == (design is None):
        raise typer.BadParameter("name a kit, or pass --design")
    k = kit.load(ws, car or kit_name(script(ws, str(design))))
    found = bake.status(config.load(), ws, k)
    check = found["check"] or {}
    text = [
        f"capture: {found['capture'] or 'none'}",
        f"changed since: {', '.join(found['stale']) or 'nothing'}",
        "bake check: "
        + (
            f"{'passed' if check.get('passed') else 'failed'} {check.get('at')}, worst p95 "
            f"{(check.get('worst') or {}).get('p95')}"
            if check
            else "none"
        ),
        f"baked views usable: {'yes' if found['checked'] else 'no'}",
    ]
    _emit(as_json, found, text)


# Telemetry: every command, however it ends, is one event in the state dir (livery.metrics).


def _logged(name: str, f: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(f)
    def run(*args: Any, **kwargs: Any) -> Any:
        from livery import metrics

        design = kwargs.get("design") or kwargs.get("designs")
        with metrics.command(name, sys.argv[1:], design, kwargs.get("car") or _kit(design)):
            return f(*args, **kwargs)

    return run


def _kit(design: Any) -> str | None:
    """The kit a design names, read from its source; nothing for several designs or none."""
    if not isinstance(design, str):
        return None
    from livery.designs import kit_name, script

    try:
        return kit_name(script(Workspace(), design))
    except Exception:
        return None


def _log_every_command(group: typer.Typer, prefix: str = "") -> None:
    for c in group.registered_commands:
        if c.callback is not None:
            name = prefix + (c.name or c.callback.__name__.lower().replace("_", "-"))
            c.callback = _logged(name, c.callback)
    for g in group.registered_groups:
        if g.typer_instance is not None:
            _log_every_command(g.typer_instance, f"{prefix}{g.name} ")


_log_every_command(app)
