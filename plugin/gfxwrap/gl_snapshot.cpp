#include "gl_snapshot.h"
#include <limits.h>

namespace snapshot {
namespace {
constexpr GLenum read_framebuffer = 0x8CA8;
constexpr GLenum sync_complete = 0x9117, already_signaled = 0x911A;
constexpr GLenum condition_satisfied = 0x911C, wait_failed = 0x911D;
constexpr GLint rgba8 = 0x8058;
bool __cdecl standalone_error_check() { return glGetError() == GL_NO_ERROR; }
bool complete(GLenum value) { return value == already_signaled || value == condition_satisfied; }
// Renderer-thread capture: the producer prepared the pool in its own context.
// One context orders every use, so no fence, flush or worker thread is involved.
bool one_context(HGLRC source, HGLRC worker) { return source && source == worker; }
template<class T> bool load_proc(T &target, const char *name) {
    PROC p = wglGetProcAddress(name);
    const auto address = reinterpret_cast<uintptr_t>(p);
    if (!p || address <= 3 || address == UINTPTR_MAX) return false;
    target = reinterpret_cast<T>(p);
    return true;
}
struct Call {
    std::atomic<unsigned> &count;
    explicit Call(std::atomic<unsigned> &v) : count(v) { count.fetch_add(1); }
    ~Call() { count.fetch_sub(1); }
};
}
static_assert(std::atomic<uint64_t>::is_always_lock_free, "slot claims must be lock-free");
static_assert(std::atomic<unsigned>::is_always_lock_free, "admission must be lock-free");
bool Gl::load(bool producer_core_dsa) {
    *this = {};
    begin_capture = end_capture = standalone_error_check;
    // Core DSA removes texture-binding mutation entirely on GL 4.5+.
    GLint major=0, minor=0;
    glGetIntegerv(0x821B, &major); glGetIntegerv(0x821C, &minor);
    if (producer_core_dsa && (major > 4 || (major == 4 && minor >= 5)))
        load_proc(copy_texture, "glCopyTextureSubImage2D");
    return load_proc(bind_framebuffer, "glBindFramebuffer") && load_proc(fence, "glFenceSync")
        && load_proc(wait, "glClientWaitSync") && load_proc(delete_sync, "glDeleteSync");
}
bool Pool::worker() const {
    return GetCurrentThreadId() == worker_thread && wglGetCurrentContext() == worker_context;
}
bool Pool::prepare(const Gl &api, HGLRC producer, unsigned w, unsigned h,
                   unsigned n, uint64_t budget) {
    // Lifecycle is worker-serialized. No resizing or ownership reuse on pressure.
    if (enabled.load() || count || calls.load() || epoch >= UINT_MAX - 1 || !producer ||
        !w || !h || w > INT_MAX || h > INT_MAX || !n || n > max_slots ||
        !api.bind_framebuffer || !api.fence || !api.wait || !api.delete_sync
        || !api.begin_capture || !api.end_capture) return false;
    const uint64_t per_texture = uint64_t(w) * h * 4;
    if (per_texture > budget / n) return false;
    // Reserve before any driver preflight; stop during setup must win activation.
    unsigned disabled = 0;
    if (!enabled.compare_exchange_strong(disabled, preparing)) return false;
    const HGLRC current = wglGetCurrentContext();
    if (!current) { stop(); return false; }
    if (current == producer) {
        // The producer prepares its own pool (renderer-thread capture): DSA
        // storage touches no binding or unpack state, and nothing waits.
        using Create = void (APIENTRY *)(GLenum, GLsizei, GLuint *);
        using Storage = void (APIENTRY *)(GLuint, GLsizei, GLenum, GLsizei, GLsizei);
        Create create_textures = nullptr; Storage storage = nullptr;
        if (!api.copy_texture || !load_proc(create_textures, "glCreateTextures")
            || !load_proc(storage, "glTextureStorage2D")) { stop(); return false; }
        gl = api;
        worker_context = source_context = current;
        worker_thread = producer_thread = GetCurrentThreadId();
        width = w; height = h; count = n; bytes = per_texture * n;
        GLuint names[max_slots]{};
        create_textures(GL_TEXTURE_2D, GLsizei(n), names);
        bool created = true;
        for (unsigned i = 0; i < n; ++i) {
            slots[i].texture = names[i]; slots[i].producer_attached = true; slots[i].fence = nullptr;
            if (names[i]) storage(names[i], 1, GLenum(rgba8), GLsizei(w), GLsizei(h));
            else created = false;
        }
        if (!created) { stop(); destroy(); return false; }
        unsigned pending = preparing;
        if (!enabled.compare_exchange_strong(pending, ++epoch)) { destroy(); return false; }
        return true;
    }
    GLint max_size = 0;
    glGetIntegerv(GL_MAX_TEXTURE_SIZE, &max_size);
    if (w > unsigned(max_size) || h > unsigned(max_size)) { stop(); return false; }
    GLint unpack_buffer = 0;
    glGetIntegerv(0x88EF, &unpack_buffer); // GL_PIXEL_UNPACK_BUFFER_BINDING
    if (unpack_buffer) { stop(); return false; }
    // This is our own worker context; errors here belong to preparation.
    if (glGetError() != GL_NO_ERROR) { stop(); return false; }
    gl = api;
    worker_context = current;
    worker_thread = GetCurrentThreadId();
    source_context = producer;
    producer_thread = 0;
    width = w; height = h; count = n; bytes = per_texture * n;
    GLint old_texture = 0;
    glGetIntegerv(GL_TEXTURE_BINDING_2D, &old_texture);
    bool ok = true;
    for (unsigned i = 0; i < count; ++i) {
        auto &slot = slots[i];
        slot.producer_attached = false;
        glGenTextures(1, &slot.texture);
        glBindTexture(GL_TEXTURE_2D, slot.texture);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
        glTexImage2D(GL_TEXTURE_2D, 0, rgba8, GLsizei(w), GLsizei(h), 0,
                     GL_RGBA, GL_UNSIGNED_BYTE, nullptr);
        if (!slot.texture || glGetError() != GL_NO_ERROR) ok = false;
    }
    glBindTexture(GL_TEXTURE_2D, GLuint(old_texture));
    // Worker-only one-time preparation completion before publishing shared names.
    glFinish();
    if (glGetError() != GL_NO_ERROR) ok = false;
    if (!ok) { stop(); destroy(); return false; }
#ifdef SNAPSHOT_TEST_HOST
    if (before_activate) before_activate();
#endif
    unsigned pending = preparing;
    if (!enabled.compare_exchange_strong(pending, ++epoch)) {
        destroy(); // cancellation won; never re-enable a stopped preparation
        return false;
    }
    return true;
}
void Pool::poison(Slot &slot, uint64_t tag) {
    slot.state.store(tag | poisoned);
    faults.fetch_add(1);
    stop(); // quarantine allocation: completion was not established
}
Result Pool::submit(unsigned expected_epoch, const Source &src, RestoreBindings bindings, uint64_t occurrence,
                    int64_t qpc, Ticket *out) {
    if (out) *out = {};
    Call entered(calls);
    if (!expected_epoch || enabled.load() != expected_epoch) return Result::inactive;
    if (wglGetCurrentContext() != source_context) return Result::wrong_context;
    const DWORD thread = GetCurrentThreadId();
    if (producer_thread && producer_thread != thread) return Result::wrong_context;
    producer_thread = thread; // one serialized producer is an API prerequisite
    if (!out || !occurrence || src.width != width || src.height != height ||
        src.x < 0 || src.y < 0 || src.surface_width > INT_MAX || src.surface_height > INT_MAX ||
        uint64_t(src.x) + width > src.surface_width ||
        uint64_t(src.y) + height > src.surface_height ||
        (!src.framebuffer && src.read_buffer != GL_FRONT && src.read_buffer != GL_BACK) ||
        (src.framebuffer && src.read_buffer != 0x8CE0)) {
        invalid.fetch_add(1);
        return Result::invalid;
    }
    // Token space never wraps; occurrence is metadata, not an ownership authority.
    if (serial == (UINT64_MAX >> 3)) { stop(); return Result::fault; }
    for (unsigned i = 0; i < count; ++i) {
        auto &slot = slots[i];
        uint64_t vacant = free;
        const uint64_t tag = (serial + 1) << 3;
        if (!slot.state.compare_exchange_strong(vacant, tag | copying)) continue;
        ++serial;
        // No source GL query at all while inactive/full. The claimed slot has no
        // GPU work yet, so a failed preflight can be returned without quarantine.
        if (!gl.begin_capture()) {
            faults.fetch_add(1); stop(); slot.state.store(free);
            return Result::unqualified;
        }
        slot.occurrence = occurrence;
        slot.qpc = qpc;
#ifdef SNAPSHOT_TEST_HOST
        if (submission_probe) submission_probe(0);
#endif
        gl.bind_framebuffer(read_framebuffer, src.framebuffer);
        glReadBuffer(src.read_buffer);
        // First attachment propagates worker-created shared-object storage/state.
        // Worker later READS only; all subsequent image writes remain on producer.
        const bool attach_texture = !gl.copy_texture || !slot.producer_attached;
        if (attach_texture) glBindTexture(GL_TEXTURE_2D, slot.texture);
#ifdef SNAPSHOT_TEST_HOST
        if (submission_probe) submission_probe(1);
#endif
        // GPU_COPY_BEGIN: both API variants share the same crop/pixel contract.
        if (gl.copy_texture)
            gl.copy_texture(slot.texture, 0, 0, 0, src.x, src.y, GLsizei(width), GLsizei(height));
        else
            glCopyTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, src.x, src.y, GLsizei(width), GLsizei(height));
        // GPU_COPY_END
#ifdef SNAPSHOT_TEST_HOST
        if (submission_probe) submission_probe(2);
#endif
        if (attach_texture) glBindTexture(GL_TEXTURE_2D, bindings.texture_2d);
        slot.producer_attached = true;
        glReadBuffer(bindings.source_read_buffer);
        gl.bind_framebuffer(read_framebuffer, bindings.read_framebuffer);
#ifdef SNAPSHOT_TEST_HOST
        if (submission_probe) submission_probe(3);
#endif
        // One context orders every later use of this texture; only a second
        // context needs the fence (it cannot flush this producer's, either).
        const bool single = one_context(source_context, worker_context);
        slot.fence = single ? nullptr : gl.fence(sync_complete, 0);
#ifdef SNAPSHOT_TEST_HOST
        if (submission_probe) submission_probe(4);
#endif
        if (!single) glFlush();
#ifdef SNAPSHOT_TEST_HOST
        if (submission_probe) submission_probe(5);
#endif
        // A fence can signal after a failed copy. Qualify BEFORE queue publication,
        // while the renderer still exclusively owns this slot. Never let a worker
        // race a caller-side check and consume unchanged/stale texture contents.
        const bool source_ok = gl.end_capture();
        if ((!single && !slot.fence) || !source_ok) { poison(slot, tag); return Result::fault; }
        slot.state.store(tag | queued);
        *out = {serial, i};
        submitted.fetch_add(1);
        return Result::submitted; // submitted != independently verified pixel identity
    }
    full.fetch_add(1);
    return Result::full;
}
Poll Pool::poll(Ticket ticket, Image *out) {
    const bool single = one_context(source_context, worker_context);
    if ((!single && !worker()) || !out || !ticket.serial || ticket.serial > (UINT64_MAX >> 3) ||
        ticket.slot >= count) return Poll::stale;
    auto &slot = slots[ticket.slot];
    const uint64_t tag = ticket.serial << 3;
    if (single) { // no GL: repeatable, so the producer can sample what it just copied
        const uint64_t state = slot.state.load();
        if (state != (tag | queued) && state != (tag | borrowed)) return Poll::stale;
        slot.state.store(tag | borrowed);
        *out = {ticket, slot.texture, width, height, slot.occurrence, slot.qpc};
        return Poll::ready;
    }
    if (slot.state.load() != (tag | queued)) return Poll::stale;
    const GLenum result = gl.wait(slot.fence, 0, 0);
    if (result == wait_failed) { poison(slot, tag); return Poll::fault; }
    if (!complete(result)) return Poll::pending;
    gl.delete_sync(slot.fence);
    slot.fence = nullptr;
    slot.state.store(tag | borrowed);
    // Explicit rebind after completion propagates shared-object updates.
    glBindTexture(GL_TEXTURE_2D, slot.texture);
    *out = {ticket, slot.texture, width, height, slot.occurrence, slot.qpc};
    return Poll::ready;
}
bool Pool::release(Ticket ticket) {
    const bool single = one_context(source_context, worker_context);
    if ((!single && !worker()) || !ticket.serial || ticket.serial > (UINT64_MAX >> 3) ||
        ticket.slot >= count) return false;
    auto &slot = slots[ticket.slot];
    const uint64_t tag = ticket.serial << 3;
    if (slot.state.load() != (tag | borrowed)) return false;
    if (single) { // later producer copies are ordered after every use
        slot.completed_serial.store(ticket.serial);
        slot.state.store(free);
        return true;
    }
    slot.fence = gl.fence(sync_complete, 0);
    glFlush();
    if (!slot.fence) { poison(slot, tag); return false; }
    slot.state.store(tag | returning);
    return true;
}
void Pool::reap() {
    if (one_context(source_context, worker_context) || !worker()) return;
    for (unsigned i = 0; i < count; ++i) {
        auto &slot = slots[i];
        const uint64_t state = slot.state.load();
        if ((state & mask) != returning) continue;
        const GLenum result = gl.wait(slot.fence, 0, 0);
        if (result == wait_failed) { poison(slot, state & ~mask); continue; }
        if (!complete(result)) continue;
        gl.delete_sync(slot.fence);
        slot.fence = nullptr;
        slot.completed_serial.store(state >> 3);
        slot.state.store(free);
    }
}
bool Pool::completed(Ticket ticket) const {
    return ticket.serial && ticket.slot < max_slots
        && ticket.serial <= slots[ticket.slot].completed_serial.load();
}
bool Pool::destroy() {
    if (!worker() || enabled.load() || calls.load()) return false;
    for (unsigned i = 0; i < count; ++i)
        if (slots[i].state.load() != free) return false;
    for (unsigned i = 0; i < count; ++i) {
        glDeleteTextures(1, &slots[i].texture);
        slots[i].texture = 0;
    }
    count = 0; bytes = 0;
    return true;
}
Counters Pool::counters() const {
    // Lifecycle owner only: byte count changes only during prepare/destroy.
    return {submitted.load(), full.load(), invalid.load(), faults.load(), bytes};
}
} // namespace snapshot
