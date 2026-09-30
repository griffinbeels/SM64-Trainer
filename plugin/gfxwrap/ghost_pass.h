/* Render-thread world-pass qualification. Pure values; no GL/RAM access.
 * A candidate remains provisional until end(): a later world pass, target
 * change, or interrupted list revokes it. Never present a provisional result.
 */
#pragma once
#include <stdint.h>

namespace ghost {
enum class PassStatus : uint32_t {
    Disabled, NoWorld, Candidate, Ready, Interrupted, InvalidProjection,
    ProjectionChanged, ViewportChanged, TargetChanged, MultipleWorlds,
    UnsupportedDepth, InvalidIdentity
};
struct RasterTarget {
    uint32_t color, depth, width, height, samples;
    uint32_t depth_mode; // 0: ordinary GL depth; 1: R32F N64 image; >=2 unsupported
    int32_t viewport[4];
    float depth_scale[2]; // the renderer's uDepthScale
    float screen_scale[2]; // renderer coordinate/aspect correction
    float noise_scale = 1.0f; // live framebuffer's m_scale, as its noise shader uses
};
/* The renderer's texture-generation lookat when the world was drawn
 * (GLideN64 gSP.lookat, eye space). It persists from ROM open (RSP_Init:
 * enabled, x=(0,1,0), y=(1,0,0)) until a G_MV_LOOKAT; disabled means the
 * eye-space normal's own x/y. */
struct Lookat { float x[3], y[3]; uint32_t enabled; };
struct WorldPass {
    uint64_t list_serial;
    uint32_t epoch, context_generation;
    uint32_t triangles;
    float projection[16]; // actual decoded/multiplied N64 projection, column major
    RasterTarget target;
    Lookat lookat; // zero (disabled) unless the renderer supplied it
};
class Pass {
public:
    void begin(uint32_t epoch, uint32_t context_generation, uint64_t serial, bool enabled);
    void projection(const float matrix[16]);
    // The live lookat, taken with the world's first triangles like projection.
    void lookat(const Lookat &);
    // Call AFTER a successful world triangle submission, with its actual GL
    // destination and mapping. Rectangles/lines never establish a world pass.
    void triangles(const RasterTarget &, uint32_t count, bool zbuffer_enabled);
    // Call at the geometry-mode transition BEFORE changing the renderer mode.
    // true requests a provisional branch; end() still decides presentation.
    bool geometry(bool was_zbuffer, bool is_zbuffer);
    // A semantic game color-target change invalidates a pending branch even
    // when an intermediate renderer FBO is used later for HUD rasterization.
    void color_target_changed();
    bool end(bool completed);
    void reset();
    PassStatus status() const { return status_; }
    const WorldPass *candidate() const;
private:
    WorldPass value_{};
    float current_projection_[16]{};
    Lookat current_lookat_{};
    PassStatus status_ = PassStatus::Disabled;
    bool projection_known_ = false, world_open_ = false, sealed_ = false;
    void refuse(PassStatus status);
};
} // namespace ghost
