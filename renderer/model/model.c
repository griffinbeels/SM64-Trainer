#include "internal.h"
#include "decomp/engine/math_util.h"
#include "decomp/engine/graph_node.h"
#include "decomp/engine/geo_layout.h"
#include "decomp/game/rendering_graph_node.h"
#include "decomp/mario/geo.inc.h"
#include "decomp/memory.h"
#include <stdlib.h>
#include <string.h>
#include <float.h>

struct sg_model *sg_active;
static volatile LONG sg_gate;

static int enter(void) { return InterlockedCompareExchange(&sg_gate, 1, 0) == 0; }
static int leave(int status) { InterlockedExchange(&sg_gate, 0); return status; }
static int valid_model(sg_model *model)
{
    if (!model || model != sg_active) return SG_INVALID_MODEL;
    return model->owner == GetCurrentThreadId() ? SG_OK : SG_WRONG_THREAD;
}
static int finite_bound(float value, float bound)
{ return value >= -bound && value <= bound; }

void sg_abort(int status)
{
    sg_active->abort_status = status;
    longjmp(sg_active->abort, 1);
}

const int16_t *sg_effective_head_rotation(void) { return sg_active->effective_head; }

static void release_model(sg_model *model)
{
    sg_traversal_reset();
    sg_geometry_reset();
    g_state = NULL;
    sg_assets_free(model);
    free(model->permanent);
    free(model->scratch);
    free(model);
    sg_active = NULL;
}

int32_t sg_model_open(const uint8_t *rom, size_t bytes, sg_model **out)
{
    sg_model *model;
    int status;
    Vec3f zero = {0, 0, 0}, unit = {1, 1, 1};
    Vec3s angles = {0, 0, 0};
    if (!out) return SG_INVALID_ARGUMENT;
    if (!enter()) return SG_BUSY;
    if (sg_active) return leave(SG_BUSY);
    *out = NULL;
    model = calloc(1, sizeof(*model));
    if (!model) return leave(SG_OUT_OF_MEMORY);
    model->owner = GetCurrentThreadId();
    status = sg_assets_load(model, rom, bytes);
    if (status != SG_OK) {
        sg_assets_free(model);
        free(model);
        return leave(status);
    }
    model->permanent = malloc(SG_PERMANENT_BYTES);
    model->scratch = malloc(SG_SCRATCH_BYTES);
    model->scratch_limit = SG_SCRATCH_BYTES;
    sg_active = model;
    if (!model->permanent || !model->scratch) {
        release_model(model);
        return leave(SG_OUT_OF_MEMORY);
    }
    g_state = &model->state;
    if (setjmp(model->abort)) {
        status = model->abort_status;
        release_model(model);
        return leave(status);
    }
    model->state.msSwimStrength = MIN_SWIM_STRENGTH;
    model->state.mgCurrentArea = &model->area;
    model->state.mgCurrentObject = model->state.mgMarioObject = &model->object;
    model->area.flags = 1;
    model->area.camera = &model->camera;
    /* The visual links/defaults from init_mario_from_save_file/init_mario,
     * without compiling or calling the simulation/save/audio dependency tree.
     */
    model->state.mgMarioStateVal.area = &model->area;
    model->state.mgMarioStateVal.marioObj = &model->object;
    model->state.mgMarioStateVal.marioBodyState = &model->state.mgBodyStates[0];
    model->state.mgMarioStateVal.animation = &model->state.mD_80339D10;
    model->state.mgMarioStateVal.controller = &model->state.mgController;
    model->state.mgMarioStateVal.spawnInfo = &model->state.mgMarioSpawnInfoVal;
    model->state.mgMarioStateVal.unkB0 = 189;
    init_graph_node_object(NULL, &model->object.header.gfx, NULL, zero, angles, unit);
    model->object.header.gfx.animInfo.animID = -1;
    model->object.header.gfx.animInfo.animAccel = 0;
    memory_init();
    model->building = 1;
    model->graph = process_geo_layout(alloc_only_pool_init(), mario_geo_ptr);
    if (model->graph && sg_held_build(model) != SG_OK) model->held_available = 0;
    model->building = 0;
    if (!model->graph) {
        release_model(model);
        return leave(SG_INVALID_GEOMETRY);
    }
    *out = model;
    return leave(SG_OK);
}

int32_t sg_model_detach(sg_model *model)
{
    int status;
    if (!enter()) return SG_BUSY;
    status = valid_model(model);
    if (status == SG_OK) model->owner = 0;
    return leave(status);
}

int32_t sg_model_adopt(sg_model *model)
{
    if (!enter()) return SG_BUSY;
    if (!model || model != sg_active) return leave(SG_INVALID_MODEL);
    if (model->owner) return leave(SG_WRONG_THREAD);
    model->owner = GetCurrentThreadId();
    return leave(SG_OK);
}

int32_t sg_model_get_atlas(sg_model *model, sg_atlas_view *out)
{
    int status;
    if (!out) return SG_INVALID_ARGUMENT;
    memset(out, 0, sizeof(*out));
    if (!enter()) return SG_BUSY;
    status = valid_model(model);
    if (status == SG_OK) {
        out->rgba = model->atlas;
        out->width = SG_MODEL_ATLAS_WIDTH;
        out->height = SG_MODEL_ATLAS_HEIGHT;
        out->animation_count = SG_ANIMATION_COUNT;
    }
    return leave(status);
}

int32_t sg_model_get_particles(sg_model *model, sg_particle_view *out)
{
    int status;
    if (!out) return SG_INVALID_ARGUMENT;
    memset(out, 0, sizeof(*out));
    if (!enter()) return SG_BUSY;
    status = valid_model(model);
    if (status == SG_OK) {
        out->frames = model->particles.frames;
        out->vertices = model->particles.vertices;
        out->atlas_rgba = model->particles.atlas;
        out->frame_count = model->particles.frame_count;
        out->vertex_count = model->particles.vertex_count;
        out->atlas_width = SG_PARTICLE_ATLAS_WIDTH;
        out->atlas_height = SG_PARTICLE_ATLAS_HEIGHT;
    }
    return leave(status);
}

int32_t sg_model_get_body_lights(sg_model *model, sg_body_lights *out)
{
    int status;
    if (!out) return SG_INVALID_ARGUMENT;
    memset(out, 0, sizeof(*out));
    if (!enter()) return SG_BUSY;
    status = valid_model(model);
    if (status == SG_OK) *out = model->body_lights;
    return leave(status);
}

int32_t sg_model_get_held_lights(sg_model *model, uint32_t kind, sg_body_lights *out)
{
    int status;
    if (!out) return SG_INVALID_ARGUMENT;
    memset(out, 0, sizeof(*out));
    if (!enter()) return SG_BUSY;
    status = valid_model(model);
    if (status == SG_OK && (kind == SG_HELD_NONE || kind >= SG_HELD_KINDS)) status = SG_INVALID_ARGUMENT;
    if (status == SG_OK) *out = model->held_lights[kind];
    return leave(status);
}

int32_t sg_model_get_animation_frames(sg_model *model, int32_t animation_id, int32_t *frames)
{
    int status;
    if (!frames) return SG_INVALID_ARGUMENT;
    *frames = 0;
    if (animation_id < 0 || animation_id >= SG_ANIMATION_COUNT) return SG_INVALID_ANIMATION;
    if (!enter()) return SG_BUSY;
    status = valid_model(model);
    if (status == SG_OK) *frames = model->animations[animation_id].loopEnd;
    return leave(status);
}

static int valid_pose(const sg_pose *pose)
{
    int i;
    if (!pose || (pose->fields & SG_POSE_REQUIRED) != SG_POSE_REQUIRED ||
        (pose->fields & ~255u) || pose->animation_frame < 0 ||
        pose->cap_state > 3 || pose->eye_state > 8 ||
        (pose->eye_state >= 4 && pose->eye_state <= 7) ||
        pose->hand_state > 5 || pose->wing_flutter > 1 || pose->grab_position > 3 ||
        pose->model_state > 0x3ff || (pose->punch_state >> 6) > 2 ||
        (pose->punch_state & 63) > 5) return 0;
    for (i = 0; i < 3; ++i)
        if (!finite_bound(pose->position[i], 10000000.0f) ||
            !finite_bound(pose->scale[i], 100.0f) ||
            (pose->scale[i] > -0.000001f && pose->scale[i] < 0.000001f)) return 0;
    if ((pose->fields & SG_POSE_HELD_RELEASED) && !(pose->fields & SG_POSE_HELD)) return 0;
    if (pose->fields & SG_POSE_HELD) {
        if (pose->held_kind == SG_HELD_NONE || pose->held_kind >= SG_HELD_KINDS ||
            pose->held_animation_id < -1 || pose->held_animation_frame < 0) return 0;
        for (i = 0; i < 3; ++i)
            if (!finite_bound(pose->held_position[i], 10000000.0f) ||
                !finite_bound(pose->held_scale[i], 100.0f) ||
                (pose->held_scale[i] > -0.000001f && pose->held_scale[i] < 0.000001f)) return 0;
        for (i = 0; i < 16; ++i) if (!finite_bound(pose->view[i], 10000000.0f)) return 0;
    }
    if (pose->fields & SG_POSE_THROW_MATRIX) {
        double det;
        const float *m = pose->throw_matrix;
        for (i = 0; i < 16; ++i) if (!finite_bound(m[i], 10000000.0f)) return 0;
        if (m[3] != 0 || m[7] != 0 || m[11] != 0 || m[15] != 1) return 0;
        det = (double)m[0] * (m[5]*m[10]-m[6]*m[9]) -
              (double)m[1] * (m[4]*m[10]-m[6]*m[8]) +
              (double)m[2] * (m[4]*m[9]-m[5]*m[8]);
        if (!(det > 1e-18 || det < -1e-18)) return 0;
    }
    return 1;
}

static int valid_buffers(const sg_mesh *mesh)
{
    uintptr_t starts[5];
    size_t lengths[5];
    unsigned i, j;
    if (!mesh->position || !mesh->normal || !mesh->color || !mesh->uv || !mesh->material ||
        !mesh->vertex_capacity || mesh->vertex_capacity > SG_MODEL_MAX_VERTICES) return 0;
    starts[0] = (uintptr_t)mesh->position; starts[1] = (uintptr_t)mesh->normal;
    starts[2] = (uintptr_t)mesh->color; starts[3] = (uintptr_t)mesh->uv;
    starts[4] = (uintptr_t)mesh->material;
    for (i = 0; i < 5; ++i) {
        lengths[i] = (size_t)mesh->vertex_capacity * (i < 3 ? 3u : i == 3 ? 2u : 1u) * sizeof(uint32_t);
        if ((starts[i] % sizeof(float)) || starts[i] > UINTPTR_MAX - lengths[i]) return 0;
        for (j = 0; j < i; ++j)
            if (starts[i] < starts[j] + lengths[j] && starts[j] < starts[i] + lengths[i]) return 0;
    }
    return 1;
}

static void restore_pose(sg_model *model, const sg_pose *pose)
{
    struct MarioState *m = &model->state.mgMarioStateVal;
    struct MarioBodyState *body = m->marioBodyState;
    struct AnimInfo *info = &model->object.header.gfx.animInfo;
    unsigned i;
    memset(model->state.mgBodyStates, 0, sizeof(model->state.mgBodyStates));
    body->action = pose->body_action;
    body->capState = pose->cap_state; body->eyeState = pose->eye_state;
    body->handState = pose->hand_state; body->wingFlutter = pose->wing_flutter;
    body->punchState = pose->punch_state; body->grabPos = pose->grab_position;
    body->modelState = pose->model_state;
    m->action = pose->action; m->flags = pose->mario_flags;
    for (i = 0; i < 3; ++i) {
        body->torsoAngle[i] = pose->torso_angles[i]; body->headAngle[i] = pose->head_angles[i];
        model->effective_head[i] = pose->head_rotation[i];
        model->object.header.gfx.pos[i] = m->pos[i] = pose->position[i];
        model->object.header.gfx.angle[i] = m->faceAngle[i] = pose->angles[i];
        model->object.header.gfx.scale[i] = pose->scale[i];
    }
    /* Upstream's signed attack counter must equal its unsigned visual counter.
     * All current blink/wing periods divide 64, preserved by this mask.
     */
    model->state.mgAreaUpdateCounter = (u16)(pose->area_update_counter & 0x7fff);
    model->state.msMarioAttackAnimCounter = (s16)model->state.mgAreaUpdateCounter;
    info->animID = (s16)pose->animation_id;
    info->curAnim = &model->animations[pose->animation_id];
    info->animFrame = pose->animation_frame; info->animYTrans = pose->anim_y_translation;
    info->animAccel = 0; info->animFrameAccelAssist = (s32)pose->animation_frame * 65536;
    info->animTimer = model->state.mgAreaUpdateCounter;
}

int32_t sg_model_evaluate(sg_model *model, const sg_pose *pose, sg_mesh *out)
{
    int status;
    size_t i;
    Mat4 transform, mario_root;
    if (!out) return SG_INVALID_ARGUMENT;
    out->vertex_count = out->error_flags = out->held_first_vertex = 0;
    if (!enter()) return SG_BUSY;
    status = valid_model(model);
    if (status != SG_OK) return leave(status);
    if (!valid_pose(pose) || !valid_buffers(out)) {
        out->error_flags = SG_MESH_BAD_POSE;
        return leave(SG_INVALID_ARGUMENT);
    }
    if (pose->animation_id < 0 || pose->animation_id >= SG_ANIMATION_COUNT ||
        pose->animation_frame >= model->animations[pose->animation_id].loopEnd) {
        out->error_flags = SG_MESH_BAD_ANIMATION;
        return leave(SG_INVALID_ANIMATION);
    }
    g_state = &model->state;
    restore_pose(model, pose);
    model->object.header.gfx.throwMatrix = NULL;
    if (pose->fields & SG_POSE_HIDDEN) return leave(SG_OK);
    if (setjmp(model->abort)) {
        model->object.header.gfx.throwMatrix = NULL;
        /* An abort inside the held object's traversal leaves it as Mario. */
        model->held_object.header.gfx.throwMatrix = NULL;
        model->state.mgMarioObject = &model->object;
        sg_traversal_reset(); sg_geometry_reset(); sg_memory_reset();
        out->error_flags = SG_MESH_SCRATCH_EXHAUSTED;
        return leave(model->abort_status);
    }
    if ((pose->fields & SG_POSE_HELD) && !(model->held_available & (1u << pose->held_kind))) {
        out->error_flags = SG_MESH_UNSUPPORTED_MATERIAL;
        return leave(SG_INVALID_GEOMETRY);
    }
    /* Mario's root as sg_traverse_frozen composes it (with his scale): what
     * his hand hands a held object. */
    if (pose->fields & SG_POSE_THROW_MATRIX) {
        memcpy(transform, pose->throw_matrix, sizeof(transform));
        model->object.header.gfx.throwMatrix = &transform;
        memcpy(mario_root, transform, sizeof(mario_root));
    } else {
        mtxf_rotate_zxy_and_translate(mario_root, model->object.header.gfx.pos, model->object.header.gfx.angle);
    }
    mtxf_scale_vec3f(mario_root, mario_root, model->object.header.gfx.scale);
    sg_geometry_bind(out);
    sg_traverse_frozen(model->graph);
    model->object.header.gfx.throwMatrix = NULL;
    out->held_first_vertex = sg_geometry_vertex_count();
    sg_held_evaluate(model, pose, (const float (*)[4])mario_root);
    out->error_flags = sg_geometry_errors();
    out->vertex_count = sg_geometry_vertex_count();
    if (!(pose->fields & SG_POSE_HELD)) out->held_first_vertex = out->vertex_count;
    sg_geometry_reset();
    restore_pose(model, pose);
    if (out->error_flags) {
        out->vertex_count = 0;
        return leave((out->error_flags & SG_MESH_TOO_SMALL) ? SG_CAPACITY : SG_INVALID_GEOMETRY);
    }
    for (i = 0; i < (size_t)out->vertex_count * 3; ++i)
        if (!finite_bound(out->position[i], FLT_MAX) || !finite_bound(out->normal[i], FLT_MAX) ||
            !finite_bound(out->color[i], FLT_MAX)) goto bad_geometry;
    for (i = 0; i < (size_t)out->vertex_count * 2; ++i)
        if (!finite_bound(out->uv[i], FLT_MAX)) goto bad_geometry;
    if (!out->vertex_count) goto bad_geometry;
    return leave(SG_OK);
bad_geometry:
    out->vertex_count = 0; out->error_flags = SG_MESH_NONFINITE;
    return leave(SG_INVALID_GEOMETRY);
}

int32_t sg_model_view(sg_model *model, const float position[3], const float focus[3],
    int16_t roll, float out_matrix[16])
{
    int status, i;
    Vec3f from, to;
    Mat4 matrix;
    double dx, dz;
    if (!out_matrix) return SG_INVALID_ARGUMENT;
    if (position) memcpy(from, position, sizeof(from));
    if (focus) memcpy(to, focus, sizeof(to));
    memset(out_matrix, 0, 16 * sizeof(float));
    if (!enter()) return SG_BUSY;
    status = valid_model(model);
    if (status != SG_OK) return leave(status);
    if (!position || !focus) return leave(SG_INVALID_ARGUMENT);
    for (i = 0; i < 3; ++i)
        if (!finite_bound(from[i], 10000000.0f) || !finite_bound(to[i], 10000000.0f))
            return leave(SG_INVALID_ARGUMENT);
    dx = (double)to[0] - from[0]; dz = (double)to[2] - from[2];
    if (dx*dx + dz*dz < 1e-12) return leave(SG_INVALID_ARGUMENT);
    mtxf_lookat(matrix, from, to, roll);
    for (i = 0; i < 16; ++i)
        if (!finite_bound(((float *)matrix)[i], FLT_MAX)) return leave(SG_INVALID_ARGUMENT);
    memcpy(out_matrix, matrix, sizeof(matrix));
    return leave(SG_OK);
}

int32_t sg_model_close(sg_model **model)
{
    int status;
    if (!model) return SG_INVALID_ARGUMENT;
    if (!*model) return SG_OK;
    if (!enter()) return SG_BUSY;
    status = valid_model(*model);
    if (status != SG_OK) return leave(status);
    release_model(*model);
    *model = NULL;
    return leave(SG_OK);
}

#ifdef SG_MODEL_TESTING
int sg_model_test_enter(void) { return enter(); }
void sg_model_test_leave(void) { leave(SG_OK); }
void sg_model_test_scratch_limit(size_t limit) { if (sg_active) sg_active->scratch_limit = limit; }
size_t sg_model_test_scratch_used(void) { return sg_active ? sg_active->scratch_used : 0; }
#endif
