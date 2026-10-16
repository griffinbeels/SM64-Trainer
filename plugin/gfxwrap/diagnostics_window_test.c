/* The real logger's retention and startup evidence, CPU only, in the working
 * directory: rotation keeps three files, the activation sidecar holds this
 * process's startup lines (the previous process keeps .1, then a cap), every
 * slow callback inside the stall watch window is logged with its QPCs, and
 * callback_summary reports its own window. tests/test_gfxwrap_logger_and_build.py
 * reads the files this leaves behind. Sizes are shrunk to exercise rotation. */
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define LOG_MAX_BYTES 8192u /* larger CPU tuples; 200 fillers still exceed three files */
#define ACTIVATION_MAX_BYTES 3000u
static void self_dir(wchar_t *out, size_t count) {
    GetCurrentDirectoryW((DWORD)count, out);
    wcsncat_s(out, count, L"\\", _TRUNCATE);
}
#include "diagnostics.h"
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "diagnostics window line %d\n", __LINE__); return 1; } } while (0)

static void drain(void) { /* the queue holds 64 lines; never overrun it here */
    for (unsigned n = 0; n < 2000 && g_log && g_log->count; n++) Sleep(1);
}

int main(void) {
    /* A previous Project64 process left its startup lines behind. */
    FILE *old = fopen("sm64_trainer_gfx.activation.log", "wb");
    CHECK(old && fputs("previous process startup\n", old) >= 0 && !fclose(old));
    diagnostics_start();
    CHECK(g_log != NULL && g_diagnostic_frequency > 0);
    const int64_t f = g_diagnostic_frequency, ms = f / 1000;
    plugin_logf("rom_open_begin", "initiated=1 practice_rom=1");

    /* Rotation: far more than three files' worth of ordinary lines. */
    for (unsigned n = 0; n < 200; n++) {
        plugin_logf("filler", "line=%u %s", n,
            "................................................................................");
        if (n % 32 == 31) drain();
    }

    /* Before any capture request: a slow call per second, 25 ms each. Only the
     * first is logged; the five-second throttle hides the next four. */
    callback_diagnostic_t update = {"UpdateScreen"};
    int64_t at = 1000 * f;
    for (unsigned n = 0; n < 5; n++, at += f) callback_diagnostic_end(&update, at, at + 25 * ms);
    /* A capture request opens the watch window: all five are logged. */
    diagnostics_watch_stalls(at);
    for (unsigned n = 0; n < 5; n++, at += f) callback_diagnostic_end(&update, at, at + 30 * ms);
    /* After STALL_WATCH_SECONDS the throttle applies again. */
    at += (STALL_WATCH_SECONDS + 1) * f;
    callback_diagnostic_end(&update, at, at + 40 * ms);
    at += f;
    callback_diagnostic_end(&update, at, at + 41 * ms);
    drain();

    /* Two summary windows: a call every 16 ms for 122 s, all 1 ms but one of
     * 10 ms in the first minute. Each summary reports only its own window. */
    callback_diagnostic_t list = {"ProcessDList"};
    at = 5000 * f;
    for (unsigned n = 0; n < 122000 / 16; n++, at += 16 * ms)
        callback_diagnostic_end(&list, at, at + (n == 100 ? 10 * ms : ms));
    drain();

    /* Past the sidecar cap: these are dropped from the sidecar only. */
    for (unsigned n = 0; n < 30; n++) {
        plugin_logf("gpu_prepare", "epoch=%u padding=%s", n,
            "................................................................................");
        if (n % 32 == 31) drain();
    }
    drain();
    HANDLE writer;
    CHECK(DuplicateHandle(GetCurrentProcess(), g_log->thread, GetCurrentProcess(),
        &writer, SYNCHRONIZE, FALSE, 0));
    diagnostics_stop();
    CHECK(WaitForSingleObject(writer, 5000) == WAIT_OBJECT_0);
    CloseHandle(writer);
    return 0;
}
