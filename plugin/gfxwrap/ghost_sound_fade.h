/* A ghost's sound under its overlap fade. His ask, 2026-09-28: "If the ghost
 * is more invisible, the audio should be quieter, too. However, I also don't
 * want to lose the benefits of the ghost audio (that is, to clearly inform us
 * about timing differences between our movements and the ghost)."
 * Display-only per frame, no allocation. Playback applies both halves while
 * the request's overlap_sound switch is on:
 *
 * - QUIETER: the gain follows the same overlap factor the picture fades by
 *   (ghost_proximity.h), floor included, moving at most STEP a frame.
 * - TIMING KEPT: while that factor is below 1, a request live Mario issued on
 *   the same game frame is his own sound played twice with no timing
 *   difference, and it is not played. One frame apart still plays.
 *
 * The player-first fade's in-front and camera terms (ghost_occlusion.h) do not
 * apply: they fade a ghost because it hides the view, and a sound hides
 * nothing. A ghost trailing him in line with the camera is faint on screen, so
 * its sound is the timing cue that is left. Evidence and witnesses:
 * docs/ghost-native-runtime.md, "Ghost sound under the overlap fade".
 */
#pragma once
#include "ghost_capture.h"
#include "ghost_occlusion.h"
#include "ghost_tuning.h"
#include <stdint.h>

namespace ghost_sound_fade {
constexpr float STEP = (1.0f - ghost_occlusion::GHOST_FLOOR) / float(GP_SOUND_FADE_FRAMES);

/* This frame's gain from the last one, toward target, at most STEP away. */
inline float follow(float last, float target) {
    if (!(target >= 0.0f && target <= 1.0f)) target = 1.0f;
    if (!(last >= 0.0f && last <= 1.0f)) return target;
    const float apart = target - last;
    return apart > STEP ? last + STEP : apart < -STEP ? last - STEP : target;
}

/* A request as a twin compares it. SM64 picks two of Mario's voice lines from
 * a family at random (mario.c: SOUND_MARIO_YAH_WAH_HOO + gAudioRandom % 3,
 * SOUND_MARIO_YAHOO_WAHA_YIPPEE + gAudioRandom % 5), so the same jump may say
 * "wah" live and "hoo" in the recording. Everything else must match exactly. */
inline uint32_t sound_key(uint32_t bits) {
    const uint32_t bank = bits >> 28, id = (bits >> 16) & 0xffu;
    uint32_t family = id;
    if (bank == 2 && id <= 0x02) family = 0x00;                     // YAH, WAH, HOO
    else if (bank == 2 && id >= 0x2b && id <= 0x2f) family = 0x2b; // YAHOO, WAHA, YIPPEE...
    return (bits & ~0x00ff0000u) | family << 16;
}

/* Live Mario issued this request on this game frame. */
inline bool twinned(const gc_sample &live, uint32_t bits) {
    if (!(live.valid_fields & GC_VALID_SOUND)) return false;
    const uint32_t key = sound_key(bits);
    for (uint32_t k = 0; k < live.sound.count && k < GC_SOUND_EVENTS; ++k)
        if (sound_key(live.sound.bits[k]) == key) return true;
    return false;
}
}
