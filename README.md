# sm64_tracker — SM64 Event API

Detects game events in **SM64 Usamune v1.93u** running in **Project64 1.6**
(Windows) by reading emulator memory, and broadcasts them as JSON over
WebSocket — star grabs with exact Usamune timing, game resets, emulator
lifecycle. Built for practice-stats tooling, stream overlays, and anything
else that wants a live feed of the game.

> Developing in this repo? **Read `CLAUDE.md` first** — module map, domain
> rules, recipes, and the documentation contract. Deep domain reference:
> `docs/architecture.md`.

## Run

    uv sync
    uv run uvicorn sm64_events.main:app --host 127.0.0.1 --port 8064

**Run from the repo root** — `data/tracker.db` is created relative to cwd.

A second server started against the same db acquires no lock and runs broadcast-only — game events are never double-recorded.

Requires Project64 1.6 running Usamune v1.93u on the same machine (8 MB /
expansion-pak memory). The server attaches automatically and reattaches if
the emulator restarts.

- **Viewer / Practice Tracker**: `http://127.0.0.1:8064/` (tabs: Practice | Routes | Live feed)
- **Events**: `ws://127.0.0.1:8064/ws/events`
- **Health**: `http://127.0.0.1:8064/health`
- **Latest snapshot**: `http://127.0.0.1:8064/state`

> **Note on the viewer**: `/` reads `ui/index.html` fresh on every request
> (edit + refresh, no restart needed). The static mount at `/ui/index.html`
> may briefly lag during edits; prefer `/`.

## Event schema

Every WebSocket message is a versioned envelope:

```json
{"v": 1, "seq": 412, "type": "star_collected", "frame": 456052,
 "timestamp_utc": "2026-06-10T22:14:03.512000Z",
 "payload": {"course_id": 8, "course_name": "Shifting Sand Land",
             "star_id": 1, "star_name": "Shining Atop the Pyramid",
             "already_collected": true,
             "igt_frames": 595, "igt": "0'19\"83",
             "igt_source": "result", "igt_reconstructed": false}}
```

- `seq`: monotonic per server run (gap = missed events)
- `frame`: game-frame stamp (30 fps), back-computed to the exact touch frame
- `star_id`: 0-based within the course (6 = 100-coin star)
- `igt`: Usamune's overall star time — the number shown on screen, correct
  across multi-area levels; `igt_frames` is the same in raw frames
- `igt_source`: `result` (Usamune's own stored final time — exact),
  `counter` (running overall counter, back-computed), `reconstructed` (see
  Behavior notes)

**Breaking change (phase 1):** `game_reset` now fires **only** on backward
timer jumps into the boot range (console reset / ROM reload). Savestate and
Usamune section-state loads that previously looked like resets now emit
`state_loaded` instead.

Other event types, same envelope:

| Type | Key payload fields | Meaning |
|---|---|---|
| `game_reset` | _(none)_ | Console reset / ROM reload (timer into boot range) |
| `practice_reset` | `igt_frames_before, mario_acted` | Usamune level reset — attempt anchor; payload carries the failed attempt's IGT and whether Mario entered any non-passive action since the last anchor (no-op resets where `mario_acted: false` are discarded) |
| `state_loaded` | `igt_frames_restored, mario_acted` | Savestate / Usamune section-state load — attempt anchor; same activity flag as practice_reset |
| `death` | `cause, igt_frames, level` | Mario died; closes the open attempt as outcome "death" with the cause in outcome_detail |
| `level_changed` | `from, to` | Level id edge; closes open attempts as abandoned; `from` is 0 on first read after attach |
| `rollout` | `dustless, frames_late, landing_frames, level` | Dive→rollout executed. `landing_frames` = visible dive-slide frames (always ≥ 1: the landing transition takes one frame before inputs are read — decomp-verified); `frames_late = landing_frames - 1`; `dustless: true` ⟺ frame-perfect (`frames_late == 0`). Rollouts whose slide entry wasn't observed (attach race, savestate mid-slide) emit nothing. Attaches to the open attempt (`rollouts_total` / `rollouts_dustless`) |
| `jump` | `dustless, frames_late, landing_frames, kind, level` | Chained jump executed: `kind: "double"` (jump-land → double jump) or `"triple"` (double-jump-land → triple jump). Same timing semantics as `rollout`; note the visible dust puff additionally requires speed (forwardVel > 16), so `dustless` means frame-perfect timing, not "no puff appeared". Attaches to the open attempt (`jumps_total` / `jumps_dustless`) |
| `attempt_completed` | `attempt_id, session_id, course_id, star_id, course_name, star_name, strat_tag, anchor_type, outcome, outcome_detail, igt_frames, igt, rta_frames, rollouts_total, rollouts_dustless, jumps_total, jumps_dustless` | Derived: an attempt just closed (success / reset / death / hard_reset / abandoned) |
| `target_set` | `course_id, star_id, strat_tag?` | User explicitly set the practice target |
| `target_changed` | `course_id, star_id, strat_tag` | Practice target moved (auto-follows last valid grab, or set by command) |
| `attempt_cleared` | `attempt_id, reason` | Attempt tombstoned; `reason` is always present, may be null (triggers full re-projection; `attempts_invalidated` follows) |
| `attempt_restored` | `attempt_id` | Tombstone undone (triggers full re-projection; `attempts_invalidated` follows) |
| `pb_saved` | `course_id, star_id, strat_tag, timer_mode, frames, attempt_id` | Personal best recorded |
| `session_started` | `session_id, label?` | New session opened (server start or `/api/session/new`) |
| `attempts_invalidated` | _(none)_ | Full re-projection ran — consumers must refetch `/api/session` |
| `emulator_connected` | _(none)_ | Attached to PJ64 process |
| `emulator_disconnected` | _(none)_ | Lost PJ64 process |

**Attempt outcomes:** `success`, `reset`, `death`, `hard_reset`, `abandoned`. `death` and `reset` count toward the default failure rate. `abandoned` (level changed before a grab) and discarded no-op resets (where `mario_acted: false`) never count toward the failure rate. Old journal entries without the `mario_acted` key default to acted (counted as real resets).

**Strategies:** Strategy names are remembered per star — switching the target star loads that star's own last-used strategy, not the previous star's. Known strategies for a star = everything registered via target-setting plus every tag appearing in that star's attempt history. The session view surfaces them in `strategies` (map of `"course_id:star_id"` → list) and `last_strat_by_star` (map → last used), and per-section `strategies` / `last_strat` fields.

**Session view payload** (`GET /api/session`) top-level fields include `scope` (`"session"` or `"lifetime"`) and `sessions` (array of all sessions, newest-first, each with `id`, `attempts`, `started_utc`, `ended_utc`). Each star section additionally carries a `timeline` object: `{max_frames, max_display, max_is_success, points:[{frames, igt, outcome, attempt_id}]}`. The axis maximum (`max_frames`) is the longest successful attempt (or the longest attempt overall when `max_is_success` is false, i.e. no successes yet). Points are lifetime — they may exceed `max_frames` on the x-axis.

**Timelines:** Each star section renders a strat map — every success, reset, and death plotted at its IGT position along a shared axis. Extending marker kinds requires two changes: one row in `TIMELINE_OUTCOMES` (`tracking/views.py`) to define the outcome key and color, and one row in `MARKERS` (`ui/components/timeline.js`) to define the SVG shape. Everything else (axis, tooltip, projection) is derived automatically from those two registries.

## HTTP API

All endpoints are under `/api`. JSON in, JSON out.

| Endpoint | Description |
|---|---|
| `GET /api/session?clock=igt\|rta[&scope=session\|lifetime]` | Full session view: target, attempts per star, stat chips, PBs, catalog; `scope=session` (default) shows only the active session, `scope=lifetime` aggregates all sessions |
| `POST /api/session/new` `{label?}` | Close the current session and open a new one |
| `POST /api/session/continue` `{session_id}` | Resume a previously ended session; new attempts land there |
| `DELETE /api/session/{id}` | Hard-delete a session and all its data (409 on the active session; PBs survive; clears recorded in the deleted session revert their targets on re-projection) |
| `POST /api/target` `{course_id, star_id, strat_tag?}` | Set the practice target |
| `POST /api/attempts/{id}/clear` `{reason?}` | Tombstone an attempt (triggers re-projection) |
| `POST /api/attempts/{id}/restore` | Undo a tombstone (triggers re-projection) |
| `POST /api/pb` `{attempt_id, timer_mode}` | Save a personal best from a success attempt |
| `GET /api/stats/registry` | List all available stat definitions with keys, labels, and default params |
| `PUT /api/statmenu` `{selections: [{key, params}]}` | Persist the stat chip selection |
| `GET /api/links/{course_id}/{star_id}` | External links for a star (Ukikipedia, etc.) |

**Error taxonomy:** `404` = no such attempt; `409` = attempt exists but is not valid for the
operation (bad timer mode, already cleared, non-success outcome, or missing clock);
`503` = database unavailable (server is running in broadcast-only mode).

> `GET /api/pb` is intentionally absent in phase 1 — current PBs are included in the
> `/api/session` response.

## Data

`data/tracker.db` is a SQLite database created on first run (gitignored). It holds an
append-only event journal — the source of truth — plus derived/materialized tables
(`attempts`, `sessions`, `pbs`). Derived tables are rebuildable from the journal.
Deleting the file resets all history.

**The server must start from the repo root** — the DB path is resolved relative to cwd.

## Tools (live diagnostics — need PJ64 + ROM running)

| Tool | Purpose |
|---|---|
| `tools/verify_addresses.py` | Address verification gate + live event watch (prints real detector output) |
| `tools/find_timer.py` | Scan RDRAM for ticking counters |
| `tools/hunt_value.py` | Find where a displayed number lives (exact-value search) |
| `tools/watch_timer.py` | Characterize a candidate address across game scenarios |
| `tools/dedupe_journal.py` | Scan for double-journaled events from concurrent-instance incidents (read-only); `--fix` deletes duplicates and re-projects (server must be stopped first) |

## Behavior notes

- If a Usamune timer reset races the star touch (reset within ~1 s of the
  grab, e.g. reset-spamming between attempts), the event reports the
  *prior attempt's* time extrapolated to the exact touch frame and sets
  `igt_reconstructed: true`. In that scenario the API is deliberately more
  accurate than Usamune's own frozen display.

## Known limitations

- Loading a savestate that was *saved during* a star dance re-emits that
  star's `star_collected` event (the load looks like a fresh grab edge);
  its `already_collected` flag may be wrong. Savestates saved outside a
  dance are safe. Usamune section states are typically safe.
- Bowser-stage key grabs use the same star-dance actions and may emit a
  `star_collected` with `course_id` 16/17 until a dedicated key event
  type exists.
