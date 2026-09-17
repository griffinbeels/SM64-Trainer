/* Process-local CPU diagnostics. This does not extend shared channel/control ABI.
 * The callback runs on the delivery worker: five-second healthy windows or
 * once per failed epoch before drain. Healthy windows never erase failure totals.
 * The pointed-to snapshot is borrowed only for the callback's duration. */
#pragma once
#include <stdint.h>
#define GD_DIAGNOSTIC_VERSION 4u
enum gd_diagnostic_phase {
    GD_PHASE_WAIT=0, GD_PHASE_TAKE, GD_PHASE_SAMPLE, GD_PHASE_DECISIONS,
    GD_PHASE_COPY, GD_PHASE_BRIDGE_RETURNS, GD_PHASE_REAP, GD_PHASE_FRONTIER,
    GD_PHASE_COUNT
};
typedef struct {
    uint64_t calls,total_ticks,max_ticks,max_started_qpc;
} gd_phase_diagnostic;
typedef struct {
    uint64_t records,pictures,same_origin,invalid_timestamps,min_ticks;
    gd_phase_diagnostic intervals;
    uint64_t buckets[6]; /* source intervals: <12, <25, <42, <75, <125, >=125ms */
} gd_cadence_diagnostic;
typedef struct {
    uint32_t bytes,version,epoch,reason,detail,current_phase;
    uint32_t source_records,offer_retirements,bridge_credits,unpublished_bridges;
    uint32_t returning_records,awaiting_decisions,awaiting_copy,unsampled;
    uint64_t qpc_frequency,observed_qpc,phase_started_qpc;
    uint64_t accounted_occurrence,published_token,oldest_admitted_qpc;
    gd_phase_diagnostic phases[GD_PHASE_COUNT];
    uint32_t healthy,reserved;
    uint64_t window_started_qpc,previous_emit_ticks;
    gd_cadence_diagnostic cadence;
    /* Logical allocation bytes, not driver/encoder VRAM; retry totals per epoch. */
    uint64_t snapshot_bytes,bridge_bytes,sample_calls,sample_reuses,publish_busy;
    /* Pictures dropped under pressure (source/pool refusals, stamp-less records). */
    uint64_t refused_pictures;
} gd_diagnostic;
typedef void (__cdecl *gd_diagnostic_callback)(const gd_diagnostic*);
#ifdef __cplusplus
extern "C" {
#endif
void __cdecl gd_set_diagnostic(gd_diagnostic_callback);
#ifdef __cplusplus
}
#endif
