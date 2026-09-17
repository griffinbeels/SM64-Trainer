#include "gpu_request.h"
#include <cstring>
#include <cwchar>
namespace {
bool zero(const void *p,size_t bytes){const auto b=static_cast<const uint8_t*>(p);
    for(size_t n=0;n<bytes;++n)if(b[n])return false;return true;}
}
bool gr_name(const gr_identity &i,wchar_t *out,unsigned count){
    return out && count && swprintf_s(out,count,GR_NAMESPACE L"%u.%u.%u",
        i.producer_pid,i.owner_pid,i.token)>0;
}
bool gr_valid(const gr_page &p,const gr_identity &i,unsigned slots_cap,uint64_t snapshot_cap){
    if(std::memcmp(p.magic,GR_MAGIC,8) || p.version!=GR_VERSION || p.bytes!=GR_BYTES || p.seq!=2
        || !i.producer_pid || !i.owner_pid || !i.control_generation || !i.token || !i.producer_birth || !i.owner_birth
        || p.producer_pid!=i.producer_pid || p.owner_pid!=i.owner_pid || p.control_generation!=i.control_generation
        || p.token!=i.token || p.producer_birth!=i.producer_birth || p.owner_birth!=i.owner_birth
        || (!p.nonce_lo&&!p.nonce_hi) || !p.slots || p.slots>8 || p.slots>slots_cap
        || !p.snapshot_budget || p.snapshot_budget>snapshot_cap
        || !p.rdram_bytes || p.rdram_bytes>(8u<<20) || !p.table_count || p.table_count>16
        || p.channel_budget<4096 || p.channel_budget>GC_MAX_MAP_BYTES
        || !p.packet_count || p.packet_count>8 || !p.packet_bytes || p.packet_bytes>GC_MAX_PACKET_BYTES
        || p.pending_bytes<p.packet_bytes || uint64_t(p.pending_bytes)>uint64_t(p.packet_count)*p.packet_bytes
        || !p.pcm_bytes || p.pcm_bytes>GC_MAX_MAP_BYTES || !p.pcm_blocks || p.pcm_blocks>4096
        || !p.max_age_ms || p.max_age_ms>60000 || !p.encoder_duration || p.encoder_duration>90000
        || p.reserved0 || p.reserved1 || !zero(p.reserved,sizeof p.reserved))return false;
    for(unsigned n=0;n<16;++n){const auto &r=p.table[n];
        if(n>=p.table_count){if(r.offset||r.length)return false;}
        else if(!r.length || r.length>128 || (r.offset&3) || (r.length&3)
                || uint64_t(r.offset)+r.length>p.rdram_bytes)return false;}
    return true;
}
bool gr_read(const gr_identity &i,unsigned slots_cap,uint64_t snapshot_cap,gr_page *out){
    if(!out)return false;*out={};wchar_t name[128];if(!gr_name(i,name,128))return false;
    HANDLE mapping=OpenFileMappingW(FILE_MAP_READ,FALSE,name);if(!mapping)return false;
    const auto page=static_cast<const gr_page*>(MapViewOfFile(mapping,FILE_MAP_READ,0,0,GR_BYTES));
    bool valid=false;
    if(page){const auto before=static_cast<const volatile gr_page*>(page)->seq;
        if(before==2){MemoryBarrier();std::memcpy(out,page,sizeof *out);MemoryBarrier();
            valid=before==static_cast<const volatile gr_page*>(page)->seq && gr_valid(*out,i,slots_cap,snapshot_cap);}
        UnmapViewOfFile(page);}
    CloseHandle(mapping);if(!valid)*out={};return valid;
}
bool gr_channel(const gr_page &p,gc_header *out){
    if(!out)return false;*out={};
    const gr_identity identity{p.producer_pid,p.owner_pid,p.control_generation,p.token,p.producer_birth,p.owner_birth};
    if(!gr_valid(p,identity,8,UINT64_MAX))return false;
    out->owner_pid=p.owner_pid;out->owner_birth=p.owner_birth;out->nonce_lo=p.nonce_lo;out->nonce_hi=p.nonce_hi;
    out->generation=p.control_generation;out->offer_count=p.slots;
    out->table_count=p.table_count;out->packet_count=p.packet_count;out->packet_bytes=p.packet_bytes;
    out->pending_bytes=p.pending_bytes;out->ram_budget=p.channel_budget;
    for(unsigned n=0;n<p.table_count;++n)out->table[n]={p.table[n].offset,p.table[n].length};
    return true;
}
