# SM64 Trainer — API reference

The live event feed, HTTP API, replay endpoints, and behavior notes. New to
the project? Start with the [README](../README.md). Developing here? Read
`CLAUDE.md` and `docs/architecture.md` first.

## Admin endpoints (localhost only)

| Endpoint | Description |
|---|---|
| `POST /api/uilog` `{surface, ...}` → `{recorded}` | **What the browser just PAINTED**, not an event. `surface` is `selector` (the quick-select row's cells, each `{name, active}`) or `target` (every objective card on the page, in DOM order). The server stamps the wall clock and the live game frame; `tools/what_happened.py` interleaves the result with the journal, which is what makes "the cell vanished BEFORE the level change" a readable fact. Always 200 — a body it does not recognise is dropped with `{"recorded": false}`, because an instrument that can make its subject throw is worse than none. |
| `POST /api/admin/shutdown` | Graceful shutdown — the desktop "close the other instance" takeover path. `{"shutting_down": true}` |
| `POST /api/admin/restart` | Full-process relaunch — the one-click "Restart server" button; picks up edited backend code. `{"restarting": true}` |

## Auto-update (localhost only)

The packaged exe self-updates from GitHub releases. All endpoints are inert
when running from source (`is_frozen()` false → `update_available: false`).

| Endpoint | Description |
|---|---|
| `GET /api/update/status[?force=1]` | `{current, frozen, update_available, latest, notes, html_url, skipped, writable, state, progress}`. `notes` is the release body (patch notes); `writable` is false when the exe's folder can't be written (popup offers a browser download instead) or during an active install; `state` ∈ `idle\|downloading\|installing\|error`, `progress` 0–1. `attempt`/`attempts` are the retry counters (see `/apply`) — `attempt > 1` means the install is being retried and `progress` has restarted. The check is cached per process (1 h TTL); `force=1` bypasses it (the manual "Check for updates"). |
| `POST /api/update/apply` | Begins the off-thread download → SHA-256 verify → exe swap, then fires the same full-process restart as `/api/admin/restart`. Returns `{state}` immediately; poll `/status` for `progress`. **Retries itself** on any failure (5 attempts, backoff) and only reports `state: "error"` once every attempt is spent — a POST from that state is a fresh run, which is what the popup's "Try again" sends. Refuses (`state: "error"`) if not frozen, no update, the folder isn't writable, or already in progress. |
| `POST /api/update/skip` `{version}` | Persist a skipped version to `data/update_state.json` so that release never re-notifies (a newer one still does). `{"skipped": version}`. |

A release **must** carry a `SM64Trainer.exe.sha256` asset; one without it is
never offered, so an unverified exe can never be applied. The exe swap uses
the Windows rename-a-running-exe trick and restores the backup on failure —
see `docs/architecture.md` → Self-update.

## Event schema

Every WebSocket message is a versioned envelope:

```json
{"v": 1, "seq": 412, "type": "star_collected", "frame": 456052,
 "timestamp_utc": "2026-06-10T22:14:03.512000Z",
 "payload": {"course_id": 8, "course_name": "Shifting Sand Land",
             "star_id": 1, "star_name": "Shining Atop the Pyramid",
             "already_collected": true,
             "igt_frames": 595, "igt": "0'19\"83",
             "igt_source": "result", "igt_reconstructed": false,
             "igt_timed_at": "xcam", "grab_frame": 594}}
```

- `seq`: monotonic per server run (gap = missed events)
- `frame`: game-frame stamp (30 fps), back-computed to the exact moment the
  time describes — for `star_collected` that is the **x-cam** frame (see
  `igt_timed_at`), which on a midair grab is later than `grab_frame`
- `star_id`: 0-based within the course (6 = 100-coin star)
- `igt`: Usamune's overall star time — the number shown on screen, correct
  across multi-area levels; `igt_frames` is the same in raw frames
- `igt_source`: `result` (Usamune's own stored final time — exact),
  `counter` (running overall counter, back-computed), `reconstructed` (see
  Behavior notes)
- `igt_source` **on a star** is also the legality signal: `result` means Usamune wrote a time at or after the x-cam, i.e. its `STOP` was GrabX or Xcam; `counter` means it did not, so `STOP` was Grab or None and the row is not leaderboard-legal. `counter` on a multi-area star is additionally the time spent in the SUBAREA, not the whole star — the overall counter restarts at an area warp and nothing can recover the earlier part.
- `igt_timed_at` *(star_collected only, added 2026-08-01)*: `xcam` — the time
  is the leaderboard-legal one, taken when Mario landed after the grab — or
  `grab`, the fallback for a grab that never reached a star dance (savestate
  load, level change, or the 300-frame backstop). Usamune's own `STOP` setting
  does not enter into it: the x-cam moment is derived from Mario's actions, so
  the number is legal whatever the TIMER menu says
- `grab_frame` *(star_collected only, added 2026-08-01)*: the frame Mario
  touched the star. Equal to `frame` on a ground grab, earlier on a midair one
- `published_after` / `result_writes` *(star_collected only, added
  2026-08-02)*: OBSERVATIONAL, read by nothing. How many frames past the x-cam
  this row actually went out, and every Usamune result-store write it saw as
  `[[offset, value], …]`. The row leaves as soon as Usamune's own answer
  agrees with our derivation of the same moment, so these two say how long
  that took on each real grab — the evidence for tuning it further

**A star's time may be revised once, shortly after the grab** (2026-08-01).
`star_collected` is published as soon as Usamune answers — 0-12 frames after
the x-cam, so the grab is acknowledged while the dance is still playing — and
Usamune sometimes writes a better answer later. Only a MULTI-AREA star's does:
the running counter restarts at an area warp, so Usamune's own whole-star
write, up to 41 frames after the x-cam, is the only source that knows the
time. When that write changes the answer, a `star_time_corrected` follows and
the recorded attempt is rebuilt with the new number. A consumer that keeps its
own copy of a star's time must handle it; one that re-reads `/api/session` on
`attempts_invalidated` (which a correction always triggers) needs nothing.

**Breaking change (phase 1):** `game_reset` now fires **only** on backward
timer jumps into the boot range (console reset / ROM reload). Savestate and
Usamune section-state loads that previously looked like resets now emit
`state_loaded` instead.

Other event types, same envelope:

| Type | Key payload fields | Meaning |
|---|---|---|
| `game_reset` | _(none)_ | Console reset / ROM reload (timer into boot range) |
| `practice_reset` | `igt_frames_before, mario_acted, paused_frames_before, acted_tracking, action, prev_action, frames_since_door, frames_since_dialog, warp_op, frames_since_warp_op, area, prev_area, area_load, teleport` | Usamune level reset — attempt anchor; `igt_frames_before` is the IGT the moment before the counter dropped, which is ONE LEG of the attempt rather than always the whole of it (the counter also restarts at a subarea load and at an in-level teleporter, neither of which is a retry — `tracking/projection.py::_with_carried_igt` sums the legs), and the payload also carries whether Mario entered any non-passive action since the last anchor (no-op resets where `mario_acted: false` are discarded); closures after ≥5 s of pause (paused_frames_before ≥ 150) are discarded as AFK. Also fires for **pause-warps**: a menu warp executed straight from the pause menu with the section timer still near zero has no IGT edge — it is detected by position change + pause streak and emitted one position-stable tick later, so the `area_changed` always journals first; `action` = Mario's action at the detection tick; `prev_action` = Mario's action on the PREVIOUS poll tick — the segment engine keys the door-echo clause on `prev_action`: a genuine door crossing has `prev_action` in `DOOR_ACTIONS` (inputs locked during the door animation on the prior tick); an L-reset respawning AT a door has a gameplay `prev_action` (e.g. freefall) and must be treated as a real reset. `area_load` (added 2026-08-01): TRUE when Usamune's counter zeroed because Mario went DEEPER into the level — into the SSL pyramid, the LLL volcano — rather than because he retried. An in-course area load zeroes the IGT exactly as an L-reset does, so it arrives as an anchor; the destination area is what separates them (a course always starts in area 1, and a reset's own reload settles BACK to it). The attempt projector records nothing for one: the run continues across the door. `teleport` (added 2026-08-03): TRUE when the counter zeroed because Mario took an IN-LEVEL teleporter — the CCM broken bridge, a WDW corner, an HMC toxic-maze pad. Those relocate him inside the SAME area, so no area edge exists for `area_load` to pair the zero with; the discriminator is how recently `ACT_TELEPORT_FADE_OUT` ran, NOT what `action` reads (the fade-in id lingers on the action byte for well over a hundred frames after the warp). Read the same way as `area_load` by the attempt projector, and by the segment engine as echo shape (6). `warp_op` / `frames_since_warp_op` / `area` / `prev_area` are OBSERVATIONAL and read by nothing — kept for the case `area_load` does not cover, walking back OUT of a subarea. All five, and the three readings that looked right and were not, are derived in `detectors/anchors.py`. Fall back to `action` for events journaled before `prev_action` was added; missing both fields → conservative close. `frames_since_door` = game frames since the last door action was observed (None if never seen); the segment engine treats 0–30 as a non-warp door echo (shape d) even when neither action nor prev_action carries door context. `frames_since_dialog` = game frames since the last textbox/intro-cutscene action (None if never seen); a textbox/cutscene re-initialises Usamune's IGT, so the segment engine treats 0–30 as a **dialogue echo** (shape 5) — a run never splits/resets on a textbox in any level (this is what stops the Lakitu-skip intro from firing a false reset one frame after the spawn). |
| `state_loaded` | `igt_frames_restored, mario_acted, paused_frames_before, acted_tracking, action, prev_action, frames_since_door, frames_since_dialog, warp_op, frames_since_warp_op, area, prev_area, area_load, teleport` | Savestate / Usamune section-state load — attempt anchor; same activity flag as practice_reset; same pause/activity discards as practice_reset; same `action`, `prev_action`, `frames_since_door`, and `frames_since_dialog` fields as practice_reset for symmetry |
| `mario_acted` | _(none)_ | Mario's first voluntary action since the last anchor (death actions never count); the tracking layer uses it to judge whether an attempt had any behavior |
| `death` | `cause, igt_frames, level` | Mario died; closes the open attempt as outcome "death" with the cause in outcome_detail. Causes: the death-action set (`standing`, `quicksand`, `electrocution`, `suffocation`, `on_stomach`, `on_back`, `eaten_by_bubba`, `drowning`, `water`) plus `fall` — a void-out (death barrier / pit), detected from the game's pending-warp pulse *before* the level unloads, so the death always precedes the spit-out's `level_changed` |
| `level_changed` | `from, to, from_area, igt_frames, igt, igt_source` | Level id edge; closes open attempts as abandoned. May arrive with `from == to`: the detector emits one establishing event on server start and a corrective event after attach gaps (`from` = last *emitted* level, not the previous read) so journal-derived level tracking never runs stale — don't infer "left level X" from `from != to`. `from_area` is `gCurrAreaIndex` BEFORE the edge (the Castle Inside subarea Mario left: lobby=1, upstairs=2, basement=3) so a segment trigger can scope a crossing by source area; best-effort across attach gaps. There is no `to_area` — the destination loads area 1 transiently then warps to the real area a poll later, so the settled destination subarea comes from the following `area_changed`, not the edge. Carries the `igt` trio (`igt_frames`/`igt`/`igt_source`) since 2026-08-06, from the shared `detectors/igt_clock.py` like every other displayed time, so the segment recorder can show a number on every row it draws; rows journaled before that read null and cannot be backfilled. |
| `area_changed` | `level, from, to, from_transient, igt_frames, igt, igt_source` | Castle area id edge (lobby=1, upstairs=2, basement=3 — areas of level 6). Same establishing/corrective semantics as `level_changed`: emits on server start and after attach gaps (`from` may equal `to`). `CURR_AREA` live-pinned 2026-06-12 (`0x8033BACA`). Carries the `igt` trio (`igt_frames`/`igt`/`igt_source`) since 2026-08-06, from the shared `detectors/igt_clock.py` like every other displayed time, so the segment recorder can show a number on every row it draws; rows journaled before that read null and cannot be backfilled. |
| `warp_entered` | `level, area, action, to, igt_frames, igt, igt_source` | The ENTRANCE TOUCH: the frame Mario collides with a painting, portal, hole or pipe, detected as an edge into a warp-entry action on the already-sampled `mario_action`. The community-comparable timing moment, and since 2026-08-04 the end condition for the 55 definitions that used to end on the course LOAD — measured over 140 castle entries, the `level_changed` that follows is a constant 77 frames away for a painting/portal (range 76-77) and 23 for a pipe. `to` (added 2026-08-04) is the level Mario ended up in, or `null` when the warp relocated him inside his own area (an in-level teleporter) or the fade was aborted. **The event is HELD until `to` is known and then published back-dated**: `frame` and the `igt` trio are always the TOUCH's, never the release's. It cannot be otherwise — decomp `level_trigger_warp()` writes nothing to `sWarpDest`, which `initiate_delayed_warp()` fills 77 frames later, immediately before the level unloads, so the destination is unknowable at the touch frame from any address. Rows written before 2026-08-04 carry no `to` at all; `projection.warp_destinations` recovers it on replay from the level edge that followed. The `igt` trio (added 2026-07-31, live report: BitDW "No Reds" read 0'35"90 where Usamune showed 0'35"96) is Usamune's own clock at the touch, from the shared `detectors/igt_clock.py` exactly as `star_collected`/`key_grabbed` carry it, so a segment ending here records Usamune's number instead of a `global_timer` delta; `igt_source` is always `"counter"` at a pipe (Usamune writes its result store on a star grab only). |
| `key_grabbed` | `level, which, igt_frames, igt, igt_source` | Mario grabbed a Bowser key or the B3 grand star. `which` is `"bitdw"` (Bowser 1, level 30), `"bitfs"` (Bowser 2, level 33), or `"grand"` (Bowser 3, level 34). The key detector claims all three fight-ending grabs. B3's grand star is NOT a collectable star — live-verified 2026-06-12: it enters `ACT_JUMBO_STAR_CUTSCENE` (0x1909), numStars unchanged, no star-dance action, `gLastCompleted*` untouched — so `star_collected` is unreachable and the grand star is handled here. `igt`/`igt_frames`/`igt_source` are Usamune's IGT for the fight, from the same shared clock as `star_collected` (so a segment ending on this grab matches Usamune's displayed time exactly, not a wall-frame delta — added 2026-06-12). |
| `star_time_corrected` | `course_id, star_id, grab_frame, igt_frames, igt, igt_source, igt_reconstructed` | Usamune revised the time of the `star_collected` immediately before it (see above): the row it names keeps its frame, its moment and its identity, and only the NUMBER changes. `frame` is that grab's x-cam. Emitted at most once per grab, ~1.5 s after it, and only when the revised answer differs from the published one — in practice only on a multi-area star. Journaled; the projector folds it back into the grab's own payload (`tracking/projection.py::time_corrections`), so a consumer reading attempts rather than raw events sees one number and never this event. |
| `spawned` | `level, kind, igt_frames, igt, igt_source` | Mario gained control at a spawn-in. `kind: "intro"` = leaving `ACT_INTRO_CUTSCENE` (file-select spawn on Castle Grounds — Lakitu Skip start anchor, control begins when the cutscene ends); `kind: "spawn"` = edge into a SPAWN_* action (non-intro spawn-ins). Carries the `igt` trio (`igt_frames`/`igt`/`igt_source`) since 2026-08-06, from the shared `detectors/igt_clock.py` like every other displayed time, so the segment recorder can show a number on every row it draws; rows journaled before that read null and cannot be backfilled. |
| `segment_armed` | `segment_id, name` | A segment definition's start trigger fired — its RTA timer is now running. **Broadcast-only — never journaled.** Consumers should treat as a live hint only; a plain `/api/session` refresh self-heals the armed state from the projector. |
| `segment_disarmed` | `segment_id, name` | A segment's timer was stopped without recording an attempt (foreign level change, a warp/savestate that landed outside the segment's start position — moving, not practicing — or silent disarm after a success). **Broadcast-only — never journaled.** |
| `segment_progress` | `segment_id, name, progress, total` | An armed multi-step segment reached its next waypoint (or rewound to the first one on a practice reset). `progress` is how many steps are consumed; the route has `total + 1` of them, and `armed_detail.steps` on `/api/session` names each. **Broadcast-only — never journaled.** This is the ONLY signal a cursor move produces: it journals no event of its own, and the position events that cause it are not refresh triggers, so a consumer that ignores this will show a step the player has already passed. |
| `rollout` | `dustless, frames_late, landing_frames, level` | Dive→rollout executed. `landing_frames` = visible dive-slide frames (always ≥ 1: the landing transition takes one frame before inputs are read — decomp-verified); `frames_late = landing_frames - 1`; `dustless: true` ⟺ frame-perfect (`frames_late == 0`). Rollouts whose slide entry wasn't observed (attach race, savestate mid-slide) emit nothing. Attaches to the open attempt (`rollouts_total` / `rollouts_dustless`) |
| `jump` | `dustless, frames_late, landing_frames, kind, level` | Chained jump executed: `kind: "double"` (jump-land → double jump) or `"triple"` (double-jump-land → triple jump). Same timing semantics as `rollout`; note the visible dust puff additionally requires speed (forwardVel > 16), so `dustless` means frame-perfect timing, not "no puff appeared". Attaches to the open attempt (`jumps_total` / `jumps_dustless`) |
| `attempt_completed` | `attempt_id, session_id, kind, course_id, star_id, course_name, star_name, segment_id, segment_name, strat_tag, anchor_type, outcome, outcome_detail, igt_frames, igt, rta_frames, rta, rollouts_total, rollouts_dustless, jumps_total, jumps_dustless` | Derived: an attempt just closed (success / reset / death / hard_reset / abandoned). `kind`: `"star"` or `"segment"`. Segment attempts: `igt_frames/igt` are null, `rta` is the formatted RTA time, `course_id/star_id/course_name/star_name` are null. Legacy payloads (pre-segment) have no `kind` field — treat absence as `"star"`. |
| `target_set` | star: `course_id, star_id, strat_tag?` · segment: `kind: "segment", segment_id` | User explicitly set the practice target. The star payload carries NO `kind` — intentional, so historical and new star payloads decode identically. Consumer rule: payloads without `kind` = star. |
| `target_changed` | `kind, course_id, star_id, strat_tag` **or** `kind, segment_id, segment_name` | Practice target moved (auto-follows last valid grab, set by command, or moved/CLEARED by the projector). Same kind-aware shape as `target_set`. May clear to no target — `kind:"star"` with `course_id:null` — when Mario leaves a star's course or a segment arms (active-star/segment exclusivity); consumers that highlight the active target must handle the null-course case. |
| `strat_set` | `course_id, star_id, strat_tag` | Star's active strategy set without moving the target; future closures for that star attribute to it |
| `attempt_cleared` | `attempt_id, reason` | Attempt tombstoned; `reason` is always present, may be null (triggers full re-projection; `attempts_invalidated` follows) |
| `attempt_restored` | `attempt_id` | Tombstone undone (triggers full re-projection; `attempts_invalidated` follows) |
| `attempt_strat_set` | `attempt_id, strat_tag` | One attempt reclassified onto another strategy (`strat_tag` null = no strategy); last write wins, so re-picking the previous one is the undo (triggers full re-projection; `attempts_invalidated` follows). When the attempt is its entity's newest non-cleared row and the tag is non-null, a `strat_set` follows — the active strategy tracks the newest attempt |
| `pb_saved` | `course_id, star_id, segment_id, strat_tag, timer_mode, frames, attempt_id` | Personal best recorded. Segment PBs: `course_id`/`star_id` null, `segment_id` set, `timer_mode` always `"rta"`. |
| `pb_undone` | `course_id, star_id, segment_id, strat_tag, timer_mode, frames, attempt_id, restored_frames, restored_attempt_id` | The current PB save was deleted; the previous save (if any) is current again — `restored_*` null when none remains |
| `data_wiped` | `kind, course_id, star_id, segment_id, session_id` | History wiped: `kind` `"star"`/`"segment"`/`"all"`, `session_id` null = every session. Applied retroactively on replay; attempts after the wipe accumulate fresh (`attempts_invalidated` follows) |
| `attempts_pruned` | `attempt_ids` | The startup prune deleted attempts a previous session left UNLABELLED — no star and no segment at all, or one but no strategy — because there is no way to look at such a row later and know what it was. Journaled once per server start, right after `session_started`, and applied retroactively on replay like `data_wiped` (`attempts_invalidated` follows). Carries explicit ids, never a rule to re-evaluate. An attempt with a saved PB or a saved replay clip is never listed |
| `session_started` | `session_id, label?` | New session opened (server start or `/api/session/new`) |
| `attempts_invalidated` | _(none)_ | Full re-projection ran — consumers must refetch `/api/session`. **Broadcast-only — never journaled** (since 2026-08-02), along with `attempt_completed` and `target_changed`: each restates something already stored, so a journal row was pure noise. Dropping all three replays byte-identical over the real journal and takes it from 23,063 rows to 19,179 |
| `emulator_connected` | _(none)_ | Attached to PJ64 process |
| `emulator_disconnected` | _(none)_ | Lost PJ64 process |
| `stage_changed` | `course_id, level, area, mode` | **Broadcast-only — never journaled.** The quick-select context the player is standing in; `mode` selects the banner: `"stars"` (a main course 1–15, `course_id` set), `"bowser_course"` (BitDW/BitFS/BitS = levels 17/19/21 → course 16/17/18, `course_id` set; banner offers the reds star + the level's no-reds pipe-entry segment), `"arena"` (a Bowser 1/2/3 fight arena = levels 30/33/34, `course_id: null`; banner offers + auto-selects the single fight segment), `"castle"` (Castle Inside / level 6, `course_id: null`; `area` 1 lobby / 2 upstairs / 3 basement selects which subarea's segments the banner offers), or `null` (secret-star areas, caps, hubs — no banner). |

**Attempt outcomes:** `success`, `reset`, `death`, `hard_reset`, `abandoned`. `death` and `reset` count toward the default failure rate. `abandoned` (level changed before a grab) and discarded no-op resets (where `mario_acted: false`) never count toward the failure rate. Old journal entries without the `mario_acted` key default to acted (counted as real resets). Three automatic discards never produce attempt rows at all: reset/load closures arriving after ≥5 s of pause (`paused_frames_before` ≥ 150 — AFK, not practice); for attempts opened by an `acted_tracking` anchor, ANY non-success closure with no `mario_acted` event during the attempt (no behavior = garbage); and attempts OPENED while Mario was in a castle hub level (castle movement, never a star attempt — `CASTLE_LEVELS` in addresses.py). Successes always count.

**Strategies:** Strategy names are remembered per star — switching the target star loads that star's own last-used strategy, not the previous star's. Known strategies for a star = everything registered via target-setting plus every tag appearing in that star's attempt history. The session view surfaces them in `strategies` (map of `"course_id:star_id"` → list) and `last_strat_by_star` (map → last used), and per-section `strategies` / `last_strat` fields.

**Session view payload** (`GET /api/session`) top-level fields include `scope` (`"session"` or `"lifetime"`) and `sessions` (array of all sessions, newest-first, each with `id`, `attempts`, `started_utc`, `ended_utc`). Each star section additionally carries a `timeline` object: `{max_frames, max_display, max_is_success, points:[{frames, igt, outcome, attempt_id}]}`. The axis maximum (`max_frames`) is the longest successful attempt (or the longest attempt overall when `max_is_success` is false, i.e. no successes yet). Points follow the requested `scope` (session view plots only that session's attempts; 2026-07-24 — previously always lifetime) and may exceed `max_frames` on the x-axis. Every star AND segment section also carries `time_filter: {min_frames, max_frames, is_default}` — the section's *effective* validity bounds (after the implicit 0.5 s default is filled in), driving the header's `⏱` chip.

**Caveats on a saved time** (`tracking/caveats.py`, added 2026-08-01): three
surfaces show a PB beside a rank, and a PB can fail to mean what that rank
implies. One derivation answers it for all of them and the payload carries a
single key — `null`, or one of `grab_timed` / `old_clock` / `unattributed`,
worst first (`CAVEAT_SEVERITY`). It appears in three places, all reading the
entity's strategy-blind CURRENT PB:

- each star/segment section's `pb.<clock>.caveat` — the practice card's PB tag;
- top-level `caveat_by_star`, `{"<course>:<star>": key}` — the quick-select
  banner. Keyed over a WIDER set than `rank_by_star`, which lists only stars
  with an active strategy: the most important caveat is that the PB has no
  strategy at all, exactly the case that map omits. Absent = no caveat;
- each `segment_targets[].caveat` — the same, for a segment cell (rule 11).

What each means: `unattributed` — the PB carries no `strat_tag`, so no strategy
can ever claim it and a rank drawn beside it would be a floor contradicting the
time itself; this is the ONE key that suppresses the ladder floor client-side.
`old_clock` — the attempt was timed by a wall-frame delta AND its closing event
type is one that would carry Usamune's IGT today (both clauses matter: most
delta-timed rows are delta forever and stay perfectly comparable).
`grab_timed` — a star timed at the grab rather than the x-cam, i.e. true of
every star row recorded before 2026-08-01 and of a fresh one whose x-cam wait
aborted. `ui/components/marks.js` owns the wording and the badge;
`tests/test_cross_language_parity.py` pins the two key sets equal.

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
| `GET /api/segments` | List all segment definitions (id, name, enabled, triggers, guards), each stamped with `origin: {key, label, region, region_label, source}` — the castle place its start rules resolve to (or the user's override), `source` is `"derived"` or `"override"` — and `is_hundred_coin_engine: bool` (spec 2026-07-28-multi-step-segments): true when the definition's own sequence includes grabbing a main course's 100-coin star (`tracking/segments.py::hundred_coin_entity`), which the client uses to exclude it from the target picker's course grid and the route step candidate list — that family's completed attempts attribute directly to the STAR now, never to the segment, so it is no longer a separately pickable thing (still listed here so the Segments library/editor can still open and edit the definition). 503 in degraded mode. |
| `POST /api/segments` `{name, start_triggers, end_triggers, guards?, waypoints?, category?, match_mode?}` | Create a new definition; validated against the trigger vocabulary (unknown type or missing required param → 409). `waypoints` is an ordered list of middle any-of clause-sets (`[]`, the default = a plain start/end pair). `match_mode` (`"loose"` default \| `"strict"`) picks the armed-branch matcher: loose stays armed through star grabs, key grabs and level changes until the end trigger fires; strict cancels the moment anything happens that isn't the next expected step. `category` is a free-text library-grouping label (`null` = ungrouped) with no effect on matching. Triggers a full re-projection — new definitions retroactively surface every past occurrence already in the journal. |
| `PUT /api/segments/{id}` `{name?, enabled?, start_triggers?, end_triggers?, guards?, waypoints?, category?, match_mode?}` | Partial update (merged with the stored definition before validation). `waypoints: []` and `category: null` are explicit clears, distinct from omitting the key entirely (`exclude_unset`). Triggers re-projection. Disabled definitions stay targetable. |
| `DELETE /api/segments/{id}` | Delete a definition and cascade-delete its PBs. Triggers re-projection. 404 if not found. |
| `POST /api/segments/{id}/reset` | Restore a **seeded** definition to its bundled defaults and clear its `seed_dirty` flag. 404 for a user-created segment (no `seed_key`) or one whose `seed_key` has no matching row in the bundled seed. Triggers re-projection. |
| `POST /api/segments/{id}/origin` `{origin: "6:3" \| null}` | Pin a segment's library category, or clear it (`origin: null`) back to the value derived from its start rules. The node must exist in `vocab().origins` — the `{key: null}` "Anywhere" group is excluded from that allowlist, so a segment can be cleared back to Auto but never force-pinned INTO "Anywhere" (400 otherwise). Stored in the `origin_overrides` ui_state KV, not the definition row, so correcting a label never flips `seed_dirty`. 404 for an unknown segment id. Broadcast-only (`origins_changed`), never journaled — a second open window picks up the change on its next visit to the Segments tab (the window that made the change updates immediately). |
| `POST /api/segments/{id}/split` `{mid, first_name, second_name}` | Break an **existing** definition into two new ones meeting at `mid` (an any-of clause-set, same shape as `start_triggers`/`end_triggers` — typically the segment's own single waypoint, promoted to a full stop: `tracking/segments.py::split_definition`, spec 2026-07-28-multi-step-segments Task 17/18). **Non-destructive**: `{id}` is left completely untouched — definitions arm in parallel, so the whole and both halves can all record on the same play — and both halves are inserted as brand-new, user-created rows (`seed_key: null`, so `reconcile_defaults` never refreshes or deletes them; `guards`/`default_strat`/`match_mode`/`enabled` are inherited from the original onto both). Response `{ok, first_id, second_id, warnings: {first: [...], second: [...]}}` — `warnings` is `tracking/lint.py` findings for each new half against the real post-split library, **informational only** (see `POST /api/segments/lint` below); `unfireable` can never appear there, since this endpoint already refuses on it (below). 404 for an unknown `{id}`. 409 when a produced half would be **unfireable** (its start and end could be satisfied by the same event) or `{id}` carries more than one waypoint (folding several into the single shared `mid` would silently drop the rest), or either name is blank — same domain-refusal convention every other segment endpoint uses. **A 409 writes nothing**: both halves are validated in full before either row is inserted, so a refusal never leaves one half behind (it did until 2026-07-29 — the first half was committed before the second was validated). Triggers a full re-projection. |
| `GET /api/segments/vocab` | Trigger vocabulary for the builder GUI: `{triggers, guards, levels, castle_areas, courses, stars, match_modes, ...}`; each trigger/guard carries a sentence `template` ("{to} {to_subarea} coming from {from} {from_subarea}") the builder renders. A param schema may carry `enum` (restrict the choices — `area_enter`'s level offers only the castle-region hubs) and `only_when` (`{param, equals}` — render a param only when a sibling param equals a value, e.g. a castle subarea selector appears only for Castle Inside). `match_modes` is `[{key, label, description}, ...]`, loose first (the default) — the Builder's Matching control (`POST/PUT /api/segments`'s `match_mode` field above) renders its `<option>`s and descriptions from this list only, never a JS copy. Always 200 (no db dependency). |
| `GET /api/segments/timeline?limit=200&view=steps\|all&after_id=<id>` | The recent journal, as rows a human can point at to define a segment from what they just did (`tracking/eventlabel.py::label_event`, Task 10). Response `{rows: [{id, frame, type, label, wall_time_utc, igt_frames}]}`, oldest first (newest last) — ordered by the journal's own `id`, **never** `frame`: `frame` is the raw game-frame counter and is not chronological (it runs backward across every practice reset and session boundary — measured against the real journal, 2026-07-28: 469 backward jumps). Default-view membership rule (server/api.py's `_TIMELINE_STEP_TYPES`/`_is_default_timeline_row`): a type shows by default iff it is ever a seeded definition's ONLY start/end route — "sole" is per-definition (an OR-alternative clause doesn't count, since the alternative already covers that definition). `view=steps` (default) shows `level_changed`/`star_collected`/`warp_entered`/`key_grabbed` (~95% of what the 84 seeded definitions' start/end clauses actually use, and never excluded regardless of this rule) **plus** `area_changed` — high-volume (1,678 of 18,656 events) but the sole route for 5 seeded definitions (4 castle-region endings — BoB/BBH/Bowser 2 → Basement/Upstairs, SL → Basement — and 1 start, BitS Entry) — **plus** `spawned` rows where payload `kind == "intro"` (Lakitu Skip's only start; a fresh-file spawn, `detectors/spawn.py`), narrowed from the raw type because an ordinary respawn (`kind == "spawn"`, 1,136 of the type's 1,164 real events) is never any definition's sole route. `view=all` adds the rest — `practice_reset`/`state_loaded` (the `attempt_anchor` pair — always an OR-alternative behind `level_enter`, an F1-retry echo, never a sole route), `spawned` rows with `kind == "spawn"`, and `game_reset` — none of those is any seeded definition's only route in or out. `limit` caps at 500 (422 above it) and is applied to the most recent rows in the selected view. 422 on an unrecognised `view`. 503 in degraded mode. **`after_id` is the live tail** (2026-08-05): it drops every row at or below that id, so a surface already holding the list asks only for what has happened since — one localhost round trip per event instead of the whole list, and no second copy of `label_event` in the browser. A broadcast event carries `seq`, never the journal `id` these rows are picked by, so a live client has to come back for the id regardless. Nothing newer than the id given is an EMPTY `rows`, never the last row over again. **`igt_frames`** is the number Usamune had on screen at that moment, surfaced from the event's own payload and null for a type that carries none — FRAMES and not the payload's pre-formatted `igt` string, because the browser renders it through `ui/format.js::fmtIgtShort` like every other time on screen and the two forms differ (the display form drops an empty minutes field). **Every type this endpoint can draw now carries one** (live report 2026-08-06: *"some events have the timer next to them, most don't? I would expect the timer for all of them"*) — `level_changed`, `area_changed` and `spawned` each stamp the shared `detectors/igt_clock.py` since that report, joining the four that already did. Forward-only: rows journaled before it read null, and the raw counter at those frames was never recorded. **A LEVEL LOAD DRAWS ONE ROW.** The load walks the area byte through two or three real `area_changed` edges before it settles, so entering a course used to list "Moved to another part of Shifting Sand Land" three times over; the endpoint drops a load's own settling edges and promotes the `spawned` that names the arrival, labelled "Started Shifting Sand Land" (`tracking/eventlabel.py::level_entry_rows`, windowed by the measured `LEVEL_LOAD_TAIL_FRAMES`). A load that never reached a spawn — he walked back out inside the window — keeps its LAST area edge instead, so no arrival is ever lost. Nothing is dropped from the journal; this is a display rule and the matcher, the clock and the position walk all still read every row. |
| `POST /api/segments/backtest` `{definition: {...same shape as POST /api/segments...}, replaces: id \| null}` | Replay an **unsaved** candidate definition against the real event journal and report what it would have done — find out *before* you save, rather than live mid-run the way every other SM64 autosplitter works (`tracking/backtest.py`). `definition` validates exactly like `POST /api/segments`: a domain-invalid shape (bad trigger type, an empty trigger list, ...) is 409; a malformed body (wrong types, a missing required field) is a plain 422, rejected before the handler runs. Response: `{fires, attempts, unclosed, arms, pb_before, pb_after, gained, lost}` — `fires` is the count of non-cleared successes; `attempts` is every attempt the candidate would have recorded, any outcome (`{id, anchor_frame, rta_frames, outcome, outcome_detail, started_utc, ended_utc, cleared, cleared_reason}`); `unclosed` is every arm still open when the journal ended (`{frame, progress, total, deadline_frame, reason}`); `arms` is how many times the candidate's start trigger armed over the whole replay — the field that tells "never armed" (`arms == 0`) apart from "armed repeatedly and never closed" (`arms > 0, fires == 0`), which `fires`/`unclosed` alone cannot: both those read `fires=0, unclosed=[]` on their own. `replaces` names the segment definition this candidate would replace, if any (404 if it names an unknown id); when supplied, `pb_before`/`pb_after` are each definition's fastest non-cleared success and `gained`/`lost` are attempt counts the candidate has that the replaced definition doesn't, and vice versa. Omit `replaces` (or pass `null`) for a brand-new definition — `pb_before` comes back `null` and `gained`/`lost` come back `0`, since there is nothing to diff against. Read-only: no journal entry, no re-projection. |
| `GET /api/segments/synthesize?ids=<id>,<id>[,<id>...]` | Turn the picked `GET /api/segments/timeline` row ids into the clauses a new segment would be defined by, plus a suggested `name` and a plain-English `start_sentence`/`end_sentence` for each end — the hinge behind "record what I just did" (`tracking/synthesize.py`, Task 12, wired up for the timeline picker in Task 13). **The server SORTS the ids and that is the contract** (2026-08-05, replacing the `start_id`/`end_id` pair this took until then): the earliest is the start, the latest is the end, and each one between is a waypoint in journal order, returned as `picked: [{id, clause, sentence}]`. Chronological order is a property the events already have, so reading it off the client's click order would let a list drawn newest-first author a definition whose steps run backwards through a walk that only ever happened one way. `steps` (the walk DERIVED between the two ends, `tracking/synthesize.py::walked_steps`) ships on every answer beside it, and the caller chooses: exactly two ids leaves `picked` empty and the derived walk fills the middle — the two-click case — while a third id says the walk is not the answer. Looks the ids up directly in the journal, the same source `/segments/timeline` reads. 422 on fewer than two DISTINCT ids (so picking one moment twice is refused here, worded for what the person did, rather than as the old 409 about arming and closing on one tick) or on an id that is not a number. 404 when any id names no journal event. 409 when a picked row's type carries no synthesis rule for the role it was picked for — start, finish or step (e.g. a `practice_reset`/`state_loaded` row — its position lives in live match state, not its own payload, so it can never become a trigger clause on its own). Sentences render through the same `card_label`/`card_template` machinery the practice card's "waiting for" line uses, so a synthesized-but-unsaved clause reads in the same voice a saved one would. Read-only: no journal entry, no state change. 503 in degraded mode. |
| `POST /api/segments/merge` `{first_id, second_id, name}` | Chain two **existing** definitions into one meeting at their shared boundary, kept as a waypoint in the middle (`tracking/segments.py::merge_definitions`, spec 2026-07-28-multi-step-segments Task 17/18) — so the merged definition still requires the route to pass through the seam, not merely to begin at `first_id`'s start and end at `second_id`'s end. Declared as a literal path before `/segments/{id}` (fastapi-patterns). **Non-destructive**: both inputs survive untouched, and the merged definition is a brand-new, user-created row (`seed_key: null`). `match_mode`/`default_strat` are inherited when both inputs agree, else `"loose"`/`null` respectively (a merge always crosses at least one boundary, which is exactly `"loose"`'s own use case; a merge of two different practiced techniques has no single obvious default strategy); `guards` are dropped (a bound describing either input's own duration would misrepresent the combined span). Response `{ok, id, warnings}` — `warnings` is `tracking/lint.py` findings for the merged result against the real post-merge library, **informational only** (see `POST /api/segments/lint` below): measured against the real 84-def corpus (Task 16), 789 of 6,345 topologically-legal merge pairs come back with an `unrunnable_arm_position` finding and 6 with `unfireable` — overwhelmingly a retry-in-place trick (LBLJ, MIPS Clip) merged with an unrelated movement, which this endpoint does not refuse (refusing would block a large share of merges `merge_definitions` itself already treats as legitimate). 404 for either unknown id. 409 (the pure op's own "do not meet" refusal) when the pair shares no boundary — checked at both level AND castle-subarea resolution, since the castle interior is one level holding three subareas on a line. Triggers a full re-projection. |
| `POST /api/segments/lint` `{definition: {...same shape as POST /api/segments...}, segment_id: id \| null}` | Author-time findings for a **not-yet-saved** definition (`tracking/lint.py`, Task 15/16) — advisory, checked before Save, never at runtime (a saved definition must keep matching whatever the Usamune warp menu invents forever). Declared before `/segments/{id}` (fastapi-patterns). Response `{warnings: [{rule, severity, message}, ...]}`, `severity` `"error"` (the definition cannot work — the editor disables Save) or `"warning"` (a real risk, advisory only). Runs the four rules in `tracking/lint.py` (`unfireable`, `unrunnable_arm_position` — both error; `start_looser_than_waypoint`, `duplicate` — both warning) against `service.segment_defs`, the **real** current library — never `[]` (that would silently drop `duplicate` with no symptom). `segment_id` names the definition being edited, if any, so `duplicate`'s self-exclusion (by id) excludes the definition's own on-disk row rather than reporting an unmodified edit as a duplicate of itself; omit (or null) for a brand-new definition. Unlike `POST /segments/backtest`, this does **not** run `validate_definition` and never 409s on a domain-invalid shape (an unknown trigger type, a clause missing a required param) — the editor calls it on every edit, including in-progress states a form passes through before it is complete, and every rule — **and everything the rules call** — tolerates that. The second half is not decoration: two rules reach `segments.can_run_from`, whose `fires_from` helper kept a bare `trig["to"]` and returned 500 on an ordinary half-filled form until 2026-07-29. Read-only: no journal entry, no state change. 503 in degraded mode. |
| `GET /api/target/ranks` | Lazy per-entity rank map for the practice-target picker: `{entity_key: {rank, division, strat}}` (`entity_key` = `"star:<course>:<star>"` / `"segment:<id>"`, `ranks/standards.py::entity_key`'s shape — never the session view's `"<course>:<star>"` composite). Per entity, grades the BEST-scoring strategy's own ladder (ties break alphabetically), not the active strategy (`rank_by_star`) and not the entity's best-possible ladder (`entity_rank` on the session view) — a third, deliberately different "which rank" answer, see `tracking/views.py::build_entity_ranks`. An entity absent from the map was never graded on any strategy — read the absence as "no rank yet", not as an error. Not part of the session view (rebuilt on every WebSocket event, and average rank modes are O(history) per strategy per entity) — fetched on demand when the picker modal opens. 503 in degraded mode. |
| `GET /api/target/strategies?entity=<key>` | Step-3 picker payload for ONE entity: `{entity, kind, current, allow_blank, strategies: [{name, rank, division, score, pb_display}]}`. `strategies` is the SAME merged list (registered ∪ observed-on-attempts ∪ rank-standard) the practice card's own dropdown offers — this endpoint exists because the header used to read a narrower, registered-only map and hide strategies the card offered. Each entry is graded independently through the section banner's own chain (`grading_basis` → `_graded_progress` on that strategy's ladder), so a `rank`/`division` here can never disagree with `GET /api/session`'s section banner for the same entity+strategy; a strategy with no ladder or nothing gradeable reports all three of `rank`/`division`/`score` as `null` — present as unranked, not missing. `rank`/`division` are RAW tier keys (`"Platinum"`, `"II"`) — the client, not the server, owns cap/display vocabulary. `pb_display` is already `format_igt`-formatted (`M'SS"CC`); don't reformat it. `current` is the entity's active strategy, masked to `null` when tombstoned. `allow_blank` is `false` only for a segment with a truthy `default_strat` (the same rule `stratpicker.js` enforces client-side) — the client must honour it, not treat it as a suggestion. 404 for an unparseable `entity` key or a segment id that names no known definition (see `build_entity_strategies`'s inverse-parse of `ranks/standards.py::entity_key`'s shape); 503 in degraded mode. |
| `POST /api/target` `{course_id, star_id, strat_tag?}` **or** `{kind: "segment", segment_id, strat_tag?}` | Set the practice target. Star targets: legacy shape works (`kind` defaults to `"star"`). Segment targets: `kind: "segment"` + `segment_id`. `strat_tag` distinguishes absent from explicit `null` (Pydantic's `model_fields_set`): key **omitted** leaves the entity's existing strategy untouched; key present and `null` (picking "(no strategy)" in the picker) explicitly clears it, journaled via the same `strat_set null` path as `POST /api/strat` — symmetric for stars and segments. A segment with a truthy `default_strat` can't actually reach "no strategy": a null `strat_set` falls back to the def's own default instead of clearing (`tracking/projection.py` caveat 17), so this only clears on the 10 legacy tricks and user-created segments. A truthy `strat_tag` sets it, as before. 404 if the segment id is not in the definition list. **409 if the player is not standing in front of it** — you may only practice what is in front of you (`tracking/practicable.py`), and the detail names where the entity actually lives ("that one is in Whomp's Fortress"). Both kinds resolve to a world node: a star's course, a segment's `start_origin`. Two cases always succeed — an entity whose definition names no place, and any pick made while the player's own place is unknown (no live stage, or a stage naming no level: the emulator is detached, so reviewing with the game closed is not read-only) — as does re-picking what is ALREADY the target, since a strategy edit posts through this same endpoint. A stage whose `mode` is `null` is **not** one of those unknowns (2026-07-27): the file select, the hubs and the cap courses are real places offering nothing to practice, so a pick from one is refused like any other. |
| `POST /api/strat` `{course_id, star_id, strat_tag?}` **or** `{kind: "segment", segment_id, strat_tag?}` | Set an entity's active strategy without changing the practice target (null clears). Kind-dispatched exactly like `/api/target`; the name is also *registered*, so it stays in that entity's dropdown before any attempt exists under it. 404 if the segment id is not in the definition list. |
| `GET /api/icons` | The icon picker's grid: `icons` = bundled stems (`ui/assets/star_icons/*.png`, e.g. `"wf5"`, `"bitdw"`) + `user_icons` = uploaded `user:<file>` entries. |
| `POST /api/icons/upload?name=<filename>` | Upload a custom icon image as the **raw request body** (no multipart). Filename is slugged and kept (same name = replace); png/jpg/jpeg/webp/gif, 2 MB max (413 above). Square ~100×100 renders best. Returns `{"icon": "user:<file>"}` to pass to `POST /api/icon`. Stored in the data dir (`data/icons/`), so uploads survive app updates. |
| `GET /api/icons/file/{name}` | Serve one uploaded user icon (the `user:` stems' img src). |
| `GET /api/icons/courses` | Course portrait art: `{courses: {stem: filename}}`, globbed from `ui/assets/course_icons/` (mixed `.webp`/`.png` — the client asks for the listing rather than guessing the extension). HMC, SSL, DDD and SL are absent on purpose: those courses aren't entered through a painting, so the game has no portrait for them (the UI falls back to their star-1 icon). |
| `POST /api/icon` `{course_id, star_id, icon?}` **or** `{kind: "segment", segment_id, icon?}` | Set (or clear, `icon: null`) an entity's quick-select icon override. Kind-dispatched exactly like `/api/strat`; `icon` must be a stem from `GET /api/icons` (400 otherwise). Stored server-side (ui_state KV `icon_overrides`, surfaced on the session view); broadcasts `icons_changed` (broadcast-only, no journal entry). Overrides win over the client's star-icons display mode. |
| `POST /api/attempts/{id}/clear` `{reason?}` | Tombstone an attempt (triggers re-projection). Also **undoes any PB the attempt saved**, both clocks — a pb row carries its own frames and would keep grading a hidden run; the previous save becomes current again, exactly as `/api/pb/undo` leaves it |
| `POST /api/attempts/{id}/restore` | Undo a tombstone (triggers re-projection). Does NOT restore a PB the tombstone undid — that row is gone; re-save it with `/api/pb` |
| `POST /api/attempts/{id}/strat` `{strat_tag}` | Reclassify one recorded attempt's strategy (`null` = no strategy); journaled + re-projected, and moves any PB the attempt saved. Older rows edit history only; reclassifying the entity's NEWEST non-cleared attempt also sets the active strategy ("that run was actually X" = "X is what I'm practicing"; `null` never propagates). `POST /api/strat` sets what to practice next directly |
| `POST /api/pb` `{attempt_id, timer_mode}` | Save a personal best from a success attempt. A save only counts while its attempt is **visible**: hide the attempt and the PB stops grading, whichever way it was hidden (`/clear`, or a validity-bounds change auto-ignoring it) |
| `POST /api/pb/undo` `{attempt_id, timer_mode}` | Undo the attempt's PB save (409 unless it is the **current** PB) — the previous save becomes current again |
| `POST /api/wipe` `{kind, course_id?, star_id?, segment_id?, scope?}` | Wipe history. `kind`: `"star"` (needs course+star), `"segment"` (needs segment_id), `"all"`. `scope`: `"session"` (default, the active session) or `"lifetime"`. Removes the scoped attempts and the PBs saved from them (lifetime star/segment wipes drop that key's PBs entirely; lifetime `"all"` factory-resets history — all events, sessions and PBs). Markers, strategies, stat menu and segment definitions always survive. |
| `GET /api/stats/registry` | List all available stat definitions with keys, labels, and default params |
| `PUT /api/statmenu` `{selections: [{key, params}]}` | Persist the stat chip selection |
| `PUT /api/markers` `{course_id, star_id, strat_tag?, markers: [{frames, label}]}` **or** `{segment_id, strat_tag?, markers: [{frames, label}]}` | Replace the timeline annotation markers for one star+strategy or segment+strategy (max 30; labels 1–60 chars trimmed; replace-the-list, no per-marker ids). `segment_id` XOR `course_id+star_id` — supplying both → 409. |
| `PUT /api/stars/{course_id}/{star_id}/time-filter` `{min_frames, max_frames?}` | Override one star's validity bounds (frames, 30 fps; `min_frames: 0` = no floor, `max_frames: null`/omitted = no ceiling). Successes outside a star or segment's bounds are **auto-ignored**: recorded but flagged cleared with an `auto: below X.XXs min` / `auto: above X.XXs max` reason, excluded from stats/PBs/graphs/runs, and still visible in the hidden bucket. Default for every star and segment: min 0.5 s, no max. Triggers a full re-projection — the new bounds apply retroactively. Segments have no equivalent endpoint; they declare overrides as `min_time`/`max_time` guard rows on their definition (`PUT /api/segments/{id}`) instead. A PB saved from a run that these bounds later invalidate stops counting **while the run is hidden** and counts again if the bounds are widened — the save is not deleted, because this kind of hiding is a rule that can change (only `/clear` deletes it). |
| `DELETE /api/stars/{course_id}/{star_id}/time-filter` | Revert a star's validity bounds to the implicit defaults (0.5 s min, no max). Triggers re-projection. |
| `GET /api/links/{course_id}/{star_id}` | External links for a star (Ukikipedia, etc.) |

**Segments:** A segment is a timed stretch defined by a start trigger (any-of list) and an end trigger (any-of list), with optional context guards. Ten built-in segments are seeded on first run (LBLJ, MIPS Clip, Lakitu Skip, BitS Entry, BitDW/BitFS/BitS Pipe Entry, Bowser 1/2/3); all are editable. Editing a seeded segment sets its `seed_dirty` flag (protecting it from a future bundled-seed refresh); `POST /api/segments/{id}/reset` restores the bundled version and clears the flag — 404 on a user-created segment, which has no seed to restore. The builder GUI lives on the Segments tab — it is 100% vocabulary-driven (`GET /api/segments/vocab` supplies types, param schemas, and level/area enums). Segment attempts are **RTA-only** (`igt_frames` is always null); they share the full attempt machinery (outcomes, timeline, PBs, stats, markers, progress). Definitions are retroactive: creating or editing a definition via `POST/PUT /api/segments` triggers a full re-projection so every past occurrence in the journal surfaces immediately. Disabled definitions stay targetable for history review. Segment attempt ids are offset from star attempt ids by `10^10 × def_id` — stable across rebuilds and unique per definition. While a segment is armed, it pins to the top of the practice page (most recent arm wins); the pin is sticky — it persists after a disarm until another segment arms; the practice target is unaffected. **A reset during an armed segment records the failure and immediately re-arms — each reset is one attempt** (Usamune respawns at the level's last entrance, which equals the segment's start position; live-gate amendment 2026-06-12). This applies to PLAYER actions only — involuntary section resets (level/area loads, walk-through doors) are classified as load echoes and touch nothing; menu warps count as player actions (the pause streak before the anchor is the discriminator). Replay clip spans equal the attempt's `started_utc → ended_utc` trigger boundaries ± padding.

**Routes:** the Routes tab builds an ordered route of stars/segments (with
"complete K of N" group steps), showing per-step and cumulative success rates,
and import/export of a route as copy-pastable JSON to share. Picking an active
route focuses the Practice tab on that route's stars/segments, in route order.
Like segments, editing a seeded route sets `seed_dirty`; `POST
/api/routes/{route_id}/reset` restores the bundled version and clears it —
404 on a user-created route.

| Endpoint | Description |
|---|---|
| `GET /api/routes` | All routes with their raw `steps` (each step `{label?, need, candidates[]}`) |
| `POST /api/routes` `{name, steps}` | Create a route. `409` if a step names a segment that doesn't exist |
| `GET /api/routes/{route_id}` | One route resolved for display: candidate names, per-step and cumulative success rates, per-step rank, `broken` when a candidate's segment was deleted |
| `PUT /api/routes/{route_id}` `{name?, steps?, start_condition?}` | Update a route. Editing the ARMED route re-arms it and voids any in-flight run (the run's plan changed under it) |
| `DELETE /api/routes/{route_id}` | Delete a route |
| `POST /api/routes/{route_id}/reset` | Restore a **seeded** route to its bundled defaults and clear its `seed_dirty` flag. 404 for a user-created route (no `seed_key`) or one whose `seed_key` has no matching row in the bundled seed. Step candidates re-resolve to the current segment ids. |
| `GET /api/routes/{route_id}/export` | The route as portable JSON — embeds the definitions of every segment it references, so it survives an import into a db that lacks them |
| `POST /api/routes/import?dry_run=true\|false` | Import an exported route. `dry_run=true` previews the resolution (which embedded segments match existing definitions and which would be created) without writing |
| `POST /api/route/select` `{route_id}` | Set (or clear, `route_id: null`) the practice-wide **active route** — journals `route_selected {route_id, segment_ids}`, snapshotting the route's member segment ids so replay can reconstruct which route was active at any past event. This is the arm scope for segments carrying the `in_active_route` guard; it is distinct from `POST /api/run/start`, which arms a route for the full-game timer. Editing the active route's `steps` re-emits `route_selected` with a fresh member snapshot |

**Runs (full-game timer):** a run is armed by selecting a route (there is no
Start button) and its clock starts when the route's `start_condition` trigger
fires — default `reset_game` (F1) — plus the configured `start_offset_ms`.
Splits are forgiving RTA: wall-clock per step **minus paused time**, retries
roll up into the step, K-of-N steps complete without duplicates, and the run
finishes on its last step. Cleared attempts (manual or auto-ignored) are
invisible to runs. Run ids are the journal id of the starting `game_reset`,
and stored split times are offset-free.

| Endpoint | Description |
|---|---|
| `GET /api/run` | The active run view: per-step cumulative times, ± vs PB, gold splits, paused ms |
| `POST /api/run/start` `{route_id}` | Arm a route (selecting a route in the Run tab calls this). The clock waits for the start condition |
| `POST /api/run/end` | End the active run |
| `POST /api/run/pause` · `POST /api/run/resume` | Exclude paused time from splits and suspend step completions while paused |
| `POST /api/run/reset` | Abort the active run without saving it |
| `GET /api/run/history?finished_only=true\|false` | Finished (and optionally aborted) runs with their splits, for the history list and PB-progression graph |
| `GET/PUT /api/run/settings` `{start_offset_ms}` | The run start offset (default 1360 ms), persisted in `ui_state` |

Run lifecycle events `run_started`/`run_ended`/`run_paused`/`run_resumed`/
`run_reset` are journaled; `run_finished`/`run_aborted`/`run_progress` are
broadcast-only.

**Rank standards** (`/api/ranks/*` — per-strategy rank ladders, the rank mode,
and full deletion of custom strategies) are documented in the
[README](../README.md#rank-badges) rather than here.

**Error taxonomy:** `404` = no such attempt; `409` = attempt exists but is not valid for the
operation (bad timer mode, already cleared, non-success outcome, missing clock, or — for
`/api/pb/undo` — not the current PB);
`503` = database unavailable (server is running in broadcast-only mode).

> `GET /api/pb` is intentionally absent in phase 1 — current PBs are included in the
> `/api/session` response.

### Replay

While the server runs it records the PJ64 window (DWM shared-surface
capture — modern window capture sees frozen content for PJ64's D3D8, and
GDI stalls on its window lock) plus game audio — per-process WASAPI
loopback of PJ64 alone, so a clip carries the game and nothing else on the
desktop; `audio_mode` in `/api/replay/status` reads `process` when that is
live and `system` when it fell back to capturing PJ64's whole output
endpoint — into `data/replay_buffer/` (scratch, wiped on
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
- `POST /api/compilation` — start a failure compilation for a star (`{"star":{"course_id":C,"star_id":S}}`) or segment (`{"segment_id":N}`), with `x_before`/`y_after` seconds around each failure. Returns `{job_id}`.
- `GET /api/compilation/{job_id}` — poll job `{state, progress, message, result}`; `result` on done: `{path, clip_count, skipped, no_finale, finale_time}`. Output MP4 lives under the replays `compilations/` dir; open it via `POST /api/replay/reveal`.

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

## Ranks & standards

**Rank badges** appear on every attempt, section banner, progress-graph node,
and route step. A rank is graded **per strategy**: only your PB achieved with a
given strategy counts toward that strategy's rank — with no time on a strategy
yet you are *unranked* on it, and a faster PB on a different strategy is never
borrowed. (The strategy-blind "overall best PB" is still shown for display.)
A global **rank mode** (header `Rank:` picker) switches what entity-level
medals grade: `pb` (default — your saved per-strategy PB), `avg10`/`avg50`
(mean of your last 10/50 valid runs), `best10`/`best50` (mean of your 10/50
fastest valid runs ever), or `lifetime` (mean of every valid run). A valid run
is a successful, non-purged, strat-tagged attempt with a time on the display's
clock; with fewer than N runs the mean covers what exists and the banner shows
"avg of K/N". Per-attempt medals always grade their own single run. The session
view carries `rank_mode`; the banner payload carries `mode` and (in avg modes)
`basis` `{frames, display, count, window}`.
Standards are loaded from `data/rank_standards.json` (a
hand-editable local file, seeded on first run from the bundled
`rank_standards.seed.json` which is regenerated by `uv run python
tools/scrape_ranks.py`). REST/WS surface:

| Method | Path | Body / Query | Effect |
|---|---|---|---|
| `GET` | `/api/ranks/standards` | `?entity=<key>` | One entity (`star:c:s` / `segment:id`): `strategies` + tiers, plus `cutoff_videos` ({strat:{rank:url}} — fastest example per tier, auto-banded from clips + user overrides), `user_videos` (raw overrides), `videos` (primary), `seeded` (the community-seed strat names — the custom-vs-default distinction), `xcams_url` (Daily Star page), and — for a 100-COIN star only, `[]`/`{}` for everything else — `strategy_groups` (`[{label, exit_star, strategies: [{name, leaf}]}]`, the server's own resolution of which exit-star variant each strategy belongs to), `exit_variants` (`{label: star_id}`) and `exit_star_options` (`[{star_id, name, label}]`, every star the run could end on, `label` null where the community publishes no times for that ending). A client renders `strategy_groups` and never re-derives grouping: a 100-coin strategy's stored name is variant-qualified because CCM's two variants both define "Standard". Omit `entity` for all. |
| `PUT` | `/api/ranks/standards/{entity}/{strategy}/{rank}` | `{"seconds": N}` | Set one threshold. Broadcasts `rank_standards_changed`. |
| `PUT` | `/api/ranks/standards/{entity}/{strategy}/{rank}/video` | `{"url": "..."}` | Hand-attach an example video to one cutoff (survives seed bumps). |
| `DELETE` | `/api/ranks/standards/{entity}/{strategy}/{rank}/video` | — | Remove a hand-attached cutoff video. |
| `POST` | `/api/ranks/standards/{entity}` | `{"strategy": "...", "exit_star": N?}` | Create a new strategy for an entity. Returns `{ok, strategy}` — **use the returned name** for the threshold/video PUTs that follow: with `exit_star` (0-5, a 100-coin star's ending) the server QUALIFIES the name with that variant's label, so what it stored differs from what you posted. An `exit_star` no variant covers mints one, named from the star it ends on — that is the "define your own 100-coin route" path. |
| `DELETE` | `/api/ranks/standards/{entity}/{strategy}` | `?purge=true` (optional) | Without `purge`: clear the strategy's standards (its column persists while the strat is registered or on past attempts). With `purge=true`: fully DELETE a CUSTOM strategy — standards + registration removed, a tombstone hides it from every dropdown; past attempts keep their times, and re-creating the same name restores them (undo). `409` on community-seeded strategies and on a segment's own default strategy (the seeded castle movements' "Standard" — purging it would leave a card whose picker offers nothing). |
| `POST` | `/api/ranks/standards/{entity}/reset` | — | Restore entity to seed defaults. |
| `PUT` | `/api/ranks/mode` | `{"mode": "pb"\|"avg10"\|"avg50"\|"best10"\|"best50"\|"lifetime"}` | Set the global rank mode (409 on unknown). Broadcasts `rank_mode_changed`. |

`rank_standards_changed` and `rank_mode_changed` are broadcast-only (no

## MARELO — the overall rating

**MARELO** rolls every rankable entity's score into one 0-100 rating per
*scope* — Overall, a course, or any route in the library. The practice
target's active route IS the scope: `GET /api/marelo` with no `scope` answers
for whatever you're currently practicing, so there is no second control to
keep in sync. An unknown scope `404`s rather than silently falling back to
Overall — a stale route id in a client must read as gone, not as a different
rating.

| Method | Path | Body / Query | Effect |
|---|---|---|---|
| `GET` | `/api/marelo/scopes` | — | Every pickable scope (`overall` first, then routes, then courses) + `active` (the current default scope id). |
| `GET` | `/api/marelo` | `?scope=<id>` (optional) | `{scope_id, label, marelo, mastery, coverage, tier, division, next_division_at, division_progress, n, practiced, entities:[{key,label,score,tier,division,gain,excluded}], celebration}` for one scope (defaults to the active route, else Overall). `404` for an unknown scope. `celebration` is the SCOPE's own rank-up and keeps its never-raise-on-GET contract: only `/api/marelo/ack` raises a watermark, so an un-acked rise keeps reporting on every fetch. There is no per-entity celebration in this payload — a star's or segment's own rank-up is performed live by the rank banner climbing in the UI (task 0012, 2026-07-27), so nothing is held server-side for a client to show later. |
| `GET` | `/api/marelo/summary` | — | `{chips:[{scope_id,label,tier,division,marelo,n,practiced}]}` — one lean aggregate per scope (`overall`, then every route whose `category` starts with `Main Categories`, then the active scope if not already present; capped at 6), for an always-visible chip row. Same scoring path as `/api/marelo` but never touches a celebration watermark (scope OR entity), so opening it can't seed or silently swallow a rank-up. |
| `GET` | `/api/marelo/history` | `?scope=<id>` (optional) | `{scope_id, points:[{utc,marelo,tier,division,practiced}]}` — the scope's MARELO recomputed chronologically from the practice journal against CURRENT standards (a seed bump reshapes the past). |
| `POST` | `/api/marelo/exclude` | `{"entity": "<key>", "excluded": true\|false}` | Opt an entity out of (or back into) every scope's numerator AND denominator. |
| `GET` | `/api/marelo/exclusions` | — | `{excluded:[<key>…]}` — the raw exclusion set, for a surface that needs ONE entity's state without scoring a scope (the strategy modal's "include in ranking" tick, which can open on an entity that is in no scope yet). |
| `POST` | `/api/marelo/ack` | `{"scope": "<id>", "key": N}` | Raise the SCOPE's celebration watermark once the UI has actually shown a rank-up (never on fetch — see the `marelo` payload's `celebration`). A missing `scope` is `400`; so is the `{entity, key}` form this accepted until task 0012 (2026-07-27), rather than a silent no-op, so an out-of-date client surfaces instead of hiding. |

`marelo_changed` is broadcast-only (no journal entry).

In `pb` grading mode `/api/marelo/history`'s series replays SAVED PBs (`pbs`
table), not every success — the same times the rating grades. Undoing a PB
removes its point.

## Compare (side-by-side video)

**Compare** puts your run side-by-side with a reference video: the left
stage plays a past attempt by id through the same replay pipeline as the
Replay tab, the right stage plays an imported comparison video — pulled
from YouTube (via `yt-dlp`), a local file, or a browser upload, normalized
to a local mp4 and cached — and one frame-accurate transport drives every
stage in lockstep (play/pause, ±1-frame step, jump-to-start). Per-clip in/out
sync-point sliders align the two videos; the shared transport itself has no
drag-scrub. With nothing saved yet, Compare **suggests** the rank standard's
example video for the active strategy (auto-selected, offered as a one-click
**▸ Load** — nothing downloads or plays until you click it). REST surface:

| Method | Path | Body / Query | Effect |
|---|---|---|---|
| `GET` | `/api/compare/view` | `?entity=<key>&strat=<tag>` | Saved comparisons for one (entity, strat) with servable `clip_url`s, the resolved auto-pick, and a rank-standard suggestion when nothing is saved yet |
| `POST` | `/api/compare/import` | `{entity_key, strat, name, source_kind: "youtube"\|"file", source_ref}` | Start a background import job (download/copy → ffmpeg-normalize → content-addressed cache) → `{job_id}` |
| `GET` | `/api/compare/import/{job_id}` | — | Poll job status: `{state: "running"\|"done"\|"error", progress, message, comparison?}` |
| `POST` | `/api/compare/upload` | `?entity_key=&strat=&name=&filename=`, raw video bytes as the request body | Start an import job from a browser-uploaded file → `{job_id}` |
| `PUT` | `/api/compare/videos/{id}` | `{name?, in_frame?, out_frame?, touch?}` | Edit a saved comparison (rename, set sync in/out frames, or bump `last_used_utc`). Broadcasts `comparisons_changed`. |
| `DELETE` | `/api/compare/videos/{id}` | — | Delete a saved comparison; evicts its cached file once no other comparison shares it. Broadcasts `comparisons_changed`. |
| `GET` | `/api/compare/cache/{name}` | — | The cached mp4 (Range/206) |

Import completion is not broadcast — the initiating client polls the job
instead, since imports are a focused single-client action; `comparisons_changed`
fires only from edits/deletes, so other clients pick up those.

## Climb tuning inspector (source checkouts only)

`/ui/tune.html` is a Godot-Inspector-style rig for the rank-up climb: it renders
the real `RankBanner` inside the real card chrome, plays a climb from any rank to
any higher one, and generates a control for every row in `ui/climbtuning.js`.

| Route | Purpose |
|---|---|
| `POST /api/tuning/{registry}` `{values: {<tunable>: number \| string}}` | Rewrite the `value:` fields of `src/sm64_events/ui/climbtuning.js` so the tuned numbers BECOME the shipped defaults — no runtime overlay, and the change lands in `git diff` ready to commit. Validates each key against that file's own `min`/`max`/`options` rather than a copy of them, so it can never drift from the registry; it can only replace a `value:` that already exists, never add a key or reach another field. Returns `{written, values, path}`. `{registry}` is a key of `TUNING_REGISTRIES` (an allowlist, never a path — the endpoint WRITES, so a caller must not be able to name the file it lands in); `climb` is the only one today and **404** names the known set. Adding a second tunable surface is one row there — see `.claude/skills/tuning-demo`. **409 when frozen** — the packaged app has no repo to write to. |

The settings string the page exports carries EVERY tunable, not just the ones
that differ from the defaults, so it still means the same thing after a default
is codified.

## Landmarks — which door, which pole, which bob-omb

A **landmark** is the specific object a `moment_reached` happened to, named by
where the game SPAWNED it (`level:area:behaviour:x,y,z`). The pool slot it
occupies is NOT part of the name and cannot be: three castle-basement doors
held slots 3/2/0, then 38/42/44 after an area reload, then 3/2/0 again. A
`kind:<behaviour>` key names a whole family game-wide instead, so naming
"Pole" once names every pole in the game.

| Endpoint | Purpose |
|---|---|
| `GET /api/landmarks` | `{names: {key: name}}` — the whole catalogue, kinds and instances in one map. One map rather than two endpoints because a row's label resolves from both. 503 in degraded mode. |
| `POST /api/landmark` | `{key, name}` — name it, or send a blank `name` to erase. Returns the refreshed `{names}`. Writes `seed_dirty=1`, so the next corpus refresh never overwrites a name the user typed. 422 on an empty key. |

**A name applies BACKWARDS, and that is a property of where it is resolved.**
No row ever stores the name it was drawn with — `GET /api/segments/timeline`
labels each row against the catalogue at fetch time — so renaming a landmark
re-labels every row it ever appeared in, with nothing to migrate. Timeline rows
carry `landmark` (the key), `landmark_kind`, `landmark_name` and
`landmark_placed` for the rename control; `landmark_placed` is false for an
object the game created mid-play (Mario, a star popping out of a box), which
has no spawn point of its own and therefore shares one key with every other of
its kind — those must not be offered a name.

**Shipped names live in the corpus** (`src/sm64_events/data/defaults.seed.json`,
authored in `tools/corpus_landmarks.py`) and reconcile at startup like segments
and routes, so a door identified once is identified for every install.


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
- `key_grabbed` and `warp_entered` are timed at the TOUCH, not at an x-cam. For a pipe there is no x-cam; for the grand star, whether Usamune's `STOP` moves the number is unmeasured, and `ACT_JUMBO_STAR_CUTSCENE` has no fall/dance pair to derive one from. So a Bowser-3 time is not known to be leaderboard-legal the way a star time now is.
- A star attempt records WHICH MOMENT its time came from (`Attempt.timed_at`, `"xcam"` | `"grab"`; `null` for a segment, a failure, or a key/pipe closure). Stamped from the closing event's own `igt_timed_at`, so it re-derives on every reproject; the ABSENCE of that payload key means `"grab"`, since it did not exist before 2026-08-01. Bowser 3's grand star is deliberately left `null` rather than claimed as either — see the limitation above.
- Star times recorded before 2026-08-01 are grab-frame times, which under `STOP` of Grab or GrabX is a different quantity from the x-cam time and is not leaderboard-legal. They cannot be repaired: the journal keeps no frames after the grab, so the derivation has nothing to run on. Forward-only fix.
