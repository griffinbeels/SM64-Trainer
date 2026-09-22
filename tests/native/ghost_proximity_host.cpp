#include "ghost_proximity.h"
#include <cstdio>
#include <limits>

int main() {
    const float p[3]{0, 0, 0}, scale[3]{1, 1, 1};
    float g[3]{0, 0, 0};
    auto gain = [&](float extra = 0) { return ghost_proximity::factor(p, scale, g, scale, extra); };
    if (gain() != 0) return 1;
    g[0] = 37;
    if (std::abs(gain() - .5f) > .00001f) return 2;
    g[0] = 74;
    if (gain() != 1 || std::abs(gain(74) - .5f) > .00001f) return 3;
    g[0] = 0; g[1] = 160;
    if (gain() != 1) return 4; // A ghost above Mario must not disappear.
    g[1] = 80;
    if (std::abs(gain() - .5f) > .00001f) return 5;
    const float big[3]{2, 2, 2}, centered[3]{0, -80, 0};
    if (ghost_proximity::factor(p, scale, centered, big, 0) != 0) return 6;
    g[0] = std::numeric_limits<float>::quiet_NaN();
    if (gain() != 1) return 7; // Missing live geometry must not invent a fade.
    g[1] = 0;
    float previous = 0;
    for (unsigned i = 0; i < 1000; ++i) {
        g[0] = float(i);
        const float current = gain(120);
        if (current < previous || current < 0 || current > 1) return 8;
        previous = current;
    }
    std::puts("proximity: coincident, touching, vertical, scale, invalid and monotonic checks passed");
    return 0;
}
