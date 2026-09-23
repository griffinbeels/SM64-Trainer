/* Ghost sound effects inside Project64. The render thread only hands over
 * recorded sound requests and the playback switch/volume, lock-free; one
 * audio thread owns SM64's audio engine (renderer/model/audio.h) and the
 * output device. Contract and limits: docs/ghost-native-runtime.md. */
#pragma once
#include <stddef.h>
#include <stdint.h>
#include "ghost_tuning.h"

namespace ghost_audio {
constexpr uint32_t queue_slots = 256;
constexpr uint32_t low_water_frames = 1088; // synthesize the next tick at or below this (34 ms)
constexpr uint32_t tick_capacity = 1088;   // SA_TICK_MAX_FRAMES
constexpr uint32_t sources = GP_GHOSTS;    // one per ghost; SA_SOURCES matches (ghost_audio.cpp)
/* After this much silent output (3 s at 32 kHz) the audio thread closes the
 * device and stops ticking until a request can be heard; it reopens on that
 * request, which sounds in the first tick written. */
constexpr uint32_t idle_close_frames = 96000;

/* Render thread only: bounded single-producer queue; never waits/allocates.
 * A full queue, or a ghost index >= sources, drops the request and counts it. */
void cue(uint32_t ghost, uint32_t sound_bits);
/* Render thread only: where that ghost sounds from, relative to the live
 * camera (+x right, -z ahead, as SM64 places its own sounds), and its gain
 * 0..1. The audio thread applies the latest placement at each tick. */
void place(uint32_t ghost, const float camera_relative[3], float gain);
/* Render thread only. playing=false (disabled, suspended, switched off) stops
 * every sound at the next audio tick and discards queued requests. Volume
 * is an exact output scale: 0 writes true silence. */
void control(bool playing, float volume);

/* Output seam: waveOut in the renderer, a captured buffer in tests. All
 * calls come from the audio thread. */
struct Output {
    void *owner;
    bool (*open)(void *owner, uint32_t rate);
    uint32_t (*queued)(void *owner);              // frames written, not yet played
    bool (*write)(void *owner, const int16_t *stereo, uint32_t frames);
    void (*wait)(void *owner, uint32_t milliseconds); // until space frees or timeout
    void (*close)(void *owner);
};
Output wave_out(); // shared-mode waveOut, event driven, no window or focus
}
namespace ghost_playback { struct SoundSink; }
namespace ghost_audio {
/* cue/control as the renderer's playback sink (ghost_link registers it). */
const ghost_playback::SoundSink *sink();

enum class Start { ok, busy, invalid_rom, engine, device, thread };
/* Worker thread. Checks the ROM's audio regions against known SM64 US audio
 * data, copies only that span, and starts the audio thread (engine + device). */
Start start(const uint8_t *rom, size_t bytes, const Output &output);
bool known_audio(const uint8_t *rom, size_t bytes);
/* Worker thread: stops and joins the audio thread; closes engine and device. */
void stop();

struct Stats {
    uint64_t cues, dropped, played, discarded, ticks, silent_ticks;
    double last_latency_ms, max_latency_ms; // request to its first audible sample
    uint32_t running;
    uint64_t underruns; // ticks written after the device had already run dry
    uint32_t device_open;
    uint64_t idle_closes, reopens, failed_reopens;
};
Stats stats();
}
