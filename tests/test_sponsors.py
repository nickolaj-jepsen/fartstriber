"""Sponsor brands: how they are saved, measured, approved, placed and reviewed, on the real McLaren kit."""

import shutil

import pytest

from livery import carspace, sponsors
from livery.config import Workspace
from livery.lint.run import thresholds
from livery.primitives import mirror_sides, sponsor, sponsor_group
from livery.review import apply as review_apply
from livery.review import items as review_items

DOOR = ((0.40, 0.5), (0.70, 0.5))  # across the doors: mapped on both sides, whatever the fit


def bars(stroke: float, gap: float, n: int = 4) -> str:
    """A stand-in wordmark: n vertical bars 100 high, `stroke` wide, `gap` apart."""
    d = " ".join(f"M{i * (stroke + gap):.1f},0 h{stroke} v100 h-{stroke} Z" for i in range(n))
    return sponsors.path_svg(d, (0, 0, n * stroke + (n - 1) * gap, 100))


def square() -> str:
    return sponsors.path_svg("M0,0 h100 v100 h-100 Z", (0, 0, 100, 100))


def brand(root, name: str, approved: bool = True, **variants: str) -> None:
    sponsors.save(root / name, variants or {"wordmark": bars(20, 20), "mark": square()})
    if approved:
        sponsors.approve(root / name)


@pytest.fixture
def kit(real_kit):
    return real_kit


@pytest.fixture
def roster(tmp_path):
    root = tmp_path / "assets" / "sponsors"
    root.mkdir(parents=True)
    with sponsors.designing(None, root):
        yield root


def test_a_supplied_file_is_kept_byte_for_byte_and_a_redraw_reopens_only_what_changed(roster):
    supplied = square().replace("\n", "\r\n")
    sponsors.save(roster / "acme", {"wordmark": bars(20, 20), "mark": supplied})
    sponsors.approve(roster / "acme")
    assert (roster / "acme" / "mark.svg").read_bytes() == supplied.encode()

    sponsors.save(roster / "acme", {"wordmark": bars(20, 30), "mark": supplied})

    variants = sponsors.load("acme").variants
    assert variants["mark"].approved
    assert not variants["wordmark"].approved


def test_thinner_strokes_and_tighter_counters_need_more_height():
    def need(stroke, gap):
        m = sponsors.measure(bars(stroke, gap))
        return sponsors.Variant(None, m["aspect"], m["stroke"], m["counter"], m["ink"], None).min_height_px(3)

    plain = need(30, 30)
    assert need(10, 30) > plain
    assert need(30, 8) > plain


def test_a_roster_variant_not_yet_approved_is_placed_and_listed_for_the_final_review(kit, roster):
    brand(roster, "acme", approved=False)

    el = sponsor(kit, "s", "side_l", "acme", (0.5, 0.6), "#ffffff", height=0.03, variant="mark")

    assert el.kind == "logo"
    assert el.car["brand"] == "acme"
    assert 'fill="#ffffff"' in el.svg
    assert sponsors.placed([el]) == {"sponsors": ["acme/mark"], "marks": []}
    assert [r["unapproved"] for r in sponsors.roster(_accepted(roster, "acme"))] == [("wordmark", "mark")]


def _accepted(root, *names: str):
    """A roster.md with these brands accepted."""
    rows = "".join(f"| {n} | {n.title()} | tyres | wide | none | red | searched | yes |\n" for n in names)
    path = root / "roster.md"
    path.write_text(
        "| Brand | Name | Category | Voice | Mark | Colours | Checked | Status |\n|---|---|---|---|---|---|---|---|\n"
        + rows,
        encoding="utf-8",
    )
    return path


def test_a_designs_own_brand_is_found_first_and_needs_no_approval(kit, roster, tmp_path):
    brand(roster, "acme")
    design = tmp_path / "designs" / "d"
    brand(design / "sponsors", "acme", approved=False, wordmark=bars(20, 20, n=2))

    with sponsors.designing(design):
        el = sponsor(kit, "s", "side_l", "acme", (0.5, 0.6), "#ffffff", height=0.03)

    assert el.car["variant"] == "wordmark"  # the design's acme has no mark, and no lockup
    assert sponsors.placed([el]) == {"sponsors": [], "marks": []}
    with pytest.raises(sponsors.SponsorError, match="has no mark"), sponsors.designing(design):
        sponsor(kit, "s", "side_l", "acme", (0.5, 0.6), "#ffffff", height=0.03, variant="mark")


def test_a_mark_below_its_minimum_height_is_refused(kit, roster):
    brand(roster, "hairline", wordmark=bars(3, 20))
    with pytest.raises(sponsors.SponsorError, match="needs"):
        sponsor(kit, "s", "side_l", "hairline", (0.5, 0.6), "#ffffff", height=0.01)


def test_a_large_only_brand_is_refused_below_headline_size_where_another_fits(kit, roster):
    sponsors.save(roster / "didone", {"wordmark": bars(20, 20)}, large_only=True)
    sponsors.approve(roster / "didone")
    brand(roster, "plain")
    at = (0.5, 0.6)
    px_per_car = carspace.px_per_unit(kit, "side_l", at)[1] / carspace.side_aspect(kit)
    height = 0.8 * thresholds().large_min_px / px_per_car

    assert sponsor(kit, "s", "side_l", "plain", at, "#ffffff", height=height, variant="wordmark")
    with pytest.raises(sponsors.SponsorError):
        sponsor(kit, "s", "side_l", "didone", at, "#ffffff", height=height)
    assert sponsor(kit, "s", "side_l", "didone", at, "#ffffff", height=1.5 * height)


def test_a_group_keeps_its_order_along_the_line_on_both_sides(kit, roster):
    brand(roster, "a")
    for name in ("b", "c"):
        shutil.copytree(roster / "a", roster / name)  # the same brand under other names, measured once

    els = mirror_sides(sponsor_group, kit, "door", ["a", "b", "c"], *DOOR, height=0.03, color="#ffffff")

    for view in ("side_l", "side_r"):
        mine = [e for e in els if e.car["view"] == view]
        assert [e.car["brand"] for e in mine] == ["a", "b", "c"]
        assert [e.car["at"][0] for e in mine] == sorted(e.car["at"][0] for e in mine)


def test_a_group_that_cannot_fit_fails_instead_of_squashing(kit, roster):
    names = [f"b{i}" for i in range(12)]
    brand(roster, names[0], wordmark=bars(20, 20, n=8))
    for name in names[1:]:
        shutil.copytree(roster / names[0], roster / name)  # the same brand under other names, measured once
    with pytest.raises(sponsors.SponsorError, match="don't fit"):
        sponsor_group(kit, "door", "side_l", names, *DOOR, height=0.02, color="#ffffff")


def test_a_brand_twice_in_one_group_is_refused(kit, roster):
    brand(roster, "a")
    with pytest.raises(sponsors.SponsorError, match="twice"):
        sponsor_group(kit, "door", "side_l", ["a", "a"], *DOOR, height=0.02, color="#ffffff")


def test_review_marks_the_roster_and_approves_or_drops_drawn_brands(roster, tmp_path):
    (roster / "roster.md").write_text(
        "| Brand | Name | Category | Voice | Mark | Colours | Checked | Status |\n"
        "|---|---|---|---|---|---|---|---|\n"
        "| keep | Keep | tyres | wide | none | red | 2026-10-19 | yes |\n"
        "| drop | Drop | oil | slab | none | blue | 2026-10-19 | yes |\n"
        "| new | New | brakes | stencil | none | grey | 2026-10-19 |  |\n",
        encoding="utf-8",
    )
    brand(roster, "keep", approved=False)
    brand(roster, "drop", approved=False)
    items = review_items.roster_items(roster / "roster.md")
    assert [i["id"] for i in items] == ["roster:new"]
    decisions = {
        "roster:new": {"decision": "reject"},
        "sponsor:keep": {"decision": "accept"},
        "sponsor:drop": {"decision": "reject"},
    }
    by_id = [{"id": k} for k in decisions]

    review_apply.apply(Workspace(root=tmp_path, assets=tmp_path / "assets"), by_id, decisions)

    assert {r["brand"]: r["status"] for r in sponsors.roster_rows()} == {
        "keep": "yes",
        "drop": "no",
        "new": "no",
    }
    assert not any(sponsors.approvals(roster / "drop").values())  # kept, to redraw
    assert [r["brand"] for r in sponsors.roster()] == ["keep"]
