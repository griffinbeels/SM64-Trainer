/* Separate leased Ghost channel. The worker and client hold the named mutex
 * for every mapped-page access. The render thread never accesses this page. */
#pragma once
#include "ghost_clip.h"
#include "ghost_origin.h"
#include <stddef.h>
#define GIPC_VERSION 2u
#define GIPC_MAGIC "SM64GIP2"
#define GIPC_SLOTS 512u
#define GIPC_PATH_CHARS 520u
#define GIPC_LEASE_MS 3000u
#define GIPC_PREFIX L"Local\\sm64_trainer_ghost_v2_"
enum gipc_state { GIPC_CLOSED, GIPC_PASSIVE, GIPC_CAPTURING, GIPC_PREPARING,
    GIPC_READY, GIPC_REFUSED };
enum gipc_reason { GIPC_OK, GIPC_OWNER_GONE, GIPC_EXPIRED, GIPC_PROTOCOL,
    GIPC_NO_ROM, GIPC_LAYOUT, GIPC_FILE, GIPC_MODEL, GIPC_CLIP, GIPC_BUSY, GIPC_ORIGIN, GIPC_ROM };
#pragma pack(push, 4)
typedef struct gipc_request {
    uint32_t owner_pid, owner_created_lo, owner_created_hi, generation;
    uint64_t token, revision;
    uint32_t heartbeat, capture_enabled, play_enabled, mode;
    uint32_t seek_igt;
    int32_t nudge;
    float opacity;
    uint32_t stop_at_star;
    uint32_t rom_generation, reserved;
    uint64_t model_id;
    uint8_t clip_id[16];
    gc_layout layout;
    uint16_t rom_path[GIPC_PATH_CHARS], clip_path[GIPC_PATH_CHARS];
    uint8_t clip_sha256[32];
    uint32_t star_action_count, star_actions[8];
    uint32_t tint_enabled, hue_degrees;
    go_marker origin;
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
    uint8_t reserved[116];
} gipc_status;
typedef struct gipc_page {
    gipc_status status;
    gipc_request request;
    uint8_t reserved[4096 - sizeof(gipc_status) - sizeof(gipc_request)];
    gclip_frame records[GIPC_SLOTS];
} gipc_page;
#pragma pack(pop)
#ifdef __cplusplus
static_assert(sizeof(gipc_status) == 256, "ghost status bytes");
static_assert(sizeof(gipc_request) == 2460, "ghost request bytes");
static_assert(offsetof(gipc_page, request) == 256, "ghost command offset");
static_assert(offsetof(gipc_page, records) == 4096, "ghost ring offset");
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
}
#endif
