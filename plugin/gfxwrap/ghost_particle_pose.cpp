#include "ghost_particle_pose.h"
#include "ghost_pose.h"
#include <cmath>
#include <cstring>

namespace ghost_particle_pose {
namespace {
// A billboard basis rotates only about the view axis (mtxf_billboard: scale
// and roll); anything touching view z is a world-oriented object.
bool view_aligned(const float basis[9]) {
    constexpr float zero = 2.0f / GC_PARTICLE_BASIS_ONE;
    return std::fabs(basis[2]) <= zero && std::fabs(basis[5]) <= zero
        && std::fabs(basis[6]) <= zero && std::fabs(basis[7]) <= zero;
}
const sg_particle_frame *frame(const sg_particle_view &view, uint32_t dl) {
    for (uint32_t i = 0; i < view.frame_count; ++i)
        if (view.frames[i].display_list == dl) return &view.frames[i];
    return nullptr;
}
}
Placed place(sg_model *model, const sg_particle_view &view, const gc_sample &recorded, const gc_camera &live,
             const float view_projection[16], ghost_compositor::ParticleVertex *out,
             ghost_compositor::Particles &result) {
    result = {};
    Placed placed{};
    const auto &set = recorded.particles;
    if (!model || !out || !(recorded.valid_fields & GC_VALID_PARTICLES) || !(recorded.valid_fields & GC_VALID_CAMERA)
            || !set.count || !view.frame_count) return placed;
    float inverse[16]{}, live_view[16]{};
    if (!ghost_pose::recorded_camera_inverse(model, recorded.camera, inverse)
            || sg_model_view(model, live.position, live.focus, live.roll, live_view) != SG_OK) return placed;
    uint32_t written = 0;
    for (uint32_t i = 0; i < set.count && i < GC_PARTICLES; ++i) {
        const auto &entry = set.entries[i];
        const auto *f = frame(view, entry.display_list);
        if (!f) { ++placed.unknown; continue; }
        if (written + f->vertex_count > ghost_compositor::particle_vertices) break;
        float basis[9], t[3];
        bool finite = true;
        for (unsigned k = 0; k < 9; ++k) basis[k] = float(entry.basis[k]) / GC_PARTICLE_BASIS_ONE;
        for (unsigned k = 0; k < 3; ++k) { t[k] = entry.translation[k]; finite &= std::isfinite(t[k]); }
        if (!finite) { ++placed.invalid; continue; }
        // The particle's origin in the world: the recorded camera removed.
        float origin[3];
        for (unsigned r = 0; r < 3; ++r)
            origin[r] = inverse[r]*t[0] + inverse[4+r]*t[1] + inverse[8+r]*t[2] + inverse[12+r];
        const bool billboard = view_aligned(basis);
        for (uint32_t v = 0; v < f->vertex_count; ++v) {
            const auto &source = view.vertices[f->first_vertex + v];
            auto &target = out[written + v];
            float offset[3]; // recorded view space, relative to the origin
            for (unsigned r = 0; r < 3; ++r)
                offset[r] = basis[r]*source.position[0] + basis[3+r]*source.position[1] + basis[6+r]*source.position[2];
            for (unsigned r = 0; r < 3; ++r) {
                // Billboard: that view-space offset along the LIVE camera's axes
                // (transpose of its rotation). Otherwise along the recorded ones.
                const float *axes = billboard ? live_view : inverse;
                target.position[r] = origin[r] + (billboard
                    ? axes[4*r]*offset[0] + axes[4*r+1]*offset[1] + axes[4*r+2]*offset[2]
                    : axes[r]*offset[0] + axes[4+r]*offset[1] + axes[8+r]*offset[2]);
            }
            std::memcpy(target.uv, source.uv, sizeof(target.uv));
            std::memcpy(target.rect, source.rect, sizeof(target.rect));
            for (unsigned k = 0; k < 4; ++k) target.rgba[k] = source.rgba[k] / 255.0f;
            if (f->fade && (entry.opacity & GC_PARTICLE_OPACITY)) target.rgba[3] *= float(entry.opacity & 0xffu) / 255.0f;
            target.cutout = f->layer == SG_PARTICLE_CUTOUT ? 1.0f : 0.0f;
        }
        written += f->vertex_count;
        ++placed.drawn;
    }
    if (written) {
        result.vertices = out; result.count = written;
        std::memcpy(result.view_projection, view_projection, sizeof(result.view_projection));
    }
    return placed;
}
}
