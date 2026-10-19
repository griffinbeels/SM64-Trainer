/* The capture's GPU work, done on the renderer's own thread in the renderer's
 * own context (docs/replay-gpu-runtime.md, "Renderer-thread capture"). A second
 * thread using the GPU in Project64 makes the NVIDIA driver freeze the renderer
 * once (threaded optimization), so nothing here ever runs anywhere else, and no
 * D3D device exists in this process: the encoder helper creates the two
 * keyed-mutex textures and this side imports them by name.
 *
 * Every call belongs to the renderer thread with the renderer's context
 * current, inside its capture gateway. Nothing waits: fences are polled with a
 * zero timeout and keyed mutexes are tried with a zero timeout. Only the state
 * a blit or readback touches is changed, and StateGuard restores it. */
#pragma once
#include <windows.h>
#include <GL/gl.h>
#include <stdint.h>
#include <stddef.h>

namespace render_capture {
using GLsync = struct __GLsync *;
using GLuint64 = unsigned long long;
// gd_status.error when reason is GD_GL_UNSUPPORTED. runtime_delivery projects
// known details into append-only ControlV1 reasons before a picture channel.
enum class LoadError : uint32_t {
    none = 0, version_45 = 1, memory_object = 2, memory_object_win32 = 3,
    keyed_mutex = 4, entry_point = 5, snapshot_entry_point = 6,
};
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
    LoadError load_error = LoadError::none;
    const char *missing_entry_point = nullptr; // process-local diagnostic only
    // Current context; false when it lacks GL 4.5 DSA or the external-memory,
    // keyed-mutex extensions the helper handoff needs.
    bool load();
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

// The two helper-created keyed-mutex textures, imported by name. The copy is a
// flipped (top row first), floor-even crop of the bottom-up snapshot.
class Bridge {
public:
    static constexpr unsigned slots = 2;
    bool prepare(const Gl &gl, unsigned source_width, unsigned source_height,
                 const wchar_t (*names)[128]);
    enum class Result { copied, full, fault };
    Result copy(unsigned index, GLuint source);
    enum class KeyReturn { ready, pending, fault };
    KeyReturn key0_returned(unsigned index);
    bool destroy();
    bool prepared() const { return prepared_; }
    DWORD error() const { return error_; }
private:
    const Gl *gl_ = nullptr;
    unsigned source_width_ = 0, source_height_ = 0, width_ = 0, height_ = 0;
    GLuint memory_[slots]{}, texture_[slots]{}, read_ = 0, draw_ = 0;
    DWORD error_ = 0;
    bool prepared_ = false;
};
} // namespace render_capture
