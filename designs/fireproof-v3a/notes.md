# fireproof-v3a notes

Built as the sketch stood (concept pick, 2nd of 6 in the pair ranking): `livery check` passes, sim run 20261021-195005.

## The design

- **Collage (`_collage`, one `geom.fill` per view).** Each view is drawn in seen space (u, v × aspect) as axis-aligned rectangles in paint order, flattened to what shows of each colour. Every edge sits on an 8 px grid, so no sliver is thinner than 8 px.
  - **Field rows (the biggest tier).** Rows of 0.5 / 1 / 1.5 big blocks tall. Each row has its own step in both splits, jittered by `row_jitter` 0.5 big blocks, so the splits are stacked rectangles and never a straight line. Base #100F0F, terracotta #CF6A4C from `core_front`, paper #DAD8CE from `core_rear`.
  - **Sides.** The splits are at u 0.30 and 0.56 at mid-height, raked `lean` 50° (tops towards the tail), so the change reads as one slash. Paper reaches under the door at the sill and terracotta reaches the B-pillar at the top. 55° made the slash read most clearly in the lean wedge; 50° keeps the fender black.
  - **Top.** Splits at u 0.42 and 0.62 on the centre line, `top_lean` -25° (the centre further back). Black bonnet, terracotta windscreen to roof, paper engine cover.
  - **Hairlines.** 6 px every 36 px, horizontal on the sides and lengthwise on top, under every block: #282726 on the black, #B7B5AC on the paper.
  - **Tiers of blocks, drawn from `p.rng` per view.** Each grid cell draws four numbers, whether it places a block or not.
    - field (96 px): tone blocks anywhere. Black `black_density` 0.6: #282726, #6B3528 and #CF6A4C embers, so the bonnet and nose carry the system. Core `core_fill` 0.25: #6B3528. Paper `field_density` 0.3: #B7B5AC, #F2F0E5, a little terracotta.
    - big (96 px, 1-3 wide, 0.5-1.5 tall): `big_density` 0.65, falling off over `big_reach` 0.18 from the nearest split. Each colour reaches into its neighbour.
    - mid (40 px): `mid_density` 0.45 over `mid_reach` 0.12, plus `mid_floor` 0.05 strays everywhere and `core_fill` inside the core.
    - dust (16 px): only in the core, within `dust_reach` 0.07 of a split.
  - **Sill band.** #100F0F from v 0.14, stepping ±16 px every 1-4 big blocks, over the blocks. A 14 px #CF6A4C pinline (`geom.outline`, along the stepped top) runs on it. Inside the terracotta core the pinline is terracotta on terracotta and doesn't show.
  - **Number blocks.** Solid #6B3528 on each door around number_l / number_r, padded 24 px, and solid #100F0F under number_top. No block that touches them is placed.
  - **Clear of the lint's lines.** Every tier block, and every hairline in stretches one big block long, is tested against `geom.edges(kit, margin=edge_margin_px 2)` before it is placed; one whose edge would run alongside a seam, shut line or outline is left out. That cleared the near-tangent errors without moving the splits.
  - **Painted whole.** The collage is cut off the sill strips (sill_strip_l / _r) and the splitter top, which are #100F0F panels of their own (see constraints).
- **Parts.** Mirrors, splitter lip and intake strakes are #CF6A4C panels. The wing is a paper panel with tone blocks scattered on wing_top. Endplates are #100F0F with paper blocks (`patterns.scatter`). The rear face (`rear_face_all`) is paper with 40 / 16 px tone and terracotta blocks.
- **Rear number block.** A solid #6B3528 rectangle round the kit's rear number box on rear_face, `rear_number_pad` 20 px (10 px ran within the lint's 12 px of the number zone), clipped to rear_face: white on paper was 1.5:1, now one more block of the collage (rear34 shows the 451 on it).
- **Colours as lowercase hex**, so the palette lint counts #100f0f once (it counted the base's #100F0F and a primitive's #100f0f as two).
- **Detail.**
  - Sill: Grivant mark (u 0.355) and Telvio mark (u 0.44) in #DAD8CE ahead of the side blade, and the Huilec wordmark behind the side intake (u 0.665-0.765).
  - Behind the rear wheel: Ostrand and Rigmount marks in #100F0F (u 0.975).
  - Top: bonnet front row of Hexmoor mark, Avelor wordmark and Zentiq mark in #DAD8CE (u 0.045). Rear deck row of Drexa and Brandvik wordmarks in #100F0F (u 0.978).
  - Tech label block on the haunch: FP-03 / GT3 EVO / T-2048 in Space Mono Bold, #878580, on a #F2F0E5 plate that reads as one more block of the collage.
  - Tow arrow in #DAD8CE on the front fender top. Extinguisher E in #DAD8CE in the core.
  - No Fireproof mark.
- **Swatches (8).** #100F0F, #282726, #CF6A4C, #6B3528, #DAD8CE, #B7B5AC, #F2F0E5, #878580.

## Constraints that shaped the layout

- **The sill strips' maps stop short of their outlines.** side_l maps i174 (sill_strip_l) about 10 px inside its outline at u 0.51, v 0, and side_r maps i045 likewise at u 0.53: the collage's edge ran alongside the outline for 115 px and 66 px (near-tangent errors). Both strips are a #100F0F panel of their own and the collage is cut off them.
- **The splitter top on the top view.** The collage's edge ran 50 px alongside i083's outline at top u -0.026, w 0, so splitter_top is a #100F0F panel of its own. The nose collage stops at the bonnet's front edge.
- **Blocks near seams are left out.** The near-tangent lint (12 px reach) is met by dropping any block or hairline stretch that would run alongside a seam, shut line or outline, so the collage thins a little along every seam. Without it, single blocks failed at i120 (side_l u 0.06, v 0.24, 47 px) and i080 (top, 45 px).
- **Rear face, rear valance and rear bumper: no view maps them.** The rear number block is drawn in template pixels (`svg`) round the kit's number box. They take panels and `patterns.scatter`, but no car-space anchor, so the brief's #100F0F rear-bumper sponsors and the optional flame mark on the rear valance can't be placed with `sponsor`. The tail's black sponsors went to the rear deck (top view) and the rear bumper side behind the wheel instead.
- **The side views barely map the nose.** Ahead of the front arch, u < 0.13, the side views map only scraps around v 0.25-0.3 (front_bumper_corner_l/r). The brief's #DAD8CE nose-corner sponsors moved to the bonnet front on top, at u 0.045. That sits inside series_bonnet_top (u 0.01-0.091), which is a warning only under policy.
- **The sill under the door is split.** At v 0.07 the door island i128 (i046 on the right) runs only from u 0.405 to 0.48, before the side-blade strip i173 (i039). The panel behind the front wheel, i158 / i054, runs from u 0.31 to 0.405. Sponsors clip to their anchor island, so a wordmark across u 0.32-0.50 was cut (GRIVANT lost "GRI"). Each of these islands takes one mark.
- **The rear deck is thin on top.** On the top view the rear deck i092 covers only u 0.96-1.0, |w| < 0.45, which sets the size of the deck sponsor row.
- **The text minimum.** At `min_text_px` 24 the tech label lines are 0.016 car lengths at least, so the labels are short (7 characters).
