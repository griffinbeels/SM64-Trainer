#include "wrapper_runtime.h"
#include "stamp_adapter.h"
#include "runtime_delivery.h"
#include "runtime_control.h"
namespace {
HMODULE renderer=nullptr;bool attempted=false,ready=false;
const rs_api_v3* clean_source=nullptr;
fn_void original_list=nullptr,original_update=nullptr;
uint32_t __cdecl epoch(void*){return gd_epoch();}
void __cdecl surface(void*,const rb_record*r){gd_surface(r);}
int __cdecl capture(void*,const rb_record*r,rb_image*i){return gd_capture(r,i);}
int __cdecl completed(void*,const rb_image*i){return gd_completed(i);}
bool pin(const void *p){HMODULE module=nullptr;
 return GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_PIN|GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,
    reinterpret_cast<LPCWSTR>(p),&module)!=FALSE;}
bool source_valid(const rs_api *s){return s&&s->bytes==sizeof(rs_api)&&s->version==RS_ABI_V2&&!s->reserved
 &&(s->capabilities&(RS_CAP_BOUNDARY|RS_CAP_SOURCE_FORMAT))==(RS_CAP_BOUNDARY|RS_CAP_SOURCE_FORMAT)
 &&s->configure&&s->stage&&s->update&&s->finish&&s->activate&&s->disarm&&s->take&&s->release&&s->stats
 &&s->begin_capture&&s->end_capture;}
bool context_valid(const rcl_api *c){return c&&c->bytes==sizeof(rcl_api)&&c->version==RCL_ABI_V1&&!c->reserved
 &&(c->capabilities&RCL_CAP_NEVER_CURRENT_ANCHOR)&&c->acquire&&c->release&&c->health&&c->error;}
}
extern "C" int __cdecl wr_configure(HMODULE module,const gfx_api_t *original,wr_read_vi vi,wr_read_ram ram){
 if(!module||!original||!original->ProcessDList||!original->UpdateScreen||!vi||!ram)return 0;
 if(attempted){const bool same=ready&&module==renderer&&original->ProcessDList==original_list
    &&original->UpdateScreen==original_update;if(clean_source)clean_source->clean_consumer(same);rc_available(same);return same;}
 using SourceQuery=const rs_api*(__cdecl *)(uint32_t,uint32_t);
 using SourceQuery3=const rs_api_v3*(__cdecl *)(uint32_t,uint32_t);
 using SourceQuery4=const rs_api_v4*(__cdecl *)(uint32_t,uint32_t);
 using ContextQuery=const rcl_api*(__cdecl *)(uint32_t,uint32_t);
 const auto query=reinterpret_cast<SourceQuery>(GetProcAddress(module,"SM64ReplaySourceV2"));
 const auto query3=reinterpret_cast<SourceQuery3>(GetProcAddress(module,"SM64ReplaySourceV3"));
 const auto query4=reinterpret_cast<SourceQuery4>(GetProcAddress(module,"SM64ReplaySourceV4"));
 const auto contexts=reinterpret_cast<ContextQuery>(GetProcAddress(module,"SM64ReplayContextV1"));
 if((!query&&!query3)||!contexts)return 0;
 // Optional diagnostics: an older renderer without it still captures.
 gd_set_context_evidence(reinterpret_cast<rs_read_context_evidence>(GetProcAddress(module,"SM64ReplayContextEvidenceV1")));
 // V4 (the ghost copy's display source) is preferred; V3 renderers still capture clean.
 const auto display=query4?query4(RS_ABI_V4,sizeof(rs_api_v4)):nullptr;
 if(query4&&!rs_valid_v4(display))return 0;
 const auto extended=display?&display->v3:query3?query3(RS_ABI_V3,sizeof(rs_api_v3)):nullptr;
 if(query3&&!display&&!rs_valid_v3(extended))return 0;
 const auto source=extended?&extended->boundary:query(RS_ABI_V2,sizeof(rs_api));const auto context=contexts(RCL_ABI_V1,sizeof(rcl_api));
 if(!source_valid(source)||!context_valid(context))return 0;
 attempted=true;renderer=module;original_list=original->ProcessDList;original_update=original->UpdateScreen;
 // Source+wrapper code remain bounded process-lifetime residents before worker creation.
 const void*queried=query4?reinterpret_cast<const void*>(query4):query3?reinterpret_cast<const void*>(query3):reinterpret_cast<const void*>(query);
 if(!pin(queried)||!pin(reinterpret_cast<const void*>(contexts))
    ||!pin(reinterpret_cast<const void*>(&wr_configure)))return 0;
 const gd_limits limits{sizeof(gd_limits),GD_VERSION,8,2000,
    256ull<<20,256ull<<20,64u<<20,1u<<20,2,0};
 const sa_ops ops{sizeof(sa_ops),SA_VERSION,nullptr,original_list,original_update,epoch,vi,ram,surface,capture,completed};
 if((display?gd_configure4(display,context,&limits):extended?gd_configure3(extended,context,&limits)
    :gd_configure(source,context,&limits))!=GD_ACCEPTED)return 0;
 if(!sa_configure(module,&ops)||!rc_bind_delivery(&limits)){gd_disarm(GD_BAD_SOURCE);return 0;}
 clean_source=extended;if(clean_source)clean_source->clean_consumer(true);
 ready=true;rc_available(TRUE);return 1;
}
extern "C" void __cdecl wr_suspend(void){
 // Revoke the capture epoch before withdrawing the renderer's clean image.
 // An in-flight source callback may finish immediately on withdrawal.
 rc_available(FALSE);if(clean_source)clean_source->clean_consumer(false);
}
