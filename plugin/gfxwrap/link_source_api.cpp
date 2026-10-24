/* Built into the source renderer, never loaded by patching its binary. */
#include "GLFunctions.h"
#include "link_source_api.h"
#include "source_refusal.h"
#include "PluginAPI.h"
#include "DisplayWindow.h"
#include "N64.h"
#if defined(SM64_GHOST_RENDERER)
#include "ghost_presentation.h"
#endif

namespace {
bool configure_attempted=false; // serialized pre-ROM, at most one pin group
bool configured_clean=false;
uint64_t update_occurrence=0;
uint32_t before_swaps=0;
int surface(rb_surface *out) {
    renderer::Identity identity{}; snapshot::RestoreBindings bindings{};
    if (!replay_gl::CandidateBindings(&identity,&bindings)) {
        out->witness=RS_SURFACE_BINDINGS;
        rs_bindings_cause cause{};replay_gl::BindingsCause(&cause);rs_write_bindings_cause(out,cause);return 0;
    }
    const auto format=replay_gl::SourceFormat();
    if (format==RB_SOURCE_UNKNOWN) {out->witness=RS_SURFACE_FORMAT;return 0;}
    unsigned drawable_width=0, drawable_height=0;
    if (!replay_gl::DrawableExtent(&drawable_width,&drawable_height)) {out->witness=RS_SURFACE_EXTENT;return 0;}
    auto &window=dwnd();
    const auto width=window.getWidth(), height=window.getHeight(), offset=window.getHeightOffset();
    if (!width || !height || width>3840 || height>2160 || offset>2160) {out->witness=RS_SURFACE_GEOMETRY;return 0;}
    if (!REG.VI_ORIGIN) {out->witness=RS_SURFACE_VI_REGISTER;return 0;}
    if (width>drawable_width || uint64_t(offset)+height>drawable_height) {out->witness=RS_SURFACE_CROP;return 0;}
    out->width=width; out->height=height; out->bottom_offset=offset;
    out->context=identity.context; out->renderer_thread=identity.owner_thread;
    out->context_generation=identity.context_generation;
    out->drawable_generation=identity.drawable_generation;
    out->read_drawable=identity.read_drawable;
    out->drawable_width=drawable_width; out->drawable_height=drawable_height;
    out->restore_read_framebuffer=bindings.read_framebuffer;
    out->restore_texture_2d=bindings.texture_2d;
    out->restore_read_buffer=bindings.source_read_buffer;
    out->source_format=format;
    out->post_vi_origin=*REG.VI_ORIGIN;
    out->swap_count=window.getBuffersSwapCount(); // diagnostic, NOT identity/success
    LARGE_INTEGER qpc; QueryPerformanceCounter(&qpc); out->boundary_qpc=qpc.QuadPart;
    return 1;
}
int surface3(rb_ticket,rb_surface* out,rs_source_descriptor3* image) {
    if(!surface(out))return 0;
    *image={};image->bytes=sizeof(*image);image->version=RS_ABI_V3;
    image->context=out->context;image->read_drawable=out->read_drawable;
    image->context_generation=out->context_generation;image->drawable_generation=out->drawable_generation;
    image->renderer_thread=out->renderer_thread;image->source_format=out->source_format;
    image->width=out->width;image->height=out->height;image->y=out->bottom_offset;
    image->surface_width=out->drawable_width;image->surface_height=out->drawable_height;
    image->vi_origin=out->post_vi_origin;image->update_occurrence=update_occurrence;
#if defined(SM64_GHOST_RENDERER)
    if(configured_clean) {
        if(out->swap_count==before_swaps){image->kind=RS_SOURCE_NO_NEW_PRESENTATION;return 1;}
        ghost_presentation::CleanSurface clean{};
        if(!ghost_presentation::clean_surface(&clean) || !clean.presentation_serial || clean.update_occurrence!=update_occurrence ||
           clean.context_generation!=out->context_generation || clean.drawable_generation!=out->drawable_generation ||
           clean.width!=out->drawable_width || clean.height!=out->drawable_height || clean.vi_origin!=out->post_vi_origin)
            return 1; // explicitly unavailable: never fall back to a visible ghost
        if(!clean.framebuffer) {
            if(clean.texture || clean.resource_generation)return 1;
            image->kind=RS_SOURCE_DEFAULT_FRONT;image->presentation_serial=clean.presentation_serial;
            image->read_buffer=0x404;return 1; // GL_FRONT
        }
        if(!clean.texture || !clean.resource_generation)return 1;
        image->kind=RS_SOURCE_CLEAN_VI;image->framebuffer=clean.framebuffer;image->read_buffer=0x8CE0; // GL_COLOR_ATTACHMENT0
        image->resource_generation=clean.resource_generation;image->presentation_serial=clean.presentation_serial;
        // The owned clean texture stores the same RGB bytes in a linear RGBA8
        // attachment. OSD refuses enabled sRGB conversion before publication.
        image->source_format=RB_SOURCE_RGB8_LINEAR;return 1;
    }
#endif
    image->kind=RS_SOURCE_DEFAULT_FRONT;image->read_buffer=0x404;return 1; // GL_FRONT
}
int configure_impl(const rs_callbacks *callbacks,bool clean) {
    if (!callbacks || callbacks->bytes!=sizeof(rs_callbacks) || callbacks->version!=RS_ABI_V2
            || callbacks->reserved[0] || callbacks->reserved[1]
            || (!callbacks->capture != !callbacks->completed)) return 0;
    if (configure_attempted) return 0;
    configure_attempted=true;
    if (callbacks->capture) {
        // Immutable callbacks can outlive CloseDLL/RomClosed acknowledgments.
        HMODULE capture=nullptr, completed=nullptr;
        const DWORD flags=GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_PIN;
        if (!GetModuleHandleExW(flags,reinterpret_cast<LPCWSTR>(callbacks->capture),&capture)
                || !GetModuleHandleExW(flags,reinterpret_cast<LPCWSTR>(callbacks->completed),&completed)
                || !rb_configure_images(callbacks->capture,callbacks->completed)) return 0;
    }
    const bool installed=(clean ? rb_bind_source3(surface3) : rb_bind_source(surface))==RB_INSTALLED;
    configured_clean=installed && clean;return installed;
}
int __cdecl configure(const rs_callbacks *callbacks){return configure_impl(callbacks,false);}
int __cdecl configure3(const rs_callbacks *callbacks){return configure_impl(callbacks,true);}
void __cdecl clean_consumer(bool enabled) {
#if defined(SM64_GHOST_RENDERER)
    ghost_presentation::clean_consumer(enabled && configured_clean);
#else
    (void)enabled;
#endif
}
// SourceV4: the ghost copy's picture for this ticket. Ticket validity and
// every shared field come from the V3 query itself, so the two cannot differ.
int __cdecl query_display(rb_ticket ticket,rs_source_descriptor3* out) {
    if(!out)return 0;
    rs_source_descriptor3 clean{};
    if(!rb_read_source_descriptor3(ticket,&clean)){*out={};return 0;}
    *out={};out->bytes=sizeof(*out);out->version=RS_ABI_V4;out->kind=RS_SOURCE_UNAVAILABLE;
#if defined(SM64_GHOST_RENDERER)
    if(!configured_clean || !ghost_presentation::display_consumer())return 1;
    if(clean.kind!=RS_SOURCE_CLEAN_VI && clean.kind!=RS_SOURCE_DEFAULT_FRONT)return 1;
    ghost_presentation::CleanSurface shown{};
    if(clean.kind==RS_SOURCE_CLEAN_VI && ghost_presentation::display_surface(&shown) && shown.framebuffer && shown.texture &&
       shown.resource_generation && shown.presentation_serial==clean.presentation_serial &&
       shown.update_occurrence==clean.update_occurrence && shown.context_generation==clean.context_generation &&
       shown.drawable_generation==clean.drawable_generation && shown.width==clean.surface_width &&
       shown.height==clean.surface_height && shown.vi_origin==clean.vi_origin) {
        *out=clean;out->version=RS_ABI_V4;out->kind=RS_SOURCE_DISPLAY_VI;
        out->framebuffer=shown.framebuffer;out->read_buffer=0x8CE0; // GL_COLOR_ATTACHMENT0
        out->source_format=RB_SOURCE_RGB8_LINEAR;out->resource_generation=shown.resource_generation;return 1;
    }
    // A private clean picture proves only clean capture. A missing or refused
    // display copy never proves that the ghosted window showed those pixels.
    if(clean.kind!=RS_SOURCE_DEFAULT_FRONT)return 1;
    out->kind=RS_SOURCE_SAME_AS_CLEAN;out->update_occurrence=clean.update_occurrence;
#endif
    return 1;
}
void __cdecl display_consumer(bool enabled) {
#if defined(SM64_GHOST_RENDERER)
    ghost_presentation::display_consumer(enabled && configured_clean);
#else
    (void)enabled;
#endif
}
void __cdecl update(const rs_request *request) {
    const rb_ticket token=request && request->bytes==sizeof(rs_request) && request->version==RS_ABI_V2
        && !request->reserved[0] && !request->reserved[1] ? request->ticket : rb_ticket{0,0};
    // No fallback/second call on refusal. The by-value token reaches the original queue.
    api().UpdateScreenCaptured(token);
}
const rs_api api_v2={sizeof(rs_api),RS_ABI_V2,RS_CAP_BOUNDARY|RS_CAP_SOURCE_FORMAT,0,
    configure,rb_stage,update,rb_finish,rb_activate,rb_disarm,rb_take,rb_release,rb_get_stats,
    replay_gl::BeginCapture,replay_gl::EndCapture};
const rs_api_v3 api_v3={sizeof(rs_api_v3),RS_ABI_V3,RS_CAP_BOUNDARY|RS_CAP_SOURCE_FORMAT|RS_CAP_CLEAN_PRESENTATION,0,
    {sizeof(rs_api),RS_ABI_V2,RS_CAP_BOUNDARY|RS_CAP_SOURCE_FORMAT,0,
    configure3,rb_stage,update,rb_finish,rb_activate,rb_disarm,rb_take,rb_release,rb_get_stats,
    replay_gl::BeginCapture,replay_gl::EndCapture},rb_read_source_descriptor3,clean_consumer};
const rs_api_v4 api_v4={sizeof(rs_api_v4),RS_ABI_V4,
    RS_CAP_BOUNDARY|RS_CAP_SOURCE_FORMAT|RS_CAP_CLEAN_PRESENTATION|RS_CAP_DISPLAY_COPY,0,
    {sizeof(rs_api_v3),RS_ABI_V3,RS_CAP_BOUNDARY|RS_CAP_SOURCE_FORMAT|RS_CAP_CLEAN_PRESENTATION,0,
    {sizeof(rs_api),RS_ABI_V2,RS_CAP_BOUNDARY|RS_CAP_SOURCE_FORMAT,0,
    configure3,rb_stage,update,rb_finish,rb_activate,rb_disarm,rb_take,rb_release,rb_get_stats,
    replay_gl::BeginCapture,replay_gl::EndCapture},rb_read_source_descriptor3,clean_consumer},
    query_display,display_consumer};
}
extern "C" void rs_before_update() {
    before_swaps=dwnd().getBuffersSwapCount();
    if(update_occurrence<UINT64_MAX)++update_occurrence;
#if defined(SM64_GHOST_RENDERER)
    if(update_occurrence==UINT64_MAX){ghost_presentation::clean_consumer(false);ghost_presentation::display_consumer(false);}
    ghost_presentation::begin_update(update_occurrence);
#endif
}
#pragma comment(linker,"/EXPORT:SM64ReplaySourceV2=_SM64ReplaySourceV2")
extern "C" const rs_api *__cdecl SM64ReplaySourceV2(uint32_t version,uint32_t bytes) {
    return version==RS_ABI_V2 && bytes==sizeof(rs_api) ? &api_v2 : nullptr;
}
#pragma comment(linker,"/EXPORT:SM64ReplaySourceV3=_SM64ReplaySourceV3")
extern "C" const rs_api_v3 *__cdecl SM64ReplaySourceV3(uint32_t version,uint32_t bytes) {
    return version==RS_ABI_V3 && bytes==sizeof(rs_api_v3) ? &api_v3 : nullptr;
}
#pragma comment(linker,"/EXPORT:SM64ReplaySourceV4=_SM64ReplaySourceV4")
extern "C" const rs_api_v4 *__cdecl SM64ReplaySourceV4(uint32_t version,uint32_t bytes) {
    return version==RS_ABI_V4 && bytes==sizeof(rs_api_v4) ? &api_v4 : nullptr;
}

// tools/build_renderer.py writes renderer_build_identity.h beside this file
// in the staged tree; the id names the pinned upstream tree plus the overlay
// sources, so an installed renderer can be compared with the bundled one.
#if __has_include("renderer_build_identity.h")
#include "renderer_build_identity.h"
#endif
#ifndef SM64_TRAINER_RENDERER_BUILD_ID
#define SM64_TRAINER_RENDERER_BUILD_ID "unbuilt"
#endif
#pragma comment(linker,"/EXPORT:SM64TrainerRendererIdentity=_SM64TrainerRendererIdentity")
extern "C" const char *__cdecl SM64TrainerRendererIdentity(void) { return SM64_TRAINER_RENDERER_BUILD_ID; }
