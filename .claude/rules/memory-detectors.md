---
paths:
  - "src/sm64_events/memory/**"
  - "src/sm64_events/detectors/**"
  - "src/sm64_events/core/snapshot.py"
  - "src/sm64_events/core/events.py"
  - "tools/find_timer.py"
  - "tools/hunt_value.py"
  - "tools/watch_timer.py"
  - "tools/verify_addresses.py"
---

# Memory reads & detectors — where to change what

| To change... | Edit |
|---|---|
| Memory addresses, action IDs, course/star names, traps, world topology | `memory/addresses.py` — THE registry; richly commented, read it. `WORLD_EDGES_TWO_WAY`/`_ONE_WAY` + `world_connections()` = directed (level, subarea) reachability under normal movement (**corrected 2026-07-27**: the one-way `(23, 19)` "DDD sub bay → BitFS" edge was wrong — BitFS is entered from the BASEMENT, `23 → 6 (area 3) → 19`, live-captured twice; a wrong edge here is not cosmetic, it made the corpus's independent walker route DDD → BitFS in one hop and forced a movement onto a `star_grabbed` start) — drives the segment builder's dropdown filtering ONLY (warp menu can fabricate any edge; validation/matcher stay permissive); `world_regions()` BFSes those same edges from `CASTLE_REGION_NODES` (gameflow order) to answer "which castle region owns this place" for the segment-origin taxonomy — BBH→courtyard, VCUtM→grounds, CotMC→basement, each arena→its exit's region; `region_for_node` adds the one case the BFS can't answer (subarea-less castle interior → lobby, the transient-lobby rule). `CASTLE_SECRET_STAR_AREAS` is MIPS-only ON PURPOSE |
| Endian decoding / typed reads | `memory/base.py` — the ONLY place that knows PJ64 byte order |
| Process attach / RDRAM discovery | `memory/pj64.py` |
| Object-pool decoding | `memory/objects.py` · test double: `memory/buffer.py` |
| Fields sampled each tick | `core/snapshot.py` |
| Event envelope / wire format | `core/events.py` |
| Star-grab + IGT logic | `detectors/star_grab.py` — docstrings carry the domain rationale; IGT itself comes from the shared `detectors/igt_clock.py` (result→counter→reconstructed) which ALSO stamps key.py's grand star — every displayed time routes through it, never a frame delta. **OPEN, and it is a LEGALITY bug, not an accuracy one — we read the result store too EARLY.** Usamune's `STOP` decides when Usamune stops: *Grab* = touching the star, *Xcam* = touching the GROUND after the grab, *GrabX* = stop at the grab then update at x-cam (manual, 2026-08-01). Leaderboards accept `STOP ∈ {GrabX, Xcam}` and any `DISPLAY` except `Hide`, so a `STOP=Grab` time is invalid, not merely early. **Measured 10/10 (`tools/verify_star_stop.py`, three presets): his screen is always `USAMUNE_STAR_RESULT` once its write SETTLES** — not the value at our action edge, and never where `USAMUNE_OVERALL` came to rest (that runs on to the star-select screen, +40..+79). We sample at the edge, so under GRABX we took Usamune's FIRST write (it writes twice) and under XCAM no write had landed within `IgtClock.RESULT_FRESH_FRAMES` at all, dropping us to `counter`. Gaps were 0 under GRAB and 2..39 frames otherwise — **not constant, so no offset fixes it; the fix has to take the LAST result write inside a settle window.** That also makes the emit non-synchronous: under XCAM the number does not exist on the edge frame. **Two consequences worth knowing before pricing anything here:** (1) `STOP` is INFERABLE from the write pattern — *write at the grab?* × *write later?* separates all four values (None/Grab/GrabX/Xcam), and `IgtClock` already keeps 150 frames of `igt_result` history, so no settings block in memory is needed to know whether a time is legal; (2) **the x-cam moment IS the star-dance entry, and Usamune's number is that frame's `USAMUNE_OVERALL` + `DISPLAY_TICK`** — measured by `tools/derive_xcam.py` (scores itself against Usamune's settled result; no human reading), 4 scoreable midair grabs, error −1 on three and −2 on one, against −4/−11/−23/−39 for our grab edge. So the existing `counter` path was already correct except for WHICH FRAME it read: `_primary` back-computes to the star touch, and it should land on the dance entry. The internal counter never stops (manual footnote, confirmed under every setting), which is what makes this readable even on `STOP=Grab`. **A GROUND grab has x-cam ON the grab frame** (his words: "I just simply ran into the star… grabbing and xcam is identical"), so Usamune writes once, before any post-grab window opens — only MIDAIR grabs separate the two moments, and only a separated pair measures anything. The residual ±1 is our own torn read, not Usamune: a snapshot is twelve `ReadProcessMemory` calls, so the action and the counter in one sample can straddle a game frame |
| game_reset | `detectors/lifecycle.py` |
| Attempt anchors (practice_reset / state_loaded) | `detectors/anchors.py` — anchors carry mario_acted + paused_frames_before + acted_tracking + save_pending (post-star save-screen latch → segment echo) + frames_since_dialog (textbox/intro-cutscene recency → segment echo shape 5: a run never splits/resets on a textbox); emits the mario_acted event; docstring covers classification (incl. the pause-warp shape: menu warp with IGT already ~0 → anchor from position change + pause streak), pause streak, and VERIFY notes |
| Death detection | `detectors/death.py` — action-set edge + pending-warp pulse for void-outs (pit falls fire BEFORE level_changed; docstring carries why); closes open attempt as outcome "death". **CLOSED 2026-08-01 — measured, and it is CORRECT as it stands. Do not "fix" it.** This is the one time source that does not go through `igt_clock.py`: the payload carries `curr.igt_overall` RAW, while star/key/pipe times all add `DISPLAY_TICK`. The live gate (`uv run python tools/verify_death_clock.py`, behavioural not address — `USAMUNE_OVERALL` is already sampled, so no `VERIFY` row and `verify_addresses.py` is not the instrument) asked the human to pause and read the frozen timer. **Eight readings across three Usamune TIMER presets, unanimously the RAW counter.** So `DISPLAY_TICK` is a star-PATH calibration — it compensates for `_primary` back-computing to a touch frame, not for any offset in Usamune's display — and routing death through the clock would have put every historical death row 3 cs ABOVE what he saw, stars included (projection stamps a star's death attempt from this same payload). The same sitting retired a false alarm in the gate itself: `counter_tracked_cleanly` demanded the counter and the game frame move EXACTLY together and so cried PROBLEM on seven of eight healthy deaths, three of them for the counter moving MORE than the frame — impossible for a stall, and the tell that a 12-call non-atomic snapshot skews ±1 at each window end (`READ_SKEW_FRAMES`). Pure core pinned by `tests/test_verify_death_clock.py`, seven mutations proved |
| Level-change detection | `detectors/level.py` — stateful: remembers last EMITTED level, journals establishing/corrective events (from may equal to) so projection-side level tracking never runs stale; closes open attempts as abandoned |
| Dust tricks (dustless rollouts/jumps) | `detectors/dust.py` — TRICKS registry (one row per trick); docstring carries the decomp-verified landing-frame timing model; counts attach to attempts via projection.py |
| Stage detection (quick-select banner context) | `detectors/stage.py` — broadcast-only `stage_changed {course_id, level, area, mode}` where `mode` ∈ stars (main course 1-15) / bowser_course (BitDW/BitFS/BitS = lvl 17/19/21 → course 16/17/18; reds star + no-reds pipe segment) / arena (Bowser 1/2/3 = lvl 30/33/34; single fight) / castle (Castle Inside subarea) / None; keys on the resolved CONTEXT so a BitDW→BitFS course swap and a lobby↔upstairs subarea switch both re-emit (offered targets differ); reuses `course_for_level` (addresses.py) |
| area_changed / warp_entered / key_grabbed / spawned | `detectors/area.py` · `detectors/warp.py` · `detectors/key.py` · `detectors/spawn.py` — segment-primitive facts; area mirrors level.py's last-EMITTED discipline + stamps `from_transient` (source area not dwelt-in — every castle entry transits the lobby, so course exits read from=1 like a real lobby walk; area_enter's "coming from" rejects transients); key detector guards star_grab from misattributing Bowser keys AND carries Usamune IGT (via igt_clock) on fight-end grabs so a segment ending on the grand star matches Usamune's time, not a wall-frame delta. **warp.py carries it too since 2026-07-31** (live report: BitDW "No Reds" read 0'35"90, Usamune 0'35"96), so it is no longer stateless — it owns an `IgtClock` and observes every tick like key.py. The touch frame IS the observed edge frame: `ACT_DISAPPEARED` counts down `actionArg`, not `actionTimer` (decomp `act_disappeared`), so there is no action-timer backdating to be had the way star_grab/key have it. A pipe writes no Usamune RESULT, so the source is always `counter`; a star grabbed earlier in the same run leaves a stale result behind and `IgtClock._result_is_fresh` is what keeps it out (pinned by test_warp.py, not by an argument about how long a star dance takes). WHY the delta was wrong, and when the payload igt may be believed: `tracking/segments.py`'s rta_frames clause — the arithmetic and the 626-sample measurement live there |

## Recipes

**Add a new event type:** tests first (`snap(**overrides)` fixture pattern from
test_star_grab.py) → `detectors/<name>.py` with `process(prev, curr) ->
list[Event]` → new memory fields go through addresses.py (+VERIFY) and a
defaulted GameSnapshot field → wire into `main.py` (resets before grabs) →
render in the relevant `ui/components/*.js` (e.g. `feed.js`) if user-visible →
document payload in README → full pytest + live check.

**Add a dust trick** (landing-cancel chain like rollouts / double jumps):
- *Same stat family* (another `jump`-type chain): ONE row in `TRICKS`
  (`detectors/dust.py`) + action ids in addresses.py (+VERIFY) + a test in
  test_dust.py. Aggregation, stats, UI all pick it up via the shared
  event_type. Done.
- *New stat family* (own `<x>_total`/`<x>_dustless` rate): the above, PLUS the
  per-family fan-out — Attempt fields + a `_dispatch` branch
  (tracking/projection.py), an ALTER TABLE migration (storage/db.py: MIGRATIONS
  + `_ATTEMPT_COLS` + `_attempt_params`), attempt_completed payload
  (tracking/service.py), `_attempt_json` (tracking/views.py), the row span in
  ui/components/practice.js, and a one-line `_dust_rate(...)` StatDef
  (stats/registry.py). Mirror the jumps commits on 2026-06-11
  (`git log --grep=jump`). If a THIRD family lands, generalize counts to a
  keyed structure instead of adding more columns.
- Timing rule (decomp-verified, do NOT re-derive from the spec — its §3 model
  is annotated as wrong): `frames_late = visible_landing_frames - 1`; one
  visible landing frame IS frame-perfect. Evidence: addresses.py.

**Locate an unknown memory value:** `tools/find_timer.py` (ticking counters) →
`tools/hunt_value.py` (exact displayed values) → `tools/watch_timer.py
ADDR:u16` (characterize across scenarios). Methodology and pitfalls:
docs/architecture.md → Memory hunting.
