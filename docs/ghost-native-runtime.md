# Native ghost runtime contracts

The Mario view evaluates recorded graphics poses with the libsm64 C model and
draws them inside the pinned GLideN64 renderer under the live camera. The whole
picture view retains the video overlay. Both use the existing ghost preference,
opacity, timing nudge and tint controls; switching to Mario closes the overlay
window. See [session.py](../src/sm64_events/ghost/session.py) and
[native_session.py](../src/sm64_events/ghost/native_session.py).

The **Ghost recording** selector lists saved templates for the current star or
segment. An explicit choice is stored per target and strategy without changing
the input-comparison template; **Use input template** restores that relationship.
Each recording is qualified separately for Mario poses and whole-picture video.
A removed choice stays unavailable until the user chooses another recording.
The selection update includes its target so a delayed request cannot choose a
ghost for a different practice target. See
[selection.py](../src/sm64_events/ghost/selection.py).

The source connects capture, archive publication, native playback, compositing
and clean video recording. This describes that integration, not a successful
release build or live acceptance result. New native recordings are required;
physics resimulation and migration of older video-only recordings are outside
the format. The full game, recording and presentation path has a 33.33 ms budget
at 30 Hz. A geometry benchmark alone cannot establish that budget.

## Versioned boundaries

| Boundary | Current contract | Source |
|---|---|---|
| Capture | GC5: layout 184, sample 636; executed root matrix, warp fields, derived clock, recorded shadow (292 at 308) and sound events (36 at 600) | [ghost_capture.h](../plugin/gfxwrap/ghost_capture.h) |
| Source clock | GCC1: config 104, output 64; raw, progress and elapsed validity | [ghost_clock.h](../plugin/gfxwrap/ghost_clock.h) |
| Renderer capture | GS4, still exported as `SM64GhostSourceV3` (the version argument must be 4); bounded 128-slot queue | [ghost_stream.h](../plugin/gfxwrap/ghost_stream.h) |
| Playback | GP7: up to 16 immutable preloaded clips per command, 32 slots, revisioned owner origins, per-ghost hue, overlap fade, the `shadow_enabled` switch sound `volume` and the owner-`suspended` pause | [ghost_playback.h](../plugin/gfxwrap/ghost_playback.h) |
| Ghost IPC | GIPC6: status 256, request 18952 (16 clip entries of 1096, then `shadow_enabled` and `volume`), ring offset 20480, 512 records of 652 | [ghost_ipc.h](../plugin/gfxwrap/ghost_ipc.h) |
| Portable clip | GCLIP4: header 160, record 652, maximum 200000 records; header flag `GCLIP_DANCE_TAIL` | [ghost_clip.h](../plugin/gfxwrap/ghost_clip.h) |
| Clean recording | SourceV3 explicitly identifies the clean VI surface | [link_source_api.h](../plugin/gfxwrap/link_source_api.h) |

Sizes are bytes. Native ghost versions are checked together; older native clips
have no compatibility reader. Their files remain on disk; `OlderPoseFormat`
provides a typed reason (`older_pose_format`) to record a new attempt. An older
committed file keeps pruning protection and is never overwritten by a later
export; a playable saved copy of the same attempt still wins. GCLIP3/GC4 files
and archive rows (no shadow or sound) are older formats in the same sense.
SourceV2 video capture retains its original contract.
GS advertises pose and world-pass capture, not `GS_CAP_RENDER`: its table is a
capture interface. Rendering uses the separate playback/draw path and requires
negotiated clean presentation. A capability bit or prepared model is not proof
of a displayed ghost.

## Source occurrences and clocks

[ghost_link.cpp](../plugin/gfxwrap/ghost_link.cpp) captures inside the serialized
renderer display-list command. [ghost_capture.cpp](../plugin/gfxwrap/ghost_capture.cpp)
reads bounded, validated N64 memory without writes. The root probe borrows the
renderer-owned RAM only within one serialized display-list occurrence.
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

Visible roots use [ghost_root.cpp](../plugin/gfxwrap/ghost_root.cpp). SM64 clears
the object's transient `throwMatrix` after traversing its graphics graph;
floor-aligned actions therefore cannot be reconstructed reliably from that
pointer or the action alone. Capture follows the persistent Mario model graph
to its selected first body display list, then observes the actual admitted F3D
calls for the opaque generated-alpha list and that body. Their adjacent matrix
loads, stack depth, fixed-point matrix bytes and the object's fresh
`cameraToObject` must agree. A second active object sharing that model, duplicate
matches, stale animation, interrupted execution or unknown graph shape refuses
the pose. Freed master-list nodes never establish ownership.

The stored pre-animation model-view matrix includes gfx scale and Mario's .25
geo scale. Playback removes the recorded camera and these scales before passing
the effective world root to the model's throw-matrix route, which applies scale
once. The current camera then projects the model. Values preserve the actual
N64 16.16 draw matrix, including its quantization; they do not recover unrecorded
float precision. Tests execute the pinned staged `gSPDisplayList` and x86 matrix
loader, and cover floor tilt, nonuniform scale, camera motion and ownership
refusals. A new live recording remains the acceptance check for the installed
practice ROM's complete graph and emitted command sequence.

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

[ghost_playback.cpp](../plugin/gfxwrap/ghost_playback.cpp) preloads each clip and
selects at the renderer boundary using live elapsed minus owner-origin elapsed.
A star adds its origin raw IGT; a segment starts at zero. The timing nudge applies
to that coordinate. Paused clocks do not advance from wall time or Python poll
cadence. A new star generation or configured star-grab action ends that origin;
tuning the same origin cannot revive it. Explicit fixed seek is a separate mode.

After a state load the renderer resolves the restore itself. The worker only
reads a list's record after that list, so its recovered command can first
apply to the next list. The renderer therefore runs the same
`ghost_origin::Recovery` over each live sample it draws from (capacity
reserved at load, never allocated while rendering). It binds to each command's
origin unless it already reached that origin itself. A restore it resolves
draws on the load frame at the restored coordinate and revives an ended star
run, as the worker's new command would. An unresolved or ambiguous restore
hides the ghost on that same frame. While the worker's own recovery is
unresolved it keeps the applied command rather than withdrawing it, so a
later restore is not delayed a frame. The
[recovery witness](../tests/test_ghost_recovery.py) runs the worker one list
behind the renderer: over 10 loads and 92 frames the previous path showed 8
frames without a ghost and 1 at a wrong position (a forward load to an
unwitnessed state); this path shows 0 and 0.

A star or segment completion removes the tracker's arm until the next attempt,
so the session marks the owner suspended. The worker used to withdraw the
command then, which dropped the restore checkpoints: a load after a star grab
hid the ghost until the tracker re-armed (the blink seen live 2026-09-22 on
every such load, never on a load before the grab). The worker now keeps the
command and forwards `suspended` (GP7). Playback hides every ghost and its
sound while suspended, until the renderer's own recovery resolves a restore
it had not resolved when the pause began; that load draws on its load frame
and the tracker's next arm confirms it. The recovery witness loads once after
a star grab and once after a segment completion, each while suspended, and
counts 0 ghostless frames over 11 loads; forwarding the old withdraw hides the
load frame, ignoring `suspended` draws the paused frames, and not reviving on
a restore hides the load frame (each red).

### Several ghosts

One command displays a set of up to 16 saved recordings (the PB plus others)
against the same live origin and the same opacity, nudge, tint switch, overlap
fade and star-end policy. Hue is per recording. Each ghost selects its own row
from the shared coordinate: before-start clamps, and after-end, gaps, wrong
scene, refused poses and its own recorded touch hide only that ghost. A live
star grab or star generation change ends every ghost. Overlap fade compares
live Mario with each ghost separately. The renderer evaluates all ghosts into
one borrowed mesh each, sorts them farthest root first and draws them with one
`begin_batch`: one clean copy and per-body self-depth. A frame with no visible
ghost behaves like the former hidden ghost; clean output, HUD and presentation
guarantees are unchanged.

The worker identifies a recording by clip id plus SHA-256. Changing the set
reads and prepares only recordings not already held; an applied command whose
other controls are unchanged keeps drawing during that read, so adding a new PB
costs one file read and one preparation. A recording that leaves the set is
released: a pending copy retires immediately and a drawn copy stops at the next
render boundary. Re-adding it before that retirement waits one boundary for a
fresh copy. The renderer holds exactly the listed set after each boundary.
Savestate recovery belongs to the live origin, so set changes keep it; only a
new ROM identity clears it. An unreadable or oversized recording refuses the
whole set (`GIPC_FILE`/`GIPC_CLIP`) and hides every ghost until a corrected set
arrives. An enabled request with no clips, more than 16, a zero or repeated id,
a hue above 359 or a nonzero reserved word is a protocol refusal. The status
page reports `clips_requested`, `clips_prepared` and, while `displayed` is set,
`clips_visible`.

For star FOLLOW, the recorded template's own first touch also ends its display,
even when the timed landing occurs later. The worker prepares a first-occurrence
action index once; command adoption caches the earliest configured touch and
drawing compares the nudged coordinate against it. Independently valid action
evidence still counts on a refused-pose row or a later duplicate coordinate.
Nudging back before the recorded touch can show an earlier pose unless the live
origin has already ended. Fixed seek and segments without star-end behavior do
not use this cutoff. Drawing neither scans the clip nor allocates an index.

A FOLLOW clip flagged `GCLIP_DANCE_TAIL` (his ask, 2026-09-23, reversing the
2026-09-19 end-at-touch ruling for the ghost's own star) ends on its own
closing grab, not its first touch: it draws through any earlier star and the
recorded closing grab and dance, then holds the last recorded pose and fades
it out over `GP_STAR_FADE_FRAMES` (a tuning constant, 15 frames = 0.5 s), one
equal opacity step a frame (body and shadow), and hides exactly that many
frames after the last record, with or without `star_ends`. Its sound fades with it (the
placement gain carries the fade) and is set silent as it hides. Unflagged
clips, every recording saved before the tail existed, still end at the touch.
The live behaviour is unchanged: his own star grab (or a new star generation)
ends every ghost on that frame, dancing or not, and the tracker's suspension
that follows his completion keeps them hidden. The suspension comes only from
his attempt, so a ghost that grabs first dances and fades on its own timeline
while he is still playing; if he grabs first, the ghosts vanish on his grab
before reaching their own. The playback witness drives both orders and the
fade values (1, 14/15 ... 1/15, then hidden at 15 frames), and an older clip
beside the dancer ending at its touch.

The 100-coin star (his report, 2026-09-23: the ghost vanished at the 100-coin
star when the run goes on to an exit star) is timed by its exit segment: its
live origin kind is `segment` and its attempt closes on the exit star's grab.
[NativeGhost](../src/sm64_events/ghost/native_session.py) therefore sends
`stop_at_star` only for a star origin, so neither his 100-coin grab nor the
ghost's ends playback; his exit grab closes the attempt and the suspension
hides the ghosts (a poll or so after his x-cam, not on the grab frame). Its
export keeps the exit star's dance (any star run, whatever owns its clock), so
a new 100-coin ghost plays through its 100-coin grab, the exit star and its
dance, then fades; an older 100-coin clip plays to its end at the exit x-cam.

The index chooses the first exact coordinate, including an unsupported pose,
then checks that row's scene against live. It never searches a later duplicate
or another scene for a usable pose. Before-start selection clamps to the first
row; gaps, after-end, wrong scene, invalid timing and pose refusals hide playback.
Per-row scenes allow coherent cross-area clips.

## Recording, preservation and ownership

[NativeCapture](../src/sm64_events/ghost/capture.py) owns a separate leased channel
named `Local\sm64_trainer_ghost_v4_<PID>`. Process birth, producer/ROM generations,
configuration, epoch and one QPC/UTC anchor identify its batches. The native
worker and Python client use a named mutex for mapped-page access; the render
thread never touches that page. File/hash checks, ROM asset preparation and clip
allocation belong to the worker. Drawing uses copied commands and borrowed
immutable buffers, with model adoption/retirement at renderer boundaries.

Zilmar's `GFX_INFO.HEADER` supplies only the first 64 cartridge bytes, not a
pointer to the full ROM. [ghost_rom.cpp](../plugin/gfxwrap/ghost_rom.cpp) discovers
the matching full image on the worker using bounded, guarded reads of committed
memory. It matches the entire normalized header, validates the model assets
without binding a model, rejects differing duplicate images, excludes its own
buffers, and rechecks header/lifecycle identity. A copied header inside a large
readable allocation does not qualify as a cartridge. The
immutable result is cached by ROM generation separately from the chosen clip.
No ROM scan or file read runs on the render thread. A ROM acquisition refusal
uses `GIPC_ROM` (11); `GIPC_FILE` (6) describes clip acquisition. Neither a shared
ROM failure nor renderer readiness changes saved-recording eligibility.

Disabling or losing the active origin withdraws drawing while retaining prepared
assets for later use. Lease/ROM shutdown revokes playback and retires assets;
worker collection and release establish storage cleanup. Thirty-two slots (a
full set plus as many retiring copies) share a 128 MiB clip/index budget, and
the worker's retained files obey the same bound. One maximum-size clip
(200000 records, about 111 minutes at 30 Hz: 200000 x (652 + 4) bytes) fits;
two maximum-size clips do not with GC5. All resident slots count, so a replacement
may defer until the previous owner's storage is retired and collected. Nothing
displaces a pending clip; the worker releases what it no longer lists.
If a reader holds the status page during revocation, the worker retries that
publication before sleeping indefinitely. An expired owner cannot leave the
mapped page reporting ready merely because the final nonblocking lock was busy.

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

A star run closes at x-cam, the dance's first frame of the grab that completes
it (the exit star for the 100-coin star), so export appends the dance tail for
any attempt that is not a segment: the rows after the closing frame while Mario stays in a star
grab/dance action (`STAR_GRAB_ACTIONS`), the game clock advances, and timing
continuity, star generation and scene hold, at most `DANCE_TAIL_MAX_FRAMES`
(180, 6 s) past the close. Measured on his 2026-09-22 archive, dances run
about 100 to 125 frames past x-cam; the exit dance ends by freezing the clock
for the exit warp, the no-exit dance by returning to idle. Export often runs
the moment the attempt closes, so it waits for the dance to end, the recording
to stall for a second or close, or the cap's duration plus a second. A reset
or state load ends the tail at once. The manifest's `tail_records` counts the
tail; `check_manifest` binds the attempt's closing frame to the row before it.
The [archive tests](../tests/test_ghost_archive.py) cover each way a dance
ends, the cap, waiting for a dance still being captured, and no tail for a
segment or a close outside the dance.

Recorded pose usability depends on the captured clock and pose components, not
the recording's framebuffer qualification. The saved `world_status` remains
unchanged diagnostic evidence. Playback builds geometry from that pose using
the live camera and a separately qualified live world pass. A historical target
refusal cannot discard otherwise valid poses; an invalid live target still
prevents drawing. Unsupported pose occurrences remain in their original slots.
The manifest's `invalid_records` counts unusable clock/pose components, not a
historical world-target refusal.

[ReplayService](../src/sm64_events/replay/service.py) can preserve a template as
`native/attempt_<id>.ghost` plus its JSON commit record without encoding an MP4.
Saved video can also carry the sidecar. Lookup validates path, content hash,
attempt/session/target and all four persisted origin fields. The native session
caches by this metadata identity and file fingerprints: reprojecting the same
attempt invalidates its old asset without decoding a large clip on every check.
An explicit preservation failure is retained for the matching attempt identity
and shown by the ghost control; saving the input template can still succeed.

Video codec choice is independent of native pose preservation. AV1/H.264
recording and saved-video compression keep the native companion bytes and
manifest unchanged. A successful video Save queues compression only after its
native companion is committed; marking a native-only template never queues
video work. Playback through either the saved URL or its original pre-Save URL
marks the saved video in use so compression defers replacing its bytes.

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

With framebuffer emulation disabled, the pinned renderer still draws through
its screen-size framebuffer. Its current emulated-framebuffer pointer is null
in this mode. Target lookup must select the actual screen-size buffer and verify
the bound draw framebuffer, and presentation must observe that buffer's separate
screen-copy path. The normal emulated-framebuffer path cannot stand in for it.

[ghost_draw.cpp](../plugin/gfxwrap/ghost_draw.cpp) builds a separate display branch.
Private self-depth applies opacity once per visible Mario pixel; original game
color, terrain depth and framebuffer writeback stay clean. The compositor uses
the installed dispatch and renderer error gateway rather than consuming
`glGetError` itself. HUD duplication is qualified against the current target and
state, preserving draw order or refusing the branch.

A pose may carry the original shadow packet (at most 24 vertices, the 16x16
IA8 texture and its own matrix). The compositor draws it as a decal over the
frame's clean copy, before any Mario, against copied terrain depth with the
renderer's decal offset (-3,-3) and LEQUAL; N64 image depth omits it rather
than guessing delta-Z.

### Recorded shadow

Capture records the shadow the game executed, never an inferred disc. SM64
allocates display-list memory downward in traversal order, and `mario_geo`
processes GEO_SHADOW before GEO_SCALE and the alpha ASM, so a drawn Mario
shadow is 9 `Vtx`, its 5-command list, its G_MTX and the scale G_MTX directly
above the opaque alpha list the root proof already owns (list at alpha+144,
matrix at +80, vertices at +184; n64decomp `shadow.c` `create_shadow_player`,
`rendering_graph_node.c` `geo_process_shadow`/`geo_process_scale`,
`mario_misc.c` `make_gfx_mario_alpha`). [ghost_root.cpp](../plugin/gfxwrap/ghost_root.cpp)
accepts it only when, in one completed occurrence and after the root match:
the master-list G_DL at the root's depth names that list once, preceded by a
G_MTX load of that matrix equal to the renderer's modelview; the list is
exactly circle call, `G_VTX` of those 9 vertices, triangle call, restore call;
the three nested calls execute at the next depth and resolve through one
segment base; the combiner/texture-load, triangle and restore lists match the
US segment-2 words; every vertex shade is white; every texel has zero
intensity; the matrix is rigid and within 1024 units of Mario's
`cameraToObject`. Anything else (another object's shadow, none drawn, a
repeated or interrupted list, a translucent or metal Mario whose root is
refused) leaves the record without `GC_VALID_SHADOW`.

The record keeps the executed matrix, raw vertex positions, S10.5 texture
coordinates, per-vertex alpha and the texture's alpha nibbles. Playback
([ghost_pose.cpp](../plugin/gfxwrap/ghost_pose.cpp) `shadow`) removes the
recorded camera exactly as for the root, expands the game's eight triangles,
applies the half-texel shift for the 3-point filter, and hands the compositor
world-space vertices under the body's live view-projection. Opacity and
overlap fade apply through the pose; the shadow is black, so tint does not.
`shadow_enabled` (GP5/GIPC5 onward, default on; `NativeChannel` key
`shadow_enabled`, a bool) removes every ghost's shadow without rebuilding it.

### Translucent world layer (water over the ghost)

The ghost is drawn after the whole world pass against copied terrain depth.
The game's water surfaces (SM64 movtex in the transparent layer, render mode
`G_RM_AA_ZB_XLU_SURF`) test depth but never write it, so the ghost used to be
drawn over them: a submerged ghost read through the water untinted. Live Mario
is drawn before them and blended under them.

The staged `ContextImpl::drawTriangles` seam
([stage_ghost_renderer.py](../tools/stage_ghost_renderer.py)) repeats each
world triangle draw ([ghost_link.cpp](../plugin/gfxwrap/ghost_link.cpp)
`begin_layer`) whose N64 render mode compares but does not update depth, with
the renderer's own program, textures, depth function and polygon offset, twice
([ghost_compositor.cpp](../plugin/gfxwrap/ghost_compositor.cpp)
`begin_layer`). The first layer draw opens the layer: it copies the game's depth
and clears the layer to no color and full transmittance. Pass 0 keeps the
game's RGB blend factors and replaces alpha with (ZERO, ONE_MINUS_SRC_ALPHA),
so the layer holds the accumulated premultiplied color and the fraction let
through. Pass 1 writes the nearest surface depth. Each pass restores the exact
draw framebuffer, blend, color mask and depth mask. The ghost fragment shader
then replaces a fragment behind that depth with `layer.rgb + ghost * layer.a`
before its opacity blend. The result equals the game blending that surface
over a ghost standing there, and a ghost in front of the surface is unchanged.
It needs no captured colors and no format change. The camera below the
surface works the same way, since SM64 draws water from both sides.

The layer takes only a draw whose GL blend is dst `ONE_MINUS_SRC_ALPHA` with
source `ONE` or `SRC_ALPHA`, depth test on and depth writes off. Other blends
(additive, destination-reading) are counted (`skipped_blend`) and stay out. A
depth-writing world draw after the layer opened, a failed depth pass or a GL
error drops the layer for that list (`broken`); the ghost then draws as before.
N64 image depth and multisampling never open it. The layer composes several
surfaces in draw order but keeps one nearest depth, so a ghost between two
translucent surfaces takes both. Level fog on the ghost's own body is not
reproduced; that would need the frame's fog state, not this layer.

The [water witness](../tests/test_ghost_water.py) (`SM64_TEST_GHOST_COMPOSITOR=1`)
plays the renderer with two water bands, one in front of and one behind the
ghost: pixels behind the near band equal the analytic water blend at 100% and
50% opacity, pixels in front and outside the ghost are unchanged, the game frame
is bit-identical with and without the repeated draws, state restores exactly,
and a broken or refused layer draws as before. Removing the shader tint, the
depth comparison, the alpha override, the depth pass or the depth-mask restore
each turns it red. The connected pipeline witness draws real Mario behind a
water plane at his waist: 5535 pixels take the water blend exactly, 4437 above
it and every pixel outside him are unchanged (2026-09-23, RTX 5090). Live GLideN64
has not run it; which SM64 draws qualify is known from the decomp render
modes and the pinned blend code, not from a live frame.

### Sound events

Capture reads the `play_sound` request ring at the same boundary as the pose
([ghost_capture.cpp](../plugin/gfxwrap/ghost_capture.cpp) `capture_sound`).
The layout supplies `play_sound`'s code, its u8 producer, the audio thread's
u8 consumer and the 256-entry ring (VERIFY rows in
[layout.py](../src/sm64_events/memory/layout.py) with `address.sound_*`
gates). Before reading, the 12 instruction words must be the US leaf naming
exactly that producer and ring (bits at +0, source at +4, producer published
after both); a different build leaves sound unavailable. A record's sound is
valid only when the previous captured frame is exactly one game frame
earlier, so requests are attributed to one frame or not at all. It keeps, in
issue order, up to 8 request bits whose source is Mario's object
`gfx.cameraToObject`, with `GC_SOUND_OVERFLOW` when more were issued and the
raw producer and consumer. More than 256 requests between two frames cannot
be detected. Python reads them as `Frame.sounds`. `sSoundMovingSpeed` and
direct `stop_sound` state are not recorded, so slide/flight pitch changes and
the snore stop are not reproduced.

### Ghost sound playback

Ghost sounds play inside Project64 from SM64's own audio engine (the pinned
libsm64 copy, [audio.c](../renderer/model/audio.c), built into the model
library). When a visible ghost's displayed record advances, the renderer
([ghost_playback.cpp](../plugin/gfxwrap/ghost_playback.cpp) `hear`) hands that
ghost's recorded requests for every record passed since its last drawn frame to
the sound sink, ghosts in request order, each record's requests in issue
order. A frame the renderer did not draw still sounds on the next one, up to
`GP_SOUND_CATCHUP` (90) records; a longer jump only re-establishes position.
A per-ghost high-water record keeps a nudge or seek backwards from replaying
anything: re-passed records stay silent. Every new timeline re-bases it: a new
run (a new, non-restored origin) sounds its first record; a state load, or the
worker re-binding the same ghost to a restored origin (how a reset re-arms
it), re-bases at the restored record and sounds what the ghost passes from
there. A command that only confirms the renderer's own restore re-bases
nothing. Until 2026-09-22 the worker's re-bind kept the first attempt's
high-water, so every later attempt of the same ghost stayed silent. Hidden ghosts (star end,
wrong scene, after end, refusals) sound nothing and replay nothing on return;
before its start a ghost is silent and sounds its first record when it begins.

[ghost_audio.cpp](../plugin/gfxwrap/ghost_audio.cpp) owns the rest. The render
thread only writes a bounded 256-entry single-producer queue (full drops and
counts) and plain atomics, so it never waits or allocates (the playback witness
counts zero render allocations). One audio thread owns the engine and a
shared-mode waveOut device (event driven, no window or focus). It synthesizes
one tick (two blocks of 528/544 stereo frames at 32 kHz) whenever the device
holds 1088 frames (34 ms) or fewer. What the device holds is measured from its
play position (`waveOutGetPosition`), not from finished headers: a header
counts in full until it finishes, and the first version's 544-frame threshold
sat below its one-tick (1088-frame) headers, so it saw "empty" only after the
device had already run dry, wrote every tick into silence and chopped the
audio about every 50 ms (the crackle heard live 2026-09-22; a tap on the live
path measured 139 of 140 writes into an empty device, 0 of 206 after). The
audio thread runs at `THREAD_PRIORITY_HIGHEST` and counts any tick written
after the device ran dry as an underrun.
`volume` (GP7/GIPC6, `NativeChannel` key `volume`, float 0..1, absent 0.5)
scales the engine output exactly; 0 writes zeros. Disabled or withdrawn
playback, including the owner-suspended pause, stops every ghost sound and
discards queued requests at the next tick.

Each ghost is its own source to the engine (`sa_place`/`sa_play`, 16
sources). Every drawn frame the renderer places each visible ghost, before
its cues: its displayed root (`pose.position`) under the live camera's
look-at, the same camera space the game hands the engine for Mario's own
sounds (`cameraToObject`: +x right, -z ahead). The engine then does what it
does for Mario: pans by that position and fades with camera distance. In the
pinned US engine (`get_sound_volume`), Mario's banks fall in amplitude from 1
at the camera to about 0.11 at 10000 units and never below 0.1 (0.9 x
intensity squared + 0.1); this copy uses one reach (level 0, 20000) where the
game uses each level's own. On its own the engine therefore never silences a
distant ghost, which is what the renderer's gain adds. On top of that the renderer multiplies each ghost's gain by
`min(1, GP_SOUND_NEAR / d)` -- the inverse-square law for intensity, so -6 dB per doubling -- d being the world distance between that
ghost's root and live Mario (gain 1 while live Mario's pose is unknown).
`GP_SOUND_NEAR` is a tuning constant, 400 units (2.5 Mario heights). The gain
scales amplitude, so it halves the level (-6 dB) for every 1.41x distance
past 400 units. Final loudness is `volume` x engine camera falloff x that
gain. Placement also steers sounds already playing, since the engine
rereads the position and volume every tick. SM64 gives each sound bank one
channel (`sMaxChannelsForSoundBank`, all 1). Mario's sounds carry
`SOUND_NO_PRIORITY_LOSS`, so distance does not rank them, and a newer sound of
equal priority in the same bank takes the channel: two ghosts, or a far ghost
and a near one, can still cut each other off, as Mario's own sounds do.

The IPC worker starts the engine from its verified ROM bytes and stops it
(joining the thread) when the ROM closes. Before the engine parses anything,
the CTL, TBL and SEQ regions must match the SHA-256 of SM64 US audio data (the
same in stock and Usamune US) and the bank-set table one of the two known
versions; `sa_open` also bounds every seq header, owns its ROM copy and frees
every parse on close. Any other ROM, or no audio device, leaves ghosts drawing
silently.

The [audio witness](../tests/test_ghost_audio.py) drives the real queue,
thread and engine into a captured buffer: each cue sounds within the next
tick and nothing before it, 0.5 is exactly half of every sample (energy ratio
0.250001), 0 is all zeros, switching off silences within one tick, altered
audio data never reaches the parser, and a full queue drops. Its waveOut
smoke plays silence only when a device exists; for three seconds it must feed
the device in real time (31599 frames/s measured) with 0 underruns. Placed
(same schedule): 2000 units right of the camera the right channel carries
21x the left's energy (mirrored on the left); gain 0.25 leaves 0.060 of the
energy; 5000 units ahead, the engine's own falloff leaves 0.133; and ghost 1
on the right, with ghost 0 placed left and silent, is sample-identical to
ghost 0 on the right. The playback witness checks each visible ghost is
placed once a frame, before its cues, at its root from the live camera, with
gain 1 within 400 units, 0.25 at 800 and 1 with no live pose. With the
deeper fill (2026-09-22, this machine) request-to-device latency measured at
most 64.8 ms (host-driven capture 32.3 ms), up from 49.9 ms. That excludes
Windows' own mixer and output delay.

[ghost_presentation.cpp](../plugin/gfxwrap/ghost_presentation.cpp) preserves the
clean drawable, applies the renderer's VI mapping/gamma to the display branch,
and mirrors qualifying OSD into clean recording. SourceV3 publishes an explicit
clean-VI descriptor bound to update occurrence, context/drawable/resource
generations and successful presentation serial. SourceV2's front-buffer contract
is not relabeled. No negotiated clean consumer means no native ghost display.
Clean/failed swaps and context loss clear visible telemetry; a generated mesh
alone never reports that the ghost reached the game window.

## Verification boundaries

**Every frame must carry Mario's position.** A committed clip counts refused
and never-captured frames (`clip.py::pose_gaps`); a nonzero count is logged when
the clip is saved and shown on that ghost's picker row, because the ghost blinks
there. `tools/ghost_extraction_audit.py` audits every committed clip and the live
pose archives (exit 1 on any hole). 2026-09-22: every post-fix clip was complete;
the only pose-less archive records were a star-exit level load, outside attempts.

Focused hosts exercise the actual capture reader, clock, origin ledger, playback
loader, compositor and presentation modules. See
[capture](../tests/test_ghost_capture.py), [clock](../tests/test_native_ghost_clock.py),
[origin](../tests/test_native_ghost_origin.py), [portable parity](../tests/test_ghost_clip_native.py),
[playback](../tests/test_ghost_playback.py), [IPC](../tests/test_ghost_ipc.py),
[presentation](../tests/test_ghost_presentation.py) and
[connected pipeline](../tests/test_ghost_pipeline.py). The portable parity host
calls the real native loader with model calls forbidden; it proves acceptance
parity, not rendered geometry.

The [screen-buffer witness](../tests/test_ghost_screen_buffer.py) executes the
staged screen-size presentation, target lookup and VI adapter against hidden
OpenGL buffers. It covers blit and texture-copy fallback, gamma, padded source
dimensions, zero/half/full opacity and clean recording. It requires both
`SM64_TEST_GHOST_PRESENTATION=1` and `SM64_GHOST_WITNESS_SOURCE` pointing to a
fresh retained renderer build. Its window/configuration shells and shader copy
implementation are synthetic; it does not run Project64.

The connected hidden-WGL proof used GC3/GS2/GP2/GCLIP2 and the actual native
capture, clock, queue, model, compositor and presentation code with synthetic
RDRAM and terrain: RTX 5090, 640x480, 20 warmups and 180 samples. CPU plus GPU
completion plus hidden swap measured p50 0.4827 ms, p95 0.7580 ms and max
1.4785 ms. It also checked 100%/50% opacity,
unchanged pixels outside Mario, clean recording and wall occlusion. That receipt
binds its actual source hashes and executable. Paused IGT with advancing global
timer leaves the selected pose unchanged. This excludes live GLideN64 terrain/VI
work, IPC and video encoding; it is not the full live gameplay budget.

The same witness on GP4 (2026-09-22, RTX 5090, 640x480, 180 samples) also draws
16 ghosts in one batch from 16 prepared clips: 36096 vertices, every ghost
visible, no pixel outside their projected triangles changed. CPU plus GPU
completion plus hidden swap measured p50 2.19 ms, p95 3.38 ms and max 3.81 ms
(one ghost: p50 0.47 ms, p95 1.27 ms, max 2.05 ms). The same exclusions apply.

On GC5/GCLIP4/GP5 (2026-09-22, same machine and sizes) the witness also
executes Mario's shadow lists through the real source, records the shadow and
draws it: 551 shadow-only pixels darkened evenly, zero pixels changed outside
the ghost and its shadow, zero changes outside the shadow when the switch
toggles, 50% opacity is one midpoint blend, and a nearer wall hides the decal.
The fixture's texture is a synthetic quarter-circle ramp, not ROM texels.
With every ghost's shadow drawn, one ghost measured p50 0.49 ms, p95 0.84 ms,
max 2.07 ms and sixteen p50 2.13 ms, p95 3.28 ms, max 3.93 ms.

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
