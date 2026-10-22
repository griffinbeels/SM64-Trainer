/* Isolated hidden real GPU witness. No emulator, capture lease or recorder.
 * A render thread owns the producer context, as LINK's does: every picture and
 * every capture tick runs there, and while an epoch is armed it keeps ticking
 * the way a renderer calls back once per VI. The consumer plays the encoder
 * helper: it creates the two keyed-mutex textures the renderer imports.
 *   <owner pid> <owner birth> [twin]   the producer; twin: a SourceV4 renderer
 *                                      whose display surface shows a ghost
 *   helper <nonce lo> <nonce hi>       the ghost copy's helper, its own process */
#include "gpu_delivery.h"
#include "gpu_delivery_diagnostics.h"
#include "gpu_startup.h"
#include "stamp_adapter.h"
#include "runtime_control.h"
#include "runtime_delivery.h"
#include "gpu_request.h"
#include "gl_fixture.h"
#include <d3d11_1.h>
#include <dxgi1_2.h>
#include <wrl/client.h>
#include <condition_variable>
#include <mutex>
#include <sstream>
#include <iostream>
extern "C" void gd_test_hold_poll(int);
extern "C" void gd_test_set_preparation_probe(void (__cdecl *)());
extern "C" void gd_test_set_iteration_probe(void (__cdecl *)());
extern "C" void gd_test_set_retired_probe(void (__cdecl *)());
extern "C" uint32_t gd_test_deadline_visits();
extern "C" int gd_test_terminal_diagnostics(gd_status*,uint32_t*);
extern "C" int gd_test_failure_status_coherence(gd_status*,uint32_t*);
extern "C" int gd_test_take_omissions(const rs_api*,uint64_t*);
extern "C" int gd_test_pressure_omissions(const rs_api*,uint64_t*,uint64_t*);
extern "C" int gd_test_retire_record(const rs_api*,uint32_t*);
extern "C" int gd_test_channel_retry(unsigned,uint32_t*);
extern "C" int gd_test_sample_retry(unsigned,uint32_t*);
extern "C" int gd_test_failure_snapshot(gd_diagnostic*,uint32_t*);
extern "C" int gd_test_healthy_snapshot(gd_diagnostic*);
extern "C" int gd_test_bootstrap_receipt(gpu_startup_v1*);
extern "C" void gd_test_bridge_fault_once(uint32_t);
extern "C" void gd_test_twin_bridge_fault_once(uint32_t);
namespace {
source_context::Registry registry;
Window* producer=nullptr;
std::atomic<uint32_t> transaction{0};
std::atomic<uint64_t> committed{0};
std::atomic<unsigned> stage_count{0};
uint32_t last_marker=0;
int64_t last_list_qpc=0;
constexpr unsigned width=17,height=11;
sa_table table{};
HANDLE prepare_entered=nullptr,prepare_continue=nullptr;
HANDLE iteration_entered=nullptr,iteration_continue=nullptr,retired_entered=nullptr,retired_continue=nullptr;
void __cdecl prepare_probe(){SetEvent(prepare_entered);CHECK(WaitForSingleObject(prepare_continue,4000)==WAIT_OBJECT_0);}
void __cdecl iteration_probe(){SetEvent(iteration_entered);CHECK(WaitForSingleObject(iteration_continue,4000)==WAIT_OBJECT_0);}
void __cdecl retired_probe(){SetEvent(retired_entered);CHECK(WaitForSingleObject(retired_continue,4000)==WAIT_OBJECT_0);}
// The worker's latest preparation note, for the gpu_prepare stamp witness.
std::atomic<bool> prepared_note{false};gd_prepare_diagnostic prepare_note{};
void __cdecl note(const gd_diagnostic*d){
    if(d->kind==GD_KIND_PREPARE && !prepared_note.load()){prepare_note=d->prepare;prepared_note.store(true);}
}
bool device_for_luid(LUID luid,ID3D11Device**device,ID3D11DeviceContext**context){
    Microsoft::WRL::ComPtr<IDXGIFactory1> factory;if(FAILED(CreateDXGIFactory1(IID_PPV_ARGS(&factory))))return false;
    for(unsigned i=0;i<32;++i){Microsoft::WRL::ComPtr<IDXGIAdapter1> adapter;if(factory->EnumAdapters1(i,&adapter)!=S_OK)break;
        DXGI_ADAPTER_DESC1 desc{};if(FAILED(adapter->GetDesc1(&desc))||memcmp(&desc.AdapterLuid,&luid,sizeof luid))continue;
        return SUCCEEDED(D3D11CreateDevice(adapter.Get(),D3D_DRIVER_TYPE_UNKNOWN,nullptr,D3D11_CREATE_DEVICE_BGRA_SUPPORT,
            nullptr,0,D3D11_SDK_VERSION,device,nullptr,context));
    }
    return false;
}
int64_t ticks(){LARGE_INTEGER v{};QueryPerformanceCounter(&v);return v.QuadPart;}
int __cdecl source_surface(rb_surface*s){
    *s={};s->width=width;s->height=height;s->context=reinterpret_cast<uintptr_t>(producer->rc);
    s->read_drawable=reinterpret_cast<uintptr_t>(producer->dc);s->renderer_thread=GetCurrentThreadId();
    s->context_generation=1;s->drawable_generation=1;s->boundary_qpc=ticks();
    s->drawable_width=64;s->drawable_height=64;s->restore_read_buffer=GL_BACK;
    s->source_format=RB_SOURCE_RGB8_LINEAR;s->post_vi_origin=last_marker;return 1;
}
// THE GHOST COPY (twin mode): a SourceV4 renderer whose display surface is a
// private FBO holding a different picture (the pattern of marker+100: "the
// ghost"), and the consumer switch the delivery turns on with the lane.
bool twin_mode=false;
std::atomic<unsigned> display_mode{1}; // 1 a ghost was drawn, 2 no ghost (same as clean)
std::atomic<bool> display_on{false};
std::atomic<unsigned> display_calls{0},consumer_calls{0};
GLuint display_fbo=0,display_texture=0;
int __cdecl source_surface3(rb_ticket,rb_surface*s,rs_source_descriptor3*d){
    source_surface(s);*d={};d->bytes=sizeof(*d);d->version=RS_ABI_V3;d->kind=RS_SOURCE_DEFAULT_FRONT;
    d->read_buffer=GL_FRONT;d->y=s->bottom_offset;d->width=s->width;d->height=s->height;
    d->surface_width=s->drawable_width;d->surface_height=s->drawable_height;d->source_format=s->source_format;
    d->context=s->context;d->read_drawable=s->read_drawable;d->renderer_thread=s->renderer_thread;
    d->context_generation=s->context_generation;d->drawable_generation=s->drawable_generation;d->vi_origin=s->post_vi_origin;
    return 1;
}
void __cdecl clean_consumer(bool){}
int __cdecl query_display(rb_ticket t,rs_source_descriptor3*out){
    display_calls.fetch_add(1);rs_source_descriptor3 clean{};
    if(!rb_read_source_descriptor3(t,&clean)){*out={};return 0;}
    *out={};out->bytes=sizeof(*out);out->version=RS_ABI_V4;out->kind=RS_SOURCE_UNAVAILABLE;
    if(!display_on.load())return 1;
    if(display_mode.load()==2){out->kind=RS_SOURCE_SAME_AS_CLEAN;out->update_occurrence=t.occurrence;return 1;}
    *out=clean;out->version=RS_ABI_V4;out->kind=RS_SOURCE_DISPLAY_VI;out->framebuffer=display_fbo;out->read_buffer=0x8CE0;
    out->source_format=RB_SOURCE_RGB8_LINEAR;out->resource_generation=1;out->presentation_serial=out->update_occurrence=t.occurrence;
    return 1;
}
void __cdecl display_consumer(bool on){display_on.store(on);consumer_calls.fetch_add(1);}
void make_display(){
    glGenTextures(1,&display_texture);glBindTexture(GL_TEXTURE_2D,display_texture);
    glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA8,64,64,0,GL_RGBA,GL_UNSIGNED_BYTE,nullptr);glBindTexture(GL_TEXTURE_2D,0);
    proc<GenFbo>("glGenFramebuffers")(1,&display_fbo);proc<BindFbo>("glBindFramebuffer")(framebuffer,display_fbo);
    proc<Attach>("glFramebufferTexture2D")(framebuffer,attachment,GL_TEXTURE_2D,display_texture,0);
    CHECK(proc<CheckFbo>("glCheckFramebufferStatus")(framebuffer)==complete);
    proc<BindFbo>("glBindFramebuffer")(framebuffer,0);CHECK(glGetError()==GL_NO_ERROR);
}
int __cdecl capture(const rb_record*r,rb_image*i){gd_surface(r);return gd_capture(r,i);}
bool __cdecl begin(){return glGetError()==GL_NO_ERROR;}
bool __cdecl end(){return glGetError()==GL_NO_ERROR;}
int __cdecl acquire(uintptr_t c,uint32_t g,rcl_lease*l){return registry.acquire(c,g,l);}
int __cdecl release(rcl_token t){return registry.release(t);}
uint32_t __cdecl health(){return registry.health();}
uint32_t __cdecl error(){return registry.error();}
uint32_t __cdecl activate(){++stage_count;return rb_activate();}
// Renderer-thread capture runs in the renderer's own context, so its GL state
// must come back exactly: with the guard on, the renderer sets unusual scissor
// and pack state before every capture call and checks it afterwards.
std::atomic<bool> guard_state{false};GLuint guard_buffer=0;
void odd_state(){
    using Gen=void(APIENTRY*)(GLsizei,GLuint*);using Bind=void(APIENTRY*)(GLenum,GLuint);
    if(!guard_buffer)proc<Gen>("glGenBuffers")(1,&guard_buffer);
    proc<Bind>("glBindBuffer")(0x88EB,guard_buffer);glEnable(GL_SCISSOR_TEST);
    glPixelStorei(GL_PACK_ALIGNMENT,2);glPixelStorei(GL_PACK_ROW_LENGTH,5);
    glPixelStorei(GL_PACK_SKIP_ROWS,1);glPixelStorei(GL_PACK_SKIP_PIXELS,1);
}
void check_odd_state(){
    GLint alignment=0,row=0,rows=0,pixels=0,buffer=0;
    glGetIntegerv(GL_PACK_ALIGNMENT,&alignment);glGetIntegerv(GL_PACK_ROW_LENGTH,&row);
    glGetIntegerv(GL_PACK_SKIP_ROWS,&rows);glGetIntegerv(GL_PACK_SKIP_PIXELS,&pixels);glGetIntegerv(0x88ED,&buffer);
    CHECK(glIsEnabled(GL_SCISSOR_TEST) && alignment==2 && row==5 && rows==1 && pixels==1 && GLuint(buffer)==guard_buffer);
}
void plain_state(){
    using Bind=void(APIENTRY*)(GLenum,GLuint);proc<Bind>("glBindBuffer")(0x88EB,0);
    glPixelStorei(GL_PACK_ALIGNMENT,4);glPixelStorei(GL_PACK_ROW_LENGTH,0);
    glPixelStorei(GL_PACK_SKIP_ROWS,0);glPixelStorei(GL_PACK_SKIP_PIXELS,0);
}
void frame(unsigned marker){
    CHECK(wglGetCurrentContext()==producer->rc);glDrawBuffer(GL_FRONT);glEnable(GL_SCISSOR_TEST);
    for(unsigned y=0;y<height;++y)for(unsigned x=0;x<width;++x){glScissor(x,y,1,1);
        glClearColor(channel(marker,x,y,0)/255.f,channel(marker,x,y,1)/255.f,channel(marker,x,y,2)/255.f,1.f);glClear(GL_COLOR_BUFFER_BIT);}
    glDrawBuffer(GL_BACK);
    if(twin_mode){ // the displayed picture: the clean one with a ghost over it
        proc<BindFbo>("glBindFramebuffer")(draw_fbo,display_fbo);
        for(unsigned y=0;y<height;++y)for(unsigned x=0;x<width;++x){glScissor(x,y,1,1);
            glClearColor(channel(marker+100,x,y,0)/255.f,channel(marker+100,x,y,1)/255.f,channel(marker+100,x,y,2)/255.f,1.f);glClear(GL_COLOR_BUFFER_BIT);}
        proc<BindFbo>("glBindFramebuffer")(draw_fbo,0);
    }
    last_marker=marker;transaction.fetch_add(1);
    rb_stamp stamp{};stamp.list_qpc=last_list_qpc=ticks();stamp.table_count=2;stamp.lengths[0]=4;stamp.lengths[1]=8;
    stamp.bytes[0]=uint8_t(marker);stamp.bytes[128]=uint8_t(255-marker);stamp.bytes[132]=0xA5;
    stamp.lists_since=1;const auto ticket=rb_stage(&stamp);
    if(guard_state.load())odd_state();
    const auto begun=rb_source_begin(ticket);rb_source_end(begun);rb_finish(ticket);
    if(guard_state.load())check_odd_state();
    if(ticket.occurrence)committed.store(ticket.occurrence);transaction.fetch_add(1);
}
gd_status status(){
    // A concurrent publication can exhaust the API's three nonblocking reads.
    // Only this isolated test client waits; production callbacks never use it.
    gd_status s{};const auto deadline=GetTickCount64()+1000;
    do{if(gd_read_status(&s))return s;Sleep(1);}while(GetTickCount64()<deadline);
    CHECK(false);return s;
}
bool wait_state(unsigned target){for(unsigned n=0;n<4000;++n){const auto s=status();if(s.state==target)return true;
        if(s.quarantined){fprintf(stderr,"quarantine reason=%u detail=%u state=%u pending=%u bridges=%u\n",s.reason,s.error,s.state,s.pending_records,s.pending_bridges);return false;}Sleep(1);}
    const auto s=status();fprintf(stderr,"state timeout wanted=%u got=%u reason=%u detail=%u records=%u bridges=%u\n",target,s.state,s.reason,s.error,s.pending_records,s.pending_bridges);return false;}
bool wait_mapping(){for(unsigned n=0;n<4000;++n){const auto s=status();if(s.state==GD_PREPARING&&s.mapping_bytes)return true;if(s.quarantined)return false;Sleep(1);}return false;}
struct Consumer {
    Microsoft::WRL::ComPtr<ID3D11Device> device;Microsoft::WRL::ComPtr<ID3D11DeviceContext> context;
    Microsoft::WRL::ComPtr<ID3D11Texture2D> texture[2];Microsoft::WRL::ComPtr<IDXGIKeyedMutex> mutex[2];
    HANDLE share[2]{};
    bool held[2]{};
    // The encoder helper's role: create the keyed-mutex textures under the
    // channel's names (the renderer imports them; it has no D3D device).
    void open(const gc_header&h,const uint16_t (*names)[128]=nullptr){
        if(!names)names=h.texture_names;
        LUID luid{h.luid_low,h.luid_high};CHECK(device_for_luid(luid,&device,&context));
        D3D11_TEXTURE2D_DESC desc{};desc.Width=h.width&~1u;desc.Height=h.height&~1u;desc.MipLevels=1;desc.ArraySize=1;
        desc.Format=DXGI_FORMAT_R8G8B8A8_UNORM;desc.SampleDesc.Count=1;desc.Usage=D3D11_USAGE_DEFAULT;
        desc.BindFlags=D3D11_BIND_RENDER_TARGET|D3D11_BIND_SHADER_RESOURCE;
        desc.MiscFlags=D3D11_RESOURCE_MISC_SHARED_NTHANDLE|D3D11_RESOURCE_MISC_SHARED_KEYEDMUTEX;
        for(unsigned i=0;i<2;++i){
            CHECK(SUCCEEDED(device->CreateTexture2D(&desc,nullptr,&texture[i])));CHECK(SUCCEEDED(texture[i].As(&mutex[i])));
            Microsoft::WRL::ComPtr<IDXGIResource1> resource;CHECK(SUCCEEDED(texture[i].As(&resource)));
            CHECK(SUCCEEDED(resource->CreateSharedHandle(nullptr,DXGI_SHARED_RESOURCE_READ|DXGI_SHARED_RESOURCE_WRITE,
                reinterpret_cast<const wchar_t*>(names[i]),&share[i])));
        }
    }
    bool take(unsigned i){CHECK(i<2&&!held[i]);const HRESULT result=mutex[i]->AcquireSync(1,0);held[i]=result==S_OK;CHECK(result==S_OK||result==WAIT_TIMEOUT);return held[i];}
    void give(unsigned i){CHECK(i<2&&held[i]);CHECK(SUCCEEDED(mutex[i]->ReleaseSync(0)));held[i]=false;}
    void verify(unsigned i,unsigned marker){
        CHECK(held[i]);D3D11_TEXTURE2D_DESC d{};texture[i]->GetDesc(&d);
        CHECK(d.Width==(width&~1u)&&d.Height==(height&~1u));
        d.Usage=D3D11_USAGE_STAGING;d.BindFlags=0;d.MiscFlags=0;d.CPUAccessFlags=D3D11_CPU_ACCESS_READ;
        Microsoft::WRL::ComPtr<ID3D11Texture2D> staging;CHECK(SUCCEEDED(device->CreateTexture2D(&d,nullptr,&staging)));
        context->CopyResource(staging.Get(),texture[i].Get());D3D11_MAPPED_SUBRESOURCE mapped{};
        CHECK(SUCCEEDED(context->Map(staging.Get(),0,D3D11_MAP_READ,0,&mapped)));
        for(unsigned y=0;y<d.Height;++y)for(unsigned x=0;x<d.Width;++x){const auto*b=static_cast<const uint8_t*>(mapped.pData)+y*mapped.RowPitch+x*4;
            for(unsigned c=0;c<3;++c)CHECK(b[c]==channel(marker,x,height-1-y,c));CHECK(b[3]==255);}
        context->Unmap(staging.Get(),0);
    }
    void close(){for(unsigned i=0;i<2;++i){CHECK(!held[i]);mutex[i].Reset();texture[i].Reset();if(share[i])CloseHandle(share[i]);share[i]=nullptr;}context.Reset();device.Reset();}
};
// The renderer's thread: owns the producer context; runs posted jobs (pictures)
// and, between them, one capture tick every 2 ms while an epoch is armed.
struct RenderThread {
    std::mutex m;std::condition_variable cv;std::function<void()> job;bool has_job=false,done=false;
    std::atomic<bool> ticking{true};std::thread thread;
    void start(){
        thread=std::thread([this]{
            CHECK(wglMakeCurrent(producer->dc,producer->rc));
            glDisable(GL_DITHER);glDisable(0x8DB9);glReadBuffer(GL_BACK);
            if(twin_mode)make_display();
            for(;;){
                std::unique_lock<std::mutex> lock(m);
                cv.wait_for(lock,std::chrono::milliseconds(2),[this]{return has_job;});
                if(has_job){auto work=std::move(job);has_job=false;lock.unlock();work();lock.lock();done=true;cv.notify_all();continue;}
                lock.unlock();
                if(ticking.load() && gd_epoch()){
                    const bool guard=guard_state.load();if(guard)odd_state();
                    rb_record idle{};idle.epoch=gd_epoch();source_surface(&idle.surface);gd_surface(&idle);
                    if(guard)check_odd_state();
                }
            }
        });
        thread.detach();
    }
    void post(std::function<void()> work){std::lock_guard<std::mutex> lock(m);job=std::move(work);has_job=true;done=false;cv.notify_all();}
    void wait(){std::unique_lock<std::mutex> lock(m);CHECK(cv.wait_for(lock,std::chrono::seconds(8),[this]{return done;}));}
    void run(std::function<void()> work){post(std::move(work));wait();}
};
RenderThread renderer;
gc_header mapped_header(const gc_header&r){
    wchar_t name[128];swprintf_s(name,L"%s%016llx%016llx",GC_NAMESPACE,r.nonce_hi,r.nonce_lo);
    HANDLE mapping=OpenFileMappingW(FILE_MAP_ALL_ACCESS,FALSE,name);CHECK(mapping);
    void*view=MapViewOfFile(mapping,FILE_MAP_ALL_ACCESS,0,0,GC_HEADER_BYTES);CHECK(view);gc_header h{};
    CHECK(gpu_channel::stable_read(view,&h,sizeof h));CHECK(gpu_channel::validate(h,h.total_bytes));
    UnmapViewOfFile(view);CloseHandle(mapping);return h;
}
struct SupervisorRequest {
    HANDLE mapping=nullptr;
    control_page_t page{};
    control_request_t control{};
    gr_page config{};
    SupervisorRequest(const gc_header& request,unsigned token){
        page.producer_pid=GetCurrentProcessId();page.generation=request.generation;
        const auto birth=gpu_channel::process_birth(GetCurrentProcess());
        page.producer_created_lo=uint32_t(birth);page.producer_created_hi=uint32_t(birth>>32);
        page.rom_open=1;
        control.token=token;control.owner_pid=request.owner_pid;control.generation=page.generation;
        control.created_lo=uint32_t(request.owner_birth);control.created_hi=uint32_t(request.owner_birth>>32);
        control.enabled=control.heartbeat=1;
        const gr_identity identity{page.producer_pid,control.owner_pid,page.generation,token,birth,request.owner_birth};
        memcpy(config.magic,GR_MAGIC,8);config.version=GR_VERSION;config.bytes=GR_BYTES;config.seq=2;
        config.producer_pid=page.producer_pid;config.owner_pid=control.owner_pid;config.control_generation=page.generation;
        config.token=token;config.producer_birth=birth;config.owner_birth=request.owner_birth;
        config.nonce_lo=token;config.nonce_hi=page.producer_pid;config.snapshot_budget=4u*1024*1024;
        config.slots=request.offer_count;config.rdram_bytes=8u<<20;config.table_count=request.table_count;
        config.channel_budget=request.ram_budget;config.packet_count=request.packet_count;
        config.packet_bytes=request.packet_bytes;config.pending_bytes=request.pending_bytes;
        config.pcm_bytes=1024;config.pcm_blocks=4;config.max_age_ms=2000;config.encoder_duration=3000;
        for(unsigned n=0;n<request.table_count;++n)config.table[n]={request.table[n].offset,request.table[n].length};
        CHECK(gr_valid(config,identity,4,config.snapshot_budget));
        wchar_t name[128];CHECK(gr_name(identity,name,128));
        mapping=CreateFileMappingW(INVALID_HANDLE_VALUE,nullptr,PAGE_READWRITE,0,GR_BYTES,name);
        CHECK(mapping&&GetLastError()!=ERROR_ALREADY_EXISTS);
        void* view=MapViewOfFile(mapping,FILE_MAP_ALL_ACCESS,0,0,GR_BYTES);CHECK(view);
        memcpy(view,&config,sizeof config);MemoryBarrier();UnmapViewOfFile(view);
    }
    ~SupervisorRequest(){CloseHandle(mapping);}
    uint32_t preparing(){
        uint32_t observed=UINT32_MAX,why=UINT32_MAX;
        rc_demand(&page,&control,&observed,&why);
        CHECK(observed==CONTROL_PREPARING&&why==0);
        const auto s=status();
        CHECK(s.state==GD_BOOTSTRAP&&s.reason==GD_NONE&&s.error==0&&s.epoch==gd_epoch()&&s.epoch);
        CHECK(s.generation==page.generation&&s.owner_pid==control.owner_pid&&s.producer_pid==page.producer_pid);
        CHECK(s.nonce_lo==config.nonce_lo&&s.nonce_hi==config.nonce_hi);
        CHECK(!s.snapshot_bytes&&!s.bridge_bytes&&!s.sample_bytes&&!s.mapping_bytes);
        CHECK(!s.pending_records&&!s.pending_bridges&&!s.published_token&&!s.quarantined);
        return s.epoch;
    }
};
void startup_race(const gc_header& request,const gd_limits& limits){
    // "Not one iteration yet" must hold: no renderer tick may advance it either.
    renderer.ticking.store(false);
    CHECK(rc_bind_delivery(&limits));rc_rom(TRUE);
    iteration_entered=CreateEventW(nullptr,FALSE,FALSE,nullptr);iteration_continue=CreateEventW(nullptr,FALSE,FALSE,nullptr);
    retired_entered=CreateEventW(nullptr,FALSE,FALSE,nullptr);retired_continue=CreateEventW(nullptr,FALSE,FALSE,nullptr);
    CHECK(iteration_entered&&iteration_continue&&retired_entered&&retired_continue);
    gd_test_set_iteration_probe(iteration_probe);
    CHECK(WaitForSingleObject(iteration_entered,4000)==WAIT_OBJECT_0);
    const auto calls=stage_count.load();
    SupervisorRequest first(request,601);
    const auto first_epoch=first.preparing();
    for(unsigned n=0;n<16;++n)CHECK(first.preparing()==first_epoch);
    CHECK(stage_count.load()==calls+1);
    // The worker has not processed even one iteration of this accepted request.
    // Preserve a real failed previous epoch for the second admission witness.
    gd_test_set_retired_probe(retired_probe);rc_revoke(GD_BAD_SOURCE);CHECK(!gd_epoch());
    gd_test_set_iteration_probe(nullptr);SetEvent(iteration_continue);
    CHECK(WaitForSingleObject(retired_entered,4000)==WAIT_OBJECT_0);
    CHECK(status().state==GD_FAULT&&status().reason==GD_BAD_SOURCE);
    const auto deadline_visits=gd_test_deadline_visits();
    gd_test_set_iteration_probe(iteration_probe);
    SupervisorRequest second(request,602);
    const auto second_epoch=second.preparing();CHECK(second_epoch!=first_epoch);
    CHECK(stage_count.load()==calls+2);
    gd_test_set_retired_probe(nullptr);SetEvent(retired_continue);
    CHECK(WaitForSingleObject(iteration_entered,4000)==WAIT_OBJECT_0);
    // An old retired iteration must not evaluate a deadline using new state.
    CHECK(gd_test_deadline_visits()==deadline_visits);
    CHECK(second.preparing()==second_epoch);
    rc_revoke(GD_CANCELLED);CHECK(!gd_epoch());
    gd_test_set_iteration_probe(nullptr);SetEvent(iteration_continue);CHECK(wait_state(GD_OFF));
    for(HANDLE h:{iteration_entered,iteration_continue,retired_entered,retired_continue})CloseHandle(h);
    renderer.ticking.store(true);
}
// The ghost copy's helper: its own process, which the witness may kill while
// it holds a texture. It creates the two "-copy-" textures the lane imports.
int helper_main(uint64_t lo,uint64_t hi){
    gc_header named{};named.nonce_lo=lo;named.nonce_hi=hi;const auto h=mapped_header(named);
    Consumer twin;twin.open(h,h.twin_texture_names);printf("helper ready\n");fflush(stdout);std::string line;
    while(std::getline(std::cin,line)){std::istringstream in(line);std::string op;in>>op;unsigned index=0,marker=0;
        if(op=="take"){in>>index>>marker;const bool got=twin.take(index);if(got)twin.verify(index,marker);printf("take %u\n",got);}
        else if(op=="give"){in>>index;twin.give(index);printf("give\n");}
        else if(op=="exit"){twin.close();printf("exit\n");fflush(stdout);return 0;}
        else CHECK(false);
        fflush(stdout);
    }
    return 1;
}
}
extern "C" int __cdecl sa_publish_table(const sa_table*t){table=*t;return t->epoch!=0;}
extern "C" void __cdecl sa_arm_refusal(uint32_t){}
extern "C" int __cdecl sa_probe_frontier(uint64_t through,sa_frontier*out){
    const auto s=transaction.load(),e=gd_epoch();const auto high=committed.load();
    if((s&1)||!e||through<high)return 0;const auto now=ticks();
    if(s!=transaction.load()||e!=gd_epoch())return 0;*out={s,e,0,0,high,now};return 1;
}
extern "C" int __cdecl sa_probe_quiescent(uint64_t through,sa_frontier*out){
    const auto s=transaction.load();const auto high=committed.load();
    if((s&1)||gd_epoch()||through<high)return 0;const auto now=ticks();
    if(s!=transaction.load()||gd_epoch())return 0;*out={s,0,0,0,high,now};return 1;
}
int main(int argc,char**argv){
    if(argc==2 && !strcmp(argv[1],"--bootstrap-receipt-cpu")){
        gpu_startup_v1 receipts[2]{};CHECK(gd_test_bootstrap_receipt(receipts));
        for(const auto&r:receipts){
            CHECK(!memcmp(r.magic,GPU_STARTUP_MAGIC,8) && r.version==1 && r.bytes==sizeof r);
            CHECK(r.epoch==17 && r.control_generation==7 && r.nonce_lo==23 && r.nonce_hi==29);
            CHECK(!r.vendor[0] && !r.renderer[0] && !r.gl_version[0] && !r.luid_available && !r.load_error);
        }
        CHECK(!receipts[0].width && !receipts[0].height && receipts[1].width==17 && receipts[1].height==11);
        printf("bootstrap receipt accepted 0 0 surface 17 11 gateway unavailable\n");return 0;
    }
    if(argc==2 && !strcmp(argv[1],"--healthy-snapshot-cpu")){
        gd_diagnostic windows[3]{};CHECK(gd_test_healthy_snapshot(windows));
        const auto&a=windows[0];const auto&b=windows[1];const auto&failure=windows[2];
        CHECK(a.healthy && b.healthy && !failure.healthy && a.epoch==17 && a.qpc_frequency==1000);
        CHECK(a.window_started_qpc==1000 && a.observed_qpc==61000);
        CHECK(b.window_started_qpc==61000 && b.observed_qpc==66000 && b.refused_pictures==1);
        CHECK(a.phases[GD_PHASE_SAMPLE].max_ticks==50 && b.phases[GD_PHASE_SAMPLE].max_ticks==3);
        CHECK(failure.phases[GD_PHASE_SAMPLE].max_ticks==50 && failure.phases[GD_PHASE_SAMPLE].total_ticks==53);
        CHECK(a.cadence.records==3 && a.cadence.pictures==1 && a.cadence.same_origin==2);
        CHECK(a.cadence.intervals.calls==2 && a.cadence.intervals.total_ticks==66);
        CHECK(a.cadence.min_ticks==16 && a.cadence.intervals.max_ticks==50);
        CHECK(a.cadence.buckets[1]==1 && a.cadence.buckets[3]==1 && !a.cadence.invalid_timestamps);
        CHECK(b.cadence.records==1 && b.cadence.intervals.calls==1 && b.cadence.intervals.max_ticks==14);
        CHECK(failure.cadence.records==4 && failure.cadence.intervals.total_ticks==80);
        CHECK(failure.reason==GD_SOURCE_REFUSED && failure.detail==7);
        printf("healthy snapshot 2 windows 1 failure cadence 16 50 14 cumulative 80 recent-max 3 cumulative-max 50\n");return 0;
    }
    if(argc==2 && !strcmp(argv[1],"--failure-snapshot-cpu")){
        gd_diagnostic d{};uint32_t receipts[2]{};CHECK(gd_test_failure_snapshot(&d,receipts));
        CHECK(d.bytes==sizeof d && d.version==GD_DIAGNOSTIC_VERSION);
        CHECK(d.qpc_frequency==10000000 && d.observed_qpc>=d.phase_started_qpc && d.oldest_admitted_qpc);
        const auto&sample=d.phases[GD_PHASE_SAMPLE];
        CHECK(sample.calls==1 && sample.total_ticks==50 && sample.max_ticks==50 && sample.max_started_qpc==100);
        printf("failure snapshot %u %u %u %u %u %u %u %u %u %u\n",receipts[0],receipts[1],d.epoch,d.reason,d.detail,
            d.current_phase,d.source_records,d.offer_retirements,d.unpublished_bridges,d.awaiting_copy);return 0;
    }
    if(argc==3 && !strcmp(argv[1],"--sample-retry-cpu")){
        uint32_t values[11]{};CHECK(gd_test_sample_retry(unsigned(strtoul(argv[2],nullptr,10)),values));
        printf("sample retry %u %u %u %u %u %u %u %u %u %u %u\n",values[0],values[1],values[2],values[3],values[4],values[5],values[6],values[7],values[8],values[9],values[10]);return 0;
    }
    if(argc==3 && !strcmp(argv[1],"--channel-retry-cpu")){
        uint32_t values[6]{};CHECK(gd_test_channel_retry(unsigned(strtoul(argv[2],nullptr,10)),values));
        printf("channel retry %u %u %u %u %u %u\n",values[0],values[1],values[2],values[3],values[4],values[5]);return 0;
    }
    if(argc==2 && (!strcmp(argv[1],"--omission-custody-cpu") || !strcmp(argv[1],"--retirement-custody-cpu"))){
        static rb_record record{};static bool taken=false;
        record.occurrence=42;record.slot=0;record.epoch=1;
        record.outcome=RB_OBSERVED;record.image.status=SA_SAME_ORIGIN;
        rs_api source{};
        source.take=[]()->const rb_record*{if(taken)return nullptr;taken=true;return &record;};
        source.release=[](rb_ticket ticket){
            CHECK(ticket.occurrence==42 && ticket.slot==0);
            // Reuse is legal as soon as release publishes FREE. Force that
            // interleaving without an OS scheduler or graphics context.
            record={};record.occurrence=99;return 1;
        };
        source.disarm=[](){};
        if(!strcmp(argv[1],"--retirement-custody-cpu")){
            uint32_t pending=0;CHECK(gd_test_retire_record(&source,&pending));
            printf("retirement custody %u %llu\n",pending,record.occurrence);return 0;
        }
        uint64_t accounted=0;CHECK(gd_test_take_omissions(&source,&accounted));
        printf("omission custody %llu %llu\n",accounted,record.occurrence);return 0;
    }
    if(argc==2 && !strcmp(argv[1],"--pressure-omissions-cpu")){
        // A record the snapshot pool refused (full) while ACTIVE, then three
        // refused stages reported by the frontier: both are counted, neither fails.
        static rb_record record{};static bool taken=false;
        record.occurrence=42;record.slot=0;record.epoch=1;
        record.outcome=RB_SURFACE_FAILED;record.image.status=uint32_t(snapshot::Result::full);
        rs_api source{};
        source.take=[]()->const rb_record*{if(taken)return nullptr;taken=true;return &record;};
        source.release=[](rb_ticket ticket){CHECK(ticket.occurrence==42 && ticket.slot==0);return 1;};
        source.disarm=[](){};
        uint64_t accounted=0,refused=0;CHECK(gd_test_pressure_omissions(&source,&accounted,&refused));
        printf("pressure omissions %llu %llu\n",accounted,refused);return 0;
    }
    if(argc==2 && !strcmp(argv[1],"--diagnostic-race-cpu")){
        gd_status rows[3]{};uint32_t projected[3]{};CHECK(gd_test_failure_status_coherence(rows,projected));
        CHECK(rows[0].generation==7 && rows[0].nonce_lo==23 && rows[0].nonce_hi==29);
        CHECK(rows[1].generation==7 && rows[1].nonce_lo==23 && rows[1].nonce_hi==29);
        CHECK(rows[2].generation==8 && rows[2].nonce_lo==31 && rows[2].nonce_hi==29);
        printf("failure coherence %u %u %u %u %u %u %u %u %u\n",rows[0].reason,rows[0].error,projected[0],
            rows[1].reason,rows[1].error,projected[1],rows[2].reason,rows[2].error,projected[2]);return 0;
    }
    if(argc==2 && !strcmp(argv[1],"--diagnostic-cpu")){
        gd_status results[2]{};uint32_t revoked=0;CHECK(gd_test_terminal_diagnostics(results,&revoked));
        printf("diagnostic %u %u %u %u %u\n",results[0].reason,results[0].error,revoked,results[1].reason,results[1].error);return 0;
    }
    if(argc==4 && !strcmp(argv[1],"helper"))return helper_main(strtoull(argv[2],nullptr,10),strtoull(argv[3],nullptr,10));
    twin_mode=argc==4 && !strcmp(argv[3],"twin");
    CHECK(argc==3 || twin_mode);const auto owner=uint32_t(strtoul(argv[1],nullptr,10));const auto birth=strtoull(argv[2],nullptr,10);
    WNDCLASSW wc{};wc.style=CS_OWNDC;wc.lpfnWndProc=DefWindowProcW;wc.hInstance=GetModuleHandleW(nullptr);wc.lpszClassName=L"SnapshotHiddenHost";CHECK(RegisterClassW(&wc));
    Window window;producer=&window;renderer.start();
    CHECK(rb_configure_images(capture,gd_completed));CHECK(twin_mode?rb_bind_source3(source_surface3):rb_bind_source(source_surface));
    rs_api source{};source.bytes=sizeof(source);source.version=RS_ABI_V2;source.capabilities=3;
    source.activate=activate;source.disarm=rb_disarm;source.take=rb_take;source.release=rb_release;source.begin_capture=begin;source.end_capture=end;
    rcl_api contexts{sizeof(rcl_api),RCL_ABI_V1,RCL_CAP_NEVER_CURRENT_ANCHOR,0,acquire,release,health,error};
    gd_limits limits{sizeof(gd_limits),GD_VERSION,4,2000,4u*1024*1024,4u*1024*1024,1024*1024,1024*1024,2,0};
    const rs_api_v3 source3{sizeof(rs_api_v3),RS_ABI_V3,RS_CAP_BOUNDARY|RS_CAP_SOURCE_FORMAT|RS_CAP_CLEAN_PRESENTATION,0,
        source,rb_read_source_descriptor3,clean_consumer};
    const rs_api_v4 source4{sizeof(rs_api_v4),RS_ABI_V4,
        RS_CAP_BOUNDARY|RS_CAP_SOURCE_FORMAT|RS_CAP_CLEAN_PRESENTATION|RS_CAP_DISPLAY_COPY,0,source3,query_display,display_consumer};
    CHECK((twin_mode?gd_configure4(&source4,&contexts,&limits):gd_configure(&source,&contexts,&limits))==GD_ACCEPTED);
    CHECK(gd_configure(&source,&contexts,&limits)==GD_BUSY);CHECK(!gd_epoch());
    gd_set_diagnostic(note);
    rb_rom_open();
    gc_header request{};request.owner_pid=owner;request.owner_birth=birth;request.generation=1;
    request.offer_count=4;request.table_count=2;request.table[0]={0,4};request.table[1]={8,8};
    request.packet_count=4;request.packet_bytes=1024;request.pending_bytes=4096;request.ram_budget=1024*1024;
    Consumer consumer;printf("ready\n");fflush(stdout);std::string line;
    while(std::getline(std::cin,line)){std::istringstream in(line);std::string op;in>>op;
        if(op=="startuprace"){startup_race(request,limits);printf("startup race 2\n");
        }else if(op=="prepnote"){
            for(unsigned n=0;n<2000 && !prepared_note.load();++n)Sleep(1);CHECK(prepared_note.load());
            const auto&p=prepare_note;
            printf("prepnote %llu %llu %llu %llu %llu %llu %llu %llu %llu %llu %llu\n",
                p.request_qpc,p.surface_qpc,p.prepare_begin,p.snapshots,p.sampler,p.luid,p.channel,
                p.reader_qpc,p.import_begin,p.imported,p.end_qpc);
        }else if(op=="start"){
            unsigned nonce=0;in>>nonce;request.nonce_lo=nonce;request.nonce_hi=GetCurrentProcessId();
            const auto prior=stage_count.load();CHECK(gd_request(&request,4u*1024*1024)==GD_ACCEPTED);
            CHECK(gd_request(&request,4u*1024*1024)==GD_UNCHANGED);CHECK(stage_count.load()==prior+1);
            CHECK(wait_state(GD_BOOTSTRAP));renderer.run([]{frame(0);});CHECK(wait_mapping());
            fprintf(stderr,"host active header\n");fflush(stderr);const auto h=mapped_header(request);
            fprintf(stderr,"host consumer open\n");fflush(stderr);consumer.open(h);
            fprintf(stderr,"host consumer opened\n");fflush(stderr);
            printf("prepared %u %u %llu %llu\n",gd_epoch(),GetCurrentProcessId(),request.nonce_lo,request.nonce_hi);
        }else if(op=="active"){CHECK(wait_state(GD_ACTIVE));printf("active\n");
        }else if(op=="frame"){unsigned marker=0;in>>marker;renderer.run([marker]{frame(marker);});printf("frame %u\n",marker);
        }else if(op=="guard"){unsigned value=0;in>>value;
            renderer.run([value]{guard_state.store(value!=0);if(!value)plain_state();});printf("guard\n");
        }else if(op=="tick"){unsigned value=0;in>>value;renderer.ticking.store(value!=0);printf("tick\n");
        }else if(op=="hold"){unsigned value=0;in>>value;gd_test_hold_poll(value);printf("hold\n");
        }else if(op=="laststamp"){printf("stamp %lld\n",last_list_qpc);
        }else if(op=="abandon"){gd_test_bridge_fault_once(128);printf("abandoned injected\n");
        }else if(op=="mode"){std::string kind;in>>kind;CHECK(kind=="ghost"||kind=="same");display_mode.store(kind=="ghost"?1:2);printf("mode\n");
        }else if(op=="displaystats"){printf("display %u %u %u\n",display_calls.load(),unsigned(display_on.load()),consumer_calls.load());
        }else if(op=="twinfault"){gd_test_twin_bridge_fault_once(1);printf("twinfault\n");
        }else if(op=="exhausted"){
            for(unsigned n=0;n<3000 && !status().quarantined;++n)Sleep(1);
            const auto s=status();CHECK(s.quarantined&&s.state==GD_FAULT);++request.nonce_lo;
            CHECK(gd_request(&request,4u*1024*1024)==GD_EXHAUSTED);printf("exhausted %u\n",s.error);
        }else if(op=="exitquarantined"){CHECK(status().quarantined);printf("exit\n");fflush(stdout);ExitProcess(0);
        }else if(op=="cancelprep"){
            prepare_entered=CreateEventW(nullptr,FALSE,FALSE,nullptr);prepare_continue=CreateEventW(nullptr,FALSE,FALSE,nullptr);CHECK(prepare_entered&&prepare_continue);
            gd_test_set_preparation_probe(prepare_probe);request.nonce_lo=333;request.nonce_hi=GetCurrentProcessId();
            CHECK(gd_request(&request,4u*1024*1024)==GD_ACCEPTED);CHECK(wait_state(GD_BOOTSTRAP));renderer.post([]{frame(0);});
            CHECK(WaitForSingleObject(prepare_entered,4000)==WAIT_OBJECT_0);
            // The renderer is deliberately blocked in its own preparation step.
            // Disarm must return independently, without joining that thread.
            gd_disarm(GD_CANCELLED);CHECK(!gd_epoch());SetEvent(prepare_continue);renderer.wait();CHECK(wait_state(GD_OFF));
            gd_test_set_preparation_probe(nullptr);CloseHandle(prepare_entered);CloseHandle(prepare_continue);printf("cancelled preparation\n");
        }else if(op=="invalid"){
            auto bad=request;bad.nonce_lo=444;bad.nonce_hi=GetCurrentProcessId();const auto calls=stage_count.load();
            bad.width=640;CHECK(gd_request(&bad,4u*1024*1024)==GD_INVALID);bad.width=0;
            bad.reserved[0]=1;CHECK(gd_request(&bad,4u*1024*1024)==GD_INVALID);bad.reserved[0]=0;
            bad.table[0]={0xfffffffcu,8};CHECK(gd_request(&bad,4u*1024*1024)==GD_INVALID);bad.table[0]={0,4};
            CHECK(gd_request(&bad,8u*1024*1024)==GD_INVALID);CHECK(stage_count.load()==calls);printf("invalid refused\n");
        }else if(op=="take"){unsigned index=0,marker=0;in>>index>>marker;const bool got=consumer.take(index);if(got)consumer.verify(index,marker);printf("take %u\n",got);
        }else if(op=="give"){unsigned index=0;in>>index;consumer.give(index);printf("give\n");
        }else if(op=="stop"){gd_disarm(GD_CANCELLED);CHECK(!gd_epoch());printf("stopped\n");
        }else if(op=="settled"){CHECK(wait_state(GD_OFF));consumer.close();printf("settled\n");
        }else if(op=="status"){const auto s=status();printf("status %u %u %u %u %u %u\n",s.state,s.reason,s.error,s.pending_records,s.pending_bridges,s.quarantined);
        }else if(op=="exit"){gd_disarm(GD_CANCELLED);CHECK(wait_state(GD_OFF));consumer.close();printf("exit\n");fflush(stdout);ExitProcess(0);
        }else CHECK(false);fflush(stdout);
    }
    return 1;
}
