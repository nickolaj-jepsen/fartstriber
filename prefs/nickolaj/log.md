# Preference log

What Nickolaj picked and rejected, and why, in his words, written by the agent from `livery review` output. The record of every decision (his note verbatim, his rating, the design hash and the critic's verdict) is `prefs/decisions.jsonl`, which review's Apply writes; this log keeps what he said. Designers and the critic read all of it before every session.

Each entry:

```
## <date>: <what was decided>
Chosen: <id>, rated <1-4> — "<his note>"
Rejected: <id> — "<his note>"
Mix: <ids> — "<his note>"
Parked: <id> — "<his note>"
Context: <a line the agent adds, never read as his taste>
```

## 2026-10-18: Fireproof wordmark
Chosen: wordmark:wide — "go with the wide wordmark" (said in chat; no note on the page)
Rejected: wordmark:pixel, wordmark:wild — no reason given
Also approved in the same review: the McLaren kit (calibration re-shot after Phase 4) and all 20 references.md rows as yes. "everything looks good"

## 2026-10-18: first session, concepts (sweep, blocks, stripes)
Chosen: concept:fireproof-stripes (edit) — "This is okay, but i think something is wrong, there are more lines on the bonnet that then texture seems to apply"
Rejected: concept:fireproof-sweep, concept:fireproof-blocks — "they are just too boring" (said in chat)
Critic ranked stripes first (7/10), sweep 6, blocks 4: its first choice matched the pick.

## 2026-10-18: fireproof-stripes, final
Accepted: final:fireproof-stripes — no note. Accepted at 6/10 (iterate) from the critic in both refinement rounds; the bonnet hairlines he flagged were a bleed bug in the renderer, fixed before the final shots. The critic's open points (side stripes broken at the door, front-heavy orange) were not his reason to hold it back.

## 2026-10-18: fireproof-stripes, round 3 final
Edit: final:fireproof-stripes — "Theres a wierd \"slant\" to the side-line". Critic 6/10 (iterate) again; six sim loops used. Round 3 made the car mostly orange and dropped amber; no comment from him on that change.

## 2026-10-18: McLaren kit, side re-measure
Rejected: kit:mclaren-720s-gt3-evo — "the black horizontal line is still not perfectly algined"
Then (same day), after two more fixes: rejected once more — "Maybe it's just the perspective, but it still seem like the beltline isn't right, see the differences on the wheel arches" (it wasn't perspective: the car sits ~1.7° tilted in the side shot). Accepted once car space was levelled on the sill.

## 2026-10-18: Evangelion concepts (unit01, unit00, nerv), no wordmark
Brief: "a couple of neo genesis evangelion liverys for the mclaren"; then "feel free to skip the wordmark and logo for these, and just focus on the eva design".
Edit: concept:eva-unit01 — "I like the colors, but otherwise the design is a bit boring, maybe something more like this? https://www.tradingpaints.com/showroom/view/1053324/EVA-RT-TEST-TYPE01"
Edit: concept:eva-nerv — "I like the idea, but again a bit boring"
Rejected: concept:eva-unit00 — no note.
Critic ranked unit01 first (7/10), unit00 6, nerv 5: its first choice matched his preferred concept, but it called unit01 "isn't boring" where he found it boring.

## 2026-10-18: McLaren kit (fender-top strips) and fireproof-stripes round 4b
Accepted: kit:mclaren-720s-gt3-evo — no note. Re-fitted so the small fender-top strips have maps (the orange notch at the fender/door join).
Accepted: final:fireproof-stripes — no note. Critic 7/10 (iterate): a 1 px orange line at the fender/door shut line, front-heavy, no brand from front or chase. The car is now orange with a yellow band nose to tail, not the brief's light car; he accepted it as is.

## 2026-10-18: favourite sim liveries
Supplied in chat as "some of my favorite liveries": 14 Trading Paints showroom paints, now `references.md` rows 21-34, all yes. Asked for the design skill to learn from them and for a v2 of Fireproof.

## 2026-10-19: favourite sim liveries, again
The same list (with SPIRIT, Trading Paints 1030773, in place of a row that no longer resolves): "can you use this to improve your design-guidelines? Maybe take some learnings from them? And maybe as a last step, update the current liveries with these learning". Seen side by side with them, our cars are flat single-tone fields with one line weight and three sponsors; the favourites layer tones of each hue, pinlines, designed band ends, accent hardware and fifteen or more small marks.

## 2026-10-19: Evangelion refinements (eva-unit01 round 3, eva-nerv round 3)
Edit: eva-unit01 — "Very nice, I think it would look a lot better if there where some kind of \"stroke\" between the colors?" Critic had it at 7/10 (iterate) in rounds 2 and 3.
Edit: eva-nerv — "Better, but still a bit boring". Critic 6/10 (iterate) in rounds 2 and 3, its first fix both times was plain black areas and too little layered detail.

## 2026-10-19: Evangelion finals (eva-unit01 round 5, eva-nerv round 4)
Both accepts were to finish, not approval: "i basically just approved the last designs to get over it done, not because they were perfect". Neither is a finished design; don't read either score as his bar.
Accepted: eva-unit01 — no note. Critic 7/10 (iterate); its open points were strokes still too faint on the door and haunch plates, a ragged bonnet number field and a gap over the front wheel.
Accepted: eva-nerv — no note. Critic 6/10 (iterate) on the hex-field version; its open points were the even, directionless hex texture, the plain nose and three unconnected motifs. He accepted it after calling the stripe versions "boring" twice.

## 2026-10-19: fireproof-v2 (round 4)
Edit: final:fireproof-v2 — "The back design is nice, but the front is a bit boring". The back is the black tail with the pixel-ember dissolve and a yellow FIREPROOF on the rear face. The front is v1's flat orange with the yellow spine, the yellow band and a nose plate. Critic 7/10 (iterate) in all four rounds; in round 4 it also called the car front-heavy (bright orange and yellow nose).

## 2026-10-19: sponsor roster
Accepted: all 14 proposed brands in `assets/sponsors/roster.md` (Grivant tyres, Ondura lubricants, Ostrand brakes, Zentiq energy drink, Corbaux watches, Telvio telemetry, Rigmount sim rigs, Brandvik Freight, Hexmoor tools, Avelor fuel, Bellcrest coffee, Norvane helmets, Drexa wheels, Altmere insurance), each with its lettering voice and mark as written. No notes.
Accepted: sponsor:fireproof — the lockup (pixel flame at 1.6× the wordmark's height, left of the wide wordmark). No note.

## 2026-10-19: Fireproof pixel wordmark
Asked for the Fireproof sponsor to match his Fireproof design system: its lowercase pixel wordmark ("fire" #f46200, "proof" #DAD8CE, on the flame's grid) and its lockup replace the wide wordmark he picked on 18 Oct. Accepted: sponsor:fireproof — no note. Then: scale the wordmark in fireproof-blocks, -stripes and -sweep by about 13/7 "so the letters match the old size".

## 2026-10-19: McLaren kit, car-locked cameras
Accepted: kit:mclaren-720s-gt3-evo — no note. Measured with car-locked cameras (each side square-on, each side from 30° up, the top straight down) instead of TV shots. Each side is fitted from its own shot, and up-facing surfaces (fender and haunch tops, door shoulders) from the top shot. Known gaps he saw in the review: the spine stops over the bonnet duct ramp (i004), and the splitter top is now paintable from the top view.

## 2026-10-19: roster brands drawn (13 of 14)
Accepted with no notes: sponsor:avelor, bellcrest, brandvik (wordmark is BRANDVIK alone, without "Freight"), drexa, grivant, hexmoor, norvane, ondura, rigmount, telvio, zentiq.
Edit: sponsor:altmere — "There's a weird "ai generated/diffusion model" vibe to these, i don't really know why". Edit: sponsor:ostrand — "again, a bit AI like". Both had been pushed down to sill size by filling their crotches round, bridging joins and redrawing letters at odd widths; the shapes came out soft and melted, inconsistent letter to letter. The ones he accepted keep the font's forms or are built from clean geometry (Drexa's rectangles and quarter circles, Hexmoor's knockout bar).
Then, redrawn: Altmere from true circles and two stroke weights, Ostrand on Barlow's own letters with only A and N widened and one straight T–R rail. Accepted: sponsor:altmere, sponsor:ostrand, and sponsor:corbaux (Playfair Bold kept crisp at a 64 px minimum height, large_only: roof, bonnet or doors, never a sill), all with no notes.

## 2026-10-19: sponsor-roster car
Accepted: final:sponsor-roster — no note. Every roster brand on a light McLaren with a black lower band, one colour per field: the Fireproof lockup on the door with Grivant and Telvio lockups under it; Ondura, Hexmoor and Drexa wordmarks and the Brandvik mark along the lower door; Hexmoor and Zentiq marks on the nose corners; Ostrand and Rigmount marks behind the rear wheel; the Altmere mark on the mirrors; Avelor on the bonnet; Corbaux on the roof and Norvane and Bellcrest on the engine cover, read from the chase cam.

## 2026-10-19: liveries updated from the favourites (eva-unit01 round 7, eva-nerv round 6, fireproof-v2 round 5)
Edit: final:eva-unit01 — "The side text is a bit boring". Critic 7/10 (iterate); its open points were a rear-heavy side, a plain front and an uneven stroke between colours.
Edit: final:eva-nerv — "Here the alignment/spacing on the text is off". Critic 7/10 (iterate), up from 6; its open points were no hexes on the nose, invisible charcoal cells in the tail and EMERGENCY merging with the number block.
Edit: final:fireproof-v2 — "I like the pixel design a lot, but it's totally missing from the front". Critic 7/10 (iterate) for the fifth round; its first fix was the same: the front half has no second layer.

## 2026-10-19: second review (fireproof-v2 round 6, eva-unit01 round 8b, eva-nerv round 7b)
Edit: final:fireproof-v2 — "i said i disliked the fireproof wordmark on the car" (said in chat; the review saved no note), then: it's the new pixel wordmark itself, not its size, colour or placement. Critic 8/10 (ship) for the first time, after the pixel burn reached the front; its optional notes were thin small sponsors, two flat-orange lower-nose parts and a roof spine that stops at the windscreen.
Accepted: final:eva-unit01 — no note. Critic 7/10 (iterate), called stalled; its open points were a busy, clipped nose and TYPE-01 half under the sim's light panel on side_r.
Accepted: final:eva-nerv — no note. Critic 7/10 (iterate); its open points were hazard bars instead of hexes on the nose (put to him as his call), a sparse mid-door and a red pinline at two heights. Accepted with the nose-cell seam waiver it carries.
Installed: eva-nerv (his pick of the two accepts).

## 2026-10-19: Fireproof on the car
Asked which Fireproof brand fireproof-v2 should carry: "just remove the wordmark (or keep it smaller/more descrete), it's not imporant for my own personal brand." Fireproof is no longer a headline sponsor by rule.

## 2026-10-19: fireproof-v2 round 7, final
Accepted: final:fireproof-v2 — no note. Door wordmarks and the nose lockup removed; one small mono "fireproof" on the rear lip. Critic 8/10 (ship); its optional notes were a busy nose with an illegible flame mark, the plain yellow front fender side (a lint limit) and thin small sponsors.
Installed: fireproof-v2, replacing eva-nerv on the car.

## 2026-10-20: Ondura renamed Huilec
Context: the lubricants brand he accepted as Ondura is renamed Huilec, because ONDURA is a live registered building-materials mark of Onduline (Ondura Group) and the roster must hold invented names before paints go public (plan decision 6). Same voice, mark idea and colours; the letters are now Barlow's own, and its variants wait for his `livery review --sponsors`.

## 2026-10-21: kit v2, Huilec, race markings, and the v2 finals
Accepted: kit:mclaren-720s-gt3-evo (v2, refitted from the calibration capture), sponsor:huilec, and the race markings air, e-switch, extinguisher, fuel, jack and tow — no notes.
Accepted: final:sponsor-roster — no note.
Accepted: final:eva-unit01, rated 2 — no note. Critic 8/10 (iterate) on sim shots; the adversary's top finding was the flat acid-green plates.
Accepted: final:eva-nerv, rated 3 — no note. The critic's open fix was the nose, still not the hex display, put to him as his call.
Accepted: final:fireproof-v2, rated 1 — no note. Critic 8/10 (iterate) on baked renders; the plain yellow front fender stayed, as a kit limit (its islands are too small for a dissolve cell).
Context: these were re-approvals after the port to library v2 and kit v2.

## 2026-10-21: Zentiq concepts (bolt split, mamba, pixel blocks)
Context: the brief is the agent's, drawn from his notes and ratings so far, for a new livery through both checkpoints; he had not asked for this car. Six concepts; the critic ranked zentiq-c first, then e, b, d, f and a, and the top three were shown.
Chosen: concept:zentiq-c, rated 3 — no note. The black and white fields swap sides across the Zentiq Z bolt, traced by an acid-green pinline.
Chosen: concept:zentiq-b, rated 3 — no note. Paul Miller's dissolve in Zentiq colours, built only from horizontal blocks.
Edit: concept:zentiq-e, rated 2 — "The line across the top is a bit to straight compare to the more "natural" curves found elsewhere in the design". The acid-green mamba coiled round a black car.
Context: he took forward every concept shown, so all three are refined: c and b as they are, e with his note.
