/* THE TEST HOST for the graphics plugin: stands in for Project64 without an
 * emulator. It creates a tiny window, fakes 8 MB of RDRAM, the VI registers
 * and a practice-ROM header, loads a plugin DLL (a test plugin, the capture
 * layer linked with fake_gfx.c, or any real plugin) and drives the Zilmar
 * calls the way PJ64 does, with no host GL context:
 *
 *   --drive <plugin.dll> <frames> [--stream <name>]
 *        per frame i: ProcessDList; VI_ORIGIN = 0x100000+i; UpdateScreen.
 *        --stream names a test plugin's control page (SM64_TEST_CONTROL_NAME,
 *        read only by test builds: gfxwrap.c control_name).
 *        --cpu-thread: the window's thread pumps messages while a second
 *        thread makes every plugin call (Project64 1.6's shape); a plugin
 *        that renders on its own thread deadlocks without it. A REAL plugin
 *        is driven the same way (how GLideN64_LINK_4.2 was measured on
 *        2026-09-05).
 *        --renderer-only: first ask SM64RendererOnlyV1 for the renderer alone
 *        (the scene lab's hosts do); the run prints what each call answered.
 *        Every run with --stream prints whether that control page appeared.
 *   --info <plugin.dll>            print GetDllInfo's name and version
 *
 * The window is a tool window shown without activation at the top-left of
 * the screen for the run's duration, layered at alpha 1 and click-through so
 * it is never seen and never takes focus. Exit code 0 on success. */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "zilmar.h"
#include "control.h"
#include "practice_rom_fixture.h"

#define RDRAM_BYTES (8u << 20)

static unsigned g_vi_origin, g_vi_status, g_vi_width, g_zero;
static unsigned g_mi_intr;

static void check_interrupts(void) {}

static int g_cpu_thread;   /* --cpu-thread: plugin calls on a second thread, the window's thread pumps */
static int g_renderer_only;   /* --renderer-only: SM64RendererOnlyV1 before any plugin call */
static const char *g_stream;  /* --stream: the test plugin's control page name */

typedef int (__cdecl *renderer_only_t)(unsigned int version);

/* Whether the control page named by --stream exists within `wait_ms`. */
static int control_page_appears(unsigned wait_ms) {
    char name[160];
    snprintf(name, sizeof name, "%s%s", g_stream, CONTROL_SUFFIX);
    for (unsigned waited = 0;; waited += 20) {
        HANDLE page = OpenFileMappingA(FILE_MAP_READ, FALSE, name);
        if (page) { CloseHandle(page); return 1; }
        if (waited >= wait_ms) return 0;
        Sleep(20);
    }
}

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

static unsigned char g_header[0x40];

static int info(const char *wrapper_path) {
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
    renderer_only_t alone = (renderer_only_t)GetProcAddress(wrapper, "SM64RendererOnlyV1");
    if (g_renderer_only) {
        if (!alone) { fprintf(stderr, "the plugin has no SM64RendererOnlyV1\n"); return 4; }
        const int other = alone(2), first = alone(1), again = alone(1);
        printf("renderer_only version2=%d first=%d again=%d\n", other, first, again);
    }
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
    if (alone) printf("renderer_only while_initiated=%d\n", alone(1));
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
    /* The capture layer's worker publishes its page on its own thread: a
     * plugin capturing shows it within seconds, the renderer alone never. */
    if (g_stream) printf("control_page %d\n", control_page_appears(g_renderer_only ? 500 : 5000));
    api.RomClosed();
    api.CloseDLL();
    if (alone && g_renderer_only) printf("renderer_only after_close=%d\n", alone(1));
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
    if (stream_name) SetEnvironmentVariableA("SM64_TEST_CONTROL_NAME", stream_name);
    g_stream = stream_name;
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
    }
    for (int index = 1; index < argc; index++)
        if (strcmp(argv[index], "--cpu-thread") == 0) g_cpu_thread = 1;
        else if (strcmp(argv[index], "--renderer-only") == 0) g_renderer_only = 1;
    if (argc >= 3 && strcmp(argv[1], "--info") == 0) return info(argv[2]);
    if (argc >= 4 && strcmp(argv[1], "--drive") == 0)
        return drive(argv[2], atoi(argv[3]), stream_name);
    fprintf(stderr, "usage: gfxwrap_host --info <dll> | --drive <dll> <frames> "
                    "[--stream <name>] [--cpu-thread] [--renderer-only]\n");
    return 1;
}
