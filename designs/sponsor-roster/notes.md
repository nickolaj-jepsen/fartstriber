# sponsor-roster: notes

A fixture (`ROLE = "fixture"`), not a livery to race: every roster brand on the McLaren in the groups a race car carries them, accepted by Nickolaj on 19 Oct. Ported to the v2 kit and library on 20 Oct; what changed is in git.

## The design

Light body `#ecebe6`, a black lower band `#151515`. One colour per field: black marks on the body, body-coloured marks on the band. No waivers.

- **Band:** each side one element, the car-space rectangle under `band_v` 0.36 (u -0.2 to 1.2) united with the kit's parts in `WHOLE`, painted whole: the sill groups `sill_l` / `sill_r` (sill strip and side intake strip) and the grille uprights `grille_upright_l` / `grille_upright_r`. Its edges go through `geom.snapped` (no move is needed on this kit) and it is tagged `crosses_seam`. It is one element, not `stripe` plus `panel`: apart, the band alone has slivers under 6 px where the sill maps stop short (`min_size`), and cut out of the sills it breaks the seam pairs at their outlines (`seam`).
- **Door:** the Fireproof lockup (`head_u` 0.62, `head_v` 0.76, `head_h` 0.042), and Grivant and Telvio as a row at `door_v` 0.585 from u 0.35 to 0.70.
- **Lower door, on the band:** Huilec, Hexmoor and Drexa wordmarks and the Brandvik mark at `sill_v` 0.23, u 0.41-0.775, gap 0.22.
- **Rear bumper corner:** Ostrand and Rigmount marks stacked at `rear_u` 0.965, v 0.78-0.42.
- **Grille uprights (the nose):** Hexmoor and Zentiq marks stacked at `nose_u` -0.005, v 0.33-0.09, height 0.018, on the uprights the band paints whole. The uprights' mapped strip is narrow: a field start at u -0.0125 or 0 is off every mapped island; the wedge over -0.01..-0.0025 looked the same in the baked front34, so it stays at the approved -0.005.
- **Mirrors:** the Altmere mark at (0.375, 0.995).
- **Top:** Avelor on the bonnet (`bonnet_u` 0.05), Corbaux on the roof (`roof_u` 0.51, reading from the rear), Norvane and Bellcrest stacked on the engine cover (`deck_u` 0.72, reading from the rear).

## Constraints that shaped the layout

- **Grille uprights i147 / i069:** the refit maps only part of each upright (body.yaml: u 0.008-0.04 is never seen). The band's footprint stopped at the map's edge inside the island, running along its outline (near-tangent error, side_l around texture (179, 1661), car (-0.009, 0.171)), and the nose marks lost most of their black field. Painting the parts whole fixes both and keeps the approved black uprights.
- **Sill groups:** the sill strip and side intake strip maps (i174, i178, i173; i045, i043, i039) stop short of their outlines, so they are painted whole.
- **Door islands i128 (left) / i046 (right), front-lower tip** (texture about x 650-780 on each): behind the front arch, the refit leaves it unmapped (approved: black under the band; now body colour in the texture). No sim shot of run 20261020-182540 sees it.
- **Sill row under the carbon side blade:** Hexmoor on the lower door sits partly under `carbon_side_blade_l` / `_r` (`occluded`, about 620 px a side). Square-on it reads; kept where it was approved.
- **Nose marks on side_l:** the left upright is seen well only from front34 (`unseen`: Zentiq 61%, Hexmoor 39% hidden from every preview view).
- **Fireproof lockup on side_r** straddles a crease the top shot shows (34% beyond it, `crease`).
- **Series sponsor blocks and number zone:** the door, headline, sill and bonnet marks sit on the template's series blocks, the band overlaps the sim's number zone, and the sim's number has low contrast on the light body (`keep_clear`, `number_contrast` warnings): this car tests the roster at its placed sizes, not a race layout.
- **Roof:** Corbaux's C falls off the roof island it is clipped to (the anchor's island), so the roof reads ORBAU, as it did in the approved build.
