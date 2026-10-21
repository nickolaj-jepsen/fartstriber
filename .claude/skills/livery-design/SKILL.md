---
name: livery-design
description: Design knowledge for Fartstriber liveries - principles stated as numbers, the rubric, Fireproof brand rules, and how to write design.py against the livery library. Use before designing or changing any livery.
---

# Livery design

Read before designing:

1. `references.md`, the rows marked **yes**: the owner's taste anchors. Look at the pictures of the rows your brief names in `.cache/references/<#>/` (`livery references fetch` caches them).
2. All of the owner's `log.md` (`prefs/<person>/`): what they picked and rejected, in their words.
3. `rubric.md` next to this file: how every round is judged.
4. The taste table, `uv run livery taste show`: what the owner has complained about more than once, as checks. Check every row in the shots before asking for a round; a design goes back to the owner only when all of them pass.

## The concept brief

A concept's `brief.md` holds the owner's brief, then:

- **Source:** one favourite whose system it transplants, a real object or photo described in words, or a one-sentence story. The devices below applied evenly make the median car the owner calls boring; the source decides which ones and how.
- **Expected:** the version of this brief anyone would make first, written down so that this concept isn't it.
- **The move:** the one thing it commits to completely.
- `References: 25, 32`: the 2-3 rows of `references.md` it draws on, whose pictures builders and the critic see.
- **50 m, 10 m, 1 m:** what reads at each distance (below), as the source plays out there.

## Three distances

The owner called every design that was only a clean big shape "boring" (their `log.md`), and accepted the Evangelion finals only to be done with them. Their favourites (`references.md` 21-34) all work at three distances at once. Design all three, in this order:

1. **50 m, the squint.** One big idea owns the car: a colour split, a band, a creature, an all-over pattern, a theme. Check the squint thumbnail (`livery preview flat`) and the 200 px shot downscales: if the idea doesn't survive them, it isn't strong enough. Detail never decides this read. Proportions near 60 / 30 / 10: a dominant field, a second field, the accent.
2. **10 m, the system.** A second layer that gives the big shape movement and finish. This is where our cars have fallen short; use most of these:
   - **Tones, not flat fields.** Each big field carries a darker or lighter tone of its own hue in large shapes: facets half a door wide (Valkyrie), darker panels on bonnet and flanks (EVA RT), spots and scales (Rawr), blocks (Paul Miller), hairlines across both fields (WRT), linework panels (SPIRIT). No plain, unbroken area larger than a door on a side or the bonnet on top.
   - **No bare edge between two fields.** Where two colours meet, the edge is an event: a pinline in the accent tracing the split (WRT, Valkyrie), a dissolve whose pieces are densest at the split and thin out both ways in two or three sizes (Trilateral, Paul Miller), or a raked cut. An even texture with no gradient reads as wallpaper.
   - **Designed band ends.** A band or stripe ends in a taper (Gulf), a run of three or four raked dashes getting shorter (EVA RT), a step (Mazdaspeed), or at a shut line or arch. Never a square end in open paint.
   - **Stripe bundles.** Several parallel stripes in graded tones read as one element: Polaroid's rainbow, the 1970s sunset, Gulf's streaks of different lengths. A bundle that runs roof, door, sill and splitter ties every view together.
   - **Three line weights.** Bands, accent pinlines about 1-2 cm wide, and tone-on-tone hairlines. Blocks alone are one weight and read flat.
   - **One lean.** Every slash, cut and dash leans the same way, raked with the car's motion.
   - **A grounded lower body.** A dark sill and lower bumper band makes the car look low and gives small sponsors a field (Getspeed, WRT, Mazdaspeed, Williams); it may rise towards the tail as a wedge.
   - **Finish contrast.** A field in a second finish reads as a tone of its own without a second colour: a matte or satin panel on a gloss field of the same hue, gloss stripes over matte. Satin reads clearly brighter, with a soft sheen (a median 4-14 levels from gloss in every view); matte only a little (it loses the sharp reflections and lifts blacks: p95 7 levels), so use it in large fields, never for detail. Metallic and chrome render darker and reflect sharply; the build brightens their paint, but under them the sim can't show a colour brighter than about 105/125/157 (R/G/B) for metallic and 89/115/146 for chrome, and the lint warns (`finish_reach`): keep metal for darks, accents and trim. Numbers: `docs/sim.md`, Rendering.
   - **The car's features drawn on.** The splitter lip, intake outlines, mirrors and wing endplates in the accent (Valkyrie, WRT, Polaroid, SPIRIT, EVA RT), where the kit maps them; the nose can be a face (Rawr's teeth along the splitter and lids over the lights).
3. **1 m, the detail.** Something that rewards a close look and stays invisible at 50 m: fine linework or a tonal pattern inside a field, hazard hatching, tech labels and part numbers, the race markings a real car carries (tow arrows, the cut-off triangle, the extinguisher E), small marks. Low contrast against its own field, so it never breaks the big read; if it doesn't show in any sim shot, it isn't there.

Then the race-car finish, which plain designs miss:

- **The brand in every view.** When the brief has a lead sponsor, it reads from the side, from the front (bonnet) and from behind (rear deck or bumper). The roof carries the number or a big mark for the chase and overhead shots, as big as it fits (WRT's outlined 46, the 1970s bonnet).
- **A sponsor ecosystem.** The references carry fifteen to thirty marks: ten to twenty small ones grouped in a few places (sills, bumper corners, haunch, mirrors, wing endplates, near the number), in one colour per field. They make a car look raced, not rendered.
- **A theme from the brand's own graphics.** Polaroid's rainbow, Lucky Strike's roundel, the 1970s sunset: the sponsor's visual language becomes the pattern system. For Fireproof that is the pixel flame: stepped blocks, embers, hard pixels.
- **Type is designed, not labelled.** The owner called plain label stacks "boring" and noticed off spacing at once (their `log.md`). One hierarchy per field: a big designation (outlined, stencil-cut or raked with the lean, like WRT's 46) and small lines aligned to it. Stacks share a left edge and a line gap; text in a plate has equal padding; tracking is consistent; nothing is clipped by an outline or arch. Check every mark in the full-size sim shots.
- **The sides may differ.** WRT swaps its fields (black nose on the left, white nose on the right); the Gulf Valkyrie is blue on one side and white on the other. The system stays the same on both sides; the arrangement may swap.

## Principles

The linter measures what it can; thresholds are the `[lint]` values in `policy.toml`, by name below.

- **Three hues plus neutrals, in tones.** At most three hue families, brand colours included, plus black, white or grey; each family may carry two or three tones for the tonal layer (`max_colours` counts every swatch). One high-chroma accent at about a tenth of the car, as pinlines, tips and lips. Contrast between neighbours, not everywhere. A gradient is drawn as stacked flat stripes or a stepped dissolve.
- **Shapes follow body lines**, or cut them on purpose. Lean bands with the car's rake rather than against it.
- **Balance front to back**: one focal point, weight distributed so neither end looks empty.
- **Sides and top read as one design.** Bands carry over the roof (`band` does this); a side-only idea needs a reason on top.
- **Number zones stay clear and contrasting.** No text or logo on a keep-clear zone (`keep_clear_px`); paint under the number with contrast at least `min_number_contrast` against white. The same goes for what the sim draws over the car (the kit's `keep_clear` features, like the door light panel): no text, logo or marking under one, and other paint there is hidden. Type and markings under an occluder (the carbon side blade) don't show from the three-quarter and high views.
- **Every edge clearly crosses or clearly avoids each shut line, paint edge (glass, arches, unpainted parts) and the number zone**: nothing running within `near_tangent_px` of one for `near_tangent_run_px` or more; a seam the paint carries on across doesn't count. A stripe stopping 2 cm before a door shut line looks like a mistake. State the intent and let the library place the edge: `band`, `stripe`, `shape` and the patterns snap a stretch that would fail onto the line or clear of it, and list every move under `snaps` in `lint.json` and the check report. Read them. A snap you don't want, or a near-tangent error the library couldn't fix, means move or reshape the element; never scan a parameter for a value that lints clean.
- **Nothing smaller than** `min_feature_px`, no stroke under `min_stroke_px`, no text under `min_text_px`.
- **Design for the cameras, not the texture.** The lint's camera warnings (`unseen`, `crease`, `hidden_detail`, `screen_width`) name the mark no preview view sees well, the type bent over a fold, the detail on a sill that faces down and the line under `min_screen_px` on screen: move or thicken it rather than waive it.
- **No transparency, gradients-as-blends, filters or blur**: every pixel has one colour and one material.

## Brand rules

- **Fireproof is the owner's own brand, not a paying sponsor.** A design carries it only if the brief asks, and small and discreet is fine: "it's not important for my own personal brand" (`prefs/log.md`). The design leads; never size the car around the wordmark.
- Fireproof is the roster brand `fireproof`, from the owner's Fireproof design system: the pixel-flame mark (`#f46200`, `#ff8000`, `#ffd500`), the lowercase pixel wordmark on the same grid, and the lockup of the two on one baseline. Place it with `sponsor(..., "fireproof", variant=..., color=...)`. `color="own"` keeps its colours: the flame's three tones, and "fire" in `#f46200` with "proof" in `#DAD8CE`, for dark grounds. One ink in any colour is the mono form, for any other ground (photos, the accent, light paint). Never trace, smooth, outline or redraw it.
- **Other sponsors come from the roster** (`assets/sponsors/`, indexed by `roster.md`; `sponsors.roster()` lists what can be placed, with category and lettering voice). Mix categories and voices in a group, never one brand twice in a group, one colour per field. Place rows and stacks with `sponsor_group`; it balances their sizes and fails if they don't fit at their minimum size, so drop a brand or lengthen the field.
- **A themed sponsor only this design needs** (an in-universe brand, a pun on the theme) is drawn into `designs/<name>/sponsors/<brand>/` with the helpers in `livery.sponsors` and placed the same way. Never add to `assets/sponsors` in a design session; propose a brand worth reusing to the owner.
- **Real logos only from files the owner supplies**, as a roster brand with `source = "supplied"`, placed with `sponsor`, same rules.
- Fonts come only from `assets/fonts`.

## Writing design.py

```python
from dataclasses import dataclass

from livery.params import DesignParams, param
from livery.primitives import band, base, mirror_sides, sponsor, sponsor_group

KIT = "mclaren-720s-gt3-evo"


@dataclass(frozen=True)
class Params(DesignParams):
    band_u: float = param(0.55, 0.3, 0.8)
    band_angle: float = param(12.0, -25.0, 25.0)


def build(kit, p: Params):
    return base("#161616").add(
        band(kit, "band", p.band_u, p.band_u + 0.08, "#f46200", angle=p.band_angle),
        mirror_sides(
            sponsor,
            kit,
            "wordmark",
            "fireproof",
            at=(0.58, 0.6),
            color="#ffffff",
            height=0.05,
            variant="wordmark",
        ),
        mirror_sides(
            sponsor_group,
            kit,
            "door",
            ["brand-a", "brand-b", "brand-c"],
            start=(0.40, 0.5),
            end=(0.70, 0.5),
            height=0.02,
            color="#ffffff",
        ),
    )
```

- Car space: sides (u nose 0 → tail 1, v sill 0 → top of the side 1), top (u, w left -1 → right 1). See `docs/architecture.md` (Design library) for every primitive.
- **Name the body, never island ids or measured numbers.** The kit's body model (`kits/<kit>/body.yaml`) names its parts (`splitter_lip`, `mirror_shell_l`, `door_l`, groups like `front_panel_l` and `wing_endplates`) and features (the arches, shut lines and window line, the door light panel, the number zones in car space, the mirror bases). Pass a part's name wherever a primitive takes an island id (`panel(kit, "lip", "splitter", RED)`, `clip_out(kit, ["mirrors", "wing_endplates"], el)`), read a feature's place from `kit.feature("door_light_panel_l").box` or `.at`, and anchor a marking at a feature (`marks(..., at="mirror_base_l")`). The lint warns on a literal island id the kit names. A name marked `unsure` may be wrong: check it in the flat views. A part or feature the kit lacks, or one in the wrong place, is a kit change to report, not a number in the design.
- Every tunable number is a `Params` field with a range; choose values with `livery sheet <design> --wedge k=a..b..step`, not by guessing in the sim.
- Randomness only from `p.rng("name")`. Imports only from math, dataclasses, typing, itertools, numpy, shapely, livery.
- **The 10 m and 1 m devices are in the library**; build them from it rather than by hand, and never by editing the SVG a primitive drew:
  - Strokes between colours, keylines round a plate, a pinline tracing a split: `geom.outline(kit, id, view, shape, [(px, colour), ...])`, with `along=` the other field for a split and `place="inside"` for a trim inside a panel edge (`view=None` with an island id, for a part no view maps). They keep their width on every island; widths under `min_outline_px` are refused because they vanished in the sim.
  - Dissolves, hazard tape, raked dash runs that end a band, stripe bundles, chevron lanes and pixels on unmapped islands: `livery.patterns`. Give a dissolve `p.rng(name)`, its tiers of cell sizes and reaches, a `field` to stay in and a palette function for zones. Pieces come out lint-clean; if a dash or chevron is refused as running along a line, move it rather than waive it.
  - Type: `typeset.type_block` with `typeset.Line`s, a designation and its small lines on one start edge raked with the lean, a `plate` with equal `pad`, `strokes` for an outlined number; `typeset.measure` sizes a line before it is placed. Its glyphs cross seams; `text` is clipped to one island, so keep it for single words that fit one.
  - Race markings (`assets/marks/`): `marks(kit, id, name, (view, u, v), colour, height, toward=...)` once the owner has approved them (`livery review --marks`); `marks.driver_banner` sets the driver's name for a window banner.
- **A pattern of your own is one element per view.** Give its many small car-space polygons to one `geom.fill` per view (one path per colour), never hundreds of `shape` elements, which make the output lint slow. Draw them with `p.rng(...)` and keep them clear of the lint's lines with the library's clearance helpers (`geom.edges(kit)`, passed to `fill` or asked per piece), which read the lint's own thresholds; never copy a lint number into a design. `geom.clean` opens away what is under `min_feature_px`.
- `livery preview flat` shows the car views; a spot can be unmapped (wheel arches, glass) or crowded by keep-clear zones; the views show where.
- A missing primitive: draw it with `svg(raw)` (template pixels) or propose a library change to the owner. Never edit `livery/` in a design session.
- `waive(element, check, reason)` is for kit and test designs; in a livery the reason must be one the owner would accept: it is recorded with their approval.
- **The kit is not yours to work around.** If a shape that is straight in car space steps, slants or doubles at a seam in the sim, or two islands disagree about where a line goes, report it (islands, where, by how much) instead of hiding it with a design change. Those were kit errors every time so far.
- **Keep the brief's character.** If no layout satisfies the critic and the lint without changing what the brief asks for (its base colour, a brand colour, light car to dark car), stop and report the conflict rather than ship the change.
