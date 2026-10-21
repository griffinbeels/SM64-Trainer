/* Receiver-only mapping. The existing captured-source worker owns this page;
 * it does not create a second source consumer or touch network state. */
#pragma once
#include "ghost_live_session.h"
#include "ghost_ipc.h"
#include <windows.h>

namespace ghost_live_ipc {
#pragma pack(push, 4)
struct Header {
    uint32_t magic,version,bytes,request_bytes,producer_pid,producer_created_lo,producer_created_hi,generation;
    uint32_t state,reason;uint64_t applied_revision;uint32_t reserved[52];
};
struct Page {Header header;ghost_live_session::Update request;uint8_t padding[4096-256-1692];};
#pragma pack(pop)
static_assert(sizeof(Header)==256 && sizeof(Page)==4096,"receiver mapping ABI");
class Receiver {
    HANDLE mapping=nullptr,mutex=nullptr,event=nullptr;Page *page=nullptr;
    Header initial{};bool initialized=false;
    bool initialize() noexcept;
    ghost_live_session::LocalOwner prior_owner{};
    uint8_t prior_room[16]{};
    uint64_t offered_revision=0,native_revision=0;bool active=false;
public:
    explicit Receiver(const gipc_status &) noexcept;
    ~Receiver();
    Receiver(const Receiver&)=delete;Receiver& operator=(const Receiver&)=delete;
    // trusted is constructed from the current GIPC lease and local cartridge.
    bool pump(const ghost_live_session::LocalOwner &trusted,uint64_t now_ns) noexcept;
    void revoke() noexcept;
    HANDLE wake() const noexcept {return event;}
};
uint64_t now_ns() noexcept;
}
