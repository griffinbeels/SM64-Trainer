#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "ghost_playback.h"
#include "ghost_scene.h"
#include "ghost_proximity.h"
#include "ghost_sound_fade.h"
#include "ghost_pose.h"
#include "ghost_particle_pose.h"
#include "ghost_compositor.h"
#include "ghost_annotation_assets.h"
#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstring>
#include <limits>
#include <new>

namespace {
template<class T> struct Mailbox {
    static_assert(sizeof(T)%4==0);
    std::atomic<uint32_t> sequence{0}, words[sizeof(T)/4]{};
    bool put(const T& value) {
        const auto before=sequence.load();
        if(before>=UINT32_MAX-2) return false;
        uint32_t copy[sizeof(T)/4]; std::memcpy(copy,&value,sizeof(value));
        sequence.store(before+1);
        for(size_t i=0;i<sizeof(copy)/4;++i) words[i].store(copy[i]);
        sequence.store(before+2); return true;
    }
    bool get(T& value) const {
        for(unsigned attempt=0;attempt<2;++attempt) {
            const auto before=sequence.load();
            if(before&1) continue;
            uint32_t copy[sizeof(T)/4];
            for(size_t i=0;i<sizeof(copy)/4;++i) copy[i]=words[i].load();
            if(sequence.load()==before) {std::memcpy(&value,copy,sizeof(value));return true;}
        }
        return false;
    }
};
// Unwanted: renderer-held storage the worker released; it retires at the next boundary.
enum ClipState : uint32_t { Empty, Building, Ready, Owned, Retired, Reclaiming, Unwanted };
// A row where the recorded raw IGT restarted (fell below the row before).
struct Restart {int16_t level=0,area=0;uint32_t index=0;};
struct Clip {
    std::atomic<uint32_t> state{Empty};
    uint32_t cancellation=0, index_count=0, action_count=0, restart_count=0;
    Restart restarts[GP_RESTARTS]{}; // oldest first; the latest GP_RESTARTS kept
    uint64_t allocation=0;
    gclip_header header{};
    gclip_frame *frames=nullptr;
    uint32_t *actions=nullptr; // first canonical occurrence per action, sorted by action
} clips[GP_CLIP_SLOTS];
struct Model {
    std::atomic<uint32_t> state{GP_MODEL_EMPTY};
    uint32_t cancellation=0;
    uint64_t id=0;
    sg_model *value=nullptr;
    ghost_annotation::Assets annotations; // worker builds; renderer borrows with the model
    bool reclaim_owned=false; // worker only while RECLAIMING
} model;
std::atomic_flag worker_gate=ATOMIC_FLAG_INIT;
DWORD worker_thread=0; // protected by worker_gate
std::atomic<uint32_t> cancellation{1}, context_serial{1};
std::atomic<bool> exhausted{false};
std::atomic<uint64_t> desired_model{0}, enabled_revision{0}, resident_bytes{0};
std::atomic<uint64_t> terminal_revision{0}; // renderer publishes; worker checks same applied lease
static_assert(GP_GHOSTS==ghost_compositor::max_poses,"one batch draws every displayed ghost");
static_assert(uint64_t(GP_CLIP_SLOTS)*GP_LONG_GHOST_FRAMES*(sizeof(gclip_frame)+sizeof(uint32_t))
              <=GP_CLIP_BUDGET,"a full set of long ghosts and its replacement fit the clip budget");
std::atomic<uint32_t> evaluations{0}, rendered{0}, gaps{0}, refusals{0}, adoption_busy{0};
std::atomic<int32_t> model_error{SG_OK};
struct PublishedRequest {gp_request value{};uint32_t cancellation=0,reserved=0;};
Mailbox<PublishedRequest> requests;
Mailbox<gp_status> reports;
uint64_t last_revision=0;

// Everything below is renderer-owned until context loss releases the binding.
DWORD render_thread=0;
uint32_t live_rom=0, live_configuration=0, acquired_cancel=0;
bool have_model=false, have_request=false, have_timeline=false, star_ended=false;
bool terminal_pending=false; // completion command can precede its first render list
bool sequence_reported=false;
bool held[GP_CLIP_SLOTS]{};          // this renderer owns the slot (Owned or Unwanted)
Clip *bound[GP_GHOSTS]{};            // request order; null until adopted
// Per ghost: the coordinate shift aligning its clip to the live attempt's
// restart, fixed for one (clip id, attempt) pair. An attempt begins at an
// unrecovered origin, in the scene live Mario is in when it first applies; a
// restore continues it. Keyed on the recording, not its slot, so a selection
// change, a toggle or a capture restart re-adopting clips elsewhere aligns
// each clip to the attempt's scene.
struct Attempt {uint64_t sequence;int16_t level,area;bool known;} attempt{};
int64_t align[GP_GHOSTS]{}; // restart_shift for a star, zero_shift for a segment
uint64_t aligned_attempt[GP_GHOSTS]{};
uint8_t aligned_id[GP_GHOSTS][16]{};
bool aligned[GP_GHOSTS]{};
gp_request active_request{};
gp_request timeline_request{};
gp_status report{};
uint64_t touch_revision=0, touch[GP_GHOSTS]{};
bool have_touch[GP_GHOSTS]{};
// Defeat (his asks, 2026-09-23 and 2026-09-28; ghost_tuning.h): when live
// Mario's x-cam dance begins on the grab that completes the attempt, each
// ghost whose own dance began later dies where it stood; one whose dance began
// first, or on the same frame, dances on. The request's star actions are the
// dances (memory/addresses.py STAR_DANCE_ACTIONS), never the midair fall
// before one: a grab that is slow to land loses to a later grab that dances
// first. completion: the coordinate a clip's own dance began (a dance tail's
// final run of dance actions, else its first recorded dance). shown: what each
// ghost drew on the latest frame and the live elapsed it drew it at, the
// evidence the defeat is decided from. dance_entry: the live elapsed his
// latest dance began at, so a completion that reaches playback later (the
// tracker's suspension) is still judged at his x-cam.
uint64_t completion[GP_GHOSTS]{};
bool have_completion[GP_GHOSTS]{};
struct Shown {bool drawn=false;uint32_t row=0;uint64_t target=0,elapsed=0;const Clip *clip=nullptr;};
Shown shown_last[GP_GHOSTS]{};
struct DanceEntry {bool dancing=false,known=false;uint64_t elapsed=0,continuity=0;} dance_entry;
// A dance the recording cut short (his ask, 2026-09-28: a pause, reset or
// state load right after the grab): its first frame (actionTimer 1), last
// recorded coordinate and the game's own last dance frame, all coordinates.
// Past the recording the ghost keeps its last recorded dance pose and plays
// the rest of the dance by animation (ghost_tuning.h GP_DANCE_*).
struct Dance {
    bool known=false;int16_t animation=0;uint32_t peace=0;int32_t frames=0;
    uint64_t start=0,recorded=0,end=0;
} dance[GP_GHOSTS];
bool dance_voiced[GP_GHOSTS]{};
struct Defeat {
    bool active=false,spent=false;
    uint32_t start=0,hold=0; // live gGlobalTimer at the trigger; the longest death
    uint16_t clock=0;        // live gAreaUpdateCounter at the trigger: the defeat's own clock
    int32_t fall=0,drown=0,limp=0; // the death animations' lengths in this ROM
    int16_t level=0,area=0;
    uint32_t epoch=0,last_timer=0;
    uint16_t last_clock=0,last_igt=0;
    bool loser[GP_GHOSTS]{},winner[GP_GHOSTS]{},cued[GP_GHOSTS]{},water[GP_GHOSTS]{};
    uint32_t row[GP_GHOSTS]{};uint64_t target[GP_GHOSTS]{};
    const Clip *clip[GP_GHOSTS]{};
} defeat;
ghost_pose::Scratch scratch[GP_GHOSTS]; // one borrowed mesh per displayed ghost
float displayed_position[GP_GHOSTS][3]{}; // the rebased root shared by picture and sound
// The worker's recovery, repeated on the renderer from the same live samples,
// so the frame a state loads can already draw at its restored coordinate.
// The worker's next command confirms it (or replaces it with its own).
struct LiveRecovery {
    ghost_origin::Recovery timing;
    gp_request owner{};          // request whose origin the timing was bound from
    uint64_t restored=0;         // sequence of the last origin evaluate used
    bool bound=false;
    LiveRecovery() {timing.reserve();} // capacity before any renderer use
    void clear() {timing.clear();owner={};restored=0;bound=false;}
    // Revoked or withdrawn: nothing follows it now, but its checkpoints stay
    // for a worker restore of the same run (adopt_recovery decides).
    void detach() {timing.forget_stream();restored=0;bound=false;}
} live_recovery;
// Ghost sound: the last record each ghost displayed, so each record's
// recorded sounds are handed over exactly once, as the ghost passes it.
constexpr int64_t unheard=INT64_MIN;
std::atomic<const ghost_playback::SoundSink*> sound_sink{nullptr};
int64_t heard[GP_GHOSTS]{};           // last displayed record index, or unheard
int64_t loudest[GP_GHOSTS]{};         // highest record whose sounds were handed over
const Clip *heard_clip[GP_GHOSTS]{};
uint64_t suspend_mark=0;              // live restore the suspension began at
bool heard_fresh[GP_GHOSTS]{};        // a new run: its first displayed record is newly passed
bool loudest_reset[GP_GHOSTS]{};      // re-establish loudest at the next displayed record
float fade_gain[GP_GHOSTS]{};         // the sound's share of its picture's fade, last drawn frame
bool fade_gain_known[GP_GHOSTS]{};    // false: the next drawn frame takes the fade as it is
// run: a new attempt. restore: a state load (records ahead are newly passed
// again). move: a nudge, seek or pause; records already heard stay silent.
enum class Forget { run, restore, move };
void forget_heard(Forget why) {
    for(uint32_t i=0;i<GP_GHOSTS;++i) {
        heard[i]=unheard;
        if(why==Forget::move)continue;
        heard_clip[i]=nullptr;heard_fresh[i]=why==Forget::run;loudest_reset[i]=true;dance_voiced[i]=false;
        fade_gain_known[i]=false; // a restart overlapping him starts quiet
    }
}
void sound_control(bool playing,float volume) {
    const auto *sink=sound_sink.load(std::memory_order_acquire);
    if(sink && sink->control)sink->control(playing?1u:0u,playing?volume:0.0f);
}
struct ShadowScratch {
    ghost_compositor::ShadowVertex vertices[24];
    uint8_t texture[256];
} shadows[GP_GHOSTS]; // borrowed shadow per displayed ghost
ghost_compositor::ParticleVertex particle_vertices[GP_GHOSTS][ghost_compositor::particle_vertices];

int worker_enter() {
    if(worker_gate.test_and_set(std::memory_order_acquire)) return GP_BUSY;
    const auto thread=GetCurrentThreadId();
    if(worker_thread && worker_thread!=thread) {
        worker_gate.clear(std::memory_order_release); return GP_WRONG_THREAD;
    }
    worker_thread=thread; return GP_OK;
}
int worker_leave(int result) {worker_gate.clear(std::memory_order_release);return result;}
void __cdecl revoke(uint32_t) {
    enabled_revision.store(0,std::memory_order_release);
    desired_model.store(0,std::memory_order_release);
    if(cancellation.fetch_add(1,std::memory_order_acq_rel)>=UINT32_MAX-1) exhausted.store(true);
}
void __cdecl withdraw() {enabled_revision.store(0,std::memory_order_release);}
bool id_equal(const uint8_t *a,const uint8_t *b) {return std::memcmp(a,b,16)==0;}
bool nonzero_id(const uint8_t *id) {
    uint8_t value=0; for(unsigned i=0;i<16;++i)value|=id[i]; return value!=0;
}
void free_clip(Clip& clip) {
    delete[] clip.frames; delete[] clip.actions;
    resident_bytes.store(resident_bytes.load()-clip.allocation);
    clip.frames=nullptr; clip.actions=nullptr; clip.header={};
    clip.allocation=0; clip.index_count=clip.action_count=clip.restart_count=0;
    clip.state.store(Empty,std::memory_order_release);
}
int reclaim_model() {
    uint32_t expected=GP_MODEL_RETIRED;
    if(model.state.compare_exchange_strong(expected,GP_MODEL_RECLAIMING)) {
        const int result=sg_model_adopt(model.value);
        if(result!=SG_OK) {
            model_error.store(result);
            if(result==SG_BUSY)model.state.store(GP_MODEL_RETIRED,std::memory_order_release);
            return GP_DEFERRED; // unexpected ownership remains quarantined
        }
        model.reclaim_owned=true;
    }
    if(model.state.load()!=GP_MODEL_RECLAIMING)return GP_OK;
    if(!model.reclaim_owned)return GP_DEFERRED;
    const int result=sg_model_close(&model.value);
    if(result!=SG_OK) {model_error.store(result);return GP_DEFERRED;}
    model.reclaim_owned=false;
    model.id=0; model.cancellation=0;
    model.state.store(GP_MODEL_EMPTY,std::memory_order_release); return GP_OK;
}
int collect_impl() {
    const auto current_cancel=cancellation.load();
    for(auto &clip:clips) {
        auto state=clip.state.load(std::memory_order_acquire);
        if(state==Ready && clip.cancellation!=current_cancel) {
            uint32_t expected=Ready;
            clip.state.compare_exchange_strong(expected,Retired);
        }
        uint32_t expected=Retired;
        if(clip.state.compare_exchange_strong(expected,Reclaiming)) free_clip(clip);
    }
    if(model.state.load(std::memory_order_acquire)==GP_MODEL_READY &&
            (model.cancellation!=current_cancel || model.id!=desired_model.load())) {
        uint32_t expected=GP_MODEL_READY;
        model.state.compare_exchange_strong(expected,GP_MODEL_RETIRED);
    }
    return reclaim_model();
}
int32_t __cdecl collect() {
    const int entered=worker_enter(); if(entered!=GP_OK)return entered;
    return worker_leave(collect_impl());
}
int32_t __cdecl prepare_model(uint64_t id,const uint8_t *rom,uint32_t bytes) {
    const int entered=worker_enter(); if(entered!=GP_OK)return entered;
    if(!id || !rom || bytes!=8u*1024u*1024u || exhausted.load()) return worker_leave(GP_INVALID);
    const auto before=cancellation.load(); desired_model.store(id);
    collect_impl();
    auto state=model.state.load(std::memory_order_acquire);
    if(state==GP_MODEL_READY || state==GP_MODEL_RENDER_OWNED) {
        return worker_leave(model.id==id && model.cancellation==before?GP_OK:GP_DEFERRED);
    }
    uint32_t expected=GP_MODEL_EMPTY;
    if(!model.state.compare_exchange_strong(expected,GP_MODEL_BUILDING)) return worker_leave(GP_DEFERRED);
    int result=sg_model_open(rom,bytes,&model.value);
    if(result==SG_OK && cancellation.load()!=before) {
        sg_model_close(&model.value); result=SG_BUSY;
    }
    if(result==SG_OK) result=sg_model_detach(model.value);
    if(result!=SG_OK) {
        model_error.store(result);
        if(model.value) {
            // Open succeeded but ownership could not be released. This worker
            // keeps custody; no render thread may acquire a partial model.
            if(sg_model_close(&model.value)!=SG_OK) {
                model.reclaim_owned=true;
                model.state.store(GP_MODEL_RECLAIMING); return worker_leave(GP_DEFERRED);
            }
        }
        model.state.store(GP_MODEL_EMPTY); return worker_leave(GP_MODEL_ERROR);
    }
    // A missing optional font atlas never refuses the body model. Extraction
    // happens on this worker, while the verified ROM copy is still resident.
    try {model.annotations.load(rom,bytes);}catch(const std::bad_alloc&) {model.annotations.ready=false;}
    model.id=id; model.cancellation=before; model_error.store(SG_OK);
    model.state.store(GP_MODEL_READY,std::memory_order_release); return worker_leave(GP_OK);
}

bool valid_clock(const gc_sample& s) {
    const auto& c=s.clock;
    if((c.valid&~7u) || (c.refusal&~1023u) || c.transition>GCC_TELEPORT)return false;
    if(c.valid&GCC_RAW_VALID) {
        if((s.valid_fields&(GC_VALID_CLOCK|GC_VALID_ACTION|GC_VALID_WARP))
                !=(GC_VALID_CLOCK|GC_VALID_ACTION|GC_VALID_WARP) || c.raw_igt!=s.igt || !c.leg_id)return false;
    } else if(c.valid || c.raw_igt || c.origin_id || c.leg_id)return false;
    if(c.valid&GCC_PROGRESS_VALID) {
        if(c.refusal || !c.origin_id || c.banked_frames>UINT64_MAX-c.raw_igt
                || c.progress_frames!=c.banked_frames+c.raw_igt)return false;
    } else if(c.banked_frames || c.progress_frames)return false;
    if(c.valid&GCC_ELAPSED_VALID) {if(!c.continuity_id)return false;}
    else if(c.elapsed_frames || c.continuity_id)return false;
    return true;
}
bool elapsed_coordinate(uint64_t elapsed,uint64_t origin,uint32_t offset,uint64_t& value) {
    if(elapsed<origin || elapsed-origin>UINT64_MAX-offset)return false;
    value=elapsed-origin+offset;return true;
}
uint64_t clip_coordinate(const Clip& clip,uint32_t index) {
    const auto& h=clip.header;
    // prepare_clip proved subtraction/addition bounds for every stored row.
    return clip.frames[index].sample.clock.elapsed_frames-h.origin_elapsed
        +(h.clock_kind==GCLIP_STAR_CLOCK?h.origin_raw:0);
}
bool recorded_touch(const Clip& clip,const gp_request& request,uint64_t& recorded) {
    bool found=false;recorded=0;
    if(request.mode!=GP_FOLLOW || !request.star_ends)return false;
    // Preparation indexed first occurrences once. A changed command needs at
    // most GP_STAR_ACTIONS binary searches, never a render-thread clip scan.
    for(uint32_t i=0;i<request.star_action_count;++i) {
        uint32_t lo=0,hi=clip.action_count;
        while(lo<hi) {
            const uint32_t middle=lo+(hi-lo)/2;
            if(clip.frames[clip.actions[middle]].sample.live_action<request.star_actions[i])lo=middle+1;
            else hi=middle;
        }
        if(lo==clip.action_count || clip.frames[clip.actions[lo]].sample.live_action!=request.star_actions[i])continue;
        const auto coordinate=clip_coordinate(clip,clip.actions[lo]);
        if(!found || coordinate<recorded)recorded=coordinate;
        found=true;
    }
    return found;
}
bool grab_action(const gp_request& request,uint32_t action) {
    for(uint32_t i=0;i<request.star_action_count && i<GP_STAR_ACTIONS;++i)
        if(request.star_actions[i] && action==request.star_actions[i])return true;
    return false;
}
// A dance tail ends in the dance of the grab that completed its attempt: the
// final unbroken run of star actions (the dances), walked back from the clip's
// end (bounded; the tail is at most 300 records). Its first row is the ghost's
// x-cam: after a midair grab that is the landing, not the grab. Earlier grabs
// (the 100-coin star before its exit star) are not the completing one. A clip
// without a tail completes at its recorded touch, its first recorded dance.
bool completing_grab(const Clip& clip,const gp_request& request,bool touched,uint64_t touch_at,uint64_t& at) {
    at=0;
    if(!(clip.header.flags&GCLIP_DANCE_TAIL)) {at=touch_at;return touched;}
    uint32_t first=clip.index_count;
    while(first>0 && clip.index_count-first<GP_DEFEAT_SCAN) {
        const auto &s=clip.frames[first-1].sample;
        if(!(s.valid_fields&GC_VALID_ACTION) || !grab_action(request,s.live_action))break;
        --first;
    }
    if(first==clip.index_count)return false;
    at=clip_coordinate(clip,first);return true;
}
// A dance tail whose recording stops before the game's dance ends. The dance
// proper starts on the first row of its final run that shows a dance
// animation (a midair grab's landing row still shows the fall), where the
// game's actionTimer is 1 and the animation frame is actionTimer - 1; the kind
// (the ground or water animation, exit or no-exit) sets when the hand shows the
// peace sign and the frame the dance ends on (ghost_tuning.h GP_DANCE_*).
bool cut_dance(const Clip& clip,const gp_request& request,Dance& out) {
    out={};
    if(!(clip.header.flags&GCLIP_DANCE_TAIL) || !clip.index_count)return false;
    const auto &end=clip.frames[clip.index_count-1].sample;
    if(!(end.valid_fields&GC_VALID_ACTION) || !grab_action(request,end.live_action))return false;
    uint32_t first=clip.index_count;
    while(first>0 && clip.index_count-first<GP_DEFEAT_SCAN) {
        const auto &s=clip.frames[first-1].sample;
        if(!(s.valid_fields&GC_VALID_ACTION) || !grab_action(request,s.live_action))break;
        --first;
    }
    for(uint32_t i=first;i<clip.index_count && !out.animation;++i) {
        const auto &p=clip.frames[i].sample.pose;
        const uint64_t at=clip_coordinate(clip,i);
        if(!(clip.frames[i].sample.valid_fields&GC_VALID_POSE) || p.animation_frame<0
                || uint64_t(p.animation_frame)>at
                || (p.animation_id!=GP_DANCE_ANIMATION && p.animation_id!=GP_WATER_DANCE_ANIMATION))continue;
        out.animation=p.animation_id;out.start=at-uint64_t(p.animation_frame);
    }
    if(!out.animation)return false;
    out.peace=out.animation==GP_WATER_DANCE_ANIMATION?GP_WATER_DANCE_PEACE_TIMER:GP_DANCE_PEACE_TIMER;
    out.recorded=clip_coordinate(clip,clip.index_count-1);
    out.end=out.start+(end.live_action==GP_DANCE_NO_EXIT_ACTION?GP_DANCE_NO_EXIT_END_TIMER:GP_DANCE_END_TIMER)-1;
    out.known=out.end>out.recorded;
    return out.known;
}
// A shadow rides only on a valid executed root; its texture is alpha only.
bool valid_shadow(const gc_sample& s) {
    const gc_shadow zero{};
    if(!(s.valid_fields&GC_VALID_SHADOW))return !std::memcmp(&s.shadow,&zero,sizeof(zero));
    const auto& m=s.shadow.matrix;
    if(!(s.valid_fields&GC_VALID_POSE) || s.pose.root_kind!=GC_ROOT_SCALED_MODELVIEW
            || s.shadow.reserved)return false;
    for(float value:m)if(!std::isfinite(value))return false;
    return m[3]==0 && m[7]==0 && m[11]==0 && m[15]==1;
}
// Particles: bounded count, overflow only when full, unused entries zero.
// A held object rides only a pose, with a segment-0x13 behaviour.
bool valid_held(const gc_sample& s) {
    const gc_held zero{};
    if(!(s.valid_fields&GC_VALID_HELD))return !std::memcmp(&s.held,&zero,sizeof(zero));
    const auto& h=s.held;
    // animation_id -1: an object without an animation, at frame 0.
    if(!(s.valid_fields&GC_VALID_POSE) || (h.behavior>>16)!=0x1300u || h.anim_state>255
            || h.animation_id<-1 || h.animation_frame<0 || (h.animation_id==-1 && h.animation_frame))return false;
    // In his hand the orientation is Mario's root: no angle of its own.
    if(h.released>1 || (!h.released && (h.angles[0] || h.angles[1] || h.angles[2])))return false;
    for(unsigned axis=0;axis<3;++axis)
        if(!std::isfinite(h.holp[axis]) || !std::isfinite(h.scale[axis]) || h.scale[axis]==0)return false;
    return true;
}
bool valid_particles(const gc_sample& s) {
    const gc_particles zero{};
    if(!(s.valid_fields&GC_VALID_PARTICLES))return !std::memcmp(&s.particles,&zero,sizeof(zero));
    const auto& d=s.particles;
    if(d.count>GC_PARTICLES || (d.flags&~uint8_t(GC_PARTICLES_OVERFLOW)) || d.reserved[0] || d.reserved[1]
            || ((d.flags&GC_PARTICLES_OVERFLOW) && d.count!=GC_PARTICLES))return false;
    const gc_particle empty{};
    for(uint32_t i=0;i<GC_PARTICLES;++i) {
        const auto& e=d.entries[i];
        if(i>=d.count) {if(std::memcmp(&e,&empty,sizeof(e)))return false;continue;}
        if(e.opacity && (e.opacity&~0x1ffu || !(e.opacity&GC_PARTICLE_OPACITY)))return false;
        for(float value:e.translation)if(!std::isfinite(value))return false;
    }
    return true;
}
bool valid_sound(const gc_sample& s) {
    const gc_sound zero{};
    if(!(s.valid_fields&GC_VALID_SOUND))return !std::memcmp(&s.sound,&zero,sizeof(zero));
    const auto& d=s.sound;
    if(d.count>GC_SOUND_EVENTS || (d.flags&~uint8_t(GC_SOUND_OVERFLOW))
            || ((d.flags&GC_SOUND_OVERFLOW) && d.count!=GC_SOUND_EVENTS))return false;
    for(uint32_t i=d.count;i<GC_SOUND_EVENTS;++i)if(d.bits[i])return false;
    return true;
}
bool valid_clip(const gclip_header& h,const gclip_frame *frames,uint32_t& indexed) {
    if(std::memcmp(h.magic,GCLIP_MAGIC,8) || h.version!=GCLIP_VERSION || h.bytes!=sizeof(h)
            || h.record_bytes!=sizeof(gclip_frame) || !h.record_count || h.record_count>GCLIP_MAX_RECORDS
            || h.rom_layout!=GC_ROM_US || h.sample_version!=GC_VERSION || !h.configuration || !h.epoch
            || !nonzero_id(h.clip_id) || !h.qpc_frequency || h.qpc_anchor<0
            || !std::isfinite(h.utc_anchor) || h.utc_anchor<=0
            || h.level<4 || h.level>36 || h.area<1 || h.area>8 || (h.flags&~GCLIP_DANCE_TAIL)
            || h.reserved[0] || h.reserved[1] || !frames
            || (h.clock_kind!=GCLIP_STAR_CLOCK && h.clock_kind!=GCLIP_SEGMENT_CLOCK)
            || !h.origin_continuity || h.origin_raw>UINT16_MAX) return false;
    indexed=0;uint32_t prior_timer=0;uint64_t sequence=0,serial=0,prior_elapsed=0;
    int64_t prior_qpc=0;bool playable=false,indexing=true;
    for(uint32_t i=0;i<h.record_count;++i) {
        const auto &f=frames[i]; const auto &s=f.sample;
        if(f.bytes!=sizeof(f) || f.version!=GCLIP_VERSION || f.configuration!=h.configuration
                || s.bytes!=sizeof(s) || s.version!=GC_VERSION || s.epoch!=h.epoch
                || s.sequence<=sequence || s.dlist_serial<=serial || (s.valid_fields&~GC_VALID_ALL)
                || s.status>GC_UNSUPPORTED_HEAD
                || s.qpc<prior_qpc || (!s.status && (s.valid_fields&GC_VALID_REQUIRED)!=GC_VALID_REQUIRED)
                || bool(s.valid_fields&GC_VALID_ACTION)!=bool(s.live_action)
                || f.world_status>static_cast<uint32_t>(ghost::PassStatus::InvalidIdentity)) return false;
        // A refused pose keeps only its refusal reason.
        gc_pose zero_pose{};zero_pose.refusal=s.pose.refusal;const gc_camera zero_camera{};
        if(s.pose.refusal>GC_REFUSED_LAST)return false;
        if(!(s.valid_fields&GC_VALID_POSE) && std::memcmp(&s.pose,&zero_pose,sizeof(s.pose)))return false;
        if(!(s.valid_fields&GC_VALID_CAMERA) && std::memcmp(&s.camera,&zero_camera,sizeof(s.camera)))return false;
        if(!(s.valid_fields&GC_VALID_WARP) && (s.pending_warp_op || s.delayed_warp_timer))return false;
        if(!valid_shadow(s) || !valid_sound(s) || !valid_particles(s) || !valid_held(s)
                || !ghost_annotation::valid_pad(s))return false;
        if(s.valid_fields&GC_VALID_POSE)for(unsigned axis=0;axis<3;++axis)
            if(!std::isfinite(s.pose.position[axis]) || !std::isfinite(s.pose.scale[axis]))return false;
        if(s.valid_fields&GC_VALID_POSE) {
            const auto &p=s.pose;
            if(p.root_kind==GC_ROOT_TRS) {
                const float empty[16]{};
                if(std::memcmp(p.root_matrix,empty,sizeof(empty)))return false;
            } else if(p.root_kind==GC_ROOT_SCALED_MODELVIEW) {
                if(!(s.valid_fields&GC_VALID_CAMERA))return false;
                for(float value:p.root_matrix)if(!std::isfinite(value))return false;
                if(p.root_matrix[3]!=0 || p.root_matrix[7]!=0 || p.root_matrix[11]!=0
                        || p.root_matrix[15]!=1)return false;
            } else return false;
        }
        if(s.valid_fields&GC_VALID_CAMERA) {
            for(unsigned axis=0;axis<3;++axis)
                if(!std::isfinite(s.camera.position[axis]) || !std::isfinite(s.camera.focus[axis]))return false;
            if(!std::isfinite(s.camera.fov))return false;
        }
        if(!valid_clock(s) || (s.clock.valid&(GCC_RAW_VALID|GCC_ELAPSED_VALID))
                !=(GCC_RAW_VALID|GCC_ELAPSED_VALID) || s.clock.continuity_id!=h.origin_continuity
                || s.level<4 || s.level>36 || s.area<1 || s.area>8
                || (i && (s.global_timer<prior_timer || s.clock.elapsed_frames<prior_elapsed)))return false;
        uint64_t coordinate=0;
        if(!elapsed_coordinate(s.clock.elapsed_frames,h.origin_elapsed,
                h.clock_kind==GCLIP_STAR_CLOCK?h.origin_raw:0,coordinate))return false;
        if(h.clock_kind==GCLIP_STAR_CLOCK && s.clock.origin_id!=frames[0].sample.clock.origin_id)indexing=false;
        if(indexing) {
            ++indexed;
            if(s.valid_fields&GC_VALID_POSE)playable=true;
        }
        sequence=s.sequence;serial=s.dlist_serial;prior_qpc=s.qpc;
        prior_timer=s.global_timer;prior_elapsed=s.clock.elapsed_frames;
    }
    const auto &first=frames[0].sample, &last=frames[h.record_count-1].sample;
    return playable && indexed && (first.valid_fields&GC_VALID_CLOCK) && (last.valid_fields&GC_VALID_CLOCK)
        && first.sequence==h.first_sequence && last.sequence==h.last_sequence
        && first.global_timer==h.first_global_timer && last.global_timer==h.last_global_timer
        && first.level==h.level && first.area==h.area && first.sequence==h.origin_sequence
        && first.global_timer==h.origin_global_timer && first.igt==h.origin_raw
        && first.clock.elapsed_frames==h.origin_elapsed;
}
int32_t __cdecl prepare_clip(const gclip_header *header,const gclip_frame *frames) {
    const int entered=worker_enter(); if(entered!=GP_OK)return entered;
    uint32_t indexed=0;
    if(!header || exhausted.load() || !valid_clip(*header,frames,indexed)) return worker_leave(GP_INVALID);
    const auto before=cancellation.load(); collect_impl();
    for(auto &clip:clips) {
        const auto state=clip.state.load(std::memory_order_acquire);
        if((state!=Ready && state!=Owned && state!=Unwanted) || !id_equal(clip.header.clip_id,header->clip_id))continue;
        // A released copy the renderer still holds cannot be revived safely.
        if(state==Unwanted)return worker_leave(GP_DEFERRED);
        if(clip.cancellation==before)
            return worker_leave(std::memcmp(&clip.header,header,sizeof(*header)) ||
                std::memcmp(clip.frames,frames,header->record_count*sizeof(*frames))?GP_INVALID:GP_OK);
    }
    // Owned/retired storage counts until its real release; nothing is displaced.
    const uint64_t allocation=uint64_t(header->record_count)*sizeof(gclip_frame)+uint64_t(indexed)*sizeof(uint32_t);
    Clip *chosen=nullptr;
    for(auto &clip:clips) {
        uint32_t expected=Empty;
        if(clip.state.compare_exchange_strong(expected,Building)) {chosen=&clip;break;}
    }
    if(!chosen) return worker_leave(GP_DEFERRED);
    if(allocation>GP_CLIP_BUDGET-resident_bytes.load()) {
        chosen->state.store(Empty); return worker_leave(GP_BUDGET);
    }
    auto *copy=new(std::nothrow) gclip_frame[header->record_count];
    auto *actions=new(std::nothrow) uint32_t[indexed];
    if(!copy || !actions) {
        delete[] copy; delete[] actions; chosen->state.store(Empty); return worker_leave(GP_BUDGET);
    }
    std::memcpy(copy,frames,header->record_count*sizeof(*copy));
    uint32_t action_count=0;
    for(uint32_t i=0;i<indexed;++i)
        if(copy[i].sample.valid_fields&GC_VALID_ACTION)actions[action_count++]=i;
    std::sort(actions,actions+action_count,[copy](uint32_t a,uint32_t b) {
        const auto left=copy[a].sample.live_action,right=copy[b].sample.live_action;
        return left<right || (left==right && a<b);
    });
    uint32_t unique=0;
    for(uint32_t i=0;i<action_count;++i)
        if(!unique || copy[actions[i]].sample.live_action!=copy[actions[unique-1]].sample.live_action)
            actions[unique++]=actions[i];
    uint32_t restarts=0;
    for(uint32_t i=1;i<indexed && header->clock_kind==GCLIP_STAR_CLOCK;++i) {
        const auto &s=copy[i].sample;
        if(s.clock.raw_igt>=copy[i-1].sample.clock.raw_igt)continue;
        if(restarts==GP_RESTARTS) {
            std::memmove(chosen->restarts,chosen->restarts+1,(GP_RESTARTS-1)*sizeof(Restart));--restarts;
        }
        chosen->restarts[restarts++]={s.level,s.area,i};
    }
    chosen->restart_count=restarts;
    chosen->frames=copy; chosen->actions=actions; chosen->index_count=indexed;chosen->action_count=unique;
    chosen->header=*header; chosen->allocation=allocation; chosen->cancellation=before;
    resident_bytes.store(resident_bytes.load()+allocation);
    chosen->state.store(before==cancellation.load()?Ready:Retired,std::memory_order_release);
    return worker_leave(before==cancellation.load()?GP_OK:GP_DEFERRED);
}
bool valid_request(const gp_request& r) {
    if(r.bytes!=sizeof(r) || r.version!=GP_VERSION || !r.revision
            || r.enabled>1 || r.tint_enabled>1 || r.clip_count>GP_GHOSTS || r.reserved1
            || r.reserved0 || r.proximity_enabled>1 || !std::isfinite(r.proximity_distance)
            || r.proximity_distance<0 || r.proximity_distance>GP_PROXIMITY_MAX
            || r.shadow_enabled>1 || r.occlusion_fade>1 || r.sound_fade>1
            || !std::isfinite(r.volume) || r.volume<0 || r.volume>1
            || r.suspended>1 || r.reserved4 || r.names_enabled>1 || r.inputs_enabled>1
            || r.input_mode>GP_INPUT_VALUES || r.name_background>GP_NAME_WHITE
            || !std::isfinite(r.name_background_opacity) || r.name_background_opacity<0
            || r.name_background_opacity>1 || r.hud_inputs_enabled>1 || r.record_ghosts>1) return false;
    const gp_clip unused{};
    for(uint32_t i=0;i<GP_GHOSTS;++i) {
        const auto &clip=r.clips[i];
        if(i>=r.clip_count) {if(std::memcmp(&clip,&unused,sizeof(clip)))return false;continue;}
        if(clip.hue_degrees>359 || clip.reserved || !ghost_annotation::valid_label(clip.label))return false;
        if(!r.enabled)continue;
        if(!nonzero_id(clip.clip_id))return false;
        for(uint32_t j=0;j<i;++j)if(id_equal(clip.clip_id,r.clips[j].clip_id))return false;
    }
    if(!r.enabled) return true;
    if(!r.owner_token || !r.model_id || !r.rom_generation || !r.configuration || !r.clip_count
            || r.mode>GP_FIXED || r.nudge_frames < -600 || r.nudge_frames>600
            || !std::isfinite(r.opacity) || r.opacity<0 || r.opacity>1 || r.star_ends>1
            || r.star_action_count>GP_STAR_ACTIONS || (r.star_ends && !r.star_action_count)) return false;
    for(uint32_t i=0;i<GP_STAR_ACTIONS;++i)
        if(i<r.star_action_count ? !r.star_actions[i] : r.star_actions[i]!=0) return false;
    if(r.mode==GP_FIXED) {const gp_origin zero{};if(std::memcmp(&r.origin,&zero,sizeof(zero)))return false;}
    else if((r.origin.kind!=GO_STAR && r.origin.kind!=GO_SEGMENT) || !r.origin.epoch
            || !r.origin.sequence || !r.origin.continuity || (r.origin.flags&~(GO_RECOVERED|GO_ZERO_CLOCK))
            || (!(r.origin.flags&GO_RECOVERED) && (r.origin.kind==GO_STAR
                ? r.origin.offset>UINT16_MAX : r.origin.offset!=0)))return false;
    return true;
}
int32_t __cdecl configure(const gp_request *request) {
    const int entered=worker_enter(); if(entered!=GP_OK)return entered;
    const auto before=cancellation.load();
    if(request && request->revision==UINT64_MAX) {
        exhausted.store(true);revoke(GP_EXHAUSTED);return worker_leave(GP_EXHAUSTED);
    }
    if(!request || !valid_request(*request)) {revoke(GP_INVALID);return worker_leave(GP_INVALID);}
    if(exhausted.load()) return worker_leave(GP_EXHAUSTED);
    if(request->revision<=last_revision) return worker_leave(GP_INVALID);
    if(!request->enabled) revoke(GP_DISABLED);
    if(!requests.put(PublishedRequest{*request,request->enabled?before:cancellation.load(),0})) {
        exhausted.store(true); revoke(GP_EXHAUSTED); return worker_leave(GP_EXHAUSTED);
    }
    last_revision=request->revision;
    enabled_revision.store(request->enabled?request->revision:0,std::memory_order_release);
    return worker_leave(GP_OK);
}
int32_t __cdecl release_worker() {
    const int entered=worker_enter(); if(entered!=GP_OK)return entered;
    collect_impl();
    if(enabled_revision.load() || model.state.load()!=GP_MODEL_EMPTY) return worker_leave(GP_DEFERRED);
    for(auto &clip:clips)if(clip.state.load()!=Empty)return worker_leave(GP_DEFERRED);
    worker_thread=0; return worker_leave(GP_OK);
}
int32_t __cdecl status(gp_status *out,uint32_t bytes) {
    if(!out || bytes!=sizeof(*out))return GP_INVALID;
    *out={}; if(!reports.get(*out))return GP_BUSY;
    out->bytes=sizeof(*out);out->version=GP_VERSION;
    out->model_state=model.state.load(); out->resident_clip_bytes=resident_bytes.load();
    out->clips_ready=out->clips_owned=out->clips_retired=0;
    for(const auto &clip:clips) {
        const auto s=clip.state.load();
        out->clips_ready+=s==Ready;out->clips_owned+=s==Owned;
        out->clips_retired+=s==Retired || s==Reclaiming || s==Unwanted;
    }
    out->evaluations=evaluations.load();out->rendered=rendered.load();out->gaps=gaps.load();
    out->refusals=refusals.load();out->adoption_busy=adoption_busy.load();out->model_error=model_error.load();
    return GP_OK;
}
int32_t __cdecl release_clip(const uint8_t *id) {
    const int entered=worker_enter(); if(entered!=GP_OK)return entered;
    if(!id || !nonzero_id(id))return worker_leave(GP_INVALID);
    for(auto &clip:clips) {
        // The renderer only moves Ready->Owned->Ready transiently while it
        // inspects a slot, so this settles within a few attempts.
        for(unsigned attempt=0;attempt<64;++attempt) {
            auto state=clip.state.load(std::memory_order_acquire);
            if((state!=Ready && state!=Owned) || !id_equal(clip.header.clip_id,id))break;
            if(clip.state.compare_exchange_strong(state,state==Ready?Retired:Unwanted))break;
        }
    }
    collect_impl();return worker_leave(GP_OK);
}
const gp_api api_v2={sizeof(gp_api),GP_VERSION,{0,0},prepare_model,prepare_clip,configure,revoke,collect,
    release_worker,status,withdraw,release_clip};

void decision(uint32_t value) {
    report.bytes=sizeof(report);report.version=GP_VERSION;report.decision=value;
    terminal_revision.store(defeat.active ? active_request.revision : 0,std::memory_order_release);
    if(!reports.put(report)) {exhausted.store(true);revoke(GP_EXHAUSTED);}
}
void decide_all(uint32_t value) {
    // Every ghost hidden: none hears records passed meanwhile.
    for(auto &last:heard)last=unheard;
    report.visible=0;
    std::memset(report.clip_decisions,uint8_t(value),sizeof(report.clip_decisions));
    decision(value);
}
void forget_shown() {for(auto &last:shown_last)last={};}
void clear_timeline() {
    have_timeline=false;star_ended=false;timeline_request={};defeat={};terminal_pending=false;dance_entry={};forget_shown();
}
void unbind() {
    for(auto &clip:bound)clip=nullptr;
    touch_revision=0;std::memset(have_touch,0,sizeof(have_touch));
}
void retire_clips() {
    for(uint32_t i=0;i<GP_CLIP_SLOTS;++i)
        if(held[i]) {clips[i].state.store(Retired,std::memory_order_release);held[i]=false;}
    unbind();
}
// An automatic PB/set refresh can release a recording whose terminal pose
// is still on screen. Keep ownership until its death/dance ends; a new run,
// switch-off, owner or context change still clears the terminal set.
void retire_unwanted(bool visible=true) {
    for(uint32_t i=0;i<GP_CLIP_SLOTS;++i) {
        bool terminal=false;
        for(uint32_t k=0;defeat.active && k<GP_GHOSTS;++k)
            terminal=terminal || defeat.clip[k]==&clips[i];
        for(uint32_t k=0;terminal_pending && k<GP_GHOSTS;++k)
            terminal=terminal || bound[k]==&clips[i];
        if(terminal && visible)continue;
        uint32_t expected=Unwanted;
        if(!held[i] || !clips[i].state.compare_exchange_strong(expected,Retired))continue;
        held[i]=false;
        for(uint32_t k=0;k<GP_GHOSTS;++k) {
            if(bound[k]==&clips[i]) {bound[k]=nullptr;shown_last[k]={};}
            if(defeat.clip[k]==&clips[i]) {
                defeat.clip[k]=nullptr;defeat.loser[k]=defeat.winner[k]=false;
            }
        }
    }
    if(!visible && (defeat.active || terminal_pending)) {
        bool retained=false;
        for(uint32_t k=0;k<GP_GHOSTS;++k)
            retained=retained || (defeat.active && defeat.clip[k]) || (terminal_pending && bound[k]);
        if(!retained)clear_timeline();
    }
}
Clip *adopt(const uint8_t *id,uint32_t current_cancel) {
    for(uint32_t i=0;i<GP_CLIP_SLOTS;++i)
        if(held[i] && id_equal(clips[i].header.clip_id,id))return &clips[i];
    for(uint32_t i=0;i<GP_CLIP_SLOTS;++i) {
        auto &clip=clips[i];uint32_t expected=Ready;
        // Claim before reading: the worker may reclaim an unclaimed Ready slot.
        if(!clip.state.compare_exchange_strong(expected,Owned))continue;
        if(clip.cancellation==current_cancel && id_equal(clip.header.clip_id,id)) {held[i]=true;return &clip;}
        expected=Owned;
        if(!clip.state.compare_exchange_strong(expected,clip.cancellation==current_cancel?Ready:Retired))
            clip.state.store(Retired,std::memory_order_release); // released while claimed
    }
    return nullptr;
}
bool retire_model() {
    if(!have_model)return true;
    const int result=sg_model_detach(model.value);
    if(result!=SG_OK) {model_error.store(result);if(result==SG_BUSY)adoption_busy.fetch_add(1);return false;}
    have_model=false;model.state.store(GP_MODEL_RETIRED,std::memory_order_release);return true;
}
bool same_origin(const gp_request& a,const gp_request& b) {
    return a.owner_token==b.owner_token && a.rom_generation==b.rom_generation
        && a.configuration==b.configuration && !std::memcmp(&a.origin,&b.origin,sizeof(a.origin));
}
bool renderer_owner() {return render_thread && render_thread==GetCurrentThreadId();}
void next_boundary() {
    if(context_serial.fetch_add(1)>=UINT32_MAX-1) {exhausted.store(true);revoke(GP_EXHAUSTED);}
}
bool same_owner(const gp_request& a,const gp_request& b) {
    return a.owner_token==b.owner_token && a.rom_generation==b.rom_generation
        && a.configuration==b.configuration;
}
// A command's origin rebinds the live timing unless the renderer already
// reached that same origin itself (the worker confirming a local restore).
// Returns true when the command moved the ghost to a timeline the renderer
// was not already drawing (a new run, or the worker's own restore/re-bind).
bool adopt_recovery(const gp_request& next) {
    auto& live=live_recovery;
    if(next.mode!=GP_FOLLOW) {live.clear();return false;}
    const bool same=live.bound && same_owner(live.owner,next);
    if(same && !std::memcmp(&live.owner.origin,&next.origin,sizeof(next.origin))) {live.owner=next;return false;}
    go_origin local{};
    const bool confirmed=same && live.timing.current(local) && !std::memcmp(&local,&next.origin,sizeof(local));
    if(!confirmed) {
        // The worker's restore of the run the renderer was following keeps the
        // renderer's checkpoints, also across a revoke (a capture restart), so
        // its next load still draws on its own frame. Worker revisions never
        // repeat or reset, so an unbroken chain proves the same run: a new run
        // always arrives as an unrecovered origin in between.
        const bool same_run=(next.origin.flags&GO_RECOVERED) && live.owner.revision
            && next.revision==live.owner.revision+1;
        live.timing.bind(next.origin,same_run);live.restored=next.origin.sequence;
    }
    live.owner=next;live.bound=true;
    return !confirmed;
}
uint32_t active_count() {uint32_t count=0;for(auto *clip:bound)count+=clip!=nullptr;return count;}
struct Coordinate {uint64_t target;bool below_zero;};
// A star ghost recorded from the level entrance, raced from a later restart
// of the game's timer (a reset to the LLL volcano entrance, where Usamune
// restarts IGT): the live origin is in a scene the ghost reaches only after
// its own restart there. Align that restart's raw IGT with the live raw IGT,
// using the latest such restart in the live origin's level and area. The
// clip's own origin scene, a segment clock or no matching restart keeps the
// ghost's own origin (shift 0).
uint64_t restart_shift(const Clip& clip,int16_t level,int16_t area) {
    const auto &first=clip.frames[0].sample;
    if(clip.header.clock_kind!=GCLIP_STAR_CLOCK || (first.level==level && first.area==area))return 0;
    for(uint32_t k=clip.restart_count;k--;) {
        const auto &restart=clip.restarts[k];
        if(restart.level!=level || restart.area!=area)continue;
        const uint64_t coordinate=clip_coordinate(clip,restart.index);
        const uint64_t raw=clip.frames[restart.index].sample.clock.raw_igt;
        return coordinate>raw?coordinate-raw:0;
    }
    return 0;
}
// A segment ghost recorded from Usamune's zero (its origin row reads at most
// GCLIP_ZERO_RAW), raced from a live origin at a zero too (GO_ZERO_CLOCK):
// both runs of a scripted start are aligned on the counter. The tracker sees
// a level load's zero one or two frames late and a reset's on its own frame,
// so the elapsed coordinate matched his pipe-entry PB's third drop-in frame
// with his reset's first, and the ghost fell two frames ahead of him
// (2026-09-29). live_raw is the counter at the live origin; a start that is
// not a zero on either side keeps the elapsed coordinate (shift 0).
int64_t zero_shift(const Clip& clip,const gp_origin& origin,int64_t live_raw) {
    if(clip.header.clock_kind!=GCLIP_SEGMENT_CLOCK || !(origin.flags&GO_ZERO_CLOCK)
            || clip.header.origin_raw>GCLIP_ZERO_RAW || live_raw<0 || live_raw>GCLIP_ZERO_RAW)return 0;
    return live_raw-int64_t(clip.header.origin_raw);
}
// One ghost against the shared live coordinate. Hides only this ghost.
// Visible ghost: hand over every record passed since its last drawn one.
// Backward moves and long jumps only re-establish where it is. twins: live
// Mario's frame while this ghost overlaps him; the displayed record's
// requests he issued on it are not handed over (ghost_sound_fade.h).
void hear(uint32_t index,const Clip& clip,uint32_t record,bool before_start,const gc_sample *twins) {
    auto &last=heard[index];auto &high=loudest[index];
    if(heard_clip[index]!=&clip) {last=unheard;heard_clip[index]=&clip;loudest_reset[index]=true;}
    if(last==unheard) {
        // Its time has not come before the start; a new run's first record is new.
        last=before_start || heard_fresh[index]?int64_t(record)-1:int64_t(record);
        if(loudest_reset[index]) {high=last;loudest_reset[index]=false;}
        heard_fresh[index]=false;
    }
    if(before_start) {last=int64_t(record)-1;return;}
    const auto *sink=sound_sink.load(std::memory_order_acquire);
    if(int64_t(record)>last && int64_t(record)-last<=int64_t(GP_SOUND_CATCHUP) && sink && sink->cue)
        for(int64_t at=std::max(last,high)+1;at<=int64_t(record);++at) {
            const auto &sample=clip.frames[at].sample;
            if(!(sample.valid_fields&GC_VALID_SOUND))continue;
            for(uint32_t k=0;k<sample.sound.count && k<GC_SOUND_EVENTS;++k)
                if(!(twins && at==int64_t(record) && ghost_sound_fade::twinned(*twins,sample.sound.bits[k])))
                    sink->cue(index,sample.sound.bits[k]);
        }
    if(int64_t(record)>high)high=int64_t(record);
    last=int64_t(record);
}
// The inverse-square law from live Mario, on top of the engine's own camera
// falloff: sound INTENSITY falls as 1/d^2, so the amplitude this gain scales
// falls as 1/d (-6 dB per doubling of distance). Full gain within
// GP_SOUND_NEAR, and while live Mario is unknown.
float sound_gain(const gc_sample& live,const float ghost[3]) {
    if(!(live.valid_fields&GC_VALID_POSE))return 1.0f;
    double squared=0;
    for(unsigned axis=0;axis<3;++axis) {
        const double apart=double(ghost[axis])-double(live.pose.position[axis]);squared+=apart*apart;
    }
    const double reach=GP_SOUND_NEAR;
    return squared>reach*reach ? float(reach/std::sqrt(squared)) : 1.0f; // NaN compares false: full gain
}
// Where a visible ghost sounds from this frame; before its cues, so a new
// sound starts at the ghost's current place. fade follows its star-dance
// fade, and its picture's fade while sound_fade is on.
void place(uint32_t index,const gc_sample& live,const float ghost[3],float fade) {
    const auto *sink=sound_sink.load(std::memory_order_acquire);
    if(!sink || !sink->place)return;
    float relative[3]{};
    if(!ghost_pose::camera_relative(model.value,live.camera,ghost,relative))relative[0]=relative[1]=relative[2]=0;
    sink->place(index,relative,sound_gain(live,ghost)*fade);
}
// A ghost whose dance has faded out: its sounds still playing fade with it.
void silence(uint32_t index) {
    const auto *sink=sound_sink.load(std::memory_order_acquire);
    const float centre[3]{};
    if(sink && sink->place)sink->place(index,centre,0.0f);
}
// The record whose pose a refused record displays: the previous pose when
// the refusal run is at most GP_BRIDGE_FRAMES long, bounded by recorded poses
// in the same scene with consecutive occurrences and coordinates. Null
// otherwise (a longer run, a scene change, the clip's first or last rows).
const gclip_frame *bridged(const Clip& clip,uint32_t at) {
    const auto posed=[&](uint32_t i){return (clip.frames[i].sample.valid_fields&GC_VALID_POSE)!=0;};
    uint32_t before=at,after=at;
    while(before>0 && !posed(before-1) && at-before<GP_BRIDGE_FRAMES)--before;
    if(before==0 || !posed(before-1))return nullptr;
    --before;
    while(after+1<clip.index_count && !posed(after+1) && after-before<GP_BRIDGE_FRAMES)++after;
    if(after+1>=clip.index_count || !posed(after+1))return nullptr;
    ++after;
    if(after-before-1>GP_BRIDGE_FRAMES)return nullptr;
    const auto &first=clip.frames[before].sample,&last=clip.frames[after].sample;
    if(last.sequence-first.sequence!=after-before
            || clip_coordinate(clip,after)-clip_coordinate(clip,before)!=after-before)return nullptr;
    for(uint32_t i=before;i<=after;++i)
        if(clip.frames[i].sample.level!=first.level || clip.frames[i].sample.area!=first.area)return nullptr;
    return &clip.frames[before];
}
uint32_t draw(uint32_t index,const gs_record& recorded,
              const gc_sample& particles_from,const gc_sample& live,const ghost::WorldPass& world,
              float fade,ghost_compositor::Pose& out);
// The dance past its recording, at coordinate `at`: the last recorded dance
// pose (its place, facing, shadow and root, which the game holds through the
// dance), with the game's animation frame and hand for that frame; the eyes
// keep blinking on the area clock. Particles, anything held and recorded
// sounds stay with the recording.
void play_dance(uint32_t index,uint64_t at,uint64_t recorded_end,gs_record& recorded) {
    const auto &d=dance[index];
    const uint64_t timer=at-d.start+1; // the game's actionTimer on this dance frame
    auto &s=recorded.sample;auto &p=s.pose;
    p.animation_id=d.animation;
    p.animation_frame=int16_t(std::min<uint64_t>(timer-1,uint64_t(d.frames-1)));
    if(timer>=d.peace)p.hand_state=GP_HAND_PEACE_SIGN;
    s.area_update_counter=uint16_t(s.area_update_counter+(at-recorded_end));
    s.valid_fields&=~uint32_t(GC_VALID_PARTICLES|GC_VALID_HELD|GC_VALID_SOUND);
}
// Whether this ghost's clip, ending at coordinate last, finishes a cut dance:
// its animation's length comes from the model once (a ROM without it keeps
// the recorded end).
bool finishes_dance(uint32_t index,uint64_t last) {
    auto &d=dance[index];
    if(!d.known || d.recorded!=last)return false;
    if(!d.frames && (sg_model_get_animation_frames(model.value,d.animation,&d.frames)!=SG_OK || d.frames<=0))
        d.known=false;
    return d.known;
}
// The dance's "here we go" (ghost_tuning.h GP_DANCE_SOUND) when it falls past
// the recording: once a run, as the ghost reaches it.
void voice_dance(uint32_t index,uint64_t played) {
    const auto &d=dance[index];
    const uint64_t at=d.start+GP_DANCE_SOUND_TIMER-1;
    if(!played || !d.known || d.recorded>=at || played<at || played-at>GP_SOUND_CATCHUP || dance_voiced[index])return;
    dance_voiced[index]=true;
    const auto *sink=sound_sink.load(std::memory_order_acquire);
    if(sink && sink->cue)sink->cue(index,GP_DANCE_SOUND);
}
// What one ghost shows this list, chosen from the live clock and scene alone
// (plan), before the world pass; evaluate poses it at the seal.
// fade: 1 normally; after a dance tail's last record (or the game's last
// dance frame, when the recording stopped mid-dance) it falls by
// 1/GP_STAR_FADE_FRAMES a frame on the held last pose, and is 0 once hidden.
// record is the selected row (its sounds play); posed the row whose pose is
// shown, earlier than record across a bridged refusal. played: the dance frame
// drawn past the recording, else 0. target is this ghost's coordinate, the
// defeat's evidence of where it stood.
struct Choice {
    uint32_t result=GP_DISABLED; // GP_READY or GP_BEFORE_START: drawn unless its pose refuses
    uint32_t record=0,posed=0;
    uint64_t target=0,played=0;
    float fade=1;
    bool falling=false;
    bool recorded=true; // false past the last recorded coordinate, including held-end fade
};
// One list's choice for every ghost, used once by evaluate at that list's seal.
struct Plan {
    bool made=false,proceed=false; // proceed: the list reached its per-ghost choice
    uint32_t epoch=0,post=0,chosen=0;
    uint64_t sequence=0,dlist_serial=0;
    gp_draw_token candidate{};
    Choice ghost[GP_GHOSTS];
} planned;
uint32_t choose(uint32_t index,const Coordinate& frame,const gc_sample& live,Choice& choice) {
    const Clip *clip=bound[index];
    if(!clip)return GP_PREPARING;
    const auto &h=clip->header;
    if(active_request.mode==GP_FOLLOW && active_request.origin.kind!=h.clock_kind)return GP_WRONG_IDENTITY;
    const auto target=frame.target;
    const uint64_t first=clip_coordinate(*clip,0);
    const uint64_t last=clip_coordinate(*clip,clip->index_count-1);
    // Before-start clamps to the first pose, including when that pose already
    // carries a touch. Unlike the live-end latch, this moves with the nudge.
    // A clip with a dance tail ends on its own closing grab instead: it plays
    // on through any earlier star (the 100-coin star before its exit star) and
    // its closing dance, then fades out over GP_STAR_FADE_FRAMES from its last
    // record, or from the game's last dance frame when the recording stopped
    // mid-dance. FIXED seek ignores it, like both end policies.
    const bool dances=(h.flags&GCLIP_DANCE_TAIL) && active_request.mode==GP_FOLLOW;
    if(have_touch[index] && (first>=touch[index] || (!dances && target>=touch[index])))return GP_STAR_ENDED;
    uint64_t at=target;
    if(target>last) {
        choice.recorded=false;
        if(!dances)return GP_AFTER_END;
        const uint64_t ends=finishes_dance(index,last)?dance[index].end:last;
        if(target>ends) {
            const uint64_t past=target-ends;
            if(past>=GP_STAR_FADE_FRAMES) {choice.fade=0;return GP_STAR_ENDED;}
            choice.fade=float(GP_STAR_FADE_FRAMES-past)/float(GP_STAR_FADE_FRAMES);
        }
        if(ends>last)choice.played=std::min(target,ends);
        at=last; // the dance's last recorded pose, held while it fades
    }
    uint32_t lo=0,hi=clip->index_count;
    while(lo<hi) {
        const uint32_t middle=lo+(hi-lo)/2;
        if(clip_coordinate(*clip,middle)<at)lo=middle+1;else hi=middle;
    }
    const auto &selected=clip->frames[lo];
    if(!frame.below_zero && at>=first && clip_coordinate(*clip,lo)!=at) {gaps.fetch_add(1);return GP_GAP;}
    float scene_translation=0;
    if(!ghost_scene::translation(selected.sample.level,selected.sample.area,live.level,live.area,scene_translation))
        return GP_WRONG_SCENE;
    if(!sequence_reported) {
        report.selected_sequence_lo=uint32_t(selected.sample.sequence);
        report.selected_sequence_hi=uint32_t(selected.sample.sequence>>32);
        sequence_reported=true;
    }
    const gclip_frame *shown=&selected;
    if(!(selected.sample.valid_fields&GC_VALID_POSE) && !(shown=bridged(*clip,lo))) {
        refusals.fetch_add(1);return GP_INVALID_FRAME;
    }
    if(!ghost_scene::compatible(shown->sample,live))return GP_WRONG_SCENE;
    choice.record=lo;choice.posed=uint32_t(shown-clip->frames);
    return frame.below_zero || target<first?GP_BEFORE_START:GP_READY;
}
// One ghost's pose, shadow and particles for the recorded (or defeated)
// sample, with every world-space part mapped into the live area's coordinates.
uint32_t draw(uint32_t index,const gs_record& recorded,
              const gc_sample& particles_from,const gc_sample& live,const ghost::WorldPass& world,
              float fade,ghost_compositor::Pose& out) {
    gs_record mapped=recorded;
    if(!ghost_scene::rebase(mapped.sample,live))return GP_WRONG_SCENE;
    std::memcpy(displayed_position[index],mapped.sample.pose.position,sizeof(displayed_position[index]));
    evaluations.fetch_add(1);
    const float opacity=active_request.opacity*fade;
    // The overlap fade rides with the player-first region, not the opacity:
    // the shaders give the two ONE floor (ghost_occlusion.h).
    const float overlap=active_request.proximity_enabled && (live.valid_fields&GC_VALID_POSE)
        ? ghost_proximity::factor(live.pose.position,live.pose.scale,
              mapped.sample.pose.position,mapped.sample.pose.scale,active_request.proximity_distance)
        : 1.0f;
    if(ghost_pose::evaluate(model.value,mapped,live,world,opacity,scratch[index],out)!=ghost_pose::Result::ok) {
        out={};refusals.fetch_add(1);return GP_POSE_REFUSED;
    }
    out.hue_degrees=active_request.tint_enabled?float(active_request.clips[index].hue_degrees):0.0f;
    // Player first: the same live Mario region for every ghost this frame,
    // under the live view-projection its body and shadow are drawn with.
    ghost_occlusion::region(out.view_projection,active_request.occlusion_fade!=0,
        (live.valid_fields&GC_VALID_POSE)!=0,live.pose.position,live.pose.scale,out.occlusion);
    out.occlusion.overlap=overlap;
    if(active_request.shadow_enabled && (mapped.sample.valid_fields&GC_VALID_SHADOW)
            && !ghost_pose::shadow(model.value,mapped.sample,out.view_projection,shadows[index].vertices,
                                   shadows[index].texture,out.shadow)) {
        out={};refusals.fetch_add(1);return GP_POSE_REFUSED;
    }
    // Recorded particles never refuse their ghost; unknown frames are skipped.
    sg_particle_view particles{};
    if((particles_from.valid_fields&GC_VALID_PARTICLES) && sg_model_get_particles(model.value,&particles)==SG_OK) {
        gc_sample mapped_particles=particles_from;
        if(ghost_scene::rebase_components(mapped_particles,recorded.sample,live))
            ghost_particle_pose::place(model.value,particles,mapped_particles,live.camera,out.view_projection,
                                       particle_vertices[index],out.particles);
    }
    rendered.fetch_add(1);
    return GP_READY;
}
// A defeated ghost: the row it stood on, playing Mario's own death from the
// trigger (one animation frame a game frame) to its last frame, where he lies
// passed out, then fading over GP_DEFEAT_FADE_FRAMES (ghost_tuning.h). Its
// place, facing, scale and shadow stay; what it held and its particles go.
uint32_t death_frames(uint32_t index) {
    return defeat.water[index] ? uint32_t(defeat.drown+defeat.limp) : uint32_t(std::max(defeat.fall,0));
}
uint32_t choose_fall(uint32_t index,uint32_t post,const gc_sample& live,Choice& choice) {
    const uint32_t hold=death_frames(index);
    if(!hold || post>=hold+GP_DEFEAT_FADE_FRAMES) {choice.fade=0;return GP_STAR_ENDED;}
    choice.fade=post<hold?1.0f:float(hold+GP_DEFEAT_FADE_FRAMES-post)/float(GP_DEFEAT_FADE_FRAMES);
    const auto &row=defeat.clip[index]->frames[defeat.row[index]];
    if(!ghost_scene::compatible(row.sample,live))return GP_WRONG_SCENE;
    choice.falling=true;choice.record=choice.posed=defeat.row[index];
    return GP_READY;
}
uint32_t pose_fall(uint32_t index,uint32_t post,const Choice& choice,const gc_sample& live,
                   const ghost::WorldPass& world,ghost_compositor::Pose& out) {
    const auto &row=defeat.clip[index]->frames[choice.posed];
    gs_record recorded{};recorded.bytes=sizeof(recorded);recorded.version=GS_VERSION;
    recorded.configuration=row.configuration;recorded.sample=row.sample;recorded.world.status=row.world_status;
    auto &p=recorded.sample.pose;
    p.animation_id=GP_DEFEAT_ANIMATION;p.eye_state=GP_DEFEAT_EYES;
    p.animation_frame=int16_t(std::min<uint32_t>(post,uint32_t(defeat.fall-1)));
    if(defeat.water[index] && post<uint32_t(defeat.drown)) { // struggling, eyes half closed
        p.animation_id=GP_DROWN_ANIMATION;p.eye_state=GP_DROWN_EYES;p.animation_frame=int16_t(post);
    } else if(defeat.water[index]) { // then limp, dead-eyed
        p.animation_id=GP_DROWN_LIMP_ANIMATION;
        p.animation_frame=int16_t(std::min<uint32_t>(post-uint32_t(defeat.drown),uint32_t(defeat.limp-1)));
    }
    p.punch_state=0;p.grab_position=0;
    for(unsigned k=0;k<3;++k)p.torso_angles[k]=p.head_angles[k]=0;
    recorded.sample.valid_fields&=~uint32_t(GC_VALID_PARTICLES|GC_VALID_HELD);
    const auto result=draw(index,recorded,recorded.sample,live,world,choice.fade,out);
    // Camera/overlap fading cannot end an otherwise visible death, including
    // a paused one. Its own end fade still follows the frozen game clock.
    if(result==GP_READY)out.occlusion.minimum=ghost_occlusion::GHOST_FLOOR;
    return result;
}
uint32_t pose_row(uint32_t index,const Choice& choice,const gc_sample& live,const ghost::WorldPass& world,
                  ghost_compositor::Pose& out) {
    const auto &clip=*bound[index];const auto &shown=clip.frames[choice.posed];
    gs_record recorded{};recorded.bytes=sizeof(recorded);recorded.version=GS_VERSION;
    recorded.configuration=shown.configuration;recorded.sample=shown.sample;
    recorded.world.status=shown.world_status;
    if(choice.played)play_dance(index,choice.played,clip_coordinate(clip,clip.index_count-1),recorded);
    return draw(index,recorded,choice.played?recorded.sample:clip.frames[choice.record].sample,live,world,
                choice.fade,out);
}
void annotate(uint32_t index,const Choice& choice,ghost_compositor::Pose& pose) {
    auto& out=pose.annotation;
    std::memcpy(out.label,active_request.clips[index].label,sizeof(out.label));
    std::memcpy(out.position,displayed_position[index],sizeof(out.position));
    out.scale=bound[index]->frames[choice.posed].sample.pose.scale[1];
    if(!(out.scale>0))out.scale=1;
    // The top of the body as drawn this frame (his own vertices, not a held
    // object's): the name sits on it, crouching, sliding or diving with him.
    out.top=NAN;
    for(uint32_t i=0;i<pose.body_count && i<pose.count;++i) {
        const float y=pose.vertices[i].position[1];
        if(std::isfinite(y) && !(y<=out.top))out.top=y;
    }
    out.names=active_request.names_enabled!=0;
    out.background=uint8_t(active_request.name_background);
    out.background_opacity=active_request.name_background_opacity;
    out.slot=uint8_t(index);
    const bool recorded=choice.recorded && !choice.falling && !choice.played;
    out.hud=active_request.hud_inputs_enabled && recorded;
    out.inputs=active_request.inputs_enabled && recorded;
    out.values=active_request.input_mode==GP_INPUT_VALUES;
    out.artwork=model.annotations.usamune;
    // A refused pose can borrow older geometry. Its pad remains the exact
    // current record: independent validity must never borrow a button hold.
    const auto& selected=bound[index]->frames[choice.record].sample;
    if((out.inputs || out.hud) && (selected.valid_fields&GC_VALID_PAD)) {out.known=true;out.pad=selected.pad;}
}
bool same_list(const Plan& list,const gc_sample& live) {
    return list.made && list.epoch==live.epoch && list.sequence==live.sequence && list.dlist_serial==live.dlist_serial;
}
}

namespace ghost_playback {
uint32_t hidden_refusals(const gclip_header& header,const gclip_frame *frames) {
    uint32_t indexed=0;
    if(!valid_clip(header,frames,indexed))return UINT32_MAX;
    Clip clip;clip.header=header;clip.frames=const_cast<gclip_frame*>(frames);clip.index_count=indexed;
    uint32_t hidden=0;
    for(uint32_t i=0;i<indexed;++i)
        if(!(frames[i].sample.valid_fields&GC_VALID_POSE) && !bridged(clip,i))++hidden;
    return hidden;
}
const gp_api *api() {return &api_v2;}
void set_sound_sink(const SoundSink *sink) {sound_sink.store(sink,std::memory_order_release);}
void safe_boundary(uint32_t rom,uint32_t configuration) {
    const auto thread=GetCurrentThreadId();
    if(render_thread && render_thread!=thread) {refusals.fetch_add(1);return;}
    render_thread=thread;live_rom=rom;live_configuration=configuration;
    next_boundary();
    PublishedRequest published{};const bool received=requests.get(published);
    const auto &next=published.value; const auto current_cancel=cancellation.load();
    const bool wanted=!exhausted.load() && rom && configuration && received && next.enabled
        && published.cancellation==current_cancel
        && next.rom_generation==rom && next.configuration==configuration;
    if(!wanted) {
        retire_clips();retire_model();have_request=false;clear_timeline();live_recovery.detach();
        sound_control(false,0);forget_heard(Forget::move);decide_all(GP_DISABLED);return;
    }
    if(enabled_revision.load()!=next.revision) {
        // Withdrawal keeps reusable Owned clips and the model, but an explicit
        // release must reach the worker even while no new command can apply.
        // Released terminal entries have no visible owner here. Unreleased
        // terminal entries keep their place and animation for a later resume.
        retire_unwanted(false);
        // Withdrawal retains reusable assets, but an explicit worker model
        // replacement must still be released at this safe render boundary.
        if(have_model && (model.cancellation!=current_cancel || model.id!=desired_model.load()))retire_model();
        sound_control(false,0);forget_heard(Forget::move);decide_all(GP_DISABLED);return;
    }
    if(acquired_cancel!=current_cancel) {clear_timeline();retire_clips();live_recovery.detach();}
    if(have_request && (next.mode!=active_request.mode || next.model_id!=active_request.model_id))clear_timeline();
    if(next.mode==GP_FOLLOW && (!have_timeline || !same_origin(timeline_request,next))) {
        // A menu can remove action/warp evidence and restart the source's
        // elapsed reference. The worker repairs that reference as RECOVERED;
        // that alone is not a state load back into the race. Keep the terminal
        // picture until the next live counters establish whether play moved.
        const bool repair=have_timeline && defeat.spent && defeat.epoch
            && same_owner(timeline_request,next) && (next.origin.flags&GO_RECOVERED)
            && timeline_request.origin.kind==next.origin.kind;
        if(!repair)clear_timeline();
        have_timeline=true;timeline_request=next;
    }
    if(have_model && (model.id!=next.model_id || model.cancellation!=current_cancel || model.id!=desired_model.load()))
        if(!retire_model()) {decide_all(GP_PREPARING);return;}
    const bool changed=touch_revision!=next.revision;
    const bool first=!have_request;
    const gp_request previous=active_request;
    active_request=next;have_request=true;acquired_cancel=current_cancel;
    if(!next.suspended)terminal_pending=false;
    bool shown=false;for(const auto &last:shown_last)shown=shown || last.drawn;
    if(!first && !defeat.spent && shown && next.mode==GP_FOLLOW && previous.mode==GP_FOLLOW
            && next.suspended && !previous.suspended && same_origin(previous,next)
            && previous.model_id==next.model_id)terminal_pending=true;
    // The terminal set raced THIS attempt. A PB refresh selects the next
    // recording while the old ghost is still dying. Shared controls update,
    // but replacing/reordering that set cannot erase its terminal sequence.
    if((defeat.active || terminal_pending) && !first && next.mode==GP_FOLLOW && previous.mode==GP_FOLLOW
            && same_owner(previous,next) && previous.model_id==next.model_id) {
        active_request.clip_count=previous.clip_count;
        std::memcpy(active_request.clips,previous.clips,sizeof(active_request.clips));
    }
    retire_unwanted();
    // Every new timeline re-bases what each ghost has heard: a new run sounds
    // its first record; a restore or the worker's re-bind (a reset re-arms
    // the same ghost that way) sounds what it passes from where it stands.
    // Only a nudge or seek on the same timeline keeps the high-water record,
    // and a command that confirms the renderer's own restore changes nothing.
    if(adopt_recovery(next) || first)
        forget_heard(first || !(next.origin.flags&GO_RECOVERED) ? Forget::run : Forget::restore);
    else if(next.nudge_frames!=previous.nudge_frames || next.mode!=previous.mode
            || next.seek_igt!=previous.seek_igt)forget_heard(Forget::move);
    // Suspension starts at the live restore state of this moment; only a later
    // restore (a state load) revives the ghost before the owner re-arms.
    if(next.suspended && (first || !previous.suspended))suspend_mark=live_recovery.restored;
    sound_control(!(next.suspended && live_recovery.restored==suspend_mark),next.volume);
    report={};report.revision=next.revision;report.owner_token=next.owner_token;report.model_id=next.model_id;
    report.clip_count=next.clip_count;report.rom_generation=rom;report.configuration=configuration;
    if(!have_model) {
        uint32_t expected=GP_MODEL_READY;
        if(model.state.compare_exchange_strong(expected,GP_MODEL_RENDER_OWNED)) {
            if(model.id!=next.model_id || model.cancellation!=current_cancel || model.id!=desired_model.load())
                model.state.store(GP_MODEL_RETIRED,std::memory_order_release);
            else {
                const int result=sg_model_adopt(model.value);
                if(result==SG_OK)have_model=true;
                else {
                    model_error.store(result);if(result==SG_BUSY)adoption_busy.fetch_add(1);
                    model.state.store(result==SG_BUSY?GP_MODEL_READY:GP_MODEL_RETIRED,std::memory_order_release);
                }
            }
        }
    }
    // Bind the displayed set (including a held terminal set), then retire
    // storage no longer needed by it. No frame references reclaimed clips.
    bool listed[GP_CLIP_SLOTS]{};
    for(uint32_t i=0;i<GP_GHOSTS;++i) {
        Clip *clip=i<active_request.clip_count?adopt(active_request.clips[i].clip_id,current_cancel):nullptr;
        if(clip)listed[clip-clips]=true;
        if(clip!=bound[i]) { // a different recording: nothing heard or shown yet
            heard_clip[i]=nullptr;shown_last[i]={};dance_voiced[i]=false;
        }
        if(changed || clip!=bound[i]) {
            bound[i]=clip;have_touch[i]=false;touch[i]=0;
            if(clip)have_touch[i]=recorded_touch(*clip,next,touch[i]);
            have_completion[i]=clip && completing_grab(*clip,next,have_touch[i],touch[i],completion[i]);
            if(!clip || !cut_dance(*clip,next,dance[i]))dance[i]={};
        }
        report.clip_decisions[i]=uint8_t(i>=active_request.clip_count?GP_DISABLED:clip?GP_READY:GP_PREPARING);
    }
    for(uint32_t i=0;i<GP_CLIP_SLOTS;++i)
        if(held[i] && !listed[i]) {clips[i].state.store(Retired,std::memory_order_release);held[i]=false;}
    touch_revision=next.revision;
    report.clip_count=active_request.clip_count;
    report.clips_active=active_count();
    decision(have_model && report.clips_active==active_request.clip_count?GP_READY:GP_PREPARING);
}
bool atlas(sg_atlas_view *out,uint64_t *id) {
    if(out)*out={};if(id)*id=0;
    if(!out || !id || !renderer_owner() || !have_model || exhausted.load())return false;
    if(sg_model_get_atlas(model.value,out)!=SG_OK)return false;
    *id=model.id;return true;
}
bool particles(sg_particle_view *out,uint64_t *id) {
    if(out)*out={};if(id)*id=0;
    if(!out || !id || !renderer_owner() || !have_model || exhausted.load())return false;
    if(sg_model_get_particles(model.value,out)!=SG_OK || !out->frame_count)return false;
    *id=model.id;return true;
}
bool annotations(ghost_annotation::View* out,uint64_t* id) {
    if(out)*out={};if(id)*id=0;
    if(!out || !id || !renderer_owner() || !have_model || exhausted.load())return false;
    *out=model.annotations.view();if(!out->rgba)return false;
    *id=model.id;return true;
}
bool drawing() {
    return renderer_owner() && have_request && have_model && active_count() && !exhausted.load()
        && active_request.revision && enabled_revision.load()==active_request.revision
        && cancellation.load()==acquired_cancel && model.id==desired_model.load()
        && active_request.rom_generation==live_rom && active_request.configuration==live_configuration;
}
bool terminal() {
    const auto revision=terminal_revision.load(std::memory_order_acquire);
    return revision && revision==enabled_revision.load(std::memory_order_acquire);
}
bool records_display() {return have_request && active_request.record_ghosts!=0;}
bool current(const gp_draw_token& token) {
    return drawing() && token.revision==active_request.revision && token.cancellation==acquired_cancel
        && context_serial.load()==token.context_serial;
}
// The frame his completion reaches playback (his dance, or the tracker's
// suspension while he dances), from what each ghost drew on the frame before:
// a ghost whose own dance began at or before his x-cam is a winner (its dance
// and fade go on from there), otherwise it falls where it stood. A ghost not
// drawn then stays hidden. Only once per run; a restore revives the run.
bool start_defeat(const gc_sample& live) {
    if(defeat.active || defeat.spent || active_request.mode!=GP_FOLLOW
            || !(live.valid_fields&GC_VALID_ACTION) || !grab_action(active_request,live.live_action))return false;
    defeat=Defeat{};defeat.spent=true;
    const auto length=[](int16_t animation) {
        int32_t frames=0;
        return sg_model_get_animation_frames(model.value,animation,&frames)==SG_OK && frames>0 ? frames : 0;
    };
    defeat.fall=length(GP_DEFEAT_ANIMATION);defeat.drown=length(GP_DROWN_ANIMATION);
    defeat.limp=length(GP_DROWN_LIMP_ANIMATION);
    // His x-cam on the live clock: the frame his latest dance began, else this one.
    const bool timed=(live.clock.valid&GCC_ELAPSED_VALID)!=0;
    const uint64_t now=live.clock.elapsed_frames;
    const uint64_t his=timed && dance_entry.known && dance_entry.continuity==live.clock.continuity_id
        && dance_entry.elapsed<=now ? dance_entry.elapsed : now;
    bool any=false;
    for(uint32_t i=0;i<GP_GHOSTS && i<active_request.clip_count;++i) {
        const auto &last=shown_last[i];
        if(!last.drawn || !bound[i] || last.clip!=bound[i])continue;
        defeat.clip[i]=bound[i];any=true;
        // Where this ghost stands now and stood at his x-cam: its coordinate
        // moves with the live clock (without one, the frame after the one it
        // last drew).
        uint64_t here=last.target+1,at=here;
        if(timed && now>=last.elapsed) {
            here=last.target+(now-last.elapsed);
            at=his>=last.elapsed ? last.target+(his-last.elapsed) : last.target-std::min(last.target,last.elapsed-his);
        }
        // A winner's dance moves on from where it stands now.
        if(have_completion[i] && completion[i]<=at) {defeat.winner[i]=true;defeat.target[i]=here;}
        else {
            // Swimming, it drowns (without the drowning animations, it falls over).
            const auto &stood=bound[i]->frames[last.row].sample;
            defeat.loser[i]=true;defeat.row[i]=last.row;
            defeat.water[i]=defeat.drown && defeat.limp && (stood.valid_fields&GC_VALID_ACTION)
                && (stood.live_action&GP_ACTION_GROUP_MASK)==GP_ACTION_GROUP_SUBMERGED;
            defeat.hold=std::max(defeat.hold,death_frames(i));
        }
    }
    if(!any)return false;
    defeat.active=true;terminal_pending=false;defeat.start=live.global_timer;defeat.clock=live.area_update_counter;
    defeat.level=live.level;defeat.area=live.area;
    defeat.epoch=live.epoch;defeat.last_timer=live.global_timer;
    defeat.last_clock=live.area_update_counter;defeat.last_igt=live.igt;
    return true;
}
// The list's choice from its sample: everything evaluate decides before any
// pose is built, including its once-a-list effects (the live timing, his
// dance entry, the star and defeat latches, restart alignment). Hidden lists
// report why here.
void plan_list(const gc_sample& live) {
    auto &list=planned;list=Plan{};
    list.made=true;list.epoch=live.epoch;list.sequence=live.sequence;list.dlist_serial=live.dlist_serial;
    next_boundary();
    gp_draw_token candidate{active_request.revision,acquired_cancel,context_serial.load()};
    if(!current(candidate)) {decide_all(GP_WRONG_IDENTITY);return;}
    if(live.bytes!=sizeof(live) || live.version!=GC_VERSION || !live.epoch || !live.sequence
            || !live.dlist_serial || !(live.valid_fields&GC_VALID_CLOCK)) {decide_all(GP_NO_CLOCK);return;}
    Coordinate frame{active_request.seek_igt,false};
    sequence_reported=false;
    // His x-cam: the live elapsed his latest dance began at (the defeat's judge).
    const bool dancing=(live.valid_fields&GC_VALID_ACTION) && grab_action(active_request,live.live_action);
    if(dancing && !dance_entry.dancing && (live.clock.valid&GCC_ELAPSED_VALID))
        dance_entry={true,true,live.clock.elapsed_frames,live.clock.continuity_id};
    dance_entry.dancing=dancing;
    // A running defeat goes on by the game's own world clock
    // (gAreaUpdateCounter), which stands still while the game is paused or in
    // Usamune's menu; gGlobalTimer runs on there, and ran every death and a
    // winner's dance out while he paused (his report, 2026-09-29: "if we pause
    // during it, it should pause that animation, and continue if we continue").
    // In his 2026-09-28 session every pause froze this counter with Usamune's.
    // A load (the global timer runs back), a scene change or its end stops it.
    uint32_t post=0;
    // Terminal poses belong to the place each ghost died, not to a recovered
    // elapsed reference. A paused menu supplies no usable derived clock, and
    // its first resumed occurrence may have a new continuity id. Compare the
    // game counters instead. A real state load/reset can rewind or jump them;
    // ordinary play advances each by at most the observed global frames.
    bool terminal=defeat.spent && defeat.epoch;
    float scene_translation=0;
    if(terminal) {
        const uint32_t frames=live.global_timer-defeat.last_timer;
        const bool moved=live.epoch!=defeat.epoch || live.global_timer<defeat.last_timer
            || !ghost_scene::translation(defeat.level,defeat.area,live.level,live.area,scene_translation)
            || live.igt<defeat.last_igt || uint32_t(live.igt-defeat.last_igt)>frames
            || uint32_t(uint16_t(live.area_update_counter-defeat.last_clock))>frames;
        if(moved) {
            defeat={};star_ended=false;forget_shown();terminal=false;
            dance_entry={dance_entry.dancing,false,0,0};
            // A pending PB set held behind the terminal sequence belongs to
            // the restored/new race from its first list. Rebind before making
            // that list's choice, rather than racing the released old clip.
            safe_boundary(live_rom,live_configuration);
            plan_list(live);return;
        } else {
            defeat.last_timer=live.global_timer;defeat.last_clock=live.area_update_counter;
            defeat.last_igt=live.igt;
        }
    }
    if(defeat.active) {
        post=uint16_t(live.area_update_counter-defeat.clock);
        if(live.global_timer<defeat.start
                || !ghost_scene::translation(defeat.level,defeat.area,live.level,live.area,scene_translation)
                || post>=defeat.hold+GP_DEFEAT_FADE_FRAMES+GP_STAR_FADE_FRAMES+GP_DEFEAT_SCAN)
            defeat.active=false;
    }
    auto origin=active_request.origin;
    if(active_request.mode==GP_FOLLOW && live_recovery.bound && !terminal) {
        // Same refusals as the worker: an unresolved or ambiguous live restore
        // hides the ghost; a resolved one draws on the frame it happened. A
        // defeat keeps playing through an unresolved one.
        live_recovery.timing.observe(live,active_request.star_actions,
            active_request.star_ends?active_request.star_action_count:0);
        if(!live_recovery.timing.current(origin)) {
            if(!defeat.active) {decide_all(GP_NO_CLOCK);return;}
        } else if(origin.sequence!=live_recovery.restored) {
            // A restoration revives the run, exactly like the worker's new
            // command, and ends a defeat: he loaded back into the race.
            star_ended=false;live_recovery.restored=origin.sequence;forget_heard(Forget::restore);
            defeat={};terminal_pending=false;forget_shown();dance_entry={dance_entry.dancing,false,0,0};
            safe_boundary(live_rom,live_configuration);
            plan_list(live);return;
        }
    }
    if(active_request.mode==GP_FOLLOW && !defeat.active) {
        bool defeated=false;
        if(active_request.suspended) {
            if(!live_recovery.bound || live_recovery.restored==suspend_mark) {
                // His completing grab reaches here as the tracker's suspension
                // when no star latch applies (the 100-coin star's exit star).
                defeated=start_defeat(live);
                if(!defeated) {sound_control(false,0);decide_all(GP_DISABLED);return;}
            } else sound_control(true,active_request.volume); // a load revived it on this frame
        }
        if(!defeated) {
            if(!valid_clock(live) || (live.clock.valid&(GCC_RAW_VALID|GCC_ELAPSED_VALID))
                    !=(GCC_RAW_VALID|GCC_ELAPSED_VALID) || live.epoch!=origin.epoch
                    || live.sequence<origin.sequence || live.clock.continuity_id!=origin.continuity
                    || !elapsed_coordinate(live.clock.elapsed_frames,origin.elapsed,
                        origin.offset,frame.target)) {decide_all(GP_NO_CLOCK);return;}
            if(origin.kind==GO_STAR && live.clock.origin_id!=origin.star_generation)star_ended=true;
            if(active_request.star_ends)for(uint32_t i=0;i<active_request.star_action_count;++i)
                if(live.live_action==active_request.star_actions[i])star_ended=true;
            if(star_ended && !(defeated=start_defeat(live))) {decide_all(GP_STAR_ENDED);return;}
        }
        if(!defeated) {
            // Fixed per attempt, from its scene. A restored origin continues that
            // attempt's live coordinate, so the same clip keeps its alignment and
            // a newly shown clip aligns to the same scene.
            if(!attempt.known || (!(origin.flags&GO_RECOVERED) && attempt.sequence!=origin.sequence))
                attempt={origin.sequence,live.level,live.area,true};
            for(uint32_t i=0;i<GP_GHOSTS;++i) {
                if(!bound[i]) {aligned[i]=false;align[i]=0;continue;}
                if(aligned[i] && aligned_attempt[i]==attempt.sequence && id_equal(aligned_id[i],bound[i]->header.clip_id))
                    continue;
                aligned[i]=true;aligned_attempt[i]=attempt.sequence;
                std::memcpy(aligned_id[i],bound[i]->header.clip_id,16);
                // The counter at the live origin: raw now less the coordinate
                // run since (a restore's coordinate is the same raw-less-origin
                // relation, ghost_origin.cpp's direct offset). A ghost first
                // shown after the counter restarted reads it below zero and
                // keeps the elapsed coordinate.
                align[i]=origin.kind==GO_STAR?int64_t(restart_shift(*bound[i],attempt.level,attempt.area))
                    :zero_shift(*bound[i],origin,int64_t(live.clock.raw_igt)-int64_t(frame.target));
            }
        }
    }
    if(defeat.active)sound_control(true,active_request.volume);
    for(uint32_t i=0;i<GP_GHOSTS && i<active_request.clip_count;++i) {
        auto &choice=list.ghost[i];
        // Its alignment (restart or zero), then the timing nudge, on this
        // ghost's coordinate: one signed move, so a shift below zero and a
        // nudge above it still meet exactly.
        Coordinate mine=frame;
        if(active_request.mode==GP_FOLLOW) {
            const int64_t delta=(bound[i] && aligned[i]?align[i]:0)+active_request.nudge_frames;
            if(delta>=0) {
                if(mine.target>UINT64_MAX-uint64_t(delta)-600) {decide_all(GP_NO_CLOCK);return;}
                mine.target+=uint64_t(delta);
            } else {
                const auto amount=uint64_t(-delta);
                if(mine.target<amount){mine.below_zero=true;mine.target=0;}else mine.target-=amount;
            }
        }
        uint32_t result=GP_STAR_ENDED;
        if(!defeat.active)result=choose(i,mine,live,choice);
        else if(defeat.winner[i] && bound[i]==defeat.clip[i]) {
            mine={defeat.target[i]+post,false};
            result=choose(i,mine,live,choice);
        } else if(defeat.loser[i] && bound[i]==defeat.clip[i])result=choose_fall(i,post,live,choice);
        if(!current(candidate)) {refusals.fetch_add(1);decide_all(GP_POSE_REFUSED);return;}
        choice.result=result;choice.target=mine.target;
        list.chosen+=result==GP_READY || result==GP_BEFORE_START;
    }
    list.proceed=true;list.post=post;list.candidate=candidate;
}
bool plan(const gc_sample& live) {
    if(!drawing())return false;
    plan_list(live);
    return planned.proceed && planned.chosen;
}
bool evaluate(const gc_sample& live,const ghost::WorldPass& world,
              ghost_compositor::Pose *out,uint32_t& count,gp_draw_token& token) {
    count=0;token={};
    if(!out)return false;
    for(uint32_t i=0;i<GP_GHOSTS;++i)out[i]={};
    if(!renderer_owner())return false;
    // The list's own plan, used once; a caller that did not plan this list plans it now.
    if(!same_list(planned,live))plan_list(live);
    const Plan &list=planned;planned.made=false;
    if(!list.proceed)return false;
    const auto candidate=list.candidate;
    report.visible=0;
    uint32_t summary=GP_DISABLED;bool before_start=true;
    for(uint32_t i=0;i<GP_GHOSTS;++i) {
        if(i>=active_request.clip_count) {report.clip_decisions[i]=uint8_t(GP_DISABLED);continue;}
        const auto &choice=list.ghost[i];
        ghost_compositor::Pose pose{};
        uint32_t result=choice.result;
        if(result==GP_READY || result==GP_BEFORE_START) {
            const auto posed=choice.falling ? pose_fall(i,list.post,choice,live,world,pose)
                : pose_row(i,choice,live,world,pose);
            if(posed!=GP_READY)result=posed;
        }
        if(!current(candidate)) {
            // A revocation during any evaluation discards the whole candidate.
            for(uint32_t j=0;j<GP_GHOSTS;++j)out[j]={};
            count=0;refusals.fetch_add(1);decide_all(GP_POSE_REFUSED);return false;
        }
        report.clip_decisions[i]=uint8_t(result);
        if(!i)summary=result;
        shown_last[i]=defeat.active || (result!=GP_READY && result!=GP_BEFORE_START)
            ? Shown{} : Shown{true,choice.posed,choice.target,live.clock.elapsed_frames,bound[i]};
        if(result!=GP_READY && result!=GP_BEFORE_START) { // hidden: silent
            heard[i]=unheard;fade_gain_known[i]=false;
            if(choice.fade==0)silence(i);
            continue;
        }
        // Its sound follows what its picture keeps at its body centre
        // (ghost_sound_fade.h), taken as it is on the frame it appears.
        // A bridged refusal sounds its own cues from the pose it shows.
        const auto &shown=bound[i]->frames[choice.posed].sample.pose;
        const auto *position=displayed_position[i];
        const float faded=active_request.sound_fade
            ? ghost_sound_fade::picture_fade(pose.view_projection,pose.occlusion,position,shown.scale) : 1.0f;
        fade_gain[i]=fade_gain_known[i]?ghost_sound_fade::follow(fade_gain[i],faded):faded;
        fade_gain_known[i]=true;
        place(i,live,position,choice.fade*fade_gain[i]);
        if(choice.falling) {
            heard[i]=unheard; // it plays no recorded sound, only its death cry, once
            const auto *sink=sound_sink.load(std::memory_order_acquire);
            if(!defeat.cued[i] && sink && sink->cue)sink->cue(i,defeat.water[i]?GP_DROWN_SOUND:GP_DEFEAT_SOUND);
            defeat.cued[i]=true;
        } else {
            // Twins drop only where he is: while the overlap fade is below 1.
            const bool near_him=active_request.sound_fade && pose.occlusion.overlap<1.0f;
            hear(i,*bound[i],choice.record,result==GP_BEFORE_START,near_him?&live:nullptr);
            voice_dance(i,choice.played);
        }
        before_start=before_start && result==GP_BEFORE_START;
        if(pose.count) {annotate(i,choice,pose);out[count++]=pose;}
    }
    // begin_batch draws in array order: farthest root first. Insertion sort
    // keeps equal depths in request order and never allocates.
    for(uint32_t i=1;i<count;++i) {
        const auto value=out[i];uint32_t j=i;
        for(;j && out[j-1].view_projection[15]<value.view_projection[15];--j)out[j]=out[j-1];
        out[j]=value;
    }
    // Every ghost has fallen, faded or finished, not merely gone from this
    // frame (a pause menu may hide them all; the defeat resumes after it).
    bool finished=true;
    for(uint32_t i=0;i<GP_GHOSTS && i<active_request.clip_count;++i)
        finished=finished && (report.clip_decisions[i]==GP_STAR_ENDED || report.clip_decisions[i]==GP_AFTER_END);
    if(defeat.active && finished && list.post)defeat.active=false;
    report.visible=count;
    if(count)summary=before_start?GP_BEFORE_START:GP_READY;
    decision(summary);
    if(count)token=candidate;
    return count!=0;
}
void retire_on_context_loss() {
    if(!renderer_owner())return;
    retire_clips();
    const bool released=retire_model();
    have_request=false;clear_timeline();sound_control(false,0);forget_heard(Forget::move);
    next_boundary();
    decide_all(GP_CONTEXT_LOST);
    if(released)render_thread=0;
}
}
