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
    // Opaque (depth-writing) frames come first: the bubble, then both mists.
    require(result.opaque_count == 6 && out[0].cutout == 1.0f && out[6].cutout == 0.0f, "opaque particles first");
    require(out[0].rgba[3] == 1.0f, "a bubble never fades by opacity");
    require(std::fabs(out[6].rgba[3] - 128.0f / 255.0f) < 1e-6f, "mist takes its recorded opacity");
    require(out[12].rgba[3] == 1.0f, "mist without a generated list stays whole");
    require(out[7].position[0] == 20 && out[7].position[2] == -500 && out[1].position[0] == 220,
            "billboard scale and origin");
    // A lit star: F3D lighting in the recorded view space by the list's own
    // light (dir 0x28,0x28,0x28, ambient 0x3f, diffuse 0x7f in red).
    {
        sg_particle_vertex lit[3]{};
        for (auto &v : lit) {
            v.mode = SG_PARTICLE_LIT | SG_PARTICLE_MIX; v.rgba[3] = 255;
            v.light_ambient[0] = 0x3f; v.light_diffuse[0] = 0x7f;
            v.light_dir[0] = v.light_dir[1] = v.light_dir[2] = 0x28;
        }
        lit[0].normal[0] = lit[0].normal[1] = lit[0].normal[2] = 0.57735f; // facing the light
        lit[1].normal[0] = lit[1].normal[1] = lit[1].normal[2] = -0.57735f; // facing away
        lit[2].normal[2] = 1;                                                // 54.7 degrees off
        sg_particle_frame star{0x0302C298u, SG_PARTICLE_CUTOUT, 0, 3, 0};
        sg_particle_view stars{&star, lit, nullptr, 1, 3, 0, 0};
        gc_sample one{};
        one.valid_fields = GC_VALID_PARTICLES | GC_VALID_CAMERA; one.particles.count = 1;
        auto &e = one.particles.entries[0]; e.display_list = 0x0302C298u;
        e.basis[0] = e.basis[4] = e.basis[8] = int16_t(GC_PARTICLE_BASIS_ONE);
        ghost_compositor::ParticleVertex shaded[ghost_compositor::particle_vertices]{};
        ghost_compositor::Particles drawn{};
        ghost_particle_pose::place(reinterpret_cast<sg_model *>(1), stars, one, gc_camera{}, identity, shaded, drawn);
        require(drawn.count == 3 && std::fabs(shaded[0].rgba[0] - (0x3f + 0x7f) / 255.0f) < 1e-4f
            && std::fabs(shaded[1].rgba[0] - 0x3f / 255.0f) < 1e-4f
            && std::fabs(shaded[2].rgba[0] - (0x3f + 0x7f * 0.57735f) / 255.0f) < 1e-3f
            && shaded[0].rgba[1] == 0 && shaded[0].mix == 1, "lit by the list's own light, mixed where the list mixes");
        // The executed matrix turns the normal: a 180-degree turn faces it away.
        e.basis[0] = e.basis[4] = e.basis[8] = int16_t(-GC_PARTICLE_BASIS_ONE);
        ghost_particle_pose::place(reinterpret_cast<sg_model *>(1), stars, one, gc_camera{}, identity, shaded, drawn);
        require(std::fabs(shaded[0].rgba[0] - 0x3f / 255.0f) < 1e-4f, "normals follow the executed matrix");
    }
    std::printf("particle pose passed: known=3 unknown=1 fade=recorded lit=f3d checks=%u\n", checks);
    return 0;
}
