#define GBDLL_BUILD
#include "gpu_encoder_dll.h"
#include "gpu_bridge_encoder.h"
#include "gpu_encoder_lease.h"
#include <new>
#include <cstring>
static_assert(sizeof(void*)==8,"x64 worker DLL only");
static_assert(sizeof(gbdll_config_v1)==688&&sizeof(gbdll_picture_v1)==40&&sizeof(gbdll_status_v1)==104,"C ABI sizes");
static_assert(sizeof(gbdll_retained_abi_v1)==16&&sizeof(gbdll_retained_v1)==56&&sizeof(gbdll_repeat_v1)==48,"Retained extension C ABI sizes");
namespace {
SRWLOCK gate=SRWLOCK_INIT;
struct Guard {bool held=TryAcquireSRWLockExclusive(&gate)!=FALSE;~Guard(){if(held)ReleaseSRWLockExclusive(&gate);}};
struct Slot {
 Microsoft::WRL::ComPtr<ID3D11Texture2D> texture;
 Microsoft::WRL::ComPtr<IDXGIKeyedMutex> mutex;
 Microsoft::WRL::ComPtr<ID3D11Query> completion;
 GpuEncoderLease lease;
};
struct Session {
 uint64_t token=0;DWORD owner=0;LUID adapter{};gbdll_state state=GBDLL_OPEN;
 uint32_t failure=GBDLL_OK;HRESULT hr=S_OK;uint64_t timeouts=0,last_pts=0;bool have_pts=false;
 Microsoft::WRL::ComPtr<ID3D11Device> device;Microsoft::WRL::ComPtr<ID3D11DeviceContext> context;
 Slot slots[GBDLL_SLOTS];BridgeEncoder encoder;
};
Session*session=nullptr;uint64_t next_token=1;bool poisoned=false;
uint32_t quarantine(Session&s,uint32_t failure,HRESULT hr) {
 s.encoder.invalidate_retained();
 if(!s.failure)s.failure=failure;if(hr!=S_OK)s.hr=hr;s.state=GBDLL_QUARANTINED;poisoned=true;
 // Pin this module; retained quarantined C++ objects are never destructed in DllMain.
 HMODULE module=nullptr;GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_PIN,
     reinterpret_cast<LPCWSTR>(&SM64GpuEncoderOpenV1),&module);
 return GBDLL_QUARANTINE;
}
uint32_t lookup(uint64_t token,Session**out) {
 if(!session||!token||session->token!=token)return GBDLL_HANDLE;
 if(session->owner!=GetCurrentThreadId())return GBDLL_THREAD;
 *out=session;return GBDLL_OK;
}
bool status_valid(const gbdll_status_v1*p){return p&&p->struct_size==sizeof(*p)&&p->version==GBDLL_VERSION;}
void status(Session&s,gbdll_status_v1*out) {
 *out={};out->struct_size=sizeof(*out);out->version=GBDLL_VERSION;
 out->state=s.state;out->failure=s.failure;out->hresult=uint32_t(s.hr);
 out->encoder_state=s.encoder.state();out->encoder_error=s.encoder.error();out->nv_status=s.encoder.driver_error();
 out->owner_thread=s.owner;out->adapter_high=s.adapter.HighPart;out->adapter_low=s.adapter.LowPart;
 out->submitted=s.encoder.submitted();out->completed=s.encoder.completed();out->delivered=s.encoder.delivered();out->timeouts=s.timeouts;
 for(unsigned i=0;i<GBDLL_SLOTS;++i){const auto&l=s.slots[i].lease;if(l.held)out->held_mask|=1u<<i;if(l.pending)out->pending_mask|=1u<<i;out->acquired+=l.acquired;out->released+=l.released;}
}
bool device_for_luid(LUID luid,ID3D11Device**device,ID3D11DeviceContext**context) {
 // Same exact-LUID import pattern as gpu_bridge::device_for_luid; no GL/WGL linkage.
 Microsoft::WRL::ComPtr<IDXGIFactory1> factory;if(FAILED(CreateDXGIFactory1(IID_PPV_ARGS(&factory))))return false;
 for(unsigned i=0;i<32;++i){Microsoft::WRL::ComPtr<IDXGIAdapter1> adapter;if(factory->EnumAdapters1(i,&adapter)!=S_OK)break;
  DXGI_ADAPTER_DESC1 desc{};if(FAILED(adapter->GetDesc1(&desc))||(desc.Flags&DXGI_ADAPTER_FLAG_SOFTWARE)||memcmp(&desc.AdapterLuid,&luid,sizeof(luid)))continue;
  return SUCCEEDED(D3D11CreateDevice(adapter.Get(),D3D_DRIVER_TYPE_UNKNOWN,nullptr,D3D11_CREATE_DEVICE_BGRA_SUPPORT,nullptr,0,D3D11_SDK_VERSION,device,nullptr,context));
 }return false;
}
uint32_t poll(Session&s) {
 if(s.state==GBDLL_QUARANTINED)return GBDLL_QUARANTINE;
 bool pending=false;
 for(auto&slot:s.slots){if(!slot.lease.pending)continue;BOOL done=FALSE;
  // Permit command-buffer progress on this worker. DONOTFLUSH required256
  // polls in the measured runtime despite End+Flush, independent of poll spacing.
  // One query per pending slot per API call; no spin/sleep or renderer work.
  const HRESULT hr=s.context->GetData(slot.completion.Get(),&done,sizeof(done),0);
  HRESULT released=S_OK;
  const auto result=slot.lease.poll_result(hr,done!=FALSE,[&]{released=slot.mutex->ReleaseSync(0);return int32_t(released);});
  if(result==GpuEncoderLease::fault)return quarantine(s,GBDLL_QUARANTINE,(hr!=S_OK&&hr!=S_FALSE)?hr:released);
  pending|=result==GpuEncoderLease::waiting;
 }
 return pending?GBDLL_PENDING:GBDLL_OK;
}
uint32_t finish(Session&s) {
 if(s.state==GBDLL_QUARANTINED)return GBDLL_QUARANTINE;
 if(s.state==GBDLL_FINISHED)return GBDLL_OK;
 s.encoder.invalidate_retained();s.state=GBDLL_FINISHING;
 const auto result=poll(s);if(result!=GBDLL_OK)return result;
 if(!s.encoder.close())return quarantine(s,GBDLL_ENCODER,E_FAIL);
 s.state=GBDLL_FINISHED;return GBDLL_OK;
}
uint32_t abandon_open(Session*s,uint32_t result){delete s;session=nullptr;return result;}
}
extern "C" uint32_t GBENC_CALL SM64GpuEncoderAbiV1(gbdll_abi_v1*out){
 if(!out)return GBDLL_ARGUMENT;if(out->struct_size!=sizeof(*out)||out->version!=GBDLL_VERSION)return GBDLL_ABI;
 *out={sizeof(*out),GBDLL_VERSION,64,sizeof(gbdll_config_v1),sizeof(gbdll_picture_v1),sizeof(gbdll_status_v1),sizeof(gbenc_options_v1),sizeof(gbenc_packet_v1),sizeof(gbenc_sink_v1),0};return GBDLL_OK;
}
extern "C" uint32_t GBENC_CALL SM64GpuEncoderOpenV1(const gbdll_config_v1*config,uint64_t*token){
 if(!token)return GBDLL_ARGUMENT;*token=0;Guard lock;if(!lock.held)return GBDLL_BUSY;
 if(poisoned)return GBDLL_QUARANTINE;if(session)return GBDLL_BUSY;
 if(!config)return GBDLL_ARGUMENT;
 if(config->struct_size!=sizeof(*config)||config->version!=GBDLL_VERSION||config->encoder.struct_size!=sizeof(config->encoder)||config->encoder.version!=GBENC_ABI_V1||config->sink.struct_size!=sizeof(config->sink)||config->sink.version!=GBENC_ABI_V1)return GBDLL_ABI;
 if(config->slot_count!=GBDLL_SLOTS||config->reserved||!config->sink.on_packet||next_token==UINT64_MAX)return GBDLL_ARGUMENT;
 for(const auto&name:config->names){bool terminator=false;for(uint16_t c:name)if(!c){terminator=true;break;}if(!name[0]||!terminator)return GBDLL_ARGUMENT;}
 if(!wcscmp(reinterpret_cast<const wchar_t*>(config->names[0]),reinterpret_cast<const wchar_t*>(config->names[1])))return GBDLL_ARGUMENT;
 Session*s=new(std::nothrow)Session;if(!s)return GBDLL_MEMORY;
 session=s;s->owner=GetCurrentThreadId();s->token=next_token++;s->adapter={config->adapter_low,config->adapter_high};
 try {
  if(!device_for_luid(s->adapter,&s->device,&s->context))return abandon_open(s,GBDLL_ADAPTER);
  Microsoft::WRL::ComPtr<ID3D11Device1> device1;if(FAILED(s->device.As(&device1)))return abandon_open(s,GBDLL_IMPORT);
  const DXGI_FORMAT format=config->encoder.input_format==GBENC_INPUT_RGBA8?DXGI_FORMAT_R8G8B8A8_UNORM:config->encoder.input_format==GBENC_INPUT_BGRA8?DXGI_FORMAT_B8G8R8A8_UNORM:DXGI_FORMAT_UNKNOWN;
  if(format==DXGI_FORMAT_UNKNOWN)return abandon_open(s,GBDLL_FORMAT);
  for(unsigned i=0;i<GBDLL_SLOTS;++i){auto&slot=s->slots[i];
   if(FAILED(device1->OpenSharedResourceByName(reinterpret_cast<const wchar_t*>(config->names[i]),DXGI_SHARED_RESOURCE_READ|DXGI_SHARED_RESOURCE_WRITE,IID_PPV_ARGS(&slot.texture))))return abandon_open(s,GBDLL_IMPORT);
   D3D11_TEXTURE2D_DESC desc{};slot.texture->GetDesc(&desc);
   const UINT sharing=D3D11_RESOURCE_MISC_SHARED_NTHANDLE|D3D11_RESOURCE_MISC_SHARED_KEYEDMUTEX;
   if(desc.Width!=config->encoder.width||desc.Height!=config->encoder.height||desc.Format!=format||desc.MipLevels!=1||desc.ArraySize!=1||desc.SampleDesc.Count!=1||desc.SampleDesc.Quality!=0||desc.Usage!=D3D11_USAGE_DEFAULT||desc.CPUAccessFlags||(desc.MiscFlags&sharing)!=sharing)return abandon_open(s,GBDLL_FORMAT);
   if(FAILED(slot.texture.As(&slot.mutex)))return abandon_open(s,GBDLL_IMPORT);
   D3D11_QUERY_DESC query{D3D11_QUERY_EVENT,0};if(FAILED(s->device->CreateQuery(&query,&slot.completion)))return abandon_open(s,GBDLL_MEMORY);
  }
  if(!s->encoder.prepare(s->device.Get(),s->context.Get(),config->encoder,config->sink)){
   if(s->encoder.state()==GBENC_QUARANTINED){*token=s->token;return quarantine(*s,GBDLL_ENCODER,E_FAIL);}return abandon_open(s,GBDLL_ENCODER);
  }
  *token=s->token;return GBDLL_OK;
 }catch(...){*token=s->token;return quarantine(*s,GBDLL_MEMORY,E_FAIL);}
}
extern "C" uint32_t GBENC_CALL SM64GpuEncoderSubmitV1(uint64_t token,const gbdll_picture_v1*p){
 Guard lock;if(!lock.held)return GBDLL_BUSY;Session*s=nullptr;const auto found=lookup(token,&s);if(found)return found;
 if(s->state==GBDLL_QUARANTINED)return GBDLL_QUARANTINE;if(s->state!=GBDLL_OPEN)return GBDLL_STATE;
 if(!p)return GBDLL_ARGUMENT;if(p->struct_size!=sizeof(*p)||p->version!=GBDLL_VERSION)return GBDLL_ABI;
 if(p->slot>=GBDLL_SLOTS||(p->flags&~GBDLL_FORCE_IDR)||!p->duration||p->pts>UINT64_MAX-p->duration||(s->have_pts&&p->pts<=s->last_pts))return GBDLL_ARGUMENT;
 auto&slot=s->slots[p->slot];if(slot.lease.held||slot.lease.pending)return GBDLL_PENDING;
 const HRESULT acquired=slot.mutex->AcquireSync(1,0);
 const auto ownership=slot.lease.acquire_result(acquired);
 if(ownership==GpuEncoderLease::timeout){++s->timeouts;return GBDLL_TIMEOUT;}
 if(ownership==GpuEncoderLease::abandoned){quarantine(*s,GBDLL_ABANDONED,acquired);return GBDLL_ABANDONED;}
 if(ownership!=GpuEncoderLease::ready)return quarantine(*s,GBDLL_QUARANTINE,acquired);
 try {
  const bool accepted=s->encoder.encode(slot.texture.Get(),p->occurrence,p->pts,p->duration,(p->flags&GBDLL_FORCE_IDR)!=0);
  if(s->encoder.state()==GBENC_QUARANTINED)return quarantine(*s,GBDLL_ENCODER,E_FAIL);
  // The source CopyResource precedes this event on THIS immediate context even
  // when NVENC mapping failed. No key return is inferred from encoder closure.
  s->context->End(slot.completion.Get());s->context->Flush();
  if(!slot.lease.begin_completion())return quarantine(*s,GBDLL_QUARANTINE,E_FAIL);
  s->last_pts=p->pts;s->have_pts=true;
  if(!accepted){s->state=GBDLL_FAILED;s->failure=GBDLL_ENCODER;}
  const auto progress=poll(*s);if(progress==GBDLL_QUARANTINE)return progress;
  return accepted?GBDLL_OK:GBDLL_ENCODER;
 }catch(...){return quarantine(*s,GBDLL_MEMORY,E_FAIL);}
}
extern "C" uint32_t GBENC_CALL SM64GpuEncoderPollV1(uint64_t token){Guard lock;if(!lock.held)return GBDLL_BUSY;Session*s=nullptr;const auto r=lookup(token,&s);return r?r:poll(*s);}
extern "C" uint32_t GBENC_CALL SM64GpuEncoderFinishV1(uint64_t token){Guard lock;if(!lock.held)return GBDLL_BUSY;Session*s=nullptr;const auto r=lookup(token,&s);return r?r:finish(*s);}
extern "C" uint32_t GBENC_CALL SM64GpuEncoderCloseV1(uint64_t token,gbdll_status_v1*out){
 Guard lock;if(!lock.held)return GBDLL_BUSY;Session*s=nullptr;const auto r=lookup(token,&s);if(r)return r;
 if(!status_valid(out))return out?GBDLL_ABI:GBDLL_ARGUMENT;
 const auto result=finish(*s);status(*s,out);if(result==GBDLL_OK){delete s;session=nullptr;}return result;
}
extern "C" uint32_t GBENC_CALL SM64GpuEncoderStatusV1(uint64_t token,gbdll_status_v1*out){
 Guard lock;if(!lock.held)return GBDLL_BUSY;Session*s=nullptr;const auto r=lookup(token,&s);if(r)return r;
 if(!status_valid(out))return out?GBDLL_ABI:GBDLL_ARGUMENT;status(*s,out);return GBDLL_OK;
}

extern "C" uint32_t GBENC_CALL SM64GpuEncoderRetainedAbiV1(gbdll_retained_abi_v1*out){
 if(!out)return GBDLL_ARGUMENT;if(out->struct_size!=sizeof(*out)||out->version!=GBDLL_RETAINED_VERSION)return GBDLL_ABI;
 *out={sizeof(*out),GBDLL_RETAINED_VERSION,sizeof(gbdll_retained_v1),sizeof(gbdll_repeat_v1)};return GBDLL_OK;
}
extern "C" uint32_t GBENC_CALL SM64GpuEncoderRetainedV1(uint64_t token,gbdll_retained_v1*out){
 Guard lock;if(!lock.held)return GBDLL_BUSY;Session*s=nullptr;const auto r=lookup(token,&s);if(r)return r;
 if(!out)return GBDLL_ARGUMENT;if(out->struct_size!=sizeof(*out)||out->version!=GBDLL_RETAINED_VERSION)return GBDLL_ABI;
 const auto&e=s->encoder;*out={sizeof(*out),GBDLL_RETAINED_VERSION,(s->state==GBDLL_OPEN&&e.retained_valid())?1u:0u,0,
 e.retained_generation(),e.retained_serial(),e.source_copies(),e.repeats(),e.last_serial()};return GBDLL_OK;
}
extern "C" uint32_t GBENC_CALL SM64GpuEncoderRepeatV1(uint64_t token,const gbdll_repeat_v1*p){
 Guard lock;if(!lock.held)return GBDLL_BUSY;Session*s=nullptr;const auto found=lookup(token,&s);if(found)return found;
 if(s->state==GBDLL_QUARANTINED)return GBDLL_QUARANTINE;if(s->state!=GBDLL_OPEN)return GBDLL_STATE;
 if(!p)return GBDLL_ARGUMENT;if(p->struct_size!=sizeof(*p)||p->version!=GBDLL_RETAINED_VERSION)return GBDLL_ABI;
 if(p->reserved||(p->flags&~GBDLL_FORCE_IDR)||!p->duration||p->pts>UINT64_MAX-p->duration||
    (s->have_pts&&(p->pts<=s->last_pts||p->occurrence<=s->encoder.last_serial())))return GBDLL_ARGUMENT;
 if(!s->encoder.retained_valid()||!p->generation||p->generation!=s->encoder.retained_generation())return GBDLL_STATE;
 try {
  const bool accepted=s->encoder.encode_retained(p->generation,p->occurrence,p->pts,p->duration,(p->flags&GBDLL_FORCE_IDR)!=0);
  if(s->encoder.state()==GBENC_QUARANTINED)return quarantine(*s,GBDLL_ENCODER,E_FAIL);
  s->last_pts=p->pts;s->have_pts=true;
  if(!accepted){s->state=GBDLL_FAILED;s->failure=GBDLL_ENCODER;}
  // No shared texture/key/copy/query is touched by repeat. Earlier keys remain for Poll.
  return accepted?GBDLL_OK:GBDLL_ENCODER;
 }catch(...){return quarantine(*s,GBDLL_MEMORY,E_FAIL);}
}

#ifndef GBENC_BUILD_ID
#define GBENC_BUILD_ID "unbuilt"
#endif
// The build id names the native sources this helper was compiled from; the
// setup screen and tools/build_plugin.py compare bundles by it, never by bytes.
extern "C" GBDLL_EXPORT const char* GBENC_CALL SM64GpuEncoderIdentityV1(void){return GBENC_BUILD_ID;}
