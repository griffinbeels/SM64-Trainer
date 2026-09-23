#ifndef SM64_TRAINER_MODEL_H
#define SM64_TRAINER_MODEL_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct sg_model sg_model;

enum sg_status {
    SG_OK = 0, SG_INVALID_ARGUMENT = -1, SG_INVALID_ROM = -2,
    SG_OUT_OF_MEMORY = -3, SG_BUSY = -4, SG_WRONG_THREAD = -5,
    SG_INVALID_ANIMATION = -6, SG_CAPACITY = -7, SG_INVALID_GEOMETRY = -8,
    SG_INVALID_MODEL = -9
};

enum sg_pose_fields {
    SG_POSE_TRANSFORM = 1u, SG_POSE_ANIMATION = 2u, SG_POSE_BODY = 4u,
    SG_POSE_HEAD_ROTATION = 8u, SG_POSE_REQUIRED = 15u,
    SG_POSE_THROW_MATRIX = 16u, SG_POSE_HIDDEN = 32u
};

/* A native C value, not a packed wire format. Angles are signed SM64 turns.
 * position/angles are the DRAWN gfx transform. body_action is separate from
 * MarioState action. head_rotation is the effective geo-node XYZ rotation,
 * after camera/action rules. throw_matrix is the native row-major Mat4 layout
 * (translation in elements 12..14), replacing position/angles when present.
 * scale applies in both paths. Missing required fields are rejected.
 */
typedef struct sg_pose {
    uint32_t fields;
    uint32_t action;
    uint32_t body_action;
    uint32_t mario_flags;
    uint32_t area_update_counter; /* Captured area clock, not global_timer. */
    int32_t animation_id;
    float position[3];
    float scale[3];
    float throw_matrix[16];
    int16_t angles[3];
    int16_t torso_angles[3];
    int16_t head_angles[3];
    int16_t head_rotation[3];
    int16_t animation_frame;
    int16_t anim_y_translation;
    uint16_t model_state;
    uint8_t cap_state, eye_state, hand_state, wing_flutter;
    uint8_t punch_state, grab_position;
} sg_pose;

enum sg_mesh_errors {
    SG_MESH_BAD_POSE = 1u, SG_MESH_BAD_ANIMATION = 2u,
    SG_MESH_TOO_SMALL = 4u, SG_MESH_NONFINITE = 8u,
    SG_MESH_SCRATCH_EXHAUSTED = 16u, SG_MESH_UNSUPPORTED_MATERIAL = 32u
};

/* Active display-list combiner, with game fade replaced by renderer opacity.
 * ALPHA_MIX: mix(vertex RGB, texture RGB, texture alpha), opaque coverage.
 * DECAL: texture RGB; MODULATE: vertex RGB * texture RGB.
 * RGBA variants additionally use texture alpha for coverage; RGB ignores it.
 */
enum sg_material {
    SG_MATERIAL_SOLID = 0, SG_MATERIAL_ALPHA_MIX = 1,
    SG_MATERIAL_DECAL_RGBA = 2, SG_MATERIAL_DECAL_RGB = 3,
    SG_MATERIAL_MODULATE_RGB = 4, SG_MATERIAL_MODULATE_RGBA = 5,
    /* Flag on a material code: the triangle's texture coordinates are
     * generated from its normal (G_TEXTURE_GEN, the metal cap's environment
     * map), so the vertex uv is meaningless and the caller computes it under
     * its own view. Only the 64x32 metal texture at scale 0x0F80/0x07C0 is
     * admitted; anything else is SG_MESH_UNSUPPORTED_MATERIAL. */
    SG_MATERIAL_TEXGEN = 0x100
};

enum { SG_MODEL_MAX_VERTICES = 3072, SG_MODEL_ATLAS_WIDTH = 704,
       SG_MODEL_ATLAS_HEIGHT = 64, SG_MODEL_ATLAS_BYTES = 180224 };

/* Arrays are caller-owned, nonoverlapping: 3 floats per vertex for position,
 * normal and color; 2 floats per vertex for UV; one material per vertex, with
 * all three vertices of a triangle agreeing. Failure clears vertex_count.
 * Capacity is checked before each triangle is written. Hidden poses succeed
 * with vertex_count=0. Material color/UV follow libsm64's atlas convention.
 */
typedef struct sg_mesh {
    float *position, *normal, *color, *uv;
    uint32_t *material;
    uint32_t vertex_capacity;
    uint32_t vertex_count;
    uint32_t error_flags;
} sg_mesh;

/* Mario's particle models (bubble, splashes, water rings, droplet, dust,
 * sparkles, lava flame), one frame per executed US display list. Vertices are
 * model-local triangles; uv lies inside rect, the frame's clamped atlas cell;
 * rgba is the combiner's shade factor (white for decal frames). CUTOUT draws
 * texels with alpha, BLEND blends texture alpha times vertex alpha. */
enum { SG_PARTICLE_FRAMES = 56, SG_PARTICLE_MAX_VERTICES = 336, SG_PARTICLE_CELLS = 64,
       SG_PARTICLE_ATLAS_WIDTH = 512, SG_PARTICLE_ATLAS_HEIGHT = 256 };
enum sg_particle_layer { SG_PARTICLE_CUTOUT = 1, SG_PARTICLE_BLEND = 2 };
typedef struct sg_particle_vertex {
    float position[3], uv[2], rect[4];
    uint8_t rgba[4];
} sg_particle_vertex;
typedef struct sg_particle_frame {
    uint32_t display_list, layer, first_vertex, vertex_count;
} sg_particle_frame;
typedef struct sg_particle_view {
    const sg_particle_frame *frames;
    const sg_particle_vertex *vertices;
    const uint8_t *atlas_rgba;
    uint32_t frame_count, vertex_count, atlas_width, atlas_height;
} sg_particle_view;

typedef struct sg_atlas_view {
    const uint8_t *rgba;
    uint32_t width, height, animation_count;
} sg_atlas_view;

/* Exactly one runtime may be open in a linked module. Calls are nonblocking:
 * concurrent/reentrant operations return SG_BUSY. Operations belong to the
 * current owner thread, with explicit detach/adopt for handoff. Open does
 * bounded asset extraction and allocation; call
 * it outside steady-state drawing. ROM bytes are not retained after open.
 * Supported layout: 8 MiB big-endian US SM64 asset sections, structurally
 * validated here; caller additionally establishes desired ROM/version identity.
 */
int32_t sg_model_open(const uint8_t *rom, size_t rom_bytes, sg_model **out);
/* Validate only the bounded texture/animation assets, using temporary storage.
 * No runtime binding, ownership gate, graph, or global rendering state is touched;
 * this may run on the worker while an existing model belongs to the renderer. */
int32_t sg_model_validate_rom(const uint8_t *rom, size_t rom_bytes);
/* Ownership transfer only: no allocation, destruction, wait or graph mutation.
 * Detach requires the current owner and leaves the runtime without an owner.
 * Adopt claims a detached runtime for the calling thread; any existing owner,
 * including the caller, returns SG_WRONG_THREAD. While detached, atlas/view/
 * evaluate/close return SG_WRONG_THREAD. SG_BUSY and all other failures leave
 * ownership unchanged. The caller must publish the handle through its own
 * synchronized handoff; these operations do not publish or reclaim storage.
 * Previously borrowed atlas pointers remain allocated until close, but the
 * caller must finish their use before handing custody to a closing owner.
 */
int32_t sg_model_detach(sg_model *model);
int32_t sg_model_adopt(sg_model *model);
int32_t sg_model_get_atlas(sg_model *model, sg_atlas_view *out);
/* Zero frames when the ROM's particle lists were not the supported ones;
 * Mario's own model still opens. Same owner/lifetime rules as the atlas. */
int32_t sg_model_get_particles(sg_model *model, sg_particle_view *out);
int32_t sg_model_evaluate(sg_model *model, const sg_pose *pose, sg_mesh *out);
/* Exact upstream SM64 look-at, including its sine table. Native Mat4 layout,
 * translation at 12..14; no N64 quantization or projection is applied. Rejects
 * nonfinite, coincident and vertical-degenerate camera pairs. Clears output on
 * failure. The same owner-thread/lifetime rules apply as evaluate.
 */
int32_t sg_model_view(sg_model *model, const float position[3],
    const float focus[3], int16_t roll, float out_matrix[16]);
/* On success clears *model; a NULL *model is a successful no-op. Atlas pointers
 * expire at close. A handle must not be reused or copied across close/reopen;
 * foreign pointers are rejected before dereferencing.
 */
int32_t sg_model_close(sg_model **model);

#ifdef __cplusplus
}
#endif
#endif
