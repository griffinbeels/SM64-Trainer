/* CPU-only delivery fixture. Never compiled into the production runtime. */
#include "gpu_delivery.h"
#include "gpu_delivery_diagnostics.h"
#include "stamp_adapter.h"
#include <atomic>
#include <stdio.h>
namespace {
const rs_api *source=nullptr; HANDLE signal=nullptr;
std::atomic<uint32_t> gate{0}, requests{0}, state{GD_OFF};
unsigned configured=0; uint64_t image_serial=0;
gd_diagnostic_callback diagnostic=nullptr;
}
extern "C" void __cdecl gd_set_diagnostic(gd_diagnostic_callback callback){diagnostic=callback;}
extern "C" int __cdecl TestDiagnostic(){
 if(!diagnostic)return 0;
 gd_diagnostic d{};d.bytes=sizeof d;d.version=GD_DIAGNOSTIC_VERSION;
 d.epoch=gate.load();d.reason=GD_SOURCE_REFUSED;d.current_phase=GD_PHASE_SAMPLE;
 d.observed_qpc=20000000;d.qpc_frequency=10000000;d.source_records=8;
 d.phases[GD_PHASE_SAMPLE]={3,1700000,1500000,18500000};diagnostic(&d);return 1;
}
extern "C" int __cdecl gd_configure(const rs_api*s,const rcl_api*c,const gd_limits*l){
 if(configured||!s||!c||!l||l->max_slots!=8)return 0;
 signal=CreateEventW(nullptr,FALSE,FALSE,nullptr);if(!signal)return 0;
 source=s;configured=1;return 1;
}
extern "C" int __cdecl gd_request(const gc_header*h,uint64_t budget){
 if(!source||!h||h->bridge_count||!budget)return GD_INVALID;
 gd_disarm(GD_CANCELLED);const auto next=source->activate();if(!next)return GD_BAD_SOURCE;
 sa_table table{sizeof(sa_table),SA_VERSION,next,8u<<20,h->table_count,0,{}};
 for(unsigned i=0;i<h->table_count;i++)table.rows[i]={h->table[i].offset,h->table[i].length};
 if(!sa_publish_table(&table)){source->disarm();return GD_INVALID;}
 requests.fetch_add(1);gate.store(next);state.store(GD_ACTIVE);SetEvent(signal);return GD_ACCEPTED;
}
extern "C" void __cdecl gd_disarm(uint32_t){gate.store(0);if(source)source->disarm();state.store(GD_OFF);if(signal)SetEvent(signal);}
extern "C" uint32_t __cdecl gd_epoch(){return gate.load();}
extern "C" void __cdecl gd_surface(const rb_record*){}
extern "C" int __cdecl gd_capture(const rb_record*r,rb_image*i){
 if(!gate.load()||r->epoch!=gate.load())return 0;
 *i={++image_serial,0,123,RB_IMAGE_SUBMITTED,0};return 1;
}
extern "C" int __cdecl gd_completed(const rb_image*){return 1;}
extern "C" HANDLE __cdecl gd_signal(){return signal;}
extern "C" int __cdecl gd_read_status(gd_status*s){*s={};s->state=state.load();s->epoch=gate.load();return 1;}
extern "C" unsigned __cdecl TestGate(){return gate.load();}
extern "C" unsigned __cdecl TestConfigured(){return configured;}
extern "C" unsigned __cdecl TestRequests(){return requests.load();}
extern "C" int __cdecl TestTake(rb_record*out){if(!source)return 0;const auto r=source->take();if(!r)return 0;
 *out=*r;return source->release({r->occurrence,r->slot})?1:-1;}
#pragma comment(linker,"/EXPORT:TestGate=_TestGate")
#pragma comment(linker,"/EXPORT:TestConfigured=_TestConfigured")
#pragma comment(linker,"/EXPORT:TestRequests=_TestRequests")
#pragma comment(linker,"/EXPORT:TestTake=_TestTake")
#pragma comment(linker,"/EXPORT:TestDiagnostic=_TestDiagnostic")
