# fireproof-v2: notes

The design as it stands: the look Nickolaj accepted on 19 Oct (round 7), ported onto the v2 library and the refitted McLaren kit (kit version 2), then refined from the round-8 verdict: a wider black edge under the side band, the wing wordmark moved clear of the swan necks, the nose plate without the flame mark, and a few more small marks. Each round's verdict is in `verdicts.jsonl`; what changed and why is in git.

## The design

v1's flame car burning into a black tail through a pixel dissolve, with the front burning too, more lightly. Palette: orange `#f46200`, flame `#ff8000` (the mark's middle tone), yellow `#ffd500`, black `#141414` and ash `#474747`. The sponsors are in the proof cream `#DAD8CE` (logos aren't counted). Parts are named from the kit's body model (`kits/mclaren-720s-gt3-evo/body.yaml`), never by island id.

- **50 m:** an orange front and a black tail wedge.
  - On the sides the burn line meets v 0 at `burn_u` 0.525 and leans `burn_angle` 58°, from the door's foot to the roofline over the rear arch.
  - On top it runs straight across at `top_u` 0.78 for |w| ≤ 0.35, then comes forward by `top_rake` 0.5 over the haunch tops.
  - The yellow core band runs nose to tail on the sides (`side_v0` 0.615 to 0.92). Its black edges run from `pin_v0` 0.572 to 0.615 below (about 14 px) and 0.92 to 0.94 above. The lower edge straddles the door's crease (about v 0.59), so it shows on both faces: at 0.585–0.6 it sat in the crease and read as its shadow, leaving yellow and orange bare on the door (round 8).
  - The yellow spine (|w| ≤ 0.1) runs from the lip over the roof to the engine cover, with a black pinline traced round it by `geom.outline`. Its width is (`gap_w` − `core_w`) × the top's px per w, about 22 px.
  - No big wordmark.
- **10 m:**
  - **The dissolve:** black behind each line is a staircase of rows on a grid of `cell_u` 0.016 (about 7 cm), square as seen. Three `patterns.dissolve` runs per view sit on the same grid (origin u 0, v `sill_v`; on top, w −1.1), with cells of 2, 1 and ½ cell (`SIZES`):
    - black pieces ahead of the line (`char_reach` 0.1 on the sides, `top_char_reach` 0.06 on top, falloff 1.5);
    - embers behind it (`ember_reach` 0.44, `ember_density` 1.0, falloff 2), yellow on the band and the spine, orange or flame elsewhere;
    - ash at an even `ash_density` 0.2.

    Each tier's reach is measured square to the line (× cos 58° on the sides). `_dissolve` sizes the tiers in texture px at the same place the dissolve measures its scale, so the cells come out exactly `cell_u` and line up with the staircase. Embers and ash stay out of the haunch's sponsor box, the rear quarter's mark box, the sim's door light panels, the rear deck (`rear_deck`) and a box round the wing's wordmark (u `tail_u` ± `tail_h`, |w| ≤ 0.34).
  - **Tuning:** the densities and reaches were raised from the hand-drawn version (ember 0.6/0.36, ash 0.1, top char 0.02, front 0.6, bonnet 0.9/0.2), because the library's dissolve drops cells that overlap a larger one, don't show whole or can't be kept clear. These values bring the pixel area back to about the approved car's: side embers about 11k px against 13–17k, top embers 38k against 37k.
  - **The front burn:**
    - On the sides, black pieces (`front_reach` 0.18, `front_density` 0.85) and embers (yellow, orange on the band; `front_ember_reach` 0.24, `front_ember_density` 0.55) behind a line from u 0.28 at v 0, leaning 0.1 u per v.
    - On top, black corner fields to `bonnet_line` 0.09 outside the plate, then black pieces (`bonnet_reach` 0.3, `bonnet_density` 1.0) and yellow embers behind the plate's frame and the corner line. They stay off the spine (|w| < 0.19) and the bonnet marks' box.
    - Every dissolve stays off the numbers.
  - **The bonnet vent** (`bonnet_vent`: the ramp and its walls) is black, with ember squares (26 and 13 px) scattered by `patterns.scatter`.
  - **Tones:**
    - Stepped flame tongues (`TONGUES`) rise from the black sill around the number (u 0.41–0.47, v 0.29–0.53).
    - Three flame hairlines (`hair_u` 0.005 wide, 0.085–0.115 ahead of the line) run from the sill to the pinline.
    - A row of flame pixels runs outside the spine's pinlines.
  - **Lower body:** a black sill to `sill_v` 0.18, reaching the nose through black bumper corners. The nose's sides below the band are black (`side-nose`, u ≤ 0.17).
  - **Features:**
    - In yellow: the mirrors, the wing endplates, the intake strakes and inserts, the right intake's bar and the grille header.
    - In black: the wing, the swan necks, the bumper corners, the crash opening, and the grille's uprights and side faces and the intakes' inner edges. The last three are new in the port: they were the flat orange the critic saw in the lower nose.
    - The splitter (`splitter`: lip and top) is in `patterns.hazard` tape: yellow with black slashes every 32 px, leaning 26.6°, now continuous diagonals instead of the old stepped tile.
  - **Nose:** a black plate (`plate_w` 0.22, to `plate_u1` 0.1) framed by a yellow `geom.outline` of `frame_w` × px per w (about 17 px). The spine's yellow core runs plainly on through it. The flame mark it carried in black read as a keyhole from front34 and added a sixth idea to the busiest part of the car, so it is gone (round 8).
- **1 m:**
  - Ash pixels in the black.
  - Roster marks in cream: hexmoor, huilec and telvio in the sill's front field (u 0.425–0.48), and the grivant wordmark in its rear field (0.648–0.768).
  - zentiq on each haunch below the door light panel.
  - The ostrand mark in cream on each tail quarter behind the rear wheel (`rear_u` 0.975, `rear_v` 0.37, `rear_h` 0.023).
  - A grivant mark in black on each mirror, and rigmount and norvane in black on the bonnet's left flank (height 0.027, up from 0.021, since they didn't read in any shot).
  - The Fireproof flame in its own colours on each sill (u 0.33), and one small yellow "fireproof" (`tail_h` 0.03) on the wing's top, the only Fireproof wordmark. At `tail_u` 1.04 it sits towards the wing's trailing edge, where the chase camera sees it whole between the swan necks. At 1.01 the right neck's upright hid its last letter in rear34.

`livery check` passes with 0 errors, 27 warnings and 3 waivers. The camera warnings show what the cameras barely see:

- the hairlines are under 8 px on screen;
- the sill marks and the mirror marks straddle creases;
- the vent's embers are foreshortened.

The keep-clear warnings are paint under the sim's door light panel and number (bands and black, not text), and the wing's wordmark on a banner block his races don't stamp. The min_size warnings are slivers where stripes and staircases meet island edges.

The waivers (`STOPS_AT_SPLITTER`) are for `nose-plate.top`, `nose-core.top` and `side-nose.side_r`. They end where the splitter's top begins, at the seam the refit now pairs, because the splitter carries its own hazard tape. Covering the splitter instead fails near-tangent (next section).

## Constraints that shaped the layout

- **The splitter's top (i083)** has a top map that stops about 6 px short of its outline at texture x 84, around (83, 1206) on the centre line. Any top-view shape on it fails near-tangent there, so every top element is clipped out of `splitter`. Since the refit, the bonnet–splitter seam is paired, so the clipped plate and nose core count as stopping at a seam: hence the waivers. On the right side, `fender_nose_r` (i086) is paired with i083 too, and the black side-nose stops there (9 of 17 seam points). On the left, `fender_nose_l` (i120) isn't paired, so there is no waiver on that side.
- **`patterns.dissolve` has no shared grid with a design's own shapes:** its cell size is the tier's px over a scale it measures at the region's middle, and that scale varies 1450–1502 px per u along a side. To line its cells up with the staircase, `_dissolve` repeats that measure, so a change to how the library measures its scale would shift the grid. A library proposal: a dissolve that takes its cell size in car units, or that also draws the solid field behind its line as a stepped edge.
- **The front fender's side** (`front_fender_l` i123, `fender_slats_l` i137/i130/i125/i122/i121, `fender_nose_l` i120, and the right-side mirrors) is a mesh of small islands. With the front line at u 0.0 the dissolve places no cell there at all; at u 0.1, one cell lands on the fender top. So the front burn starts behind the arch, and the fender side stays plain yellow (blocked fix, round 7).
- **The first tongue** runs on to u 0.4. The door (i128) shares an island with the never-seen u 0.29–0.40 under the rear fender (i158), and the seam between them pairs at u 0.384. Ending the tongue there stopped it at that seam.
- **The tongues' first row** reaches one row below `sill_v`, under the sill. The door-to-side-intake-strip seam (i128/i173, i046/i039) runs at v 0.175–0.19 near u 0.6, so a tongue starting at the sill stopped at it.
- **Sill strips:** `sill_l`/`sill_r` (sill strips and the side-intake strip) are mapped short of their outlines. They are painted whole inside the sill's own element (so the sill crosses their seams) and cut out of the burn. The side-intake strip sits under the carbon side blade (an occluder): no sponsor there.
- **Sill fields** for sponsors are short (the door at u 0.37–0.485 and the haunch at 0.635–0.775, at v 0.09). Below u 0.425 on the left door the sill is hidden from every view: the hexmoor mark was unseen there at u 0.405.
- **The sim's door light panel** (`door_light_panel_l`/`_r`, u 0.712–0.772, v 0.45–0.70) sits over the band and the burn. Embers and ash avoid it, and zentiq sits below it.
- **The side number zones and the door's crease** fix the band's lower edge. The number zones' tops are at v 0.522 (side_l, texture y 1792) and 0.531 (side_r, y 621), about 320 px per v there. An edge has to stay `near_tangent_px` (12 px) clear of them, so it can't sit below v 0.568 on side_r. At `pin_v0` 0.55 the lint snapped it locally, 7 px up on side_l and 8 px down onto the zone on side_r. The crease is at about v 0.59, where a thin edge reads as shadow. So the edge runs 0.572–0.615, across the crease, and the yellow starts 0.015 v higher than the approved 0.6.
- **The outline minimum** (`min_outline_px` 14) is above the upper side pinline (about 6 px). The pinlines stay as stacked stripes rather than `geom.outline`.
- **The tail quarter** behind the rear wheel (i160 left, i053 right) is mapped only at about u 0.96–0.99, v 0.25–0.5, about 0.03 car lengths a side. Two marks in a column didn't fit at their minimum sizes (ostrand needs 0.0222, drexa 0.0189 car lengths), so it carries one.
- **The wing's top and the swan necks:** from rear34 the right neck's upright hides the wing's top at u 1.01 next to its foot, so the wordmark sits at u 1.04 towards the trailing edge.
- **The hairlines** at 0.005 u are about 4 px across the rake. Clearing min_size would take about 0.011 u, which no longer reads as a hairline, and even 0.008 stays under the 8 px screen minimum. They are kept at the approved width.
- **Unmapped top panels** (windscreen i103, its frame i064, the vent floor i107) are never seen (99% by the camera checks), so the spine no longer goes over them in texture pixels.
- **Tiny islands and islands never seen**, dropped in the port: i068, i075, i148, i153 (1–21 px), i048 and i022.
