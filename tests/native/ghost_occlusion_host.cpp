/* Player-first fade on the real driver: reuse the hidden-WGL fixture and run
 * only occlusion cases. A flat red ghost at a known eye depth under a real
 * perspective (clip w = distance ahead of the camera); a marked live-Mario
 * region on screen; every pixel checked against the rule's own formula. */
#define main compositor_fixture_main
#include "ghost_compositor_host.cpp"
#undef main

namespace {
constexpr uint32_t W=64,H=64;
constexpr float NEAR_PLANE=10,FAR_PLANE=10000;
// The camera at the origin looking down -z: clip = (x, y, a z + b, -z).
void perspective(float m[16]) {
    std::memset(m,0,16*sizeof(float));
    m[0]=m[5]=1;m[10]=-(FAR_PLANE+NEAR_PLANE)/(FAR_PLANE-NEAR_PLANE);m[11]=-1;
    m[14]=-2*FAR_PLANE*NEAR_PLANE/(FAR_PLANE-NEAR_PLANE);
}
// The right half of the screen (the fixture's left half is a nearer wall),
// flat at eye depth d, so every fragment's clip w is exactly d.
std::vector<gc::Vertex> plane(float d,float x0=0,float x1=1) {
    const float xy[6][2]={{x0,-1},{x1,-1},{x0,1},{x0,1},{x1,-1},{x1,1}};
    std::vector<gc::Vertex> out;
    for(auto& p:xy) {gc::Vertex v{};v.position[0]=p[0]*d;v.position[1]=p[1]*d;v.position[2]=-d;
        v.normal[2]=1;v.color[0]=1;out.push_back(v);}
    return out;
}
gc::Pose ghost(const std::vector<gc::Vertex>& v,float opacity,const ghost_occlusion::Region& region) {
    gc::Pose p{};p.vertices=v.data();p.count=uint32_t(v.size());p.ambient=1;p.opacity=opacity;
    perspective(p.view_projection);p.occlusion=region;return p;
}
// Live Mario's marked region: centred in the ghost's half, 8 px radius.
ghost_occlusion::Region player(float w) {
    ghost_occlusion::Region r{};r.enabled=r.has_player=1;
    r.center[0]=.5f;r.center[1]=0;r.radius[0]=r.radius[1]=.25f;r.player_w=w;return r;
}
float smooth(float e0,float e1,float x) {
    const float t=(std::min)(1.f,(std::max)(0.f,(x-e0)/(e1-e0)));return t*t*(3-2*t);
}
// The test's own statement of the documented rule, at a pixel centre.
float expected_fade(const ghost_occlusion::Region& r,uint32_t x,uint32_t y,float w) {
    using namespace ghost_occlusion;
    if(!r.enabled)return 1;
    float fade=smooth(NEAR_HIDDEN,NEAR_FULL,w);
    if(r.has_player) {
        const float nx=(2*(x+.5f)/W-1-r.center[0])/r.radius[0],ny=(2*(y+.5f)/H-1-r.center[1])/r.radius[1];
        const float cover=1-smooth(REGION_INNER,REGION_OUTER,std::sqrt(nx*nx+ny*ny));
        fade*=1-PLAYER_STRENGTH*cover*smooth(0,FRONT_BAND,r.player_w-w);
    }
    return fade;
}
struct Tally {unsigned faded=0,exact=0,full=0;float worst=0;};
// Every pixel: the ghost's half blends red at opacity x fade, the wall half
// stays clean. Returns how many were faded, exact and fully faded.
Tally compare(const std::vector<uint8_t>& image,const std::vector<uint8_t>& clean,
              const ghost_occlusion::Region& r,float opacity,float w,bool shadow=false) {
    Tally tally;
    for(uint32_t y=0;y<H;++y)for(uint32_t x=0;x<W;++x) {
        const size_t i=(y*W+x)*4;
        if(x<W/2) {for(int c=0;c<4;++c)require(image[i+c]==clean[i+c],"wall half stays clean");continue;}
        const float fade=expected_fade(r,x,y,w),a=opacity*fade;
        const float colour[3]={shadow?0.f:255.f,0,0};
        for(int c=0;c<3;++c) {
            const float want=clean[i+c]*(1-a)+colour[c]*a,error=std::abs(float(image[i+c])-want);
            tally.worst=(std::max)(tally.worst,error);
            if(error>1.01f)std::fprintf(stderr,"pixel %u,%u channel %d: %u, want %.2f (fade %.4f)\n",x,y,c,image[i+c],want,fade);
            require(error<=1.01f,"pixel matches the player-first rule");
        }
        if(fade<1)++tally.faded;else ++tally.exact;
        if(std::abs(fade-(1-ghost_occlusion::PLAYER_STRENGTH))<1e-6f)++tally.full;
    }
    return tally;
}
std::vector<uint8_t> draw(const gc::Dispatch& g,gc::Instance* compositor,const Scene& scene,
                          const gc::Pose& p,gc::Depth mode=gc::Depth::ordinary) {
    auto t=target(scene,mode);
    require(gc::begin(compositor,t,p)==gc::Result::ok,"occlusion begin");
    require(gc::finalize(compositor,77)==gc::Result::ok,"occlusion finalize");
    ordinary_state(g,scene);return pixels(g,gc::display(compositor).framebuffer,W,H);
}
}

int main() {
    const auto foreground=GetForegroundWindow();
    Window window;auto g=dispatch();Scene scene(g,W,H);
    ordinary_state(g,scene);const auto clean=pixels(g,scene.fbo,W,H);
    const auto z=depth(g,scene),n64z=image_depth(g,scene);
    auto* compositor=gc::create(g,1);require(compositor!=nullptr,"occlusion create");
    const uint8_t atlas[4]={0,0,0,0};
    require(gc::prepare(compositor,W,H,60,atlas,1,1)==gc::Result::ok,"occlusion prepare");
    const auto body=plane(1000);const float opacity=.5f;
    const ghost_occlusion::Region off{};
    for(auto mode:{gc::Depth::ordinary,gc::Depth::n64_image}) {
        // Off: exactly the former image.
        const auto before=draw(g,compositor,scene,ghost(body,opacity,off),mode);
        const auto t0=compare(before,clean,off,opacity,1000);
        require(t0.faded==0 && t0.exact==W*H/2,"switch off draws every ghost pixel as before");
        // In front of live Mario (ghost 1000 ahead, Mario 2000): faded over him only.
        const auto front=player(2000);
        const auto over=draw(g,compositor,scene,ghost(body,opacity,front),mode);
        const auto t1=compare(over,clean,front,opacity,1000);
        require(t1.full>=150 && t1.faded>t1.full && t1.exact>0,"front ghost fades over Mario, smoothly at the rim");
        for(uint32_t y=0;y<H;++y)for(uint32_t x=W/2;x<W;++x) {
            const float nx=(2*(x+.5f)/W-1-.5f)/.25f,ny=(2*(y+.5f)/H-1)/.25f;
            if(std::sqrt(nx*nx+ny*ny)>=ghost_occlusion::REGION_OUTER)
                for(int c=0;c<4;++c)require(over[(y*W+x)*4+c]==before[(y*W+x)*4+c],"away from Mario stays byte-exact");
        }
        const size_t centre=(32*W+48)*4;
        require(std::abs(int(over[centre])-int(std::lround(20*(1-.075f)+255*.075f)))<=1,
            "over his core the ghost keeps 15% of its opacity");
        // Behind live Mario (Mario 500 ahead, ghost 1000): untouched.
        const auto behind=draw(g,compositor,scene,ghost(body,opacity,player(500)),mode);
        require(behind==before,"a ghost behind Mario is byte-exact");
        // Half the front band: partial fade.
        const auto band=player(1000+ghost_occlusion::FRONT_BAND/2);
        compare(draw(g,compositor,scene,ghost(body,opacity,band),mode),clean,band,opacity,1000);
        std::printf("mode %u: faded=%u full=%u exact=%u worst=%.2f\n",unsigned(mode),t1.faded,t1.full,t1.exact,t1.worst);
    }
    // The renderer's y flip moves the ghost and the region together: the
    // region is in the ghost's unflipped clip space, so a flipped frame is the
    // unflipped one mirrored top to bottom.
    {
        auto high=player(2000);high.center[1]=.25f;
        const auto upright=draw(g,compositor,scene,ghost(body,opacity,high));
        auto flipped_pose=ghost(body,opacity,high);flipped_pose.flip_y=1;
        const auto flipped=draw(g,compositor,scene,flipped_pose);
        require(upright!=draw(g,compositor,scene,ghost(body,opacity,player(2000))),"off-centre region changes the image");
        for(uint32_t y=0;y<H;++y)for(uint32_t x=0;x<W;++x)for(int c=0;c<4;++c)
            require(flipped[(y*W+x)*4+c]==upright[((H-1-y)*W+x)*4+c],"flip mirrors the ghost and its region together");
    }
    // Near the camera: gone inside NEAR_HIDDEN, half way between, whole at NEAR_FULL.
    ghost_occlusion::Region camera{};camera.enabled=1;
    const auto touching=plane(100),middle=plane(300),whole=plane(ghost_occlusion::NEAR_FULL);
    require(draw(g,compositor,scene,ghost(touching,1,camera))==clean,"a ghost at the camera is invisible");
    const auto half=draw(g,compositor,scene,ghost(middle,1,camera));
    const auto th=compare(half,clean,camera,1,300);
    require(th.faded==W*H/2 && std::abs(int(half[(32*W+48)*4])-int(std::lround(20*.5f+255*.5f)))<=1,"half way fades by half");
    require(draw(g,compositor,scene,ghost(whole,1,camera))==draw(g,compositor,scene,ghost(whole,1,off)),
        "from NEAR_FULL the camera fade is exact");
    // The shadow follows the same rule: a black decal over Mario's region
    // fades like the body; the body itself sits off screen.
    const auto offscreen=plane(1000,3,4);
    std::vector<gc::ShadowVertex> decal;
    for(const auto& v:plane(1000)) {gc::ShadowVertex s{};std::memcpy(s.position,v.position,sizeof(s.position));
        s.uv[0]=s.uv[1]=.5f;s.alpha=1;decal.push_back(s);}
    uint8_t ia8[256];std::memset(ia8,15,sizeof(ia8));
    auto shaded=[&](const ghost_occlusion::Region& r) {
        auto p=ghost(offscreen,1,r);p.shadow.vertices=decal.data();p.shadow.count=uint32_t(decal.size());
        p.shadow.texture_ia8=ia8;perspective(p.shadow.view_projection);return p;
    };
    const auto plain=draw(g,compositor,scene,shaded(off));
    require(compare(plain,clean,off,1,1000,true).exact==W*H/2,"shadow switch off: solid black decal");
    const auto front=player(2000);
    const auto ts=compare(draw(g,compositor,scene,shaded(front)),clean,front,1,1000,true);
    require(ts.full>=150 && ts.exact>0,"the shadow fades over Mario like its body");
    require(pixels(g,scene.fbo,W,H)==clean,"clean game RGB exact");
    require(depth(g,scene)==z && image_depth(g,scene)==n64z,"both game depth representations exact");
    gc::destroy(compositor);no_error("occlusion shutdown");require(GetForegroundWindow()==foreground,"occlusion focus unchanged");
    std::printf("occlusion passed: hidden=yes front=faded_over_mario behind=exact away=exact camera=hidden,half,exact "
                "shadow=follows depth=ordinary,n64_image clean=exact\n");
    return 0;
}
