/* Player-first fade: a ghost never blocks live Mario or the camera.
 * Display-only, per fragment, no allocation. The compositor applies the same
 * rule to every ghost body and shadow pixel; playback fills the region once
 * per frame from live Mario and the live view-projection.
 *
 *   fade = near(w) * (1 - PLAYER_STRENGTH * cover(ndc) * front(w))
 *   near  = smoothstep(NEAR_HIDDEN, NEAR_FULL, w)
 *   cover = 1 - smoothstep(REGION_INNER, REGION_OUTER, |(ndc - center) / radius|)
 *   front = smoothstep(0, FRONT_BAND, player_w - w)
 *
 * w is the fragment's clip w: its distance along the view axis in world units
 * (SM64's guPerspective uses scale 1, so clip w is eye depth). center/radius
 * bound live Mario's projected body box; player_w is his body centre's w. So
 * only ghost pixels nearer the camera than Mario AND over his body fade, by
 * how centrally they cover him; pixels behind him or away from him keep the
 * user's opacity exactly. Prior art, rejected alternatives and witnesses:
 * docs/ghost-native-runtime.md, "Player-first fade".
 */
#pragma once
#include <cmath>
#include <stdint.h>

namespace ghost_occlusion {
// Tuning: every constant of the rule lives here and nowhere else.
constexpr float BODY_HALF_WIDTH = 50.0f;  // bhvMario radius 37 plus arms, per unit gfx scale
constexpr float BODY_HEIGHT = 160.0f;     // bhvMario height, feet at the root
constexpr float REGION_INNER = 1.0f;      // full fade inside the ellipse inscribed in his box
constexpr float REGION_OUTER = 1.6f;      // none beyond 1.6x (the box corners sit at 1.41x)
constexpr float FRONT_BAND = 60.0f;       // world units nearer than his centre for the full fade
constexpr float PLAYER_STRENGTH = 0.85f;  // a ghost over his core keeps 15% of its opacity
constexpr float NEAR_HIDDEN = 150.0f;     // a ghost closer than this to the camera is invisible
constexpr float NEAR_FULL = 450.0f;       // and fully visible from here (about 43% of a 45-degree screen)
constexpr float PLAYER_MIN_W = 1.0f;      // any body corner nearer than this: no player region

/* Per-frame values for the shader. enabled=0 draws exactly as before. */
struct Region {
    uint32_t enabled;    // the A/B switch (occlusion_fade)
    uint32_t has_player; // live Mario's region below is valid
    float center[2], radius[2]; // his body box on screen, NDC before the renderer's y flip
    float player_w;      // his body centre's clip w
};

/* The GLSL rule, shared verbatim by the body and shadow shaders (a middle
 * ShaderSource string after #version). Input is the fragment's unflipped clip
 * position; uniforms come from Region and the constants above. */
constexpr const char* glsl = R"(
uniform int occlusionMode;    // 0 off, 1 camera only, 2 camera and player
uniform vec2 occluderCenter;  // live Mario's body box on screen, NDC
uniform vec2 occluderRadius;
uniform float occluderW;      // his body centre's clip w
uniform vec2 occlusionRegion; // REGION_INNER, REGION_OUTER
uniform vec2 occlusionFront;  // FRONT_BAND, PLAYER_STRENGTH
uniform vec2 nearFade;        // NEAR_HIDDEN, NEAR_FULL
float playerFirst(vec4 clip) {
    if (occlusionMode==0) return 1.0;
    float fade=smoothstep(nearFade.x,nearFade.y,clip.w);
    if (occlusionMode==2) {
        float reach=length((clip.xy/clip.w-occluderCenter)/occluderRadius);
        float cover=1.0-smoothstep(occlusionRegion.x,occlusionRegion.y,reach);
        float front=smoothstep(0.0,occlusionFront.x,occluderW-clip.w);
        fade*=1.0-occlusionFront.y*cover*front;
    }
    return fade;
}
)";
/* Uniform names in glsl; the compositor looks them up in this order. */
constexpr const char* uniform_names[] = {"occlusionMode", "occluderCenter", "occluderRadius",
    "occluderW", "occlusionRegion", "occlusionFront", "nearFade"};
constexpr unsigned uniform_count = sizeof(uniform_names) / sizeof(uniform_names[0]);

/* Live Mario's body box (feet at position, scaled by his gfx scale) under the
 * column-major world-to-clip matrix the ghosts use. Leaves has_player 0 for
 * a missing or non-finite pose, or a body at or behind the camera. */
inline void region(const float vp[16], bool enabled, bool player_valid,
                   const float position[3], const float scale[3], Region &out) {
    out = {};
    out.enabled = enabled ? 1u : 0u;
    if (!enabled || !player_valid) return;
    for (unsigned i = 0; i < 3; ++i)
        if (!std::isfinite(position[i]) || !std::isfinite(scale[i]) || scale[i] <= 0) return;
    auto clip = [&](float x, float y, float z, float c[4]) {
        for (unsigned r = 0; r < 4; ++r) c[r] = vp[r]*x + vp[4+r]*y + vp[8+r]*z + vp[12+r];
    };
    const float hx = BODY_HALF_WIDTH*scale[0], hz = BODY_HALF_WIDTH*scale[2], h = BODY_HEIGHT*scale[1];
    float lo[2] = {INFINITY, INFINITY}, hi[2] = {-INFINITY, -INFINITY};
    for (unsigned corner = 0; corner < 8; ++corner) {
        float c[4];
        clip(position[0] + (corner&1 ? hx : -hx), position[1] + (corner&2 ? h : 0.0f),
             position[2] + (corner&4 ? hz : -hz), c);
        if (!(c[3] >= PLAYER_MIN_W) || !std::isfinite(c[0]) || !std::isfinite(c[1])) return;
        for (unsigned a = 0; a < 2; ++a) {
            const float ndc = c[a]/c[3];
            lo[a] = ndc < lo[a] ? ndc : lo[a]; hi[a] = ndc > hi[a] ? ndc : hi[a];
        }
    }
    float centre[4];
    clip(position[0], position[1] + 0.5f*h, position[2], centre);
    if (!std::isfinite(centre[3])) return;
    for (unsigned a = 0; a < 2; ++a) {
        out.center[a] = 0.5f*(lo[a] + hi[a]);
        out.radius[a] = 0.5f*(hi[a] - lo[a]);
        if (!(out.radius[a] > 0) || !std::isfinite(out.radius[a])) return;
    }
    out.player_w = centre[3];
    out.has_player = 1;
}
}
