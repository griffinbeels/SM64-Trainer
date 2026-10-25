/* A fake GL 4.6 driver for link_dispatch.cpp: the real GL 1.1 exports and the
 * pointer-called entries GLideN64 loads, over one plain state struct. Legal
 * calls change state; illegal ones set an error flag and change nothing, as
 * the GL specification says. No context, window or GPU. */
#include "fake_gl.h"

namespace fake {
Gl gl;
namespace { ULONGLONG ticks = 100000; }
void reset() { gl = Gl{}; }
void raise(GLenum error) {
    for (unsigned i = 0; i < gl.flag_count; ++i) if (gl.flags[i] == error) return; // one flag per kind
    if (gl.flag_count < 8) gl.flags[gl.flag_count++] = error;
}
GLenum raw_error() {
    if (!gl.flag_count) return GL_NO_ERROR;
    const GLenum error = gl.flags[0];
    for (unsigned i = 1; i < gl.flag_count; ++i) gl.flags[i - 1] = gl.flags[i];
    --gl.flag_count; return error;
}
void advance(ULONGLONG ms) { ticks += ms; }
namespace {
constexpr GLenum capabilities[] = {GL_DEPTH_TEST, GL_CULL_FACE, GL_POLYGON_OFFSET_FILL, GL_DEPTH_CLAMP,
    GL_CLIP_DISTANCE0, GL_BLEND, GL_SCISSOR_TEST, GL_STENCIL_TEST, GL_RASTERIZER_DISCARD,
    GL_FRAMEBUFFER_SRGB, GL_DITHER};
int capability(GLenum name) {
    for (unsigned i = 0; i < sizeof(capabilities) / sizeof(capabilities[0]); ++i) if (capabilities[i] == name) return int(i);
    return -1;
}
void set(GLenum name, bool on) {
    const int bit = capability(name);
    if (bit < 0) { raise(GL_INVALID_ENUM); return; }
    gl.enabled = on ? gl.enabled | (1u << bit) : gl.enabled & ~(1u << bit);
}
bool query(GLenum name) {
    ++gl.queries;
    if (gl.failing_query && name == gl.failing_query) { raise(GL_INVALID_ENUM); return false; }
    return true;
}
void APIENTRY bind_framebuffer(GLenum target, GLuint name) {
    if (target == GL_FRAMEBUFFER) gl.read_fb = gl.draw_fb = name;
    else if (target == GL_READ_FRAMEBUFFER) gl.read_fb = name;
    else if (target == GL_DRAW_FRAMEBUFFER) gl.draw_fb = name;
    else raise(GL_INVALID_ENUM);
}
void APIENTRY delete_framebuffers(GLsizei count, const GLuint *names) {
    if (count < 0) { raise(GL_INVALID_VALUE); return; }
    for (GLsizei n = 0; n < count; ++n) {
        if (names[n] && names[n] == gl.read_fb) gl.read_fb = 0;
        if (names[n] && names[n] == gl.draw_fb) gl.draw_fb = 0;
    }
}
void APIENTRY active_texture(GLenum unit) {
    if (unit < GL_TEXTURE0 || unit - GL_TEXTURE0 >= 32) { raise(GL_INVALID_ENUM); return; }
    gl.active = unit;
}
void APIENTRY depth_range(GLfloat nearest, GLfloat farthest) { gl.depth[0] = nearest; gl.depth[1] = farthest; }
void APIENTRY use_program(GLuint program) { gl.program = program; }
void APIENTRY bind_vertex_array(GLuint name) { gl.vao = name; }
void APIENTRY delete_vertex_arrays(GLsizei count, const GLuint *names) {
    for (GLsizei n = 0; n < count; ++n) if (names[n] && names[n] == gl.vao) gl.vao = 0;
}
void APIENTRY enablei(GLenum name, GLuint index) { if (!index) set(name, true); }
void APIENTRY disablei(GLenum name, GLuint index) { if (!index) set(name, false); }
void APIENTRY attachment(GLenum, GLenum, GLenum pname, GLint *out) {
    ++gl.queries;
    *out = pname == GL_FRAMEBUFFER_ATTACHMENT_OBJECT_TYPE ? GL_FRAMEBUFFER_DEFAULT
        : pname == GL_FRAMEBUFFER_ATTACHMENT_COMPONENT_TYPE ? GL_UNSIGNED_NORMALIZED
        : pname == GL_FRAMEBUFFER_ATTACHMENT_COLOR_ENCODING ? GL_LINEAR : 8;
}
}
void load_table(bool with_active_texture) {
    g_glBindFramebuffer = bind_framebuffer; g_glDeleteFramebuffers = delete_framebuffers;
    g_glActiveTexture = with_active_texture ? active_texture : nullptr;
    g_glDepthRangef = depth_range; g_glUseProgram = use_program;
    g_glBindVertexArray = bind_vertex_array; g_glDeleteVertexArrays = delete_vertex_arrays;
    g_glEnablei = enablei; g_glDisablei = disablei;
}
ULONGLONG now() { return ticks; }
}

PFNGLBINDFRAMEBUFFERPROC g_glBindFramebuffer = nullptr;
PFNGLDELETEFRAMEBUFFERSPROC g_glDeleteFramebuffers = nullptr;
PFNGLACTIVETEXTUREPROC g_glActiveTexture = nullptr;
PFNGLDEPTHRANGEFPROC g_glDepthRangef = nullptr;
PFNGLUSEPROGRAMPROC g_glUseProgram = nullptr;
PFNGLBINDVERTEXARRAYPROC g_glBindVertexArray = nullptr;
PFNGLDELETEVERTEXARRAYSPROC g_glDeleteVertexArrays = nullptr;
PFNGLENABLEIPROC g_glEnablei = nullptr;
PFNGLDISABLEIPROC g_glDisablei = nullptr;
namespace fake { ULONGLONG now(); }
ULONGLONG fake_tick_count() { return fake::now(); }

using fake::gl;
extern "C" {
GLenum APIENTRY glGetError(void) { return fake::raw_error(); }
const GLubyte *APIENTRY glGetString(GLenum name) {
    const char *text = name == GL_VENDOR ? "Fake Vendor\x01" : name == GL_RENDERER ? "Fake Renderer"
        : name == GL_VERSION ? "4.6.0 Fake" : nullptr;
    return reinterpret_cast<const GLubyte *>(text);
}
void APIENTRY glGetIntegerv(GLenum name, GLint *out) {
    if (name == GL_MAX_COMBINED_TEXTURE_IMAGE_UNITS) { *out = gl.units; return; } // ContextCreated's lifecycle query
    if (name == GL_SAMPLE_BUFFERS) { *out = 0; return; }
    if (!fake::query(name)) return;
    switch (name) {
    case GL_READ_FRAMEBUFFER_BINDING: *out = GLint(gl.read_fb); break;
    case GL_DRAW_FRAMEBUFFER_BINDING: *out = GLint(gl.draw_fb); break;
    case GL_ACTIVE_TEXTURE: *out = GLint(gl.active); break;
    case GL_TEXTURE_BINDING_2D: *out = GLint(gl.textures[gl.active - GL_TEXTURE0]); break;
    case GL_READ_BUFFER: *out = GLint(gl.read_fb ? gl.fbo_read : gl.default_read); break;
    case GL_VIEWPORT: for (int i = 0; i < 4; ++i) out[i] = gl.viewport[i]; break;
    case GL_SCISSOR_BOX: for (int i = 0; i < 4; ++i) out[i] = gl.scissor[i]; break;
    case GL_CURRENT_PROGRAM: *out = GLint(gl.program); break;
    case GL_VERTEX_ARRAY_BINDING: *out = GLint(gl.vao); break;
    case GL_DEPTH_FUNC: *out = GLint(gl.depth_func); break;
    case GL_BLEND_SRC_RGB: *out = GLint(gl.blend[0]); break;
    case GL_BLEND_DST_RGB: *out = GLint(gl.blend[1]); break;
    case GL_BLEND_SRC_ALPHA: *out = GLint(gl.blend[2]); break;
    case GL_BLEND_DST_ALPHA: *out = GLint(gl.blend[3]); break;
    default: fake::raise(GL_INVALID_ENUM);
    }
}
void APIENTRY glGetFloatv(GLenum name, GLfloat *out) {
    if (!fake::query(name)) return;
    if (name == GL_DEPTH_RANGE) { out[0] = gl.depth[0]; out[1] = gl.depth[1]; }
    else fake::raise(GL_INVALID_ENUM);
}
void APIENTRY glGetBooleanv(GLenum name, GLboolean *out) {
    if (!fake::query(name)) return;
    if (name == GL_DEPTH_WRITEMASK) *out = gl.depth_mask;
    else fake::raise(GL_INVALID_ENUM);
}
GLboolean APIENTRY glIsEnabled(GLenum name) {
    if (!fake::query(name)) return GL_FALSE;
    const int bit = fake::capability(name);
    if (bit < 0) { fake::raise(GL_INVALID_ENUM); return GL_FALSE; }
    return (gl.enabled >> bit) & 1u ? GL_TRUE : GL_FALSE;
}
void APIENTRY glReadBuffer(GLenum mode) {
    if (!gl.read_fb && (mode == GL_NONE || mode == GL_FRONT || mode == GL_FRONT_LEFT || mode == GL_BACK
            || mode == GL_BACK_LEFT)) gl.default_read = mode;
    else if (gl.read_fb && (mode == GL_NONE || mode == GL_COLOR_ATTACHMENT0)) gl.fbo_read = mode;
    else fake::raise(GL_INVALID_OPERATION);
}
void APIENTRY glBindTexture(GLenum target, GLuint name) {
    if (target == GL_TEXTURE_2D) gl.textures[gl.active - GL_TEXTURE0] = name;
}
void APIENTRY glDeleteTextures(GLsizei count, const GLuint *names) {
    for (GLsizei n = 0; n < count; ++n)
        for (auto &bound : gl.textures) if (names[n] && bound == names[n]) bound = 0;
}
void APIENTRY glViewport(GLint x, GLint y, GLsizei width, GLsizei height) {
    gl.viewport[0] = x; gl.viewport[1] = y; gl.viewport[2] = width; gl.viewport[3] = height;
}
void APIENTRY glScissor(GLint x, GLint y, GLsizei width, GLsizei height) {
    if (width < 0 || height < 0) { fake::raise(GL_INVALID_VALUE); return; }
    gl.scissor[0] = x; gl.scissor[1] = y; gl.scissor[2] = width; gl.scissor[3] = height;
}
void APIENTRY glDepthFunc(GLenum function) { gl.depth_func = function; }
void APIENTRY glDepthMask(GLboolean flag) { gl.depth_mask = flag; }
void APIENTRY glBlendFunc(GLenum source, GLenum destination) {
    gl.blend[0] = gl.blend[2] = source; gl.blend[1] = gl.blend[3] = destination;
}
void APIENTRY glEnable(GLenum name) { fake::set(name, true); }
void APIENTRY glDisable(GLenum name) { fake::set(name, false); }
HGLRC WINAPI wglGetCurrentContext(void) { return gl.current_context; }
HDC WINAPI wglGetCurrentDC(void) { return gl.current_dc; }
PROC WINAPI wglGetProcAddress(LPCSTR name) {
    return lstrcmpA(name, "glGetFramebufferAttachmentParameteriv") ? nullptr
        : reinterpret_cast<PROC>(fake::attachment);
}
}
