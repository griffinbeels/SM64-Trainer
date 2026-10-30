/* The fake driver's TRUE state, which a scenario can move behind the
 * witness's back (as an unobserved renderer call would) and which every
 * driver query answers from. Not a model of link_dispatch: the real one runs. */
#pragma once
#include "GLFunctions.h"

namespace fake {
struct Gl {
    GLuint read_fb = 0, draw_fb = 0, program = 0, vao = 0;
    GLenum active = GL_TEXTURE0, default_read = GL_BACK, fbo_read = GL_COLOR_ATTACHMENT0;
    GLuint textures[32]{};
    GLint viewport[4]{0, 0, 640, 480}, scissor[4]{0, 0, 640, 480};
    GLfloat depth[2]{0, 1};
    GLenum depth_func = GL_LESS, blend[4]{GL_ONE, GL_ZERO, GL_ONE, GL_ZERO};
    GLboolean depth_mask = GL_TRUE;
    uint32_t enabled = 1u << 10; // link_dispatch's capability order: only DITHER on
    GLint units = 32;            // GL_MAX_COMBINED_TEXTURE_IMAGE_UNITS
    GLenum flags[8]{};           // pending error flags, at most one per kind, oldest first
    unsigned flag_count = 0;
    unsigned queries = 0;        // glGet* and glIsEnabled calls
    GLenum failing_query = 0;    // a state query that raises GL_INVALID_ENUM and answers nothing
    HGLRC current_context = reinterpret_cast<HGLRC>(0x1000);
    HDC current_dc = reinterpret_cast<HDC>(0x2000);
};
extern Gl gl;
static const HGLRC context = reinterpret_cast<HGLRC>(0x1000);
static const HDC dc = reinterpret_cast<HDC>(0x2000);
void reset();                 // a fresh driver; the clock keeps running
void raise(GLenum error);     // the driver sets an error flag
GLenum raw_error();           // the driver's own glGetError, past the witness
void advance(ULONGLONG ms);   // the clock link_dispatch reads
// GLideN64's initGLFunctions: fresh raw entry points in its table.
void load_table(bool with_active_texture = true);
}
