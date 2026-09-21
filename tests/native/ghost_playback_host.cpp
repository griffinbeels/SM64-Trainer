/* Playback ownership/timeline witness. Actual playback, stub model and pose
 * conversion: real geometry has separate model/pose witnesses. No windows. */
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "ghost_playback.h"
#include "ghost_pose.h"
#include <atomic>
#include <condition_variable>
#include <cstdlib>
#include <cstring>
#include <cstdio>
#include <functional>
#include <limits>
#include <mutex>
#include <new>
#include <stdexcept>
#include <thread>
#include <vector>

static thread_local bool rendering=false;
static std::atomic<unsigned> allocations{0},destructions{0};
void* operator new(size_t n) {
    if(rendering)allocations.fetch_add(1);
    if(void *p=std::malloc(n?n:1))return p;
    throw std::bad_alloc();
}
void* operator new[](size_t n) {return ::operator new(n);}
void operator delete(void *p) noexcept {if(rendering && p)destructions.fetch_add(1);std::free(p);}
void operator delete[](void *p) noexcept {::operator delete(p);}
void operator delete(void *p,size_t) noexcept {::operator delete(p);}
void operator delete[](void *p,size_t) noexcept {::operator delete(p);}
static unsigned checks=0;
static void check(bool ok,const char *message) {++checks;if(!ok)throw std::runtime_error(message);}
struct RenderScope {RenderScope(){rendering=true;}~RenderScope(){rendering=false;}};
class Renderer {
    std::mutex mutex;
    std::condition_variable wake,done;
    std::function<void()> job;
    bool pending=false,stopping=false;
    std::exception_ptr failed;
    std::thread thread;
public:
    Renderer():thread([&] {
        std::unique_lock<std::mutex> lock(mutex);
        for(;;) {
            wake.wait(lock,[&]{return pending || stopping;});
            if(stopping)return;
            auto operation=std::move(job);lock.unlock();
            try {RenderScope scope;operation();}catch(...){failed=std::current_exception();}
            lock.lock();pending=false;done.notify_one();
        }
    }) {}
    ~Renderer() {{std::lock_guard<std::mutex> lock(mutex);stopping=true;}wake.notify_one();thread.join();}
    void run(std::function<void()> operation) {
        std::unique_lock<std::mutex> lock(mutex);job=std::move(operation);pending=true;wake.notify_one();
        done.wait(lock,[&]{return !pending;});
        if(failed)std::rethrow_exception(failed);
    }
};

struct sg_model {DWORD owner;};
static sg_model *fake_model=nullptr;
static unsigned opens=0,closes=0;
static DWORD last_close=0;
static bool refuse_adoption=false,refuse_close=false,revoke_evaluation=false;
static uint64_t selected_sequence=0;
static uint32_t selected_world=0;
static float selected_x=0;
extern "C" int32_t sg_model_open(const uint8_t*,size_t,sg_model **out) {
    if(fake_model)return SG_BUSY;
    *out=fake_model=new sg_model{GetCurrentThreadId()};++opens;return SG_OK;
}
extern "C" int32_t sg_model_detach(sg_model *p) {
    if(p!=fake_model || !p)return SG_INVALID_MODEL;
    if(p->owner!=GetCurrentThreadId())return SG_WRONG_THREAD;
    p->owner=0;return SG_OK;
}
extern "C" int32_t sg_model_adopt(sg_model *p) {
    if(refuse_adoption)return SG_BUSY;
    if(p!=fake_model || !p)return SG_INVALID_MODEL;
    if(p->owner)return SG_WRONG_THREAD;
    p->owner=GetCurrentThreadId();return SG_OK;
}
extern "C" int32_t sg_model_close(sg_model **p) {
    if(!p || !*p || *p!=fake_model)return SG_INVALID_MODEL;
    if((*p)->owner!=GetCurrentThreadId())return SG_WRONG_THREAD;
    if(refuse_close)return SG_BUSY;
    last_close=GetCurrentThreadId();delete *p;*p=fake_model=nullptr;++closes;return SG_OK;
}
extern "C" int32_t sg_model_get_atlas(sg_model *p,sg_atlas_view *out) {
    *out={};if(p!=fake_model || !p)return SG_INVALID_MODEL;
    if(p->owner!=GetCurrentThreadId())return SG_WRONG_THREAD;
    static const uint8_t pixel[]={255,0,0,255};*out={pixel,1,1,209};return SG_OK;
}
namespace ghost_pose {
Result evaluate(sg_model *p,const gs_record& recorded,const gc_sample& live,
                const ghost::WorldPass& world,float opacity,Scratch&,ghost_compositor::Pose& out) {
    out={};
    if(!p || p!=fake_model || p->owner!=GetCurrentThreadId())return Result::model_failed;
    if(!(live.valid_fields&GC_VALID_CAMERA))return Result::missing_pose;
    if(world.epoch!=live.epoch || world.list_serial!=live.dlist_serial || !world.context_generation)
        return Result::wrong_occurrence;
    selected_world=recorded.world.status;
    selected_sequence=recorded.sample.sequence;
    selected_x=recorded.sample.pose.position[0];
    if(revoke_evaluation)ghost_playback::api()->revoke(GP_DISABLED);
    static const ghost_compositor::Vertex triangle[3]{};
    out.vertices=triangle;out.count=recorded.sample.pose.hidden?0:3;out.opacity=opacity;return Result::ok;
}
}

struct Clip {
    gclip_header header{};
    std::vector<gclip_frame> frames;
    explicit Clip(uint8_t id,uint32_t count=8):frames(count) {
        std::memcpy(header.magic,GCLIP_MAGIC,8);header.version=GCLIP_VERSION;header.bytes=sizeof(header);
        header.record_bytes=sizeof(gclip_frame);header.record_count=count;header.rom_layout=GC_ROM_US;
        header.sample_version=GC_VERSION;header.configuration=2;header.epoch=1;header.clip_id[0]=id;
        header.qpc_frequency=10000000;header.utc_anchor=1;header.first_sequence=1;header.last_sequence=count;
        header.first_global_timer=100;header.last_global_timer=100+count-1;header.level=9;header.area=1;
        header.clock_kind=GCLIP_STAR_CLOCK;header.origin_global_timer=100;
        header.origin_sequence=1;header.origin_elapsed=1000;header.origin_continuity=23;
        for(uint32_t i=0;i<count;++i) {
            auto &f=frames[i];auto &s=f.sample;
            f.bytes=sizeof(f);f.version=GCLIP_VERSION;f.configuration=2;f.world_status=GCLIP_WORLD_READY;
            s.bytes=sizeof(s);s.version=GC_VERSION;s.epoch=1;s.sequence=s.dlist_serial=i+1;
            s.global_timer=100+i;s.igt=uint16_t(i/4);s.level=9;s.area=1;
            s.valid_fields=GC_VALID_CLOCK|GC_VALID_POSE|GC_VALID_ACTION|GC_VALID_WARP;
            s.live_action=1;s.status=GC_NO_CAMERA;s.pose.root_kind=GC_ROOT_TRS;
            s.pose.scale[0]=s.pose.scale[1]=s.pose.scale[2]=1;
        }
        if(count==8) {
            const uint16_t times[]={10,11,11,11,13,14,15,16};
            for(unsigned i=0;i<8;++i)frames[i].sample.igt=times[i];
            frames[1].sample.valid_fields&=~GC_VALID_POSE;frames[1].sample.status=GC_UNSUPPORTED_ROOT;
            frames[1].sample.pose={};
            frames[3].sample.valid_fields&=~GC_VALID_POSE;frames[3].sample.status=GC_BAD_POINTER;
            frames[3].sample.pose={};
            frames[2].sample.area=2;
            for(unsigned i=4;i<8;++i)frames[i].sample.area=2;
        }
        header.origin_raw=frames[0].sample.igt;
        for(auto& f:frames) {
            auto& s=f.sample;
            s.clock={GCC_RAW_VALID|GCC_ELAPSED_VALID,GCC_UNKNOWN_ORIGIN,GCC_STEADY,s.igt,
                0,0,7,1,1000u+s.igt-header.origin_raw,23};
        }
    }
};
static gp_request request(const Clip& clip,uint64_t revision) {
    gp_request r{};r.bytes=sizeof(r);r.version=GP_VERSION;r.revision=revision;r.enabled=1;
    r.owner_token=123;r.model_id=1;r.rom_generation=7;r.configuration=4;
    std::memcpy(r.clip_id,clip.header.clip_id,16);r.opacity=1;r.star_ends=1;
    r.star_action_count=1;r.star_actions[0]=0x1302;
    r.origin={GO_STAR,5,50,77,2000,7,0,0};return r;
}
int main() {
    try {
        const DWORD worker=GetCurrentThreadId();const auto *api=ghost_playback::api();
        check(api->bytes==sizeof(gp_api) && api->version==GP_VERSION,"API version");
        std::vector<uint8_t> rom(8u*1024u*1024u);
        Clip clip(1);
        for(auto& frame:clip.frames)
            frame.world_status=static_cast<uint32_t>(ghost::PassStatus::InvalidIdentity);
        check(api->prepare_model(1,rom.data(),uint32_t(rom.size()))==GP_OK,"worker prepares detached model");
        check(fake_model && !fake_model->owner,"prepared model detached");
        check(api->prepare_clip(&clip.header,clip.frames.data())==GP_OK,
            "worker indexes reusable poses despite historical framebuffer refusal");
        clip.frames[0].sample.pose.position[0]=99;
        check(api->prepare_clip(&clip.header,clip.frames.data())==GP_INVALID,"same clip ID cannot change its payload");
        auto r=request(clip,1);check(api->configure(&r)==GP_OK,"configure playback");
        gc_sample live=clip.frames[0].sample;live.epoch=5;live.sequence=live.dlist_serial=50;
        live.valid_fields=GC_VALID_CLOCK|GC_VALID_CAMERA|GC_VALID_ACTION|GC_VALID_WARP;live.live_action=1;
        ghost::WorldPass world{};world.epoch=5;world.context_generation=2;world.list_serial=50;
        ghost_compositor::Pose pose{};gp_draw_token token{};
        uint64_t source_generation=7,source_continuity=77,elapsed_base=2000;
        Renderer renderer;
        auto draw=[&](uint64_t time,bool expected,uint32_t why,uint64_t sequence=0,int area=1) {
            live.igt=uint16_t(time);live.area=int16_t(area);
            ++live.sequence;++live.dlist_serial;world.list_serial=live.dlist_serial;
            live.clock={GCC_RAW_VALID|GCC_ELAPSED_VALID,GCC_UNKNOWN_ORIGIN,GCC_STEADY,live.igt,
                0,0,source_generation,1,elapsed_base+time,source_continuity};
            renderer.run([&] {
                check(ghost_playback::evaluate(live,world,pose,token)==expected,"selection result");
                if(expected)check(selected_sequence==sequence && ghost_playback::current(token),"selected occurrence identity");
                else check(!pose.vertices && !pose.count && !token.revision,"refusal clears candidate");
            });
            gp_status status{};check(api->status(&status,sizeof(status))==GP_OK && status.decision==why,"selection reason");
        };
        renderer.run([&] {ghost_playback::safe_boundary(7,4);sg_atlas_view atlas{};uint64_t id=0;
            check(ghost_playback::atlas(&atlas,&id) && id==1 && atlas.rgba,"renderer adopted atlas");});
        draw(8,true,GP_BEFORE_START,1);draw(10,true,GP_READY,1);check(selected_x==0,"preloaded frame is an owned copy");
        check(selected_world==static_cast<uint32_t>(ghost::PassStatus::InvalidIdentity),
            "selected pose retains its historical renderer diagnostic");
        live.valid_fields&=~GC_VALID_CAMERA;draw(10,false,GP_POSE_REFUSED);
        live.valid_fields|=GC_VALID_CAMERA;world.context_generation=0;
        draw(10,false,GP_POSE_REFUSED);world.context_generation=2;
        draw(10,true,GP_READY,1);
        draw(11,false,GP_INVALID_FRAME);draw(11,false,GP_WRONG_SCENE,0,2);
        draw(12,false,GP_GAP);draw(13,false,GP_WRONG_SCENE);
        draw(13,true,GP_READY,5,2);draw(17,false,GP_AFTER_END);
        live.valid_fields&=~GC_VALID_ACTION;draw(10,false,GP_NO_CLOCK);live.valid_fields|=GC_VALID_ACTION;
        live.live_action=r.star_actions[0];draw(10,false,GP_STAR_ENDED);
        live.live_action=1;draw(15,false,GP_STAR_ENDED);draw(0,false,GP_STAR_ENDED);
        ++r.revision;r.opacity=0.25f;
        check(api->configure(&r)==GP_OK,"same-origin tuning revision");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});draw(10,false,GP_STAR_ENDED);
        ++r.revision;r.origin.sequence=live.sequence+1;r.origin.elapsed=elapsed_base=3000;
        r.nudge_frames=3;
        r.tint_enabled=1;r.hue_degrees=217;
        check(api->configure(&r)==GP_OK,"new exact origin resumes with nudge");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});
        draw(10,true,GP_READY,5,2);check(pose.opacity==0.25f,"opacity forwarded");
        check(pose.hue_degrees==217.0f,"hue forwarded");
        const auto displayed=token;api->withdraw();
        renderer.run([&]{check(!ghost_playback::current(displayed),"withdraw cancels candidate immediately");
            ghost_playback::safe_boundary(7,4);});
        gp_status suspended{};check(api->status(&suspended,sizeof(suspended))==GP_OK
            && suspended.model_state==GP_MODEL_RENDER_OWNED && suspended.clips_owned==1
            && opens==1 && closes==0,"withdraw preserves model and clip ownership");
        ++r.revision;check(api->configure(&r)==GP_OK,"resume withdrawn assets");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});draw(10,true,GP_READY,5,2);
        ++source_generation;draw(10,false,GP_STAR_ENDED);
        ++r.revision;r.opacity=1;check(api->configure(&r)==GP_OK,"tuning after generation change");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});draw(10,false,GP_STAR_ENDED);
        r.tint_enabled=0;
        ++r.revision;r.mode=GP_FIXED;r.origin={};r.seek_igt=14;check(api->configure(&r)==GP_OK,"fixed seek without live origin");
        ++source_continuity;live.epoch=world.epoch=6;
        live.valid_fields&=~(GC_VALID_ACTION|GC_VALID_WARP);
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});draw(0,true,GP_READY,6,2);
        check(pose.hue_degrees==0.0f,"disabled tint bypassed");
        check(api->configure(&r)==GP_INVALID,"stale revision rejected");
        const gp_draw_token old=token;
        renderer.run([&]{ghost_playback::safe_boundary(7,4);check(!ghost_playback::current(old),"list boundary revokes older draw");});
        draw(0,true,GP_READY,6,2);
        api->withdraw();
        check(api->prepare_model(2,rom.data(),uint32_t(rom.size()))==GP_DEFERRED,"withdrawn model replacement waits for retirement");
        renderer.run([&]{check(!ghost_playback::current(token),"replacement cancels displayed candidate");ghost_playback::safe_boundary(7,4);});
        refuse_adoption=true;check(api->collect()==GP_DEFERRED,"busy background adoption deferred");refuse_adoption=false;
        refuse_close=true;check(api->collect()==GP_DEFERRED,"busy close retains worker custody");refuse_close=false;
        check(api->collect()==GP_OK && closes==1 && last_close==worker,"worker retries actual close");
        check(api->prepare_model(2,rom.data(),uint32_t(rom.size()))==GP_OK,"replacement model prepared");
        ++r.revision;r.model_id=2;check(api->configure(&r)==GP_OK,"select replacement model");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});draw(0,true,GP_READY,6,2);
        revoke_evaluation=true;draw(0,false,GP_POSE_REFUSED,0,2);revoke_evaluation=false;
        check(api->release_worker()==GP_DEFERRED,"cannot exit worker with render-owned storage");
        renderer.run([&]{ghost_playback::retire_on_context_loss();});
        check(api->collect()==GP_OK,"collect after actual renderer retirement");
        gp_status status{};check(api->status(&status,sizeof(status))==GP_OK && status.model_state==GP_MODEL_EMPTY
            && !status.clips_ready && !status.clips_owned && !status.clips_retired && !status.resident_clip_bytes,"all storage reclaimed");
        check(api->release_worker()==GP_OK,"worker binding released");
        int replacement_result=GP_INVALID;
        std::thread replacement([&] {
            replacement_result=api->prepare_model(3,rom.data(),uint32_t(rom.size()));
            if(replacement_result==GP_OK) {api->revoke(GP_DISABLED);replacement_result=api->collect();}
            if(replacement_result==GP_OK)replacement_result=api->release_worker();
        });replacement.join();check(replacement_result==GP_OK,"new worker can bind and reclaim");
        auto bad=clip.header;bad.record_count=GCLIP_MAX_RECORDS+1;
        check(api->prepare_clip(&bad,clip.frames.data())==GP_INVALID,"record count bounded before reading");
        bad=clip.header;bad.first_sequence=2;
        check(api->prepare_clip(&bad,clip.frames.data())==GP_INVALID,"exact clip endpoints validated");
        auto invalid=clip.frames;invalid[0].sample.valid_fields=0;invalid[0].sample.pose={};
        check(api->prepare_clip(&clip.header,invalid.data())==GP_INVALID,"clock-invalid endpoint refused");
        invalid=clip.frames;invalid[6].sample.igt=1;
        check(api->prepare_clip(&clip.header,invalid.data())==GP_INVALID,"raw and derived counter mismatch refused");
        invalid=clip.frames;invalid[6].sample.clock.elapsed_frames=1001;
        check(api->prepare_clip(&clip.header,invalid.data())==GP_INVALID,"backwards elapsed coordinate refused");
        invalid=clip.frames;invalid[4].sample.clock.continuity_id=24;
        check(api->prepare_clip(&clip.header,invalid.data())==GP_INVALID,"clock continuity gap refused");
        Clip segment(8);segment.header.clock_kind=GCLIP_SEGMENT_CLOCK;
        for(unsigned i=4;i<8;++i) {
            auto& s=segment.frames[i].sample;s.level=24;s.igt=uint16_t(i-4);
            s.clock.raw_igt=s.igt;s.clock.origin_id=8;s.live_action=0x1302;
        }
        check(api->prepare_model(4,rom.data(),uint32_t(rom.size()))==GP_OK,"segment model prepared");
        check(api->prepare_clip(&segment.header,segment.frames.data())==GP_OK,"segment spans level and raw counter reset");
        auto revision=r.revision+1;r=request(segment,revision);r.model_id=4;r.star_ends=0;
        live.epoch=world.epoch=7;live.level=9;live.valid_fields|=GC_VALID_ACTION|GC_VALID_WARP;
        r.origin={GO_SEGMENT,7,live.sequence+1,source_continuity,4000,source_generation,42,0};elapsed_base=4000;
        check(api->configure(&r)==GP_OK,"segment exact origin preserves raw but starts at zero");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});draw(0,true,GP_READY,1);
        live.level=24;++source_generation;live.live_action=r.star_actions[0];draw(3,true,GP_READY,5,2);
        ++source_continuity;draw(4,false,GP_NO_CLOCK,0,2);
        ++r.revision;r.origin.sequence=live.sequence+1;r.origin.continuity=source_continuity;
        check(api->configure(&r)==GP_OK,"new segment origin binds changed continuity");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});draw(4,true,GP_READY,6,2);
        ++r.revision;r.nudge_frames=-600;check(api->configure(&r)==GP_OK,"negative coordinate clamps without unsigned wrap");
        live.level=9;renderer.run([&]{ghost_playback::safe_boundary(7,4);});draw(0,true,GP_BEFORE_START,1);
        ++r.revision;r.origin.elapsed=0;r.nudge_frames=3;elapsed_base=UINT64_MAX-2;
        check(api->configure(&r)==GP_OK,"checked wide elapsed input");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});draw(0,false,GP_NO_CLOCK);
        ++r.revision;r.mode=GP_FIXED;r.origin={};r.seek_igt=UINT32_MAX;
        check(api->configure(&r)==GP_OK,"fixed coordinate retains full uint32 range");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});draw(0,false,GP_AFTER_END);
        Clip closing(9);closing.frames.back().sample.clock.origin_id=8;
        check(api->prepare_clip(&closing.header,closing.frames.data())==GP_OK,"star retains final closing reset as evidence");
        ++r.revision;std::memcpy(r.clip_id,closing.header.clip_id,16);r.seek_igt=16;
        check(api->configure(&r)==GP_OK,"select star ending before reset row");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});draw(0,false,GP_AFTER_END);
        ++r.revision;r.seek_igt=15;check(api->configure(&r)==GP_OK,"last star coordinate precedes closing generation");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});draw(0,true,GP_READY,7,2);
        Clip touch(10);
        // The first duplicate is an ordinary drawable pose. A later duplicate
        // carries a valid touch action despite its refused graphics pose.
        touch.frames[1].sample.valid_fields|=GC_VALID_POSE;
        touch.frames[1].sample.pose=touch.frames[0].sample.pose;
        touch.frames[1].sample.status=GC_NO_CAMERA;
        touch.frames[3].sample.live_action=0x1303;
        touch.frames[6].sample.live_action=touch.frames[7].sample.live_action=0x1302;
        check(api->prepare_clip(&touch.header,touch.frames.data())==GP_OK,"index recorded action independently from graphics pose");
        revision=r.revision+1;r=request(touch,revision);r.model_id=4;
        r.star_action_count=2;r.star_actions[1]=0x1303;
        r.origin={GO_STAR,7,live.sequence+1,source_continuity,5000,source_generation,0,0};elapsed_base=5000;
        live.level=9;live.live_action=1;
        check(api->configure(&r)==GP_OK,"follow template with its own touch and later landing");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});
        draw(10,true,GP_READY,1);draw(11,false,GP_STAR_ENDED);draw(16,false,GP_STAR_ENDED,0,2);
        ++r.revision;r.nudge_frames=-1;check(api->configure(&r)==GP_OK,"nudge can move before recorded touch without reviving live-end latch");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});draw(11,true,GP_READY,1);
        ++r.revision;r.nudge_frames=3;check(api->configure(&r)==GP_OK,"positive nudge reaches recorded touch early");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});draw(8,false,GP_STAR_ENDED);
        ++r.revision;r.nudge_frames=0;r.star_action_count=1;r.star_actions[1]=0;
        check(api->configure(&r)==GP_OK,"changed action registry recomputes first matching touch");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});
        draw(13,true,GP_READY,5,2);draw(15,false,GP_STAR_ENDED,0,2);
        ++r.revision;r.star_ends=0;check(api->configure(&r)==GP_OK,"disabled touch policy retains recorded landing tail");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});draw(16,true,GP_READY,8,2);
        ++r.revision;r.star_ends=1;r.mode=GP_FIXED;r.origin={};r.seek_igt=16;
        check(api->configure(&r)==GP_OK,"fixed seek can inspect recorded post-touch pose");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});draw(0,true,GP_READY,8,2);
        Clip touched_first(11);touched_first.frames[0].sample.live_action=0x1302;
        check(api->prepare_clip(&touched_first.header,touched_first.frames.data())==GP_OK,"clip begins on recorded touch");
        ++r.revision;std::memcpy(r.clip_id,touched_first.header.clip_id,16);r.mode=GP_FOLLOW;r.nudge_frames=-600;
        r.origin={GO_STAR,7,live.sequence+1,source_continuity,6000,source_generation,0,0};elapsed_base=6000;
        check(api->configure(&r)==GP_OK,"before-start clamp must not reveal an already touched first pose");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});draw(0,false,GP_STAR_ENDED);
        Clip large(2,GCLIP_MAX_RECORDS);
        check(api->prepare_model(4,rom.data(),uint32_t(rom.size()))==GP_OK,"budget test model");
        check(api->prepare_clip(&large.header,large.frames.data())==GP_OK,"largest supported clip");
        revision=r.revision+1;r=request(large,revision);r.model_id=4;check(api->configure(&r)==GP_OK,"own first large clip");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});
        auto next=large.header;next.clip_id[0]=3;
        check(api->prepare_clip(&next,large.frames.data())==GP_OK,"second large clip ready");
        ++r.revision;r.clip_id[0]=3;check(api->configure(&r)==GP_OK,"protect selected pending clip");
        next.clip_id[0]=4;
        check(api->prepare_clip(&next,large.frames.data())==GP_BUDGET,"active and ready clips count against one budget");
        check(api->status(&status,sizeof(status))==GP_OK && status.resident_clip_bytes<=GP_CLIP_BUDGET
            && status.clips_owned==1 && status.clips_ready==1,"resident budget accounts for both owners");
        api->revoke(GP_DISABLED);renderer.run([&]{ghost_playback::retire_on_context_loss();});
        check(api->collect()==GP_OK && api->release_worker()==GP_OK,"large clips reclaimed off renderer");
        ++r.revision;r.hue_degrees=360;check(api->configure(&r)==GP_INVALID,"hue range bounded");
        r.hue_degrees=0;r.tint_enabled=2;check(api->configure(&r)==GP_INVALID,"tint flag bounded");
        r.tint_enabled=0;
        r.origin.continuity=0;check(api->configure(&r)==GP_INVALID,"follow requires resolved origin");
        r.origin.continuity=1;r.mode=GP_FIXED;
        check(api->configure(&r)==GP_INVALID,"fixed must not accidentally retain a live origin");
        r.origin={};
        r.revision=UINT64_MAX;check(api->configure(&r)==GP_EXHAUSTED,"revision exhaustion fails closed");
        check(api->release_worker()==GP_OK,"final worker release");
        check(allocations.load()==0 && destructions.load()==0,"render path never allocates or destroys storage");
        check(opens==closes && !fake_model,"all model lifetimes reclaimed");
        std::printf("{\"ok\":true,\"checks\":%u,\"models\":%u,\"render_allocations\":%u,\"render_frees\":%u}\n",
            checks,opens,allocations.load(),destructions.load());return 0;
    } catch(const std::exception& error) {
        std::fprintf(stderr,"playback witness: %s (check %u)\n",error.what(),checks);return 1;
    }
}
