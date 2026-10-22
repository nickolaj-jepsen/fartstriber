# Fartstriber

Claude Code designs iRacing liveries as Python code. You describe a livery. Agents write it as a script, check it, shoot it in the sim and critique it, and stop at a local review page whenever the next decision is yours.

| Step | What happens |
|---|---|
| Design | `designs/<name>/design.py` draws in the car's own coordinates |
| Check | lints against the car's kit, and a second build in a fresh process proves the output repeats |
| Build | renders the paint and spec textures |
| Preview | flat views, sim-lit renders without the sim, and shots in the sim from fixed cameras |
| Judge | a separate critic agent compares each round with the last |
| Decide | you accept, edit or park it on the review page; `livery install` paints only what you accepted |

## Requirements

- Windows: the screen capture and the iRacing SDK only exist there.
- iRacing, with the car and a replay of it. A sim preview needs the sim open on that replay, windowed and not minimised; it never takes focus or sends input. With the optional `[sim]` settings, agents start the sim themselves while you're away and close it when they're done.
- [uv](https://docs.astral.sh/uv/) (it installs the Python in `.python-version`), [Claude Code](https://code.claude.com/) and [prek](https://prek.j178.dev/). For agent launches, Chrome or Edge, to sign in to iRacing once.

## Setup

1. Run `uv sync` in the repo.
2. Copy `livery.example.toml` to `%LOCALAPPDATA%\fartstriber\config.toml`, and fill in your IDs, iRacing folders and preview replay.
3. In iRacing, open the replay, open the camera tool (Ctrl+F12), and save the track's camera set once.
4. Run `uv run livery preview cameras <kit> --arm`, then reopen the replay so the sim loads the cameras. On a replay other than the one the kit was framed on, also run `uv run livery sim pick-frame <kit>`.
5. Run `uv tool install prek`, then `prek install`.
6. Optional, so agents start and close the sim: add the `[sim]` table from `livery.example.toml`, run `uv run livery sim login` and sign in to iRacing in the window it opens, then check `uv run livery sim status`.
7. Start Claude Code in the repo and accept the folder trust prompt, which turns on the project's permission rules.

## Use

- Ask Claude Code for a new livery, or a change to one. The `livery-iterate` skill runs the session.
- To add a car, use the `kit-calibrate` skill.
- `uv run livery --help` explains every command; CLAUDE.md lists the everyday ones.

| Doc | For |
|---|---|
| [docs/architecture.md](docs/architecture.md) | how the system works and why |
| [docs/sim.md](docs/sim.md) | how iRacing behaves, as measured |
| [CLAUDE.md](CLAUDE.md) | the agent's commands and rules |

## Making it yours

The repo is one person's studio as well as the tool. Fork it, then replace what is the owner's:

| What | Do |
|---|---|
| Taste | Empty `prefs/log.md` and `prefs/decisions.jsonl` (and `prefs/retro.json` once a retro has run). Replace the yes rows of `references.md`; `livery references fetch` caches their pictures. Start `.claude/skills/livery-design/taste.md` again, or let the retro propose rows |
| Liveries | Delete `designs/` except the fixtures `calibration` and `sponsor-roster` |
| Brand | Remove `assets/sponsors/fireproof/` and its row in `assets/sponsors/roster.md` (it isn't licensed to you, see NOTICE). Draw your own with the `sponsor-draw` skill. The other sponsors are invented and yours to use |
| Settings | Your own `config.toml` (setup step 2), with your name as the owner |
| Cars | The McLaren kit works once its frame is picked on your replay. For another car, `livery kit add` runs from its paint template to your kit review |

## Licence

GPL-3.0 only, see LICENSE. The Fireproof brand is all rights reserved and the fonts keep their OFL; NOTICE has the details.
