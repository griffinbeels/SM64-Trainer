/* Actual optional-capability query with an inert current-context shell. */
#define NOMINMAX
#include <windows.h>
#include "renderer_gl_state.h"
#include <cstdio>
#include <cstring>
using GLint=int;using GLuint=unsigned;using GLenum=unsigned;using GLubyte=unsigned char;
#define GL_EXTENSIONS 0x1F03
static GLint current_major=4,current_minor=3,extension_count=0;static bool arb=false;static unsigned queries=0;
void capability_integer(GLenum name,GLint *out){++queries;*out=name==0x821B?current_major:name==0x821C?current_minor:extension_count;}
#define glGetIntegerv capability_integer
const GLubyte* APIENTRY strings(GLenum,GLuint){return reinterpret_cast<const GLubyte*>(arb?"GL_ARB_multi_bind":"GL_OTHER_extension");}
void* extension(const char*){return reinterpret_cast<void*>(strings);}
#include "actual_multibind.inc"
int main(){
    renderer::Identity owner{1,1,1,1,1};
    if(multibind(owner))return 1; // stubs exist on 4.3; no advertised support
    const auto once=queries;if(multibind(owner) || queries!=once)return 2;
    ++owner.context_generation;arb=true;extension_count=1;if(!multibind(owner))return 3;
    ++owner.context_generation;arb=false;extension_count=0;current_minor=4;if(!multibind(owner))return 4;
    ++owner.context_generation;current_minor=3;extension_count=4097;if(multibind(owner))return 5;
    std::puts("multibind passed: gl43=scalar arb=enabled gl44=enabled query=once_per_context bounded=yes");return 0;
}
