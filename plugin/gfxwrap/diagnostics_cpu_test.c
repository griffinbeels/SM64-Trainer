/* Block the real logger's file write, then prove callback-side logging
 * returns while it is blocked and overload drops only diagnostic records. */
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static HANDLE write_entered, release_write;
static BOOL WINAPI slow_write(HANDLE file, LPCVOID bytes, DWORD size,
                               LPDWORD written, LPOVERLAPPED overlap) {
    SetEvent(write_entered);
    WaitForSingleObject(release_write, INFINITE);
    return WriteFile(file, bytes, size, written, overlap);
}
static void self_dir(wchar_t *out, size_t count) {
    GetCurrentDirectoryW((DWORD)count, out);
    wcsncat_s(out, count, L"\\", _TRUNCATE);
}
#define WriteFile slow_write
#include "diagnostics.h"
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "diagnostics contract line %d\n", __LINE__); return 1; } } while (0)

int main(void) {
    write_entered = CreateEventW(NULL, TRUE, FALSE, NULL);
    release_write = CreateEventW(NULL, TRUE, FALSE, NULL);
    diagnostics_start();
    CHECK(g_log != NULL);
    plugin_logf("test", "blocked writer");
    CHECK(WaitForSingleObject(write_entered, 2000) == WAIT_OBJECT_0);
    for (unsigned n = 0; n < LOG_CAPACITY * 3; n++) plugin_logf("test", "record=%u", n);
    CHECK(g_log->count <= LOG_CAPACITY && g_log_dropped >= LOG_CAPACITY);
    /* Shutdown is bounded even while storage cannot finish. The pending
     * writer must retain its lifetime until the write is released. */
    HANDLE pending_thread;
    CHECK(DuplicateHandle(GetCurrentProcess(), g_log->thread, GetCurrentProcess(),
        &pending_thread, SYNCHRONIZE, FALSE, 0));
    diagnostics_stop();
    CHECK(g_log == NULL && WaitForSingleObject(pending_thread, 0) == WAIT_TIMEOUT);
    diagnostics_start(); /* even while the prior writer is still blocked */
    CHECK(g_log != NULL);
    plugin_logf("next_session", "startup survives a slow previous shutdown");
    SetEvent(release_write);
    CHECK(WaitForSingleObject(pending_thread, 5000) == WAIT_OBJECT_0);
    CloseHandle(pending_thread);
    diagnostics_stop();
    CHECK(g_log == NULL);
    CloseHandle(write_entered); CloseHandle(release_write);
    return 0;
}
