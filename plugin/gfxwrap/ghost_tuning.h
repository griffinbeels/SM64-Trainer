/* Ghost playback tuning and limits: every number here was judged by feel or
 * sized for his machine, and each lives only here. The player-first fade
 * keeps its own rule and constants in ghost_occlusion.h. Python reads the
 * shared limits (GP_GHOSTS, GP_PROXIMITY_MAX) from ghost/native_layout.py,
 * which tests/test_ghost_layout.py checks against these values. */
#pragma once

/* Ghosts displayed at once: playback slots, one compositor batch, one sound
 * source each and the IPC request's clip entries all hold this many. */
#define GP_GHOSTS 16u
/* Terminated UTF-8 text attached to one immutable clip descriptor. */
#define GP_LABEL_BYTES 64u
/* The box behind a ghost's name (gp_request.name_background). SM64's two
 * dialog boxes: an NPC's black box with white text, a sign's white box with
 * black text (ingame_menu.c render_dialog_box_type). */
enum gp_name_background { GP_NAME_BLACK = 0, GP_NAME_WHITE = 1 };
/* The name and input display over a ghost (ghost_annotation_mesh.h), judged
 * by feel. His ask, 2026-10-02: the nametag was "way too big" and "should be
 * getting smaller the further away ghost mario is". One layout pixel (the
 * dialogue font's) is this many WORLD units, so the perspective divide
 * shrinks a far ghost's name exactly like its body; Mario is 160 tall. */
#define GP_ANNOTATION_WORLD_PER_PIXEL 1.9f
/* A ghost beside the camera never draws larger than this many N64 screen
 * pixels (of 240) per layout pixel. */
#define GP_ANNOTATION_MAX_SCREEN 0.75f
/* The name sits this far above the top of the ghost's drawn body (his ask,
 * 2026-10-02: "directly over mario's head"), world units at gfx scale 1. A
 * crouch, slide or dive lowers it with him; arms raised past his cap top
 * (about 147 above his feet) never lift it past GP_ANNOTATION_TOP_MAX. */
#define GP_ANNOTATION_GAP 8.0f
#define GP_ANNOTATION_TOP_MAX 150.0f
/* With no body to measure, the name's baseline above his feet. */
#define GP_ANNOTATION_HEIGHT (GP_ANNOTATION_TOP_MAX + GP_ANNOTATION_GAP)
/* The corner stack above the game's own input display: the HUD pixels it may
 * climb above that display (its top at y=186; the game's lives, coin and star
 * counters end near y=40), and how many ghosts it shows. His ask, 2026-10-02:
 * "ideally up to 7 total ghosts. That's probably the max cap"; rows that do
 * not fit one column split into two, the first holding the larger half. */
#define GP_HUD_STACK_PIXELS 144.0f
#define GP_HUD_STACK_MOST 7u
/* Usamune's input display, in its own HUD pixels, relative to the name's. */
#define GP_ANNOTATION_INPUT_SCALE 0.55f
/* Held buttons start this many HUD pixels past the stick ring's farthest
 * reach (or the Values column), the same as between two buttons. His ask,
 * 2026-10-02: Usamune's own spacing left "way too much of a gap". */
#define GP_ANNOTATION_BUTTON_GAP 2.0f
/* The largest extra overlap-fade distance a request may carry (world units). */
#define GP_PROXIMITY_MAX 400.0f
/* The ghost's path (ghost_path.h), his ask of 2026-10-03: "iRacing style path
 * markers on the ground, indicating the path that mario takes". The request's
 * ranges (gp_request.path_*; ghost/native_layout.py restates these), then the
 * shape, every number judged by feel in the preview of attempt 9797 over 9773. */
enum gp_path_source { GP_PATH_FASTEST = 0, GP_PATH_AVERAGE = 1 };
#define GP_PATH_AHEAD_MS_MIN 500u
#define GP_PATH_AHEAD_MS_MAX 10000u
#define GP_PATH_WIDTH_MIN 10.0f
#define GP_PATH_WIDTH_MAX 120.0f
#define GP_PATH_SPACING_MS_MIN 33u
#define GP_PATH_SPACING_MS_MAX 500u
/* Just enough to know what is coming (his ask, 2026-10-03: "progressively
 * disclose the path as we're going along it ... we aren't overwhelmed with the
 * entire path all at once"): past path_ahead_ms (the card's 1.5 s) the path
 * fades to nothing over GP_PATH_FADE_MS; GP_PATH_BEHIND_MS of it behind him
 * stays at GP_PATH_BEHIND_ALPHA (Trackmania's Editor Route draws the driven
 * part at 25%), its last GP_PATH_BEHIND_FADE_MS fading out so a point leaving
 * the back never pops. */
#define GP_PATH_FADE_MS 1000u
#define GP_PATH_BEHIND_MS 500u
#define GP_PATH_BEHIND_FADE_MS 250u
#define GP_PATH_BEHIND_ALPHA 0.25f
/* The most records one window holds: the longest look-ahead, its fade and the
 * part behind, at 30 Hz, with room for paused records. */
#define GP_PATH_SAMPLES 400u
/* Live Mario's PROGRESS along the path (his ask: "the first time we go through
 * an area, we show the first path going through that area; then when we come
 * back, we're using the second path"): it only moves forward. Each list it may
 * advance to the first run of records within GP_PATH_CAPTURE of him (height
 * counts half) inside GP_PATH_REACH_FRAMES of the ghost's time ahead of it,
 * taking the nearest of that run; off the route it holds ("the path only
 * advances when we're actually following it"). After GP_PATH_STALL_FRAMES
 * lists without a capture the reach widens to GP_PATH_CATCHUP_FRAMES and then
 * by GP_PATH_CATCHUP_GROWTH frames a list, so a shortcut, or a stretch of his
 * own route, is caught up with when he rejoins the ghost's (on his WF 100-coin
 * pair a 9 s detour never was with a fixed 6 s). A new clip, run, seek or mode
 * starts it again. Measured on his recordings (tests/test_ghost_path.py). */
#define GP_PATH_CAPTURE 150.0f
#define GP_PATH_REACH_FRAMES 30u
#define GP_PATH_STALL_FRAMES 15u
#define GP_PATH_CATCHUP_FRAMES 180u
#define GP_PATH_CATCHUP_GROWTH 4u
/* The drawn window glides to new progress instead of snapping: each list it
 * closes GP_PATH_GLIDE of the gap, at most GP_PATH_GLIDE_MAX frames of the
 * ghost's run and at least one, so a correction slides (a 9 s catch-up over
 * about a second) and ordinary running is never behind. */
#define GP_PATH_GLIDE 0.25f
#define GP_PATH_GLIDE_MAX 8.0f
/* The averaged path averages only where every ghost is this close to the
 * fastest at the same elapsed frame: two strats never average into a wall. */
#define GP_PATH_AVERAGE_GATE 200.0f
/* The ground ribbon sits this far above the floor, along the floor's normal;
 * the dark casing under it is this much wider. */
#define GP_PATH_LIFT 2.0f
#define GP_PATH_CASING_WIDTH 1.35f
#define GP_PATH_CASING_ALPHA 0.6f
/* In the air: a ribbon a third as wide, a dot on the floor under him every
 * GP_PATH_DOT_FRAMES frames at this alpha and size, and a bar across the
 * ribbon where he leaves the ground and where he lands. */
#define GP_PATH_AIR_WIDTH (1.0f / 3.0f)
#define GP_PATH_DOT_FRAMES 3u
#define GP_PATH_DOT_ALPHA 0.5f
#define GP_PATH_DOT_WIDTH 0.4f
#define GP_PATH_TICK_LENGTH 6.0f
/* Parts this near the camera fade out (world units). Walls and objects hide
 * the path like the ghost's body (his ask, 2026-10-03: "I don't want to be
 * able to see the path through walls"). */
#define GP_PATH_NEAR_HIDDEN 60.0f
#define GP_PATH_NEAR_FULL 150.0f
/* A button press (path_buttons): its Usamune icon this far above his root,
 * GP_PATH_BUTTON_SIZE world units tall so it shrinks with distance like the
 * names (his ask, 2026-10-03: "The buttons should scale with distance ...
 * They're WAY too big"), and never taller than GP_PATH_BUTTON_MAX_SCREEN
 * pixels of a 240-line picture near the camera (was a fixed 12). */
#define GP_PATH_BUTTON_HEIGHT 60.0f
#define GP_PATH_BUTTON_SIZE 30.0f
#define GP_PATH_BUTTON_MAX_SCREEN 5.0f
/* Overlap fade (ghost_proximity.h): the ellipsoid proxy for one Mario at gfx
 * scale 1, from bhvMario's radius=37, height=160 (half height 80):
 * https://github.com/n64decomp/sm64/blob/master/data/behavior_data.c */
#define GP_OVERLAP_RADIUS 37.0
#define GP_OVERLAP_HALF_HEIGHT 80.0
/* Sound: full gain within this distance of live Mario (2.5 Mario heights of
 * 160 units), then min(1, GP_SOUND_NEAR / d), inverse-square intensity. */
#define GP_SOUND_NEAR 400.0f
/* The most records whose sounds one drawn frame hands over (3 s). */
#define GP_SOUND_CATCHUP 90u
/* A ghost's sound under its picture's fade (ghost_sound_fade.h) crosses the
 * whole fade, full to silent, in no fewer than this many frames (3 = 100 ms):
 * a switch flip, a camera cut or a jump in either Mario's place never steps
 * the level, while an ordinary walk apart (about 3 frames across one body) is
 * followed as it happens. */
#define GP_SOUND_FADE_FRAMES 3u
/* A ghost whose clip carries its star dance (GCLIP_DANCE_TAIL) fades out over
 * this many frames after its last recorded dance frame (15 = 0.5 s), holding
 * that pose; its sounds fade with it and are silent once it hides. */
#define GP_STAR_FADE_FRAMES 15u
/* A dance the recording cut short (his ask, 2026-09-28: "we literally know
 * exactly how long it should last / how it works"; a pause, reset or state
 * load right after the grab) plays on from its last recorded dance pose.
 * Source: n64decomp/sm64 src/game/mario_actions_cutscene.c. act_star_dance
 * and act_star_dance_water set MARIO_ANIM_STAR_DANCE (0xCD) and
 * MARIO_ANIM_WATER_STAR_DANCE (0xB3), face the camera's yaw and hold Mario's
 * place, and show MARIO_HAND_PEACE_SIGN (2) from actionTimer 40 and 62.
 * general_star_dance_handler counts `++m->actionTimer` from 1 on the dance's
 * own first frame, plays SOUND_MARIO_HERE_WE_GO (0x240C8080; captured as
 * 0x240C8081) at 42 and at 80 either calls level_trigger_warp(WARP_OP_STAR_EXIT)
 * (level_update.c: sDelayedWarpTimer = 32, so the last dance frame is
 * actionTimer 111, fully faded by WARP_TRANSITION_FADE_INTO_MARIO) or, on a
 * no-exit star (ACT_STAR_DANCE_NO_EXIT), stops time for the save dialog.
 * His 2026-09-28 archive agrees on every dance: the animation frame is
 * actionTimer - 1 until the animation's last frame (62, 82), the peace sign
 * and the voice land on those timers, and a midair grab's dance (entered from
 * act_fall_after_star_grab on landing) starts one row after the landing row. */
#define GP_DANCE_ANIMATION 0xCD
#define GP_WATER_DANCE_ANIMATION 0xB3
#define GP_DANCE_PEACE_TIMER 40u
#define GP_WATER_DANCE_PEACE_TIMER 62u
#define GP_HAND_PEACE_SIGN 2u
#define GP_DANCE_SOUND_TIMER 42u
#define GP_DANCE_SOUND 0x240C8081u
#define GP_DANCE_END_TIMER 111u
#define GP_DANCE_NO_EXIT_END_TIMER 80u
#define GP_DANCE_NO_EXIT_ACTION 0x00001307u
/* Defeat (his asks, 2026-09-23: "because you won, you get to trigger the
 * death animation for all of the remaining ghosts that were slower than
 * you"; 2026-09-28: "the ghost death / real mario comparison should be based
 * on the XCAM DANCE start", and "It should play through the full animation,
 * and then fade out when mario's passed out fully dead"). When live Mario's
 * x-cam dance begins on the grab that completes the attempt, each ghost whose
 * own dance began later plays Mario's own death where it stood, through its
 * last frame, then fades over GP_DEFEAT_FADE_FRAMES. A ghost whose dance
 * began at or before his keeps its dance and fade. Source: n64decomp/sm64
 * src/game/mario_actions_cutscene.c and mario_actions_submerged.c; lengths
 * are the US ROM's animation table (loopEnd), read through the model. Each
 * is ANIM_FLAG_NOLOOP and its pose last changes on its last frame, so that
 * frame is where he lies passed out.
 * - On land, act_standing_death: SOUND_MARIO_DYING (0x2415FF80, captured as
 *   0x2415FF81) once, dead eyes (MARIO_EYES_DEAD) and
 *   MARIO_ANIM_DYING_FALL_OVER (0x32, 96 frames): he clutches himself, hits
 *   the ground at frame 77 and lies still at 95. The game calls its death
 *   warp at frame 80 and draws him through the warp's 48-frame delay, so it
 *   shows the whole fall. Before 2026-09-28 the ghost faded from frame 80,
 *   while the fall still settled.
 * - Swimming (the submerged action group), check_common_submerged_cancels
 *   sends a Mario whose health runs out to act_drowning: SOUND_MARIO_DROWNING
 *   (0x2423F080) once, MARIO_ANIM_DROWNING_PART1 (0xA5, 80 frames) with
 *   half-closed eyes until is_anim_at_end, then MARIO_ANIM_DROWNING_PART2
 *   (0xA6, 70 frames) with dead eyes.
 * - In the air the game has no death (Mario dies once he lands), so a ghost
 *   defeated midair plays the land death where it stood. */
#define GP_DEFEAT_ANIMATION 0x32
#define GP_DEFEAT_EYES 8u
#define GP_DEFEAT_SOUND 0x2415FF81u
#define GP_DROWN_ANIMATION 0xA5
#define GP_DROWN_LIMP_ANIMATION 0xA6
#define GP_DROWN_EYES 2u
#define GP_DROWN_SOUND 0x2423F081u
#define GP_ACTION_GROUP_MASK 0x1C0u
#define GP_ACTION_GROUP_SUBMERGED 0x0C0u
#define GP_DEFEAT_FADE_FRAMES 15u
/* The furthest back from a clip's end its completing dance is looked for:
 * the dance tail (archive DANCE_TAIL_MAX_FRAMES, 300) and its grab. */
#define GP_DEFEAT_SCAN 512u
/* A run of at most this many consecutive refused records, with a recorded
 * pose on both sides in the same scene and no missing occurrence, holds the
 * pose before it instead of blinking (2 = 67 ms, below a noticeable pause).
 * The refused rows stay refused in the clip; this is display only. */
#define GP_BRIDGE_FRAMES 2u
