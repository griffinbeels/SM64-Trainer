/* Hidden real-driver witness. Analytic pixels, no model library or emulator. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <GL/gl.h>
#include "ghost_compositor_gl.h"
#include "renderer_pixel_format.h"
#include "practice_rom_fixture.h"
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include <cmath>
#include <limits>
#include <type_traits>

namespace gc=ghost_compositor;
namespace cgl=ghost_compositor::gl;
static bool repair_window_format=false;
static int closest_format=0;
static int WINAPI closest_choice(HDC,const PIXELFORMATDESCRIPTOR*){return closest_format;}
static void require(bool value,const char* what) {if(!value){std::fprintf(stderr,"FAILED: %s (GL=%x)\n",what,glGetError());std::exit(1);}}
static void no_error(const char* what) {
    const auto error=glGetError();
    if(error)std::fprintf(stderr,"GL error %x at %s\n",error,what);
    require(error==GL_NO_ERROR,what);
}
static void GC_GL_CALL debug_message(uint32_t,uint32_t type,uint32_t,uint32_t,int32_t,const char* message,const void*) {
    if(type==0x824C)std::fprintf(stderr,"GL driver: %s\n",message);
}
static PROC load(const char* name) {
    PROC p=wglGetProcAddress(name);
    if(!p || p==reinterpret_cast<PROC>(1) || p==reinterpret_cast<PROC>(2) || p==reinterpret_cast<PROC>(3) || p==reinterpret_cast<PROC>(-1))
        p=GetProcAddress(GetModuleHandleW(L"opengl32.dll"),name);
    return p;
}
struct Window {
    HWND window=nullptr;HDC dc=nullptr;HGLRC context=nullptr;
    Window() {
        WNDCLASSW cls{};cls.style=CS_OWNDC;cls.lpfnWndProc=DefWindowProcW;cls.hInstance=GetModuleHandleW(nullptr);cls.lpszClassName=L"SM64GhostCompositorHidden";
        RegisterClassW(&cls);
        window=CreateWindowExW(WS_EX_NOACTIVATE,cls.lpszClassName,L"",WS_POPUP,0,0,64,64,nullptr,nullptr,cls.hInstance,nullptr);
        require(window && !IsWindowVisible(window),"window hidden");dc=GetDC(window);
        PIXELFORMATDESCRIPTOR pfd{};pfd.nSize=sizeof(pfd);pfd.nVersion=1;pfd.dwFlags=PFD_DRAW_TO_WINDOW|PFD_SUPPORT_OPENGL|PFD_DOUBLEBUFFER;
        pfd.iPixelType=PFD_TYPE_RGBA;pfd.cColorBits=32;pfd.cDepthBits=24;pfd.cStencilBits=8;
        int format=ChoosePixelFormat(dc,&pfd);
        if(repair_window_format){
            PIXELFORMATDESCRIPTOR candidate{};const int count=DescribePixelFormat(dc,1,sizeof(candidate),&candidate);
            closest_format=0;
            for(int index=1;index<=count && index<=4096;++index){
                if(!DescribePixelFormat(dc,index,sizeof(candidate),&candidate))continue;
                const DWORD flags=PFD_DRAW_TO_WINDOW|PFD_SUPPORT_OPENGL|PFD_DOUBLEBUFFER;
                if((candidate.dwFlags&flags)==flags && !(candidate.dwFlags&(PFD_GENERIC_FORMAT|PFD_STEREO))
                        && candidate.iPixelType==PFD_TYPE_RGBA && candidate.cRedBits && candidate.cGreenBits && candidate.cBlueBits
                        && !renderer_pixel_format::rgb8(candidate)){closest_format=index;break;}
            }
            require(closest_format!=0,"real driver exposes non-RGB8 closest choice");
            unsigned char rom[0x40];PRACTICE_FIXTURE_USAMUNE(rom);
            format=renderer_pixel_format::choose(rom,dc,&pfd,closest_choice);
            if(format==closest_format){
                DescribePixelFormat(dc,format,sizeof(candidate),&candidate);
                std::fprintf(stderr,"unreplaced format=%d rgb=%u/%u/%u alpha=%u depth=%u stencil=%u flags=%lu\n",
                    format,candidate.cRedBits,candidate.cGreenBits,candidate.cBlueBits,candidate.cAlphaBits,
                    candidate.cDepthBits,candidate.cStencilBits,candidate.dwFlags);
            }
            require(DescribePixelFormat(dc,format,sizeof(candidate),&candidate)!=0
                    && renderer_pixel_format::rgb8(candidate),"RGB8 selection replaces unsupported closest choice");
        }
        require(format && SetPixelFormat(dc,format,&pfd),"pixel format");
        context=wglCreateContext(dc);require(context && wglMakeCurrent(dc,context),"context current");
        using CreateContext=HGLRC (WINAPI*)(HDC,HGLRC,const int*);
        auto create=reinterpret_cast<CreateContext>(wglGetProcAddress("wglCreateContextAttribsARB"));
        require(create!=nullptr,"core context factory");
        GLint major=0,minor=0;glGetIntegerv(cgl::MAJOR_VERSION,&major);glGetIntegerv(cgl::MINOR_VERSION,&minor);
        const int attributes[]={0x2091,major,0x2092,minor,0x9126,1,0};
        auto core=create(dc,nullptr,attributes);require(core!=nullptr,"core context creation");
        require(wglMakeCurrent(dc,core)!=0,"core context current");wglDeleteContext(context);context=core;
        GLint profile=0;glGetIntegerv(0x9126,&profile);require((profile&1)!=0,"actual CORE profile");
    }
    ~Window() {wglMakeCurrent(nullptr,nullptr);wglDeleteContext(context);ReleaseDC(window,dc);DestroyWindow(window);}
};
static gc::Dispatch dispatch() {
    gc::Dispatch g;
#define GC_LOAD(result,name,args) g.name=reinterpret_cast<decltype(g.name)>(load("gl" #name));require(g.name!=nullptr,"dispatch " #name);
    GC_GL_FUNCTIONS(GC_LOAD)
#undef GC_LOAD
#define GC_LOAD_OPTIONAL(result,name,args) g.name=reinterpret_cast<decltype(g.name)>(load("gl" #name));
    GC_GL_OPTIONAL(GC_LOAD_OPTIONAL)
#undef GC_LOAD_OPTIONAL
    using DebugCallback=void (GC_GL_CALL*)(decltype(&debug_message),const void*);
    auto callback=reinterpret_cast<DebugCallback>(load("glDebugMessageCallback"));
    require(callback!=nullptr,"debug dispatch");callback(debug_message,nullptr);
    g.Enable(0x92E0);g.Enable(0x8242); // GL_DEBUG_OUTPUT, synchronous diagnostic callback
    return g;
}
struct Scene {
    const gc::Dispatch& g;uint32_t width,height,fbo=0,color=0,depth=0,image=0;
    Scene(const gc::Dispatch& d,uint32_t w,uint32_t h):g(d),width(w),height(h) {
        std::vector<uint8_t> rgba(w*h*4);std::vector<float> z(w*h);
        for(uint32_t y=0;y<h;++y)for(uint32_t x=0;x<w;++x) {
            const auto i=y*w+x;rgba[4*i]=20;rgba[4*i+1]=40;rgba[4*i+2]=60;rgba[4*i+3]=255;
            z[i]=x<w/2 ? .25f : 1.f;
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
        g.GenTextures(1,&image);g.BindTexture(cgl::TEXTURE_2D,image);
        g.TexImage2D(cgl::TEXTURE_2D,0,0x822E,w,h,0,0x1903,cgl::FLOAT,z.data()); // R32F
        g.TexParameteri(cgl::TEXTURE_2D,cgl::TEXTURE_MIN_FILTER,cgl::NEAREST);
        g.TexParameteri(cgl::TEXTURE_2D,cgl::TEXTURE_MAG_FILTER,cgl::NEAREST);
        no_error("scene setup");
    }
    ~Scene(){g.DeleteFramebuffers(1,&fbo);g.DeleteTextures(1,&color);g.DeleteTextures(1,&depth);g.DeleteTextures(1,&image);}
};
struct Bindings {
    const gc::Dispatch& g;uint32_t vao=0,buffers[3]={},samplers[2]={},program=0,hud_program=0;
    using Samplers=void (GC_GL_CALL*)(int32_t,uint32_t*);
    explicit Bindings(const gc::Dispatch& dispatch):g(dispatch) {
        g.GenVertexArrays(1,&vao);g.BindVertexArray(vao);g.GenBuffers(3,buffers);
        for(uint32_t i=0;i<3;++i) {
            const uint32_t target=i==0 ? cgl::ARRAY_BUFFER : i==1 ? 0x8893 : cgl::PIXEL_UNPACK_BUFFER;
            g.BindBuffer(target,buffers[i]);g.BufferData(target,512,nullptr,cgl::DYNAMIC_DRAW);
        }
        auto generate=reinterpret_cast<Samplers>(load("glGenSamplers"));require(generate!=nullptr,"sampler dispatch");generate(2,samplers);
        const char* vs_source="#version 430 core\nvoid main(){gl_Position=vec4(0,0,0,1);}";
        const char* fs_source="#version 430 core\nlayout(location=0) out vec4 c;void main(){c=vec4(1);}";
        auto compile=[&](const char* vertex,const char* fragment) {
            auto vs=g.CreateShader(cgl::VERTEX_SHADER),fs=g.CreateShader(cgl::FRAGMENT_SHADER);
            g.ShaderSource(vs,1,&vertex,nullptr);g.ShaderSource(fs,1,&fragment,nullptr);g.CompileShader(vs);g.CompileShader(fs);
            auto result=g.CreateProgram();g.AttachShader(result,vs);g.AttachShader(result,fs);g.LinkProgram(result);
            g.DeleteShader(vs);g.DeleteShader(fs);int32_t linked=0;g.GetProgramiv(result,cgl::LINK_STATUS,&linked);require(linked!=0,"host program link");return result;
        };
        program=compile(vs_source,fs_source);
        hud_program=compile("#version 430 core\nconst vec2 p[6]=vec2[6](vec2(0,-.5),vec2(.5,-.5),vec2(0,.5),vec2(0,.5),vec2(.5,-.5),vec2(.5,.5));void main(){gl_Position=vec4(p[gl_VertexID],0,1);}",
            "#version 430 core\nuniform float alpha;layout(location=0) out vec4 c;void main(){c=vec4(vec3(20,40,60)/255.0,alpha);}");
        g.BindBuffer(cgl::PIXEL_UNPACK_BUFFER,0);no_error("hostile resources");
    }
    ~Bindings() {
        g.UseProgram(0);g.BindVertexArray(0);g.BindSampler(0,0);g.BindSampler(1,0);
        auto remove=reinterpret_cast<Samplers>(load("glDeleteSamplers"));remove(2,samplers);
        g.DeleteProgram(program);g.DeleteProgram(hud_program);g.DeleteBuffers(3,buffers);g.DeleteVertexArrays(1,&vao);
    }
};
static std::vector<uint8_t> pixels(const gc::Dispatch& g,uint32_t fbo,uint32_t w,uint32_t h) {
    cgl::Guard guard(g);g.BindFramebuffer(cgl::READ_FRAMEBUFFER,fbo);g.ReadBuffer(cgl::COLOR_ATTACHMENT0);
    std::vector<uint8_t> out(w*h*4);glReadPixels(0,0,w,h,GL_RGBA,GL_UNSIGNED_BYTE,out.data());no_error("pixels");return out;
}
static std::vector<float> depth(const gc::Dispatch& g,const Scene& s) {
    cgl::Guard guard(g);g.BindFramebuffer(cgl::READ_FRAMEBUFFER,s.fbo);
    std::vector<float> out(s.width*s.height);glReadPixels(0,0,s.width,s.height,GL_DEPTH_COMPONENT,GL_FLOAT,out.data());no_error("depth read");return out;
}
static std::vector<float> image_depth(const gc::Dispatch& g,const Scene& s) {
    cgl::Guard guard(g);g.ActiveTexture(cgl::TEXTURE0);g.BindTexture(cgl::TEXTURE_2D,s.image);
    std::vector<float> out(s.width*s.height);glGetTexImage(GL_TEXTURE_2D,0,0x1903,GL_FLOAT,out.data());
    no_error("image depth read");return out;
}
static std::vector<gc::Vertex> mesh() {
    std::vector<gc::Vertex> out;
    const float xy[6][2]={{-.5f,-.5f},{.5f,-.5f},{-.5f,.5f},{-.5f,.5f},{.5f,-.5f},{.5f,.5f}};
    for(int layer=0;layer<3;++layer)for(auto& point:xy) {
        gc::Vertex v{};v.position[0]=point[0];v.position[1]=point[1];v.position[2]=layer==0 ?.5f:0.f;
        v.normal[2]=1;v.color[layer==0 ? 2:0]=1;v.uv[0]=v.uv[1]=1;out.push_back(v);
    }
    return out;
}
static gc::Target target(const Scene& s,gc::Depth depth,uint64_t generation=1) {
    gc::Target t{};t.occurrence=77;t.generation=generation;t.framebuffer=s.fbo;t.width=s.width;t.height=s.height;
    t.depth=depth;t.n64_depth_texture=s.image;t.samples=1;
    t.viewport[2]=t.scissor[2]=s.width;t.viewport[3]=t.scissor[3]=s.height;t.depth_scale=t.depth_translate=.5f;
    t.attachments=(uint64_t(s.color)<<32)|s.depth;return t;
}
static gc::Pose pose(const std::vector<gc::Vertex>& vertices,float opacity) {
    gc::Pose p{};p.vertices=vertices.data();p.count=uint32_t(vertices.size());p.ambient=1;p.opacity=opacity;
    p.view_projection[0]=p.view_projection[5]=p.view_projection[10]=p.view_projection[15]=1;return p;
}
static void hostile(const gc::Dispatch& g,const Scene& scene,const Bindings& bindings) {
    g.UseProgram(bindings.program);g.BindVertexArray(bindings.vao);g.BindBuffer(cgl::ARRAY_BUFFER,bindings.buffers[0]);
    g.BindBuffer(cgl::PIXEL_UNPACK_BUFFER,bindings.buffers[2]);g.BindSampler(0,bindings.samplers[0]);g.BindSampler(1,bindings.samplers[1]);
    g.BindFramebuffer(cgl::FRAMEBUFFER,scene.fbo);g.ReadBuffer(GL_NONE);
    g.ActiveTexture(cgl::TEXTURE0);g.BindTexture(cgl::TEXTURE_2D,scene.color);
    g.ActiveTexture(cgl::TEXTURE0+1);g.BindTexture(cgl::TEXTURE_2D,scene.image);
    // The compositor's own units and upload target hold something of the renderer's too.
    g.ActiveTexture(cgl::TEXTURE0+cgl::unit_base+1);g.BindTexture(cgl::TEXTURE_2D,scene.color);
    g.BindSampler(cgl::unit_base+2,bindings.samplers[0]);g.BindBuffer(cgl::COPY_WRITE_BUFFER,bindings.buffers[0]);
    g.ActiveTexture(cgl::TEXTURE0+3);
    g.Viewport(1,2,7,9);g.Scissor(3,4,2,3);g.DepthRange(.123456789,.876543219);
    g.ViewportIndexedf(0,1.5f,2.5f,7.5f,9.5f);
    g.ViewportIndexedf(1,11.f,12.f,13.f,14.f);g.ScissorIndexed(1,15,16,17,18);
    g.DepthRangeIndexed(1,.2,.8);g.Enablei(GL_SCISSOR_TEST,0);g.Enablei(GL_SCISSOR_TEST,1);
    g.DepthFunc(GL_GREATER);g.DepthMask(0);g.PolygonMode(GL_FRONT_AND_BACK,GL_LINE);
    g.ColorMaski(0,1,0,1,0);g.ColorMaski(1,0,1,0,1);
    g.Enablei(GL_BLEND,0);g.Enablei(GL_BLEND,1);
    g.BlendFuncSeparatei(0,GL_ONE,GL_ZERO,GL_DST_ALPHA,GL_ONE_MINUS_DST_ALPHA);
    g.BlendFuncSeparatei(1,GL_DST_COLOR,GL_SRC_COLOR,GL_ONE,GL_ZERO);
    g.BlendEquationSeparatei(0,0x800A,0x800B);g.BlendEquationSeparatei(1,cgl::FUNC_ADD,cgl::FUNC_ADD);
    g.StencilFuncSeparate(GL_FRONT,GL_NEVER,5,0x35);g.StencilMaskSeparate(GL_FRONT,0x55);
    g.StencilFuncSeparate(GL_BACK,GL_LEQUAL,7,0x67);g.StencilMaskSeparate(GL_BACK,0xAA);
    g.StencilOpSeparate(GL_FRONT,GL_REPLACE,GL_INCR,GL_DECR);g.StencilOpSeparate(GL_BACK,GL_INVERT,GL_KEEP,GL_ZERO);
    for(auto cap:cgl::enables)g.Enable(cap);
    g.PixelStorei(GL_UNPACK_ALIGNMENT,8);g.PixelStorei(GL_UNPACK_ROW_LENGTH,2);g.PixelStorei(GL_UNPACK_SKIP_ROWS,1);g.PixelStorei(GL_UNPACK_SKIP_PIXELS,1);
    no_error("hostile state");
}
static void equal_state(const gc::Dispatch& g,const cgl::State& before,const Bindings& bindings) {
    cgl::State after{};after.capture(g);require(std::memcmp(&before,&after,sizeof(after))==0,"complete touched state restored");
    uint8_t mask[4]{};g.GetBooleani_v(GL_COLOR_WRITEMASK,1,mask);
    require(mask[0]==0 && mask[1]==1 && mask[2]==0 && mask[3]==1,"independent MRT color mask unchanged");
    int32_t factor=0;g.GetIntegeri_v(cgl::BLEND_SRC_RGB,1,&factor);require(factor==GL_DST_COLOR,"independent MRT blending unchanged");
    float other_viewport[4]{};int32_t other_scissor[4]{};double other_depth[2]{};uint8_t scissor=0;
    g.GetFloati_v(GL_VIEWPORT,1,other_viewport);g.GetIntegeri_v(GL_SCISSOR_BOX,1,other_scissor);
    g.GetDoublei_v(GL_DEPTH_RANGE,1,other_depth);g.GetBooleani_v(GL_SCISSOR_TEST,1,&scissor);
    require(other_viewport[0]==11 && other_viewport[3]==14 && other_scissor[0]==15 && other_scissor[3]==18 &&
        std::abs(other_depth[0]-.2)<1e-6 && std::abs(other_depth[1]-.8)<1e-6 && scissor,"other viewport state unchanged");
    int32_t element=0;g.GetIntegerv(0x8895,&element);require(uint32_t(element)==bindings.buffers[1],"renderer VAO element buffer unchanged");
    no_error("restored state");
}
// Frame-scoped fields (the compositor's units, copy-write, unpack layout), zeroed padding first.
static cgl::Frame frame_state(const gc::Dispatch& g) {cgl::Frame f;std::memset(&f,0,sizeof(f));f.capture(g);return f;}
static void equal_frame(const gc::Dispatch& g,const cgl::Frame& before) {
    const auto after=frame_state(g);require(std::memcmp(&before,&after,sizeof(after))==0,"every frame field restored");
}
// An independent witness of every driver query: every entry of the dispatch
// the compositor is given is wrapped, and those named Get*, Is* or Check*
// record their name and first argument (not the compositor's own list).
#define GC_ID(result,name,args) q_##name,
enum Query { GC_GL_FUNCTIONS(GC_ID) q_count };
#undef GC_ID
#define GC_NAME(result,name,args) #name,
static const char* const query_names[]={GC_GL_FUNCTIONS(GC_NAME)};
#undef GC_NAME
static bool is_query(int id) {
    const char* n=query_names[id];
    return !std::strncmp(n,"Get",3) || !std::strncmp(n,"Is",2) || !std::strncmp(n,"Check",5);
}
struct Asked { int query; uint32_t first; bool operator==(const Asked& o) const {return query==o.query && first==o.first;} };
static std::vector<Asked> asked;
static gc::Dispatch spied_raw;
static uint32_t first_argument() {return 0;}
template<class T,class... Rest> static uint32_t first_argument(T first,Rest...) {
    if constexpr(std::is_pointer_v<T>)return 0;else return uint32_t(first);
}
template<int Id,auto Member,class F> struct Spy;
template<int Id,auto Member,class R,class... A> struct Spy<Id,Member,R (GC_GL_CALL*)(A...)> {
    static R GC_GL_CALL call(A... a) {
        if(is_query(Id))asked.push_back({Id,first_argument(a...)});
        return (spied_raw.*Member)(a...);
    }
};
static gc::Dispatch spy(const gc::Dispatch& raw) {
    spied_raw=raw;gc::Dispatch d=raw;
#define GC_SPY(result,name,args) d.name=Spy<q_##name,&gc::Dispatch::name,decltype(gc::Dispatch::name)>::call;
    GC_GL_FUNCTIONS(GC_SPY)
#undef GC_SPY
    return d;
}
static void ordinary_state(const gc::Dispatch& g,const Scene& s) {
    for(auto cap:cgl::enables)g.Disable(cap);
    g.Disable(GL_SCISSOR_TEST);
    g.Disablei(GL_BLEND,0);g.ColorMaski(0,1,1,1,1);g.PolygonMode(GL_FRONT_AND_BACK,GL_FILL);
    g.BindBuffer(cgl::PIXEL_UNPACK_BUFFER,0);g.BindSampler(0,0);g.BindSampler(1,0);
    g.DepthRange(0,1);g.Viewport(0,0,s.width,s.height);g.BindFramebuffer(cgl::FRAMEBUFFER,s.fbo);
    g.ReadBuffer(cgl::COLOR_ATTACHMENT0);g.PixelStorei(GL_UNPACK_ALIGNMENT,4);g.PixelStorei(GL_UNPACK_ROW_LENGTH,0);
    g.PixelStorei(GL_UNPACK_SKIP_ROWS,0);g.PixelStorei(GL_UNPACK_SKIP_PIXELS,0);
    g.ActiveTexture(cgl::TEXTURE0);g.BindTexture(GL_TEXTURE_2D,0);
    g.ActiveTexture(cgl::TEXTURE0+1);g.BindTexture(GL_TEXTURE_2D,0);
    g.ActiveTexture(cgl::TEXTURE0+cgl::unit_base+1);g.BindTexture(GL_TEXTURE_2D,0);g.ActiveTexture(cgl::TEXTURE0);
    g.BindSampler(cgl::unit_base+2,0);g.BindBuffer(cgl::COPY_WRITE_BUFFER,0);
}
static void check_image(const std::vector<uint8_t>& image,uint32_t w,uint32_t h,float opacity) {
    size_t changed=0;
    for(uint32_t y=0;y<h;++y)for(uint32_t x=0;x<w;++x) {
        bool ghost=x>=w/2 && x<3*w/4 && y>=h/4 && y<3*h/4;
        const int background[4]={20,40,60,255}, mario[4]={255,0,0,255};
        for(int c=0;c<4;++c) {
            float expected=ghost ? background[c]*(1-opacity)+mario[c]*opacity : float(background[c]);
            require(std::abs(float(image[(y*w+x)*4+c])-expected)<=.51f,"analytic image/opacity/occlusion");
        }
        if(ghost && opacity>0)++changed;
    }
    require(changed==0 || changed==w*h/8,"silhouette extent");
}
static void hud_rect(const gc::Dispatch& g,const Bindings& bindings,float alpha) {
    // Same-color and semitransparent HUD cannot be inferred from a color delta.
    g.UseProgram(bindings.hud_program);g.BindVertexArray(bindings.vao);g.Enablei(GL_BLEND,0);
    g.BlendEquationSeparatei(0,cgl::FUNC_ADD,cgl::FUNC_ADD);
    g.BlendFuncSeparatei(0,GL_SRC_ALPHA,GL_ONE_MINUS_SRC_ALPHA,GL_ZERO,GL_ONE);
    g.Uniform1f(g.GetUniformLocation(bindings.hud_program,"alpha"),alpha);g.DrawArrays(GL_TRIANGLES,0,6);
}
static void vanish_witness(const gc::Dispatch& g) {
    Scene low(g,32,32);
    auto* compositor=gc::create(g,1);require(compositor!=nullptr,"vanish compositor create");
    const uint8_t white[4]={255,255,255,255};
    require(gc::prepare(compositor,32,32,60,white,1,1)==gc::Result::ok,"vanish prepare");
    require(gc::prepare_particles(compositor,white,1,1)==gc::Result::ok,"vanish particle prepare");
    auto vertices=mesh();
    for(unsigned i=0;i<vertices.size();++i)vertices[i].position[2]=i<6 ? .25f : i<12 ? -.75f : -.5f;
    // A nearer red wall, a blue far wall and a duplicate red wall exercise
    // self-depth: a rejected body pixel must not be filled by another wall.
    const float marker[6][2]={{.5f,-.25f},{.75f,-.25f},{.5f,.25f},{.5f,.25f},{.75f,-.25f},{.75f,.25f}};
    for(const auto& point:marker) {
        gc::Vertex v{};v.position[0]=point[0];v.position[1]=point[1];v.position[2]=-.75f;
        v.normal[2]=1;v.color[1]=1;v.uv[0]=v.uv[1]=1;vertices.push_back(v);
    }
    uint8_t ia8[256];for(auto& texel:ia8)texel=0x0f;
    gc::ShadowVertex shadow[6]{};gc::ParticleVertex particle[6]{};
    const float quad[6][2]={{-.75f,0},{.75f,0},{-.75f,1},{-.75f,1},{.75f,0},{.75f,1}};
    for(unsigned i=0;i<6;++i) {
        shadow[i]={{quad[i][0],-.875f+quad[i][1]*.25f,-.75f},{.5f,.5f},.5f};
        particle[i].position[0]=quad[i][0];particle[i].position[1]=.625f+quad[i][1]*.25f;
        particle[i].position[2]=-.75f;particle[i].uv[0]=particle[i].uv[1]=.5f;
        particle[i].rect[2]=particle[i].rect[3]=1;particle[i].rgba[2]=particle[i].rgba[3]=1;
        particle[i].cutout=2;
    }
    auto normal=pose(vertices,1);normal.body_count=18;
    normal.shadow.vertices=shadow;normal.shadow.count=6;normal.shadow.texture_ia8=ia8;
    normal.particles.vertices=particle;normal.particles.count=normal.particles.opaque_count=6;
    std::memcpy(normal.shadow.view_projection,normal.view_projection,sizeof(normal.view_projection));
    std::memcpy(normal.particles.view_projection,normal.view_projection,sizeof(normal.view_projection));
    auto cap=normal;cap.body_alpha=128.f/255.f;cap.noise_alpha=true;cap.noise_seed=12345;
    auto invalid=cap;invalid.body_count=17;
    require(gc::begin(compositor,target(low,gc::Depth::ordinary),invalid)==gc::Result::invalid,"cap range ends on a triangle");
    invalid=cap;invalid.body_count=uint32_t(vertices.size())+3;
    require(gc::begin(compositor,target(low,gc::Depth::ordinary),invalid)==gc::Result::invalid,"cap range stays within the pose");
    invalid=cap;invalid.body_alpha=std::numeric_limits<float>::quiet_NaN();
    require(gc::begin(compositor,target(low,gc::Depth::ordinary),invalid)==gc::Result::invalid,"cap alpha must be finite");
    invalid=cap;invalid.noise_scale=0;
    require(gc::begin(compositor,target(low,gc::Depth::ordinary),invalid)==gc::Result::invalid,"noise scale must be positive");
    auto render=[&](const Scene& scene,const gc::Pose& p,gc::Depth mode) {
        ordinary_state(g,scene);
        require(gc::begin(compositor,target(scene,mode),p)==gc::Result::ok,"vanish begin");
        require(gc::finalize(compositor,77)==gc::Result::ok,"vanish finalize");
        return pixels(g,gc::display(compositor).framebuffer,scene.width,scene.height);
    };
    auto changed=[](const std::vector<uint8_t>& a,const std::vector<uint8_t>& b,size_t at) {
        return std::memcmp(&a[at],&b[at],3)!=0;
    };
    const auto clean=pixels(g,low.fbo,32,32);
    std::vector<uint8_t> reference;
    for(auto mode:{gc::Depth::ordinary,gc::Depth::n64_image}) {
        const auto opaque=render(low,normal,mode),noisy=render(low,cap,mode);
        auto half=cap;half.opacity=.5f;const auto half_pixels=render(low,half,mode);
        auto hue=cap;hue.hue_degrees=180;const auto hue_pixels=render(low,hue,mode);
        auto next=cap;++next.noise_seed;const auto next_pixels=render(low,next,mode);
        auto less=cap;less.body_alpha=64.f/255.f;const auto less_pixels=render(low,less,mode);
        auto overlap=cap;overlap.occlusion.overlap=0;const auto overlap_pixels=render(low,overlap,mode);
        unsigned survived=0,holes=0,seed_changes=0,less_survived=0;
        for(uint32_t y=0;y<32;++y)for(uint32_t x=0;x<32;++x) {
            const size_t at=(y*32+x)*4;const bool body=x>=8 && x<24 && y>=8 && y<24;
            if(!body) {
                require(!changed(noisy,opaque,at),"cap leaves held object shadow and particles unchanged");
                continue;
            }
            require(changed(opaque,clean,at),"vanish fixture reaches the complete solid body");
            const bool kept=changed(noisy,clean,at);
            survived+=kept;holes+=!kept;seed_changes+=kept!=changed(next_pixels,clean,at);
            require(kept==changed(half_pixels,clean,at) && kept==changed(hue_pixels,clean,at)
                && kept==changed(overlap_pixels,clean,at),"vanish mask independent of opacity tint and overlap");
            if(changed(less_pixels,clean,at)){++less_survived;require(kept,"lower env alpha keeps a subset of noise pixels");}
            for(unsigned c=0;c<3;++c) {
                const float background=float(clean[at+c]),body_color=c==0 ? 255.f : 0.f;
                const float alpha=kept ? cap.body_alpha : 0.f;
                require(std::abs(float(noisy[at+c])-(background*(1-alpha)+body_color*alpha))<=1.f,
                    "vanish surviving pixels retain environment alpha once");
                require(std::abs(float(half_pixels[at+c])-(background*(1-alpha*.5f)+body_color*alpha*.5f))<=1.f,
                    "vanish user opacity scales surviving pixels once");
            }
        }
        // Fixed 256-cell fixture: no discard gives 256, unrelated prepass
        // and color masks give about 64. Both must fail this density bound.
        require(survived>=96 && survived<=160 && holes>0,"vanish has holes and surviving body pixels in both passes");
        require(seed_changes>64 && less_survived>24 && less_survived<survived,"recorded pose seed and env alpha change noise coverage");
        require(changed(opaque,clean,(16*32+26)*4),"held marker is visible in the cap fixture");
        require(changed(opaque,clean,(28*32+16)*4),"particle is visible in the cap fixture");
        if(mode==gc::Depth::ordinary)require(changed(opaque,clean,(4*32+16)*4),"shadow is visible in the cap fixture");
        auto legacy=cap;legacy.body_count=0;
        require(render(low,legacy,mode)==opaque,"legacy poses leave every vertex unaffected by cap fields");
        auto full=cap;full.body_alpha=1;
        require(render(low,full,mode)==opaque,"full env alpha has no noise holes");
        auto invisible_body=cap;invisible_body.body_alpha=0;
        const auto zero_alpha=render(low,invisible_body,mode);
        for(uint32_t y=0;y<32;++y)for(uint32_t x=0;x<32;++x) {
            const size_t at=(y*32+x)*4;const bool body=x>=8 && x<24 && y>=8 && y<24;
            require(!changed(zero_alpha,body ? clean : opaque,at),"zero env alpha hides only Mario");
        }
        auto zero=cap;zero.opacity=0;
        require(render(low,zero,mode)==clean,"zero ghost opacity hides body and neighbors");
        auto offscreen=normal;offscreen.count=6;offscreen.body_count=0;offscreen.shadow={};offscreen.particles={};
        offscreen.view_projection[12]=3;const gc::Pose batch[]={offscreen,cap};
        ordinary_state(g,low);
        require(gc::begin_batch(compositor,target(low,mode),batch,2)==gc::Result::ok
            && gc::finalize(compositor,77)==gc::Result::ok,"vanish second batch range draws");
        require(pixels(g,gc::display(compositor).framebuffer,32,32)==noisy,"Mario range includes the batch vertex offset");
        reference=noisy;
    }
    // 32 -> 80 is the same noninteger scale as 640 -> 1600. Every output
    // fragment must inherit its source N64 pixel's hole, including 2/3px cells.
    Scene high(g,80,80);
    require(gc::prepare(compositor,80,80,60,white,1,1)==gc::Result::ok,"vanish scaled prepare");
    auto scaled=cap;scaled.noise_scale=2.5f;
    const auto high_clean=pixels(g,high.fbo,80,80);
    for(auto mode:{gc::Depth::ordinary,gc::Depth::n64_image}) {
        const auto high_pixels=render(high,scaled,mode);
        for(uint32_t y=20;y<60;++y)for(uint32_t x=20;x<60;++x) {
            const uint32_t source_x=uint32_t((float(x)+.5f)/2.5f),source_y=uint32_t((float(y)+.5f)/2.5f);
            require(changed(high_pixels,high_clean,(y*80+x)*4)==changed(reference,clean,(source_y*32+source_x)*4),
                "vanish noise remains at N64 pixel granularity when scaled");
        }
    }
    gc::destroy(compositor);no_error("vanish witness cleanup");
}
static void annotation_witness(const gc::Dispatch& g) {
    Scene scene(g,320,240);Bindings bindings(g);auto vertices=mesh();
    for(auto& v:vertices)v.position[0]=5000; // body clipped: the caption is the only visible draw
    auto* compositor=gc::create(g,1);require(compositor!=nullptr,"annotation compositor create");
    const uint8_t white[4]={255,255,255,255};
    hostile(g,scene,bindings);cgl::State before_warm{};before_warm.capture(g);
    require(gc::warm(compositor)==gc::Result::ok,"every program compiles ahead of the first ghost");
    equal_state(g,before_warm,bindings);
    require(gc::prepare(compositor,320,240,60,white,1,1)==gc::Result::ok,"annotation prepare");
    std::vector<uint8_t> atlas(ghost_annotation::atlas_width*ghost_annotation::atlas_height*4,255);
    hostile(g,scene,bindings);cgl::State prepared{};prepared.capture(g);const auto prepared_frame=frame_state(g);
    require(gc::prepare_annotations(compositor,atlas.data(),ghost_annotation::atlas_width,ghost_annotation::atlas_height)
        ==gc::Result::ok,"annotation atlas and shader prepare");
    equal_state(g,prepared,bindings);equal_frame(g,prepared_frame);
    ordinary_state(g,scene);const auto clean=pixels(g,scene.fbo,320,240);const auto z=depth(g,scene);
    const auto zi=image_depth(g,scene);
    // Names are world-sized: w=200 keeps this one at its largest screen size.
    auto p=pose(vertices,1);p.view_projection[15]=200;p.annotation.names=true;
    std::strcpy(p.annotation.label,"GHOST");p.annotation.position[0]=40;p.annotation.position[1]=-GP_ANNOTATION_HEIGHT;
    auto render=[&](const gc::Pose& payload,gc::Depth mode,bool hud=false) {
        hostile(g,scene,bindings);cgl::State before{};before.capture(g);const auto frame_before=frame_state(g);
        require(gc::begin(compositor,target(scene,mode),payload)==gc::Result::ok,"annotation frame begin");
        equal_state(g,before,bindings);equal_frame(g,frame_before);
        if(hud) {ordinary_state(g,scene);require(gc::begin_hud(compositor,77)==gc::Result::ok,"annotation HUD mirror");
            hud_rect(g,bindings,1);gc::end_hud(compositor);}
        require(gc::finalize(compositor,77)==gc::Result::ok,"annotation finalize");
        ordinary_state(g,scene);return pixels(g,gc::display(compositor).framebuffer,320,240);
    };
    for(auto mode:{gc::Depth::ordinary,gc::Depth::n64_image}) {
        const auto full=render(p,mode);require(full!=clean,"name renders above the body on the private display");
        auto off=p;off.annotation.names=false;require(render(off,mode)==clean,"independent name switch hides only annotation");
        off=p;off.opacity=0;require(render(off,mode)==clean,"zero body opacity hides annotation");
        off=p;off.body_alpha=0;require(render(off,mode)==clean,"cap environment alpha also hides annotation");
        auto half=p;half.opacity=.5f;const auto faint=render(half,mode);
        unsigned between=0;for(size_t at=0;at<full.size();at+=4)if(full[at]>clean[at]+10
            && faint[at]>clean[at] && faint[at]<full[at])++between;
        require(between>30,"annotation inherits the ghost opacity once");
        auto blocked=p;blocked.annotation.position[0]=-40;
        require(render(blocked,mode)==clean,"annotation behind copied terrain is occluded");
        auto close_pose=p;close_pose.view_projection[15]=10;close_pose.annotation.position[0]=2;
        close_pose.occlusion.enabled=1;require(render(close_pose,mode)==clean,"ordinary name shares near-camera body hiding");
        close_pose.occlusion.minimum=.15f;require(render(close_pose,mode)!=clean,"terminal name shares the death visibility floor");
        const auto hud=render(p,mode,true);
        require(hud==clean,"game HUD mirrored afterward covers the name exactly");
        require(pixels(g,scene.fbo,320,240)==clean && depth(g,scene)==z && image_depth(g,scene)==zi,
                "annotation never changes clean pixels or either game depth surface");
    }
    gc::destroy(compositor);no_error("annotation witness cleanup");
}
int main(int argc,char**argv) {
    repair_window_format=argc==2 && !std::strcmp(argv[1],"--repair-window-format");
    const auto foreground=GetForegroundWindow();
    Window window;auto g=dispatch();Scene scene(g,32,32);Bindings bindings(g);auto vertices=mesh();
    uint8_t atlas[4]={0,0,0,0};
    gc::Instance* compositor=gc::create(g,1);require(compositor!=nullptr,"component create");
    hostile(g,scene,bindings);cgl::State prepared_before{};prepared_before.capture(g);
    require(prepared_before.polygon[0]==GL_LINE && prepared_before.polygon[1]==GL_LINE,"core single polygon mode normalized");
    require(gc::prepare(compositor,32,32,60,atlas,1,1)==gc::Result::ok,"prepare");
    no_error("core prepare restoration");equal_state(g,prepared_before,bindings);
    ordinary_state(g,scene);const auto clean=pixels(g,scene.fbo,32,32);
    const auto original_depth=depth(g,scene);
    const auto original_image_depth=image_depth(g,scene);
    for(auto mode:{gc::Depth::ordinary,gc::Depth::n64_image})for(float alpha:{0.f,.5f,1.f}) {
        auto t=target(scene,mode);auto p=pose(vertices,alpha);
        hostile(g,scene,bindings);cgl::State before{};before.capture(g);const auto frame_before=frame_state(g);
        require(gc::begin(compositor,t,p)==gc::Result::ok,"begin");equal_state(g,before,bindings);equal_frame(g,frame_before);
        require(gc::display(compositor).texture==0,"no unfinalized handle");
        require(gc::finalize(compositor,77)==gc::Result::ok,"finalize");
        ordinary_state(g,scene);
        check_image(pixels(g,gc::display(compositor).framebuffer,32,32),32,32,alpha);
        require(pixels(g,scene.fbo,32,32)==clean,"clean scene unchanged");require(depth(g,scene)==original_depth,"game depth unchanged");
        require(image_depth(g,scene)==original_image_depth,"N64 image depth unchanged");
    }
    // Independent pixel oracle: identity projection has w=1, entirely inside
    // the camera-hide band. Ordinary ghosts vanish; a dying ghost keeps its
    // explicit floor in both world-depth routes. Its own opacity still fades.
    for(auto mode:{gc::Depth::ordinary,gc::Depth::n64_image})for(float floor:{0.f,.15f})for(float alpha:{.5f,1.f}) {
        auto p=pose(vertices,alpha);p.occlusion.enabled=1;p.occlusion.minimum=floor;
        ordinary_state(g,scene);
        require(gc::begin(compositor,target(scene,mode),p)==gc::Result::ok,"terminal visibility begin");
        require(gc::finalize(compositor,77)==gc::Result::ok,"terminal visibility finalize");
        check_image(pixels(g,gc::display(compositor).framebuffer,32,32),32,32,alpha*floor);
    }
    vanish_witness(g);
    annotation_witness(g);
    // Independent translucent bodies share one clean copy. A second begin()
    // would erase blue, and one shared stencil pass would suppress red.
    auto blue=vertices;
    for(auto& vertex:blue){vertex.color[0]=vertex.color[1]=0;vertex.color[2]=1;}
    gc::Pose batch[2]={pose(blue,.5f),pose(vertices,.5f)};
    hostile(g,scene,bindings);cgl::State batch_before{};batch_before.capture(g);const auto batch_frame=frame_state(g);
    require(gc::begin_batch(compositor,target(scene,gc::Depth::ordinary),batch,2)==gc::Result::ok,"batch begin");
    equal_state(g,batch_before,bindings);equal_frame(g,batch_frame);
    require(gc::finalize(compositor,77)==gc::Result::ok,"batch finalize");
    ordinary_state(g,scene);
    const auto layered=pixels(g,gc::display(compositor).framebuffer,32,32);
    for(uint32_t y=0;y<32;++y)for(uint32_t x=0;x<32;++x) {
        const bool covered=x>=16 && x<24 && y>=8 && y<24;
        const float expected[4]={covered ? 132.5f:20.f,covered ? 10.f:40.f,covered ? 78.75f:60.f,255.f};
        for(unsigned c=0;c<4;++c)require(std::abs(float(layered[(y*32+x)*4+c])-expected[c])<=1.1f,"batch layers preserved");
    }
    require(pixels(g,scene.fbo,32,32)==clean && depth(g,scene)==original_depth,"batch clean unchanged");
    gc::Pose overflow[4]={batch[0],batch[1],batch[0],batch[1]};
    require(gc::begin_batch(compositor,target(scene,gc::Depth::ordinary),overflow,4)==gc::Result::invalid &&
        !gc::display(compositor).texture,"batch aggregate capacity refuses stale display");
    require(gc::begin_batch(compositor,target(scene,gc::Depth::ordinary),batch,0)==gc::Result::invalid,"empty batch refused");
    // One batch holds GP_GHOSTS: the per-ghost GPU ranges are sized for no more.
    {
        // One triangle each: 17 x 3 vertices fit the prepared 60, so only the
        // ghost count can refuse it.
        std::vector<gc::Pose> crowd(gc::max_poses+1,batch[0]);
        for(auto& ghost:crowd)ghost.count=3;
        require(gc::begin_batch(compositor,target(scene,gc::Depth::ordinary),crowd.data(),gc::max_poses)
            ==gc::Result::ok,"a full batch of GP_GHOSTS ghosts draws");
        gc::abort(compositor);
        require(gc::begin_batch(compositor,target(scene,gc::Depth::ordinary),crowd.data(),gc::max_poses+1)
            ==gc::Result::invalid,"a batch past GP_GHOSTS refused");
    }
    // Authentic shadow decal: drawn over this frame's clean copy under every
    // Mario, tested against copied terrain depth, never written to the game.
    {
        uint8_t ia8[256];for(auto& texel:ia8)texel=0x0f; // intensity 0, alpha 15
        auto quad=[](float x0,float x1,float z,float alpha) {
            std::vector<gc::ShadowVertex> out;
            const float xy[6][2]={{x0,-1},{x1,-1},{x0,1},{x0,1},{x1,-1},{x1,1}};
            for(auto& point:xy)out.push_back({{point[0],point[1],z},{.5f,.5f},alpha});
            return out;
        };
        const auto behind=quad(-1,1,0,.5f), coplanar=quad(-1,0,-.5f,.25f);
        auto hidden=pose(vertices,1);hidden.view_projection[14]=-4; // Mario near-clipped: shadow alone
        auto shaded=[&](const std::vector<gc::ShadowVertex>& v) {
            auto p=hidden;p.shadow.vertices=v.data();p.shadow.count=uint32_t(v.size());p.shadow.texture_ia8=ia8;
            p.shadow.view_projection[0]=p.shadow.view_projection[5]=p.shadow.view_projection[10]=p.shadow.view_projection[15]=1;return p;
        };
        gc::Pose shadows[2]={shaded(behind),shaded(coplanar)};
        hostile(g,scene,bindings);cgl::State shadow_before{};shadow_before.capture(g);const auto shadow_frame=frame_state(g);
        require(gc::begin_batch(compositor,target(scene,gc::Depth::ordinary),shadows,2)==gc::Result::ok,"shadow begin");
        equal_state(g,shadow_before,bindings);equal_frame(g,shadow_frame);require(gc::finalize(compositor,77)==gc::Result::ok,"shadow finalize");
        ordinary_state(g,scene);
        const auto shadowed=pixels(g,gc::display(compositor).framebuffer,32,32);
        for(uint32_t y=0;y<32;++y)for(uint32_t x=0;x<32;++x) {
            const float keep=x<16 ? .75f : .5f; // left: terrain hides the far quad; coplanar decal passes
            const float expected[4]={20*keep,40*keep,60*keep,255};
            for(unsigned c=0;c<4;++c)require(std::abs(float(shadowed[(y*32+x)*4+c])-expected[c])<=1.1f,"shadow decal depth/alpha");
        }
        require(pixels(g,scene.fbo,32,32)==clean && depth(g,scene)==original_depth,"shadow leaves clean game untouched");
        require(gc::begin_batch(compositor,target(scene,gc::Depth::n64_image),shadows,2)==gc::Result::ok &&
            gc::finalize(compositor,77)==gc::Result::ok,"N64-image depth omits shadow");
        ordinary_state(g,scene);require(pixels(g,gc::display(compositor).framebuffer,32,32)==clean,"no delta-Z shadow guess");
        auto faded=shadows[0];faded.opacity=0;
        require(gc::begin(compositor,target(scene,gc::Depth::ordinary),faded)==gc::Result::ok &&
            gc::finalize(compositor,77)==gc::Result::ok,"faded shadow frame");
        ordinary_state(g,scene);require(pixels(g,gc::display(compositor).framebuffer,32,32)==clean,"zero opacity hides shadow");
        auto broken=shadows[0];broken.shadow.count=4;
        require(gc::begin(compositor,target(scene,gc::Depth::ordinary),broken)==gc::Result::invalid,"partial shadow triangle refuses");
        ia8[3]=0x1f;
        require(gc::begin(compositor,target(scene,gc::Depth::ordinary),shadows[0])==gc::Result::invalid,"non-black shadow texel refuses");
        ordinary_state(g,scene);
    }
    // Original and display receive exactly the same HUD raster operation.
    for(float alpha:{1.f,.5f}) {
        require(gc::begin(compositor,target(scene,gc::Depth::ordinary),pose(vertices,1))==gc::Result::ok,"HUD begin frame");
        ordinary_state(g,scene);hud_rect(g,bindings,alpha);
        require(gc::begin_hud(compositor,77)==gc::Result::ok,"HUD binding scope");hud_rect(g,bindings,alpha);gc::end_hud(compositor);
        int32_t bound=0;g.GetIntegerv(cgl::DRAW_FRAMEBUFFER_BINDING,&bound);require(uint32_t(bound)==scene.fbo,"HUD restores clean destination");
        require(gc::finalize(compositor,77)==gc::Result::ok,"HUD finalize");
        check_image(pixels(g,gc::display(compositor).framebuffer,32,32),32,32,1-alpha);
        require(pixels(g,scene.fbo,32,32)==clean,"same-color HUD clean parity");
    }
    auto bad=target(scene,gc::Depth::ordinary);bad.samples=4;
    require(gc::begin(compositor,bad,pose(vertices,1))==gc::Result::unsupported && !gc::display(compositor).texture,"MSAA refuses stale output");
    bad=target(scene,gc::Depth::ordinary);bad.generation=2;
    require(gc::begin(compositor,bad,pose(vertices,1))==gc::Result::invalid && !gc::display(compositor).texture,"generation refuses stale output");
    bad=target(scene,gc::Depth::n64_image);bad.n64_depth_texture=scene.color;
    require(gc::begin(compositor,bad,pose(vertices,1))==gc::Result::unsupported && !gc::display(compositor).texture,"wrong image format refuses");
    auto invalid_pose=pose(vertices,1);invalid_pose.count=63;
    require(gc::begin(compositor,target(scene,gc::Depth::ordinary),invalid_pose)==gc::Result::invalid && !gc::display(compositor).texture,"capacity refuses");
    g.BindFramebuffer(cgl::FRAMEBUFFER,scene.fbo);
    g.FramebufferTexture2D(cgl::FRAMEBUFFER,cgl::DEPTH_ATTACHMENT,cgl::TEXTURE_2D,0,0);
    // Detached depth is a different set of attachments: named anew, or unnamed.
    auto detached=target(scene,gc::Depth::ordinary);detached.attachments=uint64_t(scene.color)<<32;
    require(gc::begin(compositor,detached,pose(vertices,1))==gc::Result::unsupported && !gc::display(compositor).texture,"missing scene depth refuses");
    detached.attachments=0;
    require(gc::begin(compositor,detached,pose(vertices,1))==gc::Result::unsupported,"an unnamed framebuffer is checked every frame");
    g.FramebufferTexture2D(cgl::FRAMEBUFFER,cgl::DEPTH_ATTACHMENT,cgl::TEXTURE_2D,scene.depth,0);
    auto transformed=pose(vertices,1);transformed.view_projection[0]=transformed.view_projection[5]=.5f;
    transformed.view_projection[13]=.25f;transformed.flip_y=1;
    auto clipped=target(scene,gc::Depth::ordinary);clipped.scissor[0]=18;clipped.scissor[2]=8;
    require(gc::begin(compositor,clipped,transformed)==gc::Result::ok,"transform/scissor begin");
    require(gc::finalize(compositor,77)==gc::Result::ok,"transform/scissor finalize");
    auto transformed_pixels=pixels(g,gc::display(compositor).framebuffer,32,32);
    for(uint32_t y=0;y<32;++y)for(uint32_t x=0;x<32;++x) {
        const size_t i=(y*32+x)*4;
        const bool red=x>=18 && x<20 && y>=8 && y<16;
        require(transformed_pixels[i]==(red?255:20) && transformed_pixels[i+1]==(red?0:40) &&
            transformed_pixels[i+2]==(red?0:60),"independent transform/Y-flip/scissor pixels");
    }
    auto outside=pose(vertices,1);outside.view_projection[14]=-4;
    require(gc::begin(compositor,target(scene,gc::Depth::ordinary),outside)==gc::Result::ok,"near clip begin");
    require(gc::finalize(compositor,77)==gc::Result::ok && pixels(g,gc::display(compositor).framebuffer,32,32)==clean,"near clip omits geometry");
    gc::abort(compositor);require(!gc::display(compositor).texture,"explicit abort invalidates");
    require(gc::begin(compositor,target(scene,gc::Depth::ordinary),pose(vertices,1))==gc::Result::ok,"scope failure frame");
    g.Enable(GL_STENCIL_TEST);require(gc::begin_hud(compositor,77)==gc::Result::state && !gc::display(compositor).texture,"unsafe HUD aborts");
    ordinary_state(g,scene);
    require(gc::begin(compositor,target(scene,gc::Depth::ordinary),pose(vertices,1))==gc::Result::ok,"open HUD abort begin");
    require(gc::begin_hud(compositor,77)==gc::Result::ok,"open HUD scope");gc::abort(compositor);
    int32_t restored_fbo=0;g.GetIntegerv(cgl::DRAW_FRAMEBUFFER_BINDING,&restored_fbo);
    require(uint32_t(restored_fbo)==scene.fbo && !gc::display(compositor).texture,"abort closes HUD scope");
    uint8_t cutout_atlas[16]={0,255,0,255, 0,0,0,0, 0,255,0,255, 0,0,0,0};
    require(gc::prepare(compositor,32,32,60,cutout_atlas,4,1)==gc::Result::ok,"cutout atlas prepare");
    auto textured_vertices=vertices;
    for(auto& v:textured_vertices) {v.uv[0]=v.position[0]+.5f;v.uv[1]=v.position[1]+.5f;v.material=gc::Material::decal_rgba;}
    auto decal_pose=pose(textured_vertices,.5f);decal_pose.ambient=.25f;
    require(gc::begin(compositor,target(scene,gc::Depth::ordinary),decal_pose)==gc::Result::ok,"cutout begin");
    require(gc::finalize(compositor,77)==gc::Result::ok,"cutout finalize");
    auto cutout_pixels=pixels(g,gc::display(compositor).framebuffer,32,32);
    for(uint32_t y=0;y<32;++y)for(uint32_t x=0;x<32;++x) {
        const size_t i=(y*32+x)*4;const bool green=x>=16 && x<20 && y>=8 && y<24;
        const float expected[3]={green?10.f:20.f,green?147.5f:40.f,green?30.f:60.f};
        for(int c=0;c<3;++c)require(std::abs(cutout_pixels[i+c]-expected[c])<=.51f,"textured cutout/sentinel distinction");
    }
    uint8_t face_atlas[4]={0,0,255,128};
    require(gc::prepare(compositor,32,32,60,face_atlas,1,1)==gc::Result::ok,"face mix atlas prepare");
    auto face_vertices=vertices;
    for(auto& v:face_vertices) {v.material=gc::Material::alpha_mix;v.color[0]=1;v.color[1]=v.color[2]=0;}
    auto face_pose=pose(face_vertices,.5f);face_pose.ambient=.5f;
    require(gc::begin(compositor,target(scene,gc::Depth::ordinary),face_pose)==gc::Result::ok,"face mix begin");
    require(gc::finalize(compositor,77)==gc::Result::ok,"face mix finalize");
    auto face_pixels=pixels(g,gc::display(compositor).framebuffer,32,32);
    for(uint32_t y=0;y<32;++y)for(uint32_t x=0;x<32;++x) {
        const bool face=x>=16 && x<24 && y>=8 && y<24;const size_t i=(y*32+x)*4;
        const float expected[3]={face?41.75f:20.f,face?20.f:40.f,face?94.f:60.f};
        // Shader color and fixed-point blending can each quantize to UNORM8:
        // NVIDIA produced 41 for ideal 41.75. Bound the resulting one-code
        // error only INSIDE the silhouette; untouched pixels remain exact.
        for(int c=0;c<3;++c) {
            require(std::abs(face_pixels[i+c]-expected[c])<=(face?1.f:0.f),"body alpha blends RGB without cutting out body");
        }
    }
    face_vertices[1].material=gc::Material::solid;
    require(gc::begin(compositor,target(scene,gc::Depth::ordinary),pose(face_vertices,1))==gc::Result::invalid &&
        !gc::display(compositor).texture,"mixed triangle material refuses");
    for(auto mode:{gc::Material::decal_rgb,gc::Material::modulate_rgb,gc::Material::modulate_rgba}) {
        for(auto& v:face_vertices){v.material=mode;v.color[0]=v.color[1]=v.color[2]=1;}
        auto material_pose=pose(face_vertices,.5f);material_pose.ambient=.5f;
        require(gc::begin(compositor,target(scene,gc::Depth::ordinary),material_pose)==gc::Result::ok,"additional material begin");
        require(gc::finalize(compositor,77)==gc::Result::ok,"additional material finalize");
        auto material_pixels=pixels(g,gc::display(compositor).framebuffer,32,32);
        const float alpha=mode==gc::Material::modulate_rgba ? .5f*128.f/255.f : .5f;
        const float blue=mode==gc::Material::decal_rgb ? 255.f : 127.5f;
        for(uint32_t y=0;y<32;++y)for(uint32_t x=0;x<32;++x) {
            const bool face=x>=16 && x<24 && y>=8 && y<24;const size_t i=(y*32+x)*4;
            const float expected[3]={face?20*(1-alpha):20.f,face?40*(1-alpha):40.f,face?60*(1-alpha)+blue*alpha:60.f};
            for(int c=0;c<3;++c)require(std::abs(material_pixels[i+c]-expected[c])<=(face?1.f:0.f),"exact decal/modulate color and alpha");
        }
    }
    {
        // A tiled material samples as the RDP does: clamp to the tile's size,
        // then wrap by its mask, inside its atlas cell. Cell 1 holds a 32x32
        // texture whose texel (s,t) is (8s,8t,255); the rest of the atlas is
        // magenta, so any sample outside the texture shows. A full-target quad
        // runs s and t from -15.5 to 48.5 texels: pixel x samples s=2x-14.5.
        std::vector<uint8_t> tiles(128*64*4);
        for(uint32_t y=0;y<64;++y)for(uint32_t x=0;x<128;++x) {
            uint8_t *t=&tiles[(y*128+x)*4];
            const bool texel=x>=64 && x<96 && y<32;
            t[0]=texel?uint8_t((x-64)*8):255;t[1]=texel?uint8_t(y*8):0;t[2]=texel?255:128;t[3]=255;
        }
        require(gc::prepare(compositor,32,32,60,tiles.data(),128,64)==gc::Result::ok,"tile atlas prepare");
        const auto quad=[](gc::Material material) {
            std::vector<gc::Vertex> q(6);
            const float corners[6][2]={{-1,-1},{1,-1},{1,1},{-1,-1},{1,1},{-1,1}};
            for(int i=0;i<6;++i) {
                // In front of the scene's nearer (left) half too.
                auto &v=q[i];v.position[0]=corners[i][0];v.position[1]=corners[i][1];v.position[2]=-.9f;v.normal[2]=1;
                const float s=(corners[i][0]+1)*32-15.5f,t=(corners[i][1]+1)*32-15.5f;
                v.uv[0]=(64+s)/128;v.uv[1]=t/64;v.color[0]=v.color[1]=v.color[2]=1;v.material=material;
            }
            return q;
        };
        const auto word=[](bool s_clamp,uint32_t s_mask,bool t_clamp,uint32_t t_mask) {
            return 1u/*cell*/|gc::tile::present|(s_clamp?gc::tile::s_clamp:0u)|(s_mask<<gc::tile::s_mask)|(5u<<gc::tile::s_size)
                |(t_clamp?gc::tile::t_clamp:0u)|(t_mask<<gc::tile::t_mask)|(5u<<gc::tile::t_size);
        };
        // Mips' fur: both axes clamp at 32 (mask 5). King Bob-omb's kind of
        // tile: S wraps by a 16-texel mask without clamping, T clamps.
        const struct { bool s_clamp; uint32_t s_mask; bool t_clamp; uint32_t t_mask; const char *name; } rules[]={
            {true,5,true,5,"clamped tile"},{false,4,true,5,"S-wrapping tile"}};
        for(const auto &rule:rules) {
            const auto q=quad(gc::with_tile(gc::Material::decal_rgb,word(rule.s_clamp,rule.s_mask,rule.t_clamp,rule.t_mask)));
            require(gc::begin(compositor,target(scene,gc::Depth::ordinary),pose(q,1))==gc::Result::ok,rule.name);
            require(gc::finalize(compositor,77)==gc::Result::ok,"tile finalize");
            const auto tiled=pixels(g,gc::display(compositor).framebuffer,32,32);
            const auto axis=[](int texel,bool clamped,uint32_t mask) {
                if(clamped)texel=texel<0?0:texel>31?31:texel;
                if(mask)texel&=(1<<mask)-1;
                return texel;
            };
            for(uint32_t y=0;y<32;++y)for(uint32_t x=0;x<32;++x) {
                const size_t i=(y*32+x)*4;
                const int s=axis(int(2*x)-15,rule.s_clamp,rule.s_mask),t=axis(int(2*y)-15,rule.t_clamp,rule.t_mask);
                require(tiled[i]==s*8 && tiled[i+1]==t*8 && tiled[i+2]==255,"the texel the tile rules predict");
            }
        }
        // Alpha-mix through a tile (his face, cap and buttons): a clear texel
        // shows the vertex colour. Every tiled word is at or above 2^23, where
        // the shader once decoded it as float+0.5: floats step by 1 there, so
        // an odd word rounded up and alpha-mix read as a cut-out decal. His
        // face and cap showed the inside of his head (Round 13, 2026-09-28).
        std::fill(tiles.begin(),tiles.end(),uint8_t(0));
        require(gc::prepare(compositor,32,32,60,tiles.data(),128,64)==gc::Result::ok,"clear tile atlas prepare");
        auto face=quad(gc::with_tile(gc::Material::alpha_mix,word(true,5,true,5)));
        for(auto& v:face){v.color[0]=1;v.color[1]=v.color[2]=0;}
        auto face_mix=pose(face,1);face_mix.ambient=1;face_mix.diffuse=0;
        require(gc::begin(compositor,target(scene,gc::Depth::ordinary),face_mix)==gc::Result::ok,"tiled alpha-mix begin");
        require(gc::finalize(compositor,77)==gc::Result::ok,"tiled alpha-mix finalize");
        const auto mixed=pixels(g,gc::display(compositor).framebuffer,32,32);
        for(size_t i=0;i<mixed.size();i+=4)
            require(mixed[i]==255 && mixed[i+1]==0 && mixed[i+2]==0,"a clear texel of a tiled alpha-mix shows its vertex colour");
        // An unknown mask or size, or a word without its present bit, refuses.
        const auto bad=quad(static_cast<gc::Material>(uint32_t(gc::Material::decal_rgb)|((7u<<gc::tile::s_mask)<<gc::tile::shift)));
        require(gc::begin(compositor,target(scene,gc::Depth::ordinary),pose(bad,1))==gc::Result::unsupported,"malformed tile refuses");
    }
    {
        Scene resized(g,64,32);require(gc::prepare(compositor,64,32,60,atlas,1,1)==gc::Result::ok,"resize prepare");
        require(!gc::display(compositor).texture,"resize retires old handle");
        require(gc::begin(compositor,target(resized,gc::Depth::ordinary),pose(vertices,.5f))==gc::Result::ok,"resize begin");
        require(gc::finalize(compositor,77)==gc::Result::ok,"resize finalize");
        check_image(pixels(g,gc::display(compositor).framebuffer,64,32),64,32,.5f);
    }
    // The renderer's own record (Known): a frame asks the driver only what the
    // record lacks (nothing, once it holds the program, vertex array, depth
    // and blend state too), counted alike by the compositor and this witness,
    // and still puts every field back. With multi-bind, then without.
    const std::vector<Asked> residue={{q_GetIntegerv,cgl::CURRENT_PROGRAM},{q_GetIntegerv,cgl::VERTEX_ARRAY_BINDING},
        {q_GetIntegerv,cgl::DEPTH_FUNC},{q_GetBooleanv,cgl::DEPTH_WRITEMASK},{q_GetIntegeri_v,cgl::BLEND_SRC_RGB},
        {q_GetIntegeri_v,cgl::BLEND_DST_RGB},{q_GetIntegeri_v,cgl::BLEND_SRC_ALPHA},{q_GetIntegeri_v,cgl::BLEND_DST_ALPHA}};
    const std::vector<Asked> layer_residue={{q_GetBooleanv,cgl::DEPTH_WRITEMASK},{q_GetIntegeri_v,cgl::BLEND_SRC_RGB},
        {q_GetIntegeri_v,cgl::BLEND_DST_RGB},{q_GetIntegeri_v,cgl::BLEND_SRC_ALPHA},{q_GetIntegeri_v,cgl::BLEND_DST_ALPHA}};
    constexpr uint32_t render_fields=gc::Known::program|gc::Known::vertex_array|gc::Known::depth|gc::Known::blend_factors;
    unsigned known_frames=0;
    for(bool multi:{true,false}) {
        auto raw=g;if(!multi)raw.BindTextures=nullptr,raw.BindSamplers=nullptr;
        auto* known_compositor=gc::create(spy(raw),1);require(known_compositor!=nullptr,"known create");
        const auto renderer_state=[&]{hostile(g,scene,bindings);g.ViewportIndexedf(0,1,2,7,9);}; // its glViewport is whole pixels
        renderer_state();
        require(gc::prepare(known_compositor,32,32,60,atlas,1,1)==gc::Result::ok,"known prepare");
        gc::Known known{};
        known.fields=gc::Known::draw_framebuffer|gc::Known::read_framebuffer|gc::Known::viewport|gc::Known::scissor|render_fields;
        known.draw=known.read=scene.fbo;
        const int32_t viewport[4]={1,2,7,9},scissor[4]={3,4,2,3};
        std::memcpy(known.viewport_box,viewport,sizeof(viewport));std::memcpy(known.scissor_box,scissor,sizeof(scissor));
        known.caps=known.enabled=0x7f; // hostile() switched every capability on
        // ...and left these, as a renderer tracking its calls would hold them.
        known.current_program=bindings.program;known.bound_vertex_array=bindings.vao;
        known.depth_function=GL_GREATER;known.depth_write=0;
        const uint32_t hostile_blend[4]={GL_ONE,GL_ZERO,GL_DST_ALPHA,GL_ONE_MINUS_DST_ALPHA};
        std::memcpy(known.blend_factor,hostile_blend,sizeof(hostile_blend));
        std::vector<Asked> nothing,unit;if(!multi)unit.push_back({q_GetIntegerv,cgl::ACTIVE_TEXTURE});
        auto partial=residue;partial.insert(partial.end(),unit.begin(),unit.end());
        for(unsigned round=0;round<3;++round) {
            auto told=known;if(round==2)told.fields&=~render_fields; // a record without the render state
            cgl::State before{};before.capture(g);const auto frame_before=frame_state(g);
            asked.clear();(void)gc::take_cost(known_compositor);
            require(gc::begin(known_compositor,target(scene,gc::Depth::ordinary),pose(vertices,.5f),&told)==gc::Result::ok,"known begin");
            const auto cost=gc::take_cost(known_compositor);
            require(cost.queries==asked.size(),"the compositor counts every query it makes");
            // The first frame on a framebuffer verifies it; later ones reuse that.
            if(round==1)require(asked==unit,"a fully known frame asks the driver nothing");
            else if(round==2)require(asked==partial,"a frame asks what the record lacks, and only that");
            else require(asked.size()>unit.size(),"a new framebuffer is verified once");
            require(cost.ghosts==1 && cost.batch_micros>=cost.draw_micros,"stage times cover the batch");
            equal_state(g,before,bindings);equal_frame(g,frame_before);++known_frames;
            require(gc::finalize(known_compositor,77)==gc::Result::ok,"known finalize");
            ordinary_state(g,scene);check_image(pixels(g,gc::display(known_compositor).framebuffer,32,32),32,32,.5f);
            renderer_state();
        }
        // The renderer changes state it promised to leave alone: it says so.
        ordinary_state(g,scene);g.BlendEquationSeparatei(0,cgl::FUNC_ADD,cgl::FUNC_ADD);gc::resync(known_compositor);
        const int32_t whole[4]={0,0,32,32};std::memcpy(known.viewport_box,whole,sizeof(whole));
        known.enabled=0;
        require(gc::begin(known_compositor,target(scene,gc::Depth::ordinary),pose(vertices,1),&known)==gc::Result::ok,"known HUD frame");
        hud_rect(g,bindings,.5f);asked.clear();
        require(gc::begin_hud(known_compositor,77,&known)==gc::Result::ok && asked.empty(),"a known HUD scope asks nothing");
        hud_rect(g,bindings,.5f);gc::end_hud(known_compositor);
        require(gc::finalize(known_compositor,77)==gc::Result::ok,"known HUD finalize");
        check_image(pixels(g,gc::display(known_compositor).framebuffer,32,32),32,32,.5f);
        // A layer pass asks the game's blend factors and depth mask only when
        // the record lacks them.
        ordinary_state(g,scene);gc::clear_layer(known_compositor);
        g.Enable(GL_DEPTH_TEST);g.DepthMask(0);g.Enablei(GL_BLEND,0);
        g.BlendFuncSeparatei(0,GL_ONE,GL_ONE_MINUS_SRC_ALPHA,GL_ONE,GL_ONE_MINUS_SRC_ALPHA);
        known.enabled=gc::Known::depth_test|gc::Known::blend;known.depth_write=0;
        const uint32_t water_blend[4]={GL_ONE,GL_ONE_MINUS_SRC_ALPHA,GL_ONE,GL_ONE_MINUS_SRC_ALPHA};
        std::memcpy(known.blend_factor,water_blend,sizeof(water_blend));
        for(uint32_t pass=0;pass<2;++pass) {
            auto told=known;if(!pass)told.fields&=~render_fields;
            cgl::State before{};before.capture(g);const auto frame_before=frame_state(g);asked.clear();
            require(gc::begin_layer(known_compositor,target(scene,gc::Depth::ordinary),gc::LayerPass(pass),&told)==gc::Result::ok
                && asked==(pass ? nothing : layer_residue),"a known layer pass asks what the record lacks, and only that");
            gc::end_layer(known_compositor);equal_frame(g,frame_before);
            cgl::State after{};after.capture(g);require(std::memcmp(&before,&after,sizeof(after))==0,"a layer pass restores the game's state");
        }
        ordinary_state(g,scene);gc::destroy(known_compositor);no_error(multi ? "known multi-bind" : "known without multi-bind");
    }
    require(known_frames==6,"known frames ran");
    gc::destroy(compositor);no_error("destroy");
    // A genuinely separate context receives a fresh generation and resources.
    {
        Window second;auto second_g=dispatch();Scene second_scene(second_g,32,32);
        auto* fresh=gc::create(second_g,2);require(fresh!=nullptr,"second context create");
        require(gc::prepare(fresh,32,32,60,atlas,1,1)==gc::Result::ok,"second context prepare");
        require(gc::begin(fresh,target(second_scene,gc::Depth::n64_image,2),pose(vertices,1))==gc::Result::ok,"second context begin");
        require(gc::finalize(fresh,77)==gc::Result::ok,"second context finalize");
        check_image(pixels(second_g,gc::display(fresh).framebuffer,32,32),32,32,1);gc::destroy(fresh);
    }
    require(wglMakeCurrent(window.dc,window.context)!=0,"restore test context");
    require(GetForegroundWindow()==foreground,"foreground preserved");
    std::puts("compositor passed: hidden=yes profile=core depth=ordinary,n64_image opacity=0,0.5,1 materials=6 shadow=decal coplanar=once clean=unchanged hud=exact state=restored resize=yes contexts=2 known=nothing_asked vanish=noise_body_only annotations=private,depth,opacity,terminal,hud");
    return 0;
}
