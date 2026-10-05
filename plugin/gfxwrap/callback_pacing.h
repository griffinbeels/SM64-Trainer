/* Producer-side callback coverage, not a monitor/display clock. No sampling,
 * allocation, lock, logging or GPU call: every completed callback is counted.
 * A reset breaks the predecessor chain without discarding pending evidence. */
#pragma once
#include <stdint.h>
#include <string.h>
#define CP_BINS 12u
typedef struct {
    uint64_t count, total_ticks, max_ticks, max_started_qpc;
    uint64_t bins[CP_BINS];
} cp_distribution;
typedef struct {
    uint64_t frequency, generation, resets, samples, invalid;
    uint64_t previous_begin, first_qpc, last_qpc;
    cp_distribution duration, gap;
} cp_meter;
static void cp_add(cp_distribution *row, uint64_t start, uint64_t ticks, uint64_t frequency) {
    /* Refresh-sized tails, including sub-20 ms work previously hidden by
     * the callback-stall threshold. Bucket bounds are exclusive. */
    static const uint32_t us[] = {10000,16667,25000,33334,40000,50000,75000,100000,125000,250000,500000};
    unsigned bin = 0;
    const double micros = (double)ticks * 1000000.0 / (double)frequency;
    while (bin + 1 < CP_BINS && micros >= us[bin]) ++bin;
    ++row->count; row->total_ticks += ticks; ++row->bins[bin];
    if (row->count == 1 || ticks > row->max_ticks) {
        row->max_ticks = ticks; row->max_started_qpc = start;
    }
}
static void cp_observe(cp_meter *meter, uint64_t begin, uint64_t end, uint64_t frequency) {
    ++meter->samples;
    if (!frequency || !begin || end < begin || (meter->previous_begin && begin < meter->previous_begin)) {
        ++meter->invalid; meter->previous_begin = 0; return;
    }
    meter->frequency = frequency;
    if (!meter->generation) meter->generation = 1;
    if (!meter->first_qpc) meter->first_qpc = begin;
    if (meter->previous_begin) cp_add(&meter->gap, meter->previous_begin, begin - meter->previous_begin, frequency);
    cp_add(&meter->duration, begin, end - begin, frequency);
    meter->previous_begin = begin; meter->last_qpc = end;
}
static void cp_reset(cp_meter *meter) {
    ++meter->generation; ++meter->resets; meter->previous_begin = 0;
}
static void cp_window_sent(cp_meter *meter) {
    /* Lifetime sample/invalid/reset counts and the predecessor survive. A
     * gap spanning two reports is measured once, in the newer window. */
    memset(&meter->duration, 0, sizeof meter->duration);
    memset(&meter->gap, 0, sizeof meter->gap);
    meter->first_qpc = 0;
}
