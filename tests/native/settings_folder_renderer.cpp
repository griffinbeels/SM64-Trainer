/* A stand-in renderer for tests/test_scene_lab_renderer_load.py: the Zilmar
 * surface the scene-lab hosts call (tools/scene_lab/viewer_host.c and
 * replay_host.c). By default it is the renderer half of a TEST PLUGIN
 * (plugin/gfxwrap/renderer_entries.h; tools/build_plugin.py
 * build_test_plugin links it with the REAL capture layer, so the hosts meet
 * the real SM64RendererOnlyV1) and with the REAL settings-folder seam
 * (plugin/gfxwrap/renderer_settings_folder.cpp). STANDALONE makes it a DLL of
 * its own with plain Zilmar exports and neither seam: LINK's own renderer,
 * or an older Trainer build whose renderer was a second file. No GL, no
 * window of its own.
 *
 * Every call appends one line to the file SM64_FAKE_RENDERER_LOG names:
 *   module <the file this DLL was loaded from>   (DllMain, on load)
 *   call <name>
 *   folder <where GLideN64 would read its ini>   (RomOpen asks as the staged
 *          FindPluginPath does: named() first, else the DLL's own folder)
 *   late <the setter's answer after that lookup> (must refuse: 0) */
#include <windows.h>
#include <cstdio>
#include <cwchar>
#include "renderer_entries.h"
#ifndef STANDALONE
#include "renderer_settings_folder.h"
#endif

#ifdef STANDALONE
#define ENTRY(declaration) extern "C" __declspec(dllexport) declaration
#define DLL_MAIN BOOL WINAPI DllMain
#else
#define ENTRY(declaration) static declaration
#define DLL_MAIN static BOOL WINAPI standin_dll_main
#endif

EXTERN_C IMAGE_DOS_HEADER __ImageBase;

namespace {
void note(const wchar_t *format, const wchar_t *value) {
    wchar_t log[MAX_PATH];
    if (!GetEnvironmentVariableW(L"SM64_FAKE_RENDERER_LOG", log, MAX_PATH)) return;
    FILE *file = _wfopen(log, L"a, ccs=UTF-8");
    if (!file) return;
    fwprintf(file, format, value);
    fputwc(L'\n', file);
    fclose(file);
}

void own_path(wchar_t *path) {
    GetModuleFileNameW(reinterpret_cast<HMODULE>(&__ImageBase), path, MAX_PATH);
}
}

DLL_MAIN(HINSTANCE, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        wchar_t path[MAX_PATH];
        own_path(path);
        note(L"module %ls", path);
    }
    return TRUE;
}

ENTRY(void __cdecl GetDllInfo(PLUGIN_INFO *info)) {
    note(L"call %ls", L"GetDllInfo");
    info->Version = 0x0103;
    info->Type = PLUGIN_TYPE_GFX;
    info->NormalMemory = FALSE;
    info->MemoryBswaped = TRUE;
    strncpy_s(info->Name, sizeof info->Name, "Settings folder stand-in", _TRUNCATE);
}

ENTRY(BOOL __cdecl InitiateGFX(GFX_INFO)) {
    note(L"call %ls", L"InitiateGFX");
    return TRUE;
}

ENTRY(void __cdecl RomOpen(void)) {
    note(L"call %ls", L"RomOpen");
    wchar_t folder[260] = L"";
#ifndef STANDALONE
    if (!renderer_settings_folder::named(folder, 260))
#endif
    {
        own_path(folder);
        for (wchar_t *at = folder; *at; ++at) if (*at == L'\\') *at = L'/';
        if (wchar_t *slash = wcsrchr(folder, L'/')) *slash = 0;
    }
    note(L"folder %ls", folder);
#ifndef STANDALONE
    note(L"late %ls", SM64RendererSettingsFolderV1(folder) ? L"1" : L"0");
#endif
}

ENTRY(void __cdecl ProcessDList(void)) { note(L"call %ls", L"ProcessDList"); }
ENTRY(void __cdecl UpdateScreen(void)) { note(L"call %ls", L"UpdateScreen"); }

ENTRY(void __cdecl ReadScreen(void **pixels, long *width, long *height)) {
    note(L"call %ls", L"ReadScreen");
    *width = 4;
    *height = 2;
    *pixels = HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, 4 * 2 * 3);   // the hosts HeapFree it
}

ENTRY(void __cdecl RomClosed(void)) { note(L"call %ls", L"RomClosed"); }
ENTRY(void __cdecl CloseDLL(void)) { note(L"call %ls", L"CloseDLL"); }

#ifndef STANDALONE
extern "C" const gfx_api_t *__cdecl renderer_entries(void) {
    static gfx_api_t api{};
    api.GetDllInfo = GetDllInfo; api.InitiateGFX = InitiateGFX; api.RomOpen = RomOpen;
    api.ProcessDList = ProcessDList; api.UpdateScreen = UpdateScreen; api.ReadScreen = ReadScreen;
    api.RomClosed = RomClosed; api.CloseDLL = CloseDLL;
    return &api;
}

extern "C" BOOL WINAPI renderer_dll_main(HINSTANCE instance, DWORD reason, LPVOID reserved) {
    return standin_dll_main(instance, reason, reserved);
}
#endif
