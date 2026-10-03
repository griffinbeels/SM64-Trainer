/* Ghost sound through the real request queue, audio thread and SM64 audio
 * engine into a captured PCM buffer (no device), plus the shared ring the
 * renderer really writes, read by an in-process stand-in for the player. */
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#define PSAPI_VERSION 2
#include <psapi.h>
#include "ghost_audio.h"
#include "audio.h"
#include "decomp/include/PR/ultratypes.h"
#include "decomp/include/audio_defines.h"
#include <algorithm>
#include <atomic>
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
#include <string>
#include <thread>
#include <vector>

static unsigned checks = 0;
static void check(bool ok, const char *message) { ++checks; if (!ok) throw std::runtime_error(message); }

// A device that plays only when the host says a game frame has passed.
struct Capture {
    std::mutex mutex; std::condition_variable changed;
    std::vector<int16_t> pcm;
    uint64_t written = 0, played = 0;
    bool waiting = false, fail_open = false, opened = false, closed = false, open_now = false;
    unsigned opens = 0;
    static Capture &of(void *owner) { return *static_cast<Capture *>(owner); }
    static bool open(void *owner, uint32_t rate) {
        auto &self = of(owner); std::lock_guard<std::mutex> lock(self.mutex);
        self.opened = self.open_now = !self.fail_open && rate == SA_RATE;
        self.opens += self.opened; self.changed.notify_all(); return self.opened;
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
    static void close(void *owner) { // like waveOutReset: what was queued is dropped
        auto &self = of(owner); std::lock_guard<std::mutex> lock(self.mutex);
        self.closed = true; self.open_now = false; self.played = self.written; self.changed.notify_all();
    }
    ghost_audio::Output output() { return {this, open, queued, write, wait, close}; }
    // Refilled, or closed while idle (nothing more is written until a request).
    bool refilled() { return !open_now || (waiting && written - played > ghost_audio::low_water_frames); }
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
    bool is_open() { std::lock_guard<std::mutex> lock(mutex); return open_now; }
    // A request reopened the device and the thread has refilled it.
    void reopened(unsigned opens_before) {
        std::unique_lock<std::mutex> lock(mutex);
        check(changed.wait_for(lock, std::chrono::seconds(5), [&] {
            return opens > opens_before && open_now && waiting && written - played > ghost_audio::low_water_frames;
        }), "a request reopens the device");
    }
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
    // A request to a closed device is handled on the audio thread's own wake,
    // not on the next played frame: wait until every request is accounted for.
    for (int waited = 0; waited < 1000; ++waited) {
        const auto now = ghost_audio::stats();
        if (now.played + now.discarded - before.played - before.discarded >= out.cue_at.size()) break;
        Sleep(1);
    }
    out.stats = ghost_audio::stats();
    out.stats.played -= before.played; out.stats.discarded -= before.discarded;
    out.stats.idle_closes -= before.idle_closes;
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
// One sound of one class from ghost 0, placed, at a volume: everything it
// sounds. A moving sound is requested every frame for a second, as the game
// does while Mario slides; every other class once.
static std::vector<int16_t> run_one(const std::vector<uint8_t> &rom, uint32_t bits, float volume, Place place) {
    Capture capture;
    ghost_audio::control(true, volume);
    check(ghost_audio::start(rom.data(), rom.size(), capture.output()) == ghost_audio::Start::ok, "engine and capture start");
    capture.settle();
    const uint32_t requests = (bits >> SOUNDARGS_SHIFT_BANK) == SOUND_BANK_MOVING ? 30 : 1;
    for (uint32_t frame = 0; frame < 120; ++frame) {
        if (frame >= 5 && frame < 5 + requests) { ghost_audio::place(0, place.position, place.gain); ghost_audio::cue(0, bits); }
        capture.frame();
    }
    ghost_audio::stop();
    check(capture.closed && !ghost_audio::stats().running, "stop closes the device and joins");
    return capture.pcm;
}
// EVERY CLASS OF SOUND A GHOST PLAYS obeys one rule: the ghost volume x the
// ghost's gain (distance from live Mario x its overlap fade, from playback) x
// SM64's own panning and camera-distance falloff. Mario's recordings issue
// sounds in the action, moving, voice, general, object, object-2 and menu
// banks (measured 2026-09-28 over 76 clips); the menu bank (the star and cap
// grab's SOUND_MENU_STAR_SOUND, falling into a pit, entering a pipe) and a
// moving sound at a constant frequency took a fixed, centred volume that
// skipped all but the ghost volume (his report, 2026-09-28).
// `placed` is false for a sound whose own script mixes its pan
// (chan_setpanmix): SOUND_MENU_ENTER_PIPE sounds the same wherever its
// channel is panned, before the fix (1.185 right/left) and after (1.184).
struct SoundClass { const char *name; uint32_t bits; bool placed; };
static const SoundClass sound_classes[] = {
    {"action", SOUND_ACTION_TERRAIN_JUMP, true}, {"moving", SOUND_MOVING_TERRAIN_SLIDE, true},
    {"moving_constant_frequency", SOUND_MOVING_TERRAIN_SLIDE | SOUND_CONSTANT_FREQUENCY, true},
    {"voice", SOUND_MARIO_YAH_WAH_HOO, true}, {"general", SOUND_GENERAL_COIN, true},
    {"object", SOUND_OBJ_BOWSER_SPINNING, true}, {"object2", SOUND_OBJ2_BABY_PENGUIN_YELL, true},
    {"menu_star", SOUND_MENU_STAR_SOUND, true}, {"menu_hole", SOUND_MENU_ENTER_HOLE, true},
    {"menu_pipe", SOUND_MENU_ENTER_PIPE, false},
};
struct ClassResult { double whole, gain, volume, pan, camera; };
static ClassResult measure_class(const std::vector<uint8_t> &rom, const SoundClass &sound) {
    const Place ahead{{0, 0, -300}, 1}, quiet{{0, 0, -300}, .25f}, right{{2000, 0, -500}, 1},
        distant{{0, 0, -5000}, 1};
    const double whole = energy(run_one(rom, sound.bits, 1.0f, ahead));
    const auto panned = run_one(rom, sound.bits, 1.0f, right);
    return {whole, energy(run_one(rom, sound.bits, 1.0f, quiet)) / whole,
            energy(run_one(rom, sound.bits, 0.5f, ahead)) / whole,
            energy(panned, 1) / energy(panned, 0), energy(run_one(rom, sound.bits, 1.0f, distant)) / whole};
}
// Each class against the rule; every class is measured before any refuses,
// so a bypass names each class it reaches.
static std::string check_classes(const std::vector<uint8_t> &rom) {
    std::string json, failed;
    for (const auto &sound : sound_classes) {
        const auto r = measure_class(rom, sound);
        char row[200];
        std::snprintf(row, sizeof(row), "%s\"%s\":{\"gain\":%.4f,\"volume\":%.4f,\"pan\":%.3f,\"camera\":%.4f}",
                      json.empty() ? "" : ",", sound.name, r.gain, r.volume, r.pan, r.camera);
        json += row;
        const bool heard_from_anywhere = (sound.bits & SOUND_NO_VOLUME_LOSS) != 0;
        const char *broken = !(r.whole > 1e9) ? "is silent"
            : !(r.gain > 0.03 && r.gain < 0.1) ? "skips the ghost's gain (distance x overlap)"
            : !(r.volume > 0.24 && r.volume < 0.26) ? "skips the ghost volume"
            : !(!sound.placed || r.pan > 4) ? "does not pan to where the ghost is"
            // SOUND_NO_VOLUME_LOSS is the game's own "heard from anywhere";
            // the ghost's distance from live Mario still reaches it in its gain.
            : !(heard_from_anywhere || r.camera < 0.5) ? "skips the camera-distance falloff" : nullptr;
        if (broken) failed += std::string(failed.empty() ? "" : "; ") + sound.name + " " + broken;
    }
    std::fprintf(stderr, "classes: {%s}\n", json.c_str());
    check(failed.empty(), failed.empty() ? "every sound class obeys the ghost's gain rule" : failed.c_str());
    return json;
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
        check(sa_open(rom.data() + SA_AUDIO_OFFSET, SA_AUDIO_BYTES - 4) == SA_ARGUMENT, "short audio span refused");
        {   auto broken = rom; broken[SA_TBL_OFFSET + 3] = 0xff; // 255 banks: headers leave the region
            check(sa_open(broken.data() + SA_AUDIO_OFFSET, SA_AUDIO_BYTES) == SA_INVALID_ROM, "bank headers bounded"); }
        // The engine keeps the ROM's audio span (~2.3 MB), not the whole 8 MB ROM.
        size_t engine_bytes = 0;
        {   Capture held; PROCESS_MEMORY_COUNTERS_EX before{}, after{};
            GetProcessMemoryInfo(GetCurrentProcess(), reinterpret_cast<PROCESS_MEMORY_COUNTERS *>(&before), sizeof(before));
            ghost_audio::control(true, 1.0f);
            check(ghost_audio::start(rom.data(), rom.size(), held.output()) == ghost_audio::Start::ok, "engine starts");
            held.settle();
            GetProcessMemoryInfo(GetCurrentProcess(), reinterpret_cast<PROCESS_MEMORY_COUNTERS *>(&after), sizeof(after));
            ghost_audio::stop();
            engine_bytes = after.PrivateUsage > before.PrivateUsage ? after.PrivateUsage - before.PrivateUsage : 0;
            check(engine_bytes < 4u << 20, "the running engine holds its audio span, not the ROM"); }
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
        check(mute.stats.played + mute.stats.discarded == 4
              && std::all_of(mute.pcm.begin(), mute.pcm.end(), [](int16_t v) { return v == 0; }),
              "volume 0 is exact silence");
        // At volume 0 nothing is heard, so the device closes after the idle
        // time, and a request that still cannot be heard does not reopen it.
        check(mute.stats.idle_closes == 1 && mute.stats.discarded >= 1, "volume 0 closes the device when idle");

        // Idle close and reopen: after idle_close_frames of silence the thread
        // closes the device and stops ticking; the next request reopens it and
        // sounds in the first tick written, with its whole attack.
        double reopen_latency = -1;
        size_t reopen_onset_frames = SIZE_MAX, reopen_frames_to_close = 0;
        {   Capture capture; const auto before = ghost_audio::stats();
            ghost_audio::control(true, 1.0f);
            check(ghost_audio::start(rom.data(), rom.size(), capture.output()) == ghost_audio::Start::ok, "engine starts");
            capture.settle();
            for (int frame = 0; frame < 5; ++frame) capture.frame();
            const uint64_t first_at = capture.position();
            ghost_audio::cue(0, SOUND_ACTION_TERRAIN_LANDING);
            size_t frames = 0;
            while (capture.is_open() && frames < 400) { capture.frame(); ++frames; }
            reopen_frames_to_close = frames;
            const auto closed = ghost_audio::stats();
            check(!capture.is_open() && closed.idle_closes - before.idle_closes == 1 && !closed.device_open,
                  "silence closes the device");
            // The idle time is counted from the last sound, not from the request.
            check(frames * 1067 > ghost_audio::idle_close_frames && frames * 1067 < ghost_audio::idle_close_frames + 40 * 1067,
                  "closes after the idle time");
            Sleep(100);
            check(ghost_audio::stats().ticks == closed.ticks, "no ticks while closed");
            const unsigned opens = capture.opens;
            const uint64_t reopen_at = capture.position();
            ghost_audio::cue(0, SOUND_ACTION_TERRAIN_LANDING);
            capture.reopened(opens);
            for (int frame = 0; frame < 40; ++frame) capture.frame();
            const auto after = ghost_audio::stats();
            ghost_audio::stop();
            check(after.reopens - before.reopens == 1 && after.underruns == before.underruns,
                  "one reopen, and the fresh device never counts as run dry");
            reopen_latency = after.last_latency_ms;
            check(reopen_latency < 80, "a reopened request is not late");
            const size_t first = first_sound(capture.pcm, first_at), again = first_sound(capture.pcm, reopen_at);
            check(first != SIZE_MAX && again != SIZE_MAX, "both requests sound");
            reopen_onset_frames = again - size_t(reopen_at);
            check(reopen_onset_frames < ghost_audio::tick_capacity, "it sounds in the first tick written");
            const size_t span = 2 * 20 * 1067; // the landing's first 20 frames, sample for sample
            check(2 * again + span <= capture.pcm.size()
                  && std::equal(capture.pcm.begin() + 2 * first, capture.pcm.begin() + 2 * first + span,
                                capture.pcm.begin() + 2 * again), "the reopened sound is whole, not clipped");
        }
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
        const std::string classes = check_classes(rom);

        // THE SHARED RING (ghost_audio.h), the renderer's real output. First with
        // no player: it plays into nothing at the engine's rate, so the thread
        // is fed in real time, closes after 3 s of silence and reopens on a
        // request exactly as it did with waveOut. Then with a player that
        // plays at the device rate: it never finds the ring dry while the
        // renderer holds it open, and what it reads is what the engine made.
        const bool device = true; // the ring needs no device; the player owns the real one
        double smoke_latency = -1, fed_rate = 0, device_reopen_latency = -1, device_close_after_s = -1;
        uint64_t smoke_underruns = 0, player_nonzero = 0, player_frames = 0;
        {
            const auto before = ghost_audio::stats();
            ghost_audio::control(true, 1e-6f);
            LARGE_INTEGER frequency{}, begin{}, end{}, closed_at{};
            QueryPerformanceFrequency(&frequency);
            const auto seconds = [&](const LARGE_INTEGER &a, const LARGE_INTEGER &b) {
                return double(b.QuadPart - a.QuadPart) / double(frequency.QuadPart); };
            check(ghost_audio::start(rom.data(), rom.size(), ghost_audio::shared_ring()) == ghost_audio::Start::ok, "the ring opens");
            const auto opened = ghost_audio::stats();
            QueryPerformanceCounter(&begin);
            for (int i = 0; i < 45; ++i) { Sleep(33); ghost_audio::cue(0, SOUND_ACTION_TERRAIN_LANDING); }
            QueryPerformanceCounter(&end);
            const auto at_end = ghost_audio::stats();
            // Nothing audible was ever written, so the ring closes 3 s after it opened.
            while (ghost_audio::stats().device_open && seconds(begin, closed_at) < 6) { Sleep(10); QueryPerformanceCounter(&closed_at); }
            device_close_after_s = seconds(begin, closed_at);
            const auto closed = ghost_audio::stats();
            check(!closed.device_open && closed.idle_closes - before.idle_closes == 1, "the idle ring closes");
            Sleep(300);
            check(ghost_audio::stats().ticks == closed.ticks, "the closed ring is not ticked");
            fed_rate = double(at_end.ticks - opened.ticks) * 1076.0 / seconds(begin, end);
            check(fed_rate > 30000 && fed_rate < 34500, "with no player the ring is fed in real time");

            // A player, as ghost/sound_player.py does it: attach at the newest
            // frame, hand everything written to its device at once, and publish
            // the device's position as played.
            wchar_t name[80]; swprintf_s(name, L"%ls%lu", GS_RING_PREFIX, GetCurrentProcessId());
            HANDLE mapping = OpenFileMappingW(FILE_MAP_ALL_ACCESS, FALSE, name);
            check(mapping != nullptr, "the player finds the ring by Project64's pid");
            auto *header = static_cast<ghost_audio::RingHeader *>(MapViewOfFile(mapping, FILE_MAP_ALL_ACCESS, 0, 0, 0));
            check(header && !std::memcmp(header->magic, GS_RING_MAGIC, 8) && header->rate == ghost_audio::ring_rate
                  && header->capacity == ghost_audio::ring_frames, "the ring says what it carries");
            wchar_t event[96]; swprintf_s(event, L"%ls_written", name);
            HANDLE written_event = OpenEventW(SYNCHRONIZE, FALSE, event);
            swprintf_s(event, L"%ls_played", name);
            HANDLE played_event = OpenEventW(EVENT_MODIFY_STATE, FALSE, event);
            check(written_event && played_event, "the player opens both events");
            const auto *data = reinterpret_cast<const int16_t *>(header + 1);
            std::atomic<bool> quit_player{false};
            std::atomic<uint64_t> underruns{0}, nonzero{0}, frames_read{0};
            std::thread player([&] {
                int64_t sent = InterlockedCompareExchange64(&header->written, 0, 0);
                double position = double(sent);
                InterlockedExchange64(&header->played, sent);
                header->player_beat = GetTickCount(); header->player_pid = GetCurrentProcessId();
                LARGE_INTEGER last{}; QueryPerformanceCounter(&last);
                bool playing = false;
                while (!quit_player.load()) {
                    WaitForSingleObject(written_event, 5);
                    header->player_beat = GetTickCount();
                    LARGE_INTEGER now{}; QueryPerformanceCounter(&now);
                    const double advance = double(now.QuadPart - last.QuadPart) * ghost_audio::ring_rate / double(frequency.QuadPart);
                    last = now;
                    const int64_t written = InterlockedCompareExchange64(&header->written, 0, 0);
                    for (; sent < written; ++sent) {
                        const auto at = size_t(sent % ghost_audio::ring_frames) * 2;
                        nonzero += (data[at] != 0) + (data[at + 1] != 0); ++frames_read;
                    }
                    if (playing) {
                        position += advance;
                        if (position > double(sent)) {
                            if (header->renderer_open) ++underruns; // dry while the renderer holds it
                            position = double(sent); playing = false;
                        }
                    }
                    if (double(sent) > position) playing = true;
                    InterlockedExchange64(&header->played, int64_t(position));
                    SetEvent(played_event);
                }
                header->player_pid = 0;
            });
            const auto attached = ghost_audio::stats();
            ghost_audio::control(true, 1.0f); // audible now: the player reads the engine's own samples
            ghost_audio::cue(0, SOUND_ACTION_TERRAIN_LANDING);
            while (ghost_audio::stats().played == attached.played) Sleep(1);
            device_reopen_latency = ghost_audio::stats().last_latency_ms;
            for (int i = 0; i < 30; ++i) { Sleep(33); ghost_audio::cue(0, SOUND_ACTION_TERRAIN_LANDING); }
            Sleep(100); // the last request reaches its tick
            const auto stats = ghost_audio::stats();
            ghost_audio::stop();
            quit_player = true; player.join();
            UnmapViewOfFile(header); CloseHandle(mapping); CloseHandle(written_event); CloseHandle(played_event);
            check(stats.reopens - before.reopens == 1 && stats.failed_reopens == before.failed_reopens,
                  "one request reopened the ring");
            smoke_underruns = underruns.load(); player_nonzero = nonzero.load(); player_frames = frames_read.load();
            check(smoke_underruns == 0, "the player never finds the ring dry while it is open");
            check(player_nonzero > 10000, "the player reads the engine's sound");
            check(device_reopen_latency < 80, "a request that reopens the ring is not late");
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
            "\"gain_quarter_energy_ratio\":%.6f,\"camera_5000_energy_ratio\":%.6f,\"second_ghost_routed_identical\":%s,"
            "\"player_nonzero_samples\":%llu,\"player_frames\":%llu,\"engine_private_bytes\":%zu,\"capture_reopen_latency_ms\":%.3f,\"capture_reopen_onset_frames\":%zu,"
            "\"capture_frames_to_close\":%zu,\"device_close_after_s\":%.3f,\"device_reopen_latency_ms\":%.3f,"
            "\"classes\":{%s}}\n",
            checks, full.cue_at.size(), nonzero, half_energy / full_energy, full.stats.max_latency_ms,
            device ? "true" : "false", smoke_latency, stale_full,
            static_cast<unsigned long long>(smoke_underruns), fed_rate,
            right_lr, left_lr, quarter_ratio, far_ratio, routed_identical ? "true" : "false",
            static_cast<unsigned long long>(player_nonzero), static_cast<unsigned long long>(player_frames),
            engine_bytes, reopen_latency, reopen_onset_frames, reopen_frames_to_close,
            device_close_after_s, device_reopen_latency, classes.c_str());
        return 0;
    } catch (const std::exception &error) {
        std::fprintf(stderr, "audio witness: %s (check %u)\n", error.what(), checks);
        ghost_audio::stop();
        return 1;
    }
}
