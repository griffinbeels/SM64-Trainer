#include "gpu_delivery_context.h"
#include <gl/GL.h>
namespace gpu_delivery {
namespace {
bool released(int value) { return value == RCL_RELEASED || value == RCL_DELETED; }
}
bool Context::create(const rb_surface &s, const rcl_api &source) {
    if (window_ || context_ || temporary_ || quarantined_) return false;
    worker_ = GetCurrentThreadId();
    rcl_lease lease{};
    if (source.acquire(s.context, s.context_generation, &lease) != RCL_ACQUIRED) {
        error_ = source.error(); return false;
    }
    bool ok = false;
    do {
        WNDCLASSW wc{}; wc.style = CS_OWNDC; wc.lpfnWndProc = DefWindowProcW;
        wc.hInstance = GetModuleHandleW(nullptr); wc.lpszClassName = L"SM64GpuDeliveryHiddenV1";
        if (!RegisterClassW(&wc) && GetLastError() != ERROR_CLASS_ALREADY_EXISTS) break;
        window_ = CreateWindowExW(0, wc.lpszClassName, L"", WS_POPUP, 0, 0, 1, 1,
                                 nullptr, nullptr, wc.hInstance, nullptr);
        if (!window_ || IsWindowVisible(window_)) break;
        dc_ = GetDC(window_); if (!dc_) break;
        PIXELFORMATDESCRIPTOR pfd{};
        if (!DescribePixelFormat(dc_, int(lease.pixel_format), sizeof pfd, &pfd)
            || !SetPixelFormat(dc_, int(lease.pixel_format), &pfd)) break;
        temporary_ = wglCreateContext(dc_);
        if (!temporary_ || !wglMakeCurrent(dc_, temporary_)) break;
        using Create = HGLRC(WINAPI *)(HDC,HGLRC,const int*);
        auto create = reinterpret_cast<Create>(wglGetProcAddress("wglCreateContextAttribsARB"));
        if (reinterpret_cast<uintptr_t>(create) <= 3 || reinterpret_cast<uintptr_t>(create) == UINTPTR_MAX) break;
        const int attributes[] = {0x2091,4,0x2092,5,0x9126,1,0};
        context_ = create(dc_, reinterpret_cast<HGLRC>(lease.context), attributes);
        if (!context_) break;
        // Only our own temporary/current context is touched here.
        if (!wglMakeCurrent(nullptr,nullptr)) { quarantined_ = true; break; }
        if (!wglDeleteContext(temporary_)) { quarantined_ = true; break; }
        temporary_ = nullptr;
        if (!wglMakeCurrent(dc_,context_)) break;
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
}
