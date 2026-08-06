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
| Memory addresses, action IDs, course/star names, traps, world topology | `memory/addresses.py` — THE registry; richly commented, read it. `WORLD_EDGES_TWO_WAY`/`_ONE_WAY` + `world_connections()` = directed (level, subarea) reachability under normal movement (**corrected 2026-07-27**: the one-way `(23, 19)` "DDD sub bay → BitFS" edge was wrong — BitFS is entered from the BASEMENT, `23 → 6 (area 3) → 19`, live-captured twice; a wrong edge here is not cosmetic, it made the corpus's independent walker route DDD → BitFS in one hop and forced a movement onto a `star_grabbed` start) — drives the segment builder's dropdown filtering AND — since 2026-08-01 — the matcher's own topological cancel (`.claude/rules/tracking-storage.md`, [Topological validity]), so a wrong row here now silently kills real attempts; validation still stays permissive. **Corrected again 2026-08-02, by the human reading `tools/topology_map.py`** — the tool exists for exactly this, because a MISSING edge only makes the rules stricter somewhere he never walked and the journal scoring is blind to it. Each Bowser course↔arena pair is TWO-WAY (losing the fight returns you to the course), and `(34, upstairs)` is GONE: *"if you win in bowser 3 you beat the game; if you lose in bowser 3, you go back into bowser in the sky"*, so the Bowser 3 arena has no edge into the castle at all. Only the Bowser 1/2 key cutscenes remain one-way. `tests/test_defaults_corpus.py::test_exit_node_matches_the_castle_layout` pins all three; **Corrected a third time 2026-08-02, and this one was a MISSING row rather than a wrong one**: `(19, _LOBBY)` — leaving BitFS puts you in the LOBBY, not back in the basement you entered from, and that is a movement TRICK he routes on (*"the fastest path to getting to upstairs is actually to go Bowser 2 -> Basement -> Re-enter bowser in the fire sea -> Exit to lobby -> Upstairs"*). Measured over both journals on the SETTLED area: BitFS -> Lobby x11 vs Basement x1; BitDW -> Lobby x27 (already covered); BitS -> Upstairs x2 vs Lobby x1, left OUT deliberately because three observations cannot tell a trick from a mis-sample. What the missing row cost is the argument for measuring the table rather than reasoning about it: BitFS sat 3 hops from Upstairs where the basement sat 2, so Rule 2 read the fastest real route as walking away and silently killed `Bowser 2 -> Upstairs` the instant he entered the pipe. **Adding it also forced a fix in `world_regions()`**: one-way rows are walked as UNDIRECTED there, which was only ever right because every one-way row was an arena cutscene (an arena has no other castle link, so its exit IS its ownership). BitFS has a real two-way door from the basement, so one undirected pass moved it into the lobby region on gameflow order and renamed its library group. Two-way edges are walked FIRST now; a one-way EXIT only claims what is still unowned — an exit says where you come OUT, never where a place belongs. `world_regions()` BFSes those same edges from `CASTLE_REGION_NODES` (gameflow order) to answer "which castle region owns this place" for the segment-origin taxonomy — BBH→courtyard, VCUtM→grounds, CotMC→basement, each arena→its exit's region; `region_for_node` adds the one case the BFS can't answer (subarea-less castle interior → lobby, the transient-lobby rule). `CASTLE_SECRET_STAR_AREAS` is MIPS-only ON PURPOSE |
| Endian decoding / typed reads | `memory/base.py` — the ONLY place that knows PJ64 byte order |
| Process attach / RDRAM discovery | `memory/pj64.py` |
| Object-pool decoding | `memory/objects.py` · test double: `memory/buffer.py`. **WHICH door/pole/enemy Mario touched — ANSWERED 2026-08-05, and the answer is the SPAWN POINT** (`OBJECT_HOME_POS` in addresses.py, whose comment carries the two proofs). `tools/probe_objects.py` dumps the whole object every time a `gMarioState` pointer changes and `--report` lists the distinct things touched; which `gMarioState` offsets hold an object is discovered rather than asserted (a word landing on a pool SLOT BOUNDARY — `+0x78`/`+0x7C`/`+0x80`/`+0x88` announced themselves). Pure core pinned by `tests/test_probe_objects.py`, six rules mutation-proved. Full detail: **[The spawn point is the identity](#the-spawn-point-is-the-identity)** below |
| Fields sampled each tick | `core/snapshot.py` |
| Event envelope / wire format | `core/events.py` |
| Star-grab + IGT logic | `detectors/star_grab.py` — docstrings carry the domain rationale; IGT itself comes from the shared `detectors/igt_clock.py` (result→counter→reconstructed) which ALSO stamps key.py's grand star and warp.py's pipe — every displayed time routes through it, never a frame delta. **A star is timed at the X-CAM, not the grab, and Usamune's own result store — not our counter — is the number.** Both halves were live-measured on 2026-08-01 and neither is re-derivable by reading the code; the evidence, the four `STOP` values, the subarea-local counter, and the bracket on the settle wait are in **`## Star timing`** below. `key.py` is deliberately NOT changed: whether `STOP` moves the grand star's number is unmeasured, and `ACT_JUMBO_STAR_CUTSCENE` has no fall/dance pair to derive one from |
| Whether an action counts as Mario ACTING | `memory/addresses.py` — three sets, read by `anchors.py`'s activity flag: `PASSIVE_ACTIONS` (idle/spawn, and ALSO `replay/activity.py`'s idle check — do not extend it for anchor-only reasons), `DEATH_ACTIONS`, and **`LEVEL_EXIT_ACTIONS` (2026-08-03)**. The last is the contiguous decomp block `0x1926-0x192D`: Mario is FLUNG out of a level with no control, **and the byte then LINGERS**. He died in WF, was flung to the castle, sat in the pause menu 92 s, and menu-warped back into WF with `mario_action` still reading `ACT_DEATH_EXIT` — so both of the arrival's anchors reported `mario_acted: true`, the unacted-reset discard could not fire, and the 44 frames between them banked a phantom 1.5 s reset (*"there's sometimes a Reset entry RIGHT when we start the map… The first time we enter a map should never be considered a reset"*). Measured across both journals: **62 anchors land on one of these and 62 of 62 read as having acted**; six of the seven members appear in his real play. Same lingering-state shape as the teleport fade-in. **FORWARD-ONLY** — `mario_acted` is baked into every historical anchor, so existing phantom rows are not repaired by this. **STILL OPEN**: a menu warp taken while Mario was mid-action (paused on a walk) still arrives with a real action on the byte, so the arrival can still bank a phantom; closing that needs the arrival's anchors COLLAPSED into one, which moves attempt start frames and is a decision, not a fix. **A FOURTH source, and it is not a set at all (2026-08-04, task 0084)**: the action the anchor ITSELF interrupted. The docstring has always said that one is swallowed; it swallowed one POLL, and a 60 Hz poll reads a 30 fps frame twice, so the next poll re-read the identical byte onto the FRESH attempt — *"warping to the beginning of a course inside a subarea results in an extra reset… this is a really common problem within subareas."* Exact, since `global_timer` IS the frame counter and a second poll of one value cannot have seen a new action: **1,325 of 3,558 anchors in the repo journal and 811 of 2,447 in the exe's**. `AnchorDetector._anchor_action` holds the action INSTANCE, not a frame window, and the first differing byte clears it for good — which the reload's own spawn supplies within 3 frames on 1,657 of the 1,686 anchors that see one, so anything longer than a blink behaves exactly as before. Every place that starts an anchor period latches, the console reset included. **The old journals cannot score this and `tools/measure_reset_stubs.py` says why** — `mario_acted` recorded the frame and never the action, so a walk resumed after the reload is indistinguishable after the fact from the anchor's own lingering byte; its question 3 is a strict upper bound, not a blast radius. The event carries `action` from this change on (inert), which makes the next round exact. **STILL OPEN, and it is the gravity half**: a reload that drops Mario into a fall or a slide enters a DIFFERENT action from the one latched, so it clears the latch and reads as the player acting. One instance in his journal (a CCM death respawn, journal ids 24768-24772, `mario_acted` 37 frames after the anchor); separating what the game did to Mario from what he did needs evidence task 0084's report did not carry, and the `action` field above is the head start for it |
| game_reset | `detectors/lifecycle.py` |
| Mid-course MOMENTS (the vocabulary a subsection is built from) | `detectors/moment.py` — `MOMENTS` is THE registry, one row per kind (`door_open`, `textbox`), and adding one touches nothing else: `segments.TRIGGERS["moment_reached"]` matches the payload's `kind` rather than naming any. ENTRY EDGE only (an action byte reads the same for every frame of a door animation). **NOT TARGET-GATED, since 2026-08-06, and the reversal is his own.** Task 0087's rule ("these should ONLY be tracked when we explicitly select / autoselect a star or segment") predates the recorder in the form that consumes moments, and the recorder is used with NO target set — pointing at what you just did is HOW a definition gets made. Two live reports in one message, one cause: *"I went into Whomp's Fortress, triggered the Whomp King dialogue, and now nothing popped up in the segment recorder tool"* and *"briefly I was able to detect the doors in HMC, but... I lost the ability to detect those"*. His journal scored it exactly: **207 moments, every one inside a target window, then a whole session across WF, HMC and SSL with ZERO of any kind** — the HMC target had retired on the level change into WF (projection's different-course rule) and every door and dialogue after it was correctly suppressed. A RECORDER-OPEN gate does not work either and is written into the guard so it is not re-proposed: he does the thing FIRST and opens the recorder afterwards. What the gate really protected is journal volume (the 2026-08-04 trim), so the ceiling is now the MOMENTS registry itself — a door and a textbox cost ~200 rows per two days of his play; a moment per wall kick would still undo the trim. The `target_active` parameter survives as an injection seam and `tests/test_composition.py::test_the_composition_root_gates_moments_on_NOTHING` fails if `build()` wires the target into it (mutation-proved). **A MOMENT'S TIME IS `counter + DISPLAY_TICK`, the same as a live-verified pipe** — a `DISPLAY_LAG_FRAMES = 1` sat on top of that for a day and made every door a frame slow: the 16 samples behind it were read off the `global_timer` DELTA the practice log was showing, which equalled the moment's own reading in 31 of 31 runs, so the offset was measured on one number and applied to another. `segments.IGT_ARM_SKEW_FRAMES` was the real fix and stands. ORDINALS count since `reset()`, wired by `main.build()` to `TrackerService.on_attempt_boundary` (practice_reset/state_loaded only — `game_reset` moves the clock backward and the base contract already self-heals). **There is no `first_controllable` moment on purpose**: `spawn.py` already emits the edge out of `ACT_INTRO_CUTSCENE`, which addresses.py calls the canonical Lakitu-skip timing start, and a second door onto one frame is the class `test_single_source.py` exists to stop |
| Attempt anchors (practice_reset / state_loaded) | `detectors/anchors.py` — anchors carry mario_acted + paused_frames_before + acted_tracking + save_pending (post-star save-screen latch → segment echo) + frames_since_dialog (textbox/intro-cutscene recency → segment echo shape 5: a run never splits/resets on a textbox); emits the mario_acted event; docstring covers classification (incl. the pause-warp shape: menu warp with IGT already ~0 → anchor from position change + pause streak), pause streak, and VERIFY notes. **An in-course AREA LOAD fires a `practice_reset` too** — Usamune zeroes the overall IGT on an area warp exactly as it does on an L-reset, and nothing here can tell them apart: measured 2026-08-01 against a read-only backup of the live journal, **496 of 825 in-course area edges carry a co-frame `practice_reset`**. The segment matcher already treats involuntary anchors as echoes (door, save prompt, dialogue); the ATTEMPT projector does not, so entering the pyramid closed the run as a reset and opened a new one. **FIXED 2026-08-01 — the anchor payload's `area_load`, and the discriminator is the DESTINATION area**: a course always starts in area 1, so a zero paired (by recency, not co-frame equality) with an edge into a NON-1 area is Mario going deeper, while a reset's own reload walks the byte 1→2→1 and zeroes on the way BACK. Three readings that look right and are not — warp action (6% of entries vs 21% of resets, backwards), nearby `warp_entered` (6% vs 4%), door recency (0%) — plus the 424 back-to-1 anchors that are load TAILS rather than resets, are all in `anchors.py`'s docstring with their counts. `projection._dispatch` records no attempt for one and opens an attempt only if none is open. Forward-only: a historical anchor has no such key, so `.get()` is falsy and every old row replays unchanged (verified against a backup of the live journal, 3220 rows, zero differences). STILL OPEN: walking back OUT of a subarea zeroes the counter the same way and is indistinguishable from a reset by destination — the inert `warp_op` on every anchor is the evidence for that one. **An IN-LEVEL TELEPORTER fires one too, and `area_load` structurally cannot see it (2026-08-03, task 0082)** — the CCM broken bridge and the WDW corner warps relocate Mario inside the SAME area, so there is no area edge to pair the zero with, and *"we can't complete these stars in the practice tool"*. The discriminator is **how recently `ACT_TELEPORT_FADE_OUT` ran**, not what Mario's action reads now: measured on the three demonstrated warps (journal ids 23199/23200, 23218/23219, 23231/23232), the counter zeroes on the very frame Mario crosses fade-out → fade-in, 42 frames after the pad (`act_teleport_fade_out` calls `level_trigger_warp` at actionTimer 20, and the delayed warp takes 20 more), so recency is 1 frame; but the action byte still reads `ACT_TELEPORT_FADE_IN` **125 and 172 frames later**, across a menu warp into WDW, so an `action`-only test would have swallowed two real boundaries. A level edge clears the pairing the way it already clears `_last_area_edge` — the action is shared with cap-course warps that really do leave the level. Payload key `teleport`, read by `projection._dispatch` (no attempt) and by `segments._anchor_echo` shape (6) (invisible to the matcher). **NOT suppressed at the detector, and that is the interesting call**: `segments._zeroes_usamune_igt` reads every anchor to know when Usamune's counter last restarted, so a dropped anchor would leave a segment running through the warp banking a time measured from the warp. Population across all four journals: **4 in-level teleports, 4 bogus resets — 4/4, with no anchor of that shape from any other cause** |
| Death detection | `detectors/death.py` — action-set edge + pending-warp pulse for void-outs (pit falls fire BEFORE level_changed; docstring carries why); closes open attempt as outcome "death". **CLOSED 2026-08-01 — measured, and it is CORRECT as it stands. Do not "fix" it.** This is the one time source that does not go through `igt_clock.py`: the payload carries `curr.igt_overall` RAW, while star/key/pipe times all add `DISPLAY_TICK`. The live gate (`uv run python tools/verify_death_clock.py`, behavioural not address — `USAMUNE_OVERALL` is already sampled, so no `VERIFY` row and `verify_addresses.py` is not the instrument) asked the human to pause and read the frozen timer. **Eight readings across three Usamune TIMER presets, unanimously the RAW counter.** So `DISPLAY_TICK` is a star-PATH calibration — it compensates for `_primary` back-computing to a touch frame, not for any offset in Usamune's display — and routing death through the clock would have put every historical death row 3 cs ABOVE what he saw, stars included (projection stamps a star's death attempt from this same payload). The same sitting retired a false alarm in the gate itself: `counter_tracked_cleanly` demanded the counter and the game frame move EXACTLY together and so cried PROBLEM on seven of eight healthy deaths, three of them for the counter moving MORE than the frame — impossible for a stall, and the tell that a 12-call non-atomic snapshot skews ±1 at each window end (`READ_SKEW_FRAMES`). Pure core pinned by `tests/test_verify_death_clock.py`, seven mutations proved |
| Level-change detection | `detectors/level.py` — stateful: remembers last EMITTED level, journals establishing/corrective events (from may equal to) so projection-side level tracking never runs stale; closes open attempts as abandoned |
| Dust tricks (dustless rollouts/jumps) | `detectors/dust.py` — TRICKS registry (one row per trick); docstring carries the decomp-verified landing-frame timing model; counts attach to attempts via projection.py |
| Stage detection (quick-select banner context) | `detectors/stage.py` — broadcast-only `stage_changed {course_id, level, area, mode}` where `mode` ∈ stars (main course 1-15) / bowser_course (BitDW/BitFS/BitS = lvl 17/19/21 → course 16/17/18; reds star + no-reds pipe segment) / arena (Bowser 1/2/3 = lvl 30/33/34; single fight) / castle (Castle Inside subarea) / None; keys on the resolved CONTEXT so a BitDW→BitFS course swap and a lobby↔upstairs subarea switch both re-emit (offered targets differ); reuses `course_for_level` (addresses.py) |
| area_changed / warp_entered / key_grabbed / spawned | `detectors/area.py` · `detectors/warp.py` · `detectors/key.py` · `detectors/spawn.py` — segment-primitive facts. **`area.py`, `level.py` and `spawn.py` each own an `IgtClock` and stamp `igt_frames`/`igt_source`/`igt` since 2026-08-06** (live report: *"some events have the timer next to them, most don't? I would expect the timer for all of them"*). `server/api.py`'s recorder row surfaces `payload.get("igt_frames")` and never computes one, deliberately — so a blank cell means the DETECTOR did not stamp, not that the time was unknown. `IgtClock.seed(prev)` is the shared half of the ordering every clock-owning detector uses (seed, detect, `observe(curr)`); `curr` must not be in history when the reading is taken, which is why there is no one-call wrapper. Forward-only: the raw counter at a historical edge was never journaled. `tests/test_place_events_carry_the_time.py` holds it, one test per detector plus a join against `_TIMELINE_STEP_TYPES`, all mutation-proved; area mirrors level.py's last-EMITTED discipline + stamps `from_transient` (source area not dwelt-in — every castle entry transits the lobby, so course exits read from=1 like a real lobby walk; area_enter's "coming from" rejects transients); key detector guards star_grab from misattributing Bowser keys AND carries Usamune IGT (via igt_clock) on fight-end grabs so a segment ending on the grand star matches Usamune's time, not a wall-frame delta. **warp.py carries it too since 2026-07-31** (live report: BitDW "No Reds" read 0'35"90, Usamune 0'35"96), so it is no longer stateless — it owns an `IgtClock` and observes every tick like key.py. The touch frame IS the observed edge frame: `ACT_DISAPPEARED` counts down `actionArg`, not `actionTimer` (decomp `act_disappeared`), so there is no action-timer backdating to be had the way star_grab/key have it. A pipe writes no Usamune RESULT, so the source is always `counter`; a star grabbed earlier in the same run leaves a stale result behind and `IgtClock._result_is_fresh` is what keeps it out (pinned by test_warp.py, not by an argument about how long a star dance takes). WHY the delta was wrong, and when the payload igt may be believed: `tracking/segments.py`'s rta_frames clause — the arithmetic and the 626-sample measurement live there. **warp.py became a HELD EMIT 2026-08-04 (task 0081) and moved to SECOND in the chain; since 2026-08-05 the hold is skipped entirely for a painting or portal, whose destination is already written at the touch frame** — full detail below: [The entrance touch names where it leads, on its own frame](#the-entrance-touch-names-where-it-leads-on-its-own-frame) |


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
feels like it's broken and laggy"). The row is PUBLISHED the moment Usamune's
own written answer AGREES with our derivation of the same x-cam (1-3 frames
typically, 0 on a ground grab), with `PUBLISH_WAIT_FRAMES` (12, ~400 ms) only
as the backstop; the watch runs on, and if the answer CHANGES after that,
`star_time_corrected` follows and the row is rebuilt. **Agreement, never "the
first write"** — Usamune writes 2-3 times per grab even on an ordinary star
(WF Wild Blue +1=328 then +3=330) and the FIRST of that burst is the grab-time
value, so leaving on it published a number the correction then moved, on
screen, on most grabs (live report 2026-08-01: "it writes the entry into the
system… and then the xcam happens, which overrides the original entry… it
should be hidden to the user"). `AGREEMENT_FRAMES` is 1 and not 2 for exactly
that reason: the counter path runs 1-2 frames under Usamune, so a two-frame
tolerance matches the grab write. Every grab journals `published_after` and
its `result_writes` burst, inert, so the next round tunes this from ordinary
play instead of from argument. And when
`IgtClock.counter_may_be_subarea_local()` says the counter's zero point may be
an AREA load, the publish waits the FULL window instead: our number there is
the time since the pyramid door, which is exactly the impossible PB he saw
flash. **ARRIVING in a course is not that, and read as if it were until
2026-08-02** — the level byte changes on one frame but the area byte SETTLES
afterwards (3->2->1), so the load's own area edges land beside the load's own
counter zero long after the level edge that explains them, and the flag
re-armed on the way in. Live report, and it is the cleanest bug report in this
file: *"the FIRST star that I grab after entering a course has an
exceptionally high amount of delay… BUT THEN ALL THE OTHER STARS ARE ACTUALLY
PERFECTLY TIMED"*, plus his own probe — enter, reset immediately, grab, and it
was instant, because an L-reset zeroes the counter with no area edge beside
it. His journal scored it exactly: `published_after` was **45 frames on the
first grab after every course entry and 1 frame on every other grab**, binary,
no middle. `LEVEL_LOAD_TAIL_FRAMES` (60) is the fix and the window is
measured, not chosen: across 911 level entries the load's last area edge lands
at **44-47 frames** every time and the earliest genuine warp deeper into a
level appears at 60. Six of the nine slow grabs in that session were course
entries and are now fast; the other three were real subarea stars and are
still deliberately slow. **Those three are fast now too, and the wait is
GONE (2026-08-02).** His idea: *"If we cache the time it took for the player
to enter the subarea, then the xcam time is just the cached
subarea_entry_time plus the time it took to finish the subarea. Isn't that
basically what Usamune is doing anyway?"* — and his journal already held the
proof, because Usamune's write burst contains both halves (LLL Elevator
`[[2, 388], [27, 686]]` → 298; SSL Pyramid `[[0, 69], [1, 71], [27, 551]]` →
480). `IgtClock.carried_igt_at_xcam` keeps what the counter reached before the
warp and adds it back, so the whole star is OURS to state at the x-cam.
Shipped in two steps on purpose: first publishing on AGREEMENT with Usamune
(27 frames), then, after his session scored it **4/4 exact, diff 0**, at the
x-cam itself with no wait at all. Two guards hold it up, both mutation-proved:
the carry is refused unless the area edge went into a NON-1 area (anchors.py's
discriminator — a reset's reload lands back in area 1, and carrying a previous
run's time across a reset is the one failure here that would be silent), and
`_answer` must recognise Usamune's EARLY write as an echo of our own
subarea-local counter (`_PendingGrab.subarea_local_igt`) or the instant
publish ships the impossible PB at speed. The correction watch still runs, so
the remaining failure — a base captured across missed polls — self-heals. So the correction is a backstop now, not a routine event. The
correction is a compensating event like `attempt_cleared`:
`projection.time_corrections` folds it into the GRAB's own payload (so the star
attempt, a segment closed by the same grab, and the 100-coin reattribution all
read one number and none of them knows a correction exists), and
`service._track` re-projects when one is journaled. Where no write comes
(`STOP` of Grab or None, both
already illegal) the counter derivation stands in and `igt_source` reads
`"counter"` instead of `"result"`, which IS the legality signal on a star row;
that case keeps the subarea error and cannot be fixed from a counter that
restarted. **Carrying our own base across the warp WAS the fix, and this file
said it could not be done for a day** — "an L-reset zeroes the counter the same
way an area warp does and telling them apart inside the clock is how a wrong
time gets recorded silently". The premise was already false when it was
written: anchors.py had measured the discriminator (destination area) the day
before, for the attempt side of the identical ambiguity. A limit asserted from
one file's own vantage point survives exactly until someone reads the file next
door. See the carry, below. **(5) The 45-frame wait is BRACKETED at both ends by his second
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

### The 45-frame wait is GONE (2026-08-04, task 0083) — we own the epoch, Usamune owns the tick

Everything above stands except the settle WAIT. `USAMUNE_OVERALL` is a LEG's
clock, not a star's, and until now the clock could not tell a RETRY's own
reload from a warp deeper in — both move the area byte and zero the counter —
so it flagged its own number unusable and the row waited `RESULT_SETTLE_FRAMES`
(1.5 s) for a correction. Read back from his own journal's `published_after`:
**22 of 128 grabs took that branch and only 6 were genuine multi-leg stars.**
The other 16 had Usamune's final answer already written at +0 or +1. WF "Shoot
into the Wild Blue" is in that list four times — the star he named in the
report.

**`detectors/counter_epoch.py` is the fix, and it is a single-source move
rather than a new heuristic.** `anchors.py` had already measured this exact
question against the whole journal (destination area for a door, fade-OUT
recency for an in-level teleporter) and stamps its verdict on every anchor as
`area_load`/`teleport`; `igt_clock.py` was answering it privately and worse.
`EpochTracker` is the one door now: it BANKS a leg at every restart the level
caused and ZEROES at every restart the player caused, so the whole star is
always `banked + the counter`, and `counter_may_be_subarea_local()` stopped
meaning "we cannot state this time".

Three consequences worth knowing:

* **It accumulates rather than caching one leg**, and that is measured rather
  than generality for its own sake: of 875 grabs, 851 cross no involuntary
  restart, 22 cross one, and **2 cross two — the CCM 100-coin rows that
  published 37 seconds short** (`0'51"43` against `1'28"86`, journal ids
  23370/23799). A single cached half could never have stated those.
* **`LEVEL_LOAD_TAIL_FRAMES` moved into the shared rule**, where it stops the
  clock banking a leg for a course ENTRY's own area settling. Confirmed to
  change no anchor classification: **0 of 29 `area_load` anchors sit inside a
  tail.**
* **`_usamune_answer` asks two different questions of the same write.** *Is it
  final?* makes a MIDAIR grab prove the write landed strictly after the x-cam;
  *what is the best we know?* takes the loose bracket. All five corrections in
  the live journal came from conflating them — a write first OBSERVED on the
  x-cam frame can still be the grab-time write, and taking it published a
  number the watch then moved 1.5 s later (his report: `22"00`, then `22"06`).

KNOWN RESIDUAL, stated rather than discovered later: walking back OUT of a
subarea on foot zeroes the counter exactly as a retry does, and the destination
area cannot separate them (the attempt side of anchors.py says the same about
itself). It appears **0 times in those 875 grabs**, and the correction watch
still covers it. That watch is unchanged and still runs to
`RESULT_SETTLE_FRAMES`; it simply no longer bounds any publish.

## The entrance touch names where it leads, on its own frame

**Task 0081, 2026-08-04; corrected by measurement 2026-08-05.** `warp_entered`
is the ENTRANCE TOUCH — the frame Mario collides with a painting, portal, hole
or pipe. Measured over 140 castle entries in the repo journal, the level load
follows it by a constant **77 frames** for a painting/portal (range 76-77) and
**23** for a pipe (23-23). So a movement measured to the LOAD banks the fade as
travelling; on `SSL → LLL` that fade is 60% of the recorded time.

**This section said the opposite for one day, and that is the lesson.** It read
"the destination is unknowable at the touch frame, from any address… **do not
re-derive this by hunting the address**" — reasoned from decomp, never watched
in RAM, and written down with an instruction not to check it. It cost three
rounds of live reports, because the event was withheld until the level byte
moved and the practice-log row therefore landed **2.9 s** after the portal (the
journal and `data/ui_log.jsonl` split that: 2.57 s of hold, 0.33 s of websocket
and render). His ruling, 2026-08-05: *"we should never be injecting that much
artificial waiting into the system… if I can see the timer on my screen, we
should be able to detect it as well; if you can't yet, you haven't found the
right instrumentation."*

**`tools/probe_warp_block.py`** is that instrumentation — a read-only trace of
the whole warp block around each touch, safe to run beside a live session. 15
consecutive castle entries settled it:

* a **painting or portal** writes `sWarpDest` at or before the ACT_DISAPPEARED
  frame. 13 of 13 named the destination at the touch, and 12 of those differed
  from the previous warp's destination — a real write, not a stale read;
* a **pipe** is the one genuinely delayed warp: `sDelayedWarpOp` pulses 0x04 a
  frame or two in, counts 20 frames down, and `sWarpDest` is written then —
  3 frames before the level byte moves. Both pipes read a STALE castle
  destination at the touch.

So `type != 0` is NOT a freshness test on its own: it also survives a completed
painting warp (read live standing idle in DDD). The detector watches all four
bytes of the struct and treats it as live only if it CHANGED within
`FRESH_WINDOW_FRAMES` (4, slack for the poller, not for the game); the two
pipes are the negative cases that prove it, and the first value seen after
start-up is deliberately unstamped, because unstamped means hold.

The destination matters at all because the castle basement alone hosts five
exits (HMC, LLL, SSL, DDD, BitFS): an end condition reading only "a warp in the
castle" lets walking into HMC record a false MIPS Clip.

Publish order: the struct was just written (`to` = its level — the touch frame
for a painting, touch+20 for a pipe) → a level edge (`to` = the new level) → an
area edge (`to` = the unchanged level) → two bounds that guarantee nothing that
fired before this can stop firing, each publishing `to: None` — a backward
`global_timer` (console reset) and `HOLD_CAP_FRAMES` 240, which is also what
covers an in-level teleporter (it relocates Mario inside his own area, so no
edge ever arrives). `frame` and the igt trio stay the TOUCH's however late the
publish lands.

**`pending_warp_op` is not one of those bounds, and believing it could be cost
a live round (2026-08-05).** A grace window on that flag looked like the precise
way to resolve a teleporter promptly and published `to: None` on every real
painting entry instead. The game clears `sDelayedWarpOp` when the delayed warp
**initiates** and ~57 more frames of fade follow before the level byte moves:
**the flag goes quiet in the MIDDLE of the wait, not at the end of it.** His
journal, ids 25415/25371 — touch at 2519145, `level_changed 6 → 23` at 2519222,
exactly 77 frames apart, and the event published around frame 30 of that. The
regression test drives that real shape (high 20, quiet 57, edge at 77) and
asserts silence on every frame of it.

**It moved to SECOND in `main.build_detectors`, behind star_grab only.** On the
release tick one poll carries a touch that happened 77 frames ago and the level
change happening now; journaled the other way round the level change closes the
attempt the touch belonged to and one movement records as two. Same rule and
same reason as star_grab leading. Pinned by `tests/test_composition.py`.

**Historical rows carry no `to`, and `projection.warp_destinations` recovers it
on replay** — see `.claude/rules/tracking-storage.md`. Both obvious readings of
such a row were measured over the real journal and both are wrong: refusing it
vanishes 54 of 106 recorded segment successes, waving it through fabricates
105.

**OPEN, and the one thing this change does not know.** Of 202 castle→course
entries after the detector existed, 128 carry a touch and the rest do not. Every
touched arrival is unpaused, which is a clean one-way separation, but the
no-touch entries cannot be split cleanly into menu warps (fine — a fabricated
edge has no collision) and real entries the detector missed (not fine — those
movements now record nothing). Depending on how wide the pause window is drawn
the unexplained share is **between 2% and 21%**. Only a live sitting settles it:
walk into the same painting several times and count. Until then, treat a
movement that records nothing as possibly this rather than as a matcher bug.

## The spawn point is the identity

**Live-measured 2026-08-05 across one ordinary session of his, with his own
labels as ground truth.** A moment used to be `kind + level + ORDINAL` —
"the 5th door in the basement". The ordinal is a property of the POOL, not of
the door, and this is the measurement that proves it.

**The pool slot is not an identity.** His three castle-basement doors (HMC,
the moat door, DDD) held slots 3/2/0. A death exit rebuilt the area and the
same three doors came back as 38/42/44. He warped out and back and they were
3/2/0 again. Same doors, three different "5th door you opened".

**The live position is not one either.** Across 21 grabs of one SSL bob-omb the
current position (`+0xA0`) took 14 distinct values — a respawning enemy is a
genuinely different object each time.

**`OBJECT_HOME_POS` (+0x164, Vec3f) is.** Keyed on `(level, area, behaviour,
home)`, his 13 door captures collapsed onto exactly the three names he gave
them, across the reload; the bob-omb's 88 grabs across **19 pool slots**
collapsed onto one. Corroborating single words exist and are reported, but the
spawn point is the one that held for both a stationary thing and a moving one.

**Two limits, stated rather than discovered later.** An object the GAME creates
mid-play never gets a home — Mario, a star popping out of a box, the spawn
marker — so those read `(0, 0, 0)` and several of them collapse into one row;
the report prints that caveat itself. And two areas of one level can host the
same physical door at the same coordinates (the basement↔lobby warp door
appears in both), which is why the area is part of the key and not decoration.

**The two bugs that hid this for a round, because both look like a clean
result.** Volatility judged across ALL captures let Mario's own object mark
POSITION volatile, which discarded the only offset that worked. And the
two-frame re-read that confirms an offset is stable lands on a REBUILT pool if
the game reloaded inside the window, so the slot then holds whatever moved in
and the object reads as having moved; the probe now drops that second read
instead of scoring it.

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
