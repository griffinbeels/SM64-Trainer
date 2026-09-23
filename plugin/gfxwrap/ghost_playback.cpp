#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "ghost_playback.h"
#include "ghost_proximity.h"
#include "ghost_pose.h"
#include "ghost_compositor.h"
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
struct Clip {
    std::atomic<uint32_t> state{Empty};
    uint32_t cancellation=0, index_count=0, action_count=0;
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
    bool reclaim_owned=false; // worker only while RECLAIMING
} model;
std::atomic_flag worker_gate=ATOMIC_FLAG_INIT;
DWORD worker_thread=0; // protected by worker_gate
std::atomic<uint32_t> cancellation{1}, context_serial{1};
std::atomic<bool> exhausted{false};
std::atomic<uint64_t> desired_model{0}, enabled_revision{0}, resident_bytes{0};
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
bool sequence_reported=false;
bool held[GP_CLIP_SLOTS]{};          // this renderer owns the slot (Owned or Unwanted)
Clip *bound[GP_GHOSTS]{};            // request order; null until adopted
gp_request active_request{};
gp_request timeline_request{};
gp_status report{};
uint64_t touch_revision=0, touch[GP_GHOSTS]{};
bool have_touch[GP_GHOSTS]{};
ghost_pose::Scratch scratch[GP_GHOSTS]; // one borrowed mesh per displayed ghost
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
// run: a new attempt. restore: a state load (records ahead are newly passed
// again). move: a nudge, seek or pause; records already heard stay silent.
enum class Forget { run, restore, move };
void forget_heard(Forget why) {
    for(uint32_t i=0;i<GP_GHOSTS;++i) {
        heard[i]=unheard;
        if(why==Forget::move)continue;
        heard_clip[i]=nullptr;heard_fresh[i]=why==Forget::run;loudest_reset[i]=true;
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
    clip.allocation=0; clip.index_count=clip.action_count=0;
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
        if(!valid_shadow(s) || !valid_sound(s))return false;
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
            || r.proximity_distance<0 || r.proximity_distance>400
            || r.shadow_enabled>1 || r.occlusion_fade>1 || r.reserved3
            || !std::isfinite(r.volume) || r.volume<0 || r.volume>1
            || r.suspended>1 || r.reserved4) return false;
    const gp_clip unused{};
    for(uint32_t i=0;i<GP_GHOSTS;++i) {
        const auto &clip=r.clips[i];
        if(i>=r.clip_count) {if(std::memcmp(&clip,&unused,sizeof(clip)))return false;continue;}
        if(clip.hue_degrees>359 || clip.reserved)return false;
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
            || !r.origin.sequence || !r.origin.continuity || (r.origin.flags&~GO_RECOVERED)
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
    if(!reports.put(report)) {exhausted.store(true);revoke(GP_EXHAUSTED);}
}
void decide_all(uint32_t value) {
    // Every ghost hidden: none hears records passed meanwhile.
    for(auto &last:heard)last=unheard;
    report.visible=0;
    std::memset(report.clip_decisions,uint8_t(value),sizeof(report.clip_decisions));
    decision(value);
}
void clear_timeline() {have_timeline=false;star_ended=false;timeline_request={};}
void unbind() {
    for(auto &clip:bound)clip=nullptr;
    touch_revision=0;std::memset(have_touch,0,sizeof(have_touch));
}
void retire_clips() {
    for(uint32_t i=0;i<GP_CLIP_SLOTS;++i)
        if(held[i]) {clips[i].state.store(Retired,std::memory_order_release);held[i]=false;}
    unbind();
}
// The worker released these ids; stop drawing them before this list starts.
void retire_unwanted() {
    for(uint32_t i=0;i<GP_CLIP_SLOTS;++i) {
        uint32_t expected=Unwanted;
        if(!held[i] || !clips[i].state.compare_exchange_strong(expected,Retired))continue;
        held[i]=false;
        for(auto &clip:bound)if(clip==&clips[i])clip=nullptr;
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
    if(!confirmed) {live.timing.bind(next.origin);live.restored=next.origin.sequence;}
    live.owner=next;live.bound=true;
    return !confirmed;
}
uint32_t active_count() {uint32_t count=0;for(auto *clip:bound)count+=clip!=nullptr;return count;}
struct Coordinate {uint64_t target;bool below_zero;};
// One ghost against the shared live coordinate. Hides only this ghost.
// Visible ghost: hand over every record passed since its last drawn one.
// Backward moves and long jumps only re-establish where it is.
void hear(uint32_t index,const Clip& clip,uint32_t record,bool before_start) {
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
            for(uint32_t k=0;k<sample.sound.count && k<GC_SOUND_EVENTS;++k)sink->cue(index,sample.sound.bits[k]);
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
// sound starts at the ghost's current place. fade follows its star-dance fade.
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
// fade: 1 normally; after a dance tail's last record it falls by
// 1/GP_STAR_FADE_FRAMES a frame on the held last pose, and is 0 once hidden.
uint32_t select(uint32_t index,const Coordinate& frame,const gc_sample& live,const ghost::WorldPass& world,
                ghost_compositor::Pose& out,uint32_t& record,float& fade) {
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
    // record. FIXED seek ignores it, like both end policies.
    const bool dances=(h.flags&GCLIP_DANCE_TAIL) && active_request.mode==GP_FOLLOW;
    if(have_touch[index] && (first>=touch[index] || (!dances && target>=touch[index])))return GP_STAR_ENDED;
    uint64_t at=target;
    if(target>last) {
        if(!dances)return GP_AFTER_END;
        const uint64_t past=target-last;
        if(past>=GP_STAR_FADE_FRAMES) {fade=0;return GP_STAR_ENDED;}
        fade=float(GP_STAR_FADE_FRAMES-past)/float(GP_STAR_FADE_FRAMES);
        at=last; // the dance's final pose, held while it fades
    }
    uint32_t lo=0,hi=clip->index_count;
    while(lo<hi) {
        const uint32_t middle=lo+(hi-lo)/2;
        if(clip_coordinate(*clip,middle)<at)lo=middle+1;else hi=middle;
    }
    const auto &selected=clip->frames[lo];
    if(!frame.below_zero && at>=first && clip_coordinate(*clip,lo)!=at) {gaps.fetch_add(1);return GP_GAP;}
    if(live.level!=selected.sample.level || live.area!=selected.sample.area)return GP_WRONG_SCENE;
    if(!sequence_reported) {
        report.selected_sequence_lo=uint32_t(selected.sample.sequence);
        report.selected_sequence_hi=uint32_t(selected.sample.sequence>>32);
        sequence_reported=true;
    }
    const gclip_frame *shown=&selected;
    if(!(selected.sample.valid_fields&GC_VALID_POSE) && !(shown=bridged(*clip,lo))) {
        refusals.fetch_add(1);return GP_INVALID_FRAME;
    }
    gs_record recorded{};recorded.bytes=sizeof(recorded);recorded.version=GS_VERSION;
    recorded.configuration=shown->configuration;recorded.sample=shown->sample;
    recorded.world.status=shown->world_status;
    evaluations.fetch_add(1);
    float opacity=active_request.opacity*fade;
    if(active_request.proximity_enabled && (live.valid_fields&GC_VALID_POSE))
        opacity*=ghost_proximity::factor(live.pose.position,live.pose.scale,
            shown->sample.pose.position,shown->sample.pose.scale,active_request.proximity_distance);
    if(ghost_pose::evaluate(model.value,recorded,live,world,opacity,scratch[index],out)!=ghost_pose::Result::ok) {
        out={};refusals.fetch_add(1);return GP_POSE_REFUSED;
    }
    out.hue_degrees=active_request.tint_enabled?float(active_request.clips[index].hue_degrees):0.0f;
    // Player first: the same live Mario region for every ghost this frame,
    // under the live view-projection its body and shadow are drawn with.
    ghost_occlusion::region(out.view_projection,active_request.occlusion_fade!=0,
        (live.valid_fields&GC_VALID_POSE)!=0,live.pose.position,live.pose.scale,out.occlusion);
    if(active_request.shadow_enabled && (shown->sample.valid_fields&GC_VALID_SHADOW)
            && !ghost_pose::shadow(model.value,shown->sample,out.view_projection,shadows[index].vertices,
                                   shadows[index].texture,out.shadow)) {
        out={};refusals.fetch_add(1);return GP_POSE_REFUSED;
    }
    rendered.fetch_add(1);record=lo;
    return frame.below_zero || target<first?GP_BEFORE_START:GP_READY;
}
}

namespace ghost_playback {
const gp_api *api() {return &api_v2;}
void set_sound_sink(const SoundSink *sink) {sound_sink.store(sink,std::memory_order_release);}
void safe_boundary(uint32_t rom,uint32_t configuration) {
    const auto thread=GetCurrentThreadId();
    if(render_thread && render_thread!=thread) {refusals.fetch_add(1);return;}
    render_thread=thread;live_rom=rom;live_configuration=configuration;
    next_boundary();
    retire_unwanted();
    PublishedRequest published{};const bool received=requests.get(published);
    const auto &next=published.value; const auto current_cancel=cancellation.load();
    const bool wanted=!exhausted.load() && rom && configuration && received && next.enabled
        && published.cancellation==current_cancel
        && next.rom_generation==rom && next.configuration==configuration;
    if(!wanted) {
        retire_clips();retire_model();have_request=false;clear_timeline();live_recovery.clear();
        sound_control(false,0);forget_heard(Forget::move);decide_all(GP_DISABLED);return;
    }
    if(enabled_revision.load()!=next.revision) {
        // Withdrawal retains reusable assets, but an explicit worker model
        // replacement must still be released at this safe render boundary.
        if(have_model && (model.cancellation!=current_cancel || model.id!=desired_model.load()))retire_model();
        sound_control(false,0);forget_heard(Forget::move);decide_all(GP_DISABLED);return;
    }
    if(acquired_cancel!=current_cancel) {clear_timeline();retire_clips();live_recovery.clear();}
    if(next.mode==GP_FOLLOW && (!have_timeline || !same_origin(timeline_request,next))) {
        clear_timeline();have_timeline=true;timeline_request=next;
    }
    if(have_model && (model.id!=next.model_id || model.cancellation!=current_cancel || model.id!=desired_model.load()))
        if(!retire_model()) {decide_all(GP_PREPARING);return;}
    const bool changed=touch_revision!=next.revision;
    const bool first=!have_request;
    const gp_request previous=active_request;
    active_request=next;have_request=true;acquired_cancel=current_cancel;
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
    // Bind request order to held storage, then retire held clips the new
    // command no longer lists: the renderer holds exactly the displayed set.
    bool listed[GP_CLIP_SLOTS]{};
    for(uint32_t i=0;i<GP_GHOSTS;++i) {
        Clip *clip=i<next.clip_count?adopt(next.clips[i].clip_id,current_cancel):nullptr;
        if(clip)listed[clip-clips]=true;
        if(clip!=bound[i])heard_clip[i]=nullptr; // a different recording: nothing heard yet
        if(changed || clip!=bound[i]) {
            bound[i]=clip;have_touch[i]=false;touch[i]=0;
            if(clip)have_touch[i]=recorded_touch(*clip,next,touch[i]);
        }
        report.clip_decisions[i]=uint8_t(i>=next.clip_count?GP_DISABLED:clip?GP_READY:GP_PREPARING);
    }
    for(uint32_t i=0;i<GP_CLIP_SLOTS;++i)
        if(held[i] && !listed[i]) {clips[i].state.store(Retired,std::memory_order_release);held[i]=false;}
    touch_revision=next.revision;
    report.clips_active=active_count();
    decision(have_model && report.clips_active==next.clip_count?GP_READY:GP_PREPARING);
}
bool atlas(sg_atlas_view *out,uint64_t *id) {
    if(out)*out={};if(id)*id=0;
    if(!out || !id || !renderer_owner() || !have_model || exhausted.load())return false;
    if(sg_model_get_atlas(model.value,out)!=SG_OK)return false;
    *id=model.id;return true;
}
bool current(const gp_draw_token& token) {
    return renderer_owner() && have_request && have_model && active_count() && !exhausted.load()
        && token.revision && enabled_revision.load()==token.revision && active_request.revision==token.revision
        && cancellation.load()==token.cancellation && acquired_cancel==token.cancellation
        && context_serial.load()==token.context_serial && model.id==desired_model.load()
        && active_request.rom_generation==live_rom && active_request.configuration==live_configuration;
}
bool evaluate(const gc_sample& live,const ghost::WorldPass& world,
              ghost_compositor::Pose *out,uint32_t& count,gp_draw_token& token) {
    count=0;token={};
    if(!out)return false;
    for(uint32_t i=0;i<GP_GHOSTS;++i)out[i]={};
    if(!renderer_owner())return false;
    next_boundary();
    gp_draw_token candidate{active_request.revision,acquired_cancel,context_serial.load()};
    if(!current(candidate)) {decide_all(GP_WRONG_IDENTITY);return false;}
    if(live.bytes!=sizeof(live) || live.version!=GC_VERSION || !live.epoch || !live.sequence
            || !live.dlist_serial || !(live.valid_fields&GC_VALID_CLOCK)) {decide_all(GP_NO_CLOCK);return false;}
    Coordinate frame{active_request.seek_igt,false};
    sequence_reported=false;
    if(active_request.mode==GP_FOLLOW) {
        // Same refusals as the worker: an unresolved or ambiguous live restore
        // hides the ghost; a resolved one draws on the frame it happened.
        auto origin=active_request.origin;
        if(live_recovery.bound) {
            live_recovery.timing.observe(live,active_request.star_actions,
                active_request.star_ends?active_request.star_action_count:0);
            if(!live_recovery.timing.current(origin)) {decide_all(GP_NO_CLOCK);return false;}
            // A restoration revives the run, exactly like the worker's new command.
            if(origin.sequence!=live_recovery.restored) {
                star_ended=false;live_recovery.restored=origin.sequence;forget_heard(Forget::restore);
            }
        }
        if(active_request.suspended) {
            if(!live_recovery.bound || live_recovery.restored==suspend_mark) {
                sound_control(false,0);decide_all(GP_DISABLED);return false;
            }
            sound_control(true,active_request.volume); // a load revived it on this frame
        }
        if(!valid_clock(live) || (live.clock.valid&(GCC_RAW_VALID|GCC_ELAPSED_VALID))
                !=(GCC_RAW_VALID|GCC_ELAPSED_VALID) || live.epoch!=origin.epoch
                || live.sequence<origin.sequence || live.clock.continuity_id!=origin.continuity
                || !elapsed_coordinate(live.clock.elapsed_frames,origin.elapsed,
                    origin.offset,frame.target)) {decide_all(GP_NO_CLOCK);return false;}
        if(origin.kind==GO_STAR && live.clock.origin_id!=origin.star_generation)star_ended=true;
        if(active_request.star_ends)for(uint32_t i=0;i<active_request.star_action_count;++i)
            if(live.live_action==active_request.star_actions[i])star_ended=true;
        if(star_ended) {decide_all(GP_STAR_ENDED);return false;}
        if(active_request.nudge_frames<0) {
            const auto amount=uint32_t(-active_request.nudge_frames);
            if(frame.target<amount){frame.below_zero=true;frame.target=0;}else frame.target-=amount;
        } else {
            const auto amount=uint32_t(active_request.nudge_frames);
            if(frame.target>UINT64_MAX-amount){decide_all(GP_NO_CLOCK);return false;}
            frame.target+=amount;
        }
    }
    report.visible=0;
    uint32_t summary=GP_DISABLED;bool before_start=true;
    for(uint32_t i=0;i<GP_GHOSTS;++i) {
        if(i>=active_request.clip_count) {report.clip_decisions[i]=uint8_t(GP_DISABLED);continue;}
        ghost_compositor::Pose pose{};uint32_t record=0;float fade=1;
        const auto result=select(i,frame,live,world,pose,record,fade);
        if(!current(candidate)) {
            // A revocation during any evaluation discards the whole candidate.
            for(uint32_t j=0;j<GP_GHOSTS;++j)out[j]={};
            count=0;refusals.fetch_add(1);decide_all(GP_POSE_REFUSED);return false;
        }
        report.clip_decisions[i]=uint8_t(result);
        if(!i)summary=result;
        if(result!=GP_READY && result!=GP_BEFORE_START) { // hidden: silent
            heard[i]=unheard;
            if(fade==0)silence(i);
            continue;
        }
        place(i,live,bound[i]->frames[record].sample.pose.position,fade);
        hear(i,*bound[i],record,result==GP_BEFORE_START);
        before_start=before_start && result==GP_BEFORE_START;
        if(pose.count)out[count++]=pose;
    }
    // begin_batch draws in array order: farthest root first. Insertion sort
    // keeps equal depths in request order and never allocates.
    for(uint32_t i=1;i<count;++i) {
        const auto value=out[i];uint32_t j=i;
        for(;j && out[j-1].view_projection[15]<value.view_projection[15];--j)out[j]=out[j-1];
        out[j]=value;
    }
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
