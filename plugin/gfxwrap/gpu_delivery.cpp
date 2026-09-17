#include "gpu_delivery.h"
#include "gpu_delivery_diagnostics.h"
#include "gpu_delivery_metrics.h"
#include "gpu_delivery_context.h"
#include "source_snapshot.h"
#include "gpu_selection.h"
#include "gpu_bridge.h"
#include "stamp_adapter.h"
#include <atomic>
#include <cstring>
#include <memory>
#include <new>
#include <cwchar>
#ifdef GD_TEST_HOST
#include <cstdio>
#define TRACE(step) do{fprintf(stderr,"delivery %s\n",step);fflush(stderr);}while(0)
#else
#define TRACE(step) ((void)0)
#endif

// Same transaction certificate as sa_probe_frontier, valid after gate revocation.
extern "C" int __cdecl sa_probe_quiescent(uint64_t,sa_frontier*);
namespace {
using ChannelResult=gpu_channel::Result;
constexpr uint32_t bootstrap_omission=SA_BOOTSTRAP_OMISSION;
constexpr uint32_t setup_deadline_ms=5000u; // separate from admitted-picture max_age_ms
std::atomic<gd_diagnostic_callback> diagnostic_callback{nullptr};
#ifdef GD_TEST_HOST
std::atomic<bool> test_hold_poll{false};
void (__cdecl *test_preparation_probe)()=nullptr;
std::atomic<void (__cdecl *)()> test_iteration_probe{nullptr},test_retired_probe{nullptr};
std::atomic<uint32_t> test_deadline_visits{0};
gpu_selection::Result (*test_sample)(const snapshot::Image&,uint8_t*)=nullptr;
#endif
uint64_t qpc(){LARGE_INTEGER v{};QueryPerformanceCounter(&v);return uint64_t(v.QuadPart);}
bool source_valid(const rs_api* s,const rcl_api* c){
    return s && c && s->bytes==sizeof(*s) && s->version==RS_ABI_V2 && !s->reserved
        && (s->capabilities&(RS_CAP_BOUNDARY|RS_CAP_SOURCE_FORMAT))==(RS_CAP_BOUNDARY|RS_CAP_SOURCE_FORMAT)
        && s->activate && s->disarm && s->take && s->release && s->begin_capture && s->end_capture
        && c->bytes==sizeof(*c) && c->version==RCL_ABI_V1 && !c->reserved
        && c->capabilities==RCL_CAP_NEVER_CURRENT_ANCHOR && c->acquire && c->release && c->health && c->error;
}
bool limits_valid(const gd_limits* l){
    return l && l->bytes==sizeof(*l) && l->version==GD_VERSION && !l->reserved
        && l->max_slots && l->max_slots<=RB_SLOTS && l->max_age_ms>=100 && l->max_age_ms<=30000
        && l->snapshot_bytes && l->snapshot_bytes<=(uint64_t(512)<<20)
        && l->bridge_bytes && l->bridge_bytes<=(uint64_t(512)<<20)
        && l->channel_bytes>=GC_HEADER_BYTES && l->channel_bytes<=GC_MAX_MAP_BYTES
        && l->sample_bytes && l->sample_bytes<=GC_MAX_MAP_BYTES && l->poll_ms && l->poll_ms<=16;
}
struct Pending {
    const rb_record* record=nullptr;
    snapshot::Image image{};
    gc_decision decision{};
    uint64_t token=0,admitted=0;
    bool ready=false,sampled=false,published=false,decided=false,returning=false,copied=false;
    bool omitted=false; // worker dropped it (no usable stamp); image returns, nothing publishes
};
struct BridgeCredit {
    uint64_t token=0;bool receipt=false,published=false;
    gc_decision decision{};
};

struct Runtime {
    rs_api source{}; rcl_api contexts{}; gd_limits limits{};
    HANDLE wake=nullptr,changed=nullptr;
    std::atomic<uint32_t> gate{0},cancel{1},session_cancel{0},state{GD_OFF},reason{GD_NONE};
    // Worker-owned diagnostic, readable by the watchdog. Later disarms still
    // update cancellation state but cannot erase the first worker failure.
    std::atomic<uint32_t> first_failure{GD_NONE};
    std::atomic<uint32_t> request_state{0},surface_state{0}; // 0 free,1 writing,2 ready,3 owned
    std::atomic<uint32_t> status_seq{0},status_words[sizeof(gd_status)/4]{};
    std::atomic<bool> exhausted{false};
    gc_header request{};uint64_t budget=0;uint32_t epoch=0;
    rb_surface mailbox{},surface{};uint32_t mailbox_epoch=0;
    source_capture::Snapshots snapshots;
    gpu_delivery::Context context;
    gpu_selection::Sampler sampler;
    gpu_bridge::Pool bridge;
    std::unique_ptr<gpu_channel::Producer> channel;
    std::unique_ptr<uint8_t[]> sample;
    Pending pending[RB_SLOTS]{};BridgeCredit credits[GC_BRIDGES]{};
    uint64_t frequency=0,started=0,channel_started=0,drain_started=0,accounted=0,offer_token=0,bridge_token=0;
    uint64_t status_time=0,frontier_qpc=0,frontier_occurrence=0;
    uint32_t refusal_generation=0,error=0;
    bool prepared=false,bridge_attempted=false,sampler_attempted=false,quarantine=false;
    DWORD worker_thread=0;
    uint32_t diagnostic_epoch=0,phase=GD_PHASE_WAIT;
    uint64_t phase_started=0,last_active_end=0;
    gd_phase_diagnostic phase_totals[GD_PHASE_COUNT]{};
    gd_phase_diagnostic phase_recent[GD_PHASE_COUNT]{};
    gd_cadence_diagnostic cadence_total{},cadence_recent{};
    uint64_t summary_started=0,last_source_qpc=0,previous_emit_ticks=0;
    uint64_t sample_calls=0,sample_reuses=0,publish_busy=0;
    // Pictures the source or the snapshot pool could not admit while ACTIVE,
    // plus records dropped for a missing stamp. Counted, logged in the summary,
    // never fatal: the replay shows the previous picture held and the input
    // lanes show the polled fill for those frames.
    uint64_t refused_pictures=0;
    void reset_metrics(){
        phase=GD_PHASE_WAIT;phase_started=last_active_end=0;
        memset(phase_totals,0,sizeof phase_totals);memset(phase_recent,0,sizeof phase_recent);
        cadence_total={};cadence_recent={};summary_started=last_source_qpc=previous_emit_ticks=0;
        sample_calls=sample_reuses=publish_busy=0;
    }
    void phase_done(uint32_t current,uint64_t start,uint64_t end){
        delivery_metrics::record(phase_totals[current],start,end);
        delivery_metrics::record(phase_recent[current],start,end);
    }
    void observe_source(const rb_record&r){
        // Read while source.take still lends this immutable record. Omissions
        // remain boundary observations; they are not counted as captured pictures.
        if(state.load()!=GD_ACTIVE || r.epoch!=epoch || r.outcome!=RB_OBSERVED)return;
        const uint64_t at=r.surface.boundary_qpc>0?uint64_t(r.surface.boundary_qpc):0;
        for(auto*row:{&cadence_total,&cadence_recent}){
            ++row->records;row->pictures+=r.image.ownership==RB_IMAGE_SUBMITTED;
            row->same_origin+=r.image.ownership==RB_IMAGE_NONE && r.image.status==SA_SAME_ORIGIN;
            delivery_metrics::cadence(*row,last_source_qpc,at,frequency);
        }
        if(at && at>=last_source_qpc)last_source_qpc=at;
    }
    bool maybe_summary(uint64_t now){
        if(!live() || state.load()!=GD_ACTIVE || !summary_started || now<summary_started
                || now-summary_started<frequency*5)return false;
        emit_diagnostic(GD_NONE,0,true,now);
        // No catch-up burst after a stall. Failure totals and the source interval
        // predecessor survive; only this recent observation window starts anew.
        summary_started=now;memset(phase_recent,0,sizeof phase_recent);cadence_recent={};
        return true;
    }
    struct Phase {
        Runtime&r;uint32_t current,previous;uint64_t start,previous_start;
        Phase(Runtime&owner,uint32_t next):r(owner),current(next),previous(r.phase),
            start(qpc()),previous_start(r.phase_started){r.phase=next;r.phase_started=start;}
        ~Phase(){r.phase_done(current,start,qpc());r.phase=previous;r.phase_started=previous_start;}
    };
    void emit_diagnostic(uint32_t why,uint32_t detail,bool healthy=false,uint64_t observed=0){
        const auto callback=diagnostic_callback.load();
        if(!callback || worker_thread!=GetCurrentThreadId())return;
        gd_diagnostic d{};d.bytes=sizeof d;d.version=GD_DIAGNOSTIC_VERSION;
        d.epoch=epoch;d.reason=why;d.detail=detail;d.current_phase=phase;
        d.qpc_frequency=frequency;d.observed_qpc=observed?observed:qpc();d.phase_started_qpc=phase_started;
        d.healthy=healthy;d.window_started_qpc=healthy?summary_started:started;
        d.previous_emit_ticks=previous_emit_ticks;
        d.accounted_occurrence=accounted;d.published_token=offer_token;
        for(const auto&p:pending){
            d.offer_retirements+=!p.record && p.token!=0;
            if(!p.record)continue;
            ++d.source_records;d.returning_records+=p.returning;
            d.unsampled+=!p.published && !p.omitted;d.awaiting_decisions+=p.published && !p.decided;
            d.awaiting_copy+=p.decided && p.decision.kind==GC_SELECTED && !p.copied;
            if(!d.oldest_admitted_qpc || p.admitted<d.oldest_admitted_qpc)d.oldest_admitted_qpc=p.admitted;
        }
        for(const auto&c:credits){d.bridge_credits+=c.token!=0;d.unpublished_bridges+=c.token && !c.published;}
        memcpy(d.phases,healthy?phase_recent:phase_totals,sizeof phase_totals);
        d.cadence=healthy?cadence_recent:cadence_total;
        d.snapshot_bytes=snapshots.images().counters().texture_bytes;d.bridge_bytes=bridge.logical_bytes();
        d.sample_calls=sample_calls;d.sample_reuses=sample_reuses;d.publish_busy=publish_busy;
        d.refused_pictures=refused_pictures;
        const auto began=qpc();
        try{callback(&d);}catch(...){/* Diagnostics cannot replace the capture failure. */}
        previous_emit_ticks=qpc()-began;
    }
    uint32_t current_epoch() const {
        if(exhausted.load())return 0;
        const auto c=cancel.load();const auto g=gate.load();
        return c==session_cancel.load() && c==cancel.load() ? g : 0;
    }
    bool live() const {return current_epoch()==epoch && epoch!=0;}
    unsigned count() const {unsigned n=0;for(const auto&p:pending)n+=p.record!=nullptr;return n;}
    unsigned occupied() const {unsigned n=0;for(const auto&p:pending)n+=p.record!=nullptr || p.token!=0;return n;}
    unsigned bridge_count() const {unsigned n=0;for(const auto&b:credits)n+=b.token!=0;return n;}
    void set_state(uint32_t next){if(state.exchange(next)!=next)SetEvent(changed);}
    uint32_t diagnostic_reason()const{
        const auto first=first_failure.load();return first?first:reason.load();
    }
    void publish_status(){
        gd_status s{};s.state=state.load();s.reason=diagnostic_reason();s.error=error;s.epoch=epoch;
        s.generation=request.generation;s.producer_pid=GetCurrentProcessId();s.owner_pid=request.owner_pid;
        s.nonce_lo=request.nonce_lo;s.nonce_hi=request.nonce_hi;
        s.snapshot_bytes=snapshots.images().counters().texture_bytes;
        s.bridge_bytes=bridge.logical_bytes();s.sample_bytes=sample?sampler.sample_bytes():0;
        s.mapping_bytes=channel?channel->header().total_bytes:0;
        s.published_occurrence=accounted;s.published_token=offer_token;
        s.pending_records=count();s.pending_bridges=bridge_count();s.quarantined=quarantine;
        auto before=status_seq.load();if(before>=UINT32_MAX-1){
            quarantine=true;exhausted.store(true);revoke(GD_COUNTER_EXHAUSTED);set_state(GD_FAULT);return;
        }
        status_seq.store(before+1);
        uint32_t words[sizeof(s)/4];memcpy(words,&s,sizeof s);
        for(unsigned i=1;i<sizeof(s)/4;++i)status_words[i].store(words[i]);
        status_seq.store(before+2);
    }
    void revoke(uint32_t why){
        gate.store(0);const auto old=cancel.fetch_add(1);
        if(old==UINT32_MAX){exhausted.store(true);cancel.store(UINT32_MAX);}
        reason.store(why);source.disarm();snapshots.stop();SetEvent(wake);SetEvent(changed);
    }
    void fail(uint32_t why,uint32_t detail=0,bool poison=false){
        if(!first_failure.load()){first_failure.store(why);error=detail;emit_diagnostic(why,detail);}
        quarantine|=poison;revoke(why);
    }
    bool channel_ok(ChannelResult result){
        if(result==ChannelResult::ok || result==ChannelResult::empty || result==ChannelResult::busy)return true;
        if(result==ChannelResult::closed){revoke(GD_CANCELLED);return false;}
        fail(result==ChannelResult::owner_gone?GD_OWNER_GONE:GD_CHANNEL_FAILED,uint32_t(result));return false;
    }
    bool request_valid(const gc_header& h,uint64_t bytes)const{
        gc_header clean{};
        clean.owner_pid=h.owner_pid;clean.owner_birth=h.owner_birth;clean.nonce_lo=h.nonce_lo;clean.nonce_hi=h.nonce_hi;
        clean.generation=h.generation;clean.offer_count=h.offer_count;clean.table_count=h.table_count;
        clean.packet_count=h.packet_count;clean.packet_bytes=h.packet_bytes;clean.pending_bytes=h.pending_bytes;clean.ram_budget=h.ram_budget;
        memcpy(clean.table,h.table,sizeof h.table);
        if(memcmp(&clean,&h,sizeof h) || !bytes || bytes>limits.snapshot_bytes || !h.owner_pid || !h.owner_birth
            || !(h.nonce_lo|h.nonce_hi) || !h.generation || !h.offer_count || h.offer_count>limits.max_slots
            || !h.table_count || h.table_count>GC_TABLE_FIELDS || !h.packet_count || h.packet_count>GC_MAX_OFFERS
            || !h.packet_bytes || h.packet_bytes>GC_MAX_PACKET_BYTES || h.pending_bytes<h.packet_bytes
            || uint64_t(h.pending_bytes)>uint64_t(h.packet_count)*h.packet_bytes
            || h.ram_budget<GC_HEADER_BYTES || h.ram_budget>limits.channel_bytes)return false;
        for(unsigned n=0;n<GC_TABLE_FIELDS;++n){const auto&t=h.table[n];
            if(n>=h.table_count){if(t.offset||t.length)return false;}
            else if(!t.length || t.length>128 || (t.length&3) || (t.offset&3) || uint64_t(t.offset)+t.length>(8u<<20))return false;
        }
        return true;
    }
    void take_records(bool stopping){
        // Count bounded by source's eight immutable record slots, never a heap queue.
        if(!stopping)for(const auto&p:pending)if(p.record && !p.published && !p.omitted)return;
        for(unsigned n=0;n<RB_SLOTS && occupied()<request.offer_count;++n){
            const auto*r=source.take();if(!r)break;
            unsigned free=RB_SLOTS;
            for(unsigned i=0;i<request.offer_count;++i){
                if(pending[i].record && pending[i].record->slot==r->slot){fail(GD_BAD_SOURCE,ERROR_INVALID_DATA,true);return;}
                if(!pending[i].record && !pending[i].token && free==RB_SLOTS)free=i;
            }
            if(r->slot>=RB_SLOTS || free==RB_SLOTS){fail(GD_BAD_SOURCE,ERROR_INVALID_DATA,true);return;}
            auto&p=pending[free];p.record=r;p.admitted=qpc();
            if(!stopping)observe_source(*r);
            if(r->image.ownership==RB_IMAGE_QUARANTINED){fail(GD_SNAPSHOT_FAILED,r->image.status,true);return;}
            if(r->image.ownership==RB_IMAGE_NONE){
                // Bootstrap, same-VI-origin and no-swap observations are explicit
                // omissions. A full snapshot pool is a MISSING picture, counted:
                // the renderer never waits for the worker, so under pressure a
                // picture is dropped and the recording continues.
                const bool observed_omission=r->outcome==RB_OBSERVED
                    && (r->image.status==SA_SAME_ORIGIN || r->image.status==SA_NO_SWAP || r->image.status==bootstrap_omission);
                const bool pool_full=r->outcome==RB_SURFACE_FAILED && r->image.status==uint32_t(snapshot::Result::full);
                if(!stopping && state.load()==GD_ACTIVE && r->epoch==epoch && !observed_omission && !pool_full){
                    fail(GD_SOURCE_IMAGE_MISSING,r->image.status);return;
                }
                if(pool_full && state.load()==GD_ACTIVE && r->epoch==epoch)++refused_pictures;
                // release publishes this record as reusable. Retain its identity
                // before the renderer can overwrite the borrowed storage.
                const auto id=r->occurrence;const auto slot=r->slot;
                if(!source.release({id,slot})){fail(GD_BAD_SOURCE,ERROR_INVALID_DATA,true);return;}
                accounted=id;p={};
            }else if(!stopping)break; // later omissions cannot advance past this unsampled head
        }
    }
    bool release_image(Pending&p){
        if(p.returning)return true;
        if(!p.ready){const auto polled=snapshots.images().poll({p.record->image.serial,p.record->image.slot},&p.image);
            if(polled==snapshot::Poll::pending)return false;
            if(polled!=snapshot::Poll::ready){fail(GD_SNAPSHOT_FAILED,uint32_t(polled),true);return false;}
            p.ready=true;
        }
        if(!snapshots.images().release(p.image.ticket)){fail(GD_SNAPSHOT_FAILED,ERROR_INVALID_DATA,true);return false;}
        p.returning=true;return true;
    }
    bool retire_record(Pending&p,bool stopping){
        if(p.record){
            const auto slot=p.record->slot;const auto id=p.record->occurrence;
            if(!source.release({id,slot})){fail(GD_BAD_SOURCE,ERROR_INVALID_DATA,true);return false;}
            // The source is free even if the following channel operation fails.
            // Keep only owned metadata while retrying a busy offer retirement.
            p.record=nullptr;p.image={};
            if(stopping && id>accounted)accounted=id;
        }
        if(!stopping && p.published){
            const auto result=channel->retire_offer(unsigned(&p-pending),p.token);
            if(result==ChannelResult::busy)return true;
            if(!channel_ok(result))return false;
        }
        p={};return true;
    }
    void reap_records(bool stopping){
        snapshots.images().reap();
        if(snapshots.images().counters().faults){fail(GD_SNAPSHOT_FAILED,ERROR_INVALID_DATA,true);return;}
        for(auto&p:pending){
            if(!p.record){if(p.token && !retire_record(p,stopping))return;continue;}
            if(stopping && p.record->image.ownership==RB_IMAGE_NONE){
                const auto id=p.record->occurrence;
                if(!source.release({id,p.record->slot})){fail(GD_BAD_SOURCE,ERROR_INVALID_DATA,true);return;}
                if(id>accounted)accounted=id;p={};continue;
            }
            if((stopping || p.omitted) && !p.returning)release_image(p);
            if(!p.returning || !snapshots.completed(p.record->image))continue;
            if(!retire_record(p,stopping))return;
        }
    }
    bool bootstrap_ready(){
        unsigned ready=2;if(!surface_state.compare_exchange_strong(ready,3))return false;
        surface=mailbox;const auto e=mailbox_epoch;surface_state.store(0);
        if(e!=epoch || !live())return false;
        if(!surface.context || !surface.read_drawable || !surface.context_generation || !surface.drawable_generation
            || surface.width<2 || surface.height<2 || surface.width>3840 || surface.height>2160){fail(GD_BAD_SOURCE);return false;}
        const uint64_t sample_size=uint64_t((surface.width+7)/8)*((surface.height+7)/8)*4;
        const uint64_t snap_size=uint64_t(surface.width)*surface.height*4*request.offer_count;
        const uint64_t bridge_size=uint64_t(surface.width&~1u)*(surface.height&~1u)*4*GC_BRIDGES*2;
        if(sample_size>limits.sample_bytes || snap_size>budget || bridge_size>limits.bridge_bytes){fail(GD_BAD_REQUEST,ERROR_NOT_ENOUGH_MEMORY);return false;}
        // Validate total mapping before preparing any GPU allocation.
        auto config=request;config.seq=2;config.epoch=epoch;config.producer_pid=GetCurrentProcessId();
        config.producer_birth=gpu_channel::process_birth(GetCurrentProcess());config.qpc_frequency=frequency;
        config.width=surface.width;config.height=surface.height;
        // Temporary names only for layout validation; replaced by real bridge identities.
        config.texture_names[0][0]='A';config.texture_names[1][0]='B';
        if(!gpu_channel::layout(config)){fail(GD_BAD_REQUEST,ERROR_NOT_ENOUGH_MEMORY);return false;}
        set_state(GD_PREPARING);publish_status();
        TRACE("create context");
        if(!context.create(surface,contexts)){fail(GD_CONTEXT_UNAVAILABLE,context.error(),context.quarantined());return false;}
#ifdef GD_TEST_HOST
        if(test_preparation_probe)test_preparation_probe();
#endif
        if(!live())return false;
        snapshot::Gl gl;if(!gl.load(false)){fail(GD_GL_UNSUPPORTED);return false;}
        TRACE("prepare snapshots");
        if(!snapshots.prepare(gl,source,surface,request.offer_count,budget)){fail(GD_SNAPSHOT_FAILED,ERROR_INVALID_DATA,true);return false;}
        prepared=true;if(!live()){snapshots.stop();return false;}
        sampler_attempted=true;
        TRACE("prepare sampler");
        if(!sampler.prepare(surface.width,surface.height,limits.sample_bytes)){fail(GD_SAMPLE_FAILED,sampler.error(),true);return false;}
        sample.reset(new(std::nothrow)uint8_t[size_t(sample_size)]);
        if(!sample){fail(GD_SAMPLE_FAILED,ERROR_NOT_ENOUGH_MEMORY);return false;}
        if(!live())return false;
        wchar_t prefix[101];swprintf_s(prefix,L"Local\\SM64GpuImage-%016llx%016llx-%u",request.nonce_hi,request.nonce_lo,epoch);
        bridge_attempted=true;
        TRACE("prepare bridge");
        if(!bridge.prepare(surface.width,surface.height,prefix,limits.bridge_bytes)){fail(GD_BRIDGE_FAILED,bridge.error(),bridge.quarantined());return false;}
        if(!live())return false;
        const auto& actual=bridge.config();config.luid_low=actual.adapter.LowPart;config.luid_high=actual.adapter.HighPart;
        memcpy(config.texture_names,actual.names,sizeof config.texture_names);
        channel.reset(new(std::nothrow)gpu_channel::Producer);
        TRACE("create channel");
        if(!channel){fail(GD_CHANNEL_FAILED,ERROR_NOT_ENOUGH_MEMORY);return false;}
        if(!channel_ok(channel->create(config)))return false;
        channel_started=qpc();
        if(!live() || !context.revalidate(surface,contexts)){fail(GD_CONTEXT_CHANGED,contexts.error());return false;}
        TRACE("prepared");
        publish_status(); SetEvent(changed); // names/mapping ready; capture still metadata-only
        return true;
    }
    void sample_head(){
#ifdef GD_TEST_HOST
        if(test_hold_poll.load())return;
#endif
        Pending*head=nullptr;
        for(auto&p:pending)if(p.record && !p.published && !p.omitted && (!head || p.record->occurrence<head->record->occurrence))head=&p;
        if(!head)return;auto&p=*head;const auto&r=*p.record;
        if(r.epoch!=epoch || r.outcome!=RB_OBSERVED || r.image.ownership!=RB_IMAGE_SUBMITTED){fail(GD_SOURCE_RECORD_INVALID);return;}
        if(r.stamp.table_count==0 && r.stamp.lists_since==0){
            // A VI arrived before any display list of this epoch (activation
            // while paused, then resume): there is no stamp to pair. Drop the
            // picture, return its image, keep recording.
            p.omitted=true;++refused_pictures;accounted=r.occurrence;release_image(p);return;
        }
        if(!p.ready){const auto result=snapshots.images().poll({r.image.serial,r.image.slot},&p.image);
            if(result==snapshot::Poll::pending)return;
            if(result!=snapshot::Poll::ready){fail(GD_SNAPSHOT_FAILED,uint32_t(result),true);return;}p.ready=true;
        }
        if(!live())return;
        // take_records preserves the sole unpublished head. Its shared sample
        // buffer remains owned here until publication succeeds; a busy client
        // page must not repeat GPU draw/readback for this immutable picture.
        if(!p.sampled){
            ++sample_calls;
            const auto result=
#ifdef GD_TEST_HOST
                test_sample?test_sample(p.image,sample.get()):
#endif
                sampler.sample(p.image.texture,surface.width,surface.height,sample.get(),size_t(sampler.sample_bytes()));
            if(result!=gpu_selection::Result::sampled){fail(GD_SAMPLE_FAILED,sampler.error(),true);return;}
            p.sampled=true;
        }else ++sample_reuses;
        if(!live())return;
        gc_offer offer{};offer.token=offer_token+1;offer.occurrence=r.occurrence;
        offer.list_qpc=r.stamp.list_qpc;offer.boundary_qpc=r.surface.boundary_qpc;
        offer.vi_origin=r.stamp.vi_origin;offer.lists_since=r.stamp.lists_since;offer.outcome=r.outcome;
        uint8_t compact[GC_STAMP_BYTES]{};
        if(r.stamp.table_count!=request.table_count){fail(GD_SOURCE_STAMP_COUNT);return;}
        for(unsigned n=0;n<r.stamp.table_count;++n){const auto length=r.stamp.lengths[n];
            if(length>request.table[n].length || length>128 || offer.stamp_bytes+length>GC_STAMP_BYTES){fail(GD_SOURCE_STAMP_LENGTH);return;}
            offer.lengths[n]=length;memcpy(compact+offer.stamp_bytes,r.stamp.bytes+n*128,length);offer.stamp_bytes+=length;
        }
        if(!offer.token){fail(GD_COUNTER_EXHAUSTED);return;}
        const auto result=channel->publish(unsigned(&p-pending),offer,compact,sample.get());
        if(result==ChannelResult::busy){++publish_busy;return;}
        if(!channel_ok(result))return;
        p.token=++offer_token;p.published=true;accounted=r.occurrence;
    }
    bool publish_bridges(){
        for(unsigned n=0;n<GC_BRIDGES;++n){
            unsigned index=GC_BRIDGES;
            for(unsigned i=0;i<GC_BRIDGES;++i)if(credits[i].token && !credits[i].published
                && (index==GC_BRIDGES || credits[i].token<credits[index].token))index=i;
            if(index==GC_BRIDGES)return true;
            auto&credit=credits[index];
            const auto result=channel->publish_bridge(index,credit.token,credit.decision);
            if(result==ChannelResult::busy)return false;
            if(!channel_ok(result))return false;
            credit.published=true;
        }
        return true;
    }
    void decisions(){
        // A GPU copy owns key1 even when metadata publication is temporarily
        // busy. Retry that exact token before copying another selected image.
        if(!publish_bridges())return;
        for(unsigned n=0;n<RB_SLOTS;++n){unsigned slot=0;gc_decision d{};
            const auto result=channel->take_decision(slot,d);
            if(result==ChannelResult::empty || result==ChannelResult::busy)break;
            if(!channel_ok(result))return;
            if(slot>=RB_SLOTS || !pending[slot].record || pending[slot].token!=d.token){fail(GD_CHANNEL_FAILED);return;}
            auto&p=pending[slot];p.decision=d;p.decided=true;
            if(d.kind==GC_FAILED){fail(GD_SOURCE_CLIENT_FAILED,d.reason);return;}
            if(d.kind!=GC_SELECTED)release_image(p);
        }
        Pending*head=nullptr;
        for(auto&p:pending)if(p.record && p.decided && p.decision.kind==GC_SELECTED && !p.copied
                && (!head || p.decision.encode_serial<head->decision.encode_serial))head=&p;
        if(!head || !live())return;
        for(unsigned index=0;index<GC_BRIDGES;++index)if(!credits[index].token){
            gpu_bridge::Result result;
            {Phase timing(*this,GD_PHASE_COPY);result=bridge.copy(index,head->image.texture,surface.width,surface.height);}
            if(result==gpu_bridge::Result::full)continue;
            if(result!=gpu_bridge::Result::copied){fail(GD_BRIDGE_FAILED,bridge.error(),true);return;}
            // Actual key1 exists even if cancellation beats metadata publication.
            credits[index].token=++bridge_token;credits[index].decision=head->decision;
            head->copied=true;
            if(!bridge_token){fail(GD_COUNTER_EXHAUSTED);return;}
            if(!live())return;
            release_image(*head);
            if(live())publish_bridges();return;
        }
    }
    bool retire_bridge(unsigned index,bool stopping){
        auto&credit=credits[index];
        if(!stopping){
            const auto result=channel->retire_bridge_after_key0(index,credit.token);
            if(result==ChannelResult::busy)return true;
            if(!channel_ok(result))return false;
        }
        credit={};return true;
    }
    void bridge_returns(bool stopping){
        if(!stopping){for(unsigned n=0;n<GC_BRIDGES;++n){unsigned slot=0;gc_receipt receipt{};
            const auto result=channel->take_receipt(slot,receipt);
            if(result==ChannelResult::empty || result==ChannelResult::busy)break;
            if(!channel_ok(result))return;
            if(slot>=GC_BRIDGES || credits[slot].token!=receipt.token){fail(GD_CHANNEL_FAILED);return;}
            credits[slot].receipt=true;
        }}
        for(unsigned n=0;n<GC_BRIDGES;++n){auto& c=credits[n];if(!c.token || (!stopping && !c.receipt))continue;
            const auto key=bridge.key0_returned(n,stopping);
            if(key==gpu_bridge::KeyReturn::pending)continue;
            if(key!=gpu_bridge::KeyReturn::ready){fail(GD_BRIDGE_FAILED,bridge.error(),true);return;}
            if(!retire_bridge(n,stopping))return;
        }
    }
    void active_tick(){
        if(last_active_end)phase_done(GD_PHASE_WAIT,last_active_end,qpc());
        {Phase timing(*this,GD_PHASE_TAKE);take_records(false);}if(!live())return;
        {Phase timing(*this,GD_PHASE_SAMPLE);sample_head();}if(!live())return;
        {Phase timing(*this,GD_PHASE_DECISIONS);decisions();}if(!live())return;
        {Phase timing(*this,GD_PHASE_BRIDGE_RETURNS);bridge_returns(false);}if(!live())return;
        {Phase timing(*this,GD_PHASE_REAP);reap_records(false);}if(!live())return;
        Phase timing(*this,GD_PHASE_FRONTIER);
        const auto now=qpc();
        for(const auto&p:pending)if((p.record || p.token) && now-p.admitted>frequency*limits.max_age_ms/1000){fail(GD_DEADLINE);return;}
        sa_frontier frontier{};
        if(sa_probe_frontier(accounted,&frontier)){
            if(frontier.epoch!=epoch){fail(GD_SOURCE_EPOCH_CHANGED,frontier.epoch);return;}
            if(frontier.refusal_generation!=refusal_generation){
                // Each refused stage is one missing picture. Count it and go on;
                // the first refusal's counters are latched for the summary log.
                refused_pictures+=frontier.refusal_generation-refusal_generation;
                refusal_generation=frontier.refusal_generation;
            }
            frontier_qpc=uint64_t(frontier.qpc);frontier_occurrence=frontier.committed_occurrence;
        }
        // Source records/decisions have their own immediate publication event.
        // A held-picture frontier needs at most 60Hz, not a wake per fence poll.
        if(now-status_time>=frequency/60){
            status_time=now;
            if(channel_ok(channel->status(GC_ACTIVE,0,now,frontier_qpc,frontier_occurrence,offer_token)))publish_status();
        }
        last_active_end=qpc();
    }
    bool drain_tick(){
        if(!drain_started){drain_started=qpc();set_state(GD_DRAINING);
            const auto cause=diagnostic_reason();
            if(channel)channel->close(cause ? GC_REASON_RUNTIME|cause : 0); // permanently revoke this unique channel BEFORE key1 reclaim
        }
        if(quarantine || bridge.quarantined() || context.quarantined()){
            quarantine=true;exhausted.store(true);set_state(GD_FAULT);publish_status();return false;
        }
        take_records(true);reap_records(true);if(bridge_attempted)bridge_returns(true);
        if(quarantine){exhausted.store(true);set_state(GD_FAULT);publish_status();return false;}
        sa_frontier quiet{};
        if(!count() && !bridge_count() && sa_probe_quiescent(accounted,&quiet)){
            bool safe=true;
            if(sampler_attempted)safe=sampler.shutdown();
            if(safe && bridge_attempted)safe=bridge.shutdown();
            if(safe && prepared)safe=snapshots.destroy();
            if(safe)safe=context.destroy();
            if(!safe){quarantine=true;exhausted.store(true);reason.store(GD_QUARANTINED);set_state(GD_FAULT);publish_status();return false;}
            channel.reset();sample.reset();prepared=bridge_attempted=sampler_attempted=false;
            surface_state.store(0);epoch=0;drain_started=0;
            set_state(reason.load()==GD_CANCELLED || reason.load()==GD_NONE?GD_OFF:GD_FAULT);
            publish_status();request_state.store(0);SetEvent(changed);
#ifdef GD_TEST_HOST
            if(auto probe=test_retired_probe.load())probe();
#endif
            // Ownership is released. This old iteration must not inspect or
            // mutate fields which a new request may already have replaced.
            return true;
        }
        if(qpc()-drain_started>frequency*limits.max_age_ms/1000){
            quarantine=true;exhausted.store(true);reason.store(GD_QUARANTINED);set_state(GD_FAULT);
        }
        publish_status();return false;
    }
    void run(){
        worker_thread=GetCurrentThreadId();
        for(;;){
#ifdef GD_TEST_HOST
            if(auto probe=test_iteration_probe.load())probe();
#endif
            unsigned waiting=2;
            request_state.compare_exchange_strong(waiting,3);
            if(request_state.load()==3 && !exhausted.load()){
                if(diagnostic_epoch!=epoch){
                    diagnostic_epoch=epoch;reset_metrics();
                }
                if(!live()){if(drain_tick())continue;}
                else if(state.load()==GD_BOOTSTRAP){take_records(false);bootstrap_ready();}
                else if(state.load()==GD_PREPARING){
                    take_records(false);
                    const auto ready_reader=channel?channel->reader_ready():ChannelResult::empty;
                    if(!channel_ok(ready_reader))continue;
                    sa_frontier ready{};
                    if(ready_reader==ChannelResult::ok && sa_probe_frontier(accounted,&ready) && context.revalidate(surface,contexts) && live()){
                        refusal_generation=ready.refusal_generation;
                        sa_arm_refusal(epoch);
                        summary_started=qpc();
                        set_state(GD_ACTIVE);publish_status();
                        TRACE("active");
                    }
                }else if(state.load()==GD_ACTIVE){active_tick();maybe_summary(qpc());}
#ifdef GD_TEST_HOST
                test_deadline_visits.fetch_add(1);
#endif
                if(live() && state.load()!=GD_ACTIVE && qpc()-(channel_started?channel_started:started)>frequency*setup_deadline_ms/1000)fail(GD_DEADLINE);
            }
            HANDLE events[2]={wake,channel?channel->wake_event():nullptr};
            const DWORD idle_poll=limits.poll_ms*4<16?limits.poll_ms*4:16;
            const DWORD timeout=request_state.load()==3 && !exhausted.load()
                ? (state.load()==GD_ACTIVE && !occupied() && !bridge_count()?idle_poll:limits.poll_ms):INFINITE;
            WaitForMultipleObjects(events[1]?2:1,events,FALSE,timeout);
        }
    }
};
std::atomic<Runtime*> runtime{nullptr};
std::atomic<bool> configured{false};
DWORD WINAPI delivery_thread(void* p){static_cast<Runtime*>(p)->run();return 0;}
}

extern "C" int __cdecl gd_configure(const rs_api*s,const rcl_api*c,const gd_limits*l){
    if(!source_valid(s,c) || !limits_valid(l))return GD_INVALID;
    bool no=false;if(!configured.compare_exchange_strong(no,true))return GD_BUSY;
    auto*r=new(std::nothrow)Runtime;if(!r){configured.store(false);return GD_SYSTEM_ERROR;}
    r->source=*s;r->contexts=*c;r->limits=*l;
    LARGE_INTEGER f{};QueryPerformanceFrequency(&f);r->frequency=uint64_t(f.QuadPart);
    r->wake=CreateEventW(nullptr,FALSE,FALSE,nullptr);r->changed=CreateEventW(nullptr,FALSE,FALSE,nullptr);
    if(!r->frequency || !r->wake || !r->changed){if(r->wake)CloseHandle(r->wake);if(r->changed)CloseHandle(r->changed);delete r;configured.store(false);return GD_SYSTEM_ERROR;}
    HANDLE worker=CreateThread(nullptr,0,delivery_thread,r,0,nullptr);
    if(!worker){CloseHandle(r->wake);CloseHandle(r->changed);delete r;configured.store(false);return GD_SYSTEM_ERROR;}
    CloseHandle(worker);runtime.store(r);return GD_ACCEPTED;
}
extern "C" void __cdecl gd_set_diagnostic(gd_diagnostic_callback callback){diagnostic_callback.store(callback);}
extern "C" int __cdecl gd_request(const gc_header*h,uint64_t budget){
    auto*r=runtime.load();if(!r || !h || !r->request_valid(*h,budget))return GD_INVALID;
    if(r->exhausted.load())return GD_EXHAUSTED;
    const auto busy=r->request_state.load();
    if(busy){
        if(busy!=1 && !memcmp(h,&r->request,sizeof(*h)) && budget==r->budget && r->current_epoch())return GD_UNCHANGED;
        if(busy!=1 && r->current_epoch())r->revoke(GD_CANCELLED);
        return GD_BUSY;
    }
    // Never reuse a failed/closed session's names while a stale helper can exist.
    if(h->nonce_lo==r->request.nonce_lo && h->nonce_hi==r->request.nonce_hi)return GD_INVALID;
    unsigned free=0;if(!r->request_state.compare_exchange_strong(free,1))return GD_BUSY;
    const auto cancelled=r->cancel.load();r->gate.store(0);r->source.disarm();r->snapshots.stop();
    const uint32_t e=r->source.activate();
    if(!e || cancelled!=r->cancel.load()){r->source.disarm();r->request_state.store(0);return GD_BUSY;}
    sa_table table{};table.bytes=sizeof(table);table.version=SA_VERSION;table.epoch=e;table.rdram_bytes=8u<<20;table.count=h->table_count;
    for(unsigned n=0;n<table.count;++n)table.rows[n]={h->table[n].offset,h->table[n].length};
    if(!sa_publish_table(&table)){r->source.disarm();r->request_state.store(0);return GD_INVALID;}
    r->request=*h;r->budget=budget;r->epoch=e;r->reason.store(GD_NONE);r->first_failure.store(GD_NONE);r->session_cancel.store(cancelled);
    // request_state=1 exclusively owns the now-empty worker state. Publish the
    // fresh identity and BOOTSTRAP synchronously, before the supervisor can read
    // an old OFF/FAULT status after ACCEPTED. This is bounded CPU metadata only;
    // every graphics allocation/operation remains on the delivery worker.
    r->started=qpc();r->channel_started=r->drain_started=0;r->offer_token=r->bridge_token=0;
    r->frontier_qpc=r->frontier_occurrence=0;r->refusal_generation=0;r->status_time=0;r->error=0;
    r->set_state(GD_BOOTSTRAP);r->publish_status();
    if(r->exhausted.load()){r->request_state.store(2);SetEvent(r->wake);return GD_EXHAUSTED;}
    r->gate.store(e);
    if(cancelled!=r->cancel.load()){r->gate.store(0);r->source.disarm();}
    r->request_state.store(2);SetEvent(r->wake);SetEvent(r->changed);return GD_ACCEPTED;
}
extern "C" void __cdecl gd_disarm(uint32_t why){auto*r=runtime.load();if(r)r->revoke(why?why:GD_CANCELLED);}
extern "C" uint32_t __cdecl gd_epoch(){const auto*r=runtime.load();return r?r->current_epoch():0;}
extern "C" void __cdecl gd_surface(const rb_record*r){
    auto*self=runtime.load();if(!self || !r || r->epoch!=self->current_epoch() || self->state.load()!=GD_BOOTSTRAP)return;
    unsigned free=0;if(!self->surface_state.compare_exchange_strong(free,1))return;
    self->mailbox=r->surface;self->mailbox_epoch=r->epoch;self->surface_state.store(2);
}
extern "C" int __cdecl gd_capture(const rb_record*r,rb_image*out){
    auto*self=runtime.load();if(out)*out={};
    if(!self || !r || !out || !self->current_epoch() || r->epoch!=self->current_epoch())return 0;
    const auto state=self->state.load();
    if(state!=GD_ACTIVE){
        // A deliberate metadata observation is successful even if ACTIVE is
        // published before the completed source record reaches the worker.
        if(state==GD_BOOTSTRAP || state==GD_PREPARING){out->status=bootstrap_omission;return 1;}
        return 0;
    }
    return self->snapshots.submit(r,out);
}
extern "C" int __cdecl gd_completed(const rb_image*i){const auto*r=runtime.load();return r && i && r->snapshots.completed(*i);}
extern "C" HANDLE __cdecl gd_signal(){const auto*r=runtime.load();return r?r->changed:nullptr;}
extern "C" int __cdecl gd_read_status(gd_status*out){
    const auto*r=runtime.load();if(!r || !out)return 0;
    for(unsigned n=0;n<3;++n){const auto before=r->status_seq.load();if(before&1)continue;
        uint32_t words[sizeof(*out)/4]{};words[0]=before;
        for(unsigned i=1;i<sizeof(*out)/4;++i)words[i]=r->status_words[i].load();
        if(before==r->status_seq.load()){memcpy(out,words,sizeof(*out));out->state=r->state.load();out->reason=r->diagnostic_reason();return 1;}
    }
    return 0;
}
#ifdef GD_TEST_HOST
#include "gpu_delivery_retry_test.h"
// No configured runtime, renderer, window, GPU, sleep or worker. Source records
// are recycled synchronously on release to prove cadence reads occur while lent.
extern "C" int gd_test_healthy_snapshot(gd_diagnostic*out){
    if(!out)return 0;
    static gd_diagnostic*destination=nullptr;static unsigned calls=0,index=0;
    static rb_record records[3];destination=out;calls=index=0;
    Runtime r;r.worker_thread=GetCurrentThreadId();r.epoch=17;r.gate.store(17);
    r.session_cancel.store(r.cancel.load());r.frequency=1000;r.source.disarm=[](){};
    r.state.store(GD_ACTIVE);r.summary_started=1000;
    const int64_t boundaries[]={1000,1016,1066};
    for(unsigned n=0;n<3;++n){auto&v=records[n];v={};v.epoch=17;v.occurrence=n+1;v.slot=n;
        v.outcome=RB_OBSERVED;v.surface.boundary_qpc=boundaries[n];v.image.status=SA_SAME_ORIGIN;}
    records[2].image={1,2,17,RB_IMAGE_SUBMITTED,0};
    r.request.offer_count=3;
    r.source.take=[]()->const rb_record*{return index<3?&records[index++]:nullptr;};
    r.source.release=[](rb_ticket t){records[t.slot].surface.boundary_qpc=999999;return 1;};
    r.take_records(false);r.phase_done(GD_PHASE_SAMPLE,100,150);
    gd_set_diagnostic([](const gd_diagnostic*d){if(calls<3)destination[calls]=*d;++calls;throw 1;});
    bool valid=!r.maybe_summary(5999) && r.maybe_summary(6000) && calls==1 && r.live();
    rb_record next=records[2];next.surface.boundary_qpc=1080;r.observe_source(next);
    r.phase_done(GD_PHASE_SAMPLE,7000,7003);
    valid=valid && r.maybe_summary(16000) && !r.maybe_summary(16001) && calls==2 && r.live();
    r.fail(GD_SOURCE_REFUSED,7);
    valid=valid && calls==3 && !r.maybe_summary(21000);
    r.reset_metrics();
    valid=valid && r.phase_totals[GD_PHASE_SAMPLE].calls==0 && !r.cadence_total.records
        && !r.last_source_qpc && !r.summary_started;
    gd_set_diagnostic(nullptr);destination=nullptr;
    return valid;
}
// CPU-only custody witness: callbacks may immediately recycle a released record.
extern "C" int gd_test_take_omissions(const rs_api*source,uint64_t*accounted){
    if(!source || !accounted)return 0;
    Runtime r;r.source=*source;r.epoch=1;r.state.store(GD_ACTIVE);r.request.offer_count=1;
    r.take_records(false);*accounted=r.accounted;
    return !r.count() && !r.first_failure.load();
}
// CPU-only: a full snapshot pool and refused stages are counted omissions,
// never a run failure. `refused` returns the count after both.
extern "C" int gd_test_pressure_omissions(const rs_api*source,uint64_t*accounted,uint64_t*refused){
    if(!source || !accounted || !refused)return 0;
    Runtime r;r.source=*source;r.epoch=1;r.state.store(GD_ACTIVE);r.request.offer_count=1;
    r.take_records(false);*accounted=r.accounted;
    if(r.count() || r.first_failure.load())return 0;
    sa_frontier frontier{};frontier.epoch=1;frontier.refusal_generation=3;
    if(frontier.refusal_generation!=r.refusal_generation){
        r.refused_pictures+=frontier.refusal_generation-r.refusal_generation;
        r.refusal_generation=frontier.refusal_generation;
    }
    *refused=r.refused_pictures;
    return !r.first_failure.load() && r.refusal_generation==3;
}
extern "C" int gd_test_retire_record(const rs_api*source,uint32_t*pending){
    if(!source || !pending)return 0;
    Runtime r;r.source=*source;r.channel.reset(new gpu_channel::Producer);
    auto&p=r.pending[0];p.record=r.source.take();p.published=p.returning=true;p.token=1;
    if(!p.record)return 0;
    const bool retired=r.retire_record(p,false);*pending=r.count();
    return !retired && r.reason.load()==GD_CANCELLED;
}
// CPU-only causal witness. Exercise real fail/disarm/status and accepted-request
// reset without configuring a worker, opening a GL context or creating images.
extern "C" int gd_test_terminal_diagnostics(gd_status*out,uint32_t*revoked){
    if(!out || !revoked || runtime.load())return 0;
    Runtime r;r.source.disarm=[](){};r.source.activate=[]()->uint32_t{return 1;};
    r.limits.max_slots=1;r.limits.snapshot_bytes=4u<<20;r.limits.channel_bytes=1u<<20;
    r.wake=CreateEventW(nullptr,FALSE,FALSE,nullptr);r.changed=CreateEventW(nullptr,FALSE,FALSE,nullptr);
    if(!r.wake || !r.changed){if(r.wake)CloseHandle(r.wake);if(r.changed)CloseHandle(r.changed);return 0;}
    runtime.store(&r);
    r.fail(GD_SOURCE_GAP,17);r.fail(GD_DEADLINE,18);gd_disarm(GD_OWNER_GONE);
    *revoked=r.reason.load();r.publish_status();int valid=gd_read_status(&out[0]);
    gc_header h{};h.owner_pid=GetCurrentProcessId();h.owner_birth=gpu_channel::process_birth(GetCurrentProcess());
    h.nonce_lo=1;h.generation=1;h.offer_count=h.table_count=h.packet_count=1;h.table[0]={0,4};
    h.packet_bytes=h.pending_bytes=1024;h.ram_budget=1u<<20;
    valid=valid && gd_request(&h,4u<<20)==GD_ACCEPTED;
    gd_disarm(GD_CANCELLED);r.publish_status();valid=valid && gd_read_status(&out[1]);
    runtime.store(nullptr);CloseHandle(r.wake);CloseHandle(r.changed);return valid;
}
extern "C" void gd_test_hold_poll(int hold){test_hold_poll.store(hold!=0);}
extern "C" void gd_test_set_preparation_probe(void (__cdecl *probe)()){test_preparation_probe=probe;}
extern "C" void gd_test_set_iteration_probe(void (__cdecl *probe)()){
    test_iteration_probe.store(probe);auto*r=runtime.load();if(r)SetEvent(r->wake);
}
extern "C" void gd_test_set_retired_probe(void (__cdecl *probe)()){test_retired_probe.store(probe);}
extern "C" uint32_t gd_test_deadline_visits(){return test_deadline_visits.load();}
#endif
