#include "gpu_render.h"
#include <string.h>

namespace render_capture {
namespace {
template<class T> bool load_proc(T &target, const char *name) {
    const PROC p = wglGetProcAddress(name);
    const auto address = reinterpret_cast<uintptr_t>(p);
    if (!p || address <= 3 || address == UINTPTR_MAX) return false;
    target = reinterpret_cast<T>(p);
    return true;
}
bool has_extension(const char *name) {
    using StringAt = const GLubyte *(APIENTRY *)(GLenum, GLuint);
    StringAt string_at = nullptr;
    if (!load_proc(string_at, "glGetStringi")) return false;
    GLint count = 0;
    glGetIntegerv(0x821D, &count); // GL_NUM_EXTENSIONS
    for (GLint i = 0; i < count && i < 4096; ++i) {
        const auto *e = reinterpret_cast<const char *>(string_at(GL_EXTENSIONS, GLuint(i)));
        if (e && !strcmp(e, name)) return true;
    }
    return false;
}
constexpr GLenum read_target = 0x8CA8, draw_target = 0x8CA9, color0 = 0x8CE0, complete = 0x8CD5;
constexpr GLenum rgba8 = 0x8058, pack_buffer = 0x88EB, bgra = 0x80E1;
constexpr GLenum pack_buffer_binding = 0x88ED, sync_complete = 0x9117;
constexpr GLenum already_signaled = 0x911A, condition_satisfied = 0x911C, wait_failed = 0x911D;
constexpr GLenum d3d11_image = 0x958B, dedicated_memory = 0x9581, device_luid = 0x9599;
constexpr GLbitfield map_read = 0x0001, client_storage = 0x0200;
}

bool Gl::load() {
    *this = {};
    GLint major = 0, minor = 0;
    glGetIntegerv(0x821B, &major); glGetIntegerv(0x821C, &minor);
    if (major < 4 || (major == 4 && minor < 5)) return false;
    if (!has_extension("GL_EXT_memory_object") || !has_extension("GL_EXT_memory_object_win32")
        || !has_extension("GL_EXT_win32_keyed_mutex")) return false;
    return load_proc(create_framebuffers, "glCreateFramebuffers")
        && load_proc(delete_framebuffers, "glDeleteFramebuffers")
        && load_proc(framebuffer_texture, "glNamedFramebufferTexture")
        && load_proc(framebuffer_read_buffer, "glNamedFramebufferReadBuffer")
        && load_proc(framebuffer_draw_buffer, "glNamedFramebufferDrawBuffer")
        && load_proc(check_framebuffer, "glCheckNamedFramebufferStatus")
        && load_proc(blit, "glBlitNamedFramebuffer")
        && load_proc(create_textures, "glCreateTextures")
        && load_proc(texture_storage, "glTextureStorage2D")
        && load_proc(create_buffers, "glCreateBuffers")
        && load_proc(buffer_storage, "glNamedBufferStorage")
        && load_proc(delete_buffers, "glDeleteBuffers")
        && load_proc(get_buffer, "glGetNamedBufferSubData")
        && load_proc(bind_buffer, "glBindBuffer")
        && load_proc(get_texture_image, "glGetTextureSubImage")
        && load_proc(fence, "glFenceSync")
        && load_proc(client_wait, "glClientWaitSync")
        && load_proc(delete_sync, "glDeleteSync")
        && load_proc(create_memory, "glCreateMemoryObjectsEXT")
        && load_proc(delete_memory, "glDeleteMemoryObjectsEXT")
        && load_proc(memory_parameter, "glMemoryObjectParameterivEXT")
        && load_proc(import_name, "glImportMemoryWin32NameEXT")
        && load_proc(texture_storage_memory, "glTextureStorageMem2DEXT")
        && load_proc(acquire_key, "glAcquireKeyedMutexWin32EXT")
        && load_proc(release_key, "glReleaseKeyedMutexWin32EXT")
        && load_proc(get_unsigned_bytes, "glGetUnsignedBytevEXT");
}

StateGuard::StateGuard(const Gl &gl) : gl_(gl) {
    // Each query waits for the driver's queue; the capture gateway already did.
    scissor_ = glIsEnabled(GL_SCISSOR_TEST);
    glGetIntegerv(pack_buffer_binding, &pack_buffer_);
    glGetIntegerv(GL_PACK_ROW_LENGTH, &row_length_);
    glGetIntegerv(GL_PACK_SKIP_ROWS, &skip_rows_);
    glGetIntegerv(GL_PACK_SKIP_PIXELS, &skip_pixels_);
    glGetIntegerv(GL_PACK_ALIGNMENT, &alignment_);
    if (scissor_) glDisable(GL_SCISSOR_TEST);
    if (pack_buffer_) gl_.bind_buffer(pack_buffer, 0);
    if (row_length_) glPixelStorei(GL_PACK_ROW_LENGTH, 0);
    if (skip_rows_) glPixelStorei(GL_PACK_SKIP_ROWS, 0);
    if (skip_pixels_) glPixelStorei(GL_PACK_SKIP_PIXELS, 0);
    if (alignment_ != 4) glPixelStorei(GL_PACK_ALIGNMENT, 4);
}
StateGuard::~StateGuard() {
    if (alignment_ != 4) glPixelStorei(GL_PACK_ALIGNMENT, alignment_);
    if (skip_pixels_) glPixelStorei(GL_PACK_SKIP_PIXELS, skip_pixels_);
    if (skip_rows_) glPixelStorei(GL_PACK_SKIP_ROWS, skip_rows_);
    if (row_length_) glPixelStorei(GL_PACK_ROW_LENGTH, row_length_);
    gl_.bind_buffer(pack_buffer, GLuint(pack_buffer_));
    if (scissor_) glEnable(GL_SCISSOR_TEST);
}

bool adapter_luid(const Gl &gl, LUID *out) {
    if (!out || !gl.get_unsigned_bytes) return false;
    *out = {};
    gl.get_unsigned_bytes(device_luid, reinterpret_cast<GLubyte *>(out));
    return glGetError() == GL_NO_ERROR && (out->LowPart || out->HighPart);
}

bool Sampler::prepare(const Gl &gl, unsigned width, unsigned height, unsigned slots, uint64_t budget) {
    if (gl_ || !width || !height || width > 8192 || height > 8192 || !slots || slots > max_slots)
        return fault(ERROR_INVALID_PARAMETER);
    columns_ = (width + 7) / 8; rows_ = (height + 7) / 8;
    bytes_ = uint64_t(columns_) * rows_ * 4;
    if (bytes_ > budget) { bytes_ = 0; return fault(ERROR_NOT_ENOUGH_MEMORY); }
    gl_ = &gl; width_ = width; height_ = height; slots_ = slots;
    gl.create_framebuffers(1, &read_); gl.create_framebuffers(1, &draw_);
    gl.framebuffer_read_buffer(read_, color0); gl.framebuffer_draw_buffer(draw_, color0);
    gl.create_textures(GL_TEXTURE_2D, GLsizei(slots), texture_);
    gl.create_buffers(GLsizei(slots), buffer_);
    for (unsigned i = 0; i < slots; ++i) {
        gl.texture_storage(texture_[i], 1, rgba8, GLsizei(columns_), GLsizei(rows_));
        gl.buffer_storage(buffer_[i], ptrdiff_t(bytes_), nullptr, map_read | client_storage);
    }
    const GLenum error = glGetError();
    if (error != GL_NO_ERROR) { destroy(); return fault(error); }
    return true;
}
bool Sampler::issue(unsigned slot, GLuint source) {
    if (!gl_ || slot >= slots_ || !source) return fault(ERROR_INVALID_PARAMETER);
    const auto &gl = *gl_;
    if (fence_[slot]) { gl.delete_sync(fence_[slot]); fence_[slot] = nullptr; }
    gl.framebuffer_texture(read_, color0, source, 0);
    gl.framebuffer_texture(draw_, color0, texture_[slot], 0);
    // Nearest blit, 8 source pixels per sample less 1/cells, source rectangle
    // shifted so every sample point lies strictly inside pixel (8x, H-1-8y):
    // x = 8x + 1 - (x+0.5)/columns, y = H-1-8y + (y+0.5)/rows. Rows run top first.
    const GLint src_x0 = -3, src_x1 = GLint(8 * columns_) - 4;
    const GLint src_y0 = GLint(height_) + 3, src_y1 = GLint(height_) + 4 - GLint(8 * rows_);
    gl.blit(read_, draw_, src_x0, src_y0, src_x1, src_y1, 0, 0, GLint(columns_), GLint(rows_),
            GL_COLOR_BUFFER_BIT, GL_NEAREST);
    gl.bind_buffer(pack_buffer, buffer_[slot]);
    gl.get_texture_image(texture_[slot], 0, 0, 0, 0, GLsizei(columns_), GLsizei(rows_), 1,
                         bgra, GL_UNSIGNED_BYTE, GLsizei(bytes_), nullptr);
    gl.bind_buffer(pack_buffer, 0);
    fence_[slot] = gl.fence(sync_complete, 0);
    return fence_[slot] != nullptr || fault(ERROR_INVALID_DATA);
}
Sampler::Poll Sampler::take(unsigned slot, unsigned char *bytes, size_t capacity) {
    if (!gl_ || slot >= slots_ || !fence_[slot] || !bytes || capacity < bytes_) { fault(ERROR_INVALID_PARAMETER); return Poll::fault; }
    const auto &gl = *gl_;
    const GLenum state = gl.client_wait(fence_[slot], 0, 0);
    if (state == wait_failed) { fault(ERROR_INVALID_DATA); return Poll::fault; }
    if (state != already_signaled && state != condition_satisfied) return Poll::pending;
    gl.delete_sync(fence_[slot]); fence_[slot] = nullptr;
    gl.get_buffer(buffer_[slot], 0, ptrdiff_t(bytes_), bytes);
    // The worker shader wrote alpha 1.0; a copied RGBA8 snapshot carries the
    // source's alpha, which a clean-VI source need not set.
    for (uint64_t i = 3; i < bytes_; i += 4) bytes[i] = 255;
    return Poll::ready;
}
bool Sampler::destroy() {
    if (!gl_) return true;
    const auto &gl = *gl_;
    for (unsigned i = 0; i < slots_; ++i) if (fence_[i]) { gl.delete_sync(fence_[i]); fence_[i] = nullptr; }
    if (read_) gl.delete_framebuffers(1, &read_);
    if (draw_) gl.delete_framebuffers(1, &draw_);
    glDeleteTextures(GLsizei(slots_), texture_);
    gl.delete_buffers(GLsizei(slots_), buffer_);
    read_ = draw_ = 0; memset(texture_, 0, sizeof texture_); memset(buffer_, 0, sizeof buffer_);
    gl_ = nullptr; slots_ = 0; bytes_ = 0;
    return glGetError() == GL_NO_ERROR;
}

bool Bridge::prepare(const Gl &gl, unsigned source_width, unsigned source_height, const wchar_t (*names)[128]) {
    if (prepared_ || !names || source_width < 2 || source_height < 2) { error_ = ERROR_INVALID_PARAMETER; return false; }
    gl_ = &gl; source_width_ = source_width; source_height_ = source_height;
    width_ = source_width & ~1u; height_ = source_height & ~1u;
    gl.create_memory(slots, memory_);
    gl.create_textures(GL_TEXTURE_2D, slots, texture_);
    for (unsigned i = 0; i < slots; ++i) {
        const GLint dedicated = GL_TRUE;
        gl.memory_parameter(memory_[i], dedicated_memory, &dedicated);
        gl.import_name(memory_[i], 0, d3d11_image, names[i]);
        gl.texture_storage_memory(texture_[i], 1, rgba8, GLsizei(width_), GLsizei(height_), memory_[i], 0);
    }
    gl.create_framebuffers(1, &read_); gl.create_framebuffers(1, &draw_);
    gl.framebuffer_read_buffer(read_, color0); gl.framebuffer_draw_buffer(draw_, color0);
    const GLenum error = glGetError();
    if (error != GL_NO_ERROR) { error_ = error; destroy(); return false; }
    prepared_ = true;
    return true;
}
Bridge::Result Bridge::copy(unsigned index, GLuint source) {
    if (!prepared_ || index >= slots || !source) { error_ = ERROR_INVALID_PARAMETER; return Result::fault; }
    const auto &gl = *gl_;
    // Custody is the delivery's credit ledger: it copies only into a texture
    // whose key 0 came back (key0_returned). A driver that reports a timed-out
    // acquire as success (NVIDIA does) then surfaces a held key as the
    // release's INVALID_OPERATION below, a fault rather than a silent overwrite.
    if (!gl.acquire_key(memory_[index], 0, 0)) return Result::full;
    gl.framebuffer_texture(read_, color0, source, 0);
    gl.framebuffer_texture(draw_, color0, texture_[index], 0);
    // Odd height drops native row 0, odd width the rightmost column; the
    // destination is written top row first (the encoder's orientation).
    const GLint y0 = GLint(source_height_ & 1u);
    gl.blit(read_, draw_, 0, y0, GLint(width_), y0 + GLint(height_), 0, GLint(height_), GLint(width_), 0,
            GL_COLOR_BUFFER_BIT, GL_NEAREST);
    // The release is judged by GL errors: the call returns FALSE on NVIDIA even
    // for a release the consumer then acquires as key 1 (test_gpu_delivery's
    // consumer takes key 1 and verifies the pixels).
    gl.release_key(memory_[index], 1);
    const GLenum error = glGetError();
    if (error != GL_NO_ERROR) { error_ = error; return Result::fault; }
    return Result::copied;
}
Bridge::KeyReturn Bridge::key0_returned(unsigned index) {
    if (!prepared_ || index >= slots) { error_ = ERROR_INVALID_PARAMETER; return KeyReturn::fault; }
    const auto &gl = *gl_;
    if (!gl.acquire_key(memory_[index], 0, 0)) return KeyReturn::pending;
    // NVIDIA returns TRUE from a zero-timeout acquire that timed out (the
    // consumer still holds key 1, or has not taken it yet); the release then
    // reports the key was never held. Measured: 11-30 us per poll, and the
    // first poll after the consumer's release to 0 succeeds without error.
    gl.release_key(memory_[index], 0);
    const GLenum error = glGetError();
    if (error == GL_INVALID_OPERATION) return KeyReturn::pending;
    if (error != GL_NO_ERROR) { error_ = error; return KeyReturn::fault; }
    return KeyReturn::ready;
}
bool Bridge::destroy() {
    if (!gl_) return true;
    const auto &gl = *gl_;
    if (read_) gl.delete_framebuffers(1, &read_);
    if (draw_) gl.delete_framebuffers(1, &draw_);
    glDeleteTextures(slots, texture_);
    gl.delete_memory(slots, memory_);
    read_ = draw_ = 0; memset(texture_, 0, sizeof texture_); memset(memory_, 0, sizeof memory_);
    prepared_ = false; gl_ = nullptr;
    return glGetError() == GL_NO_ERROR;
}
} // namespace render_capture
