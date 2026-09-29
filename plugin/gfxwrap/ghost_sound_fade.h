/* A ghost's sound follows the fade its picture has. His asks, 2026-09-28:
 * "If the ghost is more invisible, the audio should be quieter, too. However,
 * I also don't want to lose the benefits of the ghost audio (that is, to
 * clearly inform us about timing differences between our movements and the
 * ghost)", and then, of the fade in front of Mario: "yes, we should do this
 * for the camera blocking case, too." Display-only per frame, no allocation.
 * Playback applies both halves while the request's sound_fade switch is on:
 *
 * - QUIETER: the gain follows what the picture keeps at the ghost's body
 *   centre (ghost_occlusion.h point_fade): the overlap fade, the in-front and
 *   camera terms, the floor, each only while its own switch is on. It moves
 *   at most STEP a frame.
 * - TIMING KEPT: while the overlap fade is below 1, a request live Mario
 *   issued on the same game frame is his own sound played twice with no
 *   timing difference, and it is not played. One frame apart still plays.
 *   The in-front and camera terms do not drop twins: a ghost there is not
 *   where he is.
 *
 * Evidence and witnesses: docs/ghost-native-runtime.md, "Ghost sound follows
 * its picture's fade".
 */
#pragma once
#include "ghost_capture.h"
#include "ghost_occlusion.h"
#include "ghost_tuning.h"
#include <stdint.h>

namespace ghost_sound_fade {
constexpr float STEP = 1.0f / float(GP_SOUND_FADE_FRAMES);

/* This frame's gain from the last one, toward target, at most STEP away. */
inline float follow(float last, float target) {
    if (!(target >= 0.0f && target <= 1.0f)) target = 1.0f;
    if (!(last >= 0.0f && last <= 1.0f)) return target;
    const float apart = target - last;
    return apart > STEP ? last + STEP : apart < -STEP ? last - STEP : target;
}

/* What the picture keeps at this ghost's body centre (half its height above
 * the root), under the view-projection and player-first region its pose was
 * drawn with. */
inline float picture_fade(const float vp[16], const ghost_occlusion::Region &region,
                          const float root[3], const float scale[3]) {
    const float x = root[0], y = root[1] + 0.5f * ghost_occlusion::BODY_HEIGHT * scale[1], z = root[2];
    float clip[4];
    for (unsigned r = 0; r < 4; ++r) clip[r] = vp[r] * x + vp[4 + r] * y + vp[8 + r] * z + vp[12 + r];
    return ghost_occlusion::point_fade(region, clip);
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
