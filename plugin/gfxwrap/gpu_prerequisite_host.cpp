/* CPU-only ControlV1 receipt witness. A failed producer need not have created
 * a picture channel: the existing bound backend still reports its exact cause. */
#include "runtime_delivery.h"
#include "runtime_control.h"
#include "gpu_render.h"
#include <stdio.h>

namespace { gd_status observed{}; rc_backend bound{}; }
extern "C" int __cdecl gd_read_status(gd_status *out){*out=observed;return 1;}
extern "C" int __cdecl gd_request(const gc_header*,uint64_t){return GD_ACCEPTED;}
extern "C" void __cdecl gd_disarm(uint32_t){}
extern "C" HANDLE __cdecl gd_signal(){return nullptr;}
extern "C" int __cdecl rc_configure(const rc_backend *backend,unsigned,uint64_t){bound=*backend;return 1;}
int main(){
 gd_limits limits{};limits.bytes=sizeof limits;limits.version=GD_VERSION;
 if(!rc_bind_delivery(&limits)||!bound.status)return 1;
 observed.state=GD_FAULT;observed.reason=GD_GL_UNSUPPORTED;
 for(uint32_t detail=0;detail<=7;++detail){
  observed.error=detail;uint32_t state=0,reason=0;
  if(!bound.status(&state,&reason)||state!=CONTROL_UNAVAILABLE)return 1;
  printf("prerequisite %u %u\n",detail,reason);
 }
 observed.reason=GD_SNAPSHOT_FAILED;observed.error=2;
 uint32_t state=0,reason=0;if(!bound.status(&state,&reason)||reason!=GD_SNAPSHOT_FAILED)return 1;
 printf("other %u\n",reason);return 0;
}
