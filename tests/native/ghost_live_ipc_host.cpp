/* Actual Win32 receiver page; facade instrumented to isolate this trust boundary. */
#define NOMINMAX
#include "ghost_live_ipc.h"
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <thread>
namespace {unsigned checks=0,updates=0,suspends=0,revokes=0;bool display=false;int result=GP_OK;
ghost_live_session::Update seen{};
void check(bool ok,const char *why) {++checks;if(!ok)throw std::runtime_error(why);}
}
namespace ghost_live_session {
int worker_update(const Update &value,uint64_t) noexcept {++updates;seen=value;return result;}
PresentationStatus presentation_status(uint64_t) noexcept {return {PresentationReason::ineligible,7,3,0,0,4,3,2};}
uint64_t worker_refusals() noexcept {return 0;}
void worker_suspend() noexcept {++suspends;}
void worker_revoke() noexcept {++revokes;}
}
namespace ghost_presentation {void live_consumer(bool value){display=value;}}
int main() {
    try {
        gipc_status clock{};clock.producer_pid=GetCurrentProcessId();clock.generation=19;
        FILETIME birth{},unused[3]{};check(GetProcessTimes(GetCurrentProcess(),&birth,unused,unused+1,unused+2)!=0,"process birth");
        clock.producer_created_lo=birth.dwLowDateTime;clock.producer_created_hi=birth.dwHighDateTime;
        ghost_live_ipc::Receiver receiver(clock);
        wchar_t name[160]{};swprintf_s(name,L"Local\\sm64_trainer_live_v1_%lu",GetCurrentProcessId());
        HANDLE mapping=OpenFileMappingW(FILE_MAP_ALL_ACCESS,FALSE,name);check(mapping!=nullptr,"receiver mapping");
        auto *page=static_cast<ghost_live_ipc::Page*>(MapViewOfFile(mapping,FILE_MAP_ALL_ACCESS,0,0,4096));check(page!=nullptr,"receiver view");
        check(page->header.magic==0x494c4d53 && page->header.version==2 && page->header.bytes==4096
            && page->header.request_bytes==1692 && page->header.generation==19,"fixed ABI header");
        ghost_live_session::LocalOwner owner{clock.producer_pid,birth.dwLowDateTime,birth.dwHighDateTime,19,3,2,123,1};
        auto &value=page->request;value.owner=owner;value.owner.model_id=0;
        value.revision=4;value.enabled=1;value.room[0]=1;
        check(!receiver.pump(owner,100) && updates==1 && !display,"empty room does not warm renderer");
        check(seen.owner.model_id==1 && seen.owner.token==123,"model comes only from local owner");
        value.peers[0].member=1;value.peers[0].incarnation[0]=1;value.revision=5;
        check(receiver.pump(owner,101) && display,"current local owner permits receiver");
        check(page->header.state==1 && page->header.applied_revision==5 && page->header.receipt_room[0]==1
            && page->header.receipt_owner.token==123 && page->header.receipt_owner.model_id==0
            && page->header.presentation_reason==uint32_t(ghost_live_session::PresentationReason::ineligible)
            && !page->header.displayed_mask && page->header.gl_major==4 && page->header.gl_minor==3
            && page->header.ineligible_mask==2,"receipt scopes accepted control and fixed FXAA cause without claiming display");
        const auto revision=seen.revision;
        value.peers[0].received_ns=99;value.peers[0].present=1;
        check(receiver.pump(owner,102) && seen.revision==revision && seen.peers[0].received_ns==99,"receipt retained without control revision churn");
        result=GP_BUSY;check(receiver.pump(owner,102) && display && page->header.state==0 && page->header.drops==1,
            "bounded publish miss remains pending with display demand retained");
        result=GP_INVALID;check(!receiver.pump(owner,102) && !display && page->header.state==2 && page->header.refusals==1,
            "permanent authority refusal is distinct from pending");
        result=GP_OK;check(receiver.pump(owner,102) && display,"facade resumes before subsequent owner probes");
        const auto count=updates;value.owner.token=122;
        check(receiver.pump(owner,103) && updates==count && display,"late old capability cannot cancel current owner");
        owner.token=124;owner.configuration=3;
        check(!receiver.pump(owner,104) && !display && suspends>0,"local replacement clears display before rebind");
        value.owner=owner;value.owner.model_id=0;value.revision=1;
        check(receiver.pump(owner,105) && seen.revision>revision,"new local lease rebases control revision");
        value.enabled=0;value.revision=2;
        check(!receiver.pump(owner,106) && !display && !seen.enabled,"explicit leave reaches facade");
        value.room[0]=2;value.revision=1;value.enabled=1;
        check(receiver.pump(owner,106) && display,"new room gets its own client revision sequence");
        owner.pid+=1;value.owner=owner;value.owner.model_id=0;value.revision=1;value.enabled=1;
        check(receiver.pump(owner,107) && revokes==1,"different app birth permanently retires prior membership");
        check(!receiver.pump({},108) && !display,"missing local source withdraws display");
        wchar_t mutex_name[180]{};swprintf_s(mutex_name,L"%s_mutex",name);
        HANDLE mutex=OpenMutexW(SYNCHRONIZE|MUTEX_MODIFY_STATE,FALSE,mutex_name);
        HANDLE held=CreateEventW(nullptr,TRUE,FALSE,nullptr),release=CreateEventW(nullptr,TRUE,FALSE,nullptr);
        check(mutex && held && release,"replacement barrier handles");
        std::thread old_client([&] {WaitForSingleObject(mutex,INFINITE);SetEvent(held);WaitForSingleObject(release,INFINITE);ReleaseMutex(mutex);});
        WaitForSingleObject(held,INFINITE);clock.generation=20;
        ghost_live_ipc::Receiver replacement(clock);
        check(page->header.generation==19,"busy old client blocks initial page replacement");
        SetEvent(release);old_client.join();replacement.pump({},109);
        check(page->header.generation==20,"worker retries header initialization after old client releases mutex");
        CloseHandle(mutex);CloseHandle(held);CloseHandle(release);
        UnmapViewOfFile(page);CloseHandle(mapping);
        std::printf("{\"ok\":true,\"checks\":%u,\"header\":%zu,\"update\":%zu,\"offsets\":[%zu,%zu,%zu,%zu,%zu]}\n",checks,
            sizeof(ghost_live_ipc::Header),sizeof(ghost_live_session::Update),offsetof(ghost_live_ipc::Header,receipt_room),
            offsetof(ghost_live_ipc::Header,receipt_owner),offsetof(ghost_live_ipc::Header,presentation_reason),
            offsetof(ghost_live_ipc::Header,updates),offsetof(ghost_live_ipc::Header,reserved));return 0;
    }catch(const std::exception &error){std::fprintf(stderr,"%s\n",error.what());return 1;}
}
