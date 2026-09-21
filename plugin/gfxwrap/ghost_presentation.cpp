#include "ghost_presentation.h"
#include "ghost_compositor_gl.h"
#include <atomic>
#include <new>

namespace ghost_presentation {
namespace gl=ghost_compositor::gl;
struct Instance {
    Dispatch dispatch;
    uint32_t context_generation,drawable_generation=0,resource_generation=0;
    uint32_t width=0,height=0,source_width=0,source_height=0;
    uint32_t textures[2]={},fbos[2]={}; // clean drawable, gamma result
    uint64_t update=0,serial=0;
    uint32_t vi_origin=0;
    Raster raster{};
    GhostViImage image{};
    CleanSurface published{};
    bool ready=false,saved=false,replaced=false,osd_window=false,osd=false,qualified=false;
    int32_t osd_fbo=0;
    explicit Instance(const Dispatch& d,uint32_t generation):dispatch(d),context_generation(generation) {}
};
namespace {
std::atomic<bool> negotiated{false};
ImageProvider provider=nullptr;
PresentationNotice notice=nullptr;
ImageValidity validity=nullptr;
bool raster_valid(const Raster& r) {return r.draw && r.begin_gpu && r.end_gpu;}
void reset(Instance* p) {
    end_osd(p);p->saved=p->replaced=p->osd_window=p->qualified=false;p->image={};
}
bool allocate(Instance* p,uint32_t index,uint32_t width,uint32_t height) {
    const auto& g=p->dispatch.gl;
    g.ActiveTexture(gl::TEXTURE0);g.BindTexture(gl::TEXTURE_2D,p->textures[index]);
    g.TexParameteri(gl::TEXTURE_2D,gl::TEXTURE_MIN_FILTER,gl::NEAREST);
    g.TexParameteri(gl::TEXTURE_2D,gl::TEXTURE_MAG_FILTER,gl::NEAREST);
    g.TexParameteri(gl::TEXTURE_2D,gl::TEXTURE_WRAP_S,gl::CLAMP_TO_EDGE);
    g.TexParameteri(gl::TEXTURE_2D,gl::TEXTURE_WRAP_T,gl::CLAMP_TO_EDGE);
    g.TexImage2D(gl::TEXTURE_2D,0,gl::RGBA8,width,height,0,gl::RGBA,gl::UNSIGNED_BYTE,nullptr);
    g.BindFramebuffer(gl::FRAMEBUFFER,p->fbos[index]);
    g.FramebufferTexture2D(gl::FRAMEBUFFER,gl::COLOR_ATTACHMENT0,gl::TEXTURE_2D,p->textures[index],0);
    g.ReadBuffer(gl::COLOR_ATTACHMENT0);g.DrawBuffer(gl::COLOR_ATTACHMENT0);
    return g.CheckFramebufferStatus(gl::FRAMEBUFFER)==gl::FRAMEBUFFER_COMPLETE;
}
bool save_back(Instance* p) {
    p->published={};p->qualified=false;
    if(!p->raster.begin_gpu())return false;
    {
        const auto& g=p->dispatch.gl;gl::Guard guard(g);
        g.BindFramebuffer(gl::READ_FRAMEBUFFER,0);
        int32_t selector=0;g.GetIntegerv(gl::READ_BUFFER,&selector);g.ReadBuffer(gl::BACK);
        g.ActiveTexture(gl::TEXTURE0);g.BindTexture(gl::TEXTURE_2D,p->textures[0]);
        // CopyTexSubImage transfers the stored RGB values without sRGB conversion.
        p->dispatch.CopyTexSubImage2D(gl::TEXTURE_2D,0,0,0,0,0,p->width,p->height);
        g.ReadBuffer(selector); // restore DEFAULT selector even if another FBO was originally read
    }
    p->saved=p->raster.end_gpu();return p->saved;
}
void restore_back(Instance* p) {
    if(!p->saved)return;
    const auto& g=p->dispatch.gl;gl::Guard guard(g);
    g.Disablei(gl::SCISSOR_TEST,0);g.Disable(gl::FRAMEBUFFER_SRGB);
    g.BindFramebuffer(gl::READ_FRAMEBUFFER,p->fbos[0]);g.ReadBuffer(gl::COLOR_ATTACHMENT0);
    g.BindFramebuffer(gl::DRAW_FRAMEBUFFER,0);
    int32_t selector=0;g.GetIntegerv(gl::DRAW_BUFFER0,&selector);g.DrawBuffer(gl::BACK);
    // Best-effort recovery also runs after the diagnostic gateway has refused.
    // No game framebuffer is touched, and no error is consumed here.
    g.BlitFramebuffer(0,0,p->width,p->height,0,0,p->width,p->height,gl::COLOR_BUFFER_BIT,gl::NEAREST);
    g.DrawBuffer(selector);
}
}
Instance* create(const Dispatch& d,uint32_t generation) {
    if(!generation || !d.CopyTexSubImage2D)return nullptr;
#define GP_CHECK(result,name,args) if(!d.gl.name)return nullptr;
    GC_GL_FUNCTIONS(GP_CHECK)
#undef GP_CHECK
    int32_t major=0,minor=0;d.gl.GetIntegerv(gl::MAJOR_VERSION,&major);d.gl.GetIntegerv(gl::MINOR_VERSION,&minor);
    if(major<4 || (major==4 && minor<3))return nullptr;
    return new(std::nothrow) Instance(d,generation);
}
bool prepare(Instance* p,uint32_t drawable,uint32_t width,uint32_t height,uint32_t sw,uint32_t sh) {
    if(!p)return false;
    if(!drawable || !width || !height || width>8192 || height>8192 || sw>8192 || sh>8192 || (!sw!=!sh)) {
        reset(p);p->ready=false;p->published={};return false;
    }
    if(p->ready && drawable==p->drawable_generation && width==p->width && height==p->height &&
       sw==p->source_width && sh==p->source_height)return true;
    reset(p);p->published={};p->ready=false;
    if(p->resource_generation==UINT32_MAX)return false;
    const auto& g=p->dispatch.gl;gl::Guard guard(g);
    if(!p->textures[0]){g.GenTextures(2,p->textures);g.GenFramebuffers(2,p->fbos);}
    if(!p->textures[0] || !p->textures[1] || !p->fbos[0] || !p->fbos[1])return false;
    g.BindBuffer(gl::PIXEL_UNPACK_BUFFER,0);g.PixelStorei(gl::UNPACK_ALIGNMENT,1);
    g.PixelStorei(gl::UNPACK_ROW_LENGTH,0);g.PixelStorei(gl::UNPACK_SKIP_ROWS,0);g.PixelStorei(gl::UNPACK_SKIP_PIXELS,0);
    if(!allocate(p,0,width,height) || (sw && !allocate(p,1,sw,sh)))return false;
    p->drawable_generation=drawable;p->width=width;p->height=height;
    p->source_width=sw;p->source_height=sh;++p->resource_generation;p->ready=true;return true;
}
void begin_update(Instance* p,uint64_t occurrence) {
    if(!p)return;
    reset(p);p->update=occurrence;
}
bool replace_vi(Instance* p,const GhostViImage& image,uint32_t origin,bool gamma,const Raster& raster) {
    if(!p || !p->ready || p->saved || !raster_valid(raster) || !image.texture || !image.occurrence ||
       !image.configuration || !p->update || image.context_generation!=p->context_generation ||
       !image.width || !image.height || image.width!=p->source_width || image.height!=p->source_height ||
       image.texture==p->textures[0] || image.texture==p->textures[1] ||
       p->dispatch.gl.IsEnabled(gl::FRAMEBUFFER_SRGB))return false;
    p->raster=raster;p->vi_origin=origin;
    if(!save_back(p))return false;
    if(!raster.begin_gpu())return false;
    bool drawn=false;
    try {
        if(gamma)raster.draw(raster.user,{DrawKind::gamma,image.texture,p->fbos[1],image.width,image.height});
        raster.draw(raster.user,{DrawKind::vi,gamma?p->textures[1]:image.texture,0,image.width,image.height});
        drawn=true;
    }catch(...) {} // duplicate-only renderer diagnostics must still close our gateway
    const bool clean=raster.end_gpu();
    if(!drawn || !clean){restore_back(p);p->saved=false;return false;}
    p->image=image;p->replaced=true;return true;
}
bool before_swap(Instance* p,uint32_t origin,const Raster& raster) {
    if(!p || !p->ready || !p->update || p->osd_window || !raster_valid(raster))return false;
    p->raster=raster;p->vi_origin=origin;
    if(!p->saved && !save_back(p))return false;
    p->osd_window=true;p->qualified=true;return true;
}
bool begin_osd(Instance* p) {
    if(!p || !p->osd_window || !p->qualified || p->osd)return false;
    const auto& g=p->dispatch.gl;int32_t fbo=0,buffer=0;uint8_t feedback=0;
    g.GetIntegerv(gl::DRAW_FRAMEBUFFER_BINDING,&fbo);g.GetIntegerv(gl::DRAW_BUFFER0,&buffer);
    g.GetBooleanv(gl::TRANSFORM_FEEDBACK_ACTIVE,&feedback);
    if(fbo || uint32_t(buffer)!=gl::BACK || feedback || g.IsEnabled(gl::DEPTH_TEST) ||
       g.IsEnabled(gl::STENCIL_TEST) || g.IsEnabled(gl::RASTERIZER_DISCARD) ||
       g.IsEnabled(gl::FRAMEBUFFER_SRGB) || !p->raster.begin_gpu()) {
        p->qualified=false;p->published={};return false;
    }
    p->osd_fbo=fbo;p->osd=true;g.BindFramebuffer(gl::DRAW_FRAMEBUFFER,p->fbos[0]);return true;
}
void end_osd(Instance* p) {
    if(!p || !p->osd)return;
    p->dispatch.gl.BindFramebuffer(gl::DRAW_FRAMEBUFFER,p->osd_fbo);p->osd=false;
    if(!p->raster.end_gpu()){p->qualified=false;p->published={};}
}
void after_osd(Instance* p) {
    if(!p)return;
    end_osd(p);p->osd_window=false;
    if(!p->qualified && p->replaced){restore_back(p);p->replaced=false;}
}
void cancel_overlay(Instance* p) {
    if(!p)return;
    end_osd(p);if(p->replaced)restore_back(p);
    p->replaced=p->qualified=false;p->image={};p->published={};
}
void withdraw_image(Instance* p) {
    if(!p)return;
    end_osd(p);if(p->replaced)restore_back(p);
    p->replaced=false;p->image={};
}
void swap_completed(Instance* p,bool success) {
    if(!p)return;
    if(success && p->ready && p->saved && p->qualified && !p->osd && !p->osd_window && p->serial<UINT64_MAX) {
        p->published={p->fbos[0],p->textures[0],p->width,p->height,p->context_generation,
            p->drawable_generation,p->resource_generation,p->vi_origin,++p->serial,p->update};
    }else p->published={};
    reset(p);
}
CleanSurface surface(const Instance* p){return p?p->published:CleanSurface{};}
GhostViImage pending_image(const Instance* p){return p?p->image:GhostViImage{};}
void destroy(Instance* p) {
    if(!p)return;reset(p);p->dispatch.gl.DeleteTextures(2,p->textures);
    p->dispatch.gl.DeleteFramebuffers(2,p->fbos);delete p;
}
void abandon(Instance* p){delete p;}
void set_provider(ImageProvider value,PresentationNotice completed,ImageValidity current){provider=value;notice=completed;validity=current;}
void clean_consumer(bool value){negotiated.store(value,std::memory_order_release);}
bool clean_consumer(){return negotiated.load(std::memory_order_acquire);}
}

#if defined(SM64_GHOST_RENDERER)
#include "GLFunctions.h"
#include "ghost_dispatch.h"
#include "link_dispatch.h"
#include "FrameBuffer.h"
#include "DisplayWindow.h"
#include "Config.h"
#include "N64.h"
#include "Graphics/Context.h"
#include "Graphics/Parameters.h"
#include <memory>

namespace ghost_presentation {
namespace {
Instance* current=nullptr;
renderer::Identity owner{};
std::unique_ptr<graphics::ShaderProgram> gamma_program;
uint32_t source_width=0,source_height=0;
bool osd_phase=false;
struct ViRaster { const GraphicsDrawer::CopyRectParams* params; const CachedTexture* source; };
void raster_draw(void* user,const Draw& draw) {
    auto& drawer=dwnd().getDrawer();auto* vi=static_cast<ViRaster*>(user);
    CachedTexture texture=*vi->source;texture.name=graphics::ObjectHandle(draw.texture);
    texture.frameBufferTexture=CachedTexture::fbOneSample;texture.realWidth=draw.width;texture.realHeight=draw.height;
    GraphicsDrawer::CopyRectParams params=*vi->params;
    if(draw.kind==DrawKind::gamma) {
        params={};params.srcX1=params.dstX1=draw.width;params.srcY1=params.dstY1=draw.height;
        params.srcWidth=params.dstWidth=draw.width;params.srcHeight=params.dstHeight=draw.height;
        params.filter=graphics::textureParameters::FILTER_NEAREST;params.combiner=gamma_program.get();
    }
    params.tex[0]=&texture;params.tex[1]=nullptr;
    gfxContext.bindFramebuffer(graphics::bufferTarget::DRAW_FRAMEBUFFER,graphics::ObjectHandle(draw.framebuffer));
    drawer.copyTexturedRect(params);
}
Raster fallback_raster(){return {nullptr,raster_draw,replay_gl::BeginCapture,replay_gl::EndCapture};}
void revalidate_image() {
    const auto image=pending_image(current);
    if(image.occurrence && (!validity || !validity(image))) {
        withdraw_image(current);if(notice)notice(image,0,false);
    }
}
bool identity_matches() {
    renderer::Identity now{};snapshot::RestoreBindings ignored{};
    return replay_gl::CandidateBindings(&now,&ignored) && now.context==owner.context &&
        now.context_generation==owner.context_generation && now.drawable_generation==owner.drawable_generation &&
        now.owner_thread==owner.owner_thread && now.read_drawable==owner.read_drawable;
}
bool prepare_extent(uint32_t width,uint32_t height) {
    unsigned dw=0,dh=0;
    if(!current || !identity_matches() || !replay_gl::DrawableExtent(&dw,&dh) || !replay_gl::BeginCapture())return false;
    const bool okay=prepare(current,owner.drawable_generation,dw,dh,width,height);
    const bool clean=replay_gl::EndCapture();
    if(okay && clean){source_width=width;source_height=height;return true;}
    return false;
}
}
void context_stopping() {
    osd_phase=false;
    const bool same_context=reinterpret_cast<uintptr_t>(wglGetCurrentContext())==owner.context;
    if(current) {
        if(same_context)destroy(current);
        else abandon(current);
    }
    // The normal staged lifecycle destroys before context loss. If that proof
    // was lost, never run a foreign-context shader destructor; retain one small
    // orphaned host object rather than deleting a reused GPU name.
    if(same_context)gamma_program.reset();else (void)gamma_program.release();
    current=nullptr;owner={};source_width=source_height=0;
}
void context_ready() {
    context_stopping();if(!clean_consumer())return;
    snapshot::RestoreBindings ignored{};
    if(!replay_gl::CandidateBindings(&owner,&ignored) || replay_gl::SourceFormat()==RB_SOURCE_UNKNOWN)return;
    Dispatch dispatch{};
    if(!ghost_dispatch::load(dispatch.gl))return;
    dispatch.CopyTexSubImage2D=reinterpret_cast<decltype(dispatch.CopyTexSubImage2D)>(glCopyTexSubImage2D);
    if(!replay_gl::BeginCapture())return;
    current=create(dispatch,owner.context_generation);
    if(current)gamma_program.reset(gfxContext.createGammaCorrectionShader());
    const bool clean=replay_gl::EndCapture();
    if(!current || !gamma_program || !clean || !prepare_extent(0,0)){context_stopping();return;}
}
void begin_update(uint64_t occurrence) {
    if(current)begin_update(current,occurrence);
}
void draw_vi(const FrameBuffer& source,const GraphicsDrawer::CopyRectParams& params,uint32_t origin,bool split) {
    if(!clean_consumer() || !current || !provider || !validity || !identity_matches() || split ||
       config.frameBufferEmulation.enable==0 || config.frameBufferEmulation.enableOverscan ||
       config.video.multisampling || config.video.fxaa || config.generalEmulation.enableBlitScreenWorkaround ||
       !source.m_pTexture || source.m_pTexture->frameBufferTexture!=CachedTexture::fbOneSample)return;
    // Upstream's postprocessor caches its result by width alone. A stale
    // height/scale must not map the ghost through a differently sized image.
    if(params.srcWidth!=source.m_pTexture->realWidth || params.srcHeight!=source.m_pTexture->realHeight)return;
    ViSource input{uint32_t(source.m_FBO),uint32_t(source.m_pTexture->name),source.m_pTexture->realWidth,
        source.m_pTexture->realHeight,origin,owner.context_generation,owner.drawable_generation};
    GhostViImage image{};
    if(!provider(input,&image) || !clean_consumer())return;
    if((source_width!=input.width || source_height!=input.height) && !prepare_extent(input.width,input.height))return;
    ViRaster vi{&params,source.m_pTexture};Raster raster{&vi,raster_draw,replay_gl::BeginCapture,replay_gl::EndCapture};
    const bool gamma=((*REG.VI_STATUS & 8) | config.gammaCorrection.force)!=0;
    if(!replace_vi(current,image,origin,gamma,raster) && notice)notice(image,0,false);
}
void before_swap() {
    if(!current)return;
    if(!clean_consumer() || !identity_matches()){cancel_overlay(current);return;}
    revalidate_image();
    osd_phase=before_swap(current,REG.VI_ORIGIN?*REG.VI_ORIGIN:0,fallback_raster());
}
bool begin_osd_raster(){return osd_phase && begin_osd(current);}
void end_osd_raster(){end_osd(current);}
void after_osd() {
    if(!current)return;
    after_osd(current);osd_phase=false;
    if(!clean_consumer())cancel_overlay(current);
    else revalidate_image();
}
void swap_completed(bool success) {
    if(!current)return;
    const auto image=pending_image(current);
    swap_completed(current,success && clean_consumer() && identity_matches());
    const auto clean=surface(current);
    if(image.occurrence && notice)notice(image,clean.presentation_serial,success && clean.presentation_serial);
}
bool clean_surface(CleanSurface* out) {
    if(out)*out={};
    if(!out || !clean_consumer() || !current || !identity_matches())return false;
    *out=surface(current);return out->presentation_serial!=0;
}
}
#endif
