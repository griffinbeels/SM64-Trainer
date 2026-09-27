/* Dedicated hidden worker context. Never makes a source/leased anchor current. */
#pragma once
#include "context_lifetime.h"
#include "renderer_boundary.h"
namespace gpu_delivery {
// QPC after each step of Context::create, for the gpu_prepare log line.
struct CreateSteps { uint64_t begin, window, temporary, shared, temporary_deleted, current; };
class Context {
public:
    bool create(const rb_surface&, const rcl_api&);
    bool revalidate(const rb_surface&, const rcl_api&);
    bool destroy();
    DWORD error() const { return error_; }
    bool quarantined() const { return quarantined_; }
    const CreateSteps& steps() const { return steps_; }
private:
    HWND window_ = nullptr;
    HDC dc_ = nullptr;
    HGLRC context_ = nullptr, temporary_ = nullptr;
    DWORD worker_ = 0, error_ = 0;
    bool quarantined_ = false;
    CreateSteps steps_{};
};

// The driver warm-up (gd_prewarm). The first GL context and the first D3D11
// device a new thread creates make the NVIDIA driver stall the renderer's own
// thread once per process, 104-313 ms (docs/replay-gpu-runtime.md, "First
// capture freeze"). Paying it on the delivery worker at practice ROM open,
// before the renderer draws, leaves the first capture with nothing to pay.
// Everything created here is unshared and deleted before return: no share
// group, nothing lingers (the round-48 rule). `parts` are gd_warm_part bits.
struct WarmSteps { uint64_t begin, legacy, core, d3d, end; uint32_t parts, error; };
bool warm_driver(uint32_t parts, WarmSteps*);
}
