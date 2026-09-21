# Native ghost runtime contracts

The Mario view evaluates recorded graphics poses with the libsm64 C model and
draws them inside the pinned GLideN64 renderer under the live camera. The whole
picture view retains the video overlay. Both use the existing ghost preference,
opacity, timing nudge and tint controls; switching to Mario closes the overlay
window. See [session.py](../src/sm64_events/ghost/session.py) and
[native_session.py](../src/sm64_events/ghost/native_session.py).

The source connects capture, archive publication, native playback, compositing
and clean video recording. This describes that integration, not a successful
release build or live acceptance result. New native recordings are required;
physics resimulation and migration of older video-only recordings are outside
the format. The full game, recording and presentation path has a 33.33 ms budget
at 30 Hz. A geometry benchmark alone cannot establish that budget.

## Versioned boundaries

| Boundary | Current contract | Source |
|---|---|---|
| Capture | GC3: layout 168, sample 244; raw warp fields and independent derived clock | [ghost_capture.h](../plugin/gfxwrap/ghost_capture.h) |
| Source clock | GCC1: config 104, output 64; raw, progress and elapsed validity | [ghost_clock.h](../plugin/gfxwrap/ghost_clock.h) |
| Renderer capture | GS2, exported as `SM64GhostSourceV2`; bounded 128-slot queue | [ghost_stream.h](../plugin/gfxwrap/ghost_stream.h) |
| Playback | GP2: immutable preloaded clips and revisioned owner origins | [ghost_playback.h](../plugin/gfxwrap/ghost_playback.h) |
| Ghost IPC | GIPC2: status 256, request 2460, ring offset 4096, 512 records | [ghost_ipc.h](../plugin/gfxwrap/ghost_ipc.h) |
| Portable clip | GCLIP2: header 160, record 260, maximum 240000 records | [ghost_clip.h](../plugin/gfxwrap/ghost_clip.h) |
| Clean recording | SourceV3 explicitly identifies the clean VI surface | [link_source_api.h](../plugin/gfxwrap/link_source_api.h) |

Sizes are bytes. Native ghost versions are checked together; older native clips
have no compatibility reader. SourceV2 video capture retains its original contract.
GS advertises pose and world-pass capture, not `GS_CAP_RENDER`: its table is a
capture interface. Rendering uses the separate playback/draw path and requires
negotiated clean presentation. A capability bit or prepared model is not proof
of a displayed ghost.

## Source occurrences and clocks

[ghost_link.cpp](../plugin/gfxwrap/ghost_link.cpp) captures inside the serialized
renderer display-list command. [ghost_capture.cpp](../plugin/gfxwrap/ghost_capture.cpp)
reads bounded, validated N64 memory without writes or retained RAM pointers.
The US layout comes from [ghost_layout.py](../src/sm64_events/memory/ghost_layout.py);
new camera/graphics addresses retain their live verification gates. JP is not
configured for this path. The product path targets Windows x86 Project64 and
the supported US practice ROM. Its presentation requires OpenGL 4.3; the world
hook qualifies the supported F3D command path, not LLE or arbitrary microcodes.

The sample contains graphics root, animation phase, body state, scene, camera,
live action, raw timer/warp evidence and derived clock. Physics XYZ is not a
replacement for the graphics root. Unsupported roots or visual state produce
explicit refusals. Clock, pose, camera, action and warp validity are independent;
missing pose evidence does not erase a usable clock or camera. The capture reader
clears derived clock output; [ghost_stream.cpp](../plugin/gfxwrap/ghost_stream.cpp)
fills it before publication and drawing.

The native classifier follows the existing `EpochTracker`/`IgtClock` transition
rules for levels, retries, area warps and teleports. Python encodes configuration
from the existing detector/action registries. Global timer establishes recency;
it never supplies invented elapsed IGT. Missing inputs, source gaps, rewinds,
counter wrap and unexplained backward jumps refuse affected timing evidence.
Raw identity, trusted banked star progress and continuous elapsed differences
have separate validity. Never subtract elapsed values across continuity IDs.
Level/retry transitions can retain elapsed continuity; the tracker still owns
whether a segment remains armed.

The clock runs before the source queue. Queue loss can leave a gap in stored
occurrences without losing source-clock continuity. A full queue drops and
counts the new record; the renderer does not wait. Configuration, ROM changes
and rewinds invalidate live identity. A saved clip keeps its original epoch and
does not need to share the new live epoch.

## Authoritative origins and playback

[projection.py](../src/sm64_events/tracking/projection.py) exposes the active
target's confirmed star origin or delegates to
[SegmentEngine](../src/sm64_events/tracking/segments.py). A marker contains owner
kind, session, arm identity, exact global frame and event UTC. A segment's latest
move-clock rebase has its own frame and UTC. Reattributed star entities can have
a segment-owned clock; public target kind does not determine clock kind. Closed
attempts persist `native_clock_frame`, `native_clock_utc`, `native_clock_kind`
and the actual `closed_frame`.

Python forwards changed markers and controls, not per-frame playback commands.
[ghost_origin.cpp](../plugin/gfxwrap/ghost_origin.cpp) resolves a marker against a
bounded source ledger for the current producer/configuration/epoch. UTC narrows
the exact frame to a one-second window; it does not select a nearest pose.
The first occurrence owns the frame, including refusals. Conflicting continuity,
elapsed, raw IGT, star generation or clock validity is ambiguous. Star origins
must satisfy the canonical near-zero threshold. Missing or expired origins hide
the ghost. Resolution is retained for the immutable command.

[ghost_playback.cpp](../plugin/gfxwrap/ghost_playback.cpp) preloads the clip and
selects at the renderer boundary using live elapsed minus owner-origin elapsed.
A star adds its origin raw IGT; a segment starts at zero. The timing nudge applies
to that coordinate. Paused clocks do not advance from wall time or Python poll
cadence. A new star generation or configured star-grab action ends that origin;
tuning the same origin cannot revive it. Explicit fixed seek is a separate mode.

The index chooses the first exact coordinate, including an unsupported pose,
then checks that row's scene against live. It never searches a later duplicate
or another scene for a usable pose. Before-start selection clamps to the first
row; gaps, after-end, wrong scene, invalid timing and pose refusals hide playback.
Per-row scenes allow coherent cross-area clips.

## Recording, preservation and ownership

[NativeCapture](../src/sm64_events/ghost/capture.py) owns a separate leased channel
named `Local\sm64_trainer_ghost_v2_<PID>`. Process birth, producer/ROM generations,
configuration, epoch and one QPC/UTC anchor identify its batches. The native
worker and Python client use a named mutex for mapped-page access; the render
thread never touches that page. File/hash checks, ROM asset preparation and clip
allocation belong to the worker. Drawing uses copied commands and borrowed
immutable buffers, with model adoption/retirement at renderer boundaries.

Disabling or losing the active origin withdraws drawing while retaining prepared
assets for later use. Lease/ROM shutdown revokes playback and retires assets;
worker collection and release establish storage cleanup. Three slots share a
128 MiB clip/index budget, enough for two maximum-size replacements; all resident
slots count, so another replacement can defer.

[PoseRecording](../src/sm64_events/ghost/recording.py) binds source and SQLite
archive lifetime to the machine's recorder owner. Late callbacks cannot write
into a replacement owner. Manual recording pause suppresses pose persistence
while the native lease can remain available for display. Optional native
initialization failure leaves video recording available. A failed source join
retains the archive, source and machine lock for cleanup retry.

[archive.py](../src/sm64_events/ghost/archive.py) retains refusals independently of
video coalescing or encoding. Export uses the persisted native origin and actual
closing frame, each with its own narrow UTC window. It never calculates an end
as `anchor_frame + rta_frames`. Every retained clip row needs valid raw/elapsed
evidence in one continuity, with monotonic occurrences and elapsed time;
timing-invalid archive rows cannot form a playable clip. A final closing reset
can remain as evidence, but a star index stops before the next generation.

[ReplayService](../src/sm64_events/replay/service.py) can preserve a template as
`native/attempt_<id>.ghost` plus its JSON commit record without encoding an MP4.
Saved video can also carry the sidecar. Lookup validates path, content hash,
attempt/session/target and all four persisted origin fields. The native session
caches by this metadata identity and file fingerprints: reprojecting the same
attempt invalidates its old asset without decoding a large clip on every check.

## Model, world depth and clean presentation

[ghost_pose.cpp](../plugin/gfxwrap/ghost_pose.cpp) converts the public pose into
the model's C ABI and reuses caller-owned mesh storage. Evaluation does not run
physics or advance animation. `area_update_counter` drives visual callbacks;
body action stays separate from Mario action, and raw head angles map to graph
XYZ as `[1], [2], [0]`. Explicit per-vertex material codes come from actual N64
combiner commands, not a UV heuristic. Unsupported model state is refused.

The camera comes from bounded lookup of the active graph camera matching the
area camera, including persistent position/focus/roll. It is not the final
display-list modelview matrix. Projection and viewport corrections come from
actual world submissions. Scene lighting is not captured; Mario uses the
conventional directional light. The floating-point model/view path does not
reproduce every accumulated N64 16.16 matrix quantization, so live registration
and depth alignment remain acceptance checks.

[ghost_pass.h](../plugin/gfxwrap/ghost_pass.h) qualifies actual world triangles,
projection and framebuffer/depth identity. Disabling Z-buffer geometry mode seals
only after triangles and deferred rectangles flush. A later world pass, target
change, interrupted/resumed list or stale command revokes the candidate. Ordinary
GL depth and N64 R32F image depth use different mappings; unknown routes,
multisampling and unsupported projection corrections are refused.

[ghost_draw.cpp](../plugin/gfxwrap/ghost_draw.cpp) builds a separate display branch.
Private self-depth applies opacity once per visible Mario pixel; original game
color, terrain depth and framebuffer writeback stay clean. The compositor uses
the installed dispatch and renderer error gateway rather than consuming
`glGetError` itself. HUD duplication is qualified against the current target and
state, preserving draw order or refusing the branch.

[ghost_presentation.cpp](../plugin/gfxwrap/ghost_presentation.cpp) preserves the
clean drawable, applies the renderer's VI mapping/gamma to the display branch,
and mirrors qualifying OSD into clean recording. SourceV3 publishes an explicit
clean-VI descriptor bound to update occurrence, context/drawable/resource
generations and successful presentation serial. SourceV2's front-buffer contract
is not relabeled. No negotiated clean consumer means no native ghost display.
Clean/failed swaps and context loss clear visible telemetry; a generated mesh
alone never reports that the ghost reached the game window.

## Verification boundaries

Focused hosts exercise the actual capture reader, clock, origin ledger, playback
loader, compositor and presentation modules. See
[capture](../tests/test_ghost_capture.py), [clock](../tests/test_native_ghost_clock.py),
[origin](../tests/test_native_ghost_origin.py), [portable parity](../tests/test_ghost_clip_native.py),
[playback](../tests/test_ghost_playback.py), [IPC](../tests/test_ghost_ipc.py),
[presentation](../tests/test_ghost_presentation.py) and
[connected pipeline](../tests/test_ghost_pipeline.py). The portable parity host
calls the real native loader with model calls forbidden; it proves acceptance
parity, not rendered geometry.

The connected hidden-WGL proof used GC3/GS2/GP2/GCLIP2 and the actual native
capture, clock, queue, model, compositor and presentation code with synthetic
RDRAM and terrain: RTX 5090, 640x480, 20 warmups and 180 samples. CPU plus GPU
completion plus hidden swap measured p50 0.3952 ms, p95 0.5007 ms and max
0.7434 ms. It also checked 100%/50% opacity,
unchanged pixels outside Mario, clean recording and wall occlusion. That receipt
binds its actual source hashes and executable. Paused IGT with advancing global
timer leaves the selected pose unchanged. This excludes live GLideN64 terrain/VI
work, IPC and video encoding; it is not the full live gameplay budget.

For the actual staged RSP lifetime guard, stage a fresh pinned renderer with
`tools/stage_link_witness.py --source <pinned-source> --out <temporary-output>
--boundary --context-lifetime --ghost`, set `SM64_GHOST_WITNESS_SOURCE`, and run
`tools/run_tests.py tests/test_ghost_pass.py`. An absent staged tree is a skip.
Use the shared test runner for every witness; inspect source hashes and explicit
GPU/ROM gates rather than treating skipped tests as rendered evidence.

Compilation and hidden-host success do not verify live US practice-ROM address
registration, camera alignment, terrain occlusion, uninterrupted clean recording
or the full 33.33 ms Project64 budget. Those still require current built binaries
and a separate live recording witness. Neither this document nor a synthetic
receipt closes that live acceptance gate.
