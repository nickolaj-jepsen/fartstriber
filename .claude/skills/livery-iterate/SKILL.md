---
name: livery-iterate
description: The Fartstriber design session - the owner's brief, the livery-concepts workflow, their concept pick in livery review, the livery-refine workflow, their final review, install, export and the retro. Use whenever the owner asks for a new livery, a variant or v2, another round, or changes to existing liveries.
---

# Livery design session

The owner decides twice: the concept pick and the final. Between them the work runs on its own, in two saved workflows (`.claude/workflows/`) that start `livery-builder` and `livery-critic` agents (`.claude/agents/`, which say how each does its job) and stop before `livery review`. This session writes the brief, starts the workflows, runs the reviews and passes on only results and what the owner has to do. It never replies to a notice that a workflow or an agent is still working.

Load `livery-design` first. A workflow starts with the Workflow tool, `{scriptPath: "<this checkout>/.claude/workflows/<workflow>.js", args: {...}}`: started by name, it runs a copy the session loaded earlier, without later changes to the file. Both shoot in the sim unless the kit's bake stands in for the design's sim shots: once the capture is checked and fresh and its bake check covered every finish the build uses (`livery bake status --design <name>`), the critic's rounds, the shots at the concept pick and the final review are baked, and no sim run is needed. When one may shoot, ask the owner to leave the preview replay open and the cursor off the sim window (`livery sim status` says what a run would find). A `preview sim` of a build the bake stands in for measures the bake against the sim again and warns when it drifted: then the kit needs `livery bake check` (`kit-calibrate`) before baked views are trusted again; after an iRacing update the capture is stale and needs shooting again.

## 1. Brief

In the owner's words: what the car is, the 2-3 favourites it draws on as a `References:` line (rows of `references.md` they name, or yes rows that fit), and the headline sponsor as the brief asks (Fireproof only when it asks, and small is fine). Add what always holds: the kit, number zones clear. `brief.md` is the brief as it stands: when they redirect it, fold their new words in, dated, and drop what they replace; no round sections. Run `livery references fetch` if those rows have no pictures in `.cache/references/`.

Another round or a change to an existing design goes straight to 5, with their words as the note. A variant or a v2 is a new design: concepts from here, or, when they want the same car taken further, a copy of its `design.py`, `brief.md`, `notes.md` and `sponsors/` under the new name, then 5.

## 2. Concepts

Start `livery-concepts` with `{brief, names, references}`: 6 new design names unless they ask for more (every pair is judged in both orders: 30 critics for 6 concepts, 56 for 8). It returns the critic's ranking, the top 3 checked and shot in the sim (or baked, where the bake stands in), and `for_owner`. Keep the `unshown` sketch folders until the pick: the owner may revive one.

## 3. Concept pick

`livery review <the top 3> --sketch <each unshown one>` in the background; give the owner the `for_owner` lines (the page notifies them and needs no URL), and wait for its JSON. They pick one or more, each with a note of its own, or revive a sketch (`revived`); the rest come back as not picked. Delete the sketch folders they didn't revive, which were never committed. Every session's reviews share one page: if they answer in chat instead of pressing Send, `livery review --cancel` takes yours off the page (the waiting command exits) before the next.

## 4. Log

Send has written every decision to the owner's `prefs/<person>/decisions.jsonl` (their note verbatim, a favourite, the strategy, the critic suggestions they ticked, the critic's verdict) and committed it with the decided designs' folders; its JSON says if it couldn't. Write the entry in their `log.md` from the review JSON, in their words: picked, not picked, raced, a favourite, parked, stopped, why. A line you add for context starts with `Context:`. Commit it. A critic suggestion they ticked is not their words: it goes in no log line and no taste row.

## 5. Refine

Start `livery-refine` with `{design, strategy, note, endorsed}`: their note verbatim, the decision's `strategy` (`quick` or `full`; a pick has none, which is `full`) and the critic suggestions they ticked (`endorsed`), as the review JSON gives them. A quick fix is one round on exactly their note and those suggestions, then back to them; a full round works the critic's fixes too, and goes back to them once their note and every endorsed suggestion are judged resolved and every taste row passes, or at a plateau, four rounds or a block. The critic's own fixes never hold the car back.

Several picks (a revived sketch among them) refine one after another, each on its own note. As each stops at a review, start that review in the background and go straight on to the next pick's refine: every pick lands as its own final. A Change on one of them refines it after the refine that is running.

Its `stop` says what next:

- `ready`, `quick` (a quick fix whose note isn't resolved yet; give them its `for_owner` lines) or `budget` (four rounds and not ready): 6.
- `blocked` or `kit`: tell them each `for_owner` line at once, as a proposed kit fix (`kit-calibrate`) or library change. First look for the same fault in the calibration shots in `.cache/kit-review/<kit>/`: if the calibration design shows it too, the kit or the renderer is at fault, not the design. Refine again once they have decided.
- `character` or `sim`: theirs to answer; refine again after.
- `plateau`: its `review` is the design beside the fork's variants (a pick, as in 3) or the design alone (a final, as in 6). Refine again with their note (and `adopt: <variant>` when they picked one), unless they race, park or stop it.
- `error`: report it.

Delete the variant folders it lists under `remove`, and an adopted variant's once the next run has adopted it; they are never committed. Keep its `constraints` for the retro: each is a kit or library proposal.

## 6. Final review

`livery build <design>`, then `livery review <design>`. The page shows the build's sim shots, or without them its baked views, labelled as baked, where the bake stands in; when it would have neither (`livery status <design>` and `livery bake status --design <design>`), run `livery preview sim <design>` first. The item opens with their last decision on it, the car shots they saw then beside now, and whether the critic sees their note resolved. They race it (maybe as a favourite), change it, park it (not now, worth coming back to) or stop it (not worth more work); log it as in 4. A final review needs a checked build, and racing pins exactly that build. On a change, refine again (5) with their note, its strategy and what they ticked. On park or stop, stop; a parked design is picked up again only when they ask.

## 7. Install and export

When they raced it, at once and without asking: `livery install <design>`, which refuses any other build, and tell them to run `livery install <design> --finish` after their first session of the other kind (solo or team) so that spec's .mip is archived too. Then `livery export <design>`, and give them the folder and upload line it prints, so other drivers see it through Trading Paints. A favourite is a car to learn from: propose it as a `references.md` yes row.

## 8. Retro

End the session with the `retro` skill (`/retro`).
