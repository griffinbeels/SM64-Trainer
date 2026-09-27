/* Real hidden default-framebuffer witness. No emulator, model or capture worker. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <GL/gl.h>
#include "ghost_presentation.h"
#include "ghost_compositor_gl.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include <cmath>
namespace gp=ghost_presentation;
namespace gc=ghost_compositor;
namespace gl=ghost_compositor::gl;
static void require(bool ok,const char* what) {
    if(!ok){std::fprintf(stderr,"FAILED: %s GL=%x\n",what,glGetError());std::exit(1);}
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
        WNDCLASSW cls{};cls.style=CS_OWNDC;cls.lpfnWndProc=DefWindowProcW;cls.hInstance=GetModuleHandleW(nullptr);cls.lpszClassName=L"SM64GhostPresentationHidden";
        RegisterClassW(&cls);
        window=CreateWindowExW(WS_EX_NOACTIVATE,cls.lpszClassName,L"",WS_POPUP,0,0,64,64,nullptr,nullptr,cls.hInstance,nullptr);
        require(window && !IsWindowVisible(window),"hidden window");dc=GetDC(window);
        PIXELFORMATDESCRIPTOR p{};p.nSize=sizeof(p);p.nVersion=1;p.dwFlags=PFD_DRAW_TO_WINDOW|PFD_SUPPORT_OPENGL|PFD_DOUBLEBUFFER;
        p.iPixelType=PFD_TYPE_RGBA;p.cColorBits=32;p.cDepthBits=24;p.cStencilBits=8;
        auto format=ChoosePixelFormat(dc,&p);require(format && SetPixelFormat(dc,format,&p),"format");
        context=wglCreateContext(dc);require(context && wglMakeCurrent(dc,context),"context");
        using CreateContext=HGLRC(WINAPI*)(HDC,HGLRC,const int*);
        auto create=reinterpret_cast<CreateContext>(load("wglCreateContextAttribsARB"));require(create!=nullptr,"core factory");
        GLint major=0,minor=0;glGetIntegerv(gl::MAJOR_VERSION,&major);glGetIntegerv(gl::MINOR_VERSION,&minor);
        const int attrs[]={0x2091,major,0x2092,minor,0x9126,1,0};auto core=create(dc,nullptr,attrs);
        require(core && wglMakeCurrent(dc,core),"core current");wglDeleteContext(context);context=core;
    }
    ~Window(){wglMakeCurrent(nullptr,nullptr);wglDeleteContext(context);ReleaseDC(window,dc);DestroyWindow(window);}
};
// Every state query the presentation makes, counted through its own table.
static unsigned queries=0;
#define GP_QUERIES(X) X(GetIntegerv,(uint32_t a,int32_t* b),(a,b)) X(GetBooleanv,(uint32_t a,uint8_t* b),(a,b)) \
    X(GetFloatv,(uint32_t a,float* b),(a,b)) X(GetDoublev,(uint32_t a,double* b),(a,b)) \
    X(GetIntegeri_v,(uint32_t a,uint32_t b,int32_t* c),(a,b,c)) X(GetBooleani_v,(uint32_t a,uint32_t b,uint8_t* c),(a,b,c)) \
    X(GetFloati_v,(uint32_t a,uint32_t b,float* c),(a,b,c)) X(GetDoublei_v,(uint32_t a,uint32_t b,double* c),(a,b,c))
#define GP_COUNT(name,params,args) static decltype(gc::Dispatch::name) real_##name=nullptr; \
    static void GC_GL_CALL count_##name params {++queries;real_##name args;}
GP_QUERIES(GP_COUNT)
#undef GP_COUNT
static decltype(gc::Dispatch::IsEnabled) real_IsEnabled=nullptr;
static uint8_t GC_GL_CALL count_IsEnabled(uint32_t a){++queries;return real_IsEnabled(a);}
static gp::Dispatch dispatch() {
    gp::Dispatch d{};
#define GP_LOAD(result,name,args) d.gl.name=reinterpret_cast<decltype(d.gl.name)>(load("gl" #name));require(d.gl.name!=nullptr,#name);
    GC_GL_FUNCTIONS(GP_LOAD)
#undef GP_LOAD
#define GP_WRAP(name,params,args) real_##name=d.gl.name;d.gl.name=count_##name;
    GP_QUERIES(GP_WRAP)
#undef GP_WRAP
    real_IsEnabled=d.gl.IsEnabled;d.gl.IsEnabled=count_IsEnabled;
    d.CopyTexSubImage2D=glCopyTexSubImage2D;
    d.DrawBuffers=reinterpret_cast<decltype(d.DrawBuffers)>(load("glDrawBuffers"));return d;
}
// The renderer's record, as a renderer that tracks its own calls holds it:
// asked here of the raw driver, outside the presentation's count.
static bool host_known(gp::Known* out) {
    using Active=void(GC_GL_CALL*)(uint32_t);using Bind=void(GC_GL_CALL*)(uint32_t,uint32_t);
    using Indexed=void(GC_GL_CALL*)(uint32_t,uint32_t,uint8_t*);
    static const auto active=reinterpret_cast<Active>(load("glActiveTexture"));
    static const auto bind=reinterpret_cast<Bind>(load("glBindFramebuffer"));
    static const auto indexed=reinterpret_cast<Indexed>(load("glGetBooleani_v"));
    *out={};GLint value=0,unit=0;
    glGetIntegerv(gl::READ_FRAMEBUFFER_BINDING,&value);out->read=uint32_t(value);
    glGetIntegerv(gl::DRAW_FRAMEBUFFER_BINDING,&value);out->draw=uint32_t(value);
    glGetIntegerv(gl::ACTIVE_TEXTURE,&unit);out->active_unit=uint32_t(unit)-gl::TEXTURE0;
    active(gl::TEXTURE0);glGetIntegerv(gl::TEXTURE_BINDING_2D,&value);out->unit0_texture=uint32_t(value);active(uint32_t(unit));
    bind(gl::READ_FRAMEBUFFER,0);glGetIntegerv(gl::READ_BUFFER,&value);out->default_read=uint32_t(value);
    bind(gl::READ_FRAMEBUFFER,out->read);
    out->srgb_enabled=glIsEnabled(gl::FRAMEBUFFER_SRGB);indexed(gl::SCISSOR_TEST,0,&out->scissor_enabled);
    out->fields=gp::Known::read_framebuffer|gp::Known::draw_framebuffer|gp::Known::texture_unit|
        gp::Known::default_read_buffer|gp::Known::srgb|gp::Known::scissor_test;
    return true;
}
static bool gpu_open=false,fail_begin=false,fail_end=false;
static bool begin_gpu(){require(!gpu_open,"gateway not nested");if(fail_begin)return false;require(glGetError()==0,"clean before gateway");gpu_open=true;return true;}
static bool end_gpu(){require(gpu_open,"gateway balanced");gpu_open=false;require(glGetError()==0,"clean after gateway");return !fail_end;}
static void ordinary(const gc::Dispatch& g) {
    for(auto cap:gl::enables)g.Disable(cap);
    g.Disablei(gl::BLEND,0);g.Disablei(gl::SCISSOR_TEST,0);g.ColorMaski(0,1,1,1,1);
    g.PolygonMode(gl::FRONT_AND_BACK,gl::FILL);g.BindBuffer(gl::PIXEL_UNPACK_BUFFER,0);
    g.PixelStorei(gl::UNPACK_ALIGNMENT,1);g.PixelStorei(gl::UNPACK_ROW_LENGTH,0);
    g.PixelStorei(gl::UNPACK_SKIP_ROWS,0);g.PixelStorei(gl::UNPACK_SKIP_PIXELS,0);
    g.BindFramebuffer(gl::FRAMEBUFFER,0);g.DrawBuffer(gl::BACK);g.ReadBuffer(gl::BACK);
}
static std::vector<uint8_t> pixels(const gc::Dispatch& g,uint32_t fbo,uint32_t w=64,uint32_t h=64) {
    gl::Guard guard(g);g.BindFramebuffer(gl::READ_FRAMEBUFFER,fbo);g.ReadBuffer(fbo?gl::COLOR_ATTACHMENT0:gl::BACK);
    std::vector<uint8_t> out(w*h*4);glReadPixels(0,0,w,h,GL_RGBA,GL_UNSIGNED_BYTE,out.data());
    require(glGetError()==0,"pixel read");return out;
}
static void rgb(const std::vector<uint8_t>& data,int x,int y,int r,int green,int b,const char* what,int width=64) {
    const auto* pixel=&data[(y*width+x)*4];
    if(std::abs(int(pixel[0])-r)>1 || std::abs(int(pixel[1])-green)>1 || std::abs(int(pixel[2])-b)>1)
        std::fprintf(stderr,"pixel %d,%d actual=%d,%d,%d expected=%d,%d,%d\n",x,y,pixel[0],pixel[1],pixel[2],r,green,b);
    require(std::abs(int(pixel[0])-r)<=1 && std::abs(int(pixel[1])-green)<=1 && std::abs(int(pixel[2])-b)<=1,what);
}
struct Raster {
    const gc::Dispatch& g;uint32_t program=0,vao=0,texture=0;int gamma_calls=0,vi_calls=0;
    explicit Raster(const gc::Dispatch& d):g(d) {
        const char* vs="#version 430 core\nout vec2 uv;const vec2 p[6]=vec2[6](vec2(-1,-1),vec2(1,-1),vec2(-1,1),vec2(-1,1),vec2(1,-1),vec2(1,1));void main(){vec2 xy=p[gl_VertexID];uv=(xy+1)/2;gl_Position=vec4(xy,0,1);}";
        const char* fs="#version 430 core\nin vec2 uv;uniform sampler2D t;uniform float exponent;uniform float alpha;uniform int solid;out vec4 c;void main(){c=solid!=0 ? vec4(vec3(20,40,60)/255.0,alpha) : vec4(pow(texture(t,uv).rgb,vec3(exponent)),1);}";
        auto v=g.CreateShader(gl::VERTEX_SHADER),f=g.CreateShader(gl::FRAGMENT_SHADER);
        g.ShaderSource(v,1,&vs,nullptr);g.ShaderSource(f,1,&fs,nullptr);g.CompileShader(v);g.CompileShader(f);
        program=g.CreateProgram();g.AttachShader(program,v);g.AttachShader(program,f);g.LinkProgram(program);
        int32_t okay=0;g.GetProgramiv(program,gl::LINK_STATUS,&okay);require(okay!=0,"raster shader");g.DeleteShader(v);g.DeleteShader(f);
        g.GenVertexArrays(1,&vao);g.GenTextures(1,&texture);g.BindTexture(gl::TEXTURE_2D,texture);
        std::vector<uint8_t> data(8*8*4);for(int i=0;i<64;++i){data[4*i]=64;data[4*i+1]=144;data[4*i+2]=16;data[4*i+3]=255;}
        g.TexImage2D(gl::TEXTURE_2D,0,gl::RGBA8,8,8,0,gl::RGBA,gl::UNSIGNED_BYTE,data.data());
        g.TexParameteri(gl::TEXTURE_2D,gl::TEXTURE_MIN_FILTER,gl::NEAREST);g.TexParameteri(gl::TEXTURE_2D,gl::TEXTURE_MAG_FILTER,gl::NEAREST);
    }
    ~Raster(){g.UseProgram(0);g.DeleteProgram(program);g.DeleteTextures(1,&texture);g.DeleteVertexArrays(1,&vao);}
    void setup(){g.UseProgram(program);g.BindVertexArray(vao);g.ActiveTexture(gl::TEXTURE0);g.BindSampler(0,0);g.Uniform1i(g.GetUniformLocation(program,"t"),0);}
    static void draw(void* user,const gp::Draw& draw) {
        auto& p=*static_cast<Raster*>(user);p.setup();const auto& g=p.g;
        g.BindFramebuffer(gl::DRAW_FRAMEBUFFER,draw.framebuffer);
        g.Disablei(gl::SCISSOR_TEST,0);g.Disablei(gl::BLEND,0);g.Disable(gl::DEPTH_TEST);g.Disable(gl::STENCIL_TEST);
        g.ColorMaski(0,1,1,1,1);g.BindTexture(gl::TEXTURE_2D,draw.texture);
        const bool gamma=draw.kind==gp::DrawKind::gamma;
        if(gamma){++p.gamma_calls;g.Viewport(0,0,draw.width,draw.height);}else{++p.vi_calls;g.Viewport(8,16,32,32);}
        g.Uniform1f(g.GetUniformLocation(p.program,"exponent"),gamma?.5f:1.f);g.Uniform1i(g.GetUniformLocation(p.program,"solid"),0);
        g.DrawArrays(gl::TRIANGLES,0,6);
    }
    gp::Raster api(){return {this,draw,begin_gpu,end_gpu};}
    gp::Raster known_api(){return {this,draw,begin_gpu,end_gpu,host_known};}
    void osd(float alpha) {
        setup();g.Viewport(16,20,24,8);g.Enablei(gl::BLEND,0);
        g.BlendFuncSeparatei(0,gl::SRC_ALPHA,gl::ONE_MINUS_SRC_ALPHA,gl::SRC_ALPHA,gl::ONE_MINUS_SRC_ALPHA);
        g.BlendEquationSeparatei(0,gl::FUNC_ADD,gl::FUNC_ADD);
        g.Uniform1i(g.GetUniformLocation(program,"solid"),1);g.Uniform1f(g.GetUniformLocation(program,"alpha"),alpha);
        g.DrawArrays(gl::TRIANGLES,0,6);
    }
};
static void background(const gc::Dispatch& g) {ordinary(g);glClearColor(20.f/255,40.f/255,60.f/255,1);glClear(GL_COLOR_BUFFER_BIT);}
static void completed(gp::Instance* p,Window& window){gp::after_osd(p);gp::swap_completed(p,SwapBuffers(window.dc)!=FALSE);}
static void run(uint32_t generation) {
    Window window;const auto d=dispatch();const auto& g=d.gl;ordinary(g);Raster raster(g);
    auto* p=gp::create(d,generation);require(p && gp::prepare(p,1,64,64,8,8),"prepare");
    gp::GhostViImage image{raster.texture,8,8,1,generation,55};
    require(!gp::surface(p).presentation_serial,"no initial descriptor");
    require(!gp::replace_vi(p,image,0x1000,false,raster.api()),"update occurrence required");

    for(int gamma=0;gamma<2;++gamma)for(float alpha:{1.f,.5f}) {
        background(g);gp::begin_update(p,100+gamma);const auto original=pixels(g,0);
        require(gp::replace_vi(p,image,0x1000,gamma!=0,raster.api()),"replace VI");
        require(!gp::surface(p).presentation_serial,"unpublished before swap");
        auto visible=pixels(g,0);rgb(visible,0,0,20,40,60,"outside VI unchanged");
        rgb(visible,12,18,gamma?128:64,gamma?192:144,gamma?64:16,"shared VI mapping and gamma");
        require(gp::before_swap(p,0x1000,raster.api()),"before swap");
        raster.osd(alpha);require(gp::begin_osd(p),"mirror OSD begin");raster.osd(alpha);gp::end_osd(p);
        visible=pixels(g,0);const int r=gamma?128:64,green=gamma?192:144,b=gamma?64:16;
        rgb(visible,20,24,int(20*alpha+r*(1-alpha)),int(40*alpha+green*(1-alpha)),int(60*alpha+b*(1-alpha)),"OSD actual coverage");
        completed(p,window);const auto clean=gp::surface(p);
        require(clean.framebuffer && clean.presentation_serial && clean.update_occurrence==uint64_t(100+gamma),"successful swap descriptor");
        const auto recorded=pixels(g,clean.framebuffer);for(int y=0;y<64;++y)for(int x=0;x<64;++x)
            rgb(recorded,x,y,20,40,60,"clean RGB including same-color OSD");
    }
    require(raster.gamma_calls==2 && raster.vi_calls==4,"gamma once before each VI");
    const auto old=gp::surface(p);gp::begin_update(p,200);
    require(gp::surface(p).update_occurrence==old.update_occurrence,"no swap retains explicit previous occurrence");
    background(g);require(gp::before_swap(p,0x2000,raster.api()),"clean-only fallback");
    require(!gp::surface(p).presentation_serial,"overwrite invalidates old descriptor");
    gp::after_osd(p);gp::swap_completed(p,false);require(!gp::surface(p).presentation_serial,"failed swap no descriptor");

    background(g);gp::begin_update(p,201);require(gp::replace_vi(p,image,0x2000,false,raster.api()),"cancel candidate");
    // Taking the ghost back touches the read and draw framebuffers, scissor and
    // sRGB, and puts back the default draw selectors: those are all it asks.
    int32_t draw_buffers=0;glGetIntegerv(0x8824,&draw_buffers); // GL_MAX_DRAW_BUFFERS
    uint32_t foreign=0;g.GenFramebuffers(1,&foreign); // the renderer's read framebuffer, not ours
    g.DrawBuffer(gl::FRONT);g.Enablei(gl::SCISSOR_TEST,0);g.Enable(gl::FRAMEBUFFER_SRGB);
    g.BindFramebuffer(gl::FRAMEBUFFER,foreign); // read and draw both elsewhere
    {gl::State taken{};taken.capture(g);queries=0;gp::cancel_overlay(p);
     std::printf("take-back state queries: %u (%d draw selectors)\n",queries,draw_buffers);
     require(queries<=4u+unsigned(draw_buffers),"taking the ghost back asks only what it changes");
     gl::State after{};after.capture(g);require(std::memcmp(&taken,&after,sizeof taken)==0,"taking the ghost back restores touched GL state");}
    g.Disablei(gl::SCISSOR_TEST,0);g.Disable(gl::FRAMEBUFFER_SRGB);g.BindFramebuffer(gl::FRAMEBUFFER,0);
    int32_t selector=0;g.GetIntegerv(gl::DRAW_BUFFER0,&selector);
    require(selector==gl::FRONT,"restore preserves default draw selector");g.DrawBuffer(gl::BACK);
    rgb(pixels(g,0),20,30,20,40,60,"revocation restores clean BACK");gp::swap_completed(p,true);
    require(!gp::surface(p).presentation_serial,"revocation invalidates descriptor");

    background(g);gp::begin_update(p,207);require(gp::replace_vi(p,image,0x2000,false,raster.api()),"multiple selector candidate");
    const uint32_t selectors[]={GL_FRONT_LEFT,GL_BACK_LEFT};d.DrawBuffers(2,selectors);
    require(glGetError()==0,"multiple default draw selectors supported");gp::cancel_overlay(p);
    int32_t first=0,second=0;g.GetIntegerv(gl::DRAW_BUFFER0,&first);g.GetIntegerv(gl::DRAW_BUFFER0+1,&second);
    require(first==GL_FRONT_LEFT && second==GL_BACK_LEFT,"all default draw selectors restored");g.DrawBuffer(gl::BACK);
    gp::swap_completed(p,true);

    background(g);gp::begin_update(p,206);require(gp::replace_vi(p,image,0x2000,false,raster.api()),"revision candidate");
    require(gp::before_swap(p,0x2000,raster.api()),"revision phase");
    raster.osd(.5f);require(gp::begin_osd(p),"revision OSD");raster.osd(.5f);gp::end_osd(p);gp::after_osd(p);
    gp::withdraw_image(p);rgb(pixels(g,0),20,24,20,40,60,"revision withdrawal keeps mirrored OSD");
    require(!gp::pending_image(p).occurrence,"withdrawn image cannot be acknowledged displayed");
    gp::swap_completed(p,SwapBuffers(window.dc)!=FALSE);require(gp::surface(p).presentation_serial,"revision withdrawal keeps clean capture");

    background(g);gp::begin_update(p,202);fail_begin=true;
    require(!gp::replace_vi(p,image,0x2000,false,raster.api()),"gateway begin refusal");fail_begin=false;
    require(!gp::surface(p).presentation_serial,"refusal has no descriptor");
    background(g);gp::begin_update(p,203);require(gp::replace_vi(p,image,0x2000,false,raster.api()),"unsafe OSD candidate");
    require(gp::before_swap(p,0x2000,raster.api()),"unsafe OSD phase");g.Enable(gl::DEPTH_TEST);
    require(!gp::begin_osd(p),"unsafe OSD refused");gp::after_osd(p);g.Disable(gl::DEPTH_TEST);
    rgb(pixels(g,0),20,30,20,40,60,"failed mirror cancels display");gp::swap_completed(p,true);
    require(!gp::surface(p).presentation_serial,"failed mirror no clean claim");

    // Saving BACK restores both the original read FBO and the default read
    // selector, even when those are different state objects.
    background(g);gp::begin_update(p,204);g.ReadBuffer(gl::FRONT);g.BindFramebuffer(gl::READ_FRAMEBUFFER,old.framebuffer);g.ReadBuffer(GL_NONE);
    gl::State before{};before.capture(g);queries=0;
    require(gp::before_swap(p,0x3000,raster.api()),"foreign read FBO clean save");
    // Saving the clean picture reads four values (read FBO, active unit, its
    // texture, the default read selector), not the full state guard.
    std::printf("clean save state queries: %u\n",queries);
    require(queries<=4,"the clean save queries only what it changes");
    gl::State after{};after.capture(g);require(std::memcmp(&before,&after,sizeof before)==0,"save restores touched GL state");
    g.BindFramebuffer(gl::READ_FRAMEBUFFER,0);g.GetIntegerv(gl::READ_BUFFER,&selector);require(selector==gl::FRONT,"default read selector restored");
    completed(p,window);
    // With the renderer's record the clean save and the sRGB check ask
    // nothing, and taking the ghost back asks only the default draw selectors.
    background(g);gp::begin_update(p,209);g.BindFramebuffer(gl::READ_FRAMEBUFFER,foreign);
    {gl::State saved{};saved.capture(g);queries=0;
     require(gp::before_swap(p,0x3000,raster.known_api()) && queries==0,"a known clean save asks nothing");
     gl::State after{};after.capture(g);require(std::memcmp(&saved,&after,sizeof saved)==0,"a known clean save restores state");}
    g.BindFramebuffer(gl::READ_FRAMEBUFFER,0);completed(p,window);
    background(g);gp::begin_update(p,210);queries=0;
    require(gp::replace_vi(p,image,0x2000,false,raster.known_api()) && queries==0,"a known ghost replace asks nothing");
    g.Enablei(gl::SCISSOR_TEST,0);g.Enable(gl::FRAMEBUFFER_SRGB);g.BindFramebuffer(gl::FRAMEBUFFER,foreign);
    {gl::State taken{};taken.capture(g);queries=0;gp::cancel_overlay(p);
     require(queries==unsigned(draw_buffers),"a known take-back asks only the default draw selectors");
     gl::State after{};after.capture(g);require(std::memcmp(&taken,&after,sizeof taken)==0,"a known take-back restores state");}
    g.Disablei(gl::SCISSOR_TEST,0);g.Disable(gl::FRAMEBUFFER_SRGB);g.BindFramebuffer(gl::FRAMEBUFFER,0);
    rgb(pixels(g,0),20,30,20,40,60,"a known take-back restores clean BACK");gp::swap_completed(p,true);
    g.DeleteFramebuffers(1,&foreign);
    const auto resource=gp::surface(p).resource_generation;
    require(gp::prepare(p,2,32,32,8,8),"resize prepare");require(!gp::surface(p).presentation_serial,"resize invalidates descriptor");
    gp::begin_update(p,205);background(g);require(gp::before_swap(p,0x4000,raster.api()),"resized clean save");completed(p,window);
    require(gp::surface(p).resource_generation>resource && gp::surface(p).width==32 && gp::surface(p).drawable_generation==2,"resource identity");
    gp::destroy(p);require(glGetError()==0,"destroy clean");
}
int main() {
    const auto foreground=GetForegroundWindow();require(!gp::clean_consumer(),"inactive by default");
    gp::clean_consumer(true);require(gp::clean_consumer(),"negotiated");gp::clean_consumer(false);
    run(1);run(2);require(GetForegroundWindow()==foreground,"focus unchanged");
    std::puts("presentation passed: hidden=yes contexts=2 clean=exact gamma=yes osd=samecolor,partial swap=qualified revoke=clean resize=invalidates state=restored known=asks_nothing");return 0;
}
