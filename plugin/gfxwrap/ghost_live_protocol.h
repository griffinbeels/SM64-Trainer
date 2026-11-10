/* Experimental live-pose values. No room admission, resources or pointers.
 * The receiver must bind participant_incarnation to its admitted peer first.
 * This is separate from the saved-clip and captured-source IPC contracts.
 */
#pragma once
#include "ghost_capture.h"
#include <stddef.h>
#include <stdint.h>

#define GLIVE_MAGIC "SMLV"
#define GLIVE_VERSION 0u
#define GLIVE_MAX_PEERS 3u
#define GLIVE_CORE_BYTES 308u
#define GLIVE_PACKET_BYTES 424u
#define GLIVE_VALID_FIELDS (GC_VALID_REQUIRED | GC_VALID_HELD | GC_VALID_PAD)

#pragma pack(push, 4)
typedef struct glive_header {
    uint8_t magic[4];
    uint32_t version, bytes, rom_layout, configuration, rom_generation;
    uint8_t participant_incarnation[16];
    uint64_t source_generation, elapsed_ns, continuity;
} glive_header;

typedef struct glive_packet {
    glive_header header;
    uint8_t core[GLIVE_CORE_BYTES];
    gc_held held;
    gc_pad pad;
} glive_packet;

/* Trusted decoded output, never copied directly from an untrusted packet. */
typedef struct glive_value {
    glive_header header;
    gc_sample sample;
} glive_value;
#pragma pack(pop)

#ifdef __cplusplus
static_assert(sizeof(glive_header) == 64, "live header");
static_assert(offsetof(glive_header, participant_incarnation) == 24, "live participant");
static_assert(offsetof(glive_header, source_generation) == 40, "live generation");
static_assert(offsetof(glive_header, elapsed_ns) == 48, "live time");
static_assert(offsetof(glive_header, continuity) == 56, "live continuity");
static_assert(GLIVE_CORE_BYTES == offsetof(gc_sample, shadow), "live core boundary");
static_assert(sizeof(glive_packet) == GLIVE_PACKET_BYTES, "live packet");
static_assert(sizeof(gc_held) == 44 && sizeof(gc_pad) == 8, "live optional evidence");
#endif
