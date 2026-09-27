#include "gpu_delivery_context.h"
#include "gpu_delivery.h"
#include "gpu_bridge.h"
#include <gl/GL.h>
namespace gpu_delivery {
namespace {
bool released(int value) { return value == RCL_RELEASED || value == RCL_DELETED; }
uint64_t now() { LARGE_INTEGER v{}; QueryPerformanceCounter(&v); return uint64_t(v.QuadPart); }
const wchar_t *window_class() {
    WNDCLASSW wc{}; wc.style = CS_OWNDC; wc.lpfnWndProc = DefWindowProcW;
    wc.hInstance = GetModuleHandleW(nullptr); wc.lpszClassName = L"SM64GpuDeliveryHiddenV1";
    if (!RegisterClassW(&wc) && GetLastError() != ERROR_CLASS_ALREADY_EXISTS) return nullptr;
    return wc.lpszClassName;
}
HWND hidden_window() {
    const auto name = window_class();
    return name ? CreateWindowExW(0, name, L"", WS_POPUP, 0, 0, 1, 1,
                                  nullptr, nullptr, GetModuleHandleW(nullptr), nullptr) : nullptr;
}
using CreateAttribs = HGLRC(WINAPI *)(HDC,HGLRC,const int*);
CreateAttribs attribs_factory() {
    auto create = reinterpret_cast<CreateAttribs>(wglGetProcAddress("wglCreateContextAttribsARB"));
    const auto address = reinterpret_cast<uintptr_t>(create);
    return address <= 3 || address == UINTPTR_MAX ? nullptr : create;
}
const int core45[] = {0x2091,4,0x2092,5,0x9126,1,0};
}
bool Context::create(const rb_surface &s, const rcl_api &source) {
    if (window_ || context_ || temporary_ || quarantined_) return false;
    steps_ = {}; steps_.begin = now();
    worker_ = GetCurrentThreadId();
    rcl_lease lease{};
    if (source.acquire(s.context, s.context_generation, &lease) != RCL_ACQUIRED) {
        error_ = source.error(); return false;
    }
    bool ok = false;
    do {
        window_ = hidden_window();
        if (!window_ || IsWindowVisible(window_)) break;
        dc_ = GetDC(window_); if (!dc_) break;
        PIXELFORMATDESCRIPTOR pfd{};
        if (!DescribePixelFormat(dc_, int(lease.pixel_format), sizeof pfd, &pfd)
            || !SetPixelFormat(dc_, int(lease.pixel_format), &pfd)) break;
        steps_.window = now();
        temporary_ = wglCreateContext(dc_);
        if (!temporary_ || !wglMakeCurrent(dc_, temporary_)) break;
        const auto create = attribs_factory();
        if (!create) break;
        steps_.temporary = now();
        context_ = create(dc_, reinterpret_cast<HGLRC>(lease.context), core45);
        if (!context_) break;
        steps_.shared = now();
        // Only our own temporary/current context is touched here.
        if (!wglMakeCurrent(nullptr,nullptr)) { quarantined_ = true; break; }
        if (!wglDeleteContext(temporary_)) { quarantined_ = true; break; }
        temporary_ = nullptr;
        steps_.temporary_deleted = now();
        if (!wglMakeCurrent(dc_,context_)) break;
        steps_.current = now();
        GLint major=0,minor=0,profile=0;
        glGetIntegerv(0x821B,&major); glGetIntegerv(0x821C,&minor); glGetIntegerv(0x9126,&profile);
        ok = (major>4 || (major==4 && minor>=5)) && profile==1 && glGetError()==GL_NO_ERROR;
    } while(false);
    error_ = ok ? 0 : GetLastError();
    if (!error_ && !ok) error_ = ERROR_NOT_SUPPORTED;
    // Release on EVERY path, including context-creation failure and cancellation.
    if (!released(source.release(lease.token))) { ok=false; quarantined_=true; error_=source.error(); }
    if (!ok && !quarantined_) destroy();
    return ok;
}
bool Context::revalidate(const rb_surface &s,const rcl_api &source) {
    if (worker_!=GetCurrentThreadId() || !context_ || wglGetCurrentContext()!=context_ || quarantined_) return false;
    rcl_lease lease{};
    if (source.acquire(s.context,s.context_generation,&lease)!=RCL_ACQUIRED) return false;
    const bool same=lease.context_generation==s.context_generation && int(lease.pixel_format)==GetPixelFormat(dc_);
    return released(source.release(lease.token)) && same && !source.health();
}
bool Context::destroy() {
    if (worker_ && worker_!=GetCurrentThreadId()) return false;
    if (quarantined_) return false;
    if (context_ || temporary_) {
        if (!wglMakeCurrent(nullptr,nullptr)) { error_=GetLastError();quarantined_=true;return false; }
        if (context_ && !wglDeleteContext(context_)) { error_=GetLastError();quarantined_=true;return false; }
        context_=nullptr;
        if (temporary_ && !wglDeleteContext(temporary_)) { error_=GetLastError();quarantined_=true;return false; }
        temporary_=nullptr;
    }
    if (dc_) { ReleaseDC(window_,dc_);dc_=nullptr; }
    if (window_) { DestroyWindow(window_);window_=nullptr; }
    return true;
}
bool warm_driver(uint32_t parts, WarmSteps *out) {
    WarmSteps steps{}; steps.begin = now(); steps.parts = parts;
    // Never while this thread owns a context: the warm-up must not unbind one.
    if (wglGetCurrentContext()) { steps.error = ERROR_BUSY; steps.end = now(); if (out) *out = steps; return false; }
    HWND window = hidden_window();
    HDC dc = window && !IsWindowVisible(window) ? GetDC(window) : nullptr;
    HGLRC legacy = nullptr;
    bool ok = false;
    do {
        if (!dc) break;
        // Any accelerated RGBA format: the warm-up needs the driver, not the
        // renderer's exact format, and must not know the renderer's context.
        PIXELFORMATDESCRIPTOR pfd{}; pfd.nSize = sizeof pfd; pfd.nVersion = 1;
        pfd.dwFlags = PFD_DRAW_TO_WINDOW | PFD_SUPPORT_OPENGL | PFD_DOUBLEBUFFER;
        pfd.iPixelType = PFD_TYPE_RGBA; pfd.cColorBits = 32;
        const int format = ChoosePixelFormat(dc, &pfd);
        if (!format || !SetPixelFormat(dc, format, &pfd)) break;
        legacy = wglCreateContext(dc);
        if (!legacy || !wglMakeCurrent(dc, legacy)) break;
        steps.legacy = now();
        if (parts & GD_WARM_CORE) {
            const auto create = attribs_factory();
            const HGLRC core = create ? create(dc, nullptr, core45) : nullptr;
            if (!core) break;
            const bool current = wglMakeCurrent(dc, core) != FALSE;
            glGetError();
            if (!wglMakeCurrent(dc, legacy) || !wglDeleteContext(core) || !current) break;
            steps.core = now();
        }
        if (parts & GD_WARM_D3D) {
            // The adapter GL renders on (GL_DEVICE_LUID_EXT), as the bridge does.
            using Bytes = void(APIENTRY *)(GLenum,GLubyte*);
            const auto luid_query = reinterpret_cast<Bytes>(wglGetProcAddress("glGetUnsignedBytevEXT"));
            const auto address = reinterpret_cast<uintptr_t>(luid_query);
            if (address <= 3 || address == UINTPTR_MAX) break;
            LUID adapter{};
            luid_query(0x9599, reinterpret_cast<GLubyte*>(&adapter));
            if (glGetError() != GL_NO_ERROR) break;
            Microsoft::WRL::ComPtr<ID3D11Device> device;
            Microsoft::WRL::ComPtr<ID3D11DeviceContext> context;
            if (!gpu_bridge::device_for_luid(adapter, &device, &context)) break;
            context.Reset(); device.Reset();
            steps.d3d = now();
        }
        ok = true;
    } while (false);
    if (!ok) steps.error = GetLastError() ? GetLastError() : ERROR_NOT_SUPPORTED;
    if (legacy) { wglMakeCurrent(nullptr, nullptr); wglDeleteContext(legacy); }
    if (dc) ReleaseDC(window, dc);
    if (window) DestroyWindow(window);
    steps.end = now();
    if (out) *out = steps;
    return ok;
}
}
