/* Ghost sound through the real request queue, audio thread and SM64 audio
 * engine into a captured PCM buffer (no device), plus an optional waveOut
 * smoke that only ever plays silence. */
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <mmsystem.h>
#include "ghost_audio.h"
#include "audio.h"
#include "decomp/include/PR/ultratypes.h"
#include "decomp/include/audio_defines.h"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iterator>
#include <mutex>
#include <stdexcept>
#include <vector>

static unsigned checks = 0;
static void check(bool ok, const char *message) { ++checks; if (!ok) throw std::runtime_error(message); }

// A device that plays only when the host says a game frame has passed.
struct Capture {
    std::mutex mutex; std::condition_variable changed;
    std::vector<int16_t> pcm;
    uint64_t written = 0, played = 0;
    bool waiting = false, fail_open = false, opened = false, closed = false;
    static Capture &of(void *owner) { return *static_cast<Capture *>(owner); }
    static bool open(void *owner, uint32_t rate) {
        auto &self = of(owner); std::lock_guard<std::mutex> lock(self.mutex);
        self.opened = !self.fail_open && rate == SA_RATE; return self.opened;
    }
    static uint32_t queued(void *owner) {
        auto &self = of(owner); std::lock_guard<std::mutex> lock(self.mutex);
        return uint32_t(self.written - self.played);
    }
    static bool write(void *owner, const int16_t *stereo, uint32_t frames) {
        auto &self = of(owner); std::lock_guard<std::mutex> lock(self.mutex);
        self.pcm.insert(self.pcm.end(), stereo, stereo + 2 * frames);
        self.written += frames; self.changed.notify_all(); return true;
    }
    static void wait(void *owner, uint32_t milliseconds) {
        auto &self = of(owner); std::unique_lock<std::mutex> lock(self.mutex);
        const auto before = self.played; self.waiting = true; self.changed.notify_all();
        self.changed.wait_for(lock, std::chrono::milliseconds(milliseconds), [&] { return self.played != before; });
        self.waiting = false;
    }
    static void close(void *owner) {
        auto &self = of(owner); std::lock_guard<std::mutex> lock(self.mutex); self.closed = true;
    }
    ghost_audio::Output output() { return {this, open, queued, write, wait, close}; }
    bool refilled() { return waiting && written - played > ghost_audio::low_water_frames; }
    // One game frame of audio leaves the device; wait until the thread refills.
    void frame(uint32_t frames = 1067) {
        std::unique_lock<std::mutex> lock(mutex);
        played = std::min(written, played + frames); waiting = false; changed.notify_all();
        check(changed.wait_for(lock, std::chrono::seconds(5), [&] { return refilled(); }), "audio thread refills");
    }
    void settle() {
        std::unique_lock<std::mutex> lock(mutex);
        check(changed.wait_for(lock, std::chrono::seconds(5), [&] { return refilled(); }), "audio thread starts");
    }
    uint64_t position() { std::lock_guard<std::mutex> lock(mutex); return written; }
};

struct Result {
    std::vector<int16_t> pcm; std::vector<uint64_t> cue_at; ghost_audio::Stats stats;
    uint64_t stop_at = 0, resume_at = 0;
};
struct Place { float position[3]; float gain; };
// Cues at fixed game frames from one ghost, placed camera-relative; the
// other ghost of the pair (0/1) is placed at `other` but stays silent.
// Returns the PCM and where each cue was issued.
static Result run(const std::vector<uint8_t> &rom, float volume, bool stop_midway = false,
                  Place place = {{0, 0, 0}, 1}, uint32_t ghost = 0, const Place *other = nullptr) {
    Capture capture;
    const auto before = ghost_audio::stats();
    ghost_audio::control(true, volume);
    check(ghost_audio::start(rom.data(), rom.size(), capture.output()) == ghost_audio::Start::ok, "engine and capture start");
    capture.settle();
    const uint32_t schedule[][2] = {{10, SOUND_MARIO_YAH_WAH_HOO}, {40, SOUND_ACTION_TERRAIN_LANDING},
                                    {70, SOUND_MARIO_YAHOO_WAHA_YIPPEE}, {100, SOUND_ACTION_METAL_LANDING}};
    Result out;
    for (uint32_t frame = 0; frame < 150; ++frame) {
        for (auto &cue : schedule) if (cue[0] == frame) {
            out.cue_at.push_back(capture.position());
            if (other) ghost_audio::place(ghost ^ 1, other->position, other->gain);
            ghost_audio::place(ghost, place.position, place.gain); ghost_audio::cue(ghost, cue[1]);
        }
        // Frames 72..90 sit inside the long voice cue of frame 70.
        if (frame == 72) out.stop_at = capture.position();
        if (frame == 90) out.resume_at = capture.position();
        if (stop_midway && frame == 72) ghost_audio::control(false, volume);
        if (stop_midway && frame == 90) ghost_audio::control(true, volume);
        capture.frame();
    }
    out.stats = ghost_audio::stats();
    out.stats.played -= before.played;
    ghost_audio::stop();
    check(capture.closed && !ghost_audio::stats().running, "stop closes the device and joins");
    out.pcm = capture.pcm;
    return out;
}
// Summed square of one channel (0 left, 1 right) or of both (2).
static double energy(const std::vector<int16_t> &pcm, unsigned channel = 2) {
    double sum = 0;
    for (size_t i = 0; i < pcm.size(); ++i) if (channel == 2 || i % 2 == channel) sum += double(pcm[i]) * pcm[i];
    return sum;
}
static size_t first_sound(const std::vector<int16_t> &pcm, uint64_t from_frame) {
    for (size_t i = size_t(2 * from_frame); i < pcm.size(); ++i) if (pcm[i]) return i / 2;
    return SIZE_MAX;
}

int main(int argc, char **argv) {
    try {
        check(argc == 2, "usage: ghost_audio_host rom");
        std::ifstream file(argv[1], std::ios::binary);
        std::vector<uint8_t> rom((std::istreambuf_iterator<char>(file)), {});
        check(rom.size() == SA_ROM_BYTES && rom[0] == 0x80, "big-endian US ROM");
        check(ghost_audio::known_audio(rom.data(), rom.size()), "known SM64 audio regions");
        for (uint32_t offset : {SA_CTL_OFFSET + 0x100, SA_TBL_OFFSET + 0x1000, SA_SEQ_OFFSET + 0x10, SA_BANKS_OFFSET}) {
            auto changed = rom; changed[offset] ^= 0x40;
            check(!ghost_audio::known_audio(changed.data(), changed.size()), "altered audio data refused");
            Capture unused;
            check(ghost_audio::start(changed.data(), changed.size(), unused.output()) == ghost_audio::Start::invalid_rom
                  && !unused.opened, "engine never parses unknown audio data");
        }
        check(sa_open(rom.data(), rom.size() - 4) == SA_ARGUMENT, "short ROM refused");
        {   auto broken = rom; broken[SA_TBL_OFFSET + 3] = 0xff; // 255 banks: headers leave the region
            check(sa_open(broken.data(), broken.size()) == SA_INVALID_ROM, "bank headers bounded"); }
        {   Capture refusing; refusing.fail_open = true;
            check(ghost_audio::start(rom.data(), rom.size(), refusing.output()) == ghost_audio::Start::device,
                  "no device: sound stays off");
            check(!ghost_audio::stats().running && sa_play(0, 1) == SA_CLOSED, "no thread or engine left behind"); }

        const auto full = run(rom, 1.0f);
        check(full.stats.played == 4, "every scheduled cue played once");
        check(first_sound(full.pcm, 0) >= full.cue_at[0], "nothing sounds before the first request");
        for (size_t i = 0; i < full.cue_at.size(); ++i) {
            // The request reaches the first tick written after it, nothing later.
            const auto onset = first_sound(full.pcm, full.cue_at[i]);
            check(onset != SIZE_MAX && onset < full.cue_at[i] + 2 * ghost_audio::tick_capacity,
                  "each cue sounds within the next tick");
        }
        const auto half = run(rom, 0.5f);
        check(half.pcm.size() == full.pcm.size(), "same schedule, same length");
        size_t nonzero = 0, mismatched = 0;
        double full_energy = 0, half_energy = 0;
        for (size_t i = 0; i < full.pcm.size(); ++i) {
            nonzero += full.pcm[i] != 0;
            mismatched += std::abs(2 * int(half.pcm[i]) - int(full.pcm[i])) > 1;
            full_energy += double(full.pcm[i]) * full.pcm[i]; half_energy += double(half.pcm[i]) * half.pcm[i];
        }
        check(nonzero > 10000 && mismatched == 0, "0.5 is exactly half of every sample");
        const auto mute = run(rom, 0.0f);
        check(mute.stats.played == 4 && std::all_of(mute.pcm.begin(), mute.pcm.end(), [](int16_t v) { return v == 0; }),
              "volume 0 is exact silence");
        const auto stopped = run(rom, 1.0f, true);
        // From the first tick written after the switch-off until the resume.
        const uint64_t quiet_from = stopped.stop_at + 2 * ghost_audio::tick_capacity, quiet_to = stopped.resume_at;
        const auto sounding = [&](const std::vector<int16_t> &pcm) {
            size_t count = 0; for (uint64_t i = 2 * quiet_from; i < 2 * quiet_to; ++i) count += pcm[size_t(i)] != 0; return count;
        };
        check(quiet_to > quiet_from && sounding(full.pcm) > 1000, "the voice cue still sounds there when left on");
        check(sounding(stopped.pcm) == 0, "switching off silences within one tick");
        check(first_sound(stopped.pcm, stopped.cue_at[3]) != SIZE_MAX, "sound resumes for new requests");
        // Switching off also ends the engine's sounds: nothing stale returns on resume.
        size_t stale_full = 0, stale_stopped = 0;
        for (uint64_t i = 2 * (stopped.resume_at + 2 * ghost_audio::tick_capacity); i < 2 * stopped.cue_at[3]; ++i) {
            stale_full += full.pcm[size_t(i)] != 0; stale_stopped += stopped.pcm[size_t(i)] != 0;
        }
        check(stale_stopped == 0, "no stale sound after resuming");

        // Placement: SM64's own panning and camera-distance falloff, then the
        // ghost's gain; each ghost is its own source to the engine.
        const auto right = run(rom, 1.0f, false, {{2000, 0, -500}, 1});
        const auto left = run(rom, 1.0f, false, {{-2000, 0, -500}, 1});
        const double right_lr = energy(right.pcm, 1) / energy(right.pcm, 0);
        const double left_lr = energy(left.pcm, 1) / energy(left.pcm, 0);
        check(right_lr > 4 && left_lr < 0.25, "a ghost to the right sounds right, to the left left");
        const auto quarter = run(rom, 1.0f, false, {{0, 0, 0}, .25f});
        const double quarter_ratio = energy(quarter.pcm) / energy(full.pcm);
        check(quarter_ratio > 0.055 && quarter_ratio < 0.07, "gain 0.25 is a quarter of the amplitude");
        const auto distant = run(rom, 1.0f, false, {{0, 0, -5000}, 1});
        const double far_ratio = energy(distant.pcm) / energy(full.pcm);
        check(far_ratio < 0.5, "the engine's own camera-distance falloff applies");
        // Each ghost is its own source: ghost 1 on the right, with ghost 0
        // placed left and silent, sounds exactly like ghost 0 on the right.
        const Place left_place{{-2000, 0, -500}, 1};
        const auto routed = run(rom, 1.0f, false, {{2000, 0, -500}, 1}, 1, &left_place);
        const bool routed_identical = routed.pcm == right.pcm;
        check(routed.stats.played == 4 && routed_identical, "each ghost sounds from its own placement");
        {   const auto before = ghost_audio::stats(); const float nowhere[3] = {0, 0, 0};
            ghost_audio::cue(ghost_audio::sources, 1); ghost_audio::place(ghost_audio::sources, nowhere, 1);
            check(ghost_audio::stats().dropped - before.dropped == 1, "a ghost index past the sources drops"); }

        // Optional device smoke: silence only, skipped (stated) without a device.
        const bool device = waveOutGetNumDevs() > 0;
        double smoke_latency = -1, fed_rate = 0;
        uint64_t smoke_underruns = 0;
        if (device) {
            const auto before = ghost_audio::stats();
            ghost_audio::control(true, 0.0f);
            LARGE_INTEGER frequency{}, begin{}, end{};
            QueryPerformanceFrequency(&frequency);
            check(ghost_audio::start(rom.data(), rom.size(), ghost_audio::wave_out()) == ghost_audio::Start::ok, "waveOut opens");
            const auto opened = ghost_audio::stats();
            QueryPerformanceCounter(&begin);
            for (int i = 0; i < 90; ++i) { Sleep(33); ghost_audio::cue(0, SOUND_ACTION_TERRAIN_LANDING); }
            QueryPerformanceCounter(&end);
            const auto at_end = ghost_audio::stats();
            Sleep(100); // the last request reaches its tick
            auto stats = ghost_audio::stats();
            stats.ticks = at_end.ticks;
            ghost_audio::stop();
            check(stats.played - before.played >= 90 && stats.silent_ticks - before.silent_ticks > 60,
                  "device fed real ticks of silence");
            // The device never runs dry, and is fed at its own rate (~32000 frames/s).
            smoke_underruns = stats.underruns - before.underruns;
            fed_rate = double(stats.ticks - opened.ticks) * 1076.0
                / (double(end.QuadPart - begin.QuadPart) / double(frequency.QuadPart));
            check(smoke_underruns == 0, "device never runs dry between ticks");
            check(fed_rate > 30000 && fed_rate < 34500, "device fed in real time");
            smoke_latency = stats.max_latency_ms;
        }
        // The queue is bounded and never blocks its producer (no audio thread now).
        const auto before = ghost_audio::stats();
        for (int i = 0; i < 300; ++i) ghost_audio::cue(0, SOUND_ACTION_TERRAIN_LANDING);
        check(ghost_audio::stats().dropped - before.dropped == 300 - ghost_audio::queue_slots, "full queue drops and counts");
        std::printf("{\"ok\":true,\"checks\":%u,\"cues\":%zu,\"nonzero_samples\":%zu,"
            "\"half_energy_ratio\":%.6f,\"capture_max_latency_ms\":%.3f,\"device\":%s,\"device_max_latency_ms\":%.3f,"
            "\"stale_samples_when_left_on\":%zu,\"device_underruns\":%llu,\"device_fed_frames_per_second\":%.0f,"
            "\"right_placed_right_left_energy\":%.4f,\"left_placed_right_left_energy\":%.4f,"
            "\"gain_quarter_energy_ratio\":%.6f,\"camera_5000_energy_ratio\":%.6f,\"second_ghost_routed_identical\":%s}\n",
            checks, full.cue_at.size(), nonzero, half_energy / full_energy, full.stats.max_latency_ms,
            device ? "true" : "false", smoke_latency, stale_full,
            static_cast<unsigned long long>(smoke_underruns), fed_rate,
            right_lr, left_lr, quarter_ratio, far_ratio, routed_identical ? "true" : "false");
        return 0;
    } catch (const std::exception &error) {
        std::fprintf(stderr, "audio witness: %s (check %u)\n", error.what(), checks);
        ghost_audio::stop();
        return 1;
    }
}
