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
| Star-grab + IGT logic | `detectors/star_grab.py` — docstrings carry the domain rationale; IGT itself comes from the shared `detectors/igt_clock.py` (result→counter→reconstructed) which ALSO stamps key.py's grand star and warp.py's pipe — every displayed time routes through it, never a frame delta. **A star is timed at the X-CAM, not the grab, and Usamune's own result store — not our counter — is the number.** Both halves were live-measured on 2026-08-01 and neither is re-derivable by reading the code; the evidence, the four `STOP` values, the subarea-local counter, and the bracket on the settle wait are in **`## Star timing`** below. `key.py` is deliberately NOT changed: whether `STOP` moves the grand star's number is unmeasured, and `ACT_JUMBO_STAR_CUTSCENE` has no fall/dance pair to derive one from |
| game_reset | `detectors/lifecycle.py` |
| Attempt anchors (practice_reset / state_loaded) | `detectors/anchors.py` — anchors carry mario_acted + paused_frames_before + acted_tracking + save_pending (post-star save-screen latch → segment echo) + frames_since_dialog (textbox/intro-cutscene recency → segment echo shape 5: a run never splits/resets on a textbox); emits the mario_acted event; docstring covers classification (incl. the pause-warp shape: menu warp with IGT already ~0 → anchor from position change + pause streak), pause streak, and VERIFY notes. **An in-course AREA LOAD fires a `practice_reset` too** — Usamune zeroes the overall IGT on an area warp exactly as it does on an L-reset, and nothing here can tell them apart: measured 2026-08-01 against a read-only backup of the live journal, **496 of 825 in-course area edges carry a co-frame `practice_reset`**. The segment matcher already treats involuntary anchors as echoes (door, save prompt, dialogue); the ATTEMPT projector does not, so entering the pyramid closes the run as a reset and opens a new one. OPEN — not fixed, and not fully measured either: how many of those 496 recorded a VISIBLE row is unknown (the unacted/AFK/castle discards eat some), and the obvious discriminator (area changed while the level held) also matches an L-reset taken inside a subarea, so it needs measuring rather than reasoning |
| Death detection | `detectors/death.py` — action-set edge + pending-warp pulse for void-outs (pit falls fire BEFORE level_changed; docstring carries why); closes open attempt as outcome "death". **CLOSED 2026-08-01 — measured, and it is CORRECT as it stands. Do not "fix" it.** This is the one time source that does not go through `igt_clock.py`: the payload carries `curr.igt_overall` RAW, while star/key/pipe times all add `DISPLAY_TICK`. The live gate (`uv run python tools/verify_death_clock.py`, behavioural not address — `USAMUNE_OVERALL` is already sampled, so no `VERIFY` row and `verify_addresses.py` is not the instrument) asked the human to pause and read the frozen timer. **Eight readings across three Usamune TIMER presets, unanimously the RAW counter.** So `DISPLAY_TICK` is a star-PATH calibration — it compensates for `_primary` back-computing to a touch frame, not for any offset in Usamune's display — and routing death through the clock would have put every historical death row 3 cs ABOVE what he saw, stars included (projection stamps a star's death attempt from this same payload). The same sitting retired a false alarm in the gate itself: `counter_tracked_cleanly` demanded the counter and the game frame move EXACTLY together and so cried PROBLEM on seven of eight healthy deaths, three of them for the counter moving MORE than the frame — impossible for a stall, and the tell that a 12-call non-atomic snapshot skews ±1 at each window end (`READ_SKEW_FRAMES`). Pure core pinned by `tests/test_verify_death_clock.py`, seven mutations proved |
| Level-change detection | `detectors/level.py` — stateful: remembers last EMITTED level, journals establishing/corrective events (from may equal to) so projection-side level tracking never runs stale; closes open attempts as abandoned |
| Dust tricks (dustless rollouts/jumps) | `detectors/dust.py` — TRICKS registry (one row per trick); docstring carries the decomp-verified landing-frame timing model; counts attach to attempts via projection.py |
| Stage detection (quick-select banner context) | `detectors/stage.py` — broadcast-only `stage_changed {course_id, level, area, mode}` where `mode` ∈ stars (main course 1-15) / bowser_course (BitDW/BitFS/BitS = lvl 17/19/21 → course 16/17/18; reds star + no-reds pipe segment) / arena (Bowser 1/2/3 = lvl 30/33/34; single fight) / castle (Castle Inside subarea) / None; keys on the resolved CONTEXT so a BitDW→BitFS course swap and a lobby↔upstairs subarea switch both re-emit (offered targets differ); reuses `course_for_level` (addresses.py) |
| area_changed / warp_entered / key_grabbed / spawned | `detectors/area.py` · `detectors/warp.py` · `detectors/key.py` · `detectors/spawn.py` — segment-primitive facts; area mirrors level.py's last-EMITTED discipline + stamps `from_transient` (source area not dwelt-in — every castle entry transits the lobby, so course exits read from=1 like a real lobby walk; area_enter's "coming from" rejects transients); key detector guards star_grab from misattributing Bowser keys AND carries Usamune IGT (via igt_clock) on fight-end grabs so a segment ending on the grand star matches Usamune's time, not a wall-frame delta. **warp.py carries it too since 2026-07-31** (live report: BitDW "No Reds" read 0'35"90, Usamune 0'35"96), so it is no longer stateless — it owns an `IgtClock` and observes every tick like key.py. The touch frame IS the observed edge frame: `ACT_DISAPPEARED` counts down `actionArg`, not `actionTimer` (decomp `act_disappeared`), so there is no action-timer backdating to be had the way star_grab/key have it. A pipe writes no Usamune RESULT, so the source is always `counter`; a star grabbed earlier in the same run leaves a stale result behind and `IgtClock._result_is_fresh` is what keeps it out (pinned by test_warp.py, not by an argument about how long a star dance takes). WHY the delta was wrong, and when the payload igt may be believed: `tracking/segments.py`'s rta_frames clause — the arithmetic and the 626-sample measurement live there |


## Star timing — the x-cam, the settle wait, and the evidence for both

Everything below was measured live with the human on 2026-08-01 across three
sittings. It is kept verbatim rather than summarised because each claim is
load-bearing and none of it can be recovered from the code.

**FIXED 2026-08-01: a star is timed at the X-CAM, and this was a LEGALITY bug
rather than an accuracy one.** Usamune's `STOP` decides when Usamune stops:
*Grab* = touching the star, *Xcam* = touching the GROUND after the grab,
*GrabX* = stop at the grab then update at the x-cam (manual). Leaderboards
accept `STOP ∈ {GrabX, Xcam}` and any `DISPLAY` except `Hide`, so the
grab-frame number we recorded by default was invalid, not merely early. Three
sittings settled it. **(1)** `tools/verify_star_stop.py`, 10/10 across three
TIMER presets: his screen is always `USAMUNE_STAR_RESULT` once its write
SETTLES — never the value at our action edge, and never where
`USAMUNE_OVERALL` came to rest (that runs on to the star-select screen,
+40..+79). Gaps were 0 under GRAB and 2..39 frames otherwise: **not constant,
so no offset could have fixed it.** **(2)** `tools/derive_xcam.py`, scored
automatically against that ground truth with no human reading: **the x-cam
moment IS the star-dance entry, and Usamune's number is that frame's
`USAMUNE_OVERALL` + `DISPLAY_TICK`** — error −1/−2/−1/−1 over four midair
grabs, against −4/−11/−23/−39 for the grab frame. So the `counter` path was
never wrong about the arithmetic, only about WHICH FRAME it read. **(3)** What
shipped: `STAR_DANCE_ACTIONS` (addresses.py) is the x-cam edge. A GROUND grab
enters a dance ON the grab frame (his words: "I just simply ran into the star…
grabbing and xcam is identical") so nothing defers there; a MIDAIR grab is
HELD through `ACT_FALL_AFTER_STAR_GRAB` and emitted on landing, so
**`star_collected` is no longer synchronous with the grab** — `frame` is the
x-cam and the new `grab_frame` carries the touch. `IgtClock.igt_at_xcam`
believes the result store only when its write landed at or after the x-cam
frame: under GrabX Usamune writes it TWICE and the grab-time write sits well
inside `RESULT_FRESH_FRAMES` of a short fall, which is precisely how we kept
reading the illegal number. A grab that never reaches a dance (savestate load,
level change, Usamune reset, or a 300-frame backstop) is still journaled, with
`igt_timed_at: "grab"` saying which moment it is. All of it derives from
Mario's actions, so the recorded time is legal whatever his TIMER menu says —
the manual's footnote *"the in-game timer keeps running internally"*,
confirmed under every STOP value, is what makes that possible. The residual ±1
frame is OUR torn read, not Usamune: a snapshot is twelve `ReadProcessMemory`
calls, so the action and the counter in one sample can straddle a game frame.
**Still true, still unused: `STOP` is INFERABLE from the write pattern** —
*write at the grab?* × *write later?* separates all four values
(None/Grab/GrabX/Xcam) out of the 150 frames of `igt_result` history
`IgtClock` already keeps, so no settings block in memory is needed to know
whether an OLD row was legally timed. **(4) His gate run, 11 grabs, found the
OTHER half — `USAMUNE_OVERALL` is SUBAREA-LOCAL.** Nine single-area stars
scored +0; the two he took inside a subarea were 356 and 502 frames low (LLL
"Hot-Foot-It into the Volcano" 0'40"63 vs 0'52"46, SSL "Inside the Ancient
Pyramid" 0'02"43 vs 0'19"13) — our number was the time since he entered the
volcano/pyramid, Usamune's was the whole star. The counter restarts at an area
warp, so deriving the x-cam correctly is necessary and not sufficient: **the
x-cam says WHICH MOMENT, Usamune's own result write says WHAT NUMBER.** The
emit therefore WATCHES `RESULT_SETTLE_FRAMES` (45) past the x-cam and takes the
write if one lands at or after it — measured at +0..2 on ordinary stars and
+27..41 on those two. **It no longer WAITS that long, since 2026-08-01** (live
report: "when I land, there's STILL a ton of delay after that… now the tool
feels like it's broken and laggy"). The row is PUBLISHED at
`PUBLISH_WAIT_FRAMES` (12, ~400 ms — every measured write lands +1..+9) and the
watch runs on; if the answer CHANGES after that, `star_time_corrected` follows
and the recorded row is rebuilt. **A DEADLINE, never "publish on the first
write"** — Usamune writes 2-3 times per grab even on an ordinary star (WF Wild
Blue +1=328 then +3=330), so leaving on the first published a number the
correction then moved, on screen, on most grabs (live report 2026-08-01: "it
writes the entry into the system… and then the xcam happens, which overrides
the original entry… it should be hidden to the user"). And when
`IgtClock.counter_may_be_subarea_local()` says the counter's zero point may be
an AREA load, the publish waits the FULL window instead: our number there is
the time since the pyramid door, which is exactly the impossible PB he saw
flash. So the correction is a backstop now, not a routine event. The
correction is a compensating event like `attempt_cleared`:
`projection.time_corrections` folds it into the GRAB's own payload (so the star
attempt, a segment closed by the same grab, and the 100-coin reattribution all
read one number and none of them knows a correction exists), and
`service._track` re-projects when one is journaled. Where no write comes
(`STOP` of Grab or None, both
already illegal) the counter derivation stands in and `igt_source` reads
`"counter"` instead of `"result"`, which IS the legality signal on a star row;
that case keeps the subarea error and cannot be fixed from a counter that
restarted. NOT built, and named so it is not re-derived by guess: carrying our
own base across an area warp would remove the wait and work under illegal
settings too, but an L-reset zeroes the counter the same way an area warp does
and telling them apart inside the clock is how a wrong time gets recorded
silently. **(5) The 45-frame wait is BRACKETED at both ends by his second
subarea run (five grabs, 2026-08-01), and the ceiling is the surprising
half.** Usamune never writes the answer once — every grab took 2-3 writes, the
early ones echoing our counter (write = that sample's counter + 1) at the grab
and at the dance entry, with the whole-star correction 26-28 frames past the
dance entry on a subarea star (SSL Pyramid +6=74 → +32=545; LLL Elevator
+1=465, +14=479, +41=777). So the emit cannot leave on the first write, and at
that moment nothing distinguishes "no correction is coming" from "not yet",
which is why the wait is unconditional. The OTHER end: leaving the course
ZEROES the store (WF Wild Blue +1=328, +3=330, **+92=0**), so a window widened
"for safety" journals 0'00"00. `StarGrabDetector.RESULT_SETTLE_BRACKET`
carries the evidence and `tests/test_star_grab.py` pins the constant inside
it. That same zero was scoring the gate itself: `derive_xcam.py` took the last
write in its 8 s observation window as ground truth and reported a +327
failure against a correct journal entry — it now scores at the emit's own
deadline and NAMES the writes it ignored. All five journaled times matched
Usamune exactly. **key.py is deliberately NOT changed**: whether STOP moves
the grand star's number is unmeasured, and `ACT_JUMBO_STAR_CUTSCENE` has no
fall/dance pair to derive one from

## Recipes

**Add a new event type:** tests first (`snap(**overrides)` fixture pattern from
test_star_grab.py) → `detectors/<name>.py` with `process(prev, curr) ->
list[Event]` → new memory fields go through addresses.py (+VERIFY) and a
defaulted GameSnapshot field → wire into `main.py` (order rationale lives in
`build_detectors`' own docstring; the rule that bites is that an event
describing an EARLIER frame — a held emit — is published before every detector
that closes an attempt, or one thing records as two) →
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
