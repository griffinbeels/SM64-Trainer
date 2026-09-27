/* Process-local CPU diagnostics. This does not extend shared channel/control ABI.
 * The callback runs on the delivery worker: healthy windows (a minute, or five
 * seconds when something went wrong), once per failed epoch before drain, and
 * one note per startup step (`kind`). Healthy windows never erase failure totals.
 * The pointed-to snapshot is borrowed only for the callback's duration. */
#pragma once
#include <stdint.h>
#define GD_DIAGNOSTIC_VERSION 5u
enum gd_diagnostic_kind {
    GD_KIND_WINDOW=0, /* healthy summary or failure, as before */
    GD_KIND_REQUEST,  /* the worker took a capture request: prepare.request_qpc */
    GD_KIND_PREPARE,  /* the epoch's GPU preparation finished: prepare.* */
    GD_KIND_WARM,     /* the driver warm-up at practice ROM open: warm.* */
    GD_KIND_ANCHOR    /* the renderer's shared anchor, timed once per process */
};
/* QPC stamps; zero means the step did not run. Sequential, so each step's
 * duration is its stamp minus the previous nonzero one. */
typedef struct {
    uint64_t request_qpc,surface_qpc;
    uint64_t context_begin,context_window,context_temporary,context_shared,
        context_temporary_deleted,context_current;
    uint64_t snapshots,sampler,bridge_begin,bridge_device,bridge_opened,
        bridge_transfer,bridge_registered,end_qpc;
} gd_prepare_diagnostic;
typedef struct {
    uint64_t begin_qpc,legacy_qpc,core_qpc,d3d_qpc,end_qpc;
    uint32_t parts,error;
} gd_warm_diagnostic;
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
    /* Version 5. Peaks are per healthy window; offer_count is the pool's size. */
    uint32_t kind,peak_pending,peak_bridges,offer_count;
    gd_prepare_diagnostic prepare;
    gd_warm_diagnostic warm;
    uint64_t anchor_begin_qpc,anchor_end_qpc;
} gd_diagnostic;
typedef void (__cdecl *gd_diagnostic_callback)(const gd_diagnostic*);
#ifdef __cplusplus
extern "C" {
#endif
void __cdecl gd_set_diagnostic(gd_diagnostic_callback);
#ifdef __cplusplus
}
#endif
