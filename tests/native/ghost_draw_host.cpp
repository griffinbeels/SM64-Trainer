/* The ghost glue (ghost_draw.cpp, compiled whole) against its capture gateway:
 * the glGetError reads one list costs, and that every error still reaches its
 * owner. The gateway models link_dispatch's, which link_dispatch_host proves
 * against the driver: a read takes the GL error flag; a capture scope keeps
 * an error for the renderer's next read and leaves the nominal state; the
 * layer's close drains its own. Compositor, playback and renderer are shells
 * that raise an error where a scenario says. No GL context, no window.
 * GHOST_DRAW_BASELINE builds it against an older glue, for a before/after. */
#include "ghost_draw_shell.h"
#include "ghost_draw.h"
#include "ghost_compositor.h"
#include "ghost_dispatch.h"
#include "ghost_ipc.h"
#include "ghost_link.h"
#include "ghost_playback.h"
#include "ghost_presentation.h"
#include "ghost_stream.h"
#include "link_dispatch.h"
#include <cstdio>
#include <stdexcept>

Config config;
GSPInfo gSP;
bool graphics::Context::FramebufferFetch = false;
unsigned shell_queries = 0;
void APIENTRY shell_get_integer(GLenum, GLint *value) { ++shell_queries; if (value) *value = 0; }
GLint APIENTRY shell_uniform_location(GLuint, const GLchar *) { ++shell_queries; return -1; }

namespace {
constexpr uint32_t width = 640, height = 480, screen = 7;
// Whose GL raised the error flag the next read takes.
enum class Owner { none, renderer, ghost, layer };
struct Gateway {
    GLenum flag = GL_NO_ERROR, saved = GL_NO_ERROR;
    Owner owner = Owner::none, saved_owner = Owner::none;
    bool nominal = true, open = false;
    unsigned reads = 0, drained_renderer = 0, drained_layer = 0;
} gl;
void fail_gl(Owner who) {
    if (gl.flag == GL_NO_ERROR) { gl.flag = GL_INVALID_OPERATION; gl.owner = who; }
}
GLenum take(Owner &who) {
    ++gl.reads;
    const GLenum error = gl.flag; who = gl.owner;
    gl.flag = GL_NO_ERROR; gl.owner = Owner::none;
    return error;
}
void keep(GLenum error, Owner who) { gl.saved = error; gl.saved_owner = who; gl.nominal = false; }
renderer::Identity identity() {
    renderer::Identity id{};
    id.context = 1; id.read_drawable = 1; id.context_generation = 1; id.drawable_generation = 1;
    id.owner_thread = GetCurrentThreadId();
    return id;
}
CachedTexture color{11, width, height};
FrameBuffer target_buffer{screen, &color, false};
struct Faults {
    int renderer_before_surface = -1, renderer_before_hud = -1;
    bool renderer_after_huds = false, batch = false, hud_gl = false, layer_gl = false, throw_in_layer = false;
} faults;
struct Seen { unsigned layer_passes = 0, broken = 0, finalized = 0, hud_mirrors = 0, warmed = 0, stacked = 0, values = 0; } seen;
bool usamune_hidden = false; // the list's Usamune HUD hides the player's display
int usamune_values = -1;      // its Stick (0) / Values (1) setting, -1 unknown
bool model_ready = true; // playback has built Mario's model
bool chooses = true; // the list's plan chose a ghost
bool layer_broken = false, begun = false, in_hud = false;
unsigned checks = 0;
void check(bool ok, const char *what) { ++checks; if (!ok) throw std::runtime_error(what); }
}

namespace replay_gl {
bool Raster(RasterState *s) {
    if (!gl.nominal || !s) return false;
    *s = {}; s->draw_framebuffer = screen; s->draw_known = true;
    s->viewport[2] = int32_t(width); s->viewport[3] = int32_t(height); s->viewport_known = true;
    s->depth_range[1] = 1; s->depth_known = true;
    return true;
}
bool CandidateBindings(renderer::Identity *out, snapshot::RestoreBindings *bindings) {
    if (!gl.nominal || !out) return false;
    *out = identity(); if (bindings) *bindings = {};
    return true;
}
bool Render(RenderState *r) {
    if (!gl.nominal || !r) return false;
    *r = {}; r->scissor[2] = int32_t(width); r->scissor[3] = int32_t(height); r->scissor_known = true;
    return true;
}
bool Enabled(GLenum, bool *on) { if (!gl.nominal) return false; *on = false; return true; }
bool Texture2D(unsigned, GLuint *name) { if (!gl.nominal) return false; *name = 0; return true; }
bool __cdecl BeginCapture() {
    if (!gl.nominal || gl.open || gl.saved) return false;
    Owner who{}; const GLenum error = take(who);
    if (error != GL_NO_ERROR) { keep(error, who); return false; }
    gl.open = true; return true;
}
bool __cdecl EndCapture() {
    if (!gl.open) return false;
    gl.open = false;
    Owner who{}; const GLenum error = take(who);
    if (error != GL_NO_ERROR) { keep(error, who); return false; }
    return gl.nominal;
}
bool __cdecl BeginLayer() { return BeginCapture(); }
bool __cdecl EndLayer() {
    if (!gl.open) return false;
    gl.open = false;
    Owner who{}; const GLenum error = take(who);
    if (error == GL_NO_ERROR) return gl.nominal;
    ++(who == Owner::layer ? gl.drained_layer : gl.drained_renderer);
    return false;
}
}
namespace ghost_compositor {
struct Instance { int unused = 0; };
namespace { Instance instance; }
Instance *create(const Dispatch &, uint64_t) { return &instance; }
Result prepare(Instance *, uint32_t, uint32_t, uint32_t, const uint8_t *, uint32_t, uint32_t) { return Result::ok; }
Result prepare_particles(Instance *, const uint8_t *, uint32_t, uint32_t) { return Result::unsupported; }
Result prepare_annotations(Instance *, const uint8_t *, uint32_t, uint32_t) { return Result::unsupported; }
Result warm(Instance *) { ++seen.warmed; return Result::ok; }
Result begin_batch(Instance *, const Target &, const Pose *poses, uint32_t count, const Known *) {
    if (faults.batch) fail_gl(Owner::ghost);
    for (uint32_t i = 0; i < count; ++i) {seen.stacked += poses[i].annotation.hud; seen.values += poses[i].annotation.values;}
    begun = count != 0; return begun ? Result::ok : Result::invalid;
}
Result begin_hud(Instance *, uint64_t, const Known *) {
    if (!begun || in_hud) return Result::invalid;
    in_hud = true; ++seen.hud_mirrors;
    if (faults.hud_gl) fail_gl(Owner::ghost);
    return Result::ok;
}
void end_hud(Instance *) { in_hud = false; }
Result begin_layer(Instance *, const Target &, LayerPass, const Known *) {
    if (layer_broken) return Result::state;
    ++seen.layer_passes;
    if (faults.layer_gl) fail_gl(Owner::layer);
    return Result::ok;
}
void end_layer(Instance *) {}
void clear_layer(Instance *) { layer_broken = false; }
void break_layer(Instance *) { if (!layer_broken) { layer_broken = true; ++seen.broken; } }
Result finalize(Instance *, uint64_t) {
    if (!begun || in_hud) return Result::invalid;
    ++seen.finalized; return Result::ok;
}
Display display(const Instance *) { return {}; }
void abort(Instance *) { begun = in_hud = false; }
Cost take_cost(Instance *) { return {}; }
void destroy(Instance *) {}
void abandon(Instance *) {}
}
namespace ghost_dispatch { bool load(ghost_compositor::Dispatch &) { return true; } }
namespace ghost_playback {
bool drawing() { return true; } // a displayed request holds its clips, finished or not
bool plan(const gc_sample &) { return chooses; }
void safe_boundary(uint32_t, uint32_t) {}
bool atlas(sg_atlas_view *out, uint64_t *id) {
    static const uint8_t pixel[4]{};
    *out = {}; out->rgba = pixel; out->width = out->height = 1; *id = 1;
    return model_ready;
}
bool particles(sg_particle_view *, uint64_t *) { return false; }
bool annotations(ghost_annotation::View *, uint64_t *) { return false; }
bool evaluate(const gc_sample &, const ghost::WorldPass &, ghost_compositor::Pose *out, uint32_t &count,
              gp_draw_token &token) {
    count = 0; token = {};
    if (!chooses) return false;
    out[0] = {}; out[0].annotation.hud = true; count = 1; token.revision = 1;
    return true;
}
bool current(const gp_draw_token &token) { return token.revision != 0; }
bool records_display() { return false; }
void retire_on_context_loss() {}
}
namespace ghost_presentation {
bool clean_consumer() { return true; }
void set_provider(ImageProvider, PresentationNotice, ImageValidity) {}
void context_ready() {}
}
namespace ghost_ipc { void presented(uint64_t) {} }
namespace ghost_link {
FrameBuffer *raster_buffer() { return &target_buffer; }
uint64_t attachments(const FrameBuffer *) { return (uint64_t(color.name) << 32) | 12u; }
}

#ifdef GHOST_DRAW_BASELINE
#define BEGIN_LIST(sample) ghost_draw::begin(7, 4)
#define CLOSE_LAYER
#else
#define BEGIN_LIST(sample) ghost_draw::begin(7, 4, &(sample))
#define CLOSE_LAYER ghost_draw::close_layer();
#endif

namespace {
// The staged seam (tools/stage_ghost_renderer.py) after one world draw, with
// ghost_link's gate: mirror it once per pass while begin_layer holds; the
// scope's end closes the surface however its passes ended.
void mirror_surface() {
    struct GhostLayer {
        bool active = false;
        ~GhostLayer() { if (active) ghost_draw::end_layer(); CLOSE_LAYER }
    } layer;
    for (uint32_t pass = 0; (layer.active = ghost_draw::drawing()
             && ghost_draw::begin_layer(pass, screen, ghost_link::attachments(&target_buffer))); ++pass) {
        if (faults.throw_in_layer) throw std::runtime_error("upstream draw");
        layer.active = false; ghost_draw::end_layer();
    }
}
struct List { unsigned reads, counted, layer_passes, hud_mirrors, drawn; };
// One display list, in the renderer's order: list start, the world pass with
// its translucent surfaces, the seal, the HUD, list end.
List run_list(unsigned surfaces, unsigned huds) {
    static uint64_t serial = 0; ++serial;
    gc_sample sample{}; sample.sequence = sample.dlist_serial = serial;
    ghost::WorldPass world{};
    world.list_serial = serial; world.context_generation = 1;
    world.target.color = screen; world.target.width = width; world.target.height = height; world.target.samples = 1;
    const unsigned reads = gl.reads; const Seen before = seen;
    BEGIN_LIST(sample);
    ghost_draw::observe(world.target);
    gSP.geometryMode = G_ZBUFFER;
    for (unsigned s = 0; s < surfaces; ++s) {
        if (int(s) == faults.renderer_before_surface) fail_gl(Owner::renderer); // the original draw
        try { mirror_surface(); } catch (const std::runtime_error &) {}
    }
    gSP.geometryMode = 0;
#ifdef GHOST_DRAW_BASELINE
    ghost_draw::seal(sample, world);
#else
    ghost_draw::seal(sample, world, usamune_hidden, usamune_values);
#endif
    for (unsigned h = 0; h < huds; ++h) {
        if (int(h) == faults.renderer_before_hud) fail_gl(Owner::renderer); // the original HUD draw
        if (ghost_draw::begin_hud()) ghost_draw::end_hud(); // the mirrored draw between
    }
    if (faults.renderer_after_huds) fail_gl(Owner::renderer);
    ghost_draw::finish(&world);
    gs_hook_cost cost{}; ghost_draw::take_cost(cost);
    return {gl.reads - reads, cost.error_reads, seen.layer_passes - before.layer_passes,
            seen.hud_mirrors - before.hud_mirrors, seen.finalized - before.finalized};
}
void reset() {
    gl.flag = gl.saved = GL_NO_ERROR; gl.owner = gl.saved_owner = Owner::none;
    gl.nominal = true; gl.drained_renderer = gl.drained_layer = 0;
    faults = {}; chooses = true;
}
const char *owner(Owner who) {
    return who == Owner::renderer ? "renderer" : who == Owner::ghost ? "ghost" : who == Owner::layer ? "layer" : "none";
}
// A fault, one list of 3 surfaces and 5 HUD draws, and where its error went.
void scenario(const char *name, bool comma, Faults fault) {
    reset(); run_list(3, 5); // a clean list first: the scenario's own starts clean
    faults = fault; const Seen before = seen;
    const auto list = run_list(3, 5);
    std::printf("%s\"%s\":{\"drawn\":%u,\"kept\":\"%s\",\"pending\":\"%s\",\"nominal\":%s,"
                "\"drained_layer\":%u,\"drained_renderer\":%u,\"broken\":%u,\"scope_open\":%s}",
                comma ? "," : "", name, list.drawn, owner(gl.saved_owner), owner(gl.owner),
                gl.nominal ? "true" : "false", gl.drained_layer, gl.drained_renderer,
                seen.broken - before.broken, gl.open ? "true" : "false");
    // Whatever went wrong, no scope outlives its list and the renderer's own
    // error is never drained as if it were the layer's.
    check(!gl.open, "no gateway scope outlives its list");
    check(!gl.drained_renderer, "a renderer error is never drained");
}
}

int main() {
    try {
#ifndef GHOST_DRAW_BASELINE
        // Before any ghost or model exists the compositor compiles its programs
        // once, so the first ghost's list never waits for the driver.
        model_ready = false; run_list(0, 0); run_list(0, 0); model_ready = true;
        check(seen.warmed == 1, "programs compile once, before Mario's model or any ghost");
#endif
        run_list(0, 0); run_list(0, 0); // create and prepare the compositor
#ifndef GHOST_DRAW_BASELINE
        // The corner stack hides exactly while Usamune hides the player's display.
        { const unsigned before = seen.stacked; run_list(0, 0);
          check(seen.stacked == before + 1, "a ghost's corner row reaches the compositor");
          usamune_hidden = true; run_list(0, 0); usamune_hidden = false;
          check(seen.stacked == before + 1, "Usamune's menu hides every ghost's corner row");
          const unsigned shown = seen.values; run_list(0, 0);
          check(seen.values == shown, "an unknown HUD mode keeps the stored stick display");
          usamune_values = 1; run_list(0, 0); check(seen.values == shown + 1, "his HUD's Values mode reaches every ghost");
          usamune_values = 0; run_list(0, 0); check(seen.values == shown + 1, "his HUD's Stick mode reaches every ghost");
          usamune_values = -1; }
#endif
        reset();
        std::printf("{\"ok\":true,\"baseline\":%s,",
#ifdef GHOST_DRAW_BASELINE
                    "true"
#else
                    "false"
#endif
        );
        // His LLL list: about 12 water surfaces and 27 HUD draws.
        chooses = false; const auto finished = run_list(12, 27);
        chooses = true; const auto drawn = run_list(12, 27);
        std::printf("\"finished\":{\"reads\":%u,\"counted\":%u,\"layer_passes\":%u,\"hud_mirrors\":%u,\"drawn\":%u},",
                    finished.reads, finished.counted, finished.layer_passes, finished.hud_mirrors, finished.drawn);
        std::printf("\"drawn\":{\"reads\":%u,\"counted\":%u,\"layer_passes\":%u,\"hud_mirrors\":%u,\"drawn\":%u},",
                    drawn.reads, drawn.counted, drawn.layer_passes, drawn.hud_mirrors, drawn.drawn);
        std::printf("\"shapes\":[");
        const unsigned shapes[][2] = {{0, 0}, {0, 27}, {1, 0}, {12, 0}, {12, 27}, {23, 40}};
        for (unsigned i = 0; i < sizeof(shapes) / sizeof(shapes[0]); ++i) {
            const auto list = run_list(shapes[i][0], shapes[i][1]);
            check(list.drawn == 1 && list.layer_passes == 2 * shapes[i][0] && list.hud_mirrors == shapes[i][1],
                  "a clean list draws its ghost, both passes of every surface and every HUD mirror");
            // The hook line's "gateway error reads" is the gateway's own count.
            check(list.counted == list.reads, "the glue counts every read it causes");
            std::printf("%s[%u,%u,%u]", i ? "," : "", shapes[i][0], shapes[i][1], list.reads);
        }
        const unsigned clean_queries = shell_queries; // a lost record is asked for below
        std::printf("],\"scenarios\":{");
        Faults fault{};
        fault.renderer_before_surface = 1; scenario("renderer_before_surface", false, fault); fault = {};
        fault.layer_gl = true; scenario("layer_gl", true, fault); fault = {};
        fault.batch = true; scenario("batch", true, fault); fault = {};
        fault.renderer_before_hud = 2; scenario("renderer_between_huds", true, fault); fault = {};
        fault.hud_gl = true; scenario("hud_gl", true, fault); fault = {};
        fault.renderer_after_huds = true; scenario("renderer_after_huds", true, fault); fault = {};
        fault.throw_in_layer = true; scenario("throw_in_layer", true, fault); fault = {};
        std::printf("},\"checks\":%u,\"driver_queries\":%u}\n", checks, clean_queries);
        return 0;
    } catch (const std::exception &error) {
        std::fprintf(stderr, "\nghost glue witness: %s (check %u)\n", error.what(), checks);
        return 1;
    }
}
