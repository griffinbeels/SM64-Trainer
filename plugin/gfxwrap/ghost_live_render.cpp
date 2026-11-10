#include "ghost_live_render.h"
#include "ghost_scene.h"
#include <windows.h>
#include <cmath>

namespace ghost_live {
namespace {
bool binding_valid(const ModelBinding &value) noexcept {
    return value.model && value.model_id && value.rom_generation && value.configuration && value.context_serial;
}
bool local_valid(const gc_sample &value, uint32_t required) noexcept {
    return value.bytes == sizeof(value) && value.version == GC_VERSION && value.epoch
        && value.sequence && value.dlist_serial && value.qpc >= 0
        && value.level >= 4 && value.level <= 36 && value.area >= 1 && value.area <= 8
        && (value.valid_fields & required) == required;
}
void clear_output(ghost_compositor::Pose *out) noexcept {
    if (out) for (unsigned i = 0; i < GLIVE_MAX_PEERS; ++i) out[i] = {};
}
}
bool Renderer::owner(bool claim) noexcept {
    const uint32_t thread = GetCurrentThreadId();
    auto expected = uint32_t(0);
    if (claim && owner_thread_.compare_exchange_strong(expected, thread, std::memory_order_acq_rel,
                                                       std::memory_order_acquire)) return true;
    return owner_thread_.load(std::memory_order_acquire) == thread;
}
bool Renderer::owner() const noexcept {
    return owner_thread_.load(std::memory_order_acquire) == GetCurrentThreadId();
}
void Renderer::invalidate() noexcept {
    included_ = 0;
    drawn_ = {};
}
bool Renderer::advance(uint64_t &value) noexcept {
    if (exhausted_) return false;
    if (value == UINT64_MAX) {
        exhausted_ = true; active_ = false; binding_ = {}; selected_ = 0; invalidate();
        return false;
    }
    ++value; return true;
}
bool Renderer::begin(const ModelBinding &binding, const gc_sample &local, uint64_t now_ns) noexcept {
    if (!owner(true)) return false;
    invalidate(); active_ = false; selected_ = 0; binding_ = {};
    if (!advance(boundary_)) return false;
    if (!binding_valid(binding) || !local_valid(local, GC_VALID_CLOCK)) return false;
    if (now_ns < begun_ns_ || now_ns < evaluated_ns_) return false;
    begun_ns_ = now_ns; binding_ = binding; local_ = local;
    input_.consume(adopted_, now_ns);
    active_ = true;
    for (unsigned seat = 0; seat < GLIVE_MAX_PEERS; ++seat) {
        const auto &value = adopted_.seats[seat];
        if (value.visible && input_.current(adopted_, seat, now_ns)
                && ghost_scene::compatible(value.value.sample, local)) selected_ |= 1u << seat;
    }
    return plan();
}
bool Renderer::plan() const noexcept {
    if (!owner() || !active_ || exhausted_) return false;
    for (unsigned seat = 0; seat < GLIVE_MAX_PEERS; ++seat)
        if ((selected_ & (1u << seat)) && input_.current(adopted_, seat, begun_ns_)) return true;
    return false;
}
uint32_t Renderer::available_mask() const noexcept {
    if(!owner() || !active_)return 0;
    uint32_t mask=0;
    for(unsigned i=0;i<GLIVE_MAX_PEERS;++i)
        if(adopted_.seats[i].visible && input_.current(adopted_,i,begun_ns_))mask|=1u<<i;
    return mask;
}
bool Renderer::evaluate(const gc_sample &local, const ghost::WorldPass &world, uint64_t now_ns,
                        float opacity, ghost_compositor::Pose *out, uint32_t &count,
                        DrawToken &token) noexcept {
    count = 0; token = {}; clear_output(out);
    if (!owner()) return false;
    invalidate();
    if (!advance(evaluation_)) return false;
    if (!out || !active_ || !binding_valid(binding_) || now_ns < begun_ns_ || now_ns < evaluated_ns_
            || !std::isfinite(opacity) || opacity < 0 || opacity > 1
            || !local_valid(local, GC_VALID_CLOCK | GC_VALID_CAMERA)
            || local.epoch != local_.epoch || local.sequence != local_.sequence
            || local.dlist_serial != local_.dlist_serial || world.epoch != local.epoch
            || world.list_serial != local.dlist_serial || world.context_generation != binding_.context_serial)
        return false;
    drawn_ = adopted_; evaluated_ns_ = now_ns;
    uint32_t out_seats[GLIVE_MAX_PEERS]{};
    for (unsigned seat = 0; seat < GLIVE_MAX_PEERS; ++seat) {
        if (!(selected_ & (1u << seat)) || !drawn_.seats[seat].visible
                || !input_.current(drawn_, seat, now_ns)) continue;
        const auto &value = drawn_.seats[seat].value;
        gs_record record{};
        record.bytes = sizeof(record); record.version = GS_VERSION;
        record.configuration = value.header.configuration; record.sample = value.sample;
        ghost_compositor::Pose pose{};
        if (ghost_pose::evaluate(binding_.model, record, local, world, opacity, scratch_[seat], pose,
                    value.header.rom_layout) != ghost_pose::Result::ok
                || !pose.count || pose.count > SG_MODEL_MAX_VERTICES || !pose.vertices) continue;
        // Body/held-object geometry only. No remote cosmetic/audio/annotation owner.
        pose.shadow = {}; pose.particles = {}; pose.annotation = {};
        out_seats[count] = seat; out[count++] = pose;
    }
    // Authority can be withdrawn while another peer is being evaluated. Keep
    // only current survivors before returning any candidate to the compositor.
    uint32_t surviving = 0;
    for (uint32_t at = 0; at < count; ++at) {
        const auto seat = out_seats[at];
        if (!input_.current(drawn_, seat, now_ns)) continue;
        out[surviving++] = out[at]; included_ |= 1u << seat;
    }
    count = surviving;
    for (unsigned at = count; at < GLIVE_MAX_PEERS; ++at) out[at] = {};
    if (!count) { invalidate(); return false; }
    token.owner = this; token.boundary = boundary_; token.evaluation = evaluation_;
    token.included_seats = included_;
    return true;
}
bool Renderer::current(const DrawToken &token, uint64_t now_ns) const noexcept {
    if (!owner() || !active_ || exhausted_ || !binding_valid(binding_) || token.owner != this
            || !included_ || token.included_seats != included_ || token.boundary != boundary_
            || token.evaluation != evaluation_ || now_ns < evaluated_ns_) return false;
    for (unsigned seat = 0; seat < GLIVE_MAX_PEERS; ++seat)
        if ((included_ & (1u << seat)) && (!drawn_.seats[seat].visible
                || !input_.current(drawn_, seat, now_ns))) return false;
    return true;
}
bool Renderer::source(const DrawToken &token, unsigned seat, gc_sample &out) const noexcept {
    out = {};
    if (!owner() || seat >= GLIVE_MAX_PEERS || !(token.included_seats & (1u << seat))
            || !current(token, evaluated_ns_)) return false;
    out = drawn_.seats[seat].value.sample;
    return true;
}
void Renderer::retire() noexcept {
    if (!owner()) return;
    invalidate(); active_ = false; selected_ = 0; binding_ = {};
    advance(boundary_);
}
bool Renderer::receipt(const DrawToken &token,unsigned seat,uint64_t &gate,uint64_t &received_ns) const noexcept {
    gate=received_ns=0;
    if(!owner() || seat>=GLIVE_MAX_PEERS || !(token.included_seats&(1u<<seat)) || !current(token,evaluated_ns_))return false;
    gate=drawn_.seats[seat].gate;received_ns=drawn_.seats[seat].received_ns;return true;
}
}
