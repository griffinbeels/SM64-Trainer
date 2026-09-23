/* Up to GP_GHOSTS preloaded native ghosts sharing one live origin and one set
 * of controls. Worker calls own storage; renderer calls only select/evaluate
 * borrowed values. No file access, IPC, GL, physics or waits. */
#pragma once
#include "ghost_clip.h"
#include "ghost_origin.h"
#include "model.h"
#include <stdint.h>

#define GP_VERSION 7u
#define GP_GHOSTS 16u
/* A full displayed set plus as many retiring/pending clips. */
#define GP_CLIP_SLOTS (2u * GP_GHOSTS)
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
typedef struct gp_clip {
    uint8_t clip_id[16];
    uint32_t hue_degrees, reserved;
} gp_clip;
typedef struct gp_request {
    uint32_t bytes, version;
    uint64_t revision;
    uint32_t enabled, reserved0;
    uint64_t owner_token, model_id;
    uint32_t rom_generation, configuration;
    uint32_t mode, seek_igt;
    int32_t nudge_frames;
    float opacity;
    uint32_t star_ends, star_action_count;
    uint32_t star_actions[GP_STAR_ACTIONS];
    uint32_t tint_enabled;
    uint32_t proximity_enabled;
    float proximity_distance;
    /* FOLLOW binds an exact owner occurrence; FIXED requires all zero. */
    gp_origin origin;
    /* Enabled: 1..GP_GHOSTS distinct nonzero ids, each already prepared. */
    uint32_t clip_count, reserved1;
    gp_clip clips[GP_GHOSTS];
    /* Draw each visible ghost's recorded shadow (GC_VALID_SHADOW rows only).
     * occlusion_fade fills each pose's player-first region (ghost_occlusion.h);
     * formerly the checked-zero reserved2, so 0 draws as before. */
    uint32_t shadow_enabled, occlusion_fade;
    /* Ghost sound loudness, 0..1; 0 is exact silence. */
    float volume;
    uint32_t reserved3;
    /* The tracker's arm is gone (a completed star or segment): hide and stay
     * silent, unless a state load the renderer resolves after suspension
     * brings the ghost back on that frame, ahead of the owner's next command. */
    uint32_t suspended, reserved4;
} gp_request;

typedef struct gp_status {
    uint32_t bytes, version, decision, reserved;
    uint64_t revision;
    uint64_t owner_token, model_id;
    /* clips_active: requested clips the renderer holds for this command;
     * visible: poses produced by the latest evaluate. */
    uint32_t clip_count, clips_active, visible, reserved1;
    uint32_t rom_generation, configuration, selected_sequence_lo, selected_sequence_hi;
    uint32_t model_state, clips_ready, clips_owned, clips_retired;
    uint64_t resident_clip_bytes;
    uint32_t evaluations, rendered, gaps, refusals, adoption_busy;
    int32_t model_error;
    /* Per requested clip, in request order. decision summarizes the set:
     * READY/BEFORE_START when any ghost is visible, else the first clip's. */
    uint8_t clip_decisions[GP_GHOSTS];
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
     * no caller buffer or pointer survives this call. Never displaces another
     * clip: the worker releases clips it no longer wants. */
    int32_t (__cdecl *prepare_clip)(const gclip_header *, const gclip_frame *);
    /* Revision strictly increases for the module lifetime, including worker
     * replacement and stop/seek/opacity changes; UINT64_MAX fails closed. A
     * request binds the validated owner token, ROM session, layout, model and
     * clips. A changed capture epoch refuses the old live origin while the
     * preloaded clips remain available for a newly resolved origin.
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
    /* Worker only: this clip left the displayed set. A pending copy retires
     * now; a renderer-held copy stops drawing and retires at its next safe
     * boundary. Preparing the same id before then returns DEFERRED. */
    int32_t (__cdecl *release_clip)(const uint8_t clip_id[16]);
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
 * nudge; FIXED uses an explicit uint32 coordinate without a live arm. Each clip
 * then selects its first exact coordinate INCLUDING refused poses and compares
 * live scene: before-start clamps; after-end/gaps/refusals hide only that ghost.
 * A star generation change or live star grab ends the shared origin and every
 * ghost; tuning the same origin cannot revive it. With star_ends, FOLLOW also
 * hides each ghost at its own first recorded valid star action coordinate
 * after nudge (including duplicate/refused poses). A FOLLOW clip that carries a
 * dance tail (with or without star_ends) instead draws through any earlier
 * star and its closing grab and dance, then holds the last pose and fades
 * (body, shadow, sound) over GP_STAR_FADE_FRAMES.
 * This recorded cutoff moves with nudge; FIXED ignores both end policies. A new continuity refuses until
 * a witnessed recovery offset binds: the renderer runs the worker's same
 * ghost_origin::Recovery over its live samples, so a state load it can resolve
 * draws on its own frame, and the worker's next command confirms or replaces
 * it. Drawing never subtracts elapsed values across that gap.
 * Writes visible poses into out[GP_GHOSTS] back to front (farthest root first)
 * and returns true when count > 0; clears outputs otherwise. Geometry stays
 * borrowed until the next evaluate/safe_boundary/retire call. */
bool evaluate(const gc_sample &, const ghost::WorldPass &,
              ghost_compositor::Pose *out, uint32_t &count, gp_draw_token &token);
/* Ghost sound: the renderer hands each visible ghost's recorded sound
 * requests to the sink once, as its displayed record passes them (every
 * record since the last drawn frame, at most GP_SOUND_CATCHUP), and the
 * playback switch/volume at each boundary. A hidden ghost, a nudge or seek,
 * or a restored/rebound timeline passes nothing. Null discards.
 * Each drawn frame also places every visible ghost: its displayed root
 * relative to the live camera (SM64 pans and distances that itself) and a
 * gain of min(1, GP_SOUND_NEAR / d) -- inverse-square INTENSITY -- d its world distance from live
 * Mario (1 while live Mario's pose is unknown). */
#define GP_SOUND_CATCHUP 90u
/* Tuning: a ghost whose clip carries its star dance (GCLIP_DANCE_TAIL) fades
 * out over this many frames after its last recorded dance frame (15 = 0.5 s),
 * holding that pose; its sounds fade with it and are silent once it hides. */
#define GP_STAR_FADE_FRAMES 15u
/* A run of at most this many consecutive refused records, with a recorded
 * pose on both sides in the same scene and no missing occurrence, holds the
 * pose before it instead of blinking (2 = 67 ms, below a noticeable pause).
 * The refused rows stay refused in the clip; this is display only. */
#define GP_BRIDGE_FRAMES 2u
/* Raw-IGT restarts a star clip remembers (the latest win; Usamune restarts the
 * timer at some area entrances, e.g. LLL's volcano). */
#define GP_RESTARTS 8u
#define GP_SOUND_NEAR 400.0f /* tuning: full gain within 2.5 Mario heights (160 units each) */
struct SoundSink {
    void (__cdecl *cue)(uint32_t ghost, uint32_t sound_bits);
    void (__cdecl *control)(uint32_t playing, float volume);
    void (__cdecl *place)(uint32_t ghost, const float camera_relative[3], float gain);
};
void set_sound_sink(const SoundSink *);
/* Recheck immediately before final presentation; cancellation/configuration
 * during a list must not present an older provisional candidate. */
bool current(const gp_draw_token &);
/* Actual renderer owner before its context/thread goes away. Detaches and
 * retires storage for worker reclamation; no GL or resource destruction. */
void retire_on_context_loss();
}
#endif
