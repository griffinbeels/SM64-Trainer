/* SourceV4 display descriptor: the real query_display (link_source_api.cpp,
 * built as the ghost renderer) over the real boundary, with the presentation
 * facade replaced by values this host controls. CPU only, no window or GL. */
#include "link_source_api.h"
#include "ghost_presentation.h"
#include "N64.h"       // this test's fixture headers
#include "PluginAPI.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <thread>
#define CHECK(v) do{if(!(v)){std::fprintf(stderr,"SourceV4 contract line %d: %s\n",__LINE__,#v);std::exit(1);}}while(0)
extern unsigned swaps;
extern bool swap_next;
namespace stub {
extern bool clean_on,display_on,display_copied,display_wrong_serial,clean_front;
extern uint64_t occurrence,serial;
}
namespace {
const rs_api_v4* v4=nullptr;
rs_source_descriptor3 clean{},shown{};int shown_result=-1;
rb_ticket last{};
int __cdecl capture(const rb_record* record,rb_image*) {
    const rb_ticket ticket{record->occurrence,record->slot};
    clean={};shown={};
    CHECK(v4->v3.query(ticket,&clean));
    shown_result=v4->query_display(ticket,&shown);
    // Valid only on the rendering thread, inside this ticket's callback.
    std::thread other([&]{rs_source_descriptor3 foreign{};foreign.kind=99;
        CHECK(!v4->query_display(ticket,&foreign) && foreign.bytes==0 && foreign.kind==0);});other.join();
    rs_source_descriptor3 stale{};stale.kind=99;
    if(last.occurrence)CHECK(!v4->query_display(last,&stale) && stale.kind==0);
    last=ticket;return 1;
}
int __cdecl complete(const rb_image*){return 1;}
// One VI: the boundary's update, the ghost renderer's swap, this ticket's capture.
void frame(bool presented) {
    rs_before_update();swap_next=presented;
    stub::serial+=presented;
    rb_stamp stamp{};stamp.lists_since=1;
    const auto ticket=v4->v3.boundary.stage(&stamp);CHECK(ticket.occurrence);
    rs_request request{sizeof(rs_request),RS_ABI_V2,{0,0},ticket};v4->v3.boundary.update(&request);
    v4->v3.boundary.finish(ticket);
    const auto* record=v4->v3.boundary.take();CHECK(record && record->occurrence==ticket.occurrence);
    CHECK(v4->v3.boundary.release(ticket));
}
bool zero_after_header(const rs_source_descriptor3& d) {
    rs_source_descriptor3 expected{};expected.bytes=d.bytes;expected.version=d.version;expected.kind=d.kind;
    expected.update_occurrence=d.update_occurrence;return !std::memcmp(&expected,&d,sizeof d);
}
}
int main() {
    CHECK(!SM64ReplaySourceV4(RS_ABI_V3,sizeof(rs_api_v4)) && !SM64ReplaySourceV4(RS_ABI_V4,sizeof(rs_api_v4)-4));
    v4=SM64ReplaySourceV4(RS_ABI_V4,sizeof(rs_api_v4));CHECK(rs_valid_v4(v4));
    CHECK(rs_valid_v3(SM64ReplaySourceV3(RS_ABI_V3,sizeof(rs_api_v3))));
    const rs_callbacks callbacks{sizeof(rs_callbacks),RS_ABI_V2,{0,0},capture,complete};
    CHECK(v4->v3.boundary.configure(&callbacks)==1);
    rb_rom_open();CHECK(v4->v3.boundary.activate());
    v4->v3.clean_consumer(true);CHECK(stub::clean_on);

    // Consumer off: UNAVAILABLE, and no display surface is asked for.
    stub::display_copied=true;frame(true);
    CHECK(clean.kind==RS_SOURCE_CLEAN_VI && clean.version==RS_ABI_V3);
    CHECK(shown_result==1 && shown.bytes==sizeof(shown) && shown.version==RS_ABI_V4 && shown.kind==RS_SOURCE_UNAVAILABLE);
    CHECK(zero_after_header(shown) && !shown.update_occurrence);

    // Consumer on, a ghost was copied: DISPLAY_VI with the clean descriptor's
    // geometry and identity, the display surface's framebuffer and generation.
    v4->display_consumer(true);CHECK(stub::display_on);
    frame(true);
    CHECK(clean.kind==RS_SOURCE_CLEAN_VI && shown_result==1 && shown.kind==RS_SOURCE_DISPLAY_VI && shown.version==RS_ABI_V4);
    CHECK(shown.framebuffer==21 && shown.framebuffer!=clean.framebuffer && shown.read_buffer==0x8CE0);
    CHECK(shown.source_format==RB_SOURCE_RGB8_LINEAR && shown.resource_generation==7 && clean.resource_generation==3);
    {   rs_source_descriptor3 a=clean,b=shown;
        a.version=b.version=0;a.kind=b.kind=0;a.framebuffer=b.framebuffer=0;a.resource_generation=b.resource_generation=0;
        CHECK(!std::memcmp(&a,&b,sizeof a)); // every geometry, generation, context, origin, serial and update equal
        CHECK(shown.presentation_serial==stub::serial && shown.update_occurrence==stub::occurrence && shown.width==320);
    }

    // Consumer on, private clean with no display copy: unavailable, never an alias.
    stub::display_copied=false;frame(true);
    CHECK(clean.kind==RS_SOURCE_CLEAN_VI && shown_result==1 && shown.kind==RS_SOURCE_UNAVAILABLE);
    CHECK(zero_after_header(shown) && !shown.update_occurrence);

    // A qualified unmodified front is the same picture, with only its four fields.
    stub::clean_front=true;frame(true);
    CHECK(clean.kind==RS_SOURCE_DEFAULT_FRONT && shown_result==1 && shown.kind==RS_SOURCE_SAME_AS_CLEAN);
    CHECK(zero_after_header(shown) && shown.update_occurrence==clean.update_occurrence && shown.update_occurrence==stub::occurrence);
    stub::clean_front=false;

    // A display surface from another presentation is never this ticket's picture.
    stub::display_copied=true;stub::display_wrong_serial=true;frame(true);
    CHECK(shown.kind==RS_SOURCE_UNAVAILABLE && zero_after_header(shown) && !shown.update_occurrence);
    stub::display_wrong_serial=false;

    // No new presentation this VI: the clean query says so and no ghost was copied.
    frame(false);
    CHECK(clean.kind==RS_SOURCE_NO_NEW_PRESENTATION && shown.kind==RS_SOURCE_UNAVAILABLE && zero_after_header(shown));

    // Outside any capture callback no ticket is valid.
    rs_source_descriptor3 outside{};outside.kind=99;CHECK(!v4->query_display(last,&outside) && outside.kind==0);
    CHECK(!v4->query_display({0,0},&outside) && !v4->query_display(last,nullptr));

    v4->display_consumer(false);CHECK(!stub::display_on);
    frame(true);CHECK(shown.kind==RS_SOURCE_UNAVAILABLE);
    v4->v3.clean_consumer(false);v4->v3.boundary.disarm();rb_rom_closed();
    std::puts("SourceV4 passed: display=clean-geometry same=four-fields off=unavailable foreign=refused stale=refused");return 0;
}

/* The fixture renderer: the boundary's own begin/end around a VI that swaps
 * when this host says so, and the ghost presentation's published surfaces. */
unsigned swaps=0;bool swap_next=false;
namespace stub {
bool clean_on=false,display_on=false,display_copied=false,display_wrong_serial=false,clean_front=false;
uint64_t occurrence=0,serial=0;
}
unsigned origin=0x1234;
Registers REG{&origin};
void PluginAPI::UpdateScreenCaptured(rb_ticket t){const auto owned=rb_source_begin(t);if(swap_next)++swaps;rb_source_end(owned);}
namespace ghost_presentation {
void clean_consumer(bool v){stub::clean_on=v;}
bool clean_consumer(){return stub::clean_on;}
void display_consumer(bool v){stub::display_on=v;}
bool display_consumer(){return stub::display_on;}
void begin_update(uint64_t occurrence){stub::occurrence=occurrence;}
bool clean_surface(CleanSurface* out){
    *out={stub::clean_front?0u:11u,stub::clean_front?0u:12u,640,480,1,1,stub::clean_front?0u:3u,
          origin,stub::serial,stub::occurrence};return stub::clean_on;
}
bool display_surface(CleanSurface* out){
    *out={};
    if(!stub::display_on || !stub::display_copied)return false;
    *out={21,22,640,480,1,1,7,origin,stub::serial+(stub::display_wrong_serial?1:0),stub::occurrence};return true;
}
}
