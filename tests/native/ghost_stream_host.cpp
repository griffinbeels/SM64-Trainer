/* Stream stress tests use a deterministic capture provider. The real N64 byte
 * reader has independent tests; here the boundary is queue/config ownership. */
#include "ghost_stream.h"
#include <atomic>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <thread>
extern "C" void ghost_capture_reset(gc_reader *r) { *r = {}; }
extern "C" uint32_t ghost_capture_validate_layout(const gc_layout *c) {
    return c && c->bytes == sizeof(*c) && c->version == GC_VERSION ? GC_OK : GC_BAD_CONFIG;
}
extern "C" uint32_t ghost_capture_begin(gc_reader *,const gc_layout *c,const gc_frame *f,gc_sample *s,gc_root_probe *) {
    *s = {}; s->bytes=sizeof(*s); s->version=GC_VERSION; s->epoch=f->epoch;
    s->sequence=f->sequence; s->dlist_serial=f->dlist_serial; s->qpc=f->qpc;
    s->global_timer=c->global_timer;
    s->valid_fields=GC_VALID_CLOCK | GC_VALID_CAMERA | GC_VALID_ACTION | GC_VALID_WARP;
    s->level=9; s->area=1; s->igt=static_cast<uint16_t>(f->sequence); s->live_action=2;
    return GC_OK;
}
static void emit(int64_t qpc) {
    assert(ghost_stream::begin(nullptr,0,7,qpc)); ghost_stream::end(true);
}
int main() {
    const auto *api=ghost_stream::api();
    gc_layout config{}; config.bytes=sizeof(config); config.version=GC_VERSION; config.global_timer=0x1234;
    config.clock={GCC_VERSION,30,10,10,60,30,1,65000,1,1,{6},{2}};
    config.rom_layout=GC_ROM_US;
    assert(api->configure(&config)); ghost_stream::rom(GC_ROM_US);
    // Fill all slots, prove bounded drops preserve every already-published item.
    for(unsigned i=0;i<GS_SLOTS+9;++i) emit(1000+i);
    auto stats=api->stats(); assert(stats.offered==GS_SLOTS+9 && stats.dropped==9);
    gs_record record{};
    uint64_t previous=0;
    for(unsigned i=0;i<GS_SLOTS;++i) {
        assert(api->read(&record,sizeof(record))==1);
        assert(record.bytes==sizeof(record) && record.version==GS_VERSION);
        assert(record.sample.qpc==1000+i && record.sample.sequence==previous+1);
        assert(record.sample.global_timer==0x1234); previous=record.sample.sequence;
        assert((record.sample.clock.valid&GCC_ELAPSED_VALID)
            && record.sample.clock.elapsed_frames==record.sample.sequence-1);
    }
    assert(api->read(&record,sizeof(record))==0 && record.bytes==0);
    emit(2000); assert(api->read(&record,sizeof(record))==1);
    assert(record.sample.sequence==GS_SLOTS+10); // actual missed occurrences remain visible
    assert(record.sample.clock.elapsed_frames==GS_SLOTS+9); // clock precedes queue loss
    assert(api->read(&record,sizeof(record)-1)==-1);
    // Concurrent single producer/consumer; copy never races slot reuse.
    std::atomic<bool> done{false};
    std::atomic<unsigned> received{0};
    std::thread consumer([&] {
        uint64_t last=0;
        for(;;) {
            gs_record r{};
            if(api->read(&r,sizeof(r))==1) {
                assert(r.sample.sequence>last); last=r.sample.sequence;
                assert(r.sample.qpc==int64_t(r.sample.sequence)+5000);
                assert(r.sample.global_timer==0x1234); ++received;
            } else if(done.load(std::memory_order_acquire)) {
                // Producer release of done follows its final publication.
                if(api->read(&r,sizeof(r))==0) break;
                assert(r.sample.sequence>last); last=r.sample.sequence;
                assert(r.sample.qpc==int64_t(r.sample.sequence)+5000); ++received;
            }
        }
    });
    const uint64_t first=GS_SLOTS+11;
    for(uint64_t i=0;i<50000;++i) emit(int64_t(first+i)+5000);
    done.store(true,std::memory_order_release); consumer.join();
    assert(received>0 && api->stats().offered==api->stats().taken+api->stats().dropped);
    // Malformed/disabled config cannot carry a previous pose into a new list.
    config.version=99; assert(!api->configure(&config));
    assert(!ghost_stream::begin(nullptr,0,7,1) && !ghost_stream::sample());
    config.version=GC_VERSION; config.global_timer=0x5678; assert(api->configure(&config));
    const auto before=api->stats().epoch; emit(3000);
    assert(api->read(&record,sizeof(record))==1 && record.sample.epoch>before);
    assert(record.sample.global_timer==0x5678);
    // A swap to JP under the US config: the cartridge is not open to it, and
    // no list is read until a JP config arrives (and the reverse).
    ghost_stream::rom(GC_ROM_JP);
    assert(!ghost_stream::requested() && !ghost_stream::begin(nullptr,0,7,4) && !ghost_stream::sample());
    config.rom_layout=GC_ROM_JP; assert(api->configure(&config) && ghost_stream::requested()); emit(3100);
    assert(api->read(&record,sizeof(record))==1 && record.sample.qpc==3100);
    ghost_stream::rom(GC_ROM_US); assert(!ghost_stream::requested() && !ghost_stream::begin(nullptr,0,7,5));
    config.rom_layout=GC_ROM_US; assert(api->configure(&config) && ghost_stream::requested());
    ghost_stream::rom(0); assert(!ghost_stream::begin(nullptr,0,7,2));
    api->disable(); assert(!api->stats().enabled);
    std::puts("ghost stream ownership passed");
}
