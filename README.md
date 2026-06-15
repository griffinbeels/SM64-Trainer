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
    uv run python -m sm64_events.main

**Run from the repo root** — `data/tracker.db` is created relative to cwd.

This is the canonical launch: it binds uvicorn's graceful-shutdown
deadline so one CTRL+C always terminates the server. Launching the app
via the bare uvicorn CLI works too, but pass
`--timeout-graceful-shutdown 3` — without it, a browser holding a
connection (e.g. a paused replay video) stalls CTRL+C until a 30 s
force-exit watchdog fires.

A second server started against the same db acquires no lock and runs broadcast-only — game events are never double-recorded.

Requires Project64 1.6 running Usamune v1.93u on the same machine (8 MB /
expansion-pak memory). The server attaches automatically and reattaches if
the emulator restarts.

- **Viewer / Practice Tracker**: `http://127.0.0.1:8064/` (tabs: Practice | Routes | Live feed)
- **Events**: `ws://127.0.0.1:8064/ws/events`
- **Health**: `http://127.0.0.1:8064/health` (includes a `memory` block — RSS, GC state, live object count, scratch-buffer size — for diagnosing leaks on long-running sessions; also logged as a `mem:` line every 60 s)
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
| `practice_reset` | `igt_frames_before, mario_acted, paused_frames_before, acted_tracking, action, prev_action, frames_since_door, frames_since_dialog` | Usamune level reset — attempt anchor; payload carries the failed attempt's IGT and whether Mario entered any non-passive action since the last anchor (no-op resets where `mario_acted: false` are discarded); closures after ≥5 s of pause (paused_frames_before ≥ 150) are discarded as AFK. Also fires for **pause-warps**: a menu warp executed straight from the pause menu with the section timer still near zero has no IGT edge — it is detected by position change + pause streak and emitted one position-stable tick later, so the `area_changed` always journals first; `action` = Mario's action at the detection tick; `prev_action` = Mario's action on the PREVIOUS poll tick — the segment engine keys the door-echo clause on `prev_action`: a genuine door crossing has `prev_action` in `DOOR_ACTIONS` (inputs locked during the door animation on the prior tick); an L-reset respawning AT a door has a gameplay `prev_action` (e.g. freefall) and must be treated as a real reset. Fall back to `action` for events journaled before `prev_action` was added; missing both fields → conservative close. `frames_since_door` = game frames since the last door action was observed (None if never seen); the segment engine treats 0–30 as a non-warp door echo (shape d) even when neither action nor prev_action carries door context. `frames_since_dialog` = game frames since the last textbox/intro-cutscene action (None if never seen); a textbox/cutscene re-initialises Usamune's IGT, so the segment engine treats 0–30 as a **dialogue echo** (shape 5) — a run never splits/resets on a textbox in any level (this is what stops the Lakitu-skip intro from firing a false reset one frame after the spawn). |
| `state_loaded` | `igt_frames_restored, mario_acted, paused_frames_before, acted_tracking, action, prev_action, frames_since_door, frames_since_dialog` | Savestate / Usamune section-state load — attempt anchor; same activity flag as practice_reset; same pause/activity discards as practice_reset; same `action`, `prev_action`, `frames_since_door`, and `frames_since_dialog` fields as practice_reset for symmetry |
| `mario_acted` | _(none)_ | Mario's first voluntary action since the last anchor (death actions never count); the tracking layer uses it to judge whether an attempt had any behavior |
| `death` | `cause, igt_frames, level` | Mario died; closes the open attempt as outcome "death" with the cause in outcome_detail. Causes: the death-action set (`standing`, `quicksand`, `electrocution`, `suffocation`, `on_stomach`, `on_back`, `eaten_by_bubba`, `drowning`, `water`) plus `fall` — a void-out (death barrier / pit), detected from the game's pending-warp pulse *before* the level unloads, so the death always precedes the spit-out's `level_changed` |
| `level_changed` | `from, to, from_area` | Level id edge; closes open attempts as abandoned. May arrive with `from == to`: the detector emits one establishing event on server start and a corrective event after attach gaps (`from` = last *emitted* level, not the previous read) so journal-derived level tracking never runs stale — don't infer "left level X" from `from != to`. `from_area` is `gCurrAreaIndex` BEFORE the edge (the Castle Inside subarea Mario left: lobby=1, upstairs=2, basement=3) so a segment trigger can scope a crossing by source area; best-effort across attach gaps. There is no `to_area` — the destination loads area 1 transiently then warps to the real area a poll later, so the settled destination subarea comes from the following `area_changed`, not the edge. |
| `area_changed` | `level, from, to` | Castle area id edge (lobby=1, upstairs=2, basement=3 — areas of level 6). Same establishing/corrective semantics as `level_changed`: emits on server start and after attach gaps (`from` may equal `to`). `CURR_AREA` live-pinned 2026-06-12 (`0x8033BACA`). |
| `warp_entered` | `level, area, action` | Edge into a warp/pipe-entry action on the already-sampled `mario_action`. The community-comparable timing moment for "entered the pipe" segments — the `level_changed` that follows adds constant fade time, so segment end anchors target this event instead. |
| `key_grabbed` | `level, which, igt_frames, igt, igt_source` | Mario grabbed a Bowser key or the B3 grand star. `which` is `"bitdw"` (Bowser 1, level 30), `"bitfs"` (Bowser 2, level 33), or `"grand"` (Bowser 3, level 34). The key detector claims all three fight-ending grabs. B3's grand star is NOT a collectable star — live-verified 2026-06-12: it enters `ACT_JUMBO_STAR_CUTSCENE` (0x1909), numStars unchanged, no star-dance action, `gLastCompleted*` untouched — so `star_collected` is unreachable and the grand star is handled here. `igt`/`igt_frames`/`igt_source` are Usamune's IGT for the fight, from the same shared clock as `star_collected` (so a segment ending on this grab matches Usamune's displayed time exactly, not a wall-frame delta — added 2026-06-12). |
| `spawned` | `level, kind` | Mario gained control at a spawn-in. `kind: "intro"` = leaving `ACT_INTRO_CUTSCENE` (file-select spawn on Castle Grounds — Lakitu Skip start anchor, control begins when the cutscene ends); `kind: "spawn"` = edge into a SPAWN_* action (non-intro spawn-ins). |
| `segment_armed` | `segment_id, name` | A segment definition's start trigger fired — its RTA timer is now running. **Broadcast-only — never journaled.** Consumers should treat as a live hint only; a plain `/api/session` refresh self-heals the armed state from the projector. |
| `segment_disarmed` | `segment_id, name` | A segment's timer was stopped without recording an attempt (foreign level change, a warp/savestate that landed outside the segment's start position — moving, not practicing — or silent disarm after a success). **Broadcast-only — never journaled.** |
| `rollout` | `dustless, frames_late, landing_frames, level` | Dive→rollout executed. `landing_frames` = visible dive-slide frames (always ≥ 1: the landing transition takes one frame before inputs are read — decomp-verified); `frames_late = landing_frames - 1`; `dustless: true` ⟺ frame-perfect (`frames_late == 0`). Rollouts whose slide entry wasn't observed (attach race, savestate mid-slide) emit nothing. Attaches to the open attempt (`rollouts_total` / `rollouts_dustless`) |
| `jump` | `dustless, frames_late, landing_frames, kind, level` | Chained jump executed: `kind: "double"` (jump-land → double jump) or `"triple"` (double-jump-land → triple jump). Same timing semantics as `rollout`; note the visible dust puff additionally requires speed (forwardVel > 16), so `dustless` means frame-perfect timing, not "no puff appeared". Attaches to the open attempt (`jumps_total` / `jumps_dustless`) |
| `attempt_completed` | `attempt_id, session_id, kind, course_id, star_id, course_name, star_name, segment_id, segment_name, strat_tag, anchor_type, outcome, outcome_detail, igt_frames, igt, rta_frames, rta, rollouts_total, rollouts_dustless, jumps_total, jumps_dustless` | Derived: an attempt just closed (success / reset / death / hard_reset / abandoned). `kind`: `"star"` or `"segment"`. Segment attempts: `igt_frames/igt` are null, `rta` is the formatted RTA time, `course_id/star_id/course_name/star_name` are null. Legacy payloads (pre-segment) have no `kind` field — treat absence as `"star"`. |
| `target_set` | star: `course_id, star_id, strat_tag?` · segment: `kind: "segment", segment_id` | User explicitly set the practice target. The star payload carries NO `kind` — intentional, so historical and new star payloads decode identically. Consumer rule: payloads without `kind` = star. |
| `target_changed` | `kind, course_id, star_id, strat_tag` **or** `kind, segment_id, segment_name` | Practice target moved (auto-follows last valid grab, set by command, or moved/CLEARED by the projector). Same kind-aware shape as `target_set`. May clear to no target — `kind:"star"` with `course_id:null` — when Mario leaves a star's course or a segment arms (active-star/segment exclusivity); consumers that highlight the active target must handle the null-course case. |
| `strat_set` | `course_id, star_id, strat_tag` | Star's active strategy set without moving the target; future closures for that star attribute to it |
| `attempt_cleared` | `attempt_id, reason` | Attempt tombstoned; `reason` is always present, may be null (triggers full re-projection; `attempts_invalidated` follows) |
| `attempt_restored` | `attempt_id` | Tombstone undone (triggers full re-projection; `attempts_invalidated` follows) |
| `pb_saved` | `course_id, star_id, segment_id, strat_tag, timer_mode, frames, attempt_id` | Personal best recorded. Segment PBs: `course_id`/`star_id` null, `segment_id` set, `timer_mode` always `"rta"`. |
| `pb_undone` | `course_id, star_id, segment_id, strat_tag, timer_mode, frames, attempt_id, restored_frames, restored_attempt_id` | The current PB save was deleted; the previous save (if any) is current again — `restored_*` null when none remains |
| `data_wiped` | `kind, course_id, star_id, segment_id, session_id` | History wiped: `kind` `"star"`/`"segment"`/`"all"`, `session_id` null = every session. Applied retroactively on replay; attempts after the wipe accumulate fresh (`attempts_invalidated` follows) |
| `session_started` | `session_id, label?` | New session opened (server start or `/api/session/new`) |
| `attempts_invalidated` | _(none)_ | Full re-projection ran — consumers must refetch `/api/session` |
| `emulator_connected` | _(none)_ | Attached to PJ64 process |
| `emulator_disconnected` | _(none)_ | Lost PJ64 process |
| `stage_changed` | `course_id, level, area, in_stage` | **Broadcast-only — never journaled.** The quick-select context the player is standing in. `in_stage` is true only for the 15 main courses (1–15) — STAR mode (`course_id` set). Castle Inside (level 6) is SEGMENT mode: `in_stage: false`, `course_id: null`, but `area` (1 lobby / 2 upstairs / 3 basement) selects which subarea's segments the banner offers. Bowser courses, secret-star areas, the castle grounds/courtyard and the arenas all report `in_stage: false` with no segment context. |

**Attempt outcomes:** `success`, `reset`, `death`, `hard_reset`, `abandoned`. `death` and `reset` count toward the default failure rate. `abandoned` (level changed before a grab) and discarded no-op resets (where `mario_acted: false`) never count toward the failure rate. Old journal entries without the `mario_acted` key default to acted (counted as real resets). Three automatic discards never produce attempt rows at all: reset/load closures arriving after ≥5 s of pause (`paused_frames_before` ≥ 150 — AFK, not practice); for attempts opened by an `acted_tracking` anchor, ANY non-success closure with no `mario_acted` event during the attempt (no behavior = garbage); and attempts OPENED while Mario was in a castle hub level (castle movement, never a star attempt — `CASTLE_LEVELS` in addresses.py). Successes always count.

**Strategies:** Strategy names are remembered per star — switching the target star loads that star's own last-used strategy, not the previous star's. Known strategies for a star = everything registered via target-setting plus every tag appearing in that star's attempt history. The session view surfaces them in `strategies` (map of `"course_id:star_id"` → list) and `last_strat_by_star` (map → last used), and per-section `strategies` / `last_strat` fields.

**Session view payload** (`GET /api/session`) top-level fields include `scope` (`"session"` or `"lifetime"`) and `sessions` (array of all sessions, newest-first, each with `id`, `attempts`, `started_utc`, `ended_utc`). Each star section additionally carries a `timeline` object: `{max_frames, max_display, max_is_success, points:[{frames, igt, outcome, attempt_id}]}`. The axis maximum (`max_frames`) is the longest successful attempt (or the longest attempt overall when `max_is_success` is false, i.e. no successes yet). Points are lifetime — they may exceed `max_frames` on the x-axis.

**Timelines:** Each star section renders a strat map — every success, reset, and death plotted at its IGT position along a shared axis. Extending marker kinds requires two changes: one row in `TIMELINE_OUTCOMES` (`tracking/views.py`) to define the outcome key and color, and one row in `MARKERS` (`ui/components/timeline.js`) to define the SVG shape. Everything else (axis, tooltip, projection) is derived automatically from those two registries.

**Progress graph:** Each star section also plots completion time over time (gold = explicitly saved PBs). Nodes are clickable: clicking one reveals that attempt's row in the list below (expanding past the pagination fold if needed), scrolls to it with a brief highlight, and — when the attempt has a saved replay file on disk (`HEAD /api/replay/saved/{id}` succeeds) — auto-opens its replay player as if ▶ had been pressed.

## HTTP API

All endpoints are under `/api`. JSON in, JSON out.

| Endpoint | Description |
|---|---|
| `GET /api/session?clock=igt\|rta[&scope=session\|lifetime]` | Full session view: target, attempts per star, stat chips, PBs, catalog; `scope=session` (default) shows only the active session, `scope=lifetime` aggregates all sessions; star sections are ordered newest-activity-first, the target's section is always present (pinned active star), and each section carries markers_by_strat (per-strategy timeline annotations) and progress (per-session completion-time points with is_pb flags per clock) |
| `POST /api/session/new` `{label?}` | Close the current session and open a new one |
| `POST /api/session/continue` `{session_id}` | Resume a previously ended session; new attempts land there |
| `GET/POST /api/pause` `{paused}` → `{paused, reason}` | Manual pause/resume. `reason`: `manual` = user-pressed — poller stops (no events, no journal rows), replay discards, movement does NOT resume; `afk` = idle gate (read-only, shown for visibility) — replay discards but detectors keep watching, and any input resumes instantly. Manual outranks afk; resume self-heals detector state (fresh establishing pair) |
| `DELETE /api/session/{id}` | Hard-delete a session and all its data (409 on the active session; PBs survive; clears recorded in the deleted session revert their targets on re-projection) |
| `GET /api/segments` | List all segment definitions (id, name, enabled, triggers, guards). 503 in degraded mode. |
| `POST /api/segments` `{name, start_triggers, end_triggers, guards?}` | Create a new definition; validated against the trigger vocabulary (unknown type or missing required param → 409). Triggers a full re-projection — new definitions retroactively surface every past occurrence already in the journal. |
| `PUT /api/segments/{id}` `{name?, enabled?, start_triggers?, end_triggers?, guards?}` | Partial update (merged with the stored definition before validation). Triggers re-projection. Disabled definitions stay targetable. |
| `DELETE /api/segments/{id}` | Delete a definition and cascade-delete its PBs. Triggers re-projection. 404 if not found. |
| `GET /api/segments/vocab` | Trigger vocabulary for the builder GUI: `{triggers, guards, levels, castle_areas, courses, stars}`; each trigger/guard carries a sentence `template` ("{to} {to_subarea} coming from {from} {from_subarea}") the builder renders. A param schema may carry `enum` (restrict the choices — `area_enter`'s level offers only the castle-region hubs) and `only_when` (`{param, equals}` — render a param only when a sibling param equals a value, e.g. a castle subarea selector appears only for Castle Inside). Always 200 (no db dependency). |
| `POST /api/target` `{course_id, star_id, strat_tag?}` **or** `{kind: "segment", segment_id}` | Set the practice target. Star targets: legacy shape works (`kind` defaults to `"star"`). Segment targets: `kind: "segment"` + `segment_id`. 404 if the segment id is not in the definition list. |
| `POST /api/strat` `{course_id, star_id, strat_tag?}` | Set a star's active strategy without changing the practice target (null clears) |
| `POST /api/attempts/{id}/clear` `{reason?}` | Tombstone an attempt (triggers re-projection) |
| `POST /api/attempts/{id}/restore` | Undo a tombstone (triggers re-projection) |
| `POST /api/pb` `{attempt_id, timer_mode}` | Save a personal best from a success attempt |
| `POST /api/pb/undo` `{attempt_id, timer_mode}` | Undo the attempt's PB save (409 unless it is the **current** PB) — the previous save becomes current again |
| `POST /api/wipe` `{kind, course_id?, star_id?, segment_id?, scope?}` | Wipe history. `kind`: `"star"` (needs course+star), `"segment"` (needs segment_id), `"all"`. `scope`: `"session"` (default, the active session) or `"lifetime"`. Removes the scoped attempts and the PBs saved from them (lifetime star/segment wipes drop that key's PBs entirely; lifetime `"all"` factory-resets history — all events, sessions and PBs). Markers, strategies, stat menu and segment definitions always survive. |
| `GET /api/stats/registry` | List all available stat definitions with keys, labels, and default params |
| `PUT /api/statmenu` `{selections: [{key, params}]}` | Persist the stat chip selection |
| `PUT /api/markers` `{course_id, star_id, strat_tag?, markers: [{frames, label}]}` **or** `{segment_id, strat_tag?, markers: [{frames, label}]}` | Replace the timeline annotation markers for one star+strategy or segment+strategy (max 30; labels 1–60 chars trimmed; replace-the-list, no per-marker ids). `segment_id` XOR `course_id+star_id` — supplying both → 409. Note: segment strat tags are settable via `target_set`/`strat_set` events but the practice-page strat dropdown is star-only in v1. |
| `GET /api/links/{course_id}/{star_id}` | External links for a star (Ukikipedia, etc.) |

**Segments:** A segment is a timed stretch defined by a start trigger (any-of list) and an end trigger (any-of list), with optional context guards. Ten built-in segments are seeded on first run (LBLJ, MIPS Clip, Lakitu Skip, BitS Entry, BitDW/BitFS/BitS Pipe Entry, Bowser 1/2/3); all are editable. The builder GUI lives on the Segments tab — it is 100% vocabulary-driven (`GET /api/segments/vocab` supplies types, param schemas, and level/area enums). Segment attempts are **RTA-only** (`igt_frames` is always null); they share the full attempt machinery (outcomes, timeline, PBs, stats, markers, progress). Definitions are retroactive: creating or editing a definition via `POST/PUT /api/segments` triggers a full re-projection so every past occurrence in the journal surfaces immediately. Disabled definitions stay targetable for history review. Segment attempt ids are offset from star attempt ids by `10^10 × def_id` — stable across rebuilds and unique per definition. While a segment is armed, it pins to the top of the practice page (most recent arm wins); the pin is sticky — it persists after a disarm until another segment arms; the practice target is unaffected. **A reset during an armed segment records the failure and immediately re-arms — each reset is one attempt** (Usamune respawns at the level's last entrance, which equals the segment's start position; live-gate amendment 2026-06-12). This applies to PLAYER actions only — involuntary section resets (level/area loads, walk-through doors) are classified as load echoes and touch nothing; menu warps count as player actions (the pause streak before the anchor is the discriminator). Replay clip spans equal the attempt's `started_utc → ended_utc` trigger boundaries ± padding.

- **Routes tab** — build an ordered route of stars/segments (with "complete K
  of N" group steps), see per-step and cumulative success rates, and
  import/export a route as copy-pastable JSON to share. (Practice-focus and the
  full-game run timer arrive in later phases.)

**Error taxonomy:** `404` = no such attempt; `409` = attempt exists but is not valid for the
operation (bad timer mode, already cleared, non-success outcome, missing clock, or — for
`/api/pb/undo` — not the current PB);
`503` = database unavailable (server is running in broadcast-only mode).

> `GET /api/pb` is intentionally absent in phase 1 — current PBs are included in the
> `/api/session` response.

### Replay

While the server runs it records the PJ64 window (DWM shared-surface
capture — modern window capture sees frozen content for PJ64's D3D8, and
GDI stalls on its window lock) plus game audio (loopback of the endpoint
hosting PJ64's audio session) into `data/replay_buffer/` (scratch, wiped on
startup). Video encoding runs in an `ffmpeg` subprocess when ffmpeg is on
PATH — recommended; the in-process fallback encoder stutters under load
(why: docs/architecture.md → Replay capture). Retention defaults to the
whole session; a hard disk cap (default 20 GB) evicts oldest footage
regardless. Both storage limits are adjustable live from the UI — click
the recording dot in the header (shows usage as `rec · 38 min ·
1.2/20 GB`); changes persist to `data/replay_settings.json` and apply
immediately. Saved replays under `replays/` are kept forever and never
evicted — and they stay *watchable* forever: viewing an attempt whose
footage has left the buffer (later session, evicted, restart) transparently
serves the saved file instead, so a saved PB replays in any future session
(switch the UI to lifetime scope to reach old attempts' ▶ buttons). The
attempt id in the filename is the only link — rename the `attempt_NNNN_`
prefix and the tracker no longer finds it (reorganizing folders is fine).
PJ64 must run windowed (exclusive fullscreen cannot be captured).

- `GET  /api/replay/status` — `{enabled, recording, idle, window_found, audio_mode, encoder, buffer_start_utc, buffer_end_utc, disk_bytes, retention_s, max_buffer_bytes}`
- `GET  /api/replay/settings` — `{retention_s, max_buffer_bytes, pre_pad_s, post_pad_s, save_root, saved_bytes}`
- `PUT  /api/replay/settings` — body `{retention_s|null, max_buffer_bytes, pre_pad_s?, post_pad_s?}` (null retention = whole session; omitted pads = unchanged); persists + applies immediately (shrinking evicts oldest footage now); 409 outside 60 s–24 h / 1 GiB–1 TiB / pads 0–10 s
- `POST /api/attempts/{id}/replay` — cut (or reuse) the attempt's clip → `{clip_url, duration_s, truncated, fps, game_fps, source, saved_path}` (fps = encoded rate; game_fps = 30 fps SM64 logic, the frame-step unit; `source` is `buffer` or `saved`; `saved_path` non-null whenever a saved file exists). Falls back to the saved file when the buffer can't produce the clip; clips saved before 2026-06-12 lack a metadata sidecar → `duration_s` null, `truncated` false
- `GET  /api/replay/clips/{name}` — the MP4 (supports HTTP Range; scrubs smoothly)
- `GET  /api/replay/saved/{attempt_id}` — a SAVED attempt's MP4 (same Range support); 404 when that attempt has no saved file
- `POST /api/attempts/{id}/replay/save` — copy to `replays/<YYYY-MM-DD>/session_<N>/<slug>.mp4` plus a `.json` metadata sidecar → `{path, truncated}`. Idempotent: an already-saved attempt returns its existing file (delete it in Explorer first to re-save with new padding)

Errors follow the API taxonomy: 404 unknown attempt/clip, 409 no footage /
span too short, 503 db unavailable. Clips span the whole attempt plus
padding (defaults 3 s before the anchor, 2 s after the closing event;
adjustable 0–10 s in the settings panel); `truncated` means the buffer no
longer covered part of that span. When no player input is detected for
longer than the padding window (pre+post, floor 3 s), the recorder
discards new footage instead of retaining it — `idle: true` in status, an
honest coverage hole — and resumes instantly on input, a savestate load /
practice reset, or a level entry. The segment straddling the resume is
kept, so a 0 s pre-pad clip still opens exactly at the attempt anchor.

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
| `tools/hunt_value.py` | Find where a displayed number lives (exact-value search; ±2-frame tolerance — for timers, not small indexes) |
| `tools/hunt_exact.py` | Snapshot-diff hunt for small indexes (label game states, exact u16 match, repeated label kills counters) |
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
- Bowser-stage fight-ending grabs all emit `key_grabbed`: Bowser 1/2 keys (star-dance actions in arenas 30/33) and the B3 grand star (`ACT_JUMBO_STAR_CUTSCENE` in arena 34, live-verified 2026-06-12). The grand star never emits `star_collected` — it is not a collectable star.
