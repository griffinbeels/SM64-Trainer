# Architecture & Domain Knowledge

CLAUDE.md is the index; this file holds only knowledge that has no better
home. Facts that belong to one module are documented IN that module —
follow the pointers instead of duplicating here.

## Data flow

```
Project64 1.6 process (Windows)
      │  ReadProcessMemory, ~60 Hz poll (game logic runs at 30 fps)
┌─────▼──────────┐   GameSnapshot     ┌──────────────┐   Event
│ memory/pj64.py  │ ─────────────────▶ │ detectors/*  │ ──────────▶ ─────────────────────────────────────────┐
│ attach, RDRAM   │  core/snapshot.py  │ (prev,curr)→ │                                                      │
│ scan, endian    │                    │ events       │                                                      │
└────────▲───────┘                    └──────────────┘                                                      │
┌────────┴───────┐                                                                                           ▼
│ memory/        │                                                                             ┌─────────────────────────┐
│ addresses.py   │  ← single registry: addresses, actions, names                              │ TrackerService           │
└────────────────┘                                                                             │ broadcast → journal →   │
                                                                                               │ project → attempt_      │
                                                                                               │ completed derived event │
                                                                                               └──────┬────────┬─────────┘
                                                                                                      │        │
                                                                                           ┌──────────▼──┐  ┌──▼────────────┐
                                                                                           │ storage/    │  │ server/        │
                                                                                           │ tracker.db  │  │ broadcaster +  │
                                                                                           │ (journal,   │  │ FastAPI + WS   │
                                                                                           │ attempts,   │  │ /api/* REST    │
                                                                                           │ sessions,   │  └──────┬────────┘
                                                                                           │ pbs)        │         │
                                                                                           └─────────────┘  ui/index.html,
                                                                                                            overlays,
                                                                                                            consumers
```

Polling at 60 Hz against 30 fps logic observes every game frame. Detectors
hold no I/O; the poller holds no game logic; `main.py` wires everything.
`TrackerService` is the event sink: it broadcasts first (liveness never gated
on the db), then journals, then feeds the projector; derived `attempt_completed`
events re-enter the same pipeline (the projector ignores derived types — no
recursion possible).

## Attempt tracking (phase 1, 2026-06-10)

Design decisions and their evidence — recorded so the choices aren't re-litigated.

**Attempt ID = journal id of the attempt's first event.** Stable across
full re-projections; survives server restarts. For anchored attempts the
first event is the anchor (practice_reset or state_loaded), not the
star_collected. See `tracking/projection.py` docstring — the clearing
invariant and the reset-race row both turn on this ID definition.

**Two-pass projection for retroactive clearing.**
`cleared_ids()` runs first (one linear scan) to build a tombstone map;
then `Projector.feed()` runs sequentially with that map baked in. Effect:
marking a grab as a mistake (`attempt_cleared`) retroactively re-attributes
every later failure to the previous valid practice target. Implemented in
`tracking/projection.py`; semantics in its docstring.

**Broadcast-before-journal ordering** (liveness never gated on the db).
`TrackerService.publish()` calls `broadcaster.publish()` first, then
journals, then projects. A DB failure is caught, logged, and swallowed —
the poll loop never dies (spec §9, `tracking/service.py` docstring).

**Per-event-commit latency.** Each game event is a single-row INSERT
committed immediately (SQLite WAL mode). Measured worst-case: a 4-commit
grab tick (anchor + star_collected + attempt_completed + target_changed)
ran 3.5 ms median / 5.8 ms max, well within the 16.6 ms 60 Hz budget.

**Full re-projection cost** (`_reproject()`, triggered by clear/restore
commands — not on the poll path). Measured: ~6.5 ms @ 100 events, ~23 ms
@ 1,000 events, ~97 ms @ 5,000 events. Acceptable for an explicit user
command; would need batching if it became per-tick.

**Sessions are resumable and hard-deletable.** `POST /api/session/continue` reopens an ended session by clearing its `ended_utc` — new attempts append to it as if it never closed. `DELETE /api/session/{id}` is the journal's one deletion path: it bulk-removes all journal rows whose session matches, then runs a full re-projection; the active session is protected (409). PBs are stored separately and survive. Any `attempt_cleared` events recorded inside the deleted session disappear with it — targets those clears had overridden revert to their pre-clear state in the re-projected view (documented revert, not a bug). Timelines (`timeline` field on each session-view section) are a pure read-over-lifetime-attempts projection: the `TIMELINE_OUTCOMES` registry in `tracking/views.py` maps outcome keys to display properties; `MARKERS` in `ui/components/timeline.js` maps them to SVG shapes. Adding a new marker kind is two registry rows and no other code changes.

**User-feedback round (2026-06-10 live play).** `DeathDetector`
(`detectors/death.py`) fires on action-set edge (entry into DEATH_ACTIONS)
and closes the open attempt as outcome "death". `LevelChangeDetector`
(`detectors/level.py`) fires on level-id edge and closes open attempts as
abandoned — no new memory reads required beyond `curr_level`, which was
already a registered snapshot field. The `mario_acted` activity flag is
written into `practice_reset` and `state_loaded` anchor payloads by
`AnchorDetector`; the projector discards anchors with `mario_acted: false`
as no-op reset spam (they never reach the failure-rate denominator). Strategy
memory is per-star — switching target stars loads that star's own last-used
strategy. `PASSIVE_ACTIONS` and `DEATH_ACTIONS` sets are decomp-verified
constants (see `detectors/death.py`) but remain marked VERIFY pending the
live gate with the human.

## Where the deep facts live (authoritative homes)

- **Addresses, provenance, traps** (gCurrLevelNum trap, vanilla-HUD-timer
  trap, object-pool slot fragility): inline comments in
  `memory/addresses.py`. Cross-check sources are listed in its docstring.
- **Endian decode rules** (PJ64 LE-word storage, XOR offsets):
  `memory/base.py` docstring.
- **RDRAM discovery** (osBootConfig signature scan, 8 MB expansion RAM):
  `memory/pj64.py` docstring. Usamune's own globals live above 0x80400000.
- **Star-grab detection rationale** (edge detection, why re-collections
  fire, identity ordering inside the game frame) and **IGT source
  precedence** (result → counter → reconstructed, DISPLAY_TICK, reset-race
  guard): `detectors/star_grab.py` docstrings.
- **Event schema**: README → Event schema (consumer-facing single source).

## Why there are three timers (history, not derivable from code)

Usamune keeps a SECTION counter (resets on every area warp inside a level),
a running OVERALL star-time counter, and a final-result store written at
the grab. The section counter lives in object-pool behavior data and was
our first IGT source — it validated perfectly on single-area stars (where
section == overall) and failed on "Inside the Ancient Pyramid" (multi-area).
The overall counter and result store are static expansion-RAM globals and
are what events use now. Lesson encoded here: validation scenarios must
break the degeneracy between candidate interpretations, not just confirm
values match.

## Memory hunting playbook

No public RAM map exists for Usamune internals; locate values empirically:

1. **Rate scan** — `tools/find_timer.py`: keeps addresses ticking 25–65/s
   across rounds. Tick windows scale by MEASURED elapsed time between
   reads (a fixed 1 s assumption once disqualified every true counter,
   including the known-good gGlobalTimer — when a control fails, suspect
   the filter).
2. **Exact-value intersection** — `tools/hunt_value.py`: the human types
   the number displayed on screen; intersect scans across two distinct
   values. This collapsed 8 MB to the single result-store address.
3. **Characterize** — `tools/watch_timer.py ADDR:u16`: watch candidates
   (and neighbors — mod globals cluster) across level change, area warp,
   savestate, Usamune reset, display OFF. Only then promote to the
   registry, marked VERIFY until the live gate passes.

Principles:
- A scan only distinguishes quantities that DIFFER during the scan.
- Correlated "garbage" = wrong symbol at that address; random garbage =
  wrong decode.
- Multi-address reads are not atomic across a game transition; lone
  anomalies at transition instants are read races until they repeat.
- Prefer values the mod stores for its own display — calibrated by
  definition.

## Testing strategy

- Detectors: synthetic snapshot sequences (`snap(**overrides)` fixture);
  every live bug becomes a regression test carrying the trace's real
  numbers.
- Memory: `BufferMemory` (full 8 MB, loud bounds checks) exercises the real
  endian path.
- Server: `tick()` is the testable unit; endpoints via TestClient with an
  OfflineMemory stub; WS tested end-to-end through the debug emit route.
- Tracking: synthetic event sequences fed through `TrackerService` verify
  attempt outcomes; journal rebuild (`replay(db.events())`) doubles as the
  projection's correctness oracle — if the rebuilt attempts match the
  materialized table, the two-pass invariant holds.
- UI: frontend smoke via Chrome DevTools MCP after each UI change.
- Live gate: `tools/verify_addresses.py` Phase 2 runs the REAL detector —
  required for any memory-layer change.

## Roadmap (unbuilt)

Delivered in phase 1: attempt tracking, stats registry, REST API, Practice
tab UI (features #3, #4, #6, #9, #11 from the spec). Remaining phases per
`docs/superpowers/specs/2026-06-10-practice-tracker-platform-design.md §11`:

- **Phase 2** — New detectors: RolloutDetector, DeathDetector,
  LevelChangeDetector. Requires snapshot fields + live VERIFY session.
  Turns on `dustless_rate` stat and full `outcome_detail` vocabulary.
- **Phase 3** — TriggerDetector (door/key-door rows), MenuDetector
  (menu-open address hunt required). Delivers menu-failure attempt outcome.
- **Phase 4** — Routes storage + probability board + Routes tab.
- Dedicated key / grand-star events (Bowser key grabs currently emit
  star_collected with course_id 16/17 — documented limitation).
