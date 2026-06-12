# Segment Events — Composed Practice Targets

**Date:** 2026-06-11 · **Status:** Approved (brainstorm) · **Next:** implementation plan

## Problem

The tracker only understands star grabs, but much of practice is *segments*:
timed stretches defined by a start event and an end event, not by a star.
Examples the user practices today with no tracking support: LBLJ, MIPS Clip,
Lakitu Skip, upstairs-door BLJs into BitS, "enter the pipe" in BitDW/BitFS/BitS
(16-star never collects those stars), and the three Bowser fights (ending on
key grabs / the grand star). We need to compose new trackable "events" from
sequences of primitive events, practice them as first-class targets, and make
new segments configurable in the UI without code changes.

Reference point: the [fmichea SM64 LiveSplit autosplitter](https://github.com/fmichea/sm64-livesplit-autosplitter)
solves split detection with a small vocabulary of primitives (level entry/exit,
fade-outs, key-door animations, pipe entry, star thresholds) composed by a
declarative config. This design adapts that idea to our event-sourced
architecture.

## Decisions (user-approved)

| # | Question | Decision |
|---|---|---|
| 1 | Segment shape | **Two anchors + guards**: start trigger → end trigger, optional context guards. No intermediate checkpoints (every known segment is uniquely identified by its endpoints). Amended: start/end are **any-of lists** of triggers. |
| 2 | Integration depth | **First-class practice targets**: segments get the full attempt machinery — anchored attempts, outcome taxonomy, timeline, PBs, stats, markers, progress. Target identity becomes kind-aware (star OR segment). |
| 3 | Timing | **RTA frames**: `rta_frames = end.frame − start_frame` (gGlobalTimer delta, 30 fps). `igt_frames` is NULL for segment attempts. Same M:SS.ff display format. |
| 4 | Configuration | **Builder GUI from day one**: definitions live in the DB, composed in the UI from the trigger vocabulary. Built-in segments ship as seeded, editable rows. |
| 5 | Retry semantics | **Re-arm on start trigger**: re-firing the start trigger restarts the open attempt's timer without recording a failure. Failures are recorded only on practice_reset / state_loaded / death / game_reset. Foreign level changes disarm silently (no row). |
| 6 | Architecture | **Primitives in detectors, composition in projection**: detectors journal only facts; the segment matcher runs in the tracking layer, parameterized by DB definitions. Segment attempts are derived and rebuilt by re-projection — **defining a segment retroactively surfaces every past occurrence already in the journal**. |

## New primitive events (journaled facts)

| Event | Payload | Source | Notes |
|---|---|---|---|
| `area_changed` | `{level, from, to}` | **NEW memory read: `gCurrAreaIndex`** (address VERIFY + live gate) | Detector mirrors `level.py`'s last-EMITTED discipline: establishing event on first pair, corrective events after attach gaps, journal never runs stale. Castle lobby/basement/upstairs are AREAS of one level id — this read is what makes door-scoped segments possible. |
| `warp_entered` | `{level, area, action}` | Edge on already-sampled `mario_action` into warp/pipe actions (action ids VERIFY) | End anchor for pipe/funnel segments. Chosen over `level_changed` because pipe-touch is the community-comparable timing moment (level edge adds constant fade time). |
| `key_grabbed` | `{level, which}` | Existing `STAR_GRAB_ACTIONS` edge while `curr_level` is a Bowser arena | **Fixes a latent bug**: `addresses.py` documents these actions fire for "a star (or key)" and `star_grab.py` has no key handling — today a key grab likely emits a misattributed `star_collected` (stale `last_completed_*`). `star_grab.py` gets the inverse guard. VERIFY live what the game writes on key grabs. |
| `spawned` | `{level}` | Edge into Mario's spawn/pipe-exit action (action id VERIFY) | Start anchor for Lakitu Skip ("gain control on Castle Grounds"). |

All new addresses/action ids land in `memory/addresses.py` with source comments,
marked VERIFY until they pass `tools/verify_addresses.py` with the human
(domain rule 1).

## Trigger vocabulary (ONE registry)

A new registry module (pattern: `stats/registry.py`) defines one row per
trigger type: key, human label, param schema (with enum sources: level names,
area names, course/star names), and a match function over (journal event,
projection context). The registry drives three things — matcher, API
validation, and the vocab endpoint that renders the builder GUI. Adding a
trigger type = one registry row.

**Trigger types v1:**

- `level_enter(to, from?)` — matches `level_changed`
- `level_exit(from, to?)` — matches `level_changed`
- `area_enter(level, area)` — matches `area_changed`
- `warp_entered(level)` — matches `warp_entered`
- `key_grabbed(level?)` — matches `key_grabbed`
- `star_grabbed(course?, star?)` — matches `star_collected`
- `spawned(level?)` — matches `spawned`
- `attempt_anchor(level)` — matches `practice_reset` OR `state_loaded` while
  projection's tracked level equals the param. **Why it exists:** a Usamune
  L-reset reloads the SAME level — no `level_changed` edge — so an in-level
  segment starting only on `level_enter` would close on the first practice
  reset and never re-arm. In-level seeds use
  `start: any-of [level_enter(X), attempt_anchor(X)]`, working in both full
  runs and the savestate/L-reset practice loop.

**Guards v1** (predicates over projection context at arm time):

- `prev_level(level)` — the level tracked before the start trigger fired
- `star_count_min(n)` / `star_count_max(n)` — deterministic from the journal
  by adding `num_stars` to `star_collected` payloads going forward; historical
  events without the field conservatively FAIL the guard. (None of the v1
  seeds need guards; the plumbing is one predicate list.)

## Data model

- **`segment_defs`** (new table, migration in `storage/db.py` MIGRATIONS):
  `id, name, enabled, start_triggers JSON, end_triggers JSON, guards JSON,
  created_at`. JSON validated against the vocabulary registry at the API
  boundary.
- **`attempts`**: new nullable `segment_id` column (ALTER TABLE migration +
  `_ATTEMPT_COLS` + `_attempt_params`, per the established fan-out recipe).
  Segment attempts: `course_id/star_id` NULL, time in existing `rta_frames`,
  `igt_frames` NULL, `strat_tag` supported (LBLJ has multiple setups).
- **Target identity**: generalized to kind-aware — `{kind: "star", course_id,
  star_id}` | `{kind: "segment", segment_id}`, plus `strat_tag`. Flows through
  `target_set`/`target_changed`, persistence, and `views.py` section identity.
- **PBs**: PB table keyed for segments too; `timer_mode: "rta"`. Deleting a
  definition cascade-deletes its PBs (unlike session deletion, where PBs
  survive: a deleted definition has nothing for a PB to refer to).

## Matcher semantics

One FSM per enabled definition (`IDLE ⇄ ARMED`), living in
`tracking/projection.py` beside star attempts. Same code path live and during
re-projection — parity by construction. Input: journal events + projection
context only (no wall clock, no snapshots) — deterministic replay.

| On (while ARMED unless noted) | Action |
|---|---|
| start trigger matches + guards pass (IDLE) | ARM; `start_frame` = event frame |
| start trigger matches again | **Re-arm**: update `start_frame`; no row |
| end trigger matches | **Success**: record attempt (`rta_frames = end.frame − start_frame`), broadcast `attempt_completed`, target auto-follows the segment (same rule as star grabs) |
| `practice_reset` / `state_loaded` | Close as `reset` AND **re-arm the same segment at the anchor frame** (practice-loop continuation — Usamune respawns at the level's last entrance, which is the segment's start position; live-gate amendment 2026-06-12); AFK discard applies (paused_frames_before ≥ 150 → no row, but segment still re-arms). The segment never stops being armed — the UI chip stays lit; no `segment_armed`/`segment_disarmed` notices are emitted (attempt boundary, not a state change). **Same-frame anchor = level-load echo: ignored entirely** (Usamune resets IGT on every level load; the anchor detector fires a synthetic reset on the same global-timer frame as the entry that armed the segment; a real player reset always arrives later). |
| `death` | Close as `death` |
| `game_reset` | Close as `hard_reset` |
| `level_changed` matching neither start nor end | **Silent disarm** — no row ("never reached a failure condition") |
| `area_changed` | Never disarms (castle-interior wandering is part of these segments) |
| timer anomaly (rta would be < 0) | success: discard + disarm; failure closures: record with rta_frames=None (game_reset's boot-range frame always lands here) |
| definition edited while armed (live) | FSM resets to IDLE; re-projection re-derives everything anyway |

**Ordering rule (one event, two roles):** for each event the matcher processes
closures (success/failure) BEFORE arming, and guards are re-evaluated on every
arm and re-arm. Canonical case: with "BitDW Pipe Entry" armed, a
`practice_reset` both closes the open attempt as `reset` AND matches
`attempt_anchor(BitDW)` — the attempt closes, then a fresh one arms
immediately. This is what makes the L-reset practice loop produce one attempt
per reset, mirroring star-attempt re-anchoring.

- **All enabled definitions are matched all the time** — detection is not
  gated on the active target. A full run logs MIPS, LBLJ, etc. automatically.
- **Coexistence with star attempts** (accepted v1): practicing "BitDW pipe
  entry", the pipe's `level_changed` closes the open STAR attempt as
  `abandoned` (existing behavior, excluded from failure rates) while the
  segment records `success`. BitDW's star section accumulates abandoned rows
  during pipe practice; revisit only if it annoys in practice.
- **Live feedback**: broadcast-only (never journaled) `segment_armed` /
  `segment_disarmed {segment_id, frame}` so the UI can show "timer running".

## Lifecycle

Definition CRUD → bump → **full re-projection** (existing
`attempts_invalidated` flow). Create = retroactive history materializes;
edit = history reinterpreted under new rules; delete = its attempts and PBs
drop. A stored definition referencing an unknown trigger type (code rollback)
is skipped with a logged warning and surfaced as broken/disabled in the UI —
never crashes projection.

## API (error taxonomy per `server/api.py` docstring)

| Endpoint | Behavior |
|---|---|
| `GET /api/segments` | List definitions (+ broken flag) |
| `POST /api/segments` | Create `{name, start_triggers, end_triggers, guards?, enabled?}`; validates against vocabulary (409 on unknown type/params, per the codebase's ValueError→409 taxonomy — deviation recorded in the plan); re-projects |
| `PUT /api/segments/{id}` | Edit; re-projects |
| `DELETE /api/segments/{id}` | Delete + cascade PBs; re-projects |
| `GET /api/segments/vocab` | Trigger/guard types, param schemas, level/area/course enums — straight from the registry; the builder GUI is 100% data-driven |
| `POST /api/target` | Extended: `{kind: "star"\|"segment", course_id?, star_id?, segment_id?, strat_tag?}` (kind defaults to `star` for backward compatibility) |

WS changes: `attempt_completed` gains `segment_id`/`segment_name`/`kind`;
new broadcast-only `segment_armed`/`segment_disarmed`. README event table and
endpoint table updated (consumer-facing surface).

## UI

- **`ui/components/segments.js`** — segments panel: definition list
  (enable toggle, set-target, edit, delete) + builder form. Form renders
  trigger dropdowns and param fields from `GET /api/segments/vocab`; any-of
  via "+ add alternate trigger"; live plain-English summary sentence; save
  notes that history recomputes automatically.
- **Practice page** — a segment target renders as a section with the same
  anatomy as a star section (stat chips, timeline on an RTA axis, markers,
  progress graph, PB gold). Section identity in `views.py` generalizes from
  `(course_id, star_id)` to the kind-aware key. Armed indicator from
  `segment_armed`.

## Seeded definitions (ordinary editable rows, created by migration)

| Name | Start (any-of) | End (any-of) |
|---|---|---|
| LBLJ | level_enter(Castle Inside, from=Castle Grounds) | level_enter(BitDW) |
| MIPS Clip | level_exit(HMC, to=Castle Inside) | level_enter(DDD) |
| Lakitu Skip | spawned(Castle Grounds) | level_enter(Castle Inside) |
| BitS Entry | area_enter(Castle Inside, upstairs area) | level_enter(BitS) |
| BitDW Pipe Entry | level_enter(BitDW) · attempt_anchor(BitDW) | warp_entered(BitDW) |
| BitFS Pipe Entry | level_enter(BitFS) · attempt_anchor(BitFS) | warp_entered(BitFS) |
| BitS Pipe Entry | level_enter(BitS) · attempt_anchor(BitS) | warp_entered(BitS) |
| Bowser 1 | level_enter(B1 arena) · attempt_anchor(B1 arena) | key_grabbed(B1 arena) |
| Bowser 2 | level_enter(B2 arena) · attempt_anchor(B2 arena) | key_grabbed(B2 arena) |
| Bowser 3 | level_enter(B3 arena) · attempt_anchor(B3 arena) | key_grabbed(level=34) — grand star via ACT_JUMBO_STAR_CUTSCENE [^b3] |

[^b3]: Amended at the 2026-06-12 live gate: the grand star enters ACT_JUMBO_STAR_CUTSCENE (0x1909), never a star-dance action — star_grabbed was unreachable. numStars unchanged (stayed 17), gLastCompleted* untouched, no star_collected ever fired.

## Testing

- **Matcher** (`tests/test_segments.py`): pure journal-replay tests — per-seed
  success; re-arm; silent disarm; each failure closure; AFK discard;
  negative-time discard; live-vs-replay equivalence; unknown-trigger skip;
  guard pass/fail incl. missing `num_stars` conservative-fail.
- **Detectors**: `test_area.py` mirrors `test_level.py` (establishing +
  corrective events); warp/key/spawn edge tests via the `snap(**overrides)`
  fixture; **regression test that `star_grab` no longer misattributes key
  grabs**.
- **Storage/API**: migration test, CRUD round-trip, cascade-delete,
  validation 409s, vocab endpoint shape, kind-aware target.
- **Live gate** (with the human, before merge): `gCurrAreaIndex` address +
  castle area ids; warp action ids per pipe/funnel; spawn action id; what the
  game writes to `last_completed_*` on key grabs; B3 grand-star attribution.

## VERIFY items (blocking merge, not design)

1. `gCurrAreaIndex` address and the castle's lobby/basement/upstairs area ids.
2. Warp/pipe action ids (BitDW/BitFS pipes, BitS funnel) and the three
   Bowser arena level ids.
3. Spawn/pipe-exit action id on Castle Grounds.
4. Key-grab behavior of `last_completed_course/star` and current `star_grab`
   output on a key grab.
5. Bowser 3 grand-star course/star attribution.

## Out of scope (YAGNI, revisit on demand)

- Multi-step sequences with intermediate checkpoints (no known segment needs
  them; the model extends if one ever does).
- IGT timing for segments; per-segment timer_mode display.
- Backfilling `num_stars` onto historical `star_collected` events.
- Suppressing abandoned star rows during pipe practice.
- A live on-screen running timer (beyond the armed indicator).
- Dust-trick fan-out for segments (rollout/jump counts attach to attempts
  generically already; nothing segment-specific needed).

## Module touchpoints

| Change | File |
|---|---|
| `gCurrAreaIndex`, warp/spawn action ids, arena level ids | `memory/addresses.py` (+VERIFY) |
| `area` snapshot field | `core/snapshot.py` (defaulted field) |
| `area_changed`, `warp_entered`, `key_grabbed`, `spawned` detectors | `detectors/area.py`, `detectors/warp.py`, `detectors/key.py`, `detectors/spawn.py` (+ `star_grab.py` key guard) |
| Trigger vocabulary registry | `tracking/segments.py` (new; registry + matcher FSM) |
| Matcher wiring, kind-aware target | `tracking/projection.py`, `tracking/service.py`, `tracking/views.py` |
| `segment_defs` table, `attempts.segment_id`, PB keying | `storage/db.py` (MIGRATIONS + `_ATTEMPT_COLS` + `_attempt_params`) |
| Segments CRUD + vocab + target kind | `server/api.py` |
| Builder GUI + practice-page generalization | `ui/components/segments.js` (new), `ui/components/practice.js`, `ui/store.js`, `ui/api.js` |
| Detector wiring (resets before grabs ordering preserved) | `main.py` |
| Event/endpoint docs | `README.md`; module map in `CLAUDE.md` |
