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
| What one frame of pad state IS, and the razor that identifies the pad | `inputs/frame.py` — `decode()` is the 250 Hz path and reads only the four fields a timeline needs; `fits_controller()` is the cold path (the address gate, the address hunt) and checks the struct AGAINST ITSELF: the processed stick must be the raw stick through the game's dead zone, the magnitude their hypotenuse clamped at the cap. Every version-independent fact — the offsets, the button bit table, the dead zone, `CONTROLLER_SETTLE_PHASE` — lives in `memory/addresses.py`; the ADDRESS lives in `memory/layout.py` |
| How often the pad is read, and which reading counts | `inputs/sampler.py` + the loop in `server/poller.py`. **The rate is set by the CONTROLLER, not by the game** — see [Why 250 Hz](#why-250-hz) |
| Where captured frames are stored | `inputs/store.py` — run-length chunks (migration v27), keyed by wall clock and frame counter, NEVER by attempt id. `ChunkWriter` buffers and flushes every 300 frames or on a backward counter |
| The portable text format — export, import, hand-authoring | `inputs/document.py`. Import REFUSES rather than guesses: a document from another frame rate is a load error naming the reason, because rescaling would move every input |
| Resolving one attempt to its own input | `inputs/track.py` — UTC picks the chunks, the anchor frame trims inside them |
| Reading it all back by hand | `uv run python tools/dump_inputs.py` (`--list`, `--attempt`, `--journal`, `--out`) — the end-to-end proof, memory → sampler → store → document |
| The TIMELINE a person looks at | `ui/components/inputtimeline.js` (lanes, scrub, the template drawn behind) + `ui/components/attemptdrawer.js` (the clip and the timeline on ONE clock) + `ui/components/controllerpanel.js`. Its data comes from `inputs/service.py` through `server/inputs_api.py` |
| The Usamune-look pad drawing | `ui/components/controllerpanel.js` — TWO consumers, one drawing: the frame inspector and the overlay export. Never add a second |
| Template tracks — mark, import, export, activate, delete | `inputs/templates.py` (migration v28) + the `/api/inputs/templates*` routes |
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

**Still owed, and it is the one no test here can stand in for:** the overlay's
alignment against real footage. The clip is captured on a wall clock and the
inputs on the game's frame counter. Record a clip with Usamune's own input
display ON and score our overlay against Usamune's pixels in that same
footage — the same move `tools/derive_xcam.py` makes for star times.
