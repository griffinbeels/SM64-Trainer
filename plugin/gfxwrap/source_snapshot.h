/* SourceV2 -> owned snapshot connection. Not a server/encoder/lease manager. */
#pragma once
#include "link_source_api.h"
#include "gl_snapshot.h"
namespace source_capture {
// Fixed process-lifetime object. One source renderer, one lifecycle/delivery worker.
// Worker prepares sharing and resources before admission; no renderer cleanup waits.
class Snapshots {
public:
    bool prepare(snapshot::Gl, const rs_api&, const rb_surface&, unsigned slots, uint64_t budget);
    int submit(const rb_record*, rb_image*); // rb_capture_image adapter, renderer only
    bool completed(const rb_image&) const;
    void stop(); // any thread: revoke only; worker reaps/releases/destroys
    bool destroy(); // worker, after all actual GPU uses return
    snapshot::Pool& images() { return pool_; } // worker-owned operations only
private:
    snapshot::Pool pool_;
    rb_surface prepared_{};
    std::atomic<unsigned> enabled_{0}, calls_{0};
    static constexpr unsigned preparing = ~0u;
};
} // namespace source_capture
