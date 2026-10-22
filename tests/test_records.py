"""Previews stamped with the build they show, `livery status`, telemetry and `livery stats`."""

import itertools
import json
from pathlib import Path

import pytest
from PIL import Image

from livery import approvals, critic, metrics, prefs, preview_flat, stamps, stats, status
from livery.check import run_build
from livery.config import Workspace
from livery.review import items as review_items
from tests.conftest import checked, run_cli, sim_run, write_design

BLUE = 'def build(kit, p):\n    return Livery(base="#3060c0")\n'
RED = 'def build(kit, p):\n    return Livery(base="#c03030")\n'
SEEDED = (
    "def build(kit, p):\n"
    '    r, g, b = (int(x) for x in p.rng("base").integers(0, 255, 3))\n'
    '    return Livery(base=f"#{r:02x}{g:02x}{b:02x}")\n'
)

pytestmark = pytest.mark.usefixtures("cheap_flat")


# Stamps


def test_flat_previews_name_the_build_they_show_and_a_set_never_lands_in_flat(ws):
    write_design(ws, "d", SEEDED)
    digests = run_build(ws, "d")["digests"]

    out = preview_flat.flat(ws, "d")
    varied = preview_flat.flat(ws, "d", {"seed": "3"})

    assert stamps.shows(stamps.of_flat(ws, "d"), digests)
    assert varied != out
    variant = stamps.read(varied / stamps.STAMP)
    assert variant
    assert variant["sets"] == {"seed": "3"}
    assert not stamps.shows(variant, digests)
    assert stamps.of_version(stamps.of_flat(ws, "d"), stamps.hashes(ws, "d"))


def test_a_sheet_names_the_build_of_every_tile(ws):
    write_design(ws, "d", SEEDED)

    sheet = preview_flat.seeds(ws, "d", "1..3")

    stamp = stamps.read(sheet.with_suffix(".build.json"))
    assert stamp
    tiles = stamp["tiles"]
    assert [t["set"] for t in tiles] == [{"seed": "1"}, {"seed": "2"}, {"seed": "3"}]
    assert len({t["digests"]["car.tga"] for t in tiles}) == 3


def test_a_stale_flat_preview_is_rendered_again_for_the_build_it_must_show(ws):
    digests = checked(ws, "d", render=True)["digests"]
    write_design(ws, "d", RED)
    preview_flat.flat(ws, "d")
    write_design(ws, "d", BLUE)
    assert not stamps.shows(stamps.of_flat(ws, "d"), digests)

    preview_flat.fresh(ws, "d", digests)

    assert stamps.shows(stamps.of_flat(ws, "d"), digests)


def test_a_final_review_shows_the_checked_builds_previews_and_marks_other_sim_shots(ws):
    digests = checked(ws, "d", render=True)["digests"]
    write_design(ws, "d", RED)
    preview_flat.flat(ws, "d")
    write_design(ws, "d", BLUE)
    sim_run(ws, "d", "20260101-000000", None)

    item = review_items.design_items(ws, ["d"])[0]

    assert stamps.shows(stamps.of_flat(ws, "d"), digests)
    assert "20260101-000000" in item["body"]  # shots from an unknown build, marked as such
    sim_run(ws, "d", "20260102-000000", digests)
    sim_run(ws, "d", "20260103-000000", {"car.tga": "newer", "car_spec.tga": "newer"})

    item = review_items.design_items(ws, ["d"])[0]

    assert any("20260102-000000" in i for i in item["images"])
    assert not any("20260103-000000" in i for i in item["images"])
    assert "20260103-000000" not in item["body"]


def test_a_review_shows_the_sim_shots_in_the_policys_view_order_then_the_flat_pictures(ws):
    digests = checked(ws, "d", render=True)["digests"]
    run = sim_run(ws, "d", "20260101-000000", digests)
    views = ws.policy.preview
    for view in views:
        Image.new("RGB", (90, 45), "purple").save(run / f"{view}.png")

    item = review_items.design_items(ws, ["d"])[0]

    shots = [p for p in item["images"] if "20260101-000000" in p]
    assert [Path(p).stem for p in shots] == list(views)
    assert item["images"][: len(shots)] == shots
    assert {Path(p).name for p in item["images"][len(shots) :]} >= {"views.png", "car.png", "squint.png"}
    assert len(set(item["captions"].values())) == len(item["images"])


# Status


def test_status_follows_a_design_from_script_to_approval_without_building(ws):
    write_design(ws, "d", BLUE)
    first = status.design(ws, "d")
    assert (first["check"]["state"], first["approval"]["state"], first["flat"]["state"]) == (
        "none",
        "none",
        "none",
    )

    digests = checked(ws, "d", render=True)["digests"]
    preview_flat.flat(ws, "d")
    sim_run(ws, "d", "20260101-000000", digests)
    now = status.design(ws, "d")
    assert (now["check"]["state"], now["flat"]["state"], now["sim"]["state"]) == (
        "current",
        status.THIS,
        status.THIS,
    )
    assert now["check"]["built"]

    write_design(ws, "d", RED)
    edited = status.design(ws, "d")
    assert edited["check"] == {"state": "stale", "moved": ["design"]}
    assert edited["sim"]["state"] == status.OTHER


def test_status_reports_the_approval_and_the_critics_open_fixes(ws):
    check = checked(ws, "d")
    approved = ws.design_dir("d") / "approved.json"
    approved.write_text(json.dumps({"date": "2026-10-20", "digests": check["digests"]}), encoding="utf-8")
    fix = {"text": "the nose is plain", "optional": False}
    polish = {"text": "a thinner pinline", "optional": True}
    (ws.design_dir("d") / "verdicts.jsonl").write_text(
        json.dumps(
            {"kind": "round", "round": 1, "at": "2026-10-20 10:00", "verdict": "iterate", "overall": 7}
            | {"design_hash": check["design_hash"], "fixes": [fix, polish], "answers": [{"status": "done"}]}
        )
        + "\n",
        encoding="utf-8",
    )

    row = status.design(ws, "d")

    assert row["approval"] == {"state": status.THIS, "date": "2026-10-20"}
    assert row["critic"]["on_this_version"]
    assert row["critic"]["open_fixes"] == [{"text": fix["text"], "answer": "done"}]
    assert approvals.load_approval(ws, "d")  # status reads, never writes


def test_status_and_stats_print_json_on_request(monkeypatch, ws, capsys):
    checked(ws, "d")
    assert run_cli(monkeypatch, ws, "status", "--json") == 0
    assert [row["design"] for row in json.loads(capsys.readouterr().out)] == ["d"]
    assert run_cli(monkeypatch, ws, "stats", "--json") == 0
    assert "designs" in json.loads(capsys.readouterr().out)
    assert run_cli(monkeypatch, ws, "lint", "d", "--json") == 0
    assert json.loads(capsys.readouterr().out)["errors"] == []


# Telemetry


def test_every_command_is_one_event_however_it_ends(monkeypatch, ws):
    write_design(ws, "d", BLUE)
    write_design(ws, "broken", "def build(kit, p)\n    return None\n")
    monkeypatch.setenv(metrics.SESSION[0], "session-1")

    assert run_cli(monkeypatch, ws, "lint", "d") == 0
    assert run_cli(monkeypatch, ws, "check", "broken") == 1
    assert run_cli(monkeypatch, ws, "lint", "nope") == 2

    events = [e for e in metrics.read() if e.get("event") == "command"]
    assert [(e["command"], e["design"], e["outcome"]) for e in events] == [
        ("lint", "d", "ok"),
        ("check", "broken", "failed"),
        ("lint", "nope", "error"),
    ]
    assert events[0]["kit"] == "tiny"
    assert events[0]["session"] == "session-1"
    assert events[2]["error"] == "DesignNotFound"
    assert all(e["durations_s"]["total"] >= 0 for e in events)


def test_lines_logged_during_a_command_carry_its_run_and_become_its_stages():
    with metrics.command("preview sim", [], "d") as event:
        metrics.log("d", "sim", {"reload": 2.0, "total": 9.0})
        with metrics.stage("copy"):
            pass

    line = next(r for r in metrics.read() if r.get("loop") == "sim")
    written = next(r for r in metrics.read() if r.get("event") == "command")
    assert line["run"] == written["run"] == event["run"]
    assert written["durations_s"]["reload"] == 2.0
    assert "copy" in written["durations_s"]
    assert written["durations_s"]["total"] < 9.0  # its own time, not the line's


def test_the_old_metrics_file_is_copied_in_once_and_left_in_place(monkeypatch, tmp_path):
    legacy = tmp_path / "checkout" / "runs" / "metrics.jsonl"
    legacy.parent.mkdir(parents=True)
    old = {
        "design": "d",
        "timestamp": "2026-10-18T12:12:40+0200",
        "loop": "sim",
        "shots": ".cache\\designs\\d",
    }
    legacy.write_text(json.dumps(old) + "\n", encoding="utf-8")
    monkeypatch.setattr(metrics, "LEGACY", legacy)

    metrics.log("d", "flat", {"total": 1.0})
    metrics.log("d", "flat", {"total": 1.0})

    rows = metrics.read()
    assert [r["loop"] for r in rows] == ["sim", "flat", "flat"]
    assert rows[0]["shots"] == ".cache/designs/d"
    assert legacy.exists()


# Stats


def _round(at: str, overall: int, call: str, **extra) -> dict:
    return {"kind": "round", "at": at, "overall": overall, "verdict": call, "fixes": []} | extra


def _ledger(ws: Workspace, lines: list[dict]) -> None:
    prefs.append(ws, lines)


def test_stats_sets_the_critic_beside_his_decisions_and_counts_the_loops(ws, tmp_path):
    for name in ("a", "b", "c"):
        write_design(ws, name, BLUE)
    best, answers = "bac", tmp_path / "pairs"  # the critics' ranking, and their answers
    answers.mkdir()
    for x, y in itertools.permutations("abc", 2):
        winner = "A" if best.index(x) < best.index(y) else "B"
        (answers / f"{x}{critic.PAIR}{y}.json").write_text(json.dumps({"winner": winner, "why": "x"}))
    critic.record_pairs(ws, list("abc"), answers)
    later = "2099-01-01T00:00:00+00:00"  # the review comes after the ranking
    seam = "the band steps where it crosses the bonnet seam"
    with (ws.design_dir("a") / "verdicts.jsonl").open("a", encoding="utf-8") as f:
        for r in (
            _round("2026-01-01 10:00", 7, "iterate", lint_missed=[seam, "the TOKYO-3 text reads 'YO-3'"]),
            _round("2026-01-01 11:00", 8, "iterate", shots=".cache/designs/a/sim/20260101-105000"),
        ):
            f.write(json.dumps(r) + "\n")
    _ledger(
        ws,
        [
            {"at": later, "review": "r1", "id": "concept:a", "decision": "accept", "rating": 3},
            {"at": later, "review": "r1", "id": "concept:b", "decision": "reject"},
            {"at": later, "review": "r1", "id": "concept:c", "decision": "reject"},
            {
                "at": "2026-01-01T12:00:00+01:00",
                "review": "r2",
                "id": "final:a",
                "decision": "accept",
                "rating": 4,
            },
        ],
    )
    for wait in (0.001, 30.0):
        metrics.log("a", "sim", {"lock_wait": wait, "reload": 5.0, "shot_side_l": 2.0, "total": 20.0})

    report = stats.stats(ws)

    a = next(r for r in report["designs"] if r["design"] == "a")
    assert (a["sim_runs"], a["rounds"], a["sim_runs_per_round"]) == (2, 2, 1.0)
    (concept,) = report["concepts"]
    picked = {"review": "r1", "pick": "a", "decision": "accept", "of": 3, "critic_rank": 2, "rating": 3}
    assert concept.items() >= picked.items()
    final = report["finals"][0]
    assert (final["decision"], final["critic"], final["overall"], final["rating"]) == (
        "accept",
        "iterate",
        8,
        4,
    )
    assert report["lint_missed"][stats.kind(seam)] >= 1
    assert sum(report["lint_missed"].values()) == 2
    assert report["stages"]["shots_to_verdict_min"]["median"] == 10.0
    assert (report["lock"]["runs"], report["lock"]["queued"]) == (2, 1)
    assert stats.lines(report)


def test_stats_counts_the_sims_agents_started_and_why_each_ended(ws):
    for minutes in (30, 31):
        ended = f"no run has wanted it for {minutes} min"
        metrics.log("", "sim-host", {"connect": 40.0, "ready": 250.0, "total": 3000.0}, ended=ended)
    metrics.log("", "sim-host", {"connect": None, "ready": None, "total": 5.0}, ended="failed")

    sims = stats.stats(ws)["launches"]

    assert sims["hosts"] == 3
    assert sims["ready_s"]["n"] == 2
    assert sorted(sims["ended"].values()) == [1, 2]  # the two unwanted closes count together
    assert stats.lines(stats.stats(ws))


def test_a_final_review_lists_every_waiver_with_its_reason(ws):
    check = checked(ws, "d")
    check["waivers"] = [
        {"check": "near_tangent", "id": "stroke.side_l", "reason": "it traces the plate", "hid": ["x"]}
    ]
    (ws.out("d") / "check.json").write_text(json.dumps(check), encoding="utf-8")

    body = review_items.design_items(ws, ["d"])[0]["body"]

    assert "stroke.side_l" in body
    assert "it traces the plate" in body
