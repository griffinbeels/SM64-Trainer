---
paths:
  - "src/sm64_events/inputs/**"
  - "src/sm64_events/server/inputs_api.py"
  - "src/sm64_events/ui/components/inputtimeline.js"
  - "src/sm64_events/ui/components/controllerpanel.js"
  - "src/sm64_events/ui/components/attemptdrawer.js"
  - "src/sm64_events/ui/overlay.html"
  - "tools/probe_inputs.py"
  - "tools/dump_inputs.py"
  - "tools/export_overlay.py"
---

# Controller capture — where to change what

| To change... | Edit |
|---|---|
| What one frame of pad state IS, and the razor that identifies the pad | `inputs/frame.py` — `decode()` is the 250 Hz path and reads only the four pad fields a timeline needs plus Mario's block (every byte-level fact of both structs decodes HERE; the sampler only reads and pairs); `fits_controller()` is the cold path (the address gate, the address hunt) and checks the struct AGAINST ITSELF: the processed stick must be the raw stick through the game's dead zone, the magnitude their hypotenuse clamped at the cap. Every version-independent fact — the offsets, the button bit table, the dead zone, `CONTROLLER_SETTLE_PHASE` — lives in `memory/addresses.py`; the ADDRESS lives in `memory/layout.py` |
| How often the pad is read, and which reading counts | `inputs/sampler.py` + the loop in `server/poller.py`. **The rate is set by the CONTROLLER, not by the game** — see [Why 250 Hz](#why-250-hz) |
| Where captured frames are stored | `inputs/store.py` — run-length chunks (migration v27), keyed by wall clock and frame counter, NEVER by attempt id. `ChunkWriter` buffers and flushes every 300 frames or on a backward counter -- **and the moment an attempt SETTLES** (`TrackerService.on_attempt_settled`, fired on `attempt_completed`/`practice_reset`/`state_loaded`, wired to `close` in `main.py`). Without that last trigger a finished attempt's tail sits in memory for up to ten seconds and the timeline draws a SHORT track and clamps its playhead there while the footage runs on: his 18"43 PB read 12"90, 387 of 551 frames, with the db holding all 551 and no gaps (2026-08-28). It is an event callback rather than a step in the timeline endpoint because the sampler's own `add` runs on the poll loop -- flushing from an HTTP handler would race it. **Every attempt's input persists** in the journal's own db, saved replay or not, and is never evicted (the clip ring is; the chunks are not); only `delete_session` and `wipe_all_history` remove chunks, with their session. So after a restart the timeline draws for anything ever played since capture landed, and the video only for saved clips |
| Folding frames into runs, and the zero-based axis a track is drawn on | `inputs/runs.py` — `collapse(frames, same)` is THE run-length loop (the store, the document and the timeline payload each pass only their own `same`), and `capture_axis` is THE zero-basing, which lays a counter restart end to end and keeps a hole a hole. Four copies of that loop used to exist and the document's one wrote a backwards gap row across a reset; do not write a fifth |
| The portable text format — export, import, hand-authoring | `inputs/document.py`, format v2: every row is the pad then Mario (action as `addresses.action_word` — the decomp word, or a hex id, never the GROUP, which is not reversible; yaw in game units; speed as the shortest decimal that survives float32, snapped through float32 on read). A row may stop after the pad and Mario reads as not captured; v1 files load the same way. Import REFUSES rather than guesses: a document from another frame rate is a load error naming the reason, because rescaling would move every input |
| THE LEAD-IN — the frames before the attempt's own frame 0 | `inputs/track.py::track_with_lead` (ONE derivation returns both the frames and the lead count — deriving the count separately diverged the moment the store lacked a tail, caught by its own test) + `inputs/service.py::_lead_frame` (the latest `level_changed` before the anchor, within `LEAD_REACH_S`) + the `lead_frames` payload field. His report 2026-08-31: "frame 0 of the input timeline doesn't begin until about 3 seconds into this Log Rolling clip… I can actually trigger camera movements / camera actions before then, so technically the input timeline should begin when mario actually spawns into the level." MEASURED on the attempt behind that screenshot (5093): he warped into LLL at f7958899 and reset 49 frames later, and those in-level frames — real camera input — were invisible; the end already sat EXACTLY on the `star_collected` frame, so his second sentence ("the last frame should be on star grab") already held to the frame. The lead extends the TRACK only: FRAME 0 stays the attempt's start, the lead draws as NEGATIVE frames behind a shaded band (`.input-lead-shade` — tinted to be visible in a render; at .13 alpha it was invisible, the "correct but unexplained" state he rejects). **The header prints the attempt's OWN length (`attempt_frames` = `igt_frames`), never the track's, and the lead-in note is gone** (2026-09-01: 19"16 / 575 frames before the clip -- the drawer's fetch racing the settle flush -- and 21"30 / 639 after, the tail counted in; "I think we just shouldn't display the lead-in at all"); the drawn span rides the header as `data-total`/`data-lead` for the sweeps. The PB identity his 2026-08-28 ruling pinned ("It should be IDENTICAL in length") is now the number itself. A reset-after-reset attempt has no entry in the gap and no lead. **Timeline-only by design**: `track_for_attempt` (the document, the template, the overlay export) takes no lead, so a saved document is still exactly the attempt; the active template SHIFTS by the lead so frame 0 aligns with frame 0. The fixture publishes its level entries in `_seed_level_entries` BEFORE anything arms, and `seed_inputs` reads them back out of the journal rather than recomputing where they should be. Both halves were paid for: appending the entries to the journal after the practice seeding reddened 27 tests across five unrelated files (the projector replays every event, so rows written after the fact land last and abandon what the seeding arranged, disarming segments and retiring targets), and a second copy of the start rule here drew no lead at all for the one fixture attempt whose igt outruns its rta. A moment marker states its time on the ATTEMPT's clock (`MomentRow`'s `lead`), since its frame is on the axis the lead shifts. **CORRECTED 2026-08-31 (item 53): the buffer is the CLIP's, not the level entry's.** Reaching back to the spawn put 418 frames of un-clickable timeline in front of a video carrying three seconds of run-up — "the entire left 1/3rd of the timeline is unclickable because it happened even far before the video recording started… I would expect the duration of the timeline to match the exact duration of the video, including the before and after buffer." The timeline now asks for the clip's OWN frame range (`?from_frame=&to_frame=`, derived in the browser from the frame map's extremes — the map says which game frame each video frame shows, so its extremes ARE what the video shows) and refetches when that range arrives. A span may only WIDEN the attempt, never cut it |
| THE THREE CLOCKS, AND WHY ONLY THE MAP ALIGNS THEM | MEASURED on his clip 5363 (2026-08-31), answering "are we sure that the FPS is the same?": the video encoded at **59.987 fps** while the GAME advanced at **29.800 frames/second** — cross-checked two independent ways, the map's span over the clip's duration and the picture ledger's own RAM frames over its own timestamps, both 29.800 — so **2.0130 video slots per game frame, not 2.0000**, about eleven slots across that clip. And 29.800 is not a constant: it is the emulator running slightly slow, and it sags further under load. GAME frame == INPUT frame by CONSTRUCTION (`InputSampler` reads gGlobalTimer and files under the value it read; `edge_mismatches` is its self-check), but the VIDEO frame is wall-clock and drifts against both. So any arithmetic on 1/30 is wrong by 0.67% and rising — the map is immune because it is built from stamps rather than a rate. `ui/frame.js::nextMappedTime` therefore steps THROUGH the map (this slot's frame, then the first slot of the next distinct one); the 1/30 arithmetic survives only as the fallback for a clip with no map and at the clip's own ends. This is also why "press forward 1, it does nothing" happened: 1/30 s is less than one game frame at 29.8 |
| DOES THE TIMELINE AGREE WITH THE FOOTAGE, FRAME BY FRAME | `tools/score_inputs.py` — his ask, 2026-08-31. No digit RECOGNITION, which has never been reliable: Usamune's readout is a function of the pad alone, so *the digit region changed between two video frames* ⟺ *the pad changed between the frames the map assigns them*, and every violation is a real disagreement between footage, map and track. It also names the local shift that would resolve each one, so a run reads "this stretch is one frame early". Two calibration facts, both found by LOOKING at the mask: the signature is the ink MASK, never the raw crop (the crop sits over the gameplay, so the scene changes nearly every frame — 68% "agreement", all noise), and the mask sees only the FIRE, so the blue U/D/L/R letters are invisible and the comparison is on MAGNITUDES — a sign flip is a stated blind spot. First reading on his pyramid clip: 93.3% of boundaries agree, 114 do not |
| STEPPING NEVER LEAVES THE CLIP | `ui/frame.js::clampToFrames` — every seek lands in the MIDDLE of a real frame, and both the step controls (`stepGameFrame`, so the buttons and the arrow keys) and the timeline's own playhead seek go through it. Clamping to `video.duration` exactly is PAST the last frame's interval: the element reports itself ended and presents what it likes, and since the panel reads the presented frame that answered from somewhere else entirely — his 2026-08-31 report, frame 770 of 771, right arrow, timeline jumps to 591. His rule: "simply move to the last frame in the video and not allow the user to move forward (if at the end) or backward (if at the beginning)". Mutation-proved (`tests/test_ui_frame_step.py`) by clamping to the edge again |
| THE PANEL FOLLOWS THE PRESENTED FRAME, not `currentTime` | `ui/components/inputtimeline.js` — `requestVideoFrameCallback` hands back the displayed frame's own `mediaTime`, and that is what the panel reads (a plain rAF loop is the fallback where the API is missing). `currentTime` is the time of the SEEK, and the decoder does not have to present the frame whose interval contains it: his 2026-08-31 report of "an intermediate value of 84/4" at frame 88 was exactly this — the clip's pixels, decoded, show L4 at the slot the map named, so the MAP was right and the element was still showing the previous picture. Anything that compares the panel against the video must read the presented frame, or it measures a disagreement that is not there |
| Resolving one attempt to its own input | `inputs/track.py` — UTC picks the chunks and the frame counter trims inside them at BOTH ends. **The LENGTH is the attempt's own time** (2026-08-28): the end is the grab, and the start is set so the span equals `igt_frames` -- Usamune's number, the one the row displays and the one he is graded on. It used to run from OUR anchor, so the span carried our `rta_frames` while the row showed the IGT, and the two disagree by a frame or two on nearly every run (measured over 90 of his successes: exactly one frame off the RTA on 82, -1..+2 off the IGT) -- a 0'13"56 PB drew a 13"50 timeline. An attempt with no IGT (a reset) keeps the old anchor-to-close rule. The END is the frame BEFORE the star dance's own first frame (`_dance_start`: the contiguous run of `STAR_GRAB_ACTIONS` frames containing the close, or the first starting within `GRAB_SEARCH_FRAMES` after it, and within it the first DANCE action -- **the timer runs THROUGH a midair grab's fall** (2026-09-01, attempt 5534: `ACT_FALL_AFTER_STAR_GRAB` for 5 frames, then the dance; his 576 frames reach from the spawn frame exactly to the last fall frame, and counting from the fall's first frame started the run four frames before the reset was even pressed). So frame 0 is the spawn frame -- the reset's white picture, the one he calls "the first frame I actually reset on". It used to be the first grab frame at or AFTER the close — but the dance routinely begins BEFORE it, since our clock and Usamune's disagree and the anchor can land late, and the end anchors everything (`first = last - (igt - 1)`). Measured on four of his attempts, 2026-08-31: the real dance start sat 7, 8, 8 and 25 frames earlier, and each pushed the run's own frame 0 that far LATE — at 25 he could see it, the fall after his reset reading as lead-in while Usamune's timer, paused mid-fall, already said 0'00"20. After the fix his BBH attempt's frame 0 is `spawn_spin_airborne` and it is still falling six frames in, matching that paused reading. His rule 2026-08-22: "Once mario is in star grab, none of the players inputs matter, so that's where it should stop": a chunk is ten seconds that overlaps the span, so without the end trim the next five seconds of capture draw as part of the run (his first live attempt, 2026-08-22: 598 frames for a 444-frame run) |
| Reading it all back by hand | `uv run python tools/dump_inputs.py` (`--list`, `--attempt`, `--journal`, `--out`) — the end-to-end proof, memory → sampler → store → document |
| The TIMELINE a person looks at | `ui/components/inputtimeline.js` (lanes, scrub, the template drawn behind) + `ui/components/attemptdrawer.js` (the clip and the timeline on ONE clock) + `ui/components/controllerpanel.js`. Its data is the `runs` list of `{start, length, buttons, stick_x, stick_y, yaw, speed}` dicts from `inputs/service.py` — a field added there is readable in the browser by name the moment it arrives. Times print through `ui/format.js::fmtIgtShort`, never a local formatter. **ONE CLOCK, ALWAYS**: with a clip present the timeline reads the `<video>` every frame and keeps no position of its own, and seeking the timeline seeks the video — there is no "stop following" state (his report 2026-08-22: "both of these always stay in sync"). The mapping is `frameAtTime`/`timeAtFrame`, through `anchor_offset_s` from the replay view (the clip is cut pre-pad seconds BEFORE the anchor; the track starts AT it — without the shift every input lands three seconds early — plus `replay/service.py::DISPLAY_LAG_FRAMES`, the one game frame the picture trails RAM by, measured from nine of his Forward-1 screenshots), node-driven by `tests/test_ui_input_clock.py`. **The jitter is answered by the clip's FRAME MAP** (round 32 item 17): the poller stamps every frame edge's wall time (`replay/frameclock.py`, 250 Hz precision), extraction writes `frame_map` into the clip's sidecar (video frame k -> raw game frame, display lag folded in), and the timeline maps through `mappedFrameAtTime`/`mappedTimeAtFrame` + the payload's `stretches` (the raw->axis seams, `inputs/runs.py::axis_of` — ONE conversion rule, shared with the moment markers and the overlay). The offset arithmetic stays as the fallback for clips cut before the map existed. His ruling that forced it: "We need 100% accuracy for this. If it's wrong even once, then it can't be relied on as a tool" (2026-08-23). The only step controls are the player's Back 1/Forward 1 (the timeline's own −1f/+1f were removed 2026-08-23 at his ask); they hold-to-repeat through `ui/holdrepeat.js`, whose state lives ON THE ELEMENT because the component re-renders on every step and a release must find the run a previous render started; a press remembers whether the clip was playing and the release resumes it. The playhead and the pointer work in `.input-track-column`, an overlay whose left edge is the label column's width (`--lane-label`, one CSS variable the lane grid also reads), so it cannot travel over the labels; `test_fixture_reaches_the_real_page.py` measures the two edges at both label widths |
| The MOMENT MARKERS on the timeline | `inputs/markers.py` — the journal joined onto the track by frame, nothing captured. Membership and wording are the RECORDER's own rules (`tracking/eventlabel.py::is_step` + `label_event`), so the row marks exactly what the recorder would list for that stretch of play, in the same sentences — never a second set of either. The join respects the capture axis: a counter restart finds the later stretch, a moment in a capture hole sits at its true position, one outside the track is dropped. The service passes `db.events_between`/`db.landmark_names` in (`main.py`); a service wired without them carries no markers |
| Anything the outside asks of captured input — the payload, the document, marking a template | `inputs/service.py::InputsService` is the ONE door: `server/inputs_api.py` holds no logic, only the exception-to-status map, and `main.py` wires one object. Add a capability to the service and expose it as a route; never compute in the router |
| The Usamune-look pad drawing | `ui/components/controllerpanel.js` — TWO consumers, one drawing: the frame inspector and the overlay export. Never add a second |
| Template tracks — mark, import, export, activate, delete | `inputs/templates.py` (migration v28) + the `/api/inputs/templates*` routes. The payload's `template` has the SAME shape as the run (`runs` + `actions`), and the timeline draws all of it: ghost bars and dashed stick behind yours, a dimmed Template action row beneath Mario's, a dashed speed line on ONE shared scale, and a second facing dial. His ruling 2026-08-22: *"compare my gameplay against the exact example, including all mario data"* |
| The transparent overlay export | `inputs/overlay.py` (the plan, the concat script, the ffmpeg argv — all pure) + `ui/overlay.html` (the render target) + `tools/export_overlay.py` (drives the browser, recovers alpha, encodes) |
| Re-checking the measurements, or hunting the address on a new ROM | `uv run python tools/probe_inputs.py` (`--at scan` re-hunts). Its docstring carries every answer it has given |

## Why 250 Hz

Measured with `tools/probe_inputs.py` over four live sessions, 2026-08-20.

**Missing frames was never the problem.** Zero game frames advanced unobserved
at 60, 120, 250 or 500 Hz, across windows of 180, 301, 601, 901 and 1348
frames. Windows' 15.6 ms timer granularity never bit either.

**The problem is WHEN in a frame the game writes the pad: ~62% through it.**
So a loop must look after that point or it reads the previous frame's input.
At 60 Hz — the trainer's old rate — the last look of each frame lands at 50%
and reads fresh on **0% of frames**: one frame late, on every frame,
invisibly. At 120 Hz it is 97% fresh; at 250 and 500 Hz, 99%.

Two things follow, and both are load-bearing:

- **The sampler emits each frame's LAST reading**, since that is the only one
  guaranteed to be post-rewrite. A frame is therefore emitted when the counter
  first moves PAST it.
- **The snapshot and the detectors run once per GAME frame, late in it.**
  Gating on "the frame changed" instead would take every reading at the
  EARLIEST moment in a frame, which is worse than the arbitrary phase it
  replaced. The other snapshot fields' own write phases are **unmeasured**, so
  the rule is "at least as late as the one field we checked", not a claim
  about all of them.

Cost: ~1.5% of a core, against the ~20% the 60 Hz loop spent before the
snapshot's byte swap came out (`git log --grep="byte-swapping"`).

## The traps

**An all-zero block IS a self-consistent controller.** The razor cannot reject
one, so a run of zeroes passes every check while nothing is moving — which is
exactly the false positive the first address hunt returned, and it survived 200
consecutive live reads. The gate therefore requires a DEFLECTED stick and a
HELD button, and `tests/test_inputs_frame.py` records the zero case explicitly
so nobody "fixes" the razor to reject it.

**The frame counter is not a key.** It restarts on a console reset, so frame
numbers repeat within one session. Wall clock is the only total order; a chunk
closes at that seam rather than spanning it.

**Attempt ids are not a key either.** They are re-derived from the journal on
every reprojection, so anything keyed to one orphans itself.

**We check our own frame assignment rather than trusting the rate.** The
game's own `buttonPressed` says which frame a button was newly down on, so
`InputSampler.health()` also carries `skips`/`skipped_frames`/`worst_skip` — the counter advancing by more than one between observed samples, i.e. frames NOBODY read, which the timeline shows as "No capture on this frame". Unrecoverable by design: the game keeps no per-frame pad history, and filling a hole from a neighbour would invent input. It needs a poll-loop stall past a whole game frame (33 ms), since eight samples land inside one at 250 Hz — measured over his whole journal 2026-08-31: 84 frames of 93,958 (0.089%), 39 of 245 attempts, sizes 1-7, and NOT the chunk flush (all mid-chunk, none on a seam). A counter restart is deliberately not a skip.

`InputSampler.health()["edge_mismatches"]` is non-zero exactly when we filed an
input under the wrong frame number. Read it before believing a capture.

## Round 32: Mario's own state, beside the pad

His ask, 2026-08-21: *"adding extra diagnostic info about mario alongside the
timeline"* — his orientation, his state over time as a separate row, and then
*"Mario's speed at any given frame as well, so we can exactly see how his speed
changes over time and where there are opportunities to go faster"*.

**None of it needed a new ADDRESS.** All three are offsets off `gMarioStates[0]`,
which `memory/layout.py` already carries a verified address for — so they live
in `addresses.py` (`MARIO_YAW_OFF`, `MARIO_FORWARD_VEL_OFF`, beside the action
offset that was already there) with no layout row and no address gate of their
own. They still carry a `VERIFY` until read live with him.

**One read, one window.** The sampler takes action, yaw and speed in a single
block spanning `action` (0x0C) to `forwardVel` (0x54), inside the same coherent
window as the pad. Pairing this frame's pad with next frame's action would be
the same class of error the counter sandwich exists to prevent, one field over.

**The action rides its OWN span list; yaw and speed ride each run.** A run
breaks whenever the pad moves and an action lasts across dozens of those, so
putting the action on every run would repeat one fact hundreds of times and
still leave the reader stitching the spans back together. Facing and speed
change nearly every frame, so for them a span list would be one span per frame.

**An action reads by NAME where `addresses.py` knows it and by its GROUP
otherwise** — never a bare hex id, which is a number nobody can read dressed up
as diagnostic information. The group comes from the action id itself
(`ACT_GROUP_MASK`, decomp), so every action names its family even when this
project has never heard of the specific one. `_ACTION_NAMES` is DERIVED from
the module's own `ACT_*` constants, so a new one is named the moment it is
added and a renamed one cannot leave a stale label.

**The facing is a DIAL, and it is its own overlay layer.** His words: *"the
controller input direction display could maybe be reused as a way to display
mario's actual direction (these two different things)"* — one visual language,
two facts. A dial rather than a box because a facing is an angle with no
magnitude, and a square would imply a reach that does not exist. It is a
separate export layer because his yaw changes on nearly every moving frame, so
folding it into the pad layers would multiply their distinct pictures by the
length of the run — a few dozen screenshots becoming a few thousand.

**Speed is drawn against the fastest value in THAT track**, not a fixed cap:
what he asked for is *where there are opportunities to go faster*, which is a
comparison within one run. A fixed ceiling flattens a whole slow segment into a
line at the bottom and hides exactly that.

**Chunk format v2 carries all three, and the format is stored PER CHUNK**
(migration v29). Not guessed from the blob's length: a v1 chunk and a v2 chunk
can be the same size at different run counts, so length is not a discriminator
and treating it as one decodes one as the other and returns plausible nonsense.

## The export's four traps, each found by LOOKING

Every one of these produced a file that passed its metadata check and was
wrong. None was catchable by an assertion written before the fact.

**A transparent container is not a transparent picture.** The first export
probed as `pix_fmt=yuva444p12le` — an alpha channel, present and named — with
**every pixel opaque**, because the browser painted its own background
underneath. `tools/export_overlay.py::recover_alpha` shoots each picture twice,
over black and over white, and solves the compositing equation for the true
alpha; that is exact on anti-aliased edges, where keying one shot out by colour
would fray. Its permanent home is uilab's own `screenshot(omit_background=)`,
and it should be deleted when that lands.

**An unstyled SVG circle is BLACK, not invisible.** The overlay page imports
`ControllerPanel` and nothing else, so the first render drew the stick box as a
filled black disc with black text. The page now lifts the design system out of
`ui/index.html` at load and waits for it before anything is shot — the same
"a harness must wear the real stylesheet" rule `.claude/rules/ui-core.md`
states, in the one place where the output is a video rather than a measurement.

**The concat demuxer cannot express 1/60 exactly.** A real export came out
**3 frames long** (6,129 for 6,126) — small enough to read as rounding, large
enough to slide an overlay off its footage. `-frames:v` makes the count a
demand rather than an expectation.

**Every layer must share ONE canvas, sized by the widest state.** Register is
the entire reason there are layers instead of one file to crop, and a layer
sized to its own ink does not stack. The canvas is taken once, from the
combined layer holding every button with the stick at full deflection.

A clip with a frame map now gets a MAPPED export (`mapped_concat_script`): one
line per clip video frame through the map, dropped at 0:00 over that clip with
no offset.

**The alignment against real footage is now MEASURED per clip, by the pad
reader** (`replay/padread.py`, 2026-09-01): with Usamune's input display ON,
every picture carries the game's own drawing of the pad, the reader reads it
cell by cell, the map is pinned to what it read, and `tools/score_pad_read.py`
prints how many video frames the display confirms and every one it
contradicts. The overlay rides the same map, so its alignment is that number.
A clip recorded with the display OFF refuses and keeps the clocks' map — that
is the one case no instrument here can score.
