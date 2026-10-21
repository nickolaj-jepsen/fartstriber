# fireproof-v3e notes

Concept build for the owner's concept pick (ranked 3 of 6 by the pair critics): the sketch as it was, with
only what `livery check` needed. Check passes; sim run 20261021-194036 shows the build.

## The design

- **Base:** paper #DAD8CE, gloss.
- **Five slashes, one shape at five levels of detail.** Each slash is a raked band in car space (lean 30° as seen, top towards the tail), given by its front edge where it crosses v 0.5 (`s`) and its width along u (`w`), with a terracotta #CF6A4C trailing edge behind it. On top each carries on as a chevron bar (top_lean 15°, outer ends towards the tail) that starts where the slash leaves the side at v 1.
  - 1, LOD 0, s 0.11 w 0.09: front fender above the wheel and the bonnet bar. Drawn as vector, with an 18 px terracotta pinline. The bonnet bar holds number_top.
  - 2, LOD 1, s 0.385 w 0.115: the door; the roof bar. number_l and number_r are on solid black (rasterised check: 0% uncovered at these values).
  - 3, LOD 2, s 0.62 w 0.08: behind the door shut line, ahead of the door light panel; the front of the engine cover.
  - 4, LOD 3, s 0.79 w 0.09: the haunch above the rear arch, behind the light panel; the rear of the engine cover.
  - 5, LOD 4, s 0.95 w 0.12: the rear bumper corner, rear deck and wing.
- **Rasteriser:** LOD n (n ≥ 1) is drawn on a grid of square pixels (square as seen, in each view's aspect) of `cell · ratio^(n-1)` car lengths, with cell 0.009 and ratio 2.3 (0.009, 0.021, 0.048, 0.110). Each pixel gets one colour from its coverage: black ≥ 0.62, #878580 from 0.38, #B7B5AC from 0.14 (anti-aliasing on paper), terracotta ≥ 0.6 or ≥ 0.4 with little black, #6B3528 where black and terracotta share a pixel. The trailing edge is one pixel wide at each LOD, so it becomes a staircase of terracotta blocks at the tail. A ratio over 2 keeps the tail's blocks reading bigger than the door's in the front three-quarter view, where perspective shrinks the tail (wedge ratio 2.0-2.6). Each LOD owns a raked zone of the car, split halfway along the paper between slashes, and its pixels are clipped to it.
- **Loose blocks:** behind slashes 4 (half rate) and 5, paper pixels break off with a chance that falls off with distance (loose 0.55, reach 2.5 pixels): 60% black, 25% terracotta, 15% grey. Same seed on both sides, so both sides match in car space.
- **Lower body:** black sill at v 0.17, one height nose to tail, part of the black field so its junctions with the slashes are rasterised with them; the sill groups and both front bumper groups are whole black panels; splitter top black, splitter lip and mirror shells terracotta. The side pixel fills are clipped off the sill strips (sill_strip_l/r), which the sill panel paints whole.
- **Tail:** rear_face_all (rear face, valance, tail-light trims) and number_panels are black, so the sim's rear and small numbers sit on #100F0F (they were 1.4:1 on paper). The tail reads as the end of slash 5 in rear34.
- **Labels:** "LOD 1" in Space Mono Bold, #878580, em 0.022, raked with the lean, just ahead of slash 2 on the door at v 0.7; "LOD 2" on the roof centre (u 0.52), read from behind.
- **Sponsors:** #DAD8CE on the black sill: Telvio and Avelor marks on the rear fender's sill (u 0.32-0.395), Huilec wordmark and Drexa mark behind the side intake (u 0.665-0.765), both sides. Fireproof: none.

## Constraints that shaped the layout

- The top view's nose is fragmented (headlights unmapped: no island at u 0.04-0.1, |w| 0.45-0.6; u 0.07 |w| 0.62 lands on the splitter lip i080), and series_bonnet_top keeps u 0.01-0.091 |w| < 0.324 clear: no room for nose-corner sponsors or a LOD 0 label on top.
- The front fender above the arch (i123, u 0.23-0.28, v 0.7-0.85) is too small for a label: "LOD 0" there showed 57% of its O.
- Sponsors clip to their anchor's island: on the sill the rear fender (i158) meets the door (i128) at u 0.41, and the side intake strip's diagonal edge cuts the door's sill from u ~0.42 back to the intake (marks there showed half). Only i158 u 0.32-0.395 and i128 u 0.665-0.765 take a row; three marks didn't fit in the first at their minimum sizes (Avelor 24 px, Grivant 21 px).
- series_door_l/r (u 0.484-0.754, v 0.25-0.78) keeps text off the paper behind slash 2 and around slash 3, and the door light panel (u 0.712-0.772, v 0.45-0.7) sits between slashes 3 and 4.
- The rear arch takes u 0.776-0.952 below v 0.77, so slash 4 shows on the side only above it and mostly on the haunch tops (mapped from the top view).
- The engine cover's centre between bar 3's trailing edge and bar 4 holds the cover slot: a "LOD 3" label there overlapped it and the terracotta edge.

- The sill strips' maps stop short of their island outlines (i174 u 0.431-0.711 and i045 u 0.431-0.733, v 0-0.038): the pixel fill's black ended 10-11 px inside the outline for 66-115 px around u 0.51-0.53 (near_tangent error), so the fills leave those islands to the sill panel.

## Open

- The front: the crisp LOD 0 bar leaves the bonnet, nose and front fender as plain paper fields. Every pair critic named this as the concept's risk (the owner's v2 "pixel design… totally missing from the front"); kept as the concept is for the pick.
- Sponsors: 8 marks (4 per side), short of the brief's 12-16; the haunch and nose corners in #100F0F still to place. In the side_l sim shot the sill marks are below the camera's horizon.
- Labels LOD 0, 3 and 4 still to place.
- Lint warnings left: thin slivers (min_size) where the pixel grid is clipped at its LOD zones (top 8108 px, sides ~1700 px), and 1850 px of pixels under the door light panels.
