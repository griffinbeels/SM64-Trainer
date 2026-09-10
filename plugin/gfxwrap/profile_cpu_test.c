/* Bounded lease/session behavior with a controlled clock, no renderer. */
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static uint32_t now_ms;
static int64_t ticks;
static DWORD profile_test_tick(void) { return now_ms; }
static int64_t qpc_now(void) { return ++ticks; }
#define GetTickCount profile_test_tick
#include "profile.h"
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "profile contract line %d\n", __LINE__); return 1; } } while (0)

int main(void) {
    profile_stream_t memory = {0};
    g_profile = &memory;
    memory.frequency = 1000000;
    CHECK(profile_begin() == 0); /* disabled: no QPC calls */
    CHECK(ticks == 0);
    memory.request = 10;
    CHECK(profile_begin() == 0); /* request without heartbeat is not a lease */
    memory.heartbeat = 1;
    int64_t began = profile_begin();
    CHECK(began > 0 && memory.sequence % 2 == 1 && memory.generation == 10);
    profile_record(PR_GL_READ, 9);
    profile_end(began);
    CHECK(memory.sequence % 2 == 0 && memory.metrics[PR_GL_READ].buckets[4] == 1);
    now_ms = 3000;
    CHECK(profile_begin() == 0); /* abandoned reader */
    memory.heartbeat++;
    began = profile_begin(); profile_end(began);
    CHECK(memory.metrics[PR_UPDATE].count == 2);
    now_ms = 300000;
    memory.heartbeat++;
    CHECK(profile_begin() == 0); /* renewal cannot evade hard duration bound */
    memory.request = 11;
    began = profile_begin(); profile_end(began);
    CHECK(memory.generation == 11 && memory.metrics[PR_UPDATE].count == 1);
    CHECK(memory.metrics[PR_GL_READ].count == 0 && memory.metrics[PR_VI_INTERVAL].count == 0);
    memory.request = 0;
    CHECK(profile_begin() == 0);
    return 0;
}
