/* Execute the actual renderer begin() against inert identity/gateway shells. */
#define NOMINMAX
#include <windows.h>
#include "ghost_draw.h"
#include "ghost_compositor.h"
#include "ghost_playback.h"
#include "ghost_live_session.h"
#include "ghost_viewer.h"
#include "ghost_presentation.h"
#include "renderer_gl_state.h"
#include <cstdio>
#include <stdexcept>
#include <cstring>
using GLuint=unsigned;using GLint=int;
namespace {
unsigned checks=0,creates=0,abandons=0,destroys=0,warms=0,boundaries=0,presentation_calls=0,gateway_calls=0,evaluations=0;
bool identified=true,gateway_fails=false,end_fails=false,create_fails=false,presentation_fails=false,settings_eligible=true;
renderer::Identity live{1,1,1,1,1};
void check(bool okay,const char *why){++checks;if(!okay)throw std::runtime_error(why);}
void GC_GL_CALL integer(uint32_t name,int32_t *out){*out=name==0x821B?4:3;}
void* fake_proc(const char*){return nullptr;}
}
#define wglGetProcAddress fake_proc
namespace replay_gl {
bool CandidateBindings(renderer::Identity *out,snapshot::RestoreBindings *bindings){if(!identified)return false;*out=live;*bindings={};return true;}
bool BeginCapture(){++gateway_calls;if(gateway_fails){gateway_fails=false;return false;}return true;}
bool EndCapture(){if(end_fails){end_fails=false;return false;}return true;}
}
namespace ghost_dispatch {bool load(ghost_compositor::Dispatch &out){out={};out.GetIntegerv=integer;return true;}}
namespace ghost_compositor {
Instance* create(const Dispatch&,uint64_t){++creates;if(create_fails){create_fails=false;return nullptr;}return reinterpret_cast<Instance*>(uintptr_t(1));}
Result warm(Instance*){++warms;return Result::ok;}
void abandon(Instance*){++abandons;}
void destroy(Instance*){++destroys;}
void clear_layer(Instance*){}
Result prepare(Instance*,uint32_t,uint32_t,uint32_t,const uint8_t*,uint32_t,uint32_t){return Result::ok;}
Result prepare_particles(Instance*,const uint8_t*,uint32_t,uint32_t){return Result::ok;}
Result prepare_annotations(Instance*,const uint8_t*,uint32_t,uint32_t){return Result::ok;}
}
namespace ghost_playback {
void safe_boundary(uint32_t,uint32_t){++boundaries;}
bool atlas(sg_atlas_view*,uint64_t*){return false;}
bool particles(sg_particle_view*,uint64_t*){return false;}
bool annotations(ghost_annotation::View*,uint64_t*){return false;}
bool plan(const gc_sample&){++evaluations;return false;}
}
namespace ghost_viewer {
bool atlas(sg_atlas_view*,uint64_t*){return false;}
bool particles(sg_particle_view*,uint64_t*){return false;}
bool annotations(ghost_annotation::View*,uint64_t*){return false;}
bool plan(const gc_sample&){return false;}
}
namespace ghost_live_session {
void retire_list() noexcept {}
bool begin(uint32_t,uint32_t,uint32_t,const gc_sample*,uint64_t) noexcept {return false;}
bool plan() noexcept {++evaluations;return false;}
void presentation(PresentationReason,bool) noexcept {}
void presentation_environment(uint32_t,uint32_t,uint32_t) noexcept {}
}
namespace ghost_presentation {
bool clean_consumer(){return true;}
bool eligible(){return settings_eligible;}
uint32_t ineligible_reasons(){return settings_eligible?0:2;}
void set_provider(ImageProvider,PresentationNotice,ImageValidity){}
bool context_ready(){++presentation_calls;if(presentation_fails){presentation_fails=false;return false;}return true;}
}
namespace ghost_draw {
namespace {
ghost_compositor::Instance *compositor=nullptr;
bool compositor_ready=false;
bool list_identified=false;
ghost_compositor::Dispatch dispatch{};
renderer::Identity owner{},presentation_owner{},setup_owner{};
unsigned presentation_retry=0,compositor_retry=0;
uint32_t width=0,height=0,prepared_width=0,prepared_height=0,configuration=0;
uint32_t context_gl_major=0,context_gl_minor=0;
uint64_t model_id=0,particle_model=0,annotation_model=0;
bool will_draw=false;
using GetUniform=void (APIENTRY *)(GLuint,GLint,GLint*);GetUniform get_uniform=nullptr;
using Reason=ghost_live_session::PresentationReason;
Reason compositor_failure=Reason::setup_pending;
void refused(Reason){}
void setup_refused(Reason reason){compositor_failure=reason;}
uint64_t now_ns(){return 1;}
void forget_scopes(){}
uint32_t faulted_generation=0;
bool image_for_vi(const ghost_presentation::ViSource&,ghost_presentation::GhostViImage*){return false;}
bool valid_image(const ghost_presentation::GhostViImage&){return false;}
void presented(const ghost_presentation::GhostViImage&,uint64_t,bool){}
}
void close_layer(){}void cancel(){}
#include "actual_ghost_draw_begin.inc"
}
int main(){try {
    using namespace ghost_draw;
    gateway_fails=true;begin(7,4,nullptr);
    check(!compositor && !creates && gateway_calls==1,"first gateway refusal does not create or latch compositor");
    for(unsigned i=0;i<31;++i)begin(7,4,nullptr);
    check(!creates && gateway_calls==1,"failed gateway retries at bounded cadence");
    begin(7,4,nullptr);check(compositor && creates==1 && warms==1,"same-context gateway failure recovers without renderer restart");
    identified=false;begin(7,4,nullptr);identified=true;
    check(compositor && !abandons,"temporary CandidateBindings miss retains still-owned compositor");
    begin(7,4,nullptr);check(creates==1 && !abandons,"identity recovery reuses compositor");
    ++live.context_generation;create_fails=true;begin(7,4,nullptr);
    check(!compositor && creates==2 && abandons==1,"new context create refusal is observable");
    for(unsigned i=0;i<32;++i)begin(7,4,nullptr);
    check(compositor && creates==3 && warms==2,"same-context create failure retries and recovers");
    ++live.context_generation;end_fails=true;const auto prior_creates=creates,prior_abandons=abandons;
    begin(7,4,nullptr);check(!compositor && !compositor_ready && abandons==prior_abandons+2 && faulted(),
        "a dirty setup close abandons its names and steps the ghost aside for the context");
    const auto aside_calls=gateway_calls;
    for(unsigned i=0;i<64;++i)begin(7,4,nullptr);
    check(!compositor && creates==prior_creates+1 && gateway_calls==aside_calls,
        "a stepped-aside context asks for no setup retry and no gateway");
    ++live.context_generation;begin(7,4,nullptr);
    check(compositor && compositor_ready && creates==prior_creates+2 && !faulted() && !destroys,
        "the next context sets the ghost up again");
    ++live.drawable_generation;presentation_fails=true;const auto before=presentation_calls;
    begin(7,4,nullptr);check(presentation_calls==before+1 && presentation_owner.drawable_generation!=live.drawable_generation,
        "presentation failure cannot latch its drawable as ready");
    for(unsigned i=0;i<32;++i)begin(7,4,nullptr);
    check(presentation_calls==before+2 && presentation_owner.drawable_generation==live.drawable_generation,
        "presentation retry recovers through the actual renderer caller");
    settings_eligible=false;const auto attempts=gateway_calls;const auto pending=boundaries;
    for(unsigned i=0;i<12;++i)begin(7,4,nullptr);
    check(gateway_calls==attempts && !evaluations && boundaries==pending+12,
        "ineligible settings skip ghost GPU and pose work while ownership boundary continues");
    std::printf("setup passed checks=%u gateway=recovered create=recovered observation=retained ineligible=skipped\n",checks);return 0;
}catch(const std::exception &error){std::fprintf(stderr,"draw setup: %s (check %u)\n",error.what(),checks);return 1;}}
