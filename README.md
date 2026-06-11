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
| `practice_reset` | `igt_frames_before` | Usamune level reset — attempt anchor; payload carries the failed attempt's IGT |
| `state_loaded` | `igt_frames_restored` | Savestate / Usamune section-state load — attempt anchor |
| `attempt_completed` | `attempt_id, course_id, star_id, course_name, star_name, strat_tag, anchor_type, outcome, outcome_detail, igt_frames, igt, rta_frames` | Derived: an attempt just closed (success / reset / hard_reset / abandoned) |
| `target_set` | `course_id, star_id, strat_tag` | User explicitly set the practice target |
| `target_changed` | `course_id, star_id, strat_tag` | Practice target moved (auto-follows last valid grab, or set by command) |
| `attempt_cleared` | `attempt_id, reason?` | Attempt tombstoned (triggers full re-projection; `attempts_invalidated` follows) |
| `attempt_restored` | `attempt_id` | Tombstone undone (triggers full re-projection; `attempts_invalidated` follows) |
| `pb_saved` | `course_id, star_id, strat_tag, timer_mode, frames, attempt_id` | Personal best recorded |
| `session_started` | `session_id, label?` | New session opened (server start or `/api/session/new`) |
| `attempts_invalidated` | _(none)_ | Full re-projection ran — consumers must refetch `/api/session` |
| `emulator_connected` | _(none)_ | Attached to PJ64 process |
| `emulator_disconnected` | _(none)_ | Lost PJ64 process |

## HTTP API

All endpoints are under `/api`. JSON in, JSON out.

| Endpoint | Description |
|---|---|
| `GET /api/session?clock=igt\|rta` | Full session view: target, attempts per star, stat chips, PBs, catalog |
| `POST /api/session/new` `{label?}` | Close the current session and open a new one |
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
