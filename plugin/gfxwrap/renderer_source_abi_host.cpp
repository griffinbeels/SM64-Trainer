/* Real DLL registration/lifetime witness; renderer surface is a fixture.
 * Compiled /Gz deliberately, while production source DLL uses /Gd. */
#include "link_source_api.h"
#include <cstdio>
#include <cstring>
#include <type_traits>
static_assert(std::is_same<rb_capture_image,int (__cdecl *)(const rb_record*,rb_image*)>::value,"capture ABI");
static_assert(std::is_same<rb_image_completed,int (__cdecl *)(const rb_image*)>::value,"completion ABI");
static_assert(std::is_same<rb_source_surface,int (__cdecl *)(rb_surface*)>::value,"surface ABI");
static_assert(std::is_same<decltype(rs_api::begin_capture),bool (__cdecl *)()>::value,"error preflight ABI");
static_assert(std::is_same<decltype(rs_api::end_capture),bool (__cdecl *)()>::value,"error postflight ABI");
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr,"source ABI line %d: %s\n",__LINE__,#x); return 1; } } while(0)
int main(int argc,char **argv) {
    CHECK(argc==2);
    HMODULE source=LoadLibraryW(L"source_api.dll"), callbacks=LoadLibraryW(L"source_callbacks.dll");
    CHECK(source && callbacks);
    CHECK(!GetProcAddress(source,"SM64ReplaySourceV1"));
    auto query=reinterpret_cast<const rs_api *(__cdecl *)(uint32_t,uint32_t)>(GetProcAddress(source,"SM64ReplaySourceV2"));
    auto lifecycle=reinterpret_cast<void (__cdecl *)(int)>(GetProcAddress(source,"SourceTestLifecycle"));
    auto captured=reinterpret_cast<uint32_t (__cdecl *)()>(GetProcAddress(callbacks,"Captured"));
    auto completed=reinterpret_cast<uint32_t (__cdecl *)()>(GetProcAddress(callbacks,"Completed"));
    CHECK(query && lifecycle && captured && completed);
    CHECK(!query(0,sizeof(rs_api)) && !query(1,sizeof(rs_api)) && !query(RS_ABI_V2,sizeof(rs_api)-1));
    auto api=query(RS_ABI_V2,sizeof(rs_api));
    CHECK(api && api->bytes==sizeof(rs_api) && api->capabilities==(RS_CAP_BOUNDARY|RS_CAP_SOURCE_FORMAT) && !api->reserved);
    CHECK(api->version==RS_ABI_V2 && api->begin_capture && api->end_capture);
    CHECK(api->begin_capture() && api->end_capture()); // fixture; real errors tested independently
    rs_callbacks cb{sizeof(cb),RS_ABI_V2,{0,0},
        reinterpret_cast<rb_capture_image>(GetProcAddress(callbacks,"Capture")),
        reinterpret_cast<rb_image_completed>(GetProcAddress(callbacks,"Completion"))};
    CHECK(cb.capture && cb.completed);
    CHECK(!api->configure(nullptr));
    cb.bytes--; CHECK(!api->configure(&cb)); cb.bytes++;
    cb.version++; CHECK(!api->configure(&cb)); cb.version--;
    cb.version=1; CHECK(!api->configure(&cb)); cb.version=RS_ABI_V2;
    cb.reserved[1]=1; CHECK(!api->configure(&cb)); cb.reserved[1]=0;
    auto saved=cb.completed; cb.completed=nullptr; CHECK(!api->configure(&cb)); cb.completed=saved;
    if (std::strcmp(argv[1],"late")==0) {
        lifecycle(1); CHECK(!api->configure(&cb)); lifecycle(0);
        CHECK(!api->configure(&cb) && !api->activate());
        std::puts("source ABI passed: late registration refused"); return 0;
    }
    if (std::strcmp(argv[1],"closed")==0) {
        lifecycle(1); lifecycle(2); CHECK(!api->configure(&cb));
        std::puts("source ABI passed: closed lifecycle refused"); return 0;
    }
    CHECK(api->configure(&cb)==1 && !api->configure(&cb));
    // Caller storage and ordinary module references may disappear after configure.
    cb.capture=nullptr; cb.completed=nullptr;
    CHECK(FreeLibrary(callbacks) && FreeLibrary(source));
    CHECK(GetModuleHandleW(L"source_callbacks.dll")==callbacks && GetModuleHandleW(L"source_api.dll")==source);
    CHECK(!api->activate());
    for(unsigned cycle=0;cycle<3;cycle++) {
        if(cycle==0 || cycle==2)lifecycle(1); CHECK(api->activate());
        rb_stamp stamp{}; stamp.bytes[0]=77; stamp.lists_since=1; stamp.table_count=1; stamp.lengths[0]=1;
        auto token=api->stage(&stamp); CHECK(token.occurrence);
        stamp.bytes[0]=88;
        rs_request request{sizeof(request),RS_ABI_V2,{0,0},token}; api->update(&request); api->finish(token);
        const rb_record *record=api->take(); CHECK(record && record->outcome==RB_OBSERVED && record->stamp.bytes[0]==77);
        CHECK(record->image.serial==token.occurrence && captured()==cycle+1);
        CHECK(record->surface.read_drawable==456 && record->surface.drawable_width==640 && record->surface.drawable_height==480);
        CHECK(record->surface.restore_read_framebuffer==17 && record->surface.restore_texture_2d==19 && record->surface.restore_read_buffer==0x405);
        CHECK(record->surface.source_format==RB_SOURCE_RGB8_LINEAR);
        CHECK(api->release(token)==1 && completed()==cycle+1);
        api->disarm(); CHECK(!api->stage(&stamp).occurrence);
        if(cycle==1 || cycle==2)lifecycle(0);
    }
    CHECK(api->stats().observed==3 && api->stats().installed);
    lifecycle(2); CHECK(!api->activate());
    std::puts("source ABI passed: query, registration, /Gz, pinned callbacks, hot cycles");
}
