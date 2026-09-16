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
}
bool Snapshots::prepare(snapshot::Gl gl,const rs_api &source,const rb_surface &surface,
                        unsigned slots,uint64_t budget) {
    if((source.capabilities & (RS_CAP_BOUNDARY|RS_CAP_SOURCE_FORMAT))!=(RS_CAP_BOUNDARY|RS_CAP_SOURCE_FORMAT)
            || source.reserved || source.bytes!=sizeof(rs_api) || source.version!=RS_ABI_V2 || !source.begin_capture
            || !source.end_capture || !valid(surface)) return false;
    unsigned off=0;
    if(!enabled_.compare_exchange_strong(off,preparing)) return false;
    // A renderer already inside the previous generation must leave before the
    // worker can replace metadata. Never wait for it on either calling thread.
    if(calls_.load()) { enabled_.store(0); return false; }
    gl.begin_capture=source.begin_capture; gl.end_capture=source.end_capture;
    prepared_=surface;
    if(!pool_.prepare(gl,reinterpret_cast<HGLRC>(surface.context),surface.width,surface.height,slots,budget)) {
        enabled_.store(0); return false;
    }
    unsigned pending=preparing;
    if(!enabled_.compare_exchange_strong(pending,pool_.generation())) {
        pool_.stop(); pool_.destroy(); return false;
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
    const snapshot::Source image{0,GL_FRONT,0,int(s.bottom_offset),s.width,s.height,
                                 s.drawable_width,s.drawable_height};
    const snapshot::RestoreBindings restore{s.restore_read_framebuffer,s.restore_texture_2d,s.restore_read_buffer};
    snapshot::Ticket ticket{};
    const auto result=pool_.submit(epoch,image,restore,r->occurrence,s.boundary_qpc,&ticket);
    out->status=uint32_t(result);
    if(result==snapshot::Result::fault) out->ownership=RB_IMAGE_QUARANTINED;
    if(result!=snapshot::Result::submitted) return 0;
    *out={ticket.serial,ticket.slot,epoch,RB_IMAGE_SUBMITTED,uint32_t(result)};
    return 1;
}
bool Snapshots::completed(const rb_image &image) const {
    return image.ownership==RB_IMAGE_SUBMITTED && pool_.completed({image.serial,image.slot});
}
void Snapshots::stop() { enabled_.store(0); pool_.stop(); }
bool Snapshots::destroy() {
    return !enabled_.load() && !calls_.load() && pool_.destroy();
}
} // namespace source_capture
