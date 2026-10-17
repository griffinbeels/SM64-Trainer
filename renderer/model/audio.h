/* Ghost sound effects through the pinned libsm64 copy of SM64's own audio
 * engine. Upstream keeps the engine in globals: one engine per process, and
 * every call from the one thread that opened it. No device, file or thread. */
#pragma once
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

enum { SA_OK = 0, SA_ARGUMENT = -1, SA_INVALID_ROM = -2, SA_BUSY = -3,
       SA_CLOSED = -4, SA_MEMORY = -5 };
#define SA_RATE 32000u
/* One tick is one game frame of sound: two blocks of 528 or 544 stereo frames. */
#define SA_TICK_MAX_FRAMES 1088u
#define SA_TICK_TARGET_FRAMES 1100u /* queued below this asks for the longer tick */

/* The regions the engine parses, in each practice cartridge: CTL (banks),
 * TBL (samples), SEQ (sequences, 0 the sound player) and the bank-set table
 * (u16 offsets, then each sequence's bank list), padded to SA_BANKS_BYTES.
 * The seq headers and bank sets are bounds-checked here; the instrument and
 * sample tables inside CTL are parsed by upstream code without bounds, so the
 * caller must first prove these bytes are known audio data (ghost_audio.cpp
 * hashes each region). The regions are one contiguous span, and the engine
 * reads nothing else: about 2.3 MB of US's 8 MB ROM, 1.9 MB of JP's.
 *
 * JP's data is JP's own, at its own offsets (found by their seqfile headers
 * in SM64_USAMUNE_v1.93j.z64, 2026-10-17): US added voices (Mario's
 * triple-jump yahoo/waha/yippee, "doh", "hello", "so long-a Bowser", Peach's
 * lines) and red-coin and secret chimes, so US's TBL is 0x67580
 * bytes longer and its voice (2) and menu (7) banks hold 0x40 sounds to JP's
 * 0x30; US also added sequence 0x22. The engine itself is US's, which plays
 * JP's data as JP's: its sequence player is the same code under both
 * versions, a tempo byte runs 0.3% faster than on JP, and the voice and menu
 * banks are bounded at JP's 0x30 while JP's data is loaded. */
#define SA_ROM_BYTES 0x800000u
#define SA_BANKS_BYTES 0xA0u
typedef struct sa_regions { uint32_t ctl, tbl, seq, banks; uint8_t voice_menu_sounds; } sa_regions;
static const sa_regions SA_US = {0x57B720u, 0x593560u, 0x7B0860u, 0x7CC620u, 0x40};
static const sa_regions SA_JP = {0x579140u, 0x590200u, 0x745F80u, 0x761B40u, 0x30};
#define SA_SPAN(r) ((r).banks + SA_BANKS_BYTES - (r).ctl)

/* Copies the big-endian ROM's audio span (SA_SPAN(*regions) bytes from
 * regions->ctl); owns that copy and every parse until close. */
int32_t sa_open(const uint8_t *audio, size_t bytes, const sa_regions *regions);
#define SA_SOURCES 16u /* one per displayed ghost (GP_GHOSTS) */
/* Where a source sounds from, relative to the camera as SM64 places its own
 * sounds (+x right, +y up, -z ahead), and its gain 0..1 on top of the engine's
 * own distance falloff. Takes effect at the next tick, also for sounds already
 * playing. Every source starts centred at gain 1 when the engine opens. */
int32_t sa_place(uint32_t source, const float position[3], float gain);
/* Queues one sound request from that source. Sources are separate to the
 * engine: one ghost's sound never replaces another's, but all share SM64's
 * per-bank channel limits. */
int32_t sa_play(uint32_t source, uint32_t sound_bits);
/* The engine's hook for gains[] (patched into get_sound_volume); 1 otherwise. */
float sa_source_gain(const float *x);
/* Drops unprocessed requests and ends every sound this engine started. */
int32_t sa_stop_all(void);
/* One tick into out (interleaved stereo int16). queued_frames is what the
 * output still holds; below SA_TICK_TARGET_FRAMES the longer tick is made so
 * the long-run rate stays SA_RATE. */
int32_t sa_tick(uint32_t queued_frames, int16_t *out, uint32_t capacity_frames, uint32_t *frames);
int32_t sa_close(void);

#ifdef __cplusplus
}
#endif
