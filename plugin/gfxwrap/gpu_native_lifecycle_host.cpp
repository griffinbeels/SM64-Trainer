/* CPU decision witness: real delivery/control/source-boundary code, no GL
 * context, images, encoder, lease worker or emulator. Frontier/quiescence are
 * deliberately injected; this does not reproduce actual source saturation. */
#include "gpu_delivery.cpp"
#include "runtime_control.h"
#include "runtime_delivery.h"
#include "gpu_request.h"
#include <cstdio>
#include <string>
#define REQUIRE(x) do{if(!(x)){fprintf(stderr,"native lifecycle line %d: %s\n",__LINE__,#x);return false;}}while(0)
namespace lifecycle_fixture {
bool quiet=true,frontier_enabled=false;
uint32_t frontier_epoch=0,frontier_refusals=0,activations=0;
uint32_t __cdecl activate(){++activations;return rb_activate();}
int __cdecl source_surface(rb_surface*s){*s={};s->boundary_qpc=1;return 1;}
struct Fixture {
    Runtime r;
    Fixture(){
        r.wake=CreateEventW(nullptr,FALSE,FALSE,nullptr);
        r.changed=CreateEventW(nullptr,FALSE,FALSE,nullptr);
        r.source.activate=activate;r.source.disarm=rb_disarm;
        r.source.take=rb_take;r.source.release=rb_release;
        r.limits={sizeof(gd_limits),GD_VERSION,8,2000,4u<<20,4u<<20,1u<<20,1u<<20,2,0};
        LARGE_INTEGER f{};QueryPerformanceFrequency(&f);r.frequency=uint64_t(f.QuadPart);
        rb_configure_images(gd_capture,gd_completed);rb_bind_source(source_surface);rb_rom_open();
        runtime.store(&r);
    }
    ~Fixture(){runtime.store(nullptr);rb_rom_closed();r.channel.reset();CloseHandle(r.wake);CloseHandle(r.changed);}
    gc_header request(uint64_t nonce)const{
        gc_header h{};h.owner_pid=GetCurrentProcessId();h.owner_birth=gpu_channel::process_birth(GetCurrentProcess());
        h.nonce_lo=nonce;h.nonce_hi=GetCurrentProcessId();h.generation=1;
        h.offer_count=8;h.table_count=2;h.table[0]={0,4};h.table[1]={8,8};
        h.packet_count=8;h.packet_bytes=1024;h.pending_bytes=4096;h.ram_budget=1u<<20;return h;
    }
    bool start(uint64_t nonce){auto h=request(nonce);return gd_request(&h,4u<<20)==GD_ACCEPTED;}
    bool channel(){
        auto h=r.request;h.width=h.height=2;h.epoch=r.epoch;h.qpc_frequency=r.frequency;
        h.texture_names[0][0]='A';h.texture_names[1][0]='B';
        r.channel=std::make_unique<gpu_channel::Producer>();
        return r.channel->create(h)==gpu_channel::Result::ok;
    }
    bool no_graphics(){return !wglGetCurrentContext() && !r.prepared && !r.bridge_attempted
        && !r.sampler_attempted && !r.snapshots.images().counters().texture_bytes && !r.bridge.logical_bytes();}
};
struct ControlRequest {
    control_page_t page{};control_request_t ctl{};gr_page config{};HANDLE mapping=nullptr;
    ControlRequest(const gc_header&h,uint32_t token){
        const auto birth=gpu_channel::process_birth(GetCurrentProcess());
        page.producer_pid=GetCurrentProcessId();page.generation=h.generation;page.rom_open=1;
        page.producer_created_lo=uint32_t(birth);page.producer_created_hi=uint32_t(birth>>32);
        ctl.token=token;ctl.owner_pid=h.owner_pid;ctl.created_lo=uint32_t(h.owner_birth);
        ctl.created_hi=uint32_t(h.owner_birth>>32);ctl.generation=h.generation;ctl.enabled=ctl.heartbeat=1;
        memcpy(config.magic,GR_MAGIC,8);config.version=GR_VERSION;config.bytes=GR_BYTES;config.seq=2;
        config.producer_pid=page.producer_pid;config.owner_pid=h.owner_pid;config.control_generation=h.generation;
        config.token=token;config.producer_birth=birth;config.owner_birth=h.owner_birth;
        config.nonce_lo=token;config.nonce_hi=page.producer_pid;config.snapshot_budget=4u<<20;
        config.slots=8;config.rdram_bytes=8u<<20;config.table_count=2;config.table[0]={0,4};config.table[1]={8,8};
        config.channel_budget=1u<<20;config.packet_count=8;config.packet_bytes=1024;config.pending_bytes=4096;
        config.pcm_bytes=1024;config.pcm_blocks=4;config.max_age_ms=2000;config.encoder_duration=3000;
        const gr_identity identity{page.producer_pid,h.owner_pid,h.generation,token,birth,h.owner_birth};
        wchar_t name[128]{};if(!gr_name(identity,name,128))return;
        mapping=CreateFileMappingW(INVALID_HANDLE_VALUE,nullptr,PAGE_READWRITE,0,GR_BYTES,name);
        if(!mapping||GetLastError()==ERROR_ALREADY_EXISTS)return;
        void*view=MapViewOfFile(mapping,FILE_MAP_ALL_ACCESS,0,0,GR_BYTES);if(!view)return;
        memcpy(view,&config,sizeof(config));MemoryBarrier();UnmapViewOfFile(view);
    }
    ~ControlRequest(){if(mapping)CloseHandle(mapping);}
    void demand(uint32_t&state,uint32_t&why){rc_demand(&page,&ctl,&state,&why);}
};
bool recovery(const std::string&mode){
    Fixture f;REQUIRE(f.start(100));const auto old_epoch=f.r.epoch;
    REQUIRE(rc_bind_delivery(&f.r.limits));rc_rom(TRUE);
    // The real worker cannot release this old request before the source
    // transaction proves quiescence. No fake gd_request/status/disarm is used.
    quiet=false;f.r.fail(GD_SOURCE_GAP);REQUIRE(!f.r.drain_tick());
    REQUIRE(f.r.state.load()==GD_DRAINING && f.r.request_state.load()==2);
    // The production worker owns state 3 while draining.
    f.r.request_state.store(3);
    ControlRequest next(f.request(200),200);uint32_t state=0,why=0;
    for(unsigned i=0;i<3;++i){next.demand(state,why);REQUIRE(state==CONTROL_PREPARING && why==0);}
    REQUIRE(activations==1 && !gd_epoch() && f.r.request.nonce_lo==100 && f.no_graphics());
    if(mode=="cancel-deferred")rc_revoke(CONTROL_LEASE_EXPIRED);
    else if(mode=="rom-deferred"){rc_rom(FALSE);rc_rom(TRUE);}
    else if(mode=="quarantine-deferred"){
        f.r.quarantine=true;REQUIRE(!f.r.drain_tick());REQUIRE(f.r.exhausted.load());
        next.demand(state,why);REQUIRE(state==CONTROL_UNAVAILABLE && why==CONTROL_BACKEND_EXHAUSTED);
        REQUIRE(activations==1 && !gd_epoch() && f.no_graphics());return true;
    }
    quiet=true;REQUIRE(f.r.drain_tick());REQUIRE(f.r.request_state.load()==0);
    next.demand(state,why);
    if(mode!="recover"){
        REQUIRE(state==CONTROL_UNAVAILABLE && activations==1 && !gd_epoch());
        ControlRequest fresh(f.request(201),201);fresh.demand(state,why);
    }
    REQUIRE(state==CONTROL_PREPARING && why==0 && f.r.state.load()==GD_BOOTSTRAP);
    REQUIRE(activations==2 && f.r.epoch!=old_epoch && gd_epoch()==f.r.epoch && f.no_graphics());
    REQUIRE(f.r.first_failure.load()==GD_NONE && f.r.error==0);
    if(mode=="recover"){
        REQUIRE(f.r.request.nonce_lo==200);
        for(unsigned i=0;i<3;++i){next.demand(state,why);REQUIRE(state==CONTROL_PREPARING && why==0);}
        REQUIRE(activations==2);
    }
    gd_disarm(GD_CANCELLED);REQUIRE(f.r.drain_tick());return true;
}
bool bootstrap(){
    Fixture f;REQUIRE(f.start(301));f.r.set_state(GD_PREPARING);
    rb_stamp stamp{};stamp.table_count=2;stamp.lengths[0]=4;stamp.lengths[1]=8;
    const auto ticket=rb_stage(&stamp);REQUIRE(ticket.occurrence);
    const auto begun=rb_source_begin(ticket);REQUIRE(begun.occurrence==ticket.occurrence);
    rb_source_end(begun);rb_finish(ticket);
    // The actual callback read PREPARING. Deliver its record only after ACTIVE.
    f.r.set_state(GD_ACTIVE);f.r.take_records(false);
    REQUIRE(f.r.live() && f.r.diagnostic_reason()==GD_NONE && !f.r.count());
    REQUIRE(f.r.accounted==ticket.occurrence && f.no_graphics());
    gd_disarm(GD_CANCELLED);REQUIRE(f.r.drain_tick());return true;
}
bool gap(const std::string&mode){
    Fixture f;REQUIRE(f.start(401));f.r.set_state(GD_ACTIVE);
    if(mode=="image-gap"){
        rb_record record{};record.epoch=f.r.epoch;record.slot=0;record.occurrence=1;
        record.outcome=RB_SURFACE_FAILED;record.image.status=47;
        static const rb_record*next=nullptr;next=&record;
        f.r.source.take=[]()->const rb_record*{const auto value=next;next=nullptr;return value;};
        f.r.take_records(false);
        REQUIRE(f.r.diagnostic_reason()==10016 && f.r.error==47);
    }else if(mode=="record-gap"){
        rb_record record{};record.epoch=f.r.epoch+1;record.occurrence=1;
        record.outcome=RB_OBSERVED;record.image.ownership=RB_IMAGE_SUBMITTED;
        f.r.pending[0].record=&record;f.r.sample_head();
        REQUIRE(f.r.diagnostic_reason()==10017);
    }else if(mode=="refusal-gap"){
        // Refused stages are counted missing pictures, never a run failure
        // (round 48): the run stays live and the summary carries the count.
        REQUIRE(f.channel());frontier_enabled=true;frontier_epoch=f.r.epoch;frontier_refusals=1;
        f.r.active_tick();
        REQUIRE(f.r.live() && f.r.diagnostic_reason()==GD_NONE && f.r.refused_pictures==1 && f.r.refusal_generation==1);
        frontier_refusals=3;f.r.active_tick();
        REQUIRE(f.r.live() && f.r.refused_pictures==3 && f.r.diagnostic_reason()==GD_NONE);
        gd_disarm(GD_CANCELLED);REQUIRE(f.r.drain_tick() && f.no_graphics());return true;
    }else{
        REQUIRE(f.channel());frontier_enabled=true;frontier_epoch=f.r.epoch;++frontier_epoch;
        f.r.active_tick();REQUIRE(f.r.diagnostic_reason()==10021u);
    }
    const auto first=f.r.diagnostic_reason();gd_disarm(GD_CANCELLED);f.r.publish_status();
    gd_status retained{};REQUIRE(gd_read_status(&retained));
    REQUIRE(retained.reason==first && !gd_epoch() && f.no_graphics());return true;
}
}
extern "C" int __cdecl sa_publish_table(const sa_table*t){return t&&t->epoch!=0;}
extern "C" void __cdecl sa_arm_refusal(uint32_t){}
extern "C" int __cdecl sa_probe_quiescent(uint64_t through,sa_frontier*out){
    if(!lifecycle_fixture::quiet||gd_epoch())return 0;*out={0,0,0,0,through,int64_t(qpc())};return 1;
}
extern "C" int __cdecl sa_probe_frontier(uint64_t through,sa_frontier*out){
    if(!lifecycle_fixture::frontier_enabled)return 0;
    *out={0,lifecycle_fixture::frontier_epoch,lifecycle_fixture::frontier_refusals,0,through,int64_t(qpc())};return 1;
}
int main(int argc,char**argv){
    if(argc!=2)return 2;const std::string mode=argv[1];bool passed=false;
    if(mode=="bootstrap")passed=lifecycle_fixture::bootstrap();
    else if(mode=="recover"||mode=="cancel-deferred"||mode=="rom-deferred"||mode=="quarantine-deferred")passed=lifecycle_fixture::recovery(mode);
    else if(mode=="image-gap"||mode=="record-gap"||mode=="epoch-gap"||mode=="refusal-gap")passed=lifecycle_fixture::gap(mode);
    if(!passed)return 1;printf("native lifecycle passed: %s\n",mode.c_str());return 0;
}
