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
/* A ghost whose clip carries its star dance (GCLIP_DANCE_TAIL) fades out over
 * this many frames after its last recorded dance frame (15 = 0.5 s), holding
 * that pose; its sounds fade with it and are silent once it hides. */
#define GP_STAR_FADE_FRAMES 15u
/* Defeat (his ask, 2026-09-23: "because you won, you get to trigger the
 * death animation for all of the remaining ghosts that were slower than
 * you"). When live Mario's grab completes the attempt, each ghost still
 * short of its own completing grab plays Mario's standing death where it
 * stood: act_standing_death's MARIO_ANIM_DYING_FALL_OVER (0x32; he clutches
 * himself and falls on his face), dead eyes (MARIO_EYES_DEAD) and
 * SOUND_MARIO_DYING = SOUND_ARG_LOAD(2, 4, 0x15, 0xFF, 8). The game warps
 * away at frame 80 of that fall; the ghost holds its last frame from there
 * and fades over GP_DEFEAT_FADE_FRAMES. A ghost already past its own grab
 * keeps its dance and fade. Source: n64decomp/sm64
 * src/game/mario_actions_cutscene.c (act_standing_death, common_death_handler). */
#define GP_DEFEAT_ANIMATION 0x32
#define GP_DEFEAT_EYES 8u
#define GP_DEFEAT_SOUND 0x2415FF81u
#define GP_DEFEAT_FRAMES 80u
#define GP_DEFEAT_FADE_FRAMES 15u
/* The furthest back from a clip's end its completing grab is looked for:
 * the dance tail (archive DANCE_TAIL_MAX_FRAMES, 180) and its grab. */
#define GP_DEFEAT_SCAN 512u
/* A run of at most this many consecutive refused records, with a recorded
 * pose on both sides in the same scene and no missing occurrence, holds the
 * pose before it instead of blinking (2 = 67 ms, below a noticeable pause).
 * The refused rows stay refused in the clip; this is display only. */
#define GP_BRIDGE_FRAMES 2u
