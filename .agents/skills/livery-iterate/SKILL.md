---
name: livery-iterate
description: The Fartstriber design session - the owner's brief, the livery-concepts workflow, their concept pick in livery review, the livery-refine workflow, their final review, install, export and the retro. Use whenever the owner asks for a new livery, a variant or v2, another round, or changes to existing liveries.
---

# Livery design session

The owner decides twice: the concept pick and the final. Between them the work runs on its own, in the `livery-concepts` and `livery-refine` workflows, which stop before `livery review`. This session writes the brief, starts the workflows, runs the reviews and passes on only results and what the owner has to do. It never replies to a notice that a workflow or an agent is still working.

Load `livery-design` first. Start a workflow from this checkout's script, `scriptPath: "<this checkout>/.claude/workflows/<workflow>.js"` (AGENTS.md, Harnesses, says how each harness starts one): started by name alone, it runs a copy the session loaded earlier, without later changes to the file. Both shoot in the sim unless the kit's bake stands in for the design's sim shots (`livery bake status --design <name>`): then the critic's rounds, the concept pick and the final review are baked, and no sim run is needed. When one may shoot, ask the owner to leave the preview replay open and the cursor off the sim window (`livery sim status` says what a run would find). When a `preview sim` warns that the bake drifted from the sim, the kit needs `livery bake check` (`kit-calibrate`) before baked views are trusted again.

## 1. Brief

In the owner's words: what the car is, the 2-3 favourites it draws on as a `References:` line (rows of `references.md` they name, or yes rows that fit), and the headline sponsor as the brief asks (Fireproof only when it asks, and small is fine). Add what always holds: the kit, number zones clear. `brief.md` is the brief as it stands: when they redirect it, fold their new words in, dated, and drop what they replace; no round sections. Run `livery references fetch` if those rows have no pictures in `.cache/references/`.

Another round or a change to an existing design goes straight to Refine, with their words as the note. So does a car they revived, or asked to change on a screen the page queued itself (Review again, "still OK?"): `livery library` lists it as revived or in work, and its latest note is theirs (`livery critic pack` reads it). A variant or a v2 is a new design: concepts from here, or, when they want the same car taken further, a copy of its `design.py`, `brief.md`, `notes.md` and `sponsors/` under the new name, then Refine.

## 2. Concepts

Start `livery-concepts` with 6 new design names unless they ask for more. Keep the `unshown` sketch folders it returns until the pick: the owner may revive one.

## 3. Concept pick

Run the `review` command it returned with `--sketch` for each unshown one, in the background; give the owner the `for_owner` lines (the page notifies them and needs no URL), and wait for its JSON. They pick one or more, each with a note of its own, or revive a sketch; the rest come back as not picked. Delete the sketch folders they didn't revive, which were never committed. If they answer in chat instead of pressing Send, `livery review --cancel` takes yours off the shared page before the next.

## 4. Log

Send has already appended every decision to the owner's `prefs/<person>/decisions.jsonl` and committed it with the decided designs' folders; its JSON says if it couldn't. Write the entry in their `log.md` from the review JSON, in their words: picked, not picked, raced, a favourite, parked, stopped, why, and each pin's note with the view and elements it names. A line you add for context starts with `Context:`. Commit it. A critic suggestion they ticked is not their words: it goes in no log line and no taste row.

## 5. Refine

Start `livery-refine` on the design with what the review JSON gives for it: their note verbatim, the decision's strategy, the critic suggestions they ticked and their pins (the workflow's args say how). A quick fix is one round on exactly their note, its pins and those suggestions, then back to them; a full round works the critic's fixes too, and goes back to them once their note, each pin and every endorsed suggestion are judged resolved and every taste row passes, or at a plateau, the budget or a block. The critic's own fixes never hold the car back.

Several picks (a revived sketch among them) refine one after another, each on its own note. As each stops at a review, start that review in the background and go straight on to the next pick's refine: every pick lands as its own final. A Change on one of them refines it after the refine that is running.

Its `stop` says what next:

- `ready`, `quick` (a quick fix whose note isn't resolved yet; give them its `for_owner` lines) or `budget`: Final review.
- `blocked` or `kit`: tell them each `for_owner` line at once, as a proposed kit fix (`kit-calibrate`) or library change. First look for the same fault in the calibration shots in `.cache/kit-review/<kit>/`: if the calibration design shows it too, the kit or the renderer is at fault, not the design. Refine again once they have decided.
- `character` or `sim`: theirs to answer; refine again after.
- `plateau`: its `review` is the design beside the fork's variants (a pick, as at the concept pick) or the design alone (a final). Refine again with their note (and `adopt: <variant>` when they picked one), unless they race, park or stop it.
- `error`: report it.

Delete the variant folders it lists under `remove`, and an adopted variant's once the next run has adopted it; they are never committed. Keep its `constraints` for the retro: each is a kit or library proposal.

## 6. Final review

`livery build <design>`, then `livery review <design>`. The page shows the build's sim shots, or its baked views where the bake stands in; when it would have neither (`livery status <design>`, `livery bake status --design <design>`), run `livery preview sim <design>` first. They race it (maybe as a favourite), change it, park it (not now, worth coming back to) or stop it (not worth more work); log it as after the concept pick. A final review needs a checked build, and racing pins exactly that build. On a change, refine again with what the review JSON gives. On park or stop, stop (a stop takes back an approval the design had); a parked design is picked up again only when they ask, or revive it in the Library.

## 7. Install and export

When they raced it, at once and without asking: `livery install <design>`, which refuses any other build, and tell them to run `livery install <design> --finish` after their first session of the other kind (solo or team) so that spec's .mip is archived too. Then `livery export <design>`, and give them the folder and upload line it prints, so other drivers see it through Trading Paints. A favourite is a car to learn from: add it to `references.md` as a yes row, and commit it with the design. A reference the owner supplies in chat is a yes too, with no review.

## 8. Retro

End the session with the `retro` skill (`/retro`).
