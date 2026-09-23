/* The overlay's source-owned LINK GL state observation and bounded error gateway.
 * Include after upstream GLFunctions.h declarations. Renderer thread only. */
#pragma once
#include "renderer_gl_state.h"
#include "source_format.h"
#ifdef SM64_REPLAY_CONTEXT_LIFETIME
#include "context_lifetime.h"
#endif
namespace replay_gl {
void InstallDispatch(); // only after upstream reloads its raw GL function table
// Only immediately after making a freshly CREATED context current, before any
// renderer mutations. Never call for hot activation or same-context resize.
void ContextCreated(HGLRC, HDC, bool double_buffered);
void ContextLost();
void DrawableChanged(); // same context and drawable, before a resize/fullscreen change
bool DrawableCommitted(HWND, bool resized); // observes extent; returns original resize result
bool DrawableExtent(unsigned *width, unsigned *height); // source-owned values; no query
// The renderer's own draw framebuffer, viewport and depth range, as its
// dispatched calls last set them: no driver query. Each is known only while
// nominal, on the owner thread, and once observed (the viewport after the
// renderer's first glViewport; a fresh context's draw FBO 0 and range 0..1).
struct RasterState { uint32_t draw_framebuffer; int32_t viewport[4]; float depth_range[2];
                     bool draw_known, viewport_known, depth_known; };
bool Raster(RasterState *);
// Nominal state only: copy validity/device-loss/source qualification are separate
// prerequisites. Never promote this alone to a verified replay picture.
bool CandidateBindings(renderer::Identity *, snapshot::RestoreBindings *);
#ifdef SM64_REPLAY_CONTEXT_LIFETIME
// Renderer thread, current context, under capture demand only: publish the
// never-current shared anchor the worker leases, once per context. A direct
// (no wrapper) or idle (no request) session never creates it, so the driver
// sees the original single-context renderer. One attempt per context.
bool EnsureAnchor();
#endif
uint32_t SourceFormat(); // observed once on fresh default framebuffer; no driver query
// Called only for an admitted snapshot, before mutation and after restoration/
// fence/flush, BEFORE image publication. One raw error read each; no drain.
bool __cdecl BeginCapture();
bool __cdecl EndCapture();
// The translucent world layer's scope: opens like BeginCapture. EndLayer
// drains the layer's own errors and returns false for them WITHOUT leaving
// the nominal state: a failing GL command changes no state, and the layer
// restores what it replaced by query. Out of memory, a lost context or an
// undrainable flag still invalidate, exactly like EndCapture.
bool __cdecl BeginLayer();
bool __cdecl EndLayer();
// Preserve one consumed diagnostic for the renderer's next original error read.
// This preserves visibility, not an identical driver error-flag history.
GLenum APIENTRY GetError();
void APIENTRY ReadBuffer(GLenum);
void APIENTRY Viewport(GLint, GLint, GLsizei, GLsizei); // a Windows GL 1.1 export: aliased at the call
void APIENTRY BindTexture(GLenum, GLuint);
void APIENTRY DeleteTextures(GLsizei, const GLuint *);
}
#ifndef REPLAY_GL_DISPATCH_IMPLEMENTATION
#define glReadBuffer replay_gl::ReadBuffer
#define glViewport replay_gl::Viewport
#define glBindTexture replay_gl::BindTexture
#define glDeleteTextures replay_gl::DeleteTextures
#define glGetError replay_gl::GetError
#endif
