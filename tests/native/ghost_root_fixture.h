#pragma once
#include <cmath>
#include <initializer_list>
constexpr uint32_t root_dl=0x20000, alpha_dl=0x21000, root_mtx=0x22000, body_mtx=0x22100;
constexpr uint32_t body_dl=0x04000080;
// The object initializer selects curAnim through oAnimations; unlike
// set_mario_animation it leaves animID untouched. Table indices are the
// portable model contract; these synthetic headers carry no ROM assets.
inline void object_animation_table(Fixture &f,uint32_t object,uint32_t animation,uint32_t index=1,uint32_t count=2) {
    const uint32_t table=Fixture::base+0x14800;
    f.put32(object+0x120,table);
    for(uint32_t i=0;i<count;++i)f.put32(table+4*i,i==index?animation:animation+0x20u*(i+1));
    f.put32(table+4*count,0);
}
const float floor_matrix[16]={.25f,0,0,0, 0,.25f,.1875f,0, 0,-.225f,.3f,0, 120,60,-450,1};
static void fixed(Fixture &f,uint32_t at,const float *m) {
    for(unsigned i=0;i<16;++i) {
        const auto raw=int32_t(m[i]*65536.0f);
        f.put16(Fixture::base+at+2*i,uint16_t(uint32_t(raw)>>16));
        f.put16(Fixture::base+at+32+2*i,uint16_t(raw));
    }
}
static void configure_root_scene(Fixture &f,const float *matrix) {
    const auto obj=f.config.object_pool;
    f.put16(Fixture::body+8,0); // Ordinary opaque Mario.
    f.put32(obj+0x50,0); // Decomp clears the transient throwMatrix after rendering.
    for(unsigned i=0;i<3;++i)f.putfloat(obj+0x54+4*i,matrix[12+i]);
    const auto shadow=Fixture::base+0x18000, scale=shadow+0x40, cull=scale+0x40;
    const auto alpha=cull+0x40, branch=alpha+0x40, animation=branch+0x40, butt=animation+0x40;
    f.put32(obj+0x14,shadow);
    f.node(shadow,0x28,0);f.put32(shadow+16,scale);
    f.node(scale,0x1c,shadow);f.putfloat(scale+0x18,.25f);f.put32(scale+16,cull);
    f.node(cull,0x12a,scale);f.put32(cull+0x14,Fixture::base+0x100);f.put32(cull+8,alpha);
    f.node(alpha,0x12a,scale);f.put32(alpha+0x14,Fixture::base+0x200);f.put32(alpha+8,branch);
    f.node(branch,0x10c,scale);f.put32(branch+8,cull);f.put32(branch+16,animation);
    f.put16(branch+0x1e,0);
    f.node(animation,0x19,branch);f.put32(animation+16,butt);
    f.node(butt,0x19,animation);f.put32(butt+0x14,body_dl);
    fixed(f,root_mtx,matrix);fixed(f,body_mtx,matrix);
    f.put32(Fixture::base+root_dl,0x01020040);f.put32(Fixture::base+root_dl+4,root_mtx);
    f.put32(Fixture::base+root_dl+8,0x06000000);f.put32(Fixture::base+root_dl+12,alpha_dl);
    f.put32(Fixture::base+root_dl+16,0x01020040);f.put32(Fixture::base+root_dl+20,body_mtx);
    f.put32(Fixture::base+root_dl+24,0x06000000);f.put32(Fixture::base+root_dl+28,body_dl);
    f.put32(Fixture::base+alpha_dl,0xfb000000);f.put32(Fixture::base+alpha_dl+4,0xffffffff);
    f.put32(Fixture::base+alpha_dl+8,0xb8000000);f.put32(Fixture::base+alpha_dl+12,0);
}

/* Mario's shadow as the game allocates and executes it: 9 Vtx, the 5-command
 * list, its G_MTX, the scale G_MTX, then the alpha list (downward pool). The
 * segment-2 lists are the US ROM's own words (decompressed segment2.mio0). */
constexpr uint32_t shadow_list=alpha_dl+144, shadow_mtx=alpha_dl+80, shadow_vtx=alpha_dl+184;
constexpr uint32_t segment2=0x30000;
constexpr uint32_t seg_begin=0x02017380, seg_circle=0x020173a8, seg_tris=0x02017438;
constexpr uint32_t seg_end=0x02017498, seg_texture=0x020120b8;
inline uint32_t physical2(uint32_t segmented) {return segment2+(segmented&0x00ffffff);}
// Pure translation over the camera's (identity) rotation: the shadow is
// 10 units in X and 5 in Z from Mario's cameraToObject (120,60,-450).
const float shadow_matrix[16]={1,0,0,0, 0,1,0,0, 0,0,1,0, 130,60,-445,1};
inline void words(Fixture &f,uint32_t physical,std::initializer_list<uint32_t> values) {
    uint32_t at=Fixture::base+physical;for(auto value:values){f.put32(at,value);at+=4;}
}
inline void configure_shadow_scene(Fixture &f) {
    words(f,root_dl+32,{0x01020040,shadow_mtx,0x06000000,shadow_list});
    fixed(f,shadow_mtx,shadow_matrix);
    words(f,shadow_list,{0x06000000,seg_circle,0x04800090,Fixture::base|shadow_vtx,
        0x06000000,seg_tris,0x06000000,seg_end,0xb8000000,0});
    for(unsigned v=0;v<9;++v) {
        const uint32_t at=Fixture::base+shadow_vtx+16*v;
        f.put16(at,uint16_t(int16_t(v*10-40)));f.put16(at+2,uint16_t(int16_t(-2-int(v))));
        f.put16(at+4,uint16_t(int16_t(30-int(v)*5)));f.put16(at+6,0);
        f.put16(at+8,uint16_t(int16_t((v%3)*480)));f.put16(at+10,uint16_t(int16_t((v/3)*480)));
        f.put8(at+12,255);f.put8(at+13,255);f.put8(at+14,255);f.put8(at+15,uint8_t(v==4?180:v*20));
    }
    words(f,physical2(seg_begin),{0xe7000000,0,0xb6000000,0x00022000,0xfc121824,0xff33ffff,
        0xbb000001,0xffffffff,0xb8000000,0});
    words(f,physical2(seg_circle),{0x06000000,seg_begin,0xfd700000,seg_texture,
        0xf5700000,0x07050140,0xe6000000,0,0xf3000000,0x0707f400,0xe7000000,0,
        0xf5680400,0x00050140,0xf2000000,0x0003c03c,0xb8000000,0});
    words(f,physical2(seg_tris),{0xbf000000,0x001e28,0xbf000000,0x00280a,0xbf000000,0x0a2814,
        0xbf000000,0x142832,0xbf000000,0x1e3c28,0xbf000000,0x283c46,0xbf000000,0x284650,
        0xbf000000,0x285032,0xb8000000,0});
    words(f,physical2(seg_end),{0xe7000000,0,0xbb000000,0xffffffff,0xb7000000,0x00022000,
        0xfcffffff,0xfffe793c,0xb8000000,0});
    for(unsigned i=0;i<256;++i) f.put8(Fixture::base+physical2(seg_texture)+i,uint8_t((i*7)%16));
}
// A shadow shaped like the game's: a 3x3 grid of radius 50 on flat floor at
// Mario's feet, solidity 180 (mario_geo's GEO_SHADOW), texture coordinates
// -15..15 texels over the mirrored quarter circle, and a synthetic
// quarter-circle IA8 alpha ramp (ROM texels are not reproduced here).
inline void realistic_shadow(Fixture &f,const float *matrix) {
    configure_shadow_scene(f);fixed(f,shadow_mtx,matrix);
    for(unsigned v=0;v<9;++v) {
        const uint32_t at=Fixture::base+shadow_vtx+16*v;
        const int column=int(v%3)-1,row=int(v/3)-1;
        f.put16(at,uint16_t(int16_t(column*50)));f.put16(at+2,0);f.put16(at+4,uint16_t(int16_t(row*50)));
        f.put16(at+8,uint16_t(int16_t(column*480)));f.put16(at+10,uint16_t(int16_t(row*480)));f.put8(at+15,180);
    }
    for(unsigned y=0;y<16;++y)for(unsigned x=0;x<16;++x) {
        const float fade=15.0f*(1.0f-std::sqrt(float(x*x+y*y))/15.5f);
        f.put8(Fixture::base+physical2(seg_texture)+16*y+x,uint8_t(fade<0?0:fade>15?15:int(fade+.5f)));
    }
}
#if GC_VERSION >= 5
#include "ghost_root.h"
// The renderer's calls: master list, then the three nested ones (plus the
// circle's own nested begin call, one level deeper).
inline void execute_shadow(gc_root_probe &p,const float *m,uint32_t depth=1) {
    float exact[16];for(unsigned i=0;i<16;++i)exact[i]=float(int32_t(m[i]*65536))/65536;
    ghost_root_display_list(p,root_dl+40,shadow_list,shadow_list,depth,exact);
    ghost_root_display_list(p,shadow_list,seg_circle,physical2(seg_circle),depth+1,exact);
    ghost_root_display_list(p,physical2(seg_circle),seg_begin,physical2(seg_begin),depth+2,exact);
    ghost_root_display_list(p,shadow_list+16,seg_tris,physical2(seg_tris),depth+1,exact);
    ghost_root_display_list(p,shadow_list+24,seg_end,physical2(seg_end),depth+1,exact);
}
#endif
