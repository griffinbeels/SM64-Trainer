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
    const uint32_t offsets[]={sizeof(page),offsetof(control_page_t,request),offsetof(control_page_t,build_id),offsetof(control_page_t,gpu_startup)};
    _setmode(_fileno(stdout),_O_BINARY);
    return fwrite(offsets,1,sizeof offsets,stdout)==sizeof offsets&&fwrite(&s,1,sizeof s,stdout)==sizeof s?0:1;
}
