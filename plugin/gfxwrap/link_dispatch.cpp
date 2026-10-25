/* Compiled ONLY with the pinned source overlay, never the binary wrapper.
 * Observes real dispatch calls, including cached function pointers. No per-call
 * GL queries, allocations, locks, events, I/O or capture work. Raw void GL calls
 * do not prove success: this is a nominal-state candidate, not source admission.
 * After ContextCreated the only driver queries are Reconstruct's, asked by a
 * BINDINGS refusal or a ghost list start, at most once a second
 * (source_refusal.h, "THE WITNESS HEALS").
 *
 * ONLY ON A PRACTICE ROM (practice_rom.h, read from the header GLideN64 itself
 * parses in RSP_Init). For any other cartridge ContextCreated observes nothing,
 * InstallDispatch leaves GLideN64's raw function table untouched, and the
 * remaining aliases below are a pass-through call with one flag test: the
 * renderer is the baseline LINK GLideN64 (his ruling, 2026-09-16). */
#define REPLAY_GL_DISPATCH_IMPLEMENTATION
#include "GLFunctions.h"
#include "link_dispatch.h"
#include "N64.h"
#include "practice_rom.h"
#include <atomic>
#include <limits>
#include <cstdio>
#include <cstring>
// Driver queries Reconstruct makes, all GL 3.3 core. The pinned headers
// define them; these only guard a header that lacks one.
#ifndef GL_READ_FRAMEBUFFER_BINDING
#define GL_READ_FRAMEBUFFER_BINDING 0x8CAA
#endif
#ifndef GL_DRAW_FRAMEBUFFER_BINDING
#define GL_DRAW_FRAMEBUFFER_BINDING 0x8CA6
#endif
#ifndef GL_ACTIVE_TEXTURE
#define GL_ACTIVE_TEXTURE 0x84E0
#endif
#ifndef GL_CURRENT_PROGRAM
#define GL_CURRENT_PROGRAM 0x8B8D
#endif
#ifndef GL_VERTEX_ARRAY_BINDING
#define GL_VERTEX_ARRAY_BINDING 0x85B5
#endif
#ifndef GL_BLEND_SRC_RGB
#define GL_BLEND_SRC_RGB 0x80C9
#endif
#ifndef GL_BLEND_DST_RGB
#define GL_BLEND_DST_RGB 0x80C8
#endif
#ifndef GL_BLEND_SRC_ALPHA
#define GL_BLEND_SRC_ALPHA 0x80CB
#endif
#ifndef GL_BLEND_DST_ALPHA
#define GL_BLEND_DST_ALPHA 0x80CA
#endif

namespace replay_gl {
namespace {
renderer::State state;
renderer::Identity identity{};
PFNGLBINDFRAMEBUFFERPROC raw_bind_framebuffer = nullptr;
PFNGLDELETEFRAMEBUFFERSPROC raw_delete_framebuffers = nullptr;
PFNGLACTIVETEXTUREPROC raw_active_texture = nullptr;
PFNGLDEPTHRANGEFPROC raw_depth_range = nullptr;
PFNGLUSEPROGRAMPROC raw_use_program = nullptr;
PFNGLBINDVERTEXARRAYPROC raw_bind_vertex_array = nullptr;
PFNGLDELETEVERTEXARRAYSPROC raw_delete_vertex_arrays = nullptr;
PFNGLENABLEIPROC raw_enablei = nullptr;
PFNGLDISABLEIPROC raw_disablei = nullptr;
// Raster state the ghost hooks read per draw instead of querying the driver.
GLuint draw_framebuffer = 0;
GLint viewport[4]{};
GLfloat depth_range[2]{0, 1};
bool draw_known = false, viewport_known = false, depth_known = false;
// The render state a ghost frame restores (RenderState), from a fresh
// context's defaults on; known once the dispatch observes every entry of it.
RenderState render{};
bool render_known = false;
constexpr GLenum capabilities[] = {GL_DEPTH_TEST, GL_CULL_FACE, GL_POLYGON_OFFSET_FILL, GL_DEPTH_CLAMP,
    GL_CLIP_DISTANCE0, GL_BLEND, GL_SCISSOR_TEST, GL_STENCIL_TEST, GL_RASTERIZER_DISCARD,
    GL_FRAMEBUFFER_SRGB, GL_DITHER};
uint32_t enabled = 0; // bit i: capabilities[i] is on (BLEND and SCISSOR_TEST at index 0)
void fresh_render_state() {
    render = {};
    render.depth_func = GL_LESS; render.depth_mask = true;
    render.blend[0] = render.blend[2] = GL_ONE; render.blend[1] = render.blend[3] = GL_ZERO;
    enabled = 1u << 10; // only DITHER starts enabled
}
void set_capability(GLenum capability, bool on) {
    for (unsigned i = 0; i < sizeof(capabilities) / sizeof(capabilities[0]); ++i) {
        if (capabilities[i] != capability) continue;
        enabled = on ? enabled | (1u << i) : enabled & ~(1u << i); return;
    }
}
uint32_t generation = 0, dispatch_generation = 0;
unsigned unit_limit = 0;
unsigned drawable_width = 0, drawable_height = 0;
GLuint read_framebuffer = 0;
bool nominal = false, double_buffer = false;
bool practice = false; // the context belongs to a practice ROM
bool capture_open = false;
GLenum saved_error = GL_NO_ERROR;
// One pending answer for the renderer: a later consumed error never replaces it.
void keep_for_renderer(GLenum error) { if (saved_error == GL_NO_ERROR) saved_error = error; }
uint32_t source_format = RB_SOURCE_UNKNOWN;
// THE BINDINGS CAUSE (source_refusal.h): why CandidateBindings is false. Plain
// renderer-thread writes of values each site already holds; no query or wait.
constexpr uint32_t saturated = (std::numeric_limits<uint32_t>::max)();
uint32_t cause_site = RS_BINDINGS_SITE_NONE, cause_error = 0, cause_invalidations = 0, cause_after_ms = 0;
uint32_t contexts_created = 0;
uint64_t created_ms = 0;
bool created = false, armed = false, install_called = false, lost = false;
// A structural site since ContextCreated (rs_bindings_site_recoverable), or a
// driver error that leaves state undefined: no reconstruction this context.
bool structural = false;
uint32_t ms_since_created() {
    const uint64_t elapsed = created ? GetTickCount64() - created_ms : 0;
    return elapsed > saturated ? saturated : uint32_t(elapsed);
}
// Every site that leaves the observation unarmed. The first one since
// ContextCreated, or since the last reconstruction, is the cause.
void note(uint32_t site, GLenum error = GL_NO_ERROR) {
    if (cause_invalidations != saturated) ++cause_invalidations;
    if (!rs_bindings_site_recoverable(site)) structural = true;
    if (cause_site) return;
    cause_site = site; cause_error = error; cause_after_ms = ms_since_created();
}
// Reconstruct's bookkeeping for this context: at most one attempt a second.
uint32_t reconstruct_attempts = 0, reconstruct_successes = 0;
uint64_t last_attempt_ms = 0;
bool attempted = false;
int gl_major = 0, gl_minor = 0; // GL_VERSION at ContextCreated
bool render_hooks = false;      // InstallDispatch observes every render-state entry
// The context's GL identity and reconstruction counts for the wrapper
// (SM64ReplayContextEvidenceV1): this thread's copy, published whole, copied
// by any thread without GL.
std::atomic<uint32_t> evidence_seq{0}, evidence_words[sizeof(rs_context_evidence) / 4]{};
rs_context_evidence evidence{};
bool identity_tried = false;
void publish_evidence() {
    evidence.bytes = sizeof evidence; evidence.version = RS_CONTEXT_EVIDENCE_V1;
    uint32_t words[sizeof evidence / 4];
    memcpy(words, &evidence, sizeof evidence);
    const auto seq = evidence_seq.load();
    evidence_seq.store(seq + 1);
    for (unsigned i = 0; i < sizeof evidence / 4; ++i) evidence_words[i].store(words[i]);
    evidence_seq.store(seq + 2);
}
void publish_identity(uint32_t source) {
    evidence.source = source;
    rs_bounded_text(evidence.vendor, sizeof evidence.vendor, glGetString(GL_VENDOR));
    rs_bounded_text(evidence.renderer, sizeof evidence.renderer, glGetString(GL_RENDERER));
    rs_bounded_text(evidence.gl_version, sizeof evidence.gl_version, glGetString(GL_VERSION));
    publish_evidence();
}
void observe_source_format() {
    // ContextCreated is called only for a fresh context, before renderer state
    // mutation: default READ/DRAW framebuffers are both zero. No temporary binds.
    const auto version=reinterpret_cast<const char*>(glGetString(GL_VERSION));
    int major=0, minor=0;
    if (!version || std::sscanf(version,"%d.%d",&major,&minor)<1 || major<3) return;
    gl_major=major; gl_minor=minor;
    auto query=reinterpret_cast<PFNGLGETFRAMEBUFFERATTACHMENTPARAMETERIVPROC>(
        wglGetProcAddress("glGetFramebufferAttachmentParameteriv"));
    const auto address=reinterpret_cast<uintptr_t>(query);
    if (address<=3 || address==~uintptr_t(0)) return;
    GLint object=GL_NONE, type=GL_NONE, red=0, green=0, blue=0, encoding=GL_NONE, samples=-1;
    query(GL_READ_FRAMEBUFFER,GL_FRONT_LEFT,GL_FRAMEBUFFER_ATTACHMENT_OBJECT_TYPE,&object);
    if (object!=GL_FRAMEBUFFER_DEFAULT) return;
    query(GL_READ_FRAMEBUFFER,GL_FRONT_LEFT,GL_FRAMEBUFFER_ATTACHMENT_COMPONENT_TYPE,&type);
    query(GL_READ_FRAMEBUFFER,GL_FRONT_LEFT,GL_FRAMEBUFFER_ATTACHMENT_RED_SIZE,&red);
    query(GL_READ_FRAMEBUFFER,GL_FRONT_LEFT,GL_FRAMEBUFFER_ATTACHMENT_GREEN_SIZE,&green);
    query(GL_READ_FRAMEBUFFER,GL_FRONT_LEFT,GL_FRAMEBUFFER_ATTACHMENT_BLUE_SIZE,&blue);
    query(GL_READ_FRAMEBUFFER,GL_FRONT_LEFT,GL_FRAMEBUFFER_ATTACHMENT_COLOR_ENCODING,&encoding);
    glGetIntegerv(GL_SAMPLE_BUFFERS,&samples);
    // ReadPixels and CopyTex[Sub]Image transfer the stored RGB values without
    // sRGB conversion. Restrict to RGB8 UNORM so byte quantization is unchanged.
    // Do not clear errors: the existing source gateway/first BeginCapture owns them.
    if (type!=GL_UNSIGNED_NORMALIZED || red!=8 || green!=8 || blue!=8 || samples!=0) return;
    if (encoding==GL_LINEAR) source_format=RB_SOURCE_RGB8_LINEAR;
    else if (encoding==GL_SRGB) source_format=RB_SOURCE_RGB8_SRGB;
}
void invalidate(uint32_t site, GLenum error = GL_NO_ERROR) {
    note(site, error);
    nominal = false; state.unknown(); draw_known = viewport_known = depth_known = false;
    render_known = false; render.scissor_known = false;
}
void APIENTRY BindFramebuffer(GLenum target, GLuint name) {
    raw_bind_framebuffer(target, name);
    if (!nominal) return;
    if (target != GL_FRAMEBUFFER && target != GL_READ_FRAMEBUFFER && target != GL_DRAW_FRAMEBUFFER) {
        invalidate(RS_BINDINGS_FRAMEBUFFER_TARGET); return;
    }
    state.bind_framebuffer(target, name);
    if (target != GL_DRAW_FRAMEBUFFER) read_framebuffer = name;
    if (target != GL_READ_FRAMEBUFFER) draw_framebuffer = name;
}
void APIENTRY DeleteFramebuffers(GLsizei count, const GLuint *names) {
    raw_delete_framebuffers(count, names);
    if (!nominal) return;
    if (count < 0 || unsigned(count) > renderer::State::deletion_limit || (count && !names)) {
        invalidate(RS_BINDINGS_FRAMEBUFFER_DELETE); return;
    }
    state.delete_framebuffers(count, names);
    for (GLsizei n = 0; n < count; ++n) {
        if (names[n] == read_framebuffer) read_framebuffer = 0;
        if (names[n] && names[n] == draw_framebuffer) draw_framebuffer = 0; // deleting the bound FBO binds 0
    }
}
void APIENTRY DepthRangef(GLfloat nearest, GLfloat farthest) {
    raw_depth_range(nearest, farthest);
    if (!nominal) return;
    // GL clamps both to 0..1.
    depth_range[0] = nearest < 0 ? 0 : nearest > 1 ? 1 : nearest;
    depth_range[1] = farthest < 0 ? 0 : farthest > 1 ? 1 : farthest;
    depth_known = true;
}
void APIENTRY ActiveTexture(GLenum unit) {
    raw_active_texture(unit);
    if (!nominal) return;
    if (unit < GL_TEXTURE0 || unit - GL_TEXTURE0 >= unit_limit) { invalidate(RS_BINDINGS_TEXTURE_UNIT); return; }
    state.active_texture(unit);
}
void APIENTRY UseProgram(GLuint program) {
    raw_use_program(program);
    if (nominal) render.program = program;
}
void APIENTRY BindVertexArray(GLuint name) {
    raw_bind_vertex_array(name);
    if (nominal) render.vertex_array = name;
}
void APIENTRY DeleteVertexArrays(GLsizei count, const GLuint *names) {
    raw_delete_vertex_arrays(count, names);
    if (!nominal) return;
    if (count < 0 || (count && !names)) { invalidate(RS_BINDINGS_VERTEX_ARRAY_DELETE); return; }
    for (GLsizei n = 0; n < count; ++n)
        if (names[n] && names[n] == render.vertex_array) render.vertex_array = 0; // deleting the bound array binds 0
}
void APIENTRY Enablei(GLenum capability, GLuint index) {
    raw_enablei(capability, index);
    if (nominal && index == 0) set_capability(capability, true);
}
void APIENTRY Disablei(GLenum capability, GLuint index) {
    raw_disablei(capability, index);
    if (nominal && index == 0) set_capability(capability, false);
}
}
void ContextLost() {
    nominal = false; practice = false; identity = {}; dispatch_generation = 0; state.lost();
    draw_known = viewport_known = depth_known = false; render_known = false; render.scissor_known = false;
    drawable_width = drawable_height = 0;
    capture_open = false; saved_error = GL_NO_ERROR;
    source_format = RB_SOURCE_UNKNOWN;
    cause_site = RS_BINDINGS_SITE_NONE; cause_error = cause_invalidations = cause_after_ms = 0;
    created = armed = install_called = false; lost = true;
    structural = attempted = render_hooks = false; reconstruct_attempts = reconstruct_successes = 0;
    last_attempt_ms = 0; gl_major = gl_minor = 0;
    evidence.context_generation = evidence.reconstruct_attempts = evidence.reconstruct_successes = 0;
    evidence.reconstructed_site = evidence.reconstructed_gl_error = evidence.reconstructed_ms = 0;
}
void DrawableChanged() {
    drawable_width = drawable_height = 0;
    // Followed while armed, nominal or not: a resize during an invalidation
    // still advances the drawable a reconstructed witness names.
    if (!armed) return;
    if (GetCurrentThreadId() != identity.owner_thread
            || identity.drawable_generation == (std::numeric_limits<uint32_t>::max)()) {
        invalidate(RS_BINDINGS_DRAWABLE_CHANGED); return;
    }
    ++identity.drawable_generation;
    if (nominal) state.drawable_changed(identity.drawable_generation);
}
bool DrawableCommitted(HWND window, bool resized) {
    drawable_width = drawable_height = 0;
    if (!resized || !armed || GetCurrentThreadId() != identity.owner_thread) return resized;
    RECT bounds{};
    if (!window || !GetClientRect(window, &bounds) || bounds.right <= bounds.left || bounds.bottom <= bounds.top) {
        invalidate(RS_BINDINGS_DRAWABLE_COMMITTED); return resized;
    }
    drawable_width = unsigned(bounds.right - bounds.left);
    drawable_height = unsigned(bounds.bottom - bounds.top);
    return resized;
}
bool DrawableExtent(unsigned *width, unsigned *height) {
    if (!width || !height || !nominal || GetCurrentThreadId() != identity.owner_thread
            || !drawable_width || !drawable_height) return false;
    *width = drawable_width; *height = drawable_height; return true;
}
void ContextCreated(HGLRC context, HDC drawable, bool buffered) {
    ContextLost();
    // Bookkeeping only, so a cause can tell "never created" from "not a practice ROM".
    lost = false; created = true; created_ms = GetTickCount64();
    if (contexts_created != saturated) ++contexts_created;
    practice = HEADER && practice_rom(HEADER);
    if (!practice) return; // no queries, no observed state, never a capture source
    evidence.context_generation = 0;
    if (!context || !drawable || context != wglGetCurrentContext() || drawable != wglGetCurrentDC()) {
        note(RS_BINDINGS_CREATED_NOT_CURRENT); return;
    }
    // Lifecycle only: names the GPU even if no surface ever qualifies.
    publish_identity(RS_IDENTITY_CONTEXT_CREATED); identity_tried = true;
    if (generation == (std::numeric_limits<uint32_t>::max)()) { note(RS_BINDINGS_CREATED_EXHAUSTED); return; }
    GLint units = 0;
    glGetIntegerv(GL_MAX_COMBINED_TEXTURE_IMAGE_UNITS, &units); // lifecycle only
    if (units <= 0) { note(RS_BINDINGS_CREATED_NO_UNITS); return; }
    unit_limit = unsigned(units) < renderer::State::texture_units ? unsigned(units) : renderer::State::texture_units;
    ++generation;
    identity = {reinterpret_cast<uintptr_t>(context), reinterpret_cast<uintptr_t>(drawable),
                generation, generation, GetCurrentThreadId()};
    double_buffer = buffered; read_framebuffer = 0;
    // A fresh context draws to its default framebuffer over depth 0..1; its
    // viewport is the window's until the renderer sets one, so it is unknown.
    draw_framebuffer = 0; draw_known = true; depth_range[0] = 0; depth_range[1] = 1; depth_known = true;
    viewport_known = false;
    fresh_render_state(); // known once InstallDispatch observes the pointer-called entries too
    state.created(identity, buffered); nominal = true; armed = true;
    observe_source_format();
    evidence.context_generation = generation; publish_evidence();
    // No shared context, ever: capture runs on this thread in this context
    // (renderer-thread capture), so the driver only sees the original
    // single-context renderer, with or without a recording.
}
void InstallDispatch() {
    if (!practice) return; // baseline ROM: GLideN64's own raw dispatch, untouched
    install_called = true;
    // Upstream must have resolved fresh raw addresses. Reject accidental repeated
    // installation rather than capturing ourselves and recursively dispatching.
    if (g_glBindFramebuffer == &BindFramebuffer || g_glDeleteFramebuffers == &DeleteFramebuffers
            || g_glActiveTexture == &ActiveTexture || g_glDepthRangef == &DepthRangef
            || g_glUseProgram == &UseProgram || g_glBindVertexArray == &BindVertexArray
            || g_glDeleteVertexArrays == &DeleteVertexArrays || g_glEnablei == &Enablei
            || g_glDisablei == &Disablei) { invalidate(RS_BINDINGS_DISPATCH_REINSTALLED); return; }
    raw_bind_framebuffer = g_glBindFramebuffer;
    raw_delete_framebuffers = g_glDeleteFramebuffers;
    raw_active_texture = g_glActiveTexture;
    raw_depth_range = g_glDepthRangef;
    raw_use_program = g_glUseProgram;
    raw_bind_vertex_array = g_glBindVertexArray;
    raw_delete_vertex_arrays = g_glDeleteVertexArrays;
    raw_enablei = g_glEnablei;
    raw_disablei = g_glDisablei;
    if (raw_bind_framebuffer) g_glBindFramebuffer = &BindFramebuffer;
    if (raw_delete_framebuffers) g_glDeleteFramebuffers = &DeleteFramebuffers;
    if (raw_active_texture) g_glActiveTexture = &ActiveTexture;
    if (raw_depth_range) g_glDepthRangef = &DepthRangef;
    if (raw_use_program) g_glUseProgram = &UseProgram;
    if (raw_bind_vertex_array) g_glBindVertexArray = &BindVertexArray;
    if (raw_delete_vertex_arrays) g_glDeleteVertexArrays = &DeleteVertexArrays;
    if (raw_enablei) g_glEnablei = &Enablei;
    if (raw_disablei) g_glDisablei = &Disablei;
    if (!raw_bind_framebuffer || !raw_delete_framebuffers || !raw_active_texture
            || !nominal || reinterpret_cast<HGLRC>(identity.context) != wglGetCurrentContext()
            || reinterpret_cast<HDC>(identity.read_drawable) != wglGetCurrentDC()) {
        invalidate(!raw_bind_framebuffer || !raw_delete_framebuffers || !raw_active_texture
            ? RS_BINDINGS_DISPATCH_INCOMPLETE : !nominal ? RS_BINDINGS_DISPATCH_UNARMED : RS_BINDINGS_DISPATCH_NOT_CURRENT);
        return;
    }
    dispatch_generation = identity.context_generation;
    // Every entry that changes the render state is now observed: the
    // pointer-called ones above, the GL 1.1 exports aliased at the call.
    // Without glEnablei the renderer switches BLEND through glEnable.
    render_hooks = raw_use_program && raw_bind_vertex_array && raw_delete_vertex_arrays
        && !raw_enablei == !raw_disablei;
    render_known = render_hooks;
}
void APIENTRY Viewport(GLint x, GLint y, GLsizei width, GLsizei height) {
    ::glViewport(x, y, width, height);
    if (!nominal) return;
    viewport[0] = x; viewport[1] = y; viewport[2] = width; viewport[3] = height;
    viewport_known = width >= 0 && height >= 0;
}
void APIENTRY ReadBuffer(GLenum mode) {
    ::glReadBuffer(mode);
    if (!nominal) return;
    // Conservative legal subset. Screenshot helpers can restore a selector from
    // a different FBO class. Preserve their GL call, but never certify its intent.
    const bool legal = read_framebuffer
        ? (mode == GL_NONE || mode == GL_COLOR_ATTACHMENT0)
        : (mode == GL_NONE || mode == GL_FRONT || mode == GL_FRONT_LEFT
           || (double_buffer && (mode == GL_BACK || mode == GL_BACK_LEFT)));
    if (!legal) { invalidate(RS_BINDINGS_READ_BUFFER); return; }
    state.read_buffer(mode);
}
void APIENTRY BindTexture(GLenum target, GLuint name) {
    ::glBindTexture(target, name);
    if (nominal) state.bind_texture(target, name);
}
void APIENTRY DeleteTextures(GLsizei count, const GLuint *names) {
    ::glDeleteTextures(count, names);
    if (!nominal) return;
    if (count < 0 || unsigned(count) > renderer::State::deletion_limit || (count && !names)) {
        invalidate(RS_BINDINGS_TEXTURE_DELETE); return;
    }
    state.delete_textures(count, names);
}
bool Raster(RasterState *out) {
    if (!out) return false;
    *out = {};
    if (!nominal || !dispatch_generation || dispatch_generation != identity.context_generation
            || GetCurrentThreadId() != identity.owner_thread) return false;
    out->draw_framebuffer = draw_framebuffer; out->draw_known = draw_known;
    for (unsigned i = 0; i < 4; ++i) out->viewport[i] = viewport[i];
    out->viewport_known = viewport_known;
    out->depth_range[0] = depth_range[0]; out->depth_range[1] = depth_range[1]; out->depth_known = depth_known;
    return true;
}
bool Render(RenderState *out) {
    if (!out) return false;
    *out = {};
    if (!render_known || !nominal || !dispatch_generation || dispatch_generation != identity.context_generation
            || GetCurrentThreadId() != identity.owner_thread) return false;
    *out = render; return true;
}
bool Enabled(GLenum capability, bool *on) {
    RenderState ignored{};
    if (!on || !Render(&ignored)) return false;
    for (unsigned i = 0; i < sizeof(capabilities) / sizeof(capabilities[0]); ++i)
        if (capabilities[i] == capability) { *on = (enabled >> i) & 1u; return true; }
    return false;
}
bool Texture2D(unsigned unit, GLuint *name) {
    RasterState ignored{};
    return name && Raster(&ignored) && state.texture(unit, name);
}
bool ActiveUnit(unsigned *unit) {
    RasterState ignored{};
    return unit && Raster(&ignored) && state.active_unit(unit);
}
void APIENTRY DepthFunc(GLenum function) {
    ::glDepthFunc(function);
    if (nominal) render.depth_func = function;
}
void APIENTRY DepthMask(GLboolean flag) {
    ::glDepthMask(flag);
    if (nominal) render.depth_mask = flag != GL_FALSE;
}
void APIENTRY BlendFunc(GLenum source, GLenum destination) {
    ::glBlendFunc(source, destination);
    if (!nominal) return;
    render.blend[0] = render.blend[2] = source; render.blend[1] = render.blend[3] = destination;
}
void APIENTRY Scissor(GLint x, GLint y, GLsizei width, GLsizei height) {
    ::glScissor(x, y, width, height);
    if (!nominal) return;
    render.scissor[0] = x; render.scissor[1] = y; render.scissor[2] = width; render.scissor[3] = height;
    render.scissor_known = width >= 0 && height >= 0;
}
void APIENTRY Enable(GLenum capability) {
    ::glEnable(capability);
    if (nominal) set_capability(capability, true);
}
void APIENTRY Disable(GLenum capability) {
    ::glDisable(capability);
    if (nominal) set_capability(capability, false);
}
bool CandidateBindings(renderer::Identity *out, snapshot::RestoreBindings *bindings) {
    if (!out || !nominal || !dispatch_generation || dispatch_generation != identity.context_generation
            || GetCurrentThreadId() != identity.owner_thread) return false;
    if (!state.default_bindings(identity, bindings)) return false;
    *out = identity; return true;
}
void BindingsCause(rs_bindings_cause *out) {
    if (!out) return;
    // A context ContextCreated never saw is still named, once per process.
    if (!identity_tried && HEADER && practice_rom(HEADER) && wglGetCurrentContext()) {
        identity_tried = true; publish_identity(RS_IDENTITY_FIRST_REFUSAL);
    }
    const auto known = state.known(identity);
    uint32_t flags = RS_BINDINGS_REPORTED;
    if (created) flags |= RS_BINDINGS_CONTEXT_CREATED;
    if (practice) flags |= RS_BINDINGS_PRACTICE_ROM;
    if (armed) flags |= RS_BINDINGS_ARMED;
    if (install_called) flags |= RS_BINDINGS_INSTALL_CALLED;
    if (dispatch_generation && dispatch_generation == identity.context_generation) flags |= RS_BINDINGS_DISPATCH_INSTALLED;
    if (nominal) flags |= RS_BINDINGS_NOMINAL;
    if (identity.owner_thread && GetCurrentThreadId() == identity.owner_thread) flags |= RS_BINDINGS_OWNER_THREAD;
    if (lost) flags |= RS_BINDINGS_CONTEXT_LOST;
    if (known.identity) flags |= RS_BINDINGS_STATE_IDENTITY;
    if (known.read) flags |= RS_BINDINGS_READ_KNOWN;
    if (known.default_read) flags |= RS_BINDINGS_DEFAULT_READ_KNOWN;
    if (known.unit) flags |= RS_BINDINGS_UNIT_KNOWN;
    if (known.texture) flags |= RS_BINDINGS_TEXTURE_KNOWN;
    if (structural) flags |= RS_BINDINGS_STRUCTURAL;
    *out = {cause_site, cause_error, flags, cause_invalidations, cause_after_ms, contexts_created};
}
namespace {
bool fatal(GLenum error) { return error == GL_OUT_OF_MEMORY || error == 0x0507 /* GL_CONTEXT_LOST */; }
// One flag per error kind at most, so a bounded drain empties it. The first
// becomes the renderer's pending answer; a lost context or exhausted memory
// leaves state undefined, so the witness is never rebuilt in this context.
bool drain_for_renderer() {
    for (unsigned i = 0; i < 8; ++i) {
        const GLenum error = ::glGetError();
        if (error == GL_NO_ERROR) return true;
        keep_for_renderer(error);
        if (fatal(error)) { structural = true; return false; }
    }
    return false; // a flag that will not clear: try again next second
}
// The reconstruction's own query errors are ours: drained, never the renderer's.
void drain_ours(GLenum error) {
    for (unsigned i = 0; error != GL_NO_ERROR && i < 8; ++i) {
        if (fatal(error)) structural = true;
        error = ::glGetError();
    }
}
bool reconstructable() {
    return practice && created && armed && !lost && !structural && !capture_open
        && dispatch_generation && dispatch_generation == identity.context_generation
        && GetCurrentThreadId() == identity.owner_thread
        && reinterpret_cast<HGLRC>(identity.context) == wglGetCurrentContext()
        && reinterpret_cast<HDC>(identity.read_drawable) == wglGetCurrentDC()
        && (gl_major > 3 || (gl_major == 3 && gl_minor >= 3)) // every query below is GL 3.3 core
        && (cause_site == RS_BINDINGS_SITE_NONE || rs_bindings_site_recoverable(cause_site));
}
// The driver's answers for everything the witness holds. The default
// framebuffer's selector is asked only while it is the one bound for
// reading (GLideN64 binds it before every swap); otherwise it stays unknown
// and the next attempt asks again.
bool rebuild(uint32_t &asked) {
    if (!drain_for_renderer()) return false;
    GLint read = 0, draw = 0, active = 0, texture = 0, selector = GL_NONE, view[4]{}, box[4]{};
    GLint program = 0, array = 0, function = GL_LESS, factors[4]{GL_ONE, GL_ZERO, GL_ONE, GL_ZERO};
    GLfloat range[2]{0, 1};
    GLboolean mask = GL_TRUE;
    uint32_t on = 0;
    glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &read);
    glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &draw);
    glGetIntegerv(GL_ACTIVE_TEXTURE, &active);
    glGetIntegerv(GL_TEXTURE_BINDING_2D, &texture);
    if (!read) glGetIntegerv(GL_READ_BUFFER, &selector);
    glGetIntegerv(GL_VIEWPORT, view);
    glGetFloatv(GL_DEPTH_RANGE, range);
    if (render_hooks) {
        glGetIntegerv(GL_CURRENT_PROGRAM, &program);
        glGetIntegerv(GL_VERTEX_ARRAY_BINDING, &array);
        glGetIntegerv(GL_DEPTH_FUNC, &function);
        glGetBooleanv(GL_DEPTH_WRITEMASK, &mask);
        glGetIntegerv(GL_BLEND_SRC_RGB, &factors[0]);
        glGetIntegerv(GL_BLEND_DST_RGB, &factors[1]);
        glGetIntegerv(GL_BLEND_SRC_ALPHA, &factors[2]);
        glGetIntegerv(GL_BLEND_DST_ALPHA, &factors[3]);
        glGetIntegerv(GL_SCISSOR_BOX, box);
        for (unsigned i = 0; i < sizeof(capabilities) / sizeof(capabilities[0]); ++i)
            if (glIsEnabled(capabilities[i])) on |= 1u << i;
    }
    // State queries asked, for a caller that publishes its driver cost.
    asked = 6 + (read ? 0 : 1) + (render_hooks ? 9 + sizeof(capabilities) / sizeof(capabilities[0]) : 0);
    const GLenum queried = ::glGetError();
    if (queried != GL_NO_ERROR) { drain_ours(queried); return false; }
    const auto unit = GLenum(active) - GL_TEXTURE0;
    if (GLenum(active) < GL_TEXTURE0 || unit >= unit_limit || read < 0 || draw < 0 || texture < 0) return false;
    const auto mode = GLenum(selector);
    const bool default_known = !read && (mode == GL_NONE || mode == GL_FRONT || mode == GL_FRONT_LEFT
        || (double_buffer && (mode == GL_BACK || mode == GL_BACK_LEFT)));
    state.reconstructed(identity, GLuint(read), default_known, mode, unsigned(unit), GLuint(texture));
    read_framebuffer = GLuint(read);
    draw_framebuffer = GLuint(draw); draw_known = true;
    for (unsigned i = 0; i < 4; ++i) viewport[i] = view[i];
    viewport_known = view[2] >= 0 && view[3] >= 0;
    depth_range[0] = range[0]; depth_range[1] = range[1]; depth_known = true;
    if (render_hooks) {
        render.program = GLuint(program); render.vertex_array = GLuint(array);
        render.depth_func = GLenum(function); render.depth_mask = mask != GL_FALSE;
        for (unsigned i = 0; i < 4; ++i) { render.blend[i] = GLenum(factors[i]); render.scissor[i] = box[i]; }
        render.scissor_known = box[2] >= 0 && box[3] >= 0;
        enabled = on;
    }
    render_known = render_hooks;
    nominal = true;
    return true;
}
}
bool Reconstruct(uint32_t *queries) {
    if (queries) *queries = 0;
    if (!reconstructable()) return false;
    const uint64_t now = GetTickCount64();
    if (attempted && now - last_attempt_ms < 1000) return false;
    attempted = true; last_attempt_ms = now;
    if (reconstruct_attempts != saturated) ++reconstruct_attempts;
    uint32_t asked = 0;
    const bool rebuilt = rebuild(asked);
    if (queries) *queries = asked;
    if (rebuilt) {
        if (reconstruct_successes != saturated) ++reconstruct_successes;
        evidence.reconstructed_site = cause_site; evidence.reconstructed_gl_error = cause_error;
        evidence.reconstructed_ms = ms_since_created();
        cause_site = RS_BINDINGS_SITE_NONE; cause_error = 0; cause_after_ms = 0; // the next one is its own cause
    }
    evidence.reconstruct_attempts = reconstruct_attempts; evidence.reconstruct_successes = reconstruct_successes;
    publish_evidence();
    return rebuilt;
}
uint32_t SourceFormat() {
    if (!nominal || !dispatch_generation || dispatch_generation!=identity.context_generation
            || GetCurrentThreadId()!=identity.owner_thread) return RB_SOURCE_UNKNOWN;
    return source_format;
}
GLenum APIENTRY GetError() {
    // All source consumers run on the serialized renderer thread. Worker GL
    // operations must use raw dispatch, not this source-only alias.
    // A saved error is the renderer's pending answer, returned exactly once.
    // The site that saved it already invalidated, and a reconstruction may
    // have repaired that since, so returning it never invalidates again.
    if (saved_error != GL_NO_ERROR) {
        const GLenum error = saved_error;
        saved_error = GL_NO_ERROR;
        return error;
    }
    const GLenum error = ::glGetError();
    if (error != GL_NO_ERROR) invalidate(RS_BINDINGS_RENDERER_ERROR, error);
    return error;
}
bool __cdecl BeginCapture() {
    // A pending answer no longer refuses the gateway: the GL Release build
    // never asks for it, and a reconstruction drained the driver's flags.
    if (!nominal || source_format==RB_SOURCE_UNKNOWN || capture_open || !dispatch_generation
            || dispatch_generation != identity.context_generation
            || GetCurrentThreadId() != identity.owner_thread) return false;
    const GLenum error = ::glGetError();
    if (error != GL_NO_ERROR) { keep_for_renderer(error); invalidate(RS_BINDINGS_GATEWAY_OPEN_ERROR, error); return false; }
    capture_open = true; return true;
}
bool __cdecl EndCapture() {
    if (!capture_open || GetCurrentThreadId() != identity.owner_thread) return false;
    capture_open = false;
    const GLenum error = ::glGetError();
    if (error != GL_NO_ERROR) { keep_for_renderer(error); invalidate(RS_BINDINGS_GATEWAY_CLOSE_ERROR, error); return false; }
    return nominal;
}
bool __cdecl BeginLayer() { return BeginCapture(); }
bool __cdecl EndLayer() {
    if (!capture_open || GetCurrentThreadId() != identity.owner_thread) return false;
    capture_open = false;
    bool clean = true;
    GLenum last = GL_NO_ERROR;
    // One flag per error kind at most, so a bounded drain empties it; none is
    // left for the next capture to mistake for the renderer's.
    for (unsigned i = 0; i < 8; ++i) {
        const GLenum error = ::glGetError();
        if (error == GL_NO_ERROR) return clean && nominal;
        if (error == GL_OUT_OF_MEMORY || error == 0x0507 /* GL_CONTEXT_LOST */) {
            keep_for_renderer(error); invalidate(RS_BINDINGS_LAYER_FATAL_ERROR, error); return false;
        }
        clean = false; last = error;
    }
    invalidate(RS_BINDINGS_LAYER_UNDRAINED, last); return false;
}
}
// The wrapper copies the GL identity without GL (source_refusal.h). A torn or
// unpublished copy returns 0: unknown, never a guess.
#pragma comment(linker,"/EXPORT:SM64ReplayContextEvidenceV1=_SM64ReplayContextEvidenceV1")
extern "C" int __cdecl SM64ReplayContextEvidenceV1(rs_context_evidence *out, uint32_t bytes) {
    if (!out || bytes != sizeof *out) return 0;
    for (unsigned attempt = 0; attempt < 3; ++attempt) {
        const auto before = replay_gl::evidence_seq.load();
        if (!before || (before & 1)) continue;
        uint32_t words[sizeof *out / 4];
        for (unsigned i = 0; i < sizeof *out / 4; ++i) words[i] = replay_gl::evidence_words[i].load();
        if (before == replay_gl::evidence_seq.load()) { memcpy(out, words, sizeof *out); return 1; }
    }
    return 0;
}
