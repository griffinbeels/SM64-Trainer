/* A renderer-owned clean VI picture beside the visible ghost presentation.
 * This is not the SourceV3 ABI; that adapter must bind CleanSurface to a ticket. */
#pragma once
#include "ghost_compositor.h"

namespace ghost_presentation {
struct ViSource {
    uint32_t clean_framebuffer, clean_texture, width, height, vi_origin;
    uint32_t context_generation, drawable_generation;
};
struct GhostViImage {
    uint32_t texture, width, height, configuration, context_generation;
    uint64_t occurrence;
    uint32_t framebuffer; // private display FBO; required for the screen-size blit route
};
using ImageProvider = bool (*)(const ViSource&, GhostViImage*);
using ImageValidity = bool (*)(const GhostViImage&); // current command/configuration revision
using PresentationNotice = void (*)(const GhostViImage&, uint64_t presentation_serial, bool displayed);
struct CleanSurface {
    uint32_t framebuffer, texture, width, height;
    uint32_t context_generation, drawable_generation, resource_generation, vi_origin;
    uint64_t presentation_serial, update_occurrence;
};
struct Dispatch {
    ghost_compositor::Dispatch gl;
    void (GC_GL_CALL *CopyTexSubImage2D)(uint32_t,int32_t,int32_t,int32_t,int32_t,int32_t,int32_t,int32_t)=nullptr;
    void (GC_GL_CALL *DrawBuffers)(int32_t,const uint32_t*)=nullptr;
};
enum class DrawKind { gamma, vi };
struct Draw {
    DrawKind kind;
    uint32_t texture, framebuffer, width, height;
    uint32_t source_framebuffer; // image FBO, or private gamma result for the VI draw
};
struct Raster {
    void* user;
    void (*draw)(void*,const Draw&); // same pinned renderer shader/VI rectangle
    bool (*begin_gpu)(); // diagnostic-preserving gateway; no nesting
    bool (*end_gpu)();
};
struct Instance;
Instance* create(const Dispatch&, uint32_t context_generation);
// Setup/resize only: an unchanged extent is allocation-free. Source dimensions
// may both be zero until a qualified VI image supplies the gamma target extent.
bool prepare(Instance*,uint32_t drawable_generation,uint32_t width,uint32_t height,
             uint32_t source_width,uint32_t source_height);
void begin_update(Instance*,uint64_t occurrence);
bool replace_vi(Instance*,const GhostViImage&,uint32_t vi_origin,bool gamma,const Raster&);
bool before_swap(Instance*,uint32_t vi_origin,const Raster&); // fallback/blank VI too
bool begin_osd(Instance*);
void end_osd(Instance*);
void after_osd(Instance*);
void cancel_overlay(Instance*); // restore already-saved clean BACK before swapping
void withdraw_image(Instance*); // command changed: restore clean, retain clean capture eligibility
void swap_completed(Instance*,bool success);
CleanSurface surface(const Instance*);
GhostViImage pending_image(const Instance*);
void destroy(Instance*); // original context current
void abandon(Instance*); // lost context; no GL calls

// Integration facade: provider registration is serialized setup. Negotiation
// is revocable from the control thread; false prevents new display admission and
// descriptor publication. An already-open raster scope still restores bindings.
void set_provider(ImageProvider,PresentationNotice,ImageValidity);
void clean_consumer(bool negotiated);
bool clean_consumer();
void context_ready();
void context_stopping();
void begin_update(uint64_t source_occurrence);
void before_swap();
void after_osd();
bool begin_osd_raster();
void end_osd_raster();
void swap_completed(bool success);
bool clean_surface(CleanSurface*);
}

#if defined(SM64_GHOST_RENDERER)
#include "GraphicsDrawer.h"
struct FrameBuffer;
namespace ghost_presentation {
void draw_vi(const FrameBuffer&,const GraphicsDrawer::CopyRectParams&,uint32_t vi_origin,bool split);
void draw_screen_vi(const FrameBuffer&,const GraphicsDrawer::BlitOrCopyRectParams&,uint32_t vi_origin);
}
#endif
