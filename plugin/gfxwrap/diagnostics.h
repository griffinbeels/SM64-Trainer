/* Native evidence remains available when the trainer is closed. Producers
 * never do file I/O or wait for the writer. A full/busy queue loses diagnostics,
 * not game frames. The logger starts only at InitiateGFX, never enumeration.
 * CloseDLL stops it outside loader lock; its module reference keeps a slow
 * writer's instructions valid until FreeLibraryAndExitThread. */
#pragma once
#include <stdarg.h>
#include <stddef.h>
#include "callback_pacing.h"
#ifndef GFXWRAP_BUILD_ID
#define GFXWRAP_BUILD_ID "development-unidentified"
#endif
#define LOG_CAPACITY 64u
#define LOG_LINE_BYTES 1024u
/* The live file plus .1 and .2. Healthy capture writes about 0.15 MB/h
 * (gpu_summary once a minute), so 12 MiB keeps days; a noisy session keeps
 * hours. Tests shrink the sizes to exercise rotation. */
#ifndef LOG_MAX_BYTES
#define LOG_MAX_BYTES (4u * 1024u * 1024u)
#endif
#define LOG_ROTATED 2u
/* sm64_trainer_gfx.activation.log holds only this process's startup and
 * capture-start lines (activation_event), so the main log's rotation cannot
 * take them. The previous Project64 process keeps .1; past the cap, sidecar
 * copies are dropped (the main log still has them). */
#ifndef ACTIVATION_MAX_BYTES
#define ACTIVATION_MAX_BYTES (256u * 1024u)
#endif
/* After a capture request every slow callback is logged, unthrottled, for
 * this long: the first capture's preparation is where a freeze would be. */
#define STALL_WATCH_SECONDS 15
typedef struct {
    SRWLOCK lock;
    HANDLE thread, event;
    HMODULE reference;
    LONG stopping;
    unsigned read, count;
    char lines[LOG_CAPACITY][LOG_LINE_BYTES];
    unsigned char sidecar[LOG_CAPACITY];
    int64_t pacing_progress_last[3]; /* only this session's writer uses these */
    wchar_t path[MAX_PATH], rotated[LOG_ROTATED][MAX_PATH];
    wchar_t activation[MAX_PATH], activation_previous[MAX_PATH];
} log_session_t;
static SRWLOCK g_log_lifetime = SRWLOCK_INIT;
static log_session_t *g_log;
static volatile LONG g_log_dropped;
static volatile LONG g_activation_rotated; /* once per process, by the writer */
static volatile LONG64 g_stall_watch_qpc;  /* capture request; 0 = none yet */
static int64_t g_diagnostic_frequency;

/* The events copied to the activation sidecar. callback_stall joins them
 * only inside the stall watch window. */
static int activation_event(const char *event) {
    static const char *const names[] = {
        "init_result", "rom_open_begin", "rom_open_end", "rom_closed_begin",
        "gpu_request", "gpu_prepare", "gpu_failure",
    };
    for (unsigned n = 0; n < sizeof names / sizeof *names; ++n)
        if (!strcmp(event, names[n])) return 1;
    return 0;
}

static void plugin_log_line(int sidecar, const char *event, const char *format, va_list args) {
    DWORD saved_error = GetLastError();
    /* A shutdown removes the pointer under this lock before the worker frees
     * its session. Try-only acquisition also covers simultaneous lifecycle. */
    if (!TryAcquireSRWLockShared(&g_log_lifetime)) {
        InterlockedIncrement(&g_log_dropped); SetLastError(saved_error); return;
    }
    log_session_t *session = g_log;
    if (!session) { ReleaseSRWLockShared(&g_log_lifetime); SetLastError(saved_error); return; }
    char details[720], line[LOG_LINE_BYTES];
    vsnprintf(details, sizeof details, format, args);
    SYSTEMTIME now;
    GetSystemTime(&now);
    snprintf(line, sizeof line,
        "%04u-%02u-%02uT%02u:%02u:%02u.%03uZ pid=%lu tid=%lu build=%s event=%s %s\n",
        now.wYear, now.wMonth, now.wDay, now.wHour, now.wMinute, now.wSecond,
        now.wMilliseconds, (unsigned long)GetCurrentProcessId(),
        (unsigned long)GetCurrentThreadId(), GFXWRAP_BUILD_ID, event, details);
    if (TryAcquireSRWLockExclusive(&session->lock)) {
        if (session->count < LOG_CAPACITY) {
            const unsigned slot = (session->read + session->count) % LOG_CAPACITY;
            memcpy(session->lines[slot], line, sizeof line);
            session->sidecar[slot] = (unsigned char)(sidecar != 0);
            session->count++;
            SetEvent(session->event);
        } else InterlockedIncrement(&g_log_dropped);
        ReleaseSRWLockExclusive(&session->lock);
    } else InterlockedIncrement(&g_log_dropped);
    ReleaseSRWLockShared(&g_log_lifetime);
    SetLastError(saved_error);
}

static void plugin_logf(const char *event, const char *format, ...) {
    va_list args;
    va_start(args, format);
    plugin_log_line(activation_event(event), event, format, args);
    va_end(args);
}

static void plugin_log_activation(const char *event, const char *format, ...) {
    va_list args;
    va_start(args, format);
    plugin_log_line(1, event, format, args);
    va_end(args);
}

static void plugin_log(const char *message) { plugin_logf("message", "%s", message); }

/* A capture request opens the stall watch window (callback_diagnostic_end). */
static void diagnostics_watch_stalls(int64_t request_qpc) {
    InterlockedExchange64(&g_stall_watch_qpc, (LONG64)request_qpc);
}

static HANDLE log_open_append(const wchar_t *path) {
    return CreateFileW(path, FILE_APPEND_DATA | GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL, OPEN_ALWAYS,
        FILE_ATTRIBUTE_NORMAL, NULL);
}

static void log_append(HANDLE file, const char *line) {
    DWORD written;
    DWORD bytes = (DWORD)strlen(line);
    if (!WriteFile(file, line, bytes, &written, NULL) || written != bytes)
        InterlockedIncrement(&g_log_dropped);
    CloseHandle(file);
}

static void log_write_line(log_session_t *session, const char *line, int sidecar) {
    /* File sharing plus one append WriteFile keeps records from separate PJ64
     * processes intact. Rotation may lose a race; retry on the next record. */
    HANDLE file = log_open_append(session->path);
    if (file == INVALID_HANDLE_VALUE) InterlockedIncrement(&g_log_dropped);
    else {
        LARGE_INTEGER size;
        if (GetFileSizeEx(file, &size) && size.QuadPart >= LOG_MAX_BYTES) {
            CloseHandle(file);
            for (unsigned n = LOG_ROTATED - 1; n > 0; --n)
                MoveFileExW(session->rotated[n - 1], session->rotated[n], MOVEFILE_REPLACE_EXISTING);
            MoveFileExW(session->path, session->rotated[0], MOVEFILE_REPLACE_EXISTING);
            file = log_open_append(session->path);
        }
        if (file == INVALID_HANDLE_VALUE) InterlockedIncrement(&g_log_dropped);
        else log_append(file, line);
    }
    if (!sidecar) return;
    if (!InterlockedExchange(&g_activation_rotated, 1))
        MoveFileExW(session->activation, session->activation_previous, MOVEFILE_REPLACE_EXISTING);
    file = log_open_append(session->activation);
    if (file == INVALID_HANDLE_VALUE) { InterlockedIncrement(&g_log_dropped); return; }
    LARGE_INTEGER size;
    if (GetFileSizeEx(file, &size) && size.QuadPart >= ACTIVATION_MAX_BYTES) {
        CloseHandle(file); InterlockedIncrement(&g_log_dropped); return;
    }
    log_append(file, line);
}

#include "callback_pacing_log.h"

static DWORD WINAPI log_worker(LPVOID parameter) {
    log_session_t *session = parameter;
    for (;;) {
        /* Also notice a callback which never returns. This reads three
         * atomic timestamps, performs no GPU work, and reports at most
         * once per callback per five seconds. Idle/loading is not a defect. */
        WaitForSingleObject(session->event, 250);
        callback_pacing_drain(session);
        callback_progress_drain(session);
        for (;;) {
            char line[LOG_LINE_BYTES];
            int sidecar = 0;
            AcquireSRWLockExclusive(&session->lock);
            BOOL have_line = session->count != 0;
            BOOL stopping = session->stopping != 0;
            if (have_line) {
                memcpy(line, session->lines[session->read], sizeof line);
                sidecar = session->sidecar[session->read];
                session->read = (session->read + 1) % LOG_CAPACITY;
                session->count--;
            }
            ReleaseSRWLockExclusive(&session->lock);
            if (!have_line && stopping) {
                /* Stop detached g_log before setting this flag: no producer
                 * can enqueue now. Final snapshots may have arrived after
                 * this iteration's first drain, while ordinary I/O ran. */
                callback_pacing_drain(session);
                goto finished;
            }
            if (!have_line) break;
            log_write_line(session, line, sidecar);
        }
    }
finished:;
    HMODULE reference = session->reference;
    CloseHandle(session->event);
    HeapFree(GetProcessHeap(), 0, session);
    FreeLibraryAndExitThread(reference, 0);
    return 0;
}

static void diagnostics_start(void) {
    AcquireSRWLockExclusive(&g_log_lifetime);
    if (g_log) { ReleaseSRWLockExclusive(&g_log_lifetime); return; }
    log_session_t *session = HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof *session);
    if (!session) { ReleaseSRWLockExclusive(&g_log_lifetime); return; }
    InitializeSRWLock(&session->lock);
    self_dir(session->path, MAX_PATH);
    wcsncpy_s(session->activation, MAX_PATH, session->path, _TRUNCATE);
    wcsncat_s(session->path, MAX_PATH, L"sm64_trainer_gfx.log", _TRUNCATE);
    for (unsigned n = 0; n < LOG_ROTATED; ++n)
        swprintf_s(session->rotated[n], MAX_PATH, L"%s.%u", session->path, n + 1);
    wcsncat_s(session->activation, MAX_PATH, L"sm64_trainer_gfx.activation.log", _TRUNCATE);
    swprintf_s(session->activation_previous, MAX_PATH, L"%s.1", session->activation);
    LARGE_INTEGER frequency;
    QueryPerformanceFrequency(&frequency);
    g_diagnostic_frequency = frequency.QuadPart;
    session->event = CreateEventW(NULL, FALSE, FALSE, NULL);
    if (!session->event) goto failed;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,
            (LPCWSTR)(uintptr_t)&log_worker, &session->reference)) goto failed;
    session->thread = CreateThread(NULL, 0, log_worker, session, 0, NULL);
    if (!session->thread) { FreeLibrary(session->reference); goto failed; }
    g_log = session;
    ReleaseSRWLockExclusive(&g_log_lifetime);
    return;
failed:
    if (session->event) CloseHandle(session->event);
    HeapFree(GetProcessHeap(), 0, session);
    ReleaseSRWLockExclusive(&g_log_lifetime);
}

static void diagnostics_stop(void) {
    /* Try-only final partial windows, while the writer still has its session.
     * Queued is not persisted: file failures retain their existing counters. */
    callback_pacing_flush_all();
    plugin_logf("diagnostics_stop", "dropped_records=%ld", (long)g_log_dropped);
    AcquireSRWLockExclusive(&g_log_lifetime);
    log_session_t *session = g_log;
    g_log = NULL;
    if (!session) { ReleaseSRWLockExclusive(&g_log_lifetime); return; }
    HANDLE thread = session->thread;
    AcquireSRWLockExclusive(&session->lock);
    session->stopping = 1;
    SetEvent(session->event);
    ReleaseSRWLockExclusive(&session->lock);
    ReleaseSRWLockExclusive(&g_log_lifetime);
    /* Never called by DllMain. Slow storage cannot hang emulator shutdown;
     * the writer retains its own module reference until the queue drains. */
    WaitForSingleObject(thread, 200);
    CloseHandle(thread);
}

static void log_module_path(const char *event, HMODULE module) {
    wchar_t wide[MAX_PATH];
    char path[MAX_PATH * 3];
    DWORD length = GetModuleFileNameW(module, wide, MAX_PATH);
    if (!length || length >= MAX_PATH ||
            !WideCharToMultiByte(CP_UTF8, 0, wide, -1, path, sizeof path, NULL, NULL)) {
        plugin_logf(event, "path_unavailable error=%lu", GetLastError());
        return;
    }
    plugin_logf(event, "path=\"%s\"", path);
}

/* Per-call durations of one summary window in 0.1 ms steps; the last
 * bucket holds everything slower (its p99 then reports the window max). */
#define CALLBACK_BUCKETS 256u
/* Counters bracket callback boundaries, not an exact scheduler trace. CPU time
 * can be coarse/quantized (15.625 ms observed in this machine's controls).
 * Cycles stay raw: the API explicitly forbids converting them to elapsed time.
 * Retain the QPC-to-read aperture so a delayed endpoint cannot look exact. */
static cp_cpu_sample callback_cpu_read(uint64_t boundary_qpc) {
    cp_cpu_sample sample = {0};
    const DWORD saved = GetLastError();
    const HANDLE thread = GetCurrentThread();
    sample.tid = GetCurrentThreadId(); sample.valid = 1;
    if (!QueryThreadCycleTime(thread, &sample.cycles)) {
        sample.valid = 0; sample.reason = CP_CPU_QUERY_FAILED; sample.error = GetLastError();
    }
    FILETIME created, exited, kernel = {0}, user = {0};
    if (!GetThreadTimes(thread, &created, &exited, &kernel, &user)) {
        sample.valid = 0; sample.reason = CP_CPU_QUERY_FAILED;
        if (!sample.error) sample.error = GetLastError();
    }
    sample.kernel_100ns = ((uint64_t)kernel.dwHighDateTime << 32) | kernel.dwLowDateTime;
    sample.user_100ns = ((uint64_t)user.dwHighDateTime << 32) | user.dwLowDateTime;
    LARGE_INTEGER at; QueryPerformanceCounter(&at);
    if ((uint64_t)at.QuadPart >= boundary_qpc) sample.aperture_ticks = (uint64_t)at.QuadPart-boundary_qpc;
    else { sample.valid = 0; sample.reason = CP_CPU_CLOCK_INVALID; }
    SetLastError(saved);
    return sample;
}
typedef struct {
    const char *name;
    uint64_t calls, slow_calls;
    int64_t previous, last_report, last_stall_report;
    /* The current summary window only. */
    uint64_t window_calls;
    int64_t window_total, window_max_total, window_max_gap;
    uint32_t histogram[CALLBACK_BUCKETS];
    cp_meter pacing;
    cp_cpu_sample entry_cpu;
    volatile LONG cpu_owner, cpu_conflicts;
    LONG entry_conflicts;
    int64_t entry_qpc;
    volatile LONG64 in_flight_qpc, completed_qpc;
    SRWLOCK pacing_commit;
    volatile LONG pending_resets;
    volatile LONG64 pacing_busy;
} callback_diagnostic_t;
static callback_diagnostic_t g_diagnostic_list = {"ProcessDList"};
static callback_diagnostic_t g_diagnostic_update = {"UpdateScreen"};
static callback_diagnostic_t g_diagnostic_rdp = {"ProcessRDPList"};

static void callback_diagnostic_reset_locked(callback_diagnostic_t *note) {
    const char *name = note->name;
    cp_meter pacing = note->pacing;
    cp_reset(&pacing);
    memset(note, 0, offsetof(callback_diagnostic_t, pacing));
    /* A reset inside an active call invalidates its CPU endpoints. Leave its
     * owner/entry slot intact until its matching end releases it. */
    InterlockedIncrement(&note->cpu_conflicts);
    InterlockedExchange64(&note->in_flight_qpc,0);
    InterlockedExchange64(&note->completed_qpc,0);
    note->name = name;
    note->pacing = pacing;
}
static void callback_diagnostic_reset(callback_diagnostic_t *note) {
    if (!TryAcquireSRWLockExclusive(&note->pacing_commit)) {
        InterlockedIncrement(&note->pending_resets); return;
    }
    callback_diagnostic_reset_locked(note);
    ReleaseSRWLockExclusive(&note->pacing_commit);
}
static int callback_pacing_prepare(callback_diagnostic_t *note) {
    const LONG resets = InterlockedExchange(&note->pending_resets, 0);
    if (resets) {
        callback_diagnostic_reset_locked(note);
        note->pacing.generation += (uint64_t)resets-1; note->pacing.resets += (uint64_t)resets-1;
    }
    const uint64_t busy = (uint64_t)InterlockedCompareExchange64(&note->pacing_busy, 0, 0);
    const uint64_t missed = busy-note->pacing.cpu_commit_busy;
    if (missed) {
        /* Count contended completions as unknown, without bridging a gap over
         * a missing endpoint or waiting for another producer. */
        note->pacing.samples += missed; note->pacing.invalid += missed;
        note->pacing.cpu_invalid += missed; note->pacing.cpu_commit_busy = busy;
        cp_reset(&note->pacing);
    }
    return resets != 0;
}
static void callback_pacing_flush_all(void) {
    callback_diagnostic_t *notes[] = {&g_diagnostic_list,&g_diagnostic_update,&g_diagnostic_rdp};
    static const char *const names[] = {"ProcessDList","UpdateScreen","ProcessRDPList"};
    LARGE_INTEGER at; QueryPerformanceCounter(&at);
    for (unsigned n=0;n<sizeof notes/sizeof *notes;++n) {
        callback_diagnostic_t *note=notes[n];
        if (!TryAcquireSRWLockExclusive(&note->pacing_commit)) {
            plugin_logf("callback_final_window","version=2 coverage=callback callback=%s frequency=%lld "
                "state=unavailable reason=busy_commit unsent_samples=unknown",names[n],g_diagnostic_frequency);
            continue;
        }
        callback_pacing_prepare(note);
        const uint64_t pending=note->pacing.samples-note->pacing.sent_samples;
        const int in_flight=InterlockedCompareExchange(&note->cpu_owner,0,0)!=0;
        const int queued=pending && callback_pacing_send(names[n],&note->pacing,(uint64_t)at.QuadPart);
        ReleaseSRWLockExclusive(&note->pacing_commit);
        plugin_logf("callback_final_window","version=2 coverage=callback callback=%s frequency=%lld "
            "state=%s queued_samples=%llu unsent_samples=%llu in_flight=%d",
            names[n],g_diagnostic_frequency,pending?(queued?"queued":"unsent"):"empty",
            queued?pending:0,queued?0:pending,in_flight);
    }
}

static int64_t callback_diagnostic_begin(callback_diagnostic_t *note) {
    LARGE_INTEGER at; QueryPerformanceCounter(&at);
#ifndef CALLBACK_PACING_BASELINE_HOST
    if (!g_diagnostic_frequency) return at.QuadPart;
    const LONG tid = (LONG)GetCurrentThreadId();
    const LONG conflicts = InterlockedCompareExchange(&note->cpu_conflicts, 0, 0);
    if (InterlockedCompareExchange(&note->cpu_owner, tid, 0) == 0) {
        note->entry_qpc = at.QuadPart;
        note->entry_conflicts = conflicts;
        note->entry_cpu = callback_cpu_read((uint64_t)at.QuadPart);
    } else InterlockedIncrement(&note->cpu_conflicts);
    InterlockedExchange64(&note->in_flight_qpc, at.QuadPart);
#else
    (void)note; /* CPU host only: measure the original two-QPC/end path. */
#endif
    return at.QuadPart;
}

static void callback_progress_drain(log_session_t *session) {
    callback_diagnostic_t *notes[] = {&g_diagnostic_list,&g_diagnostic_update,&g_diagnostic_rdp};
    static const char *const names[] = {"ProcessDList","UpdateScreen","ProcessRDPList"};
    int64_t *last_report = session->pacing_progress_last;
    LARGE_INTEGER at; QueryPerformanceCounter(&at);
    if (!g_diagnostic_frequency) return;
    for (unsigned n = 0; n < sizeof notes / sizeof *notes; ++n) {
        callback_diagnostic_t *note = notes[n];
        const int64_t begin = InterlockedCompareExchange64(&note->in_flight_qpc,0,0);
        const int64_t completed = InterlockedCompareExchange64(&note->completed_qpc,0,0);
        const int64_t last = begin ? begin : completed;
        if (!last || at.QuadPart < last || at.QuadPart-last < g_diagnostic_frequency/4
                || (last_report[n] && at.QuadPart-last_report[n] < g_diagnostic_frequency*5)) continue;
        last_report[n] = at.QuadPart;
        SYSTEMTIME utc; GetSystemTime(&utc);
        char line[LOG_LINE_BYTES];
        snprintf(line,sizeof line,
            "%04u-%02u-%02uT%02u:%02u:%02u.%03uZ pid=%lu tid=%lu build=%s event=callback_progress "
            "version=1 coverage=callback callback=%s state=%s observed_qpc=%lld frequency=%lld "
            "started_qpc=%lld last_completed_qpc=%lld age_ticks=%lld dropped_records=%ld\n",
            utc.wYear,utc.wMonth,utc.wDay,utc.wHour,utc.wMinute,utc.wSecond,utc.wMilliseconds,
            (unsigned long)GetCurrentProcessId(),(unsigned long)GetCurrentThreadId(),GFXWRAP_BUILD_ID,
            names[n],begin?"in_flight":"no_new_callback",at.QuadPart,g_diagnostic_frequency,
            begin,completed,at.QuadPart-last,(long)g_log_dropped);
        log_write_line(session,line,0);
    }
}

static double callback_ms(int64_t ticks) { return 1000.0 * ticks / g_diagnostic_frequency; }

static double callback_p99_ms(const callback_diagnostic_t *note) {
    const uint64_t rank = note->window_calls - note->window_calls / 100; /* ceil(0.99 n) for n >= 1 */
    uint64_t seen = 0;
    for (unsigned n = 0; n + 1 < CALLBACK_BUCKETS; ++n) {
        seen += note->histogram[n];
        if (seen >= rank) return 0.1 * (n + 1);
    }
    return callback_ms(note->window_max_total);
}

/* total spans the whole forwarded callback, including any stamp adapter work. */
static void callback_diagnostic_end(callback_diagnostic_t *note, int64_t begin, int64_t end) {
    if (!g_diagnostic_frequency) return;
#ifndef CALLBACK_PACING_BASELINE_HOST
    cp_cpu_sample entry = {0}, exit = {0};
    entry.reason = exit.reason = CP_CPU_OVERLAP;
    const LONG tid = (LONG)GetCurrentThreadId();
    const int owns_entry = InterlockedCompareExchange(&note->cpu_owner, 0, 0) == tid && note->entry_qpc == begin;
    if (owns_entry) {
        entry = note->entry_cpu;
        exit = callback_cpu_read((uint64_t)end);
        if (note->entry_conflicts != InterlockedCompareExchange(&note->cpu_conflicts, 0, 0)) {
            entry.valid = exit.valid = 0; entry.reason = exit.reason = CP_CPU_OVERLAP;
        }
    }
    if (TryAcquireSRWLockExclusive(&note->pacing_commit)) {
        if (callback_pacing_prepare(note)) {
            entry.valid = exit.valid = 0; entry.reason = exit.reason = CP_CPU_OVERLAP;
        }
        if (owns_entry && note->entry_conflicts != InterlockedCompareExchange(&note->cpu_conflicts, 0, 0)) {
            entry.valid = exit.valid = 0; entry.reason = exit.reason = CP_CPU_OVERLAP;
        }
        cp_observe_cpu(&note->pacing,(uint64_t)begin,(uint64_t)end,
                       (uint64_t)g_diagnostic_frequency,&entry,&exit);
        if (begin > 0 && end >= begin) {
            const int attention = note->pacing.duration.max_ticks >= (uint64_t)g_diagnostic_frequency/100
                || note->pacing.gap.max_ticks >= (uint64_t)g_diagnostic_frequency/20;
            const uint64_t pacing_span = note->pacing.last_qpc-note->pacing.first_qpc;
            if (pacing_span >= (uint64_t)g_diagnostic_frequency*(attention?5:60))
                callback_pacing_send(note->name,&note->pacing,(uint64_t)end);
        }
        ReleaseSRWLockExclusive(&note->pacing_commit);
    } else InterlockedIncrement64(&note->pacing_busy);
    if (owns_entry) InterlockedExchange(&note->cpu_owner, 0);
    InterlockedExchange64(&note->completed_qpc,end);
    InterlockedExchange64(&note->in_flight_qpc,0);
    if (begin <= 0 || end < begin) return;
#endif
    int64_t gap = note->previous ? begin - note->previous : 0;
    note->previous = begin;
    int64_t total = end - begin;
    note->calls++;
    note->window_calls++;
    note->window_total += total;
    if (total > note->window_max_total) note->window_max_total = total;
    if (gap > note->window_max_gap) note->window_max_gap = gap;
    const int64_t bucket = total * 10000 / g_diagnostic_frequency;
    note->histogram[bucket < CALLBACK_BUCKETS - 1 ? bucket : CALLBACK_BUCKETS - 1]++;
    BOOL slow = total >= g_diagnostic_frequency / 50 || gap >= g_diagnostic_frequency / 20;
    if (slow) note->slow_calls++;
    const int64_t watch = InterlockedCompareExchange64(&g_stall_watch_qpc, 0, 0);
    const BOOL watched = slow && watch && end >= watch
        && end - watch < g_diagnostic_frequency * STALL_WATCH_SECONDS;
    /* First stall immediately, then at most once per five seconds per callback,
     * except inside the stall watch window; a summary once a minute. Gaps also
     * include legitimate emulator pauses. */
    int64_t since_report = end - (slow ? note->last_stall_report : note->last_report);
    if (slow && !watched && note->last_stall_report && since_report < g_diagnostic_frequency * 5) return;
    if (!slow && !note->last_report) { note->last_report = end; return; }
    if (!slow && since_report < g_diagnostic_frequency * 60) return;
    if (slow) {
        (watched ? plugin_log_activation : plugin_logf)("callback_stall",
            "callback=%s calls=%llu slow_calls=%llu total_ms=%.3f gap_ms=%.3f "
            "begin_qpc=%lld end_qpc=%lld watched=%d dropped_records=%ld",
            note->name, (unsigned long long)note->calls, (unsigned long long)note->slow_calls,
            callback_ms(total), callback_ms(gap), (long long)begin, (long long)end, watched,
            (long)g_log_dropped);
        note->last_stall_report = end;
        return;
    }
    plugin_logf("callback_summary",
        "callback=%s calls=%llu slow_calls=%llu window_calls=%llu mean_ms=%.3f p99_ms=%.3f "
        "max_ms=%.3f max_gap_ms=%.3f window_s=%.1f dropped_records=%ld",
        note->name, (unsigned long long)note->calls, (unsigned long long)note->slow_calls,
        (unsigned long long)note->window_calls,
        note->window_calls ? callback_ms(note->window_total) / note->window_calls : 0.0,
        callback_p99_ms(note), callback_ms(note->window_max_total), callback_ms(note->window_max_gap),
        (double)(end - note->last_report) / g_diagnostic_frequency, (long)g_log_dropped);
    note->last_report = end;
    note->window_calls = 0;
    note->window_total = note->window_max_total = note->window_max_gap = 0;
    memset(note->histogram, 0, sizeof note->histogram);
}
