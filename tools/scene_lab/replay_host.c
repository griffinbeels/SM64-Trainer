/* THE REPLAY HOST (scene lab E0/E1): renders captured SM64 graphics tasks
 * through a REAL GLideN64 with no emulator and writes the picture it
 * presented. It is the oracle for the closure proof: if a frame directory
 * (savestate_frames.py) reproduces a picture here, its bytes suffice.
 *
 *   replay_host --renderer <dll> --plugin-dir <dir> --out <dir> [--fresh]
 *               [--warmup <frame_dir>] [--list <file>] frame_dir...
 *
 * --list names a file of frame dirs, one per line, taken in place among the
 * frame_dir arguments.
 *
 * GLideN64 reads GLideN64.ini and GLideN64.custom.ini from its OWN folder,
 * so the host copies --renderer into --plugin-dir and loads that copy.
 * Per frame: rdram.bin -> RDRAM, dmem.bin -> DMEM (OSTask at 0xFC0),
 * frame.json's VI registers, ProcessDList, UpdateScreen, ReadScreen ->
 * <out>/<index>.raw (BGR rows, bottom-up) plus <index>.json. ProcessDList
 * marks the color buffer changed, so with frame buffer emulation off (the
 * live Project64 config) the next UpdateScreen presents it whatever the VI
 * origin. Every frame renders in ONE renderer instance, in the given order;
 * --fresh restarts it per frame (RomClosed, CloseDLL, FreeLibrary and a new
 * window: a window takes one pixel format for life). The ROM header reaches
 * RomOpen through GFX_INFO.HEADER, so per-game settings apply as in
 * Project64; frames from different ROMs therefore need --fresh.
 *
 * Renderer state outlives a list (measured 2026-10-24, LINK 4.2 and the
 * trainer build alike): an instance's FIRST list renders differently from the
 * same list rendered after any other (on u_a, 18% of pixels: water through
 * walls, Mario missing), and every picture Project64 shows is a later one;
 * --warmup renders a frame first in each instance and discards its picture.
 * GLideN64 re-identifies the microcode only when its address changes, so
 * later lists in an instance never re-read the microcode bytes. A list that
 * draws nothing re-presents the previous picture.
 *
 * Project64 1.6's shape: the window's thread pumps messages while a second
 * thread makes every plugin call (gfxwrap/host.c --cpu-thread). The window is
 * a layered, click-through tool window at alpha 1, never activated. Exit 0 on
 * success; any failure prints one line naming the frame and exits non-zero. */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "zilmar.h"

#define RDRAM_BYTES (8u << 20)
#define SP_BYTES 0x2000u
#define HANG_MS 60000u

static const char *VI_NAMES[14] = {"status", "origin", "width", "intr", "v_current", "burst",
    "v_sync", "h_sync", "leap", "h_start", "v_start", "v_burst", "x_scale", "y_scale"};

typedef struct {
    const char *dir;
    unsigned vi[14];          /* GFX_INFO's VI register order */
    unsigned char header[0x40];
} frame_t;

static unsigned g_vi[14], g_zero, g_mi_intr;
static unsigned char *g_rdram;
static unsigned char g_sp[SP_BYTES];   /* DMEM then IMEM, as Project64 lays them out */
static unsigned char g_header[0x40];
static volatile LONG g_current = -1;   /* frame index the worker is on, for crash reports */
static volatile ULONGLONG g_progress;  /* last tick the worker made progress */
static frame_t *g_frames;
static const char *g_out;
static int g_warmup = -1;              /* g_frames index of --warmup, or -1 */

static void check_interrupts(void) {}

static void fail(const char *message, int index) {
    if (index >= 0) fprintf(stderr, "frame %d (%s): %s\n", index, g_frames[index].dir, message);
    else fprintf(stderr, "%s\n", message);
    fflush(stderr);
    /* Not ExitProcess: the renderer's DLL teardown faults mid-session. */
    TerminateProcess(GetCurrentProcess(), 3);
}

static LONG WINAPI on_crash(EXCEPTION_POINTERS *info) {
    LONG index = g_current;
    fprintf(stderr, "renderer crashed: exception 0x%08lx at %p on frame %ld (%s)\n",
            info->ExceptionRecord->ExceptionCode, info->ExceptionRecord->ExceptionAddress,
            index, index >= 0 ? g_frames[index].dir : "before the first frame");
    fflush(stderr);
    TerminateProcess(GetCurrentProcess(), 9);
    return EXCEPTION_EXECUTE_HANDLER;
}

static unsigned char *read_file(const char *dir, const char *name, size_t *size) {
    char path[MAX_PATH];
    snprintf(path, sizeof path, "%s\\%s", dir, name);
    FILE *file = fopen(path, "rb");
    if (!file) return NULL;
    fseek(file, 0, SEEK_END);
    long length = ftell(file);
    fseek(file, 0, SEEK_SET);
    unsigned char *data = malloc((size_t)length + 1);
    if (!data || fread(data, 1, (size_t)length, file) != (size_t)length) {
        fclose(file);
        free(data);
        return NULL;
    }
    fclose(file);
    data[length] = 0;
    *size = (size_t)length;
    return data;
}

/* frame.json is ours (scene-lab-frame/1): flat numbers inside "vi", one hex
 * string "rom_header". A key search inside the vi object is enough. */
static int parse_frame(frame_t *frame, int index) {
    size_t size;
    char *json = (char *)read_file(frame->dir, "frame.json", &size);
    if (!json) fail("cannot read frame.json", index);
    if (!strstr(json, "\"scene-lab-frame/1\"")) fail("frame.json is not scene-lab-frame/1", index);
    char *vi = strstr(json, "\"vi\"");
    char *open = vi ? strchr(vi, '{') : NULL;
    char *close = open ? strchr(open, '}') : NULL;
    if (!close) fail("frame.json has no vi object", index);
    for (int reg = 0; reg < 14; reg++) {
        char key[32];
        snprintf(key, sizeof key, "\"%s\"", VI_NAMES[reg]);
        char *at = strstr(open, key);
        if (!at || at > close) fail("frame.json vi lacks a register", index);
        at = strchr(at + strlen(key), ':');
        frame->vi[reg] = (unsigned)strtoul(at + 1, NULL, 10);
    }
    char *header = strstr(json, "\"rom_header\"");
    header = header ? strchr(header + 12, '"') : NULL;
    if (!header) fail("frame.json has no rom_header", index);
    for (int byte = 0; byte < 0x40; byte++) {
        unsigned value;
        if (sscanf(header + 1 + byte * 2, "%2x", &value) != 1)
            fail("rom_header is not 64 hex bytes", index);
        frame->header[byte] = (unsigned char)value;
    }
    free(json);
    return 0;
}

static void load_frame(int index) {
    frame_t *frame = &g_frames[index];
    size_t size;
    unsigned char *rdram = read_file(frame->dir, "rdram.bin", &size);
    if (!rdram || size == 0 || size > RDRAM_BYTES || size % 4) fail("rdram.bin missing or not <= 8 MiB of words", index);
    memcpy(g_rdram, rdram, size);
    memset(g_rdram + size, 0, RDRAM_BYTES - size);
    free(rdram);
    unsigned char *dmem = read_file(frame->dir, "dmem.bin", &size);
    if (!dmem || size != 0x1000) fail("dmem.bin missing or not 4 KiB", index);
    memcpy(g_sp, dmem, size);
    free(dmem);
    memcpy(g_vi, frame->vi, sizeof g_vi);
}

static void write_output(int index, int instance, const char *renderer, void *pixels, long width,
                         long height) {
    char path[MAX_PATH];
    snprintf(path, sizeof path, "%s\\%04d.raw", g_out, index);
    FILE *file = fopen(path, "wb");
    size_t bytes = (size_t)width * (size_t)height * 3;
    if (!file || fwrite(pixels, 1, bytes, file) != bytes) fail("cannot write the .raw output", index);
    fclose(file);
    char dir[MAX_PATH];
    strncpy_s(dir, sizeof dir, g_frames[index].dir, _TRUNCATE);
    for (char *at = dir; *at; at++) {
        if (*at == '\\') *at = '/';
        else if (*at == '"') *at = '\'';
    }
    snprintf(path, sizeof path, "%s\\%04d.json", g_out, index);
    file = fopen(path, "w");
    if (!file) fail("cannot write the .json output", index);
    fprintf(file, "{\"schema\": \"scene-lab-render/1\", \"index\": %d, \"instance\": %d, "
                  "\"frame\": \"%s\", \"renderer\": \"%s\", \"width\": %ld, \"height\": %ld, "
                  "\"format\": \"bgr24-bottom-up\"}\n", index, instance, dir, renderer, width, height);
    fclose(file);
}

typedef struct {
    const char *dll;
    HWND window;
    int first, count, instance;
} session_t;

/* Load the renderer, render frames [first, first+count) in order, unload it. */
static DWORD WINAPI run_session(LPVOID parameter) {
    session_t *session = (session_t *)parameter;
    HMODULE module = LoadLibraryExA(session->dll, NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!module) fail("LoadLibrary of the renderer failed", session->first);
    gfx_api_t api;
    RESOLVE_GFX_API(api, module);
    if (!api.GetDllInfo || !api.InitiateGFX || !api.RomOpen || !api.ProcessDList || !api.UpdateScreen
        || !api.ReadScreen || !api.RomClosed || !api.CloseDLL)
        fail("the renderer lacks a required export (ReadScreen?)", session->first);
    PLUGIN_INFO info;
    memset(&info, 0, sizeof info);
    api.GetDllInfo(&info);
    if (!info.MemoryBswaped) fail("the renderer wants unswapped memory", session->first);
    char name[sizeof info.Name];
    strncpy_s(name, sizeof name, info.Name, _TRUNCATE);
    for (char *at = name; *at; at++) if (*at == '"' || *at == '\\') *at = '\'';

    GFX_INFO gfx;
    memset(&gfx, 0, sizeof gfx);
    gfx.hWnd = session->window;
    /* A zero-height child stands in for Project64's status bar: GLideN64
     * measures hStatusBar's rect unconditionally, so NULL would leave it
     * reading an uninitialized RECT for its read offset. */
    gfx.hStatusBar = GetWindow(session->window, GW_CHILD);
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

    memcpy(g_header, g_frames[session->first].header, sizeof g_header);
    if (!api.InitiateGFX(gfx)) fail("InitiateGFX failed", session->first);
    api.RomOpen();
    if (g_warmup >= 0) {
        InterlockedExchange(&g_current, g_warmup);
        load_frame(g_warmup);
        api.ProcessDList();
        api.UpdateScreen();
    }
    for (int index = session->first; index < session->first + session->count; index++) {
        InterlockedExchange(&g_current, index);
        g_progress = GetTickCount64();
        load_frame(index);
        api.ProcessDList();
        api.UpdateScreen();
        void *pixels = NULL;
        long width = 0, height = 0;
        api.ReadScreen(&pixels, &width, &height);
        if (!pixels || width <= 0 || height <= 0) fail("ReadScreen returned no picture", index);
        write_output(index, session->instance, name, pixels, width, height);
        /* The renderer's static CRT allocates from the process heap. */
        HeapFree(GetProcessHeap(), 0, pixels);
        printf("frame %d instance %d %ldx%ld %s\n", index, session->instance, width, height,
               g_frames[index].dir);
        fflush(stdout);
    }
    api.RomClosed();
    api.CloseDLL();
    FreeLibrary(module);
    return 0;
}

static HWND make_window(void) {
    static ATOM klass;
    if (!klass) {
        WNDCLASSA wc;
        memset(&wc, 0, sizeof wc);
        wc.lpfnWndProc = DefWindowProcA;
        wc.hInstance = GetModuleHandleA(NULL);
        wc.lpszClassName = "sm64_scene_lab_replay";
        wc.style = CS_OWNDC;
        klass = RegisterClassA(&wc);
    }
    HWND window = CreateWindowExA(WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED
                                  | WS_EX_TRANSPARENT, "sm64_scene_lab_replay", "scene lab replay",
                                  WS_POPUP, 0, 0, 64, 48, NULL, NULL, GetModuleHandleA(NULL), NULL);
    if (!window) return NULL;
    CreateWindowExA(0, "sm64_scene_lab_replay", "", WS_CHILD, 0, 0, 0, 0, window, NULL,
                    GetModuleHandleA(NULL), NULL);
    SetLayeredWindowAttributes(window, 0, 1, LWA_ALPHA);
    ShowWindow(window, SW_SHOWNOACTIVATE);
    return window;
}

/* The window's thread pumps while the session thread makes the plugin calls;
 * a session that stops making progress (a renderer MessageBox, a deadlock)
 * ends the process rather than hanging the caller. */
static void run_pumped(session_t *session) {
    session->window = make_window();
    if (!session->window) fail("cannot create the render window", session->first);
    g_progress = GetTickCount64();
    HANDLE thread = CreateThread(NULL, 0, run_session, session, 0, NULL);
    if (!thread) fail("cannot start the plugin thread", session->first);
    for (;;) {
        DWORD waited = MsgWaitForMultipleObjects(1, &thread, FALSE, 1000, QS_ALLINPUT);
        if (waited == WAIT_OBJECT_0) break;
        MSG message;
        while (PeekMessageA(&message, NULL, 0, 0, PM_REMOVE)) {
            TranslateMessage(&message);
            DispatchMessageA(&message);
        }
        if (GetTickCount64() - g_progress > HANG_MS) fail("the renderer stopped responding", g_current);
    }
    CloseHandle(thread);
    DestroyWindow(session->window);
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
    const char *renderer = NULL, *plugin_dir = NULL;
    const char *warmup = NULL;
    int fresh = 0, count = 0, capacity = argc;
    g_frames = calloc((size_t)capacity, sizeof *g_frames);
    for (int index = 1; index < argc; index++) {
        if (strcmp(argv[index], "--list") == 0 && index + 1 < argc) {
            /* One frame dir per line: a long session overflows the command line. */
            FILE *list = fopen(argv[++index], "r");
            if (!list) { fprintf(stderr, "cannot read %s\n", argv[index]); return 1; }
            char line[1024];
            while (fgets(line, sizeof line, list)) {
                line[strcspn(line, "\r\n")] = 0;
                if (!line[0]) continue;
                if (count + 1 >= capacity) {   /* keep the --warmup slot after the last frame */
                    g_frames = realloc(g_frames, (size_t)capacity * 2 * sizeof *g_frames);
                    memset(g_frames + capacity, 0, (size_t)capacity * sizeof *g_frames);
                    capacity *= 2;
                }
                g_frames[count++].dir = _strdup(line);
            }
            fclose(list);
        }
        else if (strcmp(argv[index], "--renderer") == 0 && index + 1 < argc) renderer = argv[++index];
        else if (strcmp(argv[index], "--plugin-dir") == 0 && index + 1 < argc) plugin_dir = argv[++index];
        else if (strcmp(argv[index], "--out") == 0 && index + 1 < argc) g_out = argv[++index];
        else if (strcmp(argv[index], "--fresh") == 0) fresh = 1;
        else if (strcmp(argv[index], "--warmup") == 0 && index + 1 < argc) warmup = argv[++index];
        else if (argv[index][0] == '-') { fprintf(stderr, "unknown option %s\n", argv[index]); return 1; }
        else g_frames[count++].dir = argv[index];
    }
    if (!renderer || !plugin_dir || !g_out || !count) {
        fprintf(stderr, "usage: replay_host --renderer <dll> --plugin-dir <dir> --out <dir> "
                        "[--fresh] [--warmup <frame_dir>] frame_dir...\n");
        return 1;
    }
    SetUnhandledExceptionFilter(on_crash);
    /* Per-monitor pixels: the renderer sizes the window from its ini, and an
     * unscaled window fits on screen, where front-buffer reads are owned. */
    SetProcessDPIAware();
    CreateDirectoryA(g_out, NULL);
    char loaded[MAX_PATH];
    if (!copy_renderer(renderer, plugin_dir, loaded, sizeof loaded))
        fail("cannot copy the renderer into --plugin-dir", -1);
    if (warmup) {
        g_warmup = count;
        g_frames[count].dir = warmup;
        parse_frame(&g_frames[count], count);
    }
    for (int index = 0; index < count; index++) {
        parse_frame(&g_frames[index], index);
        if (!fresh && memcmp(g_frames[index].header, g_frames[0].header, 0x40) != 0)
            fail("a different ROM than the first frame: one instance opens one ROM; use --fresh", index);
        if (warmup && memcmp(g_frames[index].header, g_frames[g_warmup].header, 0x40) != 0)
            fail("a different ROM than the --warmup frame", index);
    }
    g_rdram = VirtualAlloc(NULL, RDRAM_BYTES, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (!g_rdram) fail("cannot allocate RDRAM", -1);
    session_t session;
    memset(&session, 0, sizeof session);
    session.dll = loaded;
    if (fresh) {
        for (int index = 0; index < count; index++) {
            session.first = index;
            session.count = 1;
            session.instance = index;
            run_pumped(&session);
        }
    } else {
        session.count = count;
        run_pumped(&session);
    }
    VirtualFree(g_rdram, 0, MEM_RELEASE);
    printf("rendered %d frames in %d instance(s)\n", count, fresh ? count : 1);
    return 0;
}
