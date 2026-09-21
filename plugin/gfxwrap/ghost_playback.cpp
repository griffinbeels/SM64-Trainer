#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "ghost_playback.h"
#include "ghost_pose.h"
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
enum ClipState : uint32_t { Empty, Building, Ready, Owned, Retired, Reclaiming };
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
gp_request last_worker_request{};

// Everything below is renderer-owned until context loss releases the binding.
DWORD render_thread=0;
uint32_t live_rom=0, live_configuration=0, acquired_cancel=0;
bool have_model=false, have_request=false, have_timeline=false, star_ended=false;
Clip *active_clip=nullptr;
gp_request active_request{};
gp_request timeline_request{};
gp_status report{};
uint64_t touch_revision=0, recorded_touch=0;
bool have_recorded_touch=false;
ghost_pose::Scratch scratch;

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
void recorded_boundary(const Clip& clip,const gp_request& request) {
    have_recorded_touch=false;recorded_touch=0;touch_revision=request.revision;
    if(request.mode!=GP_FOLLOW || !request.star_ends)return;
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
        if(!have_recorded_touch || coordinate<recorded_touch)recorded_touch=coordinate;
        have_recorded_touch=true;
    }
}
bool valid_clip(const gclip_header& h,const gclip_frame *frames,uint32_t& indexed) {
    if(std::memcmp(h.magic,GCLIP_MAGIC,8) || h.version!=GCLIP_VERSION || h.bytes!=sizeof(h)
            || h.record_bytes!=sizeof(gclip_frame) || !h.record_count || h.record_count>GCLIP_MAX_RECORDS
            || h.rom_layout!=GC_ROM_US || h.sample_version!=GC_VERSION || !h.configuration || !h.epoch
            || !nonzero_id(h.clip_id) || !h.qpc_frequency || h.qpc_anchor<0
            || !std::isfinite(h.utc_anchor) || h.utc_anchor<=0
            || h.level<4 || h.level>36 || h.area<1 || h.area>8 || h.flags
            || h.reserved[0] || h.reserved[1] || !frames
            || (h.clock_kind!=GCLIP_STAR_CLOCK && h.clock_kind!=GCLIP_SEGMENT_CLOCK)
            || !h.origin_continuity || h.origin_raw>UINT16_MAX) return false;
    indexed=0;uint32_t prior_timer=0;uint64_t sequence=0,serial=0,prior_elapsed=0;
    int64_t prior_qpc=0;bool playable=false,indexing=true;
    for(uint32_t i=0;i<h.record_count;++i) {
        const auto &f=frames[i]; const auto &s=f.sample;
        if(f.bytes!=sizeof(f) || f.version!=GCLIP_VERSION || f.configuration!=h.configuration
                || s.bytes!=sizeof(s) || s.version!=GC_VERSION || s.epoch!=h.epoch
                || s.sequence<=sequence || s.dlist_serial<=serial || (s.valid_fields&~31u)
                || s.status>GC_UNSUPPORTED_HEAD
                || s.qpc<prior_qpc || (!s.status && s.valid_fields!=31)
                || bool(s.valid_fields&GC_VALID_ACTION)!=bool(s.live_action)
                || f.world_status>static_cast<uint32_t>(ghost::PassStatus::InvalidIdentity)) return false;
        const gc_pose zero_pose{};const gc_camera zero_camera{};
        if(!(s.valid_fields&GC_VALID_POSE) && std::memcmp(&s.pose,&zero_pose,sizeof(s.pose)))return false;
        if(!(s.valid_fields&GC_VALID_CAMERA) && std::memcmp(&s.camera,&zero_camera,sizeof(s.camera)))return false;
        if(!(s.valid_fields&GC_VALID_WARP) && (s.pending_warp_op || s.delayed_warp_timer))return false;
        if(s.valid_fields&GC_VALID_POSE)for(unsigned axis=0;axis<3;++axis)
            if(!std::isfinite(s.pose.position[axis]) || !std::isfinite(s.pose.scale[axis]))return false;
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
            if(f.world_status==GCLIP_WORLD_READY && (s.valid_fields&GC_VALID_POSE))playable=true;
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
        if((state==Ready || state==Owned) && clip.cancellation==before && id_equal(clip.header.clip_id,header->clip_id))
            return worker_leave(std::memcmp(&clip.header,header,sizeof(*header)) ||
                std::memcmp(clip.frames,frames,header->record_count*sizeof(*frames))?GP_INVALID:GP_OK);
    }
    const uint64_t allocation=uint64_t(header->record_count)*sizeof(gclip_frame)+uint64_t(indexed)*sizeof(uint32_t);
    // Pending clips never selected by the current command may be displaced
    // on the worker. Owned/retired storage still counts until its real release.
    for(auto &clip:clips) {
        if(clip.state.load()!=Ready || id_equal(clip.header.clip_id,last_worker_request.clip_id))continue;
        uint32_t expected=Ready;
        if(clip.state.compare_exchange_strong(expected,Reclaiming))free_clip(clip);
    }
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
            || r.enabled>1 || r.tint_enabled>1 || r.hue_degrees>359
            || r.reserved0 || r.reserved[0] || r.reserved[1]) return false;
    if(!r.enabled) return true;
    if(!r.owner_token || !r.model_id || !r.rom_generation || !r.configuration || !nonzero_id(r.clip_id)
            || r.mode>GP_FIXED || r.nudge_frames < -600 || r.nudge_frames>600
            || !std::isfinite(r.opacity) || r.opacity<0 || r.opacity>1 || r.star_ends>1
            || r.star_action_count>GP_STAR_ACTIONS || (r.star_ends && !r.star_action_count)) return false;
    for(uint32_t i=0;i<GP_STAR_ACTIONS;++i)
        if(i<r.star_action_count ? !r.star_actions[i] : r.star_actions[i]!=0) return false;
    if(r.mode==GP_FIXED) {const gp_origin zero{};if(std::memcmp(&r.origin,&zero,sizeof(zero)))return false;}
    else if((r.origin.kind!=GO_STAR && r.origin.kind!=GO_SEGMENT) || !r.origin.epoch
            || !r.origin.sequence || !r.origin.continuity || r.origin.raw>UINT16_MAX || r.origin.reserved)return false;
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
    last_worker_request=*request;
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
        out->clips_retired+=s==Retired || s==Reclaiming;
    }
    out->evaluations=evaluations.load();out->rendered=rendered.load();out->gaps=gaps.load();
    out->refusals=refusals.load();out->adoption_busy=adoption_busy.load();out->model_error=model_error.load();
    return GP_OK;
}
const gp_api api_v2={sizeof(gp_api),GP_VERSION,{0,0},prepare_model,prepare_clip,configure,revoke,collect,release_worker,status,withdraw};

void decision(uint32_t value) {
    report.bytes=sizeof(report);report.version=GP_VERSION;report.decision=value;
    if(!reports.put(report)) {exhausted.store(true);revoke(GP_EXHAUSTED);}
}
void clear_timeline() {have_timeline=false;star_ended=false;timeline_request={};}
void retire_clip() {
    if(active_clip) {active_clip->state.store(Retired,std::memory_order_release);active_clip=nullptr;}
    touch_revision=recorded_touch=0;have_recorded_touch=false;
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
}

namespace ghost_playback {
const gp_api *api() {return &api_v2;}
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
        retire_clip();retire_model();have_request=false;clear_timeline();decision(GP_DISABLED);return;
    }
    if(enabled_revision.load()!=next.revision) {
        // Withdrawal retains reusable assets, but an explicit worker model
        // replacement must still be released at this safe render boundary.
        if(have_model && (model.cancellation!=current_cancel || model.id!=desired_model.load()))retire_model();
        decision(GP_DISABLED);return;
    }
    if(acquired_cancel!=current_cancel)clear_timeline();
    if(next.mode==GP_FOLLOW && (!have_timeline || !same_origin(timeline_request,next))) {
        clear_timeline();have_timeline=true;timeline_request=next;
    }
    if(active_clip && (acquired_cancel!=current_cancel || !id_equal(active_clip->header.clip_id,next.clip_id)))retire_clip();
    if(have_model && (model.id!=next.model_id || model.cancellation!=current_cancel || model.id!=desired_model.load()))
        if(!retire_model()) {decision(GP_PREPARING);return;}
    active_request=next;have_request=true;acquired_cancel=current_cancel;
    report={};report.revision=next.revision;report.owner_token=next.owner_token;report.model_id=next.model_id;
    std::memcpy(report.clip_id,next.clip_id,16);report.rom_generation=rom;report.configuration=configuration;
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
    if(!active_clip) for(auto &clip:clips) {
        uint32_t expected=Ready;
        if(!clip.state.compare_exchange_strong(expected,Owned))continue;
        if(clip.cancellation==current_cancel && id_equal(clip.header.clip_id,next.clip_id)) {active_clip=&clip;break;}
        clip.state.store(clip.cancellation==current_cancel?Ready:Retired,std::memory_order_release);
    }
    if(active_clip && touch_revision!=next.revision)recorded_boundary(*active_clip,next);
    decision(have_model && active_clip?GP_READY:GP_PREPARING);
}
bool atlas(sg_atlas_view *out,uint64_t *id) {
    if(out)*out={};if(id)*id=0;
    if(!out || !id || !renderer_owner() || !have_model || exhausted.load())return false;
    if(sg_model_get_atlas(model.value,out)!=SG_OK)return false;
    *id=model.id;return true;
}
bool current(const gp_draw_token& token) {
    return renderer_owner() && have_request && have_model && active_clip && !exhausted.load()
        && token.revision && enabled_revision.load()==token.revision && active_request.revision==token.revision
        && cancellation.load()==token.cancellation && acquired_cancel==token.cancellation
        && context_serial.load()==token.context_serial && model.id==desired_model.load()
        && active_request.rom_generation==live_rom && active_request.configuration==live_configuration;
}
bool evaluate(const gc_sample& live,const ghost::WorldPass& world,
              ghost_compositor::Pose& out,gp_draw_token& token) {
    out={};token={};
    if(!renderer_owner())return false;
    next_boundary();
    gp_draw_token candidate{active_request.revision,acquired_cancel,context_serial.load()};
    if(!current(candidate)) {decision(GP_WRONG_IDENTITY);return false;}
    const auto &h=active_clip->header;
    if(live.bytes!=sizeof(live) || live.version!=GC_VERSION || !live.epoch || !live.sequence
            || !live.dlist_serial || !(live.valid_fields&GC_VALID_CLOCK)) {decision(GP_NO_CLOCK);return false;}
    uint64_t target=active_request.seek_igt;bool below_zero=false;
    if(active_request.mode==GP_FOLLOW) {
        const auto& origin=active_request.origin;
        if(origin.kind!=h.clock_kind) {decision(GP_WRONG_IDENTITY);return false;}
        if(!valid_clock(live) || (live.clock.valid&(GCC_RAW_VALID|GCC_ELAPSED_VALID))
                !=(GCC_RAW_VALID|GCC_ELAPSED_VALID) || live.epoch!=origin.epoch
                || live.sequence<origin.sequence || live.clock.continuity_id!=origin.continuity
                || !elapsed_coordinate(live.clock.elapsed_frames,origin.elapsed,
                    origin.kind==GO_STAR?origin.raw:0,target)) {decision(GP_NO_CLOCK);return false;}
        if(origin.kind==GO_STAR && live.clock.origin_id!=origin.star_generation)star_ended=true;
        if(active_request.star_ends)for(uint32_t i=0;i<active_request.star_action_count;++i)
            if(live.live_action==active_request.star_actions[i])star_ended=true;
        if(star_ended) {decision(GP_STAR_ENDED);return false;}
        if(active_request.nudge_frames<0) {
            const auto amount=uint32_t(-active_request.nudge_frames);
            if(target<amount){below_zero=true;target=0;}else target-=amount;
        } else {
            const auto amount=uint32_t(active_request.nudge_frames);
            if(target>UINT64_MAX-amount){decision(GP_NO_CLOCK);return false;}
            target+=amount;
        }
    }
    const uint64_t first=clip_coordinate(*active_clip,0);
    const uint64_t last=clip_coordinate(*active_clip,active_clip->index_count-1);
    // Before-start clamps to the first pose, including when that pose already
    // carries a touch. Unlike the live-end latch, this moves with the nudge.
    if(have_recorded_touch && (target>=recorded_touch || first>=recorded_touch))
        {decision(GP_STAR_ENDED);return false;}
    if(target>last) {decision(GP_AFTER_END);return false;}
    uint32_t lo=0,hi=active_clip->index_count;
    while(lo<hi) {
        const uint32_t middle=lo+(hi-lo)/2;
        if(clip_coordinate(*active_clip,middle)<target)lo=middle+1;else hi=middle;
    }
    const auto &selected=active_clip->frames[lo];
    if(!below_zero && target>=first && clip_coordinate(*active_clip,lo)!=target)
        {gaps.fetch_add(1);decision(GP_GAP);return false;}
    if(live.level!=selected.sample.level || live.area!=selected.sample.area) {decision(GP_WRONG_SCENE);return false;}
    report.selected_sequence_lo=uint32_t(selected.sample.sequence);
    report.selected_sequence_hi=uint32_t(selected.sample.sequence>>32);
    if(selected.world_status!=GCLIP_WORLD_READY || !(selected.sample.valid_fields&GC_VALID_POSE)) {
        refusals.fetch_add(1);decision(GP_INVALID_FRAME);return false;
    }
    gs_record recorded{};recorded.bytes=sizeof(recorded);recorded.version=GS_VERSION;
    recorded.configuration=selected.configuration;recorded.sample=selected.sample;
    recorded.world.status=selected.world_status;
    evaluations.fetch_add(1);
    const auto result=ghost_pose::evaluate(model.value,recorded,live,world,active_request.opacity,scratch,out);
    if(result!=ghost_pose::Result::ok || !current(candidate)) {
        out={};refusals.fetch_add(1);decision(GP_POSE_REFUSED);return false;
    }
    out.hue_degrees=active_request.tint_enabled?float(active_request.hue_degrees):0.0f;
    token=candidate;rendered.fetch_add(1);decision(below_zero || target<first?GP_BEFORE_START:GP_READY);return true;
}
void retire_on_context_loss() {
    if(!renderer_owner())return;
    retire_clip();
    const bool released=retire_model();
    have_request=false;clear_timeline();
    next_boundary();
    decision(GP_CONTEXT_LOST);
    if(released)render_thread=0;
}
}
