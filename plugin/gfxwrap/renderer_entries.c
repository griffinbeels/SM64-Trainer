/* The shipped plugin's renderer (renderer_entries.h): LINK's GLideN64 v4.2's
 * own Zilmar functions, renamed GLideN64_<name> by tools/build_renderer.py
 * when it stages the upstream tree, and its DllMain, renamed GLideN64_DllMain.
 * Compiled only inside that staged tree; upstream's bodies are unchanged
 * (each still calls api().<name>, and RomOpen still clears RDRAMSize).
 *
 * DllAbout is left out: the plugin's About box is the capture layer's own
 * (gfxwrap.c), and upstream's needs its Qt settings window. */
#include "renderer_entries.h"

void __cdecl GLideN64_GetDllInfo(PLUGIN_INFO *info);
BOOL __cdecl GLideN64_InitiateGFX(GFX_INFO info);
void __cdecl GLideN64_CloseDLL(void);
void __cdecl GLideN64_ChangeWindow(void);
void __cdecl GLideN64_DrawScreen(void);
void __cdecl GLideN64_ProcessDList(void);
void __cdecl GLideN64_ProcessRDPList(void);
void __cdecl GLideN64_RomClosed(void);
void __cdecl GLideN64_RomOpen(void);
void __cdecl GLideN64_ShowCFB(void);
void __cdecl GLideN64_UpdateScreen(void);
void __cdecl GLideN64_ViStatusChanged(void);
void __cdecl GLideN64_ViWidthChanged(void);
void __cdecl GLideN64_DllConfig(HWND parent);
void __cdecl GLideN64_DllTest(HWND parent);
void __cdecl GLideN64_MoveScreen(int x, int y);
void __cdecl GLideN64_CaptureScreen(char *directory);
void __cdecl GLideN64_FBRead(unsigned int address);
void __cdecl GLideN64_FBWrite(unsigned int address, unsigned int size);
void __cdecl GLideN64_FBWList(void *entries, unsigned int size);
void __cdecl GLideN64_FBGetFrameBufferInfo(void *info);
void __cdecl GLideN64_ReadScreen(void **dest, long *width, long *height);
BOOL WINAPI GLideN64_DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved);

static const gfx_api_t k_renderer = {
    .GetDllInfo = GLideN64_GetDllInfo,
    .CloseDLL = GLideN64_CloseDLL,
    .ChangeWindow = GLideN64_ChangeWindow,
    .DrawScreen = GLideN64_DrawScreen,
    .ProcessDList = GLideN64_ProcessDList,
    .ProcessRDPList = GLideN64_ProcessRDPList,
    .RomClosed = GLideN64_RomClosed,
    .RomOpen = GLideN64_RomOpen,
    .ShowCFB = GLideN64_ShowCFB,
    .UpdateScreen = GLideN64_UpdateScreen,
    .ViStatusChanged = GLideN64_ViStatusChanged,
    .ViWidthChanged = GLideN64_ViWidthChanged,
    .DllConfig = GLideN64_DllConfig,
    .DllTest = GLideN64_DllTest,
    .InitiateGFX = GLideN64_InitiateGFX,
    .MoveScreen = GLideN64_MoveScreen,
    .CaptureScreen = GLideN64_CaptureScreen,
    .FBRead = GLideN64_FBRead,
    .FBWrite = GLideN64_FBWrite,
    .FBWList = GLideN64_FBWList,
    .FBGetFrameBufferInfo = GLideN64_FBGetFrameBufferInfo,
    .ReadScreen = GLideN64_ReadScreen,
};

const gfx_api_t *__cdecl renderer_entries(void) { return &k_renderer; }

BOOL WINAPI renderer_dll_main(HINSTANCE instance, DWORD reason, LPVOID reserved) {
    return GLideN64_DllMain(instance, reason, reserved);
}
