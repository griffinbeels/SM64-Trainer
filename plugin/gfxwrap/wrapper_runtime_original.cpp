/* Fake renderer surface + real boundary ownership; no window or GL call. */
#include "zilmar.h"
#include "link_source_api.h"
#include "context_lifetime.h"
#include <stdlib.h>
namespace {
GFX_INFO info{};unsigned lists=0,updates=0,opens=0,closes=0,delta=1;
unsigned wrapper_value(const char*name){auto p=reinterpret_cast<unsigned(__cdecl*)()>(
 GetProcAddress(GetModuleHandleW(L"sm64_trainer_gfx.dll"),name));return p?p():0;}
unsigned swaps=0; // the fake renderer's swap count: advances exactly when the origin moves
int __cdecl surface(rb_surface*s){*s={};s->width=8;s->height=6;s->context=123;s->read_drawable=456;
 s->context_generation=1;s->drawable_generation=1;s->drawable_width=8;s->drawable_height=6;
 s->renderer_thread=GetCurrentThreadId();s->post_vi_origin=*info.VI_ORIGIN_REG;s->source_format=RB_SOURCE_RGB8_LINEAR;
 s->swap_count=swaps; // the real renderer swaps exactly when the origin moved
 LARGE_INTEGER q;QueryPerformanceCounter(&q);s->boundary_qpc=q.QuadPart;return 1;}
int __cdecl configure(const rs_callbacks*c){
 if(opens||wrapper_value("TestConfigured")!=1||!c||c->bytes!=sizeof(*c)||c->version!=RS_ABI_V2)return 0;
 return rb_configure_images(c->capture,c->completed)&&rb_bind_source(surface);}
void update_original(){updates++;if(delta)++swaps;*info.VI_ORIGIN_REG+=delta;}
void __cdecl captured(const rs_request*r){const auto t=rb_source_begin(r?r->ticket:rb_ticket{});
 update_original();rb_source_end(t);}
bool __cdecl errors(){return true;}
const rs_api source{sizeof(rs_api),RS_ABI_V2,RS_CAP_BOUNDARY|RS_CAP_SOURCE_FORMAT,0,configure,
 rb_stage,captured,rb_finish,rb_activate,rb_disarm,rb_take,rb_release,rb_get_stats,errors,errors};
int __cdecl acquire(uintptr_t,uint32_t,rcl_lease*){return RCL_UNAVAILABLE;}
int __cdecl release(rcl_token){return RCL_STALE;}
uint32_t __cdecl health(){return 0;}
const rcl_api contexts{sizeof(rcl_api),RCL_ABI_V1,RCL_CAP_NEVER_CURRENT_ANCHOR,0,acquire,release,health,health};
}
extern "C" const rs_api* __cdecl SM64ReplaySourceV2(uint32_t v,uint32_t n){
#ifdef WR_UNSUPPORTED
 return nullptr;
#else
 return v==RS_ABI_V2&&n==sizeof(rs_api)?&source:nullptr;
#endif
}
extern "C" const rcl_api* __cdecl SM64ReplayContextV1(uint32_t v,uint32_t n){return v==RCL_ABI_V1&&n==sizeof(rcl_api)?&contexts:nullptr;}
extern "C" void __cdecl GetDllInfo(PLUGIN_INFO*p){*p={};p->Version=0x103;p->Type=PLUGIN_TYPE_GFX;
 p->MemoryBswaped=TRUE;strcpy_s(p->Name,"CPU source fixture");}
extern "C" BOOL __cdecl InitiateGFX(GFX_INFO i){info=i;return GetEnvironmentVariableA("WR_FAIL_INIT",nullptr,0)==0;}
extern "C" void __cdecl ProcessDList(){lists++;
#ifdef WR_UNSUPPORTED
 info.RDRAM[0]+=10;
#else
 info.RDRAM[0]++;
#endif
}
extern "C" void __cdecl UpdateScreen(){update_original();}
extern "C" void __cdecl RomOpen(){opens++;rb_rom_open();}
extern "C" void __cdecl RomClosed(){if(wrapper_value("TestGate"))ExitProcess(91);closes++;rb_rom_closed();}
extern "C" void __cdecl CloseDLL(){if(wrapper_value("TestGate"))ExitProcess(92);rb_close();}
extern "C" void __cdecl TestCounts(unsigned*p){p[0]=lists;p[1]=updates;p[2]=opens;p[3]=closes;}
extern "C" void __cdecl TestDelta(unsigned n){delta=n;}
#define EMPTY(name) extern "C" void __cdecl name(){}
EMPTY(ChangeWindow) EMPTY(DrawScreen) EMPTY(ViStatusChanged) EMPTY(ViWidthChanged)
EMPTY(ProcessRDPList) EMPTY(ShowCFB)
extern "C" void __cdecl MoveScreen(int,int){}
extern "C" void __cdecl CaptureScreen(char*){}
#define EXPORT(name) __pragma(comment(linker,"/EXPORT:" #name "=_" #name))
EXPORT(GetDllInfo) EXPORT(InitiateGFX) EXPORT(CloseDLL) EXPORT(ChangeWindow) EXPORT(DrawScreen)
EXPORT(ProcessDList) EXPORT(UpdateScreen) EXPORT(RomOpen) EXPORT(RomClosed) EXPORT(ViStatusChanged)
EXPORT(ViWidthChanged) EXPORT(ProcessRDPList) EXPORT(ShowCFB) EXPORT(MoveScreen) EXPORT(CaptureScreen)
EXPORT(TestCounts) EXPORT(TestDelta) EXPORT(SM64ReplaySourceV2) EXPORT(SM64ReplayContextV1)
