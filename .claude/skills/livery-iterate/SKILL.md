---
name: livery-iterate
description: The Fartstriber design session - the owner's brief, the livery-concepts workflow, their concept pick in livery review, the livery-refine workflow, their final review, install, export and the retro. Use whenever the owner asks for a new livery, a variant or v2, another round, or changes to existing liveries.
---

# Livery design session

The owner decides twice: the concept pick and the final. Between them the work runs on its own, in two saved workflows (`.claude/workflows/`) that start `livery-builder` and `livery-critic` agents (`.claude/agents/`, which say how each does its job) and stop before `livery review`. This session writes the brief, starts the workflows, runs the reviews and passes on only results and what the owner has to do. It never replies to a notice that a workflow or an agent is still working.

Load `livery-design` first. A workflow starts with the Workflow tool, `{name: "<workflow>", args: {...}}`. Both shoot in the sim unless the kit's bake stands in for the design's sim shots: once the capture is checked and fresh and its bake check covered every finish the build uses (`livery bake status --design <name>`), the critic's rounds, the shots at the concept pick and the final review are baked, and no sim run is needed. When one may shoot, ask the owner to leave the preview replay open and the cursor off the sim window (`livery sim status` says what a run would find). A `preview sim` of a build the bake stands in for measures the bake against the sim again and warns when it drifted: then the kit needs `livery bake check` (`kit-calibrate`) before baked views are trusted again; after an iRacing update the capture is stale and needs shooting again.

## 1. Brief

In the owner's words: what the car is, the 2-3 favourites it draws on as a `References:` line (rows of `references.md` they name, or yes rows that fit), and the headline sponsor as the brief asks (Fireproof only when it asks, and small is fine). Add what always holds: the kit, number zones clear. `brief.md` is the brief as it stands: when they redirect it, fold their new words in, dated, and drop what they replace; no round sections. Run `livery references fetch` if those rows have no pictures in `.cache/references/`.

Another round or a change to an existing design goes straight to 5, with their words as the note. A variant or a v2 is a new design: concepts from here, or, when they want the same car taken further, a copy of its `design.py`, `brief.md`, `notes.md` and `sponsors/` under the new name, then 5.

## 2. Concepts

Start `livery-concepts` with `{brief, names, references}`: 6 new design names unless they ask for more (every pair is judged in both orders: 30 critics for 6 concepts, 56 for 8). It returns the critic's ranking, the top 3 checked and shot in the sim (or baked, where the bake stands in), and `for_owner`. Delete the `unshown` sketch folders, which were never committed.

## 3. Concept pick

`livery review <the top 3>` in the background; give the owner its URL and the `for_owner` lines, and wait for its JSON. They pick one, or mix several with a note, and may rate them. Every session's reviews share one page, one waiting per checkout: if they answer in chat instead of pressing Apply, `livery review --cancel` takes yours off the page (the waiting command exits) before the next.

## 4. Log

Apply has written every decision to `prefs/decisions.jsonl` (their note verbatim, their rating, the critic's verdict) and committed it with the decided designs' folders; its JSON says if it couldn't. Write the entry in `prefs/log.md` from the review JSON, in their words: chosen, rejected, mix, parked, why. A line you add for context starts with `Context:`. Commit it.

## 5. Refine

Start `livery-refine` with `{design, budget: 4, note}`: the picked design (for a mix, the one they named first) and their note verbatim. Its `stop` says what next:

- `ship`, or `budget` (four rounds without ship): 6.
- `blocked` or `kit`: tell them each `for_owner` line at once, as a proposed kit fix (`kit-calibrate`) or library change. First look for the same fault in the calibration shots in `.cache/kit-review/<kit>/`: if the calibration design shows it too, the kit or the renderer is at fault, not the design. Refine again once they have decided.
- `character` or `sim`: theirs to answer; refine again after.
- `plateau`: its `review` is the design beside the fork's variants (a pick, as in 3) or the design alone (a final, as in 6). Refine again with their note (and `adopt: <variant>` when they picked one), unless they accept or park.
- `error`: report it.

Delete the variant folders it lists under `remove`, and an adopted variant's once the next run has adopted it; they are never committed. Keep its `constraints` for the retro: each is a kit or library proposal.

## 6. Final review

`livery build <design>`, then `livery review <design>`. The page shows the build's sim shots, or without them its baked views, labelled as baked, where the bake stands in; when it would have neither (`livery status <design>` and `livery bake status --design <design>`), run `livery preview sim <design>` first. The item opens with their last decision on it, the car shots they saw then beside now, and whether the critic sees their note resolved. They accept (good enough to race), edit with a note, or park it, and may rate it; log it as in 4. A final review needs a checked build, and accepting pins exactly that build. On edit, refine again with their note. On park, stop.

## 7. Install and export

On accept only: `livery install <design>`, which refuses any other build, and tell them to run `livery install <design> --finish` after their first session of the other kind (solo or team) so that spec's .mip is archived too. Then `livery export <design>`, and give them the folder and upload line it prints, so other drivers see it through Trading Paints.

## 8. Retro

End the session with the `retro` skill (`/retro`).
