/* Real IPC worker and asset reader, isolated named page, fake capture/playback.
 * No emulator, graphics context, child process, or visible window. */
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <winioctl.h>
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
struct Handle {
    HANDLE value=nullptr;
    ~Handle(){if(value && value!=INVALID_HANDLE_VALUE)CloseHandle(value);}
};
unsigned checks=0;
void check(bool value,const char* message) {++checks;if(!value)throw std::runtime_error(message);}
struct CartridgeHeader {
    uint8_t* allocation=nullptr;
    uint8_t* value=nullptr;
    explicit CartridgeHeader(const uint8_t* rom) {
        SYSTEM_INFO info{};GetSystemInfo(&info);
        allocation=static_cast<uint8_t*>(VirtualAlloc(nullptr,2*info.dwPageSize,
            MEM_RESERVE|MEM_COMMIT,PAGE_READWRITE));
        check(allocation!=nullptr,"header allocation");
        DWORD old=0;
        check(VirtualProtect(allocation+info.dwPageSize,info.dwPageSize,PAGE_NOACCESS,&old)!=0,
            "header ends at an inaccessible page");
        value=allocation+info.dwPageSize-64;std::memcpy(value,rom,64);
    }
    ~CartridgeHeader(){if(allocation)VirtualFree(allocation,0,MEM_RELEASE);}
};
std::atomic<DWORD> worker_owner{0};
std::atomic<unsigned> wrong_thread{0},binds{0},releases{0},revokes{0},commands{0},models{0},withdrawals{0};
std::atomic<unsigned> prepared_ids[256]{},released_ids[256]{},rom_validations{0};
// Ghost sound engine lifetime: started from the verified ROM, stopped with it.
std::atomic<unsigned> audio_starts{0},audio_stops{0};
bool __cdecl fake_audio_start(const uint8_t *rom,size_t bytes) {
    if(rom && bytes==(8u<<20))++audio_starts;return rom && bytes==(8u<<20);
}
void __cdecl fake_audio_stop() {++audio_stops;}
const ghost_ipc::AudioHooks fake_audio{fake_audio_start,fake_audio_stop};
std::atomic<uint32_t> configuration{0},model_state{GP_MODEL_EMPTY};
std::atomic<uint32_t> pending_record{0},sample_sequence{0};
std::atomic<uint32_t> burst_records{0};
std::atomic<bool> capture_enabled{false},retirement_allowed{true},fail_model{false},fail_budget{false};
std::atomic<bool> intercept_expiry{false},intercept_wait{false},barrier_failed{false};
// The worker pins this DLL until retirement, including a failed witness. Keep
// barriers alive for that whole lifetime rather than closing a pending wait.
Handle expiry_seen,resume_expiry,wait_seen,resume_wait;
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
    uint32_t configured=pending_record.exchange(0);
    const bool continuation=!configured;
    if(continuation) {
        auto remaining=burst_records.load();
        do {if(!remaining)return 0;}while(!burst_records.compare_exchange_weak(remaining,remaining-1));
        configured=configuration.load();
    }
    *out={};out->bytes=sizeof(*out);out->version=GS_VERSION;out->configuration=configured;
    auto& sample=out->sample;sample.bytes=sizeof(sample);sample.version=GC_VERSION;sample.epoch=1;
    sample.sequence=sample.dlist_serial=++sample_sequence;
    sample.valid_fields=GC_VALID_CLOCK|GC_VALID_ACTION|GC_VALID_WARP;
    sample.live_action=0x0c400201;
    sample.global_timer=100;sample.igt=10;sample.level=9;sample.area=1;sample.status=GC_NO_CAMERA;
    LARGE_INTEGER now{};QueryPerformanceCounter(&now);sample.qpc=now.QuadPart;
    sample.clock.valid=GCC_RAW_VALID|GCC_ELAPSED_VALID;sample.clock.raw_igt=10;
    sample.clock.continuity_id=1;
    // Mario's sound requests ride the same portable row.
    sample.valid_fields|=GC_VALID_SOUND;sample.sound.count=2;sample.sound.producer=9;
    sample.sound.bits[0]=0x24228080;sample.sound.bits[1]=0x30008081;
    if(continuation)sample.global_timer=200+sample_sequence.load(); // Paused raw IGT holds elapsed too.
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
    if(fail_budget.load())return GP_BUDGET; // the set does not fit; nothing prepared
    if(h)++prepared_ids[h->clip_id[0]];
    return h && frame && h->record_count==1 && frame->bytes==sizeof(*frame)?GP_OK:GP_INVALID;
}
int32_t __cdecl release_clip(const uint8_t* id) {
    if(!enter_worker())return GP_WRONG_THREAD;
    if(!id)return GP_INVALID;
    ++released_ids[id[0]];return GP_OK;
}
int32_t __cdecl configure(const gp_request* value) {
    if(!enter_worker())return GP_WRONG_THREAD;
    std::lock_guard<std::mutex> lock(command_mutex);
    if(!value || value->revision<=last_revision)return GP_INVALID;
    last_revision=value->revision;command=*value;++commands;model_state=GP_MODEL_RENDER_OWNED;return GP_OK;
}
void __cdecl revoke(uint32_t why) {
    ++revokes;if(model_state.load()!=GP_MODEL_EMPTY)model_state=GP_MODEL_RETIRED;
    if(why==GIPC_EXPIRED && intercept_expiry.exchange(false)) {
        SetEvent(expiry_seen.value);
        if(WaitForSingleObject(resume_expiry.value,2500)!=WAIT_OBJECT_0)barrier_failed=true;
    }
}
void __cdecl withdraw() {++withdrawals;}
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
    out->model_state=model_state.load();
    std::lock_guard<std::mutex> lock(command_mutex);
    out->revision=command.revision;out->owner_token=command.owner_token;
    out->decision=out->model_state==GP_MODEL_RENDER_OWNED?GP_READY:GP_DISABLED;
    return GP_OK;
}
const gs_api capture_api={sizeof(gs_api),GS_VERSION,GS_CAP_POSE|GS_CAP_WORLD_PASS,0,
    capture_configure,capture_disable,capture_read,capture_stats};
const gp_api playback_api={sizeof(gp_api),GP_VERSION,{0,0},prepare_model,prepare_clip,
    configure,revoke,collect,release_worker,playback_status,withdraw,release_clip};

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
            && frame.sample.valid_fields==(GC_VALID_CLOCK|GC_VALID_ACTION|GC_VALID_WARP|GC_VALID_SOUND) && frame.sample.igt==10
            && frame.sample.sound.count==2 && frame.sample.sound.bits[1]==0x30008081 && frame.sample.sound.producer==9
            && !std::memcmp(&frame.sample.pose,&zero,sizeof(zero)),"explicit invalid capture row preserved");
    }
    gipc_status wait(const std::function<bool(const gipc_status&)>& predicate,const char* why,DWORD milliseconds=2500) {
        const auto start=GetTickCount();
        do {auto result=status();if(predicate(result))return result;Sleep(5);}while(GetTickCount()-start<milliseconds);
        auto last=status();std::fprintf(stderr,"last status state=%u reason=%u revision=%llu token=%llu generation=%u\n",
            last.state,last.reason,last.ack_revision,last.ack_token,last.generation);
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
void digest_of(uint8_t* out,const wchar_t* hex) {
    check(std::wcslen(hex)==64,"SHA argument length");
    for(unsigned i=0;i<32;++i){unsigned byte=0;check(swscanf_s(hex+2*i,L"%2x",&byte)==1,"SHA argument");out[i]=uint8_t(byte);}
}
void set_clip(gipc_clip& clip,uint8_t id,const wchar_t* file,const wchar_t* hex,uint32_t hue) {
    clip={};clip.clip_id[0]=id;digest_of(clip.clip_sha256,hex);path(clip.clip_path,file);clip.hue_degrees=hue;
}
// Every field offset, so Python can compare its ctypes mirror to this compiler.
#define GIPC_REQUEST_FIELDS(X) X(owner_pid) X(owner_created_lo) X(owner_created_hi) X(generation) \
    X(token) X(revision) X(heartbeat) X(capture_enabled) X(play_enabled) X(mode) X(seek_igt) \
    X(nudge) X(opacity) X(stop_at_star) X(rom_generation) X(owner_suspended) X(model_id) \
    X(layout) X(rom_path) X(star_action_count) X(star_actions) X(tint_enabled) \
    X(proximity_enabled) X(proximity_distance) X(origin) X(clip_count) X(reserved1) X(clips) \
    X(shadow_enabled) X(occlusion_fade) X(volume) X(reserved3)
#define GIPC_CLIP_FIELDS(X) X(clip_id) X(clip_sha256) X(hue_degrees) X(reserved) X(clip_path)
#define GIPC_STATUS_FIELDS(X) X(magic) X(version) X(bytes) X(producer_pid) X(producer_created_lo) \
    X(producer_created_hi) X(generation) X(rom_generation) X(rom_open) X(state) X(reason) \
    X(configuration) X(epoch) X(record_bytes) X(capabilities) X(qpc_frequency) X(qpc_anchor) \
    X(utc_anchor) X(produced) X(consumed) X(dropped) X(ack_revision) X(ack_token) \
    X(playback_state) X(playback_reason) X(displayed) X(clips_requested) X(clips_prepared) \
    X(clips_visible) X(particles_dropped) X(reserved)
void field(const char* name,size_t offset) {std::printf("\"%s\":%u,",name,unsigned(offset));}
void print_layout() {
    std::printf("\"layout\":{\"page\":%u,\"ring\":%u,\"request\":{",unsigned(sizeof(gipc_page)),
        unsigned(offsetof(gipc_page,records)));
#define REQUEST(name) field(#name,offsetof(gipc_request,name));
#define CLIP(name) field(#name,offsetof(gipc_clip,name));
#define STATUS(name) field(#name,offsetof(gipc_status,name));
    GIPC_REQUEST_FIELDS(REQUEST) std::printf("\"$bytes\":%u},\"clip\":{",unsigned(sizeof(gipc_request)));
    GIPC_CLIP_FIELDS(CLIP) std::printf("\"$bytes\":%u},\"status\":{",unsigned(sizeof(gipc_clip)));
    GIPC_STATUS_FIELDS(STATUS) std::printf("\"$bytes\":%u}},",unsigned(sizeof(gipc_status)));
#undef REQUEST
#undef CLIP
#undef STATUS
}
gp_request last_command(){std::lock_guard<std::mutex> lock(command_mutex);return command;}
void ready(Page& page,gipc_request& r) {
    page.send(r);page.wait([&](const gipc_status& s){return s.ack_token==r.token && s.ack_revision==r.revision
        && s.state==GIPC_READY;},"request becomes ready");
}
void refused(Page& page,gipc_request& r,uint32_t reason) {
    const std::string why="request refusal reason="+std::to_string(reason)
        +" revision="+std::to_string(r.revision)+" token="+std::to_string(r.token);
    page.send(r);page.wait([&](const gipc_status& s){return s.ack_token==r.token && s.ack_revision==r.revision
        && s.state==GIPC_REFUSED && s.reason==reason;},why.c_str());
}
}
namespace ghost_stream {const gs_api* api(){return &capture_api;}}
namespace ghost_playback {const gp_api* api(){return &playback_api;}}
// This IPC witness isolates the real worker from the model; test_native_model
// exercises the actual asset validator, including while a model is render-owned.
extern "C" int32_t sg_model_validate_rom(const uint8_t* bytes,size_t count) {
    ++rom_validations;
    return count==(8u<<20) && bytes && !std::memcmp(bytes+(4u<<20),"ASSETROM",8)
        ? SG_OK : SG_INVALID_ROM;
}

DWORD WINAPI ghost_witness_wait(DWORD count,const HANDLE* events,BOOL all,DWORD milliseconds) {
    if(intercept_wait.exchange(false)) {
        SetEvent(wait_seen.value);
        if(WaitForSingleObject(resume_wait.value,2500)!=WAIT_OBJECT_0)barrier_failed=true;
    }
    return WaitForMultipleObjects(count,events,all,milliseconds);
}

#pragma comment(linker,"/EXPORT:GhostIpcWitness=_ghost_ipc_witness")
extern "C" int __cdecl ghost_ipc_witness(int argc,wchar_t** argv) {
    try {
        check(argc==8,"clip, digest, ROM and two more clip fixture arguments");
        uint8_t digest[32]{};digest_of(digest,argv[2]);
        uint16_t clip_path[GIPC_PATH_CHARS]{},rom_path[GIPC_PATH_CHARS]{};
        path(clip_path,argv[1]);path(rom_path,argv[3]);
        std::vector<uint8_t> bytes;gclip_header header{};
        check(ghost_assets::read(clip_path,GIPC_PATH_CHARS,2048,bytes),"bounded local clip read");
        const uint8_t id[16]={1};
        check(ghost_assets::clip(bytes,digest,id,header),"independent Python digest accepted");
        auto corrupt=bytes;corrupt.back()^=1;
        check(!ghost_assets::clip(corrupt,digest,id,header),"changed payload hash refused");
        const uint8_t wrong_id[16]={2};
        check(!ghost_assets::clip(bytes,digest,wrong_id,header),"wrong clip identity refused");
        {
            // The same clip saved as GCLIP4 (before particles): its own digest,
            // read as GCLIP5 with every sample byte kept and no particles.
            std::vector<uint8_t> v4(bytes.begin(),bytes.begin()+sizeof(gclip_header));
            std::memcpy(v4.data(),"SM64GHO4",8);
            const uint32_t v4_header[]={4,sizeof(gclip_header),652};std::memcpy(v4.data()+8,v4_header,sizeof(v4_header));
            const uint32_t v4_sample_version=5;std::memcpy(v4.data()+8+4*5,&v4_sample_version,4);
            uint32_t count=0;std::memcpy(&count,bytes.data()+20,4); // header.record_count
            for(uint32_t i=0;i<count;++i) {
                const uint8_t* record=bytes.data()+sizeof(gclip_header)+size_t(i)*sizeof(gclip_frame);
                std::vector<uint8_t> old(record,record+652);
                const uint32_t prefix[]={652,4};std::memcpy(old.data(),prefix,8);
                const uint32_t sample[]={636,5};std::memcpy(old.data()+16,sample,8);
                v4.insert(v4.end(),old.begin(),old.end());
            }
            uint8_t v4_digest[32]{};check(ghost_assets::sha256(v4,v4_digest),"GCLIP4 digest");
            gclip_header upgraded{};
            check(ghost_assets::clip(v4,v4_digest,id,upgraded) && upgraded.version==GCLIP_VERSION
                && upgraded.record_bytes==sizeof(gclip_frame) && v4==bytes,"GCLIP4 clip read as GCLIP5");
        }
        check(!ghost_assets::read(clip_path,GIPC_PATH_CHARS,1,corrupt) && corrupt.empty(),"oversized file clears output");
        const uint16_t relative[]={'x',0};
        check(!ghost_assets::read(relative,2,1024,corrupt),"relative file refused");
        // A file too big for this 32-bit process's address space is refused as
        // memory (GIPC_BUDGET), never as a bad file. Sparse: no disk is used.
        {
            wchar_t folder[MAX_PATH]{},name[MAX_PATH]{};
            check(GetTempPathW(MAX_PATH,folder) && GetTempFileNameW(folder,L"gho",0,name),"temporary file");
            HANDLE big=CreateFileW(name,GENERIC_WRITE,0,nullptr,CREATE_ALWAYS,FILE_ATTRIBUTE_TEMPORARY,nullptr);
            DWORD returned=0;LARGE_INTEGER end{};end.QuadPart=0x7F000000;
            check(big!=INVALID_HANDLE_VALUE && DeviceIoControl(big,FSCTL_SET_SPARSE,nullptr,0,nullptr,0,&returned,nullptr)
                && SetFilePointerEx(big,end,nullptr,FILE_BEGIN) && SetEndOfFile(big),"sparse 2 GB file");
            CloseHandle(big);
            uint16_t big_path[GIPC_PATH_CHARS]{};path(big_path,name);
            bool memory=false;
            check(!ghost_assets::read(big_path,GIPC_PATH_CHARS,0xF0000000u,corrupt,&memory) && memory && corrupt.empty(),
                "out of memory is reported as memory");
            memory=false;
            check(!ghost_assets::read(big_path,GIPC_PATH_CHARS,1024,corrupt,&memory) && !memory,
                "an oversized file is not memory");
            check(DeleteFileW(name),"temporary file removed");
        }
        std::vector<uint8_t> rom;
        check(ghost_assets::read(rom_path,GIPC_PATH_CHARS,8u<<20,rom) && rom.size()==(8u<<20),"ROM fixture read");
        // The in-process cartridge fixture starts word-swapped, like Project64.
        for(size_t i=0;i<rom.size();i+=4){std::swap(rom[i],rom[i+3]);std::swap(rom[i+1],rom[i+2]);}
        // Zilmar supplies only this copied 64-byte HEADER, not the ROM allocation.
        CartridgeHeader header_only(rom.data());
        expiry_seen.value=CreateEventW(nullptr,FALSE,FALSE,nullptr);
        resume_expiry.value=CreateEventW(nullptr,FALSE,FALSE,nullptr);
        wait_seen.value=CreateEventW(nullptr,FALSE,FALSE,nullptr);
        resume_wait.value=CreateEventW(nullptr,FALSE,FALSE,nullptr);
        check(expiry_seen.value && resume_expiry.value && wait_seen.value && resume_wait.value,"expiry barriers");
        ghost_ipc::set_audio(&fake_audio);
        Page page;ghost_ipc::rom(true,header_only.value);
        auto initial=page.wait([](const gipc_status& s){return s.generation && s.version==GIPC_VERSION;},"worker publishes ABI");
        gipc_request r{};r.owner_pid=GetCurrentProcessId();FILETIME birth{},unused[3]{};
        check(GetProcessTimes(GetCurrentProcess(),&birth,unused,unused+1,unused+2)!=0,"owner birth");
        r.owner_created_lo=birth.dwLowDateTime;r.owner_created_hi=birth.dwHighDateTime;r.generation=initial.generation;
        r.token=123;r.revision=1;r.capture_enabled=r.play_enabled=1;r.rom_generation=ghost_ipc::rom_generation();
        r.model_id=1;r.clip_count=1;set_clip(r.clips[0],1,argv[1],argv[2],217);r.opacity=0.75f;r.tint_enabled=1;
        r.proximity_enabled=1;r.proximity_distance=120;r.shadow_enabled=1;r.occlusion_fade=1;r.volume=0.625f;
        r.mode=GP_FIXED;
        r.star_action_count=1;r.star_actions[0]=0x1302;r.stop_at_star=1;
        r.layout.bytes=sizeof(gc_layout);r.layout.version=GC_VERSION;
        r.layout.clock.near_zero=30;
        ready(page,r);auto first=last_command();
        check(audio_starts.load()==1 && !audio_stops.load(),"sound engine starts once from the verified ROM");
        page.consume_record();
        check(first.rom_generation==r.rom_generation && first.owner_token==r.token && first.opacity==0.75f
            && first.tint_enabled==1 && first.clip_count==1 && first.clips[0].clip_id[0]==1
            && first.clips[0].hue_degrees==217 && first.star_actions[0]==0x1302,"command fields transported");
        check(first.configuration && first.revision,"capture and playback identities acknowledged");
        check(first.proximity_enabled==1 && first.proximity_distance==120,"proximity crosses actual IPC worker");
        check(first.shadow_enabled==1 && first.volume==0.625f,"shadow switch and volume cross actual IPC worker");
        check(!page.status().displayed,"ready pose is not presentation evidence");
        ghost_ipc::presented(first.revision);
        page.wait([](const gipc_status& s){return s.displayed==1;},"actual matching presentation acknowledged");
        ghost_ipc::presented(first.revision+1);
        page.wait([](const gipc_status& s){return !s.displayed;},"foreign presentation revision refused");
        ghost_ipc::presented(first.revision);
        page.wait([](const gipc_status& s){return s.displayed==1;},"matching presentation restored");
        ghost_ipc::presented(0);
        page.wait([](const gipc_status& s){return !s.displayed;},"clean swap clears display acknowledgement");
        r.revision=2;r.mode=GP_FOLLOW;
        LARGE_INTEGER origin_now{};QueryPerformanceCounter(&origin_now);
        r.origin={GO_STAR,first.configuration,1,100,
            initial.utc_anchor+double(origin_now.QuadPart-initial.qpc_anchor)/double(initial.qpc_frequency),1,9};
        ready(page,r);
        check(last_command().origin.sequence && last_command().origin.continuity==1
            && last_command().origin.offset==10,"worker binds exact source origin");
        const auto bound_origin=last_command().origin.sequence;
        burst_records=GO_HISTORY+10;
        page.wait([](const gipc_status&){return burst_records.load()==0;},"origin ages out of bounded ledger");
        check(last_command().occlusion_fade==1,"occlusion fade on crosses actual IPC worker");
        r.revision=3;r.opacity=0.6f;r.shadow_enabled=0;r.occlusion_fade=0;ready(page,r);
        check(last_command().shadow_enabled==0,"shadow A/B off crosses actual IPC worker");
        check(last_command().occlusion_fade==0,"occlusion A/B off crosses actual IPC worker");
        r.shadow_enabled=1;r.occlusion_fade=1;
        check(last_command().origin.sequence==bound_origin,"tuning reuses resolved origin after history eviction");
        r.revision=4;r.mode=GP_FIXED;r.origin={};ready(page,r);
        const auto before=commands.load();ready(page,r);check(commands.load()==before,"heartbeat is not semantic mutation");
        r.opacity=0.25f;refused(page,r,GIPC_PROTOCOL);
        check(commands.load()==before,"same revision mutation never configured");
        r.revision=5;ready(page,r);const auto five=last_command().revision;
        r.revision=4;refused(page,r,GIPC_PROTOCOL);r.revision=3;refused(page,r,GIPC_PROTOCOL);
        r.revision=4;refused(page,r,GIPC_PROTOCOL);check(last_command().revision==five,"rejected requests cannot lower high-water");
        r.revision=6;ready(page,r);
        r.revision=7;r.play_enabled=0;const auto withdrawn=withdrawals.load();page.send(r);
        page.wait([&](const gipc_status& s){return s.ack_revision==7 && s.state==GIPC_CAPTURING;},"playback stop keeps capture");
        check(withdrawals.load()>withdrawn,"playback disable withdraws without discarding assets");
        // Replacing a selected clip reuses this ROM generation's prepared copy.
        // Removing the fixture image now would make a fresh discovery fail.
        rom[0]=0;
        r.revision=8;r.play_enabled=1;r.clips[0].clip_sha256[0]^=1;refused(page,r,GIPC_FILE);
        r.revision=9;r.clips[0].clip_sha256[0]^=1;ready(page,r);
        // Several ghosts: adding one reads and prepares only that clip, while
        // the applied set keeps drawing (no withdrawal) and the ROM stays cached.
        const unsigned prepared_one=prepared_ids[1].load(),validations=rom_validations.load();
        const unsigned shown=withdrawals.load(),released_one=released_ids[1].load();
        r.revision=10;r.clip_count=2;set_clip(r.clips[1],2,argv[4],argv[5],40);ready(page,r);
        auto set=last_command();
        check(set.clip_count==2 && set.clips[0].clip_id[0]==1 && set.clips[1].clip_id[0]==2
            && set.clips[0].hue_degrees==217 && set.clips[1].hue_degrees==40,"ordered set and per-ghost hue");
        check(prepared_ids[1]==prepared_one && prepared_ids[2]==1,"adding a clip prepares only the new clip");
        check(rom_validations==validations,"adding a clip never re-reads the cartridge");
        check(withdrawals==shown,"applied ghosts keep drawing while a new clip loads");
        auto readiness=page.status();
        check(readiness.clips_requested==2 && readiness.clips_prepared==2,"set readiness published");
        // A set that does not fit says so apart from a bad clip, and shows once it fits.
        fail_budget=true;r.revision=11;r.clip_count=3;set_clip(r.clips[2],3,argv[6],argv[7],300);
        refused(page,r,GIPC_BUDGET);fail_budget=false;ready(page,r);
        check(prepared_ids[1]==prepared_one && prepared_ids[2]==1 && prepared_ids[3]==1,"third clip alone prepared");
        r.revision=12;r.clips[0]=r.clips[1];r.clips[1]=r.clips[2];r.clips[2]={};r.clip_count=2;ready(page,r);
        set=last_command();
        check(released_ids[1]==released_one+1 && !released_ids[2] && !released_ids[3]
            && set.clip_count==2 && set.clips[0].clip_id[0]==2 && set.clips[1].clip_id[0]==3,"removed ghost released");
        check(prepared_ids[2]==1 && prepared_ids[3]==1 && withdrawals==shown,"remaining ghosts retained and shown");
        const unsigned added_prepares=prepared_ids[2]+prepared_ids[3],reprepares=prepared_ids[1]-prepared_one;
        const unsigned removed=released_ids[1]-released_one;
        // Set protocol refusals, each at a fresh revision.
        auto bad=r;bad.revision=13;std::memcpy(bad.clips[1].clip_id,bad.clips[0].clip_id,16);
        refused(page,bad,GIPC_PROTOCOL);
        bad=r;bad.revision=14;std::memset(bad.clips[1].clip_id,0,16);refused(page,bad,GIPC_PROTOCOL);
        bad=r;bad.revision=15;bad.clip_count=0;refused(page,bad,GIPC_PROTOCOL);
        bad=r;bad.revision=16;bad.clip_count=GIPC_GHOSTS+1;refused(page,bad,GIPC_PROTOCOL);
        bad=r;bad.revision=17;bad.clips[1].hue_degrees=360;refused(page,bad,GIPC_PROTOCOL);
        bad=r;bad.revision=18;bad.clips[1].reserved=1;refused(page,bad,GIPC_PROTOCOL);
        bad=r;bad.revision=19;bad.shadow_enabled=2;refused(page,bad,GIPC_PROTOCOL);
        bad=r;bad.revision=20;bad.occlusion_fade=2;refused(page,bad,GIPC_PROTOCOL);
        bad=r;bad.revision=21;bad.volume=1.25f;refused(page,bad,GIPC_PROTOCOL);
        bad=r;bad.revision=22;bad.reserved3=1;refused(page,bad,GIPC_PROTOCOL);
        r.revision=23;ready(page,r);
        r.revision=24;r.owner_created_lo^=1;refused(page,r,GIPC_OWNER_GONE);
        r.owner_created_lo^=1;r.token=124;r.revision=1;ready(page,r);
        // Reproduce the reader owning the page exactly when lease expiry must
        // be published. No new request/event wakes the worker after this race.
        const auto before_expiry=revokes.load();intercept_expiry=true;
        check(WaitForSingleObject(expiry_seen.value,GIPC_LEASE_MS+1500)==WAIT_OBJECT_0,"lease revokes without a heartbeat");
        check(!capture_enabled.load() && revokes.load()>before_expiry,"expired owner stopped internally");
        page.lock();intercept_wait=true;SetEvent(resume_expiry.value);
        const DWORD reached_wait=WaitForSingleObject(wait_seen.value,2500);
        ReleaseMutex(page.mutex.value);SetEvent(resume_wait.value);
        check(reached_wait==WAIT_OBJECT_0 && !barrier_failed.load(),"publication raced a held page lock");
        page.wait([](const gipc_status& s){return s.state==GIPC_REFUSED && s.reason==GIPC_EXPIRED;},
            "unrenewed owner lease expires",GIPC_LEASE_MS+1500);
        r.revision=2;ready(page,r);
        rom[0]=0x40;
        const auto original_generation=r.rom_generation,original_releases=releases.load();
        retirement_allowed=false;ghost_ipc::rom(false);
        page.wait([](const gipc_status& s){return !s.rom_open;},"closed ROM revokes identity");
        Sleep(50);check(releases.load()==original_releases,"worker retained until model retirement");
        retirement_allowed=true;page.stopped();check(releases.load()>original_releases,"worker released after retirement");
        ghost_ipc::rom(true,header_only.value);
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
        // A new ROM generation cannot inherit the prior generation's image.
        rom[0]=0;ghost_ipc::rom(true,header_only.value);
        const auto missing=page.wait([&](const gipc_status& s){return s.generation>restarted.generation;},
            "missing cartridge starts a distinct producer");
        r.generation=missing.generation;r.rom_generation=ghost_ipc::rom_generation();
        r.token=126;r.revision=1;std::memset(r.rom_path,0,sizeof(r.rom_path));
        refused(page,r,GIPC_ROM);
        for(unsigned i=0;i<4;++i){Sleep(25);refused(page,r,GIPC_ROM);}
        rom[0]=0x40;r.revision=2;ready(page,r);
        ghost_ipc::rom(false);page.stopped();
        check(!worker_owner.load() && !wrong_thread.load(),"all worker bindings released");
        check(audio_starts.load()>=1 && audio_stops.load()==audio_starts.load(),"every sound engine stopped with its ROM");
        ghost_ipc::set_audio(nullptr);
        std::printf("{\"ok\":true,\"checks\":%u,\"commands\":%u,\"bindings\":%u,\"releases\":%u,",
            checks,commands.load(),binds.load(),releases.load());
        std::printf("\"multi\":{\"ghosts\":3,\"added_prepares\":%u,\"existing_reprepares\":%u,\"released\":%u},",
            added_prepares,reprepares,removed);
        print_layout();std::printf("\"end\":true}\n");return 0;
    }catch(const std::exception& error){
        intercept_expiry=false;intercept_wait=false;
        SetEvent(resume_expiry.value);SetEvent(resume_wait.value);ghost_ipc::rom(false);
        std::fprintf(stderr,"IPC witness: %s (check %u)\n",error.what(),checks);return 1;}
}
