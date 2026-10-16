/* Included after the existing writer primitives. Structured snapshots only:
 * formatting and file I/O run on its writer, never the renderer thread. */
#pragma once
#define CP_SNAPSHOTS 16u
typedef struct {
    char callback[32];
    DWORD submitter_tid;
    uint64_t observed_qpc;
    cp_meter meter;
} cp_snapshot;
static SRWLOCK g_pacing_lock = SRWLOCK_INIT;
static cp_snapshot g_pacing_queue[CP_SNAPSHOTS];
static unsigned g_pacing_read, g_pacing_count;
static volatile LONG g_pacing_busy;

static int callback_pacing_send(const char *name, cp_meter *meter, uint64_t observed) {
    int accepted = 0;
    if (!TryAcquireSRWLockShared(&g_log_lifetime)) goto busy;
    log_session_t *session = g_log;
    if (session && TryAcquireSRWLockExclusive(&g_pacing_lock)) {
        if (g_pacing_count < CP_SNAPSHOTS) {
            cp_snapshot *snapshot = &g_pacing_queue[(g_pacing_read + g_pacing_count) % CP_SNAPSHOTS];
            strncpy_s(snapshot->callback, sizeof snapshot->callback, name, _TRUNCATE);
            snapshot->submitter_tid = GetCurrentThreadId();
            snapshot->observed_qpc = observed; snapshot->meter = *meter;
            ++g_pacing_count; accepted = 1; SetEvent(session->event);
        }
        ReleaseSRWLockExclusive(&g_pacing_lock);
    }
    ReleaseSRWLockShared(&g_log_lifetime);
    if (accepted) { cp_window_sent(meter); return 1; }
busy:
    /* No sample is discarded: retry the enlarged window at the next callback.
     * This counter is snapshot contention, distinct from lost log records. */
    InterlockedIncrement(&g_pacing_busy);
    return 0;
}
static void callback_cpu_format(char *line, size_t capacity, size_t *used,
                                const char *prefix, const cp_cpu_pair *pair) {
    if (*used >= capacity) return;
    const int count = snprintf(line + *used, capacity - *used,
        "%s_cpu_begin_qpc=%llu %s_cpu_end_qpc=%llu %s_cpu_generation=%llu "
        "%s_cpu_tid=%u %s_cpu_valid=%u %s_cpu_reason=%u %s_cpu_error=%u "
        "%s_cpu_100ns=%llu %s_thread_cycles=%llu %s_cpu_aperture_ticks=%llu ",
        prefix,pair->begin_qpc,prefix,pair->end_qpc,prefix,pair->generation,
        prefix,pair->tid,prefix,pair->valid,prefix,pair->reason,prefix,pair->error,
        prefix,pair->cpu_100ns,prefix,pair->cycles,prefix,pair->aperture_ticks);
    if (count < 0 || (size_t)count >= capacity - *used) *used = capacity;
    else *used += (size_t)count;
}
static void callback_pacing_drain(log_session_t *session) {
    for (;;) {
        cp_snapshot snapshot;
        AcquireSRWLockExclusive(&g_pacing_lock);
        if (!g_pacing_count) { ReleaseSRWLockExclusive(&g_pacing_lock); return; }
        snapshot = g_pacing_queue[g_pacing_read];
        g_pacing_read = (g_pacing_read + 1) % CP_SNAPSHOTS; --g_pacing_count;
        ReleaseSRWLockExclusive(&g_pacing_lock);
        SYSTEMTIME at; GetSystemTime(&at);
        const cp_meter *m = &snapshot.meter;
        const cp_distribution *gap = &m->gap, *duration = &m->duration;
        /* Fixed upper bound also fits lifetime counters at UINT64_MAX. */
        char line[4096];
        const int count = snprintf(line, sizeof line,
            "%04u-%02u-%02uT%02u:%02u:%02u.%03uZ pid=%lu tid=%lu tid_role=submitter build=%s event=callback_pacing "
            "version=2 coverage=callback callback=%s frequency=%llu observed_qpc=%llu "
            "first_qpc=%llu last_qpc=%llu generation=%llu resets=%llu samples=%llu invalid=%llu "
            "snapshot_busy=%ld dropped_records=%ld durations=%llu duration_max_ticks=%llu duration_max_started_qpc=%llu "
            "gaps=%llu gap_max_ticks=%llu gap_max_started_qpc=%llu "
            "cpu_source=thread_boundary cpu_time_precision=quantized cycles_unit=raw "
            "cpu_samples=%llu cpu_invalid=%llu cpu_thread_changes=%llu cpu_commit_busy=%llu "
            "gap_bins=%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu "
            "duration_bins=%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu ",
            at.wYear,at.wMonth,at.wDay,at.wHour,at.wMinute,at.wSecond,at.wMilliseconds,
            (unsigned long)GetCurrentProcessId(),(unsigned long)snapshot.submitter_tid,GFXWRAP_BUILD_ID,
            snapshot.callback,m->frequency,snapshot.observed_qpc,m->first_qpc,m->last_qpc,m->generation,m->resets,
            m->samples,m->invalid,(long)g_pacing_busy,(long)g_log_dropped,
            duration->count,duration->max_ticks,duration->max_started_qpc,gap->count,gap->max_ticks,gap->max_started_qpc,
            m->cpu_samples,m->cpu_invalid,m->cpu_thread_changes,m->cpu_commit_busy,
            gap->bins[0],gap->bins[1],gap->bins[2],gap->bins[3],gap->bins[4],gap->bins[5],
            gap->bins[6],gap->bins[7],gap->bins[8],gap->bins[9],gap->bins[10],gap->bins[11],
            duration->bins[0],duration->bins[1],duration->bins[2],duration->bins[3],duration->bins[4],duration->bins[5],
            duration->bins[6],duration->bins[7],duration->bins[8],duration->bins[9],duration->bins[10],duration->bins[11]);
        size_t used = count < 0 || (size_t)count >= sizeof line ? sizeof line : (size_t)count;
        callback_cpu_format(line,sizeof line,&used,"duration",&duration->max_cpu);
        callback_cpu_format(line,sizeof line,&used,"gap",&gap->max_cpu);
        callback_cpu_format(line,sizeof line,&used,"outside",&m->gap_outside_cpu);
        if (used + 1 >= sizeof line) { InterlockedIncrement(&g_log_dropped); continue; }
        line[used++]='\n'; line[used]=0;
        log_write_line(session, line, 0);
    }
}
static void callback_progress_drain(log_session_t *session);
static void callback_pacing_flush_all(void);
