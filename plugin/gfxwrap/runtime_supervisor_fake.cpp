/* A blocked independent CPU worker stands in for a driver call. No graphics. */
#include "runtime_delivery.h"
#include "runtime_control.h"
#include <atomic>
#include <cstdio>
#include <cstring>
namespace {
HANDLE changed=nullptr,work=nullptr,release_work=nullptr,release_request=nullptr;
std::atomic<unsigned> state{GD_OFF},reason{0},gate{0},serial{1},requests{0},revokes{0};
std::atomic<bool> block{false},inside{false};
std::atomic<bool> busy_status{false},pause_request{false},inside_request{false};
std::atomic<unsigned> status_reads{0};
DWORD WINAPI worker(void*){
 for(;;){if(WaitForSingleObject(work,INFINITE)!=WAIT_OBJECT_0)return 1;
  const auto before=serial.load();if(!gate.load())continue;
  state.store(GD_PREPARING);SetEvent(changed);
  if(block.load()){inside.store(true);WaitForSingleObject(release_work,10000);inside.store(false);}
  if(serial.load()==before&&gate.load()){state.store(GD_ACTIVE);reason.store(0);SetEvent(changed);}
 }
}
}
extern "C" HANDLE __cdecl gd_signal(){return changed;}
extern "C" int __cdecl gd_request(const gc_header*h,uint64_t budget){
 if(!h||h->bridge_count||h->producer_pid||h->epoch||h->width||h->height||budget!=(128ull<<20))return GD_INVALID;
 if(pause_request.load()){inside_request.store(true);WaitForSingleObject(release_request,10000);inside_request.store(false);}
 ++requests;gate.store(serial.load());reason.store(0);state.store(GD_BOOTSTRAP);
 SetEvent(work);SetEvent(changed);return GD_ACCEPTED;
}
extern "C" void __cdecl gd_disarm(uint32_t value){
 ++revokes;gate.store(0);++serial;state.store(GD_OFF);reason.store(value);SetEvent(changed);
}
extern "C" int __cdecl gd_read_status(gd_status*out){
 ++status_reads;if(busy_status.exchange(false))return 0;
 if(!out)return 0;*out={};out->state=state.load();out->reason=reason.load();out->epoch=gate.load();return 1;
}
extern "C" int __cdecl fake_configure(){
 changed=CreateEventW(nullptr,FALSE,FALSE,nullptr);work=CreateEventW(nullptr,FALSE,FALSE,nullptr);
 release_work=CreateEventW(nullptr,FALSE,FALSE,nullptr);release_request=CreateEventW(nullptr,FALSE,FALSE,nullptr);
 if(!changed||!work||!release_work||!release_request)return 0;
 const gd_limits limits{sizeof(gd_limits),GD_VERSION,8,2000,128ull<<20,128ull<<20,16u<<20,1u<<20,5,0};
 if(!rc_bind_delivery(&limits))return 0;
 HANDLE thread=CreateThread(nullptr,0,worker,nullptr,0,nullptr);if(!thread)return 0;CloseHandle(thread);return 1;
}
extern "C" void __cdecl fake_command(const char *line){
 if(!strncmp(line,"block",5)){block.store(true);puts("ok");}
 else if(!strncmp(line,"pause-request",13)){pause_request.store(true);puts("ok");}
 else if(!strncmp(line,"resume-request",14)){pause_request.store(false);SetEvent(release_request);puts("ok");}
 else if(!strncmp(line,"inside-request",14))printf("%u\n",inside_request.load()?1u:0u);
 else if(!strncmp(line,"busy-status",11)){busy_status.store(true);SetEvent(changed);puts("ok");}
 else if(!strncmp(line,"reads",5))printf("%u\n",status_reads.load());
 else if(!strncmp(line,"unblock",7)){block.store(false);SetEvent(release_work);puts("ok");}
 else if(!strncmp(line,"fault",5)){state.store(GD_FAULT);reason.store(GD_SAMPLE_FAILED);SetEvent(changed);puts("ok");}
 else if(!strncmp(line,"stats",5))printf("%u %u %u %u\n",requests.load(),gate.load(),state.load(),inside.load()?1u:0u);
 else puts("bad");fflush(stdout);
}
