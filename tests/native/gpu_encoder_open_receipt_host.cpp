/* Pure formatter/retirement witness: no Windows, D3D, NVENC, DLL or device. */
#include "gpu_encoder_open_receipt.h"
#include <stdio.h>
#include <string.h>

#define CHECK(value) do{if(!(value)){fprintf(stderr,"failure line=%d %s\n",__LINE__,#value);return false;}}while(0)

static bool unavailable_fields() {
    GpuOpenFailureReceipt receipt;receipt.result=7;receipt.stage=GpuOpenFailureStage::shared_texture;
    char line[GPU_OPEN_FAILURE_LINE_BYTES];
    const size_t size=gpu_open_failure_format(line,sizeof line,receipt);
    CHECK(size==strlen(line));CHECK(size<sizeof line);
    CHECK(!strcmp(line,"event=encoder_open_failure native_result=7 stage=shared_texture encoder_state=unknown encoder_error=unknown nv_status=unknown hresult=unknown\n"));
    // Unavailable fields must not publish stale/default numbers as evidence.
    receipt.encoder_state=99;receipt.encoder_error=99;receipt.nv_status=99;receipt.hresult=99;
    CHECK(gpu_open_failure_format(line,sizeof line,receipt)==size);
    CHECK(!strstr(line,"99"));return true;
}
static bool owned_fields_and_raw_maxima() {
    GpuOpenFailureReceipt receipt;receipt.result=8;receipt.stage=GpuOpenFailureStage::encoder_prepare;
    // State can be CLOSED after preparation cleanup. The latest stored NVENC
    // failure need not be the primary failure, and zero is not a success claim.
    receipt.encoder_state=4;receipt.encoder_error=7;receipt.nv_status=10;
    receipt.encoder_state_available=receipt.encoder_error_available=receipt.nv_status_available=true;
    char line[GPU_OPEN_FAILURE_LINE_BYTES];
    CHECK(gpu_open_failure_format(line,sizeof line,receipt)>0);
    CHECK(strstr(line,"encoder_state=4 encoder_error=7 nv_status=10 hresult=unknown\n"));
    receipt.nv_status=0;receipt.nv_status_available=false;
    CHECK(gpu_open_failure_format(line,sizeof line,receipt)>0);CHECK(strstr(line,"nv_status=unknown"));
    receipt.result=receipt.encoder_state=receipt.encoder_error=receipt.nv_status=receipt.hresult=UINT32_MAX;
    receipt.nv_status_available=receipt.hresult_available=true;
    const size_t size=gpu_open_failure_format(line,sizeof line,receipt);
    CHECK(size>0&&size<sizeof line&&line[size-1]=='\n'&&line[size]==0);
    CHECK(!strcmp(line,"event=encoder_open_failure native_result=4294967295 stage=encoder_prepare encoder_state=4294967295 encoder_error=4294967295 nv_status=4294967295 hresult=4294967295\n"));
    return true;
}
static bool bounds_and_canaries() {
    GpuOpenFailureReceipt receipt;receipt.result=8;
    char full[GPU_OPEN_FAILURE_LINE_BYTES];const size_t need=gpu_open_failure_format(full,sizeof full,receipt);
    CHECK(need>0&&need<sizeof full);
    CHECK(gpu_open_failure_format(nullptr,100,receipt)==0);
    for(size_t capacity=0;capacity<=sizeof full;++capacity){
        unsigned char storage[GPU_OPEN_FAILURE_LINE_BYTES+2];memset(storage,0xA5,sizeof storage);
        const size_t size=gpu_open_failure_format(reinterpret_cast<char*>(storage+1),capacity,receipt);
        CHECK(storage[0]==0xA5&&storage[capacity+1]==0xA5);
        if(capacity<=need)CHECK(size==0);else CHECK(size==need);
        if(size)CHECK(!memcmp(storage+1,full,size+1));
    }return true;
}
static bool failed_emit_still_retires_once() {
    GpuOpenFailureReceipt receipt;receipt.result=8;
    char line[GPU_OPEN_FAILURE_LINE_BYTES];bool alive=true,valid=true;unsigned calls=0,retired=0;
    const auto result=gpu_open_failed_retire(receipt,line,sizeof line,[&](const char*text,size_t bytes){
        ++calls;valid&=alive&&bytes>0&&bytes<GPU_OPEN_FAILURE_LINE_BYTES&&text[bytes-1]=='\n';
        return false; // A missing pipe, failed write, or short write is not retried.
    },[&]{++retired;alive=false;});
    CHECK(valid&&result==8&&!alive&&calls==1&&retired==1);return true;
}
static bool truncated_format_skips_emit_but_preserves_retirement() {
    GpuOpenFailureReceipt receipt;receipt.result=7;
    char line[1];unsigned calls=0,retired=0;
    CHECK(gpu_open_failed_retire(receipt,line,sizeof line,[&](const char*,size_t){++calls;},
                                [&]{++retired;})==7);
    CHECK(calls==0&&retired==1);return true;
}
static bool stage_labels() {
    const char*names[]={"adapter","device_interface","input_format","shared_texture",
                       "texture_format","keyed_mutex","completion_query","encoder_prepare"};
    for(unsigned i=0;i<sizeof names/sizeof *names;++i)
        CHECK(!strcmp(gpu_open_failure_stage(static_cast<GpuOpenFailureStage>(i)),names[i]));
    CHECK(!strcmp(gpu_open_failure_stage(static_cast<GpuOpenFailureStage>(999)),"unknown"));return true;
}
int main() {
    struct Case{const char*name;bool(*run)();};
    const Case cases[]={{"unavailable_fields",unavailable_fields},{"owned_fields_and_raw_maxima",owned_fields_and_raw_maxima},
        {"bounds_and_canaries",bounds_and_canaries},{"failed_emit_still_retires_once",failed_emit_still_retires_once},
        {"truncated_format_skips_emit_but_preserves_retirement",truncated_format_skips_emit_but_preserves_retirement},
        {"stage_labels",stage_labels}};
    for(const auto&test:cases){if(!test.run())return 1;printf("PASS %s\n",test.name);}
    return 0;
}
