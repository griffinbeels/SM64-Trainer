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
/* A run of at most this many consecutive refused records, with a recorded
 * pose on both sides in the same scene and no missing occurrence, holds the
 * pose before it instead of blinking (2 = 67 ms, below a noticeable pause).
 * The refused rows stay refused in the clip; this is display only. */
#define GP_BRIDGE_FRAMES 2u
