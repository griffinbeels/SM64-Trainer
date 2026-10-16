/* CPU-only presentation custody/call-count witness. No GL library or window. */
#include "ghost_presentation.h"
#include "ghost_compositor_gl.h"
#include <cstdio>
#include <cstdlib>
namespace gp=ghost_presentation;
namespace gc=ghost_compositor;
namespace gl=ghost_compositor::gl;
static unsigned calls=0,copies=0,opened=0,ended=0,next_name=1,draws=0;
static bool gpu_open=false,refuse_second_open=false,refuse_next_open=false;
static void require(bool okay,const char* message) {
    if(!okay){std::fprintf(stderr,"FAILED: %s\n",message);std::exit(1);}
}
template<class T> static T empty(){return T{};}
template<> void empty<void>(){}
#define CPU_STUB(result,name,args) static result GC_GL_CALL stub_##name args {++calls;return empty<result>();}
GC_GL_FUNCTIONS(CPU_STUB)
#undef CPU_STUB
static void GC_GL_CALL integer(uint32_t name,int32_t* out) {
    ++calls;*out=name==gl::MAJOR_VERSION?4:name==gl::MINOR_VERSION?3:
        name==0x8824?1:name==gl::ACTIVE_TEXTURE?gl::TEXTURE0:
        name==gl::DRAW_BUFFER0 || name==gl::READ_BUFFER?gl::BACK:0;
}
static void GC_GL_CALL names(int32_t count,uint32_t* out){++calls;while(count--)*out++=next_name++;}
static uint32_t GC_GL_CALL complete(uint32_t){++calls;return gl::FRAMEBUFFER_COMPLETE;}
static void GC_GL_CALL copy(uint32_t,int32_t,int32_t,int32_t,int32_t,int32_t,int32_t,int32_t){++calls;++copies;}
static void GC_GL_CALL buffers(int32_t,const uint32_t*){++calls;}
static gp::Dispatch dispatch() {
    gp::Dispatch out{};
#define CPU_BIND(result,name,args) out.gl.name=stub_##name;
    GC_GL_FUNCTIONS(CPU_BIND)
#undef CPU_BIND
    out.gl.GetIntegerv=integer;out.gl.GenTextures=out.gl.GenFramebuffers=names;
    out.gl.CheckFramebufferStatus=complete;out.CopyTexSubImage2D=copy;out.DrawBuffers=buffers;return out;
}
static bool begin(){if(refuse_next_open || (refuse_second_open && opened==1))return false;require(!gpu_open,"gateway not nested");gpu_open=true;++opened;return true;}
static bool end(){require(gpu_open,"gateway balanced");gpu_open=false;++ended;return true;}
static void draw(void*,const gp::Draw&){++draws;}
static bool known(gp::Known* out) {
    *out={};out->fields=gp::Known::read_framebuffer|gp::Known::draw_framebuffer|gp::Known::texture_unit|
        gp::Known::default_read_buffer|gp::Known::srgb|gp::Known::scissor_test;out->default_read=gl::BACK;return true;
}
static void reset_counts(){calls=copies=opened=ended=draws=0;}
static void swapped(gp::Instance* p,bool success=true){gp::after_osd(p);gp::record_display(p);gp::swap_completed(p,success);}
int main() {
    auto* p=gp::create(dispatch(),7);require(p && gp::prepare(p,3,640,480,320,240),"prepare");
    const gp::Raster raster{nullptr,draw,begin,end,known};
    gp::display_consumer(true);
    for(uint64_t update=1;update<4;++update) {
        gp::begin_update(p,update);reset_counts();
        require(gp::before_swap(p,0x1000,raster),"plain before swap");
        require(!gp::begin_osd(p),"plain OSD is not mirrored");swapped(p);
        const auto front=gp::surface(p);
        require(front.presentation_serial==update && front.update_occurrence==update && front.vi_origin==0x1000,
                "front keeps successful swap identity");
        require(!front.framebuffer && !front.texture && !front.resource_generation && front.width==640 &&
                front.height==480 && front.context_generation==7 && front.drawable_generation==3,"front has no private resource claim");
        require(!calls && !copies && !opened && !ended && !draws && !gp::display(p).presentation_serial,
                "unmodified presentation does no GL work, gateway or display copy");
    }
    gp::begin_update(p,4);require(gp::surface(p).update_occurrence==3,"previous swap remains explicitly previous");
    require(gp::before_swap(p,0x1000,raster) && !gp::surface(p).presentation_serial,"new candidate invalidates old publication");
    swapped(p,false);require(!gp::surface(p).presentation_serial,"failed swap cannot claim front");
    gp::begin_update(p,5);require(gp::before_swap(p,0x1000,raster),"revoked plain candidate");
    gp::cancel_overlay(p);swapped(p);require(!gp::surface(p).presentation_serial,"revoked plain has no publication");
    gp::GhostViImage image{999,320,240,1,7,55,123,1};
    gp::begin_update(p,6);reset_counts();require(gp::replace_vi(p,image,0x2000,false,raster),"ghost replace");
    require(copies==1 && draws==1,"ghost saved clean before replacement");
    require(gp::before_swap(p,0x2000,raster) && gp::begin_osd(p),"ghost OSD mirrors");gp::end_osd(p);swapped(p);
    const auto clean=gp::surface(p),display=gp::display(p);
    require(clean.framebuffer && clean.texture && clean.resource_generation && clean.update_occurrence==6,
            "modified picture keeps real clean resource");
    require(copies==2 && display.framebuffer && display.framebuffer!=clean.framebuffer &&
            display.presentation_serial==clean.presentation_serial && display.update_occurrence==6,
            "optional ghost copy remains independent and shares occurrence");
    gp::begin_update(p,7);reset_counts();require(gp::replace_vi(p,image,0x2000,false,raster),"withdraw candidate");
    require(gp::before_swap(p,0x2000,raster),"withdraw phase");gp::after_osd(p);gp::withdraw_image(p);gp::record_display(p);gp::swap_completed(p,true);
    require(gp::surface(p).framebuffer && copies==1 && !gp::display(p).presentation_serial &&
            !gp::pending_image(p).occurrence,"withdraw restores and retains clean without ghost copy");
    gp::begin_update(p,8);require(gp::replace_vi(p,image,0x2000,false,raster),"revoke ghost candidate");
    gp::cancel_overlay(p);swapped(p);require(!gp::surface(p).presentation_serial,"revoked ghost has no publication");
    require(gp::prepare(p,4,800,600,320,240) && !gp::surface(p).presentation_serial,"resize invalidates publication");
    gp::begin_update(p,9);reset_counts();require(gp::before_swap(p,0x3000,raster),"resized front");swapped(p);
    require(!calls && gp::surface(p).width==800 && gp::surface(p).drawable_generation==4,"resized front needs no copy");
    gp::begin_update(p,10);reset_counts();refuse_second_open=true;
    require(!gp::replace_vi(p,image,0x3000,false,raster),"refused replacement never draws");refuse_second_open=false;
    require(copies==1 && !draws && opened==ended,"attempt saved clean but could not modify BACK");
    require(gp::before_swap(p,0x3000,raster) && !gp::begin_osd(p),"refused replacement needs no OSD mirror");swapped(p);
    require(!gp::surface(p).framebuffer && gp::surface(p).update_occurrence==10 && copies==1,
            "unchanged BACK publishes truthful front after a refused replacement");
    gp::begin_update(p,11);reset_counts();require(gp::replace_vi(p,image,0x3000,false,raster),"display-copy refusal candidate");
    require(gp::before_swap(p,0x3000,raster) && gp::begin_osd(p),"display-copy refusal OSD");gp::end_osd(p);gp::after_osd(p);
    refuse_next_open=true;gp::record_display(p);refuse_next_open=false;gp::swap_completed(p,true);
    require(gp::surface(p).framebuffer && gp::surface(p).update_occurrence==11 && copies==1 &&
            !gp::display(p).presentation_serial,"refused display copy preserves qualified private clean picture only");
    gp::display_consumer(false);gp::destroy(p);require(!gpu_open && opened==ended,"balanced final gateway");
    std::puts("CPU presentation passed: plain_gl_calls=0 plain_copies=0 plain_osd_mirrors=0 ghost_copies=2 identity=qualified failed_display=clean_preserved");
}
