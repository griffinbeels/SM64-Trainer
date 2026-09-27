/* One formatter for production logging and the CPU diagnostic witness. */
#pragma once
/* Milliseconds from the previous nonzero stamp; 0 when this step did not run. */
static double gd_step_ms(uint64_t *previous, uint64_t stamp, uint64_t frequency) {
    if (!stamp || !*previous || !frequency || stamp < *previous) return 0.0;
    const double ms = 1000.0 * (double)(stamp - *previous) / (double)frequency;
    *previous = stamp;
    return ms;
}
/* The startup notes (gd_diagnostic.kind): one line each, all copied to the
 * activation sidecar. Absolute QPCs let a callback_stall's begin/end be
 * placed against each step (docs/replay-gpu-runtime.md, readout rule). */
static void gd_log_note(const gd_diagnostic *s) {
    const uint64_t f = s->qpc_frequency;
    if (s->kind == GD_KIND_REQUEST) {
        /* Every slow callback for the next 15 s is logged unthrottled. */
        diagnostics_watch_stalls((int64_t)s->prepare.request_qpc);
        plugin_logf("gpu_request", "epoch=%u request_qpc=%llu frequency=%llu", s->epoch,
            s->prepare.request_qpc, f);
    } else if (s->kind == GD_KIND_PREPARE) {
        const gd_prepare_diagnostic *p = &s->prepare;
        uint64_t at = p->context_begin;
        const double window = gd_step_ms(&at, p->context_window, f);
        const double temporary = gd_step_ms(&at, p->context_temporary, f);
        const double shared = gd_step_ms(&at, p->context_shared, f);
        const double deleted = gd_step_ms(&at, p->context_temporary_deleted, f);
        const double current = gd_step_ms(&at, p->context_current, f);
        const double snapshots = gd_step_ms(&at, p->snapshots, f);
        const double sampler = gd_step_ms(&at, p->sampler, f);
        at = p->bridge_begin;
        const double device = gd_step_ms(&at, p->bridge_device, f);
        const double opened = gd_step_ms(&at, p->bridge_opened, f);
        const double transfer = gd_step_ms(&at, p->bridge_transfer, f);
        const double registered = gd_step_ms(&at, p->bridge_registered, f);
        plugin_logf("gpu_prepare", "epoch=%u request_qpc=%llu surface_qpc=%llu ctx_begin_qpc=%llu "
            "ctx_window_ms=%.3f ctx_temp_ms=%.3f ctx_shared_ms=%.3f ctx_temp_delete_ms=%.3f "
            "ctx_current_ms=%.3f snapshots_ms=%.3f sampler_ms=%.3f bridge_begin_qpc=%llu d3d_ms=%.3f "
            "dxopen_ms=%.3f transfer_ms=%.3f register_ms=%.3f end_qpc=%llu frequency=%llu",
            s->epoch, p->request_qpc, p->surface_qpc, p->context_begin, window, temporary, shared,
            deleted, current, snapshots, sampler, p->bridge_begin, device, opened, transfer,
            registered, p->end_qpc, f);
    } else if (s->kind == GD_KIND_WARM) {
        const gd_warm_diagnostic *w = &s->warm;
        uint64_t at = w->begin_qpc;
        const double legacy = gd_step_ms(&at, w->legacy_qpc, f);
        const double core = gd_step_ms(&at, w->core_qpc, f);
        const double d3d = gd_step_ms(&at, w->d3d_qpc, f);
        plugin_logf("gpu_warm", "parts=%u begin_qpc=%llu legacy_ms=%.3f core_ms=%.3f d3d_ms=%.3f "
            "end_qpc=%llu total_ms=%.3f error=%u frequency=%llu", w->parts, w->begin_qpc, legacy,
            core, d3d, w->end_qpc, f && w->end_qpc >= w->begin_qpc
                ? 1000.0 * (double)(w->end_qpc - w->begin_qpc) / (double)f : 0.0, w->error, f);
    } else if (s->kind == GD_KIND_ANCHOR) {
        plugin_logf("anchor_published", "begin_qpc=%llu end_qpc=%llu ms=%.3f frequency=%llu",
            s->anchor_begin_qpc, s->anchor_end_qpc,
            f && s->anchor_end_qpc >= s->anchor_begin_qpc && s->anchor_begin_qpc
                ? 1000.0 * (double)(s->anchor_end_qpc - s->anchor_begin_qpc) / (double)f : 0.0, f);
    }
}
static void __cdecl runtime_diagnostic(const gd_diagnostic *s) {
    static const char *phases[GD_PHASE_COUNT] = {
        "wait", "take", "sample", "decisions", "copy", "bridge_returns", "reap", "frontier"
    };
    sa_refusal source;
    if (!s || s->bytes != sizeof *s || s->version != GD_DIAGNOSTIC_VERSION) return;
    if (s->kind != GD_KIND_WINDOW) { gd_log_note(s); return; }
    /* Delivery worker only. At most ten bounded queue admissions per healthy
     * window (a minute, or five seconds when something needs attention); the
     * try-only logger owns file I/O and rotation. */
    if (s->healthy) plugin_logf("gpu_summary", "epoch=%u observed_qpc=%llu frequency=%llu "
        "window_started_qpc=%llu previous_emit_ticks=%llu source=%u retirements=%u bridges=%u "
        "unpublished=%u returning=%u decisions=%u copies=%u unsampled=%u accounted=%llu token=%llu "
        "oldest=%llu dropped_records=%ld snapshot_bytes=%llu bridge_bytes=%llu "
        "sample_calls=%llu sample_reuses=%llu publish_busy=%llu refused=%llu "
        "peak_pending=%u slots=%u peak_bridges=%u",
        s->epoch,s->observed_qpc,s->qpc_frequency,s->window_started_qpc,s->previous_emit_ticks,
        s->source_records,s->offer_retirements,s->bridge_credits,s->unpublished_bridges,
        s->returning_records,s->awaiting_decisions,s->awaiting_copy,s->unsampled,
        s->accounted_occurrence,s->published_token,s->oldest_admitted_qpc,(long)g_log_dropped,
        s->snapshot_bytes,s->bridge_bytes,s->sample_calls,s->sample_reuses,s->publish_busy,
        s->refused_pictures,s->peak_pending,s->offer_count,s->peak_bridges);
    else plugin_logf("gpu_failure", "epoch=%u reason=%u detail=%u observed_qpc=%llu frequency=%llu "
        "phase=%u phase_started=%llu source=%u retirements=%u bridges=%u unpublished=%u "
        "returning=%u decisions=%u copies=%u unsampled=%u accounted=%llu token=%llu oldest=%llu",
        s->epoch, s->reason, s->detail, s->observed_qpc, s->qpc_frequency,
        s->current_phase, s->phase_started_qpc, s->source_records, s->offer_retirements,
        s->bridge_credits, s->unpublished_bridges, s->returning_records, s->awaiting_decisions,
        s->awaiting_copy, s->unsampled, s->accounted_occurrence, s->published_token, s->oldest_admitted_qpc);
    if (!s->healthy && sa_read_refusal(&source) && source.epoch == s->epoch) {
        plugin_logf("gpu_source_refusal", "epoch=%u first_qpc=%lld cause=%u committed=%llu "
            "full=%u busy=%u baseline_full=%u baseline_busy=%u offered=%u observed=%u",
            source.epoch, source.qpc, source.cause, source.committed_occurrence,
            source.refused_full, source.refused_busy, source.baseline_full, source.baseline_busy,
            source.offered, source.observed);
    } else if (!s->healthy) plugin_logf("gpu_source_refusal", "epoch=%u first_refusal=unavailable", s->epoch);
    for (unsigned n = 0; n < GD_PHASE_COUNT; ++n) {
        const gd_phase_diagnostic *p = &s->phases[n];
        if (p->calls) plugin_logf(s->healthy?"gpu_summary_phase":"gpu_phase", "epoch=%u phase=%s calls=%llu total_ticks=%llu "
            "max_ticks=%llu max_started_qpc=%llu observed_qpc=%llu", s->epoch, phases[n], p->calls,
            p->total_ticks, p->max_ticks, p->max_started_qpc,s->observed_qpc);
    }
    const gd_cadence_diagnostic *c=&s->cadence;
    plugin_logf(s->healthy?"gpu_summary_cadence":"gpu_cadence",
        "epoch=%u records=%llu pictures=%llu same_origin=%llu invalid=%llu intervals=%llu "
        "total_ticks=%llu min_ticks=%llu max_ticks=%llu max_started_qpc=%llu "
        "b0=%llu b1=%llu b2=%llu b3=%llu b4=%llu b5=%llu observed_qpc=%llu",
        s->epoch,c->records,c->pictures,c->same_origin,c->invalid_timestamps,c->intervals.calls,
        c->intervals.total_ticks,c->min_ticks,c->intervals.max_ticks,c->intervals.max_started_qpc,
        c->buckets[0],c->buckets[1],c->buckets[2],c->buckets[3],c->buckets[4],c->buckets[5],s->observed_qpc);
}
