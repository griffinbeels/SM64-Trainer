---
paths:
  - "src/sm64_events/inputs/**"
  - "tools/probe_inputs.py"
  - "tools/dump_inputs.py"
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
