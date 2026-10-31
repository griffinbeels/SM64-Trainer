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
uint32_t width = 640, height = 480;
constexpr uint32_t screen = 7;
renderer::Identity active_identity{1, 1, 1, 1, 0};
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
    renderer::Identity id = active_identity;
    if (!id.owner_thread) id.owner_thread = GetCurrentThreadId();
    return id;
}
CachedTexture color{11, width, height};
FrameBuffer target_buffer{screen, &color, false};
struct Faults {
    int renderer_before_surface = -1, renderer_before_hud = -1;
    bool renderer_after_huds = false, batch = false, hud_gl = false, layer_gl = false, throw_in_layer = false;
} faults;
struct Seen {
    unsigned layer_passes = 0, broken = 0, finalized = 0, hud_mirrors = 0, warmed = 0, stacked = 0, values = 0;
    unsigned created = 0, prepared = 0, destroyed = 0, abandoned = 0, presentation_ready = 0, dispatch_loads = 0;
    unsigned foreign_restores = 0;
    uint32_t prepared_width = 0, prepared_height = 0;
} seen;
ghost_presentation::ImageProvider image_provider = nullptr;
ghost_presentation::ImageValidity image_validity = nullptr;
ghost_compositor::Display shown{};
bool finalized = false, token_current = true;
bool usamune_hidden = false; // the list's Usamune HUD hides the player's display
int usamune_values = -1;      // its Stick (0) / Values (1) setting, -1 unknown
bool model_ready = true; // playback has built Mario's model
// What a scenario makes refuse (the draw facts' gates): compositor creation,
// its prepare and batch results, the playback boundary's thread, negotiation.
struct Knobs {
    bool refuse_create = false, other_thread = false, unnegotiated = false;
    ghost_compositor::Result prepare = ghost_compositor::Result::ok, batch = ghost_compositor::Result::ok;
} knobs;
bool chooses = true; // the list's plan chose a ghost
bool layer_broken = false, begun = false, in_hud = false, layer_active = false;
uint64_t layer_generation = 0;
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
// link_dispatch's record of the invalidation: the gateway's close, with the error it kept.
void BindingsCause(rs_bindings_cause *out) { *out = {}; out->site = RS_BINDINGS_GATEWAY_CLOSE_ERROR; out->gl_error = gl.saved; }
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
Instance *create(const Dispatch &, uint64_t) {
    ++seen.created; begun = in_hud = finalized = layer_active = false; return knobs.refuse_create ? nullptr : &instance;
}
Result prepare(Instance *, uint32_t w, uint32_t h, uint32_t, const uint8_t *, uint32_t, uint32_t) {
    ++seen.prepared; seen.prepared_width = w; seen.prepared_height = h; return knobs.prepare;
}
Result prepare_particles(Instance *, const uint8_t *, uint32_t, uint32_t) { return Result::unsupported; }
Result prepare_annotations(Instance *, const uint8_t *, uint32_t, uint32_t) { return Result::unsupported; }
Result warm(Instance *) { ++seen.warmed; return Result::ok; }
Result begin_batch(Instance *, const Target &target, const Pose *poses, uint32_t count, const Known *,
                   const ghost_path::Frame *) {
    if (knobs.batch != Result::ok) return knobs.batch; // refused before any GL, as an MSAA target is
    if (faults.batch) fail_gl(Owner::ghost);
    for (uint32_t i = 0; i < count; ++i) {seen.stacked += poses[i].annotation.hud; seen.values += poses[i].annotation.values;}
    shown = {target.occurrence, target.generation, 22, 23, target.width, target.height};
    begun = count != 0; finalized = false; return begun ? Result::ok : Result::invalid;
}
Result begin_hud(Instance *, uint64_t, const Known *) {
    if (!begun || in_hud) return Result::invalid;
    in_hud = true; ++seen.hud_mirrors;
    if (faults.hud_gl) fail_gl(Owner::ghost);
    return Result::ok;
}
void end_hud(Instance *) {
    if (in_hud && shown.generation != identity().context_generation) ++seen.foreign_restores;
    in_hud = false;
}
Result begin_layer(Instance *, const Target &target, LayerPass, const Known *) {
    if (layer_broken) return Result::state;
    ++seen.layer_passes;
    if (faults.layer_gl) fail_gl(Owner::layer);
    layer_active = true; layer_generation = target.generation;
    return Result::ok;
}
void end_layer(Instance *) {
    if (layer_active && layer_generation != identity().context_generation) ++seen.foreign_restores;
    layer_active = false;
}
void clear_layer(Instance *p) { end_layer(p); layer_broken = false; }
void break_layer(Instance *) { if (!layer_broken) { layer_broken = true; ++seen.broken; } }
Result finalize(Instance *, uint64_t) {
    if (!begun || in_hud) return Result::invalid;
    ++seen.finalized; finalized = true; return Result::ok;
}
Display display(const Instance *) { return finalized ? shown : Display{}; }
void abort(Instance *) {
    if (in_hud && shown.generation != identity().context_generation) ++seen.foreign_restores;
    begun = in_hud = finalized = false;
}
Cost take_cost(Instance *) { return {}; }
void destroy(Instance *) { ++seen.destroyed; }
void abandon(Instance *) { ++seen.abandoned; }
}
namespace ghost_dispatch { bool load(ghost_compositor::Dispatch &) { ++seen.dispatch_loads; return true; } }
namespace ghost_playback {
bool drawing() { return true; } // a displayed request holds its clips, finished or not
bool plan(const gc_sample &) { return chooses; }
bool safe_boundary(uint32_t, uint32_t) { return !knobs.other_thread; }
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
bool current(const gp_draw_token &token) { return token_current && token.revision != 0; }
bool records_display() { return false; }
const ghost_path::Frame *path() { return nullptr; }
void retire_on_context_loss() {}
}
namespace ghost_presentation {
bool clean_consumer() { return !knobs.unnegotiated; }
void set_provider(ImageProvider provider, PresentationNotice, ImageValidity validity) {
    image_provider = provider; image_validity = validity;
}
void context_ready() { ++seen.presentation_ready; }
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
    world.list_serial = serial; world.context_generation = identity().context_generation;
    color.realWidth = width; color.realHeight = height;
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
void fresh_context() {
    ++active_identity.context_generation; run_list(0, 0); run_list(0, 0);
}
void scenario(const char *name, bool comma, Faults fault) {
    reset(); fresh_context();
    run_list(3, 5); // a clean list first: the scenario's own starts clean
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
#ifndef GHOST_DRAW_BASELINE
// The ghost's own error at its display scope's close costs the source one
// invalidation, which the source rebuilds (modelled here by the witness going
// nominal again). The ghost then asks nothing more of that context, and draws
// again in the next one.
void ghost_steps_aside() {
    reset(); fresh_context();
    const auto clean = run_list(3, 5);
    faults.batch = true; const auto faulted = run_list(3, 5); faults = {};
    const bool kept = gl.saved_owner == Owner::ghost && !gl.nominal;
    gl.nominal = true; gl.saved = GL_NO_ERROR; gl.saved_owner = Owner::none; // the source rebuilt
    const auto after = run_list(3, 5);
    const auto again = run_list(3, 5);
    const bool nominal = gl.nominal;
    fresh_context();
    const auto next = run_list(3, 5);
    std::printf(",\"ghost_steps_aside\":{\"clean_drawn\":%u,\"faulted_drawn\":%u,\"kept\":%s,"
                "\"after_drawn\":%u,\"after_reads\":%u,\"after_layers\":%u,\"again_reads\":%u,"
                "\"nominal\":%s,\"next_context_drawn\":%u}",
                clean.drawn, faulted.drawn, kept ? "true" : "false", after.drawn, after.reads, after.layer_passes,
                again.reads, nominal ? "true" : "false", next.drawn);
    check(!gl.open, "no gateway scope outlives the ghost stepping aside");
}
void transitions() {
    ghost_presentation::ViSource source{screen, color.name, width, height, 1,
        identity().context_generation, identity().drawable_generation};
    ghost_presentation::GhostViImage old_image{};
    check(image_provider && image_validity && image_provider(source, &old_image), "current image is eligible");
    const Seen before = seen;
    ++active_identity.drawable_generation; ++active_identity.read_drawable;
    width = 800; height = 600;
    auto resized = source; resized.width = width; resized.height = height;
    resized.drawable_generation = active_identity.drawable_generation;
    ghost_presentation::GhostViImage image{};
    check(!image_provider(resized, &image), "new drawable cannot borrow an old image before its list");
    model_ready = chooses = false; run_list(0, 0); run_list(0, 0);
    check(seen.created == before.created && seen.warmed == before.warmed && seen.dispatch_loads == before.dispatch_loads,
          "same-context drawable transition retains the compositor and warmed programs");
    check(seen.destroyed == before.destroyed && seen.abandoned == before.abandoned,
          "same-context drawable transition retires no context-owned resources");
    check(seen.presentation_ready == before.presentation_ready + 1, "drawable transition refreshes presentation identity");
    check(!image_validity(old_image) && !image_provider(source, &image), "old image is unavailable after transition");
    model_ready = chooses = true; run_list(0, 0); run_list(0, 0);
    check(seen.prepared == before.prepared + 1 && seen.prepared_width == width && seen.prepared_height == height,
          "resized extent prepares once without rebuilding programs");
    check(image_provider(resized, &image), "new drawable gets its own finalized image");
    token_current = false;
    check(!image_validity(image) && !image_provider(resized, &image), "revoked draw token refuses the image");
    token_current = true;
    const Seen resized_seen = seen;
    ++active_identity.context_generation; // the handle is reused: old GPU names are foreign
    model_ready = chooses = false; run_list(0, 0); run_list(0, 0);
    check(seen.created == resized_seen.created + 1 && seen.warmed == resized_seen.warmed + 1,
          "true context generation change prewarms before another ghost");
    check(seen.abandoned == resized_seen.abandoned + 1 && seen.destroyed == resized_seen.destroyed,
          "reused context handle never deletes the old generation's names");
    check(!image_validity(image) && !image_provider(resized, &image), "old generation image is refused");
    const Seen before_loss = seen;
    ++active_identity.context_generation; ghost_draw::context_lost();
    check(seen.abandoned == before_loss.abandoned + 1 && seen.destroyed == before_loss.destroyed,
          "explicit loss cannot delete names under a reused handle's new generation");
    run_list(0, 0);run_list(0, 0);
    check(seen.warmed == before_loss.warmed + 1, "loss rebuild still prewarms before a ghost");
    const Seen owned_loss = seen;
    ghost_draw::context_lost();
    check(seen.destroyed == owned_loss.destroyed + 1 && seen.abandoned == owned_loss.abandoned,
          "proven live ownership destroys resources before normal loss");
    run_list(0, 0);run_list(0, 0);
    model_ready = chooses = true; run_list(0, 0); run_list(0, 0);
    gc_sample pending{};pending.sequence = pending.dlist_serial = 100000;
    ghost::WorldPass world{};world.list_serial = pending.dlist_serial;
    world.context_generation = identity().context_generation;
    world.target.color = screen;world.target.width = width;world.target.height = height;world.target.samples = 1;
    ghost_draw::begin(7, 4, &pending);ghost_draw::observe(world.target);
    gSP.geometryMode = 0;ghost_draw::seal(pending, world, false, -1);
    check(ghost_draw::begin_hud() && gl.open, "old display and HUD scopes are open for missed-loss case");
    ++active_identity.context_generation;gl.open = false; // the new dispatch has no old gateway scope
    ghost_draw::begin(7, 4, &pending);
    check(!seen.foreign_restores && !gl.open, "begin abandons old display scopes without foreign restoration");
    run_list(0, 0);run_list(0, 0);
    ghost_draw::begin(7, 4, &pending);
    check(ghost_draw::begin_layer(0, screen, ghost_link::attachments(&target_buffer)) && gl.open,
          "old layer scope is open for missed-loss case");
    ++active_identity.context_generation;gl.open = false;
    ghost_draw::begin(7, 4, &pending);
    check(!seen.foreign_restores && !gl.open, "begin abandons old layer scopes without foreign restoration");
    run_list(0, 0);run_list(0, 0);
}
// Every list publishes its first refusal (ghost_draw_facts.h), or DREW: the
// gate a tester's report names when no ghost shows.
uint32_t gate() { return ghost_facts::list_gate.load(); }
uint32_t list_flags() { return ghost_facts::list_flags.load(); }
uint32_t code(ghost_compositor::Result result) { return uint32_t(result) + 1; }
void show(const char *name, bool comma = true) { std::printf("%s\"%s\":%u", comma ? "," : "", name, gate()); }
// One list in the renderer's order, with a step of the scenario's own between.
void sealed_list(void (*between)(), bool sealed = true, bool finished = true) {
    static uint64_t serial = 50000; ++serial;
    gc_sample sample{}; sample.sequence = sample.dlist_serial = serial;
    ghost::WorldPass world{}; world.list_serial = serial; world.context_generation = identity().context_generation;
    world.target.color = screen; world.target.width = width; world.target.height = height; world.target.samples = 1;
    ghost_draw::begin(7, 4, &sample); ghost_draw::observe(world.target);
    gSP.geometryMode = 0;
    if (sealed) ghost_draw::seal(sample, world, false, -1);
    if (between) between();
    ghost_draw::finish(finished ? &world : nullptr);
    gSP.geometryMode = 0;
}
void gates() {
    reset(); fresh_context();
    std::printf(",\"gates\":{");
    run_list(3, 5);
    check(gate() == GDF_LIST_DREW && list_flags() == (GDF_COMPOSITOR | GDF_MODEL | GDF_PLANNED)
          && ghost_facts::create_step.load() == GDF_CREATE_CREATED && ghost_facts::batch_result.load() == code(ghost_compositor::Result::ok)
          && ghost_facts::finalize_result.load() == code(ghost_compositor::Result::ok)
          && ghost_facts::observed_width.load() == width && ghost_facts::prepared_width.load() == width,
          "a drawn list says it drew, with its compositor, model and plan");
    show("drew", false);
    chooses = false; run_list(0, 0); chooses = true;
    check(gate() == GDF_LIST_NO_PLAN && !(list_flags() & GDF_PLANNED), "playback choosing no ghost is the list's first refusal");
    show("no_plan");
    model_ready = chooses = false; run_list(0, 0); model_ready = chooses = true; // no model, no pose
    check(gate() == GDF_LIST_NO_MODEL, "no Mario model held by playback names itself");
    show("no_model");
    // Playback on another thread's boundary holds no model or pose for this one.
    knobs.other_thread = true; model_ready = chooses = false; run_list(0, 0);
    knobs.other_thread = false; model_ready = chooses = true;
    check(gate() == GDF_LIST_OTHER_THREAD, "a boundary another thread owns names itself");
    show("other_thread");
    knobs.unnegotiated = true; run_list(0, 0); knobs.unnegotiated = false;
    check(gate() == GDF_LIST_NO_CLEAN_CONSUMER, "no clean presentation negotiated names itself");
    show("no_clean_consumer");
    ghost_draw::begin(7, 4, nullptr); ghost_draw::finish(nullptr);
    check(gate() == GDF_LIST_NO_SAMPLE, "a list without a pose sample names itself");
    show("no_sample");
    knobs.batch = ghost_compositor::Result::unsupported; run_list(0, 0); knobs.batch = ghost_compositor::Result::ok;
    check(gate() == GDF_LIST_BATCH && ghost_facts::batch_result.load() == code(ghost_compositor::Result::unsupported),
          "a refused batch names itself and why (an MSAA target is unsupported)");
    show("batch");
    sealed_list([] { ghost_draw::cancel(GDF_LIST_TARGET_CHANGED); });
    check(gate() == GDF_LIST_TARGET_CHANGED, "a color target changed after the seal names itself");
    show("target_changed");
    sealed_list([] { gSP.geometryMode = G_ZBUFFER; check(!ghost_draw::begin_hud(), "a z-buffered HUD draw refuses"); });
    check(gate() == GDF_LIST_HUD, "a HUD mirror that refused names itself");
    show("hud");
    sealed_list(nullptr, false);
    check(gate() == GDF_LIST_NO_SEAL, "a planned list that never sealed names itself");
    show("no_seal");
    sealed_list(nullptr, true, false);
    check(gate() == GDF_LIST_STALE, "a list that ended incomplete names itself");
    show("stale");
    // Candidate B: the compositor refused Mario's model on a fresh context.
    ++active_identity.context_generation; knobs.prepare = ghost_compositor::Result::resources;
    run_list(0, 0); run_list(0, 0); knobs.prepare = ghost_compositor::Result::ok;
    check(gate() == GDF_LIST_PREPARE && ghost_facts::prepare_result.load() == code(ghost_compositor::Result::resources)
          && list_flags() == GDF_COMPOSITOR, "a refused model prepare names itself and its result");
    show("prepare");
    run_list(0, 0);
    check(gate() == GDF_LIST_DREW && ghost_facts::prepare_result.load() == code(ghost_compositor::Result::ok),
          "the next prepare that succeeds draws");
    // Candidate A: creation refused, or its scope refused, and never retried for that context.
    ++active_identity.context_generation; knobs.refuse_create = true; run_list(0, 0); knobs.refuse_create = false;
    check(gate() == GDF_LIST_NO_COMPOSITOR && ghost_facts::create_step.load() == GDF_CREATE_REFUSED && !list_flags(),
          "a refused compositor creation names itself");
    show("no_compositor");
    run_list(0, 0);
    check(gate() == GDF_LIST_NO_COMPOSITOR, "creation is not retried for its context, and every list says so");
    ++active_identity.context_generation; gl.saved = GL_INVALID_ENUM; run_list(0, 0); gl.saved = GL_NO_ERROR;
    check(gate() == GDF_LIST_CREATE_SCOPE && ghost_facts::create_step.load() == GDF_CREATE_SCOPE,
          "a gateway refused for compositor creation names itself");
    show("create_scope");
    run_list(0, 0);
    check(gate() == GDF_LIST_NO_COMPOSITOR, "after a refused creation scope every list has no compositor");
    // No world raster target seen yet for this context's prepare.
    ghost_draw::context_lost(); run_list(0, 0);
    check(gate() == GDF_LIST_NO_EXTENT && ghost_facts::observed_width.load() == width && !ghost_facts::prepared_width.load(),
          "a context with no raster extent yet names itself");
    show("no_extent");
    run_list(0, 0); check(gate() == GDF_LIST_DREW, "the next list, extent seen, draws");
    // Candidate A: a dirty display scope, then the ghost steps aside for the context.
    faults.batch = true; run_list(3, 5); faults = {};
    check(gate() == GDF_LIST_CLOSE_DIRTY && (list_flags() & GDF_FAULTED) && ghost_facts::fault_step.load() == GDF_FAULT_DISPLAY
          && ghost_facts::fault_site.load() == RS_BINDINGS_GATEWAY_CLOSE_ERROR
          && ghost_facts::fault_error.load() == GL_INVALID_OPERATION, "a dirty display scope names itself, its site and error");
    show("close_dirty");
    gl.nominal = true; gl.saved = GL_NO_ERROR; gl.saved_owner = Owner::none; // the source rebuilt
    run_list(3, 5);
    check(gate() == GDF_LIST_FAULTED && (list_flags() & GDF_FAULTED), "every later list of that context says the ghost stepped aside");
    show("faulted");
    std::printf("}");
    fresh_context(); run_list(0, 0);
    check(gate() == GDF_LIST_DREW && !(list_flags() & GDF_FAULTED), "the next context draws again");
}
#endif
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
        transitions();
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
#ifndef GHOST_DRAW_BASELINE
        std::printf("\"transitions\":{\"initial_prewarm\":true,\"drawable_rebuilds\":0,\"extent_prepares\":1,"
                    "\"generation_rebuilds\":1,\"foreign_deletes\":0,\"stale_images_refused\":true,\"revoked_token_refused\":true,"
                    "\"loss_foreign_deletes\":0,\"owned_loss_destroys\":1,\"missed_loss_foreign_restores\":0},");
#endif
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
#ifndef GHOST_DRAW_BASELINE
        ghost_steps_aside();
#endif
        std::printf("}");
#ifndef GHOST_DRAW_BASELINE
        gates();
#endif
        std::printf(",\"checks\":%u,\"driver_queries\":%u}\n", checks, clean_queries);
        return 0;
    } catch (const std::exception &error) {
        std::fprintf(stderr, "\nghost glue witness: %s (check %u)\n", error.what(), checks);
        return 1;
    }
}
