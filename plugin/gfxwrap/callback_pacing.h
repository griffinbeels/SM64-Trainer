/* Producer-side callback coverage, not a monitor/display clock. No sampling,
 * allocation, lock, logging or GPU call: every completed callback is counted.
 * A reset breaks the predecessor chain without discarding pending evidence. */
#pragma once
#include <stdint.h>
#include <string.h>
#define CP_BINS 12u
enum {
    CP_CPU_OK, CP_CPU_NO_SAMPLE, CP_CPU_QUERY_FAILED, CP_CPU_THREAD_CHANGED,
    CP_CPU_COUNTER_REVERSED, CP_CPU_OVERLAP, CP_CPU_CLOCK_INVALID
};
typedef struct {
    uint64_t cycles, kernel_100ns, user_100ns, aperture_ticks;
    uint32_t tid, valid, reason, error;
} cp_cpu_sample;
typedef struct {
    uint64_t begin_qpc, end_qpc, cycles, cpu_100ns, aperture_ticks, generation;
    uint32_t tid, valid, reason, error;
} cp_cpu_pair;
typedef struct {
    uint64_t count, total_ticks, max_ticks, max_started_qpc;
    uint64_t bins[CP_BINS];
    cp_cpu_pair max_cpu;
} cp_distribution;
typedef struct {
    uint64_t frequency, generation, resets, samples, invalid, sent_samples;
    uint64_t previous_begin, first_qpc, last_qpc;
    cp_distribution duration, gap;
    uint64_t previous_end, cpu_samples, cpu_invalid, cpu_thread_changes, cpu_commit_busy;
    cp_cpu_sample previous_cpu_begin, previous_cpu_end;
    cp_cpu_pair gap_outside_cpu;
} cp_meter;
static cp_cpu_pair cp_pair(const cp_cpu_sample *begin, const cp_cpu_sample *end,
                          uint64_t began_qpc, uint64_t ended_qpc) {
    cp_cpu_pair pair = {0};
    pair.begin_qpc = began_qpc; pair.end_qpc = ended_qpc; pair.reason = CP_CPU_NO_SAMPLE;
    if (!begin || !end) return pair;
    pair.tid = end->tid; pair.error = begin->error ? begin->error : end->error;
    pair.aperture_ticks = begin->aperture_ticks > end->aperture_ticks
        ? begin->aperture_ticks : end->aperture_ticks;
    if (!begin->valid || !end->valid) {
        pair.reason = !begin->valid ? begin->reason : end->reason; return pair;
    }
    if (!begin->tid || begin->tid != end->tid) { pair.reason = CP_CPU_THREAD_CHANGED; return pair; }
    if (ended_qpc < began_qpc) { pair.reason = CP_CPU_CLOCK_INVALID; return pair; }
    if (end->cycles < begin->cycles || end->kernel_100ns < begin->kernel_100ns
            || end->user_100ns < begin->user_100ns) {
        pair.reason = CP_CPU_COUNTER_REVERSED; return pair;
    }
    pair.valid = 1; pair.reason = CP_CPU_OK;
    pair.cycles = end->cycles - begin->cycles;
    pair.cpu_100ns = end->kernel_100ns - begin->kernel_100ns + end->user_100ns - begin->user_100ns;
    return pair;
}
static int cp_add(cp_distribution *row, uint64_t start, uint64_t ticks, uint64_t frequency) {
    /* Refresh-sized tails, including sub-20 ms work previously hidden by
     * the callback-stall threshold. Bucket bounds are exclusive. */
    static const uint32_t us[] = {10000,16667,25000,33334,40000,50000,75000,100000,125000,250000,500000};
    unsigned bin = 0;
    const double micros = (double)ticks * 1000000.0 / (double)frequency;
    while (bin + 1 < CP_BINS && micros >= us[bin]) ++bin;
    ++row->count; row->total_ticks += ticks; ++row->bins[bin];
    if (row->count == 1 || ticks > row->max_ticks) {
        row->max_ticks = ticks; row->max_started_qpc = start;
        return 1;
    }
    return 0;
}
static void cp_observe_cpu(cp_meter *meter, uint64_t begin, uint64_t end, uint64_t frequency,
                           const cp_cpu_sample *entry, const cp_cpu_sample *exit) {
    ++meter->samples;
    if (!frequency || !begin || end < begin || (meter->previous_begin && begin < meter->previous_begin)) {
        ++meter->invalid; meter->previous_begin = meter->previous_end = 0;
        memset(&meter->previous_cpu_begin, 0, sizeof meter->previous_cpu_begin);
        memset(&meter->previous_cpu_end, 0, sizeof meter->previous_cpu_end); return;
    }
    meter->frequency = frequency;
    if (!meter->generation) meter->generation = 1;
    if (!meter->first_qpc) meter->first_qpc = begin;
    cp_cpu_pair duration = cp_pair(entry, exit, begin, end);
    duration.generation = meter->generation;
    if (duration.valid) ++meter->cpu_samples; else ++meter->cpu_invalid;
    cp_cpu_sample unavailable = {0};
    unavailable.reason = duration.reason; unavailable.error = duration.error;
    unavailable.tid = duration.tid;
    const cp_cpu_sample *gap_entry = duration.valid ? entry : &unavailable;
    if (meter->previous_begin) {
        cp_cpu_pair gap = cp_pair(&meter->previous_cpu_begin, gap_entry, meter->previous_begin, begin);
        gap.generation = meter->generation;
        if (gap.reason == CP_CPU_THREAD_CHANGED) ++meter->cpu_thread_changes;
        if (cp_add(&meter->gap, meter->previous_begin, begin - meter->previous_begin, frequency)) {
            meter->gap.max_cpu = gap;
            meter->gap_outside_cpu = cp_pair(&meter->previous_cpu_end, gap_entry, meter->previous_end, begin);
            meter->gap_outside_cpu.generation = meter->generation;
        }
    }
    if (cp_add(&meter->duration, begin, end - begin, frequency)) meter->duration.max_cpu = duration;
    /* Failed reads/overlap break both CPU endpoints, without losing wall-time samples.
     * A changed thread invalidates this gap; its successful call starts a new chain. */
    memset(&meter->previous_cpu_begin, 0, sizeof meter->previous_cpu_begin);
    memset(&meter->previous_cpu_end, 0, sizeof meter->previous_cpu_end);
    meter->previous_cpu_begin.reason = meter->previous_cpu_end.reason = CP_CPU_NO_SAMPLE;
    if (duration.valid) { meter->previous_cpu_begin = *entry; meter->previous_cpu_end = *exit; }
    else {
        meter->previous_cpu_begin = meter->previous_cpu_end = unavailable;
    }
    meter->previous_begin = begin; meter->previous_end = end; meter->last_qpc = end;
}
static void cp_observe(cp_meter *meter, uint64_t begin, uint64_t end, uint64_t frequency) {
    cp_observe_cpu(meter, begin, end, frequency, NULL, NULL);
}
static void cp_reset(cp_meter *meter) {
    ++meter->generation; ++meter->resets; meter->previous_begin = meter->previous_end = 0;
    memset(&meter->previous_cpu_begin, 0, sizeof meter->previous_cpu_begin);
    memset(&meter->previous_cpu_end, 0, sizeof meter->previous_cpu_end);
}
static void cp_window_sent(cp_meter *meter) {
    /* Lifetime sample/invalid/reset counts and the predecessor survive. A
     * gap spanning two reports is measured once, in the newer window. */
    memset(&meter->duration, 0, sizeof meter->duration);
    memset(&meter->gap, 0, sizeof meter->gap);
    memset(&meter->gap_outside_cpu, 0, sizeof meter->gap_outside_cpu);
    meter->sent_samples = meter->samples;
    meter->first_qpc = 0;
}
