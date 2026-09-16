#include "runtime_delivery.h"
#include "runtime_control.h"
namespace {
int __cdecl request(const gc_header *h,uint64_t bytes){const int result=gd_request(h,bytes);
 if(result==GD_ACCEPTED||result==GD_UNCHANGED)return RC_ACCEPTED;
 if(result==GD_BUSY)return RC_DEFERRED;
 if(result==GD_INVALID)return RC_INVALID;
 if(result==GD_EXHAUSTED)return RC_EXHAUSTED;
 return result==GD_SYSTEM_ERROR?RC_SYSTEM_ERROR:RC_REJECTED;}
int __cdecl status(uint32_t *state,uint32_t *reason){gd_status observed{};
 if(!gd_read_status(&observed))return 0;
 *reason=observed.reason;
 if(observed.state==GD_BOOTSTRAP||observed.state==GD_PREPARING)*state=CONTROL_PREPARING;
 else if(observed.state==GD_ACTIVE)*state=CONTROL_ACTIVE;
 else {*state=CONTROL_UNAVAILABLE;if(!*reason)*reason=CONTROL_BACKEND_FAILED;}
 return 1;
}
}
extern "C" int __cdecl rc_bind_delivery(const gd_limits *limits){
 if(!limits||limits->bytes!=sizeof(gd_limits)||limits->version!=GD_VERSION||limits->reserved)return 0;
 const rc_backend api{sizeof(rc_backend),RC_VERSION,request,gd_disarm,status,gd_signal};
 return rc_configure(&api,limits->max_slots,limits->snapshot_bytes);
}
