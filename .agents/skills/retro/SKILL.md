---
name: retro
description: Fartstriber session retro - read this session's transcript and the decision, verdict and metrics records since the last retro, and propose concrete edits (taste rows, lint checks, kit keep-clear zones, skill lines, AGENTS.md rules) as review items the owner accepts or rejects. Use as /retro, and as the last step of every design, kit and library session.
---

# Retro

The retro proposes; the owner decides in `livery review`; only what they accept is made. Nothing changes without their yes.

## 1. Read what happened since the last retro

`prefs/<person>/retro.json` holds `last`, when the last retro's review was applied (no file: read all of the records). From then on:

- **This session's transcript**: wherever the harness keeps it. Claude Code writes `~/.claude/projects/<project>/<session id>.jsonl`, where `<project>` is the session's starting folder with every character other than a letter or digit replaced by `-` (`C--Users-you-Development-fartstriber`), and the id is `${CLAUDE_SESSION_ID}` (also `CLAUDE_CODE_SESSION_ID` in the environment), else the newest `.jsonl` there; subagents' are in `<session id>/subagents/`. Transcripts are large: search them for the owner's messages and for errors, don't read them whole.
- `prefs/<person>/decisions.jsonl`: the owner's decisions and notes (favourites, parks, stops, pins with their own notes, which are the owner's words too, endorsed critic suggestions, their edits and undos), with the critic's verdict on the same design hash.
- `designs/*/verdicts.jsonl`: each round's fixes, the builder's answers, `lint_missed`.
- `livery stats` (with `--json` for the rows): the loop's numbers, the critic's calls beside the owner's decisions among them.
- What the session promised ("next round I'll…", "I'll propose…") and whether it happened.

## 2. Find the lessons

- The same correction from the owner twice, or a note that says what they like or don't.
- A critic verdict far from the owner's decision (a ship they stopped or parked, an iterate they raced as a favourite).
- `lint_missed` items that recur or that a check could catch before the sim.
- A fix answered `done` that came back, a `blocked` fix, a kit fault found in a design round.
- A step a session had to work out again, a rule it broke, a promise it didn't keep.

Drop what a skill, taste row, lint or rule already says. A lesson needs evidence: the owner's words, a verdict, a record line, a transcript quote.

## 3. Propose

One proposal is one edit in one place:

- a taste row, made on accept with `livery taste put <row.json>`: its quotes name the decisions they come from (`of`, the ledger uid)
- a lint check (a library change)
- a kit keep-clear zone (a kit change, through `livery kit review`)
- a line in a skill
- a rule in AGENTS.md

Write them to `.cache/retro/<time>.json` as a JSON list of `{"title", "body"}`: the title says the change in one line; the body says where it goes, gives the exact text or a diff, and the evidence. A few strong proposals beat many weak ones. Run `livery review --proposals .cache/retro/<time>.json` in the background and give the owner the URL.

## 4. Make what the owner accepts

Apply's JSON lists `proposals`: the accepted ones, to make as written, and the edited ones, to make as their note says (ask if it doesn't say). Taste rows, skill lines and AGENTS.md rules are made now. A lint check or kit zone is a library or kit change: start it as its own work, never inside a design session, and `livery build --all` after. A rejected proposal comes back only with new evidence.

Then write `prefs/<person>/retro.json` as `{"last": "<now, ISO with its offset>", "review": "<Apply's review id>"}` and commit it with the edits.
