/* THE TEST HOST for the capture layer: stands in for Project64 without an
 * emulator. It creates a tiny window, fakes 8 MB of RDRAM, the VI registers
 * and a practice-ROM header, loads the wrapper DLL (whose ini names
 * fake_gfx.dll and a test stream), and drives the Zilmar calls the way PJ64
 * does, with no host GL context:
 *
 *   --drive <wrapper.dll> <frames> [--stream <name>]
 *        per frame i: ProcessDList; VI_ORIGIN = 0x100000+i; UpdateScreen.
 *        --cpu-thread: the window's thread pumps messages while a second
 *        thread makes every plugin call (Project64 1.6's shape); a wrapped
 *        plugin that renders on its own thread deadlocks without it.
 *        --wrapped <dll>: drive a REAL plugin (an absolute path is used as
 *        is) -- how GLideN64_LINK_4.2 was measured on 2026-09-05.
 *   --info <wrapper.dll>           print GetDllInfo's name and version
 *
 * The window is a tool window shown without activation at the top-left of
 * the screen for the run's duration, layered at alpha 1 and click-through so
 * it is never seen and never takes focus. Exit code 0 on success. */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "zilmar.h"
#include "practice_rom_fixture.h"

#define RDRAM_BYTES (8u << 20)

static unsigned g_vi_origin, g_vi_status, g_vi_width, g_zero;
static unsigned g_mi_intr;

static void check_interrupts(void) {}

static int g_cpu_thread;   /* --cpu-thread: plugin calls on a second thread, the window's thread pumps */

static HWND make_window(void) {
    WNDCLASSA klass;
    memset(&klass, 0, sizeof klass);
    klass.lpfnWndProc = DefWindowProcA;
    klass.hInstance = GetModuleHandleA(NULL);
    klass.lpszClassName = "sm64_gfxwrap_host";
    klass.style = CS_OWNDC;
    RegisterClassA(&klass);
    /* Layered at alpha 1 and click-through: a wrapped plugin gets a real
     * window, but it must not be SEEN -- the suite drives this host dozens
     * of times and he found "a rainbow square in the corner of my screen"
     * (2026-09-05). */
    HWND window = CreateWindowExA(WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED
                                  | WS_EX_TRANSPARENT, klass.lpszClassName,
                                  "gfxwrap host", WS_POPUP, 0, 0, 64, 48,
                                  NULL, NULL, klass.hInstance, NULL);
    if (!window) return NULL;
    SetLayeredWindowAttributes(window, 0, 1, LWA_ALPHA);
    ShowWindow(window, SW_SHOWNOACTIVATE);
    return window;
}

static const char *g_wrapped_name = "fake_gfx.dll";   /* --wrapped overrides */
static unsigned char g_header[0x40];

static void write_ini(const char *wrapper_path, const char *stream_name) {
    char path[MAX_PATH];
    strncpy_s(path, sizeof path, wrapper_path, _TRUNCATE);
    char *slash = strrchr(path, '\\');
    char *forward = strrchr(path, '/');
    if (forward > slash) slash = forward;
    if (slash) *(slash + 1) = '\0'; else path[0] = '\0';
    strncat_s(path, sizeof path, "sm64_trainer_gfx.ini", _TRUNCATE);
    FILE *ini = fopen(path, "w");
    if (!ini) { fprintf(stderr, "cannot write %s\n", path); exit(3); }
    fprintf(ini, "wrapped=%s\n", g_wrapped_name);
    if (stream_name) fprintf(ini, "stream=%s\n", stream_name);
    fclose(ini);
}

static int info(const char *wrapper_path) {
    write_ini(wrapper_path, NULL);
    HMODULE wrapper = LoadLibraryExA(wrapper_path, NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!wrapper) { fprintf(stderr, "LoadLibrary failed: %lu\n", GetLastError()); return 2; }
    gfx_api_t api;
    RESOLVE_GFX_API(api, wrapper);
    PLUGIN_INFO plugin_info;
    memset(&plugin_info, 0, sizeof plugin_info);
    api.GetDllInfo(&plugin_info);
    printf("name %s\nversion 0x%04x\nbswaped %d\n", plugin_info.Name, plugin_info.Version,
           plugin_info.MemoryBswaped);
    return 0;
}

typedef struct {
    const char *wrapper_path;
    int frames;
    HWND window;
    int result;
} drive_job_t;

static int drive_calls(drive_job_t *job) {
    const char *wrapper_path = job->wrapper_path;
    int frames = job->frames;
    HMODULE wrapper = LoadLibraryExA(wrapper_path, NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!wrapper) { fprintf(stderr, "LoadLibrary failed: %lu\n", GetLastError()); return 2; }
    gfx_api_t api;
    RESOLVE_GFX_API(api, wrapper);
    const char *required[] = {"GetDllInfo", "CloseDLL", "ChangeWindow", "DrawScreen",
        "InitiateGFX", "MoveScreen", "ProcessDList", "RomClosed", "RomOpen", "UpdateScreen",
        "ViStatusChanged", "ViWidthChanged", "ProcessRDPList", "CaptureScreen", "ShowCFB"};
    for (size_t index = 0; index < sizeof required / sizeof required[0]; index++) {
        if (!GetProcAddress(wrapper, required[index])) {
            fprintf(stderr, "wrapper lacks %s, PJ64 1.6 would refuse it\n", required[index]);
            return 4;
        }
    }
    unsigned char *rdram = VirtualAlloc(NULL, RDRAM_BYTES, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (!rdram) { fprintf(stderr, "no fake RDRAM\n"); return 2; }
    GFX_INFO gfx;
    memset(&gfx, 0, sizeof gfx);
    gfx.hWnd = job->window;
    gfx.MemoryBswaped = TRUE;
    gfx.RDRAM = rdram;
    gfx.DMEM = rdram; gfx.IMEM = rdram;
    PRACTICE_FIXTURE_USAMUNE(g_header);
    gfx.HEADER = g_header;
    gfx.MI_INTR_REG = &g_mi_intr;
    gfx.VI_ORIGIN_REG = &g_vi_origin;
    gfx.VI_STATUS_REG = &g_vi_status;
    gfx.VI_WIDTH_REG = &g_vi_width;
    gfx.DPC_START_REG = gfx.DPC_END_REG = gfx.DPC_CURRENT_REG = gfx.DPC_STATUS_REG = &g_zero;
    gfx.DPC_CLOCK_REG = gfx.DPC_BUFBUSY_REG = gfx.DPC_PIPEBUSY_REG = gfx.DPC_TMEM_REG = &g_zero;
    gfx.VI_INTR_REG = gfx.VI_V_CURRENT_LINE_REG = gfx.VI_TIMING_REG = gfx.VI_V_SYNC_REG = &g_zero;
    gfx.VI_H_SYNC_REG = gfx.VI_LEAP_REG = gfx.VI_H_START_REG = gfx.VI_V_START_REG = &g_zero;
    gfx.VI_V_BURST_REG = gfx.VI_X_SCALE_REG = gfx.VI_Y_SCALE_REG = &g_zero;
    gfx.CheckInterrupts = check_interrupts;
    PLUGIN_INFO plugin_info;
    memset(&plugin_info, 0, sizeof plugin_info);
    api.GetDllInfo(&plugin_info);
    printf("name %s\n", plugin_info.Name);
    if (!api.InitiateGFX(gfx)) {
        fprintf(stderr, "InitiateGFX failed\n");
        api.CloseDLL();
        FreeLibrary(wrapper);
        VirtualFree(rdram, 0, MEM_RELEASE);
        return 5;
    }
    api.RomOpen();
    for (int frame = 0; frame < frames; frame++) {
        api.ProcessDList();
        g_vi_origin = 0x100000u + (unsigned)frame;
        api.UpdateScreen();
        MSG message;
        while (PeekMessageA(&message, NULL, 0, 0, PM_REMOVE)) DispatchMessageA(&message);
    }
    printf("host thread %lu: GL context %p after the run\n", (unsigned long)GetCurrentThreadId(),
           (void *)wglGetCurrentContext());
    api.RomClosed();
    api.CloseDLL();
    FreeLibrary(wrapper);
    VirtualFree(rdram, 0, MEM_RELEASE);
    printf("drove %d frames\n", frames);
    return 0;
}

static DWORD WINAPI drive_thread(LPVOID parameter) {
    drive_job_t *job = (drive_job_t *)parameter;
    job->result = drive_calls(job);
    return 0;
}

static int drive(const char *wrapper_path, int frames, const char *stream_name) {
    write_ini(wrapper_path, stream_name);
    drive_job_t job;
    memset(&job, 0, sizeof job);
    job.wrapper_path = wrapper_path;
    job.frames = frames;
    job.window = make_window();
    if (!job.window) { fprintf(stderr, "no window\n"); return 2; }
    if (!g_cpu_thread) {
        int result = drive_calls(&job);
        DestroyWindow(job.window);
        return result;
    }
    /* PJ64's shape: the window's thread pumps messages while a second
     * thread makes every plugin call -- a wrapped plugin that does its
     * window work on yet another thread needs this pump to make progress. */
    HANDLE thread = CreateThread(NULL, 0, drive_thread, &job, 0, NULL);
    if (!thread) { fprintf(stderr, "no CPU thread\n"); return 2; }
    for (;;) {
        DWORD waited = MsgWaitForMultipleObjects(1, &thread, FALSE, INFINITE, QS_ALLINPUT);
        if (waited == WAIT_OBJECT_0) break;
        MSG message;
        while (PeekMessageA(&message, NULL, 0, 0, PM_REMOVE)) {
            TranslateMessage(&message);
            DispatchMessageA(&message);
        }
    }
    CloseHandle(thread);
    DestroyWindow(job.window);
    return job.result;
}

int main(int argc, char **argv) {
    const char *stream_name = NULL;
    for (int index = 1; index + 1 < argc; index++) {
        if (strcmp(argv[index], "--stream") == 0) stream_name = argv[index + 1];
        if (strcmp(argv[index], "--wrapped") == 0) g_wrapped_name = argv[index + 1];
    }
    for (int index = 1; index < argc; index++)
        if (strcmp(argv[index], "--cpu-thread") == 0) g_cpu_thread = 1;
    if (argc >= 3 && strcmp(argv[1], "--info") == 0) return info(argv[2]);
    if (argc >= 4 && strcmp(argv[1], "--drive") == 0)
        return drive(argv[2], atoi(argv[3]), stream_name);
    fprintf(stderr, "usage: gfxwrap_host --info <dll> | --drive <dll> <frames> "
                    "[--stream <name>] [--wrapped <dll>] [--cpu-thread]\n");
    return 1;
}
