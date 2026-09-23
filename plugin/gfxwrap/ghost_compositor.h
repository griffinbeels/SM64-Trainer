/* Renderer-thread-only private display branch. No game objects or GL headers. */
#pragma once
#include <stddef.h>
#include <stdint.h>
#include "ghost_occlusion.h"

#ifdef _WIN32
#define GC_GL_CALL __stdcall
#else
#define GC_GL_CALL
#endif

/* Supply the renderer's installed dispatch, including its observation wrappers.
 * The compositor never loads a second dispatch or drains glGetError. */
#define GC_GL_FUNCTIONS(X) \
 X(void, GetIntegerv, (uint32_t, int32_t*)) \
 X(void, GetBooleanv, (uint32_t, uint8_t*)) \
 X(void, GetFloatv, (uint32_t, float*)) \
 X(void, GetDoublev, (uint32_t, double*)) \
 X(void, GetIntegeri_v, (uint32_t,uint32_t,int32_t*)) X(void, GetBooleani_v, (uint32_t,uint32_t,uint8_t*)) \
 X(void, GetFloati_v, (uint32_t,uint32_t,float*)) X(void, GetDoublei_v, (uint32_t,uint32_t,double*)) \
 X(void, Enablei, (uint32_t,uint32_t)) X(void, Disablei, (uint32_t,uint32_t)) \
 X(uint8_t, IsEnabled, (uint32_t)) \
 X(void, Enable, (uint32_t)) X(void, Disable, (uint32_t)) \
 X(void, GenFramebuffers, (int32_t, uint32_t*)) X(void, DeleteFramebuffers, (int32_t, const uint32_t*)) \
 X(void, BindFramebuffer, (uint32_t, uint32_t)) X(uint32_t, CheckFramebufferStatus, (uint32_t)) \
 X(void, GetFramebufferAttachmentParameteriv, (uint32_t,uint32_t,uint32_t,int32_t*)) \
 X(void, FramebufferTexture2D, (uint32_t, uint32_t, uint32_t, uint32_t, int32_t)) \
 X(void, BlitFramebuffer, (int32_t,int32_t,int32_t,int32_t,int32_t,int32_t,int32_t,int32_t,uint32_t,uint32_t)) \
 X(void, ReadBuffer, (uint32_t)) X(void, DrawBuffer, (uint32_t)) \
 X(void, GenTextures, (int32_t,uint32_t*)) X(void, DeleteTextures, (int32_t,const uint32_t*)) \
 X(void, ActiveTexture, (uint32_t)) X(void, BindTexture, (uint32_t,uint32_t)) \
 X(uint8_t, IsTexture, (uint32_t)) X(void, GetTexLevelParameteriv, (uint32_t,int32_t,uint32_t,int32_t*)) \
 X(void, TexParameteri, (uint32_t,uint32_t,int32_t)) \
 X(void, TexImage2D, (uint32_t,int32_t,int32_t,int32_t,int32_t,int32_t,uint32_t,uint32_t,const void*)) \
 X(void, TexSubImage2D, (uint32_t,int32_t,int32_t,int32_t,int32_t,int32_t,uint32_t,uint32_t,const void*)) \
 X(void, BindSampler, (uint32_t,uint32_t)) X(void, PixelStorei, (uint32_t,int32_t)) \
 X(void, GenRenderbuffers, (int32_t,uint32_t*)) X(void, DeleteRenderbuffers, (int32_t,const uint32_t*)) \
 X(void, BindRenderbuffer, (uint32_t,uint32_t)) X(void, RenderbufferStorage, (uint32_t,uint32_t,int32_t,int32_t)) \
 X(void, FramebufferRenderbuffer, (uint32_t,uint32_t,uint32_t,uint32_t)) \
 X(void, GenVertexArrays, (int32_t,uint32_t*)) X(void, DeleteVertexArrays, (int32_t,const uint32_t*)) \
 X(void, BindVertexArray, (uint32_t)) X(void, GenBuffers, (int32_t,uint32_t*)) \
 X(void, DeleteBuffers, (int32_t,const uint32_t*)) X(void, BindBuffer, (uint32_t,uint32_t)) \
 X(void, BufferData, (uint32_t,ptrdiff_t,const void*,uint32_t)) X(void, BufferSubData, (uint32_t,ptrdiff_t,ptrdiff_t,const void*)) \
 X(void, EnableVertexAttribArray, (uint32_t)) X(void, VertexAttribPointer, (uint32_t,int32_t,uint32_t,uint8_t,int32_t,const void*)) \
 X(uint32_t, CreateShader, (uint32_t)) X(void, ShaderSource, (uint32_t,int32_t,const char*const*,const int32_t*)) \
 X(void, CompileShader, (uint32_t)) X(void, GetShaderiv, (uint32_t,uint32_t,int32_t*)) X(void, DeleteShader, (uint32_t)) \
 X(uint32_t, CreateProgram, (void)) X(void, AttachShader, (uint32_t,uint32_t)) X(void, LinkProgram, (uint32_t)) \
 X(void, GetProgramiv, (uint32_t,uint32_t,int32_t*)) X(void, DeleteProgram, (uint32_t)) X(void, UseProgram, (uint32_t)) \
 X(int32_t, GetUniformLocation, (uint32_t,const char*)) X(void, Uniform1i, (int32_t,int32_t)) \
 X(void, Uniform1f, (int32_t,float)) X(void, Uniform2f, (int32_t,float,float)) X(void, Uniform3fv, (int32_t,int32_t,const float*)) \
 X(void, UniformMatrix4fv, (int32_t,int32_t,uint8_t,const float*)) \
 X(void, Viewport, (int32_t,int32_t,int32_t,int32_t)) X(void, Scissor, (int32_t,int32_t,int32_t,int32_t)) \
 X(void, ViewportIndexedf, (uint32_t,float,float,float,float)) X(void, ScissorIndexed, (uint32_t,int32_t,int32_t,int32_t,int32_t)) \
 X(void, DepthRangeIndexed, (uint32_t,double,double)) \
 X(void, DepthRange, (double,double)) X(void, DepthFunc, (uint32_t)) X(void, DepthMask, (uint8_t)) \
 X(void, ColorMaski, (uint32_t,uint8_t,uint8_t,uint8_t,uint8_t)) \
 X(void, BlendFuncSeparatei, (uint32_t,uint32_t,uint32_t,uint32_t,uint32_t)) X(void, BlendEquationSeparatei, (uint32_t,uint32_t,uint32_t)) \
 X(void, PolygonMode, (uint32_t,uint32_t)) \
 X(void, PolygonOffset, (float,float)) \
 X(void, StencilFuncSeparate, (uint32_t,uint32_t,int32_t,uint32_t)) X(void, StencilOpSeparate, (uint32_t,uint32_t,uint32_t,uint32_t)) \
 X(void, StencilMaskSeparate, (uint32_t,uint32_t)) X(void, ClearBufferfi, (uint32_t,int32_t,float,int32_t)) \
 X(void, ClearBufferfv, (uint32_t,int32_t,const float*)) \
 X(void, DrawArrays, (uint32_t,int32_t,int32_t)) X(void, MemoryBarrier, (uint32_t))

namespace ghost_compositor {
struct Dispatch {
#define GC_DECLARE(result,name,args) result (GC_GL_CALL *name) args = nullptr;
    GC_GL_FUNCTIONS(GC_DECLARE)
#undef GC_DECLARE
};
/* Matches the model's explicit N64 combiner semantics. All three vertices of
 * a triangle must agree; UVs/transparent texels never guess the material. */
enum class Material : uint32_t {
    solid=0, alpha_mix=1, decal_rgba=2, decal_rgb=3, modulate_rgb=4, modulate_rgba=5
};
struct Vertex { float position[3], normal[3], color[3], uv[2]; Material material; };
static_assert(sizeof(Vertex)==48 && offsetof(Vertex,material)==44,"GPU vertex layout");
enum class Depth : uint32_t { ordinary = 1, n64_image = 2 };
enum class Result : uint32_t { ok, invalid, unsupported, resources, state };
struct Target {
    uint64_t occurrence, generation;
    uint32_t framebuffer, width, height;
    Depth depth;
    uint32_t n64_depth_texture; // existing TEXTURE_2D R32F, same pixel coordinates
    uint32_t samples; // exactly 1; multisample is not silently resolved
    uint32_t unsupported; // nonzero refuses; caller's unsupported presentation modes
    int32_t viewport[4], scissor[4];
    float depth_scale, depth_translate; // N64 viewport Z mapping (usually .5,.5)
};
struct ShadowVertex { float position[3], uv[2], alpha; };
struct Shadow {
    const ShadowVertex* vertices;
    uint32_t count;
    const uint8_t* texture_ia8;
    float view_projection[16];
};
/* Recorded particles in world space: the ROM frame's triangles already
 * placed and lit. rect is the frame's atlas cell (uv is clamped to it); rgba
 * the shade; cutout 1 keeps texels with alpha >= .5, 2 is opaque (alpha
 * ignored), 0 blends; mix 1 mixes
 * rgba toward the texel by its alpha (the cap's logo) instead of modulating. */
constexpr uint32_t particle_vertices=768; // 16 particles: a knocked-off cap (156) and 15 stars (24)
struct ParticleVertex { float position[3], uv[2], rect[4], rgba[4], cutout, mix; };
struct Particles {
    const ParticleVertex* vertices;
    uint32_t count;
    float view_projection[16];
    uint32_t opaque_count; // the first ones are cutout/opaque (depth-writing); the rest blend
};
struct Pose {
    const Vertex* vertices;
    uint32_t count; // unindexed triangle list, bounded at prepare()
    float view_projection[16]; // column-major, includes model/world transform
    float light_direction[3]; // same space as supplied normals; normalized
    float ambient, diffuse, opacity;
    uint32_t flip_y; // GLideN64 world triangle path requires 1
    float hue_degrees; // 0 retains the original model colors
    Shadow shadow{}; // optional, at most 24 vertices; borrowed 16x16 IA8 texture
    Particles particles{}; // optional, at most particle_vertices; needs prepare_particles
    // Player-first fade for this body, its shadow and its particles; zero draws as before.
    ghost_occlusion::Region occlusion{};
};
struct Display { uint64_t occurrence, generation; uint32_t framebuffer, texture, width, height; };
struct Instance;

/* Calls require the same current context and thread. prepare allocates all GPU
 * storage; invoke only at context/size/atlas setup, never at a world boundary.
 * A generation change must use destroy (old context current) or abandon (lost
 * context) followed by a fresh create. prepare may resize within a generation. */
Instance* create(const Dispatch&, uint64_t generation);
Result prepare(Instance*, uint32_t width, uint32_t height, uint32_t max_vertices,
               const uint8_t* atlas_rgba, uint32_t atlas_width, uint32_t atlas_height);
/* The ROM particle atlas (model sg_particle_view). Without it particles are
 * skipped; setup-time only, like prepare. */
Result prepare_particles(Instance*, const uint8_t* atlas_rgba, uint32_t width, uint32_t height);
Result begin(Instance*, const Target&, const Pose&); // copies clean color, draws once
/* Bounded multi-ghost experiment: copy the clean surface once and draw each
 * body with its own self-depth/stencil pass. Caller supplies back-to-front
 * order and live borrowed vertices; prepare capacity is the TOTAL vertices.
 * Body sorting is approximate for interpenetrating translucent ghosts.
 * No allocations; invalid input refuses the entire candidate before drawing. */
Result begin_batch(Instance*, const Target&, const Pose*, uint32_t count);
/* Root establishes semantic HUD safety (no image writes / framebuffer feedback).
 * This scope additionally refuses depth/stencil/raster side effects. Mirror the
 * SAME raster call, then end_hud. No emulation command or state update is replayed. */
Result begin_hud(Instance*, uint64_t occurrence);
void end_hud(Instance*);
/* Translucent world layer: the game's water surfaces (and any other world
 * geometry that blends over what is behind it without writing depth) seen
 * over a ghost. The renderer mirrors each such world draw twice with its own
 * program, textures and depth state: pass 0 accumulates its premultiplied
 * color and remaining transmittance, pass 1 its nearest depth. begin_batch
 * then gives every ghost fragment behind that depth the same blend the game
 * gave the pixels behind the surface. Call clear_layer at each list start;
 * a depth-writing world draw after the layer opened breaks it for the list.
 * Only ordinary D24 depth is supported (N64 image depth never opens it). */
enum class LayerPass : uint32_t { color = 0, depth = 1 };
Result begin_layer(Instance*, const Target&, LayerPass);
void end_layer(Instance*);
void clear_layer(Instance*);
void break_layer(Instance*);
struct LayerStats { uint64_t opened, mirrored, skipped_blend, broken, applied; };
LayerStats layer_stats(const Instance*);
Result finalize(Instance*, uint64_t occurrence);
Display display(const Instance*); // zero until finalized, never a stale candidate
void abort(Instance*);
void destroy(Instance*); // original context current
void abandon(Instance*); // context gone: frees CPU ownership, makes no GL calls
}
