/* THE GRAPHICS PLUGIN Project64 1.6 loads: sm64_trainer_gfx.dll, one file.
 *
 * Round 32 item 95, 2026-09-04; one shipped mode since 2026-09-16 (the raw
 * ReadScreen/frame-stream path was deleted); one file since 2026-11-07. Every
 * Zilmar-spec call Project64 makes lands here and is forwarded to THE RENDERER
 * linked into the same DLL (the trainer's build of LINK's GLideN64 v4.2 with
 * the SourceV2/ContextV1 overlay; renderer_entries.h is the seam). On the
 * emulation thread this capture layer does two extra things:
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
 * Both also feed the scene tap (scene_tap.cpp), an M4 capture experiment that
 * stays one atomic load unless a one-shot arming file exists at RomOpen or the
 * Trainer's scene lab holds the live spool's lease (its own thread checks).
 *
 * WHY ONE FILE. Until 2026-11-07 this was a wrapper DLL that loaded the
 * renderer named by `wrapped=` in sm64_trainer_gfx.ini: a second DLL,
 * installed as sm64_trainer_gfx.renderer so Project64 would not list it.
 * Defender flagged the renderer as Trojan:Win32/Bearfoos.A!ml (2026-11-06), and
 * a proxy DLL forwarding to a sibling DLL under a disguised name is the shape
 * malware takes. His ruling (2026-11-07): "the approach that's easiest for the
 * user and has the least likelihood of being accidentally flagged as malware".
 * So this plugin loads no other plugin and reads no ini of its own: the
 * renderer is linked in, and GLideN64's own GLideN64.ini beside the plugin is
 * the only settings file, exactly as for a stock GLideN64.
 *
 * The plugin knows NO game address: the tracker publishes {rdram_offset,
 * length} entries through the control page (memory/layout.py stays the one
 * source of addresses) and this copies bytes. It reads the emulator's memory
 * and the GPU; it writes only its own mappings. Without a live capture request
 * the callbacks forward and keep the bounded diagnostics; the control worker
 * (control_worker.h) answers discovery and leases on its own thread.
 *
 * ONLY ON A PRACTICE ROM (practice_rom.h). RomOpen reads the cartridge header;
 * for vanilla SM64, another SM64 hack or another game every callback forwards
 * straight to the renderer -- no stamp adapter, no per-call timing, no lease
 * admitted -- and the control page reports `rom_baseline` so the trainer stays
 * idle. A real speedrun on the same plugin costs what plain GLideN64 costs
 * (his ruling, 2026-09-16).
 *
 * THE RENDERER-ONLY MODE (SM64RendererOnlyV1, at the end) is for the scene
 * lab's hosts, which draw saved scenes through this same file without
 * Project64: every call then goes straight to the renderer and none of the
 * above runs, so a lab host never publishes a control page beside a running
 * Project64's. */
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "zilmar.h"
#include "renderer_entries.h"
#include "wrapper_runtime.h"
#include "stamp_adapter.h"
#include "gpu_delivery_diagnostics.h"
#include "control_worker.h"
#include "practice_rom.h"
#include "scene_tap.h"

#define EXPORT __declspec(dllexport)
#define CALL __cdecl
#define GFXWRAP_VERSION 4   /* 4: the renderer is linked in (2026-11-07) */
/* The control page name the trainer opens (replay/gpudemand.py). */
#define GFXWRAP_CONTROL_NAME "sm64_trainer_gfx_v1"
/* The picker label Project64 shows. The build id, not the label, names the
 * exact sources (SM64TrainerWrapperIdentity); the label is the product. */
#define GFXWRAP_PLUGIN_NAME "SM64 Trainer v1.0"

static HMODULE g_self;
/* The renderer linked into this DLL, bound once in DllMain. */
static gfx_api_t g_renderer;
static GFX_INFO g_gfx;
static BOOL g_have_gfx;
/* InitiateGFX has been called and CloseDLL has not: SM64RendererOnlyV1 refuses. */
static BOOL g_initiated;
static BOOL g_runtime_ready;
/* Set once by SM64RendererOnlyV1 before InitiateGFX; never cleared. */
static BOOL g_renderer_only;
/* The open ROM is a practice ROM. Written by RomOpen/RomClosed on the
 * emulation thread, the same thread every frame callback runs on. */
static BOOL g_practice_rom;

static int64_t qpc_now(void) {
    LARGE_INTEGER counter;
    QueryPerformanceCounter(&counter);
    return counter.QuadPart;
}

/* This DLL's folder: where the plugin log lives (diagnostics.h). */
static void self_dir(wchar_t *out, size_t count) {
    out[0] = L'\0';
    if (GetModuleFileNameW(g_self, out, (DWORD)count) == 0) return;
    wchar_t *slash = wcsrchr(out, L'\\');
    if (slash) *(slash + 1) = L'\0';
}

#include "diagnostics.h"

/* The control page this plugin publishes: always GFXWRAP_CONTROL_NAME in the
 * shipped build. A TEST plugin (tools/build_plugin.py build_test_plugin, the
 * only build that defines GFXWRAP_TEST_PLUGIN) takes another from
 * SM64_TEST_CONTROL_NAME, so tests that run several at once, beside a live
 * Project64, never answer as each other's or as Project64's page. */
static const char *control_name(void) {
#ifdef GFXWRAP_TEST_PLUGIN
    static char named[128];
    DWORD length = GetEnvironmentVariableA("SM64_TEST_CONTROL_NAME", named, sizeof named);
    if (length && length < sizeof named) return named;
#endif
    return GFXWRAP_CONTROL_NAME;
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
        g_renderer = *renderer_entries();
        /* /MT needs the CRT's thread attach/detach notifications. */
        return renderer_dll_main(instance, reason, reserved);
    }
    if (reason == DLL_PROCESS_DETACH && reserved == NULL) {
        /* A FreeLibrary (the plugin dialog enumerating, or a plugin change).
         * On process exit (`reserved` set) the handles are already being
         * reclaimed and nothing here is safe. */
        if (g_control_signal) CloseHandle(g_control_signal);
        g_control_signal = NULL;
    }
    return renderer_dll_main(instance, reason, reserved);
}

EXPORT void CALL GetDllInfo(PLUGIN_INFO *info) {
    if (g_renderer_only) {
        if (g_renderer.GetDllInfo) g_renderer.GetDllInfo(info);
        return;
    }
    /* Answered here, without the renderer: Project64 asks every DLL in its
     * Plugin folder during discovery. The fields are the renderer's own
     * (upstream's PluginAPI::GetDllInfo) apart from the name. PJ64's narrow
     * selector clips long renderer names before the build tag, so the label is
     * the product and stays the same before and after loading; full identity
     * is in the log and on the control page. */
    info->Version = 0x0103;
    info->Type = PLUGIN_TYPE_GFX;
    info->NormalMemory = FALSE;
    info->MemoryBswaped = TRUE;
    strncpy_s(info->Name, sizeof info->Name, GFXWRAP_PLUGIN_NAME, _TRUNCATE);
}

EXPORT BOOL CALL InitiateGFX(GFX_INFO info) {
    g_initiated = TRUE;
    if (g_renderer_only) {
        g_gfx = info;
        g_have_gfx = g_renderer.InitiateGFX ? g_renderer.InitiateGFX(info) : FALSE;
        return g_have_gfx;
    }
    wr_suspend(); g_runtime_ready = FALSE; g_practice_rom = FALSE; sa_reset();
    control_stop(); /* a failed reinitialization cannot retain an old session */
    diagnostics_start();
    plugin_logf("init_begin", "wrapper_version=%u", GFXWRAP_VERSION);
    log_module_path("wrapper_loaded", g_self);
    g_gfx = info;
    st_configure(&g_gfx);
    g_have_gfx = FALSE;
    callback_diagnostic_reset(&g_diagnostic_list);
    callback_diagnostic_reset(&g_diagnostic_update);
    callback_diagnostic_reset(&g_diagnostic_rdp);
    if (!g_renderer.InitiateGFX) {   /* only a stand-in renderer can lack it */
        plugin_log("InitiateGFX refused: the linked renderer has no InitiateGFX");
        diagnostics_stop();
        return FALSE;
    }
    int64_t begin = qpc_now();
    BOOL initiated = g_renderer.InitiateGFX(info);
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
        g_runtime_ready = wr_configure(g_self, &g_renderer, runtime_vi, runtime_ram);
    control_start(control_name());
    if (g_renderer.GetDllInfo) {
        PLUGIN_INFO renderer_info = {0};
        g_renderer.GetDllInfo(&renderer_info);
        plugin_logf("wrapped_info", "name=\"%.99s\" version=%04x bswapped=%d runtime=%d",
            renderer_info.Name, renderer_info.Version, renderer_info.MemoryBswaped, g_runtime_ready);
    }
    return initiated;
}

EXPORT void CALL ProcessDList(void) {
    if (!g_practice_rom) { if (g_renderer.ProcessDList) g_renderer.ProcessDList(); return; }
    /* Producer timing covers every completed callback, independent of
     * recording retention or polling. The adapter wraps the original call,
     * so the duration includes stamp copying; formatting runs off-thread. */
    int64_t begin = callback_diagnostic_begin(&g_diagnostic_list);
    st_process_dlist();                       /* idle unless a scene-lab session is armed */
    if (g_runtime_ready) sa_process_dlist();
    else if (g_renderer.ProcessDList) g_renderer.ProcessDList();
    callback_diagnostic_end(&g_diagnostic_list, begin, qpc_now());
}

EXPORT void CALL UpdateScreen(void) {
    if (!g_practice_rom) { if (g_renderer.UpdateScreen) g_renderer.UpdateScreen(); return; }
    int64_t begin = callback_diagnostic_begin(&g_diagnostic_update);
    if (g_runtime_ready) sa_update_screen();
    else if (g_renderer.UpdateScreen) g_renderer.UpdateScreen();
    st_update_screen(g_renderer.ReadScreen);
    int64_t end = qpc_now();
    callback_diagnostic_end(&g_diagnostic_update, begin, end);
}

EXPORT void CALL RomOpen(void) {
    if (g_renderer_only) { if (g_renderer.RomOpen) g_renderer.RomOpen(); return; }
    sa_reset();
    /* Decided before the renderer opens the ROM, so no frame of a baseline
     * ROM ever reaches the stamp adapter. */
    g_practice_rom = g_have_gfx && loaded_rom_is_practice();
    if (g_practice_rom && g_gfx.MemoryBswaped && g_gfx.RDRAM && g_gfx.VI_ORIGIN_REG)
        g_runtime_ready = wr_configure(g_self, &g_renderer, runtime_vi, runtime_ram);
    else {
        wr_suspend(); g_runtime_ready = FALSE;
    }
    plugin_logf("rom_open_begin", "initiated=%d practice_rom=%d", g_have_gfx, g_practice_rom);
    if (g_renderer.RomOpen) g_renderer.RomOpen();
    measure_rdram();                          /* the expansion pak is committed by now */
    if (g_practice_rom) st_rom_open();
    control_rom(g_practice_rom, !g_practice_rom);
    plugin_logf("rom_open_end", "rdram_span=%u", (unsigned)g_rdram_span);
}

EXPORT void CALL RomClosed(void) {
    if (g_renderer_only) { if (g_renderer.RomClosed) g_renderer.RomClosed(); return; }
    /* Revoke clean-source capability before the renderer tears its context
     * down. A practice-ROM reopen renegotiates through the proven binding. */
    wr_suspend(); g_runtime_ready = FALSE;
    control_rom(FALSE, FALSE);
    g_practice_rom = FALSE;
    sa_reset();
    st_stop();
    plugin_logf("rom_closed_begin", "");
    if (g_renderer.RomClosed) g_renderer.RomClosed();
    plugin_logf("rom_closed_end", "");
}

EXPORT void CALL CloseDLL(void) {
    if (g_renderer_only) {
        if (g_renderer.CloseDLL) g_renderer.CloseDLL();
        g_have_gfx = FALSE;
        g_initiated = FALSE;
        return;
    }
    wr_suspend(); g_runtime_ready = FALSE; g_practice_rom = FALSE; sa_reset();
    st_close(15000);
    control_stop();
    plugin_logf("close_begin", "initiated=%d", g_have_gfx);
    if (g_renderer.CloseDLL) g_renderer.CloseDLL();
    g_have_gfx = FALSE;
    g_initiated = FALSE;
    plugin_logf("close_end", "");
    diagnostics_stop();
}

EXPORT void CALL ProcessRDPList(void) {
    if (!g_practice_rom) { if (g_renderer.ProcessRDPList) g_renderer.ProcessRDPList(); return; }
    int64_t begin = callback_diagnostic_begin(&g_diagnostic_rdp);
    if (g_renderer.ProcessRDPList) g_renderer.ProcessRDPList();
    callback_diagnostic_end(&g_diagnostic_rdp, begin, qpc_now());
}
EXPORT void CALL ChangeWindow(void) {
    if (g_renderer_only) { if (g_renderer.ChangeWindow) g_renderer.ChangeWindow(); return; }
    plugin_logf("change_window_begin", "");
    if (g_renderer.ChangeWindow) g_renderer.ChangeWindow();
    plugin_logf("change_window_end", "");
}
EXPORT void CALL DrawScreen(void) { if (g_renderer.DrawScreen) g_renderer.DrawScreen(); }
EXPORT void CALL ShowCFB(void) { if (g_renderer.ShowCFB) g_renderer.ShowCFB(); }
EXPORT void CALL ViStatusChanged(void) { if (g_renderer.ViStatusChanged) g_renderer.ViStatusChanged(); }
EXPORT void CALL ViWidthChanged(void) { if (g_renderer.ViWidthChanged) g_renderer.ViWidthChanged(); }
EXPORT void CALL MoveScreen(int x, int y) { if (g_renderer.MoveScreen) g_renderer.MoveScreen(x, y); }
EXPORT void CALL CaptureScreen(char *directory) { if (g_renderer.CaptureScreen) g_renderer.CaptureScreen(directory); }
/* About is ours, not the renderer's: the plugin he picks is the trainer's
 * build of LINK's GLideN64, and the credit belongs to the people whose
 * work it is. Credits are the ones the pinned GLideN64 source names in its
 * own About dialog (AboutDialog.ui). */
static const char k_about[] =
    GFXWRAP_PLUGIN_NAME " - graphics plugin for Project64\n\n"
    "Built by griffman1212 on LINK's GLideN64 v4.2 (the Luna-Project64 fork,\n"
    "commit d0d1010). GLideN64 is by Sergey Lipskiy, with Olivieryuyu, Ryan Rosser\n"
    "and its contributors, and credits Orkin (glN64), yongzh (gles2n64),\n"
    "Hiroshi Morii (GlideHQ) and ziggy (z64).\n\n"
    "This build adds the Practice Replay capture hooks. Like GLideN64 it is\n"
    "licensed under the GNU GPL v2; the modified source and the build recipe\n"
    "are published at https://github.com/griffinbeels/SM64-Trainer\n\n"
    "Build " GFXWRAP_BUILD_ID;
EXPORT void CALL DllAbout(HWND parent) {
    MessageBoxA(parent, k_about, "About " GFXWRAP_PLUGIN_NAME, MB_OK | MB_ICONINFORMATION);
}
/* The renderer's settings window. PJ64 can load a DLL only for this dialog,
 * then FreeLibrary without CloseDLL; nothing here is loaded to balance. */
EXPORT void CALL DllConfig(HWND parent) { if (g_renderer.DllConfig) g_renderer.DllConfig(parent); }
EXPORT void CALL DllTest(HWND parent) { if (g_renderer.DllTest) g_renderer.DllTest(parent); }
EXPORT void CALL FBRead(unsigned int address) { if (g_renderer.FBRead) g_renderer.FBRead(address); }
EXPORT void CALL FBWrite(unsigned int address, unsigned int size) { if (g_renderer.FBWrite) g_renderer.FBWrite(address, size); }
EXPORT void CALL FBWList(void *entries, unsigned int size) {
    if (g_renderer.FBWList) g_renderer.FBWList(entries, size);
}
EXPORT void CALL FBGetFrameBufferInfo(void *info) { if (g_renderer.FBGetFrameBufferInfo) g_renderer.FBGetFrameBufferInfo(info); }
EXPORT void CALL ReadScreen(void **dest, long *width, long *height) {
    if (g_renderer.ReadScreen) { g_renderer.ReadScreen(dest, width, height); return; }
    *dest = NULL; *width = 0; *height = 0;
}

/* This plugin's build id (tools/build_renderer.py), the same string the
 * control page, the plugin log and the About box carry. Python reads the id
 * from the file's bytes (core/capturelayer.py); no host calls this. */
EXPORT const char *CALL SM64TrainerWrapperIdentity(void) { return GFXWRAP_BUILD_ID; }

/* THE RENDERER-ONLY MODE: `int SM64RendererOnlyV1(uint32_t version)`.
 *
 * A scene-lab host (tools/scene_lab/renderer_load.h) calls it with version 1
 * right after LoadLibrary, before any Zilmar call. From then on, for as long
 * as this module stays loaded, every call goes straight to the renderer:
 * GetDllInfo answers the renderer's own fields, and InitiateGFX, RomOpen,
 * ProcessDList, UpdateScreen, RomClosed and CloseDLL do nothing else. No
 * control page or control worker (a lab host never meets a running
 * Project64's page), no stamp adapter and no GPU delivery (the host may act as
 * the renderer's SourceV3 consumer itself), no scene tap, no plugin log.
 * DllAbout stays the plugin's own box.
 *
 * Returns 1 when the mode is on (also when it already was). Returns 0, and
 * changes nothing, for any other version, or while the plugin is initiated:
 * after InitiateGFX and before CloseDLL, since the capture layer may already
 * have published its page. There is no way back: the mode ends with the
 * module. Call it on the thread that makes the plugin calls. */
EXPORT int CALL SM64RendererOnlyV1(uint32_t version) {
    if (version != 1 || g_initiated) return 0;
    g_renderer_only = TRUE;
    return 1;
}
