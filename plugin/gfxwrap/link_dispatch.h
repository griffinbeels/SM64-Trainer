/* The overlay's source-owned LINK GL state observation and bounded error gateway.
 * Include after upstream GLFunctions.h declarations. Renderer thread only. */
#pragma once
#include "renderer_gl_state.h"
#include "source_format.h"
#include "source_refusal.h"
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
// The rest of the state a ghost frame restores, as the renderer's dispatched
// calls last set it, from a fresh context's defaults on: program, vertex
// array, depth function and write mask, blend factors (glBlendFunc sets every
// draw buffer; these are buffer 0's: source/destination RGB, then alpha) and
// the scissor box (known after the renderer's first glScissor). No driver
// query; false unless nominal on the owner thread with the dispatch installed.
struct RenderState { uint32_t program, vertex_array, depth_func, blend[4]; int32_t scissor[4];
                     bool depth_mask, scissor_known; };
bool Render(RenderState *);
// Whether a tracked capability is on (BLEND and SCISSOR_TEST at index 0):
// DEPTH_TEST, CULL_FACE, POLYGON_OFFSET_FILL, DEPTH_CLAMP, CLIP_DISTANCE0,
// BLEND, SCISSOR_TEST, STENCIL_TEST, RASTERIZER_DISCARD, FRAMEBUFFER_SRGB,
// DITHER. False for any other capability or when not known.
bool Enabled(GLenum capability, bool *enabled);
// A unit's 2D texture and the active unit (0-based), as the dispatch saw them.
bool Texture2D(unsigned unit, GLuint *name);
bool ActiveUnit(unsigned *unit);
// Nominal state only: copy validity/device-loss/source qualification are separate
// prerequisites. Never promote this alone to a verified replay picture.
bool CandidateBindings(renderer::Identity *, snapshot::RestoreBindings *);
// Why CandidateBindings is false (source_refusal.h), for a BINDINGS refusal.
// Copies already-known values. Once per process, a context ContextCreated never
// saw has its GL identity read here instead: three glGetString calls, no other GL.
void BindingsCause(rs_bindings_cause *);
// Rebuilds the witness from the driver's answers after a RECOVERABLE
// invalidation (source_refusal.h), at most once a second per context; true
// when the observation is nominal again. Called where a refused witness would
// cost the recording (surface()) or the ghost's poses (ghost_link's list
// start): this file's one driver-query path after ContextCreated. `queries`
// receives the state queries it asked (0 when it did not try).
bool Reconstruct(uint32_t *queries = nullptr);
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
// Windows GL 1.1 exports the ghost's frame restores, aliased at the call.
void APIENTRY DepthFunc(GLenum);
void APIENTRY DepthMask(GLboolean);
void APIENTRY BlendFunc(GLenum, GLenum);
void APIENTRY Scissor(GLint, GLint, GLsizei, GLsizei);
void APIENTRY Enable(GLenum);
void APIENTRY Disable(GLenum);
}
#ifndef REPLAY_GL_DISPATCH_IMPLEMENTATION
#define glReadBuffer replay_gl::ReadBuffer
#define glViewport replay_gl::Viewport
#define glBindTexture replay_gl::BindTexture
#define glDeleteTextures replay_gl::DeleteTextures
#define glGetError replay_gl::GetError
#define glDepthFunc replay_gl::DepthFunc
#define glDepthMask replay_gl::DepthMask
#define glBlendFunc replay_gl::BlendFunc
#define glScissor replay_gl::Scissor
#define glEnable replay_gl::Enable
#define glDisable replay_gl::Disable
#endif
