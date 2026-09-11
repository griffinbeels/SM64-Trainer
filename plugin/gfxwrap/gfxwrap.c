/* THE CAPTURE LAYER: a wrapper graphics plugin for Project64 1.6.
 *
 * Round 32 item 95, 2026-09-04. It forwards every Zilmar-spec call to the
 * user's own graphics plugin (named by `wrapped=` in sm64_trainer_gfx.ini
 * beside this DLL) and, on the emulation thread, does two extra things:
 *
 *   ProcessDList  -- after forwarding, copies the tracker's address table out
 *                    of RDRAM into a PENDING stamp. The list the game just
 *                    sent IS frame N: gGlobalTimer still reads N (the game
 *                    increments it two VIs later), the pad in RDRAM is N's,
 *                    so every byte copied here belongs to the picture this
 *                    list draws.
 *   UpdateScreen  -- after forwarding, when VI_ORIGIN changed (the game
 *                    swapped a new buffer in -- one VI per game frame) and
 *                    the tracker wants frames: glReadPixels(GL_FRONT) of the
 *                    render window into the next ring slot, the pending
 *                    stamp beside it, published with a barrier and an event.
 *
 * The plugin knows NO game address: the tracker writes {rdram_offset, length}
 * entries into the stream header (memory/layout.py stays the one source of
 * addresses) and this copies bytes. It reads the emulator's memory and the
 * GPU; it writes only its own mapping. Without a live capture request the
 * callbacks forward, update liveness/diagnostic counters, and copy no RAM or
 * pixels. GL is never queried on that path.
 *
 * Reading GL_FRONT after the wrapped plugin's swap is exactly what GLideN64's
 * own screenshot path does (windows_DisplayWindow.cpp::_readScreen), on the
 * same thread, which is why it is the first capture point tried. It needs a
 * GL context current on the emulation thread, and GLideN64_LINK_4.2 has
 * none there: MEASURED 2026-09-05 through the test host driving that DLL
 * the way PJ64 does (window thread pumping, plugin calls on a second
 * thread) -- no context at InitiateGFX, RomOpen, ProcessDList or
 * UpdateScreen, and a host whose window thread is also the caller
 * deadlocks in RomOpen, because that build runs every GL call on a render
 * thread of its own (its RTTI names ReadScreenCommand and friends: the
 * threaded-video command queue, with no setting to turn it off). So the
 * second capture point is the wrapped plugin's own ReadScreen: a command
 * that render thread executes in order, after the swap, so the bytes it
 * hands back ARE the presented picture, already offset past PJ64's status
 * bar. It mallocs that buffer from its static CRT (linker 14.30, no
 * ucrtbase import): a VS2015+ static CRT allocates on the PROCESS heap,
 * which HeapValidate confirms before HeapFree returns the block -- a block
 * that fails validation is leaked once and the path retired, never freed
 * blind. The header's STATUS_READSCREEN bit says which path a session's
 * pictures took. */
#include <windows.h>
#include <GL/gl.h>
#include <stdio.h>
#include <string.h>
#include "zilmar.h"
#include "stream.h"
#include "capture_lease.h"

#ifndef GL_BGR_EXT
#define GL_BGR_EXT 0x80E0
#endif

#define EXPORT __declspec(dllexport)
#define CALL __cdecl

static HMODULE g_self;
static HMODULE g_wrapped_module;
static gfx_api_t g_wrapped;
static char g_wrapped_name[MAX_PATH];
static char g_stream_name[128] = STREAM_NAME;
static BOOL g_loaded_once;
static GFX_INFO g_gfx;
static BOOL g_have_gfx;

static HANDLE g_map, g_event;
static stream_header_t *g_hdr;
static uint8_t *g_slots;
static unsigned g_last_origin = 0xFFFFFFFFu;
static capture_lease_t g_capture_lease;

typedef struct {
    int64_t list_qpc;
    unsigned count;
    unsigned lengths[TABLE_ENTRIES];
    uint8_t bytes[TABLE_ENTRIES][TABLE_ENTRY_BYTES];
    unsigned lists_since;
} pending_t;
static pending_t g_pending;

static int64_t qpc_now(void) {
    LARGE_INTEGER counter;
    QueryPerformanceCounter(&counter);
    return counter.QuadPart;
}

/* -- the ini beside this DLL: wrapped=<file>, stream=<name> -------------- */
static void self_dir(wchar_t *out, size_t count) {
    out[0] = L'\0';
    if (GetModuleFileNameW(g_self, out, (DWORD)count) == 0) return;
    wchar_t *slash = wcsrchr(out, L'\\');
    if (slash) *(slash + 1) = L'\0';
}

#include "diagnostics.h"
#include "profile.h"

/* The refusal itself, once per flip: the header's counter says how many,
 * this line says why and on which thread. */
static int g_context_state = -1;   /* -1 unknown, 0 refused, 1 reading */
static BOOL g_context_reported;
static uint32_t g_context_report_tick;

static void note_capture_context(BOOL have_context) {
    int state = have_context ? 1 : 0;
    if (state == g_context_state) return;
    g_context_state = state;
    uint32_t now = GetTickCount();
    if (g_context_reported && (uint32_t)(now - g_context_report_tick) < 5000) return;
    g_context_reported = TRUE;
    g_context_report_tick = now;
    char message[160];
    if (have_context)
        snprintf(message, sizeof message, "GL context found on thread %lu; pictures flow",
                 (unsigned long)GetCurrentThreadId());
    else
        snprintf(message, sizeof message,
                 "no GL context on thread %lu (the one that calls UpdateScreen); "
                 "pictures go through the wrapped plugin's ReadScreen%s",
                 (unsigned long)GetCurrentThreadId(),
                 g_wrapped.ReadScreen ? "" : " -- which it does not export, so none");
    plugin_log(message);
}

static BOOL g_ini_read;

static void read_ini(void) {
    if (g_ini_read) return;
    g_ini_read = TRUE;
    strncpy_s(g_stream_name, sizeof g_stream_name, STREAM_NAME, _TRUNCATE);
    wchar_t path[MAX_PATH];
    self_dir(path, MAX_PATH);
    wcsncat_s(path, MAX_PATH, L"sm64_trainer_gfx.ini", _TRUNCATE);
    FILE *ini = _wfopen(path, L"r");
    if (!ini) return;
    char line[512];
    while (fgets(line, sizeof line, ini)) {
        char *end = line + strlen(line);
        while (end > line && (end[-1] == '\n' || end[-1] == '\r' || end[-1] == ' '))
            *--end = '\0';
        char *start = line;
        while (*start == ' ' || *start == '\t') start++;
        if (strncmp(start, "wrapped=", 8) == 0)
            strncpy_s(g_wrapped_name, sizeof g_wrapped_name, start + 8, _TRUNCATE);
        else if (strncmp(start, "stream=", 7) == 0)
            strncpy_s(g_stream_name, sizeof g_stream_name, start + 7, _TRUNCATE);
    }
    fclose(ini);
}

/* Load the wrapped plugin. NOT from GetDllInfo: Project64 enumerates every
 * DLL in its Plugin folder by loading it, asking GetDllInfo and freeing it
 * again, and a plugin loaded from inside that enumeration would leak one
 * reference per pass (review finding 16). So the dialog is answered from
 * the ini alone, and the real plugin loads at InitiateGFX. */
static void ensure_wrapped(void) {
    if (g_loaded_once) return;
    g_loaded_once = TRUE;
    read_ini();
    if (!g_wrapped_name[0]) { plugin_log("no wrapped= line in sm64_trainer_gfx.ini"); return; }
    wchar_t name[MAX_PATH];
    if (MultiByteToWideChar(CP_UTF8, 0, g_wrapped_name, -1, name, MAX_PATH) == 0) return;
    wchar_t path[MAX_PATH];
    if (wcschr(name, L':') || name[0] == L'\\' || name[0] == L'/') {
        wcsncpy_s(path, MAX_PATH, name, _TRUNCATE);
    } else {
        self_dir(path, MAX_PATH);
        wcsncat_s(path, MAX_PATH, name, _TRUNCATE);
    }
    g_wrapped_module = LoadLibraryExW(path, NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!g_wrapped_module) {
        char message[MAX_PATH + 64];
        snprintf(message, sizeof message, "LoadLibrary(%s) failed: %lu", g_wrapped_name, GetLastError());
        plugin_log(message);
        return;
    }
    if (g_wrapped_module == g_self ||
            GetProcAddress(g_wrapped_module, "SM64TrainerWrapperIdentity")) {
        /* The ini names THIS DLL (a copy, or an install that wrote the
         * wrapper's own name): forwarding would recurse until the stack
         * died. Stay unwrapped and say so through GetDllInfo. */
        FreeLibrary(g_wrapped_module);
        g_wrapped_module = NULL;
        plugin_log("wrapped= names the capture layer itself; staying unwrapped");
        return;
    }
    RESOLVE_GFX_API(g_wrapped, g_wrapped_module);
    /* A wrapper's own complete export table must not mask a broken wrapped
     * plugin which Project64 would have rejected when loaded directly. */
    const char *missing = NULL;
#define REQUIRE_EXPORT(name) if (!g_wrapped.name && !missing) missing = #name
    REQUIRE_EXPORT(GetDllInfo); REQUIRE_EXPORT(InitiateGFX); REQUIRE_EXPORT(CloseDLL);
    REQUIRE_EXPORT(ChangeWindow); REQUIRE_EXPORT(DrawScreen); REQUIRE_EXPORT(MoveScreen);
    REQUIRE_EXPORT(ProcessDList); REQUIRE_EXPORT(RomClosed); REQUIRE_EXPORT(RomOpen);
    REQUIRE_EXPORT(UpdateScreen); REQUIRE_EXPORT(ViStatusChanged); REQUIRE_EXPORT(ViWidthChanged);
#undef REQUIRE_EXPORT
    if (missing) {
        plugin_logf("wrapped_export_missing", "name=%s", missing);
        FreeLibrary(g_wrapped_module); g_wrapped_module = NULL;
        memset(&g_wrapped, 0, sizeof g_wrapped);
        return;
    }
    log_module_path("wrapped_loaded", g_wrapped_module);
}

static void release_wrapped(void) {
    if (g_wrapped_module) FreeLibrary(g_wrapped_module);
    g_wrapped_module = NULL;
    memset(&g_wrapped, 0, sizeof g_wrapped);
    g_loaded_once = FALSE;
    g_ini_read = FALSE;
    g_wrapped_name[0] = '\0';
}

/* How much of RDRAM is really there. GFX_INFO carries no size, and the
 * tracker's claim (8 MB, the expansion pak) can exceed a 4 MB configuration;
 * a copy past the allocation would take the emulator down mid-run. So the
 * committed span from RDRAM's base is measured, and every copy runs under a
 * structured-exception guard as well -- a bad page becomes a dropped stamp,
 * never a crash. Measured at InitiateGFX, at RomOpen, and again whenever an
 * entry sits past the span but inside the tracker's claim: his first three
 * plugin clips (2026-09-05) carried no IGT at all, because the span was
 * read once at InitiateGFX, before the ROM's expansion pak was committed,
 * and `usamune_overall` (above 4 MB) was refused all session. */
static size_t g_rdram_span;

static void measure_rdram(void) {
    MEMORY_BASIC_INFORMATION info;
    g_rdram_span = 0;
    if (!g_gfx.RDRAM) return;
    if (VirtualQuery(g_gfx.RDRAM, &info, sizeof info) != sizeof info) return;
    if (info.State != MEM_COMMIT) return;
    g_rdram_span = (size_t)((char *)info.BaseAddress + info.RegionSize - (char *)g_gfx.RDRAM);
}

static BOOL copy_guarded(void *destination, const void *source, size_t length) {
    __try {
        memcpy(destination, source, length);
        return TRUE;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return FALSE;
    }
}

/* -- the frame stream --------------------------------------------------- */
static void open_stream(void) {
    if (g_hdr) return;
    memset(&g_capture_lease, 0, sizeof g_capture_lease);
    g_map = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE,
                               0, (DWORD)TOTAL_BYTES, g_stream_name);
    if (!g_map) {
        /* ERROR_ACCESS_DENIED here is usually an elevation mismatch: an
         * elevated Project64 cannot open a mapping a normal tracker made. */
        char message[160];
        snprintf(message, sizeof message, "CreateFileMapping(%s) failed: %lu", g_stream_name, GetLastError());
        plugin_log(message);
        return;
    }
    BOOL existed = GetLastError() == ERROR_ALREADY_EXISTS;
    void *view = MapViewOfFile(g_map, FILE_MAP_ALL_ACCESS, 0, 0, TOTAL_BYTES);
    if (!view) {
        /* 150 MB must be contiguous in a 32-bit process; a fragmented
         * address space is the usual cause of this one. */
        char message[160];
        snprintf(message, sizeof message, "MapViewOfFile(%u bytes) failed: %lu", (unsigned)TOTAL_BYTES, GetLastError());
        plugin_log(message);
        CloseHandle(g_map); g_map = NULL; return;
    }
    g_hdr = (stream_header_t *)view;
    g_slots = (uint8_t *)view + HEADER_BYTES;
    if (!existed || memcmp(g_hdr->magic, STREAM_MAGIC, 8) != 0) {
        memcpy(g_hdr->magic, STREAM_MAGIC, 8);
        g_hdr->version = STREAM_VERSION;
        g_hdr->header_bytes = HEADER_BYTES;
        g_hdr->slot_count = SLOT_COUNT;
        g_hdr->slot_bytes = SLOT_BYTES;
        g_hdr->slots_offset = HEADER_BYTES;
    }
    char event_name[160];
    snprintf(event_name, sizeof event_name, "%s%s", g_stream_name, STREAM_EVENT_SUFFIX);
    g_event = CreateEventA(NULL, FALSE, FALSE, event_name);
    if (!g_event) plugin_logf("frame_event_failed", "error=%lu", GetLastError());
    g_hdr->plugin_pid = GetCurrentProcessId();
    g_hdr->plugin_version = GFXWRAP_VERSION;
    if (g_wrapped_module) g_hdr->status |= STATUS_WRAPPED_LOADED;
    profile_open(g_stream_name);
    plugin_logf("stream_open", "name=%s bytes=%u existed=%d profile=%d",
        g_stream_name, (unsigned)TOTAL_BYTES, existed, g_profile != NULL);
}

static void close_stream(void) {
    profile_close();
    /* Leaving: say so in the header, so a reader does not wait on a
     * plugin that unloaded (or a process that died with this DLL's
     * detach still running) as if it were merely quiet. */
    if (g_hdr) g_hdr->status &= ~(uint32_t)(STATUS_INITIATED | STATUS_ROM_OPEN
                                             | STATUS_GL_CONTEXT | STATUS_READSCREEN);
    if (g_hdr) UnmapViewOfFile(g_hdr);
    if (g_map) CloseHandle(g_map);
    if (g_event) CloseHandle(g_event);
    g_hdr = NULL; g_slots = NULL; g_map = NULL; g_event = NULL;
}

static stream_slot_t *slot_at(unsigned index) {
    return (stream_slot_t *)(g_slots + (size_t)index * SLOT_BYTES);
}

/* Observe demand at BOTH capture points. Resuming between a display list and
 * its VI must never attach an old stamp to the first new picture. */
static int g_capture_state = -1;
static BOOL g_capture_reported;
static uint32_t g_capture_report_tick;
static BOOL capture_requested(void) {
    if (!g_hdr || !g_have_gfx) return FALSE;
    uint32_t heartbeat = *(volatile uint32_t *)&g_hdr->tracker_alive;
    uint32_t want = *(volatile uint32_t *)&g_hdr->want_frames;
    uint32_t now = GetTickCount();
    BOOL active = capture_lease_active(&g_capture_lease, heartbeat, want, now);
    if ((int)active != g_capture_state) {
        g_capture_state = active;
        memset(&g_pending, 0, sizeof g_pending);
        if (!g_capture_reported || (uint32_t)(now - g_capture_report_tick) >= 5000) {
            g_capture_reported = TRUE;
            g_capture_report_tick = now;
            plugin_logf("capture_demand", "active=%d want=%u heartbeat=%u", active, want, heartbeat);
        }
    }
    return active;
}

/* -- the two capture points --------------------------------------------- */
static void stamp_pending(void) {
    if (!g_hdr || !g_have_gfx) return;
    unsigned count = g_hdr->table_count;
    if (count > TABLE_ENTRIES) count = TABLE_ENTRIES;
    g_pending.count = count;
    g_pending.list_qpc = qpc_now();
    size_t limit = g_hdr->rdram_bytes;
    if (limit > (8u << 20)) limit = 8u << 20; /* hardware maximum, never a game address */
    if (g_rdram_span && g_rdram_span < limit) {
        for (unsigned index = 0; index < count; index++) {
            uint64_t end = (uint64_t)g_hdr->table[index].rdram_offset + g_hdr->table[index].length;
            if (end > g_rdram_span && end <= limit) { measure_rdram(); break; }
        }
        if (g_rdram_span && g_rdram_span < limit) limit = g_rdram_span;
    }
    for (unsigned index = 0; index < count; index++) {
        unsigned offset = g_hdr->table[index].rdram_offset;
        unsigned length = g_hdr->table[index].length;
        if (length == 0 || length > TABLE_ENTRY_BYTES
                || (size_t)offset + length > limit || offset + length < offset
                || !copy_guarded(g_pending.bytes[index], g_gfx.RDRAM + offset, length)) {
            g_pending.lengths[index] = 0;
            continue;
        }
        g_pending.lengths[index] = length;
    }
    g_pending.lists_since++;
}

/* The wrapped plugin may leave a framebuffer object, a pixel-pack buffer or
 * its own pack parameters bound (GLideN64 is FBO- and PBO-heavy). With an
 * FBO bound, glReadBuffer(GL_FRONT) fails silently and glReadPixels reads
 * the plugin's internal render target; with a pack buffer bound, the pixel
 * pointer becomes an offset into GPU memory. So the read binds the window's
 * own framebuffer and no pack buffer, sets the pack parameters it relies
 * on, and puts every one of them back -- the plugin never sees a change.
 * The two binding calls are GL 1.5 / 3.0 entry points, fetched once. */
#define GL_READ_FRAMEBUFFER 0x8CA8
#define GL_READ_FRAMEBUFFER_BINDING 0x8CAA
#define GL_PIXEL_PACK_BUFFER 0x88EB
#define GL_PIXEL_PACK_BUFFER_BINDING 0x88ED
typedef void (WINAPI *fn_bind_framebuffer)(GLenum, GLuint);
typedef void (WINAPI *fn_bind_buffer)(GLenum, GLuint);
static fn_bind_framebuffer g_bind_framebuffer;
static fn_bind_buffer g_bind_buffer;
static BOOL g_gl_entry_points_looked_up;
static HGLRC g_gl_context;

static PROC valid_gl_proc(const char *name) {
    PROC address = wglGetProcAddress(name);
    uintptr_t value = (uintptr_t)address;
    return value <= 3 || value == UINTPTR_MAX ? NULL : address;
}

static void look_up_gl_entry_points(void) {
    if (g_gl_entry_points_looked_up) return;
    g_gl_entry_points_looked_up = TRUE;
    g_bind_framebuffer = (fn_bind_framebuffer)valid_gl_proc("glBindFramebuffer");
    g_bind_buffer = (fn_bind_buffer)valid_gl_proc("glBindBuffer");
}

static void read_front_buffer(void *pixels, unsigned width, unsigned height,
                              unsigned bottom_offset) {
    int64_t measured = profile_mark();
    look_up_gl_entry_points();
    GLint read_buffer = GL_BACK, read_framebuffer = 0, pack_buffer = 0;
    GLint pack[4] = {4, 0, 0, 0};      /* alignment, row length, skip rows, skip pixels */
    glGetIntegerv(GL_PACK_ALIGNMENT, &pack[0]);
    glGetIntegerv(GL_PACK_ROW_LENGTH, &pack[1]);
    glGetIntegerv(GL_PACK_SKIP_ROWS, &pack[2]);
    glGetIntegerv(GL_PACK_SKIP_PIXELS, &pack[3]);
    if (g_bind_framebuffer) {
        glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &read_framebuffer);
        if (read_framebuffer) g_bind_framebuffer(GL_READ_FRAMEBUFFER, 0);
    }
    /* GL_READ_BUFFER belongs to the bound framebuffer. Save the window's
     * selector AFTER binding it; an FBO's COLOR_ATTACHMENT0 is not a valid
     * selector to restore on the default framebuffer. */
    glGetIntegerv(GL_READ_BUFFER, &read_buffer);
    if (g_bind_buffer) {
        glGetIntegerv(GL_PIXEL_PACK_BUFFER_BINDING, &pack_buffer);
        if (pack_buffer) g_bind_buffer(GL_PIXEL_PACK_BUFFER, 0);
    }
    glPixelStorei(GL_PACK_ALIGNMENT, 4);
    glPixelStorei(GL_PACK_ROW_LENGTH, 0);
    glPixelStorei(GL_PACK_SKIP_ROWS, 0);
    glPixelStorei(GL_PACK_SKIP_PIXELS, 0);
    glReadBuffer(GL_FRONT);
    profile_end_stage(PR_GL_SETUP, measured);
    measured = profile_mark();
    glReadPixels(0, (GLint)bottom_offset, (GLsizei)width, (GLsizei)height,
                 GL_BGR_EXT, GL_UNSIGNED_BYTE, pixels);
    profile_end_stage(PR_GL_READ, measured);
    measured = profile_mark();
    glReadBuffer((GLenum)read_buffer);
    glPixelStorei(GL_PACK_ALIGNMENT, pack[0]);
    glPixelStorei(GL_PACK_ROW_LENGTH, pack[1]);
    glPixelStorei(GL_PACK_SKIP_ROWS, pack[2]);
    glPixelStorei(GL_PACK_SKIP_PIXELS, pack[3]);
    if (g_bind_buffer && pack_buffer) g_bind_buffer(GL_PIXEL_PACK_BUFFER, (GLuint)pack_buffer);
    if (g_bind_framebuffer && read_framebuffer)
        g_bind_framebuffer(GL_READ_FRAMEBUFFER, (GLuint)read_framebuffer);
    /* Do not drain glGetError: that consumes errors belonging to the renderer
     * and an error-producing driver can make such a loop unbounded. */
    profile_end_stage(PR_GL_RESTORE, measured);
}

/* -- the second capture point: the wrapped plugin's own ReadScreen ------- */
static BOOL g_readscreen_retired;      /* a returned block was not ours to free: never call it again */
static BOOL g_readscreen_empty_noted, g_readscreen_size_noted;

static BOOL process_heap_block(void *block) {
    __try {
        return HeapValidate(GetProcessHeap(), 0, block);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return FALSE;
    }
}

static void free_wrapped_buffer(void *block) {
    if (process_heap_block(block)) {
        if (HeapFree(GetProcessHeap(), 0, block)) return;
        g_readscreen_retired = TRUE;
        plugin_logf("readscreen_free_failed", "error=%lu; path retired", GetLastError());
        return;
    }
    g_readscreen_retired = TRUE;
    plugin_log("ReadScreen returned a buffer that is not a process-heap block; "
               "the ReadScreen path is retired (one buffer leaked, nothing freed blind)");
}

/* Asks the wrapped plugin for the presented picture. On success `*out`
 * holds its buffer (tightly packed BGR rows, bottom-up, width*3 bytes each,
 * exactly what glReadPixels writes into a malloc of width*height*3) and the
 * caller frees it through free_wrapped_buffer; on failure nothing is held. */
static BOOL read_screen_via_wrapped(void **out, unsigned *width, unsigned *height) {
    *out = NULL;
    if (g_readscreen_retired || !g_wrapped.ReadScreen) return FALSE;
    void *buffer = NULL;
    long buffer_width = 0, buffer_height = 0;
    int64_t measured = profile_mark();
    g_wrapped.ReadScreen(&buffer, &buffer_width, &buffer_height);
    profile_end_stage(PR_READSCREEN, measured);
    if (!buffer) {
        /* Rate bounded by the outer callback diagnostics; one reason per run. */
        if (!g_readscreen_empty_noted) {
            g_readscreen_empty_noted = TRUE; plugin_logf("readscreen_empty", "no buffer returned");
        }
        return FALSE;
    }
    if (buffer_width <= 0 || buffer_height <= 0
            || buffer_width > (long)MAX_WIDTH || buffer_height > (long)MAX_HEIGHT) {
        if (!g_readscreen_size_noted) {
            g_readscreen_size_noted = TRUE;
            plugin_logf("readscreen_invalid_size", "width=%ld height=%ld", buffer_width, buffer_height);
        }
        free_wrapped_buffer(buffer);
        return FALSE;
    }
    *out = buffer;
    *width = (unsigned)buffer_width;
    *height = (unsigned)buffer_height;
    return TRUE;
}

static void copy_packed_rows(uint8_t *destination, unsigned stride, const uint8_t *source,
                             unsigned width, unsigned height) {
    unsigned row_bytes = width * BYTES_PER_PIXEL;
    if (row_bytes == stride) { memcpy(destination, source, (size_t)stride * height); return; }
    for (unsigned row = 0; row < height; row++)
        memcpy(destination + (size_t)row * stride, source + (size_t)row * row_bytes, row_bytes);
}

static void capture_if_presented(void) {
    if (!g_hdr || !g_have_gfx) return;
    /* No GPU/context/ReadScreen work after an abandoned reader's lease.
     * want_frames alone survives a killed server while PJ64 owns the map. */
    BOOL capture_wanted = capture_requested();
    if (!g_gfx.VI_ORIGIN_REG) return;
    unsigned origin = *g_gfx.VI_ORIGIN_REG;
    if (origin == g_last_origin) return;
    g_last_origin = origin;
    if (!capture_wanted) return;
    HGLRC context = wglGetCurrentContext();
    BOOL have_context = context != NULL;
    if (g_gl_context != context) {
        g_gl_context = context;
        g_gl_entry_points_looked_up = FALSE;
    }
    note_capture_context(have_context);
    unsigned width = 0, height = 0, bottom_offset = 0;
    void *wrapped_buffer = NULL;
    if (have_context) {
        g_hdr->status |= STATUS_GL_CONTEXT;
        g_hdr->status &= ~(uint32_t)STATUS_READSCREEN;
        RECT client;
        if (!GetClientRect(g_gfx.hWnd, &client)) { g_hdr->dropped++; return; }
        width = (unsigned)(client.right - client.left);
        height = (unsigned)(client.bottom - client.top);
        /* PJ64's status bar sits INSIDE the client area; the wrapped plugin
         * draws above it (GLideN64 offsets its viewport by the bar's height),
         * so the picture starts that many GL rows up from the bottom. */
        if (g_gfx.hStatusBar && IsWindowVisible(g_gfx.hStatusBar)) {
            RECT bar;
            if (GetWindowRect(g_gfx.hStatusBar, &bar)) {
                bottom_offset = (unsigned)(bar.bottom - bar.top);
                if (bottom_offset < height) height -= bottom_offset; else bottom_offset = 0;
            }
        }
    } else {
        g_hdr->status &= ~(uint32_t)STATUS_GL_CONTEXT;
        if (!read_screen_via_wrapped(&wrapped_buffer, &width, &height)) {
            g_hdr->status &= ~(uint32_t)STATUS_READSCREEN;
            g_hdr->dropped++;
            return;
        }
        g_hdr->status |= STATUS_READSCREEN;
    }
    unsigned stride = (width * BYTES_PER_PIXEL + 3u) & ~3u;
    if (width == 0 || height == 0 || width > MAX_WIDTH || height > MAX_HEIGHT) {
        if (wrapped_buffer) free_wrapped_buffer(wrapped_buffer);
        g_hdr->status |= STATUS_FRAME_TOO_LARGE;
        g_hdr->dropped++;
        return;
    }
    g_hdr->status &= ~(uint32_t)STATUS_FRAME_TOO_LARGE;
    unsigned seq = g_hdr->write_seq + 1;
    stream_slot_t *slot = slot_at((seq - 1) % SLOT_COUNT);
    slot->seq = seq;
    slot->seq_end = 0;
    MemoryBarrier();
    slot->kind = KIND_PICTURE;
    slot->list_qpc = g_pending.list_qpc;
    slot->present_qpc = qpc_now();
    slot->vi_origin = origin;
    slot->width = width;
    slot->height = height;
    slot->stride = stride;
    slot->table_count = g_pending.count;
    slot->lists_since = g_pending.lists_since;
    for (unsigned index = 0; index < TABLE_ENTRIES; index++) {
        unsigned length = index < g_pending.count ? g_pending.lengths[index] : 0;
        slot->lengths[index] = length;
        if (length) memcpy(slot->table + index * TABLE_ENTRY_BYTES, g_pending.bytes[index], length);
    }
    if (wrapped_buffer) {
        int64_t measured = profile_mark();
        copy_packed_rows(slot->pixels, stride, (const uint8_t *)wrapped_buffer, width, height);
        free_wrapped_buffer(wrapped_buffer);
        profile_end_stage(PR_COPY, measured);
    } else {
        read_front_buffer(slot->pixels, width, height, bottom_offset);
    }
    int64_t published = profile_mark();
    MemoryBarrier();
    slot->seq_end = seq;
    MemoryBarrier();
    g_hdr->write_seq = seq;
    g_hdr->width = width;
    g_hdr->height = height;
    g_hdr->format = FORMAT_BGR8_BOTTOM_UP;
    g_pending.lists_since = 0;
    if (g_event) SetEvent(g_event);
    profile_end_stage(PR_PUBLISH, published);
}

/* -- the exported surface ----------------------------------------------- */
BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_self = instance;
        /* /MT needs the CRT's thread attach/detach notifications. */
    } else if (reason == DLL_PROCESS_DETACH && reserved == NULL) {
        /* A FreeLibrary (the plugin dialog enumerating, or a plugin change):
         * leave the header honest. On process exit (`reserved` set) the
         * handles are already being reclaimed and nothing here is safe. */
        close_stream();
    }
    return TRUE;
}

EXPORT void CALL GetDllInfo(PLUGIN_INFO *info) {
    read_ini();
    info->Version = 0x0103;
    info->Type = PLUGIN_TYPE_GFX;
    info->NormalMemory = FALSE;
    info->MemoryBswaped = TRUE;
    if (g_wrapped_module && g_wrapped.GetDllInfo) {
        /* Already running: the wrapped plugin's own name, suffixed. */
        g_wrapped.GetDllInfo(info);
        char name[100];
        snprintf(name, sizeof name, "%.80s +SM64 Trainer", info->Name);
        strncpy_s(info->Name, sizeof info->Name, name, _TRUNCATE);
        return;
    }
    if (g_wrapped_name[0]) {
        char name[100];
        snprintf(name, sizeof name, "%.80s +SM64 Trainer", g_wrapped_name);
        strncpy_s(info->Name, sizeof info->Name, name, _TRUNCATE);
        return;
    }
    strncpy_s(info->Name, sizeof info->Name,
              "SM64 Trainer capture (wrapped plugin missing)", _TRUNCATE);
}

EXPORT BOOL CALL InitiateGFX(GFX_INFO info) {
    diagnostics_start();
    plugin_logf("init_begin", "wrapper_version=%u", GFXWRAP_VERSION);
    log_module_path("wrapper_loaded", g_self);
    close_stream(); /* a reused DLL gets a fresh lease/profile producer */
    ensure_wrapped();
    g_gfx = info;
    g_have_gfx = FALSE;
    g_last_origin = 0xFFFFFFFFu;
    g_capture_state = -1;
    g_capture_reported = FALSE;
    memset(&g_pending, 0, sizeof g_pending);
    measure_rdram();
    g_gl_entry_points_looked_up = FALSE;      /* a new context: look them up again */
    g_gl_context = NULL;
    g_context_state = -1;
    g_context_reported = FALSE;
    g_readscreen_retired = FALSE;
    g_readscreen_empty_noted = g_readscreen_size_noted = FALSE;
    callback_diagnostic_reset(&g_diagnostic_list);
    callback_diagnostic_reset(&g_diagnostic_update);
    callback_diagnostic_reset(&g_diagnostic_rdp);
    if (!g_wrapped.InitiateGFX) {
        char message[MAX_PATH + 200];
        snprintf(message, sizeof message,
                 "SM64 Trainer's capture layer could not load the graphics plugin it wraps"
                 " (%s).\nCheck the wrapped= line in sm64_trainer_gfx.ini beside it, or"
                 " pick your graphics plugin again in Options > Settings > Plugins.",
                 g_wrapped_name[0] ? g_wrapped_name : "no wrapped= line in sm64_trainer_gfx.ini");
        plugin_log("InitiateGFX refused: no wrapped plugin");
        if (!GetEnvironmentVariableA("SM64_TRAINER_GFX_NO_DIALOGS", NULL, 0))   /* the test host sets it */
            MessageBoxA(g_gfx.hWnd, message, "SM64 Trainer capture layer", MB_OK | MB_ICONWARNING);
        release_wrapped();
        diagnostics_stop();
        return FALSE;
    }
    int64_t begin = qpc_now();
    BOOL initiated = g_wrapped.InitiateGFX(info);
    plugin_logf("init_result", "success=%d wrapped_ms=%.3f", initiated,
        g_diagnostic_frequency ? 1000.0 * (qpc_now() - begin) / g_diagnostic_frequency : 0.0);
    if (!initiated) {
        diagnostics_stop();
        return FALSE;
    }
    g_have_gfx = TRUE;
    open_stream();
    if (g_hdr) {
        /* A failed wrapped initialization must never advertise a producer. */
        g_hdr->status = STATUS_WRAPPED_LOADED | STATUS_INITIATED;
        g_hdr->dropped = 0;
        if (g_wrapped.GetDllInfo) {
            PLUGIN_INFO wrapped_info = {0};
            g_wrapped.GetDllInfo(&wrapped_info);
            g_hdr->wrapped_version = wrapped_info.Version;
            strncpy_s(g_hdr->wrapped_name, sizeof g_hdr->wrapped_name, g_wrapped_name, _TRUNCATE);
            plugin_logf("wrapped_info", "name=\"%.99s\" version=%04x bswapped=%d readscreen=%d",
                wrapped_info.Name, wrapped_info.Version, wrapped_info.MemoryBswaped, g_wrapped.ReadScreen != NULL);
        }
    }
    return initiated;
}

EXPORT void CALL ProcessDList(void) {
    int64_t begin = qpc_now();
    if (g_wrapped.ProcessDList) g_wrapped.ProcessDList();
    int64_t forwarded = qpc_now();
    if (g_hdr && g_have_gfx) g_hdr->lists++;
    if (capture_requested()) stamp_pending();
    callback_diagnostic_end(&g_diagnostic_list, begin, forwarded, qpc_now());
}

EXPORT void CALL UpdateScreen(void) {
    int64_t begin = qpc_now();
    int64_t began = profile_begin();
    int64_t measured = profile_mark();
    if (g_wrapped.UpdateScreen) g_wrapped.UpdateScreen();
    int64_t forwarded = qpc_now();
    profile_end_stage(PR_WRAPPED_UPDATE, measured);
    measured = profile_mark();
    if (g_hdr && g_have_gfx) g_hdr->alive++;
    capture_if_presented();
    profile_end_stage(PR_CAPTURE, measured);
    profile_end(began);
    callback_diagnostic_end(&g_diagnostic_update, begin, forwarded, qpc_now());
}

EXPORT void CALL RomOpen(void) {
    plugin_logf("rom_open_begin", "initiated=%d", g_have_gfx);
    if (g_wrapped.RomOpen) g_wrapped.RomOpen();
    measure_rdram();                          /* the expansion pak is committed by now */
    g_last_origin = 0xFFFFFFFFu;
    memset(&g_pending, 0, sizeof g_pending);
    g_gl_entry_points_looked_up = FALSE;
    g_gl_context = NULL;
    if (g_hdr && g_have_gfx) g_hdr->status |= STATUS_ROM_OPEN;
    plugin_logf("rom_open_end", "rdram_span=%u", (unsigned)g_rdram_span);
}

EXPORT void CALL RomClosed(void) {
    plugin_logf("rom_closed_begin", "lists=%u pictures=%u dropped=%u",
        g_hdr ? g_hdr->lists : 0, g_hdr ? g_hdr->write_seq : 0, g_hdr ? g_hdr->dropped : 0);
    if (g_wrapped.RomClosed) g_wrapped.RomClosed();
    if (g_hdr) g_hdr->status &= ~(uint32_t)STATUS_ROM_OPEN;
    memset(&g_pending, 0, sizeof g_pending);
    plugin_logf("rom_closed_end", "");
}

EXPORT void CALL CloseDLL(void) {
    plugin_logf("close_begin", "initiated=%d", g_have_gfx);
    if (g_wrapped.CloseDLL) g_wrapped.CloseDLL();
    g_have_gfx = FALSE;
    close_stream();
    release_wrapped();
    plugin_logf("close_end", "");
    diagnostics_stop();
}

EXPORT void CALL ProcessRDPList(void) {
    int64_t begin = qpc_now();
    if (g_wrapped.ProcessRDPList) g_wrapped.ProcessRDPList();
    int64_t end = qpc_now();
    callback_diagnostic_end(&g_diagnostic_rdp, begin, end, end);
}
EXPORT void CALL ChangeWindow(void) {
    plugin_logf("change_window_begin", "");
    if (g_wrapped.ChangeWindow) g_wrapped.ChangeWindow();
    g_gl_entry_points_looked_up = FALSE;
    g_gl_context = NULL;
    plugin_logf("change_window_end", "");
}
EXPORT void CALL DrawScreen(void) { if (g_wrapped.DrawScreen) g_wrapped.DrawScreen(); }
EXPORT void CALL ShowCFB(void) { if (g_wrapped.ShowCFB) g_wrapped.ShowCFB(); }
EXPORT void CALL ViStatusChanged(void) { if (g_wrapped.ViStatusChanged) g_wrapped.ViStatusChanged(); }
EXPORT void CALL ViWidthChanged(void) { if (g_wrapped.ViWidthChanged) g_wrapped.ViWidthChanged(); }
EXPORT void CALL MoveScreen(int x, int y) { if (g_wrapped.MoveScreen) g_wrapped.MoveScreen(x, y); }
EXPORT void CALL CaptureScreen(char *directory) { if (g_wrapped.CaptureScreen) g_wrapped.CaptureScreen(directory); }
/* PJ64 can load a DLL only for its settings dialog, then FreeLibrary without
 * CloseDLL. Balance loads here while still outside DllMain/loader lock. */
EXPORT void CALL DllAbout(HWND parent) {
    BOOL temporary = !g_wrapped_module;
    ensure_wrapped(); if (g_wrapped.DllAbout) g_wrapped.DllAbout(parent);
    if (temporary && !g_have_gfx) release_wrapped();
}
EXPORT void CALL DllConfig(HWND parent) {
    BOOL temporary = !g_wrapped_module;
    ensure_wrapped(); if (g_wrapped.DllConfig) g_wrapped.DllConfig(parent);
    if (temporary && !g_have_gfx) release_wrapped();
}
EXPORT void CALL DllTest(HWND parent) {
    BOOL temporary = !g_wrapped_module;
    ensure_wrapped(); if (g_wrapped.DllTest) g_wrapped.DllTest(parent);
    if (temporary && !g_have_gfx) release_wrapped();
}
EXPORT void CALL FBRead(unsigned int address) { if (g_wrapped.FBRead) g_wrapped.FBRead(address); }
EXPORT void CALL FBWrite(unsigned int address, unsigned int size) { if (g_wrapped.FBWrite) g_wrapped.FBWrite(address, size); }
EXPORT void CALL FBWList(void *entries, unsigned int size) {
    if (g_wrapped.FBWList) g_wrapped.FBWList(entries, size);
}
EXPORT void CALL FBGetFrameBufferInfo(void *info) { if (g_wrapped.FBGetFrameBufferInfo) g_wrapped.FBGetFrameBufferInfo(info); }
EXPORT void CALL ReadScreen(void **dest, long *width, long *height) {
    if (g_wrapped.ReadScreen) { g_wrapped.ReadScreen(dest, width, height); return; }
    *dest = NULL; *width = 0; *height = 0;
}

/* A copied wrapper is a different HMODULE; identity comparison alone cannot
 * prevent recursively wrapping that copy. No host calls this marker. */
EXPORT const char *CALL SM64TrainerWrapperIdentity(void) { return GFXWRAP_BUILD_ID; }
