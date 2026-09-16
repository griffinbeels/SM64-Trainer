/* Worker-only original-image selection sample. Experimental, not a source ABI. */
#pragma once
#include <windows.h>
#include <GL/gl.h>
#include <stdint.h>
#include <stddef.h>

namespace gpu_selection {
constexpr unsigned stride = 8;
enum class Result { sampled, invalid, wrong_worker, fault };

// One prepared sampler belongs to one dedicated GL 4.5+ worker context/thread. The
// producer never calls prepare/sample/shutdown. This object does NOT acquire or
// release source textures: caller holds a ready owned snapshot through sample
// and every other GPU use, then releases through its native custody protocol.
// Client-memory ReadPixels may block this WORKER; no no-contention claim.
class Sampler {
public:
    Sampler() = default;
    Sampler(const Sampler&) = delete;
    Sampler& operator=(const Sampler&) = delete;
    // Fixed original dimensions, never the encoder's floor-even crop.
    // byte_budget bounds this module's logical sample texture bytes. Caller
    // separately owns/reserves the output buffer and the full snapshot.
    bool prepare(unsigned width, unsigned height, uint64_t byte_budget);
    Result sample(GLuint rgba8_snapshot, unsigned width, unsigned height,
                  unsigned char* bgra, size_t capacity);
    bool shutdown(); // explicit, owning worker/context still current
    unsigned columns() const { return columns_; }
    unsigned rows() const { return rows_; }
    uint64_t sample_bytes() const { return bytes_; }
    uint64_t readback_bytes() const { return readback_bytes_; }
    unsigned reads() const { return reads_; }
    DWORD error() const { return error_; }
private:
    HGLRC context_ = nullptr;
    DWORD thread_ = 0, error_ = 0;
    unsigned width_ = 0, height_ = 0, columns_ = 0, rows_ = 0, reads_ = 0;
    uint64_t bytes_ = 0, readback_bytes_ = 0;
    GLuint framebuffer_ = 0, texture_ = 0, vao_ = 0, sampler_ = 0, program_ = 0;
    GLint height_uniform_ = -1;
    bool enabled_ = false, poisoned_ = false;
    void (APIENTRY *bind_framebuffer_)(GLenum, GLuint) = nullptr;
    GLenum (APIENTRY *check_framebuffer_)(GLenum) = nullptr;
    void (APIENTRY *active_texture_)(GLenum) = nullptr;
    void (APIENTRY *bind_sampler_)(GLuint, GLuint) = nullptr;
    void (APIENTRY *bind_buffer_)(GLenum, GLuint) = nullptr;
    void (APIENTRY *bind_vao_)(GLuint) = nullptr;
    void (APIENTRY *uniform_int_)(GLint, GLint) = nullptr;
    void (APIENTRY *use_program_)(GLuint) = nullptr;
    void (APIENTRY *delete_framebuffers_)(GLsizei, const GLuint*) = nullptr;
    void (APIENTRY *delete_vaos_)(GLsizei, const GLuint*) = nullptr;
    void (APIENTRY *delete_samplers_)(GLsizei, const GLuint*) = nullptr;
    void (APIENTRY *delete_program_)(GLuint) = nullptr;
    void (APIENTRY *clip_control_)(GLenum, GLenum) = nullptr;
    bool worker() const;
    bool prepare_program();
    Result fault(DWORD);
};
} // namespace gpu_selection
