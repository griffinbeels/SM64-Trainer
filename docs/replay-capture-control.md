# Native capture control: passive candidate

This document records the separate `--control-only` diagnostic build. The
connected source-build implementation uses the same lease protocol through
[Renderer GPU recording](replay-gpu-runtime.md); its capabilities and recording
behavior differ from the passive test below.

This is the first diagnostic candidate for replacing synchronous replay capture.
It does not yet capture pictures. The normal bundled DLL and its existing
picture/input/audio pipeline remain unchanged. Do not ship or install the
candidate as a completed replay replacement.

Build the separate candidate with `tools/build_plugin.py --control-only --out
<candidate-directory>`. The flag requires an explicit directory and refuses the
bundled DLL directory. Its source build identity ends in `-control-only`; the
Project64 plugin name includes `[passive test]`.

## What the candidate implements

- `ProcessDList`, `UpdateScreen` and `ProcessRDPList` forward directly. Their
  candidate branches make no additional clock queries, GL calls, RAM copies,
  profile/log calls, IPC calls or waits.
- One background worker owns a 4096-byte control page, small synchronization
  objects and a reference to its own DLL. There is no image pool, pixel
  readback or encoder. This describes allocation capacity, not measured whole
  process RAM savings.
- The worker observes owner process lifetime and a renewable three-second
  lease. PID plus creation time rejects reused process IDs. All such checks
  occur outside graphics callbacks.
- Client commands serialize on a named mutex; the worker only tries that mutex
  with a zero timeout when taking a snapshot. Ordinary contention retains
  the last valid command and still checks expiry. Its idle wait then observes
  mutex release/abandonment, command/lifecycle events and the owner deadline;
  no periodic polling is needed. All waits stay on this background worker.
  A torn command from an abandoned writer is refused.
- A lease can transfer from acquisition to a future source without an intervening
  disable. Closing an old lease cannot disable its successor or another generation.
- Shutdown signals retirement without joining the worker. A new initialization
  requests a new session; the worker releases the old page before reconciling
  the latest request. A failed reinitialization retires the old session too.
- Initialization does not reset the page publication sequence. Readers reject
  in-progress writes and closed/dead producers. Every status includes the native
  build identity, producer birth identity, generation and acknowledgement.

`plugin/gfxwrap/control.h` owns the native layout; `control_worker.h` owns the
worker lifetime. `replay/capturecontrol.py` is its small Python reader and
generation-bound owner interface. Discovery opens an existing control page; it
does not create a frame ring or enable recording. Only the recorder owner may
call `acquire()` when this interface is integrated with the recorder.

The candidate truthfully answers enabled demand with `UNAVAILABLE / NO_BACKEND`.
It never reports active recording merely because a control connection succeeded.
`PREPARING` is reserved for the next backend implementation. The normal recorder
factory has not been moved onto this protocol; keep the trainer server closed
during this candidate's passive gameplay check.

If the current server is also run, its existing Windows desktop fallback can
still record and save video. That does not mean this candidate captured native
pictures. Input history can remain available, but synchronized per-picture
controls require native stamps and correctly remain unavailable. Inspect
`/api/replay/status` (`frame_source`) together with the native control status.
The full replacement must recognize the new capabilities before choosing a
source and avoid allocating the unused legacy image mapping.

## Verification and next gate

`tests/test_capturecontrol.py` drives the actual x86 DLL from a small CPU-only
host and a Python client. It covers discovery, capability refusal, ownership
transfer, stale cleanup, expiry and renewal without pictures, owner death,
producer death, immediate repeated restarts, failed reinitialization, contention,
and cleanup errors. A compiled production-callback probe counts added clock/context queries,
waits and copies; injecting a clock query makes that probe fail. Neither test needs Project64,
a visible window or a GPU context.

These checks establish the tested protocol and callback properties. They do not
prove stock-relative live pacing/audio, safe GPU submission, pixel identity or
replay first-open latency. The next gate is the user's server-off gameplay check
with the separately identified passive candidate. Then prove the renderer-local
capture boundary with independent picture/stamp witnesses before GPU integration.
No candidate may be promoted on a synthetic callback result alone.

The existing `tools/graphics_diagnostics.py` collector includes a bounded
`control` status snapshot (build identity, state, generation, owner
acknowledgement and refusal reason). It does not acquire a capture lease.

The separate [renderer-boundary module](replay-renderer-boundary.md) now supplies
a CPU-tested adapter and occurrence ownership for the next stage. It is not
connected to this candidate and does not change its advertised capabilities.
