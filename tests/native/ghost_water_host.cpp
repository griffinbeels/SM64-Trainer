/* Hidden real-driver witness for the translucent world layer: the game's own
 * water draw, mirrored by the renderer seam, blends over a ghost behind it
 * exactly as it blended over the scene behind it. Analytic pixels. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <GL/gl.h>
#include "ghost_compositor_gl.h"
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

namespace gc=ghost_compositor;
namespace cgl=ghost_compositor::gl;
static void require(bool value,const char* what) {if(!value){std::fprintf(stderr,"FAILED: %s (GL=%x)\n",what,glGetError());std::exit(1);}}
static PROC load(const char* name) {
    PROC p=wglGetProcAddress(name);
    if(!p || p==reinterpret_cast<PROC>(1) || p==reinterpret_cast<PROC>(2) || p==reinterpret_cast<PROC>(3) || p==reinterpret_cast<PROC>(-1))
        p=GetProcAddress(GetModuleHandleW(L"opengl32.dll"),name);
    return p;
}
struct Window {
    HWND window=nullptr;HDC dc=nullptr;HGLRC context=nullptr;
    Window() {
        WNDCLASSW cls{};cls.style=CS_OWNDC;cls.lpfnWndProc=DefWindowProcW;cls.hInstance=GetModuleHandleW(nullptr);cls.lpszClassName=L"SM64GhostWaterHidden";
        RegisterClassW(&cls);
        window=CreateWindowExW(WS_EX_NOACTIVATE,cls.lpszClassName,L"",WS_POPUP,0,0,64,64,nullptr,nullptr,cls.hInstance,nullptr);
        require(window && !IsWindowVisible(window),"window hidden");dc=GetDC(window);
        PIXELFORMATDESCRIPTOR pfd{};pfd.nSize=sizeof(pfd);pfd.nVersion=1;pfd.dwFlags=PFD_DRAW_TO_WINDOW|PFD_SUPPORT_OPENGL|PFD_DOUBLEBUFFER;
        pfd.iPixelType=PFD_TYPE_RGBA;pfd.cColorBits=32;pfd.cDepthBits=24;
        int format=ChoosePixelFormat(dc,&pfd);require(format && SetPixelFormat(dc,format,&pfd),"pixel format");
        context=wglCreateContext(dc);require(context && wglMakeCurrent(dc,context),"context current");
        using CreateContext=HGLRC (WINAPI*)(HDC,HGLRC,const int*);
        auto create=reinterpret_cast<CreateContext>(wglGetProcAddress("wglCreateContextAttribsARB"));
        require(create!=nullptr,"core context factory");
        const int attributes[]={0x2091,4,0x2092,3,0x9126,1,0};
        auto core=create(dc,nullptr,attributes);require(core!=nullptr,"core context creation");
        require(wglMakeCurrent(dc,core)!=0,"core context current");wglDeleteContext(context);context=core;
    }
    ~Window() {wglMakeCurrent(nullptr,nullptr);wglDeleteContext(context);ReleaseDC(window,dc);DestroyWindow(window);}
};
static gc::Dispatch dispatch() {
    gc::Dispatch g;
#define GC_LOAD(result,name,args) g.name=reinterpret_cast<decltype(g.name)>(load("gl" #name));require(g.name!=nullptr,"dispatch " #name);
    GC_GL_FUNCTIONS(GC_LOAD)
#undef GC_LOAD
    return g;
}
constexpr float background[3]={20,40,60}, water[3]={0,80,200}, water_alpha=.6f;
// The game's world: far terrain on the right, a near wall on the left.
struct Scene {
    const gc::Dispatch& g;uint32_t width,height,fbo=0,color=0,depth=0;
    Scene(const gc::Dispatch& d,uint32_t w,uint32_t h):g(d),width(w),height(h) {
        std::vector<uint8_t> rgba(w*h*4);std::vector<float> z(w*h);
        for(uint32_t i=0;i<w*h;++i) {
            for(int c=0;c<3;++c)rgba[4*i+c]=uint8_t(background[c]);rgba[4*i+3]=255;
            z[i]=(i%w)<w/2 ? .25f : 1.f;
        }
        g.GenFramebuffers(1,&fbo);g.BindFramebuffer(cgl::FRAMEBUFFER,fbo);
        g.GenTextures(1,&color);g.BindTexture(cgl::TEXTURE_2D,color);
        g.TexImage2D(cgl::TEXTURE_2D,0,cgl::RGBA8,w,h,0,cgl::RGBA,cgl::UNSIGNED_BYTE,rgba.data());
        g.FramebufferTexture2D(cgl::FRAMEBUFFER,cgl::COLOR_ATTACHMENT0,cgl::TEXTURE_2D,color,0);
        g.GenTextures(1,&depth);g.BindTexture(cgl::TEXTURE_2D,depth);
        g.TexImage2D(cgl::TEXTURE_2D,0,cgl::DEPTH_COMPONENT24,w,h,0,cgl::DEPTH_COMPONENT,cgl::FLOAT,z.data());
        g.FramebufferTexture2D(cgl::FRAMEBUFFER,cgl::DEPTH_ATTACHMENT,cgl::TEXTURE_2D,depth,0);
        g.DrawBuffer(cgl::COLOR_ATTACHMENT0);g.ReadBuffer(cgl::COLOR_ATTACHMENT0);
        require(g.CheckFramebufferStatus(cgl::FRAMEBUFFER)==cgl::FRAMEBUFFER_COMPLETE,"scene FBO");
    }
    ~Scene(){g.DeleteFramebuffers(1,&fbo);g.DeleteTextures(1,&color);g.DeleteTextures(1,&depth);}
};
// Stand-in for the renderer's combiner program: premultiplied color and
// alpha with GL blend (ONE, ONE_MINUS_SRC_ALPHA), as the pinned GLideN64
// emits for G_RM_AA_ZB_XLU_SURF. Each "surface" is a full-width band.
struct Water {
    const gc::Dispatch& g;uint32_t program=0,vao=0;int32_t band=-1,shade=-1;
    explicit Water(const gc::Dispatch& d):g(d) {
        const char* vs="#version 430 core\nuniform vec3 band;"
            "const vec2 p[6]=vec2[6](vec2(-1,0),vec2(1,0),vec2(-1,1),vec2(-1,1),vec2(1,0),vec2(1,1));"
            "void main(){vec2 q=p[gl_VertexID];gl_Position=vec4(q.x,mix(band.x,band.y,q.y),band.z,1);}";
        const char* fs="#version 430 core\nuniform vec4 shade;layout(location=0) out vec4 c;void main(){c=vec4(shade.rgb*shade.a,shade.a);}";
        auto vsh=g.CreateShader(cgl::VERTEX_SHADER),fsh=g.CreateShader(cgl::FRAGMENT_SHADER);
        g.ShaderSource(vsh,1,&vs,nullptr);g.ShaderSource(fsh,1,&fs,nullptr);g.CompileShader(vsh);g.CompileShader(fsh);
        program=g.CreateProgram();g.AttachShader(program,vsh);g.AttachShader(program,fsh);g.LinkProgram(program);
        g.DeleteShader(vsh);g.DeleteShader(fsh);int32_t linked=0;g.GetProgramiv(program,cgl::LINK_STATUS,&linked);require(linked!=0,"water program");
        band=g.GetUniformLocation(program,"band");shade=g.GetUniformLocation(program,"shade");
        g.GenVertexArrays(1,&vao);
    }
    ~Water(){g.DeleteProgram(program);g.DeleteVertexArrays(1,&vao);}
    // The game's state for one translucent world draw.
    void state(const Scene& s,uint32_t src=cgl::ONE,uint32_t dst=cgl::ONE_MINUS_SRC_ALPHA) const {
        g.BindFramebuffer(cgl::FRAMEBUFFER,s.fbo);g.Viewport(0,0,s.width,s.height);g.DepthRange(0,1);
        for(auto cap:cgl::enables)g.Disable(cap);g.Disablei(cgl::SCISSOR_TEST,0);
        g.Enable(cgl::DEPTH_TEST);g.DepthFunc(cgl::LESS);g.DepthMask(0);
        g.Enablei(cgl::BLEND,0);g.BlendFuncSeparatei(0,src,dst,src,dst);g.BlendEquationSeparatei(0,cgl::FUNC_ADD,cgl::FUNC_ADD);
        g.ColorMaski(0,1,1,1,1);g.UseProgram(program);g.BindVertexArray(vao);
    }
    void draw(float y0,float y1,float window_depth) const {
        g.Uniform3fv(band,1,std::vector<float>{y0,y1,window_depth*2-1}.data());
        const float c[4]={water[0]/255,water[1]/255,water[2]/255,water_alpha};
        glUniform4fv_(shade,c);g.DrawArrays(cgl::TRIANGLES,0,6);
    }
    using Uniform4fv=void (GC_GL_CALL*)(int32_t,int32_t,const float*);
    Uniform4fv u4=reinterpret_cast<Uniform4fv>(load("glUniform4fv"));
    void glUniform4fv_(int32_t at,const float* v) const {u4(at,1,v);}
};
static std::vector<uint8_t> pixels(const gc::Dispatch& g,uint32_t fbo,uint32_t w,uint32_t h) {
    cgl::Guard guard(g);g.BindFramebuffer(cgl::READ_FRAMEBUFFER,fbo);g.ReadBuffer(cgl::COLOR_ATTACHMENT0);
    std::vector<uint8_t> out(w*h*4);glReadPixels(0,0,w,h,GL_RGBA,GL_UNSIGNED_BYTE,out.data());return out;
}
static void save(const char* name,const std::vector<uint8_t>& rgba,uint32_t w,uint32_t h) {
    const char* folder=std::getenv("SM64_GHOST_WITNESS_IMAGES");if(!folder)return;
    const std::string path=std::string(folder)+"\\"+name+".rgba";
    FILE* f=std::fopen(path.c_str(),"wb");require(f!=nullptr,"image file");
    std::fwrite(&w,4,1,f);std::fwrite(&h,4,1,f);std::fwrite(rgba.data(),1,rgba.size(),f);std::fclose(f);
}
static std::vector<gc::Vertex> ghost_quad(float x0,float x1,float y0,float y1) {
    std::vector<gc::Vertex> out;const float xy[6][2]={{x0,y0},{x1,y0},{x0,y1},{x0,y1},{x1,y0},{x1,y1}};
    for(auto& point:xy){gc::Vertex v{};v.position[0]=point[0];v.position[1]=point[1];v.normal[2]=1;v.color[0]=1;out.push_back(v);}
    return out; // z=0: window depth .5, solid red
}
static gc::Target target(const Scene& s,gc::Depth depth=gc::Depth::ordinary) {
    gc::Target t{};t.occurrence=77;t.generation=1;t.framebuffer=s.fbo;t.width=s.width;t.height=s.height;
    t.depth=depth;t.n64_depth_texture=0;t.samples=1;
    t.viewport[2]=t.scissor[2]=s.width;t.viewport[3]=t.scissor[3]=s.height;t.depth_scale=t.depth_translate=.5f;return t;
}
static gc::Pose pose(const std::vector<gc::Vertex>& v,float opacity) {
    gc::Pose p{};p.vertices=v.data();p.count=uint32_t(v.size());p.ambient=1;p.opacity=opacity;
    p.view_projection[0]=p.view_projection[5]=p.view_projection[10]=p.view_projection[15]=1;return p;
}
// One world frame: the scene, the game's two water surfaces (near .4 over the
// lower half, far .6 over the upper half), each mirrored when asked, then
// the ghost batch. Returns the display; clean receives the game's frame.
struct Frame { std::vector<uint8_t> display, clean; gc::Result near_color, near_depth; };
static Frame frame(const gc::Dispatch& g,gc::Instance* c,const Water& w,uint32_t size,float opacity,bool mirror,
                   bool break_it=false,uint32_t src=cgl::ONE) {
    Scene s(g,size,size);gc::clear_layer(c);
    Frame out{};
    auto surface=[&](float y0,float y1,float z,bool first) {
        w.state(s,src);w.draw(y0,y1,z);
        if(!mirror)return;
        cgl::State before{};before.capture(g);
        for(uint32_t pass=0;pass<2;++pass) {
            const auto result=gc::begin_layer(c,target(s),gc::LayerPass(pass));
            if(first)(pass ? out.near_depth : out.near_color)=result;
            if(result!=gc::Result::ok)break;
            w.draw(y0,y1,z);gc::end_layer(c);
            cgl::State after{};after.capture(g);
            require(std::memcmp(&before,&after,sizeof after)==0,"mirror restores the game's exact state");
        }
    };
    surface(-1,0,.4f,true);surface(0,1,.6f,false);
    if(break_it)gc::break_layer(c);
    for(auto cap:cgl::enables)g.Disable(cap);g.Disablei(cgl::BLEND,0);g.DepthMask(1);
    const auto quad=ghost_quad(0,.5f,-.5f,.5f);auto p=pose(quad,opacity);
    require(gc::begin_batch(c,target(s),&p,1)==gc::Result::ok,"batch");
    require(gc::finalize(c,77)==gc::Result::ok,"finalize");
    out.display=pixels(g,gc::display(c).framebuffer,size,size);out.clean=pixels(g,s.fbo,size,size);
    return out;
}
static float blend(float over,float under,float a) {return over*a+under*(1-a);}
int main() {
    const auto foreground=GetForegroundWindow();
    Window window;auto g=dispatch();Water w(g);
    uint8_t atlas[4]={0,0,0,0};const uint32_t n=32;
    gc::Instance* c=gc::create(g,1);require(c!=nullptr,"create");
    require(gc::prepare(c,n,n,60,atlas,1,1)==gc::Result::ok,"prepare");
    for(float opacity:{1.f,.5f}) {
        const auto layered=frame(g,c,w,n,opacity,true), plain=frame(g,c,w,n,opacity,false);
        require(layered.near_color==gc::Result::ok && layered.near_depth==gc::Result::ok,"translucent draw mirrored");
        require(layered.clean==plain.clean,"mirrors leave the game's own frame bit-identical");
        size_t tinted=0,untouched=0;
        for(uint32_t y=0;y<n;++y)for(uint32_t x=0;x<n;++x) {
            const size_t i=(y*n+x)*4;
            const bool ghost=x>=16 && x<24 && y>=8 && y<24, under=y<16; // near surface covers the lower half
            for(int c3=0;c3<3;++c3) {
                // Right: water over far terrain in both bands; left: the near wall hides it.
                const float clean=x<16 ? background[c3] : blend(water[c3],background[c3],water_alpha);
                const float red=c3==0 ? 255.f : 0.f;
                const float seen=under ? blend(water[c3],red,water_alpha) : red; // behind the near surface only
                const float expected=ghost ? blend(seen,clean,opacity) : clean;
                if(std::abs(float(layered.display[i+c3])-expected)>1.5f)
                    std::fprintf(stderr,"x=%u y=%u c=%d got=%d expected=%.1f clean=%d\n",x,y,c3,layered.display[i+c3],expected,layered.clean[i+c3]);
                require(std::abs(float(layered.display[i+c3])-expected)<=1.5f,"ghost behind water takes the water's blend");
                require(std::abs(float(layered.clean[i+c3])-clean)<=1.5f,"game frame is water over terrain");
            }
            if(!ghost)require(std::memcmp(&layered.display[i],&layered.clean[i],4)==0,"outside the ghost: exactly the game's pixel");
            if(ghost && under){++tinted;require(layered.display[i]<plain.display[i],"without the layer the ghost reads through the water");}
            if(ghost && !under){++untouched;require(std::memcmp(&layered.display[i],&plain.display[i],4)==0,"ghost in front of water is unchanged");}
        }
        require(tinted==64 && untouched==64,"tinted extent");
        char name[32];std::snprintf(name,sizeof name,"layered_%03d",int(opacity*100));save(name,layered.display,n,n);
        std::snprintf(name,sizeof name,"plain_%03d",int(opacity*100));save(name,plain.display,n,n);
    }
    // A depth-writing world draw after the layer opened drops it for the list.
    const auto broken=frame(g,c,w,n,.5f,true,true), plain=frame(g,c,w,n,.5f,false);
    require(broken.display==plain.display,"broken layer draws the ghost as before");
    // Additive (dst ONE) and destination-reading blends never enter the layer.
    const auto additive=frame(g,c,w,n,.5f,true,false,cgl::SRC_ALPHA);
    require(additive.near_color==gc::Result::ok,"SRC_ALPHA source factor accepted");
    {
        Scene s(g,n,n);gc::clear_layer(c);w.state(s,cgl::ONE,cgl::ONE);
        require(gc::begin_layer(c,target(s),gc::LayerPass::color)==gc::Result::unsupported,"additive refused");
        w.state(s,0x304 /*DST_COLOR*/,cgl::ONE_MINUS_SRC_ALPHA);
        require(gc::begin_layer(c,target(s),gc::LayerPass::color)==gc::Result::unsupported,"destination-reading refused");
        w.state(s);g.DepthMask(1);
        require(gc::begin_layer(c,target(s),gc::LayerPass::color)==gc::Result::unsupported,"depth-writing draw refused");
        w.state(s);
        require(gc::begin_layer(c,target(s,gc::Depth::n64_image),gc::LayerPass::color)==gc::Result::unsupported,"N64 image depth refused");
        g.BindFramebuffer(cgl::FRAMEBUFFER,0);
        require(gc::begin_layer(c,target(s),gc::LayerPass::color)==gc::Result::state,"foreign destination refused");
    }
    const auto stats=gc::layer_stats(c);
    require(stats.opened==4 && stats.mirrored==8 && stats.skipped_blend==3 && stats.broken==1 && stats.applied==3,"layer stats");
    gc::destroy(c);
    require(GetForegroundWindow()==foreground,"foreground preserved");
    std::printf("water passed: behind=water_blend front=unchanged outside=exact clean=identical state=restored broken=plain refused=additive,dst,depth,n64,foreign opened=%llu mirrored=%llu skipped=%llu applied=%llu\n",
        (unsigned long long)stats.opened,(unsigned long long)stats.mirrored,(unsigned long long)stats.skipped_blend,(unsigned long long)stats.applied);
    return 0;
}
