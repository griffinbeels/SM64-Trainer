#include "GLFunctions.h"
#include "ghost_draw.h"
#include "ghost_link.h"
#include "ghost_compositor.h"
#include "ghost_dispatch.h"
#include "ghost_playback.h"
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
#include <cstring>

namespace ghost_draw {
namespace {
ghost_compositor::Instance *compositor = nullptr;
ghost_compositor::Dispatch dispatch{};
renderer::Identity owner{}, presentation_owner{};
uint32_t width = 0, height = 0, prepared_width = 0, prepared_height = 0;
uint32_t configuration = 0, clean_fbo = 0, clean_texture = 0;
uint64_t model_id = 0, occurrence = 0;
gp_draw_token draw_token{};
bool provisional = false, hud = false, ready = false, layer = false;
using GetUniform = void (APIENTRY *)(GLuint, GLint, GLint*);
GetUniform get_uniform = nullptr;

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
    GLint program = 0; glGetIntegerv(GL_CURRENT_PROGRAM, &program);
    if (!program) return false;
    for (const char *name : {"uEnableDepth", "uRenderTarget"}) {
        const GLint location = glGetUniformLocation(program, name);
        if (location < 0) return false;
        GLint value = -1; get_uniform(program, location, &value);
        if (value != 0) return false;
    }
    return true;
}
bool no_color_feedback() {
    GLint previous = 0; glGetIntegerv(GL_ACTIVE_TEXTURE, &previous);
    bool valid = true;
    // The pinned N64 combiner samples game color only through tex units0/1.
    for (unsigned unit = 0; unit < 2; ++unit) {
        dispatch.ActiveTexture(GL_TEXTURE0 + unit);
        GLint texture = 0; glGetIntegerv(GL_TEXTURE_BINDING_2D, &texture);
        if (static_cast<uint32_t>(texture) == clean_texture) valid = false;
    }
    dispatch.ActiveTexture(previous); return valid;
}
}
void cancel() {
    if (hud) end_hud();
    ghost_compositor::abort(compositor);
    provisional = ready = false; occurrence = 0; draw_token = {};
}
void context_lost() {
    ghost_ipc::presented(0);
    cancel();
    ghost_playback::retire_on_context_loss();
    if (compositor) {
        if (reinterpret_cast<uintptr_t>(wglGetCurrentContext()) == owner.context)
            ghost_compositor::destroy(compositor);
        else ghost_compositor::abandon(compositor);
    }
    compositor = nullptr; owner = {}; presentation_owner = {}; dispatch = {};
    model_id = 0; prepared_width = prepared_height = width = height = 0;
    get_uniform = nullptr;
}
void begin(uint32_t rom, uint32_t config_id) {
    cancel();
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
    sg_atlas_view atlas{}; uint64_t wanted_model = 0;
    if (!ghost_playback::atlas(&atlas, &wanted_model)) return;
    if (!same(identity, owner)) {
        if (compositor) {
            if (identity.context == owner.context) ghost_compositor::destroy(compositor);
            else ghost_compositor::abandon(compositor);
        }
        compositor = nullptr; prepared_width = prepared_height = 0; model_id = 0;
        if (!ghost_dispatch::load(dispatch)) return;
        owner = identity;
        get_uniform = reinterpret_cast<GetUniform>(wglGetProcAddress("glGetUniformiv"));
        const auto proc = reinterpret_cast<uintptr_t>(get_uniform);
        if (proc <= 3 || proc == UINTPTR_MAX) get_uniform = nullptr;
        if (!replay_gl::BeginCapture()) return;
        compositor = ghost_compositor::create(dispatch, owner.context_generation);
        if (!replay_gl::EndCapture()) { ghost_compositor::abandon(compositor); compositor = nullptr; return; }
    }
    if (compositor && width && height && (width != prepared_width || height != prepared_height || model_id != wanted_model)) {
        if (!replay_gl::BeginCapture()) return;
        // One vertex range per displayed ghost; begin_batch shares the buffer.
        const auto result = ghost_compositor::prepare(compositor, width, height, GP_GHOSTS * SG_MODEL_MAX_VERTICES,
                                                     atlas.rgba, atlas.width, atlas.height);
        const bool clean = replay_gl::EndCapture();
        if (result == ghost_compositor::Result::ok && clean) {
            prepared_width = width; prepared_height = height; model_id = wanted_model;
        }
    }
}
void observe(const ghost::RasterTarget &target) {
    if (target.width && target.height && target.width <= 8192 && target.height <= 8192) {
        width = target.width; height = target.height;
    }
}
void seal(const gc_sample &sample, const ghost::WorldPass &world) {
    if (!compositor || !model_id || !ghost_presentation::clean_consumer()
            || world.context_generation != owner.context_generation
            || world.target.width != prepared_width || world.target.height != prepared_height) return;
    auto *buffer = ghost_link::raster_buffer();
    if (!buffer || !buffer->m_pTexture || uint32_t(buffer->m_FBO) != world.target.color) return;
    // Zero visible ghosts leaves this frame exactly as a hidden single ghost did.
    ghost_compositor::Pose poses[GP_GHOSTS]{}; uint32_t count = 0;
    if (!ghost_playback::evaluate(sample, world, poses, count, draw_token)) return;
    ghost_compositor::Target target{};
    target.occurrence = world.list_serial; target.generation = world.context_generation;
    target.framebuffer = world.target.color; target.width = world.target.width; target.height = world.target.height;
    target.depth = world.target.depth_mode ? ghost_compositor::Depth::n64_image : ghost_compositor::Depth::ordinary;
    target.n64_depth_texture = world.target.depth_mode ? world.target.depth : 0;
    target.samples = world.target.samples;
    target.depth_scale = world.target.depth_scale[0]; target.depth_translate = world.target.depth_scale[1];
    std::memcpy(target.viewport, world.target.viewport, sizeof(target.viewport));
    glGetIntegerv(GL_SCISSOR_BOX, target.scissor);
    if (!replay_gl::BeginCapture()) return;
    const auto result = ghost_compositor::begin_batch(compositor, target, poses, count);
    const bool clean = replay_gl::EndCapture();
    provisional = result == ghost_compositor::Result::ok && clean;
    if (provisional) {
        occurrence = world.list_serial; clean_fbo = world.target.color;
        clean_texture = uint32_t(buffer->m_pTexture->name);
    } else cancel();
}
void finish(const ghost::WorldPass *world) {
    if (!provisional || !world || world->list_serial != occurrence || !ghost_playback::current(draw_token)) {
        cancel(); return;
    }
    ready = ghost_compositor::finalize(compositor, occurrence) == ghost_compositor::Result::ok;
    if (!ready) cancel();
}
bool begin_layer(uint32_t pass, uint32_t framebuffer) {
    if (layer || !compositor || !model_id || pass > 1 || !ghost_presentation::clean_consumer()
            || width != prepared_width || height != prepared_height) return false;
    ghost_compositor::Target target{};
    target.generation = owner.context_generation; target.framebuffer = framebuffer;
    target.width = width; target.height = height; target.samples = 1;
    target.unsupported = config.video.multisampling > 0;
    target.depth = ghost_compositor::Depth::ordinary;
    if (!replay_gl::BeginCapture()) return false;
    layer = ghost_compositor::begin_layer(compositor, target, ghost_compositor::LayerPass(pass))
        == ghost_compositor::Result::ok;
    // A surface whose color entered the layer without its depth would tint
    // nothing behind it consistently: drop the layer for this list instead.
    if (!layer && (!replay_gl::EndCapture() || pass == 1)) ghost_compositor::break_layer(compositor);
    return layer;
}
void end_layer() {
    if (!layer) return;
    ghost_compositor::end_layer(compositor); layer = false;
    if (!replay_gl::EndCapture()) ghost_compositor::break_layer(compositor);
}
void break_layer() { ghost_compositor::break_layer(compositor); }
bool begin_hud() {
    if (!provisional || hud) return false;
    auto *buffer = ghost_link::raster_buffer();
    if ((gSP.geometryMode & G_ZBUFFER) || !buffer || buffer->m_isDepthBuffer
            || uint32_t(buffer->m_FBO) != clean_fbo || !image_writes_disabled() || !no_color_feedback()) {
        cancel(); return false;
    }
    if (!replay_gl::BeginCapture()) { cancel(); return false; }
    hud = ghost_compositor::begin_hud(compositor, occurrence) == ghost_compositor::Result::ok;
    if (!hud) { replay_gl::EndCapture(); cancel(); }
    return hud;
}
void end_hud() {
    if (!hud) return;
    ghost_compositor::end_hud(compositor); hud = false;
    if (!replay_gl::EndCapture()) cancel();
}
}
