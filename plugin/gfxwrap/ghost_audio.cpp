#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "ghost_audio.h"
#include "ghost_assets.h"
#include "ghost_playback.h"
#include "audio.h"
#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstring>
#include <vector>

static_assert(SA_SOURCES == ghost_audio::sources, "one engine source per displayed ghost");
static_assert(SA_RATE == ghost_audio::ring_rate, "the ring carries the engine's own rate");
static_assert(ghost_audio::ring_frames >= 4 * ghost_audio::tick_capacity, "two ticks unplayed fit twice");

namespace ghost_audio {
namespace {
struct Request { uint32_t ghost, bits; int64_t qpc; };
Request slots[queue_slots];
// Latest placement per ghost. Fields are separate atomics: a tick may read
// x from one frame and z from the next, a difference nobody can hear.
struct Placement { std::atomic<float> position[3]; std::atomic<float> gain; };
Placement placements[sources];
std::atomic<uint32_t> head{0}, tail{0};            // render writes head, audio reads tail
std::atomic<uint32_t> playing{0}, volume_bits{0}, stop_generation{0};
std::atomic<uint64_t> cues{0}, dropped{0}, played{0}, discarded{0}, ticks{0}, silent_ticks{0}, underruns{0};
std::atomic<uint64_t> last_latency{0}, max_latency{0}; // double bits
std::atomic<uint32_t> running{0}, device_open{0};
std::atomic<uint64_t> idle_closes{0}, reopens{0}, failed_reopens{0};

HANDLE thread = nullptr;
std::atomic<bool> quit{false};
Output device{};
std::vector<uint8_t> audio_copy; // the ROM's audio span, handed to the audio thread; freed once opened
// While the device is closed the audio thread sleeps on `wake` with `idle`
// set; cue() signals it. Both sides order their store before their load
// (seq_cst), so a request pushed as the thread goes idle is never missed.
HANDLE wake = nullptr; // auto-reset; lives for the process, so a late cue never signals a closed handle
std::atomic<uint32_t> idle{0};

int64_t now() { LARGE_INTEGER value{}; QueryPerformanceCounter(&value); return value.QuadPart; }
double frequency() { LARGE_INTEGER value{}; QueryPerformanceFrequency(&value); return double(value.QuadPart); }
void store_double(std::atomic<uint64_t> &target, double value) {
    uint64_t bits; std::memcpy(&bits,&value,8); target.store(bits,std::memory_order_relaxed);
}
double load_double(const std::atomic<uint64_t> &source) {
    const uint64_t bits=source.load(std::memory_order_relaxed); double value; std::memcpy(&value,&bits,8); return value;
}
float volume() {
    const uint32_t bits=volume_bits.load(std::memory_order_acquire); float value; std::memcpy(&value,&bits,4);
    return std::isfinite(value) && value>0 ? (value>1 ? 1.0f : value) : 0.0f;
}

void __cdecl sink_cue(uint32_t ghost, uint32_t bits) { cue(ghost,bits); }
void __cdecl sink_control(uint32_t on, float level) { control(on!=0,level); }
void __cdecl sink_place(uint32_t ghost, const float *position, float gain) { place(ghost,position,gain); }
const ghost_playback::SoundSink playback_sink{sink_cue, sink_control, sink_place};

bool audible() { return playing.load(std::memory_order_acquire)!=0 && volume()>0.0f; }
void discard_requests() {
    for(uint32_t at=tail.load(std::memory_order_relaxed);at!=head.load(std::memory_order_acquire);++at) {
        tail.store(at+1,std::memory_order_release); discarded.fetch_add(1,std::memory_order_relaxed);
    }
}
// Device closed: sleep, not tick, until a request can be heard (or quit).
// Returns with the device open again, or false on quit.
bool reopen_on_request() {
    for(;;) {
        idle.store(1);
        while(!quit.load() && head.load()==tail.load(std::memory_order_relaxed)) WaitForSingleObject(wake,INFINITE);
        idle.store(0);
        if(quit.load()) return false;
        // Inaudible (off or volume 0): nothing to hear, so no device either.
        if(!audible()) { discard_requests(); continue; }
        if(device.open(device.owner,SA_RATE)) { device_open.store(1); reopens.fetch_add(1); return true; }
        failed_reopens.fetch_add(1); discard_requests(); // no device now; the next request retries
    }
}

DWORD WINAPI audio_main(void *) {
    const int32_t opened=sa_open(audio_copy.data(),audio_copy.size());
    SecureZeroMemory(audio_copy.data(),audio_copy.size()); std::vector<uint8_t>().swap(audio_copy);
    if(opened!=SA_OK) return 1;
    if(!device.open(device.owner,SA_RATE)) { sa_close(); return 2; }
    device_open.store(1); running.store(1);
    // Audio refills must not queue behind emulation; the thread mostly sleeps.
    SetThreadPriority(GetCurrentThread(),THREAD_PRIORITY_HIGHEST);
    const double per_second=frequency();
    int16_t buffer[2*tick_capacity];
    uint32_t seen_stop=stop_generation.load(std::memory_order_acquire);
    bool wrote=false;
    uint32_t silent_frames=0;
    while(!quit.load(std::memory_order_acquire)) {
        if(!device_open.load(std::memory_order_relaxed)) {
            if(!reopen_on_request()) break;
            wrote=false; silent_frames=0; // a fresh device starts empty: not an underrun
        }
        const uint32_t queued=device.queued(device.owner);
        if(queued>low_water_frames) { device.wait(device.owner,5); continue; }
        if(!queued && wrote) underruns.fetch_add(1,std::memory_order_relaxed); // it ran dry before this tick
        const bool on=playing.load(std::memory_order_acquire)!=0;
        const uint32_t generation=stop_generation.load(std::memory_order_acquire);
        if(generation!=seen_stop) { seen_stop=generation; sa_stop_all(); }
        const int64_t tick_time=now();
        // Requests reach this tick's first sample after what the device holds.
        for(uint32_t at=tail.load(std::memory_order_relaxed);at!=head.load(std::memory_order_acquire);++at) {
            const Request request=slots[at%queue_slots];
            tail.store(at+1,std::memory_order_release);
            if(!on) { discarded.fetch_add(1,std::memory_order_relaxed); continue; }
            sa_play(request.ghost,request.bits); played.fetch_add(1,std::memory_order_relaxed);
            const double latency=double(tick_time-request.qpc)*1000.0/per_second+queued*1000.0/SA_RATE;
            store_double(last_latency,latency);
            if(latency>load_double(max_latency)) store_double(max_latency,latency);
        }
        // The engine reads each source's position and gain when it mixes, so
        // the latest placement also steers sounds already playing.
        for(uint32_t ghost=0;ghost<sources;++ghost) {
            const auto &from=placements[ghost];
            const float position[3]={from.position[0].load(std::memory_order_relaxed),
                from.position[1].load(std::memory_order_relaxed),from.position[2].load(std::memory_order_relaxed)};
            sa_place(ghost,position,from.gain.load(std::memory_order_relaxed));
        }
        uint32_t frames=0;
        if(sa_tick(queued,buffer,tick_capacity,&frames)!=SA_OK || !frames) break;
        const float level=on ? volume() : 0.0f;
        if(level==0.0f) { std::memset(buffer,0,frames*4); silent_ticks.fetch_add(1,std::memory_order_relaxed); }
        else if(level!=1.0f)
            for(uint32_t i=0;i<2*frames;++i) buffer[i]=int16_t(std::lrintf(float(buffer[i])*level));
        ticks.fetch_add(1,std::memory_order_relaxed);
        if(!device.write(device.owner,buffer,frames)) break;
        wrote=true;
        // Idle: an open waveOut stream woke this HIGHEST-priority thread about
        // 200 times a second and held an audio session for the whole ROM, even
        // at volume 0 (native audit, 2026-09-23). What is queued is silence.
        const bool silent=std::all_of(buffer,buffer+2*frames,[](int16_t sample) { return sample==0; });
        silent_frames=silent ? silent_frames+frames : 0;
        if(silent_frames>=idle_close_frames) {
            device.close(device.owner); device_open.store(0); idle_closes.fetch_add(1);
        }
    }
    running.store(0);
    if(device_open.exchange(0)) device.close(device.owner);
    sa_close();
    return 0;
}

// THE SHARED RING (ghost_audio.h): this side writes, the player plays. The
// waveOut device that used to live here is the player's now
// (src/sm64_events/ghost/sound_player.py), with the same pacing contract:
// queued() is what is written and not yet played, measured from the player's
// own device position, so the audio thread still refills a tick at a time.
struct Ring {
    HANDLE mapping=nullptr, written=nullptr, played=nullptr;
    RingHeader *header=nullptr; int16_t *data=nullptr;
    int64_t consumed=0, clock=0; // played as this side last saw it, and when
} ring;
bool ring_map() {
    if(ring.header) return true; // mapped once for the process; a restart reuses it
    wchar_t name[80]; swprintf_s(name,L"%ls%lu",GS_RING_PREFIX,GetCurrentProcessId());
    const DWORD bytes=DWORD(sizeof(RingHeader)+size_t(ring_frames)*4);
    HANDLE mapping=CreateFileMappingW(INVALID_HANDLE_VALUE,nullptr,PAGE_READWRITE,0,bytes,name);
    if(!mapping) return false;
    const bool existed=GetLastError()==ERROR_ALREADY_EXISTS;
    auto *view=static_cast<uint8_t*>(MapViewOfFile(mapping,FILE_MAP_ALL_ACCESS,0,0,bytes));
    wchar_t event[96]; swprintf_s(event,L"%ls_written",name);
    HANDLE written=CreateEventW(nullptr,FALSE,FALSE,event);
    swprintf_s(event,L"%ls_played",name);
    HANDLE played=CreateEventW(nullptr,FALSE,FALSE,event);
    if(!view || !written || !played) {
        if(view) UnmapViewOfFile(view);
        if(written) CloseHandle(written);
        if(played) CloseHandle(played);
        CloseHandle(mapping); return false;
    }
    auto *header=reinterpret_cast<RingHeader*>(view);
    if(!existed) {
        std::memset(view,0,bytes);
        std::memcpy(header->magic,GS_RING_MAGIC,8);
        header->version=GS_RING_VERSION; header->rate=ring_rate; header->capacity=ring_frames;
    } else if(std::memcmp(header->magic,GS_RING_MAGIC,8) || header->version!=GS_RING_VERSION
              || header->rate!=ring_rate || header->capacity!=ring_frames) {
        UnmapViewOfFile(view); CloseHandle(written); CloseHandle(played); CloseHandle(mapping); return false;
    }
    ring.mapping=mapping; ring.written=written; ring.played=played; ring.header=header;
    ring.data=reinterpret_cast<int16_t*>(view+sizeof(RingHeader));
    return true;
}
int64_t ring_written() { return InterlockedCompareExchange64(&ring.header->written,0,0); }
bool player_attached() {
    const uint32_t pid=ring.header->player_pid, beat=ring.header->player_beat;
    return pid && GetTickCount()-beat<ring_player_timeout_ms;
}
bool __cdecl ring_open(void *, uint32_t rate) {
    if(rate!=ring_rate || !ring_map()) return false;
    ring.consumed=ring_written(); ring.clock=now();
    InterlockedExchange(reinterpret_cast<volatile LONG*>(&ring.header->renderer_open),1);
    return true;
}
uint32_t __cdecl ring_queued(void *) {
    const int64_t written=ring_written(), at=now();
    if(player_attached()) ring.consumed=InterlockedCompareExchange64(&ring.header->played,0,0);
    else ring.consumed+=int64_t(double(at-ring.clock)*ring_rate/frequency()); // into nothing, at rate
    ring.clock=at;
    if(ring.consumed>written) ring.consumed=written;
    const int64_t queued=written-ring.consumed;
    return queued>int64_t(ring_frames) ? ring_frames : uint32_t(queued);
}
bool __cdecl ring_write(void *, const int16_t *stereo, uint32_t frames) {
    if(frames>ring_frames) return false;
    const int64_t at=ring_written();
    const uint32_t first=uint32_t(at%ring_frames), head_frames=std::min(frames,ring_frames-first);
    std::memcpy(ring.data+size_t(first)*2,stereo,size_t(head_frames)*4);
    std::memcpy(ring.data,stereo+size_t(head_frames)*2,size_t(frames-head_frames)*4);
    InterlockedExchange64(&ring.header->written,at+frames); // full barrier: the samples land first
    SetEvent(ring.written);
    return true;
}
void __cdecl ring_wait(void *, uint32_t milliseconds) { WaitForSingleObject(ring.played,milliseconds); }
void __cdecl ring_close(void *) {
    if(ring.header) InterlockedExchange(reinterpret_cast<volatile LONG*>(&ring.header->renderer_open),0);
}

// SHA-256 of the regions the engine parses: stock SM64 US and the Usamune US
// practice ROM share CTL/TBL/SEQ; Usamune changes only the bank-set table.
struct Region { uint32_t offset, bytes; const char *digests[2]; };
const Region regions[] = {
    {SA_CTL_OFFSET,SA_TBL_OFFSET-SA_CTL_OFFSET,{"a536f5f2a88d642fedfd67fb38b51475998ae5a83a5347e3064285f8f19c8fee",nullptr}},
    {SA_TBL_OFFSET,SA_SEQ_OFFSET-SA_TBL_OFFSET,{"d7ee2287c6e0201f6f947ef0b085b7c1dc15c24db312f99734ead3437f9eb864",nullptr}},
    {SA_SEQ_OFFSET,SA_BANKS_OFFSET-SA_SEQ_OFFSET,{"cb6fc92d8471bfc4b00bafc72d475c8b766c4b83015f0afeaeca019795988e1a",nullptr}},
    {SA_BANKS_OFFSET,SA_BANKS_BYTES,{"b516e8cc50039af0d96e1ea2d9eedb35f1f63c2ff7a846e1fe137a005a924510",
                                     "524c0be0aa96485e4c55225ec6f2f39d3acc67a5f9e481bef8d93a93839c38b3"}},
};
bool matches(const uint8_t digest[32], const char *hex) {
    if(!hex) return false;
    for(unsigned i=0;i<32;++i) {
        unsigned value=0; if(sscanf_s(hex+2*i,"%2x",&value)!=1 || value!=digest[i]) return false;
    }
    return true;
}
}

void cue(uint32_t ghost, uint32_t bits) {
    const uint32_t at=head.load(std::memory_order_relaxed);
    cues.fetch_add(1,std::memory_order_relaxed);
    if(ghost>=sources || at-tail.load(std::memory_order_acquire)>=queue_slots) {
        dropped.fetch_add(1,std::memory_order_relaxed); return;
    }
    slots[at%queue_slots]={ghost,bits,now()};
    head.store(at+1);
    if(idle.load()) SetEvent(wake); // never waits; only while the device is closed
}
void place(uint32_t ghost, const float camera_relative[3], float gain) {
    if(ghost>=sources || !camera_relative) return;
    for(unsigned axis=0;axis<3;++axis)
        if(!std::isfinite(camera_relative[axis])) return; // keep the last good placement
    auto &to=placements[ghost];
    for(unsigned axis=0;axis<3;++axis) to.position[axis].store(camera_relative[axis],std::memory_order_relaxed);
    to.gain.store(std::isfinite(gain) && gain>0 ? (gain>1 ? 1.0f : gain) : 0.0f,std::memory_order_relaxed);
}
void control(bool on, float level) {
    uint32_t bits; std::memcpy(&bits,&level,4);
    volume_bits.store(bits,std::memory_order_release);
    if(playing.exchange(on?1u:0u,std::memory_order_acq_rel) && !on)
        stop_generation.fetch_add(1,std::memory_order_acq_rel);
}
const ghost_playback::SoundSink *sink() { return &playback_sink; }
Output shared_ring() { return {nullptr,ring_open,ring_queued,ring_write,ring_wait,ring_close}; }

bool known_audio(const uint8_t *rom, size_t bytes) {
    if(!rom || bytes!=SA_ROM_BYTES) return false;
    for(const auto &region:regions) {
        std::vector<uint8_t> part(rom+region.offset,rom+region.offset+region.bytes);
        uint8_t digest[32]{};
        if(!ghost_assets::sha256(part,digest)
                || !(matches(digest,region.digests[0]) || matches(digest,region.digests[1]))) return false;
    }
    return true;
}
Start start(const uint8_t *rom, size_t bytes, const Output &output) {
    if(thread) return Start::busy;
    if(!known_audio(rom,bytes)) return Start::invalid_rom;
    if(!output.open || !output.queued || !output.write || !output.wait || !output.close) return Start::device;
    if(!wake && !(wake=CreateEventW(nullptr,FALSE,FALSE,nullptr))) return Start::thread;
    audio_copy.assign(rom+SA_AUDIO_OFFSET,rom+SA_AUDIO_OFFSET+SA_AUDIO_BYTES); device=output; quit.store(false);
    for(auto &placement:placements) { // a new engine starts every ghost centred, at full gain
        for(auto &axis:placement.position) axis.store(0.0f);
        placement.gain.store(1.0f);
    }
    thread=CreateThread(nullptr,0,audio_main,nullptr,0,nullptr);
    if(!thread) { SecureZeroMemory(audio_copy.data(),audio_copy.size()); std::vector<uint8_t>().swap(audio_copy); return Start::thread; }
    // Report an engine/device failure to the caller instead of a silent thread.
    while(!running.load() && WaitForSingleObject(thread,1)==WAIT_TIMEOUT) {}
    if(!running.load()) {
        DWORD code=0; GetExitCodeThread(thread,&code); CloseHandle(thread); thread=nullptr;
        return code==2 ? Start::device : Start::engine;
    }
    return Start::ok;
}
void stop() {
    if(!thread) return;
    quit.store(true);
    SetEvent(wake);
    WaitForSingleObject(thread,INFINITE);
    CloseHandle(thread); thread=nullptr;
}
Stats stats() {
    return {cues.load(),dropped.load(),played.load(),discarded.load(),ticks.load(),silent_ticks.load(),
        load_double(last_latency),load_double(max_latency),running.load(),underruns.load(),
        device_open.load(),idle_closes.load(),reopens.load(),failed_reopens.load()};
}
}
