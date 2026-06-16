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
uv run python -m sm64_events.main                    # run from repo root (data/ is cwd-relative); canonical — binds the CTRL+C shutdown deadline (bare uvicorn CLI needs --timeout-graceful-shutdown 3)
uv run python tools/verify_addresses.py              # live gate (needs PJ64 + ROM)
uv run python tools/dedupe_journal.py data/tracker.db          # scan double-journaled events (read-only); add --fix to repair (server must be stopped)
```

**Server port:** `core/paths.py::server_port()` is the single source — `SM64_PORT`
env override, else **8064 when frozen (the exe), 8065 from source (dev)**. This
is enforced so a dev server and a built exe never collide on one port (the
exe's single-instance takeover would otherwise fight a dev server on :8064).

## Module map — where to change what

| To change... | Edit |
|---|---|
| Memory addresses, action IDs, course/star names, traps | `memory/addresses.py` — THE registry; richly commented, read it |
| Endian decoding / typed reads | `memory/base.py` — the ONLY place that knows PJ64 byte order |
| Process attach / RDRAM discovery | `memory/pj64.py` |
| Object-pool decoding | `memory/objects.py` · test double: `memory/buffer.py` |
| Fields sampled each tick | `core/snapshot.py` |
| Event envelope / wire format | `core/events.py` |
| Star-grab + IGT logic | `detectors/star_grab.py` — docstrings carry the domain rationale; IGT itself comes from the shared `detectors/igt_clock.py` (result→counter→reconstructed) which ALSO stamps key.py's grand star — every displayed time routes through it, never a frame delta |
| game_reset | `detectors/lifecycle.py` |
| Attempt anchors (practice_reset / state_loaded) | `detectors/anchors.py` — anchors carry mario_acted + paused_frames_before + acted_tracking + save_pending (post-star save-screen latch → segment echo) + frames_since_dialog (textbox/intro-cutscene recency → segment echo shape 5: a run never splits/resets on a textbox); emits the mario_acted event; docstring covers classification (incl. the pause-warp shape: menu warp with IGT already ~0 → anchor from position change + pause streak), pause streak, and VERIFY notes |
| Death detection | `detectors/death.py` — action-set edge + pending-warp pulse for void-outs (pit falls fire BEFORE level_changed; docstring carries why); closes open attempt as outcome "death" |
| Level-change detection | `detectors/level.py` — stateful: remembers last EMITTED level, journals establishing/corrective events (from may equal to) so projection-side level tracking never runs stale; closes open attempts as abandoned |
| Dust tricks (dustless rollouts/jumps) | `detectors/dust.py` — TRICKS registry (one row per trick); docstring carries the decomp-verified landing-frame timing model; counts attach to attempts via projection.py |
| Stage detection (quick-select banner context) | `detectors/stage.py` — broadcast-only `stage_changed {course_id, level, area, in_stage}`; keys on the resolved CONTEXT: a main course 1-15 (stars) OR a Castle Inside subarea (segments — keyed on area so lobby↔upstairs re-emits); reuses `course_for_level` (addresses.py) |
| area_changed / warp_entered / key_grabbed / spawned | `detectors/area.py` · `detectors/warp.py` · `detectors/key.py` · `detectors/spawn.py` — segment-primitive facts; area mirrors level.py's last-EMITTED discipline; key detector guards star_grab from misattributing Bowser keys AND carries Usamune IGT (via igt_clock) on fight-end grabs so a segment ending on the grand star matches Usamune's time, not a wall-frame delta |
| Segment defs, trigger vocabulary, matcher FSM | `tracking/segments.py` — ONE registry (TRIGGERS/GUARDS) drives validation, matching, and the /api/segments/vocab endpoint; docstring carries the FSM invariants (closures before arming, guards re-evaluated every arm, silent disarm on foreign level change, position-gated anchor closures: retry re-arms in place at the arm position, a warp elsewhere disarms with no row and swaps to the destination's segment, load/door/save-prompt-echo shapes) |
| Segments builder UI | `ui/components/segments.js` — 100% vocab-driven (labels, sentence templates, level/area/course/star enums): adding a trigger type in tracking/segments.py appears in the UI with zero JS changes |
| Stage quick-select banner | `ui/components/stagebanner.js` — dual-mode: one-click STAR target in a main course, or one-click SEGMENT target in a Castle Inside subarea (lobby/upstairs/basement) showing segments whose start triggers begin there (`segment_targets`, derived in views.py via `_segment_start_areas` — only subarea-scoped triggers, so LBLJ never shows upstairs); data-driven from the session view; rendered atop `ui/components/practice.js` |
| Route builder UI | `ui/components/routes.js` — Routes tab: route picker + CRUD, step editor (reorder / add / remove / `need` for K-of-N groups), candidate chips, import/export panel. Display names + per-step/cumulative % from `GET /api/routes/{id}`; raw steps from `GET /api/routes`; every edit PUTs steps and re-fetches |
| Route Practice focus | `ui/components/practice.js` (RouteFocus) — active-route picker (localStorage `sm64.activeRoute`) replaces the normal lists with the route's members in order; current step = live target (else step 1) and renders the full Star/SegmentSection inline; click a candidate to set target/retry. Route order + %s from `GET /api/routes/{id}` |
| Poll loop, attach retry, layout sanity, session pause | `server/poller.py` |
| WS fan-out, seq numbers | `server/broadcaster.py` |
| HTTP/WS endpoints | `server/app.py` |
| REST API + error taxonomy | `server/api.py` — docstring has the LookupError/ValueError/RuntimeError→HTTP mapping |
| Attempt state machine / projection | `tracking/projection.py` — docstrings carry the two-pass clearing, reset-race row, clear-by-anchor-id invariant, active-star retirement (segment-arm / different-course → target None; caveat 12) |
| Event pipeline + commands (journal→project→broadcast) | `tracking/service.py` |
| Session view payload | `tracking/views.py` |
| SQLite journal + derived tables | `storage/db.py` |
| Route defs (ordered star/segment plans), cumulative success, import/export | `tracking/routes.py` — pure: `validate_route`, `route_stats` (best-K product, no-data=0), `export_route` (embeds segment defs), `resolve_import` (reuse exact match / create rest). Steps are a uniform `{label?, need:K, candidates:[star\|segment]}` shape; a route also carries a `start_condition` trigger (default `reset_game`) that arms the run clock |
| Route view payload | `tracking/views.py::build_route_view` — resolves candidate names + per-step/cumulative success + broken flag (deleted segment) |
| Route CRUD + import/export commands | `tracking/service.py` — create/update/delete_route (segment-existence check), export_route, import_route (dry-run preview); broadcast-only `routes_changed` |
| Route storage | `storage/db.py` — `routes` table (migration v7) + routes/insert_route/update_route/delete_route |
| Route REST surface | `server/api.py` — `/api/routes` CRUD, `/api/routes/{id}/export`, `/api/routes/import?dry_run=` |
| Run engine (forgiving-RTA full-game timer) | `tracking/runs.py` — pure `RunTracker`: arm on `run_started`, start the clock when the route's **`start_condition`** trigger fires (default `reset_game`=F1; a `game_reset` that is NOT the condition aborts) + `start_offset`, forgiving splits (wall-clock per step **minus paused time**, retries roll up), K-of-N no-dup completion, finish on the last step. `run_paused`/`run_resumed` exclude paused time AND suspend completions; `run_reset` aborts; `run_started` with `void_active` DISCARDS the in-flight run (route edited mid-run). `pb_run`/`gold_splits` helpers; run id = the starting game_reset journal id; times stored offset-free |
| Run projection wiring | `tracking/projection.py` — `Projector` embeds `RunTracker`, feeds it `(ev, closed)`; `finished_runs()`/`active_run_view()`/`run_notices`. Runs re-derive on replay (cache like attempts) |
| Run storage | `storage/db.py` — `runs` table (migration v8) + insert/upsert/replace/`runs(route_id?,finished_only?)`; run settings in `ui_state` (`start_offset_ms`, default 1360) |
| Run lifecycle + view + API | `tracking/service.py` (`start_run`/`end_run`/`pause_run`/`resume_run`/`reset_run`/`run_settings`; `_arm_run` snapshots the route's steps+start_condition into `run_started`; editing the armed route re-arms with `void_active` so the in-flight run is voided + fresh; persists runs; `run_started`/`run_ended`/`run_paused`/`run_resumed`/`run_reset` journaled, `run_finished`/`run_aborted`/`run_progress` broadcast-only) · `tracking/views.py` (`build_run_view`/`build_run_history`) · `server/api.py` (`/api/run/*` incl. pause/resume/reset) |
| Run view UI (Run tab) | `ui/components/runview.js` — route picker (selecting a route ARMS it — **no Start button**); **always-on clock** (idle=`0:00`+offset + route preview, active=ticks, finished=frozen — only when the LATEST run finished) ticking client-side off `started_utc`+offset **minus paused_ms**; per-step cumulative + ± vs PB + gold ★; **Pause/Resume/Reset**, **Focus** (neutral, no ±/gold), **click-to-hide** any timer (localStorage `sm64.runFocus`/`sm64.runHidden`); run-history list (finished/aborted filter) + progression graph (0-based, slower=higher, gold=PB) with click-to-expand splits, from `GET /api/run/history`. `store.js` holds `run`, refetched on `run_*`/`game_reset`; `build_run_view` carries per-step `pb_elapsed_ms`/`gold_ms`; the Routes tab has a "Run starts when:" trigger picker (reuses segments.js `ClauseRow`) |
| Single-instance guard (broadcast-only fallback) | `storage/instance_lock.py` — Windows msvcrt file-region lock; held for process lifetime |
| Duplicate-event detection logic | `storage/dedupe.py` — pure fn; used by `tools/dedupe_journal.py` |
| Journal deduplication repair tool | `tools/dedupe_journal.py` — scan (read-only) or --fix (delete duplicates + re-project; server must be stopped) |
| Stats | `stats/registry.py` — ONE StatDef per stat; THE registry; also owns chip identity + canonical order (`selection_id`/`selection_order`, mirrored in `ui/components/statmenu.js` keyOf) |
| Per-star external links | `links.py` |
| Built-in viewer UI | `ui/index.html` — served per request: edit + refresh, no restart |
| UI components, store, API client | `ui/components/` · `ui/store.js` · `ui/api.js` · `ui/app.js`; vendored Preact in `ui/vendor/`; incl. `ui/components/timeline.js` (per-star event graph; marker styles via `MARKERS` registry) · `ui/components/progress.js` (per-star completion-time graph; gold = saved PBs; node click → practice.js pickFromGraph reveals + scrolls to the row, auto-opens saved replays) · `ui/format.js` (shared display formatting — fmtIgt mirrors core/timefmt.py) |
| Wiring / startup / logging | `main.py` (composition root), `core/logging_setup.py` |
| Runtime data locations (db, replays, settings, lock, pidfile, window state, logs) | `core/paths.py` — THE path resolver; cwd-relative from source (identical to historical layout), `%LOCALAPPDATA%\sm64_tracker` when frozen; also `bundled_ffmpeg()` |
| Full-process restart primitives | `core/relaunch.py` — `server_alive`/`port_in_use`/`wait_port_free`/`spawn_replacement`; backs the one-click restart + the `SM64_RESTART` handoff (waits on real port bindability, scrubs PyInstaller `_MEIPASS2`/`_PYI_*`) |
| Desktop GUI shell (window, tray, single-instance, server runner) | `desktop/` — additive wrapper over the SAME server/UI: `app.py` (composition + native takeover dialog + restart/quit wiring), `server_runner.py` (uvicorn in a thread), `single_instance.py`, `window.py` (resizable pywebview + geometry), `tray.py`; entry `python -m sm64_events.desktop` / `gui_entry.py` |
| Admin endpoints (GUI takeover + restart) | `server/app.py` `POST /api/admin/shutdown` + `/api/admin/restart` + pidfile in the lifespan; dispatched off-thread |
| One-command portable build | `tools/build_exe.py` (+ `tools/rthook_comtypes.py`, `assets/ukiki.ico`) — PyInstaller onefile; auto-bundles ffmpeg from PATH |
| Resource observability PROBES (self + child + OS) | `core/procmem.py` — pure-ctypes samplers: self RSS + private/commit bytes, kernel-handle + GDI/USER object counts, system-wide memory (GlobalMemoryStatusEx), CHILD-process memory (ffmpeg, via Toolhelp32 by parent pid), per-type heap histogram; plus pure alarm/growth helpers (`assess_growth`, `top_type_growth`, `resource_alarms`). Degrades to 0/{} off-Windows. THE leak-ATTRIBUTION surface (2026-06-14 widening: the RSS-only monitor was blind to child/handles/system/per-type, so every fix missed) |
| Periodic perf sampler + JSONL time-series | `core/perfmon.py` — `PerfMonitor` samples the procmem probes every 60 s, logs an expanded `mem:` + top-growers line, fires one-shot per-class leak alarms, and PERSISTS each sample to `data/perf_log.jsonl` (size-capped, rotates to .prev on startup so one run = one log). Backs `/health.memory`; wired in `server/app.py` with poller tick-latency + replay ring gauges. Path resolves LAZILY so tests (conftest) never clobber the real log. Supersedes `MemoryMonitor` |
| Memory-hunting diagnostics | `tools/` — playbook in docs/architecture.md; `tools/analyze_perf_log.py` ranks `data/perf_log.jsonl` growth and NAMES the dominant climber (self-RSS vs ffmpeg-child vs handles/GDI vs system commit vs a Python type vs tick latency) — run after a long session to localize a leak |
| Replay orchestration (attach loop, source wiring, ring, idle gate) | `replay/recorder.py` + player-input tap `replay/activity.py`; `replay/clock.py` is THE QPC↔UTC contract. Idle now THROTTLES capture grabs (`is_idle` → `video.set_idle_check`) not just discards segments; the ring byte-cap is free-disk-gated (`ring.effective_cap`) so it can't fill the volume |
| Replay video capture (DWM surface primary; GDI/WGC fallbacks) | `replay/video.py` + `replay/_dwm.py` — docstrings carry the PJ64 capture pathology and the no-user32-on-grab-thread rule; `grab_period` trickles grabs to 8 Hz while idle (kills the ~2 GB/s frame-alloc churn; ffmpeg feeder untouched so resume stays seamless) |
| Replay video encoding (ffmpeg subprocess primary) | `replay/ffmpeg_sink.py` — why encode left the process, segment-CSV contract, health metrics; in-process fallback `replay/encoder.py` |
| Replay GC policy (stop-the-world watchdog + gen-2 idle collector) | `replay/_gcwatch.py` — freezes the startup heap, disables AUTO gen-2, and runs the manual `gc.collect(2)` during idle with a 5-min never-idle backstop (closes the leak from disabling gen-2 without ever collecting it); `arm(is_idle=recorder.is_idle)` from `server/app.py` lifespan |
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
10. **Browser ↔ GUI parity.** Every user-facing feature lands in `ui/` +
   server, so it appears in BOTH the browser tab and the desktop window. The
   `desktop/` shell adds ONLY native chrome (window, tray, icon,
   single-instance, restart) and must never fork or special-case the UI.

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
