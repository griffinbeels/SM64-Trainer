/* Native evidence remains available when the trainer is closed. Producers
 * never do file I/O or wait for the writer. A full/busy queue loses diagnostics,
 * not game frames. The logger starts only at InitiateGFX, never enumeration.
 * CloseDLL stops it outside loader lock; its module reference keeps a slow
 * writer's instructions valid until FreeLibraryAndExitThread. */
#pragma once
#include <stdarg.h>
#ifndef GFXWRAP_BUILD_ID
#define GFXWRAP_BUILD_ID "development-unidentified"
#endif
#define LOG_CAPACITY 64u
#define LOG_LINE_BYTES 1024u
#define LOG_MAX_BYTES (1024u * 1024u)
typedef struct {
    SRWLOCK lock;
    HANDLE thread, event;
    HMODULE reference;
    LONG stopping;
    unsigned read, count;
    char lines[LOG_CAPACITY][LOG_LINE_BYTES];
    wchar_t path[MAX_PATH], previous[MAX_PATH];
} log_session_t;
static SRWLOCK g_log_lifetime = SRWLOCK_INIT;
static log_session_t *g_log;
static volatile LONG g_log_dropped;
static int64_t g_diagnostic_frequency;

static void plugin_logf(const char *event, const char *format, ...) {
    DWORD saved_error = GetLastError();
    /* A shutdown removes the pointer under this lock before the worker frees
     * its session. Try-only acquisition also covers simultaneous lifecycle. */
    if (!TryAcquireSRWLockShared(&g_log_lifetime)) {
        InterlockedIncrement(&g_log_dropped); SetLastError(saved_error); return;
    }
    log_session_t *session = g_log;
    if (!session) { ReleaseSRWLockShared(&g_log_lifetime); SetLastError(saved_error); return; }
    char details[720], line[LOG_LINE_BYTES];
    va_list args;
    va_start(args, format);
    vsnprintf(details, sizeof details, format, args);
    va_end(args);
    SYSTEMTIME now;
    GetSystemTime(&now);
    snprintf(line, sizeof line,
        "%04u-%02u-%02uT%02u:%02u:%02u.%03uZ pid=%lu tid=%lu build=%s event=%s %s\n",
        now.wYear, now.wMonth, now.wDay, now.wHour, now.wMinute, now.wSecond,
        now.wMilliseconds, (unsigned long)GetCurrentProcessId(),
        (unsigned long)GetCurrentThreadId(), GFXWRAP_BUILD_ID, event, details);
    if (TryAcquireSRWLockExclusive(&session->lock)) {
        if (session->count < LOG_CAPACITY) {
            memcpy(session->lines[(session->read + session->count) % LOG_CAPACITY], line, sizeof line);
            session->count++;
            SetEvent(session->event);
        } else InterlockedIncrement(&g_log_dropped);
        ReleaseSRWLockExclusive(&session->lock);
    } else InterlockedIncrement(&g_log_dropped);
    ReleaseSRWLockShared(&g_log_lifetime);
    SetLastError(saved_error);
}

static void plugin_log(const char *message) { plugin_logf("message", "%s", message); }

static void log_write_line(log_session_t *session, const char *line) {
    /* File sharing plus one append WriteFile keeps records from separate PJ64
     * processes intact. Rotation may lose a race; retry on the next record. */
    HANDLE file = CreateFileW(session->path, FILE_APPEND_DATA | GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL, OPEN_ALWAYS,
        FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) { InterlockedIncrement(&g_log_dropped); return; }
    LARGE_INTEGER size;
    if (GetFileSizeEx(file, &size) && size.QuadPart >= LOG_MAX_BYTES) {
        CloseHandle(file);
        MoveFileExW(session->path, session->previous, MOVEFILE_REPLACE_EXISTING);
        file = CreateFileW(session->path, FILE_APPEND_DATA,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL, OPEN_ALWAYS,
            FILE_ATTRIBUTE_NORMAL, NULL);
        if (file == INVALID_HANDLE_VALUE) { InterlockedIncrement(&g_log_dropped); return; }
    }
    DWORD written;
    DWORD bytes = (DWORD)strlen(line);
    if (!WriteFile(file, line, bytes, &written, NULL) || written != bytes)
        InterlockedIncrement(&g_log_dropped);
    CloseHandle(file);
}

static DWORD WINAPI log_worker(LPVOID parameter) {
    log_session_t *session = parameter;
    for (;;) {
        WaitForSingleObject(session->event, INFINITE);
        for (;;) {
            char line[LOG_LINE_BYTES];
            AcquireSRWLockExclusive(&session->lock);
            BOOL have_line = session->count != 0;
            BOOL stopping = session->stopping != 0;
            if (have_line) {
                memcpy(line, session->lines[session->read], sizeof line);
                session->read = (session->read + 1) % LOG_CAPACITY;
                session->count--;
            }
            ReleaseSRWLockExclusive(&session->lock);
            if (!have_line && stopping) goto finished;
            if (!have_line) break;
            log_write_line(session, line);
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
    wcsncat_s(session->path, MAX_PATH, L"sm64_trainer_gfx.log", _TRUNCATE);
    wcsncpy_s(session->previous, MAX_PATH, session->path, _TRUNCATE);
    wcsncat_s(session->previous, MAX_PATH, L".1", _TRUNCATE);
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

typedef struct {
    const char *name;
    uint64_t calls, slow_calls;
    int64_t previous, last_report, last_stall_report, max_total, max_gap;
} callback_diagnostic_t;
static callback_diagnostic_t g_diagnostic_list = {"ProcessDList"};
static callback_diagnostic_t g_diagnostic_update = {"UpdateScreen"};
static callback_diagnostic_t g_diagnostic_rdp = {"ProcessRDPList"};

static void callback_diagnostic_reset(callback_diagnostic_t *note) {
    const char *name = note->name;
    memset(note, 0, sizeof *note);
    note->name = name;
}

/* total spans the whole forwarded callback, including any stamp adapter work. */
static void callback_diagnostic_end(callback_diagnostic_t *note, int64_t begin, int64_t end) {
    if (!g_diagnostic_frequency) return;
    int64_t gap = note->previous ? begin - note->previous : 0;
    note->previous = begin;
    int64_t total = end - begin;
    note->calls++;
    if (total > note->max_total) note->max_total = total;
    if (gap > note->max_gap) note->max_gap = gap;
    BOOL slow = total >= g_diagnostic_frequency / 50 || gap >= g_diagnostic_frequency / 20;
    if (slow) note->slow_calls++;
    /* First stall immediately, then at most once per five seconds per callback;
     * once a minute even if quiet. Gaps also include legitimate emulator pauses. */
    int64_t since_report = end - (slow ? note->last_stall_report : note->last_report);
    if (slow && note->last_stall_report && since_report < g_diagnostic_frequency * 5) return;
    if (!slow && !note->last_report) { note->last_report = end; return; }
    if (!slow && since_report < g_diagnostic_frequency * 60) return;
    plugin_logf(slow ? "callback_stall" : "callback_summary",
        "callback=%s calls=%llu slow_calls=%llu total_ms=%.3f gap_ms=%.3f "
        "max_total_ms=%.3f max_gap_ms=%.3f dropped_records=%ld",
        note->name, (unsigned long long)note->calls, (unsigned long long)note->slow_calls,
        1000.0 * total / g_diagnostic_frequency, 1000.0 * gap / g_diagnostic_frequency,
        1000.0 * note->max_total / g_diagnostic_frequency,
        1000.0 * note->max_gap / g_diagnostic_frequency, (long)g_log_dropped);
    note->last_report = end;
    if (slow) note->last_stall_report = end;
}
