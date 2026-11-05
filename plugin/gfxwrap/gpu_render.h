/* The capture's GPU work, done on the renderer's own thread in the renderer's
 * own context (docs/replay-gpu-runtime.md, "Renderer-thread capture"). A second
 * thread using the GPU in Project64 makes the NVIDIA driver freeze the renderer
 * once (threaded optimization), so nothing here ever runs anywhere else, and no
 * D3D device exists in this process: the encoder helper creates the bridge's
 * textures (and, per transport, its fences or sections) and this side opens
 * them by name.
 *
 * Every call belongs to the renderer thread with the renderer's context
 * current, inside its capture gateway. Nothing waits: fences are polled with a
 * zero timeout, keyed mutexes are tried with a zero timeout, and a semaphore
 * wait is queued only for a value the helper already reported. Only the state
 * a blit or readback touches is changed, and StateGuard restores it. */
#pragma once
#include <windows.h>
#include <GL/gl.h>
#include <stdint.h>
#include <stddef.h>
#include "gpu_transport.h"

namespace render_capture {
using GLsync = struct __GLsync *;
using GLuint64 = unsigned long long;
// gd_status.error when reason is GD_GL_UNSUPPORTED. runtime_delivery projects
// known details into append-only ControlV1 reasons before a picture channel.
// keyed_mutex is kept for older producers: a context without the keyed mutex
// now selects another transport. no_transport: every transport this context
// offers already failed in this process (or a test forced one it lacks).
enum class LoadError : uint32_t {
    none = 0, version_45 = 1, memory_object = 2, memory_object_win32 = 3,
    keyed_mutex = 4, entry_point = 5, snapshot_entry_point = 6, no_transport = 7,
};
// The context's capabilities (GT_CAP_*), one pass over its extension strings.
uint32_t probe_capabilities();
// The first transport this context supports, in KEYED_MUTEX, SEMAPHORE,
// READBACK order, at or after `floor` (a process's failed transports are
// below it). `forced` (test hosts only) starts the order at that transport.
// GT_TRANSPORT_NONE when nothing qualifies; unavailable() says why.
uint32_t select_transport(uint32_t capabilities, uint32_t forced, uint32_t floor);
LoadError unavailable(uint32_t capabilities);
struct Gl {
    void (APIENTRY *create_framebuffers)(GLsizei, GLuint *) = nullptr;
    void (APIENTRY *delete_framebuffers)(GLsizei, const GLuint *) = nullptr;
    void (APIENTRY *framebuffer_texture)(GLuint, GLenum, GLuint, GLint) = nullptr;
    void (APIENTRY *framebuffer_read_buffer)(GLuint, GLenum) = nullptr;
    void (APIENTRY *framebuffer_draw_buffer)(GLuint, GLenum) = nullptr;
    GLenum (APIENTRY *check_framebuffer)(GLuint, GLenum) = nullptr;
    void (APIENTRY *blit)(GLuint, GLuint, GLint, GLint, GLint, GLint, GLint, GLint, GLint, GLint,
                          GLbitfield, GLenum) = nullptr;
    void (APIENTRY *create_textures)(GLenum, GLsizei, GLuint *) = nullptr;
    void (APIENTRY *texture_storage)(GLuint, GLsizei, GLenum, GLsizei, GLsizei) = nullptr;
    void (APIENTRY *create_buffers)(GLsizei, GLuint *) = nullptr;
    void (APIENTRY *buffer_storage)(GLuint, ptrdiff_t, const void *, GLbitfield) = nullptr;
    void (APIENTRY *delete_buffers)(GLsizei, const GLuint *) = nullptr;
    void (APIENTRY *get_buffer)(GLuint, ptrdiff_t, ptrdiff_t, void *) = nullptr;
    void (APIENTRY *bind_buffer)(GLenum, GLuint) = nullptr;
    void (APIENTRY *get_texture_image)(GLuint, GLint, GLint, GLint, GLint, GLsizei, GLsizei, GLsizei,
                                       GLenum, GLenum, GLsizei, void *) = nullptr;
    GLsync (APIENTRY *fence)(GLenum, GLbitfield) = nullptr;
    GLenum (APIENTRY *client_wait)(GLsync, GLbitfield, GLuint64) = nullptr;
    void (APIENTRY *delete_sync)(GLsync) = nullptr;
    void (APIENTRY *create_memory)(GLsizei, GLuint *) = nullptr;
    void (APIENTRY *delete_memory)(GLsizei, const GLuint *) = nullptr;
    void (APIENTRY *memory_parameter)(GLuint, GLenum, const GLint *) = nullptr;
    void (APIENTRY *import_name)(GLuint, GLuint64, GLenum, const void *) = nullptr;
    void (APIENTRY *texture_storage_memory)(GLuint, GLsizei, GLenum, GLsizei, GLsizei, GLuint, GLuint64) = nullptr;
    GLboolean (APIENTRY *acquire_key)(GLuint, GLuint64, GLuint) = nullptr;
    GLboolean (APIENTRY *release_key)(GLuint, GLuint64) = nullptr;
    void (APIENTRY *get_unsigned_bytes)(GLenum, GLubyte *) = nullptr;
    // SEMAPHORE (GL_EXT_semaphore, GL_EXT_semaphore_win32)
    void (APIENTRY *gen_semaphores)(GLsizei, GLuint *) = nullptr;
    void (APIENTRY *delete_semaphores)(GLsizei, const GLuint *) = nullptr;
    void (APIENTRY *semaphore_parameter)(GLuint, GLenum, const GLuint64 *) = nullptr;
    void (APIENTRY *import_semaphore_name)(GLuint, GLenum, const void *) = nullptr;
    void (APIENTRY *wait_semaphore)(GLuint, GLuint, const GLuint *, GLuint, const GLuint *, const GLenum *) = nullptr;
    void (APIENTRY *signal_semaphore)(GLuint, GLuint, const GLuint *, GLuint, const GLuint *, const GLenum *) = nullptr;
    // READBACK (GL 4.5)
    void *(APIENTRY *map_range)(GLuint, ptrdiff_t, ptrdiff_t, GLbitfield) = nullptr;
    GLboolean (APIENTRY *unmap)(GLuint) = nullptr;
    LoadError load_error = LoadError::none;
    const char *missing_entry_point = nullptr; // process-local diagnostic only
    uint32_t transport = GT_TRANSPORT_NONE;    // the Bridge's transport once loaded
    // Current context: the entry points GL 4.5 DSA and `transport` need
    // (select_transport chose it from this context's capabilities).
    bool load(uint32_t transport);
};

// Saves the renderer state our blits and readbacks touch (scissor enable, the
// pack buffer and the pack pixel-store values), neutralises it, restores it.
class StateGuard {
public:
    explicit StateGuard(const Gl &gl);
    ~StateGuard();
    StateGuard(const StateGuard &) = delete;
    StateGuard &operator=(const StateGuard &) = delete;
private:
    const Gl &gl_;
    GLboolean scissor_ = GL_FALSE;
    GLint pack_buffer_ = 0, row_length_ = 0, skip_rows_ = 0, skip_pixels_ = 0, alignment_ = 4;
};

// The GL adapter's LUID (GL_DEVICE_LUID_EXT), which the helper's D3D device must match.
bool adapter_luid(const Gl &gl, LUID *out);

// The selection sample: every 8th pixel of every 8th row, top row first, BGRA
// with alpha 255, byte-identical to the full-frame selector's own sample
// (test_gpu_selection compares them). One nearest
// blit per picture into a small texture, then an asynchronous readback.
class Sampler {
public:
    static constexpr unsigned max_slots = 8;
    bool prepare(const Gl &gl, unsigned width, unsigned height, unsigned slots, uint64_t byte_budget);
    // Issue the sample of `source` (width x height RGBA8, bottom row first) into
    // readback slot `slot`. Never waits.
    bool issue(unsigned slot, GLuint source);
    enum class Poll { pending, ready, fault };
    // Zero-time: the slot's bytes once its readback completed.
    Poll take(unsigned slot, unsigned char *bgra, size_t capacity);
    bool destroy();
    unsigned columns() const { return columns_; }
    unsigned rows() const { return rows_; }
    uint64_t sample_bytes() const { return bytes_; }
    DWORD error() const { return error_; }
private:
    const Gl *gl_ = nullptr;
    unsigned width_ = 0, height_ = 0, columns_ = 0, rows_ = 0, slots_ = 0;
    uint64_t bytes_ = 0;
    GLuint read_ = 0, draw_ = 0, texture_[max_slots]{}, buffer_[max_slots]{};
    GLsync fence_[max_slots]{};
    DWORD error_ = 0;
    bool fault(DWORD error) { error_ = error; return false; }
};

// The two helper-created textures, written through gl.transport. The picture
// is a flipped (top row first), floor-even crop of the bottom-up snapshot.
//   KEYED_MUTEX: imported, written by one blit between acquiring key 0 and
//     releasing key 1; key 0 coming back is the return.
//   SEMAPHORE: imported, written by one blit after a GPU-side wait for the
//     value the helper last reported, then signalled with the next odd value;
//     the section's `returned` reaching written + 1 is the return.
//   READBACK: copy() starts an asynchronous readback into a pack buffer,
//     delivered() lands it (flipped) in the section once its fence passed,
//     and `returned` reaching `written` is the return.
class Bridge {
public:
    static constexpr unsigned slots = 2;
    bool prepare(const Gl &gl, unsigned source_width, unsigned source_height,
                 const wchar_t (*names)[128]);
    enum class Result { copied, full, fault };
    Result copy(unsigned index, GLuint source);
    // READBACK: the copied picture is in the section (zero-time fence poll,
    // then a CPU copy); every other transport is complete at copy().
    enum class Delivery { ready, pending, fault };
    Delivery delivered(unsigned index);
    enum class KeyReturn { ready, pending, fault };
    KeyReturn key0_returned(unsigned index);
    bool destroy();
    bool prepared() const { return prepared_; }
    // The key return is a GL call (KEYED_MUTEX) rather than a section read.
    bool gl_custody() const { return transport_ == GT_TRANSPORT_KEYED_MUTEX; }
    uint32_t transport() const { return transport_; }
    DWORD error() const { return error_; }
    // prepare() failed because the driver raised a GL error (it refused an
    // import), not because a name or section was missing.
    bool driver_refused() const { return driver_refused_; }
private:
    bool open_section(unsigned index, const wchar_t *texture_name);
    const Gl *gl_ = nullptr;
    uint32_t transport_ = GT_TRANSPORT_NONE;
    unsigned source_width_ = 0, source_height_ = 0, width_ = 0, height_ = 0;
    GLuint memory_[slots]{}, texture_[slots]{}, read_ = 0, draw_ = 0;
    GLuint semaphore_[slots]{}, buffer_[slots]{};
    GLsync fence_[slots]{};
    gt_section *section_[slots]{};
    int64_t written_[slots]{}; // this side's last `written`, mirrored in the section
    DWORD error_ = 0;
    bool prepared_ = false, driver_refused_ = false;
};
} // namespace render_capture
