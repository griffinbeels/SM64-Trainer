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
    // Mario's shadow: owned through allocation adjacency to the alpha list
    // that proved the root. step counts verified master + nested calls.
    uint32_t alpha_dl = 0, mario_alpha = 0, shadow_depth = 0, shadow_calls = 0, shadow_step = 0;
    uint32_t shadow_nested[3]{}, shadow_segment_base = 0;
    gc_shadow shadow{};
};

void ghost_root_prepare(gc_root_probe &, const gc_frame &, uint32_t object, uint32_t object_pool, gc_sample &);
/* pc is the physical address of the executing G_DL command, before its push.
 * dl is its original segmented operand; matrix is the renderer's current MV.
 * Invalid/unknown commands cannot revive a previous occurrence's candidate. */
void ghost_root_display_list(gc_root_probe &, uint32_t pc, uint32_t dl,
                             uint32_t physical_dl, uint32_t depth, const float matrix[16]);
/* Also publishes Mario's executed shadow (GC_VALID_SHADOW) with the root. */
void ghost_root_finish(gc_root_probe &, bool completed, gc_sample &);
/* Provisional drawing only. The renderer still must complete its world/list
 * before presenting; recording publication uses root_finish instead. */
bool ghost_root_candidate(const gc_root_probe &, gc_sample &);
