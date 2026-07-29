# Architecture & Domain Knowledge

CLAUDE.md is the index; this file holds only knowledge that has no better
home. Facts that belong to one module are documented IN that module —
follow the pointers instead of duplicating here.

**Design specs are not in this repository.** `docs/superpowers/` (the specs and
plans each feature was built from) is a local working directory, gitignored
since 2026-07-27 — it quotes the author directly and names unrelated projects,
and this repo is public. So a spec is never the authority here: **this file,
the module docstrings and the tests are.** Sections below say "spec (local
working note)" where one exists on the author's machine and nowhere else;
anything a spec settled that a future session actually needs was copied into
this file or into the code, with its evidence. If you find a claim here that
only a spec could justify, that is a bug in this file — fix it here.

**The backlog is not in this repository either.** Open work lives in `.tasks/`
(also local). Nothing below is a to-do list.

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

**Sessions are resumable and hard-deletable.** `POST /api/session/continue` reopens an ended session by clearing its `ended_utc` — new attempts append to it as if it never closed. `DELETE /api/session/{id}` is the journal's one deletion path: it bulk-removes all journal rows whose session matches, then runs a full re-projection; the active session is protected (409). PBs are stored separately and survive. Any `attempt_cleared` events recorded inside the deleted session disappear with it — targets those clears had overridden revert to their pre-clear state in the re-projected view (documented revert, not a bug). Timelines (`timeline` field on each session-view section) are a pure read over the view's SCOPED attempts (session scope plots only that session; lifetime everything — scope-following since 2026-07-24): the `TIMELINE_OUTCOMES` registry in `tracking/views.py` maps outcome keys to display properties; `MARKERS` in `ui/components/timeline.js` maps them to SVG shapes. Adding a new marker kind is two registry rows and no other code changes.

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

Garbage-run discards, markers, progress graph, pinned-target UI. (Spec + the
castle-reset attribution addendum are local working notes, 2026-06-11.) The
decisions that survived are below and in `tracking/projection.py`'s docstrings,
which are authoritative.

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
- **The rating model** (two ranks per run, the 0–100 curve and its invariant,
  divisions, scopes, mastery × coverage, watermarks) and **where to touch it to
  extend it**: this file → MARELO. Per-module change maps stay in
  `.claude/rules/{ranks,server,ui}.md`.

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

Composed segments (LBLJ, pipe entries, Bowser fights) as first-class practice targets. (Spec is a local working note, 2026-06-11.) The FSM invariants are in `tracking/segments.py`'s module docstring — that is the authority.

**Journal facts vs derived segment attempts.** Four new detectors (`detectors/area.py`, `detectors/warp.py`, `detectors/key.py`, `detectors/spawn.py`) journal primitive facts (`area_changed`, `warp_entered`, `key_grabbed`, `spawned`) into the append-only event journal exactly like star events. `SegmentEngine` in `tracking/segments.py` runs per-definition FSMs **in the tracking layer** (not in detectors) — composition is a projection concern. Because segment attempts are entirely derived from journaled primitives, **re-projection makes new definitions retroactive**: `POST /api/segments` or `PUT /api/segments/{id}` triggers a full re-projection and every past occurrence surfaces immediately, no new memory reads required.

**Id namespace offset.** Segment attempt ids = `arm_event_journal_id + 10^10 × def_id`. This puts them in a disjoint namespace from star attempt ids (raw journal ids), survives rebuilds, preserves chronological ordering within a definition via `journal_id()` (`tracking/projection.py`), and encodes the arm event id so the underlying recency is recoverable.

**CURR_AREA address.** `gCurrAreaIndex` has no decomp-derivable static address; it was hunted live 2026-06-12 with `tools/hunt_exact.py` and pinned at `0x8033BACA` (castle: 1=lobby, 2=upstairs, 3=basement — the mapping fell out of the hunt itself). Evidence comment in `memory/addresses.py`.

**Usamune section-timer behavior (live-gated 2026-06-12, five sessions).** The section IGT resets on EVERY load, not just L-resets: level entries, area-door warps, AND non-warp walk-through doors — the last resets AFTER the door animation completes (result written at the idle tick, zeroed the next), so the anchor detector sees it with gameplay actions in both context fields. An L-reset respawns Mario at the level's LAST ENTRANCE (which is why anchor-closure continuation re-arms are position-correct). Usamune MENU actions (warps like 06-01-00, menu resets) also reset IGT — and because the player navigated the pause menu, their anchors carry `paused_frames_before` 13–890 where walked load echoes carry 0–3: **the pause streak is the intent discriminator** (involuntary echo vs deliberate attempt boundary). Consumer-side classification (echo-shape taxonomy, pause gate, echo invisibility, continuation re-arm, `prev_action`/`frames_since_door`) lives in the `tracking/segments.py` docstring and `detectors/anchors.py` — this paragraph records the GAME behavior; those record the rules derived from it.

**Textbox/cutscene IGT re-init (live journal 2026-06-14, Lakitu Skip).** Textboxes and the intro cutscene engage a TIME-STOP. A *mid-level* textbox merely FREEZES the section IGT (no drop), so it never produces an anchor at all — which is why textboxes already cannot reset a star/segment timer in normal play. The one exception is a *run start*: on a fresh-file Lakitu Skip the intro cutscene (`ACT_INTRO_CUTSCENE`) ends, control is regained (`spawned kind="intro"` arms the segment), and Usamune *re-initialises* the overall counter to ~0 **one frame later** — the detector reads that 1481→0 drop (journal seq 108–111: spawn @1691, practice_reset @1692, `igt_frames_before` 1481) as a `practice_reset` that closed the just-armed segment with a bogus `0'00"03` (1-frame) row. It lands a frame after the spawn (not co-frame with any transition/arm) with no door/save context, so it slips past every other echo shape. Rule (user, 2026-06-14): **never split timing on a textbox in any level/circumstance.** Mechanism: `anchors.py` tracks `frames_since_dialog` (frames since the last textbox/intro-cutscene action; `DIALOG_ACTIONS` ∪ `ACT_INTRO_CUTSCENE`), and `tracking/segments.py` echo shape (5) suppresses any anchor within ~1 s of one. The star side needs no change: the only dialogue anchor is this castle-grounds intro, which projection already discards as castle movement (`CASTLE_LEVELS`), and mid-level textboxes emit no anchor. `DIALOG_ACTIONS` ids are decomp-quoted, marked VERIFY until the human confirms `mario_action` while a textbox is open.

**Segment/replay boundary ownership.** An attempt's boundaries ARE its trigger events: `start_frame`/`started_utc` stamp the arming event (or the rebasing real anchor), `ended_utc` stamps the closing event, and the replay clip span is `started_utc → ended_utc` ± padding **by construction** (`replay/service.py _span`). When a clip or total time looks wrong, the boundary OWNER is the segment engine's arm bookkeeping — not the replay renderer; the 2026-06-12 "replay starts at the wrong door" reports were all stale/rebased `_Arm` state (menu warp eaten as echo; door echo re-arming through the arm phase). Debug order: journal → attempt row's started/ended → only then the ring.

**Stage quick-select banner (2026-06-13/14; Bowser geography 2026-06-25).** A presentation-only consumer of the course/segment registries: `ui/components/stagebanner.js` offers one-click practice targets for wherever Mario stands. Context comes from a broadcast-only `stage_changed {course_id, level, area, mode}` event (`detectors/stage.py`) — a live signal, **never journaled** (fully recomputable from `curr_level`/`curr_area`, no historical-query value; cached on `TrackerService.current_stage` for initial page load). The single `mode` field is the dispatch: `stars` (a main course → its stars), `castle` (a Castle Inside subarea → that subarea's segments), `bowser_course` (BitDW/BitFS/BitS → the reds 8-coin star + the level's no-reds pipe-entry segment), `arena` (a Bowser 1/2/3 fight → the single fight segment, auto-selected), or `None` (no banner). Which segments a context offers is **derived from the definitions, not the matcher**: `views._segment_start_areas` reads the `to_subarea`/`area` trigger param names *statically* for the castle's per-SUBAREA offer (a bare "enter Castle Inside" never qualifies, so LBLJ stays lobby-only), and the parallel `views._segment_start_levels` reads the whole-LEVEL scope for the Bowser banners (pipe segments start in 17/19/21, fights in 30/33/34). The `segment_targets` payload now carries `enabled` + `start_levels` and includes DISABLED segments — the castle row filters `enabled` client-side, but the Bowser row shows a disabled pipe-entry segment so its **no-reds** click can ENABLE it (mutual exclusion: **reds** disables the pipe + targets the star, **no-reds** enables it + targets the segment). The banner answers "where does this *start*?" as a static value (no live `_pending` deferral like the matcher). The param-name coupling is pinned by `test_views.test_segment_banner_param_names_match_the_registry`. Auto-select (arenas) is client-side — the banner POSTs `/api/target`, keeping the detector pure/broadcast-only and browser↔GUI parity intact. Rules live in the `detectors/stage.py` docstring + `views.py` comments; the wire payload is in `docs/api.md`. (v1.3.0 revamp: main-course stars now render as per-slot SM64 star art `ui/assets/star_{1..6}.png` with a rank Hat — the Mario-cap icon, `ui/components/hat.js` (2026-07-25-mario-cap-rank-icons; superseded the earlier "Medal") — per star; presentation-only; detail in the CLAUDE.md module map.)

## Designed but unbuilt

**Not a backlog** — that is `.tasks/` (local). These are two pieces of DESIGN
work that were reasoned through, found to be worth doing, and then not done.
They live here rather than in a task file because each carries a technical
finding that would otherwise have to be re-derived. Everything else that was
once listed here shipped and is documented in its own section below; the
struck-through "Delivered" list was removed on 2026-07-28 for saying nothing a
reader could act on.

- **TriggerDetector + MenuDetector** (door/key-door rows; menu-open address
  hunt required) would deliver a menu-failure attempt outcome. **Urgency is
  low by finding, not by neglect:** the AFK rule already covers the
  practice-relevant menu case via the IGT-freeze inference (see
  "Practice-quality round" above). Hunt the address only if that inference is
  observed to misfire live — otherwise this buys an address for nothing.
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

**Final pipeline (single-mux, 2026-06-18 — supersedes the two-stream stack).**
DWM shared-surface capture (`replay/_dwm.py` + `DwmSurfaceVideoSource`) +
WASAPI loopback of the endpoint HOSTING PJ64'S SESSION (`replay/audio.py`,
RT-safe pump `replay/_system_audio.py`) both feed ONE ffmpeg.exe
(`replay/ffmpeg_sink.py::FfmpegAvSink`): video over stdin, audio over a Windows
named pipe. ffmpeg stamps BOTH by wall-clock, CFR-locks the video and
aresample-async-locks the audio to that one clock, and emits combined A+V
MPEG-TS segments → SegmentRing. Clips are a pure ffmpeg cut of those segments
(`replay/extract.py`). The in-process `encoder.py` SegmentWriter survives only
as a no-ffmpeg fallback.

**Why the rebuild — the two-clock A/V drift (2026-06-17 diagnosis).** The
previous stack carried video and audio on TWO independent clocks and reconciled
them only at extraction: video time = fed-frame-count/fps (the feeder's
sample-and-hold), audio time = cumulative-sample-count/48000 (count-based PCM
sidecars). Their rates diverged and clips drifted audio-behind-video by SECONDS
over a long session — the bug multiple sessions chased as a "fixed offset" and
never caught, because nothing locked the two RATES together. Evidence (live
7h49m session log + on-disk segment-mtime measurement, 924-segment run): video
ran ~248 ppm slow (feeder stalls resync `next_t=perf_counter()`, dropping owed
frames — asymmetric, can only LOSE time), audio ~101 ppm slow (device crystal),
NET ~147 ppm × full session wall-clock ≈ 4 s. The fix is structural, not a
tune: one muxer, one clock, audio resampled onto it (`aresample=async`).
Prototype-verified before the rewrite — 10000 ppm injected audio drift →
bounded NON-accumulating residual. The per-segment AAC priming gap that
originally forced PCM sidecars does NOT recur: it is one continuous encode the
segment muxer slices, so priming applies once at stream start. Deep notes:
[[replay-av-drift-two-clocks]] (auto-memory) + the `ffmpeg_sink.py`/`extract.py`
docstrings.

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
a 0-pre-pad clip begins, and gating raw frames/PCM is the wrong layer (the
single AV mux wants a continuous feed; it CFR-fills video and aresample-fills
audio across idle by itself). Gate at the completed-artifact boundary
(`recorder._on_segment`: A+V segments arrive there; straddlers are kept),
keep producers running. The resume
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

## Self-update — the 2026-06-16 single-exe swap (removed 2026-07-28)

Shipped in v1.0.x-v1.3.x, replaced by the manifest-sync system below on
2026-07-23. Its 49 lines of description were deleted rather than kept as a
SUPERSEDED block: the code they described is gone, and the ONE fact that
still matters -- Windows forbids DELETING a running exe or a loaded DLL but
ALLOWS renaming them, which is what makes any in-place update possible at
all -- is carried, with far more detail than this file ever had, by
`core/update_apply.py`'s module docstring. Read that. A superseded section is
a second answer to a question the live code already answers, which is exactly
the shape of thing this file is not for.

## Incremental updates (2026-07-23)

(Spec + plan are local working notes, 2026-07-23.) Why: the
onefile exe was 220 MB and every update re-downloaded all of it; the bulk
(ffmpeg, Python runtime, numpy/av DLLs) never changes between releases.

**Packaging flipped to onedir** (`dist/SM64Trainer/` = exe + `_internal\`),
installed at `%LOCALAPPDATA%\Programs\SM64Trainer`, launched via a Desktop
shortcut whose target never changes — updates swap files UNDER a stable exe
path. User data (`%LOCALAPPDATA%\SM64Trainer`) remains a separate tree,
untouched by any update, exactly as before.

**The mechanism is per-file manifest sync** (the ClickOnce/MSIX/Chrome
family; the Range-fetch-from-a-release-asset trick is the same one
electron-updater's differential downloads use in production): each release
publishes the full zip + `manifest.json` recording, per file, the SHA-256 of
its content AND the byte range of its compressed data inside the zip
(offsets read from LOCAL zip headers — the central directory's extra field
can differ in length; a wrong offset would Range-fetch garbage; the
round-trip is proven in `tests/test_make_manifest.py`). The updater diffs
the remote manifest against `installed_manifest.json` + the disk, shows the
exact download size in the popup, Range-fetches only the changed files'
byte spans (coalesced; full-zip fallback when Range breaks), verifies every
file, and swaps via a **journaled generalization of the rename trick**: all
originals rename into `.update_backup/`, a journal written before the first
file op makes ANY interruption — including a hard kill mid-swap —
recoverable by `startup_repair` at next launch (rollback → single relaunch;
the journal flips to a terminal state first, so no restart loop). Because
plans are hash diffs, skipping five versions costs the same as one.

**Migration rides the OLD updater's only capability**: it can install
exactly one asset name, `SM64Trainer.exe`. That asset is now the ~tiny
bootstrap installer (stdlib + tkinter, `bootstrap/installer.py`): the old
onefile app self-updates INTO the bootstrap, which downloads the zip once,
installs the folder, creates the shortcut, launches the app, and hands its
own path over via `--cleanup-bootstrap` for deletion (a running exe can't
delete itself). The asset must be published under that name **forever** so
a user who ignored updates for a year still migrates. It doubles as the
new-user installer, preserving the "download SM64Trainer.exe, double-click"
habit.

**Reproducibility keeps deltas small**: `build_exe.py` re-execs itself with
`PYTHONHASHSEED=1` + `SOURCE_DATE_EPOCH=<HEAD commit time>` because Python's
hash randomization perturbs compiled bytecode — without it, every `.pyc`
inside the PYZ hashes differently each build and the "changed files" set
balloons. The zip is deterministic too (sorted entries, fixed 1980
timestamps).

**Measured volatile set (2026-07-23, live-gate build machine):**
- Two `--mode app` builds at the SAME commit: 2,878 files / 553 MB —
  **0 changed bytes** (byte-identical). The `PYTHONHASHSEED=1` +
  `SOURCE_DATE_EPOCH` re-exec fully works. One first-build-only artifact:
  PyInstaller's analysis of build 1 generated `comtypes/gen/__init__.py`
  into site-packages, which build 2+ then collects (sub-KB, stable after
  the first build on a machine).
- The onedir `SM64Trainer.exe` is 24.8 MB raw / **23.4 MB deflated** and
  embeds the PYZ (ALL our Python code) — so any Python change costs
  ~23.4 MB of download. Data files (`_internal/sm64_events/ui/*.js`, seeds,
  ffmpeg) are separate files: a UI-only release deltas in KILOBYTES, and
  the ~200 MB dependency bulk moves only when `uv.lock` bumps a package.
- Real-CDN Range probe: GitHub release assets answer
  `Range: bytes=0-99` with 302→302→**206 Partial Content**, exactly 100
  bytes — the incremental premise holds against production infrastructure.
- Bootstrap installer builds at **11.2 MB** (vs the ~25 MB estimate).
- Net: typical update ≈ **23-25 MB** (was 220 MB); best case KBs; worst
  case (dependency bump) proportional to the bumped packages only.

## Compare (side-by-side, v1.3.0)

Play your own run beside reference footage frame-for-frame in one synced
transport (the "Compare" tab). Cross-cutting facts only; module-local detail
lives in the code (`compare/*`, `ui/components/compare.js` + `videosync.js`,
`tracking/views.py::build_compare_view`) and the CLAUDE.md module map.

**Reuses the replay pipeline; adds an import + a sync layer.** "My run" clips
come from the SAME replay extraction as the Practice tab (`replay/extract.py`).
Compare touches no memory/detector code — it is a pure consumer of
already-journaled runs plus an external-video service.

**Import → normalize → content-addressed cache.** `compare/importer.py`
pulls a source (yt-dlp for YouTube, copy for a file, raw bytes for an upload),
ffmpeg normalizes it, and the result lands in `paths.compare_cache_dir()` under
a content-addressed name, so the SAME video is only ever downloaded/encoded once
(dedup = "load once"). Publish is atomic (`.tmp-<name>` + `os.replace`). Rows
live in the `comparisons` table (migration v10), scoped per `(entity, strat)`.

**Offset-only lockstep sync.** Every video shares one master game-frame; each
stage carries an in-point (its clip start), and the transport re-seeks each
`<video>` to `inFrame + master` on every discrete action. Continuous play runs
them at true rate (they start aligned); pause and scrub re-sync. Scrubbing the
work-area timeline drives `controller.seek(master)` so both videos move together
(`ui/components/videosync.js`).

**Rank-standard default is opt-out.** When a `(star, strategy)` combo has nothing
open and its strategy has a rank-standard example, the UI opens it by default;
closing it opts out (persisted per combo, client-side). The source is
`build_compare_view`'s `rank_source` (the strategy's rank-standard URL, exposed
whether or not it is already saved). Everything you load is remembered per combo
and reloads next time.

**yt-dlp must be `--collect-all`'d into the build (2026-07-02).** yt-dlp lazily
imports its ~1800 site extractors by name (a string→module lookup), so
PyInstaller's static analysis never sees them and the frozen exe raises
`No module named yt_dlp.extractor...` at the FIRST comparison download — a
failure invisible from source (dev has the full package on disk). Fix: `yt_dlp`
is in `tools/build_exe.py`'s `COLLECT` list, which feeds `--collect-all` per
entry (bundles every submodule + data file). The Compare importer
(`compare/importer.py`) is the only yt-dlp consumer, so a broken bundle slips
past every test and first surfaces at a live YouTube import.

## Routes & runs

Ordered star/segment plans and a forgiving full-game run timer. Pure logic +
storage; consumer detail is in the CLAUDE.md module map and the module
docstrings.

**Routes** (`tracking/routes.py`, `routes` table v7) are ordered plans of K-of-N
candidate groups (`{label?, need:K, candidates:[star|segment]}`) plus a
`start_condition` trigger. `route_stats` scores cumulative success as the best-K
product (no-data = 0); `export_route` embeds the segment defs and
`resolve_import` reuses exact matches or creates the rest. Pure and replay-safe.

**Runs** (`tracking/runs.py::RunTracker`, `runs` table v8) time a route as a
forgiving RTA: arm on `run_started`, START the clock when the route's
`start_condition` trigger fires (default `reset_game` = F1) plus `start_offset`,
forgiving splits (wall-clock per step MINUS paused time; retries roll up),
K-of-N no-dup completion, finish on the last step. `run_paused`/`run_resumed`
exclude paused time; editing the armed route re-arms with `void_active` so the
in-flight run is voided. Run id = the starting `game_reset` journal id, so runs
re-derive on replay exactly like attempts (`tracking/projection.py` embeds the
tracker).

## Ranks

**Standards file.** `data/rank_standards.json` is keyed
`{entity: {clock, strategies: {strategy: {rank: seconds}}}}` where
entity = `star:<course_id>:<star_id>` (1-based) or `segment:<segment_id>`.
Clock is `"igt"` for stars and `"rta"` for segments. The file is
hand-editable; a corrupt or missing file silently falls back to empty
(no banners shown). On first run it is seeded from the bundled
`src/sm64_events/data/rank_standards.seed.json`, which is regenerated by
`uv run python tools/scrape_ranks.py`.

**Seed versioning and reconcile-on-load.** The bundled seed carries a `version`
integer. On load, if an existing `rank_standards.json` has an OLDER version than
the bundled seed, `RankStandards.load()` reconciles the stored file up to the
bundled one: community data (strategies/times, videos, jp_strategies, clock, new
entities/strats) is taken from the bundled seed, while user-created entities and
strategies (present in the stored file but absent from the bundled seed) are
preserved. The reconciled result is persisted and the stored version advances to
the seed's version. This ensures upgraded installs automatically receive videos
and US-corrected times without losing any user customisation. To push a community
update to existing installs: re-run `tools/scrape_ranks.py`, then bump
`SEED_VERSION` in `tools/scrape_ranks.py` (which writes the new version into the
seed file on the next scrape).

**Source transport (verified 2026-06-23).** The xcams site embeds its
precomputed standards in a Next.js static chunk as a `JSON.parse('{...}')` blob
keyed `"<stageIdx>_<starKey>"`, times in **centiseconds**; ranks with `sr:"none"`
(always Iron, sometimes Bronze) are the floor — no threshold. The scraper must
disambiguate this blob from the xcam-viewer blob (whose `times` field is a LIST,
not a rank-keyed dict). See `tools/scrape_ranks.py` docstring for the exact
selector and disambiguation logic.

**Per-cutoff example videos (added 2026-06-29).** Each cutoff time in the
standards table links to the fastest example video that *ranks that tier* (band
model): the scraper emits per-strat `clips: [[record_cs, url], …]` (every cam
with a timed record + link, from the catalog's `id_list` + cam blobs), and
`classify.resolve_cutoff_videos` buckets them by `rank_for(record)`, fastest per
tier. **Hard-won data fact:** xcams catalogs only near-WR reference runs, so each
strat's record sits *just under* its Mario cutoff — empirically the band fills the
**Mario row for 223/226 strats**, with only 34 strats reaching a 2nd+ tier and
Gold/Platinum almost never (live count, v3 seed 2026-06-29). So the auto-band is
real but sparse; lower tiers are filled by **manual per-cutoff overrides**
(`user_videos`, preserved across seed bumps by `_reconcile`) and by the section's
**xcams Daily Star link** (`links.xcams_url`) for browsing every example. The
"overall = Mario row" rule: the strat header link uses the Mario-row video, which
in the common case IS the overall-fastest clip. Don't try to source slower-tier
videos from the `beg` sheet — those rows are `[time, player, course]` with no
link.

**JP vs US version times (verified 2026-06-23).** Each cell carries
`time = {"time": primary_cs, "alt": [other_cs, "us"|"jp"] | null}`. The trainer
runs the US Usamune ROM (see `memory/addresses.py`), so `strategies` holds the
**US-effective** ladder: US time where one exists, else JP. The resolver keys on
the `alt` **label** (not position): `alt=[x,"us"]` → primary is JP, x is US;
`alt=[x,"jp"]` → primary is US, x is JP; `alt=null` → US == JP (239 cells).
`jp_strategies` is a sparse parallel dict carrying JP values only where they
differ from US (28 strats across 99 entities as of 2026-06-23); reserved for
future JP-ROM support, which would also require a JP memory map — a separate
larger effort. Scraper logic: `_resolve_jp_us` + `parse_jp_deltas` in
`tools/scrape_ranks.py`.

**Key→entity mapping (verified live + smoke-tested 2026-06-23).** Stages 0–14 =
main courses (course_id = stage+1, star_id = starKey−1); `100c*` deferred.
Stage 15 = Castle secret stars → single-star courses: wc→21, vc→22, mc→20,
aqua→24, wmotr→23, pss→19. Stage 16 = Bowser: `1n/2n/3n` "No Reds" = pipe
entries → segments 5/6/7; `1x/2x/3x` "Battle" → Bowser fight segments 8/9/10;
`*r` "Reds" have no trainer segment (skipped). Movement segments LBLJ/MIPS/Lakitu
Skip/BitS Entry get hand-authored default ladders in `DEFAULT_SEGMENT_LADDERS`.

**Classification.** `ranks/classify.py::display_cs` converts a raw IGT/RTA
float to the centisecond count that would appear on the Usamune display before
looking up the tier, so the badge always agrees with the shown time. Stars are
graded on IGT, segments on RTA (driven by the per-entity `clock` field). The
nine-tier ladder from fastest to slowest is **Mario → Grandmaster → Master →
Diamond → Platinum → Gold → Silver → Bronze → Iron** (`classify.RANK_NAMES`);
`RANK_SCORE` maps each tier to an ordinal (Mario 9 … Iron 1) used for
route-average medals in `views.py`. Iron is the unbounded FLOOR: it carries no
threshold, cannot be set, and its progress bar fills asymptotically
(`easiest_cutoff / time`) so a flat 0% means "never attempted", never "slow".

**LIVE-VERIFY GATE — PENDING.** Before fully trusting rank classification on a
live session, the human must: run a known star and verify the displayed badge
tier matches the xcams standard for that time + strategy; run a known segment and
verify the same. Record the outcome here once confirmed. Until this is done,
treat badge tiers as "best-effort" against the scraper data.

## MARELO — the overall rating (2026-07-24/25)

One rating derived from practice history, on top of the per-cutoff standards
above. (Design spec is a local working note, 2026-07-24, and design-time —
where it and this disagree, THIS is current.) Per-module "where to
change what" lives in `.claude/rules/ranks.md` (scoring/scopes/history),
`.claude/rules/server.md` (endpoints) and `.claude/rules/ui-ranks.md` +
`ui-climb.md` (surfaces) —
this section is the cross-cutting model those three assume.

**One time, three questions.** The same run grades three ways, because three
different things are being asked. Two sit side by side on a practice card: the
STRATEGY rank grades the time against the active strategy's own ladder ("how
well do I run this strat"), the ENTITY rank grades it against the entity's
**best-possible ladder** — the pointwise minimum across every strategy that has
standards (`scoring.best_ladder`) — ("how close is this to the fastest this star
can be"). Mastering a slow strat therefore maxes that strat's rank but not the
star's, which was the whole point of the design. When the two grade identically
the UI shows ONE banner labelled with both names; it decides that by comparing
the RENDERED fields, never by "is the active strat the fastest" (see
`.claude/rules/ui.md`).

The third is `views.py::build_entity_ranks` (spec
`2026-07-25-target-picker-strategy-step`): the **best-scoring strategy's own**
rank, which is what the target picker's grid cells wear. At pick time no
strategy has been chosen yet, so neither of the other two is the right question
— "how good am I at this star, at all" is. Ties break on `min(strat)`, the same
deterministic convention `_fastest_strategy` uses. It is an on-demand endpoint
(`GET /api/target/ranks`), never a session-view field: the view rebuilds on
every WebSocket event and the averaging rank modes grade O(history) per strategy
per entity.

**A ladder is defined in ONE clock, so that is the clock it grades in.**
`RankStandards.clock_for(ek)` (igt for stars, rta for segments, overridable per
entity) is THE grading clock for `_section_banner`, `entity_rank`, both picker
endpoints, AND — since the I1 fix (final review, 2026-07-26) — the attempt-row
medals and progress-graph dots `_attempt_json`/`_progress` compute (threaded in
as `rank_clock`/`seg_rank_clock`, kept separate from the DISPLAYED frames/
pb_delta, which stay on the view clock). It is deliberately NOT the view clock:
with the header's Clock control on "Anchor → grab", a star's RTA time was being
graded against its IGT-defined ladder, which is the wrong ruler — RTA includes
approach time, so it systematically under-ranked. Measured on 2026-07-26: the
same run read Platinum II on the section banner and Diamond V everywhere else;
before I1, the same measurement also caught a Diamond V banner sitting directly
above an attempt row for the very same run wearing a Platinum cap. The DISPLAYED
pb (`sec["pb"]`) still follows the view clock; that is a display choice and is
correct. Fixed in `build_session_view`, pinned both directions in
`tests/test_views.py`.

Two call sites remain named exceptions, deliberately not swept into this fix:
`rank_by_star` and `segment_targets` (both `views.py`) hardcode `"igt"`/`"rta"`
literals rather than calling `clock_for` for the stage quick-select banner's
per-star/per-segment medal. They agree with `clock_for` today only because
every loaded standard happens to define stars in igt and segments in rta — a
coincidence of the standards data, not a rule the code enforces. Routing them
through `clock_for` is a real fix; it just wasn't in scope for this pass (M1,
final review 2026-07-26).

**The 0–100 curve.** `ranks/scoring.py::score_for` interpolates a time between
the ladder's cutoffs, anchored so each tier's floor is a fixed score
(`SCORE_ANCHORS`: Mario 95, Grandmaster 90, Master 80, Diamond 70, Platinum 60,
Gold 45, Silver 25, Bronze 10; Iron 0 implicitly). Below the easiest cutoff the
same asymptotic Iron tail as the bar. **The invariant that holds the whole
system together:** `tier_from_score(score_for(L,t), defined_tiers(L)) ==
classify.rank_for(L, t)` — score and medal can never disagree, pinned over all
278 seeded ladders by `tests/test_ranks_scoring_seed.py`. Ragged ladders are
why `defined_tiers(ladder)` is REQUIRED for entity-level lookups: a ladder
missing a tier still crosses that tier's score range, so a full-table lookup
would name a tier the ladder doesn't define. Aggregates (which have no ladder)
DO use the full table — that asymmetry is deliberate and is what lets the UI's
`ANCHORS` mirror colour a MARELO score's band without re-deriving anything.

**Divisions.** Each tier is cut into `DIVISIONS_PER_TIER` (5) equal
score-width slices, numbered V (bottom) → I (top). `division_progress` returns
the tier, the numeral, the fill WITHIN the current division, and the next step
— always one division up, or the next tier's bottom division at the top of a
tier. The UI never computes this: server-side only, `views.py::_graded_progress`
is the one place a ladder + a time become the whole banner payload.

**Scope = a derived SET, not a registry.** `scopes.entity_groups(scope_id)`
resolves `overall`, `route:<id>` or `course:<id>` into GROUPS
(`{"need": k, "candidates": [...]}`), so a route's K-of-N step contributes k
slots scored by its best k candidates. Every route in the library — including
one the user invents this afternoon — is therefore a rated scope with its own
history for free, and there is no scope registry to maintain. The FOCUS ROUTE
is the scope (`_active_scope`), so there is no second scope control to keep in
sync; an unknown scope 404s deliberately rather than silently becoming a
different rating.

**MARELO = mastery × coverage.** Mastery is the mean 0–100 score over the
entities you've practiced; coverage is practiced/total slots. The load-bearing
distinction is **ABSENT vs ZERO**: an entity with no ladder never enters
`rankable_entities` at all (so it can't drag a rating), while a rankable but
unpracticed one is a real zero in the denominator. `practiced` is counted by a
key's PRESENCE in the scores map, never truthiness, so a genuine 0.0 still
counts. `gain_for` answers "what would the next tier here add to this scope" —
diluted by slot count, and targeting Gold for unpracticed entities so they read
as quests rather than floor entries.

**History is recomputed, never stored.** `ranks/history.py` replays the
successes chronologically, re-aggregating after each one, so a scope's curve
follows CURRENT standards and CURRENT route membership by construction (a seed
bump or a route edit reshapes the past — the UI says so rather than hiding it).
Pure: the caller injects the scorer.

**Celebrations ride watermarks, three distinct ops.** `ack` RAISES (UI-driven
only, once the celebration has actually been shown), `sync` LOWERS on every GET
(follows a drop down, so re-climbing celebrates again), `seed` CREATES on a
first-ever rank (so opening a scope never celebrates your whole history at
once). Scope watermarks and entity watermarks live in separate `ui_state` keys
by construction. `/api/marelo/summary` triggers none of the three — that is why
the chip row can poll safely.

**Adding to this system.** The extension points, in the order they usually come up:

| To add… | Touch |
|---|---|
| A new **scope kind** | `scopes.entity_groups` (resolve the id → groups) + `scopes.scope_list` (so the picker offers it). Scoring, history, chips, chart and breakdown all follow for free — they only ever see groups. |
| A new **rank surface** | Read `/api/marelo` (or `_score_scope` server-side). Never recompute tier/division/fill/next in JS; if the payload lacks a field, add it in `_score_scope` where the ladders are in hand. |
| A change to **the curve or the anchors** | `ranks/scoring.py` only — then mirror `SCORE_ANCHORS`/`DIVISIONS_PER_TIER`/`DIVISION_NUMERALS` into `ui/components/rankpage.js` (pinned by `tests/test_ui_rank_chart.py`) and re-run `tests/test_ranks_scoring_seed.py`, which is what proves score and medal still agree. |
| A **tier colour** | `ui/components/caps.js::CAP` — the single authority (pinned by `tests/test_ui_caps.py`). The old `ranks/standards.py::RANK_COLORS` Python copy was deleted (2026-07-25): it had no runtime consumer, existing only to be mirrored, and the mirror is what made a tier swap a three-edit job across two languages. Every `Hat` icon (medal-style and division-bearing alike — one component replaced both `Medal` and `Crest`, Task 4, 2026-07-25), gridline, rank-up dot, ladder band and card wash reads its colour from `caps.js`. |
| **Keeping an entity out of a rating** | `POST /api/marelo/exclude` (reversible; excluded rows stay in the payload as inert display rows). Entities with no standards are excluded by construction, not by flag. |

## Default routes foundation (2026-07-23, spec #1)

Ships the engine + storage mechanism for the standard Usamune route corpus
(the corpus itself is spec #2). (Spec + plan are local working notes,
2026-07-23.) Consumer detail (fields, functions) is in
`.claude/rules/tracking-storage.md` — this section records the two pieces of
cross-cutting rationale.

**The segment matcher generalizes from a 2-state chain to an N-state ordered
automaton.** `SegmentEngine` already ran a two-state instance of an
ordered-event automaton (arm → end), the same shape as a gesture recognizer or
a log-sequence matcher: advance on the expected next token, cancel on an
unexpected significant one, ignore noise. Spec #1 generalizes it to length-N
via `SegmentDef.waypoints` (an ordered list of any-of clause-sets) without
touching the existing chain at all — **empty `waypoints` is the degenerate
2-step case**, byte-for-byte identical to pre-spec behavior, which is what
made it safe to change the shared `MatchContext`/`SegmentDef` contracts
underneath every existing definition (LBLJ, the pipe entries, the Bowser
fights — all untouched). A waypoint-bearing def instead runs
`SegmentEngine._feed_waypoint`, whose precedence is: **end** (only once every
waypoint is consumed — the end trigger cannot fire early) > **death/
game_reset** (hard fail, row) > **session_started** (silent disarm, no row —
an armed sequence must not survive a session boundary) > **echo anchor**
(invisible, reuses the exact `_anchor_echo` shape taxonomy the plain chain
uses, so the two paths can never drift on what counts as an involuntary
load) > **real anchor** (rewinds `progress` to 0 and re-arms IN PLACE at the
anchor frame — the practice-retry loop continuation, but unlike the plain
chain's reset-row-then-rearm, a mid-sequence retry records **no row**: the
player never finished or failed a variant, they restarted the attempt) >
**next waypoint** (the event matches `waypoints[progress]` → advance the
pointer, no row, no re-arm) > **major action** (`star_collected`,
`key_grabbed`, or a real-edge `level_changed` that ISN'T the awaited
waypoint — decision 5, session 2026-07-23: this is a **silent abandon**, no
row, consistent with the engine's existing silent disarm on a foreign level
change and the AFK/no-op discards) > **transparent** (`area_changed`/
`warp_entered`/`spawned` mid-sequence change nothing — walking through a
castle subarea on the way to the next waypoint must not trip the matcher).

**Authoring caveat (docstring, not a code defect).** The major-action cancel
pops the def from `_armed`; the SAME event is then re-evaluated by `feed()`'s
ordinary arm/re-arm phase against `d.start_triggers` — the existing
"re-firing a start trigger while armed re-arms" convention. If a route's
start trigger is looser than (or equal to) a waypoint clause it could
collide with, the cancelling event can satisfy the start trigger and
re-arm in the same tick (a disarm+arm notice pair) instead of a clean
abandon. Spec #2's corpus authors must write each def's start trigger at
least as specific as every waypoint clause it could be confused with.

**LIVE-GATE VERIFY (deferred, per Task 3).** The real-anchor rewind-in-place
behavior for a waypoint mid-sequence assumes the same "L-reset respawns at
the last entrance = the segment's start position" fact the plain chain relies
on (verified 2026-06-12 for 2-step defs); it has not yet been live-verified
for a MULTI-step movement specifically (does a retry mid-waypoint always
rewind cleanly, or can a savestate load relocate Mario somewhere the rewind
doesn't cover?). Rewind-in-place is the conservative default until a human
session confirms it against the seeded corpus (spec #2).

**Editable defaults use the same seed/reconcile pattern as rank standards —
deliberately, not independently invented.** The repo already had one working
instance of "ship community-curated content that stays user-editable and
self-heals on update": `ranks/standards.py` over
`rank_standards.seed.json` with a `SEED_VERSION` and a reconcile that
refreshes untouched rows while preserving user edits (see the Ranks section
above). Spec #1 reuses that exact shape for routes/segments
(`tracking/defaults.py::reconcile_defaults` over `data/defaults.seed.json`)
instead of the inline-SQL path in `storage/db.py::MIGRATIONS`, which the
module map itself flags as unmaintainable and had already forced two
hand-written repair migrations (v5 LBLJ, v6 Bowser 3) — a symptom of storing
one-time community content as irreversible schema mutations rather than data.
Each row carries a stable `seed_key` slug (`seg:ccm->bitdw`,
`route:16-star-lblj`) that reconcile matches on, independent of the
autoincrement id, plus a `seed_dirty` flag: **untouched (`seed_dirty=0`)**
rows refresh from a newer seed (ship a fix, everyone gets it); **user-edited
(`seed_dirty=1`)** and **user-created (`seed_key IS NULL`)** rows are never
touched; a seed row missing from the db is inserted. `seed_dirty` flips to 1
only on a user-facing write path (`update_segment`/`update_route` in
`tracking/service.py`) — reconcile itself writes through the db layer
directly and can never self-flip the flag it's supposed to respect. **Reset
to default** (`POST /api/segments/{id}/reset`, `/api/routes/{id}/reset`)
re-copies the row from the current bundled seed by `seed_key` and clears
`seed_dirty` — the escape hatch for a user who edited a default and wants it
back, without losing every other customization.

One wrinkle unique to routes: a seeded route's step candidates reference
segments by `seed_key`, not the local autoincrement `segment_id` (the seed
file can't know a fresh install's ids). `reconcile_defaults` therefore
resolves the `segments` block FIRST, building a `seed_key → segment_id` map,
then rewrites each route step's candidates through that map
(`tracking/defaults.py::_resolve_steps`) before writing the `routes` row — an
unresolved key writes `segment_id=-1`, which renders as the same `broken`
step the UI already shows for a manually-deleted segment, rather than
crashing reconcile. `reset_route` re-resolves through the SAME helper against
the CURRENT `segment_defs` table, so a segment that was itself reset/re-seeded
under a different id still binds correctly.

**Why this matters for spec #2:** the ~45-segment, ~13-route corpus lands as
**pure seed JSON**, never hand-written SQL or one-off migrations — the
reconcile mechanism is what makes "ship the whole Usamune route corpus, keep
it editable, let a future correction reach existing installs automatically"
possible at all.

## Default routes corpus (2026-07-24, spec #2)

Spec #1 shipped the mechanism; this is the content — 55 shared castle-movement
segments, 13 main-category routes (16★ ×4, 70★ ×5, 120★ ×2, 0★, 1★) and 37
Stage RTA routes, generated from `tools/corpus_*.py` into
`data/defaults.seed.json`. Two facts below are invisible in the data they
govern, which is why they are written here rather than left to be rediscovered.

### The movement grammar is forced, not chosen

`tracking/segments.py` disarms an armed def in two ways that decide the shape
of every movement:

- A **plain** (waypoint-less) def is disarmed, with no row, by any
  `area_changed` away from its arm position (the `_at_arm_position`
  relocation rule) and by any `level_changed` matching neither its start nor
  its end.
- A **waypoint-bearing** def is silently **cancelled** by any major action —
  `star_collected`, `key_grabbed`, or a real-edge `level_changed` that is not
  the next waypoint (`_is_major_action` / `_feed_waypoint`) — while
  `area_changed`, `warp_entered` and `spawned` stay transparent.

So: a movement crossing a castle region, or a hub level (courtyard 26,
grounds 16), needs a waypoint. **The castle interior is a line**
(basement ↔ lobby ↔ upstairs), so basement→upstairs is *two* area edges and
needs one too — `seg:bowser2->upstairs` was specced as plain on the reasoning
that "its end IS the region crossing", which holds only for *adjacent*
regions, and the simulation gate caught it. Conversely a movement that spans a
Toad/MIPS grab must stay plain (star grabs are transparent there), or end at
the region boundary while the next movement *starts* on `star_grabbed` —
which is why `seg:sl->basement` ends on `area_enter` and `seg:mips2->hmc`
starts on a grab.

### Route steps must be in completion-event order

`RunTracker._apply` only ever considers `steps[current]`; an attempt matching
no candidate of that step is discarded. A step listed out of order therefore
stalls a run **permanently**, and nothing — validation, the seed, the UI —
reports it.

Within a single event, `Projector._dispatch` builds `closed` as **star
attempts first, then segment attempts**. That ordering is the reason a
movement may *start* on a star grab but must never *end* on one: on the grab
event the star attempt is offered first, so a movement ending there would
complete the movement step and leave the star's own step with its completing
attempt already consumed.

### Verifying data nobody can eyeball

~700 route steps and 56 definitions were authored against sources, not
observed behaviour, so the gates are behavioural:

- `tests/test_defaults_corpus.py` builds each movement's event stream from an
  **independent** world model — BFS over `addresses.WORLD_EDGES_*`, with the
  definition contributing only its checkpoints — and asserts exactly one
  success plus silence across all 55 other walks. The walker emits a level
  entry and its establishing `area_changed` on **one frame**, as the real
  detectors do; a frame apart, every arm records `area=None`, the relocation
  rule can never fire, and the whole file passes vacuously.
- `tests/test_defaults_corpus_routes.py` replays all 13 main routes through
  `RunTracker` and asserts each finishes, with a misordered-route negative
  control so a green result means something.
- `tests/test_corpus_routes_main.py` asserts each route's star total equals its
  category (16/70/120/1/0). This independently reproduces the community's
  CCM17/CCM18 names — 13 stars precede CCM, so you leave with 17 or 18
  depending on the option — which is what makes the transcription trustworthy.

### Shared start clauses are expected, and benign

A route that visits a stage twice (70★'s two BoB trips, 120★'s two DDD trips)
necessarily contains movements with the **same start clause** — exiting BoB
arms both `seg:bob->pss` and `seg:bob->ccm`. This is harmless *because their
ends differ*: the twin is silently disarmed by the level change into the other
destination and records no row. The visible symptom is a transient armed chip
naming the wrong movement, which self-corrects on the next level change.

Two movements sharing **both** a start and an end inside one route would be
genuinely indistinguishable — same attempts, same PB, and the run crediting
whichever closed first. `tests/test_corpus_routes_main.py` pins that this never
happens.

### Castle-secret stars (course 0)

The Toad and MIPS stars belong to no course, so `star_grab.py` reports them as
course 0. `STAR_NAMES[0]` names them; ids are decomp-derived twice over —
`include/save_file.h`'s flag order (`TOAD_STAR_1..3` = bits 0–2,
`MIPS_STAR_1/2` = bits 3–4 under `SAVE_FLAG_TO_STAR_FLAG`'s `>>24`) and
`behaviors/mips.inc.c` spawning `STAR_INDEX_ACT_4 + oBhvParams2ndByte`
independently agree on MIPS = 3 and 4. **VERIFY (live gate):** which Toad
carries index 0/1/2 — the binding follows the flag order plus the 12/25/35-star
spawn thresholds, and the journal held zero course-0 grabs when this shipped.

### Corpus refinements (2026-07-24, post-merge)

**One step per course VISIT, not per star.** A route dictates which stages you
visit in what order — never which star to grab first inside one.
`tools/corpus_vocab.group_visits` collapses each run of consecutive same-course
star steps into a single `need=N` group, applied to every seeded route (120★:
~160 steps → 67). Two visits to one course stay separate because the movement
step between them breaks the run; a documented either/or survives as
`need < len` ("any 4 of these 5"); a lone star keeps its own step and its own
name. A Stage RTA route is one visit, so it is a single step — which is why the
corpus ships 35 stage routes to the wiki's 37 lists: WDW's and RR's
"Beginner"/"Expert" 120 variants differed **only** in which star carried the
100 coins, i.e. only in order, so unordered they are the same route.

**A def whose start and end share one event is unfireable.** The full statement
and its evidence live in `tracking/segments.py`'s module docstring (the
corollary under "closures process BEFORE arming"). Shipped in the corpus once —
DDD → BitFS via the sub, where the 23 → 19 one-way edge makes `level_exit
from=23` and `level_enter to=19` the same `level_changed`. The walk-based
simulation could not catch it, because it always exits a course to its castle
landing node and so produced two events where the real hop produces one; the
guard is structural instead (a direct world edge from source to destination
means the pair is unfireable, whatever a walk does).

**Route focus.** With a route active the star selector offers only that route's
stars and the castle segment row only its segments. `service.active_route()`
carries `star_keys` (`"<course>:<star>"`, the same shape as the view's
`last_strat_by_star`) so the UI tests membership with one Set lookup. Two
deliberate non-filters: a route that never visits the course you are standing
in shows every star rather than an empty banner, and the Bowser/arena rows are
untouched because their reds-vs-no-reds toggle must see a pipe segment the
route may not list.

**Picking a route now tells the SERVER.** Until 2026-07-24 nothing in the UI
called `POST /api/route/select`, so the journaled active route was permanently
None — and since all 56 seeded movements carry the `in_active_route` guard,
they could only ever arm as a standalone target. The corpus was inert in normal
practice. `practice.js::pickRoute` writes the selection through; localStorage
stays the optimistic mirror it was specced as.

**A course's star row shows that course only.** A segment earns a cell there
only if its start trigger names that level, so castle movements (which start on
a `level_exit` or a star grab, carrying no start level) never appear — a warp
out of DDD used to leave "DDD → BitFS (sub)" sitting in Shifting Sand Land.
This narrows the "a RUNNING segment must never be invisible" rule rather than
breaking it: the header's tab-independent "Running: …" chip still names every
armed segment, and the castle rows still offer them.

**Known residual:** warping out of a course can leave other movements armed
until the next level change, because `level_exit from=X` legitimately matches
leaving X for anywhere. They no longer appear in course rows and the header
chip is honest about them. The real fix is engine-side — treating a menu warp
as a relocation that disarms — and is deliberately not folded in here.
