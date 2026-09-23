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
    uint8_t byte(uint32_t p) {
        if (!has(p,1,1)) return 0;
        return data[(p-base)^3];
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

// SM64 allocates display-list memory downward from gGfxPoolEnd in traversal
// order. For Mario (mario_geo: GEO_SHADOW -> GEO_SCALE -> culling ASM ->
// alpha ASM) a drawn shadow is: 9 Vtx, its 5-command list, its G_MTX, the
// scale G_MTX, then the opaque alpha list (n64decomp shadow.c
// create_shadow_player, rendering_graph_node.c geo_process_shadow/scale,
// mario_misc.c make_gfx_mario_alpha). The root proof already owns that alpha
// list, so Mario's shadow sits at fixed offsets from it; any other object's
// shadow, or none, cannot satisfy them together with the exact list shapes.
constexpr uint32_t shadow_list_offset = 144, shadow_matrix_offset = 80, shadow_vertex_offset = 184;
struct Command { uint32_t w0, w1; };
constexpr Command shadow_begin[] = {{0xe7000000u,0},{0xb6000000u,0x00022000u},
    {0xfc121824u,0xff33ffffu},{0xbb000001u,0xffffffffu},{0xb8000000u,0}};
constexpr Command shadow_end[] = {{0xe7000000u,0},{0xbb000000u,0xffffffffu},
    {0xb7000000u,0x00022000u},{0xfcffffffu,0xfffe793cu},{0xb8000000u,0}};
// dl_shadow_circle after its begin call and SETTIMG: 16x16 IA8 render tile,
// mirrored on both axes with mask 4 (the compositor's sampler contract).
constexpr Command shadow_circle_tail[] = {{0xf5700000u,0x07050140u},{0xe6000000u,0},
    {0xf3000000u,0x0707f400u},{0xe7000000u,0},{0xf5680400u,0x00050140u},
    {0xf2000000u,0x0003c03cu},{0xb8000000u,0}};
bool commands(Ram &ram, uint32_t physical, const Command *expected, unsigned count) {
    if (physical >= ram.size || !ram.has(base+physical, 8*count, 8)) return false;
    for (unsigned i=0;i<count;++i)
        if (ram.word(base+physical+8*i)!=expected[i].w0 || ram.word(base+physical+8*i+4)!=expected[i].w1)
            return false;
    return ram.ok;
}
bool triangles(Ram &ram, uint32_t physical) {
    Command expected[9]{};
    for (unsigned t=0;t<8;++t)
        expected[t]={0xbf000000u, uint32_t(gc_shadow_triangles[3*t]*10)<<16
            | uint32_t(gc_shadow_triangles[3*t+1]*10)<<8 | uint32_t(gc_shadow_triangles[3*t+2]*10)};
    expected[8]={0xb8000000u,0};
    return commands(ram,physical,expected,9);
}
// Segmented operand -> physical, using the base the renderer just resolved.
bool translate(uint32_t segmented, uint32_t segment_base, uint32_t segment, uint32_t &physical) {
    if ((segmented>>24)!=segment) return false;
    physical=segment_base+(segmented&0x00ffffffu); return true;
}
// Master-list call: G_MTX (its own allocation) + G_DL to the shadow list.
bool shadow_master(gc_root_probe &p, Ram &ram, uint32_t pc, uint32_t physical, const float *matrix) {
    const uint32_t alpha=p.mario_alpha;
    if (physical!=alpha+shadow_list_offset || !matrix_at(ram,pc,matrix)
            || ram.word(base+pc-4)!=alpha+shadow_matrix_offset) return false;
    const uint32_t list=base+physical, vertices=alpha+shadow_vertex_offset;
    if (!ram.has(list,40,8) || ram.word(list)!=0x06000000u || ram.word(list+8)!=0x04800090u
            || ram.word(list+12)!=(base|vertices) || ram.word(list+16)!=0x06000000u
            || ram.word(list+24)!=0x06000000u || ram.word(list+32)!=0xb8000000u || ram.word(list+36))
        return false;
    // The camera look-at is rigid, and the shadow sits within its animation
    // offset of Mario. A sanity bound, not the ownership proof above.
    for (unsigned i=0;i<3;++i) {
        double offset=0;
        for (unsigned j=0;j<3;++j) {
            double dot=0;
            for (unsigned k=0;k<3;++k) dot+=double(matrix[4*i+k])*matrix[4*j+k];
            if (std::fabs(dot-(i==j?1.0:0.0))>0.002) return false;
            offset+=double(matrix[12+j]-p.camera_to_object[j])*matrix[4*i+j];
        }
        if (std::fabs(offset)>1024) return false;
    }
    gc_shadow shadow{};
    std::memcpy(shadow.matrix,matrix,sizeof(shadow.matrix));
    for (unsigned v=0;v<GC_SHADOW_VERTICES;++v) {
        const uint32_t at=base+vertices+16*v;
        if (!ram.has(at,16,8)) return false;
        for (unsigned a=0;a<3;++a) shadow.position[v][a]=int16_t(ram.half(at+2*a));
        for (unsigned a=0;a<2;++a) shadow.uv[v][a]=int16_t(ram.half(at+8+2*a));
        // make_shadow_vertex_at_xyz: white shade, per-vertex solidity alpha.
        if (ram.byte(at+12)!=255 || ram.byte(at+13)!=255 || ram.byte(at+14)!=255) return false;
        shadow.alpha[v]=ram.byte(at+15);
    }
    if (!ram.ok) return false;
    p.shadow=shadow;
    p.shadow_nested[0]=ram.word(list+4); p.shadow_nested[1]=ram.word(list+20);
    p.shadow_nested[2]=ram.word(list+28);
    return true;
}
// Nested calls inside the shadow list, in executed order at list depth.
bool shadow_nested(gc_root_probe &p, Ram &ram, uint32_t dl, uint32_t physical) {
    const uint32_t step=p.shadow_step-1;
    if (dl!=p.shadow_nested[step] || physical>=ram.size) return false;
    const uint32_t segment=dl>>24, segment_base=physical-(dl&0x00ffffffu);
    // Every nested list and the texture come from one segment (segment 2),
    // resolved through the one base the circle call established.
    if (step==0) p.shadow_segment_base=segment_base;
    else if (segment_base!=p.shadow_segment_base) return false;
    for (unsigned i=0;i<3;++i) {
        uint32_t expected=0;
        if (!translate(p.shadow_nested[i],segment_base,segment,expected)) return false;
    }
    if (step==0) {
        const uint32_t circle=base+physical;
        uint32_t begin=0,texture=0;
        if (!ram.has(circle,72,8) || ram.word(circle)!=0x06000000u || ram.word(circle+8)!=0xfd700000u
                || !translate(ram.word(circle+4),segment_base,segment,begin)
                || !translate(ram.word(circle+12),segment_base,segment,texture)
                || !commands(ram,begin,shadow_begin,5) || !commands(ram,physical+16,shadow_circle_tail,7)
                || texture>=ram.size || !ram.has(base+texture,256,8)) return false;
        for (unsigned i=0;i<256;++i) {
            const uint8_t texel=ram.byte(base+texture+i);
            if (texel&0xf0) return false; // shadows are black: intensity zero
            p.shadow.texture[i/2]|=uint8_t((texel&15)<<((i&1)?0:4));
        }
        return ram.ok;
    }
    return step==1 ? triangles(ram,physical) : commands(ram,physical,shadow_end,5);
}
void shadow_call(gc_root_probe &p, Ram &ram, uint32_t pc, uint32_t dl, uint32_t physical,
                 uint32_t depth, const float *matrix) {
    if (!p.mario_alpha || p.shadow_calls>1) return;
    const uint32_t list=p.mario_alpha+shadow_list_offset;
    if (dl==physical && physical==list) {
        // A second execution of the list cannot be attributed to one frame;
        // the master list runs every layer at the depth that ran the body.
        if (++p.shadow_calls>1 || depth!=p.depth || !shadow_master(p,ram,pc,physical,matrix)) {
            p.shadow_step=0; return;
        }
        p.shadow_depth=depth; p.shadow_step=1; return;
    }
    if (!p.shadow_step || p.shadow_step>3 || depth!=p.shadow_depth+1) return;
    static constexpr uint32_t offsets[3]={0,16,24};
    if (pc!=list+offsets[p.shadow_step-1]) return;
    p.shadow_step=shadow_nested(p,ram,dl,physical) ? p.shadow_step+1 : 0;
}
}

bool ghost_floor_aligned(uint32_t action) {
    // align_with_floor: crawling and common_slide_action, including their
    // same-call bonk/stop destinations. Conservative both ways: a listed
    // action may have tilted the root, so it never gets a composed TRS root,
    // and it is not a discriminator for a persistent sFloorAlignMatrix.
    switch (action & 0x1ffu) {
    case 0x20: case 0x3e: case 0x3f: case 0x48:
    case 0x52: case 0x53: case 0x54: case 0x55: case 0x56:
    case 0x59: case 0x66: case 0x186: return true;
    default: return false;
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
    // Each early return leaves the reason finish publishes if the root stays unproven.
    p.reason=GC_REFUSED_CAMERA;
    if (!(sample.valid_fields&GC_VALID_CAMERA) || p.pose.hidden) return;
    p.ram=frame.rdram; p.ram_bytes=frame.rdram_bytes;
    p.epoch=frame.epoch; p.sequence=frame.sequence; p.serial=frame.dlist_serial;
    Ram ram{p.ram,p.ram_bytes};
    p.reason=GC_REFUSED_CAMERA_TO_OBJECT;
    for (unsigned i=0;i<3;++i) {
        p.camera_to_object[i]=ram.number(object+0x54+4*i);
        if (!std::isfinite(p.camera_to_object[i]) || std::fabs(p.camera_to_object[i])>=32767) return;
    }
    const auto shadow=ram.word(object+0x14), scale=ram.word(shadow+16);
    // Another active object sharing this model makes asset identity alone
    // insufficient. Refuse rather than borrow its matrix (even if coincident).
    p.reason=GC_REFUSED_SHARED_MODEL;
    for(unsigned i=0;i<240;++i) {
        const auto other=object_pool+i*0x260;
        if(other!=object && (ram.half(other+2)&17)==1 && ram.word(other+0x14)==shadow) return;
    }
    p.reason=GC_REFUSED_GRAPH;
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
    p.reason=GC_REFUSED_BODY_GRAPH;
    p.prepared=ram.ok && p.body_dl!=0;
    // align_with_floor (and the object throw-matrix flags) are the only
    // writers of Mario's throwMatrix; otherwise his root is plain TRS.
    p.floor_aligned=ghost_floor_aligned(p.pose.action) || ghost_floor_aligned(p.pose.body_action)
        || (ram.word(object+0x8c)&0xa00u) || !ram.ok;
    // Translucent or metal Mario draws a different alpha list; unsupported,
    // and never mistaken for a Mario the camera culled.
    if (p.prepared) p.reason=uint8_t((p.pose.model_state&0x300u) ? GC_REFUSED_MATERIAL : GC_REFUSED_NONE);
}

void ghost_root_display_list(gc_root_probe &p, uint32_t pc, uint32_t dl,
                             uint32_t physical_dl, uint32_t depth, const float matrix[16]) {
    if (!p.prepared || !matrix) return;
    Ram ram{p.ram,p.ram_bytes};
    const bool command=pc<p.ram_bytes && ram.word(base+pc)==0x06000000u && ram.word(base+pc+4)==dl;
    if (p.armed && command && pc==p.alpha_pc+16 && depth==p.depth && dl==p.body_dl
            && matrix_at(ram,pc,matrix)) {
        if (p.matches<2) ++p.matches;
        if (p.matches==1) p.mario_alpha=p.alpha_dl;
    }
    if (command) shadow_call(p,ram,pc,dl,physical_dl,depth,matrix);
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
    p.alpha_seen=true;
    if (!p.matches) std::memcpy(p.matrix,matrix,sizeof(p.matrix));
    p.alpha_pc=pc; p.alpha_dl=physical_dl; p.depth=depth; p.armed=true;
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
namespace {
bool same_scene(const gc_root_memory &m, const gc_sample &s) {
    return m.valid && m.epoch==s.epoch && m.level==s.level && m.area==s.area;
}
bool same_root_inputs(const gc_root_memory &m, const gc_sample &s, const gc_pose &pose) {
    return same_scene(m,s) && m.action==pose.action && m.body_action==pose.body_action
        && !std::memcmp(m.position,pose.position,sizeof(m.position))
        && !std::memcmp(m.scale,pose.scale,sizeof(m.scale))
        && !std::memcmp(m.angles,pose.angles,sizeof(m.angles));
}
void remember(gc_root_memory &m, const gc_sample &s) {
    const auto &pose=s.pose;
    m.valid=true; m.epoch=s.epoch; m.level=s.level; m.area=s.area;
    m.action=pose.action; m.body_action=pose.body_action;
    std::memcpy(m.position,pose.position,sizeof(m.position));
    std::memcpy(m.scale,pose.scale,sizeof(m.scale));
    std::memcpy(m.angles,pose.angles,sizeof(m.angles));
    m.animation_id=pose.animation_id; m.animation_frame=pose.animation_frame;
    m.anim_y=pose.anim_y_translation;
    std::memcpy(m.matrix,pose.root_matrix,sizeof(m.matrix));
    m.camera=s.camera;
    m.shadowed=(s.valid_fields&GC_VALID_SHADOW)!=0; m.shadow=s.shadow;
}
}
void ghost_root_finish(gc_root_probe &p, bool completed, gc_sample &sample, gc_root_memory *memory) {
    // The shadow publishes only beside the root it was proven through, after
    // its master call and all three nested calls executed exactly once.
    if (completed && ghost_root_candidate(p,sample)) {
        if (p.shadow_calls==1 && p.shadow_step==4) {
            sample.shadow=p.shadow; sample.valid_fields|=GC_VALID_SHADOW;
        }
        if (memory) remember(*memory,sample);
        p={}; return;
    }
    if (!p.required) { p={}; return; } // TRS, hidden, or refused before the probe
    const bool current=completed && sample.epoch==p.epoch && sample.sequence==p.sequence
        && sample.dlist_serial==p.serial;
    uint8_t reason=p.reason;
    if (!reason) reason=uint8_t(!current ? GC_REFUSED_INCOMPLETE : p.matches>1 ? GC_REFUSED_DUPLICATE
        : p.alpha_seen ? GC_REFUSED_BODY_MISMATCH : GC_REFUSED_NOT_DRAWN);
    if (reason==GC_REFUSED_NOT_DRAWN) {
        if (memory && same_root_inputs(*memory,sample,p.pose)) {
            sample.pose=p.pose; sample.valid_fields|=GC_VALID_POSE;
            sample.pose.root_kind=GC_ROOT_SCALED_MODELVIEW;
            std::memcpy(sample.pose.root_matrix,memory->matrix,sizeof(memory->matrix));
            sample.camera=memory->camera;
            if (memory->shadowed && memory->animation_id==p.pose.animation_id
                    && memory->animation_frame==p.pose.animation_frame
                    && memory->anim_y==p.pose.anim_y_translation) {
                sample.shadow=memory->shadow; sample.valid_fields|=GC_VALID_SHADOW;
            }
        } else if (!p.floor_aligned) {
            // Nothing set throwMatrix, so geo_process_object composed exactly
            // TRS(gfx pos, angle, scale); the model reproduces that itself.
            sample.pose=p.pose; sample.valid_fields|=GC_VALID_POSE;
            sample.pose.root_kind=GC_ROOT_TRS;
        } else reason=GC_REFUSED_NOT_DRAWN_FLOOR;
        if ((sample.valid_fields&GC_VALID_POSE) && sample.status==GC_UNSUPPORTED_ROOT) sample.status=GC_OK;
    }
    sample.pose.refusal=reason;
    p={};
}
