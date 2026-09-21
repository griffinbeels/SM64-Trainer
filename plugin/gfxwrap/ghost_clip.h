/* Portable, greenfield ghost recording. All scalars are little endian;
 * floats are IEEE754. No renderer resources or process-local pointers. */
#pragma once
#include "ghost_capture.h"
#include <stdint.h>
#define GCLIP_VERSION 1u
#define GCLIP_MAGIC "SM64GHO1"
#define GCLIP_MAX_RECORDS 262144u
#define GCLIP_WORLD_READY 3u
#pragma pack(push, 4)
typedef struct gclip_header {
    char magic[8];
    uint32_t version, bytes, record_bytes, record_count;
    uint32_t rom_layout, sample_version, configuration, epoch;
    uint8_t clip_id[16];
    uint64_t qpc_frequency;
    int64_t qpc_anchor;
    double utc_anchor;
    uint64_t first_sequence, last_sequence;
    uint32_t first_global_timer, last_global_timer;
    int32_t level, area;
    uint32_t flags, reserved[3];
} gclip_header;
typedef struct gclip_frame {
    uint32_t bytes, version, configuration, world_status;
    gc_sample sample;
} gclip_frame;
#pragma pack(pop)
#ifdef __cplusplus
static_assert(sizeof(gclip_header) == 128, "portable ghost header");
static_assert(sizeof(gclip_frame) == 16 + sizeof(gc_sample), "portable ghost record");
#endif
