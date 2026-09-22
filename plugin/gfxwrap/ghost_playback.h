/* One preloaded native ghost. Worker calls own storage; renderer calls only
 * select/evaluate borrowed values. No file access, IPC, GL, physics or waits. */
#pragma once
#include "ghost_clip.h"
#include "ghost_origin.h"
#include "model.h"
#include <stdint.h>

#define GP_VERSION 2u
#define GP_CLIP_SLOTS 3u
#define GP_CLIP_BUDGET (128u * 1024u * 1024u)
#define GP_STAR_ACTIONS 8u
#ifndef __cdecl
#if !defined(_MSC_VER)
#define __cdecl
#endif
#endif

enum gp_result {
    GP_OK = 0, GP_DEFERRED = 1, GP_INVALID = -1, GP_BUSY = -2,
    GP_BUDGET = -3, GP_MODEL_ERROR = -4, GP_EXHAUSTED = -5,
    GP_WRONG_THREAD = -6
};
enum gp_mode { GP_FOLLOW = 0, GP_FIXED = 1 };
enum gp_model_state {
    GP_MODEL_EMPTY, GP_MODEL_BUILDING, GP_MODEL_READY,
    GP_MODEL_RENDER_OWNED, GP_MODEL_RETIRED, GP_MODEL_RECLAIMING
};
enum gp_decision {
    GP_DISABLED, GP_PREPARING, GP_READY, GP_WRONG_IDENTITY, GP_NO_CLOCK,
    GP_WRONG_SCENE, GP_BEFORE_START, GP_AFTER_END, GP_GAP, GP_INVALID_FRAME,
    GP_STAR_ENDED, GP_POSE_REFUSED, GP_CONTEXT_LOST
};

#pragma pack(push, 4)
typedef go_origin gp_origin;
typedef struct gp_request {
    uint32_t bytes, version;
    uint64_t revision;
    uint32_t enabled, reserved0;
    uint64_t owner_token, model_id;
    uint32_t rom_generation, configuration;
    uint8_t clip_id[16];
    uint32_t mode, seek_igt;
    int32_t nudge_frames;
    float opacity;
    uint32_t star_ends, star_action_count;
    uint32_t star_actions[GP_STAR_ACTIONS];
    uint32_t tint_enabled, hue_degrees;
    uint32_t reserved[2];
    /* FOLLOW binds an exact owner occurrence; FIXED requires all zero. */
    gp_origin origin;
} gp_request;

typedef struct gp_status {
    uint32_t bytes, version, decision, reserved;
    uint64_t revision;
    uint64_t owner_token, model_id;
    uint8_t clip_id[16];
    uint32_t rom_generation, configuration, selected_sequence_lo, selected_sequence_hi;
    uint32_t model_state, clips_ready, clips_owned, clips_retired;
    uint64_t resident_clip_bytes;
    uint32_t evaluations, rendered, gaps, refusals, adoption_busy;
    int32_t model_error;
} gp_status;

typedef struct gp_draw_token {
    uint64_t revision;
    uint32_t cancellation, context_serial; /* Opaque draw/boundary serial, not a GL context ID. */
} gp_draw_token;
#pragma pack(pop)

typedef struct gp_api {
    uint32_t bytes, version, reserved[2];
    /* One serialized worker, bound by its first preparation/configuration call.
     * ROM storage is borrowed only for this call. The caller establishes ROM
     * provenance and gives different assets different nonzero model IDs.
     * Replacement returns DEFERRED until the renderer releases the old model;
     * retry/collect on the worker. Never call these from a graphics callback. */
    int32_t (__cdecl *prepare_model)(uint64_t model_id, const uint8_t *rom, uint32_t bytes);
    /* Copies/validates the entire portable clip and its index on the worker.
     * IDs identify immutable content. Budget includes ready/owned/retired clips;
     * no caller buffer or pointer survives this call. */
    int32_t (__cdecl *prepare_clip)(const gclip_header *, const gclip_frame *);
    /* Revision strictly increases for the module lifetime, including worker
     * replacement and stop/seek/opacity changes; UINT64_MAX fails closed. A
     * request binds the validated owner token, ROM session, layout, model and
     * clip. A changed capture epoch refuses the old live origin while the
     * preloaded clip remains available for a newly resolved origin.
     * star_actions comes from the application's canonical action registry. */
    int32_t (__cdecl *configure)(const gp_request *);
    /* Any lifecycle/watchdog thread: atomically cancel, never close/free/wait.
     * A later configure with a newer revision is required to enable again. */
    void (__cdecl *revoke)(uint32_t reason);
    /* Worker only; reclaim retired clips and adopt/close retired models. */
    int32_t (__cdecl *collect)(void);
    /* Worker only, after revoke+collect. Returns DEFERRED until model_state is
     * EMPTY and every clip slot is empty; then releases worker-thread binding.
     * A later worker may bind. This proves storage retirement, not that no
     * renderer code is executing or that its module may be unloaded. */
    int32_t (__cdecl *release_worker)(void);
    /* Bounded copied telemetry. Counters/slot counts may advance independently;
     * release_worker, not a status snapshot, establishes complete retirement.
     * No retained internal pointers. "rendered" counts produced pose values,
     * not display submissions (this module never calls GL). */
    int32_t (__cdecl *status)(gp_status *, uint32_t bytes);
    /* Any control worker: hide immediately without retiring preloaded assets.
     * A later strictly newer configure resumes them. Lease/ROM shutdown uses
     * revoke instead; withdraw does not authorize worker/module shutdown. */
    void (__cdecl *withdraw)(void);
} gp_api;

#ifdef __cplusplus
namespace ghost { struct WorldPass; }
namespace ghost_compositor { struct Pose; }
namespace ghost_playback {
const gp_api *api();
/* Actual serialized renderer thread, between completed uses of borrowed pose
 * arrays. Claims preloaded state or retires old ownership without allocation,
 * destruction or waiting. Zero identity revokes this ROM session. */
void safe_boundary(uint32_t rom_generation, uint32_t configuration);
/* Borrowed until the next safe_boundary/retire call. Obtain only on renderer. */
bool atlas(sg_atlas_view *out, uint64_t *model_id);
/* FOLLOW uses exact elapsed-origin differences plus the resolved origin.offset and
 * nudge; FIXED uses an explicit uint32 coordinate without a live arm. Select
 * first exact coordinate INCLUDING refused poses, then compare live scene.
 * Before-start clamps first; after-end/gaps/refusals hide. A star generation
 * change or live star grab ends that origin; tuning the same origin cannot
 * revive it. With star_ends, FOLLOW also hides at the first recorded valid
 * star action coordinate after nudge (including duplicate/refused poses).
 * This recorded cutoff moves with nudge; FIXED ignores both end policies.
 * A new continuity refuses until the worker binds a witnessed recovery offset;
 * drawing never subtracts elapsed values across that gap.
 * Clears outputs on failure.
 * Geometry stays borrowed until the next evaluate/safe_boundary/retire call. */
bool evaluate(const gc_sample &, const ghost::WorldPass &,
              ghost_compositor::Pose &out, gp_draw_token &token);
/* Recheck immediately before final presentation; cancellation/configuration
 * during a list must not present an older provisional candidate. */
bool current(const gp_draw_token &);
/* Actual renderer owner before its context/thread goes away. Detaches and
 * retires storage for worker reclamation; no GL or resource destruction. */
void retire_on_context_loss();
}
#endif
