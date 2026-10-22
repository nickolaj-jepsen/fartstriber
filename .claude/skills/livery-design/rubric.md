# Livery rubric

Shared by designers and the livery-critic, with the taste table (`taste.md`). Judge what the sim shots show, not what the script intends.

## Items, each scored 1–5

A round's `scores` name every key of this table; `livery critic` reads the keys from it.

| Key | What it asks |
|---|---|
| `reads_at_distance` | In the distance images and the squint thumbnail, does the car still read as one strong idea, and does a lead sponsor, if the brief has one, still read as a word? |
| `one_design` | Do both sides, the top, the nose and the tail read as the same design, not separate panels? |
| `seams_and_edges` | In the beltline and nose crops and every shot: do shapes continue across seams and the beltline, and does every edge clearly cross or clearly avoid each seam, shut line and number zone? |
| `sponsor_hierarchy` | Are the marks ordered as the brief asks: a lead where it asks for one, the rest clearly secondary and grouped, and no lead where it asks for none? |
| `type` | Is the type designed: blocks aligned to a shared edge or baseline, even spacing, padding and tracking, a clear hierarchy (a designation and smaller lines), one system across the car? |
| `body_lines` | Do the shapes follow or deliberately cut the car's lines (swage, arches, windows), rather than fight them? |
| `balance` | Is the visual weight balanced front to back and top to bottom; is there one focal point, not several competing? |
| `depth` | Is there something to see at every distance: the big idea from far, a second layer (tones, transitions, pinlines, lower body) mid-range, and detail that rewards a close crop? A clean big shape and nothing else scores 2; the owner calls that boring. A plain area larger than a door, a bare edge between two fields, or a single line weight caps it at 3; an even texture with no direction caps it at 3. |
| `colour` | A disciplined palette (three hues plus neutrals, tones of them allowed), one accent at about a tenth of the car, contrast where it matters, the number zones readable? |

1 = fails, 3 = competent, 5 = could not be better.

## Overall, 1–10, logged only

The scores and the overall are logged to follow a design over time; they decide nothing. 8 or more reads instantly at distance, rewards a closer look and has only polish left; 6–7 is a good or competent concept with fixes left; 5 or less fails as a concept.

Ship is computed by `livery critic status`, never chosen; the conditions your verdict sets:

- no fix that isn't optional is open: none of yours, and every one of the last round's marked `resolved`
- the owner's note, when the pack has one, `resolved`
- nothing under `blocked`
- every row of `taste.md` passing

On a round that doesn't ship, the critic's call is **iterate** (the concept holds; fix it) or **rework** (the concept is what fails; go back to concepts, not to the parameters).

## Fixes

At most three, the most important first, each describing what is visible and where, never how to code it: "the orange band dies at the C-pillar", "FIREPROOF sits 2 cm above the door shut line", "the right side's stripe is a step lower than the left's". Each is marked optional or not: optional is polish the owner wouldn't send the car back for.
