#include "ghost_live_codec.h"
#include "ghost_behaviours.h"
#include "ghost_annotation.h"
#include <cmath>
#include <cstring>
#include <limits>

namespace ghost_live {
namespace {
bool nonzero(const void *value, size_t bytes) noexcept {
    const auto *p = static_cast<const uint8_t *>(value);
    for (size_t i = 0; i < bytes; ++i) if (p[i]) return true;
    return false;
}
bool finite(float value, float limit = 10000000.0f) noexcept {
    return std::isfinite(value) && std::fabs(value) <= limit;
}
bool scale(float value) noexcept {
    return finite(value, 100.0f) && std::fabs(value) >= 0.000001f;
}
bool pose_valid(const gc_sample &s) noexcept {
    const auto &p = s.pose;
    if (p.refusal > GC_REFUSED_LAST) return false;
    constexpr size_t padding_at = offsetof(gc_pose, refusal) + sizeof(p.refusal);
    if (nonzero(reinterpret_cast<const uint8_t *>(&p) + padding_at,
                offsetof(gc_pose, root_matrix) - padding_at)) return false;
    if (!(s.valid_fields & GC_VALID_POSE)) {
        gc_pose empty{}; empty.refusal = p.refusal;
        return std::memcmp(&p, &empty, sizeof(p)) == 0;
    }
    if (!p.action || !p.body_action || p.hidden > 1 || p.cap_state > 3 || p.eye_state > 8
            || p.hand_state > 5 || p.wing_flutter > 1 || p.model_state > 0x3ff
            || p.grab_position > 3 || (p.punch_state >> 6) > 2 || (p.punch_state & 0x3f) > 5
            || (p.refusal != GC_REFUSED_NONE && p.refusal != GC_REFUSED_NOT_DRAWN)) return false;
    for (unsigned a = 0; a < 3; ++a)
        if (!finite(p.position[a]) || !scale(p.scale[a])) return false;
    // Hidden Mario is never evaluated: disappeared can retain frame -1.
    if (p.animation_id < (p.hidden ? -1 : 0) || p.animation_id > 208
            || p.animation_frame < (p.hidden ? -1 : 0)
            || (!p.hidden && p.anim_timer != s.area_update_counter)) return false;
    if (p.root_kind == GC_ROOT_TRS) return !nonzero(p.root_matrix, sizeof(p.root_matrix));
    if (p.root_kind != GC_ROOT_SCALED_MODELVIEW || !(s.valid_fields & GC_VALID_CAMERA)) return false;
    // Capture bounds both world/camera vectors to1e7. A1e8 matrix envelope
    // leaves room for their rotated difference without permitting float-scale
    // attacks. The actual local model still validates animation frame counts.
    for (float value : p.root_matrix) if (!finite(value, 100000000.0f)) return false;
    return p.root_matrix[3] == 0 && p.root_matrix[7] == 0
        && p.root_matrix[11] == 0 && p.root_matrix[15] == 1;
}
bool camera_valid(const gc_sample &s) noexcept {
    const auto &c = s.camera;
    if (!(s.valid_fields & GC_VALID_CAMERA)) return !nonzero(&c, sizeof(c));
    double distance = 0;
    for (unsigned a = 0; a < 3; ++a) {
        if (!finite(c.position[a]) || !finite(c.focus[a])) return false;
        const double delta = double(c.position[a]) - c.focus[a];
        distance += delta * delta;
    }
    return distance >= 0.000001 && finite(c.fov, 179.0f) && c.fov > 0
        && c.near_plane > 0 && c.far_plane > c.near_plane;
}
bool clock_valid(const gc_sample &s) noexcept {
    const auto &c = s.clock;
    if ((c.valid & ~7u) || (c.refusal & ~1023u) || c.transition > GCC_TELEPORT) return false;
    const uint32_t required = GC_VALID_CLOCK | GC_VALID_ACTION | GC_VALID_WARP;
    if (c.valid & GCC_RAW_VALID) {
        if ((s.valid_fields & required) != required || c.raw_igt != s.igt || !c.leg_id) return false;
    } else if (c.valid || c.raw_igt || c.origin_id || c.leg_id) return false;
    if (c.valid & GCC_PROGRESS_VALID) {
        if (c.refusal || !c.origin_id || c.banked_frames > std::numeric_limits<uint64_t>::max() - c.raw_igt
                || c.progress_frames != c.banked_frames + c.raw_igt) return false;
    } else if (c.banked_frames || c.progress_frames) return false;
    if (c.valid & GCC_ELAPSED_VALID) return c.continuity_id != 0;
    return !c.elapsed_frames && !c.continuity_id;
}
bool held_valid(const gc_sample &s, uint32_t layout) noexcept {
    const auto &h = s.held;
    if (!(s.valid_fields & GC_VALID_HELD)) return !nonzero(&h, sizeof(h));
    if (!(s.valid_fields & GC_VALID_POSE) || (h.behavior >> 16) != 0x1300u || h.anim_state > 255
            || h.animation_id < -1 || h.animation_frame < 0 || (h.animation_id == -1 && h.animation_frame)
            || h.released > 1 || (!h.released && (h.angles[0] || h.angles[1] || h.angles[2]))) return false;
    const auto symbol = ghost_behaviours::symbol(layout, h.behavior);
    const auto animations = ghost_behaviours::animations(symbol);
    if (h.animation_id >= 0 && uint32_t(h.animation_id) >= animations) return false;
    for (unsigned a = 0; a < 3; ++a)
        if (!finite(h.holp[a]) || !scale(h.scale[a])) return false;
    return true;
}
DecodeResult refused(DecodeResult result, glive_value &out) noexcept {
    std::memset(&out, 0, sizeof(out)); return result;
}
}

DecodeResult decode(const void *packet, size_t original_bytes,
                    const uint8_t expected_incarnation[16], glive_value &out) noexcept {
    // Do not read even the header from a short/oversized original datagram.
    if (original_bytes != GLIVE_PACKET_BYTES) return refused(DecodeResult::bad_size, out);
    if (!packet || !expected_incarnation || !nonzero(expected_incarnation, 16))
        return refused(DecodeResult::bad_argument, out);
    glive_packet wire{};
    std::memcpy(&wire, packet, sizeof(wire)); // bounded, works for unaligned input
    const auto &h = wire.header;
    if (std::memcmp(h.magic, GLIVE_MAGIC, sizeof(h.magic)) || h.version != GLIVE_VERSION
            || h.bytes != GLIVE_PACKET_BYTES || !ghost_behaviours::supported_layout(h.rom_layout)
            || !h.configuration || !h.rom_generation || !h.source_generation || !h.continuity
            || !nonzero(h.participant_incarnation, sizeof(h.participant_incarnation)))
        return refused(DecodeResult::bad_header, out);
    if (std::memcmp(h.participant_incarnation, expected_incarnation, 16))
        return refused(DecodeResult::wrong_incarnation, out);
    glive_value value{};
    value.header = h;
    std::memcpy(&value.sample, wire.core, sizeof(wire.core));
    value.sample.held = wire.held; value.sample.pad = wire.pad;
    const auto &s = value.sample;
    if (s.bytes != sizeof(s) || s.version != GC_VERSION || !s.epoch || !s.sequence || !s.dlist_serial
            || s.qpc < 0 || (s.valid_fields & ~uint32_t(GLIVE_VALID_FIELDS)) || s.status > GC_UNSUPPORTED_HEAD
            || (!s.status && (s.valid_fields & GC_VALID_REQUIRED) != GC_VALID_REQUIRED)
            || ((s.valid_fields & GC_VALID_CLOCK) && (s.level < 4 || s.level > 36 || s.area < 1 || s.area > 8))
            || bool(s.valid_fields & GC_VALID_ACTION) != bool(s.live_action)
            || (!(s.valid_fields & GC_VALID_WARP) && (s.pending_warp_op || s.delayed_warp_timer)))
        return refused(DecodeResult::bad_sample, out);
    if (!pose_valid(s)) return refused(DecodeResult::bad_pose, out);
    if (!camera_valid(s)) return refused(DecodeResult::bad_camera, out);
    if (!clock_valid(s)) return refused(DecodeResult::bad_clock, out);
    if (!held_valid(s, h.rom_layout)) return refused(DecodeResult::bad_held, out);
    if (!ghost_annotation::valid_pad(s)) return refused(DecodeResult::bad_pad, out);
    out = value;
    return DecodeResult::ok;
}
}
