# Renderer GPU recording

The recorder connects the renderer boundary to the GPU delivery worker, an
isolated NVIDIA encoder, and the existing input/audio/fragment system. It is
the one shipped capture path (the legacy ReadScreen/frame-stream path was
deleted on 2026-09-16 at his decision); without the installed layer the
recorder photographs the desktop with no stamps. Griffin accepted the
round-48 build live on 2026-09-16: "No lag. No stuttering. Replay is
smooth. Input timeline matches."

Component contracts live beside this page:
[renderer boundary](replay-renderer-boundary.md) (SourceV2 surface and stats),
[GL snapshot](replay-gl-snapshot.md), [LINK state](replay-link-state.md),
[GPU bridge](replay-gpu-bridge.md), [capture control](replay-capture-control.md)
(the control page and lease) and [the renderer build](../renderer/README.md).

## Why it is built this way

- **The picture is taken inside the renderer.** Window or desktop capture
  (Windows Graphics Capture, desktop duplication) stamps a picture with wall
  time but cannot say which N64 frame it shows, and does not promise every
  picture; a `SwapBuffers` hook sees swaps, not game frames (a frame can
  present none, several, or hold the last picture). Only the renderer knows,
  as `ProcessDList` ends, which game frame and which input it just drew, so
  the stamp and the picture are taken together there.
- **The renderer is a source build of LINK's GLideN64 v4.2.** It is the
  renderer his Project64 1.6 install already ran, so picture, smoothness and
  settings stay as he knows them. Stock GLideN64 offers only a synchronous CPU
  `ReadScreen`, and an earlier binary adapter depended on exact bytes and
  private offsets of one DLL. The rebuilt baseline passed his A/B (round 30):
  "same picture, smoothness and clean audio".
- **The game never waits for capture.** Unaffected emulation is his highest
  acceptance priority (round 21). The emulation thread already waits on
  renderer commands, so any capture wait would block the game; fixed pools
  refuse instead, and a refused picture is a counted gap, never a stall.
- **Passive until asked.** His requirement (round 22): with the server off the
  plugin behaves as LINK does, and capture turns on without switching plugins
  or restarting Project64. A lease with a deadline covers a hung owner; PID
  plus process birth time covers a crashed one or a reused PID.
- **Encoding happens in a separate 64-bit process.** Project64 and the plugin
  are 32-bit, 32-bit CUDA is unavailable on RTX 50-series cards, and NVENC has
  no OpenGL input on Windows. Pictures therefore cross from GL into a D3D11
  shared texture that a 64-bit helper encodes. The helper lives in its own
  kill-on-close job so a stuck driver call can be ended without restarting the
  server.
- **Only small things reach Python.** Selection samples, stamps and compressed
  packets; media persistence runs on its own sink thread. Capture credits held
  across AAC, SQLite or a slow timer drained the source pool (rounds 37, 48).
- **One capture route.** His decision, 2026-09-16: "Delete the CPU path." That
  route carried the synchronous `ReadScreen` barrier. The desktop grab remains
  only for a machine or moment without the layer, and says so.
- **The practice ROM is decided natively.** A real run must see plain GLideN64
  even with no trainer open, so the wrapper reads the cartridge header itself
  (below).
- **The stride-8 selection sample stays.** It matches the accepted duplicate
  and settling decisions, and picture identity is preserved. A change confined
  inside one 8x8 cell can hold the previous picture; removing the sample needs
  renderer-certified picture identity (rounds 39, 48).

## Shipped files and how they are built

| File | Role | Built by | Build id suffix |
|---|---|---|---|
| `GLideN64_SM64Trainer.dll` (label `LINK v4.2 [SM64 Trainer]`) | the renderer: LINK's GLideN64 v4.2 at the pinned commit plus the SourceV2/ContextV1 overlay from `plugin/gfxwrap/` | `tools/build_renderer.py` (pin, credits and the prebuilt-library recipe in `renderer/`) | `-renderer` |
| `sm64_trainer_gfx.dll` (label `SM64 Trainer v1.0`) | the capture wrapper Project64 selects; loads the renderer named by `sm64_trainer_gfx.ini` | `tools/build_plugin.py` | `-gpu-runtime` |
| `SM64GpuEncoderV1.dll` | the 64-bit NVENC helper the server's isolated encoder process loads | `tools/build_plugin.py` | `-gpu-encoder` |

All three are build outputs in `src/sm64_events/data/plugin/`, which git
ignores, and are packaged by `tools/build_exe.py`. Each carries an embedded
build id naming the exact sources it came from. `tools/build_natives.py`
rebuilds whichever disagrees with the tree, from a shared cache keyed by that
id when another checkout already built it; the server launched from a checkout
(so `run-test-server.bat`), the full verification lane and release run it, and
`tests/test_bundled_natives.py` fails when a built file and the tree disagree, and the setup screen compares an installed file
with the bundled one by that id (`core/capturelayer.py::_files_match`), so a
rebuild of unchanged sources never reads as "differs from this build". The
setup screen installs the renderer and the wrapper together, writes the ini
to name the renderer, selects the wrapper, and remembers the plugin it
replaced (`previous_graphics_dll`) for Remove. The wrapper's About box
credits GLideN64's authors and links the published source (GPL-2.0).

Practice ROM gate: at RomOpen the wrapper reads the cartridge header
(`plugin/gfxwrap/practice_rom.h`, mirrored by `core/onboarding.py::is_practice_rom`
and compared in `tests/test_practice_rom.py`). On any other ROM every callback
forwards straight to the renderer with no stamp adapter or per-call timing,
the control page reports `rom_baseline` with `rom_open` false so no lease can
activate capture, and the renderer overlay observes nothing and leaves
GLideN64's raw GL function table untouched. The server mirrors it: the poller
refuses a positively identified non-practice ROM and the recorder's capture
gate records nothing while it does. The idle control, delivery and log
threads created at InitiateGFX stay asleep.

Swapping ROMs needs no restart of either side. Project64 1.6 copies the new
cartridge's header into the buffer `GFX_INFO.HEADER` points at before every
RomOpen (`Cpu.cpp` StartEmulation and Machine_LoadState, which also calls
RomClosed/RomOpen around a state load), so the wrapper classifies each open
afresh. RDRAM is neither cleared nor released between ROMs, so the server
cannot see a swap in game memory: the poller reads the header before the
detectors whenever the timer goes back and at least every
`PRACTICE_ROM_CHECK_S`, and re-offers a refused cartridge every
`PRACTICE_ROM_RETRY_S`. `Pj64Memory.rom_header` remembers where it found the
image, for every reader of that process, because Allocate_ROM releases the old
image before allocating the next, and looks first where Project64 1.6 keeps it
(0x10000000 past RDRAM): a steady check, or a fresh reader, is one 0x40-byte
read (`tests/test_pj64_rom_header.py` drives that against a real process;
`tests/test_poller.py` drives the whole swap cycle).

Updates: when the user clicks Update in the app, the new build restarts and
`CaptureLayer.refresh_if_stale` (at boot and every two seconds) copies the
new renderer and wrapper over the installed ones as soon as Project64 is
closed, keeps the ini pointing at the renderer, and records the new hashes.
A source checkout's server does the same for its own rebuilds. Each
trainer refreshes only an install made by an installer like it -- the
installed app's by the installed app, a checkout's only by that same
checkout -- whose wrapper still matches its receipt, and never with an
older build (by the DLLs' link times; `core/plugin_ownership.py`). A wrapper
someone else copied in, another checkout's install or a newer build is left
alone and the setup screen asks. The status field `automatic_update` tells
the UI which case it is, and onboarding never reopens for an automatic update.

## Owners and data flow

`SourceFactory` is called only after `ReplayRecorder` acquires the machine-wide
recorder lock. GPU discovery reads the small control page. After a live matching
GPU-capable producer is registered with the retry gate, it checks the machine's
complete DXGI adapter inventory. If every adapter is definitely unsupported,
the same source-selection call keeps the desktop grab before creating GPU
demand, waiting for a channel or starting a helper. A capability bit describes
the wrapper's route; it does not prove the hardware can deliver it. The paired
`GpuCapture`/`GpuSink` shares one capture coordinator and one bounded media sink;
it starts no second encoder. Existing recordings and fragment readers keep
their existing format and exact source-key lookup.

```mermaid
flowchart LR
  R[Renderer: picture plus input stamp] --> S[Bounded GPU snapshot]
  S --> G[GPU delivery worker]
  G --> E[Isolated NVENC helper]
  G --> M[Small sample and exact stamp]
  E --> P[Compressed packet]
  M --> A[Existing selector: sample and exact stamps]
  P --> Q[Bounded media command queue]
  A --> Q
  PCM[Process audio tap and clock placement] --> Q
  Q --> W[Sink: ledger, AAC, fragment mux and archive]
  W --> V[Shared replay fragments]
```

The renderer submits its copy and immutable occurrence/QPC/stamp without waiting
for GPU completion, the delivery worker, the encoder, Python, or disk. Pool
exhaustion refuses admission; it cannot turn into a renderer wait. A refused
stage or a full snapshot pool is a **counted missing picture** (`refused` in
the `gpu_summary` log line), never a run failure: the replay holds the
previous picture and the input lanes show the polled fill for those frames.
Before round 48 one refusal ended the run and the retry gate withheld 10-60 s
of footage. The next captured picture carries `lists_since >= 2` and is filed
inexact, so no false exactness enters through an omission. GPU commands
still consume bandwidth and share the driver with the game. Unnoticeable live
contention is a measured acceptance requirement, not a guarantee implied by the
absence of explicit waits.

The renderer's own thread, in its own context and inside its capture
callbacks, does every capture GPU step: the snapshot copy, the selection
sample, the bridge copy into the helper's texture (with orientation and crop)
and the key checks ([renderer-thread capture](#first-capture-freeze)). The
delivery thread never touches the GPU: it picks up requests, reads the
frontier between VIs, drains and checks deadlines. Only small samples, stamps and
compressed packets reach Python. The helper owns synchronous native encoder and
shared-key operations inside a private kill-on-close process job. Its independent
watchdog can end a stalled helper without entering an emulation callback.

`GpuDemand` separately owns the capture lease. It never performs media or GPU
operations. A stopped server's lease expires independently of Python's media
worker. With no demand the native worker is passive and the renderer runs as
the original single-context renderer. With demand it still does: since
2026-09-27 every capture GPU call runs on the renderer's own thread in its
own context ([renderer-thread capture](#first-capture-freeze)), so no other
thread in Project64 ever touches the GPU. Since 2026-09-28 the overlay
publishes no shared context at all: round 48's never-current anchor
(`EnsureAnchor`, and its `anchor_published` timing line) is gone, ContextV1
stays exported for the wrapper's ABI check, and every lease is refused
(`test_link_source_witness`, `test_context_lifetime`'s `abi` mode). Whether
round 48's anchor move or the same day's driver update ended the passive
stutter is not isolated (see [what failed](#what-failed-and-why)); with no
anchor left, that question is moot for this design.

The media worker's tick is a high-resolution waitable timer plus the native
producer's own event (`tickwait.py`): a `threading.Event.wait(0.004)` sleeps
15.5 ms on Windows, three of which per picture drained the eight-slot source
pool on any short stall. Packet reservations shrink to the encoded size when
the packet returns, and the pending budget admits every native slot at once.

Most 4 ms ticks find nothing new: on a static screen the producer writes only
its status, at up to 60 Hz. Each tick first loads the seqlock words it consumes
(status, publication, both bridges; `Client.change_words`). Every native write
holds its word odd and leaves it two higher, and every offer or bridge
publish/retire also moves the publication word, so unchanged even words skip
the offer and bridge scans; a status-only change reads just the status and its
frontier. Age deadlines, helper replies and dispatch still run every tick, and a
quiet tick reuses the encoder helper's health sample for at most 20 ms (a fault
with a command in flight still arrives at once as its failed reply). On the real
channel an idle tick fell from 50 to 3.4 µs and a status-only tick costs 13 µs
(`test_idle_tick_costs_a_fraction_of_a_full_scan`); live, before this,
`adapter_pump` was 42.7-49 ms/s at about 270 ticks/s (2026-09-25).

A torn status is no news to the tick. Native holds the status word odd across
its copy, so a writer preempted by a machine-wide stall tears every read until
it runs again; three back-to-back reads used to end the run, which ended runs
of 5.3 h, 30 min and 61 min on 2026-09-22, 09-23 and 09-25. Reads now yield
between attempts, and the tick reuses the last good status until the status
stays torn past the 2 s age deadline, which still ends a run whose writer died
mid-copy. Setup still refuses a torn status at once. A survived episode logs
one "status was torn" line.

A true pause (F2, AFK) freezes the committed video end. Silence is padded only
up to that end and stamped there, so it muxes at once instead of piling PCM
against the frozen frontier; PCM ahead of the frontier waits, bounded by
capacity, and is not counted stale. `tests/test_gpu_audio_pause.py` pins
eight synthetic frozen seconds with no failure. With continuous real
callbacks (proctap zeros) the PCM capacity (about 5.5 s) still ends the run
explicitly; a typed paused-frontier seal remains the next step.

A stall of the whole interpreter must not deadlock the two streams. PCM is
written only up to the committed video end, and a video packet muxes only up to
the audio end plus the 100 ms lead (`lead_ticks`). Callbacks are stamped at
arrival, so a stop-the-world pause of the audio callback leaves a gap in the
PCM timeline. When that gap was at least the lead, the next PCM block straddled
the video end and the next picture sat past the audio end, and neither could
move. The head picture then hit the 2 s "pending age limit" and recording
stopped. That was every such failure in the live log from 2026-09-19 to
2026-09-23 (14 of 14 came 1 to 3 s after the recorder's forced gen-2
collection, `_gcwatch.py`, which pauses for 54 to 139 ms), and the picture
ledger shows 30 fps feeds up to 2.0 s before each failure. PCM arrives in
source order, so a waiting block proves the gap before it is final: the video
gate now counts audio as covered up to that block's start
(`PcmBuffer.head_ticks`). `test_an_interpreter_stall_does_not_deadlock_video_against_audio`
drives 30 fps pictures and 10 ms callbacks through stalls of 50 to 500 ms;
before the fix the 140, 300 and 500 ms cases failed with the live error.

The same stall used to leave audio late for the rest of the recording. The
first chunk after the stall opened the gap at its arrival time, the buffered
chunks were laid out after it, and placement only moves forward, so audio
stayed stamped late by the stall: 144 ms after a 140 ms stall, 504 ms after
500 ms, adding up with every forced collection. A chunk cannot end after it
arrived, so `AudioPlacement` now pulls back by dropping a chunk's oldest
samples until it ends on arrival. It never moves a chunk earlier than audio
already written, so nothing overlaps or reorders. It pulls back at once when
a chunk is more than 50 ms late (the stall's backlog), and otherwise when
every chunk in the last 0.5 s was more than one callback (10 ms) late. That
second case covers what a burst leaves behind, and also a device clock that
runs fast against the wall clock. The stall costs its own audio instead of
shifting what follows. `test_a_callback_stall_does_not_leave_audio_late`
ends within one callback after 140 and 500 ms stalls, and
`test_ordinary_callback_jitter_is_never_trimmed` keeps 30 s of 0-20 ms
delivery jitter whole.

## Startup, idle and failure

The paired GPU sink is bound before audio startup, but GPU demand starts only
after primary/fallback audio initialization finishes. This keeps cold audio
setup outside the active native queue and lease deadlines. Early PCM is dropped
before the media handoff exists; the first source offer still owns the origin.
Stop/pause during setup is reconciled before video demand. The desktop grab's
sink keeps its video-before-audio startup order.

Preparation publishes the fresh request identity and BOOTSTRAP state before
acceptance returns. Channel names and shared resources become available while
the source remains metadata-only. The helper completes Open and the sink prepares
its AAC context, filter and mux format before the client publishes `encoder_ready`;
only then can picture admission begin. Preparation emits no dummy audio or media
header and assigns no clock. The first real offer establishes the media origin.
Its bind command creates the archive on the sink, leaving capture free to consume
the next offers. Startup cannot fill a source queue with cold codec construction.

Helper Open also settles WHICH CODEC this adapter records in. The session asks
for AV1 first — 32% of H.264's bytes at equal quality and faster to encode
(measured 2026-09-20) — and the encoder checks `nvEncGetEncodeGUIDs` before it
configures anything. An adapter without an AV1 encoder answers `GBDLL_CODEC`,
typed apart from every real fault, and the session asks the SAME helper for
H.264 inside the same startup budget; `gpusettings.py` remembers the refusal
per adapter LUID, so only the first capture of a launch pays the extra round
trip. (Until 2026-09-27 the helper controller rejected result 16 as a
malformed reply and the helper exited on the refusal, so this fallback never
ran: an adapter without AV1 failed every attempt. A refusal by codec that
opened nothing now leaves the helper running.) An adapter refusing H.264 too
is an unsupported adapter, below. The codec that actually opened is what `PacketFragmentMux` writes the
fragments as, so an `av01` track is never described as `avc1`.

The lease supervisor allows five seconds from admitted request to ACTIVE. Helper
Open uses its startup budget; ordinary native requests use the shorter call
deadline. An unavailable backend ends the request explicitly. An adapter that
can never open the encoder is a verdict, not a failure: `gpuadapter.py` asks
DXGI and the driver (AMD/Intel, a software adapter, a missing NVIDIA driver or
an NVENC API older than the encoder's 13.0). Discovery can settle this before
channel startup only when a nonempty, complete inventory rules out every
possible capture adapter. Enumeration/description failures, its safety bound,
unknown vendors and any plausible NVIDIA adapter retain the GPU route. A missing
NVENC version answer cannot distinguish an absent driver from a failed query,
so NVIDIA candidates retain the existing actual-LUID handling in that case. Only
a successful query proving an old API rejects NVIDIA at discovery. Hybrid
machines therefore still use the channel's actual GL adapter LUID, never the
primary monitor or enumeration order. That actual-adapter check runs before
the helper, and an H.264 refusal by codec answers the same way.
`gpusettings.py` remembers the actual-adapter verdict per adapter and driver
version; the retry gate arms no cooldown and discovery declines that producer,
so the recorder's next attach keeps the desktop grab
(`sourcefactory.DesktopForUnsupportedGpu`, status `gpu_unsupported`). The early
machine verdict selects that fallback in the current call. Setup still requires
its written pictures, live matching control and moving game/input evidence;
the hardware verdict alone cannot complete setup. The 2026-10-19 AMD report
showed repeated channel-startup deadlines before codec selection; this fixes
the admission-order defect without identifying its native GL failure. Actual
replay and setup completion on that tester's machine remain a live gate.
An NVIDIA GPU without NVENC hardware still
fails as a driver error at session open, indistinguishable from a transient
one without a native change, and keeps the retry gate. Transient failures
retry only after proved cleanup, with 10/20/40/60-second capped delays. A short
productive prefix or capability refresh does not waive the delay. Discovery,
storage-maintenance and audio-worker faults have their own bounded retry delays.
New process identity and observed normal ROM lifecycle permit a fresh request.
Closing the ROM or Project64 is such a normal end, and its whole log is one
INFO line ("Project64 closed the ROM; recording stopped" or "Project64
closed; recording stopped"). The plugin revokes GPU capability a moment before
it marks the ROM closed (`gfxwrap.c` RomClosed), so the lease supervisor reads
the ROM before the capability and waits up to 120 ms for the ROM or process to
close before calling a capability loss a fault (`gpudemand.CLOSING_GRACE_S`);
until 2026-09-28 every close raised "GPU recording stopped" with a traceback.
A Project64 that loaded a capture layer from another build (`LayerVersionMismatch`)
is a verdict for that process, like an unsupported adapter: one WARNING and
the desktop grab until it restarts. `tests/test_pj64_lifecycle.py` holds one
case per open/close order. Native reason9 (resources exhausted) is process-lifetime sticky: the user must
fully close and reopen Project64. A ROM reload/control generation change cannot
prove recovery, so the retry gate retains that refusal for the same process birth.

Failed cleanup retains its original owner and retries closure before admitting
a replacement. GPU media closure stays on its owning media thread. A completed
publication failure differs from a still-running writer. Unproved native lease
closure remains quarantined; repeatedly clearing an error flag cannot establish
that a lost native owner is safe. This path may still require manual recovery.
Windows unmap/close and mutex-release return values are checked individually.
A partial constructor transfers its surviving owner to the supervisor; a failed
release retains that same handle and original mutex-owning thread for retry.
The control retains a birth-verified process handle: only its signaled state
proves PJ64 exited. Access/wait errors never substitute for exit evidence.
Server shutdown joins the packaged plugin-refresh worker before closing its
passive setup observer. Permanent release failures remain explicit pending
cleanup; tests do not claim that a stuck driver can be safely force-released.
`/api/replay/status` exposes `recovery` and `audio_health`, including error and
retry timing. A failed audio worker restarts after its old callbacks stop;
timestamped sinks retain their original video clock. The in-process writer
used only without an ffmpeg binary closes its run at an audio gap instead of
shifting subsequent samples.

Python cleanup does not prove that the native worker has finished retiring its
source transactions and GPU ownership. A new request arriving during that drain
remains PREPARING with the same identity. The independent watchdog retries
admission within the existing setup deadline; it does not read the old run's
terminal status as the new run's result. Cancellation or ROM closure invalidates
that pending request. Invalid, exhausted and system-error admission remain
distinct permanent refusals. Intentional metadata-only bootstrap observations
remain valid if their completed records arrive just after ACTIVE is published.

Failure logs retain raw numeric codes and decode their native namespace through
`gpudiagnostics.py`. Distinct source failure codes separate missing images,
invalid records/stamps, client rejection, epoch changes and refused admission.
Refused admission alone does not establish queue saturation. Bounded media-worker
timings distinguish channel/encoder startup, pre-admission sink preparation, first offer, media construction,
tick execution and gaps between ticks. They are emitted on failure, never from
an emulator callback. Earlier generic source-gap logs cannot identify a branch
retroactively, and a logical recorder attachment does not prove advancing media.

`GPU capture failure before cleanup` records server observation time, raw channel
reason, adapter custody and sink queue occupancy before lifecycle reconciliation
or writer retirement. The adapter's `failure_custody` is latched inside its first
abort, before clearing its queues; top-level adapter counts describe the later
post-abort state. Both remain available for comparison. Sink counts logged only
at retirement cannot establish whether the queue was empty when capture failed.
These fixed-size diagnostics neither change source admission nor perform channel
or GPU operations. Server observation time is not the first native-refusal time.

Native failures additionally emit `gpu_failure`, `gpu_source_refusal` and
`gpu_phase` into the existing plugin log. The wrapper latches the first source
admission refusal after ACTIVE, with QPC, source occurrence and FULL/BUSY counters
from the existing renderer statistics API. Worker phase totals/peaks and their
start QPCs describe sampling, bridge copies, credit returns and record retirement.
The failure log carries the QPC frequency to align these with UTC. A delayed
worker observes the refusal later; its observation time must not be mistaken for
when the source ran out of room. The callback runs on whichever delivery thread
saw the failure (the renderer's, for a GPU step) before drain; the bounded
logger only try-locks its queue and drops the line on contention, so it never
adds a wait to emulation.

Automatic inactivity keeps the same healthy capture request, encoder and audio
clock alive. Reset or movement resumes retention of the already-recorded tail;
it does not wait for a new GPU activation. Explicit session pause still revokes
capture and disposes that request's media/helper objects. Explicit unpause gets
a new nonce and source epoch. Source shutdown signals revocation
first; recorder shutdown then stops the audio source before joining the sink.
Only the last consumed native frontier can seal the final video interval.

Project64/ROM teardown revokes the capture epoch before withdrawing the clean
image source. A delivery worker that already took a record checks the live
epoch before treating a missing image as a fault. If image loss (native reason
10016) arrives first, the lease supervisor keeps reading control for at most
one second. Only explicit ROM closure, producer death/identity retirement or a
fresh-request retirement makes that end normal. Otherwise it retains the
original fault, including when the recorder is stopped during that window.
Media reconciliation allows 1.25 seconds for this classification and the next
control poll. Cleanup failures remain faults; reopening uses a fresh request.
Unpublished work is an explicit incomplete suffix, never invented coverage.
Expected recorder cancellation is typed separately from failure cleanup. A
channel-close race is benign only with that cancellation and a compatible
terminal channel reason; real native faults and failed disposal stay failures.
Source timestamps and the honest incomplete-coverage notice are never shifted
to compensate for a recording run that began after the attempt.

Partial construction retains the mux for cleanup. Archive failure cannot skip
channel or helper disposal. A helper `done` flag is insufficient: the private job
must be empty, have zero active processes, and have its handle closed. Unproved
cleanup prevents capture reuse and scratch deletion.

Retirement revokes source/channel admission, then drains only already-admitted
helper replies and requests native Close before mux/archive finalization. It
does not publish those replies or acknowledge metadata through a closed channel.
Close may return PENDING; a bounded media-worker loop retries Close. The controller
accepts success only with closed native/encoder states, zero held/pending masks
and all exact custody returns. Process disposal is checked separately afterward.
Protocol/watchdog failures still terminate the owned helper. Destroying its D3D
device with a key held can abandon the shared mutex and leave native resources
exhausted; graceful Close avoids that premature destruction when retirement can
complete. Native retirement deadlines can still expire before cleanup is reached.

The ledger stays attached if any capture/media owner fails to join. Its SQLite
connection belongs to a retained sink as well as the recorder; closing it early
could block bounded teardown or invalidate queued source metadata.

Partial audio startup owns its source before calling start. Fallback requires
successful disposal of that source; failed teardown retains the actual source,
output or ledger object and the machine recorder lock. `audiostop.py` isolates
proctap's private Windows disposal contract: the actual reader must finish before
the native COM owner is released. The installed dependency's public stop method
alone cannot establish this. PCM pump stop admission never waits for queue space.
None of these teardown operations enters the emulator process.

Startup garbage collection finishes before capture activation. Opportunistic
gen-2 collection uses `recorder.can_collect`, which requires retired GPU ownership
and no pending failed cleanup; automatic idle is not a safe collection window.
The existing five-minute backstop remains, so this is not a promise of zero
Python scheduling pauses.

## First capture freeze

His report (2026-09-25): "a very brief visual stutter in the gameplay. It
never happens again, just once towards the beginning of the session." Live,
one UpdateScreen ran 153 ms inside the first capture's native preparation.

**Cause (host-measured, 2026-09-27): NVIDIA's threaded optimization.**
Project64 has no driver profile, and the global profile leaves "Threaded
optimization" on auto. While it is engaged, the first time any other thread
in the process uses the GPU (a GL context, or a D3D11 device, even one made
on the renderer's own thread) the driver blocks the renderer for 150-270 ms,
once per process. A scratch driver profile on a scratch executable (deleted
afterwards; his Project64 settings were never touched) settled it, 8 fresh
processes per arm: forced off, first captures ran 6.4-15.6 ms; forced on,
193-231 ms; auto, 170-268 ms. Turning it off for Project64 is not the fix:
the renderer's own draw calls then cost about 20 times the CPU (0.17 against
0.009 ms for 800 calls, 1.1 against 0.045 ms for 6000).

**Fix: [renderer-thread capture](glossary.md#renderer-thread-capture).**
Project64 makes no second GL context and no D3D11 device. Every capture GPU
call runs on the renderer's own thread, in its own context, inside the
callbacks the renderer already makes: `gd_surface` on every VI drives
`render_tick`, and `gd_capture` issues a picture's sample right after its
copy. `plugin/gfxwrap/gpu_render.cpp` holds the three pieces:

- the sample, one nearest blit whose source rectangle is shifted so every
  output texel lands inside source pixel (8x, H-1-8y), read back into a buffer
  without waiting and taken at a later VI;
- the bridge: the helper creates the two keyed-mutex textures under the
  channel's names, and the renderer imports them and writes a selected
  picture with one flipped, even-cropped blit ([bridge](replay-gpu-bridge.md));
- a state guard that hands the scissor test and pack state back exactly.

The snapshot pool (`gl_snapshot.cpp`) is prepared in the renderer's own
context when the producer's context is the current one: DSA storage, no fence
or flush, and a poll that makes no GL call. `gl_snapshot.h` is an overlay
header and did not change, so the renderer keeps its build id
(`3833db66...-renderer`) and every non-practice ROM still runs the identical
renderer DLL. Because the helper now creates the bridge textures, the channel
wire became version 2 (`GC_CHANNEL_VERSION`, `gpuchannel.VERSION`, compared by
`test_gpu_channel`); [the ghost copy](#the-ghost-copy) made it version 3. A trainer meeting a wrapper from another build fails
with "the capture layer in Project64 speaks GPU channel version 1 and this
trainer speaks version 2: they come from different builds. Close Project64 so
the trainer can update the capture layer", and the setup screen's build-id
check reports the same installed layer as differing from this build. The
channel takes the client's decisions head first: once per VI it can find two,
and a later offer in a lower slot is not out of order. Without that, the media-stall
witness beside other GPU witnesses (ghost pipeline and compositor hosts, a second
cadence run) ended 6 of 6 runs with "channel failed" (reason `0x20002719`,
`GD_CHANNEL_FAILED`, detail `out_of_order`); with it, 8 of 8 passed under the same
load. Contention on his machine (OBS, clip recorders) makes the same two-replies
case, so this was a real way to end a recording, not a test artefact.

The delivery thread keeps requests, drain and deadlines, and reads the
frontier: the source's update transaction is open for the whole renderer
callback, so the frontier can only be read between VIs. It never uses GL.
Four rules follow from "the renderer calls back only on a VI":

- The renderer calls back only on a VI whose record it could stage, so at
  most seven pictures are held (one source slot always cycles) and omissions
  behind an unpublished picture are released at once. Each VI offers at most
  one picture and copies one; at 30 pictures on 60 VIs that is twice the
  need, so a backlog drains at 30 pictures a second.
- The picture captured just before a Project64 pause is offered after the
  pause; the frontier holds at it meanwhile, and pictures do not age while
  the renderer is silent.
- GL names outlive their request until the renderer's next armed VI or its
  context (see [long sessions](#long-sessions-and-inactive-play)).
- A test renderer must keep calling back between pictures (the cadence host
  runs a 60 Hz VI clock), or preparation never finishes.

Host acceptance (2026-09-27, the stutter host: the real stamp adapter,
delivery, channel and encoder helper against a hidden 60 Hz renderer at
1600x1200, one fresh process per run, worst renderer VI): the
worker-context build froze 164.9-272.7 ms at the first capture in 8 of 8;
renderer-thread capture ran 9.5-15.0 ms in 8 of 8 (the first copy and sample
into new textures: 6.8 and 4.7 ms in a traced run), second captures
4.8-7.3 ms. Capture now costs the renderer about 0.35 ms per picture: the VI
that carries a picture takes median 1.04-1.19 ms against 0.93-1.02 (p99
1.69-2.70 against 1.53-2.21), and the VI after it, which takes the sample and
copies the selected picture, 0.26-0.28 ms against 0.03-0.04. A synthetic game
load (800 and 6000 draw calls a frame) put capture's share of the renderer
thread at 0.45-0.52 and 1.30-1.58 ms against the old 0.31-0.34 and
1.24-1.34 ms, while the game's own draw calls kept their threaded cost.

Threaded optimization stays engaged once capture runs. The same stutter host,
drawing 6000 calls per picture (8 fresh processes per build), timed those
calls at 0.027-0.028 ms before the first capture and 0.034-0.038 ms during
and after it; with the optimization forced off on a scratch profile (deleted
afterwards) they took 0.94-0.98 ms. Under that load the first capture froze
161-206 ms on the worker-context build and ran 10.5-13.1 ms here, and the
intro before any capture peaked at 11.7-12.9 ms: there is no warm-up left to
cost the intro anything.

Rejected: the driver warm-up (2026-09-26 to 09-27), which kept the worker's
context and paid the same freeze once in every practice-ROM boot intro; a
D3D11 device on the renderer thread (274-408 ms, plus 100-122 ms to make it);
exporting from D3D11 inside Project64 (118-481 ms); a second GL context on
the renderer thread (25-84 ms, and 5-9 ms p99 per picture).

**Readout (his next session, no action from him).** The
[activation log](glossary.md#activation-log) keeps `rom_open_*`,
`gpu_request`, `gpu_prepare` and every `callback_stall`
in the 15 s after a request. `gpu_prepare` now reads `prepare_begin_qpc`,
`snapshots_ms`, `sampler_ms`, `luid_ms`, `channel_ms`, `reader_qpc`,
`import_begin_qpc`, `import_ms` and `end_qpc`, all renderer-thread steps, so a
stall overlapping them is our own cost on his renderer (a few ms in the
host). A stall of 100 ms or more near the first capture that overlaps none of
them remains unattributed by this readout. Correlate other graphics activity;
absence of a preparation overlap alone cannot name a component or thread.

**Measured: Discord's graphics capture hook.** Discord injects
into Project64 and, on the next present, runs
`GLState::InitCapture` then `Using WGL_NV_DX_interop2` (a D3D11 device plus NV
interop) inside SwapBuffers on the renderer thread: one UpdateScreen
`callback_stall` of 25-120 ms, whenever Discord attaches (1.9 s to 33 s after
launch). To identify it, compare the `Attach pid` and `InitCapture` lines of
`%APPDATA%\discord\logs\discord_hook.log` (local time) with the activation
log's `callback_stall`. Native QPC is the server's monotonic clock
(QPC / 10^7); the `GPU recording active` line's stages carry both it and Unix
time. Matched to the millisecond: 2026-09-24 34 ms, 09-25 65 ms, 09-28 120.8
and 120.5 ms. On 09-29, its 111 ms initialization interval ended in the same
millisecond as a 114.251 ms UpdateScreen stall; trainer GPU preparation had
finished 31 seconds earlier. The 09-25 153 ms freeze came 2 s before Discord
attached; it was ours and renderer-thread capture removed it. The hook logs
identify capture initialization, but do not establish which Discord feature
requested it. Discord's [revamped Overlay](https://discord.com/blog/player-release-q12025)
operates independently of direct game hooking; the visible Overlay, Clips and
screensharing must be distinguished before changing settings or claiming a fix.

**Opt-in compatibility experiment.** Before launching Project64, set
`SM64_TRAINER_GL_PROFILE=compatibility` in its launching environment to request
the compatibility profile on supported practice ROMs. The unset/default value,
unknown values and every non-practice ROM retain LINK's core-profile request.
This preserves the GL version and existing context creation, renderer thread
and capture bridge. The renderer must include the current
`renderer_context_profile.h`; it participates in the renderer build identity.
Changing this environment value after context creation has no effect on that
context. Clear it before launching the baseline comparison.

This tests whether the core profile contributes to Discord's
`Binding shared texture failed 500` report. A legacy-state/core mismatch is a
hypothesis, since Discord's final error check could observe an earlier error.
Successful shared-texture binding does not prove cold capture initialization
is fast. Compare fresh PIDs with Overlay enabled, preserving the actual
Clips/screensharing conditions, and correlate both logs as above. The candidate
needs native compilation, recording/ghost GPU witnesses and human live
verification before it can be called compatible or a stutter fix.

**Removed speculative overlay warm-up (2026-10-05).** The October 3 experiment
made a D3D11 device inside Project64 to reduce a later overlay's initialization
cost. Its synthetic host supplied a 250 ms load interval. The shipped trigger
only saw six VIs without a display list; that did not establish a safe load
interval. The live October 5 log records a 164.1 ms callback containing this
initialization. Its `absorbed` flag counted the complete callback, including
the injected initialization itself, so it did not prove existing loading slack.

The device warm-up, trigger, and special exemption were removed. No shipped
Project64 unit may create a D3D11 device or another GL context. The source guard
in `tests/test_gpu_delivery.py` covers every shipped unit. Historical host
results establish only the experiment's artificial conditions; they do not
certify Discord's actual attach or resolve intermittent gameplay stutter.
Current reports and the preserved source are in the private October 5 round.

## The ghost copy

With Ghosts in recording on, the clean recording never contains a ghost; a
second recording of the displayed picture runs beside it in the same capture
run (his decision, 2026-10-03, in
[ghost-native-runtime.md](ghost-native-runtime.md#ghosts-in-the-recording)).
The renderer keeps the displayed picture in its own display surface and names
it through SourceV4's `query_display`. The delivery's **twin lane** records it
through a second helper process with its own two textures. The twin never
waits on or extends the clean picture's custody, and a twin fault stops only
the twin: nothing in the lane can fail, quarantine or delay the clean capture.
With the switch off there is no display copy, no twin pool and no twin helper.

The channel (version 3) carries the lane in a 768-byte block at
`twin_offset` (after the payloads; RAM only, always present): the client's
request (`gc_twin_client`: READY with a strictly increasing lane once its
helper created both `-copy-` textures named in the header, OFF otherwise), the
producer's status (`gc_twin_status`: OFF, PREPARING, ACTIVE, STOPPED, a raw
`GD_TWIN_*` reason, and `resolved_pts`, below which every selected picture has
its twin outcome), two twin bridges and two twin receipts in the clean ones'
shapes. Every entry has its own sequence word. The producer writes an OFF
status with the channel's identity before the header, like the clean status.

All twin GPU work is on the renderer's thread, like the clean work:

1. **Start**, the render tick after a READY with a new lane while the clean
   capture is ACTIVE: a twin snapshot pool with the clean pool's images and
   size (refused if both pools together would pass the snapshot limit), an
   import of the twin textures, then ACTIVE and `display_consumer(true)`.
   A SourceV3 renderer stops the lane at once with `GD_TWIN_UNAVAILABLE`.
2. **Capture**, in `gd_capture` after the clean copy and its sample:
   `DISPLAY_VI` snapshots the display surface into the twin pool;
   `SAME_AS_CLEAN` copies nothing (the clean snapshot stands for it); a full
   twin pool is a missing twin picture. No sample is taken for the twin.
3. **Decision**, right after the clean bridge copy succeeded and before the
   clean image returns: a free twin texture (key 0 proven) receives the display
   or clean snapshot and its bridge carries the clean decision's occurrence,
   encode serial, PTS and duration. No free texture, a full key or a missing
   twin picture counts `missed`; it is never retried. A twin copy fault stops the
   lane with `GD_TWIN_BRIDGE_FAILED`.
4. **Custody** mirrors the clean ledger: a twin receipt plus key 0 retires a
   twin texture. Stopping, key 0 alone retires it.
5. **Stop**, on OFF or a new lane: no new twin copies, STOPPED once every
   twin texture came back, then this lane's names are deleted. A key held past
   the picture deadline (`max_age_ms`) is `GD_TWIN_KEY_ABANDONED`: its names wait
   for the epoch's GL retirement and no other lane starts in that capture run.

Every twin GL step outside a picture's copy runs in a capture gateway of its
own and drains its errors there. The source gateway records any GL error it
finds at its end and then refuses every later capture, so an error left by a
twin step would have ended the clean recording. The twin pool's copies drain
their own errors the same way (`Snapshots::prepare(..., owns_errors)`).

Renderer cost, measured 2026-10-03 with the cadence host (`SM64_CADENCE_TWIN=1`,
`test_ghost_copy_native`): 60 VIs and 30 pictures a second at 1600x1200 through
the real helper and media path, a ghost on every picture (the worst case: a
display copy on every presentation), this host returning each twin texture at
once. Over four paired runs of 660 VIs, the renderer's time per VI (the whole
update, display copy and capture included) had a median of 811-838 us with the
lane off and 940-1083 us with it on, a p99 of 1.96-4.87 ms off and 2.12-3.13 ms
on, and a maximum of 8.5-11.6 ms either way; zero clean refusals, every clean
picture decoded exactly, and 330 of 330 twin pictures copied. The GPU's power
state moves these numbers more than the lane does (the same run measured 300 us
medians while other GPU work held the clocks up), so read them as "about a
tenth of a millisecond more per VI", not as a precise cost. Under that kind of
outside load the existing 1600x1200 cadence witness refuses pictures on the
base commit too (3-7 per run, the same on this design's commit), so a refusal
there is not attributed to the lane without an interleaved base comparison.

## Accuracy and bounds

The unchanged picture selector uses the same stride sample and source stamps.
Occurrence IDs distinguish repeated/reset game counters and equal rounded UTC
times. The encoder receives monotonic 90kHz PTS; actual VFR held durations remain
separate from nominal 30fps encoding metadata. Retained heartbeats encode a fresh
packet from the retained input without another source GPU copy or shared acquire.

The bridge blit applies the existing top-down orientation and floor-even
top-left crop; alpha passes through, and the encoder does not read it. The
selection sample forces alpha opaque. Existing quality choices and time-forced IDRs feed the
encoder. `PacketFragmentMux` uses PyAV's `add_mux_stream` (PyAV17.1 or newer), so it
does not decode or re-encode compressed video. The existing audio placement and
pacing operate on real timed stereo PCM before AAC encoding and shared fragments.

`GpuSettings` owns fixed queue count, byte and age limits. Selection checks media
credit before consuming another native offer. Audio callback handoff is bounded
and never waits for an encoder/media worker. Native snapshots and bridge pools
also have fixed slot/byte limits; those count logical payloads, not total driver
or encoder VRAM. Capture handoff queues do not grow with the session's frame
count; persistent media and identity indexes follow the retained recording.

`gpumediaworker.py` owns selected-row/feed persistence, AAC, fragment muxing and
archive publication on a separate sink thread. The capture coordinator uses the
same selector with a private short history and copied stamp registrations. Probes
run once at selection; complete bounded row snapshots enter the FIFO before its
comparison baseline advances. Video and its exact feed identity enter atomically.
The sink stores that feed before muxing can expose corresponding sample bytes.
Delivery counts advance only after a successful mux write, not on queue admission.

The queue includes its in-flight command in byte/count/age bounds; no lock around
admission encloses codec, database or archive operations. Once compressed packet
ownership moves there, GPU source/bridge credits can return independently. No
full-image copy or extra encoder is introduced. Capacity, excessive age and sink
errors end the affected recording explicitly rather than dropping pictures or
growing memory indefinitely. Shutdown joins the sink after native helper
retirement. An unfinished sink retains archive and ledger ownership; a joined
publication failure does not masquerade as surviving GPU ownership.

The autonomous `test_gpu_cadence.py` fixture runs the actual native stamp adapter,
source pool, GPU bridge and NVIDIA encoder while the producer advances without
waiting for Python. A deliberate 350 ms mux stall fills the source pool in the
synchronous control (counted refusals); the separated sink keeps all 60 pictures
with zero refusals, exact independently decoded picture/source-PTS joins and
unchanged decoded audio.
This closes a hole in older GPU tests whose stubbed frontier could never report
source refusals. It does not certify untested live resolutions or long-session
driver/scheduler behavior. Current live startup remains a separate acceptance test.

During automatic idle, `fragmentretention.py` expires closed extents wholly born
in that idle interval. It keeps the configured pre-roll, one predecessor extent
for GOP/AAC dependencies, and the current writer extent. The cutoff uses the
published A/V frontier, frozen before reading the current idle epoch. A resume
cannot let an in-flight trimming pass advance into the required lead-in. Earlier
active history remains subject to normal ring budgets; it is not removed just
because the player became idle. Continuing legacy sinks discard extents born
after an explicit pause, preserving the preceding automatic-idle tail.

Retention uses a deque per idle epoch, with each candidate admitted and expired
once. No whole-session scan is added per picture. Reader leases can temporarily
hold expired bytes beyond the soft storage cap; release permits deletion and
eviction-driven identity pruning. Save/PB publication remains separately owned.

## Long sessions and inactive play

Session length alone does not increase source/snapshot/bridge slot counts.
The source pool has eight slots, the snapshot pool seven (one source slot
always cycles, so every VI still reaches the renderer callback that does the
GPU work) and the bridge two. Texture and encoder resources are prepared for a
request and reused per picture. The helper's encoder retires with the
request; this process's GL names retire at the renderer's next armed VI (the
next request's first), or with the renderer's context, because only the
renderer's thread may delete them. Between requests Project64 therefore keeps
one idle pool (about 60 MB at 1600x1200). Logical texture-byte limits exclude opaque driver
and encoder allocations. A permanently unsafe native retirement quarantines
one runtime pool and refuses subsequent requests in that PJ64 process instead
of accumulating replacement pools. Closing PJ64 ends that process ownership.

| State | Continuing work | Retention and release |
| --- | --- | --- |
| Server absent | Passive lifecycle/lease control; the inactive delivery worker sleeps on events. Revocation is asynchronous. | No new capture after revocation; admitted work drains. An unsafe retirement stays quarantined until PJ64 exits. |
| Playing with server | Input polling, source snapshots, selection, encoding, audio and fragment publication. | Fixed handoff queues; completed footage follows the configured recent-attempt window and byte/free-space budgets. Saved/PB files are separate. |
| Motionless with game still advancing | Capture stays warm to preserve the first returning input and its picture; automatic idle changes retention. | Closed idle extents expire behind the pre-roll/GOP/AAC tail. Existing active history still follows normal budgets. |
| PJ64 CPU paused | Input polling and control/media coordination continue. No advancing source boundary means no new source snapshot or selection; the one or two pictures captured just before the pause wait for the next VI's renderer callback, so they are offered after resume and the frontier holds at them meanwhile. | Exact repeated input-read instants spool in bounded pending RAM; their compressed evidence grows with the pause. Game-menu pause can still animate and follows the preceding row. |
| Explicit trainer pause | Demand is revoked and request-owned capture/media resources retire. | Resume starts a fresh request. Unproved cleanup retains its owner and prevents replacement. |
| PJ64 closes or server shuts down | Revoke, drain, close the owned helper/audio/sink, then release storage ownership. | An unfinished owner remains explicit; shutdown does not declare cleanup successful merely because it was requested. |

Handoff backpressure must not multiply GPU sampling: the sole unpublished head
owns its completed selection sample until publication succeeds. A busy client
page retries publication of those same bytes and stamp. The next occurrence
gets a fresh sample; no picture, source identity or timestamp is inferred.

Other bounds have different meanings. The fragment index retains at most
131,072 samples, recent completion metadata at most 1,000 attempts, and replay
cut locks only current holders/waiters. These are not a bound on the database:
input/journal history outlives temporary video retention. A long paused frame's
exact timestamp stream is searched in bounded decompression blocks; it is still
linear in evidence size to validate the entire stream. Sealing and serializing
that compressed history currently materialize the complete blob. Failed file
deletion can also retain bytes and pending retry entries beyond soft limits.
Neither case should be described as constant-memory storage for unlimited time.

True CPU pause needs a separate live check. If native picture/frontier delivery
stops while media coordination continues, audio cannot advance beyond committed
video. The audio pacer's wall-clock silence fill can still produce PCM even when
native audio callbacks stop. The bounded PCM age/count/byte limits then end the
run and recovery can retry. This prevents unlimited buffering; it does not
establish quiet paused-hours behavior. Whether PJ64's actual pause leaves the
frontier advancing or retires demand first is a separate live observation.
Do not invent a video frontier from wall clock or discard genuine audio to hide
the condition.

The five-minute Python full-GC backstop remains a measured scheduling pause.
Removing it without replacing cyclic-object reclamation would reintroduce a
known long-session leak. Bounded queues and accelerated CPU tests do not prove
smooth live presentation, stable driver memory, or acceptable multi-hour play.
Use the native logical-allocation/retry summaries and retained server resource
history described in [profiling](profiling.md), with the user's long-session
smoothness/audio report as a separate acceptance witness.

## Setup and diagnostics

`/api/replay/status` exposes `frame_source_health.kind="gpu"`, queue/byte counts,
source epoch, current state and any bounded failure message. Its
`capture_receipt` binds producer PID/birth, control generation, request token and
source epoch to actual non-repeat pictures acknowledged by the mux and ledger.
Encoder heartbeats cannot inflate this receipt.
`automatic_idle` and `sessions_started` distinguish inactive retention from
capture teardown and expose unexpected activation churn.
`pump_paths` counts media ticks that skipped the channel (`quiet`), read only
its status (`status`) or scanned offers and bridges (`scan`);
`torn_status_reads` counts torn status reads the tick survived.
Top-level `recording` is false when the GPU worker is preparing, unavailable or
failed, even if the recorder still owns a waiting worker. `publication_error`
retains the unavailable reason. `frame_source_health.publication` reports pending
compressed bytes/blocks, writer error and the longest archive feed with its UTC
start. The final writer snapshot is logged after retirement even on failure.

Setup verifies physical wrapper files independently from runtime readiness.
Active GPU readiness needs moving lease acknowledgments and captured-picture
receipts. Deliberate idle preserves a positive receipt only for the same live
producer/request and requires fresh ROM/game/input evidence. It does not create
periodic GPU work just to make a readiness indicator move. See the
[setup chain](../.claude/rules/chain-setup-readiness.md).

The server log records GPU run/source/adapter/native identity at activation,
audio initialization duration, and one owning error report including the media
symptom and lease supervisor reason. Native channel closure retains its first
terminal reason: channel reasons use tag0x10000000, delivery/control reasons
tag0x20000000. These tags change no wire layout or capture operation.

Use `tools/graphics_diagnostics.py --seconds 0 --output <new-folder>` to preserve
read-only loaded paths and ControlV1 build identity. The wrapper writes
`sm64_trainer_gfx.log` (4 MiB, then `.1` and `.2`) and the startup-only
`sm64_trainer_gfx.activation.log` beside itself ([profiling](profiling.md));
an existing log can belong to an earlier build, so verify its PID/build/time
before attribution.
Never restart the user's applications to collect diagnostics.

The current native encoder is NVIDIA NVENC. No CUDA Toolkit installation is
required. Other GPU encoders remain unqualified; an unsupported GPU keeps the
desktop grab rather than run blocking capture on the emulation thread. The
installed app starts its helper as itself with `--gpu-helper` (`gui_entry.py`);
a source run starts the base interpreter on the helper module
(`core/childproc.py::python_child`), so the helper is the server's direct
child and keeps STARTF_FORCEOFFFEEDBACK. The portable extension seam is the encoded packet and
exact custody contract, not a second timing model.

## What failed and why

Diagnosed dead ends, so a future change does not repeat them. Hop-level
entries live in the chain catalogues:
[input timeline](../.claude/rules/chain-input-timeline-frame.md),
[replay readiness](../.claude/rules/chain-replay-readiness.md) and
[setup readiness](../.claude/rules/chain-setup-readiness.md).

- **CPU `ReadScreen` capture lagged the game (rounds 14-17).** Lag worsened
  with seconds of crackling and pitch distortion. LINK's `ReadScreen` runs one
  synchronous command on the renderer thread and emulation waits for it; there
  is no asynchronous export. Calling it from a worker still waits on the
  renderer handoff, and CUDA after readback keeps the GPU-CPU-GPU round trip.
  Replaced by the in-renderer GPU snapshot and delivery worker; deleted
  2026-09-16.
- **Passive stutter with the wrapper installed (rounds 13-16, 45-48): cured,
  cause never isolated.** "Super laggy right now" (round 45); "Still stutters
  with direct R31" (round 47). It continued with the server closed and stopped
  when Project64 closed. Round 48 moved the shared GL anchor from ROM open to
  the first captured frame (`EnsureAnchor`), and the same day the NVIDIA driver
  was updated to 32.0.16.1692; the next build was accepted: "No lag. No
  stuttering." The anchor on/off pair was never run, so which change cured it
  is unknown. Ruled out along the way: the round-14 inactive-callback changes
  (GL probing, callback file I/O), the `ReadScreen` barrier (cannot explain a
  stutter with the server closed), storage maintenance (a 2 s cadence, not
  every few frames), the five-minute GC (38-84 ms) and helper accounting. If it
  returns, run the anchor on/off pair on the current driver first.
- **Source refusal 10022 (rounds 32-48).** "I noticed a bunch of errors when
  starting the server" (round 32), and a PB saved with no footage. The causes
  were layered: capture credits held across cold media setup, AAC and SQLite
  (round 37); a `threading.Event.wait(0.004)` tick that sleeps 15.5 ms and
  drained the eight-slot pool (round 48); and one refusal ending the whole run.
  Fixed by the separate media sink, `tickwait.py`, and counting a refusal as a
  missing picture. Rejected: a bigger pool (masks stalls, costs GPU memory);
  moving only disk publication (round 36, AAC and SQLite stayed on the credit
  path and it failed live); a fully native mux (rewrites clocks that were
  already exact); retries or log suppression (the refusal is a symptom).
- **No footage after a settings change (round 48).** View answered "No
  published replay footage is available yet" while recording read true.
  `pending_bytes` had been raised to 9 MiB, above the native request cap of
  slots times packet bytes (8 MiB), so every GPU request was refused. The caps
  are now shared in `gpurequest.validate_limits` and a test packs the shipped
  `GpuSettings`.
- **Unbounded cleanup retries (2026-09-15 full gate).** A test worker looped
  for nine hours logging a pending lease cleanup every 30 s. Both cleanup loops
  (`GpuDemand._retire`, `GpuCapture._retry_cleanup`) now have retry budgets and
  keep the owner.
- **An opt-in GPU witness broke silently (round 48, found 2026-09-16).**
  Round 48 made the stamp adapter omit an origin change with no swap
  (`SA_NO_SWAP`). The fake renderer in `gpu_cadence_host.cpp` never counted
  swaps, so every picture after the first was omitted, and its blocking control
  still expected a refusal to end the run. The configured gate skips the GPU
  witnesses (they need `SM64_TEST_GPU_BRIDGE=1` or `SM64_TEST_GPU_SELECTION=1`),
  so nothing went red. After any native contract change, run them with their
  flags on this machine's NVIDIA GPU.
