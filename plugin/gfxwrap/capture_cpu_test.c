/* CPU-only host: no Windows/GL entry points, DLL loading or shared mapping.
 * readback_under_test.h is the actual production read_front_buffer function,
 * extracted by test_gfxwrap_cpu.py and compiled against this state model. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define assert(condition) do { if (!(condition)) { \
    fprintf(stderr, "state contract failed at line %d\n", __LINE__); exit(1); \
} } while (0)
#include "capture_lease.h"
typedef unsigned GLenum;
typedef unsigned GLuint;
typedef int GLint;
typedef int GLsizei;
enum {GL_BACK=1, GL_FRONT, GL_COLOR_ATTACHMENT0, GL_READ_BUFFER,
      GL_PACK_ALIGNMENT, GL_PACK_ROW_LENGTH, GL_PACK_SKIP_ROWS,
      GL_PACK_SKIP_PIXELS, GL_READ_FRAMEBUFFER, GL_READ_FRAMEBUFFER_BINDING,
      GL_PIXEL_PACK_BUFFER, GL_PIXEL_PACK_BUFFER_BINDING, GL_BGR_EXT,
      GL_UNSIGNED_BYTE, GL_NO_ERROR=0};
static int bound_fbo, selectors[8], pack_buffer, pack[4], errors;
static void bind_fbo(GLenum target, GLuint value) { (void)target; bound_fbo=(int)value; }
static void bind_buffer(GLenum target, GLuint value) { (void)target; pack_buffer=(int)value; }
static void (*g_bind_framebuffer)(GLenum, GLuint)=bind_fbo;
static void (*g_bind_buffer)(GLenum, GLuint)=bind_buffer;
static void look_up_gl_entry_points(void) {}
enum {PR_GL_SETUP, PR_GL_READ, PR_GL_RESTORE};
static int64_t profile_mark(void) { return 0; }
static void profile_end_stage(unsigned stage, int64_t began) { (void)stage; (void)began; }
static void glGetIntegerv(GLenum name, GLint *value) {
    if (name==GL_READ_BUFFER) *value=selectors[bound_fbo];
    else if (name==GL_READ_FRAMEBUFFER_BINDING) *value=bound_fbo;
    else if (name==GL_PIXEL_PACK_BUFFER_BINDING) *value=pack_buffer;
    else { assert(name>=GL_PACK_ALIGNMENT && name<=GL_PACK_SKIP_PIXELS);
           *value=pack[name-GL_PACK_ALIGNMENT]; }
}
static void glPixelStorei(GLenum name, GLint value) { pack[name-GL_PACK_ALIGNMENT]=value; }
static void glReadBuffer(GLenum value) {
    if (bound_fbo==0 && value==GL_COLOR_ATTACHMENT0) { errors++; return; }
    selectors[bound_fbo]=(int)value;
}
static void glReadPixels(GLint x, GLint y, GLsizei width, GLsizei height,
                         GLenum format, GLenum type, void *pixels) {
    assert(x==0 && y==2 && width==4 && height==1);
    assert(format==GL_BGR_EXT && type==GL_UNSIGNED_BYTE);
    assert(bound_fbo==0 && selectors[0]==GL_FRONT && pack_buffer==0);
    assert(pack[0]==4 && pack[1]==0 && pack[2]==0 && pack[3]==0);
    memset(pixels, 0x71, 12);
}
static GLenum glGetError(void) { return GL_NO_ERROR; }
#include "readback_under_test.h"

int main(void) {
    capture_lease_t lease={0};
    assert(!capture_lease_active(&lease, 0, 1, 100)); /* no reader */
    assert(capture_lease_active(&lease, 1, 1, 200));
    assert(capture_lease_active(&lease, 1, 1, 3199));
    assert(!capture_lease_active(&lease, 1, 1, 3200)); /* killed server */
    assert(!capture_lease_active(&lease, 1, 1, 9000));
    assert(capture_lease_active(&lease, 2, 1, 9001)); /* new owner */
    assert(!capture_lease_active(&lease, 2, 0, 9002)); /* normal stop */
    assert(!capture_lease_active(&lease, 3, 0, 9500)); /* idle heartbeat */
    assert(capture_lease_active(&lease, 3, 1, 9501)); /* resume */
    assert(capture_lease_active(&lease, UINT32_MAX, 1, UINT32_MAX-100));
    assert(capture_lease_active(&lease, UINT32_MAX, 1, 50)); /* clock wrap */
    assert(!capture_lease_active(&lease, UINT32_MAX, 1, 3000));
    assert(capture_lease_active(&lease, 0, 1, 3100)); /* counter wrap */

    for (int fbo=0; fbo<=7; fbo+=7) {
        for (int selector=GL_BACK; selector<=GL_FRONT; selector++) {
            unsigned char pixels[12]={0};
            int saved_pack[4]={8, 320, 11, 13};
            bound_fbo=fbo; selectors[0]=selector; selectors[7]=GL_COLOR_ATTACHMENT0;
            pack_buffer=19; memcpy(pack, saved_pack, sizeof pack); errors=0;
            read_front_buffer(pixels, 4, 1, 2);
            assert(bound_fbo==fbo && selectors[0]==selector);
            assert(selectors[7]==GL_COLOR_ATTACHMENT0 && pack_buffer==19);
            assert(memcmp(pack, saved_pack, sizeof pack)==0 && errors==0);
            for (int i=0; i<12; i++) assert(pixels[i]==0x71);
        }
    }
    return 0;
}
