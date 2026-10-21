# eva-nerv: notes

The design Nickolaj accepted on 19 Oct, ported onto the v2 kit (refitted from the calibration capture) and the v2 library. Each round's verdict is in `verdicts.jsonl`; what changed and why is in git.

## The design

NERV's hexagon emergency display: large orange cells cover the nose, shrink and break up over the door, and burn down to a black tail along the tape's 45° rake. No sponsor marks (Nickolaj, 18 Oct). Palette: black `#121212`, charcoal `#5c5c5c`, burnt orange `#a33f00`, orange `#f46200`, red `#d7001e` and white.

- **Rake position:** c = u − v as seen at 45° on the sides, and u − 0.19 on top, which continues the sides at their top edge. Everything that cools uses it.
- **Honeycomb (50 m, 10 m), the design's own code:**
  - **Core:** ahead of c 0.20 every large cell is orange. On the fenders and bonnet they are painted whole however the maps cut them, so the nose reads as cells. Cells touching the bonnet number (the kit's `number_top`, plus `hood_margin` 12 px) are left out, so 451 sits in a gap. Where the core's edge would run along an outline it is snapped (`geom.snapped`; one move on top, 7 px at the bonnet's left edge).
  - **Sizes:** large (r 0.04 u) ahead of c 0.22, medium (0.024) up to 0.50, small (0.012) behind. The black gap is 20% of the radius, at least `hair` 0.004 u. Each size fills the gaps the larger ones left; a smaller cell that overlaps a larger one is cut back to it and kept if three quarters remain.
  - **Cooling:** all orange ahead of c 0.34, fading linearly to none at 0.72. Burnt orange peaks at 30% halfway; the rest are charcoal, with up to 5% left black. As the field cools, the cells survive only in raked streams 0.09 apart, which narrow to 70% at the cold end.
  - **Whole cells only:** from the door's front (`door_u` 0.29) back, a cell is left out if the paint shows less than 80% of it. It is also left out if:
    - cleaning would open away a sliver of it on one island;
    - a free edge runs alongside an outline, or two neighbouring cells together run along one for the lint's run;
    - it touches the type, the plate and its keyline, the number's uprights, the kit's `door_light_panel_*` or `mirror_base_*` / `mirror_shell_*`.

    Along the tape, cells are cut straight, standing on its pinline.
  - **Rear face (`rear_face`):** no view maps it, so it carries a texture-space pattern of small charcoal cells (r 16 px).
- **Tape (`patterns.hazard`):** the low frame (45°, `band_v1` 0.23 between the wheels, `end_v1` 0.40 at the ends, stepping inside the arches), split along the lean into an orange, a burnt and a charcoal region, each cut in the black gap between two slashes. The cut is orange ahead of c 0.40, burnt up to 0.55 and charcoal behind, so the tape cools slash by slash. Each region's slashes are `period` 0.052 u apart, `phase` 0.9. The front bumper corners (`front_bumper_corner_l/_r`) get hazard tape in texture space, 48 px period. Chevrons on top (`spine_w` 0.12, opening to the full width over the tail) cool in the same steps.
- **Red pinline (`patterns.dash_run`, the accent's line weight):** 0.025 v above the tape and 0.04 v wide. On top it runs along both edges of the lane. Every run ends in three raked dashes, each 0.75 of the one before, the first 0.025 u long. On the sides it breaks over the side intake: the front run ends at u 0.49 and the rear one starts at 0.745. Under the door number it opens between two red uprights (u 0.385–0.49, up to v 0.53), so the number sits on black.
- **Features in red (`panel`):** `splitter`, `wing_endplates`, `mirrors`, `mirror_base_l/_r`, `intake_insert_l/_r`, `fender_slats_l/_r` and `number_panels`.
- **Type (`typeset.type_block`), one system:**
  - **EMERGENCY,** Barlow Condensed 800, white, three times:
    - on each door, one letter per red cell (r 0.02) in a row at `title_v` 0.725, each letter's ink centred in its cell;
    - across the roof skin (the `roof` part's whole island), reading from the front, on a red plate with 45° ends, as long as its text plus the same padding as above and below the letters.

    The door's letter-per-cell spacing is the only wide tracking.
  - **The door plate (1 m):** BLOOD TYPE: BLUE over UMBILICAL CABLE, Barlow Condensed 700, orange, 0.017 u. Both lines share one raked start edge and a line gap of `pad`. They sit on a black plate with `pad` 0.0065 u on every side, square to its raked ends. A 14 px orange keyline goes outside the plate (`geom.outline`). The plate stands a pad behind the number's right upright, centred between the pinline and the EMERGENCY row.

## Changed from the approved look, and why

Compared with the approved paint (`.cache/approved/eva-nerv/2b992d60e01d/paint.png`; its `views.png` and `car.png` are from an earlier round, 20:21, not the approved one) and with the old script rendered on the v2 kit:

- **Plate keyline:** 14 px outside the plate instead of a 6 px hairline inside it. `geom.outline` refuses strokes under `min_outline_px` (14), and the old one warned that it hardly showed on screen (5.5 px). The pad went from 0.008 to 0.0065 u (wedge 0.005–0.008), so the framed plate stays about its old size.
- **Pinline dashes:** each 0.75 of the one before instead of 0.64 and 0.56. `dash_run` refuses a last dash under the lint's minimum feature: 20 px across a 45° rake, and the old one was 14 px.
- **Cells near the light panel:** a few fewer, because the kit's `door_light_panel` box sits lower (v 0.45–0.70, not 0.55–0.745).
- **The kit's refit:** cells shift slightly on top, near the windscreen and the mirror bases. A few cells are gone where two neighbours ran along an outline together (on top at texture (999, 858) on the old build) or where a sliver would have stopped at a seam (charcoal cells on both sides).
- **The roof skin** is drawn through its own map now that the refit maps it right (u 0.33–0.455). The old script swapped in the top view's linear map there.

## Waivers

- `hex-solid.side_r`, seam: 20 of 42 seam points, all between the right front fender (i072, or the door i046) and its slats (i085, i100, i105, i108), which are painted red over the core. `hex-solid.side_l` passes (7 of 36) and has none.
- `hex-solid.top`, seam: 107 of 204 points. The top's whole nose cells sit on the top view's own honeycomb, so they don't continue onto the side islands they meet. Those are the buttresses against the mirror shells and door shoulders (i078/i150, i078/i168, i038/i078), the splitter top against the fender noses (i083/i086, i083/i120), and the fender noses against the fenders (i120/i123, i072/i086). Nickolaj accepted the build with this waiver on 19 Oct.

## Constraints that shaped the layout

- **Tape tones:** `patterns.hazard` draws two colours, so the cooling tape is three hazards over regions cut in the gaps. It takes its period in texture px and converts it at the scale where each region sits. Different regions would get different periods, so the design converts its car-space period at the point hazard measures from (`_scale_at`, which follows the library's own search order). Proposal: a hazard with a period in car lengths, or a colour per slash by rake position.
- **Lane gap:** the chevron lane breaks for 0.04 u at u 0.605, over the roof / engine cover shut line (`roof_cover_seam`, i078 / i104). Without the break the chevron there (spine-21) stops at 3 of 3 seam points between i078 and i104. `patterns.chevrons` can neither cut a lane at a gap nor open it over the tail, so the lane stays the design's own (one element per chevron, so each crosses its seams whole). Chevrons that would show only under the red splitter are left out: the seam between `splitter_top` (i083) and the bonnet (i099) broke under them.
- **The honeycomb** stays the design's own. `patterns.dissolve` would keep:
  - neither the core's whole cells where the maps cut them (it leaves out a cell shown under 85%, which would blacken the nose, the 50 m read);
  - nor the straight cut along the pinline (its cells stay whole inside the field);
  - nor a gap per size, or the cut-back interlock of sizes.
- **Rear face:** its honeycomb tile isn't square (√3 : 3), which `pattern_fill` needs, so it stays `svg(raw)` over the part's outline.
- **Pinline break:** the side intake's top edge (`intake_crease_*`) rises from v 0.16 at u 0.555 to v 0.43 at u 0.776, too shallow for a level pinline to cross, so the line breaks there.
- **Number uprights:** with no top bar and the pinline open under the number, nothing runs along the number box's edges. A closed frame ran along its top and bottom (near-tangent).
- **Lattice offsets** (`du_l` 0.02, `du_m` 0.03, `du_s` 0.015, `tdu_m` 0.01) were scanned against the 19 Oct kit and still pass on the refit.
- **Plates that don't fit:**
  - in the sill tape: the sill strips and side intake leave no island wide enough;
  - on the rear bumper corner: `rear_bumper_side_*` is narrower than a raked plate;
  - a second line on the haunch, where the body turns over and the sim cut it off.

## Warnings left (13), all intended

- **Series blocks:** EMERGENCY's door letters (three per side) and the plate's text sit on the series-sponsor block. His races stamp no series decals (policy.toml).
- **Number zones:** red `number_panels` and the rear face's cells lie on the sim's number zones. The number reads on them.
- **Thin features:** `hex-solid.top` has 4472 px of slivers where the whole core meets small islands.
- **Creases:** two of side_r's EMERGENCY letters (title-2, title-3) straddle the door shoulder's fold as the top camera sees it. They read whole in the side shots (sim 20261020-184231, side_r), and side_l's don't warn.

## Known weaknesses

From the last round (7, 19 Oct 22:06, 7/10), all left as Nickolaj accepted them (answered `skipped` in this port):
- **Nose:** from the low front camera and the sides, the nose corners, bumper sides and the fender ahead of the front wheel are full-height hazard bars and warped orange shards. The hexes read as cells only from above.
- **Mid-door:** the black under the plate and round the light panel holds only a few loose small cells.
- **Pinline heights:** the door's pinline runs low under the number. Behind the rear wheel it comes back about a tape's width higher.
- **Fewer marks** than the guidelines' ten to twenty.
