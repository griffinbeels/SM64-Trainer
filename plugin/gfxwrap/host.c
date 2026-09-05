/* THE TEST HOST for the capture layer: stands in for Project64 without an
 * emulator. It creates a tiny window with a GL context, fakes 8 MB of RDRAM
 * and the VI registers, loads the wrapper DLL (whose ini names fake_gfx.dll
 * and a test stream), and drives the Zilmar calls the way PJ64 does:
 *
 *   --layout                       print every stream constant as NAME VALUE
 *   --drive <wrapper.dll> <frames> [--stream <name>]
 *        per frame i: RDRAM[0..3] = 1000+i (little-endian), RDRAM[64..67] =
 *        0x11223300+i, RDRAM[128..130] = (i, 2i, 3i) as b,g,r; ProcessDList;
 *        VI_ORIGIN = 0x100000+i; UpdateScreen; then ONE extra UpdateScreen
 *        with the origin unchanged (must capture nothing).
 *        --cpu-thread: the window's thread pumps messages while a second
 *        thread makes every plugin call (Project64 1.6's shape); a wrapped
 *        plugin that renders on its own thread deadlocks without it.
 *        --no-context: no host GL context at all, so the wrapped plugin
 *        makes its own or the wrapper has to read through ReadScreen.
 *        --wrapped <dll>: drive a REAL plugin (an absolute path is used as
 *        is) -- how GLideN64_LINK_4.2 was measured on 2026-09-05.
 *   --info <wrapper.dll>           print GetDllInfo's name and version
 *
 * The window is a tool window shown without activation at the top-left of
 * the screen for the run's duration (a hidden window has no front buffer to
 * read), so it never takes focus. Exit code 0 on success. */
#include <windows.h>
#include <GL/gl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "zilmar.h"
#include "stream.h"

#define RDRAM_BYTES (8u << 20)

static unsigned g_vi_origin, g_vi_status, g_vi_width, g_zero;
static unsigned g_mi_intr;

static void check_interrupts(void) {}

static int print_layout(void) {
#define PRINT_ONE(name) printf("%s %lld\n", #name, (long long)(name));
    STREAM_LAYOUT(PRINT_ONE)
#undef PRINT_ONE
    return 0;
}

static int g_no_context;   /* --no-context: the wrapped plugin creates its own, as inside PJ64 */
static int g_cpu_thread;   /* --cpu-thread: plugin calls on a second thread, the window's thread pumps */

static HWND make_gl_window(HDC *device_out, HGLRC *context_out) {
    WNDCLASSA klass;
    memset(&klass, 0, sizeof klass);
    klass.lpfnWndProc = DefWindowProcA;
    klass.hInstance = GetModuleHandleA(NULL);
    klass.lpszClassName = "sm64_gfxwrap_host";
    klass.style = CS_OWNDC;
    RegisterClassA(&klass);
    HWND window = CreateWindowExA(WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE, klass.lpszClassName,
                                  "gfxwrap host", WS_POPUP, 0, 0, 64, 48,
                                  NULL, NULL, klass.hInstance, NULL);
    if (!window) return NULL;
    HDC device = GetDC(window);
    if (g_no_context) {
        ShowWindow(window, SW_SHOWNOACTIVATE);
        *device_out = device;
        *context_out = NULL;
        return window;
    }
    PIXELFORMATDESCRIPTOR descriptor;
    memset(&descriptor, 0, sizeof descriptor);
    descriptor.nSize = sizeof descriptor;
    descriptor.nVersion = 1;
    descriptor.dwFlags = PFD_DRAW_TO_WINDOW | PFD_SUPPORT_OPENGL | PFD_DOUBLEBUFFER;
    descriptor.iPixelType = PFD_TYPE_RGBA;
    descriptor.cColorBits = 24;
    int format = ChoosePixelFormat(device, &descriptor);
    if (!format || !SetPixelFormat(device, format, &descriptor)) return NULL;
    HGLRC context = wglCreateContext(device);
    if (!context) return NULL;
    ShowWindow(window, SW_SHOWNOACTIVATE);
    *device_out = device;
    *context_out = context;
    return window;
}

static const char *g_wrapped_name = "fake_gfx.dll";   /* --wrapped overrides */
static unsigned g_rdram_committed_mb = 8;             /* --rdram-mb overrides */
static int g_dirty_gl;                                /* --dirty-gl: the fake leaves GL state bound */

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

/* Fake RDRAM the way an emulator with a smaller configuration would hold
 * it: 8 MB of address space RESERVED, only the first --rdram-mb committed,
 * so a stamp entry above the commit faults like a real overrun would. */
static unsigned char *allocate_rdram(void) {
    unsigned char *base = VirtualAlloc(NULL, RDRAM_BYTES, MEM_RESERVE, PAGE_NOACCESS);
    if (!base) return NULL;
    size_t committed = (size_t)g_rdram_committed_mb << 20;
    if (committed > RDRAM_BYTES) committed = RDRAM_BYTES;
    if (!VirtualAlloc(base, committed, MEM_COMMIT, PAGE_READWRITE)) return NULL;
    return base;
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
    HDC device;
    HGLRC context;
    int result;
} drive_job_t;

static int drive_calls(drive_job_t *job) {
    const char *wrapper_path = job->wrapper_path;
    int frames = job->frames;
    HWND window = job->window;
    HDC device = job->device;
    HGLRC context = job->context;
    if (context && !wglMakeCurrent(device, context)) { fprintf(stderr, "wglMakeCurrent failed\n"); return 2; }
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
    unsigned char *rdram = allocate_rdram();
    if (!rdram) { fprintf(stderr, "no fake RDRAM\n"); return 2; }
    GFX_INFO gfx;
    memset(&gfx, 0, sizeof gfx);
    gfx.hWnd = window;
    gfx.MemoryBswaped = TRUE;
    gfx.RDRAM = rdram;
    gfx.DMEM = rdram; gfx.IMEM = rdram; gfx.HEADER = rdram;
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
    if (!api.InitiateGFX(gfx)) { fprintf(stderr, "InitiateGFX failed\n"); return 5; }
    api.RomOpen();
    for (int frame = 0; frame < frames; frame++) {
        unsigned counter = 1000u + (unsigned)frame;
        unsigned pad = 0x11223300u + (unsigned)frame;
        memcpy(rdram + 0, &counter, 4);
        memcpy(rdram + 64, &pad, 4);
        rdram[128] = (unsigned char)frame;
        rdram[129] = (unsigned char)(2 * frame);
        rdram[130] = (unsigned char)(3 * frame);
        rdram[131] = (unsigned char)g_dirty_gl;
        api.ProcessDList();
        g_vi_origin = 0x100000u + (unsigned)frame;
        api.UpdateScreen();
        api.UpdateScreen();              /* the same origin again: no capture */
        MSG message;
        while (PeekMessageA(&message, NULL, 0, 0, PM_REMOVE)) DispatchMessageA(&message);
    }
    printf("host thread %lu: GL context %p after the run\n", (unsigned long)GetCurrentThreadId(),
           (void *)wglGetCurrentContext());
    api.RomClosed();
    api.CloseDLL();
    wglMakeCurrent(NULL, NULL);
    if (context) wglDeleteContext(context);
    ReleaseDC(window, device);
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
    job.window = make_gl_window(&job.device, &job.context);
    if (!job.window) { fprintf(stderr, "no GL window\n"); return 2; }
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
        if (strcmp(argv[index], "--rdram-mb") == 0) g_rdram_committed_mb = (unsigned)atoi(argv[index + 1]);
    }
    for (int index = 1; index < argc; index++)
        if (strcmp(argv[index], "--dirty-gl") == 0) g_dirty_gl = 1;
    for (int index = 1; index < argc; index++)
        if (strcmp(argv[index], "--no-context") == 0) g_no_context = 1;
    for (int index = 1; index < argc; index++)
        if (strcmp(argv[index], "--cpu-thread") == 0) g_cpu_thread = 1;
    if (argc >= 2 && strcmp(argv[1], "--layout") == 0) return print_layout();
    if (argc >= 3 && strcmp(argv[1], "--info") == 0) return info(argv[2]);
    if (argc >= 4 && strcmp(argv[1], "--drive") == 0)
        return drive(argv[2], atoi(argv[3]), stream_name);
    fprintf(stderr, "usage: gfxwrap_host --layout | --info <dll> | --drive <dll> <frames> "
                    "[--stream <name>] [--wrapped <dll>] [--rdram-mb <n>] [--dirty-gl] [--no-context] [--cpu-thread]\n");
    return 1;
}
