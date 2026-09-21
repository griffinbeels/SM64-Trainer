#include "ghost_pose.h"
#include <cmath>
#include <cstring>
namespace ghost_pose {
static_assert(static_cast<uint32_t>(ghost_compositor::Material::solid) == SG_MATERIAL_SOLID);
static_assert(static_cast<uint32_t>(ghost_compositor::Material::alpha_mix) == SG_MATERIAL_ALPHA_MIX);
static_assert(static_cast<uint32_t>(ghost_compositor::Material::decal_rgba) == SG_MATERIAL_DECAL_RGBA);
static_assert(static_cast<uint32_t>(ghost_compositor::Material::decal_rgb) == SG_MATERIAL_DECAL_RGB);
static_assert(static_cast<uint32_t>(ghost_compositor::Material::modulate_rgb) == SG_MATERIAL_MODULATE_RGB);
static_assert(static_cast<uint32_t>(ghost_compositor::Material::modulate_rgba) == SG_MATERIAL_MODULATE_RGBA);
namespace {
bool sample_valid(const gc_sample &s, uint32_t required) {
    return s.bytes == sizeof(s) && s.version == GC_VERSION && s.epoch && s.sequence
        && s.dlist_serial && (s.valid_fields & required) == required;
}
void multiply(const float *a, const float *b, float *out) {
    // Same column-major operation as GLideN64 MultMatrix, with separate output.
    for (unsigned c = 0; c < 4; ++c)
        for (unsigned r = 0; r < 4; ++r)
            out[c*4+r] = a[r]*b[c*4] + a[4+r]*b[c*4+1]
                + a[8+r]*b[c*4+2] + a[12+r]*b[c*4+3];
}
}
Result evaluate(sg_model *model, const gs_record &record, const gc_sample &live,
                const ghost::WorldPass &world, float opacity, Scratch &scratch,
                ghost_compositor::Pose &out) {
    out = {};
    if (record.bytes != sizeof(record) || record.version != GS_VERSION || !record.configuration
            || record.world.status != static_cast<uint32_t>(ghost::PassStatus::Ready))
        return Result::missing_pose;
    const gc_sample &recorded = record.sample;
    if (!sample_valid(recorded, GC_VALID_CLOCK | GC_VALID_POSE)
            || !sample_valid(live, GC_VALID_CLOCK | GC_VALID_CAMERA)) return Result::missing_pose;
    if (recorded.level != live.level || recorded.area != live.area) return Result::wrong_scene;
    if (world.epoch != live.epoch || world.list_serial != live.dlist_serial
            || !world.context_generation) return Result::wrong_occurrence;
    const auto &p = recorded.pose;
    if (p.root_kind != GC_ROOT_TRS || !std::isfinite(opacity) || opacity < 0 || opacity > 1)
        return Result::unsupported_pose;
    // The first material route is ordinary Mario. The source component carries
    // unsupported state explicitly; it cannot quietly render metal as cloth.
    if (p.model_state & 0x300u) return Result::unsupported_pose;
    sg_pose pose{};
    pose.fields = SG_POSE_REQUIRED | (p.hidden ? SG_POSE_HIDDEN : 0);
    pose.action = p.action; pose.body_action = p.body_action;
    pose.mario_flags = p.mario_flags; pose.area_update_counter = recorded.area_update_counter;
    pose.animation_id = p.animation_id; pose.animation_frame = p.animation_frame;
    pose.anim_y_translation = p.anim_y_translation;
    std::memcpy(pose.position,p.position,sizeof(pose.position));
    std::memcpy(pose.scale,p.scale,sizeof(pose.scale));
    std::memcpy(pose.angles,p.angles,sizeof(pose.angles));
    std::memcpy(pose.torso_angles,p.torso_angles,sizeof(pose.torso_angles));
    std::memcpy(pose.head_angles,p.head_angles,sizeof(pose.head_angles));
    pose.head_rotation[0] = p.head_angles[1];
    pose.head_rotation[1] = p.head_angles[2];
    pose.head_rotation[2] = p.head_angles[0];
    pose.model_state = p.model_state;
    pose.cap_state = p.cap_state; pose.eye_state = p.eye_state; pose.hand_state = p.hand_state;
    pose.wing_flutter = p.wing_flutter; pose.punch_state = p.punch_state;
    pose.grab_position = p.grab_position;
    float view[16]{};
    if (sg_model_view(model,live.camera.position,live.camera.focus,live.camera.roll,view) != SG_OK)
        return Result::invalid_camera;
    sg_mesh mesh{scratch.position,scratch.normal,scratch.color,scratch.uv,scratch.material,
        SG_MODEL_MAX_VERTICES,0,0};
    const int32_t status = sg_model_evaluate(model,&pose,&mesh);
    if (status == SG_CAPACITY) return Result::capacity;
    if (status != SG_OK || mesh.error_flags || mesh.vertex_count > SG_MODEL_MAX_VERTICES)
        return Result::model_failed;
    ghost_compositor::Pose built{};
    for (uint32_t i = 0; i < mesh.vertex_count; ++i) {
        if (scratch.material[i] > SG_MATERIAL_MODULATE_RGBA) return Result::model_failed;
        auto &v = scratch.vertices[i];
        std::memcpy(v.position,scratch.position+3*i,sizeof(v.position));
        std::memcpy(v.normal,scratch.normal+3*i,sizeof(v.normal));
        std::memcpy(v.color,scratch.color+3*i,sizeof(v.color));
        std::memcpy(v.uv,scratch.uv+2*i,sizeof(v.uv));
        v.material = static_cast<ghost_compositor::Material>(scratch.material[i]);
    }
    multiply(world.projection,view,built.view_projection);
    for (unsigned c = 0; c < 4; ++c) {
        built.view_projection[4*c] *= world.target.screen_scale[0];
        built.view_projection[4*c+1] *= world.target.screen_scale[1];
    }
    built.vertices = scratch.vertices; built.count = mesh.vertex_count;
    // Mario's conventional model light. Environment/material matching is a
    // separate renderer contract; this does not pretend to capture scene lights.
    const float inverse_sqrt3 = 0.57735026919f;
    built.light_direction[0] = built.light_direction[1] = built.light_direction[2] = inverse_sqrt3;
    built.ambient = 0.5f; built.diffuse = 0.5f; built.opacity = opacity; built.flip_y = 1;
    out = built;
    return Result::ok;
}
}
