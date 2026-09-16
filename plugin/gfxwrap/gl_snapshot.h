/* Experimental owned-texture transport; not a PJ64/backend selection ABI. */
#pragma once
#include <windows.h>
#include <GL/gl.h>
#include <atomic>
#include <stdint.h>

namespace snapshot {
constexpr unsigned max_slots = 8;
using Sync = struct SyncObject *;
struct Gl {
    void (APIENTRY *bind_framebuffer)(GLenum, GLuint) = nullptr;
    Sync (APIENTRY *fence)(GLenum, GLbitfield) = nullptr;
    GLenum (APIENTRY *wait)(Sync, GLbitfield, uint64_t) = nullptr;
    void (APIENTRY *delete_sync)(Sync) = nullptr;
    void (APIENTRY *copy_texture)(GLuint, GLint, GLint, GLint, GLint, GLint, GLsizei, GLsizei) = nullptr;
    // Standalone hosts default to a raw GL error check. Source integration MUST
    // replace both with its diagnostic-preserving, state-invalidating gateway.
    bool (__cdecl *begin_capture)() = nullptr;
    bool (__cdecl *end_capture)() = nullptr;
    // Caller must qualify the actual producer; a newer worker alone is insufficient.
    bool load(bool producer_core_dsa = false); // current worker; never per picture
};
struct Ticket { uint64_t serial = 0; unsigned slot = 0; };
struct Image { Ticket ticket; GLuint texture; unsigned width, height;
               uint64_t occurrence; int64_t qpc; };
enum class Result { submitted, inactive, full, invalid, wrong_context, fault, unqualified };
enum class Poll { pending, ready, stale, fault };
/* The renderer owns these values. Never guess defaults or call GL queries in
 * its callback to manufacture them. An integration must prove its state witness.
 * source_read_buffer is the source FBO's selector, not current read FBO's. */
struct RestoreBindings { GLuint read_framebuffer, texture_2d; GLenum source_read_buffer; };
struct Source { GLuint framebuffer; GLenum read_buffer; int x, y;
                unsigned width, height, surface_width, surface_height; };
struct Counters { unsigned submitted, full, invalid, faults; uint64_t texture_bytes; };

/* Fixed CPU object must outlive all possible producer invocations. One serialized
 * producer; one worker owns prepare/poll/release/reap/destroy. Stop is any-thread.
 * Shared contexts/identical implementation and validated source format are caller
 * prerequisites. Worker waits/cleanup never occur on the producer. */
class Pool {
public:
    bool prepare(const Gl &, HGLRC producer, unsigned width, unsigned height,
                 unsigned count, uint64_t byte_budget);
    unsigned generation() const { const unsigned v = enabled.load(); return v == preparing ? 0 : v; }
    void stop() { enabled.store(0); }
    Result submit(unsigned generation, const Source &, RestoreBindings, uint64_t occurrence,
                  int64_t qpc, Ticket *out);
    Poll poll(Ticket, Image *out); // worker, zero-time completion query
    bool release(Ticket);         // worker: fence AFTER all uses of Image.texture
    // Genuine tickets from this process-lifetime Pool only; not an arbitrary-ID validator.
    // Persists across destroy/reprepare and never infers completion from stale polling.
    bool completed(Ticket) const;
    void reap();                 // worker: zero-time release-fence queries
    bool destroy();              // worker: refuses if any ownership remains
    Counters counters() const;
#ifdef SNAPSHOT_TEST_HOST
    void (*before_activate)() = nullptr;
    void (*submission_probe)(unsigned phase) = nullptr;
#endif
private:
    static constexpr unsigned preparing = ~0u;
    enum State : uint64_t { free, copying, queued, borrowed, returning, poisoned };
    static constexpr uint64_t mask = 7;
    struct Slot {
        std::atomic<uint64_t> state{free};
        std::atomic<uint64_t> completed_serial{0};
        GLuint texture = 0;
        bool producer_attached = false;
        Sync fence = nullptr;
        uint64_t occurrence = 0;
        int64_t qpc = 0;
    } slots[max_slots];
    Gl gl;
    HGLRC source_context = nullptr, worker_context = nullptr;
    DWORD producer_thread = 0, worker_thread = 0;
    std::atomic<unsigned> enabled{0}, calls{0};
    std::atomic<unsigned> submitted{0}, full{0}, invalid{0}, faults{0};
    unsigned epoch = 0, width = 0, height = 0, count = 0;
    uint64_t serial = 0, bytes = 0;
    bool worker() const;
    void poison(Slot &, uint64_t);
};
} // namespace snapshot
