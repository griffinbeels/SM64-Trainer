/* Failure-only scalar evidence. No allocation, device calls or CRT stream
 * locks. The caller owns the bounded emitter and the original retirement. */
#pragma once
#include <stddef.h>
#include <stdint.h>

enum class GpuOpenFailureStage {
    adapter, device_interface, input_format, shared_texture,
    texture_format, keyed_mutex, completion_query, encoder_prepare
};
struct GpuOpenFailureReceipt {
    uint32_t result=0;
    GpuOpenFailureStage stage=GpuOpenFailureStage::adapter;
    uint32_t encoder_state=0,encoder_error=0,nv_status=0,hresult=0;
    bool encoder_state_available=false,encoder_error_available=false;
    bool nv_status_available=false,hresult_available=false;
};
constexpr size_t GPU_OPEN_FAILURE_LINE_BYTES=384;

inline const char*gpu_open_failure_stage(GpuOpenFailureStage stage) {
    switch(stage) {
    case GpuOpenFailureStage::adapter:return "adapter";
    case GpuOpenFailureStage::device_interface:return "device_interface";
    case GpuOpenFailureStage::input_format:return "input_format";
    case GpuOpenFailureStage::shared_texture:return "shared_texture";
    case GpuOpenFailureStage::texture_format:return "texture_format";
    case GpuOpenFailureStage::keyed_mutex:return "keyed_mutex";
    case GpuOpenFailureStage::completion_query:return "completion_query";
    case GpuOpenFailureStage::encoder_prepare:return "encoder_prepare";
    }return "unknown";
}
struct GpuOpenFailureLine {
    char*line;size_t capacity,used=0;bool complete=true;
    void put(char value) {
        if(used+1>=capacity){complete=false;return;}
        line[used++]=value;line[used]=0;
    }
    void text(const char*value){while(*value)put(*value++);}
    void number(uint32_t value) {
        char digits[10];unsigned count=0;
        do{digits[count++]=char('0'+value%10);value/=10;}while(value);
        while(count)put(digits[--count]);
    }
    void field(const char*name,uint32_t value,bool available=true) {
        put(' ');text(name);put('=');
        if(available)number(value);else text("unknown");
    }
};
inline size_t gpu_open_failure_format(char*line,size_t capacity,const GpuOpenFailureReceipt&r) {
    if(!line||!capacity)return 0;
    line[0]=0;GpuOpenFailureLine out{line,capacity};
    out.text("event=encoder_open_failure");out.field("native_result",r.result);
    out.text(" stage=");out.text(gpu_open_failure_stage(r.stage));
    out.field("encoder_state",r.encoder_state,r.encoder_state_available);
    out.field("encoder_error",r.encoder_error,r.encoder_error_available);
    out.field("nv_status",r.nv_status,r.nv_status_available);
    out.field("hresult",r.hresult,r.hresult_available);out.put('\n');
    return out.complete?out.used:0; // Never send a truncated diagnostic line.
}
template<class Emit,class Retire>
inline uint32_t gpu_open_failed_retire(const GpuOpenFailureReceipt&r,char*line,size_t capacity,
                                      Emit emit,Retire retire) {
    const size_t bytes=gpu_open_failure_format(line,capacity,r);
    if(bytes)emit(line,bytes); // Best effort, once. No retry or delivery claim.
    retire();return r.result; // Failed emission cannot change the native result.
}
