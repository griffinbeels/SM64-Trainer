/* Hidden autonomous source fixture. Links the real stamp adapter/frontier;
 * no emulator, machine-wide capture request, or installed plugin is opened.
 * SM64_CADENCE_TWIN=1: a SourceV4 renderer, and "twin on" makes this process
 * the ghost copy's client and helper too (it returns every twin texture at
 * once), so a stream measures the renderer's cost with the twin lane on. */
#include "gpu_delivery.h"
#include "stamp_adapter.h"
#include "gl_fixture.h"
#include <d3d11_1.h>
#include <dxgi1_2.h>
#include <wrl/client.h>
#include <atomic>
#include <iostream>
#include <mutex>
#include <sstream>

namespace {
unsigned width=320, height=240;
Window *producer=nullptr;
source_context::Registry registry;
rs_api source{};
uint64_t updates=0;
// swaps: the real renderer swaps exactly when the origin moves, and the stamp
// adapter omits an origin change with no swap (SA_NO_SWAP), so the fake must count.
unsigned marker=0, origin=0, swaps=0;
bool fresh_picture=true, swap_presentation=false;
HANDLE render_request=nullptr, render_done=nullptr;
rs_request render_command{};
// Project64 calls UpdateScreen on every VI whether or not the game drew, and
// renderer-thread capture does its GPU work inside those calls. Between stream
// commands a 60Hz clock supplies same-origin VIs; one emulation thread means
// VIs never overlap.
std::mutex vi_lock;
std::atomic<bool> vi_clock{true};
// The renderer's time per streamed VI: the whole update, and its capture part.
constexpr unsigned timed_capacity=2400;
std::atomic<bool> timing{false};std::atomic<unsigned> timed{0};
double vi_us[timed_capacity]{},capture_us[timed_capacity]{};
int64_t ticks(){LARGE_INTEGER v{};QueryPerformanceCounter(&v);return v.QuadPart;}
double frequency_us(){static const double f=[]{LARGE_INTEGER v{};QueryPerformanceFrequency(&v);return double(v.QuadPart)/1e6;}();return f;}
// THE GHOST COPY: the display surface, and what the ghost presentation costs
// the renderer per presented picture with the lane on (one full copy of the
// presented picture into it, plus a ghost drawn over that copy).
bool twin_mode=false;
std::atomic<bool> display_on{false};
GLuint display_fbo=0,display_texture=0;unsigned display_width=0,display_height=0;

int source_surface(rb_surface *s) {
    *s={};s->width=width;s->height=height;s->drawable_width=width;s->drawable_height=height;
    s->context=reinterpret_cast<uintptr_t>(producer->rc);
    s->read_drawable=reinterpret_cast<uintptr_t>(producer->dc);
    s->renderer_thread=GetCurrentThreadId();s->context_generation=s->drawable_generation=1;
    s->restore_read_buffer=GL_BACK;s->source_format=RB_SOURCE_RGB8_LINEAR;s->post_vi_origin=origin;s->swap_count=swaps;
    LARGE_INTEGER qpc{};QueryPerformanceCounter(&qpc);s->boundary_qpc=qpc.QuadPart;return 1;
}
void present_display() {
    using Blit=void(APIENTRY*)(GLint,GLint,GLint,GLint,GLint,GLint,GLint,GLint,GLbitfield,GLenum);
    static const auto blit=proc<Blit>("glBlitFramebuffer");
    const auto bind=proc<BindFbo>("glBindFramebuffer");
    if(display_width!=width || display_height!=height){
        if(!display_fbo){proc<GenFbo>("glGenFramebuffers")(1,&display_fbo);glGenTextures(1,&display_texture);}
        glBindTexture(GL_TEXTURE_2D,display_texture);
        glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA8,width,height,0,GL_RGBA,GL_UNSIGNED_BYTE,nullptr);glBindTexture(GL_TEXTURE_2D,0);
        bind(framebuffer,display_fbo);proc<Attach>("glFramebufferTexture2D")(framebuffer,attachment,GL_TEXTURE_2D,display_texture,0);
        CHECK(proc<CheckFbo>("glCheckFramebufferStatus")(framebuffer)==complete);bind(framebuffer,0);
        display_width=width;display_height=height;
    }
    glReadBuffer(GL_FRONT);bind(draw_fbo,display_fbo);
    blit(0,0,width,height,0,0,width,height,GL_COLOR_BUFFER_BIT,GL_NEAREST);
    glScissor(8,8,32,32);glClearColor(1.f,1.f,1.f,1.f);glClear(GL_COLOR_BUFFER_BIT); // the ghost
    bind(draw_fbo,0);glReadBuffer(GL_BACK);
}
void render_update(const rs_request *request) {
    const auto began=ticks();
    ++updates;
    const auto ticket=request?rb_source_begin(request->ticket):rb_ticket{};
    CHECK(wglGetCurrentContext()==producer->rc);
    if(fresh_picture){
        glDrawBuffer(swap_presentation?GL_BACK:GL_FRONT);glEnable(GL_SCISSOR_TEST);glScissor(0,0,width,height);
        glClearColor((marker%251)/250.f,((marker*7+marker/251*31)%251)/250.f,((marker*17)%251)/250.f,1.f);
        glClear(GL_COLOR_BUFFER_BIT);glDrawBuffer(GL_BACK);
        if(swap_presentation)CHECK(SwapBuffers(producer->dc));
        if(twin_mode && display_on.load())present_display();
        ++origin;++swaps;
    }
    const auto captured=ticks();
    rb_source_end(ticket);
    if(timing.load()){const auto n=timed.load();
        if(n<timed_capacity){vi_us[n]=double(ticks()-began)/frequency_us();capture_us[n]=double(ticks()-captured)/frequency_us();timed.store(n+1);}}
}
int source_surface3(rb_ticket,rb_surface *s,rs_source_descriptor3 *d) {
    source_surface(s);*d={};d->bytes=sizeof(*d);d->version=RS_ABI_V3;d->kind=RS_SOURCE_DEFAULT_FRONT;
    d->read_buffer=GL_FRONT;d->y=s->bottom_offset;d->width=s->width;d->height=s->height;
    d->surface_width=s->drawable_width;d->surface_height=s->drawable_height;d->source_format=s->source_format;
    d->context=s->context;d->read_drawable=s->read_drawable;d->renderer_thread=s->renderer_thread;
    d->context_generation=s->context_generation;d->drawable_generation=s->drawable_generation;d->vi_origin=s->post_vi_origin;
    return 1;
}
void clean_consumer(bool){}
int query_display(rb_ticket t,rs_source_descriptor3 *out) {
    rs_source_descriptor3 clean{};
    if(!rb_read_source_descriptor3(t,&clean)){*out={};return 0;}
    *out={};out->bytes=sizeof(*out);out->version=RS_ABI_V4;out->kind=RS_SOURCE_UNAVAILABLE;
    if(!display_on.load())return 1;
    if(!display_fbo || display_width!=clean.surface_width || display_height!=clean.surface_height){
        out->kind=RS_SOURCE_SAME_AS_CLEAN;out->update_occurrence=t.occurrence;return 1;
    }
    *out=clean;out->version=RS_ABI_V4;out->kind=RS_SOURCE_DISPLAY_VI;out->framebuffer=display_fbo;out->read_buffer=0x8CE0;
    out->source_format=RB_SOURCE_RGB8_LINEAR;out->resource_generation=1;out->presentation_serial=out->update_occurrence=t.occurrence;
    return 1;
}
void display_consumer(bool on){display_on.store(on);}
// The ghost copy's client and helper in one: READY once both "-copy-"
// textures exist, then every twin bridge is taken (key 1), returned (key 0)
// and receipted at once.
struct TwinHelper {
    std::thread thread;std::atomic<bool> stopping{false},stopped{false};
    std::atomic<uint64_t> returned{0};gc_twin_status last{};
    HANDLE mapping=nullptr;uint8_t *view=nullptr;gc_header h{};
    static void put(uint8_t *target,const void *data,uint32_t bytes){
        const auto seq=uint32_t(InterlockedCompareExchange((volatile LONG*)target,0,0));CHECK(!(seq&1));
        InterlockedExchange((volatile LONG*)target,LONG(seq+1));memcpy(target+4,static_cast<const uint8_t*>(data)+4,bytes-4);
        MemoryBarrier();InterlockedExchange((volatile LONG*)target,LONG(seq+2));
    }
    gc_twin *twin() const {return reinterpret_cast<gc_twin*>(view+h.twin_offset);}
    void request(uint64_t lane){
        gc_twin_client c{};c.state=lane?GC_TWIN_CLIENT_READY:GC_TWIN_CLIENT_OFF;c.lane=lane;
        c.nonce_lo=h.nonce_lo;c.nonce_hi=h.nonce_hi;c.epoch=h.epoch;c.generation=h.generation;
        put(reinterpret_cast<uint8_t*>(&twin()->client),&c,sizeof c);
    }
    gc_twin_status status(){gc_twin_status s{};CHECK(gpu_channel::stable_read(&twin()->status,&s,sizeof s));return s;}
    void start(uint64_t lo,uint64_t hi){
        wchar_t name[128];swprintf_s(name,L"%s%016llx%016llx",GC_NAMESPACE,hi,lo);
        mapping=OpenFileMappingW(FILE_MAP_ALL_ACCESS,FALSE,name);CHECK(mapping);
        view=static_cast<uint8_t*>(MapViewOfFile(mapping,FILE_MAP_ALL_ACCESS,0,0,0));CHECK(view);
        CHECK(gpu_channel::stable_read(view,&h,sizeof h)&&h.version==GC_CHANNEL_VERSION&&h.twin_bytes==GC_TWIN_BYTES);
        thread=std::thread([this]{run();});
    }
    void run(){
        using Microsoft::WRL::ComPtr;
        ComPtr<IDXGIFactory1> factory;CHECK(SUCCEEDED(CreateDXGIFactory1(IID_PPV_ARGS(&factory))));
        ComPtr<ID3D11Device> device;ComPtr<ID3D11DeviceContext> context;
        for(unsigned i=0;i<32 && !device;++i){ComPtr<IDXGIAdapter1> adapter;if(factory->EnumAdapters1(i,&adapter)!=S_OK)break;
            DXGI_ADAPTER_DESC1 desc{};CHECK(SUCCEEDED(adapter->GetDesc1(&desc)));
            if(desc.AdapterLuid.LowPart!=h.luid_low||desc.AdapterLuid.HighPart!=h.luid_high)continue;
            CHECK(SUCCEEDED(D3D11CreateDevice(adapter.Get(),D3D_DRIVER_TYPE_UNKNOWN,nullptr,0,nullptr,0,D3D11_SDK_VERSION,&device,nullptr,&context)));}
        CHECK(device);
        D3D11_TEXTURE2D_DESC desc{};desc.Width=h.width&~1u;desc.Height=h.height&~1u;desc.MipLevels=1;desc.ArraySize=1;
        desc.Format=DXGI_FORMAT_R8G8B8A8_UNORM;desc.SampleDesc.Count=1;desc.Usage=D3D11_USAGE_DEFAULT;
        desc.BindFlags=D3D11_BIND_RENDER_TARGET|D3D11_BIND_SHADER_RESOURCE;
        desc.MiscFlags=D3D11_RESOURCE_MISC_SHARED_NTHANDLE|D3D11_RESOURCE_MISC_SHARED_KEYEDMUTEX;
        ComPtr<ID3D11Texture2D> textures[2];ComPtr<IDXGIKeyedMutex> keys[2];HANDLE shares[2]{};
        for(unsigned i=0;i<2;++i){
            CHECK(SUCCEEDED(device->CreateTexture2D(&desc,nullptr,&textures[i]))&&SUCCEEDED(textures[i].As(&keys[i])));
            ComPtr<IDXGIResource1> resource;CHECK(SUCCEEDED(textures[i].As(&resource)));
            CHECK(SUCCEEDED(resource->CreateSharedHandle(nullptr,DXGI_SHARED_RESOURCE_READ|DXGI_SHARED_RESOURCE_WRITE,
                reinterpret_cast<const wchar_t*>(h.twin_texture_names[i]),&shares[i])));
        }
        request(1);
        uint64_t done[2]{};const auto deadline_ms=2000ull;uint64_t stop_started=0;
        for(;;){
            for(unsigned i=0;i<2;++i){gc_bridge b{};
                if(!gpu_channel::stable_read(&twin()->bridges[i],&b,sizeof b)||!b.token||b.token==done[i])continue;
                const HRESULT got=keys[i]->AcquireSync(1,100);CHECK(got==S_OK);CHECK(SUCCEEDED(keys[i]->ReleaseSync(0)));
                gc_receipt r{};r.accepted=1;r.token=b.token;r.occurrence=b.occurrence;r.encode_serial=b.encode_serial;
                r.nonce_lo=h.nonce_lo;r.nonce_hi=h.nonce_hi;r.epoch=h.epoch;r.generation=h.generation;r.texture_index=i;
                put(reinterpret_cast<uint8_t*>(&twin()->receipts[i]),&r,sizeof r);done[i]=b.token;returned.fetch_add(1);
            }
            if(stopping.load()){
                if(!stop_started){stop_started=GetTickCount64();request(0);}
                last=status();
                if(last.state==GC_TWIN_STOPPED||GetTickCount64()-stop_started>deadline_ms)break;
            }
            Sleep(1);
        }
        for(auto share:shares)if(share)CloseHandle(share);
        stopped.store(true);
    }
    void stop(){stopping.store(true);thread.join();UnmapViewOfFile(view);CloseHandle(mapping);}
};
TwinHelper *twin_helper=nullptr;
uint64_t started_nonce=0;
void percentiles(const double *values,unsigned n,double out[3]){
    std::vector<double> sorted(values,values+n);std::sort(sorted.begin(),sorted.end());
    out[0]=n?sorted[n/2]:0;out[1]=n?sorted[std::min<size_t>(n-1,size_t(n*0.99))]:0;out[2]=n?sorted[n-1]:0;
}
void original_update(const rs_request *request) {
    render_command=request?*request:rs_request{};
    CHECK(SetEvent(render_request));
    CHECK(WaitForSingleObject(render_done,5000)==WAIT_OBJECT_0);
}
void passive_update(){original_update(nullptr);}
void process_list(){}
uint32_t current_epoch(void*){return gd_epoch();}
uint32_t vi_origin(void*){return origin;}
int read_ram(void*,uint32_t offset,uint32_t length,uint8_t *out) {
    memset(out,0,length);
    if(offset==0&&length==4)memcpy(out,&marker,sizeof marker);
    else if(offset==8&&length==8){out[0]=uint8_t(255-marker);out[4]=0xA5;}
    else return 0;
    return 1;
}
int configure_source(const rs_callbacks *c){return rb_configure_images(c->capture,c->completed);}
void surface(void*,const rb_record *r){gd_surface(r);}
int capture(void*,const rb_record *r,rb_image *i){return gd_capture(r,i);}
int completed(void*,const rb_image *i){return gd_completed(i);}
bool begin(){return glGetError()==GL_NO_ERROR;}
bool end(){return glGetError()==GL_NO_ERROR;}
int acquire(uintptr_t c,uint32_t g,rcl_lease *lease){return registry.acquire(c,g,lease);}
int release(rcl_token token){return registry.release(token);}
uint32_t health(){return registry.health();}
uint32_t error(){return registry.error();}
void frame(unsigned value){std::lock_guard<std::mutex> hold(vi_lock);marker=value;sa_process_dlist();sa_update_screen();}
gd_status status() {
    gd_status result{};
    for(unsigned n=0;n<1000;++n){if(gd_read_status(&result))return result;Sleep(1);}
    CHECK(false);return result;
}
bool wait_state(unsigned target,bool mapping=false) {
    for(unsigned n=0;n<4000;++n){const auto s=status();
        if(s.state==target&&(!mapping||s.mapping_bytes))return true;
        if(s.quarantined)return false;Sleep(1);}
    return false;
}
// The ended request's GPU names stay until the renderer's next armed VI
// deletes them (renderer-thread capture): settled means no work, not no bytes.
bool settled() {
    for(unsigned n=0;n<4000;++n){const auto s=status();
        if(!gd_epoch()&&!s.epoch&&!s.quarantined&&!s.pending_records&&!s.pending_bridges)return true;
        if(s.quarantined)return false;Sleep(1);}
    return false;
}
}

extern "C" const rs_api *__cdecl SM64ReplaySourceV2(uint32_t version,uint32_t bytes) {
    return version==RS_ABI_V2&&bytes==sizeof(rs_api)?&source:nullptr;
}

int main(int argc,char **argv) {
    CHECK(argc==3);
    const auto owner=uint32_t(strtoul(argv[1],nullptr,10));
    const auto birth=strtoull(argv[2],nullptr,10);
    WNDCLASSW wc{};wc.style=CS_OWNDC;wc.lpfnWndProc=DefWindowProcW;
    wc.hInstance=GetModuleHandleW(nullptr);wc.lpszClassName=L"SnapshotHiddenHost";
    CHECK(RegisterClassW(&wc));Window window;producer=&window;
    CHECK(SetWindowPos(window.hwnd,nullptr,0,0,width,height,SWP_NOACTIVATE|SWP_NOMOVE|SWP_NOZORDER));
    CHECK(!IsWindowVisible(window.hwnd)&&wglMakeCurrent(window.dc,window.rc));
    glDisable(GL_DITHER);glDisable(0x8DB9);glReadBuffer(GL_BACK);
    render_request=CreateEventW(nullptr,FALSE,FALSE,nullptr);
    render_done=CreateEventW(nullptr,FALSE,FALSE,nullptr);
    CHECK(render_request&&render_done&&wglMakeCurrent(nullptr,nullptr));
    std::thread renderer([]{
        CHECK(wglMakeCurrent(producer->dc,producer->rc));
        CHECK(SetEvent(render_done));
        for(;;){
            CHECK(WaitForSingleObject(render_request,INFINITE)==WAIT_OBJECT_0);
            render_update(&render_command);CHECK(SetEvent(render_done));
        }
    });
    CHECK(WaitForSingleObject(render_done,5000)==WAIT_OBJECT_0);
    source={sizeof(rs_api),RS_ABI_V2,RS_CAP_BOUNDARY|RS_CAP_SOURCE_FORMAT,0,
        configure_source,rb_stage,original_update,rb_finish,rb_activate,rb_disarm,
        rb_take,rb_release,rb_get_stats,begin,end};
    const sa_ops adapters{sizeof(sa_ops),SA_VERSION,nullptr,process_list,passive_update,
        current_epoch,vi_origin,read_ram,surface,capture,completed};
    twin_mode=GetEnvironmentVariableA("SM64_CADENCE_TWIN",nullptr,0)!=0;
    CHECK(sa_configure(GetModuleHandleW(nullptr),&adapters));
    CHECK(twin_mode?rb_bind_source3(source_surface3):rb_bind_source(source_surface));
    const rcl_api contexts{sizeof(rcl_api),RCL_ABI_V1,RCL_CAP_NEVER_CURRENT_ANCHOR,0,
        acquire,release,health,error};
    const gd_limits limits{sizeof(gd_limits),GD_VERSION,8,2000,128u<<20,128u<<20,4u<<20,1u<<20,2,0};
    const rs_api_v3 source3{sizeof(rs_api_v3),RS_ABI_V3,RS_CAP_BOUNDARY|RS_CAP_SOURCE_FORMAT|RS_CAP_CLEAN_PRESENTATION,0,
        source,rb_read_source_descriptor3,clean_consumer};
    const rs_api_v4 source4{sizeof(rs_api_v4),RS_ABI_V4,
        RS_CAP_BOUNDARY|RS_CAP_SOURCE_FORMAT|RS_CAP_CLEAN_PRESENTATION|RS_CAP_DISPLAY_COPY,0,source3,query_display,display_consumer};
    CHECK((twin_mode?gd_configure4(&source4,&contexts,&limits):gd_configure(&source,&contexts,&limits))==GD_ACCEPTED);rb_rom_open();
    gc_header request{};request.owner_pid=owner;request.owner_birth=birth;request.generation=1;
    request.offer_count=8;request.table_count=2;request.table[0]={0,4};request.table[1]={8,8};
    request.packet_count=8;request.packet_bytes=1u<<20;request.pending_bytes=4u<<20;request.ram_budget=4u<<20;
    std::thread([]{for(;;){Sleep(16);
        if(!vi_clock.load() || !gd_epoch())continue;
        std::lock_guard<std::mutex> hold(vi_lock);
        if(!vi_clock.load())continue;
        fresh_picture=false;sa_update_screen();fresh_picture=true;
    }}).detach();
    puts("ready");fflush(stdout);std::string line;
    while(std::getline(std::cin,line)) {
        std::istringstream input(line);std::string command;input>>command;
        if(command=="fixture") {
            unsigned presentation=0;input>>width>>height>>presentation;
            CHECK(width>=320&&width<=1600&&height>=240&&height<=1200&&presentation<=1);
            CHECK(SetWindowPos(window.hwnd,nullptr,0,0,width,height,SWP_NOACTIVATE|SWP_NOMOVE|SWP_NOZORDER));
            CHECK(!IsWindowVisible(window.hwnd));swap_presentation=presentation!=0;
            puts("fixture configured");
        } else if(command=="start") {
            input>>request.nonce_lo;request.nonce_hi=GetCurrentProcessId();started_nonce=request.nonce_lo;
            CHECK(gd_request(&request,uint64_t(width)*height*4*8)==GD_ACCEPTED);frame(0);
            CHECK(wait_state(GD_PREPARING,true));
            printf("prepared %u %u %llu %llu\n",gd_epoch(),GetCurrentProcessId(),request.nonce_lo,request.nonce_hi);
        } else if(command=="active") {CHECK(wait_state(GD_ACTIVE));puts("active");
        } else if(command=="stream") {
            unsigned frames=0,period=0;input>>frames>>period;
            CHECK(frames>0&&frames<=200&&period>0&&period<=50);
            // The VI clock keeps running: Project64 calls UpdateScreen on every
            // VI, so same-origin VIs fall between these pictures as in play.
            const auto started=GetTickCount64();unsigned pictures=0;puts("stream started");fflush(stdout);
            // Python can consume meanwhile; it cannot pace this producer by
            // waiting for each offer, encode result, or metadata acknowledgment.
            for(unsigned i=0;i<frames;++i){
                const auto due=started+uint64_t(i)*period;
                while(GetTickCount64()<due)Sleep(1);
                frame(30+i);++pictures;
            }
            const auto counters=rb_get_stats();
            printf("stream done %u %u %u %llu pool_full=%u\n",pictures,counters.refused_full,counters.refused_busy,GetTickCount64()-started,counters.surface_failed);
        } else if(command=="stream_vi") {
            unsigned count=0,hz=0,divisor=0;input>>count>>hz>>divisor;
            CHECK(count>0&&count<=1200&&hz>0&&hz<=120&&divisor>0&&divisor<=4);
            // A requested twin lane starts on the VI clock's idle ticks first,
            // so its one-time preparation is not among the measured VIs.
            if(twin_helper)for(unsigned n=0;n<2000&&twin_helper->status().state!=GC_TWIN_ACTIVE;++n)Sleep(1);
            const auto before=updates,started=GetTickCount64();
            uint64_t first_refusal=0;gd_status first{};uint32_t max_records=0,max_bridges=0;
            vi_clock.store(false);
            {std::lock_guard<std::mutex> idle(vi_lock);timed.store(0);timing.store(true);} // after the clock's last idle VI
            puts("stream started");fflush(stdout);
            for(unsigned i=0;i<count;++i){
                const auto due=started+uint64_t(i)*1000/hz;
                while(GetTickCount64()<due)Sleep(1);
                std::lock_guard<std::mutex> hold(vi_lock);
                fresh_picture=i%divisor==0;
                if(fresh_picture){marker=30+i/divisor;sa_process_dlist();}
                sa_update_screen();
                const auto counters=rb_get_stats();gd_status current{};
                if(gd_read_status(&current)){
                    if(current.pending_records>max_records)max_records=current.pending_records;
                    if(current.pending_bridges>max_bridges)max_bridges=current.pending_bridges;
                }
                if(!first_refusal&&(counters.refused_full||counters.refused_busy)){
                    first_refusal=GetTickCount64()-started;first=current;
                }
            }
            timing.store(false);
            fresh_picture=true;const auto counters=rb_get_stats();const auto streamed=updates-before;vi_clock.store(true);
            double vi[3],capture[3];percentiles(vi_us,timed.load(),vi);percentiles(capture_us,timed.load(),capture);
            gc_twin_status twin{};if(twin_helper)twin=twin_helper->status();
            printf("stream done %llu %u %u %llu first_ms=%llu records=%u bridges=%u reason=%u peak_records=%u peak_bridges=%u pool_full=%u"
                " timed=%u vi_us=%.1f,%.1f,%.1f capture_us=%.1f,%.1f,%.1f twin=%u,%llu,%llu\n",
                streamed,counters.refused_full,counters.refused_busy,GetTickCount64()-started,
                first_refusal,first.pending_records,first.pending_bridges,first.reason,max_records,max_bridges,counters.surface_failed,
                timed.load(),vi[0],vi[1],vi[2],capture[0],capture[1],capture[2],twin.state,twin.copied,twin.missed);
        } else if(command=="twin") {
            std::string state;input>>state;CHECK(twin_mode&&(state=="on"||state=="off"));
            if(state=="on"){CHECK(!twin_helper);twin_helper=new TwinHelper;twin_helper->start(started_nonce,GetCurrentProcessId());puts("twin requested");}
            else {CHECK(twin_helper);twin_helper->stop();const auto s=twin_helper->last;
                printf("twin stopped state=%u reason=%u copied=%llu missed=%llu returned=%llu\n",s.state,s.reason,s.copied,s.missed,
                    twin_helper->returned.load());delete twin_helper;twin_helper=nullptr;}
        } else if(command=="status") {
            const auto s=status();printf("status %u %u %u %u %u %u\n",s.state,s.reason,s.error,s.pending_records,s.pending_bridges,s.quarantined);
        } else if(command=="stop") {gd_disarm(GD_CANCELLED);puts("stopped");
        } else if(command=="settled") {CHECK(settled());puts("settled");
        } else if(command=="exit") {
            gd_disarm(GD_CANCELLED);CHECK(settled());puts("exit");fflush(stdout);ExitProcess(0);
        } else CHECK(false);
        fflush(stdout);
    }
    return 1;
}
