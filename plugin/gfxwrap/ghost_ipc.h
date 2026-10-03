/* Separate leased Ghost channel. The worker and client hold the named mutex
 * for every mapped-page access. The render thread never accesses this page. */
#pragma once
#include "ghost_clip.h"
#include "ghost_origin.h"
#include "ghost_tuning.h"
#include <stddef.h>
#define GIPC_VERSION 13u
#define GIPC_MAGIC "SM64GI13"
#define GIPC_SLOTS 512u
#define GIPC_PATH_CHARS 520u
#define GIPC_LEASE_MS 3000u
#define GIPC_PREFIX L"Local\\sm64_trainer_ghost_v13_"
#define GIPC_GHOSTS GP_GHOSTS
enum gipc_state { GIPC_CLOSED, GIPC_PASSIVE, GIPC_CAPTURING, GIPC_PREPARING,
    GIPC_READY, GIPC_REFUSED };
enum gipc_reason { GIPC_OK, GIPC_OWNER_GONE, GIPC_EXPIRED, GIPC_PROTOCOL,
    GIPC_NO_ROM, GIPC_LAYOUT, GIPC_FILE, GIPC_MODEL, GIPC_CLIP, GIPC_BUSY, GIPC_ORIGIN, GIPC_ROM,
    /* The set does not fit: GP_CLIP_BUDGET, or memory in PJ64's 2 GiB. The
     * clips are fine; fewer or shorter ones would show. */
    GIPC_BUDGET };
#pragma pack(push, 4)
/* One saved recording in the displayed set. Hue is per ghost; tint_enabled
 * stays global. id+SHA is the identity the worker reuses across set changes. */
typedef struct gipc_clip {
    uint8_t clip_id[16];
    uint8_t clip_sha256[32];
    uint32_t hue_degrees;
    uint32_t reserved;
    uint16_t clip_path[GIPC_PATH_CHARS];
    char label[GP_LABEL_BYTES]; /* terminated UTF-8, validated before publication */
} gipc_clip;
typedef struct gipc_request {
    uint32_t owner_pid, owner_created_lo, owner_created_hi, generation;
    uint64_t token, revision;
    uint32_t heartbeat, capture_enabled, play_enabled, mode;
    uint32_t seek_igt;
    int32_t nudge;
    float opacity;
    uint32_t stop_at_star;
    uint32_t rom_generation, owner_suspended;
    uint64_t model_id;
    gc_layout layout;
    uint16_t rom_path[GIPC_PATH_CHARS];
    uint32_t star_action_count, star_actions[8];
    uint32_t tint_enabled;
    uint32_t proximity_enabled;
    float proximity_distance;
    go_marker origin;
    /* play_enabled requires 1..GIPC_GHOSTS nonzero, distinct clip ids. */
    uint32_t clip_count, reserved1;
    gipc_clip clips[GIPC_GHOSTS];
    /* 0 draws no recorded shadow for any ghost (the A/B switch). 1 in
     * occlusion_fade fades ghost pixels that block live Mario or the camera
     * (ghost_occlusion.h); formerly the checked-zero reserved2, so 0 is off. */
    uint32_t shadow_enabled, occlusion_fade;
    /* Ghost sound loudness 0..1; 0 is exact silence. 1 in sound_fade lets
     * each ghost's sound follow its picture's fade (ghost_sound_fade.h);
     * formerly the checked-zero reserved3, so 0 is off. */
    float volume;
    uint32_t sound_fade;
    uint32_t names_enabled, inputs_enabled, input_mode;
    /* The name's dialog box (gp_name_background) and its alpha, then the
     * ghosts' inputs stacked above the game's own input display. */
    uint32_t name_background;
    float name_background_opacity;
    uint32_t hud_inputs_enabled;
    /* 1: the recording takes the picture the window shows (gp_request). */
    uint32_t record_ghosts;
    /* The ghost's path: exactly gp_request's path_* fields. */
    uint32_t path_enabled, path_buttons, path_source, path_clip, path_ahead_ms;
    float path_width, path_opacity;
    uint32_t path_spacing_ms;
} gipc_request;
typedef struct gipc_status {
    char magic[8];
    uint32_t version, bytes, producer_pid, producer_created_lo, producer_created_hi, generation;
    uint32_t rom_generation, rom_open, state, reason;
    uint32_t configuration, epoch, record_bytes, capabilities;
    uint64_t qpc_frequency;
    int64_t qpc_anchor;
    double utc_anchor;
    uint64_t produced, consumed, dropped, ack_revision, ack_token;
    uint32_t playback_state, playback_reason;
    uint32_t displayed; /* Most recent SwapBuffers contains this command's ghost. */
    /* Set readiness: listed, prepared for playback, and drawn in the frame
     * that set displayed (zero while displayed is zero). */
    uint32_t clips_requested, clips_prepared, clips_visible;
    /* Mario's particles captured this lease but not kept (beyond 16 a frame). */
    uint32_t particles_dropped;
    /* The last list's render hooks (gs_hook_cost, in its order): driver
     * queries and microseconds on the emulation thread, then the list and its
     * stages. Measured live, never asked of him. */
    uint32_t hook_gl_queries, hook_micros;
    uint32_t hook_list, hook_list_micros, hook_flush_micros;
    uint32_t hook_open_micros, hook_batch_micros, hook_validate_micros, hook_close_micros, hook_finalize_micros;
    uint32_t hook_state_micros, hook_copy_micros, hook_draw_micros, hook_draw_max_micros, hook_restore_micros;
    uint32_t hook_hud_micros, hook_hud_draws, hook_layer_passes;
    uint32_t hook_query_micros, hook_error_reads, hook_ghosts;
    uint8_t reserved[16];
} gipc_status;
typedef struct gipc_page {
    gipc_status status;
    gipc_request request;
    uint8_t reserved[20480 - sizeof(gipc_status) - sizeof(gipc_request)];
    gclip_frame records[GIPC_SLOTS];
} gipc_page;
#pragma pack(pop)
#ifdef __cplusplus
static_assert(sizeof(gipc_status) == 256, "ghost status bytes");
static_assert(sizeof(gipc_clip) == 1160, "ghost clip entry bytes");
static_assert(sizeof(gipc_request) == 20052, "ghost request bytes");
static_assert(offsetof(gipc_page, request) == 256, "ghost command offset");
static_assert(offsetof(gipc_page, records) == 20480, "ghost ring offset");
namespace ghost_ipc {
// Serialized emulation lifecycle caller, outside any DList. Never joins.
// Zilmar HEADER contains only the first 64 bytes. The worker discovers and
// copies the matching full cartridge through guarded reads for this generation.
void rom(bool opened, const uint8_t *rom_header = nullptr);
// Render-thread-safe atomic identity. A closed ROM has generation zero.
uint32_t rom_generation();
// Render-thread notice: zero for a clean/failed swap or lost context. Atomic,
// with no mapped-page access; the worker qualifies it against current playback.
void presented(uint64_t playback_revision);
/* Ghost sound engine lifetime, bound to the worker's verified ROM bytes.
 * The renderer glue registers it; hosts without audio leave it unset. */
struct AudioHooks { bool (__cdecl *start)(const uint8_t *rom, size_t bytes); void (__cdecl *stop)(); };
void set_audio(const AudioHooks *);
}
#endif
