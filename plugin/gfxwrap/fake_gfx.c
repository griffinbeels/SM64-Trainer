/* A FAKE wrapped graphics plugin for the test host: exports the Zilmar 1.3
 * surface, and its UpdateScreen paints the window the colour the host wrote
 * into fake RDRAM at FAKE_COLOUR_OFFSET (b, g, r bytes) and swaps -- so the
 * wrapper's GL_FRONT read has a known picture to capture. Nothing else. */
#include <windows.h>
#include <GL/gl.h>
#include <string.h>
#include "zilmar.h"

#define EXPORT __declspec(dllexport)
#define CALL __cdecl
#define FAKE_COLOUR_OFFSET 128

static GFX_INFO g_gfx;
static BOOL g_have_gfx;
static unsigned g_updates;

EXPORT void CALL GetDllInfo(PLUGIN_INFO *info) {
    info->Version = 0x0103;
    info->Type = PLUGIN_TYPE_GFX;
    info->NormalMemory = FALSE;
    info->MemoryBswaped = TRUE;
    strncpy_s(info->Name, sizeof info->Name, "Fake GFX for the capture layer's host", _TRUNCATE);
}

EXPORT BOOL CALL InitiateGFX(GFX_INFO info) {
    g_gfx = info;
    g_have_gfx = TRUE;
    return TRUE;
}

EXPORT void CALL UpdateScreen(void) {
    if (!g_have_gfx || !wglGetCurrentContext()) return;
    const unsigned char *colour = g_gfx.RDRAM + FAKE_COLOUR_OFFSET;
    glClearColor(colour[2] / 255.0f, colour[1] / 255.0f, colour[0] / 255.0f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    HDC device = GetDC(g_gfx.hWnd);
    SwapBuffers(device);
    ReleaseDC(g_gfx.hWnd, device);
    g_updates++;
}

EXPORT void CALL ProcessDList(void) {}
EXPORT void CALL ProcessRDPList(void) {}
EXPORT void CALL RomOpen(void) {}
EXPORT void CALL RomClosed(void) {}
EXPORT void CALL CloseDLL(void) { g_have_gfx = FALSE; }
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
