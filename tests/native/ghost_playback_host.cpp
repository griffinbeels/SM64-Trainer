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
    if(world.epoch!=live.epoch || world.list_serial!=live.dlist_serial)return Result::wrong_occurrence;
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
        for(uint32_t i=0;i<count;++i) {
            auto &f=frames[i];auto &s=f.sample;
            f.bytes=sizeof(f);f.version=GCLIP_VERSION;f.configuration=2;f.world_status=GCLIP_WORLD_READY;
            s.bytes=sizeof(s);s.version=GC_VERSION;s.epoch=1;s.sequence=s.dlist_serial=i+1;
            s.global_timer=100+i;s.igt=uint16_t(i/4);s.level=9;s.area=1;
            s.valid_fields=GC_VALID_CLOCK|GC_VALID_POSE;s.status=GC_NO_CAMERA;s.pose.root_kind=GC_ROOT_TRS;
        }
        if(count==8) {
            const uint16_t times[]={10,11,11,0,13,14,15,16};
            for(unsigned i=0;i<8;++i)frames[i].sample.igt=times[i];
            frames[1].sample.valid_fields=GC_VALID_CLOCK;frames[1].sample.status=GC_UNSUPPORTED_ROOT;
            frames[1].sample.pose={};
            frames[3].sample.valid_fields=0;frames[3].sample.status=GC_BAD_POINTER;
            frames[3].sample.pose={};
        }
    }
};
static gp_request request(const Clip& clip,uint64_t revision) {
    gp_request r{};r.bytes=sizeof(r);r.version=GP_VERSION;r.revision=revision;r.enabled=1;
    r.owner_token=123;r.model_id=1;r.rom_generation=7;r.configuration=4;
    std::memcpy(r.clip_id,clip.header.clip_id,16);r.opacity=1;r.star_ends=1;
    r.star_action_count=1;r.star_actions[0]=0x1302;return r;
}
int main() {
    try {
        const DWORD worker=GetCurrentThreadId();const auto *api=ghost_playback::api();
        check(api->bytes==sizeof(gp_api) && api->version==GP_VERSION,"API version");
        std::vector<uint8_t> rom(8u*1024u*1024u);
        Clip clip(1);
        check(api->prepare_model(1,rom.data(),uint32_t(rom.size()))==GP_OK,"worker prepares detached model");
        check(fake_model && !fake_model->owner,"prepared model detached");
        check(api->prepare_clip(&clip.header,clip.frames.data())==GP_OK,"worker indexes explicit invalid rows");
        clip.frames[0].sample.pose.position[0]=99;
        check(api->prepare_clip(&clip.header,clip.frames.data())==GP_INVALID,"same clip ID cannot change its payload");
        auto r=request(clip,1);check(api->configure(&r)==GP_OK,"configure playback");
        gc_sample live=clip.frames[0].sample;live.epoch=5;live.sequence=live.dlist_serial=50;
        live.valid_fields=GC_VALID_CLOCK|GC_VALID_CAMERA|GC_VALID_ACTION;live.live_action=1;
        ghost::WorldPass world{};world.epoch=5;world.context_generation=2;world.list_serial=50;
        ghost_compositor::Pose pose{};gp_draw_token token{};
        Renderer renderer;
        auto draw=[&](uint16_t time,bool expected,uint32_t why,uint64_t sequence=0) {
            live.igt=time;
            renderer.run([&] {
                check(ghost_playback::evaluate(live,world,pose,token)==expected,"selection result");
                if(expected)check(selected_sequence==sequence && ghost_playback::current(token),"selected occurrence identity");
                else check(!pose.vertices && !pose.count && !token.revision,"refusal clears candidate");
            });
            gp_status status{};check(api->status(&status,sizeof(status))==GP_OK && status.decision==why,"selection reason");
        };
        renderer.run([&] {ghost_playback::safe_boundary(7,4);sg_atlas_view atlas{};uint64_t id=0;
            check(ghost_playback::atlas(&atlas,&id) && id==1 && atlas.rgba,"renderer adopted atlas");});
        draw(10,true,GP_READY,1);check(selected_x==0,"preloaded frame is an owned copy");
        draw(10,true,GP_READY,1);draw(8,true,GP_BEFORE_START,1);
        draw(11,false,GP_INVALID_FRAME);draw(12,false,GP_GAP);draw(13,true,GP_READY,5);draw(17,false,GP_AFTER_END);
        live.valid_fields&=~GC_VALID_ACTION;draw(10,false,GP_INVALID_FRAME);live.valid_fields|=GC_VALID_ACTION;
        live.live_action=r.star_actions[0];draw(10,false,GP_STAR_ENDED);
        live.live_action=1;draw(15,false,GP_STAR_ENDED);draw(13,true,GP_READY,5);
        live.live_action=r.star_actions[0];draw(13,false,GP_STAR_ENDED);
        live.live_action=1;draw(0,true,GP_BEFORE_START,1);draw(10,true,GP_READY,1);
        live.live_action=r.star_actions[0];draw(14,false,GP_STAR_ENDED);
        live.live_action=1;live.epoch=world.epoch=6;draw(14,true,GP_READY,6);
        r.revision=2;r.star_ends=0;r.nudge_frames=3;r.opacity=0.25f;
        r.tint_enabled=1;r.hue_degrees=217;
        check(api->configure(&r)==GP_OK,"configure nudge and segment");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});
        live.live_action=r.star_actions[0];draw(10,true,GP_READY,5);check(pose.opacity==0.25f,"opacity forwarded");
        check(pose.hue_degrees==217.0f,"hue forwarded");
        r.tint_enabled=0;
        r.revision=3;r.mode=GP_FIXED;r.seek_igt=14;check(api->configure(&r)==GP_OK,"fixed seek");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});draw(0,true,GP_READY,6);
        check(pose.hue_degrees==0.0f,"disabled tint bypassed");
        check(api->configure(&r)==GP_INVALID,"stale revision rejected");
        const gp_draw_token old=token;
        renderer.run([&]{ghost_playback::safe_boundary(7,4);check(!ghost_playback::current(old),"list boundary revokes older draw");});
        draw(0,true,GP_READY,6);
        check(api->prepare_model(2,rom.data(),uint32_t(rom.size()))==GP_DEFERRED,"model replacement waits for retirement");
        renderer.run([&]{check(!ghost_playback::current(token),"replacement cancels displayed candidate");ghost_playback::safe_boundary(7,4);});
        refuse_adoption=true;check(api->collect()==GP_DEFERRED,"busy background adoption deferred");refuse_adoption=false;
        refuse_close=true;check(api->collect()==GP_DEFERRED,"busy close retains worker custody");refuse_close=false;
        check(api->collect()==GP_OK && closes==1 && last_close==worker,"worker retries actual close");
        check(api->prepare_model(2,rom.data(),uint32_t(rom.size()))==GP_OK,"replacement model prepared");
        r.revision=4;r.model_id=2;check(api->configure(&r)==GP_OK,"select replacement model");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});draw(0,true,GP_READY,6);
        revoke_evaluation=true;draw(0,false,GP_POSE_REFUSED);revoke_evaluation=false;
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
        check(api->prepare_clip(&clip.header,invalid.data())==GP_INVALID,"ambiguous clock reset refused");
        Clip large(2,GCLIP_MAX_RECORDS);
        check(api->prepare_model(4,rom.data(),uint32_t(rom.size()))==GP_OK,"budget test model");
        check(api->prepare_clip(&large.header,large.frames.data())==GP_OK,"largest supported clip");
        r=request(large,5);r.model_id=4;check(api->configure(&r)==GP_OK,"own first large clip");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});
        auto next=large.header;next.clip_id[0]=3;
        check(api->prepare_clip(&next,large.frames.data())==GP_OK,"second large clip ready");
        r.revision=6;r.clip_id[0]=3;check(api->configure(&r)==GP_OK,"protect selected pending clip");
        next.clip_id[0]=4;
        check(api->prepare_clip(&next,large.frames.data())==GP_BUDGET,"active and ready clips count against one budget");
        check(api->status(&status,sizeof(status))==GP_OK && status.resident_clip_bytes<=GP_CLIP_BUDGET
            && status.clips_owned==1 && status.clips_ready==1,"resident budget accounts for both owners");
        api->revoke(GP_DISABLED);renderer.run([&]{ghost_playback::retire_on_context_loss();});
        check(api->collect()==GP_OK && api->release_worker()==GP_OK,"large clips reclaimed off renderer");
        r.revision=7;r.hue_degrees=360;check(api->configure(&r)==GP_INVALID,"hue range bounded");
        r.hue_degrees=0;r.tint_enabled=2;check(api->configure(&r)==GP_INVALID,"tint flag bounded");
        r.tint_enabled=0;
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
