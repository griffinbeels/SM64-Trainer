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
// The compositor's tile fields are the model's sg_tile, moved above its mode.
static_assert(ghost_compositor::tile::cell == SG_TILE_CELL && ghost_compositor::tile::s_clamp == SG_TILE_S_CLAMP
    && ghost_compositor::tile::s_mask == SG_TILE_S_MASK_SHIFT && ghost_compositor::tile::s_size == SG_TILE_S_SIZE_SHIFT
    && ghost_compositor::tile::t_clamp == SG_TILE_T_CLAMP && ghost_compositor::tile::t_mask == SG_TILE_T_MASK_SHIFT
    && ghost_compositor::tile::t_size == SG_TILE_T_SIZE_SHIFT && ghost_compositor::tile::field == SG_TILE_FIELD
    && ghost_compositor::tile::present == SG_TILE_PRESENT);
namespace {
bool sample_valid(const gc_sample &s, uint32_t required) {
    return s.bytes == sizeof(s) && s.version == GC_VERSION && s.epoch && s.sequence
        && s.dlist_serial && (s.valid_fields & required) == required;
}
// The held objects the model draws, by segmented behaviour (US; each agrees
// with STROOP's map in data/behaviours_us.tsv, and the animated ones with the
// LOAD_ANIMATIONS of their model's table in the ROM's behaviour segment).
struct HeldModel { uint32_t behavior; uint32_t kind; };
constexpr HeldModel held_models[] = {
    {0x13003174u, SG_HELD_BOBOMB},      // bhvBobomb
    {0x130020E8u, SG_HELD_PENGUIN},     // bhvSmallPenguin
    {0x130001F4u, SG_HELD_KING_BOBOMB}, // bhvKingBobomb
    {0x13000528u, SG_HELD_CHUCKYA},     // bhvChuckya
    {0x13004218u, SG_HELD_BREAKABLE_BOX}, // bhvBreakableBoxSmall (the cork box)
    {0x13001650u, SG_HELD_BREAKABLE_BOX}, // bhvJumpingBox (the crazy box, same model)
    {0x130044FCu, SG_HELD_MIPS},        // bhvMips: its fur clamps (sg_tile)
};
uint32_t held_kind(uint32_t behavior) {
    for (const auto &held : held_models) if (held.behavior == behavior) return held.kind;
    return SG_HELD_NONE;
}
void multiply(const float *a, const float *b, float *out) {
    // Same column-major operation as GLideN64 MultMatrix, with separate output.
    for (unsigned c = 0; c < 4; ++c)
        for (unsigned r = 0; r < 4; ++r)
            out[c*4+r] = a[r]*b[c*4] + a[4+r]*b[c*4+1]
                + a[8+r]*b[c*4+2] + a[12+r]*b[c*4+3];
}
}
// SM64 look-at is rigid: the recorded camera inverts by transpose.
bool recorded_camera_inverse(sg_model *model, const gc_camera &camera, float inverse[16]) {
    float view[16]{};
    if(sg_model_view(model,camera.position,camera.focus,camera.roll,view)!=SG_OK) return false;
    std::memset(inverse,0,16*sizeof(float));
    for(unsigned c=0;c<3;++c)for(unsigned r=0;r<3;++r) inverse[4*c+r]=view[4*r+c];
    inverse[15]=1;
    for(unsigned r=0;r<3;++r)
        inverse[12+r]=-(inverse[r]*view[12]+inverse[4+r]*view[13]+inverse[8+r]*view[14]);
    return true;
}
namespace {
// G_TEXTURE_GEN exactly as the live renderer computes it (GLideN64 gSP.cpp):
// with its lookat enabled, x/y are the unit eye-space normal dotted with the
// lookat vectors, otherwise its own x/y. (x+1)*512 and (y+1)*512 scaled by
// gSPTexture(0x0F80, 0x07C0) are 0..62 x 0..31 texels of the 64x32 metal
// texture, atlas cell 0 of the model's SG_ATLAS_CELLS. The live view and
// the live lookat stand in for how the game would draw the ghost now.
bool environment_uv(const float view[16], const ghost::Lookat &lookat, const float normal[3], float uv[2]) {
    float eye[3];
    for (unsigned r = 0; r < 3; ++r)
        eye[r] = view[r]*normal[0] + view[4+r]*normal[1] + view[8+r]*normal[2];
    const float length = std::sqrt(eye[0]*eye[0] + eye[1]*eye[1] + eye[2]*eye[2]);
    if (!std::isfinite(length) || length <= 0) return false;
    for (float &axis : eye) axis /= length;
    float x = eye[0], y = eye[1];
    if (lookat.enabled) {
        x = lookat.x[0]*eye[0] + lookat.x[1]*eye[1] + lookat.x[2]*eye[2];
        y = lookat.y[0]*eye[0] + lookat.y[1]*eye[1] + lookat.y[2]*eye[2];
    }
    if (!std::isfinite(x) || !std::isfinite(y)) return false;
    const float s = (x + 1.0f) * 512.0f * (0x0F80 / 65536.0f);
    const float t = (y + 1.0f) * 512.0f * (0x07C0 / 65536.0f);
    uv[0] = s / 64.0f / float(SG_ATLAS_CELLS);
    uv[1] = t / 64.0f;
    return true;
}
bool restore_root(sg_model *model, const gc_sample &sample, sg_pose &pose) {
    const auto &p=sample.pose;
    if (p.root_kind==GC_ROOT_TRS) {
        const float zero[16]{};
        return std::memcmp(p.root_matrix,zero,sizeof(zero))==0;
    }
    if (p.root_kind!=GC_ROOT_SCALED_MODELVIEW || !(sample.valid_fields&GC_VALID_CAMERA)) return false;
    const auto *m=p.root_matrix;
    for(unsigned i=0;i<16;++i) if(!std::isfinite(m[i])) return false;
    if(m[3]!=0 || m[7]!=0 || m[11]!=0 || m[15]!=1) return false;
    float inverse[16]{}, world[16]{};
    // Remove the RECORDED camera, then the geo .25 and gfx scales;
    // sg_model_evaluate applies those scales exactly once again.
    if(!recorded_camera_inverse(model,sample.camera,inverse)) return false;
    multiply(inverse,m,world);
    for(unsigned c=0;c<3;++c) {
        if(!std::isfinite(p.scale[c]) || std::fabs(p.scale[c])<0.000001f) return false;
        for(unsigned r=0;r<3;++r) world[4*c+r]/=0.25f*p.scale[c];
    }
    for(float value:world)if(!std::isfinite(value))return false;
    pose.fields|=SG_POSE_THROW_MATRIX;
    std::memcpy(pose.throw_matrix,world,sizeof(world));
    return true;
}
}
bool camera_relative(sg_model *model, const gc_camera &live, const float world[3], float out[3]) {
    float view[16]{};
    if(sg_model_view(model,live.position,live.focus,live.roll,view)!=SG_OK) return false;
    // Column-major look-at (translation in 12..14), the same product as drawing.
    for(unsigned r=0;r<3;++r)
        out[r]=view[r]*world[0]+view[4+r]*world[1]+view[8+r]*world[2]+view[12+r];
    return std::isfinite(out[0]) && std::isfinite(out[1]) && std::isfinite(out[2]);
}
bool shadow(sg_model *model, const gc_sample &recorded, const float view_projection[16],
            ghost_compositor::ShadowVertex vertices[24], uint8_t texture[256],
            ghost_compositor::Shadow &out) {
    out = {};
    const auto &s = recorded.shadow;
    float inverse[16]{}, world[16]{};
    if (!model || !(recorded.valid_fields & GC_VALID_SHADOW) || !(recorded.valid_fields & GC_VALID_CAMERA)
            || !recorded_camera_inverse(model, recorded.camera, inverse)) return false;
    multiply(inverse, s.matrix, world);
    for (float value : world) if (!std::isfinite(value)) return false;
    for (unsigned i = 0; i < 24; ++i) {
        const unsigned v = gc_shadow_triangles[i];
        auto &out_vertex = vertices[i];
        const float x = s.position[v][0], y = s.position[v][1], z = s.position[v][2];
        for (unsigned r = 0; r < 3; ++r)
            out_vertex.position[r] = world[r]*x + world[4+r]*y + world[8+r]*z + world[12+r];
        // Vtx tc is S10.5 texels under gSPTexture(0xFFFF); N64 texel centres
        // sit on integers, GL's on halves: the shader's 3-point filter expects
        // the half-texel shift applied here.
        for (unsigned a = 0; a < 2; ++a)
            out_vertex.uv[a] = (float(s.uv[v][a]) / 32.0f * (65535.0f / 65536.0f) + 0.5f) / 16.0f;
        out_vertex.alpha = float(s.alpha[v]) / 255.0f;
    }
    for (unsigned i = 0; i < 256; ++i)
        texture[i] = uint8_t((s.texture[i / 2] >> ((i & 1) ? 0 : 4)) & 15);
    out.vertices = vertices; out.count = 24; out.texture_ia8 = texture;
    std::memcpy(out.view_projection, view_projection, sizeof(out.view_projection));
    return true;
}
Result evaluate(sg_model *model, const gs_record &record, const gc_sample &live,
                const ghost::WorldPass &world, float opacity, Scratch &scratch,
                ghost_compositor::Pose &out) {
    out = {};
    // Historical framebuffer qualification does not invalidate captured Mario
    // state. The caller supplies the separately qualified LIVE world pass.
    if (record.bytes != sizeof(record) || record.version != GS_VERSION || !record.configuration)
        return Result::missing_pose;
    const gc_sample &recorded = record.sample;
    if (!sample_valid(recorded, GC_VALID_CLOCK | GC_VALID_POSE)
            || !sample_valid(live, GC_VALID_CLOCK | GC_VALID_CAMERA)) return Result::missing_pose;
    if (recorded.level != live.level || recorded.area != live.area) return Result::wrong_scene;
    if (world.epoch != live.epoch || world.list_serial != live.dlist_serial
            || !world.context_generation) return Result::wrong_occurrence;
    const auto &p = recorded.pose;
    if (!std::isfinite(opacity) || opacity < 0 || opacity > 1)
        return Result::unsupported_pose;
    // Vanish cap (and a warp fade): modelState 0x100 | alpha, the executed
    // envcolor alpha. The ghost shows it on top of the user's opacity; the
    // game's dither becomes an even blend. Metal (0x200) is its own variant.
    if (p.model_state & 0x100u) opacity *= float(p.model_state & 0xffu) / 255.0f;
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
    if (!restore_root(model,recorded,pose)) return Result::unsupported_pose;
    float view[16]{};
    if (sg_model_view(model,live.camera.position,live.camera.focus,live.camera.roll,view) != SG_OK)
        return Result::invalid_camera;
    // The object in his hand, when the model knows its geo; its billboards
    // face the live camera the ghost is drawn under.
    const gc_held &held = recorded.held;
    if ((recorded.valid_fields & GC_VALID_HELD) && held_kind(held.behavior) != SG_HELD_NONE) {
        pose.fields |= SG_POSE_HELD;
        pose.held_kind = held_kind(held.behavior);
        pose.held_animation_id = held.animation_id; pose.held_animation_frame = held.animation_frame;
        pose.held_anim_y_translation = held.anim_y_translation; pose.held_anim_state = held.anim_state;
        std::memcpy(pose.held_position,held.holp,sizeof(pose.held_position));
        std::memcpy(pose.held_scale,held.scale,sizeof(pose.held_scale));
        std::memcpy(pose.view,view,sizeof(pose.view));
        pose.roll = live.camera.roll;
        pose.held_global_timer = recorded.global_timer;
    }
    sg_mesh mesh{scratch.position,scratch.normal,scratch.color,scratch.uv,scratch.material,
        SG_MODEL_MAX_VERTICES,0,0};
    // A hidden Mario draws nothing. The game never advanced his animation
    // (act_disappeared leaves frame -1), so the model is not asked to.
    int32_t status = p.hidden ? SG_OK : sg_model_evaluate(model,&pose,&mesh);
    // A held object the model refuses (its textures missing, an animation it
    // does not have, no room left in the mesh) is left out rather than
    // losing Mario.
    if ((status != SG_OK || mesh.error_flags) && (pose.fields & SG_POSE_HELD)) {
        pose.fields &= ~uint32_t(SG_POSE_HELD);
        mesh = {scratch.position,scratch.normal,scratch.color,scratch.uv,scratch.material,
            SG_MODEL_MAX_VERTICES,0,0};
        status = sg_model_evaluate(model,&pose,&mesh);
    }
    if (status == SG_CAPACITY) return Result::capacity;
    if (status != SG_OK || mesh.error_flags || mesh.vertex_count > SG_MODEL_MAX_VERTICES)
        return Result::model_failed;
    ghost_compositor::Pose built{};
    for (uint32_t i = 0; i < mesh.vertex_count; ++i) {
        const uint32_t material = scratch.material[i] & SG_MATERIAL_CODE;
        const uint32_t tile = scratch.material[i] >> SG_MATERIAL_TILE_SHIFT;
        if (material > SG_MATERIAL_MODULATE_RGBA || tile >= (SG_TILE_PRESENT << 1)) return Result::model_failed;
        auto &v = scratch.vertices[i];
        std::memcpy(v.position,scratch.position+3*i,sizeof(v.position));
        std::memcpy(v.normal,scratch.normal+3*i,sizeof(v.normal));
        std::memcpy(v.color,scratch.color+3*i,sizeof(v.color));
        std::memcpy(v.uv,scratch.uv+2*i,sizeof(v.uv));
        // Metal Mario's environment map follows the live view, as the game's
        // would from this camera.
        if ((scratch.material[i] & SG_MATERIAL_TEXGEN) && !environment_uv(view,world.lookat,v.normal,v.uv))
            return Result::model_failed;
        v.material = ghost_compositor::with_tile(static_cast<ghost_compositor::Material>(material), tile);
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
