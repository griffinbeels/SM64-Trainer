/* Process-local evidence for one serialized, completed F3D occurrence. */
#pragma once
#include "ghost_capture.h"

struct gc_root_probe {
    const uint8_t *ram = nullptr;
    uint32_t ram_bytes = 0, epoch = 0, body_dl = 0, alpha_pc = 0, depth = 0;
    uint64_t sequence = 0, serial = 0;
    gc_pose pose{};
    float camera_to_object[3]{}, matrix[16]{};
    uint32_t matches = 0;
    bool prepared = false, armed = false, required = false;
    // An opaque Mario-shaped alpha list executed at cameraToObject: Mario
    // was drawn, whether or not his body followed it.
    bool alpha_seen = false;
    bool floor_aligned = false; // this frame may have set throwMatrix (tilt)
    uint8_t reason = GC_REFUSED_NONE; // why prepare could not arm the proof
    // The alpha list the recorded modelState implies: 16 bytes opaque, 24
    // translucent (vanish cap), words in executed order.
    uint32_t alpha_bytes = 16, alpha_words[6]{};
    // Mario's shadow: owned through allocation adjacency to the alpha list
    // that proved the root. step counts verified master + nested calls.
    uint32_t alpha_dl = 0, mario_alpha = 0, shadow_depth = 0, shadow_calls = 0, shadow_step = 0;
    uint32_t shadow_nested[3]{}, shadow_segment_base = 0;
    gc_shadow shadow{};
};

/* The last root proven by executed lists, kept by the serialized producer
 * across occurrences (reset with its reader/epoch). When a completed
 * occurrence draws no Mario at all (the camera culled him: a star spawn or
 * dialog cutscene, or it simply looks away), pose.refusal is
 * GC_REFUSED_NOT_DRAWN and the pose is still published when either
 *  - his root inputs (gfx position, angles, scale, action, body action) are
 *    bit-identical to the remembered frame in the same epoch and scene: the
 *    game would draw the identical root, so the record carries that matrix
 *    with the camera it executed under (and the shadow, if the animation is
 *    identical too); or
 *  - no floor alignment can apply this frame: geo_process_object composes
 *    TRS(gfx pos, angle, scale) itself, so the record says GC_ROOT_TRS.
 * A culled, floor-aligned Mario that moved stays refused (tilt unknown). */
struct gc_root_memory {
    bool valid = false, shadowed = false;
    uint32_t epoch = 0, action = 0, body_action = 0;
    int16_t level = 0, area = 0, angles[3]{}, animation_id = 0, animation_frame = 0, anim_y = 0;
    float position[3]{}, scale[3]{}, matrix[16]{};
    gc_camera camera{};
    gc_shadow shadow{};
};

/* align_with_floor's actions and their same-call bonk/stop destinations
 * (owned here; the TRS capture path refuses them too). */
bool ghost_floor_aligned(uint32_t action);
void ghost_root_prepare(gc_root_probe &, const gc_frame &, uint32_t object, uint32_t object_pool, gc_sample &);
/* pc is the physical address of the executing G_DL command, before its push.
 * dl is its original segmented operand; matrix is the renderer's current MV.
 * Invalid/unknown commands cannot revive a previous occurrence's candidate. */
void ghost_root_display_list(gc_root_probe &, uint32_t pc, uint32_t dl,
                             uint32_t physical_dl, uint32_t depth, const float matrix[16]);
/* Also publishes Mario's executed shadow (GC_VALID_SHADOW) with the root,
 * names the refusal in pose.refusal, and with memory remembers or carries. */
void ghost_root_finish(gc_root_probe &, bool completed, gc_sample &, gc_root_memory *memory = nullptr);
/* Provisional drawing only. The renderer still must complete its world/list
 * before presenting; recording publication uses root_finish instead. */
bool ghost_root_candidate(const gc_root_probe &, gc_sample &);
