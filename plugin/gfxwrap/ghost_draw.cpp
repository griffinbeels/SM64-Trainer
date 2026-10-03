#include "GLFunctions.h"
#include "ghost_draw.h"
#include "ghost_link.h"
#include "ghost_compositor.h"
#include "ghost_dispatch.h"
#include "ghost_playback.h"
#include "ghost_annotation.h"
#include "ghost_presentation.h"
#include "ghost_stream.h"
#include "ghost_ipc.h"
#include "link_dispatch.h"
#include "Config.h"
#include "FrameBuffer.h"
#include "gSP.h"
#include "gDP.h"
#include "GBI.h"
#include "Graphics/Context.h"
#include <chrono>
#include <cstring>

namespace ghost_draw {
namespace {
ghost_compositor::Instance *compositor = nullptr;
ghost_compositor::Dispatch dispatch{};
renderer::Identity owner{}, presentation_owner{};
uint32_t width = 0, height = 0, prepared_width = 0, prepared_height = 0;
uint32_t configuration = 0, clean_fbo = 0, clean_texture = 0;
uint64_t model_id = 0, particle_model = 0, annotation_model = 0, occurrence = 0;
gp_draw_token draw_token{};
bool provisional = false, hud = false, ready = false, layer = false;
// This list draws a ghost: playback chose one from the list's own sample
// (ghost_playback::plan) on the prepared model. The translucent world layer
// (every XLU draw again plus a depth copy) runs only then, so a finished,
// faded, suspended or out-of-area ghost costs what no ghost costs.
bool will_draw = false;
/* The capture gateway's scopes (docs: What a drawn ghost asks the driver).
 * display: opened by the seal's read, it holds the batch and every HUD
 * mirror of the list and closes with one read at finish or cancel. A HUD
 * mirror's GL is two binds between the ghost display and the target the
 * seal verified, so it needs no read of its own: an error anywhere in the
 * scope, the renderer's included, cancels the image before it is ready and
 * is kept for the renderer, exactly as either read alone would.
 * layer: one per mirrored surface, both passes; its closing read drains the
 * layer's own errors, so no renderer GL but the mirrored draw runs inside.
 * layer_color: the color pass entered the layer and the depth pass has not. */
bool display_scope = false, layer_scope = false, layer_color = false;
using GetUniform = void (APIENTRY *)(GLuint, GLint, GLint*);
GetUniform get_uniform = nullptr;
// This list's drawing cost (take_cost): stage times, the glue's own driver
// queries (the compositor counts its own), and the gateway's error reads.
using Clock = std::chrono::steady_clock;
gs_hook_cost cost{};
uint64_t query_nanos = 0;
Clock::time_point hud_start{};
uint32_t since(Clock::time_point start) {
    const auto us = std::chrono::duration_cast<std::chrono::microseconds>(Clock::now() - start).count();
    return us < 0 ? 0u : us > INT32_MAX ? uint32_t(INT32_MAX) : uint32_t(us);
}
template <class Query> void ask(Query &&query) {
    const auto start = Clock::now(); query(); ++cost.gl_queries;
    query_nanos += uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now() - start).count());
}
bool gateway(bool (__cdecl *call)()) { ++cost.error_reads; return call(); }
/* What the renderer already knows of its state, as its dispatch observed it
 * (link_dispatch): draw and read bindings, viewport and scissor, program,
 * vertex array, depth and blend state and the capabilities it switches. The
 * ghost code restores whatever it changes, so these stay the driver's. */
ghost_compositor::Known known_state() {
    using ghost_compositor::Known;
    Known known{};
    replay_gl::RasterState raster{};
    if (replay_gl::Raster(&raster)) {
        if (raster.draw_known) { known.fields |= Known::draw_framebuffer; known.draw = raster.draw_framebuffer; }
        if (raster.viewport_known) {
            known.fields |= Known::viewport; std::memcpy(known.viewport_box, raster.viewport, sizeof(raster.viewport));
        }
    }
    renderer::Identity identity{}; snapshot::RestoreBindings bindings{};
    if (replay_gl::CandidateBindings(&identity, &bindings)) {
        known.fields |= Known::read_framebuffer; known.read = bindings.read_framebuffer;
    }
    replay_gl::RenderState render{};
    if (replay_gl::Render(&render)) {
        known.fields |= Known::program | Known::vertex_array | Known::depth | Known::blend_factors;
        known.current_program = render.program; known.bound_vertex_array = render.vertex_array;
        known.depth_function = render.depth_func; known.depth_write = render.depth_mask ? 1 : 0;
        std::memcpy(known.blend_factor, render.blend, sizeof(render.blend));
        if (render.scissor_known) {
            known.fields |= Known::scissor; std::memcpy(known.scissor_box, render.scissor, sizeof(render.scissor));
        }
    }
    // Known::Cap bit order.
    const GLenum caps[] = {GL_DEPTH_TEST, GL_CULL_FACE, GL_POLYGON_OFFSET_FILL, GL_DEPTH_CLAMP, GL_CLIP_DISTANCE0,
        GL_BLEND, GL_SCISSOR_TEST};
    for (uint32_t i = 0; i < sizeof(caps) / sizeof(caps[0]); ++i) {
        bool on = false;
        if (!replay_gl::Enabled(caps[i], &on)) continue; // not observed: asked
        known.caps |= 1u << i; if (on) known.enabled |= 1u << i;
    }
    return known;
}

bool same(const renderer::Identity &a, const renderer::Identity &b) {
    return a.context && a.context == b.context && a.context_generation == b.context_generation
        && a.drawable_generation == b.drawable_generation && a.read_drawable == b.read_drawable
        && a.owner_thread == b.owner_thread;
}
bool valid_image(const ghost_presentation::GhostViImage &image) {
    return ready && image.occurrence == occurrence && image.configuration == configuration
        && image.context_generation == owner.context_generation && ghost_playback::current(draw_token);
}
bool image_for_vi(const ghost_presentation::ViSource &source, ghost_presentation::GhostViImage *out) {
    if (out) *out = {};
    if (!out || source.clean_framebuffer != clean_fbo || source.clean_texture != clean_texture
            || source.context_generation != owner.context_generation
            || source.drawable_generation != owner.drawable_generation) return false;
    const auto display = ghost_compositor::display(compositor);
    ghost_presentation::GhostViImage image{display.texture, display.width, display.height,
        configuration, owner.context_generation, display.occurrence, display.framebuffer};
    if (!valid_image(image) || display.width != source.width || display.height != source.height) return false;
    *out = image; return true;
}
void presented(const ghost_presentation::GhostViImage &image, uint64_t, bool displayed) {
    ghost_ipc::presented(displayed && valid_image(image) ? draw_token.revision : 0);
}
bool image_writes_disabled() {
    // The pinned combiner's depth_compare returns before any image access when
    // uEnableDepth=0. depth_render is a separate path and must be disabled too.
    if (!config.frameBufferEmulation.N64DepthCompare) return true;
    if (!get_uniform || graphics::Context::FramebufferFetch) return false;
    GLint program = 0; replay_gl::RenderState render{};
    if (replay_gl::Render(&render)) program = GLint(render.program);
    else ask([&] { glGetIntegerv(GL_CURRENT_PROGRAM, &program); });
    if (!program) return false;
    for (const char *name : {"uEnableDepth", "uRenderTarget"}) {
        GLint location = -1; ask([&] { location = glGetUniformLocation(program, name); });
        if (location < 0) return false;
        GLint value = -1; ask([&] { get_uniform(program, location, &value); });
        if (value != 0) return false;
    }
    return true;
}
bool no_color_feedback() {
    // The pinned N64 combiner samples game color only through tex units0/1,
    // whose bindings the dispatch observes; asked only before it has.
    GLuint observed[2]{};
    if (replay_gl::Texture2D(0, &observed[0]) && replay_gl::Texture2D(1, &observed[1]))
        return observed[0] != clean_texture && observed[1] != clean_texture;
    GLint previous = 0; ask([&] { glGetIntegerv(GL_ACTIVE_TEXTURE, &previous); });
    bool valid = true;
    for (unsigned unit = 0; unit < 2; ++unit) {
        dispatch.ActiveTexture(GL_TEXTURE0 + unit);
        GLint texture = 0; ask([&] { glGetIntegerv(GL_TEXTURE_BINDING_2D, &texture); });
        if (static_cast<uint32_t>(texture) == clean_texture) valid = false;
    }
    dispatch.ActiveTexture(previous); return valid;
}
// Closes the display scope: one read for this list's ghost GL (the batch and
// every HUD mirror) and the renderer's between them.
bool close_display() {
    if (!display_scope) return true;
    display_scope = false;
    const auto stage = Clock::now();
    const bool clean = gateway(replay_gl::EndCapture);
    cost.close_micros += since(stage);
    return clean;
}
}
void cancel() {
    if (hud) end_hud();
    ghost_compositor::abort(compositor);
    close_display();
    provisional = ready = false; occurrence = 0; draw_token = {};
}
void context_lost() {
    ghost_ipc::presented(0);
    close_layer(); cancel();
    ghost_playback::retire_on_context_loss();
    if (compositor) {
        if (reinterpret_cast<uintptr_t>(wglGetCurrentContext()) == owner.context)
            ghost_compositor::destroy(compositor);
        else ghost_compositor::abandon(compositor);
    }
    compositor = nullptr; owner = {}; presentation_owner = {}; dispatch = {};
    model_id = particle_model = annotation_model = 0; prepared_width = prepared_height = width = height = 0; will_draw = false;
    get_uniform = nullptr;
}
void begin(uint32_t rom, uint32_t config_id, const gc_sample *sample) {
    close_layer(); cancel(); will_draw = false;
    ghost_compositor::clear_layer(compositor);
    ghost_playback::safe_boundary(rom, config_id);
    configuration = config_id;
    if (!ghost_presentation::clean_consumer()) return;
    renderer::Identity identity{}; snapshot::RestoreBindings ignored{};
    if (!replay_gl::CandidateBindings(&identity, &ignored)) return;
    if (!same(identity, presentation_owner)) {
        ghost_presentation::set_provider(image_for_vi, presented, valid_image);
        ghost_presentation::context_ready(); presentation_owner = identity;
    }
    // The compositor and its programs exist before any ghost is asked for, so
    // the driver's compile lands seconds after the game starts, not on the
    // first ghost's list (a fresh shader cache cost that list 36 ms, 2026-10-02).
    if (!same(identity, owner)) {
        if (compositor) {
            if (identity.context == owner.context) ghost_compositor::destroy(compositor);
            else ghost_compositor::abandon(compositor);
        }
        compositor = nullptr; prepared_width = prepared_height = 0; model_id = particle_model = annotation_model = 0;
        if (!ghost_dispatch::load(dispatch)) return;
        owner = identity;
        get_uniform = reinterpret_cast<GetUniform>(wglGetProcAddress("glGetUniformiv"));
        const auto proc = reinterpret_cast<uintptr_t>(get_uniform);
        if (proc <= 3 || proc == UINTPTR_MAX) get_uniform = nullptr;
        if (!replay_gl::BeginCapture()) return;
        compositor = ghost_compositor::create(dispatch, owner.context_generation);
        if (compositor) ghost_compositor::warm(compositor);
        if (!replay_gl::EndCapture()) { ghost_compositor::abandon(compositor); compositor = nullptr; return; }
    }
    sg_atlas_view atlas{}; uint64_t wanted_model = 0;
    if (!ghost_playback::atlas(&atlas, &wanted_model)) return;
    if (compositor && width && height && (width != prepared_width || height != prepared_height || model_id != wanted_model)) {
        if (!replay_gl::BeginCapture()) return;
        // One vertex range per displayed ghost; begin_batch shares the buffer.
        const auto result = ghost_compositor::prepare(compositor, width, height, GP_GHOSTS * SG_MODEL_MAX_VERTICES,
                                                     atlas.rgba, atlas.width, atlas.height);
        const bool clean = replay_gl::EndCapture();
        if (result == ghost_compositor::Result::ok && clean) {
            prepared_width = width; prepared_height = height; model_id = wanted_model; particle_model = annotation_model = 0;
        }
    }
    // The ROM particle atlas rides on the prepared model; none leaves particles undrawn.
    sg_particle_view particles{}; uint64_t particle_id = 0;
    if (compositor && model_id && particle_model != model_id && ghost_playback::particles(&particles, &particle_id)
            && particle_id == model_id) {
        if (!replay_gl::BeginCapture()) return;
        const auto result = ghost_compositor::prepare_particles(compositor, particles.atlas_rgba,
                                                              particles.atlas_width, particles.atlas_height);
        if (replay_gl::EndCapture() && result == ghost_compositor::Result::ok) particle_model = model_id;
    }
    ghost_annotation::View annotations{};uint64_t annotation_id=0;
    if(compositor && model_id && annotation_model!=model_id && ghost_playback::annotations(&annotations,&annotation_id)
            && annotation_id==model_id) {
        if(!replay_gl::BeginCapture())return;
        const auto result=ghost_compositor::prepare_annotations(compositor,annotations.rgba,annotations.width,annotations.height);
        if(replay_gl::EndCapture() && result==ghost_compositor::Result::ok)annotation_model=model_id;
    }
    will_draw = compositor && model_id && model_id == wanted_model && sample && ghost_playback::plan(*sample);
}
void observe(const ghost::RasterTarget &target) {
    if (target.width && target.height && target.width <= 8192 && target.height <= 8192) {
        width = target.width; height = target.height;
    }
}
void seal(const gc_sample &sample, const ghost::WorldPass &world, bool usamune_hidden) {
    if (display_scope) cancel(); // a list seals once; never nest its scope
    if (!compositor || !model_id || !ghost_presentation::clean_consumer()
            || world.context_generation != owner.context_generation
            || world.target.width != prepared_width || world.target.height != prepared_height) return;
    auto *buffer = ghost_link::raster_buffer();
    if (!buffer || !buffer->m_pTexture || uint32_t(buffer->m_FBO) != world.target.color) return;
    // Zero visible ghosts leaves this frame exactly as a hidden single ghost did.
    ghost_compositor::Pose poses[GP_GHOSTS]{}; uint32_t count = 0;
    if (!ghost_playback::evaluate(sample, world, poses, count, draw_token)) return;
    // His ask, 2026-10-02: "when in the usamune menu, we should hide the input
    // display in the bottom left ... because the normal player's input display
    // is also hidden." The stack follows his display; names and bodies do not.
    if (usamune_hidden) for (uint32_t i = 0; i < count; ++i) poses[i].annotation.hud = false;
    ghost_compositor::Target target{};
    target.occurrence = world.list_serial; target.generation = world.context_generation;
    target.framebuffer = world.target.color; target.width = world.target.width; target.height = world.target.height;
    target.depth = world.target.depth_mode ? ghost_compositor::Depth::n64_image : ghost_compositor::Depth::ordinary;
    target.n64_depth_texture = world.target.depth_mode ? world.target.depth : 0;
    target.samples = world.target.samples;
    target.depth_scale = world.target.depth_scale[0]; target.depth_translate = world.target.depth_scale[1];
    std::memcpy(target.viewport, world.target.viewport, sizeof(target.viewport));
    target.attachments = ghost_link::attachments(buffer);
    // The game's scissor clips the ghost and is what the compositor restores:
    // as the dispatch observed it, asked only before the renderer set one.
    auto known = known_state();
    if (known.fields & ghost_compositor::Known::scissor)
        std::memcpy(target.scissor, known.scissor_box, sizeof(target.scissor));
    else {
        ask([&] { glGetIntegerv(GL_SCISSOR_BOX, target.scissor); });
        known.fields |= ghost_compositor::Known::scissor;
        std::memcpy(known.scissor_box, target.scissor, sizeof(target.scissor));
    }
    auto stage = Clock::now();
    const bool open = gateway(replay_gl::BeginCapture); cost.open_micros += since(stage);
    if (!open) return;
    display_scope = true;
    stage = Clock::now();
    const auto result = ghost_compositor::begin_batch(compositor, target, poses, count, &known);
    cost.batch_micros += since(stage);
    // The batch's own errors are read when the display scope closes.
    provisional = result == ghost_compositor::Result::ok;
    if (provisional) cost.ghosts = count;
    if (provisional) {
        occurrence = world.list_serial; clean_fbo = world.target.color;
        clean_texture = uint32_t(buffer->m_pTexture->name);
    } else cancel();
}
void finish(const ghost::WorldPass *world) {
    if (!provisional || !world || world->list_serial != occurrence || !ghost_playback::current(draw_token)
            || !close_display()) {
        cancel(); return;
    }
    const auto stage = Clock::now();
    ready = ghost_compositor::finalize(compositor, occurrence) == ghost_compositor::Result::ok;
    cost.finalize_micros += since(stage);
    if (!ready) cancel();
}
bool begin_layer(uint32_t pass, uint32_t framebuffer, uint64_t attachments) {
    // After the seal the layer is spent, and the display scope is open.
    if (layer || !will_draw || provisional || !compositor || !model_id || pass > 1 || (pass == 1) != layer_color
            || !ghost_presentation::clean_consumer() || width != prepared_width || height != prepared_height)
        return false;
    // The depth pass repeats the draw the color pass mirrored, inside its
    // scope: no renderer GL has run since that scope's read.
    if (!pass) {
        if (layer_scope || !gateway(replay_gl::BeginLayer)) return false;
        layer_scope = true;
    }
    ghost_compositor::Target target{};
    target.generation = owner.context_generation; target.framebuffer = framebuffer;
    target.width = width; target.height = height; target.samples = 1;
    target.unsupported = config.video.multisampling > 0;
    target.depth = ghost_compositor::Depth::ordinary; target.attachments = attachments;
    const auto known = known_state();
    layer = ghost_compositor::begin_layer(compositor, target, ghost_compositor::LayerPass(pass), &known)
        == ghost_compositor::Result::ok;
    if (layer) { ++cost.layer_passes; layer_color = !pass; }
    return layer;
}
void end_layer() {
    if (!layer) return;
    ghost_compositor::end_layer(compositor); layer = false;
}
void close_layer() {
    if (!layer_scope) return;
    end_layer(); layer_scope = false;
    // A surface whose color entered the layer without its depth would tint
    // nothing behind it consistently, and the layer's own GL error drops it:
    // the layer goes for this list, never the source.
    const bool clean = gateway(replay_gl::EndLayer);
    if (!clean || layer_color) ghost_compositor::break_layer(compositor);
    layer_color = false;
}
bool layer_open() { return layer_scope; }
void break_layer() { ghost_compositor::break_layer(compositor); }
bool drawing() { return will_draw; }
bool begin_hud() {
    if (!provisional || hud) return false;
    hud_start = Clock::now();
    auto *buffer = ghost_link::raster_buffer();
    if ((gSP.geometryMode & G_ZBUFFER) || !buffer || buffer->m_isDepthBuffer
            || uint32_t(buffer->m_FBO) != clean_fbo || !image_writes_disabled() || !no_color_feedback()) {
        cost.hud_micros += since(hud_start); cancel(); return false;
    }
    // Inside the display scope the seal opened: no read of its own.
    const auto known = known_state();
    hud = ghost_compositor::begin_hud(compositor, occurrence, &known) == ghost_compositor::Result::ok;
    if (!hud) { cost.hud_micros += since(hud_start); cancel(); }
    else ++cost.hud_draws;
    return hud;
}
void end_hud() {
    if (!hud) return;
    ghost_compositor::end_hud(compositor); hud = false;
    cost.hud_micros += since(hud_start); // the mirrored draw included
}
void take_cost(gs_hook_cost &into) {
    const auto drawn = ghost_compositor::take_cost(compositor);
    into.gl_queries += cost.gl_queries + drawn.queries;
    into.query_micros += uint32_t(query_nanos / 1000) + drawn.query_micros;
    into.error_reads += cost.error_reads;
    into.open_micros = cost.open_micros; into.batch_micros = cost.batch_micros;
    into.close_micros = cost.close_micros; into.finalize_micros = cost.finalize_micros;
    into.validate_micros = drawn.validate_micros;
    into.state_micros = drawn.state_micros; into.copy_micros = drawn.copy_micros;
    into.draw_micros = drawn.draw_micros; into.draw_max_micros = drawn.draw_max_micros;
    into.restore_micros = drawn.restore_micros;
    into.hud_micros = cost.hud_micros; into.hud_draws = cost.hud_draws; into.layer_passes = cost.layer_passes;
    into.ghosts = cost.ghosts;
    cost = {}; query_nanos = 0;
}
}
