# Fartstriber

An agent designs iRacing liveries as Python code, renders and checks them, and looks at them in the sim. How the system works and why: `docs/architecture.md`. How iRacing behaves, as measured: `docs/sim.md`.

## Harnesses

This file is the project's instructions, and every agent reads it: Claude Code reads `AGENTS.md` directly, as does any other harness that reads it. The skills are markdown in the Agent Skills format, in `.agents/skills/`. A piece below that needs a harness's own driver says what to do without one.

| Piece | Where | On a harness that doesn't load it |
|---|---|---|
| Skills | `.agents/skills/<name>/SKILL.md`, one per task, with its own files beside it | read the one whose description matches the task, with its files beside it |
| Roles | `.claude/agents/<name>.md`: the prompt a fresh subagent gets, with its tools | start a fresh subagent with that file's body as the prompt, and only the tools it names |
| Workflows | `.claude/workflows/<name>.js`: the steps, the agents they start and their arguments | Claude Code starts one with the Workflow tool and its `scriptPath`; elsewhere run the steps the file lists, in order, and keep its stop before `livery review` |
| Hooks | `.claude/hooks/`, wired in `.claude/settings.json` | they refuse only what the rules below already refuse: hold to those yourself |

## Commands

Every deterministic step is a `uv run livery` command; `--help` explains each one and every command takes `--json`.

| When | Run |
|---|---|
| Inner loop on a design | `livery lint`, `livery preview flat` and `livery sheet --wedge`, trying values with `--set`; then `livery check` and `livery build` |
| After any kit or library change | `livery build --all` in the background. Approvals it carries are in the ledger: commit them with the change. Each design it asks "still OK?" waits on the review page; tell the owner which |
| Sim loop | `livery preview sim <design>` in the background (it can wait minutes for the sim lock, or, under the owner's `[sim]` settings, for the sim to start, and says once what it waits for); `livery sim status` says what a sim run would find and what a launch needs |
| Decisions | `livery review` in the background. It blocks until the owner sends that screen; Send appends to the owner's `prefs/<person>/decisions.jsonl` and commits what it wrote. Every session's reviews share one page: if the owner answers in chat instead, `--cancel` takes this checkout's off it. A reference the owner supplies in chat is a yes: write it into `references.md` yourself. `livery library` says where everything stands, revived cars included |
| Critic | `livery critic pack`, a fresh `livery-critic` subagent on each folder it prints, then the matching `livery critic` step to record the answer; `livery critic status <design>` says whether it goes back to the owner, whether it ships, and what is missing |
| Final | `livery install <design>` installs only the build the owner approved (`--restore` undoes it); `livery export <design>` writes it under Trading Paints' upload names |
| Where things stand | `livery status [design]`, instead of reading `.cache`; `livery stats` for the loop's numbers |
| Tests | `uv run pytest`, spread over the CPU's cores; `-n0` runs one test in this process (a debugger, `-s`) |
| Lint, format, types | `uv run ruff check . && uv run ruff format --check . && uv run pyrefly check`, as the pre-commit hook runs them; fixing a baselined type error means `pyrefly check --prune-baseline` in the same commit |

## Files

- `.cache/designs/<name>/`: a design's build and preview output. Nothing under `.cache/` is committed.
- `policy.toml` `[lint]` is part of every check's hash: changing a threshold re-checks every design.
- Personal settings are `config.toml` in the state dir, `%LOCALAPPDATA%\fartstriber`, not in the checkout: `livery config show` prints them.
- Where everything else lives: `docs/architecture.md` (Layout, Settings).

## Rules

- Design sessions edit only `designs/<name>/`. Never edit `livery/`, a kit, another design, or anything in `.cache/` during a design session. A missing primitive is drawn with `svg(raw)` or proposed to the owner as a library change, which re-checks every design.
- Never edit a kit by hand; go through `livery kit review`.
- Patch files with the edit tool or a `.py` script in the scratchpad, never with inline heredocs in a shell.
- Each task session works in its own git worktree and branch; run `uv sync` there first (sessions run with `UV_NO_SYNC=1`), and again after changing pyproject.toml or uv.lock. Commit and push your own work as you go, on that branch; it merges into main once tests and CI pass. A failing pre-commit hook means fix and commit again, never `--no-verify`.
- The sim lock and the paint journal are machine-wide, in `%LOCALAPPDATA%\fartstriber`, so parallel sessions queue for the one sim.
- The sim loop must never get in the owner's way: no keystrokes or mouse input, never focus, raise or restore the iRacing window, reload through the SDK, and stop the moment they bring the sim to the front. A run may resize a normal sim window (not minimised, maximised or fullscreen) behind other windows to `policy.toml` `[preview] window`, and always puts its size back. If a capture comes back blank, stop and report. Ask them to keep the cursor off the sim window during a run: the sim draws its replay UI over the car there.
- Agents start the sim only through a run, when the owner's launch rule allows it, and close only a sim an agent started. `livery sim up`, `down`, `hold` and `allow` are the owner's; never run them, and never `livery sim login`.
- Docs describe the current system in as few words as it takes, structured as sections, tables, bullets and diagrams rather than long prose. No temporary docs (plans, work in progress), no history, no lists of what the code contains. Each fact lives in one place and the others point to it.
- Tests assert behaviour, not copies of values: no tests that pin kit numbers, registries of primitives, or message text.
