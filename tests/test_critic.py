"""Critic v2: what a pack holds, the pairwise compare and concept pairs, and ship computed from records."""

import itertools
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

from livery import check, cli, critic, references
from livery.config import Workspace
from livery.review import apply as review_apply
from livery.review import items as review_items
from tests.conftest import OWNER, TASTE_IDS, checked, sim_run, write_design
from tests.test_review_critic import suggestion, verdict

FIX = suggestion("the bonnet is one plain field")


def _digests(ws: Workspace, name: str) -> dict:
    return json.loads((ws.out(name) / "check.json").read_text(encoding="utf-8"))["digests"]


def _run(ws: Workspace, name: str, stamp: str, colour: str = "purple") -> Path:
    """A sim run of the design's checked build, which is made first if there is none."""
    if not (ws.out(name) / "check.json").exists():
        checked(ws, name)
    return sim_run(ws, name, stamp, _digests(ws, name), colour)


def _files(manifest: dict | list | str | bool) -> list[str]:
    """Every path a manifest names: all its strings but the mode, the owner's name and the fix texts."""
    if isinstance(manifest, str):
        return [manifest]
    if isinstance(manifest, list):
        return [p for v in manifest for p in _files(v)]
    if isinstance(manifest, dict):
        return [
            p for k, v in manifest.items() if k not in ("mode", "owner", "previous_fixes") for p in _files(v)
        ]
    return []  # a flag, like baked


def test_a_round_pack_holds_its_own_copies_upscaled_distance_images_and_anchors(ws):
    _run(ws, "d", "20260101-000000")
    (ws.design_dir("d") / "brief.md").write_text("A brief.\n\nReferences: 25, 32\n", encoding="utf-8")
    favourite = references.folder(ws) / "25"
    favourite.mkdir(parents=True)
    for n in range(critic.ANCHOR_PICTURES + 2):
        Image.new("RGB", (critic.ANCHOR_PX * 2, 100), "green").save(favourite / f"25_{n}.jpg")
    boring = sim_run(ws, "fireproof-sweep", "20251018-000000", None, "orange")
    references.copy_boring(ws, log=lambda m: None)
    ws.references.write_text("| # | Livery | Car | Why | Yes/no |\n", encoding="utf-8")
    ws.prefs.mkdir(parents=True, exist_ok=True)
    (ws.prefs / "log.md").write_text("He likes a second layer.\n", encoding="utf-8")
    warnings: list[str] = []

    folder = critic.pack(ws, ["d"], warn=warnings.append)

    manifest = json.loads((folder / "manifest.json").read_text())
    named = _files(manifest)
    assert len(named) > 10
    assert all((folder / p).is_file() for p in named)  # every file is inside the pack
    for key in ("rubric", "taste", "references", "prefs"):
        assert (folder / manifest[key]).read_bytes()
    assert manifest["owner"] == OWNER  # whose taste it judges
    entry = manifest["designs"]["d"]
    for path in entry["distance"]:
        assert Image.open(folder / path).width == critic.DISTANCE_PX * critic.UPSCALE
    assert len(entry["anchors"]["25"]) == critic.ANCHOR_PICTURES
    assert all(Image.open(folder / p).width == critic.ANCHOR_PX for p in entry["anchors"]["25"])
    assert sorted(Path(p).name for p in manifest["boring"]["fireproof-sweep"]) == sorted(
        p.name for p in boring.glob("*.png")
    )
    assert any("32" in w for w in warnings)  # a named row with nothing cached is reported, not hidden


def test_a_pack_says_what_the_sim_draws_over_the_car_and_what_hides_its_paint(ws):
    (ws.kits / "tiny" / "body.yaml").write_text(
        "features:\n"
        "  lamp: {view: side_l, kind: keep_clear, box: [0.7, 0.4, 0.8, 0.6], label: the sim's lamp, note: dark box}\n"
        "  fin: {view: side_l, kind: occluder, box: [0.5, 0.0, 0.6, 0.2], note: hides the sill from above}\n",
        encoding="utf-8",
    )
    _run(ws, "d", "20260101-000000")

    folder = critic.pack(ws, ["d"])

    entry = json.loads((folder / "manifest.json").read_text())["designs"]["d"]
    notes = (folder / entry["kit"]).read_text(encoding="utf-8")
    assert "the sim's lamp" in notes
    assert "hides the sill from above" in notes


def test_only_shots_of_the_checked_build_are_packed(ws):
    checked(ws, "d")
    sim_run(ws, "d", "20260101-000000", None)  # from before runs recorded their build
    with pytest.raises(critic.StaleShots):
        critic.pack(ws, ["d"])
    shot = sim_run(ws, "d", "20260102-000000", _digests(ws, "d"))
    sim_run(ws, "d", "20260103-000000", {"car.tga": "older", "car_spec.tga": "older"})

    folder = critic.pack(ws, ["d"])

    assert critic.records(ws, "d") == []
    assert json.loads((ws.out("d") / "critic.json").read_text())["round"]["shots"].endswith(shot.name)
    assert (folder / "d" / "squint.png").exists()
    write_design(ws, "d", 'def build(kit, p):\n    return Livery(base="#c03030")\n')
    with pytest.raises(check.NotChecked):  # edited since the build: build it, then shoot it
        critic.pack(ws, ["d"])


def test_compare_packs_show_both_orders_without_saying_which_is_newer(ws):
    _run(ws, "d", "20260101-000000", "orange")
    _run(ws, "d", "20260102-000000", "purple")

    first, second = critic.pack_compare(ws, "d")

    for folder, a_colour in ((first, (128, 0, 128)), (second, (255, 165, 0))):
        manifest = json.loads((folder / "manifest.json").read_text())
        assert Image.open(folder / manifest["A"]["shots"][0]).getpixel((0, 0)) == a_colour
        assert [Path(p).name for p in manifest["A"]["shots"]] == [
            Path(p).name for p in manifest["B"]["shots"]
        ]
        assert "2026" not in (folder / "manifest.json").read_text()


@pytest.mark.parametrize(
    ("first", "second", "result"),
    [
        ("A", "B", "better"),  # this round won in both orders
        ("B", "A", "worse"),
        ("A", "A", "same"),  # each critic picked whatever came first
        ("same", "B", "same"),
    ],
)
def test_a_compare_counts_only_where_both_orders_agree(ws, first, second, result):
    _run(ws, "d", "20260101-000000")
    _run(ws, "d", "20260102-000000")
    critic.pack_compare(ws, "d")

    got = critic.record_compare(ws, "d", {"better": first, "where": "x"}, {"better": second, "where": "y"})

    assert got["result"] == result
    assert critic.records(ws, "d")[-1]["result"] == result


def test_a_compare_without_its_pack_is_refused(ws):
    _run(ws, "d", "20260101-000000")
    with pytest.raises(critic.BadVerdict):
        critic.record_compare(ws, "d", {"better": "A"}, {"better": "B"})


@pytest.mark.parametrize(
    "bad",
    [
        {"findings": []},
        {"findings": [{"text": "plain bumper", "view": "front34"}]},  # optional or not
        {"findings": [{"text": "x", "view": "", "optional": True}]},
    ],
)
def test_malformed_adversary_findings_are_refused(bad):
    with pytest.raises(critic.BadVerdict):
        critic.validate_adversary({"closest": "25"} | bad)


def _two_rounds(ws, *, first=None, second=None, compare=("A", "B"), top_optional=True) -> None:
    """Round one, answered; round two on new shots with its compare and adversary check."""
    _run(ws, "d", "20260101-000000")
    critic.record(ws, "d", first or verdict(fixes=[FIX]))
    critic.answer(ws, "d", [{"status": "done", "note": "tones on the bonnet"}])
    _run(ws, "d", "20260102-000000")
    critic.pack(ws, ["d"])
    resolved = [{"status": "resolved", "evidence": "front34.png"}]
    critic.record(ws, "d", second or verdict(previous=resolved, fixes=[suggestion("polish", optional=True)]))
    critic.pack_compare(ws, "d")
    critic.record_compare(ws, "d", {"better": compare[0]}, {"better": compare[1]})
    critic.pack(ws, ["d"], mode="adversary")
    finding = {"text": "the mirrors are plain", "view": "front34", "optional": top_optional}
    critic.record_adversary(ws, "d", {"closest": "25", "findings": [finding]})


def test_a_round_ships_when_every_check_passes(ws):
    _two_rounds(ws)
    got = critic.status(ws, "d")
    assert got["ship"], got["why"]
    assert got["call"] == "ship"


def _failing(id_: str) -> list[dict]:
    return [{"id": i, "pass": i != id_, "where": "front34"} for i in TASTE_IDS]


RESOLVED = [{"status": "resolved", "evidence": "front34.png"}]


@pytest.mark.parametrize(
    "spoil",
    [
        {"second": verdict(previous=RESOLVED, fixes=[FIX])},  # an open fix that matters
        {"second": verdict(previous=[{"status": "partly", "evidence": "front34.png"}], fixes=[])},
        {"second": verdict(previous=RESOLVED, fixes=[], blocked=["the beltline steps at the door"])},
        {"second": verdict(previous=RESOLVED, fixes=[], taste=_failing("front-as-back"))},
        {"compare": ("B", "A")},  # worse than the last round
        {"top_optional": False},
    ],
)
def test_a_round_does_not_ship_when_any_check_fails(ws, spoil):
    _two_rounds(ws, **spoil)
    got = critic.status(ws, "d")
    assert not got["ship"]
    assert got["why"]
    assert got["call"] == "iterate"


def test_a_round_without_its_compare_or_adversary_check_does_not_ship(ws):
    _run(ws, "d", "20260101-000000")
    critic.record(ws, "d", verdict(fixes=[]))
    assert len(critic.status(ws, "d")["why"]) == 1  # the adversary check
    _run(ws, "d", "20260102-000000")
    critic.record(ws, "d", verdict(fixes=[]))
    assert len(critic.status(ws, "d")["why"]) == 2  # and the compare with round one


def test_a_design_changed_since_its_round_does_not_ship(ws):
    _two_rounds(ws)
    assert critic.status(ws, "d")["ship"]

    write_design(ws, "d", 'def build(kit, p):\n    return Livery(base="#c03030")\n')
    assert not critic.status(ws, "d")["ship"]


NOTE = "I like the pixel design a lot, but it's totally missing from the front"


def _decide(ws: Workspace, decision: str, note: str = "") -> None:
    """The owner's decision on final:d, applied as a review applies it."""
    item = {"id": "final:d", "kind": "final", "title": "d", "body": "", "images": [], "mix": False}
    review_apply.apply(ws, [item], {"final:d": {"decision": decision, "note": note}})


def _judged(status: str) -> dict:
    return {"status": status, "evidence": "front34.png"}


def test_a_round_judges_the_owners_latest_note_until_they_accept_the_final(ws):
    _run(ws, "d", "20260101-000000")
    _decide(ws, "edit", NOTE)
    _decide(ws, "edit")  # a decision without a note leaves the last one standing

    folder = critic.pack(ws, ["d"])

    entry = json.loads((folder / "manifest.json").read_text())["designs"]["d"]
    assert json.loads((folder / entry["owner_note"]).read_text())["note"] == NOTE
    with pytest.raises(critic.BadVerdict):
        critic.record(ws, "d", verdict())
    with pytest.raises(critic.BadVerdict):
        critic.record(ws, "d", verdict(owner_note={"status": "partly", "evidence": "elsewhere.png"}))
    recorded = critic.record(ws, "d", verdict(owner_note=_judged("partly")))
    assert (recorded["owner_note"]["note"], recorded["owner_note"]["status"]) == (NOTE, "partly")

    _decide(ws, "accept")
    critic.answer(ws, "d", [{"status": "done", "note": "the pixels wrap the nose"}])
    entry = json.loads((critic.pack(ws, ["d"]) / "manifest.json").read_text())["designs"]["d"]
    assert "owner_note" not in entry


@pytest.mark.parametrize("status", ["resolved", "partly"])
def test_a_design_ships_only_once_the_owners_note_is_resolved_and_the_final_shows_the_judgement(ws, status):
    checked(ws, "d")
    _decide(ws, "edit", NOTE)
    polish = [suggestion("polish", optional=True)]
    _two_rounds(
        ws,
        first=verdict(fixes=[FIX], owner_note=_judged("not")),
        second=verdict(previous=RESOLVED, fixes=polish, owner_note=_judged(status)),
    )

    assert critic.status(ws, "d")["ship"] is (status == "resolved")
    (item,) = review_items.design_items(ws, ["d"])
    assert item["owner_note"]["judged"] == {
        "status": status,
        "evidence": "front34.png",
        "round": 2,
        "this_build": True,
    }
    assert NOTE in item["body"]
    assert verdict()["verdict"] not in item["body"]  # the critic's call never reaches the owner


def test_a_note_after_the_last_round_keeps_the_design_from_shipping_until_a_round_judges_it(ws):
    _two_rounds(ws)
    assert critic.status(ws, "d")["ship"]

    _decide(ws, "edit", NOTE)
    assert not critic.status(ws, "d")["ship"]
    _decide(ws, "accept")
    assert critic.status(ws, "d")["ship"]


def test_the_critics_own_fixes_never_hold_the_car_back_from_the_owner(ws):
    _decide(ws, "edit", NOTE)
    _two_rounds(
        ws,
        first=verdict(fixes=[FIX], owner_note=_judged("not")),
        second=verdict(
            previous=RESOLVED, fixes=[suggestion("a fix that matters")], owner_note=_judged("resolved")
        ),
    )

    got = critic.status(ws, "d")
    assert got["ready"]
    assert not got["holds"]
    assert not got["ship"]
    assert got["why"]  # status keeps reporting the critic's own fix


@pytest.mark.parametrize(
    "second",
    [
        verdict(previous=RESOLVED, fixes=[], owner_note=_judged("partly")),
        verdict(previous=RESOLVED, fixes=[], owner_note=_judged("resolved"), taste=_failing("front-as-back")),
        verdict(previous=RESOLVED, fixes=[], owner_note=_judged("resolved"), blocked=["the beltline steps"]),
    ],
)
def test_the_owners_note_every_taste_row_and_anything_blocked_hold_the_car_back(ws, second):
    _decide(ws, "edit", NOTE)
    _two_rounds(ws, first=verdict(fixes=[FIX], owner_note=_judged("not")), second=second)

    got = critic.status(ws, "d")
    assert not got["ready"]
    assert got["holds"]
    assert set(got["holds"]) <= set(got["why"])


ENDORSED = "Mirror marks don't read from any camera"


def test_a_suggestion_the_owner_endorsed_is_judged_every_round_until_it_is_resolved(ws):
    _run(ws, "d", "20260101-000000")
    item = {"id": "final:d", "kind": "final", "title": "d", "body": "", "images": [], "mix": False}
    review_apply.apply(ws, [item], {"final:d": {"decision": "edit", "note": "", "endorsed": [ENDORSED]}})

    folder = critic.pack(ws, ["d"])

    entry = json.loads((folder / "manifest.json").read_text())["designs"]["d"]
    assert json.loads((folder / entry["owner_note"]).read_text())["endorsed"] == [ENDORSED]
    with pytest.raises(critic.BadVerdict):  # one judgement per endorsed suggestion
        critic.record(ws, "d", verdict(fixes=[]))
    with pytest.raises(critic.BadVerdict):  # no note in their words, only the suggestion
        critic.record(
            ws, "d", verdict(fixes=[], owner_note=_judged("resolved"), endorsed=[_judged("partly")])
        )
    recorded = critic.record(ws, "d", verdict(fixes=[], endorsed=[_judged("partly")]))
    assert recorded["endorsed"][0]["suggestion"] == ENDORSED
    assert not critic.status(ws, "d")["ready"]

    critic.pack(ws, ["d"])
    critic.record(ws, "d", verdict(fixes=[], endorsed=[_judged("resolved")]))
    assert critic.status(ws, "d")["ready"]


def test_bradley_terry_orders_a_tournament_with_an_upset_and_a_tie():
    names = ["d", "b", "a", "c"]
    strength = {"a": 3, "b": 2, "c": 1, "d": 0}
    games = []
    for x, y in itertools.permutations(names, 2):
        games.append((x, y, 1.0 if strength[x] > strength[y] else 0.0))
    games[games.index(("d", "a", 0.0))] = ("d", "a", 1.0)  # one upset
    games[games.index(("b", "c", 1.0))] = ("b", "c", 0.5)  # one tie

    fitted = critic.bradley_terry(names, games)

    assert sorted(names, key=lambda n: -fitted[n]) == ["a", "b", "c", "d"]
    assert all(abs(v) < 10 for v in fitted.values())


def test_a_concept_that_never_wins_still_gets_a_finite_strength():
    fitted = critic.bradley_terry(["a", "b"], [("a", "b", 1.0), ("b", "a", 0.0)])
    assert fitted["a"] > fitted["b"] > -10


def _flat(ws: Workspace, name: str) -> None:
    """A concept with a flat preview of its script as it stands."""
    checked(ws, name)


def test_concepts_are_packed_in_every_pair_both_ways_and_ranked_from_the_answers(ws, tmp_path):
    names = ["c1", "c2", "c3"]
    for n in names:
        _flat(ws, n)

    root = critic.pack_pairs(ws, names)

    packs = sorted(p.name for p in root.iterdir())
    assert packs == sorted(critic.PAIR.join(p) for p in itertools.permutations(names, 2))
    manifest = json.loads((root / packs[0] / "manifest.json").read_text())
    assert all((root / packs[0] / p).is_file() for p in _files(manifest))
    answers = tmp_path / "answers"
    answers.mkdir()
    better = {"c2": 3, "c3": 2, "c1": 1}
    for p in packs:
        a, b = p.split(critic.PAIR)
        winner = "A" if better[a] > better[b] else "B"
        (answers / f"{p}.json").write_text(json.dumps({"winner": winner, "why": "x"}), encoding="utf-8")

    assert critic.record_pairs(ws, names, answers)["ranking"] == ["c2", "c3", "c1"]
    logged = critic.latest_ranking(ws, names)  # what review compares the owner's pick with
    assert logged
    assert logged["ranking"] == ["c2", "c3", "c1"]
    assert [critic.records(ws, n)[-1]["place"] for n in names] == [3, 1, 2]
    assert critic.history(ws, "c2") == []  # a ranking is no round

    (answers / f"c1{critic.PAIR}c2.json").unlink()
    with pytest.raises(critic.BadVerdict):  # every pair in both orders
        critic.record_pairs(ws, names, answers)


def test_a_pair_needs_two_concepts_with_flat_previews_of_their_scripts(ws):
    _flat(ws, "c1")
    ws.design_dir("c2").mkdir(parents=True)
    with pytest.raises(critic.NoShots):
        critic.pack_pairs(ws, ["c1", "c2"])
    with pytest.raises(critic.UsageError):
        critic.pack_pairs(ws, ["c1", "c1"])
    _flat(ws, "c2")
    critic.pack_pairs(ws, ["c1", "c2"])
    write_design(ws, "c2", 'def build(kit, p):\n    return Livery(base="#c03030")\n')
    with pytest.raises(critic.StaleShots):  # edited since its flat preview
        critic.pack_pairs(ws, ["c1", "c2"])


def _cli(monkeypatch, ws, *args: str) -> int:
    monkeypatch.setattr(cli, "Workspace", lambda: ws)
    monkeypatch.setattr(sys, "argv", ["livery", *args])
    with pytest.raises(SystemExit) as e:
        cli.main()
    return int(e.value.code or 0)


def test_the_cli_prints_the_ship_decision_and_refuses_a_rank_with_no_source(monkeypatch, ws, capsys):
    _run(ws, "d", "20260101-000000")
    assert _cli(monkeypatch, ws, "critic", "status", "d") == 0
    assert json.loads(capsys.readouterr().out)["ship"] is False
    assert _cli(monkeypatch, ws, "critic", "rank", "a", "b") == 2
    assert _cli(monkeypatch, ws, "critic", "pack", "d", "--compare", "--adversary") == 2
    assert _cli(monkeypatch, ws, "critic", "pack", "d", "--compare") == 1  # no earlier round
