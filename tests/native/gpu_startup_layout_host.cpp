#include <windows.h>
#include <cstdio>
#include <cstring>
#include <io.h>
#include <fcntl.h>
#include "control.h"
int main(){
    control_page_t page{};auto&s=page.gpu_startup;
    memcpy(s.magic,GPU_STARTUP_MAGIC,8);s.version=GPU_STARTUP_VERSION;s.bytes=sizeof s;
    s.epoch=7;s.load_error=5;s.width=640;s.height=480;s.luid_available=1;s.luid_high=-1;s.luid_low=42;
    s.control_generation=3;s.nonce_lo=5;s.nonce_hi=6;
    strcpy_s(s.vendor,"fixture AMD");strcpy_s(s.renderer,"fixture renderer");
    strcpy_s(s.gl_version,"4.6 fixture");strcpy_s(s.missing_entry_point,"glFixture");
    auto&c=page.gpu_startup_context;
    memcpy(c.magic,GPU_STARTUP_CONTEXT_MAGIC,8);c.version=GPU_STARTUP_CONTEXT_VERSION;c.bytes=sizeof c;
    c.epoch=7;c.identity_source=1;c.bindings_site=14;c.bindings_gl_error=0x502;c.bindings_flags=1;
    c.bindings_invalidations=2;c.bindings_after_ms=30;c.bindings_contexts=4;
    strcpy_s(c.vendor,"fixture context AMD");strcpy_s(c.renderer,"fixture context renderer");
    strcpy_s(c.gl_version,"4.6 context fixture");
    const uint32_t offsets[]={sizeof(page),offsetof(control_page_t,request),offsetof(control_page_t,build_id),
        offsetof(control_page_t,gpu_startup),offsetof(control_page_t,gpu_startup_context)};
    _setmode(_fileno(stdout),_O_BINARY);
    return fwrite(offsets,1,sizeof offsets,stdout)==sizeof offsets&&fwrite(&s,1,sizeof s,stdout)==sizeof s
        &&fwrite(&c,1,sizeof c,stdout)==sizeof c?0:1;
}
