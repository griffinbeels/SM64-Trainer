/* A FAKE wrapped graphics plugin for the test host: exports the Zilmar 1.3
 * surface, and its UpdateScreen paints the window the colour the host wrote
 * into fake RDRAM at FAKE_COLOUR_OFFSET (b, g, r bytes) and swaps -- so the
 * wrapper's GL_FRONT read has a known picture to capture. Its ReadScreen
 * answers the same colour WITHOUT a GL context, the way a plugin that
 * renders on its own thread (GLideN64_LINK_4.2) does: a malloc of
 * width*height*3 packed BGR rows, the client rectangle's size, that the
 * caller frees -- so the host's --no-context run exercises the wrapper's
 * second capture point end to end, the free included. */
#include <windows.h>
#include <GL/gl.h>
#include <stddef.h>
#include <stdlib.h>
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

#define FAKE_DIRTY_GL_OFFSET 131
#define GL_READ_FRAMEBUFFER 0x8CA8
#define GL_PIXEL_PACK_BUFFER 0x88EB
#define GL_STATIC_READ 0x88E9
typedef void (WINAPI *fn_gen)(GLsizei, GLuint *);
typedef void (WINAPI *fn_bind)(GLenum, GLuint);
typedef void (WINAPI *fn_buffer_data)(GLenum, ptrdiff_t, const void *, GLenum);

/* Review finding 5's regression guard: a real plugin may leave a
 * framebuffer object, a pixel-pack buffer and its own pack parameters
 * bound after presenting. When the host sets RDRAM[131], this leaves
 * all three dirty so the wrapper's read has to cope. */
static void leave_gl_dirty(void) {
    static GLuint framebuffer, pack_buffer;
    fn_gen gen_framebuffers = (fn_gen)wglGetProcAddress("glGenFramebuffers");
    fn_bind bind_framebuffer = (fn_bind)wglGetProcAddress("glBindFramebuffer");
    fn_gen gen_buffers = (fn_gen)wglGetProcAddress("glGenBuffers");
    fn_bind bind_buffer = (fn_bind)wglGetProcAddress("glBindBuffer");
    fn_buffer_data buffer_data = (fn_buffer_data)wglGetProcAddress("glBufferData");
    if (gen_framebuffers && bind_framebuffer) {
        if (!framebuffer) gen_framebuffers(1, &framebuffer);
        bind_framebuffer(GL_READ_FRAMEBUFFER, framebuffer);
    }
    if (gen_buffers && bind_buffer && buffer_data) {
        if (!pack_buffer) { gen_buffers(1, &pack_buffer); }
        bind_buffer(GL_PIXEL_PACK_BUFFER, pack_buffer);
        buffer_data(GL_PIXEL_PACK_BUFFER, 4 << 20, NULL, GL_STATIC_READ);
    }
    glPixelStorei(GL_PACK_ALIGNMENT, 1);
    glPixelStorei(GL_PACK_ROW_LENGTH, 999);
    glPixelStorei(GL_PACK_SKIP_ROWS, 3);
    glPixelStorei(GL_PACK_SKIP_PIXELS, 5);
}

EXPORT void CALL UpdateScreen(void) {
    if (!g_have_gfx || !wglGetCurrentContext()) return;
    const unsigned char *colour = g_gfx.RDRAM + FAKE_COLOUR_OFFSET;
    glClearColor(colour[2] / 255.0f, colour[1] / 255.0f, colour[0] / 255.0f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    HDC device = GetDC(g_gfx.hWnd);
    SwapBuffers(device);
    ReleaseDC(g_gfx.hWnd, device);
    if (g_gfx.RDRAM[FAKE_DIRTY_GL_OFFSET]) leave_gl_dirty();
    g_updates++;
}

EXPORT void CALL ReadScreen(void **dest, long *width, long *height) {
    /* Instrument sensitivity witness, confined to the fake renderer. */
    if (GetEnvironmentVariableA("SM64_FAKE_READSCREEN_DELAY", NULL, 0)) Sleep(25);
    *dest = NULL; *width = 0; *height = 0;
    if (!g_have_gfx) return;
    RECT client;
    if (!GetClientRect(g_gfx.hWnd, &client)) return;
    long client_width = client.right - client.left, client_height = client.bottom - client.top;
    if (client_width <= 0 || client_height <= 0) return;
    unsigned char *buffer = malloc((size_t)client_width * (size_t)client_height * 3);
    if (!buffer) return;
    const unsigned char *colour = g_gfx.RDRAM + FAKE_COLOUR_OFFSET;
    for (size_t pixel = 0; pixel < (size_t)client_width * (size_t)client_height; pixel++) {
        buffer[pixel * 3] = colour[0];
        buffer[pixel * 3 + 1] = colour[1];
        buffer[pixel * 3 + 2] = colour[2];
    }
    *dest = buffer; *width = client_width; *height = client_height;
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
