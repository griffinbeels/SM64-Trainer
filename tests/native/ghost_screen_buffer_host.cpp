/* Executes staged upstream screen presentation and the real facade/compositor.
 * Only upstream window/config/cache shells are synthetic; all pixels use WGL. */
#define main presentation_fixture_main
#include "ghost_presentation_host.cpp"
#undef main
#include <array>
#include <functional>
#include <memory>
#include <utility>

using u32=uint32_t;using s32=int32_t;
namespace graphics {
struct ObjectHandle {
    uint32_t value=0;
    ObjectHandle()=default;ObjectHandle(uint32_t v):value(v){}
    operator uint32_t()const{return value;}
    static const ObjectHandle defaultFramebuffer;
};
const ObjectHandle ObjectHandle::defaultFramebuffer{};
using TextureParam=uint32_t;
namespace bufferTarget {constexpr u32 DRAW_FRAMEBUFFER=gl::DRAW_FRAMEBUFFER,READ_FRAMEBUFFER=gl::READ_FRAMEBUFFER;}
namespace textureParameters {constexpr u32 FILTER_LINEAR=gl::LINEAR,FILTER_NEAREST=gl::NEAREST;}
namespace blitMask {constexpr u32 COLOR_BUFFER=gl::COLOR_BUFFER_BIT;}
struct Context {
    struct BlitFramebuffersParams {
        ObjectHandle readBuffer,drawBuffer;
        s32 srcX0,srcX1,dstX0,dstX1,srcY0,srcY1,dstY0,dstY1;
        u32 mask,filter;
    };
};
}
using namespace graphics;
struct CachedTexture {enum{fbOneSample=1};ObjectHandle name;u32 realWidth=0,realHeight=0;int frameBufferTexture=fbOneSample;};
struct FrameBuffer {ObjectHandle m_FBO;CachedTexture* m_pTexture=nullptr;};
struct Program {bool gamma=false;};
static Program ordinary_program{},gamma_program_object{true};
static gp::Dispatch api_dispatch;
static bool native_blit=true;
static int blits=0,copies=0,provider_calls=0;
struct GraphicsDrawer {
    struct CopyRectParams {
        s32 srcX0=0,srcY0=0,srcX1=0,srcY1=0;u32 srcWidth=0,srcHeight=0;
        s32 dstX0=0,dstY0=0,dstX1=0,dstY1=0;u32 dstWidth=0,dstHeight=0;
        bool invertX=false,invertY=false;std::array<CachedTexture*,2> tex{};
        Program* combiner=nullptr;TextureParam filter=gl::NEAREST;
    };
    struct BlitOrCopyRectParams:CopyRectParams {ObjectHandle readBuffer,drawBuffer;u32 mask=gl::COLOR_BUFFER_BIT;};
    uint32_t program=0,vao=0;
    void init() {
        const auto& g=api_dispatch.gl;
        const char* vs="#version 430 core\nout vec2 uv;uniform vec2 lo,hi,uvlo,uvhi;const vec2 q[6]=vec2[6](vec2(0,0),vec2(1,0),vec2(0,1),vec2(0,1),vec2(1,0),vec2(1,1));void main(){vec2 p=q[gl_VertexID];uv=mix(uvlo,uvhi,p);gl_Position=vec4(mix(lo,hi,p),0,1);}";
        const char* fs="#version 430 core\nin vec2 uv;uniform sampler2D tex;uniform float exponent;out vec4 c;void main(){c=vec4(pow(texture(tex,uv).rgb,vec3(exponent)),1);}";
        const auto v=g.CreateShader(gl::VERTEX_SHADER),f=g.CreateShader(gl::FRAGMENT_SHADER);
        g.ShaderSource(v,1,&vs,nullptr);g.ShaderSource(f,1,&fs,nullptr);g.CompileShader(v);g.CompileShader(f);
        program=g.CreateProgram();g.AttachShader(program,v);g.AttachShader(program,f);g.LinkProgram(program);
        int32_t okay=0;g.GetProgramiv(program,gl::LINK_STATUS,&okay);require(okay!=0,"screen shader");
        g.DeleteShader(v);g.DeleteShader(f);g.GenVertexArrays(1,&vao);
    }
    void copyTexturedRect(const CopyRectParams& p) {
        ++copies;const auto& g=api_dispatch.gl;g.UseProgram(program);g.BindVertexArray(vao);
        g.ActiveTexture(gl::TEXTURE0);g.BindTexture(gl::TEXTURE_2D,p.tex[0]->name);g.BindSampler(0,0);
        g.TexParameteri(gl::TEXTURE_2D,gl::TEXTURE_MIN_FILTER,p.filter);g.TexParameteri(gl::TEXTURE_2D,gl::TEXTURE_MAG_FILTER,p.filter);
        g.TexParameteri(gl::TEXTURE_2D,gl::TEXTURE_WRAP_S,gl::CLAMP_TO_EDGE);g.TexParameteri(gl::TEXTURE_2D,gl::TEXTURE_WRAP_T,gl::CLAMP_TO_EDGE);
        g.Uniform1i(g.GetUniformLocation(program,"tex"),0);g.Uniform1f(g.GetUniformLocation(program,"exponent"),p.combiner->gamma?.5f:1.f);
        float x0=2.f*p.dstX0/p.dstWidth-1.f,x1=2.f*p.dstX1/p.dstWidth-1.f;
        float y0=2.f*p.dstY0/p.dstHeight-1.f,y1=2.f*p.dstY1/p.dstHeight-1.f;
        if(p.invertX){x0=-x0;x1=-x1;}if(p.invertY){y0=-y0;y1=-y1;}
        g.Uniform2f(g.GetUniformLocation(program,"lo"),x0,y0);g.Uniform2f(g.GetUniformLocation(program,"hi"),x1,y1);
        g.Uniform2f(g.GetUniformLocation(program,"uvlo"),float(p.srcX0)/p.srcWidth,float(p.srcY0)/p.srcHeight);
        g.Uniform2f(g.GetUniformLocation(program,"uvhi"),float(p.srcX1)/p.srcWidth,float(p.srcY1)/p.srcHeight);
        g.Viewport(0,0,p.dstWidth,p.dstHeight);g.Disablei(gl::SCISSOR_TEST,0);g.Disablei(gl::BLEND,0);
        g.Disable(gl::DEPTH_TEST);g.Disable(gl::STENCIL_TEST);g.ColorMaski(0,1,1,1,1);g.DrawArrays(gl::TRIANGLES,0,6);
        require(glGetError()==0,"screen texture raster");
    }
    void blitOrCopyTexturedRect(const BlitOrCopyRectParams&);
    void clearDepthBuffer(){glClear(GL_DEPTH_BUFFER_BIT);}
    void destroy(){api_dispatch.gl.DeleteProgram(program);api_dispatch.gl.DeleteVertexArrays(1,&vao);}
};
static GraphicsDrawer drawer;
struct FakeContext {
    void bindFramebuffer(u32 target,ObjectHandle value){api_dispatch.gl.BindFramebuffer(target,value);}
    void clearColorBuffer(float r,float g,float b,float a){glClearColor(r,g,b,a);glClear(GL_COLOR_BUFFER_BIT);}
    bool blitFramebuffers(const Context::BlitFramebuffersParams& p) {
        if(!native_blit)return false;
        ++blits;const auto& g=api_dispatch.gl;g.Disablei(gl::SCISSOR_TEST,0);
        g.BindFramebuffer(gl::READ_FRAMEBUFFER,p.readBuffer);g.ReadBuffer(uint32_t(p.readBuffer)?gl::COLOR_ATTACHMENT0:gl::BACK);
        g.BindFramebuffer(gl::DRAW_FRAMEBUFFER,p.drawBuffer);g.DrawBuffer(uint32_t(p.drawBuffer)?gl::COLOR_ATTACHMENT0:gl::BACK);
        g.BlitFramebuffer(p.srcX0,p.srcY0,p.srcX1,p.srcY1,p.dstX0,p.dstY0,p.dstX1,p.dstY1,p.mask,p.filter);
        require(glGetError()==0,"screen blit");return true;
    }
} gfxContext;
#include "blit_or_copy.inc"
struct {
    struct {int enable=0,enableOverscan=0,forceDepthBufferClear=0;} frameBufferEmulation;
    struct {int multisampling=0,fxaa=0;} video;
    struct {int enableBlitScreenWorkaround=0;} generalEmulation;
    struct {int force=0;} gammaCorrection;
} config;
static u32 vi_origin=0x123456,vi_status=0;
struct {u32* VI_ORIGIN=&vi_origin;u32* VI_STATUS=&vi_status;} REG;
struct {u32 changed=0;} gDP;
constexpr u32 CHANGED_SCISSOR=1;
struct FrameBufferList {
    std::vector<FrameBuffer> m_list;FrameBuffer* current=nullptr;
    FrameBuffer* getCurrent(){return current;}
#include "screen_accessor.inc"
    void _renderScreenSizeBuffer();
};
static FrameBufferList buffers;
static FrameBufferList& frameBufferList(){return buffers;}
namespace ghost_link {
#ifndef GL_DRAW_FRAMEBUFFER_BINDING
#define GL_DRAW_FRAMEBUFFER_BINDING 0x8CA6
#endif
#include "raster_target.inc"
}
static std::vector<uint8_t> visible;
static Window* active_window=nullptr;
namespace ghost_presentation {
static Instance* current=nullptr;
static struct {u32 context_generation=1,drawable_generation=1;} owner;
static uint32_t source_width=0,source_height=0;
static bool identity_good=true,provider_enabled=true;
static GhostViImage offered{};
static std::unique_ptr<Program> gamma_program(new Program{true});
static bool give_image(const ViSource& source,GhostViImage* out) {
    ++provider_calls;require(source.clean_framebuffer==uint32_t(buffers.m_list.back().m_FBO),"original screen FBO identity");
    require(source.clean_texture==uint32_t(buffers.m_list.back().m_pTexture->name),"original screen texture identity");
    if(!provider_enabled)return false;*out=offered;return true;
}
static ImageProvider provider=give_image;
static bool image_valid(const GhostViImage&){return true;}
static ImageValidity validity=image_valid;
static PresentationNotice notice=nullptr;
static bool identity_matches(){return identity_good;}
static bool prepare_extent(u32 w,u32 h) {
    source_width=w;source_height=h;return prepare(current,1,64,64,w,h);
}
struct ViRaster {const GraphicsDrawer::CopyRectParams* params;const CachedTexture* source;const GraphicsDrawer::BlitOrCopyRectParams* screen;};
}
namespace replay_gl {static bool BeginCapture(){return begin_gpu();}static bool EndCapture(){return end_gpu();}}
struct DisplayWindow {
    u32 getWidth()const{return 64;}u32 getHeight()const{return 48;}
    u32 getScreenWidth()const{return 64;}u32 getScreenHeight()const{return 64;}u32 getHeightOffset()const{return 0;}
    GraphicsDrawer& getDrawer(){return drawer;}
    void swapBuffers() {
        const gp::Raster raster{nullptr,nullptr,begin_gpu,end_gpu};
        auto valid=raster;valid.draw=[](void*,const gp::Draw&){require(false,"unused fallback raster");};
        require(gp::before_swap(gp::current,vi_origin,valid),"screen before swap");
        gp::after_osd(gp::current);glFinish();visible=pixels(api_dispatch.gl,0);
        gp::swap_completed(gp::current,SwapBuffers(active_window->dc)!=FALSE);
    }
} display_window;
static DisplayWindow& dwnd(){return display_window;}
struct CombinerInfo {
    static CombinerInfo& get(){static CombinerInfo info;return info;}
    Program* getTexrectCopyProgram(){return &ordinary_program;}
};
namespace ghost_presentation {
#include "vi_raster.inc"
#include "vi_admission.inc"
#include "screen_vi.inc"
}
struct PostProcessor {
    FrameBuffer filtered{};std::vector<std::function<FrameBuffer*(PostProcessor&,FrameBuffer*)>> filters;
    static PostProcessor& get(){static PostProcessor processor;return processor;}
    const auto& getPostprocessingList(){return filters;}
};
#define OS_WINDOWS
#define SM64_GHOST_RENDERER
#include "screen_route.inc"

static FrameBuffer make_target(CachedTexture& texture,u32 w,u32 h,uint32_t* depth=nullptr) {
    const auto& g=api_dispatch.gl;u32 fbo=0,tex=0;g.GenFramebuffers(1,&fbo);g.GenTextures(1,&tex);
    g.BindTexture(gl::TEXTURE_2D,tex);g.TexImage2D(gl::TEXTURE_2D,0,gl::RGBA8,w,h,0,gl::RGBA,gl::UNSIGNED_BYTE,nullptr);
    g.BindFramebuffer(gl::FRAMEBUFFER,fbo);g.FramebufferTexture2D(gl::FRAMEBUFFER,gl::COLOR_ATTACHMENT0,gl::TEXTURE_2D,tex,0);
    if(depth){g.GenRenderbuffers(1,depth);g.BindRenderbuffer(gl::RENDERBUFFER,*depth);g.RenderbufferStorage(gl::RENDERBUFFER,gl::DEPTH_COMPONENT24,w,h);g.FramebufferRenderbuffer(gl::FRAMEBUFFER,gl::DEPTH_ATTACHMENT,gl::RENDERBUFFER,*depth);}
    g.DrawBuffer(gl::COLOR_ATTACHMENT0);g.ReadBuffer(gl::COLOR_ATTACHMENT0);require(g.CheckFramebufferStatus(gl::FRAMEBUFFER)==gl::FRAMEBUFFER_COMPLETE,"screen FBO");
    texture={ObjectHandle(tex),w,h,CachedTexture::fbOneSample};return {ObjectHandle(fbo),&texture};
}
static bool same_rgb(const std::vector<uint8_t>& a,const std::vector<uint8_t>& b) {
    if(a.size()!=b.size())return false;
    for(size_t i=0;i<a.size();++i)if(i%4!=3 && a[i]!=b[i])return false;
    return true;
}
int main() {
    const auto foreground=GetForegroundWindow();Window window;active_window=&window;api_dispatch=dispatch();
    const auto& g=api_dispatch.gl;ordinary(g);drawer.init();
    CachedTexture clean_texture{},filtered_texture{};u32 depth=0;
    const auto clean=make_target(clean_texture,80,72,&depth);const auto filtered=make_target(filtered_texture,80,72);
    buffers.m_list.push_back(clean);g.BindFramebuffer(gl::FRAMEBUFFER,clean.m_FBO);
    require(!buffers.getCurrent() && ghost_link::raster_buffer()==&buffers.m_list.back(),"FBE-off real screen target");
    g.BindFramebuffer(gl::DRAW_FRAMEBUFFER,0);require(!ghost_link::raster_buffer(),"default drawable is not world target");
    g.BindFramebuffer(gl::DRAW_FRAMEBUFFER,filtered.m_FBO);require(!ghost_link::raster_buffer(),"foreign target refused");
    g.BindFramebuffer(gl::DRAW_FRAMEBUFFER,clean.m_FBO);config.frameBufferEmulation.enable=1;
    require(!ghost_link::raster_buffer(),"FBE-on never borrows screen fallback");buffers.current=&buffers.m_list.back();
    require(ghost_link::raster_buffer()==buffers.current,"FBE-on current target");buffers.current=nullptr;config.frameBufferEmulation.enable=0;
    g.Disablei(gl::SCISSOR_TEST,0);g.DepthMask(1);glClearDepth(1);glClearColor(20.f/255,40.f/255,60.f/255,1);glClear(GL_COLOR_BUFFER_BIT|GL_DEPTH_BUFFER_BIT|GL_STENCIL_BUFFER_BIT);
    g.Enablei(gl::SCISSOR_TEST,0);g.Scissor(0,50,80,22);glClearColor(.2f,.4f,.1f,1);glClear(GL_COLOR_BUFFER_BIT);g.Disablei(gl::SCISSOR_TEST,0);
    const auto clean_before=pixels(g,clean.m_FBO,80,72);
    std::vector<float> depth_before(80*72),depth_after(80*72);g.BindFramebuffer(gl::READ_FRAMEBUFFER,clean.m_FBO);glReadPixels(0,0,80,72,GL_DEPTH_COMPONENT,GL_FLOAT,depth_before.data());
    auto* compositor=gc::create(g,1);const uint8_t atlas[]={255,255,255,255};require(gc::prepare(compositor,80,72,3,atlas,1,1)==gc::Result::ok,"compositor setup");
    gp::current=gp::create(api_dispatch,1);require(gp::current && gp::prepare_extent(80,72),"presentation setup");
    gc::Vertex mesh[3]{};const float xy[3][2]={{-.7f,-.7f},{.7f,-.7f},{0,.7f}};
    for(int i=0;i<3;++i){mesh[i].position[0]=xy[i][0];mesh[i].position[1]=xy[i][1];mesh[i].normal[2]=1;mesh[i].color[0]=1;mesh[i].material=gc::Material::solid;}
    gc::Pose pose{};pose.vertices=mesh;pose.count=3;pose.view_projection[0]=pose.view_projection[5]=pose.view_projection[10]=pose.view_projection[15]=1;
    pose.light_direction[2]=1;pose.ambient=1;
    gc::Target target{};target.occurrence=1;target.generation=1;target.framebuffer=clean.m_FBO;target.width=80;target.height=72;target.samples=1;
    target.depth=gc::Depth::ordinary;target.depth_scale=target.depth_translate=.5f;target.viewport[2]=target.scissor[2]=80;target.viewport[3]=target.scissor[3]=72;
    auto& post=PostProcessor::get();post.filtered=filtered;
    post.filters.push_back([](PostProcessor& p,FrameBuffer* input) {
        if(!vi_status && !config.gammaCorrection.force)return input;
        GraphicsDrawer::CopyRectParams params{};params.srcX1=params.dstX1=80;params.srcY1=params.dstY1=72;
        params.srcWidth=params.dstWidth=80;params.srcHeight=params.dstHeight=72;params.tex[0]=input->m_pTexture;params.combiner=&gamma_program_object;
        gfxContext.bindFramebuffer(bufferTarget::DRAW_FRAMEBUFFER,p.filtered.m_FBO);drawer.copyTexturedRect(params);return &p.filtered;
    });
    u32 occurrence=1;gp::clean_consumer(true);
    for(bool blit:{true,false})for(bool gamma:{false,true}) {
        native_blit=blit;vi_status=gamma?8:0;gp::provider_enabled=false;gp::begin_update(gp::current,++occurrence);
        buffers._renderScreenSizeBuffer();const auto clean_visible=visible;
        std::vector<uint8_t> opaque;
        for(float opacity:{1.f,.5f,0.f}) {
            pose.opacity=opacity;target.occurrence=++occurrence;
            require(gc::begin(compositor,target,pose)==gc::Result::ok && gc::finalize(compositor,target.occurrence)==gc::Result::ok,"private ghost branch");
            const auto image=gc::display(compositor);CachedTexture ghost_texture{ObjectHandle(image.texture),80,72,CachedTexture::fbOneSample};
            // Independent oracle: upstream presents that whole private FBO as
            // its source. The adapter must produce exactly those RGB pixels.
            buffers.m_list.back()={ObjectHandle(image.framebuffer),&ghost_texture};gp::provider_enabled=false;
            gp::begin_update(gp::current,++occurrence);buffers._renderScreenSizeBuffer();const auto expected=visible;
            buffers.m_list.back()=clean;gp::provider_enabled=true;
            gp::offered={image.texture,80,72,1,1,image.occurrence,image.framebuffer};
            gp::begin_update(gp::current,++occurrence);buffers._renderScreenSizeBuffer();
            require(same_rgb(visible,expected),"screen facade matches upstream blit/copy and gamma");
            const auto recorded=gp::surface(gp::current);require(recorded.framebuffer && recorded.update_occurrence==occurrence,"clean source occurrence");
            require(same_rgb(pixels(g,recorded.framebuffer),clean_visible),"recording remains exact clean presentation");
            if(opacity==1)opaque=visible;
            if(opacity==0)require(same_rgb(visible,clean_visible),"zero opacity is clean");
            size_t changed=0;
            for(size_t i=0;i<visible.size();i+=4) {
                const bool inside=opaque[i]!=clean_visible[i] || opaque[i+1]!=clean_visible[i+1] || opaque[i+2]!=clean_visible[i+2];
                changed+=inside;
                for(size_t channel=0;channel<3;++channel) {
                    if(!inside)require(visible[i+channel]==clean_visible[i+channel],"outside Mario unchanged");
                    if(!gamma && opacity==.5f)require(std::abs(int(visible[i+channel])*2-int(opaque[i+channel])-int(clean_visible[i+channel]))<=2,"opacity applied once");
                }
            }
            require(changed>100,"visible ghost positive control");
        }
    }
    require(blits>0 && copies>0,"both actual upstream branches executed");
    const auto calls=provider_calls;gp::clean_consumer(false);gp::begin_update(gp::current,++occurrence);buffers._renderScreenSizeBuffer();require(provider_calls==calls,"clean negotiation required");
    gp::clean_consumer(true);gp::identity_good=false;gp::begin_update(gp::current,++occurrence);buffers._renderScreenSizeBuffer();require(provider_calls==calls,"stale context refused");gp::identity_good=true;
    require(pixels(g,clean.m_FBO,80,72)==clean_before,"original clean color unchanged");
    g.BindFramebuffer(gl::READ_FRAMEBUFFER,clean.m_FBO);glReadPixels(0,0,80,72,GL_DEPTH_COMPONENT,GL_FLOAT,depth_after.data());require(depth_before==depth_after,"original depth unchanged");
    gp::destroy(gp::current);gp::current=nullptr;gc::destroy(compositor);drawer.destroy();
    const u32 fbos[]={clean.m_FBO,filtered.m_FBO},textures[]={clean_texture.name,filtered_texture.name};g.DeleteFramebuffers(2,fbos);g.DeleteTextures(2,textures);g.DeleteRenderbuffers(1,&depth);
    require(glGetError()==0,"all resources clean");require(GetForegroundWindow()==foreground,"focus unchanged");
    std::puts("screen route passed: targets=bound blit=yes fallback=yes gamma=yes opacity=0,50,100 clean=exact");
}
