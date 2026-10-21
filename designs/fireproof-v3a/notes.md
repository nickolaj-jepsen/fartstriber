# fireproof-v3a notes

The concept-pick build (2nd of 6 in the pair ranking) without the haunch label plate the owner called the "info-box", with a nose as layered as the tail and a raced-car mark layer the side cameras see.

## The design

- **Collage (`_collage`, one `geom.fill` per view).** Each view is drawn in seen space (u, v × aspect) as axis-aligned rectangles in paint order, flattened to what shows of each colour. Every edge sits on an 8 px grid, so no sliver is thinner than 8 px.
  - **Field rows (the biggest tier).** Rows of 0.5 / 1 / 1.5 big blocks tall. Each row has its own step in both splits, jittered by `row_jitter` 0.5 big blocks, so the splits are stacked rectangles and never a straight line. Base #100F0F, terracotta #CF6A4C from `core_front`, paper #DAD8CE from `core_rear`.
  - **Sides.** The splits are at u 0.30 and 0.56 at mid-height, raked `lean` 50° (tops towards the tail), so the change reads as one slash. Paper reaches under the door at the sill and terracotta reaches the B-pillar at the top.
  - **Top.** Splits at u 0.42 and 0.62 on the centre line, `top_lean` -25° (the centre further back). Black bonnet, terracotta windscreen to roof, paper engine cover.
  - **Hairlines.** 6 px every 36 px, horizontal on the sides and lengthwise on top, under every block: #403E3C on the black, #B7B5AC on the paper.
  - **Tiers of blocks, drawn from `p.rng` per view.** Each grid cell draws four numbers, whether it places a block or not.
    - field (96 px): tone blocks anywhere. Black `black_density` 0.6: #403E3C, #6B3528 and #CF6A4C embers. Core `core_fill` 0.25: #6B3528. Paper `field_density` 0.3: #B7B5AC, #878580, #F2F0E5, a little terracotta.
    - big (96 px, 1-3 wide, 0.5-1.5 tall): `big_density` 0.65, falling off over `big_reach` 0.18 from the nearest split. Each colour reaches into its neighbour.
    - mid (40 px): `mid_density` 0.45 over `mid_reach` 0.12; `core_fill` inside the core, `black_mid` 0.15 anywhere in the black, `mid_floor` 0.05 in the paper.
    - dust (16 px): in the core within `dust_reach` 0.07 of a split.
    - **The nose.** In the black, big, mid and dust blocks grow again towards the nose: `nose_density` 0.5 falling off over `nose_reach` 0.3 car lengths from u 0. In the black away from the split the palette is mostly #403E3C and #6B3528 with terracotta embers (0.45 / 0.35 / 0.12), so the bonnet stays a dark field. round 1's critic found the front plainer than the tail. 0.2-0.6 wedged: 0.5 layers the bonnet without turning it terracotta from above.
    - **Colours.** #403E3C replaced #282726 as the black's tone (#282726 vanished on #100F0F in the sim). #878580 joined the paper's tones (#B7B5AC alone read flat on the haunch at 10 m).
  - **Sill band.** #100F0F from v 0.14, stepping ±16 px every 1-4 big blocks, over the blocks. A `pin_px` 20 px #CF6A4C pinline (`geom.outline`, along the stepped top) runs on it, stopping at the number block's sides. Inside the terracotta core it is terracotta on terracotta and doesn't show.
  - **Number blocks.** Solid #6B3528 on each door around number_l / number_r, padded 24 px, and solid #100F0F under number_top. No block that touches them is placed. No #6B3528 block lands within `number_halo_px` 48 px of the door blocks (it turns terracotta), so their edge stays clear (round 1: in side_r the block merged with its neighbours).
  - **Sponsor fields.** No tier block is placed under the flank marks (FRONT_FIELD, HAUNCH_FIELD) or the bonnet row (BONNET_FIELD), so each mark sits on one field.
  - **Clear of the lint's lines.** Every tier block, and every hairline stretch one big block long, is tested against `geom.edges(kit, margin=edge_margin_px 2, run_margin=edge_run_margin_px 16)` before it is placed. One whose edge would run alongside a seam, shut line or outline is left out. The run margin is there because two clear blocks side by side can make one long run (it failed at side_l texture (312, 1509) without it).
  - **Painted whole.** The collage is cut off the sill strips (sill_strip_l / _r) and the splitter top, which are #100F0F panels of their own (see constraints).
- **Parts.** Mirrors and the splitter lip are #CF6A4C panels. The intake strakes are #100F0F: in the accent their curved fins read as flame licks (round 1, optional fix). The bumper corners (front_bumper_corner_l / _r) carry `patterns.scatter` blocks of #403E3C, #6B3528 and terracotta, `bumper_px` 40 and 20 px at `bumper_density` 0.75. The wing is a paper panel with tone blocks scattered on wing_top. The endplates are #100F0F with paper blocks. The rear face (`rear_face_all`) is paper with 40 / 16 px tone and terracotta blocks.
- **Rear number block.** A solid #6B3528 rectangle round the kit's rear number box on rear_face, `rear_number_pad` 20 px, clipped to rear_face.
- **Colours as lowercase hex**, so the palette lint counts #100f0f once.
- **Detail.**
  - Flank, ahead of the number on the panel behind the front wheel: Altmere and Norvane marks in #DAD8CE at v 0.40 (u 0.318-0.366, its rear edge 64 px ahead of the number), with the extinguisher E above them at v 0.70 on the same centre line. This is where the low side_l camera sees them (it loses the sill behind the verge).
  - Haunch: the HEXMOOR wordmark in #100F0F on the paper above the rear arch (u 0.775-0.865, v 0.85).
  - Sill: Grivant mark (u 0.355) and Telvio mark (u 0.44) in #DAD8CE ahead of the side blade, and the Huilec wordmark behind the side intake (u 0.665-0.765).
  - Behind the rear wheel: Ostrand and Rigmount marks in #100F0F (u 0.975).
  - Top: bonnet front row of Hexmoor mark, Avelor wordmark and Zentiq mark in #DAD8CE (u 0.045). Rear deck row of Drexa and Brandvik wordmarks in #100F0F (u 0.978).
  - Tow arrow in #DAD8CE on the front fender top.
  - No tech labels: the FP-03 / GT3 EVO / T-2048 plate on the haunch was the owner's "info-box" ("I'm not sure i like the side-pod info-box", concept pick 21 Oct). No Fireproof mark.
- **Swatches (8).** #100F0F, #403E3C, #CF6A4C, #6B3528, #DAD8CE, #878580, #B7B5AC, #F2F0E5.

## Constraints that shaped the layout

- **The sill strips' maps stop short of their outlines.** side_l maps i174 (sill_strip_l) about 10 px inside its outline at u 0.51, v 0, and side_r maps i045 likewise at u 0.53: the collage's edge ran alongside the outline for 115 px and 66 px (near-tangent errors). Both strips are a #100F0F panel of their own and the collage is cut off them.
- **The splitter top on the top view.** The collage's edge ran 50 px alongside i083's outline at top u -0.026, w 0, so splitter_top is a #100F0F panel of its own. Blocks scattered on it (24 / 16 px, all that fit its thin strip) measured 3.5 px on screen in front34 and 1.5 px on top (`screen_width`), so it stays plain.
- **Blocks near seams are left out.** The near-tangent lint (12 px reach, 40 px run) is met by dropping any block or hairline stretch that would run alongside a seam, shut line or outline, so the collage thins along every seam. Without it, single blocks failed at i120 (side_l u 0.06, v 0.24, 47 px) and i080 (top, 45 px).
- **The side views barely map the nose.** Ahead of the front arch, u < 0.13, the side views map the bumper corners (i021 / i020) only in slivers, so the collage drops nearly every block there. The corners take scattered blocks on their own islands instead, axis-aligned in the texture, not in car space. Only a few 40 px squares fit their narrow bands at all. The brief's nose-corner sponsors are on the bonnet front on top, at u 0.045, inside series_bonnet_top (u 0.01-0.091), which is a warning only under policy.
- **The low side_l camera loses the sill.** The verge in front of the car hides v < ~0.15 in side_l, so the sill marks and the sill pinline show only in side_r, front34 and rear34. The flank marks are above it.
- **Flank marks between the arch, a crease and the number.** On the panel behind the front wheel (fender_rear, i158 / i054), the number's 64 px keep-clear leaves u < 0.366, and the front arch leaves u > ~0.315. A crease crosses the panel at about v 0.56, so a column of two marks there was bent (norvane 33-35 % beyond the fold): the marks sit side by side at v 0.40 and the E above the crease.
- **The haunch row above the rear arch.** The shoulder has a crease at about u 0.87, v 0.85 that bent a second mark (Drexa 18 %, Hexmoor 35 % when it led), and lower down the texture is too compressed for two marks at their minimum sizes, so the haunch carries one wordmark. It sits in the banner_l zone (u 0.753-0.925, v 0.769-0.973), a warning only under policy.
- **Rear face, rear valance and rear bumper: no view maps them.** The rear number block is drawn in template pixels (`svg`) round the kit's number box. They take panels and `patterns.scatter`, but no car-space anchor, so the brief's #100F0F rear-bumper sponsors and the optional flame mark on the rear valance can't be placed with `sponsor`. The tail's black marks are on the rear deck (top view) and the rear bumper side behind the wheel instead.
- **The sill under the door is split.** At v 0.07 the door island i128 (i046 on the right) runs only from u 0.405 to 0.48, before the side-blade strip i173 (i039). The panel behind the front wheel, i158 / i054, runs from u 0.31 to 0.405. Sponsors clip to their anchor island, so a wordmark across u 0.32-0.50 was cut (GRIVANT lost "GRI"). Each of these islands takes one mark.
- **The rear deck is thin on top.** On the top view the rear deck i092 covers only u 0.96-1.0, |w| < 0.45, which sets the size of the deck sponsor row.
