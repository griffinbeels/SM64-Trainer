/* CPU stand-in for the pinned GLideN64 GLFunctions.h, so the REAL
 * link_dispatch.cpp compiles against a fake driver (fake_gl.cpp) with no
 * context, window or GPU. GDI and GL are declared without dllimport
 * (_GDI32_), so the host defines the GL it uses and never loads opengl32.
 * Test hosts only (tests/test_link_dispatch_cpu.py). */
#pragma once
#define _GDI32_
#include <windows.h>
#include <GL/gl.h>
#include <stdint.h>

#define GL_FRAMEBUFFER 0x8D40
#define GL_READ_FRAMEBUFFER 0x8CA8
#define GL_DRAW_FRAMEBUFFER 0x8CA9
#define GL_READ_FRAMEBUFFER_BINDING 0x8CAA
#define GL_DRAW_FRAMEBUFFER_BINDING 0x8CA6
#define GL_TEXTURE0 0x84C0
#define GL_ACTIVE_TEXTURE 0x84E0
#define GL_COLOR_ATTACHMENT0 0x8CE0
#define GL_DEPTH_CLAMP 0x864F
#define GL_CLIP_DISTANCE0 0x3000
#define GL_RASTERIZER_DISCARD 0x8C89
#define GL_FRAMEBUFFER_SRGB 0x8DB9
#define GL_MAX_COMBINED_TEXTURE_IMAGE_UNITS 0x8B4D
#define GL_FRAMEBUFFER_ATTACHMENT_OBJECT_TYPE 0x8CD0
#define GL_FRAMEBUFFER_DEFAULT 0x8218
#define GL_FRAMEBUFFER_ATTACHMENT_COLOR_ENCODING 0x8210
#define GL_FRAMEBUFFER_ATTACHMENT_COMPONENT_TYPE 0x8211
#define GL_FRAMEBUFFER_ATTACHMENT_RED_SIZE 0x8212
#define GL_FRAMEBUFFER_ATTACHMENT_GREEN_SIZE 0x8213
#define GL_FRAMEBUFFER_ATTACHMENT_BLUE_SIZE 0x8214
#define GL_SAMPLE_BUFFERS 0x80A8
#define GL_UNSIGNED_NORMALIZED 0x8C17
#define GL_SRGB 0x8C40
#define GL_CURRENT_PROGRAM 0x8B8D
#define GL_VERTEX_ARRAY_BINDING 0x85B5
#define GL_BLEND_SRC_RGB 0x80C9
#define GL_BLEND_DST_RGB 0x80C8
#define GL_BLEND_SRC_ALPHA 0x80CB
#define GL_BLEND_DST_ALPHA 0x80CA

typedef void (APIENTRY *PFNGLBINDFRAMEBUFFERPROC)(GLenum, GLuint);
typedef void (APIENTRY *PFNGLDELETEFRAMEBUFFERSPROC)(GLsizei, const GLuint *);
typedef void (APIENTRY *PFNGLACTIVETEXTUREPROC)(GLenum);
typedef void (APIENTRY *PFNGLDEPTHRANGEFPROC)(GLfloat, GLfloat);
typedef void (APIENTRY *PFNGLUSEPROGRAMPROC)(GLuint);
typedef void (APIENTRY *PFNGLBINDVERTEXARRAYPROC)(GLuint);
typedef void (APIENTRY *PFNGLDELETEVERTEXARRAYSPROC)(GLsizei, const GLuint *);
typedef void (APIENTRY *PFNGLENABLEIPROC)(GLenum, GLuint);
typedef void (APIENTRY *PFNGLDISABLEIPROC)(GLenum, GLuint);
typedef void (APIENTRY *PFNGLGETFRAMEBUFFERATTACHMENTPARAMETERIVPROC)(GLenum, GLenum, GLenum, GLint *);

// GLideN64's own function table, which InstallDispatch replaces.
extern PFNGLBINDFRAMEBUFFERPROC g_glBindFramebuffer;
extern PFNGLDELETEFRAMEBUFFERSPROC g_glDeleteFramebuffers;
extern PFNGLACTIVETEXTUREPROC g_glActiveTexture;
extern PFNGLDEPTHRANGEFPROC g_glDepthRangef;
extern PFNGLUSEPROGRAMPROC g_glUseProgram;
extern PFNGLBINDVERTEXARRAYPROC g_glBindVertexArray;
extern PFNGLDELETEVERTEXARRAYSPROC g_glDeleteVertexArrays;
extern PFNGLENABLEIPROC g_glEnablei;
extern PFNGLDISABLEIPROC g_glDisablei;

// The host's clock: Reconstruct's once-a-second rule runs on fake time.
ULONGLONG fake_tick_count();
#define GetTickCount64 fake_tick_count
