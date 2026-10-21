#include "ghost_live_ipc.h"
#include "ghost_presentation.h"
#include <cstring>
#include <cwchar>

namespace ghost_live_ipc {
namespace {uint64_t revision=0; // same single GIPC worker; survives ROM workers
bool lock(HANDLE h) noexcept {const auto r=h?WaitForSingleObject(h,0):WAIT_FAILED;return r==WAIT_OBJECT_0 || r==WAIT_ABANDONED;}
}
uint64_t now_ns() noexcept {
    LARGE_INTEGER q{},f{};QueryPerformanceCounter(&q);QueryPerformanceFrequency(&f);
    if(q.QuadPart<0 || f.QuadPart<=0)return 0;
    return uint64_t((static_cast<long double>(q.QuadPart)*1000000000.L)/f.QuadPart);
}
Receiver::Receiver(const gipc_status &source) noexcept {
    wchar_t name[160]{};swprintf_s(name,L"Local\\sm64_trainer_live_v1_%lu",GetCurrentProcessId());
    wchar_t suffix[180]{};swprintf_s(suffix,L"%s_mutex",name);mutex=CreateMutexW(nullptr,FALSE,suffix);
    swprintf_s(suffix,L"%s_wake",name);event=CreateEventW(nullptr,FALSE,FALSE,suffix);
    mapping=CreateFileMappingW(INVALID_HANDLE_VALUE,nullptr,PAGE_READWRITE,0,sizeof(Page),name);
    if(mapping)page=static_cast<Page*>(MapViewOfFile(mapping,FILE_MAP_ALL_ACCESS,0,0,sizeof(Page)));
    initial.magic=0x494c4d53;initial.version=1;initial.bytes=sizeof(Page);initial.request_bytes=sizeof(ghost_live_session::Update);
    initial.producer_pid=source.producer_pid;initial.producer_created_lo=source.producer_created_lo;
    initial.producer_created_hi=source.producer_created_hi;initial.generation=source.generation;
    initialize();
}
bool Receiver::initialize() noexcept {
    if(initialized)return true;
    if(!page || !lock(mutex))return false;
    std::memset(page,0,sizeof(*page));page->header=initial;
    initialized=true;ReleaseMutex(mutex);return true;
}
Receiver::~Receiver() {
    revoke();if(page)UnmapViewOfFile(page);
    if(mapping)CloseHandle(mapping);if(mutex)CloseHandle(mutex);if(event)CloseHandle(event);
}
void Receiver::revoke() noexcept {
    if(active)ghost_live_session::worker_suspend();
    ghost_presentation::live_consumer(false);active=false;offered_revision=0;prior_owner={};
    std::memset(prior_room,0,sizeof(prior_room));
}
bool Receiver::pump(const ghost_live_session::LocalOwner &trusted,uint64_t now) noexcept {
    if(!initialize())return false; // an old client may still hold the mutex during ROM replacement
    if(!trusted.token || !trusted.configuration) {revoke();return false;}
    if(std::memcmp(&prior_owner,&trusted,sizeof(trusted))) {
        if(prior_owner.pid && (prior_owner.pid!=trusted.pid || prior_owner.created_lo!=trusted.created_lo
                || prior_owner.created_hi!=trusted.created_hi))ghost_live_session::worker_revoke();
        revoke();prior_owner=trusted;
    }
    ghost_live_session::Update value{};
    if(!page || !lock(mutex))return active;
    value=page->request;ReleaseMutex(mutex);
    auto expected=trusted;expected.model_id=0;
    // A late close/write from an old source capability cannot retire its replacement.
    if(std::memcmp(&value.owner,&expected,sizeof(expected)))return active;
    if(std::memcmp(prior_room,value.room,sizeof(prior_room))) {
        std::memcpy(prior_room,value.room,sizeof(prior_room));offered_revision=0;
    }
    if(!value.revision || value.revision<offered_revision)return active;
    if(value.revision!=offered_revision) {
        if(revision>=UINT64_MAX-1) {revoke();return false;}
        native_revision=++revision;offered_revision=value.revision;
    }
    value.owner=trusted;value.revision=native_revision;
    const auto result=ghost_live_session::worker_update(value,now);
    active=result==GP_OK && value.enabled;
    bool members=false;for(const auto &peer:value.peers)members|=peer.member!=0;
    ghost_presentation::live_consumer(active && members);
    if(lock(mutex)) {
        page->header.state=active?1:0;page->header.reason=result<0?uint32_t(-result):0;
        page->header.applied_revision=offered_revision;ReleaseMutex(mutex);
    }
    return active && members;
}
}
