# Fartstriber: production plan

This is the brief until its finish line is met. Then it is deleted, and the decisions that last move into `docs/architecture.md`. It was agreed with Nickolaj on 19 Oct 2026, after an analysis of the code, docs, metrics, the 18 earlier sessions and outside research.

## Finish line

1. **A public v0 is released.** One GitHub repo that others fork, with:
   - a fresh history
   - GPL-3.0
   - the v2 design loop and the McLaren kit v2
   - a fork guide that someone else can follow
2. **One new livery shows the loop works.** It was made through the new flow with only Nickolaj's two checkpoints, he rated it 3 or 4, it was uploaded to Trading Paints and raced, and a `raced` event is logged.

## Decisions

| # | Decision |
|---|---|
| 1 | Scope: a personal tool, more cars, paints others see (through Trading Paints) and public open source. |
| 2 | One public repo, fork model. Others fork it and replace designs/, prefs/ and references.md. The owner's name comes from user config, not code, skills or docs. |
| 3 | The public repo starts from a fresh history. The full history stays in a private archive repo. Commits use the GitHub noreply address from then on. |
| 4 | Licence GPL-3.0. The Fireproof mark, wordmark and lockup (`assets/sponsors/fireproof`) are all rights reserved, in a NOTICE. Fonts keep their OFL. |
| 5 | S6 (decoding the UI's httproot .dat) and the template-derived kit files ship as they are. Nickolaj accepts the iRacing ToS risk. |
| 6 | Ondura is renamed and redrawn through sponsor review. roster.md records each name check as evidence, not a date. Colours stay. |
| 7 | Publish after the v2 batch, McLaren only. The v2 batch is built car-generic. The F4 waits for Nickolaj's replay and is added in public. |
| 8 | Claude commits and pushes its own work without asking. While the plan runs unattended (Nickolaj's goal of 19 Oct: as little human input as possible, push regularly), work packages run in agent worktrees, the orchestrating session merges them into main once tests and CI pass, and pushes. |
| 9 | One session = one worktree + branch + PR. Machine state lives in `%LOCALAPPDATA%\fartstriber`: a named-mutex sim lock, the paint journal and backups, the review queue, the user config and telemetry. Until phase 2 lands, one session at a time. |
| 10 | Two checkpoints: the concept pick (with an optional direction note) and the final. In between, an autonomous loop with a budget of 4 rounds per design. A plateau (two rounds 'same') forks 2-3 variants with fresh builders, then escalates with a side-by-side. Escalate early on a blocked fix or a change of the brief's character. The six-sim-loop cap goes. |
| 11 | Critic v2. Each round it marks earlier fixes resolved, partly or not (with a shot), compares A/B against the last round in both orders, and checks every row of a taste table. Ship = no open non-optional fix, all taste rows pass, not worse than last round, and an adversarial second critic agrees. Concepts are ranked round-robin pairwise. The 1-10 is logged only and the model is pinned. This revokes PoC decision 16 (no distilled rules). |
| 12 | `livery references fetch` caches the yes rows' showroom images in `.cache/references/`, never committed. Each brief names 2-3 rows from the favourites, and those images go to builders and critic. fireproof-sweep's and fireproof-blocks' sim shots are negative anchors. references.md rows 1-20 are deleted. |
| 13 | Review gains `park` (stop, not approved, not installable) and an optional 1-4 rating (done with it, fine, would race, love it). Accept means 'good enough to race'. |
| 14 | Concepts: 6-8 sketches with no lint polishing. Each names its source (a favourite to transplant, a real object, a story) and writes down the 'expected' version it must not be. They are ranked pairwise on flat views plus car.png. The top 3 go to check and the sim, and only those 3 reach Nickolaj. |
| 15 | A `livery-builder` agent definition, a `livery-critic` with its model pinned, and saved workflows `livery-concepts` and `livery-refine`, each stopping at `livery review`. livery-iterate launches them. The main session passes on only results and requests for Nickolaj. |
| 16 | Delete eva-unit00, fireproof-blocks, fireproof-sweep and s2-pattern. fireproof-stripes retires once fireproof-v2 is approved. calibration becomes a library function per kit, primitives-demo moves to tests/fixtures, and sponsor-roster is a fixture. designs/ holds only liveries. |
| 17 | near-tangent: fix the semantics (ignore seam-paired continuous outlines, measure along the boundary) and add snapping (primitives snap within a few px onto or clear of real edges and report what moved), both in the v2 batch. |
| 18 | Everything that moves pixels lands in one v2 batch on one branch, followed by one `build --all`, one kit review and one re-approval of the finals. |
| 19 | S5b bake at the five preview views after the v2 batch. It must pass `bake check` against a real preview sim run first. Builders use it on every edit, and the critic may judge it in the autonomous middle rounds. The final round and every review use real sim shots. |
| 20 | Committed outcomes: verdicts.jsonl (the only verdict store, with design hash, shots and round), approved.json, decisions.jsonl written by Apply (notes verbatim, rating; seeded from `.cache/review`), and prefs/log.md (his words only, with context lines marked). Telemetry from a wrapper that logs every livery command goes to the state dir, read by `livery stats`. |
| 21 | Finishes: `livery kit spec` shoots a grey ladder per preset at the preview views. Metallic compensation becomes a measured per-material lookup, the lint warns above the reachable brightness, and satin is re-tuned until it reads differently from gloss. The skill teaches finish contrast as a layer. |
| 22 | Image generation stays a non-goal. Revisit it if two sessions of sketch concepts still come out 'expected'. |
| 23 | A retro runs at the end of every design, kit and library session, and on demand as `/retro`. It proposes edits (taste rows, lints, kit zones, skill lines, CLAUDE.md rules) as review items. |
| 24 | Trading Paints Downloader runs while Nickolaj races. Phase 2 checks what loads. `livery install` warns while it runs, and `livery export <design>` writes the files for a manual upload. |
| 25 | Session transcripts keep Claude Code's default 30-day expiry. |
| 26 | Kit v2 measures at the five preview views the track camera set already holds (side views square-on, tilted top, the two three-quarters). One calibration capture per car (FULL Gray-code geometry plus the grey ladder per material) feeds kit fit, the bake and the finishes. The car-set measurement cameras, `kit cameras` and the replay reopens they need go. |

Defaults chosen without a question:
- pyrefly as the type checker (basedpyright until 20 Oct: pyrefly checks the project in about 2 s instead of 19 s)
- ruff ignores E501, since `ruff format` owns line length
- a sim run that finds the sim window in front waits instead of driving it
- S4's spike script is deleted at once, because a skill calls it and it writes the team paint with no lock or backup
- the other spike scripts are deleted as each is integrated
- docs/spikes.md becomes docs/sim.md, a topic reference of measured sim facts

## How work runs

- Each work package runs as an agent in its own worktree and branch; the orchestrating session merges it into main when tests pass, and pushes (decision 8).
- Packages that touch different files run side by side; sim work runs one at a time. Pixel-moving work collects on one branch until the re-approval pass.
- Each package keeps `uv run pytest` and `uv run ruff check .` green and updates the docs it changes in the same PR.

## Phase 0: Land (this session)

- [x] Freeze: no other session in this checkout until the push.
- [x] Commit `.gitattributes` on its own. Set `core.autocrlf=false` for the repo, and convert the CRLF working files to LF.
- [x] Land the tree in logical commits: LF writes and metrics isolation; approvals, `build --all` and critic history; the Fireproof redraw; S5 and S5b; the run metrics. The design session committed its own folders (9510e1f).
- [x] Delete eva-unit00, fireproof-blocks, fireproof-sweep, s2-pattern and, now that fireproof-v2 is approved, fireproof-stripes. Fix eva-unit01's unused variable, so ruff is green.
- [x] Seed `prefs/decisions.jsonl` from the 24 files in `.cache/review` (82 decisions).
- [x] Put this plan in place of docs/poc-plan.md.
- [x] Create the private repo nickolaj-jepsen/fartstriber, rename master to main, push, and add Windows CI (ruff, format, pytest).

## Phase 1: Green baseline and guardrails (no pixel changes) — done

- **1a Hygiene**
  - ruff: `line-ending = "lf"`, RUF and PT, ignore E501, drop the dead noqa markers
  - one `LiveryError` hierarchy caught once in `main()`, with the source lint run before hashing
  - fix the child build reading its result from stdout, `parse_range`, the `preview_sim` shim, `_not_built` and the help texts
  - move primitives-demo to tests/fixtures, mark calibration and sponsor-roster as fixtures, and have `build --all` fail only on liveries and fixtures
  - delete the spike scripts already integrated (S1-S4, S6-S8, common, capture)
  - docs/spikes.md becomes docs/sim.md
  - Nickolaj, by hand: delete `.cache/spikes` (8 GB of spike captures) and the loose files at the `.cache` root; keep sim shots, critic packs and the S5b runs until the bake is in the library
  - the S5 scripts' helpers (common, capture, s2-s4) stay until S5's decode moves into the library
- **1b Guardrails** (parallel with 1a)
  - `.claude/settings.json`: `UV_NO_SYNC=1`; deny edits to kits/, .cache/ and the iRacing folders; ask before `git add -A`, `commit -a`, `stash` and `reset`; allow `uv run livery|pytest|ruff`
  - hooks: format and fix on edit, a SessionStart status, and protection for approved.json and prefs/log.md
  - a prek pre-commit hook
  - basedpyright on livery/ and tests/ in CI
  - a README

## Phase 2: Sim safety and isolation — done except 2c (Trading Paints Downloader wasn't running; the check waits for a session with it on)

- **2a SimSession.** Every sim run goes through it: preview, kit measure, kit review, install, restore and finish. It provides:
  - a named mutex
  - an on-disk paint journal, replayed after a crash
  - checks for the replay's identity, the car and the camera groups
  - foreground checks before every broadcast
  - snapshot and restore of camera, UI and replay state
  - one persistent capture session
  - a tolerant reload
  - a reload after putting the paint back
  - a run manifest with build digests
  - a FakeIR test suite for the CLAUDE.md sim rules
- **2b Machine state.** Move it to `%LOCALAPPDATA%\fartstriber`. Add `livery sim status` (connected, replay, groups, lock holder, TP Downloader), sim jobs that wait for readiness and say once what they need, and a shot check that refuses shots with the replay UI over the car.
- **2c Trading Paints check.** One test: install a paint, join a session with TP Downloader running, and record what loads in docs/sim.md.
- **2d Worktrees.** The worktree and commit rules go into CLAUDE.md, and parallel sessions start.

## Phase 3: Trust chain, config, records (no pixel changes; 3a and 3b in parallel) — done

- **3a Config and hashes.**
  - A committed policy file (lint, crops, shot and measure presets, track), hashed into check.json.
  - A user config in the state dir (IDs, paths, iRacing dirs, replay with its sha256, owner name), plus a test fixture config.
  - Toolchain hash = the modules a build imports. Design hash = the files the build reads, including design-local sponsors.
  - Kit approval enforced by final review and install.
- **3b Records and freshness.**
  - Previews stamped with their build (`build.json` in flat/, each sim run and each sheet). Review, pin and critic pack accept only matching stamps.
  - `livery status [design]`, and a consistent `--json`.
  - The decision 20 split: the verdicts.jsonl backfill, decisions.jsonl from Apply, the telemetry wrapper and `livery stats`. `critic record` stops writing notes.md.
  - brief.md and notes.md hold the current state only.

## Phase 4: Agent flow v2 (4b, then 4a; 4c in parallel) — done

- **4b Critic v2** (decision 11) — done:
  - fix resolution, pairwise A/B and Bradley-Terry concept ranking
  - the taste table, seeded from prefs/log.md and lint_missed
  - an adversarial ship check
  - `references fetch` and anchors in the pack
  - the rubric and references copied into the pack, distance images upscaled from 200 px, `omitClaudeMd`, model pinned
- **4a Builder and workflows** (decisions 10, 14 and 15) — done; the first real runs of livery-refine (on eva-unit01 and fireproof-v2, 20 Oct) found and fixed a builder-hook bug in worktrees and a stop rule that stopped on non-blocking reports:
  - `livery-builder` (Edit/Write only, scope hooks, a stop-time git check, "blocked" required with evidence)
  - `livery-concepts` and `livery-refine` workflows
  - the livery-iterate rewrite (its trigger includes "v2" and "another round")
- **4c Review and learning** (decisions 13 and 23) — done: park and rating, the `/retro` skill, a sponsor-draw skill with the AI-vibe lesson, and `livery export`.

## Phase 5: The v2 batch (one branch; everything that moves pixels) — merged into main on 20 Oct; the kit review and the re-approval are the owner's

1. **Extract the finalists' private helpers into the library, byte-identical.** No re-approval is needed, so this may merge to main early. Done: `livery.geom`; the dissolves, strokes, type and kit workarounds stay in the designs until steps 4-6.
2. **Car-generic kits** — done (per-kit profiles, a kit-defined view list, maps.json and approved.json, a kit version and fingerprints in phase 3; poses sized to the car, `calibration(kit)`, `livery sim pick-frame` and `livery kit add`, which stops at the fit until `kit fit` reads the calibration capture, step 3):
   - per-kit sim profiles
   - a kit-defined list of views
   - poses sized to the car
   - `calibration(kit)`
   - `livery kit add <psd>`
   - maps.json and approved.json split out of kit.yaml
   - a kit version, plus a fingerprint of the template and the car .dat
3. **Measurement at the preview views** (decision 26) — done (the McLaren refitted from its capture, kit version 2, awaiting the kit review):
   - shared `livery/graycode.py` from S5b (sub-texel, 11 bits in 8 pairs, plane fit)
   - the lock taken per paint, with resume
   - a kit_fit test harness and `kit fit --compare`, then simplify fit()
   - the car-set camera path and the S5 and S5b scripts deleted
4. **A kit body model:** named parts, features, grouped front panels, and sim overlays (the door light panel and others) as strict keep-clear zones. Done (`livery kit label`, kits/<kit>/body.yaml; 14 unsure names and the open questions go to the kit review).
5. **near-tangent:** the semantics fix, `clear_of_edges()` and snapping. Done (`livery/lint/edges.py`; snaps reported in lint.json and check.json).
6. **New primitives** (done: `geom.outline`, `livery.typeset`, `livery.patterns`, `assets/marks` awaiting the owner's review; the final review lists waivers):
   - `outline`
   - `type_block` (glyph geometry)
   - `livery.patterns` (hazard, dissolve, dash-run band ends, stripe bundles, chevrons)
   - an approved race markings set with kit anchors
   - remove `logo()`, `raster_fill` and the unread brand flags, and make `waive()` take a reason
7. **Finishes** (decision 21) — done: `livery kit spec` writes per-preset lookups from the capture's ladders, the lint warns above a finish's reach, and satin is clearcoat 64 (with a full clearcoat it read as gloss).
8. **Rewrite eva-unit01, eva-nerv and fireproof-v2** on v2. Then `build --all`, the kit review, and re-approval of the finals. Done up to the owner's part: all three pass check on kit v2 (critic v2: eva-unit01 8 and fireproof-v2 8 on baked renders, eva-nerv 7 with its nose waiting on the owner's decision). Left: a sim-shot ship round each when the sim is free, then the kit review and the re-approval.

## Phase 6: Publish — done

- [x] LICENSE (GPL-3.0) and a NOTICE for the Fireproof brand.
- [x] The owner name from user config everywhere 'Nickolaj' is written today, in code, skills and docs (`livery config show`).
- [x] Ondura renamed Huilec (awaiting the owner's sponsor review), and name-check evidence in roster.md. Sponsor approval now gates the final review and install, not placement.
- [x] A README and fork guide: setup, your own replay with `livery sim pick-frame`, kit add, and what to replace in a fork.
- [x] A final docs pass, then a new public repo nickolaj-jepsen/fartstriber with the cleaned tree as its root commit. The private repo becomes fartstriber-archive. Checked on 20 Oct: no secrets, iRacing IDs or local paths in the tracked files. Held until no other session works on the current history (the switch replaces main's history), then:
  1. Merge or close every other branch and worktree; `git status` clean on main, CI green.
  2. `gh repo rename fartstriber-archive` (the private repo keeps the full history); `git remote rename origin archive`.
  3. `git checkout --orphan public && git commit -m "Fartstriber v0"` with the GitHub noreply address as the author email (`git config user.email <id>+<login>@users.noreply.github.com`, the id from `gh api user --jq .id`).
  4. `gh repo create nickolaj-jepsen/fartstriber --public --source . --remote origin --push` from that branch, then `git branch -m main archive-main` and `git branch -m public main`, tracking origin/main.
  5. Check that CI passes on the public repo and that the README and NOTICE render.

## Phase 7: Sim-lit preview and a 3D-aware lint — done (bake check passed: worst p95 8 at front34; the 3D lint flags 12 of 18 testable critic lint_missed items)

- The bake (decision 19), with staleness tracking and about 190 MB per car in `.cache`.
- Visibility and foreshortening per texel from the bake, feeding lint warnings for:
  - detail on down-facing surfaces
  - strokes too thin to see
  - text over creases
  - marks hidden under the side blade

## Phase 8: More cars

- When Nickolaj's F4 replay exists: run the two no-out-lap spikes (painting another driver's car in a replay; drafting a kit from the UI bake's UV map), then add the F4 through `livery kit add`. This is where the car-generic view model gets proven.

## Not doing

- SVG-generation models
- rebuilding Trading Paints' Paint Builder
- sim speed work (an 18 s loop isn't the bottleneck)
- packaging or releases
- mesh or Blender pipelines
- vectorising image-model output
