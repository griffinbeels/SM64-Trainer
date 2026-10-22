#include "gpu_delivery.h"
#include "gpu_delivery_diagnostics.h"
#include "gpu_delivery_metrics.h"
#include "source_snapshot.h"
#include "gpu_render.h"
#include "gpu_startup.h"
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
std::atomic<uint32_t> test_bridge_fault{0}; // the next bridge copy faults with this error
std::atomic<uint32_t> test_twin_bridge_fault{0}; // the next twin copy faults
bool (*test_sample)(const snapshot::Image&,uint8_t*)=nullptr; // replaces the GPU sample: true = bytes ready
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
// What gd_capture did for the ghost copy: a display snapshot of its own, the
// clean picture stands for it (no ghost copied that update), or nothing.
enum TwinKind : uint32_t { twin_none=0, twin_display=1, twin_same=2, twin_missing=3 };
struct TwinCapture { uint64_t occurrence=0,lane=0; uint32_t kind=twin_none; rb_image image{}; };
struct Pending {
    const rb_record* record=nullptr;
    snapshot::Image image{};
    gc_decision decision{};
    uint64_t token=0,admitted=0;
    bool ready=false,sampled=false,published=false,decided=false,returning=false,copied=false;
    bool omitted=false; // worker dropped it (no usable stamp); image returns, nothing publishes
    uint32_t twin_kind=twin_none;uint64_t twin_lane=0;rb_image twin_image{}; // returned with the clean image
};
struct BridgeCredit {
    uint64_t token=0;bool receipt=false,published=false;
    gc_decision decision{};
};

struct Runtime {
    rs_api source{};rs_api_v3 source3{};rs_api_v4 source4{}; rcl_api contexts{}; gd_limits limits{};
    HANDLE wake=nullptr,changed=nullptr;
    std::atomic<uint32_t> gate{0},cancel{1},session_cancel{0},state{GD_OFF},reason{GD_NONE};
    // Publish reason/detail together before callbacks or cancellation. A reader
    // must never classify a new refusal with a previous snapshot's detail.
    // Low32=reason, high32=detail; later disarms preserve the first failure.
    std::atomic<uint64_t> first_failure{0};
    std::atomic<uint32_t> request_state{0},surface_state{0}; // 0 free,1 writing,2 ready,3 owned
    std::atomic<uint32_t> status_seq{0},status_words[sizeof(gd_status)/4]{};
    std::atomic<uint32_t> startup_seq{0},startup_words[sizeof(gpu_startup_v1)/4]{};
    // Request admission initializes CPU identity while request_state=1 owns
    // the empty runtime. Subsequent writes belong to the renderer thread.
    gpu_startup_v1 startup{};
    std::atomic<bool> exhausted{false};
    gc_header request{};uint64_t budget=0;uint32_t epoch=0;
    rb_surface mailbox{},surface{};uint32_t mailbox_epoch=0;
    uint64_t mailbox_qpc=0; // the renderer's surface handoff (gd_surface)
    gd_prepare_diagnostic prepare_steps{};
    // RENDERER-THREAD CAPTURE (docs/replay-gpu-runtime.md). Every GPU call runs
    // on the renderer's own thread in its own context, from its capture
    // callbacks; the delivery thread never touches GL or D3D. A second thread
    // using the GPU made the NVIDIA driver freeze the renderer once per process.
    // The renderer thread takes this lock only if it is free (it never waits);
    // the delivery thread takes it to drain and to check deadlines.
    SRWLOCK tick_lock=SRWLOCK_INIT;
    render_capture::Gl gl;snapshot::Gl copy_gl;
    source_capture::Snapshots snapshots;
    render_capture::Sampler sampler;
    render_capture::Bridge bridge;
    std::atomic<uint32_t> sample_fault{0}; // set by gd_capture, reported by the next tick
    wchar_t texture_names[GC_BRIDGES][128]{};
    // GL names from an ended epoch, deleted by the renderer before it prepares
    // again, or forgotten if their context has since been replaced.
    bool gpu_retired=false;uintptr_t gpu_context=0;uint32_t gpu_context_generation=0;
    bool in_render=false,gateway_open=false; // render_tick only
    uint64_t last_render_tick=0;
    std::unique_ptr<gpu_channel::Producer> channel;
    std::unique_ptr<uint8_t[]> sample;
    Pending pending[RB_SLOTS]{};BridgeCredit credits[GC_BRIDGES]{};
    uint64_t frequency=0,started=0,channel_started=0,drain_started=0,accounted=0,taken_through=0,offer_token=0,bridge_token=0;
    uint64_t status_time=0,frontier_qpc=0,frontier_occurrence=0;
    uint32_t status_records=0,status_bridges=0; // the counts the status last carried
    uint32_t refusal_generation=0;
    bool prepared=false,bridge_attempted=false,sampler_attempted=false,quarantine=false;
    DWORD worker_thread=0; // the delivery thread (drain, deadlines); ticks also run on the renderer's
    uint32_t diagnostic_epoch=0,phase=GD_PHASE_WAIT;
    uint64_t phase_started=0,last_active_end=0;
    gd_phase_diagnostic phase_totals[GD_PHASE_COUNT]{};
    gd_phase_diagnostic phase_recent[GD_PHASE_COUNT]{};
    gd_cadence_diagnostic cadence_total{},cadence_recent{};
    uint64_t summary_started=0,last_source_qpc=0,previous_emit_ticks=0;
    uint64_t sample_calls=0,sample_reuses=0,publish_busy=0;
    // Per healthy window: the most snapshot-pool records/offers and bridge
    // slots held at once, and the pressure counters at the last summary.
    uint32_t peak_pending=0,peak_bridges=0;
    uint64_t summary_refused=0,summary_busy=0;
    // Pictures the source or the snapshot pool could not admit while ACTIVE,
    // plus records dropped for a missing stamp. Counted, logged in the summary,
    // never fatal: the replay shows the previous picture held and the input
    // lanes show the polled fill for those frames.
    uint64_t refused_pictures=0;
    // THE GHOST COPY (twin lane; docs/replay-gpu-runtime.md "The ghost copy").
    // A second helper records the displayed picture beside the clean one. The
    // renderer thread owns all of it (render_tick, gd_capture), and nothing
    // here calls fail() or sets quarantine: the twin never delays or refuses a
    // clean picture, and a twin fault stops only the twin.
    source_capture::Snapshots twin_images;
    render_capture::Bridge twin_bridge;
    wchar_t twin_names[GC_BRIDGES][128]{};
    TwinCapture twin_capture[RB_SLOTS]{}; // gd_capture -> take_records, by record slot
    BridgeCredit twin_credits[GC_BRIDGES]{};uint64_t twin_credit_qpc[GC_BRIDGES]{};
    std::atomic<uint64_t> twin_capturing{0}; // the lane gd_capture copies for; 0 = none
    std::atomic<uint32_t> twin_capture_fault{0}; // a twin snapshot fault, handled by the next tick
    uint32_t twin_state=GC_TWIN_OFF,twin_reason=GD_NONE,twin_poison=GD_NONE;
    uint64_t twin_lane=0,twin_started_lane=0,twin_resolved=0,twin_copied=0,twin_missed=0;
    uint64_t twin_stop_qpc=0,twin_bridge_token=0;
    // twin_gpu: this epoch made twin GL names. twin_poisoned: a twin key is
    // held or broken, so those names wait for the epoch's GL retirement and
    // no new lane starts in this epoch.
    bool twin_stopping=false,twin_gpu=false,twin_poisoned=false,twin_retired=false;
    void reset_metrics(){
        phase=GD_PHASE_WAIT;phase_started=last_active_end=0;
        memset(phase_totals,0,sizeof phase_totals);memset(phase_recent,0,sizeof phase_recent);
        cadence_total={};cadence_recent={};summary_started=last_source_qpc=previous_emit_ticks=0;
        sample_calls=sample_reuses=publish_busy=0;
        peak_pending=peak_bridges=0;summary_refused=summary_busy=0;
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
    // Worth a line within five seconds: pictures refused, a busy client page,
    // a source interval of 75 ms or more, or a snapshot pool with every slot held.
    bool summary_attention() const {
        return refused_pictures!=summary_refused || publish_busy!=summary_busy
            || cadence_recent.buckets[4] || cadence_recent.buckets[5]
            || (request.offer_count && peak_pending>=request.offer_count);
    }
    bool maybe_summary(uint64_t now){
        // Healthy play logs once a minute (about 0.15 MB/h); a window that needs
        // attention closes at the next five-second boundary instead.
        if(!live() || state.load()!=GD_ACTIVE || !summary_started || now<summary_started
                || now-summary_started<frequency*5)return false;
        if(now-summary_started<frequency*60 && !summary_attention())return false;
        emit_diagnostic(GD_NONE,0,true,now);
        // No catch-up burst after a stall. Failure totals and the source interval
        // predecessor survive; only this recent observation window starts anew.
        summary_started=now;memset(phase_recent,0,sizeof phase_recent);cadence_recent={};
        peak_pending=peak_bridges=0;summary_refused=refused_pictures;summary_busy=publish_busy;
        return true;
    }
    struct Phase {
        Runtime&r;uint32_t current,previous;uint64_t start,previous_start;
        Phase(Runtime&owner,uint32_t next):r(owner),current(next),previous(r.phase),
            start(qpc()),previous_start(r.phase_started){r.phase=next;r.phase_started=start;}
        ~Phase(){r.phase_done(current,start,qpc());r.phase=previous;r.phase_started=previous_start;}
    };
    void emit_diagnostic(uint32_t why,uint32_t detail,bool healthy=false,uint64_t observed=0,uint32_t kind=GD_KIND_WINDOW){
        const auto callback=diagnostic_callback.load();
        if(!callback)return; // callers hold tick_lock: one emitter at a time
        gd_diagnostic d{};d.bytes=sizeof d;d.version=GD_DIAGNOSTIC_VERSION;d.kind=kind;
        d.prepare=prepare_steps;d.offer_count=request.offer_count;
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
        d.snapshot_bytes=snapshots.images().counters().texture_bytes;d.bridge_bytes=bridge_bytes();
        d.sample_calls=sample_calls;d.sample_reuses=sample_reuses;d.publish_busy=publish_busy;
        d.refused_pictures=refused_pictures;d.peak_pending=peak_pending;d.peak_bridges=peak_bridges;
        const auto began=qpc();
        try{callback(&d);}catch(...){/* Diagnostics cannot replace the capture failure. */}
        previous_emit_ticks=qpc()-began;
    }
    // The helper owns the two textures; these are the bytes the import maps.
    uint64_t bridge_bytes() const {
        return bridge.prepared()?uint64_t(surface.width&~1u)*(surface.height&~1u)*4*GC_BRIDGES:0;
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
        const auto first=uint32_t(first_failure.load());return first?first:reason.load();
    }
    void publish_startup(){
        const auto seq=startup_seq.load();startup_seq.store(seq+1);
        const auto*words=reinterpret_cast<const uint32_t*>(&startup);
        for(unsigned i=0;i<sizeof(startup)/4;++i)startup_words[i].store(words[i]);
        startup_seq.store(seq+2);
    }
    void publish_status(){
        const auto failure=first_failure.load();
        gd_status s{};s.state=state.load();s.reason=failure?uint32_t(failure):reason.load();
        s.error=uint32_t(failure>>32);s.epoch=epoch;
        s.generation=request.generation;s.producer_pid=GetCurrentProcessId();s.owner_pid=request.owner_pid;
        s.nonce_lo=request.nonce_lo;s.nonce_hi=request.nonce_hi;
        s.snapshot_bytes=snapshots.images().counters().texture_bytes;
        s.bridge_bytes=bridge_bytes();s.sample_bytes=sample?sampler.sample_bytes():0;
        s.mapping_bytes=channel?channel->header().total_bytes:0;
        s.published_occurrence=accounted;s.published_token=offer_token;
        s.pending_records=count();s.pending_bridges=bridge_count();s.quarantined=quarantine;
        status_records=s.pending_records;status_bridges=s.pending_bridges;
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
        reason.store(why);source.disarm();snapshots.stop();
        twin_capturing.store(0);twin_images.stop();twin_consumer(false);
        SetEvent(wake);SetEvent(changed);
    }
    void fail(uint32_t why,uint32_t detail=0,bool poison=false){
        uint64_t empty=0;
        if(first_failure.compare_exchange_strong(empty,(uint64_t(detail)<<32)|why))emit_diagnostic(why,detail);
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
    // Everything at or below this occurrence is published, omitted or released:
    // the lowest unpublished picture caps what was taken behind it.
    uint64_t resolved_through() const {
        uint64_t through=taken_through;
        for(const auto&p:pending)if(p.record && !p.published && !p.omitted && p.record->occurrence<=through)through=p.record->occurrence-1;
        return through>accounted?through:accounted;
    }
    void take_records(bool stopping){
        // Count bounded by source's eight immutable record slots, never a heap queue.
        // Omissions behind an unpublished head are released at once: the
        // renderer calls back only on a VI whose record it could stage, and
        // renderer-thread capture needs that call to sample the head.
        for(unsigned n=0;n<RB_SLOTS && occupied()<request.offer_count;++n){
            const auto*r=source.take();if(!r)break;
            unsigned free=RB_SLOTS;
            for(unsigned i=0;i<request.offer_count;++i){
                if(pending[i].record && pending[i].record->slot==r->slot){fail(GD_BAD_SOURCE,ERROR_INVALID_DATA,true);return;}
                if(!pending[i].record && !pending[i].token && free==RB_SLOTS)free=i;
            }
            if(r->slot>=RB_SLOTS || free==RB_SLOTS){fail(GD_BAD_SOURCE,ERROR_INVALID_DATA,true);return;}
            auto&p=pending[free];p.record=r;p.admitted=qpc();twin_adopt(p);
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
                if(!stopping && live() && state.load()==GD_ACTIVE && r->epoch==epoch && !observed_omission && !pool_full){
                    fail(GD_SOURCE_IMAGE_MISSING,r->image.status);return;
                }
                if(pool_full && state.load()==GD_ACTIVE && r->epoch==epoch)++refused_pictures;
                // release publishes this record as reusable. Retain its identity
                // before the renderer can overwrite the borrowed storage.
                const auto id=r->occurrence;const auto slot=r->slot;
                if(!source.release({id,slot})){fail(GD_BAD_SOURCE,ERROR_INVALID_DATA,true);return;}
                p={};if(id>taken_through)taken_through=id;
            }else if(r->occurrence>taken_through)taken_through=r->occurrence;
        }
        accounted=resolved_through();
    }
    bool release_image(Pending&p){
        if(p.returning)return true;
        if(!p.ready){const auto polled=snapshots.images().poll({p.record->image.serial,p.record->image.slot},&p.image);
            if(polled==snapshot::Poll::pending)return false;
            if(polled!=snapshot::Poll::ready){fail(GD_SNAPSHOT_FAILED,uint32_t(polled),true);return false;}
            p.ready=true;
        }
        if(!snapshots.images().release(p.image.ticket)){fail(GD_SNAPSHOT_FAILED,ERROR_INVALID_DATA,true);return false;}
        twin_release(p);p.returning=true;return true;
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
    // Opens the renderer's capture gateway at a tick's first GL step: under
    // threaded optimization each error query waits for the driver's queue, so
    // a VI with no GPU work never makes one.
    bool gpu(){
        if(gateway_open)return true;
        if(!in_render || !source.begin_capture())return false;
        gateway_open=true;return true;
    }
    bool bootstrap_ready(){
        if(surface_state.load()!=2)return false;
        // Preserve a source callback even if its GL gateway refuses capture.
        // Zero dimensions distinguish an accepted request with no surface;
        // GL strings remain empty until the gateway actually opens.
        if(startup.width!=mailbox.width || startup.height!=mailbox.height){
            startup.width=mailbox.width;startup.height=mailbox.height;publish_startup();
        }
        if(!gpu())return false;
        unsigned ready=2;if(!surface_state.compare_exchange_strong(ready,3))return false;
        surface=mailbox;const auto e=mailbox_epoch;prepare_steps.surface_qpc=mailbox_qpc;surface_state.store(0);
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
        config.twin_texture_names[0][0]='C';config.twin_texture_names[1][0]='D';
        if(!gpu_channel::layout(config)){fail(GD_BAD_REQUEST,ERROR_NOT_ENOUGH_MEMORY);return false;}
        set_state(GD_PREPARING);publish_status();
#ifdef GD_TEST_HOST
        if(test_preparation_probe)test_preparation_probe();
#endif
        if(!live())return false;
        // The renderer's own context is current: this is its thread (render_tick).
        prepare_steps.prepare_begin=qpc();
        const auto text=[](char*target,size_t capacity,const GLubyte*source){
            if(!source)return;size_t n=0;while(n+1<capacity&&source[n]){target[n]=char(source[n]);++n;}target[n]=0;};
        text(startup.vendor,sizeof startup.vendor,glGetString(GL_VENDOR));
        text(startup.renderer,sizeof startup.renderer,glGetString(GL_RENDERER));
        text(startup.gl_version,sizeof startup.gl_version,glGetString(GL_VERSION));
        publish_startup(); // Preserve identity even if a later driver capability query stalls.
        if(!gl.load()){startup.load_error=uint32_t(gl.load_error);
            text(startup.missing_entry_point,sizeof startup.missing_entry_point,reinterpret_cast<const GLubyte*>(gl.missing_entry_point));
            publish_startup();fail(GD_GL_UNSUPPORTED,uint32_t(gl.load_error));return false;}
        // Renderer-thread Pool::prepare already requires copy_texture; do not
        // change the separate-context pool's existing safe non-DSA fallback.
        if(!copy_gl.load(true)||!copy_gl.copy_texture){startup.load_error=uint32_t(render_capture::LoadError::snapshot_entry_point);
            text(startup.missing_entry_point,sizeof startup.missing_entry_point,reinterpret_cast<const GLubyte*>(copy_gl.missing_entry_point));
            publish_startup();fail(GD_GL_UNSUPPORTED,startup.load_error);return false;}
        publish_startup();
        TRACE("prepare snapshots");
        gpu_context=surface.context;gpu_context_generation=surface.context_generation;
        // At most RB_SLOTS-1 pictures are held, so one record slot always
        // cycles and every VI still reaches this callback (renderer-thread
        // capture does its GPU work there).
        const unsigned images=request.offer_count<RB_SLOTS?request.offer_count:RB_SLOTS-1;
        if(!snapshots.prepare(copy_gl,source,surface,images,budget,source3.query?&source3:nullptr)){fail(GD_SNAPSHOT_FAILED,ERROR_INVALID_DATA,true);return false;}
        prepare_steps.snapshots=qpc();
        prepared=true;if(!live()){snapshots.stop();return false;}
        sampler_attempted=true;
        TRACE("prepare sampler");
        if(!sampler.prepare(gl,surface.width,surface.height,request.offer_count,limits.sample_bytes)){fail(GD_SAMPLE_FAILED,sampler.error(),true);return false;}
        prepare_steps.sampler=qpc();
        sample.reset(new(std::nothrow)uint8_t[size_t(sample_size)]);
        if(!sample){fail(GD_SAMPLE_FAILED,ERROR_NOT_ENOUGH_MEMORY);return false;}
        if(!live())return false;
        LUID adapter{};
        if(!render_capture::adapter_luid(gl,&adapter)){fail(GD_BRIDGE_FAILED,ERROR_NOT_SUPPORTED);return false;}
        prepare_steps.luid=qpc();
        config.luid_low=adapter.LowPart;config.luid_high=adapter.HighPart;
        startup.luid_available=1;startup.luid_low=adapter.LowPart;startup.luid_high=adapter.HighPart;publish_startup();
        // The helper creates these two keyed-mutex textures; PREPARING imports them.
        for(unsigned i=0;i<GC_BRIDGES;++i){
            swprintf_s(texture_names[i],L"Local\\SM64GpuImage-%016llx%016llx-%u-texture-%u",request.nonce_hi,request.nonce_lo,epoch,i);
            for(unsigned c=0;c<128;++c)config.texture_names[i][c]=uint16_t(texture_names[i][c]);
            // The ghost copy's helper creates these two when its lane starts.
            swprintf_s(twin_names[i],L"Local\\SM64GpuImage-%016llx%016llx-%u-copy-%u",request.nonce_hi,request.nonce_lo,epoch,i);
            for(unsigned c=0;c<128;++c)config.twin_texture_names[i][c]=uint16_t(twin_names[i][c]);
        }
        channel.reset(new(std::nothrow)gpu_channel::Producer);
        TRACE("create channel");
        if(!channel){fail(GD_CHANNEL_FAILED,ERROR_NOT_ENOUGH_MEMORY);return false;}
        if(!channel_ok(channel->create(config)))return false;
        channel_started=qpc();prepare_steps.channel=channel_started;
        TRACE("prepared");
        publish_status(); SetEvent(changed); // names/mapping ready; capture still metadata-only
        return true;
    }
    // Renderer: import the helper's textures once its reader is ready (the
    // helper created them when it opened).
    void preparing_tick(){
        if(bridge_attempted)return;
        const auto ready_reader=channel?channel->reader_ready():ChannelResult::empty;
        if(!channel_ok(ready_reader) || ready_reader!=ChannelResult::ok || !gpu())return;
        bridge_attempted=true;prepare_steps.reader_qpc=qpc();prepare_steps.import_begin=qpc();
        if(!bridge.prepare(gl,surface.width,surface.height,texture_names)){fail(GD_BRIDGE_FAILED,bridge.error(),false);return;}
        prepare_steps.imported=qpc();
        SetEvent(wake);
    }
    // Delivery thread: ACTIVE at the first frontier read after the import.
    void activate_when_imported(){
        if(!bridge.prepared())return;
        sa_frontier ready{};
        if(sa_probe_frontier(accounted,&ready) && live()){
            refusal_generation=ready.refusal_generation;
            sa_arm_refusal(epoch);
            summary_started=qpc();
            set_state(GD_ACTIVE);publish_status();
            prepare_steps.end_qpc=qpc();emit_diagnostic(GD_NONE,0,false,0,GD_KIND_PREPARE);
            TRACE("active");
        }
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
            p.omitted=true;++refused_pictures;accounted=resolved_through();release_image(p);return;
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
            // gd_capture issued this picture's sample with its snapshot copy;
            // its readback completes asynchronously. Never wait for it here.
#ifdef GD_TEST_HOST
            if(test_sample){if(!test_sample(p.image,sample.get()))return;}
            else
#endif
            {
                if(!gpu())return;
                const auto taken=sampler.take(p.image.ticket.slot,sample.get(),size_t(sampler.sample_bytes()));
                if(taken==render_capture::Sampler::Poll::pending)return;
                if(taken!=render_capture::Sampler::Poll::ready){fail(GD_SAMPLE_FAILED,sampler.error(),true);return;}
            }
            ++sample_calls;p.sampled=true;
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
        p.token=++offer_token;p.published=true;accounted=resolved_through();
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
        if(!head || !live() || !gpu())return;
        for(unsigned index=0;index<GC_BRIDGES;++index)if(!credits[index].token){
#ifdef GD_TEST_HOST
            if(const auto injected=test_bridge_fault.exchange(0)){fail(GD_BRIDGE_FAILED,injected,true);return;}
#endif
            render_capture::Bridge::Result result;int twin=twin_skip;
            {Phase timing(*this,GD_PHASE_COPY);render_capture::StateGuard guard(gl);result=bridge.copy(index,head->image.texture);
                // The ghost copy of this exact picture, only once the clean copy
                // succeeded (so no GL error is pending), before its image returns.
                if(result==render_capture::Bridge::Result::copied && live())twin=twin_copy(*head);}
            if(result==render_capture::Bridge::Result::full)continue;
            if(result!=render_capture::Bridge::Result::copied){fail(GD_BRIDGE_FAILED,bridge.error(),true);return;}
            // Actual key1 exists even if cancellation beats metadata publication.
            credits[index].token=++bridge_token;credits[index].decision=head->decision;
            head->copied=true;
            if(!bridge_token){fail(GD_COUNTER_EXHAUSTED);return;}
            twin_outcome(*head,twin);
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
            // A stopping epoch abandons its keys: the helper's textures end with
            // its session, and this side's imports are deleted by the renderer.
            if(!stopping){
                if(!gpu())return;
                const auto key=bridge.key0_returned(n);
                if(key==render_capture::Bridge::KeyReturn::pending)continue;
                if(key!=render_capture::Bridge::KeyReturn::ready){fail(GD_BRIDGE_FAILED,bridge.error(),true);return;}
            }
            if(!retire_bridge(n,stopping))return;
        }
    }
    void active_tick(){
        if(last_active_end)phase_done(GD_PHASE_WAIT,last_active_end,qpc());
        {Phase timing(*this,GD_PHASE_TAKE);take_records(false);}if(!live())return;
        {const auto held=occupied();if(held>peak_pending)peak_pending=held;}
        {Phase timing(*this,GD_PHASE_SAMPLE);sample_head();}if(!live())return;
        {Phase timing(*this,GD_PHASE_DECISIONS);decisions();}if(!live())return;
        {const auto held=bridge_count();if(held>peak_bridges)peak_bridges=held;}
        {Phase timing(*this,GD_PHASE_BRIDGE_RETURNS);bridge_returns(false);if(live())twin_tick();}if(!live())return;
        {Phase timing(*this,GD_PHASE_REAP);reap_records(false);}if(!live())return;
        const auto now=qpc();
        for(const auto&p:pending)if((p.record || p.token) && now>p.admitted && now-p.admitted>frequency*limits.max_age_ms/1000){fail(GD_DEADLINE);return;}
        // The frontier's status is published between VIs; the counts go out
        // the VI they change.
        if(count()!=status_records || bridge_count()!=status_bridges)publish_status();
        last_active_end=qpc();
    }
    // The source's update transaction is open for the whole renderer callback,
    // so the frontier is read between VIs, here on the delivery thread (CPU
    // only). A paused emulator's frontier advances unless a picture from just
    // before the pause still waits for the renderer's next callback.
    void frontier_tick(){
        {Phase timing(*this,GD_PHASE_TAKE);take_records(false);}if(!live())return;
        Phase timing(*this,GD_PHASE_FRONTIER);
        const auto now=qpc();
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
    }
    bool drain_tick(){
        if(!drain_started){drain_started=qpc();set_state(GD_DRAINING);
            const auto cause=diagnostic_reason();
            twin_end_epoch();
            if(channel)channel->close(cause ? GC_REASON_RUNTIME|cause : 0); // permanently revoke this unique channel
        }
        if(quarantine){
            exhausted.store(true);set_state(GD_FAULT);publish_status();return false;
        }
        take_records(true);reap_records(true);if(bridge_attempted)bridge_returns(true);
        if(quarantine){exhausted.store(true);set_state(GD_FAULT);publish_status();return false;}
        sa_frontier quiet{};
        if(!count() && !bridge_count() && sa_probe_quiescent(accounted,&quiet)){
            // No GL here: a drain runs when the renderer no longer calls back.
            // It deletes this epoch's names before it prepares again.
            if(prepared || sampler_attempted || bridge_attempted)gpu_retired=true;
            if(twin_gpu)twin_retired=true;
            twin_state=GC_TWIN_OFF;twin_reason=twin_poison=GD_NONE;twin_lane=twin_started_lane=0;
            twin_resolved=twin_copied=twin_missed=twin_bridge_token=0;twin_stopping=twin_gpu=twin_poisoned=false;
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
    // One pass of the state machine. The renderer thread runs every GPU step
    // (renderer=true, its context current, inside its capture gateway); the
    // delivery thread picks up requests, reads the frontier, drains and checks
    // deadlines, all without GL.
    // Returns true when a drain released ownership (fields may be reused).
    bool iterate(bool renderer){
        unsigned waiting=2;
        request_state.compare_exchange_strong(waiting,3);
        if(request_state.load()!=3 || exhausted.load())return false;
        if(diagnostic_epoch!=epoch){
            diagnostic_epoch=epoch;reset_metrics();
            prepare_steps={};prepare_steps.request_qpc=started;
            emit_diagnostic(GD_NONE,0,false,0,GD_KIND_REQUEST);
        }
        if(!live())return drain_tick();
        const auto now_state=state.load();
        if(now_state==GD_BOOTSTRAP){take_records(false);if(renderer)bootstrap_ready();}
        else if(now_state==GD_PREPARING){take_records(false);if(renderer)preparing_tick();else activate_when_imported();}
        else if(now_state==GD_ACTIVE){if(renderer)active_tick();else {frontier_tick();maybe_summary(qpc());}}
#ifdef GD_TEST_HOST
        test_deadline_visits.fetch_add(1);
#endif
        if(live() && state.load()!=GD_ACTIVE && qpc()-(channel_started?channel_started:started)>frequency*setup_deadline_ms/1000)fail(GD_DEADLINE);
        return false;
    }
    // A paused emulator stops calling back; the pause is not the pictures' age.
    // A record the delivery thread took during the pause ages from now.
    void resume_after_pause(uint64_t gap,uint64_t now){
        for(auto&p:pending)if(p.record || p.token)p.admitted=p.admitted+gap<now?p.admitted+gap:now;
        if(last_active_end)last_active_end+=gap;
    }
    // Delete (same context) or forget (context replaced) an ended epoch's names.
    void release_retired(const rb_surface&s){
        const bool same=s.context==gpu_context && s.context_generation==gpu_context_generation;
        // The twin's names first, in their own gateway: a held or broken twin
        // key must not leave an error for the clean capture's gateway.
        if(twin_retired && (!same || twin_gpu_begin())){
            if(same){twin_bridge.destroy();if(!twin_images.destroy())twin_images.abandon();twin_gpu_end();}
            else {twin_bridge=render_capture::Bridge{};twin_images.abandon();}
            twin_retired=false;
        }
        if(!gpu_retired || !gpu())return;
        if(same){sampler.destroy();bridge.destroy();if(!snapshots.destroy())snapshots.abandon();}
        else {sampler=render_capture::Sampler{};bridge=render_capture::Bridge{};snapshots.abandon();}
        gpu_retired=false;
    }
    // The renderer's capture callback (gd_surface), every VI while an epoch is
    // armed. Never waits: a tick the delivery thread holds is skipped.
    void render_tick(const rb_surface&s){
        if(!TryAcquireSRWLockExclusive(&tick_lock))return;
        if(channel)channel->adopt();
        const uint64_t now=qpc();
        if(last_render_tick && now-last_render_tick>frequency/10)resume_after_pause(now-last_render_tick,now);
        last_render_tick=now;
        if(request_state.load()==3 && live() && state.load()!=GD_BOOTSTRAP
                && (s.context!=surface.context || s.context_generation!=surface.context_generation)){
            fail(GD_CONTEXT_CHANGED); // the renderer replaced the context our names live in
        }
        const auto fault=sample_fault.exchange(0);
        if(fault && live()){fail(GD_SAMPLE_FAILED,fault,true);}
        in_render=true;gateway_open=false;
        release_retired(s);
        iterate(true);
        if(gateway_open && !source.end_capture() && live())fail(GD_GL_UNSUPPORTED,ERROR_INVALID_DATA);
        in_render=gateway_open=false;
        ReleaseSRWLockExclusive(&tick_lock);
    }
    // The capture gateway is open around a picture's snapshot copy; issue its
    // selection sample right behind it, in the same context.
    void issue_sample(const rb_image&image){
#ifdef GD_TEST_HOST
        if(test_sample)return;
#endif
        snapshot::Image copied{};
        if(snapshots.images().poll({image.serial,image.slot},&copied)!=snapshot::Poll::ready || !copied.texture
                || !source.begin_capture()){sample_fault.store(ERROR_INVALID_DATA);return;}
        bool issued;
        {render_capture::StateGuard guard(gl);issued=sampler.issue(image.slot,copied.texture);}
        if(!source.end_capture() || !issued)sample_fault.store(sampler.error()?sampler.error():ERROR_INVALID_DATA);
    }
    // ---- The ghost copy (twin lane) ----
    static constexpr int twin_skip=-2,twin_miss=-1,twin_fault=-3;
    void twin_consumer(bool on){if(source4.display_consumer)source4.display_consumer(on);}
    void twin_publish(){if(channel)channel->twin_status(twin_state,twin_reason,twin_lane,twin_resolved,twin_copied,twin_missed);}
    // One flag per error kind at most, so a bounded drain empties it.
    static bool drain_gl(){bool clean=true;for(unsigned i=0;i<8;++i){if(glGetError()==GL_NO_ERROR)return clean;clean=false;}return false;}
    // Twin GL work outside a picture's copy runs in a gateway of its own and
    // drains its errors there: the source gateway records any error it finds
    // and then refuses every later capture, clean included.
    bool twin_gpu_begin(){
        if(gateway_open){gateway_open=false;
            if(!source.end_capture()){if(live())fail(GD_GL_UNSUPPORTED,ERROR_INVALID_DATA);return false;}}
        return in_render && source.begin_capture();
    }
    bool twin_gpu_end(){const bool clean=drain_gl();return source.end_capture() && clean;}
    // gd_capture, right after the clean snapshot and its sample: the record's
    // twin picture, kept by record slot until take_records adopts it.
    void capture_twin(const rb_record&r){
        auto&c=twin_capture[r.slot];c={};c.occurrence=r.occurrence;
        const auto lane=twin_capturing.load();if(!lane)return;
        c.lane=lane;c.kind=twin_missing;
        rs_source_descriptor3 d{};
        if(!source4.query_display({r.occurrence,r.slot},&d) || d.bytes!=sizeof d || d.version!=RS_ABI_V4)return;
        if(d.kind==RS_SOURCE_SAME_AS_CLEAN){c.kind=twin_same;return;}
        if(d.kind!=RS_SOURCE_DISPLAY_VI)return;
        rb_image image{};
        if(twin_images.submit_display(&r,d,&image)){c.kind=twin_display;c.image=image;return;}
        // A full twin pool is one missed twin picture; any other refusal ends the lane.
        if(image.status!=uint32_t(snapshot::Result::full) && image.status!=uint32_t(snapshot::Result::inactive))
            twin_capture_fault.store(GD_TWIN_SNAPSHOT_FAILED);
    }
    void twin_release(Pending&p){
        if(p.twin_kind==twin_display){
            // One context: CPU-only and repeatable; a stale ticket belongs to a forgotten pool.
            const snapshot::Ticket ticket{p.twin_image.serial,p.twin_image.slot};snapshot::Image ignored{};
            if(twin_images.images().poll(ticket,&ignored)==snapshot::Poll::ready)twin_images.images().release(ticket);
        }
        p.twin_kind=twin_none;p.twin_image={};
    }
    void twin_adopt(Pending&p){
        const auto&c=twin_capture[p.record->slot];
        if(c.occurrence!=p.record->occurrence)return;
        p.twin_kind=c.kind;p.twin_lane=c.lane;p.twin_image=c.image;
        // Captured for a lane that has since stopped: never copied.
        if(twin_stopping || twin_state!=GC_TWIN_ACTIVE || p.twin_lane!=twin_lane)twin_release(p);
    }
    // Inside the clean copy's state guard, once the clean copy succeeded.
    int twin_copy(Pending&p){
        if(twin_state!=GC_TWIN_ACTIVE || twin_stopping)return twin_skip;
        if(p.twin_lane!=twin_lane || (p.twin_kind!=twin_display && p.twin_kind!=twin_same))return twin_miss;
        unsigned t=GC_BRIDGES;for(unsigned i=0;i<GC_BRIDGES && t==GC_BRIDGES;++i)if(!twin_credits[i].token)t=i;
        if(t==GC_BRIDGES)return twin_miss; // both twin textures still with the helper
        GLuint picture=p.image.texture;
        if(p.twin_kind==twin_display){snapshot::Image shown{};
            if(twin_images.images().poll({p.twin_image.serial,p.twin_image.slot},&shown)!=snapshot::Poll::ready || !shown.texture)
                return twin_miss;
            picture=shown.texture;
        }
#ifdef GD_TEST_HOST
        if(test_twin_bridge_fault.exchange(0))return twin_fault;
#endif
        const auto result=twin_bridge.copy(t,picture);
        if(result==render_capture::Bridge::Result::full)return twin_miss;
        if(result!=render_capture::Bridge::Result::copied){drain_gl();return twin_fault;}
        return int(t);
    }
    // Every SELECTED picture of an active lane gets one final twin outcome.
    void twin_outcome(const Pending&p,int twin){
        if(twin==twin_skip)return;
        if(twin==twin_fault){twin_abandon(GD_TWIN_BRIDGE_FAILED);return;}
        if(twin>=0){
            auto&c=twin_credits[twin];c={};c.token=++twin_bridge_token;c.decision=p.decision;twin_credit_qpc[twin]=qpc();
            // The key moved: a twin bridge nobody can learn of could never come back.
            if(!channel || channel->publish_twin_bridge(unsigned(twin),c.token,p.decision)!=ChannelResult::ok){
                twin_abandon(GD_TWIN_BRIDGE_FAILED);return;
            }
            c.published=true;++twin_copied;
        }else ++twin_missed;
        twin_resolved=p.decision.pts;twin_publish();
    }
    void twin_start(uint64_t lane){
        twin_started_lane=twin_lane=lane;twin_reason=GD_NONE;twin_resolved=twin_copied=twin_missed=0;
        twin_stopping=false;
        auto refuse=[this](uint32_t why){twin_state=GC_TWIN_STOPPED;twin_reason=why;twin_publish();};
        if(!source4.query_display){refuse(GD_TWIN_UNAVAILABLE);return;} // "capture layer predates ghost copies"
        if(twin_poisoned){refuse(twin_poison);return;}
        twin_state=GC_TWIN_PREPARING;twin_publish();
        // The twin pool doubles the snapshot memory; the immutable limit bounds both.
        const uint64_t bytes=snapshots.images().counters().texture_bytes;
        if(!bytes || bytes>limits.snapshot_bytes/2){refuse(GD_TWIN_SNAPSHOT_FAILED);return;}
        if(!twin_gpu_begin()){refuse(GD_TWIN_SNAPSHOT_FAILED);return;}
        twin_gpu=true;
        const unsigned images=request.offer_count<RB_SLOTS?request.offer_count:RB_SLOTS-1;
        uint32_t why=GD_NONE;
        if(!twin_images.prepare(copy_gl,source,surface,images,budget,nullptr,true))why=GD_TWIN_SNAPSHOT_FAILED;
        else if(!twin_bridge.prepare(gl,surface.width,surface.height,twin_names))why=GD_TWIN_IMPORT_FAILED;
        if(why){twin_images.stop();if(!twin_images.destroy())twin_images.abandon();twin_bridge.destroy();}
        if(!twin_gpu_end() && !why){
            why=GD_TWIN_IMPORT_FAILED; // an error the steps did not report
            twin_images.stop();
            if(twin_gpu_begin()){twin_bridge.destroy();if(!twin_images.destroy())twin_images.abandon();twin_gpu_end();}
            else twin_poisoned=true;
        }
        if(why){refuse(why);return;}
        twin_state=GC_TWIN_ACTIVE;twin_capturing.store(lane);twin_consumer(true);twin_publish();
    }
    // No new twin copies from here; STOPPED once every twin credit retired.
    void twin_stop(uint32_t why){
        twin_capturing.store(0);twin_consumer(false);
        if(why && !twin_reason)twin_reason=why;
        if(twin_state!=GC_TWIN_PREPARING && twin_state!=GC_TWIN_ACTIVE)return;
        if(twin_stopping)return;
        twin_stopping=true;twin_stop_qpc=qpc();twin_images.stop();
        for(auto&p:pending)twin_release(p);
    }
    // A twin key that is held or broken: forget its credits, keep its names
    // for the epoch's GL retirement, and start no other lane this epoch.
    void twin_abandon(uint32_t why){
        twin_poisoned=true;if(!twin_poison)twin_poison=why;
        for(unsigned n=0;n<GC_BRIDGES;++n){twin_credits[n]={};twin_credit_qpc[n]=0;}
        twin_stop(why);
    }
    void twin_returns(){
        bool held=false;for(const auto&c:twin_credits)held|=c.token!=0;
        if(!held)return;
        for(unsigned n=0;n<GC_BRIDGES;++n){unsigned slot=0;gc_receipt receipt{};
            const auto result=channel->take_twin_receipt(slot,receipt);
            if(result==ChannelResult::empty || result==ChannelResult::busy)break;
            if(result!=ChannelResult::ok || slot>=GC_BRIDGES || twin_credits[slot].token!=receipt.token){twin_stop(GD_CHANNEL_FAILED);break;}
            twin_credits[slot].receipt=true;
        }
        const auto now=qpc();
        for(unsigned n=0;n<GC_BRIDGES;++n){auto&c=twin_credits[n];if(!c.token)continue;
            if(now>twin_credit_qpc[n] && now-twin_credit_qpc[n]>frequency*limits.max_age_ms/1000){twin_abandon(GD_TWIN_KEY_ABANDONED);return;}
            // Stopping, the key alone retires it: the receipt only acknowledged metadata.
            if(!c.receipt && !twin_stopping)continue;
            if(!gpu())return;
            const auto key=twin_bridge.key0_returned(n);
            if(key==render_capture::Bridge::KeyReturn::pending)continue;
            if(key!=render_capture::Bridge::KeyReturn::ready){drain_gl();twin_abandon(GD_TWIN_BRIDGE_FAILED);return;}
            if(channel->retire_twin_bridge_after_key0(n,c.token)!=ChannelResult::ok)twin_stop(GD_CHANNEL_FAILED);
            c={};twin_credit_qpc[n]=0;
        }
    }
    // Every twin credit retired: delete this lane's names, then STOPPED.
    void twin_finish(){
        for(const auto&c:twin_credits)if(c.token)return;
        bool gone=false;
        if(twin_gpu_begin()){
            if(!twin_poisoned)twin_bridge.destroy();
            gone=twin_images.destroy();twin_gpu_end();
        }
        // A record taken later still returns its twin image; wait for it, bounded.
        if(!gone && qpc()-twin_stop_qpc<=frequency*limits.max_age_ms/1000)return;
        if(!gone)twin_images.abandon();
        twin_stopping=false;twin_state=GC_TWIN_STOPPED;twin_publish();
    }
    // render_tick while the clean capture is ACTIVE: the client's request, then custody.
    void twin_tick(){
        if(const auto fault=twin_capture_fault.exchange(0))twin_stop(fault);
        gc_twin_client client{};
        const auto read=channel->twin_client(client);
        const bool running=twin_state==GC_TWIN_PREPARING || twin_state==GC_TWIN_ACTIVE;
        if(read==ChannelResult::invalid){if(running)twin_stop(GD_CHANNEL_FAILED);}
        else if(read==ChannelResult::ok){
            const bool ready=client.state==GC_TWIN_CLIENT_READY;
            if(running && (!ready || client.lane!=twin_lane))twin_stop(GD_NONE);
            else if(!running && ready && client.lane>twin_started_lane)twin_start(client.lane);
        }
        twin_returns();
        if(twin_stopping)twin_finish();
    }
    // The clean epoch ends: its keys are abandoned with it (the helper's
    // textures end with its session) and the renderer deletes the imports.
    void twin_end_epoch(){
        twin_capturing.store(0);twin_consumer(false);twin_images.stop();
        for(unsigned n=0;n<GC_BRIDGES;++n){twin_credits[n]={};twin_credit_qpc[n]=0;}
        if(twin_state==GC_TWIN_PREPARING || twin_state==GC_TWIN_ACTIVE){
            twin_state=GC_TWIN_STOPPED;if(!twin_reason)twin_reason=GD_CANCELLED;twin_publish();
        }
        twin_stopping=false;
    }
    void run(){
        worker_thread=GetCurrentThreadId();
        for(;;){
#ifdef GD_TEST_HOST
            if(auto probe=test_iteration_probe.load())probe();
#endif
            AcquireSRWLockExclusive(&tick_lock);
            if(channel)channel->adopt();
            const bool released=iterate(false);
            ReleaseSRWLockExclusive(&tick_lock);
            if(released)continue;
            HANDLE events[2]={wake,channel?channel->wake_event():nullptr};
            // While ACTIVE the renderer does the GPU work; this thread reads
            // the frontier, which the status publishes at most at 60Hz.
            const DWORD timeout=request_state.load()==3 && !exhausted.load()
                ? (state.load()==GD_ACTIVE?16u:limits.poll_ms):INFINITE;
            WaitForMultipleObjects(events[1]?2:1,events,FALSE,timeout);
        }
    }
};
std::atomic<Runtime*> runtime{nullptr};
std::atomic<bool> configured{false};
DWORD WINAPI delivery_thread(void* p){static_cast<Runtime*>(p)->run();return 0;}
}

static int configure_delivery(const rs_api*s,const rs_api_v3*extended,const rs_api_v4*display,const rcl_api*c,const gd_limits*l){
    if(!source_valid(s,c) || !limits_valid(l))return GD_INVALID;
    bool no=false;if(!configured.compare_exchange_strong(no,true))return GD_BUSY;
    auto*r=new(std::nothrow)Runtime;if(!r){configured.store(false);return GD_SYSTEM_ERROR;}
    r->source=*s;if(extended)r->source3=*extended;if(display)r->source4=*display;r->contexts=*c;r->limits=*l;
    LARGE_INTEGER f{};QueryPerformanceFrequency(&f);r->frequency=uint64_t(f.QuadPart);
    r->wake=CreateEventW(nullptr,FALSE,FALSE,nullptr);r->changed=CreateEventW(nullptr,FALSE,FALSE,nullptr);
    if(!r->frequency || !r->wake || !r->changed){if(r->wake)CloseHandle(r->wake);if(r->changed)CloseHandle(r->changed);delete r;configured.store(false);return GD_SYSTEM_ERROR;}
    HANDLE worker=CreateThread(nullptr,0,delivery_thread,r,0,nullptr);
    if(!worker){CloseHandle(r->wake);CloseHandle(r->changed);delete r;configured.store(false);return GD_SYSTEM_ERROR;}
    CloseHandle(worker);runtime.store(r);return GD_ACCEPTED;
}
extern "C" int __cdecl gd_configure(const rs_api*s,const rcl_api*c,const gd_limits*l){return configure_delivery(s,nullptr,nullptr,c,l);}
extern "C" int __cdecl gd_configure3(const rs_api_v3*s,const rcl_api*c,const gd_limits*l){
    return rs_valid_v3(s)?configure_delivery(&s->boundary,s,nullptr,c,l):GD_INVALID;
}
extern "C" int __cdecl gd_configure4(const rs_api_v4*s,const rcl_api*c,const gd_limits*l){
    return rs_valid_v4(s)?configure_delivery(&s->v3.boundary,&s->v3,s,c,l):GD_INVALID;
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
    r->started=qpc();r->channel_started=r->drain_started=0;r->offer_token=r->bridge_token=r->twin_bridge_token=0;
    r->frontier_qpc=r->frontier_occurrence=0;r->refusal_generation=0;r->status_time=0;
    // No graphics call: make request/surface/gateway absence distinguishable
    // before a stalled source or renderer can prevent channel publication.
    r->startup={};memcpy(r->startup.magic,GPU_STARTUP_MAGIC,8);
    r->startup.version=GPU_STARTUP_VERSION;r->startup.bytes=sizeof r->startup;r->startup.epoch=e;
    r->startup.control_generation=h->generation;r->startup.nonce_lo=h->nonce_lo;r->startup.nonce_hi=h->nonce_hi;
    r->publish_startup();
    r->set_state(GD_BOOTSTRAP);r->publish_status();
    if(r->exhausted.load()){r->request_state.store(2);SetEvent(r->wake);return GD_EXHAUSTED;}
    r->gate.store(e);
    if(cancelled!=r->cancel.load()){r->gate.store(0);r->source.disarm();}
    r->request_state.store(2);SetEvent(r->wake);SetEvent(r->changed);return GD_ACCEPTED;
}
extern "C" void __cdecl gd_disarm(uint32_t why){auto*r=runtime.load();if(r)r->revoke(why?why:GD_CANCELLED);}
extern "C" uint32_t __cdecl gd_epoch(){const auto*r=runtime.load();return r?r->current_epoch():0;}
extern "C" void __cdecl gd_surface(const rb_record*r){
    auto*self=runtime.load();if(!self || !r || r->epoch!=self->current_epoch())return;
    if(self->state.load()==GD_BOOTSTRAP){
        unsigned free=0;
        if(self->surface_state.compare_exchange_strong(free,1)){
            self->mailbox=r->surface;self->mailbox_epoch=r->epoch;self->mailbox_qpc=qpc();self->surface_state.store(2);
        }
    }
    self->render_tick(r->surface); // the renderer's thread, its context current
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
    const int submitted=self->snapshots.submit(r,out);
    if(submitted && out->ownership==RB_IMAGE_SUBMITTED){self->issue_sample(*out);self->capture_twin(*r);}
    return submitted;
}
extern "C" int __cdecl gd_completed(const rb_image*i){const auto*r=runtime.load();return r && i && r->snapshots.completed(*i);}
extern "C" HANDLE __cdecl gd_signal(){const auto*r=runtime.load();return r?r->changed:nullptr;}
extern "C" int __cdecl gd_read_status(gd_status*out){
    const auto*r=runtime.load();if(!r || !out)return 0;
    for(unsigned n=0;n<3;++n){const auto before=r->status_seq.load();if(before&1)continue;
        uint32_t words[sizeof(*out)/4]{};words[0]=before;
        for(unsigned i=1;i<sizeof(*out)/4;++i)words[i]=r->status_words[i].load();
        const auto failure=r->first_failure.load();
        const auto state=r->state.load();const auto why=failure?uint32_t(failure):r->reason.load();
        // Admission replaces the request identity before its complete snapshot.
        // Revalidate after reading the atomic tuple to avoid mixing epochs.
        if(failure!=r->first_failure.load() || before!=r->status_seq.load() || r->request_state.load()==1)continue;
        memcpy(out,words,sizeof(*out));out->state=state;out->reason=why;out->error=uint32_t(failure>>32);return 1;
    }
    return 0;
}
extern "C" int __cdecl gd_read_startup(gpu_startup_v1*out){
    const auto*r=runtime.load();if(!r||!out)return 0;
    for(unsigned n=0;n<3;++n){const auto before=r->startup_seq.load();if(!before||before&1)continue;
        uint32_t words[sizeof(*out)/4]{};
        for(unsigned i=0;i<sizeof(*out)/4;++i)words[i]=r->startup_words[i].load();
        if(before==r->startup_seq.load()){memcpy(out,words,sizeof(*out));return 1;}
    }return 0;
}
#ifdef GD_TEST_HOST
#include "gpu_delivery_retry_test.h"
extern "C" int __cdecl rc_test_delivery_status(uint32_t*,uint32_t*);
// Observe real fail() inside its callback, before revoke/drain can publish a
// replacement status. No worker, context, renderer or live control page exists.
extern "C" int gd_test_failure_status_coherence(gd_status*out,uint32_t*controls){
    if(!out || !controls || runtime.load())return 0;
    Runtime r;r.source.disarm=[](){};r.source.activate=[]()->uint32_t{return 17;};
    r.limits.max_slots=1;r.limits.snapshot_bytes=4u<<20;r.limits.channel_bytes=1u<<20;
    r.wake=CreateEventW(nullptr,FALSE,FALSE,nullptr);r.changed=CreateEventW(nullptr,FALSE,FALSE,nullptr);
    if(!r.wake || !r.changed){if(r.wake)CloseHandle(r.wake);if(r.changed)CloseHandle(r.changed);return 0;}
    runtime.store(&r);
    gc_header h{};h.owner_pid=GetCurrentProcessId();h.owner_birth=gpu_channel::process_birth(GetCurrentProcess());
    h.nonce_lo=23;h.nonce_hi=29;h.generation=7;h.offer_count=h.table_count=h.packet_count=1;h.table[0]={0,4};
    h.packet_bytes=h.pending_bytes=1024;h.ram_budget=1u<<20;
    int valid=gd_request(&h,4u<<20)==GD_ACCEPTED;
    static gd_status*rows=nullptr;static uint32_t*projected=nullptr;static bool callback_ok=false;
    rows=out;projected=controls;callback_ok=false;
    gd_set_diagnostic([](const gd_diagnostic*){
        uint32_t state=0;callback_ok=gd_read_status(&rows[0]) && rc_test_delivery_status(&state,&projected[0]);
    });
    r.fail(GD_GL_UNSUPPORTED,uint32_t(render_capture::LoadError::keyed_mutex));
    gd_set_diagnostic(nullptr);
    valid=valid && callback_ok;
    r.fail(GD_DEADLINE,18);gd_disarm(GD_OWNER_GONE);
    uint32_t state=0;
    valid=valid && gd_read_status(&out[1]) && rc_test_delivery_status(&state,&controls[1]);
    // Stand in only for completed CPU retirement, then use real admission/reset.
    r.request_state.store(0);r.epoch=0;r.set_state(GD_OFF);
    h.nonce_lo=31;h.generation=8;
    valid=valid && gd_request(&h,4u<<20)==GD_ACCEPTED && gd_read_status(&out[2])
        && rc_test_delivery_status(&state,&controls[2]);
    gd_disarm(GD_CANCELLED);runtime.store(nullptr);CloseHandle(r.wake);CloseHandle(r.changed);return valid;
}
// The accepted-request API and bootstrap path, with the source refusing its
// gateway. No worker, context, surface discovery or GPU allocation is started.
extern "C" int gd_test_bootstrap_receipt(gpu_startup_v1*out){
    if(!out || runtime.load())return 0;
    Runtime r;r.source.disarm=[](){};r.source.activate=[]()->uint32_t{return 17;};
    r.source.begin_capture=[](){return false;};
    r.limits.max_slots=1;r.limits.snapshot_bytes=4u<<20;r.limits.channel_bytes=1u<<20;
    r.wake=CreateEventW(nullptr,FALSE,FALSE,nullptr);r.changed=CreateEventW(nullptr,FALSE,FALSE,nullptr);
    if(!r.wake || !r.changed){if(r.wake)CloseHandle(r.wake);if(r.changed)CloseHandle(r.changed);return 0;}
    r.startup.width=999;memcpy(r.startup.vendor,"stale vendor",13);
    runtime.store(&r);
    gc_header h{};h.owner_pid=GetCurrentProcessId();h.owner_birth=gpu_channel::process_birth(GetCurrentProcess());
    h.nonce_lo=23;h.nonce_hi=29;h.generation=7;h.offer_count=h.table_count=h.packet_count=1;h.table[0]={0,4};
    h.packet_bytes=h.pending_bytes=1024;h.ram_budget=1u<<20;
    int valid=gd_request(&h,4u<<20)==GD_ACCEPTED && gd_read_startup(&out[0]);
    r.in_render=true;r.mailbox.width=17;r.mailbox.height=11;r.surface_state.store(2);
    valid=valid && !r.bootstrap_ready() && gd_read_startup(&out[1]);
    gd_disarm(GD_CANCELLED);runtime.store(nullptr);CloseHandle(r.wake);CloseHandle(r.changed);return valid;
}
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
    // A quiet window waits the full minute; five seconds is not enough.
    bool valid=!r.maybe_summary(5999) && !r.maybe_summary(6000) && !r.maybe_summary(60999)
        && r.maybe_summary(61000) && calls==1 && r.live();
    rb_record next=records[2];next.surface.boundary_qpc=1080;r.observe_source(next);
    r.phase_done(GD_PHASE_SAMPLE,7000,7003);
    // A refused picture closes the window at the next five-second boundary.
    ++r.refused_pictures;
    valid=valid && !r.maybe_summary(65999) && r.maybe_summary(66000) && !r.maybe_summary(66001)
        && calls==2 && r.live();
    r.fail(GD_SOURCE_REFUSED,7);
    valid=valid && calls==3 && !r.maybe_summary(71000);
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
extern "C" void gd_test_bridge_fault_once(uint32_t error){test_bridge_fault.store(error);}
extern "C" void gd_test_twin_bridge_fault_once(uint32_t error){test_twin_bridge_fault.store(error);}
extern "C" uint32_t gd_test_deadline_visits(){return test_deadline_visits.load();}
#endif
