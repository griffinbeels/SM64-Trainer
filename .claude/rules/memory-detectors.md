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
| Memory addresses, action IDs, course/star names, traps, world topology | `memory/addresses.py` — THE registry; richly commented, read it. `WORLD_EDGES_TWO_WAY`/`_ONE_WAY` + `world_connections()` = directed (level, subarea) reachability under normal movement — drives the segment builder's dropdown filtering ONLY (warp menu can fabricate any edge; validation/matcher stay permissive); `world_regions()` BFSes those same edges from `CASTLE_REGION_NODES` (gameflow order) to answer "which castle region owns this place" for the segment-origin taxonomy — BBH→courtyard, VCUtM→grounds, CotMC→basement, each arena→its exit's region; `region_for_node` adds the one case the BFS can't answer (subarea-less castle interior → lobby, the transient-lobby rule). `CASTLE_SECRET_STAR_AREAS` is MIPS-only ON PURPOSE |
| Endian decoding / typed reads | `memory/base.py` — the ONLY place that knows PJ64 byte order |
| Process attach / RDRAM discovery | `memory/pj64.py` |
| Object-pool decoding | `memory/objects.py` · test double: `memory/buffer.py` |
| Fields sampled each tick | `core/snapshot.py` |
| Event envelope / wire format | `core/events.py` |
| Star-grab + IGT logic | `detectors/star_grab.py` — docstrings carry the domain rationale; IGT itself comes from the shared `detectors/igt_clock.py` (result→counter→reconstructed) which ALSO stamps key.py's grand star — every displayed time routes through it, never a frame delta |
| game_reset | `detectors/lifecycle.py` |
| Attempt anchors (practice_reset / state_loaded) | `detectors/anchors.py` — anchors carry mario_acted + paused_frames_before + acted_tracking + save_pending (post-star save-screen latch → segment echo) + frames_since_dialog (textbox/intro-cutscene recency → segment echo shape 5: a run never splits/resets on a textbox); emits the mario_acted event; docstring covers classification (incl. the pause-warp shape: menu warp with IGT already ~0 → anchor from position change + pause streak), pause streak, and VERIFY notes |
| Death detection | `detectors/death.py` — action-set edge + pending-warp pulse for void-outs (pit falls fire BEFORE level_changed; docstring carries why); closes open attempt as outcome "death" |
| Level-change detection | `detectors/level.py` — stateful: remembers last EMITTED level, journals establishing/corrective events (from may equal to) so projection-side level tracking never runs stale; closes open attempts as abandoned |
| Dust tricks (dustless rollouts/jumps) | `detectors/dust.py` — TRICKS registry (one row per trick); docstring carries the decomp-verified landing-frame timing model; counts attach to attempts via projection.py |
| Stage detection (quick-select banner context) | `detectors/stage.py` — broadcast-only `stage_changed {course_id, level, area, mode}` where `mode` ∈ stars (main course 1-15) / bowser_course (BitDW/BitFS/BitS = lvl 17/19/21 → course 16/17/18; reds star + no-reds pipe segment) / arena (Bowser 1/2/3 = lvl 30/33/34; single fight) / castle (Castle Inside subarea) / None; keys on the resolved CONTEXT so a BitDW→BitFS course swap and a lobby↔upstairs subarea switch both re-emit (offered targets differ); reuses `course_for_level` (addresses.py) |
| area_changed / warp_entered / key_grabbed / spawned | `detectors/area.py` · `detectors/warp.py` · `detectors/key.py` · `detectors/spawn.py` — segment-primitive facts; area mirrors level.py's last-EMITTED discipline + stamps `from_transient` (source area not dwelt-in — every castle entry transits the lobby, so course exits read from=1 like a real lobby walk; area_enter's "coming from" rejects transients); key detector guards star_grab from misattributing Bowser keys AND carries Usamune IGT (via igt_clock) on fight-end grabs so a segment ending on the grand star matches Usamune's time, not a wall-frame delta |

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
