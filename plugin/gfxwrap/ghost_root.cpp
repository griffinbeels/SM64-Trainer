#include "ghost_root.h"
#include <cmath>
#include <cstring>
#include <initializer_list>

namespace {
constexpr uint32_t base = 0x80000000u;
struct Ram {
    const uint8_t *data; uint32_t size; bool ok = true;
    bool has(uint32_t p, uint32_t n, uint32_t align = 4) {
        if (p < base || (p & (align-1)) || uint64_t(p-base)+n > size) ok = false;
        return ok;
    }
    uint32_t word(uint32_t p) {
        if (!has(p,4)) return 0;
        uint32_t v; std::memcpy(&v,data+p-base,4); return v;
    }
    uint16_t half(uint32_t p) {
        if (!has(p,2,2)) return 0;
        return uint16_t(uint16_t(data[(p-base)^3])<<8 | data[(p-base+1)^3]);
    }
    float number(uint32_t p) { const auto v=word(p); float f; std::memcpy(&f,&v,4); return f; }
};

// Persistent geo nodes, not GraphNodeMasterList listHeads: geo_process_root
// frees the latter's allocation pool before the renderer executes the DList.
struct BodyWalk {
    Ram &ram; int16_t distance;
    uint32_t visited[64]{}, count = 0;
    uint32_t chain(uint32_t first, uint32_t parent, unsigned depth, bool one = false) {
        if (!first || depth >= 16) return 0;
        auto node = first;
        do {
            if (count == 64 || !ram.has(node,0x20)) return 0;
            for (uint32_t i=0;i<count;++i) if (visited[i] == node) return 0;
            visited[count++] = node;
            if (ram.word(node+12) != parent) return 0;
            const auto type=ram.half(node), flags=ram.half(node+2);
            if (!(flags&1) || (flags&18)) return 0;
            auto child=ram.word(node+16);
            uint32_t result=0;
            if (type==0x19) {
                if (const auto dl=ram.word(node+0x14)) return dl;
                result=chain(child,node,depth+1);
            } else if (type==0x10c) {
                const int16_t selected=int16_t(ram.half(node+0x1e));
                if (selected<0 || selected>7 || !child) return 0;
                const auto initial=child;
                for (int i=0;i<selected;++i) {
                    child=ram.word(child+8);
                    if (!child || child==initial) return 0;
                }
                result=chain(child,node,depth+1,true);
            } else if (type==0x0b) {
                if (int16_t(ram.half(node+0x14))<=distance && distance<int16_t(ram.half(node+0x16)))
                    result=chain(child,node,depth+1);
            } else if (type==0x0a) result=chain(child,node,depth+1);
            else return 0; // No guessed traversal of a different model layout.
            if (result) return result;
            if (!ram.ok || one) return 0;
            node=ram.word(node+8);
        } while (node && node!=first);
        return 0;
    }
};

bool matrix_at(Ram &ram, uint32_t command, const float *matrix) {
    if (command<8 || command>=ram.size || ram.word(base+command-8)!=0x01020040u) return false;
    const auto address=ram.word(base+command-4);
    if (address>=ram.size || !ram.has(base+address,64,8)) return false;
    for (unsigned i=0;i<16;++i) {
        const float expected=float(int16_t(ram.half(base+address+2*i)))
            +float(ram.half(base+address+32+2*i))/65536.0f;
        if (!std::isfinite(matrix[i]) || matrix[i]!=expected) return false;
    }
    const double det=double(matrix[0])*(double(matrix[5])*matrix[10]-double(matrix[6])*matrix[9])
        -double(matrix[1])*(double(matrix[4])*matrix[10]-double(matrix[6])*matrix[8])
        +double(matrix[2])*(double(matrix[4])*matrix[9]-double(matrix[5])*matrix[8]);
    return ram.ok && det!=0 && std::isfinite(det)
        && matrix[3]==0 && matrix[7]==0 && matrix[11]==0 && matrix[15]==1;
}
}

void ghost_root_prepare(gc_root_probe &p, const gc_frame &frame, uint32_t object,
                        uint32_t object_pool, gc_sample &sample) {
    p={};
    if (!(sample.valid_fields&GC_VALID_POSE)) return;
    p.required=sample.pose.root_kind==GC_ROOT_UNRESOLVED;
    p.pose=sample.pose;
    if (p.required) {
        sample.pose={}; sample.valid_fields&=~GC_VALID_POSE;
        if (!sample.status) sample.status=GC_UNSUPPORTED_ROOT;
    }
    if (!(sample.valid_fields&GC_VALID_CAMERA) || p.pose.hidden) return;
    p.ram=frame.rdram; p.ram_bytes=frame.rdram_bytes;
    p.epoch=frame.epoch; p.sequence=frame.sequence; p.serial=frame.dlist_serial;
    Ram ram{p.ram,p.ram_bytes};
    for (unsigned i=0;i<3;++i) {
        p.camera_to_object[i]=ram.number(object+0x54+4*i);
        if (!std::isfinite(p.camera_to_object[i]) || std::fabs(p.camera_to_object[i])>=32767) return;
    }
    const auto shadow=ram.word(object+0x14), scale=ram.word(shadow+16);
    // Another active object sharing this model makes asset identity alone
    // insufficient. Refuse rather than borrow its matrix (even if coincident).
    for(unsigned i=0;i<240;++i) {
        const auto other=object_pool+i*0x260;
        if(other!=object && (ram.half(other+2)&17)==1 && ram.word(other+0x14)==shadow) return;
    }
    if (!ram.has(shadow,0x1c) || ram.half(shadow)!=0x28 || ram.word(shadow+12)
            || !ram.has(scale,0x1c) || ram.half(scale)!=0x1c
            || ram.word(scale+12)!=shadow || ram.word(scale+0x14) || ram.number(scale+0x18)!=0.25f) return;
    const auto cull=ram.word(scale+16), alpha=ram.word(cull+8), body=ram.word(alpha+8);
    for (auto node : {cull,alpha}) {
        if (!ram.has(node,0x1c) || ram.half(node)!=0x12a || !(ram.half(node+2)&1)
                || ram.word(node+12)!=scale || ram.word(node+16) || !ram.word(node+0x14)
                || ram.word(node+0x18)) return;
    }
    if (ram.half(body)!=0x10c) return;
    const auto distance=int16_t(-int16_t(std::floor(p.camera_to_object[2])));
    BodyWalk walk{ram,distance};
    p.body_dl=walk.chain(body,scale,0,true);
    p.prepared=ram.ok && p.body_dl!=0;
}

void ghost_root_display_list(gc_root_probe &p, uint32_t pc, uint32_t dl,
                             uint32_t physical_dl, uint32_t depth, const float matrix[16]) {
    if (!p.prepared || !matrix) return;
    Ram ram{p.ram,p.ram_bytes};
    const bool command=pc<p.ram_bytes && ram.word(base+pc)==0x06000000u && ram.word(base+pc+4)==dl;
    if (p.armed && command && pc==p.alpha_pc+16 && depth==p.depth && dl==p.body_dl
            && matrix_at(ram,pc,matrix)) {
        if (p.matches<2) ++p.matches;
    }
    p.armed=false;
    // make_gfx_mario_alpha: opaque Mario has exactly ENVCOLOR + ENDDL.
    // Translucent/metal material support is deliberately outside this route.
    if (!command || (p.pose.model_state&0x300u) || dl!=physical_dl
            || physical_dl>=p.ram_bytes || !ram.has(base+physical_dl,16,8)
            || ram.word(base+physical_dl)!=0xfb000000u || ram.word(base+physical_dl+4)!=0xffffffffu
            || ram.word(base+physical_dl+8)!=0xb8000000u || ram.word(base+physical_dl+12)
            || !matrix_at(ram,pc,matrix)) return;
    for (unsigned i=0;i<3;++i)
        if (std::fabs(matrix[12+i]-p.camera_to_object[i])>1.0f/32768.0f) return;
    if (!p.matches) std::memcpy(p.matrix,matrix,sizeof(p.matrix));
    p.alpha_pc=pc; p.depth=depth; p.armed=true;
}

bool ghost_root_candidate(const gc_root_probe &p, gc_sample &sample) {
    if (p.prepared && p.matches==1 && sample.epoch==p.epoch
            && sample.sequence==p.sequence && sample.dlist_serial==p.serial) {
        sample.pose=p.pose; sample.valid_fields|=GC_VALID_POSE;
        sample.pose.root_kind=GC_ROOT_SCALED_MODELVIEW;
        std::memcpy(sample.pose.root_matrix,p.matrix,sizeof(p.matrix));
        if (sample.status==GC_UNSUPPORTED_ROOT) sample.status=GC_OK;
        return true;
    }
    return false;
}
void ghost_root_finish(gc_root_probe &p, bool completed, gc_sample &sample) {
    if (completed) ghost_root_candidate(p,sample);
    p={};
}
