/* THE CAPTURE LAYER: the wrapper graphics plugin Project64 1.6 selects.
 *
 * Round 32 item 95, 2026-09-04; one shipped mode since 2026-09-16 (the raw
 * ReadScreen/frame-stream path was deleted). It forwards every Zilmar-spec
 * call to THE RENDERER named by `wrapped=` in sm64_trainer_gfx.ini beside this
 * DLL (the trainer's build of LINK's GLideN64 v4.2 with the SourceV2/ContextV1
 * overlay) and, on the emulation thread, does two extra things:
 *
 *   ProcessDList  -- the stamp adapter wraps the original call and copies the
 *                    tracker's address table out of RDRAM into a PENDING stamp.
 *                    The list the game just sent IS frame N: gGlobalTimer still
 *                    reads N, the pad in RDRAM is N's, so every byte copied
 *                    here belongs to the picture this list draws.
 *   UpdateScreen  -- the adapter forwards through the renderer's captured
 *                    present; when the renderer finishes the picture it hands an
 *                    owned GPU snapshot plus that stamp to the delivery worker,
 *                    which never waits on Python.
 *
 * The plugin knows NO game address: the tracker publishes {rdram_offset,
 * length} entries through the control page (memory/layout.py stays the one
 * source of addresses) and this copies bytes. It reads the emulator's memory
 * and the GPU; it writes only its own mappings. Without a live capture request
 * the callbacks forward and keep the bounded diagnostics; the control worker
 * (control_worker.h) answers discovery and leases on its own thread.
 *
 * A wrapped renderer without the SourceV2 export (a stock GLideN64) is
 * forwarded to but never captured: the runtime refuses to configure and the
 * control page says so. That is why the setup screen installs OUR renderer.
 *
 * ONLY ON A PRACTICE ROM (practice_rom.h). RomOpen reads the cartridge header;
 * for vanilla SM64, another SM64 hack or another game every callback forwards
 * straight to the renderer -- no stamp adapter, no per-call timing, no lease
 * admitted -- and the control page reports `rom_baseline` so the trainer stays
 * idle. A real speedrun on the same plugin costs what plain GLideN64 costs
 * (his ruling, 2026-09-16). */
#include <windows.h>
#include <stdio.h>
#include <string.h>
#include "zilmar.h"
#include "wrapper_runtime.h"
#include "stamp_adapter.h"
#include "gpu_delivery_diagnostics.h"
#include "control_worker.h"
#include "practice_rom.h"

#define EXPORT __declspec(dllexport)
#define CALL __cdecl
#define GFXWRAP_VERSION 3
/* The control page name the trainer opens (replay/gpudemand.py); the ini's
 * `stream=` line overrides it for test hosts that drive several wrappers. */
#define GFXWRAP_CONTROL_NAME "sm64_trainer_gfx_v1"
/* The picker label Project64 shows. The build id, not the label, names the
 * exact sources (SM64TrainerWrapperIdentity); the label is the product. */
#define GFXWRAP_PLUGIN_NAME "SM64 Trainer v1.0"

static HMODULE g_self;
static HMODULE g_wrapped_module;
static gfx_api_t g_wrapped;
static char g_wrapped_name[MAX_PATH];
static char g_stream_name[128] = GFXWRAP_CONTROL_NAME;
static BOOL g_loaded_once;
static GFX_INFO g_gfx;
static BOOL g_have_gfx;
static BOOL g_runtime_ready;
/* The open ROM is a practice ROM. Written by RomOpen/RomClosed on the
 * emulation thread, the same thread every frame callback runs on. */
static BOOL g_practice_rom;

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

static BOOL g_ini_read;

static void read_ini(void) {
    if (g_ini_read) return;
    g_ini_read = TRUE;
    strncpy_s(g_stream_name, sizeof g_stream_name, GFXWRAP_CONTROL_NAME, _TRUNCATE);
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
 * never a crash. Measured at InitiateGFX and again at RomOpen: his first three
 * plugin clips (2026-09-05) carried no IGT at all, because the span was read
 * once at InitiateGFX, before the ROM's expansion pak was committed, and
 * `usamune_overall` (above 4 MB) was refused all session. */
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

#include "gpu_delivery_log.h"

static BOOL loaded_rom_is_practice(void) {
    unsigned char header[PRACTICE_ROM_HEADER_BYTES];
    if (!g_gfx.HEADER || !copy_guarded(header, g_gfx.HEADER, sizeof header)) return FALSE;
    return practice_rom(header) ? TRUE : FALSE;
}

static uint32_t __cdecl runtime_vi(void *unused) {
    (void)unused;
    __try { return g_gfx.VI_ORIGIN_REG ? *g_gfx.VI_ORIGIN_REG : UINT32_MAX; }
    __except (EXCEPTION_EXECUTE_HANDLER) { return UINT32_MAX; }
}
static int __cdecl runtime_ram(void *unused, uint32_t offset, uint32_t length, uint8_t *out) {
    (void)unused;
    if (!g_have_gfx || !g_gfx.RDRAM || !length || length > 128
            || (uint64_t)offset + length > g_rdram_span) return 0;
    return copy_guarded(out, g_gfx.RDRAM + offset, length);
}

/* -- the exported surface ----------------------------------------------- */
BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_self = instance;
        /* /MT needs the CRT's thread attach/detach notifications. */
    } else if (reason == DLL_PROCESS_DETACH && reserved == NULL) {
        /* A FreeLibrary (the plugin dialog enumerating, or a plugin change).
         * On process exit (`reserved` set) the handles are already being
         * reclaimed and nothing here is safe. */
        if (g_control_signal) CloseHandle(g_control_signal);
        g_control_signal = NULL;
    }
    return TRUE;
}

EXPORT void CALL GetDllInfo(PLUGIN_INFO *info) {
    read_ini();
    info->Version = 0x0103;
    info->Type = PLUGIN_TYPE_GFX;
    info->NormalMemory = FALSE;
    info->MemoryBswaped = TRUE;
    /* PJ64's narrow selector clips long renderer names before the build tag.
     * Keep this label stable before/after loading; full identity is in the log
     * and control page. GetDllInfo must not load the renderer during discovery. */
    if (g_wrapped_module && g_wrapped.GetDllInfo) g_wrapped.GetDllInfo(info);
    strncpy_s(info->Name, sizeof info->Name,
        g_wrapped_module || g_wrapped_name[0]
            ? GFXWRAP_PLUGIN_NAME : GFXWRAP_PLUGIN_NAME " (not set up)", _TRUNCATE);
}

EXPORT BOOL CALL InitiateGFX(GFX_INFO info) {
    wr_suspend(); g_runtime_ready = FALSE; g_practice_rom = FALSE; sa_reset();
    control_stop(); /* a failed reinitialization cannot retain an old session */
    diagnostics_start();
    plugin_logf("init_begin", "wrapper_version=%u", GFXWRAP_VERSION);
    log_module_path("wrapper_loaded", g_self);
    ensure_wrapped();
    g_gfx = info;
    g_have_gfx = FALSE;
    callback_diagnostic_reset(&g_diagnostic_list);
    callback_diagnostic_reset(&g_diagnostic_update);
    callback_diagnostic_reset(&g_diagnostic_rdp);
    if (!g_wrapped.InitiateGFX) {
        char message[MAX_PATH + 200];
        snprintf(message, sizeof message,
                 "SM64 Trainer's capture layer could not load the graphics plugin it wraps"
                 " (%s).\nCheck the wrapped= line in sm64_trainer_gfx.ini beside it, or"
                 " set up Practice Replay again from the trainer's Settings.",
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
    measure_rdram();
    gd_set_diagnostic(runtime_diagnostic);
    if (info.MemoryBswaped && info.RDRAM && info.VI_ORIGIN_REG)
        g_runtime_ready = wr_configure(g_wrapped_module, &g_wrapped, runtime_vi, runtime_ram);
    control_start(g_stream_name);
    if (g_wrapped.GetDllInfo) {
        PLUGIN_INFO wrapped_info = {0};
        g_wrapped.GetDllInfo(&wrapped_info);
        plugin_logf("wrapped_info", "name=\"%.99s\" version=%04x bswapped=%d runtime=%d",
            wrapped_info.Name, wrapped_info.Version, wrapped_info.MemoryBswaped, g_runtime_ready);
    }
    return initiated;
}

EXPORT void CALL ProcessDList(void) {
    if (!g_practice_rom) { if (g_wrapped.ProcessDList) g_wrapped.ProcessDList(); return; }
    /* The only witness on the thread that matters: two QPC reads per
     * call (~50 ns) and a bounded off-thread log. The adapter wraps the
     * original call, so total_ms here includes the stamp copy. */
    int64_t begin = qpc_now();
    if (g_runtime_ready) sa_process_dlist();
    else if (g_wrapped.ProcessDList) g_wrapped.ProcessDList();
    callback_diagnostic_end(&g_diagnostic_list, begin, qpc_now());
}

EXPORT void CALL UpdateScreen(void) {
    if (!g_practice_rom) { if (g_wrapped.UpdateScreen) g_wrapped.UpdateScreen(); return; }
    int64_t begin = qpc_now();
    if (g_runtime_ready) sa_update_screen();
    else if (g_wrapped.UpdateScreen) g_wrapped.UpdateScreen();
    callback_diagnostic_end(&g_diagnostic_update, begin, qpc_now());
}

EXPORT void CALL RomOpen(void) {
    sa_reset();
    /* Decided before the renderer opens the ROM, so no frame of a baseline
     * ROM ever reaches the stamp adapter. */
    g_practice_rom = g_have_gfx && loaded_rom_is_practice();
    plugin_logf("rom_open_begin", "initiated=%d practice_rom=%d", g_have_gfx, g_practice_rom);
    if (g_wrapped.RomOpen) g_wrapped.RomOpen();
    measure_rdram();                          /* the expansion pak is committed by now */
    control_rom(g_practice_rom, !g_practice_rom);
    plugin_logf("rom_open_end", "rdram_span=%u", (unsigned)g_rdram_span);
}

EXPORT void CALL RomClosed(void) {
    control_rom(FALSE, FALSE);
    g_practice_rom = FALSE;
    sa_reset();
    plugin_logf("rom_closed_begin", "");
    if (g_wrapped.RomClosed) g_wrapped.RomClosed();
    plugin_logf("rom_closed_end", "");
}

EXPORT void CALL CloseDLL(void) {
    wr_suspend(); g_runtime_ready = FALSE; g_practice_rom = FALSE; sa_reset();
    control_stop();
    plugin_logf("close_begin", "initiated=%d", g_have_gfx);
    if (g_wrapped.CloseDLL) g_wrapped.CloseDLL();
    g_have_gfx = FALSE;
    release_wrapped();
    plugin_logf("close_end", "");
    diagnostics_stop();
}

EXPORT void CALL ProcessRDPList(void) {
    if (!g_practice_rom) { if (g_wrapped.ProcessRDPList) g_wrapped.ProcessRDPList(); return; }
    int64_t begin = qpc_now();
    if (g_wrapped.ProcessRDPList) g_wrapped.ProcessRDPList();
    callback_diagnostic_end(&g_diagnostic_rdp, begin, qpc_now());
}
EXPORT void CALL ChangeWindow(void) {
    plugin_logf("change_window_begin", "");
    if (g_wrapped.ChangeWindow) g_wrapped.ChangeWindow();
    plugin_logf("change_window_end", "");
}
EXPORT void CALL DrawScreen(void) { if (g_wrapped.DrawScreen) g_wrapped.DrawScreen(); }
EXPORT void CALL ShowCFB(void) { if (g_wrapped.ShowCFB) g_wrapped.ShowCFB(); }
EXPORT void CALL ViStatusChanged(void) { if (g_wrapped.ViStatusChanged) g_wrapped.ViStatusChanged(); }
EXPORT void CALL ViWidthChanged(void) { if (g_wrapped.ViWidthChanged) g_wrapped.ViWidthChanged(); }
EXPORT void CALL MoveScreen(int x, int y) { if (g_wrapped.MoveScreen) g_wrapped.MoveScreen(x, y); }
EXPORT void CALL CaptureScreen(char *directory) { if (g_wrapped.CaptureScreen) g_wrapped.CaptureScreen(directory); }
/* About is ours, not the renderer's: the plugin he picks is the trainer's
 * build of LINK's GLideN64, and the credit belongs to the people whose
 * work it is. The renderer's own About stays reachable by selecting the
 * renderer DLL directly in Project64. Credits are the ones the pinned
 * GLideN64 source names in its own About dialog (AboutDialog.ui). */
static const char k_about[] =
    GFXWRAP_PLUGIN_NAME " - graphics plugin for Project64\n\n"
    "Built by griffman1212 on LINK's GLideN64 v4.2 (the Luna-Project64 fork,\n"
    "commit d0d1010). GLideN64 is by Sergey Lipskiy, with Olivieryuyu, Ryan Rosser\n"
    "and its contributors, and credits Orkin (glN64), yongzh (gles2n64),\n"
    "Hiroshi Morii (GlideHQ) and ziggy (z64).\n\n"
    "This build adds the Practice Replay capture hooks. Like GLideN64 it is\n"
    "licensed under the GNU GPL v2; the modified source and the build recipe\n"
    "are published at https://github.com/griffinbeels/SM64-Trainer\n\n"
    "Capture wrapper build " GFXWRAP_BUILD_ID;
EXPORT void CALL DllAbout(HWND parent) {
    MessageBoxA(parent, k_about, "About " GFXWRAP_PLUGIN_NAME, MB_OK | MB_ICONINFORMATION);
}
/* PJ64 can load a DLL only for its settings dialog, then FreeLibrary without
 * CloseDLL. Balance loads here while still outside DllMain/loader lock. */
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
