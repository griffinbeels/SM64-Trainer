/* Immutable supervisor-only request, published before ControlV1 enable. */
#pragma once
#include <stdint.h>
#include <stddef.h>
#define GR_VERSION 1u
#define GR_BYTES 512u
#define GR_MAGIC "SM64GPR1"
#define GR_NAMESPACE L"Local\\SM64Trainer.GpuRequest.v1."
#pragma pack(push,8)
typedef struct { uint32_t offset,length; } gr_field;
typedef struct {
    char magic[8];
    uint32_t version,bytes,seq,producer_pid,owner_pid,control_generation,token,reserved0;
    uint64_t producer_birth,owner_birth,nonce_lo,nonce_hi,snapshot_budget;
    uint32_t slots,rdram_bytes,table_count,channel_budget,packet_count,packet_bytes,
        pending_bytes,pcm_bytes,pcm_blocks,max_age_ms,encoder_duration,reserved1;
    gr_field table[16];
    uint8_t reserved[256];
} gr_page;
#pragma pack(pop)
#ifdef __cplusplus
static_assert(sizeof(gr_page)==512 && offsetof(gr_page,table)==128, "request wire layout");
#include <windows.h>
#include "gpu_channel.h"
struct gr_identity {
    uint32_t producer_pid,owner_pid,control_generation,token;
    uint64_t producer_birth,owner_birth;
};
bool gr_valid(const gr_page&,const gr_identity&,unsigned slots_cap,uint64_t snapshot_cap);
bool gr_read(const gr_identity&,unsigned slots_cap,uint64_t snapshot_cap,gr_page*);
bool gr_channel(const gr_page&,gc_header*); // partial configuration, no guessed surface
bool gr_name(const gr_identity&,wchar_t*,unsigned characters);
#endif
