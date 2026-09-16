#include "stamp_adapter.h"
#include <atomic>
#include <stdio.h>
#include <cstring>
#include <thread>
#define CHECK(x) do{if(!(x)){fprintf(stderr,"stamp adapter line %d: %s\n",__LINE__,#x);ExitProcess(1);}}while(0)
namespace {
const rs_api *source=nullptr;
std::atomic<uint32_t> gate{0};unsigned dlist_calls=0,reads=0,surfaces=0,captures=0;
unsigned char ram[256]{};bool read_failure=false,ready=true,completed=false;
rb_record last{};uint64_t published=0,image_serial=0;
void (__cdecl *original_update)();uint32_t (__cdecl *origin)();
void (__cdecl *scenario)(unsigned,unsigned,int);void (__cdecl *rom)(int);
void (__cdecl *source_pause)(HANDLE,HANDLE);
struct Event {HANDLE h=CreateEventW(nullptr,TRUE,FALSE,nullptr);Event(){CHECK(h);}~Event(){CloseHandle(h);}
 void set(){CHECK(SetEvent(h));}void wait(){CHECK(WaitForSingleObject(h,5000)==WAIT_OBJECT_0);}};
void __cdecl process(){++dlist_calls;++ram[0];ram[128]=static_cast<unsigned char>(ram[0]+7);}
uint32_t __cdecl active(void*){return gate.load();}
uint32_t __cdecl vi(void*){return origin();}
int __cdecl read(void*,uint32_t offset,uint32_t length,uint8_t*out){
 ++reads;CHECK(uint64_t(offset)+length<=sizeof(ram));
 if(read_failure&&offset==128){out[0]=99;return 0;}std::memcpy(out,ram+offset,length);return 1;}
void __cdecl surface(void*,const rb_record*){++surfaces;}
int __cdecl capture(void*,const rb_record *r,rb_image *image){
 ++captures;last=*r;if(!ready){*image={};return 1;}
 *image={++image_serial,0,999,RB_IMAGE_SUBMITTED,0};return 1; // pool epoch intentionally differs
}
int __cdecl complete(void*,const rb_image*){return completed?1:0;}
void table(uint32_t epoch){sa_table t{};t.bytes=sizeof(t);t.version=SA_VERSION;t.epoch=epoch;
 t.rdram_bytes=sizeof(ram);t.count=2;t.rows[0]={0,4};t.rows[1]={128,4};CHECK(sa_publish_table(&t));}
void activate(){const auto e=source->activate();CHECK(e);table(e);gate.store(e);}
const rb_record *take(){const auto r=source->take();CHECK(r);published=r->occurrence;return r;}
void release(const rb_record*r){CHECK(source->release({r->occurrence,r->slot}));}
void next(unsigned delta=1,unsigned swaps=1,bool has_surface=true){scenario(delta,swaps,has_surface);sa_update_screen();}
void normal(){
 // No DList is still a real occurrence, explicitly zero/inexact.
 next(1,0);auto r=take();CHECK(r->stamp.lists_since==0 && r->image.epoch==999);release(r);
 sa_process_dlist();CHECK(reads==2);next(1,3);r=take();
 CHECK(r->stamp.bytes[0]==1 && r->stamp.bytes[128]==8 && r->stamp.lists_since==1);
 CHECK(r->surface.swap_count==3 && r->image.ownership==RB_IMAGE_SUBMITTED);release(r);
 // Origin moved, swap count did not: the front buffer is still the last
 // picture (config dialog / resize early return). An omission, never a pair.
 sa_process_dlist();sa_process_dlist();next(1,0);r=take();
 CHECK(r->image.ownership==RB_IMAGE_NONE && r->image.status==SA_NO_SWAP && r->surface.swap_count==3);release(r);
 // The next real swap carries both lists since the last acked picture.
 next(1,2);r=take();CHECK(r->stamp.bytes[0]==3 && r->stamp.lists_since==2 && r->image.ownership==RB_IMAGE_SUBMITTED);release(r);
 next(1,1);r=take();CHECK(r->stamp.lists_since==0 && r->image.ownership==RB_IMAGE_SUBMITTED);release(r);
}
void omissions(){
 sa_process_dlist();ready=false;next();auto r=take();CHECK(r->image.ownership==RB_IMAGE_NONE);release(r);
 sa_process_dlist();ready=true;next(0,3);r=take();
 CHECK(r->image.ownership==RB_IMAGE_NONE && r->image.status==SA_SAME_ORIGIN && captures==1);release(r);
 sa_process_dlist();next();r=take();CHECK(r->stamp.lists_since==3 && r->stamp.bytes[0]==3);release(r);
 sa_process_dlist();next(1,0,false);r=take();CHECK(r->outcome==RB_SURFACE_FAILED);release(r);
 sa_process_dlist();next();r=take();CHECK(r->stamp.lists_since==2);release(r);
 CHECK(surfaces==4); // valid same-origin surface still bootstraps metadata
}
void epochs(){
 sa_process_dlist();gate.store(0);source->disarm();const auto before=reads;
 sa_process_dlist();next();CHECK(reads==before && !source->take());
 activate();next(0);auto r=take();CHECK(r->image.status==SA_SAME_ORIGIN);release(r);
 next();r=take();CHECK(r->stamp.lists_since==0 && !r->stamp.list_qpc && !r->stamp.table_count);release(r);
 sa_process_dlist();activate();next();r=take();CHECK(r->stamp.lists_since==0 && !r->stamp.table_count);release(r);
 sa_process_dlist();sa_reset();next();r=take();CHECK(r->stamp.lists_since==0 && !r->stamp.list_qpc);release(r);
}
void invalid(){
 sa_table t{};t.bytes=sizeof(t);t.version=SA_VERSION;t.epoch=gate.load();t.rdram_bytes=256;t.count=1;
 t.rows[0]={250,12};CHECK(!sa_publish_table(&t));t.rows[0]={0,129};CHECK(!sa_publish_table(&t));
 read_failure=true;sa_process_dlist();next();auto r=take();
 CHECK(r->stamp.table_count==2 && r->stamp.lengths[0]==4 && r->stamp.lengths[1]==0);
 for(unsigned n=128;n<256;++n)CHECK(r->stamp.bytes[n]==0);release(r);
 read_failure=false;sa_process_dlist();next();r=take();CHECK(r->stamp.lengths[1]==4 && r->stamp.bytes[128]==9);release(r);
}
void custody(){
 sa_process_dlist();next();auto r=take();completed=false;
 const rb_ticket held{r->occurrence,r->slot};CHECK(!source->release(held));
 next();auto other=take();CHECK(other->stamp.lists_since==0);completed=true;release(other);CHECK(source->release(held));
}
void capacity(){
 const rb_record *held[RB_SLOTS]{};
 for(unsigned i=0;i<RB_SLOTS;++i){sa_process_dlist();next();held[i]=take();}
 sa_frontier before{},after{};CHECK(sa_probe_frontier(published,&before));
 const auto previous_origin=origin();sa_process_dlist();next();
 CHECK(origin()==previous_origin+1 && !source->take());
 CHECK(sa_probe_frontier(published,&after));
 CHECK(after.refusal_generation==before.refusal_generation+1);
 CHECK(after.committed_occurrence==before.committed_occurrence);
 release(held[0]);sa_process_dlist();next();auto r=take();
 CHECK(r->stamp.lists_since==2);release(r);
 for(unsigned i=1;i<RB_SLOTS;++i)release(held[i]);
}
Event *entered=nullptr,*resume=nullptr;unsigned pause_phase=0;
void __cdecl probe(unsigned phase){if(phase==pause_phase){entered->set();resume->wait();}}
void refusal_diagnostics(){
 sa_refusal first{},again{};CHECK(!sa_read_refusal(&first));CHECK(!sa_read_refusal(nullptr));
 const rb_record *held[RB_SLOTS]{};
 for(auto &r:held){sa_process_dlist();next();r=take();}
 const auto through=published;next();CHECK(!sa_read_refusal(&first)); // tolerated bootstrap pressure
 sa_arm_refusal(gate.load());next();CHECK(sa_read_refusal(&first));
 CHECK(first.epoch==gate.load() && first.cause==SA_REFUSAL_FULL && first.qpc>0);
 CHECK(first.committed_occurrence==through && first.refused_full==first.baseline_full+1);
 CHECK(first.refused_busy==first.baseline_busy);
 next();CHECK(sa_read_refusal(&again) && !std::memcmp(&first,&again,sizeof first));
 for(auto r:held)release(r);
 gate.store(0);source->disarm();next();
 CHECK(sa_read_refusal(&again) && !std::memcmp(&first,&again,sizeof first));
 activate();CHECK(sa_read_refusal(&again) && again.epoch!=gate.load());sa_arm_refusal(gate.load());
 rb_stamp stamp{};const auto staged=source->stage(&stamp);CHECK(staged.occurrence);
 next();CHECK(sa_read_refusal(&again));
 CHECK(again.epoch==gate.load() && again.epoch!=first.epoch && again.cause==SA_REFUSAL_BUSY);
 CHECK(again.refused_busy==again.baseline_busy+1 && again.refused_full==again.baseline_full);
 source->finish(staged);release(take());
 gate.store(0);source->disarm();activate();sa_arm_refusal(gate.load());
 for(auto &r:held){sa_process_dlist();next();r=take();}
 Event in,go;entered=&in;resume=&go;pause_phase=4;sa_test_set_probe(probe);
 std::thread writer([]{next();});in.wait();
 sa_refusal torn{};CHECK(!sa_read_refusal(&torn) && !torn.epoch);
 go.set();writer.join();sa_test_set_probe(nullptr);
 CHECK(sa_read_refusal(&torn) && torn.epoch==gate.load() && torn.cause==SA_REFUSAL_FULL);
 for(auto r:held)release(r);
 printf("refusal diagnostic full=%u busy=%u retained=%lld current_epoch=%u\n",
     first.refused_full,again.refused_busy,first.qpc,torn.epoch);
}
void frontier(unsigned phase){
 sa_process_dlist();next();auto r=take();const auto capture_qpc=r->surface.boundary_qpc;
 sa_frontier f{};CHECK(!sa_probe_frontier(0,&f));release(r);
 CHECK(sa_probe_frontier(published,&f) && f.qpc>=capture_qpc);
 const auto before=f.sequence;sa_reset();CHECK(sa_probe_frontier(published,&f) && f.sequence>before);
 if(phase==1||phase==2){
  Event in,go;entered=&in;resume=&go;pause_phase=phase;sa_test_set_probe(probe);
  int accepted=-1;std::thread worker([&]{sa_frontier candidate{};accepted=sa_probe_frontier(published,&candidate);});
  in.wait();next();r=take();release(r);go.set();worker.join();CHECK(!accepted);sa_test_set_probe(nullptr);
 }else{
  Event in,go;entered=&in;resume=&go;
  if(phase==3){pause_phase=3;sa_test_set_probe(probe);}else source_pause(in.h,go.h);
  std::thread producer([]{next();});in.wait();CHECK(!sa_probe_frontier(UINT64_MAX,&f));go.set();producer.join();
  sa_test_set_probe(nullptr);source_pause(nullptr,nullptr);r=take();release(r);
 }
 CHECK(sa_probe_frontier(published,&f));next();r=take();CHECK(r->surface.boundary_qpc>=f.qpc);release(r);
}
void quiescence(){
 sa_frontier f{};CHECK(!sa_probe_quiescent(UINT64_MAX,&f));
 Event in,go;entered=&in;resume=&go;pause_phase=3;sa_test_set_probe(probe);
 sa_process_dlist();std::thread producer([]{next();});in.wait();
 gate.store(0);source->disarm();CHECK(!sa_probe_quiescent(UINT64_MAX,&f));
 go.set();producer.join();sa_test_set_probe(nullptr);
 CHECK(!sa_probe_quiescent(published,&f));
 auto r=take();release(r);CHECK(sa_probe_quiescent(published,&f) && f.epoch==0);
 CHECK(!sa_probe_frontier(published,&f));
 Event entered2,resume2;entered=&entered2;resume=&resume2;pause_phase=1;sa_test_set_probe(probe);
 int accepted=-1;std::thread worker([&]{sa_frontier candidate{};accepted=sa_probe_quiescent(published,&candidate);});
 entered2.wait();activate();resume2.set();worker.join();sa_test_set_probe(nullptr);CHECK(!accepted);
}
void cancellation(){
 Event in,go;entered=&in;resume=&go;pause_phase=3;sa_test_set_probe(probe);
 sa_process_dlist();std::thread producer([]{next();});in.wait();
 gate.store(0);source->disarm();completed=false;go.set();producer.join();sa_test_set_probe(nullptr);
 auto r=take();CHECK(r->image.ownership==RB_IMAGE_SUBMITTED);
 const rb_ticket held{r->occurrence,r->slot};CHECK(!source->release(held));
 activate();next();auto other=take();CHECK(other->stamp.lists_since==0 && !other->stamp.table_count);
 completed=true;release(other);CHECK(source->release(held));
}
template<class T>T symbol(HMODULE module,const char*name){auto p=GetProcAddress(module,name);CHECK(p);return reinterpret_cast<T>(p);}
}
int __cdecl main(int argc,char **argv){
 CHECK(argc==3);auto module=LoadLibraryA(argv[1]);CHECK(module);
 using Query=const rs_api*(__cdecl *)(uint32_t,uint32_t);
 source=symbol<Query>(module,"SM64ReplaySourceV2")(RS_ABI_V2,sizeof(rs_api));CHECK(source);
 original_update=symbol<decltype(original_update)>(module,"TestUpdate");origin=symbol<decltype(origin)>(module,"TestOrigin");
 scenario=symbol<decltype(scenario)>(module,"TestScenario");rom=symbol<decltype(rom)>(module,"TestRom");
 source_pause=symbol<decltype(source_pause)>(module,"TestPause");
 const sa_ops ops{sizeof(sa_ops),SA_VERSION,nullptr,process,original_update,active,vi,read,surface,capture,complete};
 const bool late=!strcmp(argv[2],"late");if(late)rom(1);
 CHECK(sa_configure(module,&ops)==!late);CHECK(!sa_configure(module,&ops));
 if(!late)rom(1);
 completed=true;
 if(late){gate.store(123);sa_process_dlist();next();CHECK(!reads && !captures);}
 else {activate();const char *mode=argv[2];
  if(!strcmp(mode,"quiescence"))quiescence();else if(!strcmp(mode,"normal"))normal();else if(!strcmp(mode,"omissions"))omissions();
  else if(!strcmp(mode,"epochs"))epochs();else if(!strcmp(mode,"invalid"))invalid();
  else if(!strcmp(mode,"custody"))custody();else if(!strcmp(mode,"frontier_before"))frontier(1);
  else if(!strcmp(mode,"capacity"))capacity();else if(!strcmp(mode,"refusal"))refusal_diagnostics();
  else if(!strcmp(mode,"cancellation"))cancellation();
  else if(!strcmp(mode,"frontier_after"))frontier(2);else if(!strcmp(mode,"frontier_finish"))frontier(3);
  else if(!strcmp(mode,"frontier_source"))frontier(4);else CHECK(false);}
 gate.store(0);source->disarm();rom(0);sa_reset();rom(2);CHECK(FreeLibrary(module));
 sa_frontier final{};CHECK(!sa_probe_frontier(UINT64_MAX,&final));
 printf("stamp adapter passed: %s\n",argv[2]);return 0;
}
