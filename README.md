# Fartstriber

Claude Code designs iRacing liveries as Python code. A design is a script that draws in the car's own
coordinates. The `livery` CLI builds it deterministically into the paint and spec textures, lints it against
the car's kit, renders flat previews and shoots the car in the sim from fixed cameras. A separate critic agent
judges the sim shots, and `livery install` puts on the car only a build you accepted in a local review page.

## Requirements

- Windows: the screen capture and the iRacing SDK only exist there.
- iRacing, with the car and a replay of it to preview on. A sim preview needs the sim open on that replay,
  windowed and not minimised; it never takes focus or sends input.
- [uv](https://docs.astral.sh/uv/), which also installs the Python in `.python-version`.
- [Claude Code](https://code.claude.com/), and [prek](https://prek.j178.dev/) for the git pre-commit hook.

## First run on a new machine

1. Clone the repo and run `uv sync` in it. The Claude Code hooks in `.claude/` run with the `.venv` it makes.
2. Copy `livery.example.toml` to `%LOCALAPPDATA%\fartstriber\config.toml` and put in your own values: your
   IDs, the iRacing folders and the preview replay. Every worktree reads that one file. The replay frame and
   cameras in each kit's `profile.toml` belong to the replay they were picked on; another replay needs its own.
3. Open the replay in iRacing, open the camera tool (Ctrl+F12) and save the track's camera set once.
4. Run `uv run livery preview cameras <kit> --arm` to add the preview cameras to that set (it waits for the
   sim to close if camera.ini has to change), then open the replay again so the sim loads them. A kit framed
   on another replay than yours needs its frame picked on yours: `uv run livery sim pick-frame <kit>`.
5. Install the pre-commit hook: `uv tool install prek`, then `prek install` in the repo.
6. Start Claude Code in the repo and accept the folder trust prompt, which turns on the project's permission
   rules.

## Everyday use

Ask Claude Code for a new livery or a change to one. The `livery-iterate` skill runs the session and stops at
`livery review` whenever the next decision is yours. CLAUDE.md lists the commands, `uv run livery --help`
explains each one, and docs/architecture.md says how the system works and why. Adding a car is the
`kit-calibrate` skill.

## Making it yours

This repo is one person's studio as well as the tool: fork it and replace what is the owner's.

- **Your taste:** empty `prefs/log.md`, `prefs/decisions.jsonl` and, once a retro has run, `prefs/retro.json`;
  replace the yes rows of
  `references.md` (keep the table, put in liveries you like; `livery references fetch` caches their
  pictures). The critic and the builders read these every session, so they learn your taste from your
  reviews from then on. `.claude/skills/livery-design/taste.md` is distilled from the owner's notes: start it
  again from yours, or let the retro propose rows.
- **Your liveries:** delete the folders in `designs/` except the fixtures (`calibration`, `sponsor-roster`).
- **Your brand:** the Fireproof brand is not licensed to you (NOTICE). Remove `assets/sponsors/fireproof/`
  and its row in `assets/sponsors/roster.md`, and draw your own with the `sponsor-draw` skill; the other
  sponsors are invented and yours to use.
- **Your settings:** your own `config.toml` in the state dir (step 2 above), with your name as the owner.
- **Your cars:** the McLaren kit works for anyone who owns the car, once its frame is picked on your replay.
  For another car, `livery kit add` (the `kit-calibrate` skill) runs from its paint template to your kit
  review.

## Licence

GPL-3.0 only, see LICENSE. The Fireproof brand in `assets/sponsors/fireproof` is all rights reserved and the
fonts keep their OFL; NOTICE has the details.
