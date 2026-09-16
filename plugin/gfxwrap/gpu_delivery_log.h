/* One formatter for production logging and the CPU diagnostic witness. */
#pragma once
static void __cdecl runtime_diagnostic(const gd_diagnostic *s) {
    static const char *phases[GD_PHASE_COUNT] = {
        "wait", "take", "sample", "decisions", "copy", "bridge_returns", "reap", "frontier"
    };
    sa_refusal source;
    if (!s || s->bytes != sizeof *s || s->version != GD_DIAGNOSTIC_VERSION) return;
    /* Delivery worker only. At most ten bounded queue admissions per five-second
     * healthy window; the existing try-only logger owns file I/O and rotation. */
    if (s->healthy) plugin_logf("gpu_summary", "epoch=%u observed_qpc=%llu frequency=%llu "
        "window_started_qpc=%llu previous_emit_ticks=%llu source=%u retirements=%u bridges=%u "
        "unpublished=%u returning=%u decisions=%u copies=%u unsampled=%u accounted=%llu token=%llu "
        "oldest=%llu dropped_records=%ld snapshot_bytes=%llu bridge_bytes=%llu "
        "sample_calls=%llu sample_reuses=%llu publish_busy=%llu refused=%llu",
        s->epoch,s->observed_qpc,s->qpc_frequency,s->window_started_qpc,s->previous_emit_ticks,
        s->source_records,s->offer_retirements,s->bridge_credits,s->unpublished_bridges,
        s->returning_records,s->awaiting_decisions,s->awaiting_copy,s->unsampled,
        s->accounted_occurrence,s->published_token,s->oldest_admitted_qpc,(long)g_log_dropped,
        s->snapshot_bytes,s->bridge_bytes,s->sample_calls,s->sample_reuses,s->publish_busy,
        s->refused_pictures);
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
