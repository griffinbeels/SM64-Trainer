/* Built into the source renderer, never loaded by patching its binary. */
#include "GLFunctions.h"
#include "link_source_api.h"
#include "PluginAPI.h"
#include "DisplayWindow.h"
#include "N64.h"

namespace {
bool configure_attempted=false; // serialized pre-ROM, at most one pin group
int surface(rb_surface *out) {
    renderer::Identity identity{}; snapshot::RestoreBindings bindings{};
    if (!replay_gl::CandidateBindings(&identity,&bindings)) return 0;
    const auto format=replay_gl::SourceFormat();
    if (format==RB_SOURCE_UNKNOWN) return 0;
#ifdef SM64_REPLAY_CONTEXT_LIFETIME
    // Demand has arrived (an admitted ticket reached the original command on
    // this thread): publish the worker's anchor now, never at ROM open. A
    // refused anchor is reported by ContextV1 acquire/error, not hidden here.
    replay_gl::EnsureAnchor();
#endif
    unsigned drawable_width=0, drawable_height=0;
    if (!replay_gl::DrawableExtent(&drawable_width,&drawable_height)) return 0;
    auto &window=dwnd();
    const auto width=window.getWidth(), height=window.getHeight(), offset=window.getHeightOffset();
    if (!width || !height || width>3840 || height>2160 || offset>2160 || !REG.VI_ORIGIN) return 0;
    if (width>drawable_width || uint64_t(offset)+height>drawable_height) return 0;
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
int __cdecl configure(const rs_callbacks *callbacks) {
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
    return rb_bind_source(surface)==RB_INSTALLED;
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
}
#pragma comment(linker,"/EXPORT:SM64ReplaySourceV2=_SM64ReplaySourceV2")
extern "C" const rs_api *__cdecl SM64ReplaySourceV2(uint32_t version,uint32_t bytes) {
    return version==RS_ABI_V2 && bytes==sizeof(rs_api) ? &api_v2 : nullptr;
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
