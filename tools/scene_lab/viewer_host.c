/* THE VIEWER HOST (scene lab E3): a persistent replay host that renders
 * recorded SM64 graphics tasks on request, so a page can be driven by the
 * REAL GLideN64 without Project64. replay_host.c is the batch oracle; this
 * is the same renderer setup kept alive behind a binary pipe protocol.
 *
 *   viewer_host --renderer <dll> --plugin-dir <dir> --header <128 hex digits>
 *
 * Requests arrive on stdin, responses leave on stdout (little-endian):
 *   request  = magic 'VWRQ', kind (1 render, 2 quit), flags (1 camera,
 *              2 discard), run_count, byte_count, task[16] (OSTask words),
 *              vi[14] (GFX_INFO order), camera[16] (doubles, row-major,
 *              row vectors: view' = view * camera), then run_count
 *              (begin, end) RDRAM storage ranges, then their byte_count bytes.
 *   response = magic 'VWRS', status (bit 0: the camera walk failed, so the
 *              picture is the recorded camera), width, height, render_us,
 *              read_us, camera_us, matrices, pixel_bytes, then ReadScreen's
 *              BGR rows bottom-up (none for a discard request).
 * A request writes only its closure runs: bytes outside a task's closure are
 * never read (the closure proof), so frames need no RDRAM reset and serve in
 * any order. Every request rewrites all of its closure, which also undoes the
 * previous request's camera rewrite.
 *
 * The camera ports recamera.py: every G_MTX MODELVIEW|LOAD under a
 * perspective projection is post-multiplied by `camera`, inside RDRAM, after
 * the runs are written. No camera flag leaves the bytes untouched, so the
 * recorded camera reproduces the recorded picture exactly.
 *
 * replay_host.c documents the renderer's quirks this relies on: the ini is
 * read from the renderer's own folder (hence --plugin-dir), an instance's
 * FIRST list renders wrong (the caller warms with a discard request), and
 * one instance opens one ROM. Same thread shape as Project64 1.6: the window
 * thread pumps while a second thread makes every plugin call; the window is
 * a layered, click-through, never-activated tool window at alpha 1. A
 * malformed request or a renderer that stops responding mid-request ends the
 * process with one line on stderr; EOF or a quit request exits 0. */
#include <windows.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "zilmar.h"

#define RDRAM_BYTES (8u << 20)
#define SP_BYTES 0x2000u
#define HANG_MS 30000u
#define MAX_RUNS 65536u
#define MAX_MATRICES 4096
#define ADDRESS_MASK 0x7FFFFFu
#define REQUEST_MAGIC 0x51525756u   /* "VWRQ" */
#define RESPONSE_MAGIC 0x53525756u  /* "VWRS" */

#pragma pack(push, 1)
typedef struct {
    uint32_t magic, kind, flags, run_count, byte_count;
    uint32_t task[16];
    uint32_t vi[14];
    double camera[16];
} request_t;

typedef struct {
    uint32_t magic, status, width, height, render_us, read_us, camera_us, matrices, pixel_bytes;
} response_t;
#pragma pack(pop)

static unsigned g_vi[14], g_zero, g_mi_intr;
static unsigned char *g_rdram, *g_staging;
static uint32_t *g_runs;
static unsigned char g_sp[SP_BYTES];   /* DMEM then IMEM, as Project64 lays them out */
static unsigned char g_header[0x40];
static volatile LONG g_busy;           /* 1 while a request is inside the renderer */
static volatile ULONGLONG g_progress;  /* tick the current request started */
static const char *g_dll;
static double g_ticks_per_us;

static void check_interrupts(void) {}

static void fail(const char *message) {
    fprintf(stderr, "viewer_host: %s\n", message);
    fflush(stderr);
    /* Not ExitProcess: the renderer's DLL teardown faults mid-session. */
    TerminateProcess(GetCurrentProcess(), 3);
}

static LONG WINAPI on_crash(EXCEPTION_POINTERS *info) {
    fprintf(stderr, "viewer_host: renderer crashed: exception 0x%08lx at %p\n",
            info->ExceptionRecord->ExceptionCode, info->ExceptionRecord->ExceptionAddress);
    fflush(stderr);
    TerminateProcess(GetCurrentProcess(), 9);
    return EXCEPTION_EXECUTE_HANDLER;
}

static uint32_t elapsed_us(LARGE_INTEGER since) {
    LARGE_INTEGER now;
    QueryPerformanceCounter(&now);
    return (uint32_t)((double)(now.QuadPart - since.QuadPart) / g_ticks_per_us);
}

/* 1 on success, 0 on a clean EOF before the first byte; anything else fails. */
static int read_exact(void *buffer, size_t size) {
    HANDLE input = GetStdHandle(STD_INPUT_HANDLE);
    size_t done = 0;
    while (done < size) {
        DWORD chunk = (DWORD)(size - done > (1u << 20) ? (1u << 20) : size - done), got = 0;
        if (!ReadFile(input, (char *)buffer + done, chunk, &got, NULL) || got == 0) {
            if (done == 0) return 0;
            fail("stdin closed inside a request");
        }
        done += got;
    }
    return 1;
}

static void write_exact(const void *buffer, size_t size) {
    HANDLE output = GetStdHandle(STD_OUTPUT_HANDLE);
    size_t done = 0;
    while (done < size) {
        DWORD chunk = (DWORD)(size - done > (1u << 20) ? (1u << 20) : size - done), wrote = 0;
        if (!WriteFile(output, (const char *)buffer + done, chunk, &wrote, NULL) || wrote == 0)
            fail("stdout closed");
        done += wrote;
    }
}

/* RDRAM storage is word-swapped, so an aligned u32 load is the game's word. */
static int word_at(uint32_t address, uint32_t *value) {
    if (address & 3 || address > RDRAM_BYTES - 4) return 0;
    *value = *(uint32_t *)(g_rdram + address);
    return 1;
}

/* N64 Mtx: 16 s16 integer halves, then 16 u16 fraction halves, row-major. */
static void read_matrix(uint32_t address, double out[16]) {
    const uint32_t *words = (const uint32_t *)(g_rdram + address);
    for (int i = 0; i < 16; i++) {
        uint32_t whole = words[i / 2], fraction = words[8 + i / 2];
        int16_t integer = (int16_t)(i & 1 ? whole & 0xFFFF : whole >> 16);
        uint16_t part = (uint16_t)(i & 1 ? fraction & 0xFFFF : fraction >> 16);
        out[i] = ((double)integer * 65536.0 + part) / 65536.0;
    }
}

static void write_matrix(uint32_t address, const double in[16]) {
    uint32_t fixed[16];
    for (int i = 0; i < 16; i++) {
        double scaled = in[i] * 65536.0;   /* s15.16 saturates instead of wrapping */
        scaled = scaled > 2147483647.0 ? 2147483647.0 : scaled < -2147483648.0 ? -2147483648.0 : scaled;
        fixed[i] = (uint32_t)(int32_t)llround(scaled);
    }
    uint32_t *words = (uint32_t *)(g_rdram + address);
    for (int k = 0; k < 8; k++) {
        words[k] = (fixed[2 * k] & 0xFFFF0000u) | (fixed[2 * k + 1] >> 16);
        words[8 + k] = (fixed[2 * k] << 16) | (fixed[2 * k + 1] & 0xFFFFu);
    }
}

static int by_address(const void *a, const void *b) {
    uint32_t left = *(const uint32_t *)a, right = *(const uint32_t *)b;
    return left < right ? -1 : left > right;
}

/* recamera.py's walk: Fast3D G_MOVEWORD segments, G_DL calls and branches,
 * G_ENDDL, and G_MTX. Returns the distinct MODELVIEW|LOAD matrix addresses
 * under a perspective projection, or -1 when the list leaves RDRAM or never
 * ends (the caller then renders the recorded camera). */
static int modelview_loads(uint32_t display_list, uint32_t *found) {
    uint32_t segment[16] = {0}, stack[32];
    int depth = 1, count = 0, perspective = 0;
    stack[0] = display_list & ADDRESS_MASK;
    for (int step = 0; step < (1 << 20); step++) {
        uint32_t w0, w1, at = stack[depth - 1];
        if (!word_at(at, &w0) || !word_at(at + 4, &w1)) return -1;
        stack[depth - 1] += 8;
        uint32_t op = w0 >> 24;
        uint32_t target = (segment[(w1 >> 24) & 0xF] + (w1 & ADDRESS_MASK)) & ADDRESS_MASK;
        if (op == 0xBC && (w0 & 0xFF) == 0x06) {
            segment[(w0 >> 10) & 0xF] = w1 & 0xFFFFFF;
        } else if (op == 0x01 && (w0 & 0xFFFF) == 64) {
            uint32_t param = (w0 >> 16) & 0xFF;
            if (target & 3 || target > RDRAM_BYTES - 64) return -1;
            if (param & 0x01 && param & 0x02) {
                double projection[16];
                read_matrix(target, projection);
                perspective = fabs(projection[11] + 1.0) < 1e-3;
            } else if (!(param & 0x01) && param & 0x02 && perspective) {
                if (count == MAX_MATRICES) return -1;
                found[count++] = target;
            }
        } else if (op == 0x06) {
            if (((w0 >> 16) & 0xFF) == 0) {
                if (depth == 32) return -1;
                stack[depth++] = target;
            } else {
                stack[depth - 1] = target;
            }
        } else if (op == 0xB8) {
            if (--depth == 0) {
                qsort(found, (size_t)count, sizeof *found, by_address);
                int unique = 0;
                for (int i = 0; i < count; i++)
                    if (unique == 0 || found[unique - 1] != found[i]) found[unique++] = found[i];
                return unique;
            }
        }
    }
    return -1;
}

static int apply_camera(const request_t *request) {
    static uint32_t found[MAX_MATRICES];
    int count = modelview_loads(request->task[12], found);
    for (int m = 0; m < count; m++) {
        double before[16], after[16];
        read_matrix(found[m], before);
        for (int row = 0; row < 4; row++)
            for (int column = 0; column < 4; column++) {
                double sum = 0;
                for (int k = 0; k < 4; k++) sum += before[row * 4 + k] * request->camera[k * 4 + column];
                after[row * 4 + column] = sum;
            }
        write_matrix(found[m], after);
    }
    return count;
}

/* Reads one request's runs and bytes into staging, validating every length
 * before RDRAM changes. Returns 0 on a clean EOF, 2 on quit. */
static int read_request(request_t *request) {
    if (!read_exact(request, sizeof *request)) return 0;
    if (request->magic != REQUEST_MAGIC) fail("bad request magic");
    if (request->kind == 2) return 2;
    if (request->kind != 1) fail("unknown request kind");
    if (request->run_count > MAX_RUNS || request->byte_count > RDRAM_BYTES)
        fail("request exceeds the run or byte bound");
    if (request->run_count && !read_exact(g_runs, (size_t)request->run_count * 8)) fail("runs missing");
    uint64_t total = 0;
    for (uint32_t run = 0; run < request->run_count; run++) {
        uint32_t begin = g_runs[2 * run], end = g_runs[2 * run + 1];
        if (begin > end || end > RDRAM_BYTES) fail("a run lies outside RDRAM");
        total += end - begin;
    }
    if (total != request->byte_count) fail("run lengths disagree with byte_count");
    if (request->byte_count && !read_exact(g_staging, request->byte_count)) fail("run bytes missing");
    for (int index = 0; index < 16; index++)
        if (!isfinite(request->camera[index])) fail("camera holds a non-finite number");
    return 1;
}

static DWORD WINAPI serve(LPVOID parameter) {
    HWND window = (HWND)parameter;
    HMODULE module = LoadLibraryExA(g_dll, NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!module) fail("LoadLibrary of the renderer failed");
    gfx_api_t api;
    RESOLVE_GFX_API(api, module);
    if (!api.GetDllInfo || !api.InitiateGFX || !api.RomOpen || !api.ProcessDList || !api.UpdateScreen
        || !api.ReadScreen || !api.RomClosed || !api.CloseDLL)
        fail("the renderer lacks a required export (ReadScreen?)");
    PLUGIN_INFO info;
    memset(&info, 0, sizeof info);
    api.GetDllInfo(&info);
    if (!info.MemoryBswaped) fail("the renderer wants unswapped memory");

    GFX_INFO gfx;
    memset(&gfx, 0, sizeof gfx);
    gfx.hWnd = window;
    gfx.hStatusBar = GetWindow(window, GW_CHILD);   /* replay_host.c: never NULL */
    gfx.MemoryBswaped = TRUE;
    gfx.HEADER = g_header;
    gfx.RDRAM = g_rdram;
    gfx.DMEM = g_sp;
    gfx.IMEM = g_sp + 0x1000;
    gfx.MI_INTR_REG = &g_mi_intr;
    gfx.DPC_START_REG = gfx.DPC_END_REG = gfx.DPC_CURRENT_REG = gfx.DPC_STATUS_REG = &g_zero;
    gfx.DPC_CLOCK_REG = gfx.DPC_BUFBUSY_REG = gfx.DPC_PIPEBUSY_REG = gfx.DPC_TMEM_REG = &g_zero;
    gfx.VI_STATUS_REG = &g_vi[0]; gfx.VI_ORIGIN_REG = &g_vi[1]; gfx.VI_WIDTH_REG = &g_vi[2];
    gfx.VI_INTR_REG = &g_vi[3]; gfx.VI_V_CURRENT_LINE_REG = &g_vi[4]; gfx.VI_TIMING_REG = &g_vi[5];
    gfx.VI_V_SYNC_REG = &g_vi[6]; gfx.VI_H_SYNC_REG = &g_vi[7]; gfx.VI_LEAP_REG = &g_vi[8];
    gfx.VI_H_START_REG = &g_vi[9]; gfx.VI_V_START_REG = &g_vi[10]; gfx.VI_V_BURST_REG = &g_vi[11];
    gfx.VI_X_SCALE_REG = &g_vi[12]; gfx.VI_Y_SCALE_REG = &g_vi[13];
    gfx.CheckInterrupts = check_interrupts;
    if (!api.InitiateGFX(gfx)) fail("InitiateGFX failed");
    api.RomOpen();

    static request_t request;
    for (;;) {
        int kind = read_request(&request);
        if (kind != 1) break;
        g_progress = GetTickCount64();
        InterlockedExchange(&g_busy, 1);
        const unsigned char *bytes = g_staging;
        for (uint32_t run = 0; run < request.run_count; run++) {
            uint32_t begin = g_runs[2 * run], length = g_runs[2 * run + 1] - begin;
            memcpy(g_rdram + begin, bytes, length);
            bytes += length;
        }
        memcpy(g_sp + 0xFC0, request.task, sizeof request.task);
        memcpy(g_vi, request.vi, sizeof g_vi);
        response_t response = {RESPONSE_MAGIC, 0, 0, 0, 0, 0, 0, 0, 0};
        LARGE_INTEGER start;
        QueryPerformanceCounter(&start);
        if (request.flags & 1) {
            int matrices = apply_camera(&request);
            response.status = matrices < 0;
            response.matrices = matrices < 0 ? 0 : (uint32_t)matrices;
            response.camera_us = elapsed_us(start);
            QueryPerformanceCounter(&start);
        }
        api.ProcessDList();
        api.UpdateScreen();
        response.render_us = elapsed_us(start);
        void *pixels = NULL;
        if (!(request.flags & 2)) {
            long width = 0, height = 0;
            QueryPerformanceCounter(&start);
            api.ReadScreen(&pixels, &width, &height);
            response.read_us = elapsed_us(start);
            if (!pixels || width <= 0 || height <= 0 || width > 16384 || height > 16384)
                fail("ReadScreen returned no picture");
            response.width = (uint32_t)width;
            response.height = (uint32_t)height;
            response.pixel_bytes = (uint32_t)(width * height * 3);
        }
        InterlockedExchange(&g_busy, 0);
        write_exact(&response, sizeof response);
        if (pixels) {
            write_exact(pixels, response.pixel_bytes);
            HeapFree(GetProcessHeap(), 0, pixels);   /* the renderer's static CRT uses this heap */
        }
    }
    api.RomClosed();
    api.CloseDLL();
    FreeLibrary(module);
    return 0;
}

static HWND make_window(void) {
    WNDCLASSA wc;
    memset(&wc, 0, sizeof wc);
    wc.lpfnWndProc = DefWindowProcA;
    wc.hInstance = GetModuleHandleA(NULL);
    wc.lpszClassName = "sm64_scene_lab_viewer";
    wc.style = CS_OWNDC;
    if (!RegisterClassA(&wc)) return NULL;
    HWND window = CreateWindowExA(WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED
                                  | WS_EX_TRANSPARENT, "sm64_scene_lab_viewer", "scene lab viewer",
                                  WS_POPUP, 0, 0, 64, 48, NULL, NULL, GetModuleHandleA(NULL), NULL);
    if (!window) return NULL;
    CreateWindowExA(0, "sm64_scene_lab_viewer", "", WS_CHILD, 0, 0, 0, 0, window, NULL,
                    GetModuleHandleA(NULL), NULL);
    SetLayeredWindowAttributes(window, 0, 1, LWA_ALPHA);
    ShowWindow(window, SW_SHOWNOACTIVATE);
    return window;
}

static int copy_renderer(const char *renderer, const char *plugin_dir, char *loaded, size_t size) {
    const char *base = strrchr(renderer, '\\');
    const char *forward = strrchr(renderer, '/');
    if (forward > base) base = forward;
    base = base ? base + 1 : renderer;
    snprintf(loaded, size, "%s\\%s", plugin_dir, base);
    char source[MAX_PATH], target[MAX_PATH];
    if (!GetFullPathNameA(renderer, MAX_PATH, source, NULL) || !GetFullPathNameA(loaded, MAX_PATH, target, NULL))
        return 0;
    return _stricmp(source, target) == 0 || CopyFileA(source, target, FALSE);
}

int main(int argc, char **argv) {
    const char *renderer = NULL, *plugin_dir = NULL, *header = NULL;
    for (int index = 1; index + 1 < argc; index += 2) {
        if (strcmp(argv[index], "--renderer") == 0) renderer = argv[index + 1];
        else if (strcmp(argv[index], "--plugin-dir") == 0) plugin_dir = argv[index + 1];
        else if (strcmp(argv[index], "--header") == 0) header = argv[index + 1];
    }
    if (!renderer || !plugin_dir || !header || strlen(header) != 128) {
        fprintf(stderr, "usage: viewer_host --renderer <dll> --plugin-dir <dir> --header <128 hex>\n");
        return 1;
    }
    for (int byte = 0; byte < 0x40; byte++) {
        unsigned value;
        if (sscanf(header + byte * 2, "%2x", &value) != 1) fail("--header is not 64 hex bytes");
        g_header[byte] = (unsigned char)value;
    }
    SetUnhandledExceptionFilter(on_crash);
    SetProcessDPIAware();   /* replay_host.c: the ini-sized window must fit on screen */
    LARGE_INTEGER frequency;
    QueryPerformanceFrequency(&frequency);
    g_ticks_per_us = (double)frequency.QuadPart / 1e6;
    static char loaded[MAX_PATH];
    if (!copy_renderer(renderer, plugin_dir, loaded, sizeof loaded)) fail("cannot copy the renderer");
    g_dll = loaded;
    g_rdram = VirtualAlloc(NULL, RDRAM_BYTES, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    g_staging = VirtualAlloc(NULL, RDRAM_BYTES, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    g_runs = VirtualAlloc(NULL, MAX_RUNS * 8, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (!g_rdram || !g_staging || !g_runs) fail("cannot allocate RDRAM");
    HWND window = make_window();
    if (!window) fail("cannot create the render window");
    HANDLE thread = CreateThread(NULL, 0, serve, window, 0, NULL);
    if (!thread) fail("cannot start the plugin thread");
    for (;;) {
        DWORD waited = MsgWaitForMultipleObjects(1, &thread, FALSE, 1000, QS_ALLINPUT);
        if (waited == WAIT_OBJECT_0) break;
        MSG message;
        while (PeekMessageA(&message, NULL, 0, 0, PM_REMOVE)) {
            TranslateMessage(&message);
            DispatchMessageA(&message);
        }
        if (g_busy && GetTickCount64() - g_progress > HANG_MS) fail("the renderer stopped responding");
    }
    CloseHandle(thread);
    DestroyWindow(window);
    return 0;
}
