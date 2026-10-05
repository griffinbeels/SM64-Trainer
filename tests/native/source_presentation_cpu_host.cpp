/* CPU-only public SourceV3/V4 projection over qualified presentation fixtures. */
#include "link_source_api.h"
#include "ghost_presentation.h"
#include "N64.h"
#include <cstdio>
#include <cstdlib>
unsigned source_swaps=0;bool source_swap_success=true;
unsigned origin=0x1234;Registers REG{&origin};
static unsigned mode=0;
static uint64_t current_update=0;
static bool consumer=false;
namespace ghost_presentation {
void clean_consumer(bool value){consumer=value;}
void display_consumer(bool){}
bool display_consumer(){return true;}
void begin_update(uint64_t value){current_update=value;}
bool clean_surface(CleanSurface* out) {
    *out={};if(!consumer || mode==0)return false;
    const bool private_surface=mode==2 || mode==8;
    *out={private_surface?101u:0u,private_surface?102u:0u,640,480,1,1,private_surface?3u:0u,origin,
          current_update,current_update};
    if(mode==3)--out->update_occurrence;
    if(mode==4)++out->drawable_generation;
    if(mode==7)out->texture=102;
    if(mode==8)out->resource_generation=0;
    if(mode==9)++out->vi_origin;
    if(mode==10)++out->context_generation;
    if(mode==11)out->presentation_serial=0;
    return true;
}
bool display_surface(CleanSurface* out){*out={};return false;}
}
static const rs_api_v3* source=nullptr;
static const rs_api_v4* display=nullptr;
static rs_source_descriptor3 observed{},observed_display{};
static void require(bool okay,const char* message) {
    if(!okay){std::fprintf(stderr,"FAILED: %s\n",message);std::exit(1);}
}
static int __cdecl capture(const rb_record* r,rb_image* image) {
    require(source->query({r->occurrence,r->slot},&observed)!=0,"source query inside capture");
    require(display->query_display({r->occurrence,r->slot},&observed_display)!=0,"display query inside capture");
    *image={};return 1;
}
static int __cdecl completed(const rb_image*){return 1;}
int main() {
    source=SM64ReplaySourceV3(RS_ABI_V3,sizeof(rs_api_v3));
    display=SM64ReplaySourceV4(RS_ABI_V4,sizeof(rs_api_v4));
    require(source && display,"public source tables");
    const rs_callbacks callbacks{sizeof(rs_callbacks),RS_ABI_V2,{0,0},capture,completed};
    require(source->boundary.configure(&callbacks)!=0,"configure clean-capable source");
    source->clean_consumer(true);rb_rom_open();require(source->boundary.activate()!=0,"activate");
    const uint32_t expected[]={RS_SOURCE_UNAVAILABLE,RS_SOURCE_DEFAULT_FRONT,RS_SOURCE_CLEAN_VI,
        RS_SOURCE_UNAVAILABLE,RS_SOURCE_UNAVAILABLE,RS_SOURCE_NO_NEW_PRESENTATION,RS_SOURCE_UNAVAILABLE,
        RS_SOURCE_UNAVAILABLE,RS_SOURCE_UNAVAILABLE,RS_SOURCE_UNAVAILABLE,RS_SOURCE_UNAVAILABLE,RS_SOURCE_UNAVAILABLE};
    for(mode=0;mode<sizeof(expected)/sizeof(expected[0]);++mode) {
        source_swap_success=mode!=5;source->clean_consumer(mode!=6);
        rb_stamp stamp{};const auto ticket=source->boundary.stage(&stamp);require(ticket.occurrence!=0,"stage");
        const rs_request request{sizeof(request),RS_ABI_V2,{0,0},ticket};source->boundary.update(&request);
        source->boundary.finish(ticket);const auto* record=source->boundary.take();require(record!=nullptr,"take");
        require(observed.kind==expected[mode],"qualified source kind");
        if(mode==1)require(!observed.framebuffer && !observed.resource_generation && observed.read_buffer==0x404 &&
            observed.source_format==RB_SOURCE_RGB8_SRGB && observed.presentation_serial==current_update &&
            observed.update_occurrence==current_update,"default front keeps truthful format and swap identity");
        if(mode==2)require(observed.framebuffer==101 && observed.resource_generation==3 && observed.read_buffer==0x8CE0 &&
            observed.source_format==RB_SOURCE_RGB8_LINEAR,"modified picture keeps its private clean resource");
        if(mode==1 || mode==2) {
            require(observed_display.kind==RS_SOURCE_SAME_AS_CLEAN &&
                observed_display.update_occurrence==current_update,"ghostless twin shares this clean occurrence");
        }else require(observed_display.kind==RS_SOURCE_UNAVAILABLE,"refused presentation has no twin identity");
        require(source->boundary.release(ticket)!=0,"release");
    }
    source->boundary.disarm();rb_rom_closed();
    std::puts("CPU source passed: front=truthful clean=private stale=refused revoke=refused failed_swap=omitted twin=same_occurrence");
}
