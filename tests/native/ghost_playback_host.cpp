/* Playback ownership/timeline witness. Actual playback, stub model and pose
 * conversion: real geometry has separate model/pose witnesses. No windows. */
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "ghost_playback.h"
#include "ghost_pose.h"
#include <atomic>
#include <cmath>
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
static unsigned checks=0,multi_ghosts=0,multi_visible=0,multi_prepares=0;
static float dance_fade[GP_STAR_FADE_FRAMES]{};static unsigned dance_hidden_after=0;
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
static bool refuse_shadow=false;
static bool perspective=false; // a real look-down-minus-z projection for the occlusion region
static unsigned shadow_calls=0;
static uint64_t shadow_sequence=0;
// Sound sink: fixed storage, since the renderer must never allocate.
static uint32_t sound_log[256],sound_ghost[256];static unsigned sound_count=0;
static uint32_t last_playing=2;static float last_volume=-1;
struct Placed {unsigned count;float position[3];float gain;};
static Placed placed[GP_GHOSTS];static unsigned unplaced_cues=0;
static void __cdecl test_cue(uint32_t ghost,uint32_t bits) {
    if(ghost>=GP_GHOSTS || !placed[ghost].count)++unplaced_cues; // a sound starts where its ghost is
    if(sound_count<256){sound_ghost[sound_count]=ghost;sound_log[sound_count++]=bits;}
}
static void __cdecl test_control(uint32_t playing,float volume) {last_playing=playing;last_volume=volume;}
static void __cdecl test_place(uint32_t ghost,const float *position,float gain) {
    if(ghost>=GP_GHOSTS){++unplaced_cues;return;}
    auto &p=placed[ghost];++p.count;std::memcpy(p.position,position,sizeof(p.position));p.gain=gain;
}
static const ghost_playback::SoundSink test_sink{test_cue,test_control,test_place};
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
    out.vertices=triangle;out.count=recorded.sample.pose.hidden?0:3;out.opacity=opacity;
    // Identify the recorded row and its depth in the otherwise opaque matrix.
    out.view_projection[0]=recorded.sample.pose.position[0];
    out.view_projection[15]=recorded.sample.pose.position[2];
    if(perspective) {
        // clip = (x, y, z, -z): the camera at the origin looking down -z.
        std::memset(out.view_projection,0,sizeof(out.view_projection));
        out.view_projection[0]=out.view_projection[5]=out.view_projection[10]=1;out.view_projection[11]=-1;
    }
    return Result::ok;
}
// The pose witness proves the look-at product; this one proves which point
// is placed: the camera here is a pure translation.
bool camera_relative(sg_model *p,const gc_camera& live,const float world[3],float out[3]) {
    if(!p || p!=fake_model || p->owner!=GetCurrentThreadId())return false;
    for(unsigned axis=0;axis<3;++axis)out[axis]=world[axis]-live.position[axis];
    return true;
}
// Conversion math has the pose witness; this proves which row's shadow is used.
bool shadow(sg_model *p,const gc_sample& recorded,const float view_projection[16],
            ghost_compositor::ShadowVertex vertices[24],uint8_t texture[256],ghost_compositor::Shadow& out) {
    out={};++shadow_calls;
    if(refuse_shadow || !p || !(recorded.valid_fields&GC_VALID_SHADOW))return false;
    shadow_sequence=recorded.sequence;
    for(unsigned i=0;i<24;++i)vertices[i]={{recorded.shadow.matrix[12],0,0},{0,0},1};
    texture[0]=recorded.shadow.texture[0];
    out.vertices=vertices;out.count=24;out.texture_ia8=texture;
    std::memcpy(out.view_projection,view_projection,sizeof(out.view_projection));return true;
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
            // Row 6 drew the game's shadow beside an executed root.
            auto& shadowed=frames[5].sample;
            shadowed.valid_fields|=GC_VALID_SHADOW|GC_VALID_CAMERA;
            shadowed.pose.root_kind=GC_ROOT_SCALED_MODELVIEW;
            shadowed.pose.root_matrix[0]=shadowed.pose.root_matrix[5]=shadowed.pose.root_matrix[10]=1;
            shadowed.pose.root_matrix[15]=shadowed.shadow.matrix[15]=1;
            shadowed.shadow.matrix[0]=shadowed.shadow.matrix[5]=shadowed.shadow.matrix[10]=1;
            shadowed.shadow.matrix[12]=42;shadowed.shadow.texture[0]=0x5a;
        }
        header.origin_raw=frames[0].sample.igt;
        for(auto& f:frames) {
            auto& s=f.sample;
            s.clock={GCC_RAW_VALID|GCC_ELAPSED_VALID,GCC_UNKNOWN_ORIGIN,GCC_STEADY,s.igt,
                0,0,7,1,1000u+s.igt-header.origin_raw,23};
        }
    }
    // One row per coordinate 0..count-1; x names the clip and row, z its depth.
    static Clip linear(uint8_t id,uint32_t count,uint32_t touch_at,float depth) {
        Clip clip(id,count);
        for(uint32_t i=0;i<count;++i) {
            auto& s=clip.frames[i].sample;s.igt=uint16_t(i);
            s.pose.position[0]=float(id*100u+i);s.pose.position[2]=depth;
            if(i==touch_at)s.live_action=0x1302;
            s.clock.raw_igt=s.igt;s.clock.elapsed_frames=1000u+i;
        }
        clip.header.origin_raw=0;return clip;
    }
};
static gp_request request(const Clip& clip,uint64_t revision) {
    gp_request r{};r.bytes=sizeof(r);r.version=GP_VERSION;r.revision=revision;r.enabled=1;
    r.owner_token=123;r.model_id=1;r.rom_generation=7;r.configuration=4;
    r.clip_count=1;std::memcpy(r.clips[0].clip_id,clip.header.clip_id,16);r.opacity=1;r.star_ends=1;
    r.star_action_count=1;r.star_actions[0]=0x1302;r.shadow_enabled=1;
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
        ghost_compositor::Pose poses[GP_GHOSTS]{};uint32_t count=0;gp_draw_token token{};
        auto &pose=poses[0];
        uint64_t source_generation=7,source_continuity=77,elapsed_base=2000;
        Renderer renderer;
        auto draw=[&](uint64_t time,bool expected,uint32_t why,uint64_t sequence=0,int area=1) {
            // The game clock moves with IGT, as in play: the renderer's live
            // recovery treats IGT outrunning it as a forward state load.
            live.igt=uint16_t(time);live.area=int16_t(area);live.global_timer=uint32_t(5000+time);
            ++live.sequence;++live.dlist_serial;world.list_serial=live.dlist_serial;
            live.clock={GCC_RAW_VALID|GCC_ELAPSED_VALID,GCC_UNKNOWN_ORIGIN,GCC_STEADY,live.igt,
                0,0,source_generation,1,elapsed_base+time,source_continuity};
            renderer.run([&] {
                const bool drawn=ghost_playback::evaluate(live,world,poses,count,token);
                if(drawn!=expected)std::fprintf(stderr,"time %llu drew %d, expected %d\n",
                    static_cast<unsigned long long>(time),int(drawn),int(expected));
                check(drawn==expected,"selection result");
                if(expected)check(count==1 && selected_sequence==sequence && ghost_playback::current(token),"selected occurrence identity");
                else check(!pose.vertices && !pose.count && !count && !token.revision,"refusal clears candidate");
            });
            gp_status status{};const bool reported=api->status(&status,sizeof(status))==GP_OK;
            if(status.decision!=why)std::fprintf(stderr,"time %llu decision %u, expected %u\n",
                static_cast<unsigned long long>(time),status.decision,why);
            check(reported && status.decision==why,"selection reason");
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
        // An invalid clock frame leaves the live timing unresolved (as on the
        // worker) until a witnessed checkpoint names the next state again: the
        // live pose is that witness here.
        live.valid_fields|=GC_VALID_POSE;draw(10,true,GP_READY,1);
        live.valid_fields&=~GC_VALID_ACTION;draw(10,false,GP_NO_CLOCK);live.valid_fields|=GC_VALID_ACTION;
        live.live_action=r.star_actions[0];draw(10,false,GP_STAR_ENDED);
        // Elapsed never runs backward inside one continuity, so the latch is
        // checked later in the same timeline.
        live.live_action=1;draw(15,false,GP_STAR_ENDED);draw(16,false,GP_STAR_ENDED);
        ++r.revision;r.opacity=0.25f;
        check(api->configure(&r)==GP_OK,"same-origin tuning revision");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});draw(10,false,GP_STAR_ENDED);
        ++r.revision;r.origin.sequence=live.sequence+1;r.origin.elapsed=elapsed_base=3000;
        r.nudge_frames=3;
        r.tint_enabled=1;r.clips[0].hue_degrees=217;
        check(api->configure(&r)==GP_OK,"new exact origin resumes with nudge");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});
        draw(10,true,GP_READY,5,2);check(pose.opacity==0.25f,"opacity forwarded");
        check(pose.hue_degrees==217.0f,"hue forwarded");
        check(!pose.shadow.count && !pose.shadow.vertices && !shadow_calls,"row without a recorded shadow draws none");
        ++r.revision;r.proximity_enabled=1;
        check(api->configure(&r)==GP_OK,"enable overlap fading");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});
        live.valid_fields|=GC_VALID_POSE;live.pose.position[0]=0;
        draw(10,true,GP_READY,5,2);check(pose.opacity==0,"coincident Mario fully transparent");
        live.pose.position[0]=37;
        draw(10,true,GP_READY,5,2);check(pose.opacity==0.125f,"half body overlap multiplies configured opacity");
        live.pose.position[0]=74;
        draw(10,true,GP_READY,5,2);check(pose.opacity==0.25f,"touching bodies at zero extra distance restore opacity");
        ++r.revision;r.proximity_distance=74;
        check(api->configure(&r)==GP_OK,"extra fade distance");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});
        draw(10,true,GP_READY,5,2);check(pose.opacity==0.125f,"distance changes actual generated ghost opacity");
        live.valid_fields&=~GC_VALID_POSE;
        draw(10,true,GP_READY,5,2);check(pose.opacity==0.25f,"missing player pose does not invent fading");
        live.valid_fields|=GC_VALID_POSE;live.pose.position[0]=0;
        ++r.revision;r.proximity_enabled=0;
        check(api->configure(&r)==GP_OK,"overlap A/B disable");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});
        draw(10,true,GP_READY,5,2);check(pose.opacity==0.25f,"disabled overlap preserves visibility even at coincidence");
        check(!pose.occlusion.enabled && !pose.occlusion.has_player,"occlusion fade off leaves the region empty");
        // Player first: live Mario's body box under the pose's own live
        // view-projection reaches the drawn pose; opacity is untouched.
        ++r.revision;r.occlusion_fade=1;check(api->configure(&r)==GP_OK,"occlusion fade on");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});
        perspective=true;live.pose.position[0]=0;live.pose.position[1]=0;live.pose.position[2]=-1000;
        for(auto& axis:live.pose.scale)axis=1;
        draw(10,true,GP_READY,5,2);
        {
            const auto& o=pose.occlusion;auto same=[](float a,float b){return std::fabs(a-b)<1e-5f;};
            check(o.enabled && o.has_player && pose.opacity==0.25f,"live Mario region reaches the pose");
            check(same(o.center[0],0) && same(o.radius[0],50.0f/950) && same(o.center[1],80.0f/950)
                  && same(o.radius[1],80.0f/950) && same(o.player_w,1000),"region is his projected body box");
        }
        live.pose.position[2]=500;draw(10,true,GP_READY,5,2);
        check(pose.occlusion.enabled && !pose.occlusion.has_player,"Mario behind the camera has no region");
        live.pose.position[2]=-1000;live.valid_fields&=~GC_VALID_POSE;draw(10,true,GP_READY,5,2);
        check(pose.occlusion.enabled && !pose.occlusion.has_player,"missing player pose keeps only the camera fade");
        live.valid_fields|=GC_VALID_POSE;
        ++r.revision;r.occlusion_fade=0;check(api->configure(&r)==GP_OK,"occlusion A/B off");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});draw(10,true,GP_READY,5,2);
        check(!pose.occlusion.enabled && !pose.occlusion.has_player,"switched-off fade reaches no pose");
        perspective=false;live.pose.position[0]=live.pose.position[2]=0;
        live.valid_fields&=~GC_VALID_POSE;
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
        check(pose.shadow.count==24 && shadow_sequence==6 && pose.shadow.vertices[0].position[0]==42
              && pose.shadow.texture_ia8 && pose.shadow.texture_ia8[0]==0x5a,"recorded shadow reaches its ghost");
        check(!std::memcmp(pose.shadow.view_projection,pose.view_projection,sizeof(pose.view_projection)),
              "shadow shares its body's live projection");
        refuse_shadow=true;draw(0,false,GP_POSE_REFUSED,0,2);refuse_shadow=false;
        const auto shadow_before=shadow_calls;
        ++r.revision;r.shadow_enabled=0;check(api->configure(&r)==GP_OK,"shadow A/B switch off");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});draw(0,true,GP_READY,6,2);
        check(!pose.shadow.count && !pose.shadow.vertices && shadow_calls==shadow_before,
              "switched-off shadow is never built or drawn");
        ++r.revision;r.shadow_enabled=1;check(api->configure(&r)==GP_OK,"shadow A/B switch on");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});draw(0,true,GP_READY,6,2);
        check(pose.shadow.count==24,"shadow returns with the switch");
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
        r.origin={GO_SEGMENT,7,live.sequence+1,source_continuity,4000,source_generation,0,0};elapsed_base=4000;
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
        ++r.revision;std::memcpy(r.clips[0].clip_id,closing.header.clip_id,16);r.seek_igt=16;
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
        ++r.revision;std::memcpy(r.clips[0].clip_id,touched_first.header.clip_id,16);r.mode=GP_FOLLOW;r.nudge_frames=-600;
        r.origin={GO_STAR,7,live.sequence+1,source_continuity,6000,source_generation,0,0};elapsed_base=6000;
        check(api->configure(&r)==GP_OK,"before-start clamp must not reveal an already touched first pose");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});draw(0,false,GP_STAR_ENDED);
        {
            // Short refusal runs (a one-frame BLJ land) between two poses in
            // one scene hold the previous pose; a longer run, a scene change
            // or the clip's end still hides the ghost.
            Clip gap=Clip::linear(23,14,UINT32_MAX,5);
            const auto refuse=[&](unsigned i) {
                auto &s=gap.frames[i].sample;s.valid_fields&=~GC_VALID_POSE;
                s.pose={};s.pose.refusal=GC_REFUSED_ANIMATION_RANGE;s.status=GC_BAD_VALUE;
            };
            refuse(1);gap.frames[2].sample.area=2;
            refuse(4);refuse(6);refuse(7);refuse(9);refuse(10);refuse(11);refuse(13);
            check(api->prepare_clip(&gap.header,gap.frames.data())==GP_OK,"prepare clip with short refusal runs");
            revision=r.revision+1;r=request(gap,revision);r.model_id=4;r.mode=GP_FIXED;r.origin={};
            const auto seek=[&](uint32_t at,bool shown,uint32_t why,uint64_t sequence,float x) {
                ++r.revision;r.seek_igt=at;check(api->configure(&r)==GP_OK,"seek a bridge row");
                live.level=9;renderer.run([&]{ghost_playback::safe_boundary(7,4);});
                draw(0,shown,why,sequence,1);
                if(shown)check(selected_x==x,"bridge shows the previous pose");
            };
            seek(3,true,GP_READY,4,2303);
            seek(4,true,GP_READY,4,2303);   // one refused frame holds row 3
            seek(6,true,GP_READY,6,2305);   // two refused frames hold row 5
            seek(7,true,GP_READY,6,2305);
            seek(1,false,GP_INVALID_FRAME,0,0);  // next pose is in another area
            seek(10,false,GP_INVALID_FRAME,0,0); // three refused frames: a real gap
            seek(13,false,GP_INVALID_FRAME,0,0); // nothing after it
        }
        {
            // Three ghosts on one live origin. Each ends on its own recorded
            // touch or length; a live grab ends them all.
            Clip a=Clip::linear(20,30,20,5),b=Clip::linear(21,30,8,50),c=Clip::linear(22,10,UINT32_MAX,20);
            for(auto *ghost:{&a,&b,&c}) {
                check(api->prepare_clip(&ghost->header,ghost->frames.data())==GP_OK,"prepare one ghost of the set");
                ++multi_prepares;
            }
            revision=r.revision+1;r=request(a,revision);r.model_id=4;r.tint_enabled=1;
            r.clip_count=3;r.clips[0].hue_degrees=10;
            std::memcpy(r.clips[1].clip_id,b.header.clip_id,16);r.clips[1].hue_degrees=200;
            std::memcpy(r.clips[2].clip_id,c.header.clip_id,16);r.clips[2].hue_degrees=300;
            r.origin={GO_STAR,7,live.sequence+1,source_continuity,7000,source_generation,0,0};elapsed_base=7000;
            live.level=9;live.live_action=1;
            check(api->configure(&r)==GP_OK,"configure three ghosts on one origin");
            renderer.run([&]{ghost_playback::safe_boundary(7,4);});
            multi_ghosts=r.clip_count;
            auto frame=[&](uint64_t time,std::initializer_list<float> rows,uint32_t why,
                           std::initializer_list<uint32_t> each) {
                live.igt=uint16_t(time);live.area=1;++live.sequence;++live.dlist_serial;
                live.global_timer=uint32_t(9000+time); // the game clock moves with IGT
                world.list_serial=live.dlist_serial;
                live.clock={GCC_RAW_VALID|GCC_ELAPSED_VALID,GCC_UNKNOWN_ORIGIN,GCC_STEADY,live.igt,
                    0,0,source_generation,1,elapsed_base+time,source_continuity};
                renderer.run([&] {
                    const bool drawn=ghost_playback::evaluate(live,world,poses,count,token);
                    check(drawn==(rows.size()>0) && count==rows.size(),"visible ghost count");
                    unsigned k=0;
                    for(float row:rows)check(poses[k++].view_projection[0]==row,"ghost, row and back-to-front order");
                    for(k=count;k<GP_GHOSTS;++k)check(!poses[k].vertices && !poses[k].count,"unused outputs cleared");
                    check(drawn==(token.revision!=0) && (!drawn || ghost_playback::current(token)),"one batch token");
                });
                if(count>multi_visible)multi_visible=count;
                gp_status status{};
                check(api->status(&status,sizeof(status))==GP_OK && status.decision==why
                    && status.visible==count && status.clip_count==r.clip_count,"set summary decision");
                unsigned k=0;for(auto value:each)check(status.clip_decisions[k++]==value,"per-ghost decision");
            };
            // Depth order is b (z 50), c (z 20), a (z 5).
            frame(5,{2105,2205,2005},GP_READY,{GP_READY,GP_READY,GP_READY});
            check(poses[0].hue_degrees==200 && poses[1].hue_degrees==300 && poses[2].hue_degrees==10,
                "hue follows its own ghost");
            frame(8,{2208,2008},GP_READY,{GP_READY,GP_STAR_ENDED,GP_READY});
            frame(9,{2209,2009},GP_READY,{GP_READY,GP_STAR_ENDED,GP_READY});
            frame(10,{2010},GP_READY,{GP_READY,GP_STAR_ENDED,GP_AFTER_END});
            frame(19,{2019},GP_READY,{GP_READY,GP_STAR_ENDED,GP_AFTER_END});
            frame(20,{},GP_STAR_ENDED,{GP_STAR_ENDED,GP_STAR_ENDED,GP_AFTER_END});
            ++r.revision;r.nudge_frames=-3;check(api->configure(&r)==GP_OK,"one nudge moves every ghost");
            renderer.run([&]{ghost_playback::safe_boundary(7,4);});
            frame(11,{2208,2008},GP_READY,{GP_READY,GP_STAR_ENDED,GP_READY});
            ++r.revision;r.nudge_frames=0;r.proximity_enabled=1;check(api->configure(&r)==GP_OK,"per-ghost fade");
            renderer.run([&]{ghost_playback::safe_boundary(7,4);});
            live.valid_fields|=GC_VALID_POSE;live.pose.position[0]=2005;live.pose.position[1]=0;live.pose.position[2]=5;
            frame(5,{2105,2205,2005},GP_READY,{GP_READY,GP_READY,GP_READY});
            check(poses[0].opacity==1 && poses[1].opacity==1 && poses[2].opacity==0,"only the coincident ghost fades");
            live.valid_fields&=~GC_VALID_POSE;
            ++r.revision;r.proximity_enabled=0;check(api->configure(&r)==GP_OK,"fade off");
            renderer.run([&]{ghost_playback::safe_boundary(7,4);});
            // Removing one ghost hides it at the next boundary; the others play on.
            gp_status before{};check(api->status(&before,sizeof(before))==GP_OK && before.clips_owned==3,"set held");
            check(api->release_clip(b.header.clip_id)==GP_OK,"release one ghost");
            check(api->prepare_clip(&b.header,b.frames.data())==GP_DEFERRED,"held released copy is not revived");
            renderer.run([&]{ghost_playback::safe_boundary(7,4);});
            frame(5,{2205,2005},GP_READY,{GP_READY,GP_PREPARING,GP_READY});
            check(api->collect()==GP_OK,"collect released ghost");
            gp_status after{};check(api->status(&after,sizeof(after))==GP_OK && after.clips_owned==2
                && after.resident_clip_bytes<before.resident_clip_bytes,"released storage reclaimed");
            check(api->prepare_clip(&b.header,b.frames.data())==GP_OK,"fresh copy after retirement");++multi_prepares;
            renderer.run([&]{ghost_playback::safe_boundary(7,4);});
            frame(5,{2105,2205,2005},GP_READY,{GP_READY,GP_READY,GP_READY});
            // Replacing the set keeps a and adds a segment-clock clip: only the
            // mismatched ghost refuses. Unlisted held clips retire.
            Clip mixed=Clip::linear(23,30,UINT32_MAX,1);mixed.header.clock_kind=GCLIP_SEGMENT_CLOCK;
            check(api->prepare_clip(&mixed.header,mixed.frames.data())==GP_OK,"prepare added ghost");++multi_prepares;
            ++r.revision;r.clip_count=2;std::memcpy(r.clips[1].clip_id,mixed.header.clip_id,16);r.clips[2]={};
            check(api->configure(&r)==GP_OK,"replace the displayed set");
            renderer.run([&]{ghost_playback::safe_boundary(7,4);});
            frame(6,{2006},GP_READY,{GP_READY,GP_WRONG_IDENTITY});
            check(api->collect()==GP_OK && api->status(&after,sizeof(after))==GP_OK && after.clips_owned==2
                && !after.clips_ready,"renderer holds exactly the listed set");
            live.live_action=0x1302;frame(7,{},GP_STAR_ENDED,{GP_STAR_ENDED,GP_STAR_ENDED});
            live.live_action=1;frame(8,{},GP_STAR_ENDED,{GP_STAR_ENDED,GP_STAR_ENDED});
        }
        {
            // Ghost sound: each record's recorded requests reach the sink once,
            // in ghost order then record order, as the displayed record passes.
            Clip s1=Clip::linear(30,200,UINT32_MAX,5),s2=Clip::linear(31,200,UINT32_MAX,9);
            for(auto [clip,tag]:{std::pair<Clip*,uint32_t>{&s1,1000},{&s2,2000}})
                for(uint32_t i=0;i<200;++i) {
                    auto &s=clip->frames[i].sample;s.valid_fields|=GC_VALID_SOUND;
                    s.sound.count=1;s.sound.bits[0]=tag+i;
                }
            s1.frames[5].sample.sound.count=2;s1.frames[5].sample.sound.bits[1]=1905;
            s2.frames[7].sample.valid_fields&=~GC_VALID_SOUND;s2.frames[7].sample.sound={};
            check(api->prepare_clip(&s1.header,s1.frames.data())==GP_OK && api->prepare_clip(&s2.header,s2.frames.data())==GP_OK,
                  "prepare clips carrying sounds");
            ghost_playback::set_sound_sink(&test_sink);sound_count=0;
            revision=r.revision+1;r=request(s1,revision);r.model_id=4;r.clip_count=2;
            std::memcpy(r.clips[1].clip_id,s2.header.clip_id,16);r.star_ends=0;r.star_action_count=0;r.star_actions[0]=0;
            r.volume=.75f;r.origin={GO_STAR,7,live.sequence+1,source_continuity,8000,source_generation,0,0};
            elapsed_base=8000;live.level=9;live.live_action=1;live.area=1;
            check(api->configure(&r)==GP_OK,"configure two ghosts with sound");
            renderer.run([&]{ghost_playback::safe_boundary(7,4);});
            check(last_playing==1 && last_volume==.75f,"volume reaches the sink");
            auto step=[&](uint64_t time,std::initializer_list<uint32_t> expected,bool shown=true) {
                live.igt=uint16_t(time);++live.sequence;++live.dlist_serial;world.list_serial=live.dlist_serial;
                live.global_timer=uint32_t(12000+time);
                live.clock={GCC_RAW_VALID|GCC_ELAPSED_VALID,GCC_UNKNOWN_ORIGIN,GCC_STEADY,live.igt,
                    0,0,source_generation,1,elapsed_base+time,source_continuity};
                sound_count=0;for(auto &p:placed)p={};
                renderer.run([&]{check(ghost_playback::evaluate(live,world,poses,count,token)==shown,"sound frame drawn");});
                const std::vector<uint32_t> want(expected),heard(sound_log,sound_log+sound_count);
                for(unsigned k=0;k<sound_count;++k)
                    check(sound_ghost[k]==(sound_log[k]/1000==2?1u:0u),"each sound comes from its own ghost");
                if(heard!=want) {
                    std::fprintf(stderr,"time %llu heard",static_cast<unsigned long long>(time));
                    for(auto bits:heard)std::fprintf(stderr," %u",bits);
                    std::fprintf(stderr,"\n");
                }
                check(heard==want,"exactly the passed records' sounds");
            };
            step(0,{1000,2000});                      // a new run: its first record is newly passed
            step(1,{1001,2001});
            step(4,{1002,1003,1004,2002,2003,2004});  // frames not drawn still sound, in order
            step(4,{});                                // a paused ghost repeats nothing
            step(5,{1005,1905,2005});                  // several requests of one record keep their order
            step(8,{1006,1007,1008,2006,2008});        // a record without sound adds none
            // Each visible ghost sounds from its displayed root relative to the
            // live camera, at min(1,(GP_SOUND_NEAR/d)^2) of d from live Mario.
            live.camera.position[0]=3000;live.camera.position[1]=50;live.camera.position[2]=-100;
            live.valid_fields|=GC_VALID_POSE;live.pose.position[0]=3008;live.pose.position[1]=0;live.pose.position[2]=5;
            step(8,{});
            check(placed[0].count==1 && placed[1].count==1 && !placed[2].count,"every visible ghost is placed once a frame");
            check(placed[0].position[0]==8 && placed[0].position[1]==-50 && placed[0].position[2]==105
                  && placed[1].position[0]==108 && placed[1].position[2]==109,"placed at its root from the live camera");
            check(placed[0].gain==1 && placed[1].gain==1,"within GP_SOUND_NEAR of live Mario: full gain");
            live.pose.position[0]=3008+2*GP_SOUND_NEAR;step(8,{});
            check(std::fabs(placed[0].gain-.5f)<1e-6f,"twice GP_SOUND_NEAR away: half the amplitude, a quarter the intensity");
            const double distant=double(3808-3108)*(3808-3108)+16.0;
            check(std::fabs(placed[1].gain-float(GP_SOUND_NEAR/std::sqrt(distant)))<1e-6f,"each ghost its own distance");
            live.valid_fields&=~GC_VALID_POSE;step(8,{});
            check(placed[0].gain==1 && placed[1].gain==1,"unknown live Mario: full gain");
            std::memset(live.camera.position,0,sizeof(live.camera.position));
            ++r.revision;r.nudge_frames=-3;check(api->configure(&r)==GP_OK,"nudge back");
            renderer.run([&]{ghost_playback::safe_boundary(7,4);});
            step(9,{});step(11,{});step(12,{1009,2009}); // re-passed records stay silent
            ++r.revision;r.nudge_frames=0;check(api->configure(&r)==GP_OK,"nudge forward");
            renderer.run([&]{ghost_playback::safe_boundary(7,4);});
            step(20,{});step(21,{1021,2021});          // the jump itself sounds nothing
            step(21+GP_SOUND_CATCHUP+1,{});            // a long gap is not replayed as a burst
            step(21+GP_SOUND_CATCHUP+2,{1000+23+GP_SOUND_CATCHUP,2000+23+GP_SOUND_CATCHUP});
            ++r.revision;r.clip_count=1;r.clips[1]={};check(api->configure(&r)==GP_OK,"one ghost left");
            renderer.run([&]{ghost_playback::safe_boundary(7,4);});
            step(114,{1114});                          // a removed ghost is silent
            live.area=2;step(116,{},false);live.area=1; // hidden frames sound nothing,
            step(117,{});step(118,{1118});             // and reappearing replays none
            ++r.revision;r.volume=0;check(api->configure(&r)==GP_OK,"volume zero");
            renderer.run([&]{ghost_playback::safe_boundary(7,4);});
            check(last_playing==1 && last_volume==0,"exact zero volume reaches the sink");
            api->withdraw();renderer.run([&]{ghost_playback::safe_boundary(7,4);});
            check(last_playing==0,"withdrawn playback stops sound");
            // A new run nudged earlier: silent before its start, then its first record.
            ++r.revision;r.volume=.5f;r.nudge_frames=-2;r.origin.sequence=live.sequence+1;
            r.origin.elapsed=elapsed_base=9000;check(api->configure(&r)==GP_OK,"new run before its start");
            renderer.run([&]{ghost_playback::safe_boundary(7,4);});
            check(last_playing==1 && last_volume==.5f,"resumed playback restores sound");
            step(0,{});step(1,{});step(2,{1000});step(3,{1001});
            step(40,{1002,1003,1004,1005,1905,1006,1007,1008,1009,1010,1011,1012,1013,1014,1015,1016,1017,1018,
                     1019,1020,1021,1022,1023,1024,1025,1026,1027,1028,1029,1030,1031,1032,1033,1034,1035,
                     1036,1037,1038});
            // The live reset sequence: the tracker's arm ends (owner suspended:
            // the worker withdraws), then its next command binds the SAME ghost
            // to the restored start again. Attempts 2 and 3 must sound too.
            for(uint32_t attempt=2;attempt<=3;++attempt) {
                api->withdraw();renderer.run([&]{ghost_playback::safe_boundary(7,4);});
                check(last_playing==0,"a reset between attempts silences");
                ++r.revision;r.nudge_frames=0;elapsed_base=9000+attempt*1000;
                r.origin={GO_STAR,7,live.sequence+1,source_continuity,elapsed_base,source_generation,0,GO_RECOVERED};
                check(api->configure(&r)==GP_OK,"worker re-binds the restored start");
                renderer.run([&]{ghost_playback::safe_boundary(7,4);});
                step(0,{});step(1,{1001});step(3,{1002,1003}); // the restored first record is where it stands
            }
            check(unplaced_cues==0,"every sound was placed before it was cued");
            ghost_playback::set_sound_sink(nullptr);
        }
        {
            // Star dance: a clip carrying its dance tail keeps its ghost through
            // its own touch and dance, then holds the last pose and fades it out
            // over GP_STAR_FADE_FRAMES, sound included. A clip without the tail
            // still ends at its touch; a live grab still ends every ghost.
            Clip dancer=Clip::linear(40,30,20,5),older=Clip::linear(41,30,20,9);
            dancer.header.flags=GCLIP_DANCE_TAIL;
            for(auto *clip:{&dancer,&older})
                for(uint32_t i=0;i<30;++i) {
                    auto &s=clip->frames[i].sample;
                    if(i>20)s.live_action=0x1302; // still dancing after the touch
                    s.valid_fields|=GC_VALID_SOUND;s.sound.count=1;s.sound.bits[0]=(clip==&dancer?4000u:5000u)+i;
                }
            Clip segment_tail=Clip::linear(42,30,20,5);segment_tail.header.flags=GCLIP_DANCE_TAIL;
            segment_tail.header.clock_kind=GCLIP_SEGMENT_CLOCK;
            check(api->prepare_clip(&segment_tail.header,segment_tail.frames.data())==GP_OK,
                  "a segment-clock clip (the 100-coin star's) may carry a dance tail");
            check(api->release_clip(segment_tail.header.clip_id)==GP_OK,"release the unlisted clip");
            Clip unknown=Clip::linear(43,30,20,5);unknown.header.flags=2;
            check(api->prepare_clip(&unknown.header,unknown.frames.data())==GP_INVALID,"unknown clip flag refused");
            check(api->prepare_clip(&dancer.header,dancer.frames.data())==GP_OK
                  && api->prepare_clip(&older.header,older.frames.data())==GP_OK,"prepare dancer and older clip");
            ghost_playback::set_sound_sink(&test_sink);
            revision=r.revision+1;r=request(dancer,revision);r.model_id=4;r.clip_count=2;r.volume=1;
            std::memcpy(r.clips[1].clip_id,older.header.clip_id,16);
            uint32_t timer_base=30000;
            auto run=[&](uint64_t elapsed) {
                ++r.revision;r.suspended=0;
                r.origin={GO_STAR,7,live.sequence+1,source_continuity,elapsed,source_generation,0,0};
                elapsed_base=elapsed;timer_base+=1000;
                live.level=9;live.area=1;live.live_action=1;live.valid_fields&=~GC_VALID_POSE;
                check(api->configure(&r)==GP_OK,"a new run against the dancer and the older clip");
                renderer.run([&]{ghost_playback::safe_boundary(7,4);});
            };
            auto step=[&](uint64_t time,uint32_t why,std::initializer_list<uint32_t> each,
                          std::initializer_list<uint32_t> expected_sounds) {
                live.igt=uint16_t(time);++live.sequence;++live.dlist_serial;world.list_serial=live.dlist_serial;
                live.global_timer=uint32_t(timer_base+time);
                live.clock={GCC_RAW_VALID|GCC_ELAPSED_VALID,GCC_UNKNOWN_ORIGIN,GCC_STEADY,live.igt,
                    0,0,source_generation,1,elapsed_base+time,source_continuity};
                sound_count=0;for(auto &p:placed)p={};
                bool drawn=false;
                renderer.run([&]{drawn=ghost_playback::evaluate(live,world,poses,count,token);});
                gp_status status{};
                check(api->status(&status,sizeof(status))==GP_OK && status.decision==why,"dance decision");
                unsigned k=0;for(auto value:each)check(status.clip_decisions[k++]==value,"per-ghost dance decision");
                const std::vector<uint32_t> want(expected_sounds),heard(sound_log,sound_log+sound_count);
                check(heard==want,"dance sounds exactly as its records pass");
                return drawn;
            };
            // Ghost grabs first: it dances on, then fades over the constant.
            run(11000);
            check(step(19,GP_READY,{GP_READY,GP_READY},{4019,5019}),"both ghosts before their touch");
            check(step(20,GP_READY,{GP_READY,GP_STAR_ENDED},{4020}) && count==1,"the older clip ends at its touch");
            check(step(29,GP_READY,{GP_READY,GP_STAR_ENDED},{4021,4022,4023,4024,4025,4026,4027,4028,4029})
                  && poses[0].opacity==1 && selected_x==4029,"the dancer draws its whole dance at full opacity");
            float previous=1;
            for(uint32_t k=1;k<GP_STAR_FADE_FRAMES;++k) {
                check(step(29+k,GP_READY,{GP_READY,GP_STAR_ENDED},{}) && count==1,"the dancer fades on its last pose");
                const float expected=float(GP_STAR_FADE_FRAMES-k)/float(GP_STAR_FADE_FRAMES);
                dance_fade[k]=poses[0].opacity;
                check(poses[0].opacity==expected && poses[0].opacity<previous && selected_x==4029,
                      "fade falls by one step a frame from the held last pose");
                check(placed[0].count==1 && placed[0].gain==expected,"its sound fades with it");
                previous=poses[0].opacity;
            }
            check(!step(29+GP_STAR_FADE_FRAMES,GP_STAR_ENDED,{GP_STAR_ENDED,GP_STAR_ENDED},{}),
                  "hidden exactly GP_STAR_FADE_FRAMES after the dance's last record");
            check(placed[0].count==1 && placed[0].gain==0,"its sound is silenced as it hides");
            check(!step(40+GP_STAR_FADE_FRAMES,GP_STAR_ENDED,{GP_STAR_ENDED,GP_STAR_ENDED},{}) && placed[0].gain==0,
                  "and stays silent");
            dance_hidden_after=GP_STAR_FADE_FRAMES;
            // He grabs first: the live grab ends every ghost on that frame,
            // before the dancer reaches its touch.
            run(12000);
            check(step(10,GP_READY,{GP_READY,GP_READY},{4010,5010}),"both ghosts running");
            live.live_action=0x1302;
            check(!step(11,GP_STAR_ENDED,{GP_STAR_ENDED,GP_STAR_ENDED},{}),"a live grab ends every ghost at once");
            live.live_action=1;check(!step(12,GP_STAR_ENDED,{GP_STAR_ENDED,GP_STAR_ENDED},{}),"latched");
            // Ghost first, then he grabs while it dances: the live grab still
            // ends it, and the tracker's suspension that follows keeps it hidden.
            run(13000);
            check(step(25,GP_READY,{GP_READY,GP_STAR_ENDED},{4025}),"dancing while he still plays");
            live.live_action=0x1302;
            check(!step(26,GP_STAR_ENDED,{GP_STAR_ENDED,GP_STAR_ENDED},{}),"his grab ends the dance");
            ++r.revision;r.suspended=1;check(api->configure(&r)==GP_OK,"the tracker suspends after his completion");
            renderer.run([&]{ghost_playback::safe_boundary(7,4);});
            live.live_action=1;check(!step(27,GP_DISABLED,{},{}),"suspended: hidden");
            // The 100-coin star: its clock is its exit segment's (no star_ends),
            // and its run grabs the 100-coin star (rows 10..14, a no-exit dance)
            // before closing on the exit star (row 20) and dancing to row 29.
            // With its dance tail the ghost plays through both grabs and fades
            // after the exit dance; an older clip plays to its end.
            Clip hundred=Clip::linear(44,30,20,5),hundred_older=Clip::linear(45,30,20,9);
            hundred.header.flags=GCLIP_DANCE_TAIL;
            for(auto *clip:{&hundred,&hundred_older}) {
                clip->header.clock_kind=GCLIP_SEGMENT_CLOCK;
                for(uint32_t i=10;i<15;++i)clip->frames[i].sample.live_action=0x1307;
                for(uint32_t i=21;i<30;++i)clip->frames[i].sample.live_action=0x1302;
                check(api->prepare_clip(&clip->header,clip->frames.data())==GP_OK,"prepare a 100-coin run");
            }
            revision=r.revision+1;r=request(hundred,revision);r.model_id=4;r.star_ends=0;r.volume=1;
            r.clip_count=2;std::memcpy(r.clips[1].clip_id,hundred_older.header.clip_id,16);
            r.origin={GO_SEGMENT,7,live.sequence+1,source_continuity,14000,source_generation,0,0};
            elapsed_base=14000;timer_base+=1000;live.live_action=1;
            check(api->configure(&r)==GP_OK,"a 100-coin run on its exit segment's clock");
            renderer.run([&]{ghost_playback::safe_boundary(7,4);});
            check(step(12,GP_READY,{GP_READY,GP_READY},{}) && count==2,"both play through the 100-coin grab");
            check(step(29,GP_READY,{GP_READY,GP_READY},{}) && count==2,"and through the exit star's dance");
            check(step(30,GP_READY,{GP_READY,GP_AFTER_END},{}) && count==1
                  && poses[0].opacity==float(GP_STAR_FADE_FRAMES-1)/float(GP_STAR_FADE_FRAMES),
                  "the dance-tail clip fades after the exit dance; the older one has ended");
            check(!step(29+GP_STAR_FADE_FRAMES,GP_STAR_ENDED,{GP_STAR_ENDED,GP_AFTER_END},{}),"then it hides");
            ghost_playback::set_sound_sink(nullptr);
        }
        check(api->prepare_model(4,rom.data(),uint32_t(rom.size()))==GP_OK,"budget test model");
        {
            Clip maximum(12,GCLIP_MAX_RECORDS);
            check(api->prepare_clip(&maximum.header,maximum.frames.data())==GP_OK,"largest supported clip");
            // Nothing displaces a pending clip; the worker releases what it dropped.
            check(api->release_clip(maximum.header.clip_id)==GP_OK,"release unselected pending clip");
        }
        // Two 40%-budget clips fit; a third cannot. Derive the count from
        // the frame and action-index cost so format growth preserves the test.
        constexpr uint32_t budget_count=uint32_t(uint64_t(GP_CLIP_BUDGET)*2/5
            /(sizeof(gclip_frame)+sizeof(uint32_t)));
        static_assert(budget_count<GCLIP_MAX_RECORDS);
        Clip large(2,budget_count);
        check(api->prepare_clip(&large.header,large.frames.data())==GP_OK,"first budget-sized clip ready");
        revision=r.revision+1;r=request(large,revision);r.model_id=4;check(api->configure(&r)==GP_OK,"own first large clip");
        renderer.run([&]{ghost_playback::safe_boundary(7,4);});
        auto next=large.header;next.clip_id[0]=3;
        check(api->prepare_clip(&next,large.frames.data())==GP_OK,"second large clip ready");
        ++r.revision;r.clips[0].clip_id[0]=3;check(api->configure(&r)==GP_OK,"protect selected pending clip");
        next.clip_id[0]=4;
        check(api->prepare_clip(&next,large.frames.data())==GP_BUDGET,"active and ready clips count against one budget");
        check(api->status(&status,sizeof(status))==GP_OK && status.resident_clip_bytes<=GP_CLIP_BUDGET
            && status.clips_owned==1 && status.clips_ready==1,"resident budget accounts for both owners");
        api->revoke(GP_DISABLED);renderer.run([&]{ghost_playback::retire_on_context_loss();});
        check(api->collect()==GP_OK && api->release_worker()==GP_OK,"large clips reclaimed off renderer");
        ++r.revision;r.clips[0].hue_degrees=360;check(api->configure(&r)==GP_INVALID,"hue range bounded");
        r.clips[0].hue_degrees=0;r.tint_enabled=2;check(api->configure(&r)==GP_INVALID,"tint flag bounded");
        r.tint_enabled=0;r.clip_count=0;check(api->configure(&r)==GP_INVALID,"enabled set needs a clip");
        r.clip_count=GP_GHOSTS+1;check(api->configure(&r)==GP_INVALID,"set size bounded");
        r.clip_count=2;r.clips[1]=r.clips[0];check(api->configure(&r)==GP_INVALID,"duplicate clip refused");
        r.clips[1].clip_id[0]=0;check(api->configure(&r)==GP_INVALID,"zero clip id refused");
        r.clips[1].clip_id[1]=9;r.clips[1].reserved=1;check(api->configure(&r)==GP_INVALID,"clip reserved word checked");
        r.clips[1]={};r.clip_count=1;r.clips[1].hue_degrees=5;
        check(api->configure(&r)==GP_INVALID,"entries past the set are zero");
        r.clips[1]={};
        r.tint_enabled=0;
        r.proximity_enabled=2;check(api->configure(&r)==GP_INVALID,"proximity flag bounded");
        r.proximity_enabled=1;r.proximity_distance=std::numeric_limits<float>::quiet_NaN();
        check(api->configure(&r)==GP_INVALID,"proximity NaN refused");
        r.proximity_distance=401;check(api->configure(&r)==GP_INVALID,"proximity distance bounded");
        r.proximity_distance=0;
        r.shadow_enabled=2;check(api->configure(&r)==GP_INVALID,"shadow flag bounded");
        r.shadow_enabled=1;r.occlusion_fade=2;check(api->configure(&r)==GP_INVALID,"occlusion flag bounded");
        r.occlusion_fade=0;r.volume=1.5f;check(api->configure(&r)==GP_INVALID,"volume bounded");
        r.volume=std::numeric_limits<float>::quiet_NaN();check(api->configure(&r)==GP_INVALID,"volume NaN refused");
        r.volume=.5f;r.reserved3=1;check(api->configure(&r)==GP_INVALID,"sound reserved word checked");
        r.reserved3=0;
        r.suspended=2;check(api->configure(&r)==GP_INVALID,"suspension flag bounded");
        r.suspended=0;r.reserved4=1;check(api->configure(&r)==GP_INVALID,"suspension reserved word checked");
        r.reserved4=0;
        r.origin.continuity=0;check(api->configure(&r)==GP_INVALID,"follow requires resolved origin");
        r.origin.continuity=1;r.mode=GP_FIXED;
        check(api->configure(&r)==GP_INVALID,"fixed must not accidentally retain a live origin");
        r.origin={};
        r.revision=UINT64_MAX;check(api->configure(&r)==GP_EXHAUSTED,"revision exhaustion fails closed");
        check(api->release_worker()==GP_OK,"final worker release");
        check(allocations.load()==0 && destructions.load()==0,"render path never allocates or destroys storage");
        check(opens==closes && !fake_model,"all model lifetimes reclaimed");
        std::printf("{\"ok\":true,\"checks\":%u,\"models\":%u,\"render_allocations\":%u,\"render_frees\":%u,"
            "\"multi\":{\"ghosts\":%u,\"max_visible\":%u,\"prepares\":%u},\"dance\":{\"hidden_after\":%u,\"fade\":[1",
            checks,opens,allocations.load(),destructions.load(),multi_ghosts,multi_visible,multi_prepares,dance_hidden_after);
        for(uint32_t k=1;k<GP_STAR_FADE_FRAMES;++k)std::printf(",%.6f",dance_fade[k]);
        std::printf(",0]}}\n");return 0;
    } catch(const std::exception& error) {
        std::fprintf(stderr,"playback witness: %s (check %u)\n",error.what(),checks);return 1;
    }
}
