import json

import pytest

from livery import approvals, config, critic, hashing, install, preview_flat, sponsors
from livery import kit as kits
from livery.check import run_build
from livery.config import Workspace
from livery.review import apply as review_apply
from livery.review.items import design_items
from tests.conftest import OWNER, checked, two_islands, write_design

BLUE = 'def build(kit, p):\n    return Livery(base="#3060c0")\n'
RED = 'def build(kit, p):\n    return Livery(base="#c03030")\n'

pytestmark = pytest.mark.usefixtures("cheap_flat")


def _final(ws, name: str) -> dict:
    """The final review's item, shown with a flat preview of the build."""
    preview_flat.flat(ws, name)
    return {"id": f"final:{name}", "kind": "final", "title": name, "pin": approvals.pin_of(ws, name)}


def _metrics(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_accepting_a_final_pins_the_build_shown_and_install_needs_it(ws, private_metrics):
    built = checked(ws, "d", BLUE, render=True)
    with pytest.raises(approvals.NotApproved):
        approvals.require(ws, "d", built)

    item = _final(ws, "d")
    written = review_apply.apply(ws, [item], {"final:d": {"decision": "accept", "note": "ship it"}})

    assert any("pinned" in w for w in written)
    assert approvals.require(ws, "d", built)["note"] == "ship it"
    assert approvals.compare(ws, "d", built["digests"])["status"] == "approved"
    line = _metrics(private_metrics)[-1]
    assert (line["loop"], line["decision"], line["pinned"]) == ("review", "accept", True)


def test_approvals_and_the_review_of_a_moved_build_name_the_owner_from_the_settings(ws):
    checked(ws, "d", BLUE, render=True)
    kit = {"id": "kit:tiny", "kind": "kit", "title": "tiny"}
    review_apply.apply(
        ws, [_final(ws, "d"), kit], {"final:d": {"decision": "accept"}, "kit:tiny": {"decision": "accept"}}
    )
    now = checked(ws, "d", RED, render=True)

    assert (approvals.load_approval(ws, "d") or {}).get("by") == OWNER
    assert kits.require_approved(ws, "tiny")["by"] == OWNER
    with pytest.raises(approvals.NotApproved) as refused:
        approvals.require(ws, "d", now)
    assert OWNER in str(refused.value)
    preview_flat.flat(ws, "d")
    (ws.root / review_apply.LEDGER).unlink()  # its decision would name them too; now only the move does
    (item,) = design_items(ws, ["d"])
    assert OWNER in item["body"]


def test_a_build_that_moved_while_under_review_is_not_pinned(ws):
    checked(ws, "d", BLUE, render=True)
    item = _final(ws, "d")
    checked(ws, "d", RED, render=True)

    written = review_apply.apply(ws, [item], {"final:d": {"decision": "accept"}})

    assert any("not pinned" in w for w in written)
    assert approvals.load_approval(ws, "d") is None


def test_a_final_is_pinned_only_with_a_flat_preview_of_the_build_shown(ws):
    checked(ws, "d", BLUE, render=True)
    item = _final(ws, "d")
    preview_flat.flat(ws, "d", {"seed": "7"})  # a --set variant goes elsewhere
    write_design(ws, "d", RED)
    preview_flat.flat(ws, "d")  # flat/ now shows a script that was never built
    write_design(ws, "d", BLUE)

    written = review_apply.apply(ws, [item], {"final:d": {"decision": "accept"}})

    assert any("not pinned" in w for w in written)
    assert approvals.load_approval(ws, "d") is None
    preview_flat.flat(ws, "d")
    review_apply.apply(ws, [item], {"final:d": {"decision": "accept"}})
    assert approvals.load_approval(ws, "d") is not None


def test_a_changed_design_reports_what_moved_and_how_much(ws):
    checked(ws, "d", BLUE, render=True)
    review_apply.apply(ws, [_final(ws, "d")], {"final:d": {"decision": "accept"}})
    now = checked(ws, "d", RED, render=True)

    result = approvals.compare(ws, "d", now["digests"])

    assert result["status"] == "changed"
    assert result["changed_by"] == ["script"]
    assert result["paint_changed"] == 1.0  # every car pixel went from blue to red
    assert (ws.root / result["diff"]).exists()
    with pytest.raises(approvals.NotApproved):
        approvals.require(ws, "d", now)


def test_build_all_stands_each_design_by_its_role_and_approval(ws):
    for name in ("kept", "moved"):
        checked(ws, name, BLUE, render=True)
        review_apply.apply(ws, [_final(ws, name)], {f"final:{name}": {"decision": "accept"}})
    checked(ws, "moved", RED, render=True)
    checked(ws, "new", BLUE, render=True)
    write_design(ws, "broken", "def build(kit, p)\n    return None\n")
    checked(ws, "fixture", 'ROLE = "fixture"\n' + RED, render=True)

    report = approvals.standings(ws)

    assert {name: (row["role"], row["status"]) for name, row in report.items()} == {
        "broken": ("livery", "failed"),
        "fixture": ("fixture", "built"),
        "kept": ("livery", "approved"),
        "moved": ("livery", "changed"),
        "new": ("livery", "unapproved"),
    }
    assert set(approvals.failing(report)) == {"broken", "moved"}


def test_a_fixture_that_moved_from_its_approval_fails_build_all_like_a_livery():
    report = {
        "a": {"role": "livery", "status": "unapproved"},
        "b": {"role": "fixture", "status": "built"},
        "c": {"role": "fixture", "status": "changed"},
    }
    assert approvals.failing(report) == ["c"]


def test_concept_decisions_are_logged_with_the_critics_rank(ws, private_metrics, tmp_path):
    for name in ("a", "b"):
        write_design(ws, name, BLUE)
    answers = tmp_path / "pairs"  # b beats a in both orders
    answers.mkdir()
    for pair, winner in ((f"a{critic.PAIR}b", "B"), (f"b{critic.PAIR}a", "A")):
        (answers / f"{pair}.json").write_text(json.dumps({"winner": winner, "why": "x"}))
    critic.record_pairs(ws, ["a", "b"], answers)
    items = [{"id": f"concept:{n}", "kind": "concept", "title": n} for n in ("a", "b")]

    review_apply.apply(ws, items, {"concept:a": {"decision": "accept"}, "concept:b": {"decision": "reject"}})

    ranks = {r["design"]: r["critic_rank"] for r in _metrics(private_metrics) if r["loop"] == "review"}
    assert ranks == {"a": 2, "b": 1}


def test_line_endings_do_not_change_the_design_hash(ws):
    write_design(ws, "d", BLUE)
    path = ws.design_dir("d") / "design.py"
    lf = path.read_bytes().replace(b"\r\n", b"\n")
    path.write_bytes(lf)
    before = hashing.design_hash(ws, "d")
    path.write_bytes(lf.replace(b"\n", b"\r\n"))
    assert hashing.design_hash(ws, "d") == before


def test_a_final_review_and_install_refuse_a_design_on_a_kit_not_approved_as_it_is(ws):
    built = checked(ws, "d", BLUE, render=True)
    review_apply.apply(ws, [_final(ws, "d")], {"final:d": {"decision": "accept"}})
    kit = ws.kits / "tiny"
    assert approvals.require(ws, "d", built)

    kits.write(kit, meta=kits.read_meta(kit) | {"bleed_px": 5})  # a refit the owner hasn't seen
    with pytest.raises(kits.KitNotApproved):
        approvals.require(ws, "d", built)
    with pytest.raises(kits.KitNotApproved):
        design_items(ws, ["d"])

    (kit / kits.APPROVAL).unlink()
    with pytest.raises(kits.KitNotApproved):
        approvals.require(ws, "d", built)


# --- roster sponsors and race markings: placed and built unapproved, gated at the final review and install

SQUARE = sponsors.path_svg("M0,0 h100 v100 h-100 Z", (0, 0, 100, 100))
SPONSORED = """from livery.model import Livery
from livery.primitives import sponsor

KIT = "two"


def build(kit, p):
    return Livery(base="#3060c0").add(
        sponsor(kit, "s", "side_l", "acme", (0.3, 0.5), "#ffffff", height=0.2, variant="mark")
    )
"""


@pytest.fixture
def sponsored(tmp_path) -> Workspace:
    """A design on the two-island kit, approved, placing acme, a brand not yet approved from a roster of the
    test's own."""
    ws = Workspace(root=tmp_path, assets=tmp_path / "assets")
    two_islands(tmp_path)
    kits.approve(ws.kits / "two", "Tester", "")
    sponsors.save(ws.sponsors / "acme", {"mark": SQUARE})
    ws.design_dir("d").mkdir(parents=True)
    (ws.design_dir("d") / "design.py").write_text(SPONSORED, encoding="utf-8")
    return ws


def test_an_unapproved_roster_variant_builds_but_gates_install_and_the_final_until_the_review_accepts_it(
    sponsored,
):
    built = run_build(sponsored, "d")
    assert built["passed"]
    assert approvals.standings(sponsored)["d"]["needs_approval"]
    with pytest.raises(approvals.NotApproved, match="acme"):
        approvals.require(sponsored, "d", built)
    cfg = config.load()
    with pytest.raises(approvals.NotApproved, match="acme"):
        install.install(cfg, sponsored, "d", log=lambda _: None)
    assert not cfg.paint_root.exists()

    written = review_apply.apply(sponsored, [_final(sponsored, "d")], {"final:d": {"decision": "accept"}})

    assert any("not pinned" in w and "acme" in w for w in written)
    assert approvals.load_approval(sponsored, "d") is None

    items = [{"id": "sponsor:acme", "kind": "sponsor"}, _final(sponsored, "d")]
    decisions = {"final:d": {"decision": "accept"}, "sponsor:acme": {"decision": "accept"}}
    review_apply.apply(sponsored, items, decisions)  # the brand and the final in the same review

    assert approvals.load_approval(sponsored, "d") is not None
    assert not approvals.unapproved(sponsored, approvals.pin_of(sponsored, "d"))


def test_approving_a_brand_keeps_every_design_checked(ws, tmp_path):
    ws = Workspace(root=ws.root, assets=tmp_path / "assets")
    sponsors.save(ws.assets / "sponsors" / "acme", {"mark": SQUARE})
    write_design(ws, "d", BLUE)
    before = hashing.design_hash(ws, "d")

    sponsors.approve(ws.assets / "sponsors" / "acme")
    assert hashing.design_hash(ws, "d") == before

    sponsors.save(
        ws.assets / "sponsors" / "acme", {"mark": sponsors.path_svg("M0,0 h90 v100 h-90 Z", (0, 0, 90, 100))}
    )
    assert hashing.design_hash(ws, "d") != before
