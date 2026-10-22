/* Three live body poses using a caller-owned local model. No model/GL/IO owner. */
#pragma once
#include "ghost_live_input.h"
#include "ghost_pose.h"
#include <atomic>
#include <stdint.h>

namespace ghost_live {
class Renderer;
struct ModelBinding {
    sg_model *model = nullptr;
    uint64_t model_id = 0;
    uint32_t rom_generation = 0, configuration = 0, context_serial = 0;
};
/* Process-local token, never a wire/IPC value. Its lifetime ends with owner. */
struct DrawToken {
    const Renderer *owner = nullptr;
    uint64_t boundary = 0, evaluation = 0;
    uint32_t included_seats = 0;
};
class Renderer {
public:
    /* Owns three large fixed scratch buffers. Construct in stable/static or
     * setup-allocated storage, not PJ64's default stack. No model is retained
     * by Input; this object only borrows the model passed to begin.
     * First begin binds one render thread for this object's lifetime. */
    explicit Renderer(Input &input) noexcept : input_(input) {}
    Renderer(const Renderer &) = delete;
    Renderer &operator=(const Renderer &) = delete;

    /* Every list invalidates previous tokens/geometry before Input consumption.
     * Binding names the ACTUAL local borrowed model/ROM/config/context; none
     * comes from a received packet. Caller keeps that borrow alive and calls
     * retire on this render owner BEFORE releasing it. A new render thread
     * needs a new Renderer after the old owner has stopped.
     * plan may choose before the local camera/world is qualified at the seal. */
    bool begin(const ModelBinding &, const gc_sample &local, uint64_t now_ns) noexcept;
    bool plan() const noexcept;
    uint32_t available_mask() const noexcept;
    /* out has room for GLIVE_MAX_PEERS values and does not alias this object.
     * Output is in increasing included-seat order. Geometry borrows this
     * object's per-seat scratch until the next evaluate,
     * begin or retire. Every evaluation attempt invalidates the prior token,
     * including refusal. Caller supplies the qualified local WorldPass. */
    bool evaluate(const gc_sample &local, const ghost::WorldPass &, uint64_t now_ns,
                  float opacity, ghost_compositor::Pose *out, uint32_t &count,
                  DrawToken &) noexcept;
    /* Recheck at ACTUAL presentation. Only included seats participate. This
     * supplements, rather than replaces, the caller's local-model/context
     * borrow validity check. Input withdrawal never authorizes model release. */
    bool current(const DrawToken &, uint64_t now_ns) const noexcept;
    // Copy only an included source from the exact retained evaluation, for
    // annotation/proximity. Caller still rechecks current at presentation.
    bool source(const DrawToken &, unsigned seat, gc_sample &out) const noexcept;
    bool receipt(const DrawToken &,unsigned seat,uint64_t &gate,uint64_t &received_ns) const noexcept;
    void retire() noexcept; // same render owner, before borrowed-resource release

private:
    friend struct RendererWitness; // counter limits only, in the native host
    Input &input_;
    std::atomic<uint32_t> owner_thread_{0};
    ModelBinding binding_{};
    Snapshot adopted_{}, drawn_{};
    gc_sample local_{};
    uint64_t boundary_ = 0, evaluation_ = 0, begun_ns_ = 0, evaluated_ns_ = 0;
    uint32_t selected_ = 0, included_ = 0;
    bool active_ = false, exhausted_ = false;
    ghost_pose::Scratch scratch_[GLIVE_MAX_PEERS];

    bool owner(bool claim) noexcept;
    bool owner() const noexcept;
    bool advance(uint64_t &) noexcept;
    void invalidate() noexcept;
};
}
