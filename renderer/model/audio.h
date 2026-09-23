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

/* US ROM regions the engine parses. The seq headers are bounds-checked here;
 * the instrument/sample tables inside CTL are parsed by upstream code without
 * bounds, so the caller must first prove these bytes are known audio data. */
#define SA_ROM_BYTES 0x800000u
#define SA_CTL_OFFSET 0x57B720u
#define SA_TBL_OFFSET 0x593560u
#define SA_SEQ_OFFSET 0x7B0860u
#define SA_BANKS_OFFSET 0x7CC621u
#define SA_BANKS_BYTES 0xA0u
/* Upstream's audio_init DMA-copies 0x100 bytes from the bank-set table, 0x60
 * past the 0xA0 it uses (upstream read them from its whole-ROM copy). */
#define SA_BANKS_READ_BYTES 0x100u
/* Those regions are one contiguous span, and the engine reads nothing else:
 * about 2.3 MB of the 8 MB ROM (TBL's samples are most of it). */
#define SA_AUDIO_OFFSET SA_CTL_OFFSET
#define SA_AUDIO_BYTES (SA_BANKS_OFFSET + SA_BANKS_READ_BYTES - SA_AUDIO_OFFSET)

/* Copies the big-endian ROM's audio span (SA_AUDIO_BYTES starting at
 * SA_AUDIO_OFFSET); owns that copy and every parse until close. */
int32_t sa_open(const uint8_t *audio, size_t bytes);
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
