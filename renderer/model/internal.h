#ifndef SG_MODEL_INTERNAL_H
#define SG_MODEL_INTERNAL_H
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#undef near
#undef far
#include <setjmp.h>
#include "model.h"
#include "decomp/global_state.h"
#include "decomp/game/camera.h"

#define SG_ANIMATION_COUNT 209
#define SG_PERMANENT_BYTES (512u * 1024u)
#define SG_SCRATCH_BYTES (256u * 1024u)

struct AllocOnlyPool { unsigned active, permanent; };
struct sg_particle_cell { uint32_t image, format, width, height, x, y; };
struct sg_particles {
    sg_particle_frame frames[SG_PARTICLE_FRAMES];
    sg_particle_vertex vertices[SG_PARTICLE_MAX_VERTICES];
    struct sg_particle_cell cells[SG_PARTICLE_CELLS];
    uint32_t frame_count, vertex_count, cell_count;
    uint8_t atlas[SG_PARTICLE_ATLAS_WIDTH * SG_PARTICLE_ATLAS_HEIGHT * 4];
};
struct sg_model {
    DWORD owner;
    struct GlobalState state;
    struct Object object;
    struct Area area;
    struct Camera camera;
    struct GraphNode *graph;
    struct Animation animations[SG_ANIMATION_COUNT];
    uint8_t atlas[SG_MODEL_ATLAS_BYTES];
    struct sg_particles particles;
    sg_body_lights body_lights;
    uint8_t *permanent, *scratch;
    size_t permanent_used, scratch_used;
    size_t scratch_limit;
    struct AllocOnlyPool pools[8];
    struct AllocOnlyPool *display_pool;
    unsigned building;
    jmp_buf abort;
    int abort_status;
    int16_t effective_head[3];
};

extern struct sg_model *sg_active;
int sg_assets_load(struct sg_model *, const uint8_t *, size_t);
void sg_assets_free(struct sg_model *);
int sg_particles_load(struct sg_particles *, const uint8_t *segment, uint32_t size);
/* Appends the common1 (segment 3) flame frames; a refusal leaves the loaded set. */
int sg_particles_add_common1(struct sg_particles *, const uint8_t *segment, uint32_t size);
void sg_abort(int status);
void sg_memory_reset(void);
const int16_t *sg_effective_head_rotation(void);
/* Staged renderer/adapter helpers. No upstream types escape model.h. */
void sg_geometry_bind(sg_mesh *mesh);
uint32_t sg_geometry_errors(void);
uint32_t sg_geometry_vertex_count(void);
void sg_geometry_reset(void);
void sg_traverse_frozen(struct GraphNode *node);
void sg_traversal_reset(void);

#endif
