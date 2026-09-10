---
paths:
  - "src/sm64_events/inputs/**"
  - "src/sm64_events/server/inputs_api.py"
  - "src/sm64_events/ui/components/inputtimeline*.js"
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
| Where captured frames are stored | `inputs/store.py` stores run-length chunks (migration v32) under session, wall-clock bounds and raw counter; attempts are reprojected, so attempt IDs are not storage keys. `InputSampler` latches the session when it observes a frame; `ChunkWriter` retains that owner through buffering and flush, drops observations from before any session, and splits on owner changes or a non-increasing counter. It flushes every 300 samples and when an attempt settles (`TrackerService.on_attempt_settled`, wired in `main.py`). Chunk timestamps bound emission/flush, not individual observation times; a paused pending sample can be much older. `chunks_between` retains IDs/session/bounds when widening an attempt query. Captured inputs persist independently of saved video; session deletion and history wipe remove them. Regression: `tests/test_inputs_capture_identity.py` |
| Folding frames into runs, and the zero-based axis a track is drawn on | `inputs/runs.py` — `collapse(frames, same)` is THE run-length loop (the store, the document and the timeline payload each pass only their own `same`), and `capture_axis` is THE zero-basing, which lays a counter restart end to end and keeps a hole a hole. Four copies of that loop used to exist and the document's one wrote a backwards gap row across a reset; do not write a fifth |
| The portable text format — export, import, hand-authoring | `inputs/document.py`, format v2: every row is the pad then Mario (action as `addresses.action_word` — the decomp word, or a hex id, never the GROUP, which is not reversible; yaw in game units; speed as the shortest decimal that survives float32, snapped through float32 on read). A row may stop after the pad and Mario reads as not captured; v1 files load the same way. Import REFUSES rather than guesses: a document from another frame rate is a load error naming the reason, because rescaling would move every input |
| THE LEAD-IN — the frames before the attempt's own frame 0 | `inputs/track.py::resolve_track` returns frames, raw origin, full axis extent and lead distance together. Missing samples at either boundary remain gaps; a lead of ten counter positions stays ten even if only two were captured. Timeline runs, action spans, moments and template shifts use that same origin. The header prints `attempt_frames` (IGT when available), while `frames` includes the requested clip buffers. Documents/templates use the attempt alone and preserve leading/trailing gaps with existing v2 gap rows. Optional `from_frame`/`to_frame` widen the selected occurrence; raw min/max cannot represent an entire clip across counter resets, so full occurrence-aware clip joining remains open. The fixture publishes level entries before arming, then seeds one ordered stream in its new session only; overlapping star/segment views share physical samples. `tests/test_inputs_capture_identity.py` and `tests/test_input_fixture_capture.py` cover these contracts |
| THE THREE CLOCKS, AND WHY ONLY THE MAP ALIGNS THEM | MEASURED on his clip 5363 (2026-08-31), answering "are we sure that the FPS is the same?": the video encoded at **59.987 fps** while the GAME advanced at **29.800 frames/second** — cross-checked two independent ways, the map's span over the clip's duration and the picture ledger's own RAM frames over its own timestamps, both 29.800 — so **2.0130 video slots per game frame, not 2.0000**, about eleven slots across that clip. And 29.800 is not a constant: it is the emulator running slightly slow, and it sags further under load. GAME frame == INPUT frame by CONSTRUCTION (`InputSampler` reads gGlobalTimer and files under the value it read; `edge_mismatches` is its self-check), but the VIDEO frame is wall-clock and drifts against both. So any arithmetic on 1/30 is wrong by 0.67% and rising — the map is immune because it is built from stamps rather than a rate. `ui/frame.js::nextMappedTime` therefore steps THROUGH the map (this slot's frame, then the first slot of the next distinct one); the 1/30 arithmetic survives only as the fallback for a clip with no map and at the clip's own ends. This is also why "press forward 1, it does nothing" happened: 1/30 s is less than one game frame at 29.8 |
| STEPPING NEVER LEAVES THE CLIP | `ui/frame.js::clampToFrames` — every seek lands in the MIDDLE of a real frame, and both the step controls (`stepGameFrame`, so the buttons and the arrow keys) and the timeline's own playhead seek go through it. Clamping to `video.duration` exactly is PAST the last frame's interval: the element reports itself ended and presents what it likes, and since the panel reads the presented frame that answered from somewhere else entirely — his 2026-08-31 report, frame 770 of 771, right arrow, timeline jumps to 591. His rule: "simply move to the last frame in the video and not allow the user to move forward (if at the end) or backward (if at the beginning)". Mutation-proved (`tests/test_ui_frame_step.py`) by clamping to the edge again |
| A picture-feed clip has no uniform grid | `ui/frame.js::clipClock`, `slotAtTime` and `timeOfSlot` use `frame_times` and the actual clip origin. Timeline seeks share that clock. `inputs/overlay.py` exports validated `picture_states` at those VFR timestamps and preserves the final hold; it never reconstructs mapped state from raw counters or stretches. Input-only export retains its game-frame clock. Clock, feed and overlay timing tests pin these contracts. |
| THE CLIP'S OWN FIRST TIMESTAMP -- every slot counts from it | `ui/frame.js::slotAtTime`/`timeOfSlot` (+ `clipStart` on `stepGameFrame`/`nextMappedTime`), `inputtimeline.js::mappedFrameAtTime`/`mappedTimeAtFrame` (+ `clipStart`), the view's `video_start_s` (`replay/extract.py::video_start_of`, ffprobe on the cut). DIAGNOSED 2026-09-01 over `.claude/rules/chain-input-timeline-frame.md` on his Log Rolling report ("84 2 on screen, 84 in our tool"): the map was RIGHT (slot 496 = R2 = the track's x=2), but an accurate cut leaves its sub-frame remainder on the first picture (5782: frames at k/60 + 0.011003 s), so a seek to (k + 0.5)/60 lands 2.7 ms BEFORE frame k begins and Chromium presents k-1 -- measured with a Chromium probe on the real clip: every step one picture early, the corrected seek exact, and a clip whose first frame sits at 0.000 (5574) exact either way. First divergence at hop 6 of the chain; no reader change could have held. THE PROBE SHIPS as `tools/probe_clip_seek.py --attempt N --slots a-b` (headless Chromium runs the shipped frame.js against the cached clip, old seek vs corrected seek, prints the presented slot per seek), and its FIRST RUN caught the fix not working: `video_start_of` derived ffprobe by replacing 'ffmpeg' in the WHOLE path, which renamed the install FOLDER too (D:/ffmpeg/bin/), so it silently read 0.0 on the very clip it was written for -- `extract.py::ffprobe_beside` replaces by file name and falls back to PATH's ffprobe, pinned in `tests/test_replay_extract.py`. A clip cut BEFORE the field existed gets it measured once when the view first reads its sidecar |
| THE PANEL FOLLOWS THE PRESENTED FRAME, not `currentTime` | `ui/components/inputtimeline.js` — `requestVideoFrameCallback` hands back the displayed frame's own `mediaTime`, and that is what the panel reads (a plain rAF loop is the fallback where the API is missing). `currentTime` is the time of the SEEK, and the decoder does not have to present the frame whose interval contains it: his 2026-08-31 report of "an intermediate value of 84/4" at frame 88 was exactly this — the clip's pixels, decoded, show L4 at the slot the map named, so the MAP was right and the element was still showing the previous picture. Anything that compares the panel against the video must read the presented frame, or it measures a disagreement that is not there |
| Resolving one attempt to its own input | `inputs/track.py` preserves tight-query chunk IDs while widening the search, separates observed counter/session seams, then uses one IGT/dance interval both to eliminate disjoint occurrences and to trim the selected one. Repeated ranges that remain ambiguous raise `ValueError` (API 409), never silently select the first. A valid IGT prefix can precede the RTA anchor; a delayed dance can extend the end. The last timed frame precedes the first dance action (a midair grab's fall remains timed), and `first = last - (igt_frames - 1)` when IGT is known. Missing boundary samples do not shorten that extent or move frame zero. These legacy chunk bounds cannot prove an unobserved reset or a per-frame observation time; full capture occurrence identity is still needed for a clip spanning epochs. `tests/test_inputs_capture_identity.py` reproduces opposite inputs on repeated counters and the partial-capture bounds failures |
| Reading it all back by hand | `uv run python tools/dump_inputs.py` (`--list`, `--attempt`, `--journal`, `--out`) — the end-to-end proof, memory → sampler → store → document |
| The timeline and its clock | `ui/components/inputtimeline.js` composes the view and re-exports its public helpers. `inputtimelinemodel.js` owns pure coordinate/occurrence calculations; `inputtimelinehooks.js` owns loading, presented-picture subscription and memoized geometry; `inputtimelinelanes.js` retains static lanes until data, zoom, template visibility/shift or seek mapping changes. Interaction, chrome and inspector have sibling modules. Displayed times subtract the lead. `attemptdrawer.js` supplies the replay clock; `controllerpanel.js` draws input/facing state. Missing identity shows unavailable input; ambiguous repeats are refused. [The chain](chain-input-timeline-frame.md) owns timing evidence and limitations. |
| The MOMENT MARKERS on the timeline | `inputs/markers.py` — the journal joined onto the track by frame, nothing captured. Membership and wording are the RECORDER's own rules (`tracking/eventlabel.py::is_step` + `label_event`), so the row marks exactly what the recorder would list for that stretch of play, in the same sentences — never a second set of either. The join respects the capture axis: a counter restart finds the later stretch, a moment in a capture hole sits at its true position, one outside the track is dropped. The service passes `db.events_between`/`db.landmark_names` in (`main.py`); a service wired without them carries no markers |
| Anything the outside asks of captured input — the payload, the document, marking a template | `inputs/service.py::InputsService` is the ONE door: `server/inputs_api.py` holds no logic, only the exception-to-status map, and `main.py` wires one object. Add a capability to the service and expose it as a route; never compute in the router |
| The Usamune-look pad drawing | `ui/components/controllerpanel.js` — TWO consumers, one drawing: the frame inspector and the overlay export. Never add a second |
| Template tracks: save, import, export, activate, delete | `inputs/templates.py` and `/api/inputs/templates*` own named local operations. Replacement and activation are atomic on the shared connection. Template buttons, actions, stick and speed occupy the same rows as the attempt; both indicators share each stick/facing display. The author stub and portable metadata leave profiles/community sharing to separate tasks. See the approved template contract below. |
| The transparent overlay export | `inputs/overlay.py` (the plan, the concat script, the ffmpeg argv — all pure) + `ui/overlay.html` (the render target) + `tools/export_overlay.py` (drives the browser, recovers alpha, encodes) |
| Re-checking the measurements, or hunting the address on a new ROM | `uv run python tools/probe_inputs.py` (`--at scan` re-hunts). Its docstring carries every answer it has given |

## Local template comparison (round 35, 2026-09-06)

`inputtemplates.js` owns named save, file/paste preview and import, filtered
local selection, download and removal inside the attempt drawer. The service's
attempt-bound preview/import/select methods are the reusable import boundary;
source document metadata and local target/strategy binding are separate. A
foreign segment number is never silently treated as this database's identity.
`inputpreferences.js` shares overlay-row preferences and template invalidation
between mounted timelines and same-origin tabs. Failed refreshes keep the old
comparison and provide Retry.

Griffin's correction: **"we shouldn't have a new row just for the template"**;
**"a second stick for each of the displays matching the color of the template."**
All buttons and Mario actions share their original lanes. The controller and
facing SVGs each carry a second amber dashed indicator with a hollow head;
there are only two inspector displays, not four. Optional template props on
`ControllerPanel`/`FacingDial` leave overlay-export rendering unchanged.
`curvePath` breaks SVG subpaths at capture gaps; stick reach and speed peak
are shared across both tracks and every subpath. Calculating reach after
splitting moved identical raw 84 values to different heights across a gap.

The portable v2 document accepts optional `author:` credit and `name:`. Export
adds the library name without rewriting imported source/body bytes or unknown
headers; Copy inputs uses that same export. Import autofills the embedded name,
except when the player has typed one. Legacy files remain loadable. New rows
use explicit single-space separators: width padding concatenated Cdown+Cleft
with +0,+84 and made Pyramid attempt 7571 impossible to save. The regression
round-trips every button mask, and padded/CRLF legacy separators remain valid.
Local credit is injected by `InputsService`,
currently `griffman1212`; it is not an account. Community transport and media
remain future tasks 0130/0131. Native exports require pywebview's
`ALLOW_DOWNLOADS=True` before startup; its default WebView2 handler cancels
downloads otherwise (`test_window.py` pins the setting).

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
(migration v34). Not guessed from the blob's length: a v1 chunk and a v2 chunk
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

**The overlay rides the clip's own frame map**, so its alignment is the map's:
one line per video frame through the stamps, dropped at 0:00 with no offset.
A clip with no map (recorded without the capture layer) exports on the plain
arithmetic instead and cannot claim frame accuracy. The pad reader that used
to align it -- reading Usamune's input display out of the pixels -- is an
offline instrument now (`tools/score_pad_read.py`), because the layer stamps
the pad beside every picture and the shipped check compares two numbers rather
than recognising glyphs.

Main sync (2026-09-06): main owns imports/recording links through v31;
input migrations follow at v32-v35. `_migrate` recognizes both earlier input
histories (v27-v30 and v31-v34) by their tables, atomically adds only the missing
main block, and advances over the already-applied input prefix. Presence of
`attempt_recordings` distinguishes the newer main schema from the second input
history. `test_storage_input_branch_upgrade.py` covers all eight old versions,
main v30/v31, preserved input/template/recording bytes and rollback/retry.
Renumbering alone fails reopening existing worktree databases.
