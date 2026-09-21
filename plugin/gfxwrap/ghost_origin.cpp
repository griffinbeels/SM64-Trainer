#include "ghost_origin.h"
#include "ghost_capture.h"
#include <cmath>

namespace ghost_origin {
void Ledger::clear() {
    configuration_ = epoch_ = count_ = next_ = 0;
    sequence_ = 0;
}
void Ledger::add(uint32_t configuration, const gc_sample &sample) {
    if (!configuration || !sample.epoch || !sample.sequence
            || sample.bytes != sizeof(sample) || sample.version != GC_VERSION) return;
    if (sample.sequence <= sequence_) return;
    if (configuration != configuration_ || sample.epoch != epoch_) {
        clear(); configuration_ = configuration; epoch_ = sample.epoch;
    }
    sequence_ = sample.sequence;
    entries_[next_] = {sample.global_timer, sample.igt, sample.valid_fields,
        sample.clock.valid, sample.qpc, sample.sequence, sample.clock.continuity_id,
        sample.clock.elapsed_frames, sample.clock.origin_id};
    next_ = (next_ + 1) % GO_HISTORY;
    if (count_ < GO_HISTORY) ++count_;
}
go_result Ledger::resolve(const go_marker &marker, uint64_t frequency,
                         int64_t anchor, double utc, uint32_t near_zero,
                         go_origin &out) const {
    out = {};
    if ((marker.kind != GO_STAR && marker.kind != GO_SEGMENT)
            || !marker.configuration || !marker.epoch || !marker.session_id || !marker.arm_id
            || !std::isfinite(marker.utc) || marker.utc <= 0
            || !frequency || anchor < 0 || !std::isfinite(utc) || utc <= 0) return GO_INVALID;
    if (marker.configuration != configuration_ || marker.epoch != epoch_) return GO_MISSING;
    const Entry *first = nullptr;
    for (uint32_t i = 0; i < count_; ++i) {
        const Entry &entry = entries_[(next_ + GO_HISTORY - count_ + i) % GO_HISTORY];
        if (entry.timer != marker.global_timer) continue;
        const double captured = utc + (static_cast<double>(entry.qpc) - static_cast<double>(anchor))
            / static_cast<double>(frequency);
        if (std::abs(captured - marker.utc) > GO_UTC_WINDOW) continue;
        if (!first) first = &entry;
        else if (entry.continuity != first->continuity || entry.elapsed != first->elapsed
                || entry.raw != first->raw || entry.star_generation != first->star_generation
                || entry.clock_valid != first->clock_valid
                || (entry.valid & GC_VALID_CLOCK) != (first->valid & GC_VALID_CLOCK)) return GO_AMBIGUOUS;
    }
    if (!first) return GO_MISSING;
    // The first occurrence owns this frame even when it was refused. A later
    // valid pose or clock must never make that original refusal disappear.
    const uint32_t required = GCC_RAW_VALID | GCC_ELAPSED_VALID;
    if (!(first->valid & GC_VALID_CLOCK) || (first->clock_valid & required) != required || !first->continuity
            || (marker.kind == GO_STAR && first->raw > near_zero)) return GO_INVALID;
    out = {marker.kind, marker.epoch, first->sequence, first->continuity,
        first->elapsed, first->star_generation, first->raw, 0};
    return GO_READY;
}
}
