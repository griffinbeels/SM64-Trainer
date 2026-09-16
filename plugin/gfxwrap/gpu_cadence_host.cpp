/* Hidden autonomous source fixture. Links the real stamp adapter/frontier;
 * no emulator, machine-wide capture request, or installed plugin is opened. */
#include "gpu_delivery.h"
#include "stamp_adapter.h"
#include "gl_fixture.h"
#include <iostream>
#include <sstream>

namespace {
unsigned width=320, height=240;
Window *producer=nullptr;
source_context::Registry registry;
rs_api source{};
uint64_t updates=0;
unsigned marker=0, origin=0;
bool fresh_picture=true, swap_presentation=false;
HANDLE render_request=nullptr, render_done=nullptr;
rs_request render_command{};

int source_surface(rb_surface *s) {
    *s={};s->width=width;s->height=height;s->drawable_width=width;s->drawable_height=height;
    s->context=reinterpret_cast<uintptr_t>(producer->rc);
    s->read_drawable=reinterpret_cast<uintptr_t>(producer->dc);
    s->renderer_thread=GetCurrentThreadId();s->context_generation=s->drawable_generation=1;
    s->restore_read_buffer=GL_BACK;s->source_format=RB_SOURCE_RGB8_LINEAR;s->post_vi_origin=origin;
    LARGE_INTEGER qpc{};QueryPerformanceCounter(&qpc);s->boundary_qpc=qpc.QuadPart;return 1;
}
void render_update(const rs_request *request) {
    ++updates;
    const auto ticket=request?rb_source_begin(request->ticket):rb_ticket{};
    CHECK(wglGetCurrentContext()==producer->rc);
    if(fresh_picture){
        glDrawBuffer(swap_presentation?GL_BACK:GL_FRONT);glEnable(GL_SCISSOR_TEST);glScissor(0,0,width,height);
        glClearColor((marker%251)/250.f,((marker*7+marker/251*31)%251)/250.f,((marker*17)%251)/250.f,1.f);
        glClear(GL_COLOR_BUFFER_BIT);glDrawBuffer(GL_BACK);
        if(swap_presentation)CHECK(SwapBuffers(producer->dc));
        ++origin;
    }
    rb_source_end(ticket);
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
void frame(unsigned value){marker=value;sa_process_dlist();sa_update_screen();}
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
bool settled() {
    for(unsigned n=0;n<4000;++n){const auto s=status();
        if(!gd_epoch()&&!s.epoch&&!s.quarantined&&!s.snapshot_bytes&&!s.bridge_bytes
            &&!s.pending_records&&!s.pending_bridges)return true;
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
    using Create=HGLRC(WINAPI*)(HDC,HGLRC,const int*);
    const auto create=proc<Create>("wglCreateContextAttribsARB");
    const int attributes[]={0x2091,4,0x2092,5,0x9126,2,0};
    CHECK(registry.publish(window.rc,1,GetPixelFormat(window.dc),create,window.dc,attributes));
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
    CHECK(sa_configure(GetModuleHandleW(nullptr),&adapters));CHECK(rb_bind_source(source_surface));
    const rcl_api contexts{sizeof(rcl_api),RCL_ABI_V1,RCL_CAP_NEVER_CURRENT_ANCHOR,0,
        acquire,release,health,error};
    const gd_limits limits{sizeof(gd_limits),GD_VERSION,8,2000,128u<<20,128u<<20,4u<<20,1u<<20,2,0};
    CHECK(gd_configure(&source,&contexts,&limits)==GD_ACCEPTED);rb_rom_open();
    gc_header request{};request.owner_pid=owner;request.owner_birth=birth;request.generation=1;
    request.offer_count=8;request.table_count=2;request.table[0]={0,4};request.table[1]={8,8};
    request.packet_count=8;request.packet_bytes=1u<<20;request.pending_bytes=4u<<20;request.ram_budget=4u<<20;
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
            input>>request.nonce_lo;request.nonce_hi=GetCurrentProcessId();
            CHECK(gd_request(&request,uint64_t(width)*height*4*8)==GD_ACCEPTED);frame(0);
            CHECK(wait_state(GD_PREPARING,true));
            printf("prepared %u %u %llu %llu\n",gd_epoch(),GetCurrentProcessId(),request.nonce_lo,request.nonce_hi);
        } else if(command=="active") {CHECK(wait_state(GD_ACTIVE));puts("active");
        } else if(command=="stream") {
            unsigned frames=0,period=0;input>>frames>>period;
            CHECK(frames>0&&frames<=200&&period>0&&period<=50);
            const auto before=updates,started=GetTickCount64();puts("stream started");fflush(stdout);
            // Python can consume meanwhile; it cannot pace this producer by
            // waiting for each offer, encode result, or metadata acknowledgment.
            for(unsigned i=0;i<frames;++i){
                const auto due=started+uint64_t(i)*period;
                while(GetTickCount64()<due)Sleep(1);
                frame(30+i);
            }
            const auto counters=rb_get_stats();
            printf("stream done %llu %u %u %llu\n",updates-before,counters.refused_full,counters.refused_busy,GetTickCount64()-started);
        } else if(command=="stream_vi") {
            unsigned count=0,hz=0,divisor=0;input>>count>>hz>>divisor;
            CHECK(count>0&&count<=1200&&hz>0&&hz<=120&&divisor>0&&divisor<=4);
            const auto before=updates,started=GetTickCount64();
            uint64_t first_refusal=0;gd_status first{};uint32_t max_records=0,max_bridges=0;
            puts("stream started");fflush(stdout);
            for(unsigned i=0;i<count;++i){
                const auto due=started+uint64_t(i)*1000/hz;
                while(GetTickCount64()<due)Sleep(1);
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
            fresh_picture=true;const auto counters=rb_get_stats();
            printf("stream done %llu %u %u %llu first_ms=%llu records=%u bridges=%u reason=%u peak_records=%u peak_bridges=%u\n",
                updates-before,counters.refused_full,counters.refused_busy,GetTickCount64()-started,
                first_refusal,first.pending_records,first.pending_bridges,first.reason,max_records,max_bridges);
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
