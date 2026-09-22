/* Recorded graphics pose -> frozen native mesh in the LIVE camera.
 * Separate from transport and GL; all arrays are caller-owned/reused.
 */
#pragma once
#include "ghost_capture.h"
#include "ghost_pass.h"
#include "ghost_stream.h"
#include "ghost_compositor.h"
#include "model.h"
namespace ghost_pose {
enum class Result : uint32_t { ok, missing_pose, wrong_scene, wrong_occurrence,
    unsupported_pose, invalid_camera, model_failed, capacity };
struct Scratch {
    float position[SG_MODEL_MAX_VERTICES * 3];
    float normal[SG_MODEL_MAX_VERTICES * 3];
    float color[SG_MODEL_MAX_VERTICES * 3];
    float uv[SG_MODEL_MAX_VERTICES * 2];
    uint32_t material[SG_MODEL_MAX_VERTICES];
    ghost_compositor::Vertex vertices[SG_MODEL_MAX_VERTICES];
};
Result evaluate(sg_model *, const gs_record &recorded, const gc_sample &live,
                const ghost::WorldPass &, float opacity, Scratch &,
                ghost_compositor::Pose &out);
/* The recorded shadow in world space under the same live view_projection as
 * its body: removes the RECORDED camera from the executed shadow matrix,
 * expands the game's triangle order and unpacks the IA8 texture into the
 * caller's storage. False (out cleared) for invalid evidence. */
bool shadow(sg_model *, const gc_sample &recorded, const float view_projection[16],
            ghost_compositor::ShadowVertex vertices[24], uint8_t texture[256],
            ghost_compositor::Shadow &out);
}
