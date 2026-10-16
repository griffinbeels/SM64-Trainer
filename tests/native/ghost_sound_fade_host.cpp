/* The CPU copy of the player-first fade (ghost_occlusion.h point_fade), which
 * the ghost's sound reads, against the witnesses' own statement of the rule
 * (ghost_occlusion_expect.h), which the GPU pixels are checked against too. */
#include "ghost_occlusion.h"
#include "ghost_occlusion_expect.h"
#include "ghost_sound_fade.h"
#include <cmath>
#include <cstdio>

static unsigned checks = 0, failures = 0;
static void check(bool ok, const char *message) {
    ++checks;
    if (!ok && failures++ < 20) std::fprintf(stderr, "failed: %s\n", message);
}

int main() {
    using namespace ghost_occlusion;
    constexpr uint32_t size = 64;
    const float depths[] = {100, 150, 200, 300, 450, 600, 940, 1000, 1030, 1060, 1200};
    const float overlaps[] = {1.0f, 0.6f, 0.15f};
    double worst = 0;
    for (unsigned mode = 0; mode < 3; ++mode)
        for (float overlap : overlaps)
            for (float w : depths)
                for (uint32_t y = 0; y < size; y += 3)
                    for (uint32_t x = 0; x < size; x += 3) {
                        Region r = occlusion_expect::player(1000);
                        r.enabled = mode > 0; r.has_player = mode > 1; r.overlap = overlap;
                        const float clip[4] = {(2 * (x + .5f) / size - 1) * w, (2 * (y + .5f) / size - 1) * w, 0, w};
                        const double apart = std::fabs(point_fade(r, clip) - occlusion_expect::fade(r, x, y, w, size, size));
                        worst = apart > worst ? apart : worst;
                        check(apart < 1e-5, "a point keeps what a pixel there keeps");
                    }
    // Behind the camera plane the picture draws nothing: the camera term by
    // the distance behind it, and no in-front term.
    Region r = occlusion_expect::player(1000);
    const float behind[4] = {0, 0, 0, -300}, far_behind[4] = {0, 0, 0, -600}, at[4] = {0, 0, 0, 0};
    check(std::fabs(point_fade(r, behind) - 0.5f) < 1e-6f, "300 behind the camera keeps half");
    check(point_fade(r, far_behind) == 1.0f && point_fade(r, at) == 0.0f, "whole 450 behind, silent at the plane");
    r.overlap = 0.15f;
    check(std::fabs(point_fade(r, far_behind) - 0.15f) < 1e-6f, "the overlap fade still applies behind the camera");
    const float broken[4] = {NAN, 0, 0, 500};
    check(point_fade(r, broken) == 1.0f, "a point that cannot be projected keeps everything");
    // The sound's step: at most STEP a frame, the whole fade in GP_SOUND_FADE_FRAMES.
    float gain = 1;
    for (unsigned frame = 0; frame < GP_SOUND_FADE_FRAMES; ++frame) {
        const float next = ghost_sound_fade::follow(gain, 0);
        check(gain - next <= ghost_sound_fade::STEP + 1e-6f, "never more than a step");
        gain = next;
    }
    check(gain < 1e-6f, "silent after GP_SOUND_FADE_FRAMES");
    check(ghost_sound_fade::follow(0.5f, NAN) == 0.5f + ghost_sound_fade::STEP || ghost_sound_fade::follow(0.5f, NAN) == 1.0f,
          "an unknown target rises toward whole");
    std::printf("{\"ok\":%s,\"checks\":%u,\"worst\":%.9f}\n", failures ? "false" : "true", checks, worst);
    return failures ? 1 : 0;
}
