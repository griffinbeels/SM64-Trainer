/* Source-owned HGLRC lifetime, independent of SourceV2's layout. Windows x86. */
#pragma once
#include <windows.h>
#include <stdint.h>
#define RCL_ABI_V1 1u
#define RCL_SLOTS 4u
#define RCL_CAP_NEVER_CURRENT_ANCHOR 1u
#ifdef __cplusplus
extern "C" {
#endif
typedef struct { uint64_t value; uint32_t slot, reserved; } rcl_token;
typedef struct {
    rcl_token token;
    uintptr_t context; // owned never-current anchor, not the requested renderer HGLRC
    uint32_t context_generation, pixel_format, reserved;
} rcl_lease;
enum rcl_result { RCL_ACQUIRED=1, RCL_RELEASED, RCL_DELETED, RCL_DEFERRED,
    RCL_STALE, RCL_QUARANTINED, RCL_UNAVAILABLE };
enum rcl_health { RCL_DELETE_FAILED=1, RCL_UNBIND_FAILED=2, RCL_SERIAL_EXHAUSTED=4 };
typedef struct {
    uint32_t bytes, version, capabilities, reserved;
    /* One serialized worker; release on EVERY creation outcome. Use own DC.
     * Never make the leased source context current or retain it after release. */
    int (__cdecl *acquire)(uintptr_t context, uint32_t generation, rcl_lease *);
    int (__cdecl *release)(rcl_token);
    uint32_t (__cdecl *health)(void);
    uint32_t (__cdecl *error)(void);
} rcl_api;
const rcl_api *__cdecl SM64ReplayContextV1(uint32_t version, uint32_t bytes);
#ifdef __cplusplus
}
#include <atomic>
namespace source_context {
// Lifecycle calls belong to the original serialized renderer thread. Revoke
// BEFORE unbind; Unbound AFTER it. Unbound never releases a DC or waits.
bool Publish(HGLRC, HDC, uint32_t generation, int pixel_format);
rcl_token Revoke(HGLRC);
int Retire(rcl_token); // anchor is never current; original deletion/DC ownership unchanged
class Registry {
public:
    using Delete = BOOL (WINAPI *)(HGLRC);
    using Create = HGLRC (WINAPI *)(HDC,HGLRC,const int *);
    explicit Registry(Delete deleter=wglDeleteContext) : delete_(deleter) {}
    // A null factory adopts the passed handle for CPU/direct-sharing witnesses.
    // Production always supplies a factory and reserves a slot before creation.
    bool publish(HGLRC, uint32_t generation, int pixel_format,
                 Create factory=nullptr, HDC dc=nullptr, const int *attributes=nullptr);
    rcl_token revoke(HGLRC);
    int unbound(rcl_token, BOOL succeeded);
    int acquire(uintptr_t context, uint32_t generation, rcl_lease *);
    int release(rcl_token);
    uint32_t health() const { return health_.load(); }
    uint32_t error() const { return error_.load(); }
#ifdef RCL_TEST_HOST
    void test_acquire_probe(void (__cdecl *probe)(unsigned)){probe_=probe;}
#endif
private:
    enum State : uint64_t { free=0, writing=1, live=2, held=3, deleting=4, quarantine=5 };
    static constexpr uint64_t state_mask=7, lifetime_mask=0xffffffff00000000ull;
    struct Slot {
        std::atomic<uint64_t> word{0};
        std::atomic<bool> revoked{true}, retired{false}, failed{false};
        uintptr_t context=0, owned_context=0;
        uint32_t generation=0, pixel_format=0;
    } slots_[RCL_SLOTS];
    uint32_t next_lifetime_=0; // renderer only; never wraps
    std::atomic<uint32_t> health_{0}, error_{0};
    Delete delete_;
#ifdef RCL_TEST_HOST
    void (__cdecl *probe_)(unsigned)=nullptr;
#endif
    int delete_idle(Slot&, uint64_t expected);
};
}
static_assert(sizeof(void*)==4 && sizeof(rcl_token)==16 && sizeof(rcl_lease)==32 && sizeof(rcl_api)==32, "context V1 x86 ABI");
static_assert(std::atomic<uint64_t>::is_always_lock_free, "tagged ownership must be lock free");
#endif
