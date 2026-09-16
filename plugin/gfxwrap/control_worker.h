/* Stage-one candidate: no backend is admitted. All IPC, owner checks and
 * lease clocks run on this worker, never on a forwarded graphics call.
 * The worker pins its DLL and owns its handles. CloseDLL only sets a flag;
 * it never joins a worker under the renderer or loader dependency chain. */
#pragma once
#include "control.h"
#ifdef GFXWRAP_GPU_RUNTIME
#include "runtime_control.h"
#endif
#ifndef GFXWRAP_BUILD_ID
#define GFXWRAP_BUILD_ID "unversioned"
#endif
static volatile LONG g_control_running;
static volatile LONG g_control_rom;
static volatile LONG g_control_baseline;
static volatile LONG g_control_epoch;
static volatile LONG g_control_desired;
/* Created at initialization, closed only at final DLL detach after the worker
 * drops its module reference. Lifecycle calls can signal it without racing a
 * worker-owned handle close. No graphics-frame callback touches this event. */
static HANDLE g_control_signal;
typedef struct {
    HMODULE module;
    char name[128];
} control_start_t;

static int control_request_read(control_page_t *page, control_request_t *out, HANDLE mutex) {
    DWORD lock = WaitForSingleObject(mutex, 0);
    if (lock == WAIT_TIMEOUT) return 0;
    if (lock != WAIT_OBJECT_0 && lock != WAIT_ABANDONED) return -1;
    BOOL valid = FALSE;
    for (unsigned attempt = 0; attempt < 3; ++attempt) {
        uint32_t seq = *(volatile uint32_t *)&page->request.seq;
        if (seq & 1) continue;
        MemoryBarrier();
        memcpy(out, &page->request, sizeof *out);
        MemoryBarrier();
        if (seq == *(volatile uint32_t *)&page->request.seq) { valid = TRUE; break; }
    }
    ReleaseMutex(mutex);
    return valid ? 1 : -1;
}

static void control_publish(control_page_t *page, uint32_t state, uint32_t reason,
                            const control_request_t *request) {
    InterlockedIncrement((volatile LONG *)&page->seq);
    page->state = state;
    page->reason = reason;
    page->ack_token = request ? request->token : 0;
    page->ack_heartbeat = request ? request->heartbeat : 0;
    page->rom_open = (uint32_t)InterlockedCompareExchange(&g_control_rom, 0, 0);
    page->rom_baseline = (uint32_t)InterlockedCompareExchange(&g_control_baseline, 0, 0);
    #ifdef GFXWRAP_GPU_RUNTIME
    page->capabilities = CONTROL_CAP_PASSIVE | rc_capabilities();
    #endif
    MemoryBarrier();
    InterlockedIncrement((volatile LONG *)&page->seq);
}

static HANDLE control_owner(const control_request_t *request) {
    HANDLE owner = OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION,
                               FALSE, request->owner_pid);
    if (!owner) return NULL;
    FILETIME created, exited, kernel, user;
    if (!GetProcessTimes(owner, &created, &exited, &kernel, &user)
            || created.dwLowDateTime != request->created_lo
            || created.dwHighDateTime != request->created_hi) {
        CloseHandle(owner);
        return NULL;
    }
    return owner;
}

static DWORD WINAPI control_worker(void *argument) {
    control_start_t *start = argument;
    HMODULE module = start->module;
restart:
    ;
    LONG session = InterlockedCompareExchange(&g_control_desired, 0, 0);
    char name[200];
    snprintf(name, sizeof name, "%s%s%s", start->name, CONTROL_SUFFIX,
             CONTROL_PRODUCER_SUFFIX);
    HANDLE gate = CreateMutexA(NULL, FALSE, name);
    HANDLE map = NULL, wake = NULL, owner = NULL, client = NULL;
    control_page_t *page = NULL;
    BOOL owns_gate = FALSE;
    if (!gate) goto done;
    DWORD acquired = WaitForSingleObject(gate, 0);
    if (acquired != WAIT_OBJECT_0 && acquired != WAIT_ABANDONED) goto done;
    owns_gate = TRUE;
    snprintf(name, sizeof name, "%s%s", start->name, CONTROL_SUFFIX);
    map = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0,
                             CONTROL_BYTES, name);
    if (!map) goto done;
    page = MapViewOfFile(map, FILE_MAP_ALL_ACCESS, 0, 0, CONTROL_BYTES);
    if (!page) goto done;
    /* Never erase the independently owned request half during reconnect. */
    uint32_t generation = page->generation + 1;
    if (!generation) generation = 1;
    uint32_t publishing = (page->seq + 1) | 1u;
    InterlockedExchange((volatile LONG *)&page->seq, (LONG)publishing);
    memset(page, 0, 16);
    memset((char *)page + 20, 0, 108);
    memcpy(page->magic, CONTROL_MAGIC, 8);
    page->version = CONTROL_VERSION;
    page->bytes = CONTROL_BYTES;
    page->producer_pid = GetCurrentProcessId();
    FILETIME birth, exited, kernel, user;
    if (GetProcessTimes(GetCurrentProcess(), &birth, &exited, &kernel, &user)) {
        page->producer_created_lo = birth.dwLowDateTime;
        page->producer_created_hi = birth.dwHighDateTime;
    }
    page->generation = generation;
    page->capabilities = CONTROL_CAP_PASSIVE;
    #ifdef GFXWRAP_GPU_RUNTIME
    page->capabilities |= rc_capabilities();
    #endif
    page->state = CONTROL_PASSIVE;
    strncpy_s(page->build_id, sizeof page->build_id, GFXWRAP_BUILD_ID, _TRUNCATE);
    MemoryBarrier();
    InterlockedExchange((volatile LONG *)&page->seq, (LONG)(publishing + 1));
    snprintf(name, sizeof name, "%s%s%s", start->name, CONTROL_SUFFIX,
             CONTROL_WAKE_SUFFIX);
    wake = CreateEventA(NULL, FALSE, FALSE, name);
    if (!wake) goto done;
    snprintf(name, sizeof name, "%s%s%s", start->name, CONTROL_SUFFIX,
             CONTROL_CLIENT_SUFFIX);
    client = CreateMutexA(NULL, FALSE, name);
    if (!client) goto done;
    control_request_t previous = {0};
    uint32_t renewed_at = 0;
    BOOL expired = FALSE;
    while (session && InterlockedCompareExchange(&g_control_desired, 0, 0) == session) {
        control_request_t request;
        uint32_t state = CONTROL_PASSIVE, reason = 0;
        BOOL live_lease = FALSE;
        int received = control_request_read(page, &request, client);
        if (received == 0) request = previous;
        if (received >= 0) {
            BOOL current = request.token && request.enabled
                && request.generation == generation;
            BOOL changed = current && (!previous.enabled
                || previous.generation != generation || request.token != previous.token
                || request.owner_pid != previous.owner_pid
                || request.created_lo != previous.created_lo
                || request.created_hi != previous.created_hi);
            if (changed || !current) {
                if (owner) CloseHandle(owner);
                owner = current ? control_owner(&request) : NULL;
                expired = FALSE;
                renewed_at = GetTickCount();
            }
            if (current) {
                if (request.heartbeat != previous.heartbeat) {
                    renewed_at = GetTickCount();
                    expired = FALSE;
                }
                if (!owner || WaitForSingleObject(owner, 0) != WAIT_TIMEOUT)
                    reason = CONTROL_OWNER_GONE;
                else if (expired || (uint32_t)(GetTickCount() - renewed_at)
                                     >= CONTROL_LEASE_MS) {
                    reason = CONTROL_LEASE_EXPIRED;
                    expired = TRUE;
                } else {
                    live_lease = TRUE;
                    /* Explicit capability refusal, never fake recording.
                     * Stage two must prove the picture boundary first. */
                    state = CONTROL_UNAVAILABLE;
                    reason = CONTROL_NO_BACKEND;
                }
            }
            #ifdef GFXWRAP_GPU_RUNTIME
            if (live_lease) rc_demand(page, &request, &state, &reason);
            else rc_revoke(reason);
            #endif
            control_publish(page, state, reason, &request);
            previous = request;
        } else {
            #ifdef GFXWRAP_GPU_RUNTIME
            rc_revoke(CONTROL_PROTOCOL_ERROR);
            #endif
            /* A writer killed mid-command cannot preserve active demand. */
            control_publish(page, CONTROL_PASSIVE, CONTROL_PROTOCOL_ERROR, NULL);
        }
        HANDLE signals[5] = {wake, g_control_signal};
        DWORD count = 2, timeout = INFINITE, client_index = MAXDWORD;
        #ifdef GFXWRAP_GPU_RUNTIME
        if (rc_signal()) signals[count++] = rc_signal();
        #endif
        if (owner && live_lease) {
            signals[count++] = owner;
            uint32_t age = (uint32_t)(GetTickCount() - renewed_at);
            timeout = age < CONTROL_LEASE_MS ? CONTROL_LEASE_MS - age : 0;
        }
        if (received == 0) {
            /* A contender can acquire after the publisher's event, then die
             * without another signal. Observe release/abandonment directly. */
            client_index = count;
            signals[count++] = client;
        }
        /* Fully passive: sleep until a command or lifecycle transition. A
         * live lease adds owner death and one expiry deadline, never polling. */
        DWORD waited = WaitForMultipleObjects(count, signals, FALSE, timeout);
        if (waited == WAIT_FAILED) break;
        if (client_index != MAXDWORD && (waited == WAIT_OBJECT_0 + client_index
                || waited == WAIT_ABANDONED_0 + client_index))
            ReleaseMutex(client); /* Wait acquired it; retry the snapshot. */
    }
done:
    #ifdef GFXWRAP_GPU_RUNTIME
    rc_revoke(CONTROL_CLOSED);
    #endif
    if (page) {
        control_publish(page, CONTROL_CLOSED, 0, NULL);
        UnmapViewOfFile(page);
    }
    if (owner) CloseHandle(owner);
    if (client) CloseHandle(client);
    if (wake) CloseHandle(wake);
    if (map) CloseHandle(map);
    if (owns_gate) ReleaseMutex(gate);
    if (gate) CloseHandle(gate);
    InterlockedExchange(&g_control_running, 0);
    if (InterlockedCompareExchange(&g_control_desired, 0, 0) != 0
            && InterlockedCompareExchange(&g_control_desired, 0, 0) != session
            && InterlockedCompareExchange(&g_control_running, 1, 0) == 0)
        goto restart;
    HeapFree(GetProcessHeap(), 0, start);
    FreeLibraryAndExitThread(module, 0);
    return 0;
}

static void control_start(const char *stream_name) {
    #ifdef GFXWRAP_GPU_RUNTIME
    rc_rom(FALSE);
    #endif
    /* Latest lifecycle request wins. A worker retires its old page before
     * reconciling the new session; immediate CloseDLL/InitiateGFX loses nothing.
     * Stream name is fixed for this loaded DLL, as configured at startup. */
    InterlockedExchange(&g_control_rom, 0);
    InterlockedExchange(&g_control_baseline, 0);
    LONG epoch = InterlockedIncrement(&g_control_epoch);
    if (!epoch) epoch = InterlockedIncrement(&g_control_epoch);
    InterlockedExchange(&g_control_desired, epoch);
    if (InterlockedCompareExchange(&g_control_running, 1, 0) != 0) {
        if (g_control_signal) SetEvent(g_control_signal);
        return;
    }
    if (!g_control_signal) g_control_signal = CreateEventA(NULL, FALSE, FALSE, NULL);
    if (!g_control_signal) { InterlockedExchange(&g_control_running, 0); return; }
    control_start_t *start = HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof *start);
    if (!start) { InterlockedExchange(&g_control_running, 0); return; }
    if (!GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,
                           (LPCSTR)&control_worker, &start->module)) {
        HeapFree(GetProcessHeap(), 0, start);
        InterlockedExchange(&g_control_running, 0);
        return;
    }
    strncpy_s(start->name, sizeof start->name, stream_name, _TRUNCATE);
    HANDLE thread = CreateThread(NULL, 0, control_worker, start, 0, NULL);
    if (thread) CloseHandle(thread);
    else {
        FreeLibrary(start->module);
        HeapFree(GetProcessHeap(), 0, start);
        InterlockedExchange(&g_control_running, 0);
    }
}
static void control_stop(void) {
    #ifdef GFXWRAP_GPU_RUNTIME
    rc_rom(FALSE);
    #endif
    InterlockedExchange(&g_control_desired, 0);
    InterlockedExchange(&g_control_rom, 0);
    InterlockedExchange(&g_control_baseline, 0);
    if (g_control_signal) SetEvent(g_control_signal);
}
/* `opened`: a practice ROM is open, so capture may be admitted. `baseline`:
 * another ROM is open and the wrapper forwards as the plain renderer. A
 * baseline ROM never reaches rc_rom, so no lease can activate capture. */
static void control_rom(BOOL opened, BOOL baseline) {
    #ifdef GFXWRAP_GPU_RUNTIME
    rc_rom(opened);
    #endif
    InterlockedExchange(&g_control_baseline, baseline);
    InterlockedExchange(&g_control_rom, opened);
    if (g_control_signal) SetEvent(g_control_signal);
}
