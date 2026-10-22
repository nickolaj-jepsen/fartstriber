---
name: livery-iterate
description: The Fartstriber design session - the owner's brief, the livery-concepts workflow, their concept pick in livery review, the livery-refine workflow, their final review, install, export and the retro. Use whenever the owner asks for a new livery, a variant or v2, another round, or changes to existing liveries.
---

# Livery design session

The owner decides twice: the concept pick and the final. Between them the work runs on its own, in the `livery-concepts` and `livery-refine` workflows, which stop before `livery review`. This session writes the brief, starts the workflows, runs the reviews and passes on only results and what the owner has to do. It never replies to a notice that a workflow or an agent is still working.

Load `livery-design` first. A workflow starts with the Workflow tool. Both shoot in the sim: when you start one, ask the owner to leave the preview replay open and the cursor off the sim window (`livery sim status` says what a run would find).

## 1. Brief

In the owner's words: what the car is, the 2-3 favourites it draws on as a `References:` line (rows of `references.md` they name, or yes rows that fit), and the headline sponsor as the brief asks (Fireproof only when it asks, and small is fine). Add what always holds: the kit, number zones clear. `brief.md` is the brief as it stands: when they redirect it, fold their new words in, dated, and drop what they replace; no round sections. Run `livery references fetch` if those rows have no pictures in `.cache/references/`.

Another round or a change to an existing design goes straight to Refine, with their words as the note. A variant or a v2 is a new design: concepts from here, or, when they want the same car taken further, a copy of its `design.py`, `brief.md`, `notes.md` and `sponsors/` under the new name, then Refine.

## 2. Concepts

Start `livery-concepts` with 6 new design names unless they ask for more. Delete the `unshown` sketch folders it returns, which were never committed.

## 3. Concept pick

Run the `review` command it returned, in the background; give the owner its URL and the `for_owner` lines, and wait for its JSON. They pick one, or mix several with a note, and may rate them. If they answer in chat instead of pressing Apply, `livery review --cancel` takes yours off the shared page before the next.

## 4. Log

Apply has already appended every decision to `prefs/decisions.jsonl` and committed it with the decided designs' folders; its JSON says if it couldn't. Write the entry in `prefs/log.md` from the review JSON, in their words: chosen, rejected, mix, parked, why. A line you add for context starts with `Context:`. Commit it.

## 5. Refine

Start `livery-refine` on the picked design (for a mix, the one they named first) with their note verbatim. Its `stop` says what next:

- `ship` or `budget`: Final review.
- `blocked` or `kit`: tell them each `for_owner` line at once, as a proposed kit fix (`kit-calibrate`) or library change. First look for the same fault in the calibration shots in `.cache/kit-review/<kit>/`: if the calibration design shows it too, the kit or the renderer is at fault, not the design. Refine again once they have decided.
- `character` or `sim`: theirs to answer; refine again after.
- `plateau`: its `review` is the design beside the fork's variants (a pick, as at the concept pick) or the design alone (a final). Refine again with their note (and `adopt: <variant>` when they picked one), unless they accept or park.
- `error`: report it.

Delete the variant folders it lists under `remove`, and an adopted variant's once the next run has adopted it; they are never committed. Keep its `constraints` for the retro: each is a kit or library proposal.

## 6. Final review

`livery build <design>`, then `livery review <design>`. They accept (good enough to race), edit with a note, or park it, and may rate it; log it as after the concept pick. A final review needs a checked build, and accepting pins exactly that build. On edit, refine again with their note. On park, stop.

## 7. Install and export

On accept only: `livery install <design>`, which refuses any other build, and tell them to run `livery install <design> --finish` after their first session of the other kind (solo or team) so that spec's .mip is archived too. Then `livery export <design>`, and give them the folder and upload line it prints, so other drivers see it through Trading Paints.

## 8. Retro

End the session with the `retro` skill (`/retro`).
