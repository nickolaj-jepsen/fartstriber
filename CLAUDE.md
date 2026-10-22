# Fartstriber

Claude Code designs iRacing liveries as Python code, renders and checks them, and looks at them in the sim. How the system works and why: `docs/architecture.md`. How iRacing behaves, as measured: `docs/sim.md`.

## Commands

Every deterministic step is a `uv run livery` command; `--help` explains each one and every command takes `--json`.

| When | Run |
|---|---|
| Inner loop on a design | `livery lint <design>`, `livery preview flat <design> [--set k=v]`, `livery sheet <design> --wedge k=a..b..step`; then `livery check` and `livery build` |
| After any kit or library change | `livery build --all` in the background; tell the owner about every approved design it reports as changed |
| Sim loop | `livery preview sim <design>` in the background (it can wait minutes for the sim lock, or, under the owner's `[sim]` settings, for the sim to start, and says once what it waits for); `livery sim status` says what a sim run would find and what a launch needs |
| Decisions | `livery review <designs>`, or `--kit <kit>`, `--roster`, `--sponsors`, `--marks`, `--references`, `--proposals <file>`, in the background. It blocks until the owner applies; Apply appends to `prefs/decisions.jsonl` and commits what it wrote. Every session's reviews share one page: `--list` shows them, and `--cancel` takes this checkout's off it |
| Critic | `livery critic pack <design> [--compare or --adversary]`, a fresh `livery-critic` subagent on each folder, then `livery critic record`, `compare` or `adversary`; `livery critic status <design>` says whether it ships. Concepts: `livery critic pack --pairs <designs>`, then `livery critic rank <designs> --pairs <dir>`. `livery references fetch` caches the favourites' pictures |
| Final | `livery install <design>` installs only the build the owner approved (`--restore` undoes it); `livery export <design>` writes it under Trading Paints' upload names |
| Where things stand | `livery status [design]`: the check, the approval, whether previews show the checked build, the critic's last round. Read it instead of `.cache`. `livery stats`: the loop's numbers |
| Tests | `uv run pytest`, spread over the CPU's cores; `-n0` runs one test in this process (a debugger, `-s`) |
| Lint, format, types | `uv run ruff check . && uv run ruff format --check . && uv run pyrefly check`, as the pre-commit hook runs them |

## Files

- `.cache/designs/<name>/`: a design's build and preview output. Nothing under `.cache/` is committed.
- `policy.toml`: project policy (lint thresholds, the views a preview shoots), part of every check's hash.
- `config.toml` in the state dir, `%LOCALAPPDATA%\fartstriber`: personal settings (the owner's name, IDs, iRacing folders, the preview replay, when agents start the sim). `livery.example.toml` lists the keys; `livery config show` prints them.
- `kits/<kit>/profile.toml`: how the sim shows a car.

## Rules

- Design sessions edit only `designs/<name>/`. Never edit `livery/`, a kit, another design, or anything in `.cache/` during a design session. A missing primitive is drawn with `svg(raw)` or proposed to the owner as a library change, which re-checks every design.
- Never edit a kit by hand; go through `livery kit review`.
- Never commit build output.
- Patch files with the Edit tool or a `.py` script in the scratchpad, never with inline heredocs in Bash.
- Each task session works in its own git worktree and branch; run `uv sync` there first (sessions run with `UV_NO_SYNC=1`), and again after changing pyproject.toml or uv.lock. Commit and push your own work as you go, on that branch; it merges into main once tests and CI pass. A failing pre-commit hook means fix and commit again, never `--no-verify`.
- The sim lock and the paint journal are machine-wide, in `%LOCALAPPDATA%\fartstriber`, so parallel sessions queue for the one sim.
- The sim loop must never get in the owner's way: no keystrokes or mouse input, never focus, raise or restore the iRacing window, reload through the SDK, and stop the moment they bring the sim to the front. If a capture comes back blank, stop and report. Ask them to keep the cursor off the sim window during a run: the sim draws its replay UI over the car there.
- Agents start the sim only through a run, when the owner's launch rule allows it, and close only a sim an agent started. `livery sim up`, `down`, `hold` and `allow` are the owner's; never run them, and never `livery sim login`.
- Docs describe the current system in as few words as it takes, structured as sections, tables, bullets and diagrams rather than long prose. No temporary docs (plans, work in progress), no history, no lists of what the code contains. Each fact lives in one place and the others point to it.
- Tests assert behaviour, not copies of values: no tests that pin kit numbers, registries of primitives, or message text.

## Skills

| Skill | For |
|---|---|
| `livery-design` | design principles, rubric, brand rules |
| `livery-iterate` | the session procedure |
| `kit-calibrate` | adding a car |
| `sponsor-draw` | drawing a brand |
| `retro` | the last step of every design, kit and library session, and `/retro` on demand |
