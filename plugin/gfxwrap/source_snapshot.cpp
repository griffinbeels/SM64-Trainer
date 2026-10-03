#include "source_snapshot.h"
namespace source_capture {
namespace {
bool valid(const rb_surface &s) {
    return s.context && s.read_drawable && s.context_generation && s.drawable_generation
        && (s.source_format==RB_SOURCE_RGB8_LINEAR || s.source_format==RB_SOURCE_RGB8_SRGB)
        && s.renderer_thread && s.width && s.height && s.width<=3840 && s.height<=2160
        && s.drawable_width>=s.width && uint64_t(s.bottom_offset)+s.height<=s.drawable_height;
}
bool matches(const rb_surface &a,const rb_surface &b) {
    return a.context==b.context && a.read_drawable==b.read_drawable
        && a.context_generation==b.context_generation && a.drawable_generation==b.drawable_generation
        && a.renderer_thread==b.renderer_thread && a.width==b.width && a.height==b.height
        && a.bottom_offset==b.bottom_offset && a.drawable_width==b.drawable_width
        && a.drawable_height==b.drawable_height && a.source_format==b.source_format;
}
struct Entered { std::atomic<unsigned>&calls; explicit Entered(std::atomic<unsigned>&c):calls(c){calls.fetch_add(1);}
    ~Entered(){calls.fetch_sub(1);} };
// The source gateway records any GL error it finds at its end and then
// refuses every later capture. A pool that owns its errors takes them first:
// a failed copy fails only itself. One source renderer per process.
bool (__cdecl *source_end)()=nullptr;
bool __cdecl end_owning_errors() {
    bool clean=true;
    for(unsigned i=0;i<8;++i){if(glGetError()==GL_NO_ERROR)break;clean=false;}
    return source_end() && clean;
}
}
void Snapshots::abandon() { enabled_.store(0); pool_.exchange(new snapshot::Pool)->stop(); }
bool Snapshots::prepare(snapshot::Gl gl,const rs_api &source,const rb_surface &surface,
                        unsigned slots,uint64_t budget,const rs_api_v3* clean_source,bool owns_errors) {
    if((source.capabilities & (RS_CAP_BOUNDARY|RS_CAP_SOURCE_FORMAT))!=(RS_CAP_BOUNDARY|RS_CAP_SOURCE_FORMAT)
            || source.reserved || source.bytes!=sizeof(rs_api) || source.version!=RS_ABI_V2 || !source.begin_capture
            || !source.end_capture || !valid(surface) || (clean_source && !rs_valid_v3(clean_source))) return false;
    unsigned off=0;
    if(!enabled_.compare_exchange_strong(off,preparing)) return false;
    // A renderer already inside the previous generation must leave before the
    // worker can replace metadata. Never wait for it on either calling thread.
    if(calls_.load()) { enabled_.store(0); return false; }
    gl.begin_capture=source.begin_capture; gl.end_capture=source.end_capture;
    if(owns_errors){source_end=source.end_capture;gl.end_capture=end_owning_errors;}
    prepared_=surface;query_=clean_source ? clean_source->query : nullptr;
    // Prepared on the renderer's thread, the pool shares its one context.
    if(!pool_.load()->prepare(gl,reinterpret_cast<HGLRC>(surface.context),surface.width,surface.height,slots,budget)) {
        enabled_.store(0); return false;
    }
    unsigned pending=preparing;
    if(!enabled_.compare_exchange_strong(pending,pool_.load()->generation())) {
        pool_.load()->stop(); pool_.load()->destroy(); return false;
    }
    return true;
}
int Snapshots::submit(const rb_record *r,rb_image *out) {
    if(!out) return 0;
    *out={}; Entered entered(calls_);
    const unsigned epoch=enabled_.load();
    if(!epoch || epoch==preparing || !r || !r->occurrence || !r->epoch) return 0;
    const auto &s=r->surface;
    if(!matches(s,prepared_) || GetCurrentThreadId()!=s.renderer_thread
            || reinterpret_cast<uintptr_t>(wglGetCurrentDC())!=s.read_drawable) {
        out->status=uint32_t(snapshot::Result::wrong_context); return 0;
    }
    snapshot::Source image{0,GL_FRONT,0,int(s.bottom_offset),s.width,s.height,
                                 s.drawable_width,s.drawable_height};
    snapshot::RestoreBindings restore{s.restore_read_framebuffer,s.restore_texture_2d,s.restore_read_buffer};
    if(query_) {
        rs_source_descriptor3 d{};
        if(!query_({r->occurrence,r->slot},&d) || d.bytes!=sizeof(d) || d.version!=RS_ABI_V3 || d.reserved ||
           d.context!=s.context || d.read_drawable!=s.read_drawable || d.renderer_thread!=s.renderer_thread ||
           d.context_generation!=s.context_generation || d.drawable_generation!=s.drawable_generation ||
           d.x || d.y!=s.bottom_offset || d.width!=s.width || d.height!=s.height ||
           d.surface_width!=s.drawable_width || d.surface_height!=s.drawable_height || d.vi_origin!=s.post_vi_origin ||
           (d.kind!=RS_SOURCE_DEFAULT_FRONT && d.kind!=RS_SOURCE_CLEAN_VI)) {
            out->status=uint32_t(snapshot::Result::unqualified);return 0;
        }
        const bool clean=d.kind==RS_SOURCE_CLEAN_VI;
        if((clean && (!d.framebuffer || d.read_buffer!=0x8CE0 || !d.resource_generation ||
                      !d.presentation_serial || !d.update_occurrence || d.source_format!=RB_SOURCE_RGB8_LINEAR)) ||
           (!clean && (d.framebuffer || d.read_buffer!=GL_FRONT || d.source_format!=s.source_format))) {
            out->status=uint32_t(snapshot::Result::unqualified);return 0;
        }
        image={d.framebuffer,d.read_buffer,int(d.x),int(d.y),d.width,d.height,d.surface_width,d.surface_height};
        // The private source FBO permanently owns COLOR_ATTACHMENT0; restoring
        // the default framebuffer's selector into it would be an invalid call.
        if(clean)restore.source_read_buffer=0x8CE0;
    }
    return copy(epoch,*r,image,restore,out);
}
int Snapshots::submit_display(const rb_record *r,const rs_source_descriptor3 &d,rb_image *out) {
    if(!out) return 0;
    *out={}; Entered entered(calls_);
    const unsigned epoch=enabled_.load();
    if(!epoch || epoch==preparing || !r || !r->occurrence || !r->epoch) return 0;
    const auto &s=r->surface;
    if(!matches(s,prepared_) || GetCurrentThreadId()!=s.renderer_thread
            || reinterpret_cast<uintptr_t>(wglGetCurrentDC())!=s.read_drawable) {
        out->status=uint32_t(snapshot::Result::wrong_context); return 0;
    }
    if(d.bytes!=sizeof(d) || d.version!=RS_ABI_V4 || d.kind!=RS_SOURCE_DISPLAY_VI || d.reserved ||
       !d.framebuffer || d.read_buffer!=0x8CE0 || d.source_format!=RB_SOURCE_RGB8_LINEAR || !d.resource_generation ||
       !d.presentation_serial || !d.update_occurrence ||
       d.context!=s.context || d.read_drawable!=s.read_drawable || d.renderer_thread!=s.renderer_thread ||
       d.context_generation!=s.context_generation || d.drawable_generation!=s.drawable_generation ||
       d.x || d.y!=s.bottom_offset || d.width!=s.width || d.height!=s.height ||
       d.surface_width!=s.drawable_width || d.surface_height!=s.drawable_height || d.vi_origin!=s.post_vi_origin) {
        out->status=uint32_t(snapshot::Result::unqualified); return 0;
    }
    const snapshot::Source image{d.framebuffer,d.read_buffer,0,int(d.y),d.width,d.height,d.surface_width,d.surface_height};
    return copy(epoch,*r,image,{s.restore_read_framebuffer,s.restore_texture_2d,0x8CE0},out);
}
int Snapshots::copy(unsigned epoch,const rb_record &r,const snapshot::Source &image,snapshot::RestoreBindings restore,
                    rb_image *out) {
    snapshot::Ticket ticket{};
    const auto result=pool_.load()->submit(epoch,image,restore,r.occurrence,r.surface.boundary_qpc,&ticket);
    out->status=uint32_t(result);
    if(result==snapshot::Result::fault) out->ownership=RB_IMAGE_QUARANTINED;
    if(result!=snapshot::Result::submitted) return 0;
    *out={ticket.serial,ticket.slot,epoch,RB_IMAGE_SUBMITTED,uint32_t(result)};
    return 1;
}
bool Snapshots::completed(const rb_image &image) const {
    return image.ownership==RB_IMAGE_SUBMITTED && pool_.load()->completed({image.serial,image.slot});
}
void Snapshots::stop() { enabled_.store(0); pool_.load()->stop(); }
bool Snapshots::destroy() {
    return !enabled_.load() && !calls_.load() && pool_.load()->destroy();
}
} // namespace source_capture
