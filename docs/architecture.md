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
`practice.js`); inline function refs re-fire on EVERY render in this
push-driven UI — side effects inside them need explicit once-guards or
gameplay events trigger them continuously (2nd bug in the render-frequency
family: `replay.js` autoPlayed comment — paused videos resumed by play());
the served UI requires an explicit cache policy — `/` and `/ui/*` are
no-cache via the app.py middleware, or browsers heuristically mix stale
and fresh module versions (dead-pause-button incident: cached store.js
without the handler next to fresh header.js with the button); user-facing
units live in the USER's domain — "frame" = the 30 fps game-logic frame
(`GAME_FPS`, core/timefmt.py), never the 60 fps encoded rate (frame-step
needed two presses); htm does not decode HTML entities; stat-chip identity
AND order are registry-owned (`stats/registry.py` `selection_id` /
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

For VANILLA statics (no public US map entry, e.g. file-scope `s*` in
decomp .c files), derive before hunting: a translation unit's FORCE_BSS
block lays out in declaration order, legacy `D_8033xxxx` names inside the
block pin the INTERNAL offsets (they encode JP addresses — mind aggregate
alignment: structs ≥ 8 bytes align to 8), and one already-live-verified
symbol in the same block pins the absolute US position. Worked example
with two independent anchors: PENDING_WARP_OP in addresses.py
(sDelayedWarpOp, derived 2026-06-12 from sTimerRunning = HUD_TIMER_RUNNING).
Derived addresses are still VERIFY until the live gate.

No public RAM map exists for Usamune internals; locate values empirically:

1. **Rate scan** — `tools/find_timer.py`: keeps addresses ticking 25–65/s
   across rounds. Tick windows scale by MEASURED elapsed time between
   reads (a fixed 1 s assumption once disqualified every true counter,
   including the known-good gGlobalTimer — when a control fails, suspect
   the filter).
2. **Exact-value intersection** — `tools/hunt_value.py`: the human types
   the number displayed on screen; intersect scans across two distinct
   values. This collapsed 8 MB to the single result-store address.
   Its ±2-frame tolerance is for TIMER display slack — it cannot tell
   small indexes (1/2/3) apart; the 2026-06-12 area hunt converged on
   door COUNTERS instead (ascending inputs select for things that count).
3. **Snapshot diff for small indexes** — `tools/hunt_exact.py`: label
   RDRAM snapshots by game state ("lobby", "upstairs"...), match EXACT
   u16 values, REPEAT a label at the end (counters never return to an
   earlier value — the repeat kills them), cap values < 64, and read the
   globals band (0x8032xxxx–0x8033xxxx) first — area-derived level-heap
   data also satisfies the signature but the canonical named global lives
   in the band. Found gCurrAreaIndex (0x8033BACA) in one pass.
4. **Characterize** — `tools/watch_timer.py ADDR:u16`: watch candidates
   (and neighbors — mod globals cluster) across level change, area warp,
   savestate, Usamune reset, display OFF. Only then promote to the
   registry, marked VERIFY until the live gate passes.

Principles:
- Before hunting a NEW address, check whether the state is already
  distinguishable from a field the snapshot ALREADY samples — a new memory
  read is the heaviest option (touches addresses.py + snapshot.py, both
  shared contracts, plus a live gate). The save-prompt fix (2026-06-12)
  needed to detect the post-star "SAVE & CONTINUE?" screen; a live watch
  showed `mario_action` already held `ACT_EXIT_LAND_SAVE_DIALOG` (0x1327)
  for the whole menu — one tick before `gMenuMode` (0x803314F8) even flipped
  to 2 — so it reused the sampled action instead of adding `gMenuMode`,
  collapsing a 5-file change to 3 files and zero shared-contract edits.
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

## Segment events (2026-06-11/12, segment-events branch)

Composed segments (LBLJ, pipe entries, Bowser fights) as first-class practice targets. Full spec: `docs/superpowers/specs/2026-06-11-segment-events-design.md`.

**Journal facts vs derived segment attempts.** Four new detectors (`detectors/area.py`, `detectors/warp.py`, `detectors/key.py`, `detectors/spawn.py`) journal primitive facts (`area_changed`, `warp_entered`, `key_grabbed`, `spawned`) into the append-only event journal exactly like star events. `SegmentEngine` in `tracking/segments.py` runs per-definition FSMs **in the tracking layer** (not in detectors) — composition is a projection concern. Because segment attempts are entirely derived from journaled primitives, **re-projection makes new definitions retroactive**: `POST /api/segments` or `PUT /api/segments/{id}` triggers a full re-projection and every past occurrence surfaces immediately, no new memory reads required.

**Id namespace offset.** Segment attempt ids = `arm_event_journal_id + 10^10 × def_id`. This puts them in a disjoint namespace from star attempt ids (raw journal ids), survives rebuilds, preserves chronological ordering within a definition via `journal_id()` (`tracking/projection.py`), and encodes the arm event id so the underlying recency is recoverable.

**CURR_AREA address.** `gCurrAreaIndex` has no decomp-derivable static address; it was hunted live 2026-06-12 with `tools/hunt_exact.py` and pinned at `0x8033BACA` (castle: 1=lobby, 2=upstairs, 3=basement — the mapping fell out of the hunt itself). Evidence comment in `memory/addresses.py`.

**Usamune section-timer behavior (live-gated 2026-06-12, five sessions).** The section IGT resets on EVERY load, not just L-resets: level entries, area-door warps, AND non-warp walk-through doors — the last resets AFTER the door animation completes (result written at the idle tick, zeroed the next), so the anchor detector sees it with gameplay actions in both context fields. An L-reset respawns Mario at the level's LAST ENTRANCE (which is why anchor-closure continuation re-arms are position-correct). Usamune MENU actions (warps like 06-01-00, menu resets) also reset IGT — and because the player navigated the pause menu, their anchors carry `paused_frames_before` 13–890 where walked load echoes carry 0–3: **the pause streak is the intent discriminator** (involuntary echo vs deliberate attempt boundary). Consumer-side classification (echo-shape taxonomy, pause gate, echo invisibility, continuation re-arm, `prev_action`/`frames_since_door`) lives in the `tracking/segments.py` docstring and `detectors/anchors.py` — this paragraph records the GAME behavior; those record the rules derived from it.

**Segment/replay boundary ownership.** An attempt's boundaries ARE its trigger events: `start_frame`/`started_utc` stamp the arming event (or the rebasing real anchor), `ended_utc` stamps the closing event, and the replay clip span is `started_utc → ended_utc` ± padding **by construction** (`replay/service.py _span`). When a clip or total time looks wrong, the boundary OWNER is the segment engine's arm bookkeeping — not the replay renderer; the 2026-06-12 "replay starts at the wrong door" reports were all stale/rebased `_Arm` state (menu warp eaten as echo; door echo re-arming through the arm phase). Debug order: journal → attempt row's started/ended → only then the ring.

**Stage quick-select banner (2026-06-13/14).** A presentation-only consumer of the course/segment registries: standing in a main course OR a Castle Inside subarea, `ui/components/stagebanner.js` offers one-click practice targets — that course's stars, or the segments whose start triggers begin in that subarea. Context comes from a broadcast-only `stage_changed {course_id, level, area, in_stage}` event (`detectors/stage.py`) — a live signal, **never journaled** (fully recomputable from `curr_level`/`curr_area`, no historical-query value; cached on `TrackerService.current_stage` for initial page load). Which segments a subarea offers is **derived from the definitions, not the matcher**: `views._segment_start_areas` reads the `to_subarea`/`area` trigger param names *statically* and surfaces only subarea-scoped triggers (a bare "enter Castle Inside" never qualifies, so LBLJ stays lobby-only). This is a deliberate split — the matcher answers "did this event fire?" with live area resolution (the castle loads the lobby transiently, so destination subarea defers to `_pending`); the banner answers "where does this *start*?" as a static value, no deferral. The param-name coupling between the two is pinned by `test_views.test_segment_banner_param_names_match_the_registry`. Rules live in the `detectors/stage.py` docstring + `views.py` comments; the wire payload is in README.

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
  journals re-derive via the projection's compat shim). The particle flags
  and jump-chain action ids were live-verified 2026-06-12 (segment-events
  gate sessions: consistent `[DUST]` and dustless/late classification
  across castle/BitS/arena play).
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
- ~~Dedicated key / grand-star events~~ — **Delivered in segment-events branch**: `key_grabbed` claims all three fight-ending grabs (B1/B2 keys + B3 grand star via `ACT_JUMBO_STAR_CUTSCENE` — the grand star never enters a star-dance action; evidence in `addresses.py`).
- **`door_used` primitive (designed follow-up, 2026-06-12):** doors are
  object-pool objects with fixed positions, and `MarioState`'s used-object
  pointer names the exact door during the animation — a `door_used {x, z,
  behavior}` event would (a) replace the heuristic `frames_since_door`
  echo window in `tracking/segments.py` with an exact causal link, and
  (b) enable door-scoped triggers in the builder ("you use the door at…").
  Needs one hunted+verified read (the usedObj offset). User-validated want.

## Replay capture (2026-06-11/12 live-audit marathon)

Self-contained PJ64 window+audio recording into a disk segment ring
(`replay/` zone; spec carries an outcome addendum — its original stack was
rebuilt twice). The final shape, and the evidence, so neither rebuild gets
re-litigated. Module-local traps (NVENC probe dims, PyAV time_base through
reformat, wall-clock pts across holes, WGC/proctap API quirks) live in the
`encoder.py`/`extract.py`/`video.py`/`audio.py` docstrings — pointers, not
copies, here.

**Final pipeline.** DWM shared-surface capture (`replay/_dwm.py` +
`DwmSurfaceVideoSource`) → lock-free submit into an ffmpeg.exe subprocess
(`replay/ffmpeg_sink.py`: a sample-and-hold feeder paces exact-fps stdin
writes; ffmpeg owns NVENC encode + MPEG-TS segment rotation) → SegmentRing.
Audio: WASAPI loopback of the endpoint HOSTING PJ64'S SESSION
(`replay/audio.py`) → real-time-safe pump (`replay/_system_audio.py`) →
wall-clock placement → PCM sidecar chunks. Clips re-encode at extraction
(`replay/extract.py`).

**Capture pathology — why three video backends exist.** PJ64 1.6 / Jabo
D3D8 presents via the legacy BITBLT model: its pixels live in the window's
redirection surface, and capture APIs differ in WHICH surface they read and
THROUGH WHICH door:

| Path | Result for PJ64 1.6 | Evidence (live, 2026-06-11) |
|---|---|---|
| WGC window / DXGI duplication | FROZEN content — reads the DWM composition path, refreshed at dirty-region cadence for this app class on Win11 24H2 | ~1-6 unique frames/s during play; 188 deliveries → 1 unique image in 6 s |
| WGC monitor (cropped) | Real pixels, but records occluders; DPI-unaware app ⇒ logical client size vs physical DWM bounds (black-bands bug) | 2560x1440 virtualized vs 2403x1907 physical, seen live |
| GDI BitBlt window DC | Fresh pixels but SERIALIZES with the target's UI thread — PJ64 holds its window lock ~110-170 ms once a second (internal 1 Hz work; hiding the FPS display did NOT remove it — user-tested) | 1 Hz stall train in per-phase grab timing |
| DwmGetDxSharedSurface (undocumented user32) | Redirection surface as a shared D3D11 texture, readable with NO window lock — the wired primary | 600 grabs/10 s, 30.1 distinct/s, 0 stalls |

Corollary that cost a round: the grab thread must make NO user32 calls at
all — even a 1 Hz cached-handle re-query inherited the ~170 ms lock stall.
A separate geometry thread owns every window query (`replay/video.py`).

**Hard real-time in CPython — why encoding left the process.** At 60 fps
(16.7 ms/frame) and PortAudio callbacks (21 ms budget), every Python thread
pays every other thread's latency through the GIL. Real offenders were
evicted one by one — disk/encode work in the audio callback (~6 % sample
loss), gen-2 GC stop-the-world, per-call ctypes/COM construction, PyAV
holding the GIL through avcodec_open2 (~110 ms per NVENC session) — each
fix real, yet the residual glitches were DOSE-INVARIANT: missed-slot counts
identical at 1.5x vs 2x grab oversampling, grab rate pinned at ~57/s across
three different timer mechanisms. Dose-invariance to local fixes is the
signature of a structural cause. The structural fix: encode/segmentation in
an ffmpeg subprocess; the in-Python hot path shrinks to a reference swap
plus a GIL-releasing pipe write (`replay/ffmpeg_sink.py` docstring).
Rule worth keeping: if a data path has a hard deadline, its Python side may
contain only GIL-releasing syscalls — anything heavier goes out of process.

**Audio facts (homes: `audio.py`, `_system_audio.py`, `extract.py`):**
- Per-app endpoint routing breaks "capture the default device": PJ64's
  session lives on "Game (Elgato Wave:XLR)", not the default "System"
  endpoint — silence while the user hears the game. Target the endpoint
  hosting the pid's session.
- Liveness must be proven by CONTENT, not status: proctap start()s fine
  and delivers all-zero PCM (couldn't hear a beep from its own process);
  WASAPI loopback goes silently deaf when the target app restarts or
  endpoints re-enumerate. The deaf-stream watchdog compares pump loudness
  against the pid's session peak and reopens the stream.
- WASAPI loopback delivers nothing while the endpoint is idle: place PCM
  by wall clock; never assume a continuous stream.
- AAC consumes EXACT 1024-sample frames: feeding rate//fps blocks (800 at
  60 fps) padded every block → 800/1024 = 78 % playback speed, heard as
  "slow motion with layered distortion".

**When replay misbehaves, read the persistent log BEFORE theorizing** —
every wrong theory of the marathon died on one of these numbers:
`ffmpeg sink:` fed/s + max write (healthy: 60.0 / 6-8 ms steady-state;
first window after spawn ~59 / ~100 ms is a normal init transient),
`recorder video:` CFR fills (in-process fallback path only),
`audio pump:` overflow/drops, gc-watchdog pause lines, and `mem:` RSS /
object-count / scratch trend (`core/procmem.py`).

**Idle gating + pause layer (2026-06-12).** No-input footage is DISCARDED,
never produced-then-paused: stopping the ffmpeg child was shipped first
and reverted — every resume respawned it with a ~0.2 s hole exactly where
a 0-pre-pad clip begins, and gating raw PCM would have shifted the
COUNT-BASED audio cursor (silent A/V desync). Gate at the
completed-artifact boundary (`recorder._on_segment`: both segment kinds
arrive there; straddlers are kept), keep producers running. The resume
signal must include the ANCHOR — igt reset / level entry — not just
movement: Mario stands passive through post-load fade-ins, so
movement-only resume opened 0-pre-pad clips ~2 s late on a frozen frame
(`replay/activity.py`). Manual pause (POST /api/pause) outranks the idle
gate and stops the poller too; AFK pause CANNOT stop the poller, because
the activity tap that detects the player's return rides it — the watchdog
may sleep the system, never itself. Precedence and the reason wire format
live in `server/app.py pause_state`; resume self-heals detectors through
the reattach contract (`poller.set_paused` clears `_prev`).

**Extended-runtime resource safety (2026-06-13 incident).** A long session
froze the whole machine on memory pressure; closing the server recovered it
instantly. Three compounding causes, each fixed:
1. *Idle discarded results, not work.* The idle gate dropped completed
   segments but capture/encode NEVER throttled — DWM grabs ran at the full
   oversampled rate whenever PJ64 was open (each grab a ~8 MB surface copy →
   ~2 GB/s of transient allocation, 24/7, even while AFK). Fix: while idle the
   grab loop trickles to 8 Hz (`video.grab_period`, wired via
   `recorder.is_idle` → `set_idle_check`); the ffmpeg feeder is untouched, so
   resume stays seamless (no child respawn hole — the constraint that killed
   the earlier "pause the sink" attempt still holds). NVENC keeps encoding the
   static frame by design (cheap, GPU-side); the win is killing the Python
   allocation churn that drove the RAM pressure.
2. *gen-2 GC disabled with no manual collection.* `_gcwatch.arm()` raised the
   gen-2 threshold to ~manual to stop stop-the-world glitches, but NOTHING
   ever ran the "manual" collection — so any cyclic object reaching gen-2 was
   never reclaimed for the process lifetime (an unbounded leak; gen-0/gen-1
   still freed short-lived cycles, which is why it took hours). Fix:
   `_gcwatch._Gen2Collector` runs `gc.collect(2)` OPPORTUNISTICALLY while the
   recorder is idle (a stop-the-world pause is invisible when footage is
   discarded), with a 5-minute force backstop for never-idle sessions. The
   glitch mitigation is intact; the leak is closed.
3. *Disk could fill, and a near-full volume thrashes everything.* The 20 GiB
   scratch cap with `retention_s=None` can be approached over long ACTIVE play
   (idle discards, so the ring only grows while recording). A full system disk
   squeezes the Windows pagefile → the same "out of memory / everything laggy"
   symptom as a RAM leak. Fix: `ring.effective_cap` gates the byte cap on
   actual free disk (5 GiB margin), so the buffer shrinks rather than filling
   the volume regardless of the configured cap.

**Memory observability is now mandatory, because we were blind.** Nothing
sampled the process, so a true leak was indistinguishable from OS file-cache
pressure. `core/procmem.py` samples RSS (psapi via ctypes — set argtypes or
the pointer truncates to 32-bit and silently reads 0), GC generation state,
live object count, and scratch size; it backs `/health.memory` and logs a
`mem:` line every 60 s with a one-shot growth alarm (`assess_growth`). When a
long session misbehaves, the `mem:` trend (RSS + objects climbing together =
leak; RSS up with objects flat = native/file-cache) is the first number to
read.

**Shutdown is a liveness property** (CTRL+C hung with ffmpeg still
logging into a dead terminal, 2026-06-12). Every exit link is bounded:
uvicorn connection drain 3 s (browsers hold keep-alive + `<video>` Range
connections forever — main.py), replay teardown 15 s on a DAEMON thread
(deliberately not asyncio.to_thread — executor threads are non-daemon and
joined at interpreter exit, which recreates the hang one layer down;
`server/app.py _stop_replay_bounded`), and the OS-level backstop is a
kill-on-close Job Object assigned to every ffmpeg child
(`ffmpeg_sink._assign_kill_on_close`, behaviorally tested) — an orphan
encoder is structurally impossible no matter how Python dies.
