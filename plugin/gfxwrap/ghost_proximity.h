/* Display-only readability curve. No allocation, timing or model mutation.
 * Uses an ellipsoid proxy sized from bhvMario's radius=37, height=160:
 * https://github.com/n64decomp/sm64/blob/master/data/behavior_data.c
 * This is deliberately not a pixel-overlap test or a Trackmania formula.
 */
#pragma once
#include <cmath>

namespace ghost_proximity {
inline float factor(const float live_position[3], const float live_scale[3],
                    const float ghost_position[3], const float ghost_scale[3],
                    float extra_distance) {
    if (!std::isfinite(extra_distance) || extra_distance < 0.0f) return 1.0f;
    double delta[3], extent[3];
    for (unsigned i = 0; i < 3; ++i) {
        if (!std::isfinite(live_position[i]) || !std::isfinite(ghost_position[i]) ||
            !std::isfinite(live_scale[i]) || !std::isfinite(ghost_scale[i]) ||
            live_scale[i] <= 0.0f || ghost_scale[i] <= 0.0f) return 1.0f;
        const double radius = i == 1 ? 80.0 : 37.0;
        extent[i] = radius * (double(live_scale[i]) + ghost_scale[i]);
        delta[i] = double(ghost_position[i]) - live_position[i];
        if (i == 1) delta[i] += radius * (double(ghost_scale[i]) - live_scale[i]);
    }
    double squared_distance = 0, normalized_distance = 0;
    for (unsigned i = 0; i < 3; ++i) {
        squared_distance += delta[i] * delta[i];
        const double normalized = delta[i] / extent[i];
        normalized_distance += normalized * normalized;
    }
    if (squared_distance == 0) return 0.0f;
    const double distance = std::sqrt(squared_distance);
    const double touching_distance = distance / std::sqrt(normalized_distance);
    const double t = distance / (touching_distance + extra_distance);
    if (t >= 1.0) return 1.0f;
    return float(t * t * (3.0 - 2.0 * t));
}
}
