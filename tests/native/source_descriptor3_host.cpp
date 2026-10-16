/* Actual slot sidecars: renderer-only callback scope, ABA and cancellation. */
#include "link_source_api.h"
#include <cstdio>
#include <cstdlib>
#include <thread>
#define CHECK(v) do{if(!(v)){std::fprintf(stderr,"SourceV3 contract line %d: %s\n",__LINE__,#v);std::exit(1);}}while(0)
static unsigned captured=0;
static bool cancel_inside=false;
static rb_ticket previous{};
static int __cdecl provider(rb_ticket ticket,rb_surface* surface,rs_source_descriptor3* source) {
    rs_source_descriptor3 unavailable{};CHECK(!rb_read_source_descriptor3(ticket,&unavailable));
    surface->renderer_thread=GetCurrentThreadId();surface->width=320;surface->height=240;
    *source={};source->bytes=sizeof(*source);source->version=RS_ABI_V3;source->kind=RS_SOURCE_CLEAN_VI;
    source->framebuffer=unsigned(ticket.occurrence)+100;source->presentation_serial=ticket.occurrence+200;
    return 1;
}
static int __cdecl capture(const rb_record* record,rb_image*) {
    ++captured;const rb_ticket ticket{record->occurrence,record->slot};rs_source_descriptor3 source{};
    CHECK(rb_read_source_descriptor3(ticket,&source));CHECK(source.framebuffer==unsigned(ticket.occurrence)+100);
    CHECK(source.presentation_serial==ticket.occurrence+200);
    std::thread other([&]{rs_source_descriptor3 foreign{};CHECK(!rb_read_source_descriptor3(ticket,&foreign));});other.join();
    CHECK(!rb_read_source_descriptor3(previous,&source));
    if(cancel_inside){rb_disarm();CHECK(!rb_read_source_descriptor3(ticket,&source));}
    return 1;
}
static int __cdecl complete(const rb_image*){return 1;}
int main() {
    CHECK(rb_configure_images(capture,complete));CHECK(rb_bind_source3(provider)==RB_INSTALLED);
    rb_rom_open();CHECK(rb_activate());rb_stamp stamp{};
    for(unsigned i=0;i<RB_SLOTS*3;++i) {
        const auto ticket=rb_stage(&stamp);CHECK(ticket.occurrence);rs_source_descriptor3 source{};
        CHECK(!rb_read_source_descriptor3(ticket,&source));const auto owned=rb_source_begin(ticket);CHECK(owned.occurrence==ticket.occurrence);
        rb_source_end(owned);CHECK(!rb_read_source_descriptor3(ticket,&source));rb_finish(ticket);
        const auto*record=rb_take();CHECK(record && record->occurrence==ticket.occurrence && record->outcome==RB_OBSERVED);
        CHECK(rb_release(ticket));CHECK(!rb_read_source_descriptor3(ticket,&source));previous=ticket;
    }
    cancel_inside=true;const auto ticket=rb_stage(&stamp);rb_source_end(rb_source_begin(ticket));rb_finish(ticket);
    const auto*record=rb_take();CHECK(record && record->outcome==RB_RETIRED);CHECK(rb_release(ticket));
    CHECK(captured==RB_SLOTS*3+1);rb_rom_closed();rb_close();
    std::puts("SourceV3 passed: per-ticket descriptor callback-only wrong-thread=refused ABA=refused cancellation=refused");return 0;
}
