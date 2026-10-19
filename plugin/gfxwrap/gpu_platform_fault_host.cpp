/* Contract refusals run without creating any graphics device. */
#include "gpu_platform_encoder.h"
#include <stddef.h>
#include <stdio.h>
static_assert(sizeof(gbenc_options_v1)==128,"V1 options size unchanged");
static_assert(offsetof(gbenc_options_v1,backend)==112&&offsetof(gbenc_options_v1,backend_quality)==116,"append-only backend words");
int main(){
    const gbenc_sink_v1 sink{};
    for(unsigned fault=0;fault<5;++fault){
        gbenc_options_v1 options{};options.struct_size=sizeof(options);options.version=GBENC_ABI_V1;
        if(fault==0)options.backend=2;
        if(fault==1)options.backend_quality=1;
        if(fault==2)options.reserved[0]=1;
        if(fault==3)options.reserved[1]=1;
        if(fault==4)options.struct_size-=4;
        PlatformEncoder encoder;
        if(encoder.prepare(nullptr,nullptr,options,sink)||encoder.error()!=GBENC_INVALID_ARGUMENT||encoder.submitted()!=0)return 1;
        if(!encoder.close())return 2;
    }
    puts("PASS unsupported backend, mixed policy, reserved fields and ABI size refuse before device access");return 0;
}
