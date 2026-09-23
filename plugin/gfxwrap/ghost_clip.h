/* Portable, greenfield ghost recording. All scalars are little endian;
 * floats are IEEE754. No renderer resources or process-local pointers. */
#pragma once
#include "ghost_capture.h"
#include <stddef.h>
#include <stdint.h>
#define GCLIP_VERSION 4u
#define GCLIP_MAGIC "SM64GHO4"
/* 200000 records (111 minutes at 30 Hz) plus their 4-byte index fit the
 * 128 MiB playback budget: 200000 * (652 + 4) bytes. */
#define GCLIP_MAX_RECORDS 200000u
#define GCLIP_WORLD_READY 3u
enum gclip_clock_kind { GCLIP_STAR_CLOCK = 1u, GCLIP_SEGMENT_CLOCK = 2u };
/* Header flag: the records after the attempt's closing frame are Mario's
 * closing star grab and dance, kept for display (the dance tail). A star run
 * sets it; the 100-coin star's clip does too although its clock is its exit
 * segment's. */
#define GCLIP_DANCE_TAIL 1u
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
    uint32_t clock_kind, origin_global_timer, origin_raw, flags;
    uint64_t origin_sequence, origin_elapsed, origin_continuity;
    uint32_t reserved[2];
} gclip_header;
typedef struct gclip_frame {
    uint32_t bytes, version, configuration, world_status;
    gc_sample sample;
} gclip_frame;
#pragma pack(pop)
#ifdef __cplusplus
static_assert(sizeof(gclip_header) == 160, "portable ghost header");
static_assert(offsetof(gclip_header, clock_kind) == 112, "portable clock kind");
static_assert(offsetof(gclip_header, origin_sequence) == 128, "portable origin sequence");
static_assert(offsetof(gclip_header, origin_continuity) == 144, "portable continuity");
static_assert(sizeof(gclip_frame) == 652, "portable ghost record");
#endif
