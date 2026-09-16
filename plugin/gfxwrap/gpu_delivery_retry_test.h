/* CPU-only real-channel retry witness, included only by the native test build.
 * Source/GPU completion is a supplied precondition. Actual publication,
 * seqlock-busy refusal, retry and slot reuse use gpu_channel::Producer. */
#pragma once
namespace {
struct RetryChannel {
    Runtime r;
    HANDLE mapping=nullptr;
    uint8_t*view=nullptr;
    uint32_t releases=0;
    rb_record record{};
    static RetryChannel*current;
    ~RetryChannel(){if(view)UnmapViewOfFile(view);if(mapping)CloseHandle(mapping);current=nullptr;}
    bool open(unsigned scenario){
        current=this;record.occurrence=42;record.slot=0;
        r.source.disarm=[](){};
        r.source.release=[](rb_ticket t){
            if(t.occurrence!=42 || t.slot!=0)return 0;
            ++current->releases;current->record={};current->record.occurrence=99;return 1;
        };
        r.request.offer_count=1;r.channel.reset(new gpu_channel::Producer);
        gc_header h{};h.owner_pid=GetCurrentProcessId();h.owner_birth=gpu_channel::process_birth(GetCurrentProcess());
        h.nonce_lo=qpc();h.nonce_hi=scenario+1;h.epoch=h.generation=1;h.qpc_frequency=10000000;
        h.width=h.height=8;h.offer_count=h.table_count=h.packet_count=1;h.table[0]={0,4};
        h.packet_bytes=h.pending_bytes=1024;h.ram_budget=1u<<20;
        h.texture_names[0][0]='A';h.texture_names[1][0]='B';
        if(r.channel->create(h)!=ChannelResult::ok)return false;
        mapping=OpenFileMappingW(FILE_MAP_ALL_ACCESS,FALSE,r.channel->name());
        if(mapping)view=static_cast<uint8_t*>(MapViewOfFile(mapping,FILE_MAP_ALL_ACCESS,0,0,0));
        return view!=nullptr;
    }
    gc_offer offer(uint64_t token,uint64_t occurrence){
        gc_offer o{};o.token=token;o.occurrence=occurrence;o.list_qpc=1;o.boundary_qpc=2;
        o.stamp_bytes=o.lengths[0]=4;o.lists_since=1;o.outcome=RB_OBSERVED;return o;
    }
    ChannelResult publish(uint64_t token,uint64_t occurrence){
        const uint8_t stamp[4]{},sample[4]{};
        return r.channel->publish(0,offer(token,occurrence),stamp,sample);
    }
    bool decision(unsigned kind,gc_decision&d){
        const auto&h=r.channel->header();
        d={};d.seq=2;d.kind=kind;d.token=1;d.occurrence=42;
        d.nonce_lo=h.nonce_lo;d.nonce_hi=h.nonce_hi;d.epoch=h.epoch;d.generation=h.generation;
        if(kind==GC_SELECTED){d.encode_serial=1;d.nominal_duration=3000;}else d.reason=1;
        memcpy(view+h.decision_offset,&d,sizeof d);
        unsigned slot=99;gc_decision received{};
        return r.channel->take_decision(slot,received)==ChannelResult::ok && slot==0;
    }
    void busy(bool value){InterlockedExchange(reinterpret_cast<volatile LONG*>(view+r.channel->header().client_offset),value?1:0);}
    bool offer_present()const{return reinterpret_cast<const gc_offer*>(view+r.channel->header().offer_offset)->kind!=0;}
    bool bridge_present()const{return reinterpret_cast<const gc_bridge*>(view+r.channel->header().bridge_offset)->ready!=0;}
};
RetryChannel*RetryChannel::current=nullptr;
}

extern "C" int gd_test_sample_retry(unsigned busy_count,uint32_t*out){
    if(!out || !busy_count || busy_count>100000)return 0;
    RetryChannel t;if(!t.open(3))return 0;
    auto&r=t.r;r.epoch=1;r.gate.store(1);r.session_cancel.store(r.cancel.load());
    r.request.table_count=1;r.request.table[0]={0,4};
    r.surface.width=r.surface.height=8;r.sample.reset(new uint8_t[4]);
    auto&record=t.record;record.epoch=1;record.outcome=RB_OBSERVED;
    record.image.ownership=RB_IMAGE_SUBMITTED;
    record.stamp.table_count=1;record.stamp.lengths[0]=4;
    record.stamp.bytes[0]=42;record.stamp.list_qpc=1;record.stamp.lists_since=1;
    record.surface.boundary_qpc=2;
    auto&p=r.pending[0];p.record=&record;p.ready=true;p.image.texture=73;
    static uint32_t samples=0;samples=0;
    // Only the GPU sampling boundary is substituted. sample_head and the actual
    // channel publication/readback run unchanged, with a held-odd client page.
    test_sample=[](const snapshot::Image&image,uint8_t*bytes){
        ++samples;bytes[0]=uint8_t(samples);bytes[1]=uint8_t(image.texture);
        bytes[2]=0x5a;bytes[3]=0xa5;return gpu_selection::Result::sampled;
    };
    struct ResetSample{~ResetSample(){test_sample=nullptr;}}reset;
    t.busy(true);
    for(unsigned n=0;n<busy_count;++n)r.sample_head();
    out[0]=samples;out[1]=p.published;out[2]=t.offer_present();
    t.busy(false);r.sample_head();
    const auto&h=r.channel->header();
    const auto*offer=reinterpret_cast<const gc_offer*>(t.view+h.offer_offset);
    if(!p.published || !t.offer_present() || offer->occurrence!=42 || offer->token!=1
            || offer->list_qpc!=1 || offer->boundary_qpc!=2 || offer->stamp_bytes!=4)return 0;
    const uint8_t expected_stamp[]={42,0,0,0};
    if(memcmp(t.view+offer->stamp_offset,expected_stamp,4))return 0;
    out[3]=samples;out[4]=t.view[offer->sample_offset];out[5]=t.view[offer->sample_offset+1];
    if(t.view[offer->sample_offset+2]!=0x5a || t.view[offer->sample_offset+3]!=0xa5)return 0;
    // Recycle the same slot. The next occurrence must get a fresh sample.
    const auto next_record=record;gc_decision decision{};
    if(!t.decision(GC_SUPPRESSED,decision) || !r.retire_record(p,false))return 0;
    record=next_record;record.occurrence=43;record.stamp.bytes[0]=43;
    p.record=&record;p.ready=true;p.image.texture=74;r.sample_head();
    offer=reinterpret_cast<const gc_offer*>(t.view+h.offer_offset);
    if(!p.published || offer->occurrence!=43 || offer->token!=2 || t.view[offer->stamp_offset]!=43)return 0;
    out[6]=samples;out[7]=t.view[offer->sample_offset];
    static gd_diagnostic emitted{};r.worker_thread=GetCurrentThreadId();
    gd_set_diagnostic([](const gd_diagnostic*d){emitted=*d;});
    r.emit_diagnostic(GD_NONE,0,true);gd_set_diagnostic(nullptr);
    out[8]=uint32_t(emitted.sample_calls);out[9]=uint32_t(emitted.sample_reuses);out[10]=uint32_t(emitted.publish_busy);
    if(emitted.snapshot_bytes || emitted.bridge_bytes)return 0; // CPU fixture owns no GPU allocation.
    r.reset_metrics();return !r.sample_calls && !r.sample_reuses && !r.publish_busy;
}

extern "C" int gd_test_channel_retry(unsigned scenario,uint32_t*out){
    if(!out || scenario>2)return 0;
    RetryChannel t;if(!t.open(scenario) || t.publish(1,42)!=ChannelResult::ok)return 0;
    gc_decision d{};if(!t.decision(scenario?GC_SELECTED:GC_SUPPRESSED,d))return 0;
    if(scenario==0){
        auto&p=t.r.pending[0];p.record=&t.record;p.token=1;p.published=true;
        t.busy(true);if(!t.r.retire_record(p,false))return 0;
        out[0]=t.r.count();out[1]=t.r.occupied();out[2]=t.offer_present();
        t.busy(false);t.r.reap_records(false);
        out[3]=t.r.occupied();out[4]=t.releases;
        out[5]=uint32_t(t.publish(2,100)); // actual slot must accept its next offer
    }else{
        auto&credit=t.r.credits[0];credit.token=1;credit.decision=d;
        if(scenario==1){
            t.busy(true);t.r.publish_bridges();
            out[0]=credit.token!=0;out[1]=credit.published;out[2]=t.bridge_present();
            t.busy(false);if(!t.r.publish_bridges())return 0;
            out[3]=credit.published;out[4]=t.bridge_present();
            out[5]=t.r.publish_bridges() && !t.r.first_failure.load();
        }else{
            if(!t.r.publish_bridges())return 0;credit.receipt=true;
            t.busy(true);if(!t.r.retire_bridge(0,false))return 0;
            out[0]=credit.token!=0;out[1]=credit.published;out[2]=t.bridge_present();
            t.busy(false);if(!t.r.retire_bridge(0,false))return 0;
            out[3]=credit.token!=0;out[4]=t.bridge_present();out[5]=!t.r.first_failure.load();
        }
    }
    return 1;
}

extern "C" int gd_test_failure_snapshot(gd_diagnostic*out,uint32_t*receipts){
    if(!out || !receipts)return 0;
    static gd_diagnostic*destination=nullptr;static Runtime*observed=nullptr;
    static uint32_t calls=0,gate_at_callback=0;
    Runtime r;destination=out;observed=&r;calls=gate_at_callback=0;
    r.worker_thread=GetCurrentThreadId();r.epoch=17;r.gate.store(17);
    r.session_cancel.store(r.cancel.load());r.frequency=10000000;r.source.disarm=[](){};
    rb_record record{};record.occurrence=42;
    auto&p=r.pending[0];p.record=&record;p.token=1;p.published=p.decided=true;
    p.decision.kind=GC_SELECTED;p.admitted=qpc()-100;
    r.pending[1].token=2;r.pending[1].published=true;
    r.credits[0].token=1;r.accounted=42;r.offer_token=2;
    r.phase_done(GD_PHASE_SAMPLE,100,150);r.phase=GD_PHASE_COPY;r.phase_started=qpc()-10;
    gd_set_diagnostic([](const gd_diagnostic*d){
        *destination=*d;++calls;gate_at_callback=observed->gate.load();
        throw 1; // A faulty diagnostic must not replace capture's first cause.
    });
    r.fail(GD_SOURCE_REFUSED,7);r.fail(GD_DEADLINE,8);gd_set_diagnostic(nullptr);
    receipts[0]=calls;receipts[1]=gate_at_callback;
    return r.first_failure.load()==GD_SOURCE_REFUSED && r.error==7 && !r.gate.load();
}
