/* Ghost playback tuning and limits: every number here was judged by feel or
 * sized for his machine, and each lives only here. The player-first fade
 * keeps its own rule and constants in ghost_occlusion.h. Python reads the
 * shared limits (GP_GHOSTS, GP_PROXIMITY_MAX) from ghost/native_layout.py,
 * which tests/test_ghost_layout.py checks against these values. */
#pragma once

/* Ghosts displayed at once: playback slots, one compositor batch, one sound
 * source each and the IPC request's clip entries all hold this many. */
#define GP_GHOSTS 16u
/* The largest extra overlap-fade distance a request may carry (world units). */
#define GP_PROXIMITY_MAX 400.0f
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
/* A ghost's sound under its overlap fade (ghost_sound_fade.h) crosses the
 * whole fade, full to floor, in no fewer than this many frames (3 = 100 ms):
 * a ghost appearing, a switch flip or a jump in either Mario's place never
 * steps the level, while an ordinary walk apart (about 3 frames across one
 * body) is followed as it happens. */
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
 * on the XCAM DANCE start"). When live Mario's x-cam dance begins on the grab
 * that completes the attempt, each ghost whose own dance began later plays
 * Mario's standing death where it stood: act_standing_death's
 * MARIO_ANIM_DYING_FALL_OVER (0x32; he clutches
 * himself and falls on his face), dead eyes (MARIO_EYES_DEAD) and
 * SOUND_MARIO_DYING = SOUND_ARG_LOAD(2, 4, 0x15, 0xFF, 8). The game warps
 * away at frame 80 of that fall; the ghost holds its last frame from there
 * and fades over GP_DEFEAT_FADE_FRAMES. A ghost whose dance began at or
 * before his keeps its dance and fade. Source: n64decomp/sm64
 * src/game/mario_actions_cutscene.c (act_standing_death, common_death_handler). */
#define GP_DEFEAT_ANIMATION 0x32
#define GP_DEFEAT_EYES 8u
#define GP_DEFEAT_SOUND 0x2415FF81u
#define GP_DEFEAT_FRAMES 80u
#define GP_DEFEAT_FADE_FRAMES 15u
/* The furthest back from a clip's end its completing dance is looked for:
 * the dance tail (archive DANCE_TAIL_MAX_FRAMES, 180) and its grab. */
#define GP_DEFEAT_SCAN 512u
/* A run of at most this many consecutive refused records, with a recorded
 * pose on both sides in the same scene and no missing occurrence, holds the
 * pose before it instead of blinking (2 = 67 ms, below a noticeable pause).
 * The refused rows stay refused in the clip; this is display only. */
#define GP_BRIDGE_FRAMES 2u
