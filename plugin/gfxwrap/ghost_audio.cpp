#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <mmsystem.h>
#include "ghost_audio.h"
#include "ghost_assets.h"
#include "ghost_playback.h"
#include "audio.h"
#include <atomic>
#include <cmath>
#include <cstring>
#include <vector>
#pragma comment(lib, "winmm.lib")

namespace ghost_audio {
namespace {
struct Request { uint32_t bits; int64_t qpc; };
Request slots[queue_slots];
std::atomic<uint32_t> head{0}, tail{0};            // render writes head, audio reads tail
std::atomic<uint32_t> playing{0}, volume_bits{0}, stop_generation{0};
std::atomic<uint64_t> cues{0}, dropped{0}, played{0}, discarded{0}, ticks{0}, silent_ticks{0};
std::atomic<uint64_t> last_latency{0}, max_latency{0}; // double bits
std::atomic<uint32_t> running{0};

HANDLE thread = nullptr;
std::atomic<bool> quit{false};
Output device{};
std::vector<uint8_t> rom_copy; // handed to the audio thread; freed once opened

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

void __cdecl sink_cue(uint32_t bits) { cue(bits); }
void __cdecl sink_control(uint32_t on, float level) { control(on!=0,level); }
const ghost_playback::SoundSink playback_sink{sink_cue, sink_control};

DWORD WINAPI audio_main(void *) {
    const int32_t opened=sa_open(rom_copy.data(),rom_copy.size());
    SecureZeroMemory(rom_copy.data(),rom_copy.size()); std::vector<uint8_t>().swap(rom_copy);
    if(opened!=SA_OK) return 1;
    if(!device.open(device.owner,SA_RATE)) { sa_close(); return 2; }
    running.store(1);
    const double per_second=frequency();
    int16_t buffer[2*tick_capacity];
    uint32_t seen_stop=stop_generation.load(std::memory_order_acquire);
    while(!quit.load(std::memory_order_acquire)) {
        const uint32_t queued=device.queued(device.owner);
        if(queued>low_water_frames) { device.wait(device.owner,20); continue; }
        const bool on=playing.load(std::memory_order_acquire)!=0;
        const uint32_t generation=stop_generation.load(std::memory_order_acquire);
        if(generation!=seen_stop) { seen_stop=generation; sa_stop_all(); }
        const int64_t tick_time=now();
        // Requests reach this tick's first sample after what the device holds.
        for(uint32_t at=tail.load(std::memory_order_relaxed);at!=head.load(std::memory_order_acquire);++at) {
            const Request request=slots[at%queue_slots];
            tail.store(at+1,std::memory_order_release);
            if(!on) { discarded.fetch_add(1,std::memory_order_relaxed); continue; }
            sa_play(request.bits); played.fetch_add(1,std::memory_order_relaxed);
            const double latency=double(tick_time-request.qpc)*1000.0/per_second+queued*1000.0/SA_RATE;
            store_double(last_latency,latency);
            if(latency>load_double(max_latency)) store_double(max_latency,latency);
        }
        uint32_t frames=0;
        if(sa_tick(queued,buffer,tick_capacity,&frames)!=SA_OK || !frames) break;
        const float level=on ? volume() : 0.0f;
        if(level==0.0f) { std::memset(buffer,0,frames*4); silent_ticks.fetch_add(1,std::memory_order_relaxed); }
        else if(level!=1.0f)
            for(uint32_t i=0;i<2*frames;++i) buffer[i]=int16_t(std::lrintf(float(buffer[i])*level));
        ticks.fetch_add(1,std::memory_order_relaxed);
        if(!device.write(device.owner,buffer,frames)) break;
    }
    running.store(0);
    device.close(device.owner);
    sa_close();
    return 0;
}

// waveOut: blocks of up to one tick, completion signalled by an event.
struct Wave {
    HWAVEOUT handle=nullptr; HANDLE event=nullptr;
    WAVEHDR headers[6]{}; int16_t data[6][2*tick_capacity]{};
} wave;
bool __cdecl wave_open(void *, uint32_t rate) {
    wave.event=CreateEventW(nullptr,FALSE,FALSE,nullptr);
    if(!wave.event) return false;
    WAVEFORMATEX format{}; format.wFormatTag=WAVE_FORMAT_PCM; format.nChannels=2;
    format.nSamplesPerSec=rate; format.wBitsPerSample=16; format.nBlockAlign=4;
    format.nAvgBytesPerSec=rate*4;
    if(waveOutOpen(&wave.handle,WAVE_MAPPER,&format,reinterpret_cast<DWORD_PTR>(wave.event),0,
            CALLBACK_EVENT)!=MMSYSERR_NOERROR) {
        CloseHandle(wave.event); wave.event=nullptr; wave.handle=nullptr; return false;
    }
    for(unsigned i=0;i<6;++i) { wave.headers[i]={}; wave.headers[i].dwFlags=WHDR_DONE; }
    return true;
}
uint32_t __cdecl wave_queued(void *) {
    uint32_t frames=0;
    for(auto &header:wave.headers) if(!(header.dwFlags&WHDR_DONE)) frames+=header.dwBufferLength/4;
    return frames;
}
bool __cdecl wave_write(void *, const int16_t *stereo, uint32_t frames) {
    for(unsigned i=0;i<6;++i) {
        auto &header=wave.headers[i];
        if(!(header.dwFlags&WHDR_DONE)) continue;
        if(header.dwFlags&WHDR_PREPARED) waveOutUnprepareHeader(wave.handle,&header,sizeof(header));
        std::memcpy(wave.data[i],stereo,frames*4);
        header={}; header.lpData=reinterpret_cast<LPSTR>(wave.data[i]); header.dwBufferLength=frames*4;
        return waveOutPrepareHeader(wave.handle,&header,sizeof(header))==MMSYSERR_NOERROR
            && waveOutWrite(wave.handle,&header,sizeof(header))==MMSYSERR_NOERROR;
    }
    return false;
}
void __cdecl wave_wait(void *, uint32_t milliseconds) { WaitForSingleObject(wave.event,milliseconds); }
void __cdecl wave_close(void *) {
    if(wave.handle) {
        waveOutReset(wave.handle);
        for(auto &header:wave.headers)
            if(header.dwFlags&WHDR_PREPARED) waveOutUnprepareHeader(wave.handle,&header,sizeof(header));
        waveOutClose(wave.handle);
    }
    if(wave.event) CloseHandle(wave.event);
    wave.handle=nullptr; wave.event=nullptr;
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

void cue(uint32_t bits) {
    const uint32_t at=head.load(std::memory_order_relaxed);
    cues.fetch_add(1,std::memory_order_relaxed);
    if(at-tail.load(std::memory_order_acquire)>=queue_slots) { dropped.fetch_add(1,std::memory_order_relaxed); return; }
    slots[at%queue_slots]={bits,now()};
    head.store(at+1,std::memory_order_release);
}
void control(bool on, float level) {
    uint32_t bits; std::memcpy(&bits,&level,4);
    volume_bits.store(bits,std::memory_order_release);
    if(playing.exchange(on?1u:0u,std::memory_order_acq_rel) && !on)
        stop_generation.fetch_add(1,std::memory_order_acq_rel);
}
const ghost_playback::SoundSink *sink() { return &playback_sink; }
Output wave_out() { return {nullptr,wave_open,wave_queued,wave_write,wave_wait,wave_close}; }

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
    rom_copy.assign(rom,rom+bytes); device=output; quit.store(false);
    thread=CreateThread(nullptr,0,audio_main,nullptr,0,nullptr);
    if(!thread) { SecureZeroMemory(rom_copy.data(),rom_copy.size()); std::vector<uint8_t>().swap(rom_copy); return Start::thread; }
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
    quit.store(true,std::memory_order_release);
    WaitForSingleObject(thread,INFINITE);
    CloseHandle(thread); thread=nullptr;
}
Stats stats() {
    return {cues.load(),dropped.load(),played.load(),discarded.load(),ticks.load(),silent_ticks.load(),
        load_double(last_latency),load_double(max_latency),running.load()};
}
}
