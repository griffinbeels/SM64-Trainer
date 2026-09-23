/* GhostV2 is independent of the video SourceV2 stamp/coalescing ABI. One value
 * is offered per rendered DList, including refusals. No emulation waits.
 * Lifecycle and consumer contract: docs/ghost-native-runtime.md.
 */
#pragma once
#include "ghost_capture.h"
#include <stdint.h>

#define GS_VERSION 5u
#define GS_SLOTS 128u
#define GS_CAP_POSE 1u
#define GS_CAP_WORLD_PASS 2u
#define GS_CAP_RENDER 4u
#ifndef __cdecl
#if !defined(_MSC_VER)
#define __cdecl
#endif
#endif

#pragma pack(push, 4)
typedef struct gs_world {
    uint32_t status, context_generation, triangles, color, depth, width, height;
    uint32_t samples, depth_mode;
    int32_t viewport[4];
    float depth_scale[2], screen_scale[2], projection[16];
} gs_world;
typedef struct gs_record {
    uint32_t bytes, version, configuration, reserved;
    gc_sample sample;
    gs_world world;
} gs_record;
typedef struct gs_stats {
    uint32_t bytes, version, epoch, configuration;
    uint32_t offered, dropped, taken, busy, enabled;
    uint32_t particles_dropped; /* Mario's particles drawn beyond the 16 kept */
    /* The last completed list's render hooks: driver state queries they made
     * (zero once the dispatch has observed the raster state) and the
     * microseconds spent inside them on the emulation thread. */
    uint32_t hook_gl_queries, hook_micros;
} gs_stats;
#pragma pack(pop)

typedef struct gs_api {
    uint32_t bytes, version, capabilities, reserved;
    /* One serialized control writer. A valid config starts a fresh capture
     * identity at the next DList; a malformed config disables capture.
     * Passing NULL disables. Does not access RAM, GL, files, or the model.
     */
    int (__cdecl *configure)(const gc_layout *);
    void (__cdecl *disable)(void);
    /* One consumer, copies one record and releases that slot. Empty returns0.
     * -1 means malformed output size. No caller-retained internal pointers.
     */
    int (__cdecl *read)(gs_record *, uint32_t bytes);
    gs_stats (__cdecl *stats)(void);
} gs_api;

#ifdef __cplusplus
#include "ghost_pass.h"
namespace ghost_stream {
const gs_api *api();
bool requested(); // render thread, one atomic load; no timing while idle
void cancel(); // render thread: invalidate any provisional/finalized display candidate
void rom(bool opened);
// Called inside the serialized renderer DList command, before RSP processing.
bool begin(const uint8_t *rdram, uint32_t bytes, uint32_t context_generation, int64_t qpc);
void projection(const float matrix[16]);
void lookat(const ghost::Lookat &);
void triangles(const ghost::RasterTarget &, uint32_t count, bool zbuffer);
bool geometry(bool was_zbuffer, bool is_zbuffer);
void color_target_changed();
void display_list(uint32_t pc, uint32_t dl, uint32_t physical_dl, uint32_t depth, const float *matrix);
void end(bool completed);
// Renderer thread, once per list: the hooks' own cost for that list.
void hooks(uint32_t gl_queries, uint32_t micros);
const gc_sample *sample();
const ghost::WorldPass *candidate();
}
#endif
