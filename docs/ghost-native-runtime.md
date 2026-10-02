# Native ghost runtime contracts

The Mario view evaluates recorded graphics poses with the libsm64 C model and
draws them inside the pinned GLideN64 renderer under the live camera. The whole
picture view retains the video overlay. Both use the existing ghost preference,
opacity, timing nudge and tint controls; switching to Mario closes the overlay
window. See [session.py](../src/sm64_events/ghost/session.py) and
[native_session.py](../src/sm64_events/ghost/native_session.py).

The **Ghosts** checkboxes choose the local PB, other saved runs and imported
ghosts for the current star or segment. The choice is stored per target and
strategy without changing the input-comparison template. When PB is ticked but
no PB or other ghost is selected, the active input template supplies the ghost.
Local recordings are qualified separately for Mario poses and whole-picture
video; imported collections supply native poses. A removed choice stays
unavailable until the user chooses another recording. Selection updates include
the target so a delayed request cannot choose ghosts for another practice target. See
[selection.py](../src/sm64_events/ghost/selection.py).

The source connects capture, archive publication, native playback, compositing
and clean video recording. This describes that integration, not a successful
release build or live acceptance result. New native recordings are required;
physics resimulation and migration of older video-only recordings are outside
the format. The full game, recording and presentation path has a 33.33 ms budget
at 30 Hz. A geometry benchmark alone cannot establish that budget.

## Names, inputs and collections

The native controls remember **Names** (on initially), **Inputs** (off initially)
and **Stick / Values**. Each ghost carries its own bounded credit: `YOUR PB` for
the selected local PB, `YOUR RUN` for another local recording, and the exporting
player's name for an imported ghost. Credit is presentation metadata; changing
it never changes clip identity or restarts playback. Input and label metadata
travel inside the pose through depth sorting and inherit its visibility.

The input display reads `gc_pad` from the ghost's selected **recorded occurrence**,
even when a missing body pose is bridged from an earlier occurrence. Raw signed
stick X/Y and held buttons are captured beside that pose, from the existing
verified player-one controller root. Pressed buttons only provide a sanity check.
Unavailable pad evidence is distinct from a known neutral stick, and never hides
a valid body pose. A local clip recorded before pad capture plays as its
[playback copy](../src/sm64_events/ghost/padfill.py): `ReplayService.native_with_inputs`
fills each missing pad from the attempt's input track at the record's own
`global_timer` (calibrated 2026-10-02: 45,659 of 45,659 agree at that frame),
keeps the clip id and gives the copy its own SHA-256 under `save_root/native/inputs/`.
The committed clip is never changed. A frame the track lacks, and a whole clip whose
track Mario's action does not line up, keep the unavailable-input indicator. Synthetic
death, extrapolated dance and post-recording holds have no invented inputs.

The font is the original thin white SM64 dialogue font. Names and inputs are
sized in world units (`GP_ANNOTATION_*` in `ghost_tuning.h`), so a far ghost's
name shrinks with its body, up to a largest size beside the camera. The
renderer draws the world upside down (`flip_y`), so layout offsets flip with
their anchor. The input display sits above the name, the stick base (or the
Values column) through the last held button centred on it, and reproduces Usamune's
own, read from its practice code (RAM `0x80408848`) with the big-icon setting:
the mirrored stick base at its alpha `0x91`, the 16x16 ring moved by the raw
stick clamped to ±70 and divided by 5, then the held buttons' 16x16 icons in
its table order, drawn just past the ring's farthest reach (`GP_ANNOTATION_BUTTON_GAP`)
rather than at Usamune's own x=80. **Values** prints Y's row above X's: a HUD `U`/`D` or `L`/`R`
letter (none at zero) and the absolute value. Usamune draws nothing for L or
the D-pad, and neither does the ghost. Unknown input shows a faded base with
`?`, never a neutral stick. Annotation rendering belongs to the private ghost
display surface before HUD compositing; clean video stays unchanged.

**Ghost Library** opens beside the native controls. It shows recent buffer-covered
and saved local runs plus imported recordings, with creator, target, strategy,
time, fastest/recent ordering and search. Practice and unfinished runs are hidden
initially. Selection spans pages: **Select filtered** selects the entire filtered
list. Export produces one `.sm64ghosts` collection; choosing that file imports
all its ghosts. The collection bound is 4096 entries per file, independent of
the renderer's 16-ghost race limit. **Race selected here** preserves selected
local recordings before applying the target's race choice.

`ghost/portable.py` validates every entry and clip before publication: bounded
ZIP/manifest/record sizes, exact file hashes, supported native format, recorded
clock and star course. The 100-coin star retains its legitimate segment-clock
recording. Creator credit, timing and strategy travel with the original bytes;
re-export retains foreign credit, and repeated imports are idempotent. Credit
identifies the name supplied by the exporter, not an authenticated account.

Imported ghosts live beneath `save_root/shared-ghosts`, separate from attempts,
PBs and input templates. They cannot become personal bests or golds. Star keys
are canonical course/star IDs. Segment signatures expand parent and trigger
references and match the actual definition and clock, independent of foreign
numeric IDs; unmatched or ambiguous segments remain in the library with a
readable explanation. A bulk read binds one current definition snapshot.

## Versioned boundaries

| Boundary | Current contract | Source |
|---|---|---|
| Capture | GC8: layout 192 (`player1_controller` at 188), sample 1268; executed root matrix, warp fields, derived clock, recorded shadow (292 at 308), sound events (36 at 600), the pose refusal byte (`gc_pose.refusal`, record offset 153, formerly reserved), particles (580 at 636), the [held object](#held-objects) (44 at 1216, its released angle at 36) and optional exact pad (8 at 1260, validity bit 512) | [ghost_capture.h](../plugin/gfxwrap/ghost_capture.h) |
| Source clock | GCC1: config 104, output 64; raw, progress and elapsed validity | [ghost_clock.h](../plugin/gfxwrap/ghost_clock.h) |
| Renderer capture | GS7, still exported as `SM64GhostSourceV3` (the version argument must be 7); bounded 128-slot queue | [ghost_stream.h](../plugin/gfxwrap/ghost_stream.h) |
| Playback | GP8: up to 16 immutable preloaded clips per command, 32 slots, revisioned owner origins, per-ghost hue and 64-byte terminated UTF-8 label, overlap fade, shadow/occlusion/sound switches, volume, owner suspension and global names/inputs/mode controls | [ghost_playback.h](../plugin/gfxwrap/ghost_playback.h) |
| Ghost IPC | GIPC9: status 256, request 19996 (layout 192; 16 clip entries of 1160), ring offset 20480, 512 records of 1284; names/inputs/mode appended after sound controls | [ghost_ipc.h](../plugin/gfxwrap/ghost_ipc.h) |
| Portable clip | GCLIP7: header 160, record 1284, maximum 100000 records; header flag `GCLIP_DANCE_TAIL`. GCLIP6 (1276), GCLIP5 (1268) and GCLIP4 (652) still load with unavailable pad; GCLIP5 also has nothing released, GCLIP4 also has no particles | [ghost_clip.h](../plugin/gfxwrap/ghost_clip.h) |
| Clean recording | SourceV3 explicitly identifies the clean VI surface | [link_source_api.h](../plugin/gfxwrap/link_source_api.h) |

Sizes are bytes. Native ghost versions are checked together. GCLIP6 (GC7,
1276-byte records, before exact pad), GCLIP5 (GC6,
1268-byte records, before released objects) and GCLIP4 (GC5, 652-byte records,
before particles) are read by a compatibility step: the worker, after checking
the saved file's SHA-256, and `clip.py` both rewrite them as GCLIP7 with every
sample byte kept and the blocks they never had zero
([ghost_clip_upgrade.h](../plugin/gfxwrap/ghost_clip_upgrade.h),
`upgrade_record`), so they bind to their attempt and play exactly as before
(GCLIP4 tested on a real saved clip, `tests/fixtures/attempt_2549_*`); an
older clip longer than 100000 records is refused. An older record claiming the
new pad-valid bit is refused, rather than fabricating neutral input from a
zero-extended block. The pose archive's GC7, GC6 and GC5 rows read the same way. Older native clips have no
compatibility reader. Their files remain on disk; `OlderPoseFormat` provides a
typed reason (`older_pose_format`) to record a new attempt. An older committed
file keeps pruning protection and is never overwritten by a later export; a
playable saved copy of the same attempt still wins. GCLIP3/GC4 files and
archive rows (no shadow or sound) are older formats in the same sense.
SourceV2 video capture retains its original contract.
GS advertises pose and world-pass capture, not `GS_CAP_RENDER`: its table is a
capture interface. Rendering uses the separate playback/draw path and requires
negotiated clean presentation. A capability bit or prepared model is not proof
of a displayed ghost.

### Adding a captured field

The C headers own every size, offset, version and limit. Python states each one
once in [native_layout.py](../src/sm64_events/ghost/native_layout.py), and
`clip.py`, `native_protocol.py` and the byte fixtures import it.
`tests/test_ghost_layout.py` compiles `tests/native/ghost_layout_host.cpp`,
which prints each of those names from `sizeof`/`offsetof` and the macros. It
fails on a wrong value, on a Python name the host does not print, and on a
decoder `struct` format that no longer spans the bytes between two named
offsets. Tuning numbers and ghost limits live in
[ghost_tuning.h](../plugin/gfxwrap/ghost_tuning.h) (the player-first fade keeps
its own in `ghost_occlusion.h`). A new block of evidence touches:

1. `ghost_capture.h`: the block at the END of `gc_sample`, its `GC_VALID_` bit
   (and `GC_VALID_ALL`), a `GC_VERSION` bump; filled in the capture source,
   with the static_asserts in `ghost_capture.cpp` moved on.
2. `ghost_clip.h`: the `GCLIP_VERSION`/magic bump and record-size assert. Only
   when older saved ghosts must keep playing, an upgrade step like
   `ghost_clip_upgrade.h` and `clip.py::upgrade_record`.
3. `native_layout.py` and `ghost_layout_host.cpp`: the new names, in both.
4. `clip.py`: decode and validate the block; a refused row keeps it zero.
5. The consumer (playback, pose or compositor) that uses it.
6. `tests/ghost_native_fixture.py` and the block's own tests.
7. The table above.

Earlier offsets do not move, so tests that name them keep passing. Any edit
under `plugin/gfxwrap` changes the wrapper and renderer build ids, so the DLLs
are rebuilt once when the work is integrated (see the native plugin rule).

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

### Held objects

What Mario holds (a Bob-omb, a baby penguin, a heavy object, Bowser's tail,
or the SSL hands-free-holding object) is drawn from his right hand
(`geo_process_held_object`), whose callback writes
`marioBodyState->heldObjLastPosition` (the HOLP). The object's own graph node
never draws while held: most holdables switch it off
(`cur_obj_disable_rendering`), while the Bob-omb and Mips keep it active but
set `GRAPH_RENDER_INVISIBLE` (`bobomb_held_loop`, `bhv_mips_held`), so
`geo_process_object` still advances their animation before `obj_is_in_view`
culls them. Either way a current `animTimer` is this frame's animation. Capture
([ghost_capture.cpp](../plugin/gfxwrap/ghost_capture.cpp) `capture_held`)
records `gc_held` only when all of these hold: Mario's pose is valid (a root
refused or carried away later clears it), `heldObj` is an aligned pool slot
that is alive with its own node off or invisible, its `animTimer` equals this frame's
`area_update_counter`, and its frame lies inside the loaded animation. The
block holds the segmented behaviour (`behaviour - behaviour_base + 0x13000000`,
the identity; `bhvBobomb` is 0x13003174), the animation id, frame and y
translation, its `oAnimState` (switch frames such as the Bob-omb's blink), the HOLP (its world root) and its gfx scale. Its orientation
needs no field: the hand copies Mario's own root matrix (his throwMatrix),
so the held root is Mario's recorded world root rotation, rows scaled by
`scale`, at the HOLP. A held object without an animation (the cork and crazy
boxes) has no timer to prove it: it is recorded with animation id -1 at frame
0, and `ghost_root_finish` keeps it only beside a root Mario's own executed
lists proved this frame, since the hand draws it whenever his body draws. A
carried or TRS root (his body not drawn) drops it. Every held object needs a
model (`sharedChild`). The legacy TRS reader still refuses a holding Mario and
records none.

The ghost draws the held objects the model knows (`held_models` in
[ghost_pose.cpp](../plugin/gfxwrap/ghost_pose.cpp): the Bob-omb, the small
penguin, King Bob-omb, Chuckya, the cork and crazy boxes, Mips and Ukiki), as part
of Mario's mesh, their textures clamped and wrapped per render tile
([model README](../renderer/model/README.md)), and lit like Mario but by the
lights their own lists load (`sg_model_get_held_lights`; a vertex names its
light by its diffuse and ambient colours, since King Bob-omb loads one white
diffuse with two ambients). A held colour none of its lights names leaves the
object out, never Mario. The recorded
animation frame, `oAnimState` and `gGlobalTimer` (the penguin's blink), at the
HOLP, its billboards turned to the live camera. Holding Bowser's tail holds
his tail anchor, which has no model and no animation, so nothing is
recorded or drawn in the hand; Bowser himself is a world object, and the
ghost shows Mario swinging with empty hands. An object the model does not
know, or refuses (no textures, an animation it lacks, no room in the mesh),
is left out and Mario still draws.

Ukiki's normal and macro behaviours share the canonical thirteen-animation
table; capture resolves the current animation pointer against that table,
including the held animation at index 12. Its geo keeps all four eye/cap
states, including Mario's stolen cap and its common1 texture. The full held
graph requires 3,258 vertices beside wing-cap Mario, so the shared model and
compositor budgets use `SG_MODEL_MAX_VERTICES` (4,096). A refused held or ridden
object clears both `SG_POSE_HELD` and `SG_POSE_HELD_RELEASED` before Mario's
fallback evaluation; an unavailable companion must not hide Mario.

**Ridden companions.** Hoot is captured from `usedObj` while riding the Owl;
a Koopa shell is captured from `riddenObj` during the exact ground, jump and
fall shell actions. Both use the existing optional held block with `released` 1:
their own recorded gfx position, angles and scale, independent of Mario's root
and HOLP. The shell is unanimated (id -1, frame 0). Their canonical geo and
textures draw in the same ghost mesh with the same tint and user opacity.
Existing clips that lack these companion blocks cannot reconstruct them;
record a new attempt for a shell, Owl or previously uncaptured Ukiki.

**Released (thrown or dropped).** Once `heldObj` clears, the reader
(`gc_reader`) follows the slot Mario last held, one game frame at a time,
while the object stays active and keeps the behaviour it had in his hand, for
at most `GC_RELEASED_FRAMES` (300, 10 s). A skipped game frame, a changed
behaviour (the slot reused) or an inactive object (the Bob-omb explodes) ends
the follow for good; grabbing it again returns it to his hand. On each
followed frame whose own node draws it (active, visible, in Mario's area, no
throwMatrix or billboard) the held block carries `released` 1, its gfx
position in `holp`, its gfx angle in `angles`, and the same behaviour,
animation and scale; `geo_process_object` composed TRS(position, angle,
scale), and `held.c` does the same (`SG_POSE_HELD_RELEASED`). A frame its
node does not draw (culled, not yet shown) carries nothing, so the ghost's
object disappears exactly where the game's did. It rides Mario's pose like
the in-hand block. An object without an animation (a box) is kept beside a
carried root too, since its own node, not his hand, drew it. Witnessed by
`tests/native/ghost_root_host.cpp` `released_block` (every rule mutation-proved)
and `tests/native/held_pose_host.cpp` (a released Bob-omb at its own position;
the box turned by its own yaw). Live, in his Behind Chain Chomp's Gate runs, is
unwitnessed.

### Recorded hat state

The post-callback `gc_pose.cap_state` is captured on each occurrence and retained
in the clip, then supplied unchanged to the model. The canonical
`geo_switch_mario_cap_on_off` uses bit0 to select the capped head (0) or capless
head with full hair (1); bit1 controls cap wings independently. Losing a hat,
recovering it and beginning a recording hatless therefore follow the recorded
frame. Live Mario's hat and raw cap flags do not override this visual state.
The usual zero value selects Mario's ordinary capped head.

The capture, clip and pose witnesses cover both transitions and a capless first
frame. The real-ROM model witness checks the capped head's M-logo texture is
absent from the capless head, keeps both meshes deterministic across transitions
and covers capless vanish/metal bodies. This field already existed in stored
poses; compatible older clips retain their recorded hat state without rerecording.

### Cap effects (vanish and metal)

`geo_switch_mario_cap_effect` picks Mario's body from `modelState >> 8`: 0
ordinary, 1 vanish, 2 metal, 3 metal vanish. The root walk already follows
the selected case. `geo_mirror_mario_set_alpha` builds the alpha list from
`modelState`. With `0x100 | alpha` below 255 (the vanish cap's
`MODEL_STATE_NOISE_ALPHA` 0x180, or a warp fade), the list is
`gDPSetAlphaCompare(G_AC_DITHER)`, `gDPSetEnvColor(255,255,255,alpha)` and the
end (24 bytes, transparent layer); otherwise it is the opaque envcolor and the
end (16 bytes). Capture expects exactly the list the recorded `model_state`
implies, alpha included ([ghost_root.cpp](../plugin/gfxwrap/ghost_root.cpp)).
A mismatch is not Mario's alpha. No capture field changed: `model_state`
already carries both effects.

Playback ([ghost_pose.cpp](../plugin/gfxwrap/ghost_pose.cpp)) applies recorded
environment alpha only to Mario's body triangles. Below 255 it uses alpha-noise
comparison in both self-depth and colour passes: the same pixel is rejected
in both, and surviving pixels retain their environment alpha as the live
transparent layer does. The integer mask follows the captured global timer and
the framebuffer's N64 scale, independent of ghost tint and user opacity.
Recorded `model_state`, including cap-expiry flicker, controls this effect;
raw cap flags do not. Held/ridden objects, shadows and particles retain the
shared ghost opacity. Old clips already include `model_state` and need no
recapture for the vanish effect. The pattern is deterministic ghost noise;
the live RDP random sequence was not recorded. The model draws the
metal body from the ROM's own metal lists. Those lists switch on
`G_TEXTURE_GEN`, which the model now reports per triangle
(`SG_MATERIAL_TEXGEN`; [patches.py](../renderer/model/patches.py)). Playback
replaces those vertices' coordinates exactly as the live renderer computes
them (GLideN64 `gSPProcessVertex`). With its lookat enabled, x and y are the
unit eye-space normal dotted with the lookat vectors; otherwise they are the
normal's own x and y. Then `(x + 1) * 512` and `(y + 1) * 512`, scaled by
`gSPTexture(0x0F80, 0x07C0)`, give 0..62 x 0..31 texels of the ROM's 64x32
metal texture, under the live view. The metal body is fully environment
mapped (2256 of 2256 vertices on the US ROM). Metal with the wing cap draws
`mario_metal_cap_wings` from the ROM's metal wing textures (segment 4
0x0400A090 and 0x0400B090). Upstream libsm64 leaves them out, so the model
adds them as atlas cells 11 and 12, the last two of Mario's thirteen atlas cells.

The lookat is live state, not an assumption. In the pinned LINK d0d10105,
`RSP_SetDefaultState` runs only from `RSP_Init` (ROM open): lookat enabled,
x=(0,1,0), y=(1,0,0). Only an F3D `G_MOVEMEM` `G_MV_LOOKATX/Y` (`gSPLookAt`)
changes it afterwards, and nothing resets it per frame, per task or on a
ucode load. So whatever last sent a lookat (the Goddard title head can) stays
in force, and a savestate does not restore it. [ghost_link.cpp](../plugin/gfxwrap/ghost_link.cpp)
reads `gSP.lookat` and `lookatEnable` with each world triangle submission.
The world pass keeps the value from the world's first triangles
(`WorldPass::lookat`), and ghost_pose maps the ghost's metal with it. The one
remaining approximation is the unrecorded live dither pattern.

### Pose refusals and the gaps they closed

Every refused pose names its one failing check in `gc_pose.refusal` (the
`gc_refusal` enum, append-only; Python `clip.REFUSALS`, `Frame.refusal_name`).
A refused record keeps only that byte in its pose block; both loaders
([ghost_playback.cpp](../plugin/gfxwrap/ghost_playback.cpp) `valid_clip`,
[clip.py](../src/sm64_events/ghost/clip.py)) accept it and refuse unknown
values. `tools/ghost_extraction_audit.py` prints it per hole. Records written
before 2026-09-23 have zero there (`unrecorded`).

The first live archive with holes (2026-09-23, about 76k records) showed four
causes, each fixed at its source:

- **Holding or riding** (status 10: Bob-omb, Li'l Penguin, a heavy object,
  Bowser's tail, the SSL hands-free-holding glitch that keeps `heldObj` set
  through ordinary jumps and slides for 300+ frames, a shell). Capture refused
  any nonzero `heldObj`/`riddenObj`. `geo_process_held_object` draws the held
  object from Mario's hand node, after his alpha/body pair, so the executed
  root proof is unaffected; the executed path now accepts these frames. The
  ghost draws Mario alone (hand pose and `grab_position` included), not the
  held object or shell. The legacy TRS reader keeps the refusal (`held`).
- **Culled by the camera** (status 9: red-coin/8-coin star spawns freezing
  Mario mid side flip or rollout, Bowser's final dialog). `geo_process_object`
  still updates Mario's animation and `cameraToObject`, then `obj_is_in_view`
  skips every list, so nothing proved the root. When a completed occurrence
  shows no Mario alpha list at all (`not_drawn`), [ghost_root.cpp](../plugin/gfxwrap/ghost_root.cpp)
  publishes the pose if either (1) his root inputs (gfx position, angles,
  scale, action, body action) are bit-identical to the last executed root in
  the same epoch and scene: the record carries that matrix and, as
  `sample.camera`, the camera it executed under, plus its shadow when the
  animation is identical too; or (2) neither action is floor-aligned and the
  object's throw-matrix flags are clear: `align_with_floor` is the only writer
  of Mario's `throwMatrix`, so the game composed TRS(gfx position, angle,
  scale) and the record says `GC_ROOT_TRS`. A floor-aligned Mario that moved
  while culled stays refused (`not_drawn_floor`: the tilt is unknown). A
  Mario whose alpha list executed without his body (`body_mismatch`), a
  duplicate, an interrupted list or a shared model is never carried. The star-spawn freeze starts after Mario's own
  update that frame, so its first culled frame has already moved off the
  last drawn one; route (2) covers it. Culled frames carry no shadow unless
  route (1) applies.
- **Invisible Mario** (status 4, `act_disappeared` and similar). The
  renderer skips an inactive Mario, so his animation stays at
  `set_mario_animation`'s startFrame-1 (-1) with a stale timer. A hidden pose
  no longer checks animation fields; playback draws nothing for it without
  evaluating the model ([ghost_pose.cpp](../plugin/gfxwrap/ghost_pose.cpp)).
- **A jump that lands in the frame it starts** (`animation_range`, status 4,
  closed 2026-09-23): every BLJ press on the endless stairs (castle area 2,
  `ACT_LONG_JUMP_LAND`; 20 in his 24"20 BitS Entry PB) and every jump off a
  slide slope in the pyramid (`ACT_JUMP_LAND` between butt slides; 8 in his
  21"00 pyramid PB). `set_mario_action_airborne` writes animID -1 for
  `ACT_JUMP`, `ACT_LONG_JUMP`, `ACT_BACKFLIP` and `ACT_STEEP_JUMP` so the
  jump's animation restarts. `common_air_action_step` sets no animation on a
  landing step, so a jump that lands at once leaves -1 while the renderer
  still draws `curAnim`, Mario's DMA buffer, at an advanced frame. The next
  frame's `set_mario_animation` sees -1 and restarts the land animation at
  frame 0: that restart was the evidence. Capture now names the loaded
  animation from `gMarioState->animList` (`currentAddr` matched against the
  DMA table, only when `bufTarget` is `curAnim` and one entry matches).
  Earlier clips keep the refused rows; each is a single frame that playback
  bridges, so the ghost never blinked there.
- **Short gaps, bridged in playback**: playback holds the previous pose over
  any run of at most `GP_BRIDGE_FRAMES` (2) refused records. The run must have a recorded pose on both sides, in the same
  level and area, with no missing occurrence
  ([ghost_playback.cpp](../plugin/gfxwrap/ghost_playback.cpp) `bridged`). A
  67 ms hold is below what reads as a pause; a blink is not. Longer runs, scene
  changes and the clip's first or last rows still hide the ghost. The clip
  and the audit keep the rows refused: this is display, not extraction. A
  bridged row's own sounds still play, placed at the pose it shows (its own
  pose is empty, the world origin).
- **Painting exit, kept hidden**: the first 16 frames of `ACT_EXIT_AIRBORNE`
  (status 3) come before the action's first `set_mario_animation`, so the new
  level's Mario object has no animation (`animation`). The game draws him
  unanimated at his spawn point, which is in the painting's plane. On
  2026-09-23 he sat at (-2304, 0, -4520) for all 16 frames, then launched at
  +32 units a frame out of it. By decomp reading (not yet footage), those
  frames are also the arrival transition (`init_mario_after_warp`'s default
  star iris, 0x10 frames). A ghost drawn there would sit inside the painting
  under the same terrain depth, so it stays hidden. The model has no
  unanimated pose to draw it with anyway. Footage of a painting exit would
  confirm or overturn this.

Poles and grabs have no capture rule of their own: their gfx angles carry
the pole's pitch/roll into the executed (or TRS) root. In the 2026-09-23 Elevator
Tour clip every pole frame (grab, hold, climb) has a pose. The ghost was missing
on the poles because of alignment: that clip was recorded from the level entrance,
the later attempts started inside the volcano, and the star coordinate follows
elapsed time, so the ghost was still outside (another area, hidden) while he
climbed. [Restart alignment](#restart-alignment) fixes this.

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

A segment's ghost starts where the segment does, which inside the castle is
often mid-area: his MIPS Clip starts when he opens the HMC door, and its clock
and its recorded ghost start at the counter zero that door causes about 61
frames later (session 25, 2026-09-24). The engine publishes no marker for an
arm that began anywhere else (`SegmentEngine._ghost_starts_here`): a practice
reset re-arms the segment where Mario respawns, which is its start only when
the segment starts at a level or area entry or at the reset itself, and a
"move" arm waits for its zero, or for its 90-frame window to pass without one
(then the arm frame is the clock, as the attempt's timing does). Before this
the ghost walked out from the reset, then from the door frame, and snapped back
61 frames at the zero. Replaying his journal, archive and a MIPS Clip clip
through the projector, worker and playback: 341 frames with a ghost where none
belonged and 6 misplaced before; 1 and 0 after, over four runs; the ghost
appears 3 frames after the zero (the tracker's poll and the worker's list),
already at the right point, and hides while its own Mario is inside the DDD
warp.

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
A star adds its origin raw IGT; a segment starts at zero. The timing nudge
applies to that coordinate, after the ghost's alignment: a star's restart
alignment (below), or a segment's zero alignment.

### Zero alignment

A segment ghost recorded from Usamune's counter zero (its origin row reads at
most `GCLIP_ZERO_RAW`, the near-zero) and raced from a live origin at a zero
too (`GO_ZERO_CLOCK`) is shifted by live origin raw less recorded origin raw,
once per attempt (`zero_shift`). Both runs of a scripted start then meet on the
counter, whichever frame the tracker noticed each zero. Any other segment
keeps the elapsed coordinate.

Why (2026-09-29, his Bowser 1 drop-in: "It SEEMS like they literally fall
faster than real mario"): the tracker sees a level load's counter zero one or
two frames late (the capture's counter restarts on the level-change frame, the
anchor lands after it) and a reset's on its own frame. His PB was recorded
from a pipe arrival, so its first row read 3 while his resets start at 1. On
the elapsed coordinate the scripted fall ran two frames ahead, a Mario-height
below him: the pose archive's 8 reset drop-ins that night each matched the PB
exactly at a shift of two frames, and his one arrival at zero. 7 of the 30
saved segment clips began at counter 2 or 3 rather than 1. A star was
never affected: its coordinate has always been the counter.

### Restart alignment

Usamune restarts raw IGT at some area entrances, for example LLL's volcano.
A star ghost recorded from the level entrance and raced from a reset to that
restart would otherwise spend its walk outside while live Mario is already
inside. `prepare_clip` keeps each clip's last `GP_RESTARTS` (8) raw-IGT restarts:
rows whose raw IGT is below the row before, with their level and area. When a
live star origin first applies, the renderer
([ghost_playback.cpp](../plugin/gfxwrap/ghost_playback.cpp) `restart_shift`)
compares live Mario's scene with the clip's first row. If they differ and
the clip restarted in live Mario's level and area, it shifts that ghost's
coordinate so the latest such restart's raw IGT meets the live raw IGT.
Otherwise, including segments and fixed seek, nothing changes. The shift is
fixed per recording (clip id, not its slot) and attempt. An attempt begins at
an unrecovered origin, in the scene live Mario is in when it first applies. A
restore resolved into it (`GO_RECOVERED`) continues its timeline, so the same
recording keeps its shift, and a recording newly shown during it (a selection
change, a toggle, a capture restart re-adopting clips into other slots) aligns
to the attempt's scene. A later live scene change never re-aligns. Touch, dance tail and
after-end checks use the shifted coordinate.

Real replay (2026-09-23, the actual playback selection over the saved 2814
Elevator Tour clip and the archived live frames): attempts 2843, 2847, 2854
and 2857, started inside the volcano, went from 227, 298, 279 and 297
wrong-scene frames to 0, with the ghost from their first frame. 2847 and 2857
end 30 and 12 frames before his own grab, at the ghost's recorded touch (its
366 against his 378 IGT on 2857). 2832, started at the entrance like the
ghost, is unchanged. Paused clocks do not advance from wall time or Python poll
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

A load names a point on the run by a witnessed checkpoint: the same raw IGT,
level and area, action, animation, animation frame and angles, and a drawn
root within `GO_RESTORE_PLACE` (32 units) per axis. Without one, raw IGT
names it before any banked transition: a star's coordinate is its raw IGT,
and a segment whose origin read Usamune's own zero (raw IGT at most the
layout's near-zero, `GO_ZERO_CLOCK`) reads raw IGT less its origin's. That
places a state saved in an earlier attempt too, also after a reset, which is
how he practises (round 10). A segment that began elsewhere cannot know such
a state's coordinate and stays hidden rather than wrong. A move-clock
segment's origin is Mario's first movement, so a state from another attempt
lands up to that attempt's own start delay (at most the near-zero second)
away. The relation between raw IGT and the coordinate settles on the first
checkpoint on the origin's timeline, which for the renderer can come after
its bind.

A load that re-arms a segment (the tracker ends the attempt there and times a
new one from it) keeps the ghost on the run it continues: the new arm carries
the run's marker (`SegmentEngine._continued_ghost`), and only a practice reset
starts a new ghost clock. Before this, each load re-armed a new marker at the
load frame; one he logged that the renderer never saw (IGT 297 to 296, seven
frames after the previous restore) missed the worker's two-frame hand-off and
restarted the ghost at 0. A 100-coin engine no longer re-arms on a load at
all under attempt rules 2. The root is read back
through the camera's fixed-point matrices, so a loaded state never draws
exactly where its saved frame did. His WF 100 Coins loads (2026-09-23,
session 23: one state saved at IGT 296, loaded seven times) drew Mario 0.06
to 7.4 units away; the previous exact compare matched none of them, so each
load hid the ghost until the tracker's new arm restarted it from the
segment's beginning. Replaying his archived frames (1129 of the run), his
PB clip and the markers this projector publishes over his own journal
through the real worker and playback: before, 718 frames wrong and no load
frame right; now 1128 right, 0 wrong, and the one hidden frame is the run's
first, before the tracker's arm arrives.

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

A capture restart keeps the run (his report, 2026-09-23: spamming state loads
in Blast Away the Wall, "eventually the ghost broke, and it never showed up
again, until I reset"). His journal and pose archive show the cause: mid-run
the recorder's GPU capture failed ("pending age limit") and reconnected, which
opened a new capture channel (new owner token) in the same trainer process.
The worker revoked, reconfigured capture (a new configuration and epoch) and
cleared both its recovery and its origin ledger; the tracker's arm still named
the attempt's first frame, captured before the restart, so it could never
resolve again until the next attempt re-armed. The loads were incidental.
Now any revoke except a protocol refusal parks the recovery (only a ROM close
also demands an exact global timer afterwards), and the re-live on the same
cartridge hands it back to the same process, tracker session and layout,
still suspended as parked (restoring used to re-demand an exact global timer,
which Usamune loads never meet because they keep the timer running). A star
resumes by its own IGT at once; a segment at its next witnessed load. The renderer keeps its
own checkpoints across the revoke too (`detach`), and a worker restore of the
same run (a recovered origin with no command skipped in between, since worker
revisions never repeat) keeps them on rebind, so later loads still draw on
their own frame. The recovery witness restarts capture mid-run and then makes
600 rapid loads (star and segment; backward, forward, repeated; Usamune style
holding IGT a few frames and PJ64 style rewinding the timer): 2 hidden frames
after the restart for the star, 1 for the segment's first load (the worker's
first command arrives a list later), 0 wrong frames. Before, the star ghost
never returned in that run.

### Several ghosts

One command displays a set of up to 16 saved recordings (the PB plus others)
against the same live origin and the same opacity, nudge, tint switch, overlap
fade and star-end policy. Hue is per recording. Each ghost selects its own row
from the shared coordinate: before-start clamps, and after-end, gaps, wrong
scene, refused poses (except a bridged short run) and its own recorded touch
hide only that ghost. A live
star grab or star generation change ends the run: every ghost still racing
is defeated (below) and the rest stay hidden. Overlap fade compares
live Mario with each ghost separately
([ghost_proximity.h](../plugin/gfxwrap/ghost_proximity.h)): touching bodies
fade the ghost smoothly down to the shared floor, 15% of its opacity
(`GHOST_FLOOR` in ghost_occlusion.h), never to nothing. Playback hands the
factor to the shaders with the player-first region rather than folding it into
the pose's opacity, so the two fades share that one floor (see the
player-first section). On a pole, a ledge or
Blast Away the Wall's plank both Marios snap to one point, 1 to 4 units
apart; before the floor the ghost vanished for the whole hold (his report,
2026-09-23). The [proximity witness](../tests/test_ghost_proximity_native.py)
checks the floor at distance 0 and 1, the half-overlap midpoint, full
opacity from touching, and that the curve never drops below the floor. The renderer evaluates all ghosts into
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
closing dance, not its first touch: it draws through any earlier star and the
recorded closing grab and dance, then holds the last recorded pose and fades
it out over `GP_STAR_FADE_FRAMES` (a tuning constant, 15 frames = 0.5 s), one
equal opacity step a frame (body and shadow), and hides exactly that many
frames after the last record, with or without `star_ends`. Its sound fades with it (the
placement gain carries the fade) and is set silent as it hides. Unflagged
clips, every recording saved before the tail existed, still end at the touch.
A ghost that dances first dances and fades on its own timeline while he is
still playing, and his own dance later does not cut that short.

**A dance the recording cut short plays on** (his ask, 2026-09-28: "we
literally know exactly how long it should last / how it works"). He often
pauses, resets or loads a state a second or two into the dance: on
2026-09-28, 19 of his 20 recorded dances stopped before the game's own end.
Past a tail's last record, playback keeps that last recorded dance pose (the
game holds Mario's place and facing through the dance) and plays the rest by
animation, the way `act_star_dance` / `act_star_dance_water` do in the
[n64decomp source](https://github.com/n64decomp/sm64/blob/master/src/game/mario_actions_cutscene.c):
the dance starts on the first row showing the dance animation (actionTimer 1;
after a midair grab that is the row after the landing), the animation frame
is actionTimer - 1 until the animation's last frame (62 on the ground, 82 in
water), the hand shows the peace sign from actionTimer 40 (62 in water), "here
we go" sounds at 42, and the dance ends at actionTimer 111, where the exit
warp's 32-frame delay after actionTimer 80 runs out (a no-exit dance ends at
80, where the game stops time for its save dialog). Then it fades as above.
Its eyes blink on the area clock; particles, anything held and recorded sounds
stay with the recording. The constants and their source lines are in
`ghost_tuning.h` (`GP_DANCE_*`). The rule matched all 1,373 dance rows of his
2026-09-28 archive (animation, frame, hand and place, 1,058 ground and 315
water rows, no mismatch) and the one dance he let finish ended at actionTimer
111. Facing settles on the second dance frame (the camera's yaw), so a
recording cut on the dance's first frame would hold a slightly turned pose; his
shortest recorded dance ran 13 frames. A recording cut before the landing (a
reset during the fall) keeps its fall and fades: where Mario lands is
physics, not animation. The [playback witness](../tests/native/ghost_playback_host.cpp)
drives a cut ground dance, a water dance, a midair grab's landing into the
dance and a no-exit dance, and restoring any one rule turns it red.

**Defeat** (his asks, 2026-09-23: "because you won, you get to trigger the
death animation for all of the remaining ghosts that were slower than you";
2026-09-28: "the ghost death / real mario comparison should be based on the
XCAM DANCE start"). The request's star actions are the dances
(`STAR_DANCE_ACTIONS`), not the midair fall before one, so his grab in the
air does not end the race: his dance does. When his dance completes the
attempt, on the star latch (his dance, or a new star generation while he is
in one) or, where no latch applies (the 100-coin star's exit star), on the
tracker's suspension arriving while he dances, playback decides from what
each ghost drew the frame before and where it stood at his x-cam: the live
elapsed clock his latest dance began at (`dance_entry`), so a suspension that
arrives a poll late is still judged at his dance, not where it arrives:

- a ghost whose own dance began at or before his (a dance tail's final run
  of dance actions, walked back from its end; else its first recorded
  dance) keeps its dance and fade, moving on from where it stands now. A
  ghost that grabbed later but danced first wins; a tie is not slower;
- a ghost whose dance began later, including one that grabbed first but is
  still falling, dies where it stood, playing the game's whole death to its
  last frame, where he lies passed out, and only then fades over
  `GP_DEFEAT_FADE_FRAMES`, sound included (his ask, 2026-09-28: "play through
  the full animation, and then fade out when mario's passed out fully
  dead"). On land that is `act_standing_death`: `MARIO_ANIM_DYING_FALL_OVER`
  (0x32, 96 frames; he hits the ground at frame 77 and lies still at 95),
  dead eyes, `SOUND_MARIO_DYING` once; visible 111 frames (3.7 s) in all.
  Swimming (the submerged action group) it drowns as `act_drowning` does:
  `MARIO_ANIM_DROWNING_PART1` (0xA5, 80 frames, half-closed eyes), then
  `PART2` (0xA6, 70 frames, dead eyes), `SOUND_MARIO_DROWNING` once; 165
  frames (5.5 s). The game has no midair death (Mario dies after he lands),
  so a ghost defeated in the air plays the land death where it stood. The
  lengths are the ROM's (`sg_model_get_animation_frames`); each animation is
  non-looping and its pose last changes on its last frame. Before this the
  ghost faded from frame 80 (the game's death-warp frame), while the fall
  was still settling. Torso and head go straight, and what it held and its
  particles go; the root, facing, scale and shadow are its recorded row's.
  A reset or state load back into the race still ends it at once, since the
  ghosts race again from the start;
- a ghost not drawn then stays hidden.

A Bowser 1/2 key ends its segment exactly like a star (his ruling,
2026-09-28: "The key should work the same way as a normal star"): its grab
is the same exit dance, and his key reaches playback as the tracker's
suspension, judged at his x-cam. The Bowser 3 grand star's own cutscene
(`ACT_JUMBO_STAR_CUTSCENE`, sent with the dances as a race-ending action)
counts as its dance. Targets without either keep what they had: a segment
(MIPS Clip, a castle route leg) ends at its close with no defeat, and a
suspension with no dance (a reset) only hides. The defeat runs on the game's
own world clock, `gAreaUpdateCounter` in the live sample, through tuning and
the suspension. That clock stands still while the game is paused or in
Usamune's menu, as `gGlobalTimer` does not, so a death, a cut dance and a
winner's dance through his defeat freeze there and resume from the frame they
stood on (his report, 2026-09-29: "if we pause during it, it should pause
that animation, and continue if we continue"; before, the death ran out on
`gGlobalTimer` while he paused). A ghost's own dance already froze, since
its coordinate is the live elapsed clock. The defeat ends early only when
every ghost has fallen, faded or finished, not when a frame (a pause menu)
draws none. It happens once per run: a reset or a state load that restores
the run ends it and draws the ghosts at the new state, and an incompatible scene change or
the global timer running back ends it. Tuning lives in
`ghost_tuning.h`. The playback witness drives him first (both fall, cry once,
frame by frame, held, faded, hidden), the ghost first (it dances on through
his dance and the suspension), a tie, his midair grab while a ground ghost
dances first, a ghost still falling from an earlier grab, the 100-coin exit
star through the suspension (judged at his x-cam) and a reset's suspension;
breaking the winner rule, the x-cam judge, the animation, the eyes, the cry or
the trigger each turns it red. The fade values for a dance stay (1, 14/15 ...
1/15, then hidden at 15 frames), with an older clip beside the dancer ending
at its touch.

An automatic PB update can replace the selected recording during death, or
even before the renderer's first completing-dance list. IPC retains the
identical FOLLOW command during completion suspension and asset loading,
publishes completion with the old descriptors before releasing that set, and the renderer
holds the raced clips, order and hues while it judges or plays that terminal
sequence. The worker cannot reclaim those frame arrays until the terminal
sequence ends; preparation retries can then fit a replacement that would exceed
the budget alongside the old recording. A suspension without
a completing action still hides the ordinary run. A new origin, a genuine
restore, owner/ROM/model/mode departure or explicit revocation clears the held
sequence; the next race binds the latest PB set on its first list. A paused
terminal keeps its game-clock phase and root even through origin repair.

Camera and overlap fading retain `GHOST_FLOOR` of a dying ghost's shared
opacity, so proximity cannot reduce the death to zero visibility. Its own
final fade still runs after the animation, and freezes with the game clock.
World depth, camera clipping and source/scene validity remain authoritative.
The CPU playback/IPC witnesses cover both PB delivery orders, both paused-clock
patterns, storage reclamation, tint/order retention and restore/mode negatives.
The compositor witness checks the terminal floor and body-only vanish mask on
both depth routes; rendered/live acceptance is separate from these CPU checks.

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
row; gaps, after-end, wrong scene, invalid timing and pose refusals hide playback,
except that a refusal run of at most two records inside one scene holds the pose before it.
Per-row scenes allow coherent cross-area clips.

DDD (level 23), areas 1 and 2, has one bounded exception in
[ghost_scene.h](../plugin/gfxwrap/ghost_scene.h). The
[pinned level script](https://github.com/n64decomp/sm64/blob/06ec56df7f951f88da05f468cdcacecba496145a/levels/ddd/script.c)
defines the seamless portal displacement: area 1 to 2 subtracts 8192 from x;
area 2 to 1 adds 8192. Both areas render copies of the swimming tunnel:
[area 1 geometry](https://github.com/n64decomp/sm64/blob/06ec56df7f951f88da05f468cdcacecba496145a/levels/ddd/areas/1/2/model.inc.c)
and [area 2 geometry](https://github.com/n64decomp/sm64/blob/06ec56df7f951f88da05f468cdcacecba496145a/levels/ddd/areas/2/2/model.inc.c).
Their common volume is a 13-vertex x/y tunnel profile, extruded over
z -511..512 in area 1 coordinates; the helper records its source-derived
vertices. It includes the upper passage, deep shafts, beveled bend and right
duct. Its bounding box also contains empty air and an area-1-only left duct,
so bounds alone do not admit a root. A valid recorded root inside the profile
may draw in either area, mapped by the portal translation; a root outside it,
an unrelated area or another level still refuses. Live Mario's pose is not
needed to establish the recorded root's location. Terrain depth, frustum and
camera fades continue to apply.

Selection, planning, normal poses and held death poses use that same admission
rule. Playback keeps the exact selected timeline, animation and sound cues;
it neither waits for live Mario nor chooses a different area's frame. Rebased
copies translate Mario's world position, recorded camera position/focus and
the held object's HOLP, only for fields whose validity bits are set; absent
fields keep their format's zero invariant. Recorded view-space root/shadow matrices and particle
positions stay unchanged: removing the rebased recorded camera moves each
world root once, including scaled-modelview Mario. Proximity and sound use the
same displayed root. Known particles on a short bridged pose refusal use the
admitted shown pose's recorded scene and their own current recorded camera;
the bridge never crosses a recorded scene boundary. Saved clips stay immutable.

When playback is ready but its last presentation is hidden for `GP_WRONG_SCENE`,
the ghost card says "Ghost is in another area." Playback stays running and its
controls stay available. A visible ghost suppresses this set-level message;
unknown decisions and stale acknowledgements do not supply an area explanation.
In the measured DDD pair on 2026-09-29, ghost 8313 entered area 2 at coordinate
553 and live attempt 8322 at 560: seven mismatched frames (233 ms), with valid
saved poses throughout 16–22 seconds. Those roots lie in the shared connector;
the strict area-equality guard hid a correctly timed ghost there. Native tests
cover both lead and lag crossings, both root kinds, held objects, shadow and
particle placement, overlap/sound positions, connector boundaries and other
scene refusals. Compilation and native-host proof do not establish the final
live Project64 picture; the current renderer still needs a live tunnel witness.

## Recording, preservation and ownership

[NativeCapture](../src/sm64_events/ghost/capture.py) owns a separate leased channel
named `Local\sm64_trainer_ghost_v9_<PID>`. Process birth, producer/ROM generations,
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

Losing the active origin withdraws drawing while retaining prepared assets.
Switching playback off (nothing listed to draw) also releases its clips; a
later switch-on reads the files again. Withdrawal still retires released clips at the next
render boundary, including released terminal poses, so worker collection can
free their storage and the same clip can load again. An unreleased terminal pose
keeps its position and death phase across temporary withdrawal. The
[recovery witness](../tests/test_ghost_recovery.py) checks both ownership cases,
off/on reuse and repeated restores without renderer-thread allocation or freeing.
The worker holds no second copy of
anything playback holds: a clip file's bytes are dropped once playback has
prepared its copy (id, SHA-256 and header stay), and the cartridge's once the
model is built from it (its digest stays). A retired model or a lost renderer
context rebuilds both from fresh reads, the clips checked against the same
SHA-256. The sound engine keeps only its audio span, 2.25 MB of the 8 MB
cartridge ([`SA_AUDIO_BYTES`](#ghost-sound-playback)). Lease/ROM shutdown revokes playback and retires assets;
worker collection and release establish storage cleanup. Thirty-two slots (a
full set plus as many retiring copies) share a 192 MiB clip/index budget, and
the files the worker is still reading obey the same bound. It is sized so sixteen
2m40 ghosts can be replaced by sixteen more in flight (32 x 4800 x
(sizeof(gclip_frame) + 4) bytes; his longest raced segment is 1m51): GC6
records are about twice GC5's, and the earlier 128 MiB would have cut that to
under two minutes a ghost. It stays well under the 2 GiB his Project64 has:
wermi's build v7 is 32-bit and not large-address-aware (checked 2026-09-23),
and the emulator, GLideN64, its texture caches and the worker's retained files
share that space. The budget is a ceiling, not a reservation. Every clip
allocation fails soft instead of throwing inside Project64. The worker's file
read and older-clip upgrade refuse with `GIPC_FILE` (6). The retained-file total,
the asset list and playback's copy (`new(std::nothrow)`, `GP_BUDGET`) refuse
the set with `GIPC_BUDGET` (12), kept apart from a bad clip (`GIPC_CLIP`, 8)
so the ghost card can say "These ghosts are too long to show together;
untick one." (one ghost: "Making room for this ghost..."). One maximum-size clip
(100000 records, about 55 minutes at 30 Hz) fits. The pose archive keeps its
own window (two hours, whatever the video keeps; a ceiling of 288 MiB of raw
records), independent of that clip cap, so a late save still reaches an early
attempt. It writes in WAL with one commit per 30 rows or second rather than
per batch, and stores rows zlib-compressed under integer keys (about 570 B a
row, 118 MiB for the two hours); the measurements and the schema-version rule
(an older file is recreated, never migrated) live in
[archive.py](../src/sm64_events/ghost/archive.py). A ghost kept for a template
mark and then saved with its video shares one file between both names. All resident slots count, so a replacement
may defer until the previous owner's storage is retired and collected. Nothing
displaces a pending clip; the worker releases what it no longer lists.
The render hooks run on the emulation thread for every z-buffered draw while
capture is configured, ghost or not, so they make no driver state queries:
the draw framebuffer, viewport and depth range come from the renderer's own
dispatched calls (`replay_gl::Raster`; `glViewport` is aliased at the call,
like `glReadBuffer`). A query is only the fallback before the dispatch has
seen a value, and each is counted. The translucent layer's hook leaves before
any framebuffer lookup when no ghost will draw the list. The last list's
hook cost rides `gs_stats` (`gs_hook_cost`) to the status page (`hook_*`);
the trainer logs it once a minute ([what the numbers mean](#what-the-hook-numbers-mean)).

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
An attempt that ends by entering a level closes on the level change, inside
the load where the capture has no clock: the last 3 frames of each of his MIPS
Clip runs (ending at DDD entry, 2026-09-24), which refused every save's ghost.
Such a clip ends on the last clocked occurrence at most
`CLOSING_TRANSITION_MAX` (30) frames before the close, and its manifest's
`closing_transition` says how many frames earlier; `check_manifest` binds the
clip's last frame to `closed_frame` less that.

A star run closes at x-cam, the dance's first frame of the grab that completes
it (the exit star for the 100-coin star), so export appends the dance tail for
any attempt that is not a segment, and for a segment closed by a Bowser key
or the grand star (`closed_by` `key_grabbed`): the rows after the closing frame
while Mario stays in a star grab/dance action or the grand star's cutscene
(`TAIL_ACTIONS`), the game clock advances, and timing continuity, star
generation and scene hold, at most `DANCE_TAIL_MAX_FRAMES` (300, 10 s) past
the close. His Bowser 1 PB (attempt 80000006404, 2026-09-28) closed on idle
frame 49272 with the key's exit dance from 49273, the same frame-early close
as a ground star, and its segment kept no tail, so the ghost vanished at the
key; so do his Bowser 2 saves. The grand star's tail keeps its fall to the
arena's centre, the landing, the raise and the wing-cap take-off, and ends
where the scripted flight into the credits begins (the recorded animation
becomes `MARIO_ANIM_WING_CAP_FLY`); playback holds the last pose and fades. It
is not finished by animation when cut: its take-off moves Mario by the
animation's own translation. Whether Usamune's counter keeps running through
that cutscene is unmeasured (no grand-star grab survives in a pose archive):
a frozen counter would leave that tail empty. A grab straight into the dance (on the ground, or
swimming) can close one frame before the first recorded dance frame: the
star-grab detector back-computes the x-cam from Mario's action timer, which
the dance counts from 1 on its own first frame, and a snapshot may read a
frame stale. So when the tracker timed the attempt at the x-cam (`timed_at`),
the dance may begin up to `DANCE_ENTRY_FRAMES` (2) rows after the close.
Before this, export required the closing row itself to dance and dropped the
whole dance of every such grab: his SSL "Shining Atop the Pyramid" PB 07"00
(attempt 5386, closed on walking frame 821, dancing from 822), two more SSL
saves, two WF "Blast Away the Wall" saves (grabbed from a ledge) and his DDD
"The Manta Ray's Reward" PB (attempt 5551, swimming). Midair grabs kept their
dance because their close is the landing. Re-exported from
his 2026-09-28 archive with the fix, 5386 keeps a 100-row dance and 5551 a
54-row water dance, the attempt rows byte-identical, and two midair saves are
unchanged. Measured on his 2026-09-22 archive, dances run
about 100 to 125 frames past x-cam; the exit dance ends by freezing the clock
for the exit warp, the no-exit dance by returning to idle. A pause also
freezes the clock and so ends the tail; playback finishes the dance (above). Export often runs
the moment the attempt closes, so it waits for the dance to end, the recording
to stall for a second or close, or the cap's duration plus a second. A reset
or state load ends the tail at once. The manifest's `tail_records` counts the
tail; `check_manifest` binds the attempt's closing frame to the row before it.
The [archive tests](../tests/test_ghost_archive.py) cover each way a dance
ends, the cap, waiting for a dance still being captured, a key segment, the
grand star up to its flight, and no tail for another segment or a close
outside the dance.

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
combiner commands, not a UV heuristic. [Cap effects](#cap-effects-vanish-and-metal)
describes the vanish and metal material routes.

**His face showed the inside of his head** (his Round 13 report, 2026-09-28:
"the wrong side of the face / body is being rendered"). The fragment shader
decoded each vertex's material word as `uint(mode+0.5)`. Every tiled word
(the face, cap, sideburns, mustache and buttons) lies at or above 2^23, where
floats step by 1, so the sum rounded an odd word up: alpha-mix (1) read as a
cut-out decal (2). Where the texture is clear the face was discarded and the
inside of the far side of his head showed through, hair and cap across his
cheek. The word is exact (flat, below 2^24), so the shader now takes
`uint(mode)`. The [compositor witness](../tests/native/ghost_compositor_host.cpp)
draws a tiled alpha-mix over a clear texture and needs its vertex colour on
every pixel; with the old decode it is transparent.

The game never draws the back of a triangle its list leaves `G_CULL_BACK` on:
all of Mario, only his cap's wings two-sided (the model marks each triangle,
`SG_MATERIAL_CULL_BACK`; the game's RSP init sets it for every object). The
pose moves those facing away from the live camera's eye after the drawn ones
(`Pose::culled`), judged in world space: his winding agrees with his vertex
normals on every triangle. From outside this hides nothing the self-depth
pass would show; the [pipeline](../tests/test_ghost_pipeline.py) draws the
culled back faces too and requires an identical picture, then puts the
camera's near plane through his body, where two-sided drawing would show the
inside of his far side. The [pose witness](../tests/native/ghost_pose_host.cpp)
culls a wall seen from behind and keeps a two-sided one; the
[model witness](../tests/native/model_host.cpp) finds every triangle culled but
the wings'.

The camera comes from bounded lookup of the active graph camera matching the
area camera, including persistent position/focus/roll. It is not the final
display-list modelview matrix. Projection and viewport corrections come from
actual world submissions. Mario is lit by his own lights, as the game lights
him: the model reads each light his lists load (`gsSPLight` L0 diffuse and
direction, L1 ambient; six in the US ROM, each ambient half its diffuse, all
from (40,40,40)), and the pose shades every vertex with F3D's formula,
ambient + diffuse * max(0, N.L) clamped per vertex, with the light in EYE
space, so the lit side follows the live camera exactly as live Mario's does
(GLideN64 `gSPLightVertex`). A vertex colour none of his lights names refuses
the pose rather than guessing. Measured against his replay of attempt 2549
(four registered frames): the lit side of cap, overalls and gloves now
reaches 255 like the game's (it stopped at 204 to 245); the red and skin
medians sit within 5 of the game's in all four (they were off by up to 57 and
48) and blue within 18 (was 72).
The floating-point model/view path does not
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

### What a drawn ghost asks the driver

The ghost draws on GLideN64's own thread, which Project64's emulation thread
waits on. Under NVIDIA's threaded optimization every driver query (a glGet,
glIsEnabled or framebuffer check) waits for the driver's own thread to catch
up, so a ghost frame asks as little as it can
([ghost_compositor.h](../plugin/gfxwrap/ghost_compositor.h)):

- **What the renderer already knows** (`Known`), as its dispatch observed
  every call (`link_dispatch`, [replay-link-state](replay-link-state.md)):
  draw and read bindings, viewport and scissor box, program, vertex array,
  depth function and mask, blend factors and the seven capabilities GLideN64
  switches.
- **What the renderer never changes**, recorded by query once at `prepare`:
  stencil, color mask, blend equations, polygon mode and offset, depth range,
  every other capability, the compositor's texture units 28-31 (bound with
  GL 4.4 multi-bind), the copy-write buffer it uploads through, the unpack
  layout and transform feedback. The census behind it, of the pinned
  GLideN64 d0d1010: every capability it uses goes through `CachedEnable`
  (BLEND, CLIP_DISTANCE0, CULL_FACE, DEPTH_CLAMP, DEPTH_TEST,
  POLYGON_OFFSET_FILL, SCISSOR_TEST); it never calls glStencil*,
  glColorMask, glBlendEquation*, glBlendFuncSeparate, glPolygonMode,
  glBindSampler or glDepthRange, binds no copy-write or unpack buffer, and
  sets the polygon offset only in `_initStates`. A renderer that changes any
  of it calls `resync`. A caller that passes no `Known` promises nothing and
  has everything asked, every frame.
- **What was verified about a framebuffer** (complete, 8-bit linear color and
  D24 depth, its read and draw buffers, the N64 depth texture) is reused while
  the glue's name for its attachments holds (`ghost_link::attachments`: color
  texture and depth object).
- So an ordinary ghost frame asks the driver **nothing** (the base code asked
  92). A HUD draw's feedback check reads units 0 and 1 from the dispatch too,
  and the presentation's clean save and sRGB check read its record
  (`ghost_presentation::Known`), so a presented ghost frame asks nothing
  either.

What still asks, and why. The capture gateway reads glGetError, which waits for
the driver thread like any query, only where the answer changes what happens
([ghost_draw.cpp](../plugin/gfxwrap/ghost_draw.cpp)):

- **Before the seal, once.** The batch saves and restores the state the
  dispatch recorded, so the renderer's world pass must not have failed.
- **When the list's ghost GL ends, once** (at list end, or when the ghost is
  cancelled). The batch and every HUD mirror share this scope. A capture read
  handles an error the same whoever raised it: kept for the renderer's next
  read, the nominal state left, the image cancelled before it is ready. So
  an error anywhere in the scope, the renderer's between HUD draws included,
  ends exactly as a read around each HUD draw would have ended it, and a HUD
  mirror, whose own GL is two binds between the ghost display and the target
  the seal verified, reads nothing.
- **Twice per mirrored translucent surface**, before its color pass and after
  its depth pass. The layer drains its own errors without leaving the nominal
  state, so its scope may hold no renderer GL but the mirrored draw; the depth
  pass follows the color pass with nothing between. The seam's scope closes
  the surface however its passes end (`ghost_link::close_layer`).
- Around the presentation's copies at VI.

A list whose plan chose no ghost reads nothing. His LLL list, about 12 water
surfaces and 27 HUD draws: 104 reads before (2, plus 2 per HUD draw, plus 4
per surface), 26 now; with his run finished, 48 before and none now. The
[glue witness](../tests/test_ghost_draw.py) compiles the glue whole against
renderer shells and counts them (`SM64_GHOST_DRAW_BASELINE=<revision>`
prints an older glue's numbers beside), and shows where each error goes: the
renderer's is never drained, the layer's is, and any error in the display
scope cancels the image. GL_KHR_debug output cannot replace the reads: an
error still sets its flag ("the GL will also generate a debug output
message"), a context created without the debug bit may generate no messages
at all, and messages arrive asynchronously unless synchronous output is on,
which the spec warns "may potentially result in drastically reduced driver
performance" ([KHR_debug](https://registry.khronos.org/OpenGL/extensions/KHR/KHR_debug.txt)).

Other queries: without GL 4.4 multi-bind a frame asks the active unit; a record the dispatch lost (after a GL
error, or before the renderer's first glViewport or glScissor) is asked for;
with N64 depth compare on, the HUD check reads the combiner's two uniforms; and
taking a drawn ghost back (a withdrawn or refused image, rare) reads the
default framebuffer's draw selectors, 8 on NVIDIA, beside its own four state
queries when nothing is known. GLideN64's own on-screen text mirror
(`begin_osd`) still asks its safety checks per text draw.

Witnesses: the [compositor host](../tests/native/ghost_compositor_host.cpp)
wraps every dispatch entry and records each Get, Is and Check call by name. A
fully known frame asks nothing (the active unit alone without multi-bind), a
record without the render state asks exactly those 8 values, a known HUD
scope and layer pass nothing, and the compositor's own count equals the
witness's. After every call the state equals the state before, field for
field, the compositor's units and copy-write buffer included. The
[presentation host](../tests/native/ghost_presentation_host.cpp) asks nothing
for a known clean save or replace, restores every touched value after taking a
ghost back (4 queries plus the draw selectors unknown, the selectors alone
known) with scissor, sRGB and a foreign framebuffer set. The
[link witness](../tests/test_link_source_witness.py) drives LINK's own cached
setters and direct calls and compares each observed value with the driver; it
goes red without any one observation. The [pipeline](../tests/test_ghost_pipeline.py)
asks nothing per ghost frame for 1, 4 and 16 ghosts at 640x480 and 1600x1200.

### The first ghost's list

Shader compiles happen before any ghost is asked for. The compositor and its
four programs (body, shadow, particle, annotation) are created on the first
list after the trainer's clean recording attaches, seconds after the game
starts, and `prepare` then takes them (`ghost_compositor::warm`). Before, the first ghost's list
compiled them: on 2026-10-02 a build with a new shader missed the driver's
cache and that list took 35.8 ms (the NVIDIA GLCache file was written in the
same 100 ms). Measured on the RTX 5090 with a hidden context: body 23 ms,
shadow 11, particle 14, annotation 8 cold; under 0.4 ms each once the driver
has cached that source. Storage (textures, renderbuffer, buffers) is about
1 ms either way.

### What the hook numbers mean

Once a minute the trainer logs `native render hooks`
([capture.py](../src/sm64_events/ghost/capture.py) `HookCost`), one line for
the lists that drew a ghost and one for the rest: their difference is the
ghost's cost. Each list counts once, by its serial (the pump polls twice per
list). Per list, from `gs_hook_cost` in
[ghost_stream.h](../plugin/gfxwrap/ghost_stream.h):

| Number | What it covers |
|---|---|
| hooks | Time inside the timed hooks: list begin, the per-draw hooks, the seal, list end. Its definition predates the stages, so older lines compare. It includes the renderer flush. |
| renderer flush | GLideN64's deferred triangles and rectangles, flushed at the seal: the renderer's own work, moved earlier, not the ghost's. |
| gateway open, close | The capture gateway's glGetError before the seal, and once when the list's ghost GL ends (the batch and every HUD mirror; at list end, inside hooks, or at a cancel). Under threaded optimization each waits for everything queued before it: the open for the renderer's world pass, the close for the batch and the HUD. |
| batch | The compositor's begin_batch: validate (every pose and vertex), state (the renderer state it will restore: known values plus the residue), copy (clean color and depth), draw (shadows, uploads, bodies, particles; also the slowest single ghost), restore. |
| finalize | Closing the ghost branch at list end. |
| whole list | ProcessDList, begin to end, the renderer's own work included. |
| HUD mirror | Each HUD draw mirrored into the ghost's display, the duplicate draw included; it reads no error of its own. Not part of hooks. |
| layer passes | Translucent-layer (water) mirrors, two per surface; none on a list whose plan chose no ghost. |
| driver queries | Every query the ghost code made in the list, and the time inside them. |
| gateway error reads | glGetError calls around ghost GL: 2 per drawn list plus 2 per mirrored surface (was 2, plus 2 per HUD draw, plus 4 per surface). |

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
repeated or interrupted list, a root that was not proven) leaves the record
without `GC_VALID_SHADOW`. A vanish-cap alpha list is 24 bytes, not 16, so the
shadow's offsets count from the end of the alpha list the root proof owns.

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
The layer has its own GL error scope (`replay_gl::BeginLayer`/`EndLayer`): its
ordinary errors are drained and never leave the nominal state that replay
capture and the ghost depend on; out of memory or a lost context still does.
The layer runs only on a list whose plan chose a ghost (`ghost_playback::plan`,
from the list's own sample at its start, before the world pass): a finished,
faded, suspended, out-of-area, withdrawn or disabled ghost repeats no draw and
copies no depth. It used to run whenever a request held its clips
(`ghost_playback::drawing`), which a finished ghost still does: live on
2026-09-28, his LLL and SSL lists after his 7-2 run mirrored 20 to 24 layer
passes each, with 40 to 48 error reads, at about 880 us of hooks against
42-90 us. The plan makes the same choice evaluate made at the seal (the
[playback witness](../tests/test_ghost_playback.py) plans before every
evaluation and checks the two agree); only a chosen ghost whose pose then
refuses costs a wasted layer. After the seal the layer is spent and refused.
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

### Recorded particles

Mario's particles are ordinary objects: `bhvBubbleMario` spawns bubbles
(`bhvSmallWaterWave`, `MODEL_BUBBLE`), `bhvWaterSplash` a splash and its
droplets, `bhvIdleWaterWave`/`bhvWaveTrail` water rings, and the dust, burn
smoke and sparkle generators theirs (n64decomp `object_list_processor.c`
`sParticleTypes`). Their motion uses the game's random numbers, so capture
records what the game drew instead of simulating it.
[ghost_particle_capture.cpp](../plugin/gfxwrap/ghost_particle_capture.cpp)
walks the object pool once per list for active, rendered objects whose
`parentObj` chain reaches `gMarioObject` within four hops (a droplet is two).
In the same display-list hook as the root probe, it records each such object's
first pushed display list whose modelview translation equals that object's
fresh `cameraToObject` (0.05 units), with the executed 3x3 in 4.12 fixed point
and the view-space translation. Up to 16 are kept per frame: when more were
drawn (a shallow splash throws 18 droplets), the 16 nearest Mario, nearest
first, with `GC_PARTICLES_OVERFLOW` set; the rest are counted in the source's
`gs_stats.particles_dropped` and the IPC status page's `particles_dropped`.
Another object's bubble, a coin's sparkle or a particle whose spawner
slot was already reused is not recorded. No new RAM address is read.

The model library reads the frames' geometry and textures from the same
decoded ROM segment 4 as Mario's textures
([particles.c](../renderer/model/particles.c)): 45 US display lists (bubble,
dust, burn smoke, small splash, water rings, sparkles, water splash, droplet,
sparkle animation), each followed through a bounded Fast3D subset into one
textured quad and a 512x256 atlas. Any other command or format leaves the
model with no particles; Mario still opens. The 8 lava-flame frames
(`bhvFlameMario`, `MODEL_RED_FLAME`, shaded by their environment colour) come
from common1 (segment 3, MIO0 at ROM 0x201410 in both the US and Usamune US
ROMs); if those lists do not read, only the flame is missing. Playback
([ghost_particle_pose.cpp](../plugin/gfxwrap/ghost_particle_pose.cpp)) removes
the recorded camera from each matrix. A view-aligned (billboard) matrix is
re-aligned to the live camera, as the game billboards every frame; a water
ring keeps its world orientation. Frames the model lacks are skipped. The
compositor ([ghost_particle_gl.h](../plugin/gfxwrap/ghost_particle_gl.h))
draws them right after their ghost's body, against copied terrain and that
body's depth, with the translucent world layer applied: cutout frames (the
game's texture-edge layer) keep texels at alpha 0.5 or more, blend frames
multiply texture and vertex alpha, and both take the ghost's opacity.
Particles never refuse their ghost and have no switch.

common1 also supplies the ground-pound mist and white puffs (`MODEL_MIST`,
0x03000880/0x03000920), sand (0x0302BCD0), leaves (0x0301CDE0) and snow
(`white_particle_dl`, 0x0302C8A0). Mist fades with its object's `oOpacity`,
which reaches the game's combiner (`G_CC_MODULATEIFADEA`) through a list the
game generates each frame (`geo_update_layer_transparency`): a segment-0
list pushed at the object's position just before its model list. Capture
never records that list; it reads its `G_SETENVCOLOR 255,255,255,alpha` and
stores the alpha with the particle (`gc_particle.opacity`, flag
`GC_PARTICLE_OPACITY`), and playback multiplies it into frames the model
marks `fade`.

Three kinds are 3D meshes, also from common1: the stars a ground pound or a
wall bonk throws (`MODEL_CARTOON_STAR`, 0x0302C298..0x0302C318, four light
colours and an unlit one; a successful wall kick throws none). Those stars
set `oAnimState` 4, so the game draws the unlit list 0x0302C318, flat yellow
255,255,0 at every angle; his footage agrees (attempt 2794's ground pound
decodes to 255,253,0), and the pipeline witness pins its render to that
colour. The other kinds are the punch dirt (`MODEL_DIRT_ANIMATION`,
0x0302C378..0x0302C458) and the wind- or snowman-knocked cap
(`MODEL_MARIOS_CAP`, 0x03022F48, 156 vertices; a cap Klepto drops is Klepto's
and is not recorded). The reader follows their `gsSPLight` loads (G_MOVEMEM
0x86 diffuse and direction, 0x88 ambient), per-vertex normals and the
`SHADE`, `SHADEFADEA` and `BLENDRGBFADEA` combiners (the cap's logo mixes
toward the texture by its alpha; untextured triangles sample one white cell).
Placement lights each vertex with F3D's own formula, as GLideN64's
`gSPLightVertex` does: ambient plus diffuse times N.L, negatives dropped,
clamped, with the list's light in the recorded view space and the normal
turned by the executed matrix. That is the lighting the game drew at record
time, including the lit cartoon-star lists' back-facing normals (0,0,-127),
which leave those at their ambient colour. The game's layer decides depth: opaque and
texture-edge frames (alpha ignored, or tested at .5) are drawn first with a
nearest-surface prepass and one colour per pixel, so an opaque mesh never
blends with itself; translucent frames follow without writing depth. A cap
that is fading (opacity below 255) is translucent, as the game switches it to
its transparent layer. Environment bubbles (`envfx`) are not objects and not
Mario's.

The [particle witness](../tests/test_ghost_particles.py)
(`SM64_TEST_GHOST_COMPOSITOR=1`) checks cutout and blend frames, hiding
behind the body and behind terrain, the clean frame and GL state; removing the
draw, the body depth test, the terrain test or the cutout each turns it red.
The model witness checks the 58 textured frames decode to one quad each, the
12 lit meshes carry their lights and normals (642 vertices in all, the cap
mixing and fading) (17063 visible
segment-4 texels, the flame's environment colour) on both the US and the
Usamune US ROM. The [probe witness](../tests/test_ghost_particle_capture.py) keeps the 16
nearest of 21 drawn (5 counted), skips nested calls, another object's bubble and
inactive objects, and publishes nothing for an interrupted list. The connected
pipeline records a bubble, a droplet (two hops) and a
splash frame while ignoring another object's bubble, and draws them from the
real ROM atlas (19740 pixels, 0 outside); ignoring the parent chain or the
position match, dropping the stream hook or the playback placement each turns
it red (2026-09-23).

### Player-first fade

A ghost never blocks live Mario or the camera. [ghost_occlusion.h](../plugin/gfxwrap/ghost_occlusion.h)
owns the rule and every tuning constant; the body, shadow and particle shaders
share its GLSL, so a shadow, a bubble or a dust puff fades exactly like its body. Per fragment,
on top of the user's opacity, a fragment keeps
`near x max(0.15, overlap x block)`: `overlap` is the overlap fade, `block` the
in-front term below and `near` the camera term. ONE floor for both: applied
one after the other, each with its own 15% floor, a ghost holding a pole with
Mario (overlapping him, and in front of him from the camera) fell to about 5%
and still read as vanished (second review, 2026-09-23). Only the camera term
and the star-dance fade in the opacity may take a ghost below the floor.

- **In front of live Mario, over him.** Playback projects his body box (feet at
  his position, 100 wide and 160 tall per unit gfx scale) with the ghosts' own
  live view-projection: a screen ellipse plus his body centre's clip w. A ghost
  fragment's `block` is `1 - 0.85 x cover x front`, so over his core it is
  the same 15% floor as the overlap fade. `cover` is 1 inside
  the ellipse inscribed in his box and falls smoothly to 0 at 1.6 times that.
  `front` rises from 0 at his centre's depth to 1 at 60 units nearer the
  camera. Behind him, or away from him on screen, a ghost is byte-exact.
- **At the camera.** `smoothstep(150, 450, w)`: invisible within 150 units of
  the camera, whole from 450. Clip w is eye distance because SM64's
  guPerspective uses scale 1, and GLideN64's screen scale only touches x and y.

Without a live pose, only the camera term applies. `occlusion_fade` (the
`NativeChannel` key, a bool, absent means on) removes both terms. It lives in
the checked-zero word after `shadow_enabled` in both the IPC and the playback
request, so no size or version moved: 0 draws exactly as before, and an older
DLL refuses 1 with `GIPC_PROTOCOL`. Why per fragment rather than per ghost:
a per-ghost fade (Trackmania's "ghosts close to you turn transparent") fades
the whole body when only an arm crosses Mario, and it cannot tell "in front
of" from "beside". Screen-space cutouts around the player (Unreal's depth and
camera-distance fades) fade only the pixels that block. The box assumes a
standing Mario, so during a dive the edges of his body get a softer fade.

The [occlusion witness](../tests/test_ghost_occlusion.py) (hidden WGL, both
depth routes) checks every pixel against the rule. Over the core, the ghost
keeps 15% of its opacity (208 fully faded pixels, 524 faded in all), the rim
is smooth, pixels outside 1.6x and a ghost behind Mario are byte-exact, and
the camera term hides a ghost at 100 units, halves it at 300 and is exact at
450. The shadow fades over Mario like its body, the renderer's y flip mirrors
the ghost and its region together, and the clean game image and both depths
stay exact. A ghost overlapping Mario at the floor and in front of him keeps
exactly 15% on every pixel, over his core too; the overlap fade alone at half
applies to the whole ghost; and the camera term still hides an overlapping
ghost at 100 units and halves the floor at 300. The [particle witness](../tests/test_ghost_particles.py) draws a
particle over Mario's region the same way: every pixel follows the rule (52
fully faded, 124 faded in all), behind him it is byte-exact, at the camera it
is hidden or halved, and the flip mirrors it with its region. The [playback witness](../tests/test_ghost_playback.py)
checks that the projected box reaches each pose, that there is no region when
Mario is behind the camera or has no pose, and that the switch empties it. The
connected pipeline (2026-09-23, RTX 5090, 640x480, real model) faded 8444 ghost
pixels over a live Mario 300 units beyond the ghost. It changed 0 pixels
outside the ghost and 0 with Mario 300 units nearer. It then timed the fade on
over 180 samples: one ghost p50 0.62 ms, p95 1.11 ms, max 1.35 ms including
swap; sixteen p50 3.05 ms, p95 4.16 ms, max 4.51 ms. Removing the fade from
the body, the shadow, the particles or the camera term, reversing the front
test, ignoring the region, taking the region after the y flip, or dropping the
switch in playback, IPC or the channel each turns a witness red.

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
after the device ran dry as an underrun. After 3 s of silent output
(`idle_close_frames`, counted after `volume`, so volume 0 is silent) it closes
the device and stops ticking: the open stream otherwise woke it about 200
times a second and held an audio session for as long as the ROM ran (native
audit, 2026-09-23). It then sleeps on an event that `cue` sets only while it
is idle. A request that can be heard reopens the device and sounds in the
first tick written, whole; one that cannot (switched off, volume 0) is
discarded without reopening. A fresh device starts empty, so the first tick
after a reopen is not counted as an underrun.
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

Every class of ghost sound follows that one rule. Mario's recordings carry
requests in the action, moving, voice, general, object, object-2 and menu
banks (76 clips, 2026-09-28). Upstream `update_game_sound` plays the menu bank
(Mario's star and cap grab `SOUND_MENU_STAR_SOUND`, falling into a pit,
entering a pipe) and a moving sound at a constant frequency at volume 1,
centred, without `get_sound_volume`. So a far ghost's star chime kept full
level, and only the ghost volume reached it (his report, 2026-09-28). The
staged engine ([patches.py](../renderer/model/patches.py)) gives those the
voice bank's volume and pan from the ghost's own position and keeps their
constant pitch. `SOUND_NO_VOLUME_LOSS` (falling into a pit) still skips the
engine's camera falloff, as in the game, and the ghost's gain still applies.
`SOUND_MENU_ENTER_PIPE`'s own script mixes its pan, so it sounds centred
wherever its ghost is. No ghost sound is music: capture keeps only requests
from Mario's object, so the cap and star music (sequences) never reach a
ghost. The audio witness plays one sound of each class. Gain 0.25 must leave
3 to 10% of its energy, volume 0.5 a quarter, 2000 units right at least 4x more
in the right channel (the pipe aside) and 5000 units ahead under half (the pit
aside). On the unpatched engine the three menu sounds and the
constant-frequency moving sound kept 100% at gain 0.25.

The IPC worker starts the engine from its verified ROM bytes and stops it
(joining the thread) when the ROM closes. Before the engine parses anything,
the CTL, TBL and SEQ regions must match the SHA-256 of SM64 US audio data (the
same in stock and Usamune US) and the bank-set table one of the two known
versions; `sa_open` also bounds every seq header, owns a copy of only the
ROM's audio span (`SA_AUDIO_BYTES`, about 2.3 MB: CTL through the bank-set
table's 0x100 bytes that upstream's init reads) rather than upstream's whole
8 MB ROM, and frees every parse on close. Any other ROM, or no audio device, leaves ghosts drawing
silently.

The [audio witness](../tests/test_ghost_audio.py) drives the real queue,
thread and engine into a captured buffer: each cue sounds within the next
tick and nothing before it, 0.5 is exactly half of every sample (energy ratio
0.250001), 0 is all zeros, switching off silences within one tick, altered
audio data never reaches the parser, and a full queue drops. The running
engine adds 2.5 MB of private memory (under a 4 MB bound). After a sound
and the idle time the device closes and no tick runs; the next request
reopens it and sounds within the first tick (49 frames in) with the same
samples as the first time, and volume 0 closes the device without reopening
it. Its waveOut smoke plays silence only when a device exists (a volume below
one sample step, which still counts as audible): for two seconds it must feed
the device in real time (31501 frames/s measured), close it 3 s after opening
(2.994 s) and reopen it on a request (7.7 ms from request to first tick,
waveOutOpen included), with 0 underruns across both. Placed
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

### Ghost sound follows its picture's fade

His report (2026-09-28): fully overlapping a ghost for the first seconds of most
WF stars "sounds like all of my audio is duplicated", and "If the ghost is more
invisible, the audio should be quieter, too. However, I also don't want to lose
the benefits of the ghost audio (that is, to clearly inform us about timing
differences between our movements and the ghost)." Offered the in-front fade
too, he chose it: "yes, we should do this for the camera blocking case, too."

The `sound_fade` switch (the card's "Fade its sound", beside Volume; default
on) applies
[ghost_sound_fade.h](../plugin/gfxwrap/ghost_sound_fade.h) in playback. It is
the word after `volume` in both the IPC and the playback request, formerly the
checked-zero `reserved3`, so no size or version moved and an older DLL refuses
1 with `GIPC_PROTOCOL`.

- **Quieter.** Each ghost's placed gain is also multiplied by what its picture
  keeps at its body centre (half its height above the root), projected with the
  pose's own view-projection: `point_fade` in
  [ghost_occlusion.h](../plugin/gfxwrap/ghost_occlusion.h), the GLSL rule's CPU
  copy. That is the overlap fade, the in-front term and the camera term, under
  one 15% floor, each only while its own switch is on. Behind the camera plane
  the picture draws nothing, so a point there takes the camera term by its
  distance behind the plane and no in-front term: a ghost passing the camera
  stays faded until 450 units behind it. The gain moves at most
  1 / `GP_SOUND_FADE_FRAMES` (3) a drawn frame. A ghost that appears (a new
  run, a restore, back from hidden) takes its fade as it is, so a run that
  starts on him is quiet from its first sound. The engine ramps each note's
  volume across its own mix as well (`process_envelope`), so no step reaches
  the output.
- **Timing kept.** While the overlap fade is below 1, a request of the displayed
  record that live Mario issued on the same game frame (the live sample's own
  sound list) is not cued: it is his sound twice with no timing difference.
  Mario's two random voice families (`SOUND_MARIO_YAH_WAH_HOO`,
  `SOUND_MARIO_YAHOO_WAHA_YIPPEE`) count as one line each. A request one frame
  earlier or later still plays, and records caught up after an undrawn frame
  are not compared.
  The in-front and camera terms drop no twin: a ghost there is not where he
  is. A ghost trailing him in line with the camera is now as faint to the ear
  as on screen; that trade was offered and he chose it.

Measured 2026-09-28 on his recordings: 16 stars with two or more native
recordings (64 clips), each against every other as ghost and live (250 pairs),
aligned on the star clock as playback aligns them, extra distance 0. For each
ghost sound, the nearest same sound live Mario made:

| Bodies | Ghost sounds | Same frame | Within 1 frame | Within 6 frames |
|---|---|---|---|---|
| Overlapping (fade below 0.5) | 3580 | 56% | 80% | 96% |
| Half overlapping (0.5 to 1) | 3383 | 22% | 55% | 91% |
| Apart (fade 1) | 15859 | 8% | 19% | 49% |

Overlapping means in step (WF alone: 61% on the same frame), so the fade
quiets the doubling where it happens and leaves every sound offset in place at
full level. Dropping same-frame twins removes 56% of the overlapping sounds and
22% of the half-overlapping ones, which still play at 50 to 100% of the gain.
Not measured: the delay between Project64's own audio output and the ghost's
waveOut device, which shifts every ghost sound against his by one amount (up
to about 65 ms on the ghost side, above).

The playback witness walks ghost 0 on live Mario and ghost 1 beside him:
ghost 0 sounds at exactly the floor from a run's first frame; his same-frame
request, and the same jump in another voice line, play once while ghost 1's
twin still plays; one frame early or late plays; walking apart lifts the gain
one step a frame to full; switched off it is full on him and the twin plays;
switched back on it moves one step; with the overlap fade off it is full. A
second walk puts a ghost on the camera's axis 300 units in front of him: it
sounds at the floor from the first frame, climbs a step a frame once he stands
between it and the camera, is full with the in-front switch off or the sound
switch off, drops no twin there, and keeps half 300 units from the camera, as
its picture does. The [sound fade witness](../tests/test_ghost_sound_fade_native.py)
compares `point_fade` with the witnesses' own statement of the rule at 47916
points (every mode, three overlaps, eleven depths); the GPU pixels are
checked against that same statement.

## Verification boundaries

**Every frame must carry Mario's position.** A committed clip counts refused
and never-captured frames (`clip.py::pose_gaps`); a nonzero count is logged when
the clip is saved. The picker row names only the frames where the ghost really
blinks (`clip.py::blink_frames`): a refused run playback bridges is not one.
`hidden_refusals` states the bridge rule in Python and natively, compared by
`tests/test_ghost_clip_native.py`. `tools/ghost_extraction_audit.py` audits every committed clip and the live
pose archives (exit 1 on any hole), naming each hole's pose refusal and
counting frames whose Mario was culled but posed. 2026-09-22: every post-fix
clip was complete; the only pose-less archive records were a star-exit level
load, outside attempts. 2026-09-23: see [pose refusals](#pose-refusals-and-the-gaps-they-closed).

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

2026-09-27 (RTX 5090, 180 samples each, fade and shadows on, other test
processes on the machine), the compositor's own stages per ghost frame at
1600x1200, p50 (p95) in microseconds: one ghost batch 151 (203): validate 63
(93), state 2 (3), copy 38 (49), draw 41 (57), restore 5 (8); four ghosts
batch 389 (497), validate 251 (318), draw 86 (120); sixteen batch 1432 (2092),
validate 1028 (1593), draw 300 (419). Checking every vertex is most of a
multi-ghost batch (about 63 us per ghost). Nine driver queries on every timed
frame. The hidden host runs without threaded optimization, so its queries cost
little: alternating base and changed runs at 640x480 measured one ghost p50
0.34-0.35 against 0.31-0.34 ms and sixteen unchanged within noise (2.70-2.95
ms). The query count is what changes under a threaded driver; the live hook
line measures that. With the renderer's tracked record (same day, same host):
no driver query on any timed frame, state 0 us, one ghost batch 145 (207),
four 372 (459), sixteen 1281 (1335) at 1600x1200.

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
