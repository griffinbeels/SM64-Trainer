/* A stand-in renderer for tests/test_scene_lab_renderer_load.py: the Zilmar
 * surface the scene-lab hosts call (tools/scene_lab/viewer_host.c and
 * replay_host.c), linked with the REAL settings-folder seam
 * (plugin/gfxwrap/renderer_settings_folder.cpp) unless NO_SETTINGS_FOLDER,
 * which stands in for LINK's own renderer. No GL, no window of its own.
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
#include "zilmar.h"
#ifndef NO_SETTINGS_FOLDER
#include "renderer_settings_folder.h"
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

BOOL WINAPI DllMain(HINSTANCE, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        wchar_t path[MAX_PATH];
        own_path(path);
        note(L"module %ls", path);
    }
    return TRUE;
}

#define EXPORT extern "C" __declspec(dllexport)

EXPORT void __cdecl GetDllInfo(PLUGIN_INFO *info) {
    note(L"call %ls", L"GetDllInfo");
    info->Version = 0x0103;
    info->Type = PLUGIN_TYPE_GFX;
    info->NormalMemory = FALSE;
    info->MemoryBswaped = TRUE;
    strncpy_s(info->Name, sizeof info->Name, "Settings folder stand-in", _TRUNCATE);
}

EXPORT BOOL __cdecl InitiateGFX(GFX_INFO) {
    note(L"call %ls", L"InitiateGFX");
    return TRUE;
}

EXPORT void __cdecl RomOpen(void) {
    note(L"call %ls", L"RomOpen");
    wchar_t folder[260] = L"";
#ifndef NO_SETTINGS_FOLDER
    if (!renderer_settings_folder::named(folder, 260))
#endif
    {
        own_path(folder);
        for (wchar_t *at = folder; *at; ++at) if (*at == L'\\') *at = L'/';
        if (wchar_t *slash = wcsrchr(folder, L'/')) *slash = 0;
    }
    note(L"folder %ls", folder);
#ifndef NO_SETTINGS_FOLDER
    note(L"late %ls", SM64RendererSettingsFolderV1(folder) ? L"1" : L"0");
#endif
}

EXPORT void __cdecl ProcessDList(void) { note(L"call %ls", L"ProcessDList"); }
EXPORT void __cdecl UpdateScreen(void) { note(L"call %ls", L"UpdateScreen"); }

EXPORT void __cdecl ReadScreen(void **pixels, long *width, long *height) {
    note(L"call %ls", L"ReadScreen");
    *width = 4;
    *height = 2;
    *pixels = HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, 4 * 2 * 3);   // the hosts HeapFree it
}

EXPORT void __cdecl RomClosed(void) { note(L"call %ls", L"RomClosed"); }
EXPORT void __cdecl CloseDLL(void) { note(L"call %ls", L"CloseDLL"); }
