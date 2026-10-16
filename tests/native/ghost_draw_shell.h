/* The few renderer (GLideN64) names ghost_draw.cpp reads, as plain shells, so
 * the glue compiles whole without the renderer. tests/test_ghost_draw.py
 * points Config.h, FrameBuffer.h, gSP.h, gDP.h, GBI.h, GLFunctions.h and
 * Graphics/Context.h here. Driver queries are counted, never answered. */
#pragma once
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <GL/gl.h>
#include <stdint.h>
typedef char GLchar;
#define GL_TEXTURE0 0x84C0
#define GL_ACTIVE_TEXTURE 0x84E0
#define GL_CLIP_DISTANCE0 0x3000
#define GL_DEPTH_CLAMP 0x864F
#define GL_CURRENT_PROGRAM 0x8B8D
extern unsigned shell_queries;
void APIENTRY shell_get_integer(GLenum, GLint *);
GLint APIENTRY shell_uniform_location(GLuint, const GLchar *);
#define glGetIntegerv shell_get_integer
#define glGetUniformLocation shell_uniform_location
struct Config {
    struct { bool enable = false, N64DepthCompare = false; } frameBufferEmulation;
    struct { int multisampling = 0; } video;
};
extern Config config;
struct CachedTexture { uint32_t name = 0, realWidth = 0, realHeight = 0; };
struct FrameBuffer { uint32_t m_FBO = 0; CachedTexture *m_pTexture = nullptr; bool m_isDepthBuffer = false; };
struct GSPInfo { uint32_t geometryMode = 0; };
extern GSPInfo gSP;
#define G_ZBUFFER 0x00000001u
namespace graphics { struct Context { static bool FramebufferFetch; }; }
