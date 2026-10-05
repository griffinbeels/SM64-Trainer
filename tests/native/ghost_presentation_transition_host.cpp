/* Actual presentation lifecycle glue and pinned staged DisplayWindow methods,
 * against CPU resource/identity shells. No WGL context, GPU calls or window. */
#include "ghost_presentation.h"
#include "renderer_gl_state.h"
#include "source_format.h"
#include <cstdio>
#include <memory>
#include <stdexcept>

namespace {
unsigned checks=0,creates=0,destroys=0,abandons=0,shader_creates=0,shader_deletes=0,allocations=0,notices=0;
unsigned foreign_resets=0;
unsigned drawable_width=640,drawable_height=480;
bool identity_available=true,format_known=true,extent_available=true,extent_succeeds=true,negotiated=true;
renderer::Identity live{1,1,1,1,1};
void check(bool okay,const char* what){++checks;if(!okay)throw std::runtime_error(what);}
}
namespace replay_gl {
bool CandidateBindings(renderer::Identity* out,snapshot::RestoreBindings* bindings) {
    if(!identity_available || !live.context || !live.context_generation || !live.owner_thread)return false;
    *out=live;if(bindings)*bindings={};return true;
}
unsigned SourceFormat(){return format_known?2u:RB_SOURCE_UNKNOWN;}
bool DrawableExtent(unsigned* w,unsigned* h){*w=drawable_width;*h=drawable_height;return extent_available;}
bool BeginCapture(){return true;}
bool EndCapture(){return true;}
}
namespace graphics {
struct ShaderProgram {~ShaderProgram(){++shader_deletes;}};
}
struct Context {
    graphics::ShaderProgram* createGammaCorrectionShader(){++shader_creates;return new graphics::ShaderProgram;}
    void destroy(){}
} gfxContext;
struct Config {struct {uint32_t force=0;float level=2;} gammaCorrection;} config;
void GC_GL_CALL copy_texture(uint32_t,int32_t,int32_t,int32_t,int32_t,int32_t,int32_t,int32_t){}
void GC_GL_CALL draw_buffers(int32_t,const uint32_t*){}
#define glCopyTexSubImage2D copy_texture
auto g_glDrawBuffers=draw_buffers;
namespace ghost_dispatch {bool load(ghost_compositor::Dispatch&){return true;}}
namespace ghost_presentation {
struct Instance {
    uint32_t context_generation=0,drawable_generation=0,width=0,height=0,source_width=0,source_height=0;
    uint64_t update=0;
    bool ready=false;
    CleanSurface published{},display_published{};
};
namespace {
Instance* current=nullptr;
renderer::Identity owner{};
std::unique_ptr<graphics::ShaderProgram> gamma_program;
uint32_t gamma_force=0;float gamma_level=0;
uint32_t source_width=0,source_height=0;
bool osd_phase=false;
void announced(const GhostViImage&,uint64_t,bool){++notices;}
PresentationNotice notice=announced;
void reset(Instance* p){if(p->context_generation!=live.context_generation)++foreign_resets;}
}
bool clean_consumer(){return negotiated;}
Instance* create(const Dispatch&,uint32_t generation){++creates;auto* p=new Instance;p->context_generation=generation;return p;}
void destroy(Instance* p){++destroys;delete p;}
void abandon(Instance* p){++abandons;delete p;}
bool prepare(Instance* p,uint32_t generation,uint32_t w,uint32_t h,uint32_t sw,uint32_t sh){
    p->published=p->display_published={};
    if(!extent_succeeds){p->ready=false;return false;}
    if(!p->ready || p->drawable_generation!=generation || p->width!=w || p->height!=h ||
       p->source_width!=sw || p->source_height!=sh)++allocations;
    p->drawable_generation=generation;p->width=w;p->height=h;p->source_width=sw;p->source_height=sh;p->ready=true;
    return true;
}
#include "actual_presentation_lifetime.inc"
bool published(){return current && current->ready && current->published.presentation_serial && identity_matches();}
void publish(){check(current && current->ready,"publication needs prepared extent");current->update=1;current->published.presentation_serial=1;}
}

enum class DrawingState {Non,Other};
struct Drawer {
    void _initData(){}
    void _destroyData(){check(!ghost_presentation::published(),"staged transition invalidates before drawer destruction");}
    DrawingState getDrawingState(){return DrawingState::Other;}
};
struct DisplayWindow {
    bool m_bToggleFullscreen=false,m_bResizeWindow=false,m_bFullscreen=false,resize_ok=true;
    Drawer m_drawer;
    bool changeWindow();bool resizeWindow();void stop();void closeWindow();
    void _changeWindow(){_resizeWindow();}
    bool _resizeWindow(){++live.drawable_generation;++live.read_drawable;drawable_width+=16;drawable_height+=12;return resize_ok;}
    bool _start(){++live.context_generation;return true;}
    void _stop(){live={};}
    void updateScale(){}
};
#include "actual_presentation_window.inc"

int main(){try {
    using namespace ghost_presentation;
    context_ready();check(creates==1 && shader_creates==1 && allocations==1,"initial setup prewarms and prepares without a ghost");
    context_ready();check(creates==1 && shader_creates==1 && allocations==1,"stable setup allocates nothing");
    DisplayWindow window;
    publish();window.m_bResizeWindow=true;check(window.resizeWindow(),"pinned resize forwards");
    check(creates==1 && shader_creates==1 && destroys==0 && shader_deletes==0,"drawable resize preserves context resources");
    check(allocations==2 && !published(),"resize prepares extent and retires old picture");
    publish();window.m_bToggleFullscreen=true;check(window.changeWindow(),"pinned fullscreen forwards");
    check(creates==1 && shader_creates==1 && destroys==0 && shader_deletes==0,"fullscreen preserves context resources");
    check(allocations==3 && !published(),"fullscreen refreshes extent and image identity");
    publish();++live.drawable_generation;check(!published(),"new drawable generation refuses old tokens before refresh");
    context_ready();check(!published() && owner.drawable_generation==live.drawable_generation,"ready renews drawable identity");
    publish();source_width=320;source_height=240;extent_available=false;++live.drawable_generation;context_ready();
    check(!published() && !current->ready && !source_width && !source_height && creates==1 && shader_creates==1,
          "missing extent refuses old picture without recompiling or keeping old source dimensions");
    extent_available=true;context_ready();publish();extent_succeeds=false;++live.drawable_generation;context_ready();
    check(!published() && !current->ready,"failed allocation refuses old picture");
    extent_succeeds=true;context_ready();
    unsigned old_deletes=shader_deletes,old_destroys=destroys;
    ++live.context_generation;drawable_changing();
    check(!current && !gamma_program && !foreign_resets,"pre-change invalidation abandons foreign state without restoration");
    context_ready();
    check(creates==2 && shader_creates==2 && abandons==1 && shader_deletes==old_deletes && destroys==old_destroys,
          "reused context handle abandons old generation without foreign deletes");
    ++live.context;context_ready();check(creates==3 && shader_creates==3 && abandons==2,"new context prewarms again");
    ++live.owner_thread;context_ready();check(creates==4 && shader_creates==4 && abandons==3,"new owner cannot retain old resources");
    publish();identity_available=false;context_ready();check(!current && !gamma_program && abandons==4,"missing identity safely abandons");
    identity_available=true;context_ready();publish();format_known=false;context_ready();
    check(!current && !gamma_program && destroys==old_destroys+1 && shader_deletes==old_deletes+1,"missing format refuses setup with owned cleanup");
    format_known=true;context_ready();publish();window.resize_ok=false;window.m_bResizeWindow=true;window.resizeWindow();
    check(creates==7 && shader_creates==7 && abandons==5,"failed resize fallback rebuilds new context generation");
    const unsigned gamma_deletes=shader_deletes;
    config.gammaCorrection.force=1;config.gammaCorrection.level=2.5f;context_ready();
    check(creates==7 && shader_creates==8 && shader_deletes==gamma_deletes+1 && current->ready &&
          gamma_force==1 && gamma_level==2.5f,"changed gamma settings renew only gamma while preserving context storage");
    publish();window.stop();check(!current && !gamma_program && !published(),"true stop cleans up before context disappears");
    check(!foreign_resets,"no presentation reset restores a foreign generation");
    context_ready();check(!current,"zero context cannot become ready");
    std::printf("transition passed checks=%u resize=retained fullscreen=retained initial=prewarmed stale=refused failure=refused foreign=abandoned stop=destroyed\n",checks);
    return 0;
}catch(const std::exception& error){std::fprintf(stderr,"presentation transition: %s (check %u)\n",error.what(),checks);return 1;}}
