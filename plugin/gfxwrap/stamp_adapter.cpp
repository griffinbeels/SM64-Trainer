#include "stamp_adapter.h"
#include <atomic>
#include <cstring>
namespace {
sa_ops ops{};
const rs_api *source=nullptr;
bool attempted=false;
std::atomic<bool> bound{false},exhausted{false};
rb_stamp pending{}; // serialized original producer only
uint32_t seen_epoch=0,last_origin=UINT32_MAX,next_sequence=0,next_refusal=0;
uint32_t latched_epoch=0;
// Renderer thread only (capture callback): the swap count seen with the
// previous record of this epoch, so an origin change without a swap is
// recognised (SA_NO_SWAP) instead of pairing a stale front buffer.
uint32_t last_swap_count=0;bool have_swap=false;
std::atomic<uint32_t> sequence{0},ack_epoch{0},refusals{0};
std::atomic<uint64_t> committed{0},ack_occurrence{0};
std::atomic<uint32_t> refusal_sequence{0},refusal_words[sizeof(sa_refusal)/4]{};
std::atomic<uint32_t> refusal_armed{0},refusal_baseline_full{0},refusal_baseline_busy{0};
struct AtomicTable {
    std::atomic<uint32_t> sequence{0},epoch{0},limit{0},count{0};
    std::atomic<uint32_t> offsets[RB_STAMP_FIELDS]{},lengths[RB_STAMP_FIELDS]{};
} table;
#ifdef SA_TEST_HOST
void (__cdecl *probe)(unsigned)=nullptr;
#define PROBE(n) do {if(probe)probe(n);}while(0)
#else
#define PROBE(n) ((void)0)
#endif
int64_t now(){LARGE_INTEGER qpc{};QueryPerformanceCounter(&qpc);return qpc.QuadPart;}
uint32_t epoch(){return bound.load()&&!exhausted.load()?ops.current_epoch(ops.user):0;}
void refresh(uint32_t e){if(e!=seen_epoch){pending={};seen_epoch=e;have_swap=false;last_swap_count=0;}}
void latch_refusal(uint32_t e){
    if(!e || latched_epoch==e || refusal_armed.load()!=e || epoch()!=e)return;
    const auto before=refusal_sequence.load();if(before>=UINT32_MAX-1)return;
    const auto baseline_full=refusal_baseline_full.load(),baseline_busy=refusal_baseline_busy.load();
    const auto counters=source->stats();const auto at=now();
    if(epoch()!=e || refusal_armed.load()!=e)return; // cancellation must not impersonate active admission pressure
    sa_refusal value{};value.epoch=e;value.qpc=at;value.committed_occurrence=committed.load();
    value.offered=counters.offered;value.refused_full=counters.refused_full;
    value.refused_busy=counters.refused_busy;value.observed=counters.observed;
    value.baseline_full=baseline_full;value.baseline_busy=baseline_busy;
    if(counters.refused_full!=baseline_full)value.cause|=SA_REFUSAL_FULL;
    if(counters.refused_busy!=baseline_busy)value.cause|=SA_REFUSAL_BUSY;
    if(!value.cause)value.cause=SA_REFUSAL_UNCLASSIFIED;
    refusal_sequence.store(before+1);PROBE(4);
    uint32_t words[sizeof(value)/4]{};std::memcpy(words,&value,sizeof value);
    for(unsigned n=1;n<sizeof(value)/4;++n)refusal_words[n].store(words[n]);
    refusal_sequence.store(before+2);latched_epoch=e;
}
bool enter(){
    if(next_sequence>=UINT32_MAX-1){exhausted.store(true);return false;}
    sequence.store(++next_sequence);return true;
}
void leave(){sequence.store(++next_sequence);}
bool read_table(uint32_t e,sa_table &out){
    const auto before=table.sequence.load();if(before&1)return false;
    out.epoch=table.epoch.load();out.rdram_bytes=table.limit.load();out.count=table.count.load();
    if(out.count>RB_STAMP_FIELDS)return false;
    for(unsigned n=0;n<out.count;++n)out.rows[n]={table.offsets[n].load(),table.lengths[n].load()};
    return before==table.sequence.load() && out.epoch==e;
}
int __cdecl capture(const rb_record *record,rb_image *image){
    if(!record||!image)return 0;
    *image={};
    if(!record->epoch||record->epoch!=epoch()){image->status=SA_CANCELLED;return 1;}
    if(ops.surface)ops.surface(ops.user,record);
    if(record->surface.post_vi_origin==record->stamp.vi_origin){image->status=SA_SAME_ORIGIN;return 1;}
    // The renderer swaps only when the origin changed AND no dialog/resize
    // returned early. An origin change with the same swap count means the
    // front buffer still holds the previous picture: never pair it.
    const uint32_t swaps=record->surface.swap_count;
    const bool swapped=!have_swap || swaps!=last_swap_count;
    have_swap=true;last_swap_count=swaps;
    if(!swapped){image->status=SA_NO_SWAP;return 1;}
    const int result=ops.capture(ops.user,record,image);
    if(result && ((image->ownership==RB_IMAGE_SUBMITTED && image->serial)
            || image->status==SA_BOOTSTRAP_OMISSION)){
        // Pool image epoch is a different namespace. Ack the SOURCE epoch.
        // A bootstrap observation acks too: otherwise the first ACTIVE
        // picture inherits every display list since activation and can
        // never be exact.
        ack_epoch.store(record->epoch);
        ack_occurrence.store(record->occurrence);
    }
    return result;
}
int __cdecl complete(const rb_image *image){return image&&ops.completed?ops.completed(ops.user,image):0;}
bool pin(const void *address){
    HMODULE module=nullptr;
    return GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_PIN|GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,
        reinterpret_cast<LPCWSTR>(address),&module)!=FALSE;
}
}
extern "C" int __cdecl sa_configure(HMODULE renderer,const sa_ops *provided){
    if(attempted || !provided || provided->bytes!=sizeof(sa_ops) || provided->version!=SA_VERSION
            || !provided->process_dlist || !provided->update_screen || !provided->current_epoch
            || !provided->vi_origin || !provided->read_ram || !provided->capture || !provided->completed)return 0;
    attempted=true;ops=*provided;
    using Query=const rs_api *(__cdecl *)(uint32_t,uint32_t);
    const auto query=reinterpret_cast<Query>(GetProcAddress(renderer,"SM64ReplaySourceV2"));
    if(!query)return 0;
    const auto api=query(RS_ABI_V2,sizeof(rs_api));
    if(!api || api->bytes!=sizeof(rs_api) || api->version!=RS_ABI_V2 || api->reserved
            || (api->capabilities&(RS_CAP_BOUNDARY|RS_CAP_SOURCE_FORMAT))!=(RS_CAP_BOUNDARY|RS_CAP_SOURCE_FORMAT)
            || !api->configure || !api->stage || !api->update || !api->finish || !api->activate
            || !api->disarm || !api->take || !api->release || !api->stats
            || !api->begin_capture || !api->end_capture)return 0;
    // Setup only; every delegate may outlive CloseDLL and its mutable API table.
    const void *addresses[]={reinterpret_cast<const void*>(query),reinterpret_cast<const void*>(capture),
        reinterpret_cast<const void*>(complete),reinterpret_cast<const void*>(ops.process_dlist),
        reinterpret_cast<const void*>(ops.update_screen),reinterpret_cast<const void*>(ops.current_epoch),
        reinterpret_cast<const void*>(ops.vi_origin),reinterpret_cast<const void*>(ops.read_ram),
        reinterpret_cast<const void*>(ops.capture),reinterpret_cast<const void*>(ops.completed)};
    for(auto address:addresses)if(!pin(address))return 0;
    if(ops.surface&&!pin(reinterpret_cast<const void*>(ops.surface)))return 0;
    const rs_callbacks callbacks{sizeof(rs_callbacks),RS_ABI_V2,{0,0},capture,complete};
    if(!api->configure(&callbacks))return 0;
    source=api;bound.store(true);return 1;
}
extern "C" int __cdecl sa_publish_table(const sa_table *input){
    if(!input || input->bytes!=sizeof(sa_table) || input->version!=SA_VERSION || input->reserved
            || !input->epoch || input->count>RB_STAMP_FIELDS || !input->rdram_bytes || input->rdram_bytes>(8u<<20))return 0;
    for(unsigned n=0;n<input->count;++n){const auto &r=input->rows[n];
        if(!r.length || r.length>128 || (r.offset&3) || (r.length&3)
                || uint64_t(r.offset)+r.length>input->rdram_bytes)return 0;}
    const auto before=table.sequence.load();if((before&1)||before>=UINT32_MAX-1)return 0;
    table.sequence.store(before+1);
    table.epoch.store(input->epoch);table.limit.store(input->rdram_bytes);table.count.store(input->count);
    for(unsigned n=0;n<input->count;++n){table.offsets[n].store(input->rows[n].offset);table.lengths[n].store(input->rows[n].length);}
    table.sequence.store(before+2);return 1;
}
extern "C" void __cdecl sa_process_dlist(void){
    if(!ops.process_dlist)return;
    refresh(epoch());ops.process_dlist(); // freeze the ORIGINAL call's resulting RAM
    const auto e=epoch();refresh(e);if(!e || !enter())return;
    pending.list_qpc=now();sa_table snapshot{};
    pending.table_count=read_table(e,snapshot)?snapshot.count:0;
    std::memset(pending.lengths,0,sizeof(pending.lengths));std::memset(pending.bytes,0,sizeof(pending.bytes));
    for(unsigned n=0;n<pending.table_count;++n){const auto &row=snapshot.rows[n];
        uint8_t copied[128]{};
        if(ops.read_ram(ops.user,row.offset,row.length,copied)){
            pending.lengths[n]=row.length;std::memcpy(pending.bytes+n*128,copied,row.length);}}
    if(pending.lists_since!=UINT32_MAX)++pending.lists_since;
    leave();
}
extern "C" void __cdecl sa_update_screen(void){
    if(!ops.update_screen)return;
    const auto e=epoch();refresh(e);
    if(!e || !enter()){ops.update_screen();last_origin=ops.vi_origin(ops.user);return;}
    pending.vi_origin=last_origin;
    const auto ticket=source->stage(&pending);
    if(!ticket.occurrence)latch_refusal(e);
    const rs_request request{sizeof(rs_request),RS_ABI_V2,{0,0},ticket};
    source->update(&request); // ALWAYS exactly one original VI, even a zero ticket
    PROBE(3);
    source->finish(ticket);
    if(ticket.occurrence){
        committed.store(ticket.occurrence);
        if(ack_occurrence.load()==ticket.occurrence && ack_epoch.load()==e)pending.lists_since=0;
    }else if(next_refusal==UINT32_MAX)exhausted.store(true);
    else refusals.store(++next_refusal);
    last_origin=ops.vi_origin(ops.user);leave();
}
extern "C" void __cdecl sa_reset(void){
    if(!enter())return;
    pending={};seen_epoch=0;last_origin=UINT32_MAX;
    leave();
}
static int probe_transaction(uint64_t published_through,sa_frontier *out,bool inactive){
    if(!out)return 0;*out={};
    const auto first=sequence.load();const auto e=epoch();
    if(!bound.load() || (first&1) || (inactive ? e!=0 : e==0))return 0;
    const auto high=committed.load();const auto refused=refusals.load();if(published_through<high)return 0;
    PROBE(1);const auto qpc=now();PROBE(2);
    if(sequence.load()!=first || epoch()!=e)return 0;
    *out={first,e,refused,0,high,qpc};return 1;
}
extern "C" int __cdecl sa_probe_frontier(uint64_t through,sa_frontier *out){return probe_transaction(through,out,false);}
extern "C" int __cdecl sa_probe_quiescent(uint64_t through,sa_frontier *out){return probe_transaction(through,out,true);}
extern "C" void __cdecl sa_arm_refusal(uint32_t e){
    if(!e || epoch()!=e || refusal_armed.load()==e)return;
    const auto counters=source->stats();if(epoch()!=e)return;
    refusal_armed.store(0); // keep the baseline publication coherent across epochs
    refusal_baseline_full.store(counters.refused_full);refusal_baseline_busy.store(counters.refused_busy);
    refusal_armed.store(e);
}
extern "C" int __cdecl sa_read_refusal(sa_refusal *out){
    if(!out)return 0;*out={};
    for(unsigned attempt=0;attempt<3;++attempt){
        const auto before=refusal_sequence.load();if(!before || (before&1))continue;
        uint32_t words[sizeof(*out)/4]{};words[0]=before;
        for(unsigned n=1;n<sizeof(*out)/4;++n)words[n]=refusal_words[n].load();
        if(before==refusal_sequence.load()){std::memcpy(out,words,sizeof(*out));return 1;}
    }
    return 0;
}
#ifdef SA_TEST_HOST
extern "C" void __cdecl sa_test_set_probe(void (__cdecl *next)(unsigned)){probe=next;}
#endif
