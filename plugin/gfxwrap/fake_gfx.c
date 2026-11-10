/* A FAKE renderer for the test plugin (renderer_entries.h): the Zilmar 1.3
 * surface without the SourceV2 export, linked with the capture layer by
 * tools/build_plugin.py build_test_plugin, so the plugin forwards every call
 * to it and never captures. SM64_FAKE_INIT_FAIL makes InitiateGFX fail;
 * SM64_FAKE_DLIST_DELAY makes each ProcessDList take 25 ms. */
#include <windows.h>
#include <string.h>
#include "renderer_entries.h"

static void __cdecl fake_info(PLUGIN_INFO *info) {
    info->Version = 0x0103;
    info->Type = PLUGIN_TYPE_GFX;
    info->NormalMemory = FALSE;
    info->MemoryBswaped = TRUE;
    strncpy_s(info->Name, sizeof info->Name, "Fake GFX for the capture layer's host", _TRUNCATE);
}

static BOOL __cdecl fake_initiate(GFX_INFO info) {
    (void)info;
    return GetEnvironmentVariableA("SM64_FAKE_INIT_FAIL", NULL, 0) ? FALSE : TRUE;
}

static void __cdecl fake_list(void) {
    if (GetEnvironmentVariableA("SM64_FAKE_DLIST_DELAY", NULL, 0)) Sleep(25);
}
static void __cdecl fake_nothing(void) {}
static void __cdecl fake_move(int x, int y) { (void)x; (void)y; }
static void __cdecl fake_capture(char *directory) { (void)directory; }
static void __cdecl fake_window(HWND parent) { (void)parent; }

static const gfx_api_t k_fake = {
    .GetDllInfo = fake_info,
    .InitiateGFX = fake_initiate,
    .ProcessDList = fake_list,
    .UpdateScreen = fake_nothing,
    .ProcessRDPList = fake_nothing,
    .RomOpen = fake_nothing,
    .RomClosed = fake_nothing,
    .CloseDLL = fake_nothing,
    .ChangeWindow = fake_nothing,
    .DrawScreen = fake_nothing,
    .ShowCFB = fake_nothing,
    .ViStatusChanged = fake_nothing,
    .ViWidthChanged = fake_nothing,
    .MoveScreen = fake_move,
    .CaptureScreen = fake_capture,
    .DllConfig = fake_window,
    .DllTest = fake_window,
};

const gfx_api_t *__cdecl renderer_entries(void) { return &k_fake; }

BOOL WINAPI renderer_dll_main(HINSTANCE instance, DWORD reason, LPVOID reserved) {
    (void)instance; (void)reason; (void)reserved;
    return TRUE;
}
