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

/* Copies the big-endian ROM; owns that copy and every parse until close. */
int32_t sa_open(const uint8_t *rom, size_t bytes);
/* Queues one sound request at a fixed centred source (full volume, no pan). */
int32_t sa_play(uint32_t sound_bits);
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
