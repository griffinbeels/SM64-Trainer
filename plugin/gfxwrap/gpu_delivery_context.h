/* Dedicated hidden worker context. Never makes a source/leased anchor current. */
#pragma once
#include "context_lifetime.h"
#include "renderer_boundary.h"
namespace gpu_delivery {
class Context {
public:
    bool create(const rb_surface&, const rcl_api&);
    bool revalidate(const rb_surface&, const rcl_api&);
    bool destroy();
    DWORD error() const { return error_; }
    bool quarantined() const { return quarantined_; }
private:
    HWND window_ = nullptr;
    HDC dc_ = nullptr;
    HGLRC context_ = nullptr, temporary_ = nullptr;
    DWORD worker_ = 0, error_ = 0;
    bool quarantined_ = false;
};
}
