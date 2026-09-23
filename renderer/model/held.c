/* The object Mario holds, drawn into his mesh the way geo_process_held_object
 * draws it from his hand: his own root rows (with his scale), each scaled by
 * the object's scale, at the HOLP. The graph runs in the given view's camera
 * space so its billboards face that camera (mtxf_billboard), then every
 * vertex returns to world space by the rigid inverse of that view.
 * The models are HELD_MODELS in tools/build_model.py (held_cells.h).
 */
#include "internal.h"
#include "decomp/shim.h"
#include "decomp/engine/geo_layout.h"
#include "decomp/engine/math_util.h"
#include "decomp/game/rendering_graph_node.h"
#include "decomp/include/object_fields.h"
#include "decomp/held/held_cells.h"
#include <string.h>

#define DECLARE_HELD(name, kind) \
    extern void *sg_##name##_geo; \
    extern const struct Animation *const *sg_##name##_animations; \
    extern const int sg_##name##_animation_count;
SG_HELD_MODELS(DECLARE_HELD)

/* object_helpers.c: a switch on the object's oAnimState (the Bob-omb's blink). */
Gfx *geo_switch_anim_state(s32 callContext, struct GraphNode *node, void *context)
{
    struct GraphNodeSwitchCase *switchCase = (struct GraphNodeSwitchCase *)node;
    struct Object *obj = (struct Object *)gCurGraphNodeObject;
    (void)context;
    if (callContext == GEO_CONTEXT_RENDER && obj) {
        if (obj->oAnimState >= switchCase->numCases) obj->oAnimState = 0;
        switchCase->selectedCase = (s16)obj->oAnimState;
    }
    return NULL;
}

/* behaviors/tuxie.c: the penguins' blink, on gGlobalTimer. Tuxie's mother's
 * angry eyes (case 3) are hers alone; she is never held. */
Gfx *geo_switch_tuxie_mother_eyes(s32 callContext, struct GraphNode *node, void *context)
{
    struct GraphNodeSwitchCase *switchCase = (struct GraphNodeSwitchCase *)node;
    s32 timer;
    (void)context;
    if (callContext == GEO_CONTEXT_RENDER) {
        timer = gGlobalTimer % 50;
        if (timer < 43) switchCase->selectedCase = 0;
        else if (timer < 45) switchCase->selectedCase = 1;
        else if (timer < 47) switchCase->selectedCase = 2;
        else switchCase->selectedCase = 1;
    }
    return NULL;
}

/* King Bob-omb's and Chuckya's hands place a Mario THEY hold; held by
 * Mario, they hold nothing, and the node draws nothing either way. */
Gfx *geo_update_held_mario_pos(s32 callContext, struct GraphNode *node, void *context)
{
    (void)callContext; (void)node; (void)context;
    return NULL;
}

/* One held kind's gdSPDefLights1 table (held_cells.h), as a vertex's diffuse
 * and ambient colours name them. Two lights sharing both colours with
 * different directions leave none: the colours would not say which lit it. */
static void add_held_light(struct sg_model *model, unsigned kind, int ar, int ag, int ab,
                           int r, int g, int b, int x, int y, int z)
{
    sg_body_lights *lights = &model->held_lights[kind];
    sg_body_light entry;
    uint32_t i;
    if (lights->count == UINT32_MAX) return;
    entry.ambient[0] = (uint8_t)ar; entry.ambient[1] = (uint8_t)ag; entry.ambient[2] = (uint8_t)ab;
    entry.diffuse[0] = (uint8_t)r; entry.diffuse[1] = (uint8_t)g; entry.diffuse[2] = (uint8_t)b;
    entry.direction[0] = (int8_t)x; entry.direction[1] = (int8_t)y; entry.direction[2] = (int8_t)z;
    for (i = 0; i < lights->count; ++i)
        if (!memcmp(lights->lights[i].diffuse, entry.diffuse, 3) && !memcmp(lights->lights[i].ambient, entry.ambient, 3)) {
            if (memcmp(&lights->lights[i], &entry, sizeof(entry))) lights->count = UINT32_MAX;
            return;
        }
    if (lights->count < SG_BODY_LIGHTS) lights->lights[lights->count++] = entry;
    else lights->count = UINT32_MAX;
}

int sg_held_build(struct sg_model *model)
{
    Vec3f zero = {0, 0, 0}, unit = {1, 1, 1};
    Vec3s angles = {0, 0, 0};
    int status = SG_OK;
    /* One permanent pool for every held graph: the model has eight pools. */
    struct AllocOnlyPool *pool = alloc_only_pool_init();
    init_graph_node_object(NULL, &model->held_object.header.gfx, NULL, zero, angles, unit);
#define BUILD_HELD(name, kind) \
    model->held_graph[kind] = process_geo_layout(pool, sg_##name##_geo); \
    model->held_animations[kind] = sg_##name##_animations; \
    model->held_animation_count[kind] = sg_##name##_animation_count; \
    if (!model->held_graph[kind]) status = SG_INVALID_GEOMETRY;
    SG_HELD_MODELS(BUILD_HELD)
#undef BUILD_HELD
    {
        unsigned kind;
        memset(model->held_lights, 0, sizeof(model->held_lights));
#define HELD_LIGHT(kind, ar, ag, ab, r, g, b, x, y, z) add_held_light(model, kind, ar, ag, ab, r, g, b, x, y, z);
        SG_HELD_LIGHTS(HELD_LIGHT)
#undef HELD_LIGHT
        for (kind = 0; kind < SG_HELD_KINDS; ++kind)
            if (model->held_lights[kind].count == UINT32_MAX) memset(&model->held_lights[kind], 0, sizeof(sg_body_lights));
    }
    return status;
}

void sg_held_evaluate(struct sg_model *model, const sg_pose *pose, const float mario_root[4][4])
{
    struct Object *held = &model->held_object;
    struct AnimInfo *info = &held->header.gfx.animInfo;
    const struct Animation *animation;
    struct GraphNodeCamera camera;
    struct Object *mario = gMarioObject;
    u32 global_timer = gGlobalTimer;
    Mat4 root, view, local;
    uint32_t first = sg_geometry_vertex_count(), last, i;
    int row, column;
    if (!(pose->fields & SG_POSE_HELD)) return;
    if (pose->held_kind == SG_HELD_NONE || pose->held_kind >= SG_HELD_KINDS
            || !(model->held_available & (1u << pose->held_kind)) || !model->held_graph[pose->held_kind]) {
        sg_abort(SG_INVALID_ARGUMENT);
    }
    /* An object without animations (a box) is held with id -1, frame 0. */
    if (!model->held_animation_count[pose->held_kind]) {
        if (pose->held_animation_id != -1 || pose->held_animation_frame) sg_abort(SG_INVALID_ANIMATION);
        animation = NULL;
    } else {
        if (pose->held_animation_id < 0 || pose->held_animation_id >= model->held_animation_count[pose->held_kind])
            sg_abort(SG_INVALID_ANIMATION);
        animation = model->held_animations[pose->held_kind][pose->held_animation_id];
        if (pose->held_animation_frame < 0 || pose->held_animation_frame >= animation->loopEnd)
            sg_abort(SG_INVALID_ANIMATION);
    }
    /* In his hand: Mario's rows, translation at the HOLP. Released: its own
     * node's rotate-and-translate, as geo_process_object. Either way the
     * traversal scales rows by the object's own scale (mtxf_scale_vec3f). */
    if (pose->fields & SG_POSE_HELD_RELEASED) {
        Vec3f position; Vec3s angles;
        for (column = 0; column < 3; ++column) {
            position[column] = pose->held_position[column]; angles[column] = pose->held_angles[column];
        }
        mtxf_rotate_zxy_and_translate(local, position, angles);
    } else {
        memcpy(local, mario_root, sizeof(local));
        for (column = 0; column < 3; ++column) local[3][column] = pose->held_position[column];
    }
    memcpy(view, pose->view, sizeof(view));
    mtxf_mul(root, local, view);
    info->animID = pose->held_animation_id;
    info->curAnim = (struct Animation *)animation;
    info->animFrame = pose->held_animation_frame;
    info->animYTrans = pose->held_anim_y_translation;
    info->animAccel = 0; info->animFrameAccelAssist = (s32)pose->held_animation_frame * 65536;
    info->animTimer = g_state->mgAreaUpdateCounter;
    for (column = 0; column < 3; ++column) held->header.gfx.scale[column] = pose->held_scale[column];
    held->oAnimState = pose->held_anim_state;
    held->header.gfx.throwMatrix = &root;
    memset(&camera, 0, sizeof(camera));
    camera.roll = pose->roll;
    gMarioObject = held;
    gCurGraphNodeObject = &held->header.gfx;
    gCurGraphNodeCamera = &camera;
    gGlobalTimer = pose->held_global_timer;
    sg_traverse_frozen(model->held_graph[pose->held_kind]);
    gGlobalTimer = global_timer;
    gCurGraphNodeCamera = NULL;
    gCurGraphNodeObject = NULL;
    gMarioObject = mario;
    held->header.gfx.throwMatrix = NULL;
    /* Camera space back to world: p = (p' - t) R^T for the rigid look-at. */
    last = sg_geometry_vertex_count();
    for (i = first; i < last; ++i) {
        float *p = sg_geometry_position(i), *n = sg_geometry_normal(i), q[3], m[3];
        for (row = 0; row < 3; ++row) { q[row] = p[row] - view[3][row]; m[row] = n[row]; }
        for (row = 0; row < 3; ++row) {
            p[row] = q[0] * view[row][0] + q[1] * view[row][1] + q[2] * view[row][2];
            n[row] = m[0] * view[row][0] + m[1] * view[row][1] + m[2] * view[row][2];
        }
    }
}
