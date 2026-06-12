# sm64_tracker — Claude Development Guide

This project is developed **exclusively by Claude**; the human runs the
emulator and verifies live behavior. Future sessions have no memory of past
ones — this file and `docs/architecture.md` ARE the memory. Keep them lean
and current: stale documentation is a broken build.

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
uv run uvicorn sm64_events.main:app --host 127.0.0.1 --port 8064   # run from repo root (data/ is cwd-relative)
uv run python tools/verify_addresses.py              # live gate (needs PJ64 + ROM)
uv run python tools/dedupe_journal.py data/tracker.db          # scan double-journaled events (read-only); add --fix to repair (server must be stopped)
```

## Module map — where to change what

| To change... | Edit |
|---|---|
| Memory addresses, action IDs, course/star names, traps | `memory/addresses.py` — THE registry; richly commented, read it |
| Endian decoding / typed reads | `memory/base.py` — the ONLY place that knows PJ64 byte order |
| Process attach / RDRAM discovery | `memory/pj64.py` |
| Object-pool decoding | `memory/objects.py` · test double: `memory/buffer.py` |
| Fields sampled each tick | `core/snapshot.py` |
| Event envelope / wire format | `core/events.py` |
| Star-grab + IGT logic | `detectors/star_grab.py` — docstrings carry the domain rationale |
| game_reset | `detectors/lifecycle.py` |
| Attempt anchors (practice_reset / state_loaded) | `detectors/anchors.py` — anchors carry mario_acted + paused_frames_before + acted_tracking; emits the mario_acted event; docstring covers classification, pause streak, and VERIFY notes |
| Death detection | `detectors/death.py` — action-set edge + pending-warp pulse for void-outs (pit falls fire BEFORE level_changed; docstring carries why); closes open attempt as outcome "death" |
| Level-change detection | `detectors/level.py` — stateful: remembers last EMITTED level, journals establishing/corrective events (from may equal to) so projection-side level tracking never runs stale; closes open attempts as abandoned |
| Dust tricks (dustless rollouts/jumps) | `detectors/dust.py` — TRICKS registry (one row per trick); docstring carries the decomp-verified landing-frame timing model; counts attach to attempts via projection.py |
| Poll loop, attach retry, layout sanity, session pause | `server/poller.py` |
| WS fan-out, seq numbers | `server/broadcaster.py` |
| HTTP/WS endpoints | `server/app.py` |
| REST API + error taxonomy | `server/api.py` — docstring has the LookupError/ValueError/RuntimeError→HTTP mapping |
| Attempt state machine / projection | `tracking/projection.py` — docstrings carry the two-pass clearing, reset-race row, clear-by-anchor-id invariant |
| Event pipeline + commands (journal→project→broadcast) | `tracking/service.py` |
| Session view payload | `tracking/views.py` |
| SQLite journal + derived tables | `storage/db.py` |
| Single-instance guard (broadcast-only fallback) | `storage/instance_lock.py` — Windows msvcrt file-region lock; held for process lifetime |
| Duplicate-event detection logic | `storage/dedupe.py` — pure fn; used by `tools/dedupe_journal.py` |
| Journal deduplication repair tool | `tools/dedupe_journal.py` — scan (read-only) or --fix (delete duplicates + re-project; server must be stopped) |
| Stats | `stats/registry.py` — ONE StatDef per stat; THE registry; also owns chip identity + canonical order (`selection_id`/`selection_order`, mirrored in `ui/components/statmenu.js` keyOf) |
| Per-star external links | `links.py` |
| Built-in viewer UI | `ui/index.html` — served per request: edit + refresh, no restart |
| UI components, store, API client | `ui/components/` · `ui/store.js` · `ui/api.js` · `ui/app.js`; vendored Preact in `ui/vendor/`; incl. `ui/components/timeline.js` (per-star event graph; marker styles via `MARKERS` registry) · `ui/components/progress.js` (per-star completion-time graph; gold = saved PBs; node click → practice.js pickFromGraph reveals + scrolls to the row, auto-opens saved replays) · `ui/format.js` (shared display formatting — fmtIgt mirrors core/timefmt.py) |
| Wiring / startup / logging | `main.py` (composition root), `core/logging_setup.py` |
| Memory-hunting diagnostics | `tools/` — playbook in docs/architecture.md |
| Replay orchestration (attach loop, source wiring, ring, idle gate) | `replay/recorder.py` + player-input tap `replay/activity.py`; `replay/clock.py` is THE QPC↔UTC contract |
| Replay video capture (DWM surface primary; GDI/WGC fallbacks) | `replay/video.py` + `replay/_dwm.py` — docstrings carry the PJ64 capture pathology and the no-user32-on-grab-thread rule |
| Replay video encoding (ffmpeg subprocess primary) | `replay/ffmpeg_sink.py` — why encode left the process, segment-CSV contract, health metrics; in-process fallback `replay/encoder.py` |
| Replay audio (endpoint-by-pid, RT-safe pump, deaf-stream watchdog) | `replay/audio.py` + `replay/_system_audio.py` |
| Replay clip extraction (wall-clock pts, exact-1024 AAC) | `replay/extract.py` |
| Replay REST surface (status/extract/save/serve) | `server/replay_api.py` — FileResponse for Range/206; same error taxonomy as api.py |
| Replay player + recording dot | `ui/components/replay.js` |

(All paths under `src/sm64_events/` unless noted.) Tests mirror modules:
`tests/test_<module>.py` — read the test file first; it's the executable spec.

## Parallel work zones

Safe to work concurrently (one branch/worktree each): **detectors/**,
**server/**, **ui/**, **memory/ + tools/**, **storage/ + stats/ + tracking/**,
**replay/**, **docs/** — each with its tests. The `storage/+stats/+tracking/` zone shares
the `Attempt` contract internally; keep it in one branch.
**Shared contracts — never edit in two branches at once:** `core/events.py`,
`core/snapshot.py`, `memory/addresses.py`, `tracking/projection.py`, `main.py`.
Contract changes land on master first, then dependent work fans out. Merge
with `--no-ff`; run the full suite on the merged result; delete the branch.

## Domain rules

1. New memory address → `addresses.py` only, with source comment, marked
   `VERIFY` until it passes the live gate with the human.
2. Star grabs MUST fire on re-collection: action-EDGE detection, never
   save-flag diffing.
3. IGT comes from the Usamune expansion-RAM globals (see star_grab.py for
   the result → counter → reconstructed precedence). Never the vanilla HUD
   timer, never object-pool addresses (slot-dependent — see addresses.py).
4. Detectors get consecutive (prev, curr) pairs, may keep bounded internal
   state, must self-heal when global_timer jumps backward.
5. Calibration constants (DISPLAY_TICK etc.) encode live-measured behavior —
   don't "simplify" them; their evidence is in the docstrings.
6. Read-only: never write to emulator memory.
7. Timestamps UTC; the primary clock is game frames (30 fps).
8. Keep the poller's implausible-read refusal — it has caught bugs in our
   own registry.
9. One server instance per db — enforced by `storage/instance_lock.py`; second instances run broadcast-only (events NOT double-recorded).

## Recipes

**Add a new event type:** tests first (`snap(**overrides)` fixture pattern
from test_star_grab.py) → `detectors/<name>.py` with
`process(prev, curr) -> list[Event]` → new memory fields go through
addresses.py (+VERIFY) and a defaulted GameSnapshot field → wire into
`main.py` (resets before grabs) → render in `ui/index.html` if user-visible
→ document payload in README → full pytest + live check.

**Add a dust trick** (landing-cancel chain like rollouts / double jumps):
- *Same stat family* (another `jump`-type chain, e.g. side flip out of a
  landing): ONE row in `TRICKS` (`detectors/dust.py`) + action ids in
  addresses.py (+VERIFY) + a test in test_dust.py. Aggregation, stats, UI
  all pick it up via the shared event_type. Done.
- *New stat family* (own `<x>_total`/`<x>_dustless` rate): the above, PLUS
  the per-family fan-out — Attempt fields + a `_dispatch` branch
  (tracking/projection.py), an ALTER TABLE migration (storage/db.py:
  MIGRATIONS + `_ATTEMPT_COLS` + `_attempt_params`), attempt_completed
  payload (tracking/service.py), `_attempt_json` (tracking/views.py), the
  row span in ui/components/practice.js, and a one-line
  `_dust_rate(...)` StatDef (stats/registry.py). Mirror the jumps
  commits on 2026-06-11 (`git log --grep=jump`); each step has a test to
  copy. If a THIRD family ever lands, generalize counts to a keyed
  structure instead of adding more columns.
- Timing rule (decomp-verified, do NOT re-derive from the spec — its §3
  model is annotated as wrong): `frames_late = visible_landing_frames - 1`;
  one visible landing frame IS frame-perfect. Evidence: addresses.py.

**Add a user-visible replay setting** (another knob like storage/padding):
bounds row in `SETTINGS_LIMITS` + plumb `validate_settings`/`save_settings`
/`apply_settings_file` (replay/config.py) → live-apply + getter in
`ReplayService.update_settings`/`settings()` → field on `SettingsBody`
(server/replay_api.py) → input in the recording-dot panel
(`ui/components/replay.js` BufferSettings) → README settings lines → tests
in test_replay_{config,service,api}.py. Mirror commits 69bb83d / 29fd542.
Settings persist in `data/replay_settings.json` (a JSON overlay beats a db
migration for scalars); corrupt/out-of-range files lose to defaults so the
server always starts.

**Locate an unknown memory value:** `tools/find_timer.py` (ticking
counters) → `tools/hunt_value.py` (exact displayed values) →
`tools/watch_timer.py ADDR:u16` (characterize across scenarios). Methodology
and pitfalls: docs/architecture.md → Memory hunting.

**Build a UI / consumer:** speak only to the API — `ws://…/ws/events`
(schema in README), `GET /state` for initial state, `GET /health` for
liveness. Heavier frontends go in the ui zone or a new top-level dir.

**Wrap up a feature:** after the merge, run the `create-artifacts` skill
(`.claude/skills/create-artifacts/`) — it routes the session's mistakes,
review findings, and unverified assumptions into tests, hooks, point-of-use
comments, docs, skills, and memories, each placed where the next session
hits it before repeating the mistake.

## Definition of done — every merge

- `uv run pytest -q` passes; new behavior has tests
- new memory reads live-verified with the human via the harness
- this module map updated if files were added/moved; README updated if the
  consumer-facing surface changed; docs/architecture.md updated if domain
  knowledge was gained (record hard-won facts WITH their evidence,
  immediately — the next session has no memory of this one)
- one fact, one authoritative place: code docstrings for module-local
  knowledge, addresses.py for memory facts, README for the API surface,
  architecture.md only for cross-cutting knowledge — link, don't duplicate
- commit messages explain WHY (follow the style in `git log`)
