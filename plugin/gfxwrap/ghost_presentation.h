/* A renderer-owned clean VI picture beside the visible ghost presentation.
 * This is not the SourceV3 ABI; that adapter must bind CleanSurface to a ticket. */
#pragma once
#include "ghost_compositor.h"
// Each refusal of a ghost picture, and how readying the context ended, are
// recorded as the draw path's facts for the trainer's reports.
#include "ghost_draw_facts.h"

namespace ghost_presentation {
struct ViSource {
    uint32_t clean_framebuffer, clean_texture, width, height, vi_origin;
    uint32_t context_generation, drawable_generation;
};
struct GhostViImage {
    uint32_t texture, width, height, configuration, context_generation;
    uint64_t occurrence;
    uint32_t framebuffer; // private display FBO; required for the screen-size blit route
    uint32_t record_display; // 1: the recording takes this displayed picture (Ghosts in recording)
};
using ImageProvider = bool (*)(const ViSource&, GhostViImage*);
using ImageValidity = bool (*)(const GhostViImage&); // current command/configuration revision
using PresentationNotice = void (*)(const GhostViImage&, uint64_t presentation_serial, bool displayed);
struct CleanSurface {
    // Zero framebuffer/texture/resource names the unmodified default front,
    // qualified by the same successful swap and occurrence as a private copy.
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
/* The renderer's own record of what a presentation step changes, so the
 * step asks the driver nothing for it (each query waits for the driver's
 * thread). A field whose bit is clear is asked. */
struct Known {
    enum : uint32_t { read_framebuffer = 1, draw_framebuffer = 2,
        texture_unit = 4 /* the active unit and unit 0's 2D texture */, default_read_buffer = 8,
        srgb = 16, scissor_test = 32 /* viewport 0 */ };
    uint32_t fields;
    uint32_t read, draw, active_unit /* 0-based */, unit0_texture, default_read;
    uint8_t srgb_enabled, scissor_enabled;
};
struct Raster {
    void* user;
    void (*draw)(void*,const Draw&); // same pinned renderer shader/VI rectangle
    bool (*begin_gpu)(); // diagnostic-preserving gateway; no nesting
    bool (*end_gpu)();
    bool (*known)(Known*) = nullptr; // optional renderer record; nothing known without it
};
struct Instance;
Instance* create(const Dispatch&, uint32_t context_generation);
// Setup/resize only: an unchanged extent is allocation-free. Source dimensions
// may both be zero until a qualified VI image supplies the gamma target extent.
bool prepare(Instance*,uint32_t drawable_generation,uint32_t width,uint32_t height,
             uint32_t source_width,uint32_t source_height);
void begin_update(Instance*,uint64_t occurrence);
bool replace_vi(Instance*,const GhostViImage&,uint32_t vi_origin,bool gamma,const Raster&);
bool before_swap(Instance*,uint32_t vi_origin,const Raster&); // unmodified/blank VI needs no copy
bool begin_osd(Instance*);
void end_osd(Instance*);
void after_osd(Instance*);
// After OSD and any withdrawal: an image asking for it, while the display
// consumer is on, copies the picture the window is about to show into the
// display surface (allocated on first use). The clean surface is never touched.
void record_display(Instance*);
void cancel_overlay(Instance*); // restore already-saved clean BACK before swapping
void withdraw_image(Instance*); // command changed: restore clean, retain clean capture eligibility
void swap_completed(Instance*,bool success);
CleanSurface surface(const Instance*);
// The display copy published with the same swap as surface(), or nothing.
CleanSurface display(const Instance*);
GhostViImage pending_image(const Instance*);
void destroy(Instance*); // original context current
void abandon(Instance*); // lost context; no GL calls

// Integration facade: provider registration is serialized setup. Negotiation
// is revocable from the control thread; false prevents new display admission and
// descriptor publication. An already-open raster scope still restores bindings.
void set_provider(ImageProvider,PresentationNotice,ImageValidity);
void clean_consumer(bool negotiated);
bool clean_consumer();
// The ghost copy's lane: any thread. Off, record_display copies nothing.
void display_consumer(bool active);
bool display_consumer();
void context_ready();
void context_stopping();
void drawable_changing(); // invalidate pictures before resize; retain live context resources
void begin_update(uint64_t source_occurrence);
void before_swap();
void after_osd();
bool begin_osd_raster();
void end_osd_raster();
void swap_completed(bool success);
bool clean_surface(CleanSurface*);
// True only when the display copy belongs to the published clean picture.
bool display_surface(CleanSurface*);
}

#if defined(SM64_GHOST_RENDERER)
#include "GraphicsDrawer.h"
struct FrameBuffer;
namespace ghost_presentation {
void draw_vi(const FrameBuffer&,const GraphicsDrawer::CopyRectParams&,uint32_t vi_origin,bool split);
void draw_screen_vi(const FrameBuffer&,const GraphicsDrawer::BlitOrCopyRectParams&,uint32_t vi_origin);
}
#endif
