/* A FAKE wrapped graphics plugin for the test hosts: the Zilmar 1.3 surface
 * without the SourceV2 export, so the wrapper forwards every call to it and
 * never captures. SM64_FAKE_INIT_FAIL makes InitiateGFX fail;
 * SM64_FAKE_DLIST_DELAY makes each ProcessDList take 25 ms. */
#include <windows.h>
#include <string.h>
#include "zilmar.h"

#define EXPORT __declspec(dllexport)
#define CALL __cdecl

EXPORT void CALL GetDllInfo(PLUGIN_INFO *info) {
    info->Version = 0x0103;
    info->Type = PLUGIN_TYPE_GFX;
    info->NormalMemory = FALSE;
    info->MemoryBswaped = TRUE;
    strncpy_s(info->Name, sizeof info->Name, "Fake GFX for the capture layer's host", _TRUNCATE);
}

EXPORT BOOL CALL InitiateGFX(GFX_INFO info) {
    (void)info;
    return GetEnvironmentVariableA("SM64_FAKE_INIT_FAIL", NULL, 0) ? FALSE : TRUE;
}

EXPORT void CALL ProcessDList(void) {
    if (GetEnvironmentVariableA("SM64_FAKE_DLIST_DELAY", NULL, 0)) Sleep(25);
}
EXPORT void CALL UpdateScreen(void) {}
EXPORT void CALL ProcessRDPList(void) {}
EXPORT void CALL RomOpen(void) {}
EXPORT void CALL RomClosed(void) {}
EXPORT void CALL CloseDLL(void) {}
EXPORT void CALL ChangeWindow(void) {}
EXPORT void CALL DrawScreen(void) {}
EXPORT void CALL ShowCFB(void) {}
EXPORT void CALL ViStatusChanged(void) {}
EXPORT void CALL ViWidthChanged(void) {}
EXPORT void CALL MoveScreen(int x, int y) { (void)x; (void)y; }
EXPORT void CALL CaptureScreen(char *directory) { (void)directory; }
EXPORT void CALL DllAbout(HWND parent) { (void)parent; }
EXPORT void CALL DllConfig(HWND parent) { (void)parent; }
EXPORT void CALL DllTest(HWND parent) { (void)parent; }
