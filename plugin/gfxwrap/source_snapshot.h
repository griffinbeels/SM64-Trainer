/* SourceV2 -> owned snapshot connection. Not a server/encoder/lease manager. */
#pragma once
#include "link_source_api.h"
#include "gl_snapshot.h"
namespace source_capture {
// Fixed process-lifetime object. One source renderer, one lifecycle/delivery worker.
// Worker prepares sharing and resources before admission; no renderer cleanup waits.
class Snapshots {
public:
    // owns_errors: the pool's own GL errors are consumed at the end of each
    // copy and fail only that copy, never the source's capture gateway (the
    // ghost copy's pool: a twin fault must not end the clean capture).
    bool prepare(snapshot::Gl, const rs_api&, const rb_surface&, unsigned slots, uint64_t budget,
                 const rs_api_v3* clean_source=nullptr, bool owns_errors=false);
    // The renderer's context is gone or replaced: forget its texture names
    // without GL (they died with it) by starting a fresh pool.
    void abandon();
    int submit(const rb_record*, rb_image*); // rb_capture_image adapter, renderer only
    // The ghost copy: a SourceV4 DISPLAY_VI descriptor for this record, which
    // must name the record's own geometry and identity. Renderer only.
    int submit_display(const rb_record*, const rs_source_descriptor3&, rb_image*);
    bool completed(const rb_image&) const;
    void stop(); // any thread: revoke only; worker reaps/releases/destroys
    bool destroy(); // worker, after all actual GPU uses return
    snapshot::Pool& images() { return *pool_.load(); } // worker-owned operations only
private:
    int copy(unsigned epoch, const rb_record&, const snapshot::Source&, snapshot::RestoreBindings, rb_image*);
    // Replaced, never freed, when a context is forgotten: stop() may still be
    // running on another thread (revocation), and a Pool holds no GL itself.
    std::atomic<snapshot::Pool*> pool_{new snapshot::Pool};
    rb_surface prepared_{};
    int (__cdecl *query_)(rb_ticket,rs_source_descriptor3*)=nullptr;
    std::atomic<unsigned> enabled_{0}, calls_{0};
    static constexpr unsigned preparing = ~0u;
};
} // namespace source_capture
