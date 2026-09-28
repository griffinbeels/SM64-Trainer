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
    float ambient[SG_MODEL_MAX_VERTICES * 3];
    float uv[SG_MODEL_MAX_VERTICES * 2];
    uint32_t material[SG_MODEL_MAX_VERTICES];
    ghost_compositor::Vertex vertices[SG_MODEL_MAX_VERTICES];
    /* The model's vertices for this pose, in position/material order, before
     * the back faces the game culls leave the drawn vertices (Pose::count). */
    uint32_t evaluated;
};
Result evaluate(sg_model *, const gs_record &recorded, const gc_sample &live,
                const ghost::WorldPass &, float opacity, Scratch &,
                ghost_compositor::Pose &out);
/* A world point relative to the LIVE camera, in the space SM64 hands its own
 * sound engine (Mario's cameraToObject: +x right, +y up, -z ahead). */
bool camera_relative(sg_model *, const gc_camera &live, const float world[3], float out[3]);
/* The inverse of a recorded camera's look-at (rigid: transpose), native
 * column-major; removes that camera from an executed modelview. */
bool recorded_camera_inverse(sg_model *, const gc_camera &recorded, float inverse[16]);
/* The recorded shadow in world space under the same live view_projection as
 * its body: removes the RECORDED camera from the executed shadow matrix,
 * expands the game's triangle order and unpacks the IA8 texture into the
 * caller's storage. False (out cleared) for invalid evidence. */
bool shadow(sg_model *, const gc_sample &recorded, const float view_projection[16],
            ghost_compositor::ShadowVertex vertices[24], uint8_t texture[256],
            ghost_compositor::Shadow &out);
}
