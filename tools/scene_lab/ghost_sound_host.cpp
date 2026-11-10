/* The Scene viewer's ghost sound (M4 round 5, lab only): one ghost's recorded sound requests
 * through SM64's own audio engine, the in-game ghost's (renderer/model/audio.h, the staged and
 * patched libsm64 copy), into PCM. ghost_sound.py drives it; one process serves a scene.
 *
 *   ghost_sound_host.exe
 *   stdin, once:       "ROM1", u32 bytes, the ROM (big-endian .z64)
 *   stdin, per ghost:  "GSR1", u32 ticks, u32 cues, then per tick {f32 x, y, z, f32 gain, u32 count},
 *                      then the cues' sound bits (u32 each) in tick order
 *   stdout, per ghost: i32 status (0, or the engine's SA_* error), u32 frames, u32 frames per tick,
 *                      int16 stereo PCM at SA_RATE
 *
 * A tick is one game frame of sound, as in the game: its cues sound from its first sample, its
 * placement (camera-relative position, gain) steers everything that source plays. Each ghost
 * gets a fresh engine, so ghosts never cut one another off. The engine refuses any ROM whose
 * audio regions do not hash to the known US or JP audio (ghost_audio::known_audio), as in the
 * game. No device, window or file: stdin and stdout only. */
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <fcntl.h>
#include <io.h>
#include "ghost_audio.h"
#include "audio.h"
#include <cmath>
#include <cstdio>
#include <cstring>
#include <vector>

namespace {
bool read_all(void *to, size_t bytes) {
    auto *at = static_cast<uint8_t *>(to);
    while (bytes) {
        const size_t got = fread(at, 1, bytes, stdin);
        if (!got) return false;
        at += got; bytes -= got;
    }
    return true;
}
bool write_all(const void *from, size_t bytes) { return fwrite(from, 1, bytes, stdout) == bytes; }

struct Tick { float position[3]; float gain; uint32_t count; };
constexpr uint32_t most_ticks = 30u * 60u * 30u;    // 30 minutes of game frames
constexpr uint32_t most_cues = 8u * most_ticks;     // GC_SOUND_EVENTS a frame
}

int main(int argc, char **argv) {
    _setmode(_fileno(stdin), _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);
    (void)argc; (void)argv;
    char magic[4];
    uint32_t rom_bytes = 0;
    if (!read_all(magic, 4) || std::memcmp(magic, "ROM1", 4) || !read_all(&rom_bytes, 4) || rom_bytes != SA_ROM_BYTES)
        return 2;
    std::vector<uint8_t> rom(rom_bytes);
    if (!read_all(rom.data(), rom.size())) return 2;
    if (!ghost_audio::known_audio(rom.data(), rom.size())) { fprintf(stderr, "not a known SM64 audio ROM\n"); return 3; }
    const sa_regions &regions = rom[0x3e] == 'J' ? SA_JP : SA_US;
    const std::vector<uint8_t> audio(rom.begin() + regions.ctl, rom.begin() + regions.ctl + SA_SPAN(regions));
    std::vector<Tick> ticks;
    std::vector<uint32_t> cues, lengths;
    std::vector<int16_t> pcm;
    int16_t buffer[2 * SA_TICK_MAX_FRAMES];
    for (;;) {
        uint32_t header[2];
        if (!read_all(magic, 4)) return 0;                       // EOF: done
        if (std::memcmp(magic, "GSR1", 4) || !read_all(header, 8) || header[0] > most_ticks || header[1] > most_cues)
            return 4;
        ticks.resize(header[0]);
        cues.resize(header[1]);
        if ((header[0] && !read_all(ticks.data(), ticks.size() * sizeof(Tick)))
                || (header[1] && !read_all(cues.data(), cues.size() * 4)))
            return 4;
        uint64_t total = 0;
        for (const auto &tick : ticks) total += tick.count;
        if (total != header[1]) return 4;
        lengths.assign(ticks.size(), 0);
        pcm.clear();
        int32_t status = sa_open(audio.data(), audio.size(), &regions);
        // The engine paces itself by what a device still holds: a device that plays one game
        // frame (SA_RATE / 30) a tick and holds the target keeps its long-run rate SA_RATE.
        const double per_tick = SA_RATE / 30.0;
        uint64_t produced = 0;
        size_t next_cue = 0;
        for (size_t k = 0; status == SA_OK && k < ticks.size(); ++k) {
            const Tick &tick = ticks[k];
            const float position[3] = {tick.position[0], tick.position[1], tick.position[2]};
            if (std::isfinite(position[0]) && std::isfinite(position[1]) && std::isfinite(position[2]))
                sa_place(0, position, std::isfinite(tick.gain) ? tick.gain : 0.0f);
            for (uint32_t i = 0; i < tick.count; ++i) sa_play(0, cues[next_cue++]);
            const double held = SA_TICK_TARGET_FRAMES + double(produced) - k * per_tick;
            uint32_t frames = 0;
            status = sa_tick(held > 0 ? uint32_t(held) : 0u, buffer, SA_TICK_MAX_FRAMES, &frames);
            lengths[k] = frames;
            pcm.insert(pcm.end(), buffer, buffer + 2 * frames);
            produced += frames;
        }
        sa_close();                                             // a closed engine answers SA_CLOSED: fine
        const int32_t reply_status = status;
        const uint32_t frames = uint32_t(produced);
        if (!write_all(&reply_status, 4) || !write_all(&frames, 4)
                || (!lengths.empty() && !write_all(lengths.data(), lengths.size() * 4))
                || (!pcm.empty() && !write_all(pcm.data(), pcm.size() * 2)) || fflush(stdout))
            return 5;
    }
}
