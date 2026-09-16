#include "runtime_control.h"
#include "gpu_request.h"
#include <atomic>
namespace {
rc_backend backend{};unsigned maximum_slots=0;uint64_t maximum_snapshot=0;HANDLE event=nullptr;
bool attempted=false;std::atomic<bool> configured{false},available{false},rom{false},live{false},exhausted{false};
std::atomic<uint32_t> cancellation{1};
gr_identity last{};uint32_t admitted_serial=0,last_reason=CONTROL_FRESH_REQUEST,last_state=CONTROL_PREPARING; // one supervisor
gc_header pending_request{};uint64_t pending_budget=0;bool pending_admission=false;
bool pin(const void *address){HMODULE module=nullptr;
 return GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_PIN|GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,
     reinterpret_cast<LPCWSTR>(address),&module)!=FALSE;}
void cancel(uint32_t reason){
 if(exhausted.load())return;
 if(cancellation.fetch_add(1)>=INT32_MAX-1u)exhausted.store(true);
 if(configured.load())backend.disarm(reason);
 live.store(false);
}
bool same(const gr_identity &a,const gr_identity &b){return a.producer_pid==b.producer_pid
 && a.owner_pid==b.owner_pid && a.control_generation==b.control_generation && a.token==b.token
 && a.producer_birth==b.producer_birth && a.owner_birth==b.owner_birth;}
}
extern "C" int __cdecl rc_configure(const rc_backend *api,unsigned slots,uint64_t bytes){
 if(attempted || !api || api->bytes!=sizeof(rc_backend)||api->version!=RC_VERSION
     || !api->request||!api->disarm||!api->status||!api->signal||!slots||slots>8||!bytes)return 0;
 attempted=true;
 const void *addresses[]={reinterpret_cast<const void*>(api->request),reinterpret_cast<const void*>(api->disarm),
    reinterpret_cast<const void*>(api->status),reinterpret_cast<const void*>(api->signal)};
 for(auto address:addresses)if(!pin(address))return 0;
 HANDLE handle=api->signal();if(!handle)return 0;
 backend=*api;maximum_slots=slots;maximum_snapshot=bytes;event=handle;configured.store(true);available.store(true);return 1;
}
extern "C" uint32_t __cdecl rc_capabilities(void){return configured.load()&&available.load()?CONTROL_CAP_GPU:0;}
extern "C" void __cdecl rc_available(int supported){available.store(supported!=0);if(!supported)cancel(CONTROL_NO_BACKEND);}
extern "C" HANDLE __cdecl rc_signal(void){return configured.load()?event:nullptr;}
extern "C" void __cdecl rc_rom(int opened){rom.store(opened!=0);if(!opened)cancel(CONTROL_CLOSED);}
extern "C" void __cdecl rc_revoke(uint32_t reason){if(live.exchange(false))cancel(reason);}
extern "C" void __cdecl rc_demand(const control_page_t *page,const control_request_t *request,
                                  uint32_t *state,uint32_t *reason){
 if(!state||!reason)return;*state=CONTROL_UNAVAILABLE;*reason=CONTROL_NO_BACKEND;
 if(!configured.load()||!available.load()||!page||!request)return;
 if(!rom.load()){*state=CONTROL_PASSIVE;*reason=0;return;}
 if(exhausted.load()){*reason=CONTROL_BACKEND_FAILED;return;}
 const gr_identity identity{page->producer_pid,request->owner_pid,page->generation,request->token,
    (uint64_t(page->producer_created_hi)<<32)|page->producer_created_lo,
    (uint64_t(request->created_hi)<<32)|request->created_lo};
 const auto before=cancellation.load();
 if(!same(identity,last)){
    rc_revoke(CONTROL_FRESH_REQUEST);last=identity;last_reason=CONTROL_BAD_GPU_REQUEST;pending_admission=false;
    const auto activation=cancellation.load();
    gr_page config{};gc_header partial{};
    if(!gr_read(identity,maximum_slots,maximum_snapshot,&config)||!gr_channel(config,&partial)){
        *reason=last_reason;return;}
    if(!rom.load()||!available.load()||exhausted.load()||cancellation.load()!=activation){*reason=CONTROL_FRESH_REQUEST;return;}
    pending_request=partial;pending_budget=config.snapshot_budget;pending_admission=true;
    admitted_serial=activation;last_reason=0;last_state=CONTROL_PREPARING;live.store(true);
 }else if(!live.load() || admitted_serial!=before){
    *reason=last_reason?last_reason:CONTROL_FRESH_REQUEST;return;
 }
 if(pending_admission){
    const auto activation=admitted_serial;
    const int result=backend.request(&pending_request,pending_budget);
    if(cancellation.load()!=activation || !rom.load() || !available.load()){
        cancel(CONTROL_FRESH_REQUEST);last_reason=CONTROL_FRESH_REQUEST;pending_admission=false;*reason=last_reason;return;}
    if(result==RC_DEFERRED){*state=CONTROL_PREPARING;*reason=0;return;}
    pending_admission=false;
    if(result!=RC_ACCEPTED){
        last_reason=result==RC_INVALID?CONTROL_BACKEND_INVALID:
            result==RC_EXHAUSTED?CONTROL_BACKEND_EXHAUSTED:
            result==RC_SYSTEM_ERROR?CONTROL_BACKEND_SYSTEM_ERROR:CONTROL_BACKEND_FAILED;
        cancel(last_reason);*reason=last_reason;return;}
 }
 uint32_t observed=last_state,error=0;
 const int fresh=backend.status(&observed,&error);
 if(fresh && ((observed!=CONTROL_ACTIVE&&observed!=CONTROL_PREPARING) || error)){
    last_reason=error?error:CONTROL_BACKEND_FAILED;rc_revoke(last_reason);*reason=last_reason;return;}
 if(!rom.load() || !available.load() || admitted_serial!=cancellation.load()){
    rc_revoke(CONTROL_FRESH_REQUEST);*reason=CONTROL_FRESH_REQUEST;return;}
 if(fresh)last_state=observed;
 *state=last_state;*reason=0; // A busy bounded status snapshot is a deferred read.
}
