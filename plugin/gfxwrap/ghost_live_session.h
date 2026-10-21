/* Trusted local worker -> bounded live poses. No provider, paths or ROM IO. */
#pragma once
#include "ghost_live_render.h"
#include "ghost_playback.h"
#include <stddef.h>

namespace ghost_live_session {
// Initial receiver freshness bound, not a network latency guarantee.
constexpr uint64_t stale_after_ns = 250000000ull;
constexpr unsigned receiver_history_capacity = 256; // bounded prototype tombstones; never evicted
#pragma pack(push, 4)
struct LocalOwner {
    uint32_t pid = 0, created_lo = 0, created_hi = 0, generation = 0;
    uint32_t rom_generation = 0, configuration = 0;
    uint64_t token = 0, model_id = 0;
};
struct Controls {
    float opacity = 1, proximity_distance = 300, name_background_opacity = .6f;
    uint32_t tint_enabled = 1, proximity_enabled = 1, occlusion_fade = 1, names_enabled = 1;
    uint32_t name_background = GP_NAME_BLACK, record_ghosts = 0;
    uint32_t inputs_enabled = 0, input_mode = GP_INPUT_STICK, hud_inputs_enabled = 0;
};
struct Peer {
    uint8_t incarnation[16]{};
    uint32_t member = 0, present = 0, hue_degrees = 0;
    char label[GP_LABEL_BYTES]{};
    uint64_t received_ns = 0;
    glive_packet packet{};
};
struct Update {
    LocalOwner owner{};
    uint8_t room[16]{};
    uint64_t revision = 0;
    uint32_t enabled = 0, hidden = 0;
    Controls controls{};
    Peer peers[GLIVE_MAX_PEERS]{};
};
#pragma pack(pop)
static_assert(sizeof(LocalOwner) == 40 && sizeof(Controls) == 48, "live local control layout");
static_assert(sizeof(Peer) == 524 && offsetof(Peer, packet) == 100, "live peer layout");
static_assert(sizeof(Update) == 1692 && offsetof(Update, peers) == 120, "live update layout");

// Same serialized worker as gp_api. Root has already validated owner process,
// lease, local ROM and room admission. Revision changes control metadata;
// equal revision may offer newer payloads only. present offers a latest value,
// not a fresh receipt: repeated bytes retain their original received_ns.
int worker_update(const Update &, uint64_t now_ns) noexcept;
void worker_revoke() noexcept; // permanent room/app-owner retirement; before hard gp_api.revoke
// Local capture/model lease ended, while the same app and logical room remain.
// Retains admitted source identities and their replay fences through retarget.
void worker_suspend() noexcept;
int worker_collect() noexcept;
int worker_release() noexcept; // after revoke and gp_api.release_worker succeeds

struct Token {
    ghost_live::DrawToken draw{};
    ghost_live::ModelBinding model{};
    uint64_t revision = 0, render_generation = 0;
};
// Single actual render owner. Call after playback.safe_boundary and before
// releasing its model/context; render methods never prepare or free a model.
bool begin(uint32_t rom, uint32_t configuration, uint32_t context,
           const gc_sample *local, uint64_t now_ns) noexcept;
bool plan() noexcept;
bool evaluate(const gc_sample &, const ghost::WorldPass &, uint64_t now_ns,
              ghost_compositor::Pose *out, uint32_t &count, Token &) noexcept;
bool current(const Token &, uint64_t now_ns) noexcept;
bool records_display() noexcept;
void retire_list() noexcept; // before playback.safe_boundary may detach the model
void retire_on_context_loss() noexcept;
}
