/* Hidden real-driver witness for recorded particles: drawn after their ghost's
 * body, hidden behind that body and behind terrain, cutout and blended frames,
 * the clean frame untouched and the renderer's state restored. Analytic pixels. */
#define main compositor_fixture_program
#include "ghost_compositor_host.cpp"
#undef main

// A 2x1 atlas: a mostly opaque green texel (cutout keeps it, fully) and a
// mostly transparent white one (cutout drops it).
static const uint8_t particle_atlas[8]={0,255,0,200, 255,255,255,100};
static gc::ParticleVertex corner(float x,float y,float z,bool transparent,float alpha,bool cutout) {
    gc::ParticleVertex v{};v.position[0]=x;v.position[1]=y;v.position[2]=z;
    const float u=transparent ? .75f : .25f;v.uv[0]=u;v.uv[1]=.5f;
    v.rect[0]=v.rect[2]=u;v.rect[1]=v.rect[3]=.5f;
    v.rgba[0]=v.rgba[1]=v.rgba[2]=1;v.rgba[3]=alpha;v.cutout=cutout ? 1.f : 0.f;return v;
}
static void quad(std::vector<gc::ParticleVertex>& out,float x0,float x1,float y0,float y1,float z,
                 bool transparent,float alpha,bool cutout) {
    const float xy[6][2]={{x0,y0},{x1,y0},{x0,y1},{x0,y1},{x1,y0},{x1,y1}};
    for(auto& p:xy)out.push_back(corner(p[0],p[1],z,transparent,alpha,cutout));
}
struct Expected { float rgb[3]; };
int main() {
    const auto foreground=GetForegroundWindow();
    Window window;auto g=dispatch();Scene scene(g,32,32);Bindings bindings(g);
    uint8_t atlas[4]={0,0,0,0};
    gc::Instance* c=gc::create(g,1);require(c!=nullptr,"create");
    require(gc::prepare(c,32,32,60,atlas,1,1)==gc::Result::ok,"prepare");
    hostile(g,scene,bindings);cgl::State before_prepare{};before_prepare.capture(g);
    require(gc::prepare_particles(c,particle_atlas,2,1)==gc::Result::ok,"particle atlas");
    equal_state(g,before_prepare,bindings);
    ordinary_state(g,scene);const auto clean=pixels(g,scene.fbo,32,32);
    // Body: red quad over pixels x16..23, y8..23 at depth .5 (left half of the
    // screen is a near wall at .25, the right half far terrain).
    std::vector<gc::Vertex> body;
    {
        const float xy[6][2]={{0,-.5f},{.5f,-.5f},{0,.5f},{0,.5f},{.5f,-.5f},{.5f,.5f}};
        for(auto& p:xy){gc::Vertex v{};v.position[0]=p[0];v.position[1]=p[1];v.normal[2]=1;v.color[0]=1;body.push_back(v);}
    }
    std::vector<gc::ParticleVertex> particles;
    quad(particles,.25f,.75f,-.25f,.25f,-.5f,false,1,true);  // A: cutout green, in front: x20..27 y12..19
    quad(particles,-.25f,0,-.25f,.25f,-.5f,true,1,true);     // B: cutout of the thin white texel: nothing
    quad(particles,0,1,.25f,.75f,.5f,false,.5f,false);       // C: blended, BEHIND the body: x16..31 y20..27
    quad(particles,-1,-.5f,-.25f,.25f,0,false,1,true);       // D: behind the near wall: hidden
    for(float opacity:{1.f,.5f}) {
        auto p=pose(body,opacity);
        p.particles.vertices=particles.data();p.particles.count=uint32_t(particles.size());
        p.particles.view_projection[0]=p.particles.view_projection[5]=p.particles.view_projection[10]=p.particles.view_projection[15]=1;
        hostile(g,scene,bindings);cgl::State before{};before.capture(g);
        require(gc::begin_batch(c,target(scene,gc::Depth::ordinary),&p,1)==gc::Result::ok,"batch");
        equal_state(g,before,bindings);
        require(gc::finalize(c,77)==gc::Result::ok,"finalize");ordinary_state(g,scene);
        const auto image=pixels(g,gc::display(c).framebuffer,32,32);
        require(pixels(g,scene.fbo,32,32)==clean,"clean frame untouched");
        const float bg[3]={20,40,60},red[3]={255,0,0},green[3]={0,255,0};
        for(uint32_t y=0;y<32;++y)for(uint32_t x=0;x<32;++x) {
            float rgb[3]={bg[0],bg[1],bg[2]};
            const bool on_body=x>=16 && x<24 && y>=8 && y<24;
            if(on_body)for(int k=0;k<3;++k)rgb[k]=red[k]*opacity+rgb[k]*(1-opacity);
            if(x>=20 && x<28 && y>=12 && y<20)for(int k=0;k<3;++k)rgb[k]=green[k]*opacity+rgb[k]*(1-opacity);
            // C samples the green texel too, at vertex alpha .5, only off the body.
            const float a=200.f/255*.5f*opacity;
            if(x>=16 && y>=20 && y<28 && !on_body)for(int k=0;k<3;++k)rgb[k]=green[k]*a+rgb[k]*(1-a);
            for(int k=0;k<3;++k) {
                const float got=image[(y*32+x)*4+k];
                if(std::abs(got-rgb[k])>1.6f)
                    std::fprintf(stderr,"x=%u y=%u c=%d got=%.0f expected=%.1f opacity=%.1f\n",x,y,k,got,rgb[k],opacity);
                require(std::abs(got-rgb[k])<=1.6f,"particles: cutout, blend, body and terrain occlusion");
            }
        }
    }
    auto bad=pose(body,1);std::vector<gc::ParticleVertex> partial(particles.begin(),particles.begin()+4);
    bad.particles.vertices=partial.data();bad.particles.count=4;
    require(gc::begin_batch(c,target(scene,gc::Depth::ordinary),&bad,1)==gc::Result::invalid,"partial particle triangle refused");
    gc::destroy(c);
    require(GetForegroundWindow()==foreground,"foreground preserved");
    std::puts("particles passed: cutout=texels blend=alpha behind_body=hidden behind_terrain=hidden clean=unchanged state=restored");
    return 0;
}
