#pragma once
constexpr uint32_t root_dl=0x20000, alpha_dl=0x21000, root_mtx=0x22000, body_mtx=0x22100;
constexpr uint32_t body_dl=0x04000080;
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
