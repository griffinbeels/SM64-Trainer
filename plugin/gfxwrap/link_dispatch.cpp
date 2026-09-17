/* Compiled ONLY with the pinned source overlay, never the binary wrapper.
 * Observes real dispatch calls, including cached function pointers. No per-call
 * GL queries, allocations, locks, events, I/O or capture work. Raw void GL calls
 * do not prove success: this is a nominal-state candidate, not source admission.
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
#include <limits>
#include <cstdio>

namespace replay_gl {
namespace {
renderer::State state;
renderer::Identity identity{};
PFNGLBINDFRAMEBUFFERPROC raw_bind_framebuffer = nullptr;
PFNGLDELETEFRAMEBUFFERSPROC raw_delete_framebuffers = nullptr;
PFNGLACTIVETEXTUREPROC raw_active_texture = nullptr;
uint32_t generation = 0, dispatch_generation = 0;
unsigned unit_limit = 0;
unsigned drawable_width = 0, drawable_height = 0;
GLuint read_framebuffer = 0;
bool nominal = false, double_buffer = false;
bool practice = false; // the context belongs to a practice ROM
bool capture_open = false;
bool anchor_attempted = false, anchor_published = false; // renderer thread only
GLenum saved_error = GL_NO_ERROR;
uint32_t source_format = RB_SOURCE_UNKNOWN;
void observe_source_format() {
    // ContextCreated is called only for a fresh context, before renderer state
    // mutation: default READ/DRAW framebuffers are both zero. No temporary binds.
    const auto version=reinterpret_cast<const char*>(glGetString(GL_VERSION));
    int major=0;
    if (!version || std::sscanf(version,"%d",&major)!=1 || major<3) return;
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
void invalidate() { nominal = false; state.unknown(); }
void APIENTRY BindFramebuffer(GLenum target, GLuint name) {
    raw_bind_framebuffer(target, name);
    if (!nominal) return;
    if (target != GL_FRAMEBUFFER && target != GL_READ_FRAMEBUFFER && target != GL_DRAW_FRAMEBUFFER) {
        invalidate(); return;
    }
    state.bind_framebuffer(target, name);
    if (target != GL_DRAW_FRAMEBUFFER) read_framebuffer = name;
}
void APIENTRY DeleteFramebuffers(GLsizei count, const GLuint *names) {
    raw_delete_framebuffers(count, names);
    if (!nominal) return;
    if (count < 0 || unsigned(count) > renderer::State::deletion_limit || (count && !names)) {
        invalidate(); return;
    }
    state.delete_framebuffers(count, names);
    for (GLsizei n = 0; n < count; ++n) if (names[n] == read_framebuffer) read_framebuffer = 0;
}
void APIENTRY ActiveTexture(GLenum unit) {
    raw_active_texture(unit);
    if (!nominal) return;
    if (unit < GL_TEXTURE0 || unit - GL_TEXTURE0 >= unit_limit) { invalidate(); return; }
    state.active_texture(unit);
}
}
void ContextLost() {
#ifdef SM64_REPLAY_CONTEXT_LIFETIME
    // Revoke AND retire here: a context abandoned without _stop (resize
    // restart) must not leave its anchor alive in the driver. _stop's own
    // later Retire finds the freed slot and reports stale, which is fine.
    if (identity.context) {
        const auto retirement = source_context::Revoke(reinterpret_cast<HGLRC>(identity.context));
        if (retirement.value) source_context::Retire(retirement);
    }
    anchor_attempted = anchor_published = false;
#endif
    nominal = false; practice = false; identity = {}; dispatch_generation = 0; state.lost();
    drawable_width = drawable_height = 0;
    capture_open = false; saved_error = GL_NO_ERROR;
    source_format = RB_SOURCE_UNKNOWN;
}
void DrawableChanged() {
    drawable_width = drawable_height = 0;
    if (!nominal) return;
    if (GetCurrentThreadId() != identity.owner_thread
            || identity.drawable_generation == (std::numeric_limits<uint32_t>::max)()) {
        invalidate(); return;
    }
    state.drawable_changed(++identity.drawable_generation);
}
bool DrawableCommitted(HWND window, bool resized) {
    drawable_width = drawable_height = 0;
    if (!resized || !nominal || GetCurrentThreadId() != identity.owner_thread) return resized;
    RECT bounds{};
    if (!window || !GetClientRect(window, &bounds) || bounds.right <= bounds.left || bounds.bottom <= bounds.top) {
        invalidate(); return resized;
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
    practice = HEADER && practice_rom(HEADER);
    if (!practice) return; // no queries, no state, never a capture source
    if (!context || !drawable || context != wglGetCurrentContext() || drawable != wglGetCurrentDC()
            || generation == (std::numeric_limits<uint32_t>::max)()) return;
    GLint units = 0;
    glGetIntegerv(GL_MAX_COMBINED_TEXTURE_IMAGE_UNITS, &units); // lifecycle only
    if (units <= 0) return;
    unit_limit = unsigned(units) < renderer::State::texture_units ? unsigned(units) : renderer::State::texture_units;
    ++generation;
    identity = {reinterpret_cast<uintptr_t>(context), reinterpret_cast<uintptr_t>(drawable),
                generation, generation, GetCurrentThreadId()};
    double_buffer = buffered; read_framebuffer = 0;
    state.created(identity, buffered); nominal = true;
    observe_source_format();
    // No anchor here. R31 published the shared context at every ROM open,
    // consumer or not, so a server-off session ran in a two-context share
    // group the original renderer never has. EnsureAnchor publishes it on
    // the first admitted capture surface instead.
}
#ifdef SM64_REPLAY_CONTEXT_LIFETIME
bool EnsureAnchor() {
    if (!nominal || source_format == RB_SOURCE_UNKNOWN || !dispatch_generation
            || dispatch_generation != identity.context_generation
            || GetCurrentThreadId() != identity.owner_thread) return false;
    if (anchor_attempted) return anchor_published;
    anchor_attempted = true;
    const auto drawable = reinterpret_cast<HDC>(identity.read_drawable);
    anchor_published = source_context::Publish(reinterpret_cast<HGLRC>(identity.context), drawable,
                                              identity.context_generation, GetPixelFormat(drawable));
    return anchor_published;
}
#endif
void InstallDispatch() {
    if (!practice) return; // baseline ROM: GLideN64's own raw dispatch, untouched
    // Upstream must have resolved fresh raw addresses. Reject accidental repeated
    // installation rather than capturing ourselves and recursively dispatching.
    if (g_glBindFramebuffer == &BindFramebuffer || g_glDeleteFramebuffers == &DeleteFramebuffers
            || g_glActiveTexture == &ActiveTexture) { invalidate(); return; }
    raw_bind_framebuffer = g_glBindFramebuffer;
    raw_delete_framebuffers = g_glDeleteFramebuffers;
    raw_active_texture = g_glActiveTexture;
    if (raw_bind_framebuffer) g_glBindFramebuffer = &BindFramebuffer;
    if (raw_delete_framebuffers) g_glDeleteFramebuffers = &DeleteFramebuffers;
    if (raw_active_texture) g_glActiveTexture = &ActiveTexture;
    if (!raw_bind_framebuffer || !raw_delete_framebuffers || !raw_active_texture
            || !nominal || reinterpret_cast<HGLRC>(identity.context) != wglGetCurrentContext()
            || reinterpret_cast<HDC>(identity.read_drawable) != wglGetCurrentDC()) {
        invalidate(); return;
    }
    dispatch_generation = identity.context_generation;
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
    if (!legal) { invalidate(); return; }
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
        invalidate(); return;
    }
    state.delete_textures(count, names);
}
bool CandidateBindings(renderer::Identity *out, snapshot::RestoreBindings *bindings) {
    if (!out || !nominal || !dispatch_generation || dispatch_generation != identity.context_generation
            || GetCurrentThreadId() != identity.owner_thread) return false;
    if (!state.default_bindings(identity, bindings)) return false;
    *out = identity; return true;
}
uint32_t SourceFormat() {
    if (!nominal || !dispatch_generation || dispatch_generation!=identity.context_generation
            || GetCurrentThreadId()!=identity.owner_thread) return RB_SOURCE_UNKNOWN;
    return source_format;
}
GLenum APIENTRY GetError() {
    // All source consumers run on the serialized renderer thread. Worker GL
    // operations must use raw dispatch, not this source-only alias.
    const GLenum error = saved_error ? saved_error : ::glGetError();
    saved_error = GL_NO_ERROR;
    if (error != GL_NO_ERROR) invalidate();
    return error;
}
bool __cdecl BeginCapture() {
    if (!nominal || source_format==RB_SOURCE_UNKNOWN || capture_open || saved_error || !dispatch_generation
            || dispatch_generation != identity.context_generation
            || GetCurrentThreadId() != identity.owner_thread) return false;
    const GLenum error = ::glGetError();
    if (error != GL_NO_ERROR) { saved_error = error; invalidate(); return false; }
    capture_open = true; return true;
}
bool __cdecl EndCapture() {
    if (!capture_open || GetCurrentThreadId() != identity.owner_thread) return false;
    capture_open = false;
    const GLenum error = ::glGetError();
    if (error != GL_NO_ERROR) { saved_error = error; invalidate(); return false; }
    return nominal;
}
}
