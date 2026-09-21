/* Bounded source-thread clock. No capture ABI, target/segment policy or IO. */
#pragma once
#include <stdint.h>
#define GCC_VERSION 1u
#define GCC_LIST_MAX 8u
enum gcc_input_fields { GCC_CLOCK=1, GCC_ACTION=2, GCC_WARP=4, GCC_REQUIRED=7 };
enum gcc_valid { GCC_RAW_VALID=1, GCC_PROGRESS_VALID=2, GCC_ELAPSED_VALID=4 };
enum gcc_transition { GCC_UNKNOWN, GCC_STEADY, GCC_ORIGIN, GCC_LEVEL,
    GCC_RETRY, GCC_AREA_WARP, GCC_TELEPORT };
enum gcc_refusal { GCC_ACCEPTED=0, GCC_UNKNOWN_ORIGIN=1, GCC_INCOMPLETE=2,
    GCC_SEQUENCE_GAP=4, GCC_REWIND=8, GCC_REPEATED=16, GCC_BAD_INPUT=32,
    GCC_DISCONTINUITY=64, GCC_COUNTER_WRAP=128, GCC_EXHAUSTED=256,
    GCC_FRAME_GAP=512 };
#pragma pack(push, 4)
typedef struct gcc_config {
    uint32_t version, near_zero, area_window, teleport_window, level_tail, warp_window;
    uint32_t teleport_action, wrap_ceiling, castle_count, warp_count;
    uint32_t castle_levels[GCC_LIST_MAX], warp_ops[GCC_LIST_MAX];
} gcc_config;
typedef struct gcc_input {
    uint32_t fields, epoch;
    uint64_t sequence;
    uint32_t global_timer, raw_igt;
    int32_t level, area;
    uint32_t action, warp_op;
    int32_t warp_timer;
    /* An externally established attempt/star zero origin, exactly this
     * occurrence with raw IGT zero. A subarea zero is not a new star origin.
     * Both zero means absent. A mid-leg boolean/approximate frame is forbidden.
     * This does not seed a pre-existing bank or implement segment arming. */
    uint32_t origin_epoch;
    uint64_t origin_sequence;
} gcc_input;
typedef struct gcc_output {
    uint32_t valid, refusal, transition, raw_igt;
    /* origin_id and leg_id are opaque generations whenever RAW_VALID is set,
     * including unknown-bank attachment (initial origin generation zero).
     * Identity is not evidence that banked/progress are known. */
    uint64_t banked_frames, progress_frames, origin_id, leg_id;
    /* Arbitrary reference for differences only, valid independently of star
     * progress. Never subtract values from different continuity IDs. */
    uint64_t elapsed_frames, continuity_id;
} gcc_output;
#pragma pack(pop)
#ifdef __cplusplus
static_assert(sizeof(gcc_config)==104,"native clock config ABI");
static_assert(sizeof(gcc_output)==64,"native clock evidence ABI");
#endif
/* Caller-owned storage, exclusively one serialized source thread. Fields are
 * implementation state, not a portable file or IPC layout. init starts a new
 * instance identity; reset permits a new source sequence while preserving the
 * instance's monotonic ID counters. */
typedef struct gcc_clock {
    gcc_config config;
    gcc_input previous;
    uint64_t last_sequence;
    uint64_t banked, origin, leg;
    uint64_t elapsed, continuity;
    uint32_t have_elapsed;
    uint32_t initialized, exhausted, have_previous, trusted, history_start;
    uint32_t have_area, area_frame, entered_area;
    uint32_t have_teleport, teleport_frame, have_warp, warp_frame;
    uint32_t have_level, level_frame, awaiting_level_zero;
} gcc_clock;
#ifdef __cplusplus
extern "C" {
#endif
int gcc_init(gcc_clock*, const gcc_config*);
void gcc_reset(gcc_clock*);
/* Classification follows EpochTracker + IgtClock._track_epoch. Separate
 * evidence guards refuse progress on missing inputs, pre-queue sequence gaps
 * or skipped game frames that could conceal a warp countdown reaching zero,
 * rewinds, unknown origin, u16 wrap and unexplained backwards counter jumps.
 * Source sequence is monotonic across epochs. Downstream queue loss is not a
 * source input gap. Duplicate/stale occurrences
 * are refused without changing the state, so they cannot bank twice.
 * banked/progress are cleared unless GCC_PROGRESS_VALID is present.
 * origin/leg identity remains available with RAW_VALID to detect a later
 * attempt change even when attachment did not establish a trusted bank.
 * A complete observation after a barrier starts a new elapsed reference at
 * zero; this does not assert the star's bank is zero. Missing inputs have no
 * elapsed reference until the next complete observation. Level/retry keeps
 * elapsed continuity; only the target owner chooses a segment origin/stop.
 * global_timer is used only for recency, never to estimate missing IGT. */
uint32_t gcc_step(gcc_clock*, const gcc_input*, gcc_output*);
#ifdef __cplusplus
}
#endif
