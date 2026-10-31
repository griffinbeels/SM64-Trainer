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
    int32_t draw_buffers=0;
    enum { CLEAN=0, GAMMA=1, DISPLAY=2 };
    uint32_t textures[3]={},fbos[3]={}; // clean drawable, gamma result, displayed picture (lazy)
    uint64_t update=0,serial=0;
    uint32_t vi_origin=0;
    Raster raster{};
    GhostViImage image{};
    CleanSurface published{};
    bool display_copied=false;uint32_t display_generation=0;CleanSurface display_published{};
    bool ready=false,saved=false,replaced=false,osd_window=false,osd=false,qualified=false;
    // A ghost-only scope (the clean save before a ghost, the ghost's VI draw,
    // its OSD mirror or display copy) closed dirty: that cost the recording
    // one witness invalidation, so this instance draws no more ghosts. The
    // clean presentation of plain frames needs no gateway and carries on.
    bool ghost_faulted=false;
    int32_t osd_fbo=0;
    explicit Instance(const Dispatch& d,uint32_t generation):dispatch(d),context_generation(generation) {}
};
namespace {
std::atomic<bool> negotiated{false},display_wanted{false};
ImageProvider provider=nullptr;
PresentationNotice notice=nullptr;
ImageValidity validity=nullptr;
bool raster_valid(const Raster& r) {return r.draw && r.begin_gpu && r.end_gpu;}
void reset(Instance* p) {
    end_osd(p);p->saved=p->replaced=p->osd_window=p->qualified=p->display_copied=false;p->image={};
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
Known known(const Raster& raster) {
    Known out{};
    if(!raster.known || !raster.known(&out))out={};
    return out;
}
// save_back changes exactly these: the read framebuffer, unit 0's 2D texture
// and the active unit (the default framebuffer's read selector it restores
// inline). The full gl::Guard queried and restored ~77 values every frame for
// them (~25 of its ~30 us); this restores three, asking only what the
// renderer's record does not hold.
struct CopyGuard {
    const ghost_compositor::Dispatch& g; int32_t read_fbo=0,unit=0,texture=0;
    CopyGuard(const ghost_compositor::Dispatch& d,const Known& k):g(d) {
        if(k.fields&Known::read_framebuffer)read_fbo=int32_t(k.read);
        else g.GetIntegerv(gl::READ_FRAMEBUFFER_BINDING,&read_fbo);
        if(k.fields&Known::texture_unit) {unit=int32_t(gl::TEXTURE0+k.active_unit);texture=int32_t(k.unit0_texture);}
        else {g.GetIntegerv(gl::ACTIVE_TEXTURE,&unit);g.ActiveTexture(gl::TEXTURE0);g.GetIntegerv(gl::TEXTURE_BINDING_2D,&texture);}
    }
    ~CopyGuard() {
        g.ActiveTexture(gl::TEXTURE0);g.BindTexture(gl::TEXTURE_2D,uint32_t(texture));
        g.ActiveTexture(uint32_t(unit));g.BindFramebuffer(gl::READ_FRAMEBUFFER,uint32_t(read_fbo));
    }
};
// restore_back changes exactly these: the read and draw framebuffers, scissor
// at viewport 0 and sRGB writes (the default framebuffer's draw selectors it
// restores inline; the read selector it sets is its own framebuffer's).
struct BlitGuard {
    const ghost_compositor::Dispatch& g; int32_t read_fbo=0,draw_fbo=0; uint8_t scissor=0,srgb=0;
    BlitGuard(const ghost_compositor::Dispatch& d,const Known& k):g(d) {
        if(k.fields&Known::read_framebuffer)read_fbo=int32_t(k.read);
        else g.GetIntegerv(gl::READ_FRAMEBUFFER_BINDING,&read_fbo);
        if(k.fields&Known::draw_framebuffer)draw_fbo=int32_t(k.draw);
        else g.GetIntegerv(gl::DRAW_FRAMEBUFFER_BINDING,&draw_fbo);
        if(k.fields&Known::scissor_test)scissor=k.scissor_enabled;
        else g.GetBooleani_v(gl::SCISSOR_TEST,0,&scissor);
        if(k.fields&Known::srgb)srgb=k.srgb_enabled;else srgb=g.IsEnabled(gl::FRAMEBUFFER_SRGB);
    }
    ~BlitGuard() {
        (scissor ? g.Enablei : g.Disablei)(gl::SCISSOR_TEST,0);(srgb ? g.Enable : g.Disable)(gl::FRAMEBUFFER_SRGB);
        g.BindFramebuffer(gl::READ_FRAMEBUFFER,uint32_t(read_fbo));g.BindFramebuffer(gl::DRAW_FRAMEBUFFER,uint32_t(draw_fbo));
    }
};
// BACK -> one of our surfaces (clean or display), inside the diagnostic gateway.
bool copy_back(Instance* p,uint32_t index) {
    if(!p->raster.begin_gpu())return false;
    {
        const auto& g=p->dispatch.gl;const auto k=known(p->raster);CopyGuard guard(g,k);
        g.BindFramebuffer(gl::READ_FRAMEBUFFER,0);
        int32_t selector=int32_t(k.default_read);
        if(!(k.fields&Known::default_read_buffer))g.GetIntegerv(gl::READ_BUFFER,&selector);
        g.ReadBuffer(gl::BACK);
        g.ActiveTexture(gl::TEXTURE0);g.BindTexture(gl::TEXTURE_2D,p->textures[index]);
        // CopyTexSubImage transfers the stored RGB values without sRGB conversion.
        p->dispatch.CopyTexSubImage2D(gl::TEXTURE_2D,0,0,0,0,0,p->width,p->height);
        g.ReadBuffer(selector); // restore DEFAULT selector even if another FBO was originally read
    }
    const bool clean=p->raster.end_gpu();
    if(!clean)p->ghost_faulted=true;
    return clean;
}
bool save_back(Instance* p) {
    p->published=p->display_published={};p->qualified=false;
    p->saved=copy_back(p,Instance::CLEAN);return p->saved;
}
// The display surface's first use, inside the gateway. Allocation binds a
// framebuffer and the unpack state, so the whole state is put back.
bool allocate_display(Instance* p) {
    if(p->textures[Instance::DISPLAY])return true;
    if(p->display_generation==UINT32_MAX)return false;
    const auto& g=p->dispatch.gl;gl::Guard guard(g);
    g.GenTextures(1,&p->textures[Instance::DISPLAY]);g.GenFramebuffers(1,&p->fbos[Instance::DISPLAY]);
    g.BindBuffer(gl::PIXEL_UNPACK_BUFFER,0);g.PixelStorei(gl::UNPACK_ALIGNMENT,1);
    g.PixelStorei(gl::UNPACK_ROW_LENGTH,0);g.PixelStorei(gl::UNPACK_SKIP_ROWS,0);g.PixelStorei(gl::UNPACK_SKIP_PIXELS,0);
    if(p->textures[Instance::DISPLAY] && p->fbos[Instance::DISPLAY] && allocate(p,Instance::DISPLAY,p->width,p->height)) {
        ++p->display_generation;return true;
    }
    g.DeleteTextures(1,&p->textures[Instance::DISPLAY]);g.DeleteFramebuffers(1,&p->fbos[Instance::DISPLAY]);
    p->textures[Instance::DISPLAY]=p->fbos[Instance::DISPLAY]=0;return false;
}
void restore_back(Instance* p) {
    if(!p->saved)return;
    const auto& g=p->dispatch.gl;BlitGuard guard(g,known(p->raster));
    g.Disablei(gl::SCISSOR_TEST,0);g.Disable(gl::FRAMEBUFFER_SRGB);
    g.BindFramebuffer(gl::READ_FRAMEBUFFER,p->fbos[0]);g.ReadBuffer(gl::COLOR_ATTACHMENT0);
    g.BindFramebuffer(gl::DRAW_FRAMEBUFFER,0);
    uint32_t selectors[16]={};bool multiple=false;
    for(int32_t i=0;i<p->draw_buffers;++i) {
        int32_t selector=0;g.GetIntegerv(gl::DRAW_BUFFER0+i,&selector);selectors[i]=uint32_t(selector);
        if(i && selector)multiple=true;
    }
    g.DrawBuffer(gl::BACK);
    // Best-effort recovery also runs after the diagnostic gateway has refused.
    // No game framebuffer is touched, and no error is consumed here.
    g.BlitFramebuffer(0,0,p->width,p->height,0,0,p->width,p->height,gl::COLOR_BUFFER_BIT,gl::NEAREST);
    if(multiple)p->dispatch.DrawBuffers(p->draw_buffers,selectors);
    else g.DrawBuffer(selectors[0]); // also preserves aggregate FRONT/BACK selectors
}
}
Instance* create(const Dispatch& d,uint32_t generation) {
    if(!generation || !d.CopyTexSubImage2D || !d.DrawBuffers)return nullptr;
#define GP_CHECK(result,name,args) if(!d.gl.name)return nullptr;
    GC_GL_FUNCTIONS(GP_CHECK)
#undef GP_CHECK
    int32_t major=0,minor=0;d.gl.GetIntegerv(gl::MAJOR_VERSION,&major);d.gl.GetIntegerv(gl::MINOR_VERSION,&minor);
    if(major<4 || (major==4 && minor<3))return nullptr;
    int32_t buffers=0;d.gl.GetIntegerv(0x8824,&buffers); // GL_MAX_DRAW_BUFFERS
    if(buffers<1 || buffers>16)return nullptr;
    auto* p=new(std::nothrow) Instance(d,generation);if(p)p->draw_buffers=buffers;return p;
}
bool prepare(Instance* p,uint32_t drawable,uint32_t width,uint32_t height,uint32_t sw,uint32_t sh) {
    if(!p)return false;
    if(!drawable || !width || !height || width>8192 || height>8192 || sw>8192 || sh>8192 || (!sw!=!sh)) {
        reset(p);p->ready=false;p->published=p->display_published={};return false;
    }
    if(p->ready && drawable==p->drawable_generation && width==p->width && height==p->height &&
       sw==p->source_width && sh==p->source_height)return true;
    reset(p);p->published=p->display_published={};p->ready=false;
    if(p->resource_generation==UINT32_MAX)return false;
    const auto& g=p->dispatch.gl;gl::Guard guard(g);
    if(!p->textures[0]){g.GenTextures(2,p->textures);g.GenFramebuffers(2,p->fbos);}
    if(!p->textures[0] || !p->textures[1] || !p->fbos[0] || !p->fbos[1])return false;
    g.BindBuffer(gl::PIXEL_UNPACK_BUFFER,0);g.PixelStorei(gl::UNPACK_ALIGNMENT,1);
    g.PixelStorei(gl::UNPACK_ROW_LENGTH,0);g.PixelStorei(gl::UNPACK_SKIP_ROWS,0);g.PixelStorei(gl::UNPACK_SKIP_PIXELS,0);
    if(!allocate(p,Instance::CLEAN,width,height) || (sw && !allocate(p,Instance::GAMMA,sw,sh)))return false;
    // An allocated display surface follows the drawable; a failed one is
    // dropped and allocated again by the next record_display that needs it.
    if(p->textures[Instance::DISPLAY]) {
        if(p->display_generation<UINT32_MAX && allocate(p,Instance::DISPLAY,width,height))++p->display_generation;
        else {g.DeleteTextures(1,&p->textures[Instance::DISPLAY]);g.DeleteFramebuffers(1,&p->fbos[Instance::DISPLAY]);
            p->textures[Instance::DISPLAY]=p->fbos[Instance::DISPLAY]=0;}
    }
    p->drawable_generation=drawable;p->width=width;p->height=height;
    p->source_width=sw;p->source_height=sh;++p->resource_generation;p->ready=true;return true;
}
void begin_update(Instance* p,uint64_t occurrence) {
    if(!p)return;
    reset(p);p->update=occurrence;
}
bool replace_vi(Instance* p,const GhostViImage& image,uint32_t origin,bool gamma,const Raster& raster) {
    // Each refusal names itself for the picture's facts (ghost_draw_facts.h).
    const uint32_t refused=!p || !p->ready || !p->update?GDF_PRESENT_NOT_READY:p->saved?GDF_PRESENT_REPEAT:
       p->ghost_faulted?GDF_PRESENT_FAULTED:!raster_valid(raster)?GDF_PRESENT_DRAW:
       !image.texture || !image.occurrence || !image.configuration || image.context_generation!=p->context_generation ||
       !image.width || !image.height || image.width!=p->source_width || image.height!=p->source_height ||
       image.texture==p->textures[0] || image.texture==p->textures[1] || image.texture==p->textures[2]?GDF_PRESENT_IMAGE:0u;
    if(refused){ghost_facts::present_refuse(refused);return false;}
    const auto k=known(raster);
    if((k.fields&Known::srgb) ? k.srgb_enabled!=0 : p->dispatch.gl.IsEnabled(gl::FRAMEBUFFER_SRGB)!=0) {
        ghost_facts::present_refuse(GDF_PRESENT_SRGB);return false;
    }
    p->raster=raster;p->vi_origin=origin;
    if(!save_back(p)){ghost_facts::present_refuse(GDF_PRESENT_COPY);return false;}
    if(!raster.begin_gpu()){p->saved=false;ghost_facts::present_refuse(GDF_PRESENT_DRAW);return false;}
    bool drawn=false;
    try {
        if(gamma)raster.draw(raster.user,{DrawKind::gamma,image.texture,p->fbos[1],image.width,image.height,image.framebuffer});
        raster.draw(raster.user,{DrawKind::vi,gamma?p->textures[1]:image.texture,0,image.width,image.height,
            gamma?p->fbos[1]:image.framebuffer});
        drawn=true;
    }catch(...) {} // duplicate-only renderer diagnostics must still close our gateway
    const bool clean=raster.end_gpu();
    if(!clean)p->ghost_faulted=true;
    if(!drawn || !clean){restore_back(p);p->saved=false;ghost_facts::present_refuse(GDF_PRESENT_DRAW);return false;}
    p->image=image;p->replaced=true;return true;
}
bool before_swap(Instance* p,uint32_t origin,const Raster& raster) {
    if(!p || !p->ready || !p->update || p->osd_window || !raster_valid(raster))return false;
    p->raster=raster;p->vi_origin=origin;
    // The original BACK is already clean when no ghost replaced the VI.
    // Qualify its successful swap without another full-drawable copy or OSD
    // mirror; SourceV3 names DEFAULT_FRONT for this occurrence after the swap.
    p->published=p->display_published={};
    p->osd_window=true;p->qualified=true;return true;
}
bool begin_osd(Instance* p) {
    if(!p || !p->saved || !p->osd_window || !p->qualified || p->osd)return false;
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
    if(!p->raster.end_gpu()){p->qualified=false;p->published={};p->ghost_faulted=true;}
}
void after_osd(Instance* p) {
    if(!p)return;
    end_osd(p);p->osd_window=false;
    if(!p->qualified && p->replaced){restore_back(p);p->replaced=false;}
}
// HIS CHOICE, 2026-10-03 (option A, "the most faithful ghost recreation"):
// with Ghosts in recording on, a second copy keeps exactly what the window
// shows -- ghosts, names, corner inputs and OSD -- while the clean recording
// stays clean ("they will want the clean recording 100% of the time"). One
// more copy of BACK into the display surface, only on frames that drew a
// ghost and only while the ghost copy's lane consumes it. A failed copy
// publishes no display picture; the clean picture is unaffected.
void record_display(Instance* p) {
    if(!p || !p->replaced || !p->qualified || p->ghost_faulted || !p->image.record_display || p->osd || p->osd_window ||
       !display_consumer())return;
    if(!p->textures[Instance::DISPLAY]) {
        if(!p->raster.begin_gpu())return;
        const bool made=allocate_display(p);
        if(!p->raster.end_gpu()){p->ghost_faulted=true;return;}
        if(!made)return;
    }
    p->display_copied=copy_back(p,Instance::DISPLAY);
}
void cancel_overlay(Instance* p) {
    if(!p)return;
    end_osd(p);if(p->replaced)restore_back(p);
    p->replaced=p->qualified=false;p->image={};p->published=p->display_published={};
}
void withdraw_image(Instance* p) {
    if(!p)return;
    end_osd(p);if(p->replaced)restore_back(p);
    p->replaced=false;p->image={};
}
void swap_completed(Instance* p,bool success) {
    if(!p)return;
    if(success && p->ready && p->qualified && !p->osd && !p->osd_window && p->serial<UINT64_MAX) {
        p->published={p->saved?p->fbos[0]:0,p->saved?p->textures[0]:0,p->width,p->height,p->context_generation,
            p->drawable_generation,p->saved?p->resource_generation:0,p->vi_origin,++p->serial,p->update};
        // The display copy shares the clean picture's serial and update.
        p->display_published=p->display_copied ? CleanSurface{p->fbos[Instance::DISPLAY],p->textures[Instance::DISPLAY],
            p->width,p->height,p->context_generation,p->drawable_generation,p->display_generation,p->vi_origin,
            p->serial,p->update} : CleanSurface{};
    }else p->published=p->display_published={};
    reset(p);
}
CleanSurface surface(const Instance* p){return p?p->published:CleanSurface{};}
CleanSurface display(const Instance* p){return p?p->display_published:CleanSurface{};}
GhostViImage pending_image(const Instance* p){return p?p->image:GhostViImage{};}
void destroy(Instance* p) {
    if(!p)return;reset(p);p->dispatch.gl.DeleteTextures(3,p->textures);
    p->dispatch.gl.DeleteFramebuffers(3,p->fbos);delete p;
}
void abandon(Instance* p){delete p;}
void set_provider(ImageProvider value,PresentationNotice completed,ImageValidity current){provider=value;notice=completed;validity=current;}
void clean_consumer(bool value){negotiated.store(value,std::memory_order_release);}
bool clean_consumer(){return negotiated.load(std::memory_order_acquire);}
void display_consumer(bool value){display_wanted.store(value,std::memory_order_release);}
bool display_consumer(){return display_wanted.load(std::memory_order_acquire);}
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
uint32_t gamma_force=0;float gamma_level=0;
uint32_t source_width=0,source_height=0;
bool osd_phase=false;
struct ViRaster {
    const GraphicsDrawer::CopyRectParams* params;
    const CachedTexture* source;
    const GraphicsDrawer::BlitOrCopyRectParams* screen;
};
void raster_draw(void* user,const Draw& draw) {
    auto& drawer=dwnd().getDrawer();auto* vi=static_cast<ViRaster*>(user);
    CachedTexture texture=*vi->source;texture.name=graphics::ObjectHandle(draw.texture);
    texture.frameBufferTexture=CachedTexture::fbOneSample;texture.realWidth=draw.width;texture.realHeight=draw.height;
    if(vi->screen && draw.kind==DrawKind::vi) {
        // FBE-off presents pixel coordinates through the actual renderer blit,
        // whose fallback uses its own texture coordinates. Keep both paths,
        // including padded extents, flips and clipping, exactly as upstream.
        auto params=*vi->screen;
        params.readBuffer=graphics::ObjectHandle(draw.source_framebuffer);
        params.drawBuffer=graphics::ObjectHandle(draw.framebuffer);
        params.tex[0]=&texture;params.tex[1]=nullptr;
        drawer.blitOrCopyTexturedRect(params);return;
    }
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
// What the dispatch observed of the bindings a presentation step changes.
bool known_state(Known* out) {
    *out={};
    replay_gl::RasterState raster{};
    if(replay_gl::Raster(&raster) && raster.draw_known) {out->fields|=Known::draw_framebuffer;out->draw=raster.draw_framebuffer;}
    renderer::Identity identity{};snapshot::RestoreBindings bindings{};
    if(replay_gl::CandidateBindings(&identity,&bindings)) {
        out->fields|=Known::read_framebuffer|Known::default_read_buffer;
        out->read=bindings.read_framebuffer;out->default_read=bindings.source_read_buffer;
    }
    unsigned unit=0;GLuint texture=0;
    if(replay_gl::ActiveUnit(&unit) && replay_gl::Texture2D(0,&texture)) {
        out->fields|=Known::texture_unit;out->active_unit=unit;out->unit0_texture=texture;
    }
    bool on=false;
    if(replay_gl::Enabled(GL_FRAMEBUFFER_SRGB,&on)){out->fields|=Known::srgb;out->srgb_enabled=on;}
    if(replay_gl::Enabled(GL_SCISSOR_TEST,&on)){out->fields|=Known::scissor_test;out->scissor_enabled=on;}
    return true;
}
Raster fallback_raster(){return {nullptr,raster_draw,replay_gl::BeginCapture,replay_gl::EndCapture,known_state};}
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
bool owns_context(const renderer::Identity& now) {
    return owner.context && now.context==owner.context && now.context_generation==owner.context_generation &&
        now.owner_thread==owner.owner_thread;
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
    if(notice)notice({},0,false);
    osd_phase=false;
    renderer::Identity now{};snapshot::RestoreBindings ignored{};
    const bool same_context=replay_gl::CandidateBindings(&now,&ignored) && owns_context(now);
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
void drawable_changing() {
    if(notice)notice({},0,false);
    osd_phase=false;
    if(current) {
        renderer::Identity now{};snapshot::RestoreBindings ignored{};
        if(!replay_gl::CandidateBindings(&now,&ignored) || !owns_context(now)) {
            context_stopping();return;
        }
        reset(current);current->update=0;current->published=current->display_published={};
    }
}
void context_ready() {
    // How each attempt ends is the presentation's fact (ghost_draw_facts.h).
    if(!clean_consumer()){ghost_facts::ready(GDF_READY_NO_CLEAN_CONSUMER);context_stopping();return;}
    renderer::Identity now{};
    snapshot::RestoreBindings ignored{};
    if(!replay_gl::CandidateBindings(&now,&ignored)){ghost_facts::ready(GDF_READY_NO_IDENTITY);context_stopping();return;}
    if(replay_gl::SourceFormat()==RB_SOURCE_UNKNOWN){ghost_facts::ready(GDF_READY_SOURCE_FORMAT);context_stopping();return;}
    if(current && gamma_program && owns_context(now)) {
        drawable_changing();owner=now;source_width=source_height=0;
        // The pinned shader captures these uniforms in its constructor. A
        // settings-driven drawer restart must apply the new gamma too.
        if(gamma_force!=config.gammaCorrection.force || gamma_level!=config.gammaCorrection.level) {
            if(!replay_gl::BeginCapture()){current->ready=false;ghost_facts::ready(GDF_READY_SCOPE);return;}
            gamma_program.reset(gfxContext.createGammaCorrectionShader());
            gamma_force=config.gammaCorrection.force;gamma_level=config.gammaCorrection.level;
            const bool clean=replay_gl::EndCapture();
            if(!gamma_program || !clean) {
                ghost_facts::ready(!gamma_program?GDF_READY_GAMMA:GDF_READY_DIRTY);context_stopping();return;
            }
        }
        if(!prepare_extent(0,0)){current->ready=false;ghost_facts::ready(GDF_READY_EXTENT);}
        else ghost_facts::ready(GDF_READY_READY);
        return;
    }
    context_stopping();owner=now;
    Dispatch dispatch{};
    if(!ghost_dispatch::load(dispatch.gl)){ghost_facts::ready(GDF_READY_DISPATCH);return;}
    dispatch.CopyTexSubImage2D=reinterpret_cast<decltype(dispatch.CopyTexSubImage2D)>(glCopyTexSubImage2D);
    dispatch.DrawBuffers=reinterpret_cast<decltype(dispatch.DrawBuffers)>(g_glDrawBuffers);
    if(!replay_gl::BeginCapture()){ghost_facts::ready(GDF_READY_SCOPE);return;}
    current=create(dispatch,owner.context_generation);
    if(current) {
        gamma_program.reset(gfxContext.createGammaCorrectionShader());
        gamma_force=config.gammaCorrection.force;gamma_level=config.gammaCorrection.level;
    }
    const bool clean=replay_gl::EndCapture();
    // In the original order: the extent is prepared only for a clean setup.
    const uint32_t step=!current?GDF_READY_CREATE:!gamma_program?GDF_READY_GAMMA:!clean?GDF_READY_DIRTY:
        !prepare_extent(0,0)?GDF_READY_EXTENT:GDF_READY_READY;
    ghost_facts::ready(step);
    if(step!=GDF_READY_READY){context_stopping();return;}
}
void begin_update(uint64_t occurrence) {
    if(current)begin_update(current,occurrence);
}
static void draw_vi_image(const FrameBuffer& source,const GraphicsDrawer::CopyRectParams& params,
                          uint32_t origin,bool split,const GraphicsDrawer::BlitOrCopyRectParams* screen) {
    // This picture's first refusal, published however it returns (ghost_draw_facts.h).
    ghost_facts::Present present;
    const uint32_t refused=!clean_consumer()?GDF_PRESENT_NO_CLEAN_CONSUMER:!current?GDF_PRESENT_NO_PRESENTATION:
       !provider || !validity?GDF_PRESENT_NO_PROVIDER:!identity_matches()?GDF_PRESENT_IDENTITY:split?GDF_PRESENT_SPLIT:
       config.frameBufferEmulation.enableOverscan?GDF_PRESENT_OVERSCAN:
       config.video.multisampling?GDF_PRESENT_MULTISAMPLING:config.video.fxaa?GDF_PRESENT_FXAA:
       config.generalEmulation.enableBlitScreenWorkaround?GDF_PRESENT_BLIT_WORKAROUND:
       !source.m_pTexture || source.m_pTexture->frameBufferTexture!=CachedTexture::fbOneSample?GDF_PRESENT_SOURCE:0u;
    if(refused)return present.refuse(refused);
    // Upstream's postprocessor caches its result by width alone. A stale
    // height/scale must not map the ghost through a differently sized image.
    if(!params.tex[0] || params.tex[0]->realWidth!=source.m_pTexture->realWidth ||
       params.tex[0]->realHeight!=source.m_pTexture->realHeight)return present.refuse(GDF_PRESENT_SOURCE_EXTENT);
    if(!screen && (params.srcWidth!=source.m_pTexture->realWidth || params.srcHeight!=source.m_pTexture->realHeight))
        return present.refuse(GDF_PRESENT_SOURCE_EXTENT);
    ViSource input{uint32_t(source.m_FBO),uint32_t(source.m_pTexture->name),source.m_pTexture->realWidth,
        source.m_pTexture->realHeight,origin,owner.context_generation,owner.drawable_generation};
    GhostViImage image{};
    if(!provider(input,&image))return present.refuse(GDF_PRESENT_NO_IMAGE);
    if(!clean_consumer())return present.refuse(GDF_PRESENT_NO_CLEAN_CONSUMER);
    if(screen && !image.framebuffer)return present.refuse(GDF_PRESENT_NO_DISPLAY_BUFFER);
    if((source_width!=input.width || source_height!=input.height) && !prepare_extent(input.width,input.height))
        return present.refuse(GDF_PRESENT_EXTENT);
    ViRaster vi{&params,source.m_pTexture,screen};
    Raster raster{&vi,raster_draw,replay_gl::BeginCapture,replay_gl::EndCapture,known_state};
    const bool gamma=((*REG.VI_STATUS & 8) | config.gammaCorrection.force)!=0;
    if(!replace_vi(current,image,origin,gamma,raster) && notice)notice(image,0,false);
}
void draw_vi(const FrameBuffer& source,const GraphicsDrawer::CopyRectParams& params,uint32_t origin,bool split) {
    if(config.frameBufferEmulation.enable)draw_vi_image(source,params,origin,split,nullptr);
}
void draw_screen_vi(const FrameBuffer& source,const GraphicsDrawer::BlitOrCopyRectParams& params,uint32_t origin) {
    if(!config.frameBufferEmulation.enable)draw_vi_image(source,params,origin,false,&params);
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
    else {revalidate_image();record_display(current);}
}
void swap_completed(bool success) {
    // Once a swap: whether the presentation can carry a ghost at all.
    ghost_facts::presentation(current && current->ready,current && current->ghost_faulted);
    const auto image=pending_image(current);
    swap_completed(current,current && success && clean_consumer() && identity_matches());
    const auto clean=surface(current);
    // A clean-only or refused presentation retires a previously visible ghost.
    // Report every swap, including when the presentation context is unavailable.
    if(notice)notice(image,clean.presentation_serial,image.occurrence && success && clean.presentation_serial);
}
bool clean_surface(CleanSurface* out) {
    if(out)*out={};
    if(!out || !clean_consumer() || !current || !identity_matches())return false;
    *out=surface(current);return out->presentation_serial!=0;
}
bool display_surface(CleanSurface* out) {
    if(out)*out={};
    if(!out || !clean_consumer() || !display_consumer() || !current || !identity_matches())return false;
    const auto clean=surface(current),shown=display(current);
    if(!clean.presentation_serial || shown.presentation_serial!=clean.presentation_serial)return false;
    *out=shown;return true;
}
}
#endif
