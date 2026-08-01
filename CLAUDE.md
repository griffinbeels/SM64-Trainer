# SM64 Trainer — Claude Development Guide

This project is developed **exclusively by Claude**; the human runs the
emulator and verifies live behavior. Future sessions have no memory of past
ones — this file, `.claude/rules/*.md`, and `docs/architecture.md` ARE the
memory. Keep them lean and current: stale documentation is a broken build.

## What this is

A Python server that reads Super Mario 64 (**Usamune v1.93u** practice ROM)
memory out of **Project64 1.6** (Windows) via `ReadProcessMemory`, detects
game events — star grabs with exact Usamune timing, game resets — and
broadcasts JSON over WebSocket. PJ64 1.6 has no scripting API; external
memory polling is the only path, and every address was located and
live-verified empirically. Stack: Python 3.12+ via **uv** (never pip),
FastAPI + uvicorn, pymem, pytest.

## Commands

```
uv sync
uv run pytest -q                                     # MUST pass before any merge
uv run python -m sm64_events.main                    # run from repo root (data/ is cwd-relative); canonical — binds the CTRL+C shutdown deadline
uv run python tools/verify_addresses.py              # live gate (needs PJ64 + ROM)
uv run python tools/verify_death_clock.py            # live gate, ANSWERED 2026-08-01 (raw counter); re-run only to re-check
uv run python tools/verify_star_stop.py              # live gate, ANSWERED 2026-08-01: the screen is Usamune's RESULT store once it SETTLES (10/10)
uv run python tools/derive_xcam.py                   # live gate, ANSWERED + now the REGRESSION gate: scores what we journal against Usamune (just play; MIDAIR grabs are the ones that measure)
uv run python tools/dev_cleanup.py                   # kill orphaned dev/harness servers (auto-runs at session start)
uv run python tools/dedupe_journal.py data/tracker.db  # scan double-journaled events; --fix repairs (server stopped)
uv run pytest tests/test_responsive.py -q            # render every breakpoint; report layout defects (no PJ64 needed)
uv run python tools/contact_sheet.py .objective-card # one surface at 1500/1200/900/850, in one image -- LOOK at it
uv run python tools/mark_sheet.py                    # every candidate CAVEAT treatment on both surfaces, side by side -- a design PICK is owed on this
uv run python tools/measure_objective_card.py        # re-measure the fixed card heights against real content
```

**Supported window size: 850px wide minimum, any height** (2026-07-29). One
number in two places, compared by `tests/test_min_supported_width.py`:
`desktop/window.py::MIN_WINDOW_WIDTH` enforces it in the shipped app (min_size,
the default geometry, AND a clamp on restored geometry — the first constrains
dragging only, and the other two are how an existing install would keep
reopening at 480px), and `tools/uilab_project.py::min_viewport_width` is where
the sweep stops measuring. Narrower is not a bug worth filing.

**Server port:** `core/paths.py::server_port()` is the single source — `SM64_PORT`
env override, else **8064 frozen (the exe), 8065 from source (dev)** so a dev
server and a built exe never collide.

## Module map — detailed knowledge is path-scoped

The per-module "where to change what" tables live in `.claude/rules/` and load
automatically when you touch matching files. Zones:

| Zone | Dirs | Rule file |
|---|---|---|
| Memory reads + detectors + recipes (new event, dust trick, memory hunting) | `memory/`, `detectors/`, `core/snapshot.py`, `core/events.py` | `.claude/rules/memory-detectors.md` |
| Tracking, storage, stats, routes/runs/segments, defaults corpus | `tracking/`, `storage/`, `stats/`, `data/`, `tools/corpus_*` | `.claude/rules/tracking-storage.md` |
| Server, REST/WS APIs, wiring, paths, perf probes | `server/`, `main.py`, `core/paths.py`, `core/procmem.py`, `core/perfmon.py` | `.claude/rules/server.md` |
| UI shell, shared primitives, **verification norms** (loads for all of `ui/`) | `ui/` | `.claude/rules/ui-core.md` |
| Practice, stage banner, pickers, segments, routes, runs, strategies, graphs | `ui/components/practice*`, `stagebanner.js`, `entity*`, `segments.js`, `routes.js`, `runview.js`, `strat*`, `links.py` | `.claude/rules/ui-practice.md` |
| Rank icons + caps, banners, Rank tab, MARELO pill | `ui/components/caps.js`, `rankicon.js`, `hat.js`, `ranks.js`, `rankpage.js`, `marelo.js`, `standards.js` | `.claude/rules/ui-ranks.md` |
| Celebrations, the level-up climb, the tuning inspector | `ui/celebrations.js`, `rankclimb.js`, `climb*.js`, `tune*`, `components/celebrate.js`, `server/tuning_api.py` | `.claude/rules/ui-climb.md` |
| Replay capture/encode/extract, compare, compilation + **their UI** | `replay/`, `compare/`, `core/recorder_lock.py`, `ui/components/replay.js`, `compare.js`, `videosync.js`, `failcomp.js` | `.claude/rules/replay-compare.md` |
| Desktop shell, self-update, build, release | `desktop/`, `bootstrap/`, `core/update*`, `tools/build_exe.py`, `tools/release.py` | `.claude/rules/desktop-update-release.md` |
| Ranks (classify, standards, scraper) | `ranks/`, `tools/scrape_ranks.py` | `.claude/rules/ranks.md` |

(All paths under `src/sm64_events/` unless noted.) Tests mirror modules:
`tests/test_<module>.py` — read the test file first; it's the executable spec.

## Parallel work zones

Safe to work concurrently (one branch/worktree each): **detectors/**,
**server/**, **ui/**, **memory/ + tools/**, **storage/ + stats/ + tracking/**,
**replay/**, **docs/** — each with its tests. The `storage/+stats/+tracking/`
zone shares the `Attempt` contract internally; keep it in one branch.
**Shared contracts — never edit in two branches at once:** `core/events.py`,
`core/snapshot.py`, `memory/addresses.py`, `tracking/projection.py`, `main.py`.
Contract changes land on main first, then dependent work fans out. Merge with
`--no-ff`; run the full suite on the merged result; delete the branch.

## Domain rules

1. New memory address → `addresses.py` only, with source comment, marked
   `VERIFY` until it passes the live gate with the human.
2. Star grabs MUST fire on re-collection: action-EDGE detection, never
   save-flag diffing.
3. IGT comes from the Usamune expansion-RAM globals via `detectors/igt_clock.py`
   (result → counter → reconstructed). Never the vanilla HUD timer, never
   object-pool addresses (slot-dependent), never a global_timer frame delta.
4. Detectors get consecutive (prev, curr) pairs, may keep bounded internal
   state, must self-heal when global_timer jumps backward.
5. Calibration constants (DISPLAY_TICK etc.) encode live-measured behavior —
   don't "simplify" them; their evidence is in the docstrings.
6. Read-only: never write to emulator memory.
7. Timestamps UTC; the primary clock is game frames (30 fps).
8. Keep the poller's implausible-read refusal — it has caught bugs in our
   own registry.
9. One server instance per db (`storage/instance_lock.py`); second instances
   run broadcast-only. One RECORDER machine-wide (`core/recorder_lock.py`).
10. **Browser ↔ GUI parity.** Every user-facing feature lands in `ui/` +
    server, so it appears in BOTH the browser tab and the desktop window.
    `desktop/` adds ONLY native chrome and never forks the UI.
11. **Star ↔ segment parity.** Stars and segments are two kinds of the SAME
    practiced thing — attempts, PBs, strats, ranks, markers, replays, routes.
    A feature built for one ships for both in the same change, or the
    asymmetry is written down with its reason. Enforced structurally (shared
    components `stratpicker.js`/`PbTag`/`TimeFilterChip`/`StandardsPanel`;
    kind-dispatched endpoints `/api/target`, `/api/strat`, `/api/wipe`) and by
    `tests/test_ui_section_parity.py`.
12. **Route step order is a hard contract** — seeded route steps must be in
    completion-event order or a run stalls permanently and silently (detail in
    `.claude/rules/tracking-storage.md`).

## Dev-process rules

- **No orphaned processes.** Any server you start (dev server, `http.server`
  harness) dies in the same session. `tools/dev_cleanup.py` runs at session
  start and kills provably-dead leftovers; don't rely on it as a maid service.
  Don't start `python -m sm64_events.main` for UI checks while the user may be
  playing — the recorder lock is the only thing protecting their recording.
- **UI changes are verified by rendering** (headless Chrome or chrome-devtools
  MCP), never by unit tests + `node --check` alone — that combination once
  shipped an invisible feature.
- **Anything judged by FEEL gets an inspector, never a guess.** Timings,
  easing, juice, transitions, layout weights: build the tuning page first, let
  the human tune it live, and codify what he saves — "like how I would work
  with an Inspector in Godot" (2026-07-27). The rig is the deliverable; the
  numbers are its output. Recipe, the four properties that make a surface
  extractable, and the traps: the **`tuning-demo`** skill. Worked example at
  `/ui/tune.html` (`ui/climbtuning.js` + `ui/tune.js` + `server/tuning_api.py`).
  Corollary that bites everywhere else: **no test may assert the CONTENTS of a
  shipped default.** Once SAVE writes the tuning defaults, a test reading
  `DEFAULTS` turns every tuning round red — pin the law against a reference
  config and check only that the live values are in range. The same shape
  arrives with no inspector anywhere near it: four tests in `test_views.py`
  named the default stat chips, so changing a *preference* (2026-07-27, avg
  10/25/50 + success rate replacing best/worst) was a red build for reasons
  unrelated to the change. A test that needs a config should STORE the one it
  probes; the default's own coherence — no two entries sharing an identity,
  every entry addressable by both implementations that read it — belongs in
  ONE place (`tests/test_stats.py` for the stat menu).
- **A value two surfaces show gets ONE DOOR, and the door is enforced.** "Don't
  repeat yourself" cannot fail a build, and the divergence that matters is never
  copy-paste: three surfaces each grew their own honest way to turn a star into
  an icon and quietly disagreed (2026-07-26). Three checkable properties, all
  three or none:
  1. one module owns the derivation, import-free where it can be, so node/pytest
     can drive it directly;
  2. its public call takes **identity only, never ingredients** — every argument
     a caller assembles is a chance to assemble it differently, and this bug
     produced both halves (three hand-built contexts, then one call site passing
     the context BUILDER where the context belonged, silently defaulting every
     field);
  3. a row in `tests/test_single_source.py` naming the INGREDIENTS — the asset
     path, the lookup table, the literal — so no other file may name them. Not
     "is the shared function called", which passes while a second path exists
     beside it: the question is whether a second path can be written at all.
  Prove a new row has teeth by mutation (add the violation, watch it fail,
  revert) — a scan that matches nothing is green forever.
  This finds a second DOOR, never a wrong value through the right one: that same
  context-builder bug satisfied every scan and still repainted a whole grid.
  Rendering is the other half, not an alternative to it.
- **When one door is impossible, the duplicate gets a test that COMPARES the
  two.** Four values are computed in Python for the server and again in JS for
  the browser — the rank ladder (`classify.RANK_NAMES` ↔ `caps.js::CAP`), the
  rank-mode registry, the IGT display format, stat-chip identity. That second
  copy is a real decision (the browser cannot round-trip for it), not an
  oversight, and until 2026-07-28 each site was held together by a comment
  saying "keep the two in lockstep". `tests/test_cross_language_parity.py`
  compares the REAL implementations: import-free JS modules are imported by
  node; the ones that pull in Preact have their declaration extracted from
  comment-stripped source and evaluated. Never restate the rule in the test —
  a restatement is a third copy. Same mutation proof as above.
- **A pointer must resolve in a FRESH CLONE.** `docs/superpowers/`, `.tasks/`,
  `internal_notes/`, `.planning/` and `.superpowers/` are local working
  directories in a PUBLIC repo. Citing one is a dead link for everyone but this
  machine — and ignoring a directory does not touch the files already citing
  it, so it fails silently for the only person who cannot see it. State the
  FACT and name a tracked thing that carries it.
  `tests/test_docs_links_resolve.py` enforces this, with a by-path exemption
  list carrying a reason per row.
- **A rule file is a MAP, and it has a budget.** `.claude/rules/*.md` load
  automatically on a matching file read, so their cost is paid on every edit in
  the zone. `.claude/rules/ui.md` reached ~26k tokens with an 18,301-character
  table cell before it was split four ways (2026-07-28). When one grows, split
  it by path into a narrower sub-zone rule and lift long narratives into
  `## sections` below the table — never summarize, the evidence is the point.
  `tests/test_rule_files.py` holds the ceilings and, more usefully, fails when
  a `paths:` glob matches nothing: a rule that never loads reaches nobody while
  looking perfectly healthy.
- **Exit-code honesty:** run verification through the Bash tool. Never pipe
  native exes into `Select-Object` or use `2>&1` on them in PS 5.1 (false
  failures).
- Before ending a turn, re-scan recent user messages for unanswered asks
  (stacked messages historically dropped bug reports).

## Definition of done — every merge

- `uv run pytest -q` passes; new behavior has tests
- **responsive sweep clean** (`uv run pytest tests/test_responsive.py -q`) — a
  new defect is fixed, or owed in `tools/uilab_project.py::known_defects` with a
  reason. Component layout gates on `@container`, never `@media`; the law and
  why are in `.claude/rules/ui-core.md`. The rig itself is **uilab**, a shared
  machine-level module (`Desktop/code/uilab`) installed editable — improve the
  instrumentation THERE, not here, and run its `tools/check_consumers.py`
  afterwards
- **responsiveness is part of the feature, not a pass afterwards.** A UI change
  is not done at one width. The three habits, in the order they pay:
  1. **Look at it.** `tools/contact_sheet.py <selector>` — one surface at
     1500/1200/900/850 in one image. Every expensive failure in this area was
     obvious on sight and invisible to every assertion; the cheapest moment to
     see it is while still writing the code, not in review.
  2. **Ask what state the fixture is in.** The gates are only as good as the
     page `tools/ui_fixture.py` reaches, and a wrong state does not go red — it
     reports a clean page nobody is looking at. That has been the root cause
     three times. If your feature needs data the fixture does not seed, seed it
     and add a line to `tests/test_fixture_reaches_the_real_page.py`.
  3. **Give a new surface its own contract test if a probe cannot express it.**
     A defect probe answers "is something broken"; it cannot answer "does this
     component draw itself the same way in both layouts"
     (`test_rank_banner_continuity.py`) or "does the widest value in the corpus
     fit" (`test_objective_name_fits.py`). Both of those were user-reported
     bugs that no probe could have caught, in either direction.
  Prove any new guard by mutation — put the bug back, watch it go red, revert.
  A guard nobody has seen fail is green forever
- new memory reads live-verified with the human via the harness
- rule files / this file updated if modules were added or moved; README
  updated if the consumer-facing surface changed; docs/architecture.md updated
  if domain knowledge was gained (record hard-won facts WITH their evidence)
- one fact, one authoritative place: code docstrings for module-local
  knowledge, addresses.py for memory facts, **docs/api.md for the API
  surface**, `.claude/rules/` for per-zone change maps, architecture.md only
  for cross-cutting knowledge — link, don't duplicate. The README is for a
  HUMAN deciding whether to use or build this; endpoint tables belong in
  docs/api.md (`tests/test_docs_cover_api.py` accepts either file, so this is
  a convention the test cannot enforce for you)
- commit messages explain WHY (follow the style in `git log`)

**Build a UI / consumer:** speak only to the API — `ws://…/ws/events` (schema
in `docs/api.md`), `GET /state` for initial state, `GET /health` for liveness.
