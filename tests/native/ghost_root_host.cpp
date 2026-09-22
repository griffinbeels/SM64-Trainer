/* Byte fixture follows geo_process_object -> GEO_SCALE(.25) -> generated
 * alpha -> first animated-part body. throwMatrix is already NULL here. */
#define main capture_fixture_main
#include "ghost_capture_host.cpp"
#undef main
#include <cmath>
#include <functional>
#if GC_VERSION >= 4
#include "ghost_root.h"
#endif

#include "ghost_root_fixture.h"
static void scene(Fixture &f) {
    configure_root_scene(f,floor_matrix);
    f.put32(f.config.mario_state+0x0c,0x04008448);f.put32(Fixture::body,0x04008448);
}
#if GC_VERSION >= 4
static void begin(Fixture &f,gc_root_probe &probe,uint32_t expected=GC_UNSUPPORTED_ROOT) {
    for(size_t i=0;i<f.n64.size();++i) f.ram[i]=f.n64[i^3];
    ghost_capture_begin(&f.reader,&f.config,&f.frame,&f.sample,&probe);
    check(f.sample.status==expected && !(f.sample.valid_fields&GC_VALID_POSE),"root published before execution");
    check(zero(f.sample.pose),"unqualified root retained pose bytes");
    ++f.frame.sequence;++f.frame.dlist_serial;
}
static void pair(gc_root_probe &p) {
    // Match RSP_LoadMatrix's fixed point conversion, including truncation.
    float m[16];for(unsigned i=0;i<16;++i)m[i]=float(int32_t(floor_matrix[i]*65536))/65536;
    ghost_root_display_list(p,root_dl+8,alpha_dl,alpha_dl,1,m);
    ghost_root_display_list(p,root_dl+24,body_dl,0x40080,1,m);
}
// Mario's executed shadow: captured beside its root, or refused outright.
static bool shadowed(Fixture &f,gc_root_probe &p,const std::function<void()> &execute,bool completed=true) {
    begin(f,p);pair(p);execute();ghost_root_finish(p,completed,f.sample);
    check(!(f.sample.valid_fields&GC_VALID_SHADOW)==zero(f.sample.shadow),"shadow bytes follow validity");
    return (f.sample.valid_fields&GC_VALID_SHADOW)!=0;
}
static void shadows(Fixture &f) {
    gc_root_probe p;
    const auto fresh=[&]{
        scene(f);configure_shadow_scene(f);f.put16(f.config.object_pool+0x42,0x8123); // fresh animation
    };
    const auto normal=[&]{execute_shadow(p,shadow_matrix);};
    fresh();
    check(shadowed(f,p,normal) && (f.sample.valid_fields&GC_VALID_POSE),"Mario's executed shadow captured");
    const auto &s=f.sample.shadow;
    check(s.matrix[12]==130 && s.matrix[14]==-445 && s.matrix[0]==1 && s.matrix[15]==1,"executed shadow matrix kept");
    check(s.position[0][0]==-40 && s.position[8][0]==40 && s.position[3][1]==-5 && s.position[8][2]==-10,"Vtx positions");
    check(s.uv[2][0]==960 && s.uv[6][1]==960 && s.uv[4][0]==480,"Vtx S10.5 texture coordinates");
    check(s.alpha[4]==180 && s.alpha[8]==160 && !s.reserved,"per-vertex solidity");
    check(s.texture[0]==0x07 && s.texture[1]==(uint8_t((14<<4)|5)) && s.texture[127]==uint8_t((((254*7)%16)<<4)|((255*7)%16)),
          "IA8 alpha nibbles packed in texel order");
    // Refusals: each leaves the pose, never a guessed shadow.
    fresh();check(!shadowed(f,p,[]{}) && (f.sample.valid_fields&GC_VALID_POSE),"no executed shadow, no shadow");
    fresh();check(!shadowed(f,p,normal,false),"interrupted list published a shadow");
    fresh();check(!shadowed(f,p,[&]{normal();normal();}),"repeated shadow list accepted");
    fresh();check(!shadowed(f,p,[&]{
        float exact[16];for(unsigned i=0;i<16;++i)exact[i]=float(int32_t(shadow_matrix[i]*65536))/65536;
        ghost_root_display_list(p,root_dl+40,shadow_list,shadow_list,1,exact);
        ghost_root_display_list(p,shadow_list,seg_circle,physical2(seg_circle),2,exact);
        ghost_root_display_list(p,shadow_list+16,seg_tris,physical2(seg_tris),2,exact);}),
        "shadow without its closing call accepted");
    fresh();check(!shadowed(f,p,[&]{execute_shadow(p,shadow_matrix,2);}) ,"shadow at another depth than its nested calls accepted"
        );
    // Another object's shadow: same list shape, not at Mario's allocation.
    fresh();words(f,root_dl+44,{shadow_list+48});words(f,shadow_list+48,{0x06000000,seg_circle,0x04800090,
        Fixture::base|shadow_vtx,0x06000000,seg_tris,0x06000000,seg_end,0xb8000000,0});
    check(!shadowed(f,p,[&]{
        float exact[16];for(unsigned i=0;i<16;++i)exact[i]=float(int32_t(shadow_matrix[i]*65536))/65536;
        ghost_root_display_list(p,root_dl+40,shadow_list+48,shadow_list+48,1,exact);
        ghost_root_display_list(p,shadow_list+48,seg_circle,physical2(seg_circle),2,exact);
        ghost_root_display_list(p,shadow_list+64,seg_tris,physical2(seg_tris),2,exact);
        ghost_root_display_list(p,shadow_list+72,seg_end,physical2(seg_end),2,exact);}),"another object's shadow accepted");
    fresh();words(f,root_dl+36,{0x22200});fixed(f,0x22200,shadow_matrix);
    check(!shadowed(f,p,normal),"shadow matrix outside Mario's allocation accepted");
    fresh();words(f,shadow_list+12,{Fixture::base|(shadow_vtx+16)});
    check(!shadowed(f,p,normal),"vertices outside Mario's allocation accepted");
    fresh();f.put8(Fixture::base+shadow_vtx+16*3+13,0);check(!shadowed(f,p,normal),"coloured shadow vertex accepted");
    fresh();f.put8(Fixture::base+physical2(seg_texture)+9,0x1f);check(!shadowed(f,p,normal),"non-black shadow texel accepted");
    fresh();words(f,physical2(seg_tris)+4,{0x001e32});check(!shadowed(f,p,normal),"different triangle order accepted");
    fresh();words(f,physical2(seg_circle)+52,{0x00050141});check(!shadowed(f,p,normal),"different texture tile accepted");
    fresh();words(f,physical2(seg_begin)+20,{0});check(!shadowed(f,p,normal),"different shadow combiner accepted");
    fresh();words(f,physical2(seg_end)+12,{0});check(!shadowed(f,p,normal),"different shadow restore accepted");
    // A correct triangle list resolved through a different segment base.
    fresh();words(f,physical2(seg_tris)+0x1000,{0xbf000000,0x001e28,0xbf000000,0x00280a,0xbf000000,0x0a2814,
        0xbf000000,0x142832,0xbf000000,0x1e3c28,0xbf000000,0x283c46,0xbf000000,0x284650,
        0xbf000000,0x285032,0xb8000000,0});
    check(!shadowed(f,p,[&]{
        float exact[16];for(unsigned i=0;i<16;++i)exact[i]=float(int32_t(shadow_matrix[i]*65536))/65536;
        ghost_root_display_list(p,root_dl+40,shadow_list,shadow_list,1,exact);
        ghost_root_display_list(p,shadow_list,seg_circle,physical2(seg_circle),2,exact);
        ghost_root_display_list(p,shadow_list+16,seg_tris,physical2(seg_tris)+0x1000,2,exact);
        ghost_root_display_list(p,shadow_list+24,seg_end,physical2(seg_end),2,exact);}),"inconsistent segment base accepted");
    fresh();
    float far[16];std::memcpy(far,shadow_matrix,sizeof(far));far[12]=120+2000;
    fixed(f,shadow_mtx,far);check(!shadowed(f,p,[&]{execute_shadow(p,far);}),"shadow far from Mario accepted");
    fresh();
    float skew[16];std::memcpy(skew,shadow_matrix,sizeof(skew));skew[1]=0.5f;
    fixed(f,shadow_mtx,skew);check(!shadowed(f,p,[&]{execute_shadow(p,skew);}),"non-rigid shadow matrix accepted");
    fresh();begin(f,p);pair(p);pair(p);normal();ghost_root_finish(p,true,f.sample);
    check(!(f.sample.valid_fields&(GC_VALID_POSE|GC_VALID_SHADOW)) && zero(f.sample.shadow),"shadow without a proven root");
    fresh();check(shadowed(f,p,normal),"shadow proof recovers on the next occurrence");
}
#endif
int main(int argc,char **argv) {
    check(argc==2,"usage: ghost_root_host config"); Fixture f(argv[1]);scene(f);
#if GC_VERSION < 4
    check(f.run()==GC_OK,"RED: completed floor-aligned root is refused after throwMatrix was cleared");
#else
    gc_root_probe p;begin(f,p);check(p.prepared,"Mario persistent graph was not qualified");pair(p);
    ghost_root_finish(p,true,f.sample);
    check(f.sample.status==GC_OK && (f.sample.valid_fields&GC_VALID_POSE),"executed floor root not restored");
    check(f.sample.pose.root_kind==GC_ROOT_SCALED_MODELVIEW,"floor root guessed TRS");
    check(f.sample.pose.root_matrix[9]==float(int32_t(floor_matrix[9]*65536))/65536,"fixed matrix was not preserved");
    check(f.sample.pose.scale[1]==1.25f,"graphics scale lost");
    begin(f,p);pair(p);gc_sample preview=f.sample;
    check(ghost_root_candidate(p,preview) && (preview.valid_fields&GC_VALID_POSE),"drawing preview omitted proven root");
    check(!(f.sample.valid_fields&GC_VALID_POSE),"drawing preview changed recording evidence");
    ghost_root_finish(p,false,f.sample);
    check(!(f.sample.valid_fields&GC_VALID_POSE),"preview bypassed completion guard");
    begin(f,p);pair(p);pair(p);ghost_root_finish(p,true,f.sample);
    check(!(f.sample.valid_fields&GC_VALID_POSE) && zero(f.sample.pose),"duplicate owner accepted");
    begin(f,p);pair(p);ghost_root_finish(p,false,f.sample);
    check(!(f.sample.valid_fields&GC_VALID_POSE),"interrupted list published a root");
    begin(f,p);ghost_root_finish(p,true,f.sample);
    check(!(f.sample.valid_fields&GC_VALID_POSE),"old matrix survived new occurrence");
    begin(f,p);pair(p);++f.sample.epoch;ghost_root_finish(p,true,f.sample);
    check(!(f.sample.valid_fields&GC_VALID_POSE),"wrong epoch reused root");
    f.putfloat(f.config.object_pool+0x54,121);begin(f,p);pair(p);ghost_root_finish(p,true,f.sample);
    check(!(f.sample.valid_fields&GC_VALID_POSE),"shared Mario model at another position matched owner");
    scene(f);const auto clone=f.config.object_pool+0x260;
    f.put16(clone+2,1);f.put32(clone+0x14,Fixture::base+0x18000);
    begin(f,p);pair(p);ghost_root_finish(p,true,f.sample);
    check(!(f.sample.valid_fields&GC_VALID_POSE),"second active owner of shared model accepted");
    f.put16(clone+2,0);
    scene(f);f.put32(Fixture::base+root_dl+16,0x01000040);begin(f,p);pair(p);ghost_root_finish(p,true,f.sample);
    check(!(f.sample.valid_fields&GC_VALID_POSE),"matrix multiply accepted as fresh root load");
    scene(f);begin(f,p);
    float exact[16];for(unsigned i=0;i<16;++i)exact[i]=float(int32_t(floor_matrix[i]*65536))/65536;
    ghost_root_display_list(p,root_dl+8,alpha_dl,alpha_dl,1,exact);
    check(p.armed,"depth witness did not first admit alpha");
    ghost_root_display_list(p,root_dl+24,body_dl,0x40080,2,exact);
    ghost_root_finish(p,true,f.sample);
    check(!(f.sample.valid_fields&GC_VALID_POSE),"body at another traversal depth accepted");
    scene(f);f.put32(Fixture::base+root_dl+24,0);begin(f,p);pair(p);ghost_root_finish(p,true,f.sample);
    check(!(f.sample.valid_fields&GC_VALID_POSE),"callback without an executed body command accepted");
    scene(f);
    const auto branch=Fixture::base+0x18100, animation=branch+0x40;
    const auto start=branch+0xc0, near_lod=start+0x40, far_lod=near_lod+0x40, far_part=far_lod+0x40;
    f.put16(branch+0x1e,1);f.put32(animation+8,start);
    f.node(start,0x0a,branch);f.put32(start+8,animation);f.put32(start+16,near_lod);
    f.node(near_lod,0x0b,start);f.put16(near_lod+0x14,uint16_t(-2048));f.put16(near_lod+0x16,400);
    f.put32(near_lod+8,far_lod);
    f.node(far_lod,0x0b,start);f.put16(far_lod+0x14,400);f.put16(far_lod+0x16,1600);
    f.put32(far_lod+8,near_lod);f.put32(far_lod+16,far_part);
    f.node(far_part,0x19,far_lod);f.put32(far_part+0x14,body_dl);
    begin(f,p);pair(p);ghost_root_finish(p,true,f.sample);
    check(f.sample.valid_fields&GC_VALID_POSE,"selected moving/LOD branch did not own its executed body");
    f.put16(branch+0x1e,7);begin(f,p);pair(p);ghost_root_finish(p,true,f.sample);
    check(!(f.sample.valid_fields&GC_VALID_POSE),"unavailable selected model branch accepted");
    scene(f);begin(f,p);
    float wrong[16];std::memcpy(wrong,floor_matrix,sizeof(wrong));wrong[0]=.5f;
    ghost_root_display_list(p,root_dl+8,alpha_dl,alpha_dl,1,wrong);pair(p);
    // A bad preceding candidate cannot poison a subsequent complete proof.
    ghost_root_finish(p,true,f.sample);check(f.sample.valid_fields&GC_VALID_POSE,"valid proof after refused candidate lost");
    scene(f);f.put16(f.config.object_pool+0x42,0);begin(f,p,GC_BAD_VALUE); // source freshness still enforced
    check(!p.prepared,"stale animation admitted root candidate");
    shadows(f);
    std::puts("executed root qualification passed");
#endif
}
