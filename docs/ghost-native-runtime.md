# Native ghost runtime contracts

The native ghost uses libsm64 C geometry inside the pinned GLideN64 C++
renderer. It evaluates recorded graphics poses independently at 30 Hz. Physics
resimulation and migration of older video-only recordings are outside this
format. The full recording and presentation path has a 33.33 ms frame budget;
geometry microbenchmarks alone do not establish that budget.

This implementation is still in progress. `SM64GhostSourceV1` currently exposes
pose and world-pass capture. It does **not** advertise `GS_CAP_RENDER`; model
evaluation, the compositor, clip transport and clean presentation must be wired
together before the native option can be offered to a player.

## Source occurrence and lifetime

[`ghost_stream.h`](../plugin/gfxwrap/ghost_stream.h) owns the versioned native
capture API. It is independent of the video SourceV2 stamp and its coalescing.
One value is offered for each observed display-list occurrence, including explicit
refusals. Configuration, ROM changes and rewinds invalidate prior live identity.
A recorded clip retains its original identity; replay does not require its epoch
to equal the current live epoch.

The producer runs inside the serialized renderer command. The reader copies a
value from a bounded queue and releases the slot. It never retains a pointer to
emulator RAM or an internal slot. A full queue counts and drops the new record;
the emulation thread never waits for the consumer. Configuration has one
serialized control writer and bounded render-thread acquisition.

The record includes graphics root transform, animation phase, body visual state,
level/area, camera and occurrence clocks. Component validity is separate: a live
camera may be usable while live Mario's pose is unsupported. Playback needs a
valid **recorded pose** and a valid **live camera**. Missing state must not be
filled from a neighboring frame or inferred from physics position.

## Model conversion and materials

[`ghost_pose.cpp`](../plugin/gfxwrap/ghost_pose.cpp) converts the captured public
pose into the model's C ABI and interleaves its caller-owned mesh for the
compositor. Model initialization and asset loading belong outside steady-state
drawing. Scratch storage is reused; geometry evaluation does not advance physics
or depend on which recorded frame was evaluated previously.

`area_update_counter` drives visual callbacks. The captured body action is kept
separate from Mario's action, and raw head angles map to effective graph XYZ as
`[1], [2], [0]`. The view uses the live rendered camera and SM64's look-at
calculation. Projection and viewport corrections come from actual renderer state.
Scene lighting is not captured yet; the current converter supplies Mario's
conventional directional light.

The current floating-point geometry/view path does not reproduce SM64's 16.16
quantization of every accumulated camera/bone matrix. Quantizing the view alone
would not make it equivalent. Live depth alignment remains a measured acceptance
boundary. Projection already contains screen roll; applying it again is wrong.

Material codes are explicit per vertex and originate in the model's actual N64
combiner commands: solid, alpha mix, decal RGBA, decal RGB, modulate RGB and
modulate RGBA. Texture coordinates are not a material discriminator. Model and
compositor codes are checked together at compile time. Unsupported visual state
is refused rather than silently rendered as ordinary Mario.

## World pass and clean display

[`ghost_pass.h`](../plugin/gfxwrap/ghost_pass.h) qualifies a world pass from
actual triangle submissions, projection and target state. Clearing Z-buffer
geometry mode provisionally seals the pass only after both triangles and
deferred rectangles have flushed. A later world pass, target change or
interrupted list revokes it. A resumed RSP list is incomplete even when its tail
clears the upstream `infloop` flag. Presentation may consume only a finalized
candidate with the current configuration and context generation.

Ordinary OpenGL depth and the renderer's N64 R32F image-depth route have different
representations. The target carries their actual mapping and viewport. Unknown
depth routes, multisampling and unsupported projection corrections are refused.
The compositor needs private self-depth so overlapping Mario triangles receive
opacity once per visible pixel. Original game color, depth and framebuffer
writeback stay clean.

The compositor does not consume `glGetError`, which belongs to the renderer's
existing diagnostic gateway. Integration must supply the installed GL dispatch
and revoke a candidate on errors observed during preparation, drawing or HUD
mirroring. Resource validation alone is not proof that every driver operation
succeeded.

The presentation branch must preserve HUD draw order and the renderer's VI
mapping. SourceV2's front-buffer capture contract cannot be relabeled as an
offscreen clean surface. Native playback therefore remains unavailable until an
explicit clean capture/presentation contract and its real-renderer witness exist.

## Verification

`tests/test_ghost_pass.py` compiles the actual pass, queue and pose-conversion
modules against native hosts. The model-provider stub in the conversion host
tests conversion and refusal behavior; it does not prove libsm64 fidelity. The
separate model tests own real geometry and asset validation. Compositor tests own
actual hidden OpenGL rendering, opacity, terrain depth, material and state restore.

For the staged RSP lifetime witness, generate a fresh renderer tree with
`tools/stage_link_witness.py --source <pinned-source> --out <temporary-output>
--boundary --context-lifetime --ghost`, set `SM64_GHOST_WITNESS_SOURCE` to that
output, then run `tools/run_tests.py tests/test_ghost_pass.py`. The test compiles
the guard extracted from the staged renderer, including the resumed-list case.
An absent source tree is an explicit skip.

Compilation and hidden-host tests do not prove live Usamune address validity,
camera registration, visible terrain occlusion or the full 33.33 ms budget.
Those require the completed runtime and a separate live recording witness.
