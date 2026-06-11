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

**Concurrent-instance incident (2026-06-11).** Two servers polling the same emulator double-journaled every game event; prevention is the msvcrt file-region lock at startup (`storage/instance_lock.py`) — the second instance runs broadcast-only; repair is `tools/dedupe_journal.py` (strict type+frame+payload+5 s window rule; see module docstrings).

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

## Practice-quality round (2026-06-11)

Garbage-run discards, markers, progress graph, pinned-target UI. Spec:
`docs/superpowers/specs/2026-06-11-garbage-runs-markers-progress-ui-design.md`
(decision log there is authoritative); castle fix:
`docs/superpowers/plans/2026-06-11-castle-reset-attribution-addendum.md`.

**AFK discard rides an inference, not an address.** "Paused in the Usamune
menu" is inferred as: `igt_overall` frozen while `global_timer` advances
(game logic stopped). The detector measures the streak
(`detectors/anchors.py`), the projection owns the threshold
(`PAUSE_DISCARD_FRAMES = 150`, `tracking/projection.py`) — mechanism/policy
split. Known limits, accepted by design: pausing the EMULATOR freezes both
clocks (not caught); dialog/cutscene time-stop also freezes IGT (a 5 s+
sign-read then reset discards — AFK-adjacent). VERIFY: the menu-freeze
assumption has not been instrument-confirmed live; if the rule never fires,
check it with `tools/watch_timer.py` on USAMUNE_OVERALL vs GLOBAL_TIMER
before reaching for the Phase-3 menu-address hunt.

**Projection rules version themselves per attempt.** New discard semantics
ride a payload marker on the OPENING anchor (`acted_tracking: true`), so
attempts opened by old-detector anchors keep legacy semantics and journals
replay byte-identical forever — no global version flags. Use this pattern
for every future projection-rule change. Death actions are excluded from
the activity trigger (a same-tick `mario_acted` would defeat the
unacted-death discard — caught by tracing the live pipeline, not unit
tests); water/airborne idle states are NOT in `PASSIVE_ACTIONS`, so an AFK
drowning after a savestate-load into water still counts (accepted, same
family as the knockback limitation).

**Corrections flow through the journal, never around it.** The castle rule
(`projection.py` caveat 9) made projector `_level` freshness load-bearing,
which exposed that a stateless edge detector misses level changes across
attach gaps. `LevelChangeDetector` now remembers the last level it EMITTED
and journals establishing/corrective events (`from` may equal `to`) — the
journal stays the single source of truth and live state can never diverge
from rebuild. Never seed live projector state from a snapshot directly.
`CASTLE_LEVELS` 16/26 (grounds/courtyard) remain VERIFY; 6 is
journal-evidenced.

**UI engineering invariants (pointers — details live at the code):**
pixel→SVG coordinate mapping goes through `getScreenCTM().inverse()`, never
bounding-box fractions (letterboxing; `timeline.js` clickToPlace comment);
allocation floors must renormalize or they silently clip the newest data
(`progress.js` segment-layout comment carries the worked thresholds);
`*.map(component)` over stateful children needs `key=` in this push-driven
UI or WS reorders migrate form state across stars (wrong-star writes —
`practice.js`); htm does not decode HTML entities; stat-chip identity AND
order are registry-owned (`stats/registry.py` `selection_id` /
`selection_order`, mirrored once in `statmenu.js` keyOf) — never compare
raw params; `ui/format.js` fmtIgt mirrors `core/timefmt.py` — keep in
lockstep.

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

Delivered in phase 1 (this branch): attempt tracking, stats registry, REST API, Practice
tab UI, death/level-change detectors, activity discard, per-star strategies, timelines,
session continue/delete, PB-glow, single-instance lock (features #3, #4, #6, #9, #11 +
live-feedback round + incident-response from the spec). Remaining phases per
`docs/superpowers/specs/2026-06-10-practice-tracker-platform-design.md §11`:

- **Phase 2** — dust tricks: built (rollout + chained double/triple jump
  events, per-attempt counts, `dustless_rate`/`dustless_jump_rate` stats, UI
  rate displays, schema v3). The original "direct dive→rollout edge" model
  was WRONG — a 50-trial live session + decomp cross-check established that
  landing transitions run `set_mario_action(...); break;` so one visible
  landing frame IS the frame-perfect input (evidence quoted in
  `memory/addresses.py`; model documented in `detectors/dust.py`; old
  journals re-derive via the projection's compat shim). NOT yet
  live-verified: `MARIO_PARTICLE_FLAGS`, `PARTICLE_DUST` and the jump-chain
  action ids are VERIFY-marked until a human session rollouts/chain-jumps
  while watching `tools/verify_addresses.py` (expect `[DUST]` on dusty
  slide/landing lines and correct dustless/late classification).
- **Practice-quality round (2026-06-11, delivered):** AFK/no-activity/castle
  discards, `mario_acted` + `strat_set` events, timeline markers, progress
  graph, pinned active star, sort/hide-resets/batching controls, stat-chip
  identity+order registry. See the section above.
- **Phase 3** — TriggerDetector (door/key-door rows), MenuDetector
  (menu-open address hunt required). Delivers menu-failure attempt outcome.
  Urgency reduced: the AFK rule already covers the practice-relevant menu
  case via the IGT-freeze inference; hunt the address only if that inference
  misfires live.
- **Phase 4** — Routes storage + probability board + Routes tab.
- Dedicated key / grand-star events (Bowser key grabs currently emit
  star_collected with course_id 16/17 — documented limitation).
