#include "ghost_pass.h"
#include <cmath>
#include <cstring>
#include <limits>

namespace ghost {
namespace {
bool finite(const float *values, unsigned count) {
    for (unsigned i = 0; i < count; ++i)
        if (!std::isfinite(values[i])) return false;
    return true;
}
bool perspective(const float *m) {
    // guPerspective may normalize the entire matrix. Screen-roll multiplication
    // rotates X/Y but retains a nonzero W-from-Z and zero constant W.
    return m && finite(m, 16) && m[15] == 0.0f && m[11] < 0.0f
        && m[3] == 0.0f && m[7] == 0.0f && m[14] < 0.0f;
}
bool same_projection(const float *a, const float *b) {
    for (unsigned i = 0; i < 16; ++i) if (a[i] != b[i]) return false;
    return true;
}
bool target_valid(const RasterTarget &t) {
    return t.color && t.depth && t.width && t.height && t.width <= 16384
        && t.height <= 16384 && t.viewport[2] > 0 && t.viewport[3] > 0
        && t.viewport[2] <= 32768 && t.viewport[3] <= 32768
        && finite(t.depth_scale, 2) && t.depth_scale[0] > 0.0f
        && finite(t.screen_scale, 2) && t.screen_scale[0] != 0.0f
        && t.screen_scale[1] != 0.0f;
}
bool same_mapping(const RasterTarget &a, const RasterTarget &b) {
    for (unsigned i = 0; i < 4; ++i) if (a.viewport[i] != b.viewport[i]) return false;
    for (unsigned i = 0; i < 2; ++i)
        if (a.depth_scale[i] != b.depth_scale[i] || a.screen_scale[i] != b.screen_scale[i])
            return false;
    return true;
}
}
void Pass::reset() { *this = Pass{}; }
void Pass::begin(uint32_t epoch, uint32_t generation, uint64_t serial, bool enabled) {
    reset();
    if (!enabled) return;
    if (!epoch || !generation || !serial) { refuse(PassStatus::InvalidIdentity); return; }
    value_.epoch = epoch; value_.context_generation = generation; value_.list_serial = serial;
    status_ = PassStatus::NoWorld;
}
void Pass::refuse(PassStatus status) {
    status_ = status; world_open_ = false; sealed_ = true;
    // Clear the entire payload on failure; consumers cannot retain old pixels.
    value_ = {};
}
void Pass::projection(const float m[16]) {
    if (sealed_ || status_ == PassStatus::Disabled) return;
    projection_known_ = perspective(m);
    if (world_open_ && (!projection_known_ || !same_projection(value_.projection, m))) {
        refuse(PassStatus::ProjectionChanged); return;
    }
    if (projection_known_) std::memcpy(current_projection_, m, sizeof(current_projection_));
}
void Pass::triangles(const RasterTarget &t, uint32_t count, bool zbuffer) {
    if (sealed_ || status_ == PassStatus::Disabled || !count || !zbuffer) return;
    if (status_ == PassStatus::Candidate) { refuse(PassStatus::MultipleWorlds); return; }
    if (!projection_known_) { refuse(PassStatus::InvalidProjection); return; }
    if (!target_valid(t)) { refuse(PassStatus::InvalidIdentity); return; }
    if (t.samples != 1 || t.depth_mode > 1) { refuse(PassStatus::UnsupportedDepth); return; }
    if (!world_open_) {
        value_.target = t;
        std::memcpy(value_.projection, current_projection_, sizeof(value_.projection));
        world_open_ = true;
    } else if (t.color != value_.target.color || t.depth != value_.target.depth
            || t.width != value_.target.width || t.height != value_.target.height
            || t.depth_mode != value_.target.depth_mode) {
        refuse(PassStatus::TargetChanged); return;
    } else if (!same_mapping(t, value_.target)) {
        refuse(PassStatus::ViewportChanged); return;
    }
    if (count > std::numeric_limits<uint32_t>::max() - value_.triangles) {
        refuse(PassStatus::Interrupted); return;
    }
    value_.triangles += count;
}
bool Pass::geometry(bool was_zbuffer, bool is_zbuffer) {
    if (sealed_ || status_ == PassStatus::Disabled) return false;
    if (status_ == PassStatus::Candidate && is_zbuffer && !was_zbuffer) {
        refuse(PassStatus::MultipleWorlds); return false;
    }
    if (!world_open_ || !was_zbuffer || is_zbuffer) return false;
    world_open_ = false;
    status_ = PassStatus::Candidate;
    return true;
}
void Pass::color_target_changed() {
    if (!sealed_ && (world_open_ || status_ == PassStatus::Candidate))
        refuse(PassStatus::TargetChanged);
}
bool Pass::end(bool completed) {
    if (sealed_) return false;
    sealed_ = true;
    if (!completed) { refuse(PassStatus::Interrupted); return false; }
    if (status_ == PassStatus::Candidate) { status_ = PassStatus::Ready; return true; }
    if (status_ != PassStatus::Disabled) refuse(PassStatus::NoWorld);
    return false;
}
const WorldPass *Pass::candidate() const {
    return status_ == PassStatus::Candidate || status_ == PassStatus::Ready ? &value_ : nullptr;
}
} // namespace ghost
