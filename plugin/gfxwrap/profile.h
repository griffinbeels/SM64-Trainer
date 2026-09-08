/* Optional capture profiling, independent of the picture stream ABI.
 * CPU wall-clock durations include driver waits; they are NOT GPU timings.
 * One emulation-thread writer; readers accept only matching even sequences.
 * Fixed storage, no allocation or I/O at a presentation, disabled by default.
 */
#pragma once
#include <stdint.h>
#include <stddef.h>
#define PROFILE_MAGIC "SM64PRF1"
#define PROFILE_VERSION 1
#define PROFILE_SUFFIX "_profile_v1"
#define PROFILE_BUCKETS 32
enum { PR_UPDATE, PR_WRAPPED_UPDATE, PR_CAPTURE, PR_GL_SETUP, PR_GL_READ,
       PR_GL_RESTORE, PR_READSCREEN, PR_COPY, PR_PUBLISH, PR_VI_INTERVAL,
       PROFILE_STAGES };
typedef struct {
    uint64_t count, total_ticks, max_ticks;
    uint64_t buckets[PROFILE_BUCKETS];
} profile_metric_t;
typedef struct {
    char magic[8];
    uint32_t version, pid;
    volatile uint32_t sequence;
    uint32_t generation;
    int64_t frequency;
    volatile uint32_t request, heartbeat; /* reader owns only these fields */
    uint8_t reserved[24];
    profile_metric_t metrics[PROFILE_STAGES];
} profile_stream_t;
_Static_assert(offsetof(profile_stream_t, metrics) == 64, "profile metrics offset");
_Static_assert(sizeof(profile_metric_t) == 280, "profile metric bytes");
_Static_assert(sizeof(profile_stream_t) == 2864, "profile stream bytes");

static HANDLE g_profile_map;
static profile_stream_t *g_profile;
static BOOL g_profile_on;
static uint32_t g_profile_heartbeat, g_profile_touch, g_profile_start;
static int64_t g_profile_previous_vi;

static void profile_open(const char *stream_name) {
    char name[180];
    snprintf(name, sizeof name, "%s%s", stream_name, PROFILE_SUFFIX);
    g_profile_map = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE,
                                      0, sizeof(profile_stream_t), name);
    if (!g_profile_map) return;
    g_profile = MapViewOfFile(g_profile_map, FILE_MAP_ALL_ACCESS, 0, 0,
                              sizeof(profile_stream_t));
    if (!g_profile) { CloseHandle(g_profile_map); g_profile_map = NULL; return; }
    /* Preserve a waiting reader's request, but invalidate the old producer. */
    g_profile->sequence |= 1;
    MemoryBarrier();
    memcpy(g_profile->magic, PROFILE_MAGIC, 8);
    g_profile->version = PROFILE_VERSION;
    g_profile->pid = GetCurrentProcessId();
    LARGE_INTEGER frequency;
    QueryPerformanceFrequency(&frequency);
    g_profile->frequency = frequency.QuadPart;
    g_profile->generation = 0;
    memset(g_profile->metrics, 0, sizeof g_profile->metrics);
    MemoryBarrier();
    g_profile->sequence++;
    g_profile_on = FALSE;
    g_profile_previous_vi = 0;
    g_profile_heartbeat = 0;
}

static void profile_close(void) {
    g_profile_on = FALSE;
    if (g_profile) UnmapViewOfFile(g_profile);
    if (g_profile_map) CloseHandle(g_profile_map);
    g_profile = NULL;
    g_profile_map = NULL;
}

static int64_t profile_mark(void) { return g_profile_on ? qpc_now() : 0; }

static void profile_record(unsigned stage, int64_t elapsed) {
    if (!g_profile_on || elapsed < 0) return;
    profile_metric_t *metric = &g_profile->metrics[stage];
    uint64_t ticks = (uint64_t)elapsed;
    uint64_t us = ticks * 1000000 / (uint64_t)g_profile->frequency;
    unsigned bucket = 0;
    /* Bucket 0: [0,1) us; bucket n: [2^(n-1),2^n) us. */
    while (us && bucket < PROFILE_BUCKETS - 1) { us >>= 1; bucket++; }
    metric->count++;
    metric->total_ticks += ticks;
    if (ticks > metric->max_ticks) metric->max_ticks = ticks;
    metric->buckets[bucket]++;
}

static void profile_end_stage(unsigned stage, int64_t began) {
    if (g_profile_on) profile_record(stage, qpc_now() - began);
}

static int64_t profile_begin(void) {
    g_profile_on = FALSE;
    if (!g_profile || !g_profile->request) { g_profile_previous_vi = 0; return 0; }
    uint32_t now = GetTickCount(), request = g_profile->request;
    if (!request) { g_profile_previous_vi = 0; return 0; }
    if (g_profile->heartbeat != g_profile_heartbeat) {
        g_profile_heartbeat = g_profile->heartbeat;
        g_profile_touch = now;
    }
    if (!g_profile_heartbeat || (uint32_t)(now - g_profile_touch) >= 3000) {
        g_profile_previous_vi = 0;
        return 0;
    }
    if (request == g_profile->generation && (uint32_t)(now - g_profile_start) >= 300000) {
        g_profile_previous_vi = 0;
        return 0;
    }
    g_profile->sequence++;
    MemoryBarrier();
    if (request != g_profile->generation) {
        g_profile->generation = request;
        memset(g_profile->metrics, 0, sizeof g_profile->metrics);
        g_profile_start = now;
        g_profile_previous_vi = 0;
    }
    g_profile_on = TRUE;
    int64_t began = qpc_now();
    if (g_profile_previous_vi) profile_record(PR_VI_INTERVAL, began - g_profile_previous_vi);
    g_profile_previous_vi = began;
    return began;
}

static void profile_end(int64_t began) {
    if (!g_profile_on) return;
    profile_end_stage(PR_UPDATE, began);
    MemoryBarrier();
    g_profile->sequence++;
    g_profile_on = FALSE;
}
