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
    uint32_t state,reason;uint64_t applied_revision;
    uint8_t receipt_room[16];ghost_live_session::LocalOwner receipt_owner;
    uint32_t presentation_reason,eligible_mask,selected_mask,displayed_mask;
    uint64_t updates,refusals,drops,presentations;
    uint32_t gl_major,gl_minor,ineligible_mask;
    uint32_t reserved[23];
};
struct Page {Header header;ghost_live_session::Update request;uint8_t padding[4096-256-1692];};
#pragma pack(pop)
static_assert(sizeof(Header)==256 && sizeof(Page)==4096,"receiver mapping ABI");
static_assert(offsetof(Header,receipt_room)==48 && offsetof(Header,receipt_owner)==64
    && offsetof(Header,presentation_reason)==104 && offsetof(Header,updates)==120
    && offsetof(Header,gl_major)==152 && offsetof(Header,reserved)==164,"receiver V2 consumes reserved header fields only");
class Receiver {
    HANDLE mapping=nullptr,mutex=nullptr,event=nullptr;Page *page=nullptr;
    Header initial{};bool initialized=false;
    bool initialize() noexcept;
    ghost_live_session::LocalOwner prior_owner{};
    uint8_t prior_room[16]{};
    uint64_t offered_revision=0,native_revision=0;bool active=false;
    uint64_t updates=0,refusals=0,drops=0;
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
