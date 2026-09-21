/* Real IPC worker and asset reader, isolated named page, fake capture/playback.
 * No emulator, graphics context, child process, or visible window. */
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "ghost_ipc.h"
#include "ghost_assets.h"
#include "ghost_stream.h"
#include "ghost_playback.h"
#include <atomic>
#include <cstdio>
#include <cstring>
#include <cwchar>
#include <functional>
#include <mutex>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
unsigned checks=0;
void check(bool value,const char* message) {++checks;if(!value)throw std::runtime_error(message);}
std::atomic<DWORD> worker_owner{0};
std::atomic<unsigned> wrong_thread{0},binds{0},releases{0},revokes{0},commands{0},models{0};
std::atomic<uint32_t> configuration{0},model_state{GP_MODEL_EMPTY};
std::atomic<uint32_t> pending_record{0},sample_sequence{0};
std::atomic<bool> capture_enabled{false},retirement_allowed{true},fail_model{false};
std::mutex command_mutex;
gp_request command{};
uint64_t last_revision=0;
bool enter_worker() {
    DWORD expected=0,thread=GetCurrentThreadId();
    if(worker_owner.compare_exchange_strong(expected,thread)){++binds;return true;}
    if(expected==thread)return true;
    ++wrong_thread;return false;
}
int __cdecl capture_configure(const gc_layout*) {capture_enabled=true;pending_record=++configuration;return 1;}
void __cdecl capture_disable() {capture_enabled=false;}
int __cdecl capture_read(gs_record* out,uint32_t bytes) {
    if(!out || bytes!=sizeof(*out))return -1;
    const uint32_t configured=pending_record.exchange(0);if(!configured)return 0;
    *out={};out->bytes=sizeof(*out);out->version=GS_VERSION;out->configuration=configured;
    auto& sample=out->sample;sample.bytes=sizeof(sample);sample.version=GC_VERSION;sample.epoch=1;
    sample.sequence=sample.dlist_serial=++sample_sequence;sample.valid_fields=GC_VALID_CLOCK;
    sample.global_timer=100;sample.igt=10;sample.level=9;sample.area=1;sample.status=GC_NO_CAMERA;
    out->world.status=GCLIP_WORLD_READY;return 1;
}
gs_stats __cdecl capture_stats() {
    gs_stats out{};out.bytes=sizeof(out);out.version=GS_VERSION;out.epoch=1;
    out.configuration=configuration.load();out.enabled=capture_enabled.load();return out;
}
int32_t __cdecl prepare_model(uint64_t id,const uint8_t* bytes,uint32_t size) {
    if(!enter_worker())return GP_WRONG_THREAD;
    ++models;
    if(fail_model.load())return GP_MODEL_ERROR;
    if(!id || size!=(8u<<20) || !bytes || bytes[0]!=0x80 || bytes[0x3e]!='E')return GP_INVALID;
    model_state=GP_MODEL_READY;return GP_OK;
}
int32_t __cdecl prepare_clip(const gclip_header* h,const gclip_frame* frame) {
    if(!enter_worker())return GP_WRONG_THREAD;
    return h && frame && h->record_count==1 && frame->bytes==sizeof(*frame)?GP_OK:GP_INVALID;
}
int32_t __cdecl configure(const gp_request* value) {
    if(!enter_worker())return GP_WRONG_THREAD;
    std::lock_guard<std::mutex> lock(command_mutex);
    if(!value || value->revision<=last_revision)return GP_INVALID;
    last_revision=value->revision;command=*value;++commands;model_state=GP_MODEL_RENDER_OWNED;return GP_OK;
}
void __cdecl revoke(uint32_t) {++revokes;if(model_state.load()!=GP_MODEL_EMPTY)model_state=GP_MODEL_RETIRED;}
int32_t __cdecl collect() {
    if(!enter_worker())return GP_WRONG_THREAD;
    if(model_state.load()==GP_MODEL_RETIRED && retirement_allowed.load())model_state=GP_MODEL_EMPTY;
    return model_state.load()==GP_MODEL_RETIRED?GP_DEFERRED:GP_OK;
}
int32_t __cdecl release_worker() {
    if(!enter_worker())return GP_WRONG_THREAD;
    collect();if(model_state.load()!=GP_MODEL_EMPTY)return GP_DEFERRED;
    worker_owner=0;++releases;return GP_OK;
}
int32_t __cdecl playback_status(gp_status* out,uint32_t bytes) {
    if(!out || bytes!=sizeof(*out))return GP_INVALID;
    *out={};out->bytes=sizeof(*out);out->version=GP_VERSION;
    out->model_state=model_state.load();return GP_OK;
}
const gs_api capture_api={sizeof(gs_api),GS_VERSION,GS_CAP_POSE|GS_CAP_WORLD_PASS,0,
    capture_configure,capture_disable,capture_read,capture_stats};
const gp_api playback_api={sizeof(gp_api),GP_VERSION,{0,0},prepare_model,prepare_clip,
    configure,revoke,collect,release_worker,playback_status};

struct Handle {
    HANDLE value=nullptr;
    ~Handle(){if(value && value!=INVALID_HANDLE_VALUE)CloseHandle(value);}
};
struct Page {
    Handle mapping,mutex,wake,producer;
    gipc_page* value=nullptr;
    Page() {
        wchar_t name[160];swprintf_s(name,L"%s%lu",GIPC_PREFIX,GetCurrentProcessId());
        const std::wstring base(name);
        mapping.value=CreateFileMappingW(INVALID_HANDLE_VALUE,nullptr,PAGE_READWRITE,0,sizeof(gipc_page),base.c_str());
        check(mapping.value!=nullptr,"private mapping");
        value=static_cast<gipc_page*>(MapViewOfFile(mapping.value,FILE_MAP_ALL_ACCESS,0,0,sizeof(gipc_page)));
        check(value!=nullptr,"private mapping view");
        mutex.value=CreateMutexW(nullptr,FALSE,(base+L"_mutex").c_str());
        wake.value=CreateEventW(nullptr,FALSE,FALSE,(base+L"_wake").c_str());
        producer.value=CreateMutexW(nullptr,FALSE,(base+L"_producer").c_str());
        check(mutex.value && wake.value && producer.value,"private synchronization");
    }
    ~Page(){if(value)UnmapViewOfFile(value);}
    void lock(){check(WaitForSingleObject(mutex.value,2000)==WAIT_OBJECT_0,"page mutex");}
    gipc_status status(){lock();auto copy=value->status;ReleaseMutex(mutex.value);return copy;}
    void send(gipc_request& request) {
        ++request.heartbeat;lock();value->request=request;ReleaseMutex(mutex.value);SetEvent(wake.value);
    }
    void consume_record() {
        lock();const auto status=value->status;
        const auto frame=value->records[(status.produced-1)%GIPC_SLOTS];
        value->status.consumed=status.produced;ReleaseMutex(mutex.value);SetEvent(wake.value);
        check(status.produced>0 && frame.bytes==sizeof(frame) && frame.version==GCLIP_VERSION
            && frame.configuration==status.configuration && frame.world_status==GCLIP_WORLD_READY,
            "capture value copied into portable ring");
        const gc_pose zero{};
        check(frame.sample.sequence && frame.sample.bytes==sizeof(gc_sample) && frame.sample.version==GC_VERSION
            && frame.sample.valid_fields==GC_VALID_CLOCK && frame.sample.igt==10
            && !std::memcmp(&frame.sample.pose,&zero,sizeof(zero)),"explicit invalid capture row preserved");
    }
    gipc_status wait(const std::function<bool(const gipc_status&)>& predicate,const char* why,DWORD milliseconds=2500) {
        const auto start=GetTickCount();
        do {auto result=status();if(predicate(result))return result;Sleep(5);}while(GetTickCount()-start<milliseconds);
        auto last=status();std::fprintf(stderr,"last status state=%u reason=%u revision=%llu generation=%u\n",
            last.state,last.reason,last.ack_revision,last.generation);
        throw std::runtime_error(why);
    }
    void stopped() {
        wait([](const gipc_status& s){return s.state==GIPC_CLOSED;},"worker closes page");
        check(WaitForSingleObject(producer.value,2000)==WAIT_OBJECT_0,"worker releases producer lifetime");
        ReleaseMutex(producer.value);
    }
};
void path(uint16_t* out,const wchar_t* value) {
    check(std::wcslen(value)<GIPC_PATH_CHARS,"fixture path bound");
    std::memcpy(out,value,(std::wcslen(value)+1)*sizeof(wchar_t));
}
gp_request last_command(){std::lock_guard<std::mutex> lock(command_mutex);return command;}
void ready(Page& page,gipc_request& r) {
    page.send(r);page.wait([&](const gipc_status& s){return s.ack_token==r.token && s.ack_revision==r.revision
        && s.state==GIPC_READY;},"request becomes ready");
}
void refused(Page& page,gipc_request& r,uint32_t reason) {
    page.send(r);page.wait([&](const gipc_status& s){return s.ack_token==r.token && s.ack_revision==r.revision
        && s.state==GIPC_REFUSED && s.reason==reason;},"request refusal");
}
}
namespace ghost_stream {const gs_api* api(){return &capture_api;}}
namespace ghost_playback {const gp_api* api(){return &playback_api;}}

#pragma comment(linker,"/EXPORT:GhostIpcWitness=_ghost_ipc_witness")
extern "C" int __cdecl ghost_ipc_witness(int argc,wchar_t** argv) {
    try {
        check(argc==4,"clip, digest and ROM fixture arguments");
        uint8_t digest[32]{};check(std::wcslen(argv[2])==64,"SHA argument length");
        for(unsigned i=0;i<32;++i){unsigned byte=0;check(swscanf_s(argv[2]+2*i,L"%2x",&byte)==1,"SHA argument");digest[i]=uint8_t(byte);}
        uint16_t clip_path[GIPC_PATH_CHARS]{},rom_path[GIPC_PATH_CHARS]{};
        path(clip_path,argv[1]);path(rom_path,argv[3]);
        std::vector<uint8_t> bytes;gclip_header header{};
        check(ghost_assets::read(clip_path,GIPC_PATH_CHARS,1024,bytes),"bounded local clip read");
        const uint8_t id[16]={1};
        check(ghost_assets::clip(bytes,digest,id,header),"independent Python digest accepted");
        auto corrupt=bytes;corrupt.back()^=1;
        check(!ghost_assets::clip(corrupt,digest,id,header),"changed payload hash refused");
        const uint8_t wrong_id[16]={2};
        check(!ghost_assets::clip(bytes,digest,wrong_id,header),"wrong clip identity refused");
        check(!ghost_assets::read(clip_path,GIPC_PATH_CHARS,1,corrupt) && corrupt.empty(),"oversized file clears output");
        const uint16_t relative[]={'x',0};
        check(!ghost_assets::read(relative,2,1024,corrupt),"relative file refused");
        std::vector<uint8_t> rom;
        check(ghost_assets::read(rom_path,GIPC_PATH_CHARS,8u<<20,rom) && rom.size()==(8u<<20),"ROM fixture read");
        // The in-process cartridge fixture starts word-swapped, like Project64.
        for(size_t i=0;i<rom.size();i+=4){std::swap(rom[i],rom[i+3]);std::swap(rom[i+1],rom[i+2]);}
        Page page;ghost_ipc::rom(true,rom.data());
        auto initial=page.wait([](const gipc_status& s){return s.generation && s.version==GIPC_VERSION;},"worker publishes ABI");
        gipc_request r{};r.owner_pid=GetCurrentProcessId();FILETIME birth{},unused[3]{};
        check(GetProcessTimes(GetCurrentProcess(),&birth,unused,unused+1,unused+2)!=0,"owner birth");
        r.owner_created_lo=birth.dwLowDateTime;r.owner_created_hi=birth.dwHighDateTime;r.generation=initial.generation;
        r.token=123;r.revision=1;r.capture_enabled=r.play_enabled=1;r.rom_generation=ghost_ipc::rom_generation();
        r.model_id=1;r.clip_id[0]=1;r.opacity=0.75f;r.tint_enabled=1;r.hue_degrees=217;
        r.star_action_count=1;r.star_actions[0]=0x1302;r.stop_at_star=1;
        r.layout.bytes=sizeof(gc_layout);r.layout.version=GC_VERSION;
        std::memcpy(r.clip_sha256,digest,32);std::memcpy(r.clip_path,clip_path,sizeof(clip_path));
        ready(page,r);auto first=last_command();
        page.consume_record();
        check(first.rom_generation==r.rom_generation && first.owner_token==r.token && first.opacity==0.75f
            && first.tint_enabled==1 && first.hue_degrees==217 && first.star_actions[0]==0x1302,"command fields transported");
        check(first.configuration && first.revision,"capture and playback identities acknowledged");
        const auto before=commands.load();ready(page,r);check(commands.load()==before,"heartbeat is not semantic mutation");
        r.opacity=0.25f;refused(page,r,GIPC_PROTOCOL);
        check(commands.load()==before,"same revision mutation never configured");
        r.revision=5;ready(page,r);const auto five=last_command().revision;
        r.revision=4;refused(page,r,GIPC_PROTOCOL);r.revision=3;refused(page,r,GIPC_PROTOCOL);
        r.revision=4;refused(page,r,GIPC_PROTOCOL);check(last_command().revision==five,"rejected requests cannot lower high-water");
        r.revision=6;ready(page,r);
        r.revision=7;r.play_enabled=0;const auto revoked=revokes.load();page.send(r);
        page.wait([&](const gipc_status& s){return s.ack_revision==7 && s.state==GIPC_CAPTURING;},"playback stop keeps capture");
        check(revokes.load()>revoked,"playback disable revokes");
        r.revision=8;r.play_enabled=1;r.clip_sha256[0]^=1;refused(page,r,GIPC_FILE);
        r.revision=9;r.clip_sha256[0]^=1;ready(page,r);
        r.revision=10;r.owner_created_lo^=1;refused(page,r,GIPC_OWNER_GONE);
        r.owner_created_lo^=1;r.token=124;r.revision=1;ready(page,r);
        page.wait([](const gipc_status& s){return s.state==GIPC_REFUSED && s.reason==GIPC_EXPIRED;},
            "unrenewed owner lease expires",GIPC_LEASE_MS+1500);
        r.revision=2;ready(page,r);
        const auto original_generation=r.rom_generation,original_releases=releases.load();
        retirement_allowed=false;ghost_ipc::rom(false);
        page.wait([](const gipc_status& s){return !s.rom_open;},"closed ROM revokes identity");
        Sleep(50);check(releases.load()==original_releases,"worker retained until model retirement");
        retirement_allowed=true;page.stopped();check(releases.load()>original_releases,"worker released after retirement");
        ghost_ipc::rom(true,rom.data());
        auto restarted=page.wait([&](const gipc_status& s){return s.generation>initial.generation;},"worker restarted");
        r.generation=restarted.generation;r.rom_generation=ghost_ipc::rom_generation();r.token=125;r.revision=1;
        check(r.rom_generation>original_generation,"ROM generation advances");ready(page,r);
        check(last_command().revision>five && binds.load()>=2 && wrong_thread.load()==0,"worker rebind preserves monotonic playback identity");
        // A terminal model error is latched for one immutable asset identity.
        fail_model=true;r.revision=2;r.model_id=2;refused(page,r,GIPC_MODEL);
        const auto failed_attempts=models.load();
        for(unsigned i=0;i<4;++i){Sleep(25);refused(page,r,GIPC_MODEL);}
        check(models.load()==failed_attempts,"permanent model failure does not loop at worker cadence");
        fail_model=false;r.revision=3;r.model_id=3;ready(page,r);
        // Explicit fixture path remains supported independently of live ROM copying.
        r.revision=4;r.model_id=4;std::memcpy(r.rom_path,rom_path,sizeof(rom_path));ready(page,r);
        ghost_ipc::rom(false);page.stopped();
        check(!worker_owner.load() && !wrong_thread.load(),"all worker bindings released");
        std::printf("{\"ok\":true,\"checks\":%u,\"commands\":%u,\"bindings\":%u,\"releases\":%u}\n",
            checks,commands.load(),binds.load(),releases.load());return 0;
    }catch(const std::exception& error){ghost_ipc::rom(false);
        std::fprintf(stderr,"IPC witness: %s (check %u)\n",error.what(),checks);return 1;}
}
