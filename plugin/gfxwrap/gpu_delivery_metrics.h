/* Delivery-worker-only aggregates. Existing source timestamps cross no new ABI. */
#pragma once
#include "gpu_delivery_diagnostics.h"

namespace delivery_metrics {
inline void record(gd_phase_diagnostic &row,uint64_t start,uint64_t end) {
    if(end<start)return;
    const auto elapsed=end-start;++row.calls;row.total_ticks+=elapsed;
    if(row.calls==1 || elapsed>row.max_ticks){row.max_ticks=elapsed;row.max_started_qpc=start;}
}
inline void cadence(gd_cadence_diagnostic &row,uint64_t previous,uint64_t current,uint64_t frequency) {
    if(!current || (previous && current<previous)){++row.invalid_timestamps;return;}
    if(!previous)return;
    record(row.intervals,previous,current);
    const auto delta=current-previous;
    if(row.intervals.calls==1 || delta<row.min_ticks)row.min_ticks=delta;
    // Absolute buckets describe boundary cadence, not a guessed game-frame rate.
    const double ms=double(delta)/double(frequency)*1000;
    ++row.buckets[ms<12?0:ms<25?1:ms<42?2:ms<75?3:ms<125?4:5];
}
}
