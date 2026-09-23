/* The object Mario holds, drawn into his mesh the way geo_process_held_object
 * draws it from his hand: his own root rows (with his scale), each scaled by
 * the object's scale, at the HOLP. The graph runs in the given view's camera
 * space so its billboards face that camera (mtxf_billboard), then every
 * vertex returns to world space by the rigid inverse of that view.
 */
#include "internal.h"
#include "decomp/shim.h"
#include "decomp/engine/geo_layout.h"
#include "decomp/engine/math_util.h"
#include "decomp/game/rendering_graph_node.h"
#include "decomp/include/object_fields.h"
#include <string.h>

extern void *sg_bobomb_geo;
extern const struct Animation *const *sg_bobomb_animations;

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

int sg_held_build(struct sg_model *model)
{
    Vec3f zero = {0, 0, 0}, unit = {1, 1, 1};
    Vec3s angles = {0, 0, 0};
    init_graph_node_object(NULL, &model->held_object.header.gfx, NULL, zero, angles, unit);
    model->held_graph[SG_HELD_BOBOMB] = process_geo_layout(alloc_only_pool_init(), sg_bobomb_geo);
    model->held_animations[SG_HELD_BOBOMB] = sg_bobomb_animations;
    return model->held_graph[SG_HELD_BOBOMB] ? SG_OK : SG_INVALID_GEOMETRY;
}

/* Animation count of a NULL-terminated table (at most 64). */
static int animation_count(const struct Animation *const *table)
{
    int count = 0;
    while (table && count < 64 && table[count]) ++count;
    return count;
}

void sg_held_evaluate(struct sg_model *model, const sg_pose *pose, const float mario_root[4][4])
{
    struct Object *held = &model->held_object;
    struct AnimInfo *info = &held->header.gfx.animInfo;
    const struct Animation *const *table;
    const struct Animation *animation;
    struct GraphNodeCamera camera;
    struct Object *mario = gMarioObject;
    Mat4 root, view, local;
    uint32_t first = sg_geometry_vertex_count(), last, i;
    int row, column;
    if (!(pose->fields & SG_POSE_HELD)) return;
    if (pose->held_kind == SG_HELD_NONE || pose->held_kind >= SG_HELD_KINDS
            || !(model->held_available & (1u << pose->held_kind)) || !model->held_graph[pose->held_kind]) {
        sg_abort(SG_INVALID_ARGUMENT);
    }
    table = model->held_animations[pose->held_kind];
    if (pose->held_animation_id < 0 || pose->held_animation_id >= animation_count(table)) sg_abort(SG_INVALID_ANIMATION);
    animation = table[pose->held_animation_id];
    if (pose->held_animation_frame < 0 || pose->held_animation_frame >= animation->loopEnd) sg_abort(SG_INVALID_ANIMATION);
    /* Mario's rows, translation at the HOLP; the traversal scales rows by the
     * object's own scale, as geo_process_held_object's mtxf_scale_vec3f. */
    memcpy(local, mario_root, sizeof(local));
    for (column = 0; column < 3; ++column) local[3][column] = pose->held_position[column];
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
    sg_traverse_frozen(model->held_graph[pose->held_kind]);
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
