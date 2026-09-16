/* No GPU calls: checked ownership transitions used by the DLL and CPU fault tests. */
#pragma once
#include <stdint.h>
struct GpuEncoderLease {
 bool held=false,pending=false,quarantined=false;
 uint64_t acquired=0,released=0;
 enum Result {ready,timeout,abandoned,waiting,fault};
 Result acquire_result(int32_t hr) {
  if(held||pending||quarantined)return fault;
  if(hr==0x102)return timeout;
  if(hr==0x80){quarantined=true;return abandoned;}
  if(hr!=0){quarantined=true;return fault;}
  held=true;++acquired;return ready;
 }
 bool begin_completion(){if(!held||pending||quarantined)return false;pending=true;return true;}
 template<class Release> Result poll_result(int32_t hr,bool gpu_done,Release release) {
  if(quarantined)return fault;
  if(!pending)return held?fault:ready;
  if(hr==1||(hr==0&&!gpu_done))return waiting;
  if(hr!=0){quarantined=true;return fault;}
  if(release()!=0){quarantined=true;return fault;}
  held=false;pending=false;++released;return ready;
 }
};
