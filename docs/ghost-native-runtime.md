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
| Capture | GC6: layout 188 (`behaviour_base` at 184), sample 1252; executed root matrix, warp fields, derived clock, recorded shadow (292 at 308), sound events (36 at 600), the pose refusal byte (`gc_pose.refusal`, record offset 153, formerly reserved), particles (580 at 636) and the [held object](#held-objects) (36 at 1216) | [ghost_capture.h](../plugin/gfxwrap/ghost_capture.h) |
| Source clock | GCC1: config 104, output 64; raw, progress and elapsed validity | [ghost_clock.h](../plugin/gfxwrap/ghost_clock.h) |
| Renderer capture | GS5, still exported as `SM64GhostSourceV3` (the version argument must be 5); bounded 128-slot queue | [ghost_stream.h](../plugin/gfxwrap/ghost_stream.h) |
| Playback | GP7: up to 16 immutable preloaded clips per command, 32 slots, revisioned owner origins, per-ghost hue, overlap fade, the `shadow_enabled` and `occlusion_fade` switches, sound `volume` and the owner-`suspended` pause | [ghost_playback.h](../plugin/gfxwrap/ghost_playback.h) |
| Ghost IPC | GIPC7: status 256, request 18956 (layout 188; 16 clip entries of 1096, then `shadow_enabled`, `occlusion_fade` and `volume`), ring offset 20480, 512 records of 1268 | [ghost_ipc.h](../plugin/gfxwrap/ghost_ipc.h) |
| Portable clip | GCLIP5: header 160, record 1268, maximum 100000 records; header flag `GCLIP_DANCE_TAIL`. GCLIP4 (record 652) still loads, read as having no particles | [ghost_clip.h](../plugin/gfxwrap/ghost_clip.h) |
| Clean recording | SourceV3 explicitly identifies the clean VI surface | [link_source_api.h](../plugin/gfxwrap/link_source_api.h) |

Sizes are bytes. Native ghost versions are checked together. GCLIP4 (GC5,
652-byte records, the saved and PB ghosts recorded before particles) is the one
format read by a compatibility step: the worker, after checking the saved
file's SHA-256, and `clip.py` both rewrite it as GCLIP5 with every sample byte
kept and no particle block ([ghost_clip_upgrade.h](../plugin/gfxwrap/ghost_clip_upgrade.h),
`upgrade_record`), so it binds to its attempt and plays exactly as before
(tested on a real saved clip, `tests/fixtures/attempt_2549_*`); a GCLIP4 clip
longer than 100000 records is refused. Older native clips have no
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
(`geo_process_held_object`). The object's own graph node is off while held,
so its animation advances only there, in the same callback that writes
`marioBodyState->heldObjLastPosition` (the HOLP). Capture
([ghost_capture.cpp](../plugin/gfxwrap/ghost_capture.cpp) `capture_held`)
records `gc_held` only when all of these hold: Mario's pose is valid (a root
refused or carried away later clears it), `heldObj` is an aligned pool slot
that is active with its own node off, its `animTimer` equals this frame's
`area_update_counter`, and its frame lies inside the loaded animation. The
block holds the segmented behaviour (`behaviour - behaviour_base + 0x13000000`,
the identity; `bhvBobomb` is 0x13003174), the animation id, frame and y
translation, its `oAnimState` (switch frames such as the Bob-omb's blink), the HOLP (its world root) and its gfx scale. Its orientation
needs no field: the hand copies Mario's own root matrix (his throwMatrix),
so the held root is Mario's recorded world root rotation, rows scaled by
`scale`, at the HOLP. A held object without an animation cannot prove it was
drawn this frame and is not recorded. The legacy TRS reader still refuses a
holding Mario and records none.

The ghost draws the held objects the model knows (`held_models` in
[ghost_pose.cpp](../plugin/gfxwrap/ghost_pose.cpp): the Bob-omb, the small
penguin, King Bob-omb and Chuckya), as part of Mario's mesh: the recorded
animation frame, `oAnimState` and `gGlobalTimer` (the penguin's blink), at the
HOLP, its billboards turned to the live camera. Holding Bowser's tail holds
his tail anchor, which has no model and no animation, so nothing is
recorded or drawn in the hand; Bowser himself is a world object, and the
ghost shows Mario swinging with empty hands. An object the model does not
know, or refuses (no textures, an animation it lacks, no room in the mesh),
is left out and Mario still draws.

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

Playback ([ghost_pose.cpp](../plugin/gfxwrap/ghost_pose.cpp)) multiplies the
user's opacity by the recorded alpha (0x80 for the vanish cap, about half).
The game's dithered transparency becomes an even blend. The model draws the
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
adds them as atlas cells 11 and 12 (`SG_ATLAS_CELLS` 13).

The lookat is live state, not an assumption. In the pinned LINK d0d10105,
`RSP_SetDefaultState` runs only from `RSP_Init` (ROM open): lookat enabled,
x=(0,1,0), y=(1,0,0). Only an F3D `G_MOVEMEM` `G_MV_LOOKATX/Y` (`gSPLookAt`)
changes it afterwards, and nothing resets it per frame, per task or on a
ucode load. So whatever last sent a lookat (the Goddard title head can) stays
in force, and a savestate does not restore it. [ghost_link.cpp](../plugin/gfxwrap/ghost_link.cpp)
reads `gSP.lookat` and `lookatEnable` with each world triangle submission.
The world pass keeps the value from the world's first triangles
(`WorldPass::lookat`), and ghost_pose maps the ghost's metal with it. The one
remaining approximation is the dither pattern.

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
- **Short gaps, bridged in playback**: 40 single frames of long-jump land
  during the BitS-stairs BLJ (status 4). The frame after each is a fresh land
  animation at frame 0, so the renderer did not process Mario on the refused
  frame. A new recording's reason will say whether he was inactive (now a hidden
  pose) or in another area index (`area`). Whatever the cause, playback now
  holds the previous pose over any run of at most `GP_BRIDGE_FRAMES` (2)
  refused records. The run must have a recorded pose on both sides, in the same
  level and area, with no missing occurrence
  ([ghost_playback.cpp](../plugin/gfxwrap/ghost_playback.cpp) `bridged`). A
  67 ms hold is below what reads as a pause; a blink is not. Longer runs, scene
  changes and the clip's first or last rows still hide the ghost. The clip
  and the audit keep the rows refused: this is display, not extraction.
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
to that coordinate, after any restart alignment.

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
fixed per ghost and attempt origin. A restore resolved into that attempt
(`GO_RECOVERED`) keeps it, because its coordinate continues the attempt's
timeline. A later live scene change never re-aligns. Touch, dance tail and
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
row; gaps, after-end, wrong scene, invalid timing and pose refusals hide playback,
except that a refusal run of at most two records inside one scene holds the pose before it.
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
full set plus as many retiring copies) share a 192 MiB clip/index budget, and
the worker's retained files obey the same bound. It is sized so sixteen
2m40 ghosts can be replaced by sixteen more in flight (32 x 4800 x
(sizeof(gclip_frame) + 4) bytes; his longest raced segment is 1m51): GC6
records are about twice GC5's, and the earlier 128 MiB would have cut that to
under two minutes a ghost. It stays well under the 2 GiB his Project64 has:
wermi's build v7 is 32-bit and not large-address-aware (checked 2026-09-23),
and the emulator, GLideN64, its texture caches and the worker's retained files
share that space. The budget is a ceiling, not a reservation. Every clip
allocation fails soft instead of throwing inside Project64. The worker's file
read and GCLIP4 upgrade refuse with `GIPC_FILE` (6). The retained-file total,
the asset list and playback's copy (`new(std::nothrow)`, `GP_BUDGET`) refuse
the set with `GIPC_BUDGET` (12), kept apart from a bad clip (`GIPC_CLIP`, 8)
so the ghost card can say "These ghosts are too long to show together;
untick one." (one ghost: "Making room for this ghost..."). One maximum-size clip
(100000 records, about 55 minutes at 30 Hz) fits. The pose archive keeps its
own window (288 MiB of rows, two hours at 30 Hz), independent of that clip
cap, so a late save still reaches an early attempt. All resident slots count, so a replacement
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
combiner commands, not a UV heuristic. [Cap effects](#cap-effects-vanish-and-metal)
describes the vanish and metal material routes.

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
The layer runs only while playback is drawing (`ghost_playback::drawing`), so
a retired, withdrawn or disabled playback repeats no draw and blits no depth.
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
share its GLSL, so a shadow, a bubble or a dust puff fades exactly like its body. Per fragment, on top of the
user's opacity and the overlap fade:

- **In front of live Mario, over him.** Playback projects his body box (feet at
  his position, 100 wide and 160 tall per unit gfx scale) with the ghosts' own
  live view-projection: a screen ellipse plus his body centre's clip w. A ghost
  fragment keeps `1 - 0.85 x cover x front` of its opacity. `cover` is 1 inside
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
stay exact. The [particle witness](../tests/test_ghost_particles.py) draws a
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
