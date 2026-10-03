/* The overlay warm-up (overlay_warmup.h), driven the way gfxwrap.c drives it.
 * argv[1]:
 *   logic - its policy with a stand-in device maker: no GPU, no window
 *   cold  - a busy GL renderer that never calls it; an overlay's hook at 3 s
 *   warm  - the same renderer calling it as the wrapper does, with a 250 ms
 *           load at 1.5 s (no new picture, as a practice reset holds it)
 * The overlay's hook does what Discord's does inside SwapBuffers: a D3D11
 * device, WGL_NV_DX_interop2 and one registered texture. The window is
 * hidden and never shown. Prints one JSON line. */
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <timeapi.h>
#include <GL/gl.h>
#include <d3d11.h>
#include "overlay_warmup.h"
#include <algorithm>
#include <cstdio>
#include <stdexcept>
#include <string>
#include <vector>

static void check(bool ok, const char *what) { if (!ok) throw std::runtime_error(what); }

static int fake_made = 0;
static int fake_maker() { ++fake_made; return 1; }

static void logic() {
    ow_set_maker(fake_maker);
    auto vi = [](double ms) { const int warmed = ow_vi(); if (warmed) ow_vi_done(ms); return warmed; };
    for (int i = 0; i < 10; ++i) { check(!vi(1), "never before the first picture"); }
    for (int i = 0; i < OW_SETTLE_LISTS - 1; ++i) {
        ow_list(); check(!vi(1), "30 fps: a list every other VI");
        check(!vi(1), "one empty VI is ordinary play");
    }
    for (int i = 0; i < 8; ++i) check(!vi(1), "a hold before enough pictures warms nothing");
    ow_list();
    for (int i = 0; i < 3; ++i) { ow_list(); check(!vi(1), "drawing"); check(!vi(1), "drawing"); check(!vi(1), "a 20 fps lag VI"); }
    int fired = 0;
    ow_list();
    for (int i = 1; i <= 8; ++i) if (vi(5)) { fired = i; break; } // too quick: nothing to absorb yet
    check(fired == OW_HOLD_VIS, "the hold's sixth VI without a picture warms");
    check(ow_read().attempts == 1 && !ow_read().absorbed && fake_made == 1, "a quick warm is not absorbed");
    ow_list(); vi(1);
    for (int i = 0; i < 8; ++i) if (vi(120)) break;
    check(ow_read().attempts == 2 && ow_read().absorbed && fake_made == 2, "the next hold tries again and absorbs");
    ow_list(); vi(1);
    for (int i = 0; i < 20; ++i) check(!vi(120), "once absorbed, nothing more");
    ow_reset();
    check(ow_read().lists == 0 && ow_read().devices == 0, "a stand-in maker keeps no device");
    std::printf("{\"mode\":\"logic\",\"ok\":true,\"attempts\":%u}\n", ow_read().attempts);
}

typedef HANDLE (WINAPI *DXOpenDevice)(void *);
typedef HANDLE (WINAPI *DXRegisterObject)(HANDLE, void *, GLuint, GLenum, GLenum);
typedef BOOL (WINAPI *DXLockObjects)(HANDLE, GLint, HANDLE *);
typedef HRESULT (WINAPI *CreateDevice)(IDXGIAdapter *, D3D_DRIVER_TYPE, HMODULE, UINT, const D3D_FEATURE_LEVEL *,
                                        UINT, UINT, ID3D11Device **, D3D_FEATURE_LEVEL *, ID3D11DeviceContext **);

static double now_ms() {
    static LARGE_INTEGER f{}; if (!f.QuadPart) QueryPerformanceFrequency(&f);
    LARGE_INTEGER t{}; QueryPerformanceCounter(&t); return double(t.QuadPart) * 1000.0 / double(f.QuadPart);
}

static bool overlay_init() {
    HMODULE d3d11 = LoadLibraryW(L"d3d11.dll");
    auto create = reinterpret_cast<CreateDevice>(GetProcAddress(d3d11, "D3D11CreateDevice"));
    ID3D11Device *device = nullptr; ID3D11DeviceContext *context = nullptr;
    if (!create || FAILED(create(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                                 nullptr, 0, D3D11_SDK_VERSION, &device, nullptr, &context))) return false;
    auto open = reinterpret_cast<DXOpenDevice>(wglGetProcAddress("wglDXOpenDeviceNV"));
    auto reg = reinterpret_cast<DXRegisterObject>(wglGetProcAddress("wglDXRegisterObjectNV"));
    auto lock = reinterpret_cast<DXLockObjects>(wglGetProcAddress("wglDXLockObjectsNV"));
    auto unlock = reinterpret_cast<DXLockObjects>(wglGetProcAddress("wglDXUnlockObjectsNV"));
    if (!open || !reg || !lock || !unlock) return false;
    HANDLE dx = open(device);
    D3D11_TEXTURE2D_DESC desc{}; desc.Width = 1600; desc.Height = 1200; desc.MipLevels = 1; desc.ArraySize = 1;
    desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM; desc.SampleDesc.Count = 1; desc.Usage = D3D11_USAGE_DEFAULT;
    desc.BindFlags = D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_RENDER_TARGET;
    ID3D11Texture2D *texture = nullptr;
    if (!dx || FAILED(device->CreateTexture2D(&desc, nullptr, &texture))) return false;
    GLuint gl = 0; glGenTextures(1, &gl);
    HANDLE object = reg(dx, texture, gl, GL_TEXTURE_2D, 0x0001 /* WGL_ACCESS_READ_WRITE_NV */);
    return object && lock(dx, 1, &object) && unlock(dx, 1, &object); // kept, as the overlay keeps it
}

static void renderer(bool warm) {
    timeBeginPeriod(1);
    const HWND foreground = GetForegroundWindow();
    WNDCLASSW cls{}; cls.style = CS_OWNDC; cls.lpfnWndProc = DefWindowProcW; cls.hInstance = GetModuleHandleW(nullptr);
    cls.lpszClassName = L"OverlayWarmupHidden"; RegisterClassW(&cls);
    HWND window = CreateWindowExW(WS_EX_NOACTIVATE, cls.lpszClassName, L"", WS_POPUP, 0, 0, 1600, 1200,
                                  nullptr, nullptr, cls.hInstance, nullptr);
    HDC dc = GetDC(window);
    PIXELFORMATDESCRIPTOR pfd{}; pfd.nSize = sizeof(pfd); pfd.nVersion = 1;
    pfd.dwFlags = PFD_DRAW_TO_WINDOW | PFD_SUPPORT_OPENGL | PFD_DOUBLEBUFFER; pfd.iPixelType = PFD_TYPE_RGBA;
    pfd.cColorBits = 32; pfd.cDepthBits = 24;
    SetPixelFormat(dc, ChoosePixelFormat(dc, &pfd), &pfd);
    HGLRC rc = wglCreateContext(dc); wglMakeCurrent(dc, rc);
    std::vector<float> quad = {-0.01f, -0.01f, 0.01f, -0.01f, 0.01f, 0.01f, -0.01f, 0.01f};
    glEnableClientState(GL_VERTEX_ARRAY); glVertexPointer(2, GL_FLOAT, 0, quad.data());
    const double period = 1000.0 / 60.0, start = now_ms();
    double next = start, overlay_window = 0, hold_max = 0, playing_max = 0, warm_at = -1;
    bool hooked = false, ok = true;
    for (unsigned vi = 0; now_ms() - start < 4500; ++vi) {
        const double begin = now_ms(), t = begin - start;
        const bool hold = t >= 1500 && t < 1750;
        const bool draws = !hold && vi % 2 == 0; // 30 pictures a second, none while loading
        if (draws) {
            glViewport(0, 0, 1600, 1200); glClearColor(0.1f, 0.2f, 0.3f, 1); glClear(GL_COLOR_BUFFER_BIT);
            for (int i = 0; i < 3000; ++i) {
                glColor3f(float(i % 7) / 7.f, float(i % 5) / 5.f, float(i % 3) / 3.f);
                glLoadIdentity(); glTranslatef(float(i % 97) / 50.f - 1, float(i % 89) / 45.f - 1, 0);
                glDrawArrays(GL_TRIANGLE_FAN, 0, 4);
            }
            if (warm) ow_list();
        }
        // UpdateScreen: the wrapper's order.
        const double screen = now_ms();
        const int warming = warm ? ow_vi() : 0;
        if (warming) warm_at = t;
        if (!hooked && t >= 3000) { hooked = true; ok = overlay_init(); } // the overlay's next present
        SwapBuffers(dc);
        const double took = now_ms() - screen;
        if (warming) ow_vi_done(took);
        const double vi_ms = now_ms() - begin;
        if (t >= 3000 && t < 4000) overlay_window = std::max(overlay_window, vi_ms);
        else if (hold) hold_max = std::max(hold_max, vi_ms);
        else if (t > 300) playing_max = std::max(playing_max, vi_ms);
        next += period;
        const double after = now_ms();
        if (next > after) Sleep(DWORD(next - after)); else next = after;
    }
    const ow_stats stats = ow_read();
    std::printf("{\"mode\":\"%s\",\"ok\":%s,\"attempts\":%u,\"devices\":%u,\"absorbed\":%u,\"warm_vi_ms\":%.1f,"
                "\"warm_at_ms\":%.0f,\"hold_max_ms\":%.1f,\"overlay_window_ms\":%.1f,\"playing_max_ms\":%.1f,"
                "\"hidden\":%s,\"focus_unchanged\":%s}\n",
                warm ? "warm" : "cold", ok ? "true" : "false", stats.attempts, stats.devices, stats.absorbed,
                stats.last_vi_ms, warm_at, hold_max, overlay_window, playing_max,
                IsWindowVisible(window) ? "false" : "true", GetForegroundWindow() == foreground ? "true" : "false");
    wglMakeCurrent(nullptr, nullptr); wglDeleteContext(rc); ReleaseDC(window, dc); DestroyWindow(window);
    timeEndPeriod(1);
    check(ok, "the overlay's hook ran");
}

int main(int argc, char **argv) {
    const std::string mode = argc > 1 ? argv[1] : "logic";
    try {
        if (mode == "logic") logic();
        else renderer(mode == "warm");
        return 0;
    } catch (const std::exception &error) {
        std::fprintf(stderr, "overlay warm-up witness: %s\n", error.what());
        return 1;
    }
}
