/* Particle placement alone, cameras stubbed to identity: which frames exist,
 * how the recorded opacity reaches a fading frame, and billboard alignment. */
#include "ghost_particle_pose.h"
#include "ghost_pose.h"
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>

static unsigned checks = 0;
static void require(bool value, const char *what) {
    ++checks;
    if (!value) { std::fprintf(stderr, "FAILED: %s\n", what); std::exit(1); }
}
// Recorded camera and live camera: identity look-ats, so view space is world space.
extern "C" int32_t sg_model_view(sg_model *, const float *, const float *, int16_t, float out[16]) {
    std::memset(out, 0, 16 * sizeof(float)); out[0] = out[5] = out[10] = out[15] = 1; return SG_OK;
}
namespace ghost_pose {
bool recorded_camera_inverse(sg_model *model, const gc_camera &camera, float inverse[16]) {
    return sg_model_view(model, camera.position, camera.focus, camera.roll, inverse) == SG_OK;
}
}

int main() {
    sg_particle_vertex quad[6]{};
    for (auto &v : quad) { v.rgba[0] = v.rgba[1] = v.rgba[2] = v.rgba[3] = 255; }
    quad[1].position[0] = 10;
    sg_particle_frame frames[2] = {{0x03000880u, SG_PARTICLE_BLEND, 0, 6, 1},   // mist: fades
                                   {0x0401DD60u, SG_PARTICLE_CUTOUT, 0, 6, 0}}; // bubble: does not
    sg_particle_view view{frames, quad, nullptr, 2, 6, 0, 0};
    gc_sample recorded{};
    recorded.valid_fields = GC_VALID_PARTICLES | GC_VALID_CAMERA;
    recorded.particles.count = 4;
    const uint32_t lists[4] = {0x03000880u, 0x03000880u, 0x0401DD60u, 0x04099999u};
    const uint16_t opacity[4] = {GC_PARTICLE_OPACITY | 0x80, 0, GC_PARTICLE_OPACITY | 0x80, 0};
    for (uint32_t i = 0; i < 4; ++i) {
        auto &e = recorded.particles.entries[i];
        e.display_list = lists[i]; e.opacity = opacity[i];
        e.basis[0] = e.basis[4] = e.basis[8] = int16_t(2 * GC_PARTICLE_BASIS_ONE); // billboard, scale 2
        e.translation[0] = float(100 * i); e.translation[2] = -500;
    }
    ghost_compositor::ParticleVertex out[ghost_compositor::particle_vertices]{};
    ghost_compositor::Particles result{};
    const float identity[16] = {1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1};
    const auto placed = ghost_particle_pose::place(reinterpret_cast<sg_model *>(1), view, recorded, gc_camera{},
                                                   identity, out, result);
    require(placed.drawn == 3 && placed.unknown == 1 && result.count == 18, "three known frames drawn, one unknown skipped");
    require(std::fabs(out[0].rgba[3] - 128.0f / 255.0f) < 1e-6f, "mist takes its recorded opacity");
    require(out[6].rgba[3] == 1.0f, "mist without a generated list stays whole");
    require(out[12].rgba[3] == 1.0f && out[12].cutout == 1.0f, "a bubble never fades by opacity");
    require(out[1].position[0] == 20 && out[1].position[2] == -500 && out[13].position[0] == 220,
            "billboard scale and origin");
    std::printf("particle pose passed: known=3 unknown=1 fade=recorded checks=%u\n", checks);
    return 0;
}
