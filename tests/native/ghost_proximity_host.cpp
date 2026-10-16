#include "ghost_proximity.h"
#include <cstdio>
#include <limits>

int main() {
    const float p[3]{0, 0, 0}, scale[3]{1, 1, 1};
    float g[3]{0, 0, 0};
    auto gain = [&](float extra = 0) { return ghost_proximity::factor(p, scale, g, scale, extra); };
    // The floor, stated here rather than read from the header: an overlapping
    // ghost keeps 15% of its opacity, and half an overlap sits half way up.
    const float floor = .15f, half = floor + (1 - floor) * .5f;
    if (ghost_occlusion::GHOST_FLOOR != floor) return 9;
    if (gain() != floor) return 1; // Coincident (a pole, a ledge): faint, never gone.
    g[0] = 1;
    if (gain() < floor || gain() > floor + .001f) return 10; // one unit apart, as on the plank
    g[0] = 37;
    if (std::abs(gain() - half) > .00001f) return 2;
    g[0] = 74;
    if (gain() != 1 || std::abs(gain(74) - half) > .00001f) return 3;
    g[0] = 0; g[1] = 160;
    if (gain() != 1) return 4; // A ghost above Mario must not disappear.
    g[1] = 80;
    if (std::abs(gain() - half) > .00001f) return 5;
    const float big[3]{2, 2, 2}, centered[3]{0, -80, 0};
    if (ghost_proximity::factor(p, scale, centered, big, 0) != floor) return 6;
    g[0] = std::numeric_limits<float>::quiet_NaN();
    if (gain() != 1) return 7; // Missing live geometry must not invent a fade.
    g[1] = 0;
    float previous = 0;
    for (unsigned i = 0; i < 1000; ++i) {
        g[0] = float(i);
        const float current = gain(120);
        if (current < previous || current < floor || current > 1) return 8;
        previous = current;
    }
    std::puts("proximity: floor at coincidence, touching, vertical, scale, invalid and monotonic checks passed");
    return 0;
}
