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
constexpr GLenum read_target = 0x8CA8, draw_target = 0x8CA9, color0 = 0x8CE0, complete = 0x8CD5;
constexpr GLenum rgba8 = 0x8058, pack_buffer = 0x88EB, bgra = 0x80E1;
constexpr GLenum pack_buffer_binding = 0x88ED, sync_complete = 0x9117;
constexpr GLenum already_signaled = 0x911A, condition_satisfied = 0x911C, wait_failed = 0x911D;
constexpr GLenum d3d11_image = 0x958B, dedicated_memory = 0x9581, device_luid = 0x9599;
constexpr GLenum d3d12_fence = 0x9594, fence_value = 0x9595, layout_general = 0x958D;
constexpr GLbitfield map_read = 0x0001, client_storage = 0x0200;
}

uint32_t probe_capabilities() {
    uint32_t bits = GT_CAP_PROBED;
    GLint major = 0, minor = 0;
    glGetIntegerv(0x821B, &major); glGetIntegerv(0x821C, &minor);
    if (major > 4 || (major == 4 && minor >= 5)) bits |= GT_CAP_GL45;
    using StringAt = const GLubyte *(APIENTRY *)(GLenum, GLuint);
    StringAt string_at = nullptr;
    if (load_proc(string_at, "glGetStringi")) {
        static const struct { const char *name; uint32_t bit; } known[] = {
            {"GL_EXT_memory_object", GT_CAP_MEMORY_OBJECT}, {"GL_EXT_memory_object_win32", GT_CAP_MEMORY_OBJECT_WIN32},
            {"GL_EXT_win32_keyed_mutex", GT_CAP_KEYED_MUTEX}, {"GL_EXT_semaphore", GT_CAP_SEMAPHORE},
            {"GL_EXT_semaphore_win32", GT_CAP_SEMAPHORE_WIN32},
        };
        GLint count = 0;
        glGetIntegerv(0x821D, &count); // GL_NUM_EXTENSIONS
        for (GLint i = 0; i < count && i < 4096; ++i) {
            const auto *e = reinterpret_cast<const char *>(string_at(GL_EXTENSIONS, GLuint(i)));
            if (e) for (const auto &k : known) if (!strcmp(e, k.name)) bits |= k.bit;
        }
    }
    // A WGL extension, so not in the GL list. Recorded for diagnosis only.
    using WglExtensions = const char *(WINAPI *)(HDC);
    WglExtensions wgl = nullptr;
    const HDC dc = wglGetCurrentDC();
    if (dc && load_proc(wgl, "wglGetExtensionsStringARB")) {
        const char *list = wgl(dc);
        constexpr char wanted[] = "WGL_NV_DX_interop2";
        for (const char *at = list; at && (at = strstr(at, wanted)) != nullptr; at += sizeof wanted - 1) {
            const char after = at[sizeof wanted - 1];
            if ((at == list || at[-1] == ' ') && (after == ' ' || after == 0)) { bits |= GT_CAP_WGL_DX_INTEROP2; break; }
        }
    }
    return bits;
}
uint32_t select_transport(uint32_t caps, uint32_t forced, uint32_t floor) {
    // The adapter LUID the helper's device must match comes from
    // GL_EXT_memory_object_win32's DEVICE_LUID_EXT, so every transport needs it.
    const uint32_t base = GT_CAP_PROBED | GT_CAP_GL45 | GT_CAP_MEMORY_OBJECT | GT_CAP_MEMORY_OBJECT_WIN32;
    if ((caps & base) != base) return GT_TRANSPORT_NONE;
    const bool offered[GT_TRANSPORT_COUNT] = {false, (caps & GT_CAP_KEYED_MUTEX) != 0,
        (caps & (GT_CAP_SEMAPHORE | GT_CAP_SEMAPHORE_WIN32)) == (GT_CAP_SEMAPHORE | GT_CAP_SEMAPHORE_WIN32), true};
    // A forced transport is the test hosts' starting point; demotion past it
    // then runs exactly as it would from the keyed mutex.
    uint32_t lowest = floor > GT_TRANSPORT_KEYED_MUTEX ? floor : GT_TRANSPORT_KEYED_MUTEX;
    if (forced > lowest) lowest = forced;
    for (uint32_t t = lowest; t < GT_TRANSPORT_COUNT; ++t)
        if (offered[t]) return t;
    return GT_TRANSPORT_NONE;
}
LoadError unavailable(uint32_t caps) {
    if (!(caps & GT_CAP_GL45)) return LoadError::version_45;
    if (!(caps & GT_CAP_MEMORY_OBJECT)) return LoadError::memory_object;
    if (!(caps & GT_CAP_MEMORY_OBJECT_WIN32)) return LoadError::memory_object_win32;
    return LoadError::no_transport;
}

bool Gl::load(uint32_t selected) {
    *this = {};
    GLint major = 0, minor = 0;
    glGetIntegerv(0x821B, &major); glGetIntegerv(0x821C, &minor);
    if (major < 4 || (major == 4 && minor < 5)) { load_error = LoadError::version_45; return false; }
    if (selected == GT_TRANSPORT_NONE || selected >= GT_TRANSPORT_COUNT) { load_error = LoadError::no_transport; return false; }
    const auto require = [this](auto &target, const char *name) {
        if (load_proc(target, name)) return true;
        load_error = LoadError::entry_point; missing_entry_point = name; return false;
    };
    const bool common = require(create_framebuffers, "glCreateFramebuffers")
        && require(delete_framebuffers, "glDeleteFramebuffers")
        && require(framebuffer_texture, "glNamedFramebufferTexture")
        && require(framebuffer_read_buffer, "glNamedFramebufferReadBuffer")
        && require(framebuffer_draw_buffer, "glNamedFramebufferDrawBuffer")
        && require(check_framebuffer, "glCheckNamedFramebufferStatus")
        && require(blit, "glBlitNamedFramebuffer")
        && require(create_textures, "glCreateTextures")
        && require(texture_storage, "glTextureStorage2D")
        && require(create_buffers, "glCreateBuffers")
        && require(buffer_storage, "glNamedBufferStorage")
        && require(delete_buffers, "glDeleteBuffers")
        && require(get_buffer, "glGetNamedBufferSubData")
        && require(bind_buffer, "glBindBuffer")
        && require(get_texture_image, "glGetTextureSubImage")
        && require(fence, "glFenceSync")
        && require(client_wait, "glClientWaitSync")
        && require(delete_sync, "glDeleteSync")
        && require(get_unsigned_bytes, "glGetUnsignedBytevEXT");
    if (!common) return false;
    bool loaded = true;
    if (selected == GT_TRANSPORT_KEYED_MUTEX || selected == GT_TRANSPORT_SEMAPHORE)
        loaded = require(create_memory, "glCreateMemoryObjectsEXT")
            && require(delete_memory, "glDeleteMemoryObjectsEXT")
            && require(memory_parameter, "glMemoryObjectParameterivEXT")
            && require(import_name, "glImportMemoryWin32NameEXT")
            && require(texture_storage_memory, "glTextureStorageMem2DEXT");
    if (loaded && selected == GT_TRANSPORT_KEYED_MUTEX)
        loaded = require(acquire_key, "glAcquireKeyedMutexWin32EXT")
            && require(release_key, "glReleaseKeyedMutexWin32EXT");
    if (loaded && selected == GT_TRANSPORT_SEMAPHORE)
        loaded = require(gen_semaphores, "glGenSemaphoresEXT")
            && require(delete_semaphores, "glDeleteSemaphoresEXT")
            && require(semaphore_parameter, "glSemaphoreParameterui64vEXT")
            && require(import_semaphore_name, "glImportSemaphoreWin32NameEXT")
            && require(wait_semaphore, "glWaitSemaphoreEXT")
            && require(signal_semaphore, "glSignalSemaphoreEXT");
    if (loaded && selected == GT_TRANSPORT_READBACK)
        loaded = require(map_range, "glMapNamedBufferRange") && require(unmap, "glUnmapNamedBuffer");
    if (loaded) transport = selected;
    return loaded;
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
#ifdef RENDER_SAMPLE_CROP_FIRST // witness negative control: samples the even crop
    columns_ = ((width & ~1u) + 7) / 8; rows_ = ((height & ~1u) + 7) / 8;
#else
    columns_ = (width + 7) / 8; rows_ = (height + 7) / 8;
#endif
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
#ifdef RENDER_SAMPLE_WRONG_Y // witness negative control: rows bottom first
    const GLint src_y0 = -3, src_y1 = GLint(8 * rows_) - 4;
#else
    const GLint src_y0 = GLint(height_) + 3, src_y1 = GLint(height_) + 4 - GLint(8 * rows_);
#endif
    gl.blit(read_, draw_, src_x0, src_y0, src_x1, src_y1, 0, 0, GLint(columns_), GLint(rows_),
            GL_COLOR_BUFFER_BIT, GL_NEAREST);
    gl.bind_buffer(pack_buffer, buffer_[slot]);
#ifdef RENDER_SAMPLE_RGBA_BYTES // witness negative control: red and blue swapped
    const GLenum order = GL_RGBA;
#else
    const GLenum order = bgra;
#endif
    gl.get_texture_image(texture_[slot], 0, 0, 0, 0, GLsizei(columns_), GLsizei(rows_), 1,
                         order, GL_UNSIGNED_BYTE, GLsizei(bytes_), nullptr);
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
    // The selector compares opaque samples; a copied RGBA8 snapshot carries
    // the source's alpha, which a clean-VI source need not set.
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

bool Bridge::open_section(unsigned index, const wchar_t *texture_name) {
    wchar_t name[GT_NAME_UNITS];
    if (!gt_derived_name(name, texture_name, L"-section")) { error_ = ERROR_INVALID_PARAMETER; return false; }
    // CPU only: the helper created the section with its textures.
    const HANDLE mapping = OpenFileMappingW(FILE_MAP_READ | FILE_MAP_WRITE, FALSE, name);
    if (!mapping) { error_ = GetLastError(); return false; }
    const uint64_t bytes = gt_section_bytes(transport_, width_, height_);
    void *view = MapViewOfFile(mapping, FILE_MAP_READ | FILE_MAP_WRITE, 0, 0, SIZE_T(bytes));
    const DWORD mapped_error = view ? 0 : GetLastError();
    CloseHandle(mapping); // the view keeps the section alive
    if (!view) { error_ = mapped_error; return false; }
    MEMORY_BASIC_INFORMATION region{};
    auto *s = static_cast<gt_section *>(view);
    if (VirtualQuery(view, &region, sizeof region) != sizeof region || region.RegionSize < bytes
        || s->magic != GT_SECTION_MAGIC || s->version != GT_SECTION_VERSION || s->transport != transport_
        || s->width != width_ || s->height != height_ || s->pitch != width_ * 4
        || s->header_bytes != GT_SECTION_HEADER_BYTES || (s->format != 1 && s->format != 2)
        || InterlockedCompareExchange64(&s->written, 0, 0) || InterlockedCompareExchange64(&s->returned, 0, 0)) {
        UnmapViewOfFile(view); error_ = ERROR_INVALID_DATA; return false;
    }
    section_[index] = static_cast<gt_section *>(view);
    return true;
}
bool Bridge::prepare(const Gl &gl, unsigned source_width, unsigned source_height, const wchar_t (*names)[128]) {
    if (prepared_ || !names || source_width < 2 || source_height < 2
        || gl.transport == GT_TRANSPORT_NONE || gl.transport >= GT_TRANSPORT_COUNT) { error_ = ERROR_INVALID_PARAMETER; return false; }
    gl_ = &gl; transport_ = gl.transport; driver_refused_ = false;
    source_width_ = source_width; source_height_ = source_height;
    width_ = source_width & ~1u; height_ = source_height & ~1u;
    if (transport_ != GT_TRANSPORT_KEYED_MUTEX)
        for (unsigned i = 0; i < slots; ++i) if (!open_section(i, names[i])) { destroy(); return false; }
    if (transport_ == GT_TRANSPORT_READBACK) {
        gl.create_buffers(slots, buffer_);
        for (unsigned i = 0; i < slots; ++i)
            gl.buffer_storage(buffer_[i], ptrdiff_t(uint64_t(width_) * height_ * 4), nullptr, map_read | client_storage);
    } else {
        gl.create_memory(slots, memory_);
        gl.create_textures(GL_TEXTURE_2D, slots, texture_);
        for (unsigned i = 0; i < slots; ++i) {
            const GLint dedicated = GL_TRUE;
            gl.memory_parameter(memory_[i], dedicated_memory, &dedicated);
            gl.import_name(memory_[i], 0, d3d11_image, names[i]);
            gl.texture_storage_memory(texture_[i], 1, rgba8, GLsizei(width_), GLsizei(height_), memory_[i], 0);
        }
        if (transport_ == GT_TRANSPORT_SEMAPHORE) {
            gl.gen_semaphores(slots, semaphore_);
            for (unsigned i = 0; i < slots; ++i) {
                wchar_t fence_name[GT_NAME_UNITS];
                if (!gt_derived_name(fence_name, names[i], L"-fence")) { error_ = ERROR_INVALID_PARAMETER; destroy(); return false; }
                gl.import_semaphore_name(semaphore_[i], d3d12_fence, fence_name);
            }
        }
        gl.create_framebuffers(1, &read_); gl.create_framebuffers(1, &draw_);
        gl.framebuffer_read_buffer(read_, color0); gl.framebuffer_draw_buffer(draw_, color0);
    }
    const GLenum error = glGetError();
    if (error != GL_NO_ERROR) { error_ = error; destroy(); driver_refused_ = true; return false; }
    prepared_ = true;
    return true;
}
Bridge::Result Bridge::copy(unsigned index, GLuint source) {
    if (!prepared_ || index >= slots || !source) { error_ = ERROR_INVALID_PARAMETER; return Result::fault; }
    const auto &gl = *gl_;
    // Odd height drops native row 0, odd width the rightmost column; the
    // destination is written top row first (the encoder's orientation).
#ifdef RENDER_FIDELITY_WRONG_ODD_ROW // witness negative control: keeps native row 0
    const GLint y0 = 0;
#else
    const GLint y0 = GLint(source_height_ & 1u);
#endif
    if (transport_ == GT_TRANSPORT_READBACK) {
        // Custody: the helper uploaded the last picture (returned == written).
        if (fence_[index] || InterlockedCompareExchange64(&section_[index]->returned, 0, 0) != written_[index])
            return Result::full;
        gl.bind_buffer(pack_buffer, buffer_[index]);
        // Bottom-up rows into the pack buffer; delivered() flips them.
        gl.get_texture_image(source, 0, 0, y0, 0, GLsizei(width_), GLsizei(height_), 1,
                             section_[index]->format == 2 ? bgra : GL_RGBA, GL_UNSIGNED_BYTE,
                             GLsizei(uint64_t(width_) * height_ * 4), nullptr);
        gl.bind_buffer(pack_buffer, 0);
        fence_[index] = gl.fence(sync_complete, 0);
        const GLenum error = glGetError();
        if (error != GL_NO_ERROR || !fence_[index]) { error_ = error ? error : ERROR_INVALID_DATA; return Result::fault; }
        return Result::copied;
    }
    if (transport_ == GT_TRANSPORT_SEMAPHORE) {
        // Custody: the helper signalled written + 1 after reading the last
        // picture (zero before the first). The wait below is GPU-side ordering
        // for a value it already reported, never a CPU wait.
        const int64_t returned = InterlockedCompareExchange64(&section_[index]->returned, 0, 0);
        if (returned != (written_[index] ? written_[index] + 1 : 0)) return Result::full;
        const GLenum layout = layout_general;
        if (written_[index]) {
            const GLuint64 value = GLuint64(returned);
            gl.semaphore_parameter(semaphore_[index], fence_value, &value);
            gl.wait_semaphore(semaphore_[index], 0, nullptr, 1, &texture_[index], &layout);
        }
    } else {
        // Custody is the delivery's credit ledger: it copies only into a texture
        // whose key 0 came back (key0_returned). A driver that reports a timed-out
        // acquire as success (NVIDIA does) then surfaces a held key as the
        // release's INVALID_OPERATION below, a fault rather than a silent overwrite.
        if (!gl.acquire_key(memory_[index], 0, 0)) return Result::full;
    }
    gl.framebuffer_texture(read_, color0, source, 0);
    gl.framebuffer_texture(draw_, color0, texture_[index], 0);
#ifdef RENDER_FIDELITY_OMIT_FLIP // witness negative control: bottom row first
    const GLint top = 0, bottom = GLint(height_);
#else
    const GLint top = GLint(height_), bottom = 0;
#endif
#ifndef RENDER_BRIDGE_OMIT_COPY // witness negative control: the key moves, the pixels do not
    gl.blit(read_, draw_, 0, y0, GLint(width_), y0 + GLint(height_), 0, top, GLint(width_), bottom,
            GL_COLOR_BUFFER_BIT, GL_NEAREST);
#endif
    if (transport_ == GT_TRANSPORT_SEMAPHORE) {
        // The next odd value; the signal flushes the GL command stream.
        const int64_t next = written_[index] ? written_[index] + 2 : 1;
        const GLuint64 value = GLuint64(next);
        const GLenum layout = layout_general;
        gl.semaphore_parameter(semaphore_[index], fence_value, &value);
        gl.signal_semaphore(semaphore_[index], 0, nullptr, 1, &texture_[index], &layout);
        const GLenum error = glGetError();
        if (error != GL_NO_ERROR) { error_ = error; return Result::fault; }
        written_[index] = next;
        InterlockedExchange64(&section_[index]->written, next);
        return Result::copied;
    }
    // The release is judged by GL errors: the call returns FALSE on NVIDIA even
    // for a release the consumer then acquires as key 1 (test_gpu_delivery's
    // consumer takes key 1 and verifies the pixels).
    gl.release_key(memory_[index], 1);
    const GLenum error = glGetError();
    if (error != GL_NO_ERROR) { error_ = error; return Result::fault; }
    return Result::copied;
}
Bridge::Delivery Bridge::delivered(unsigned index) {
    if (!prepared_ || index >= slots) { error_ = ERROR_INVALID_PARAMETER; return Delivery::fault; }
    if (transport_ != GT_TRANSPORT_READBACK || !fence_[index]) return Delivery::ready;
    const auto &gl = *gl_;
    const GLenum state = gl.client_wait(fence_[index], 0, 0);
    if (state == wait_failed) { error_ = ERROR_INVALID_DATA; return Delivery::fault; }
    if (state != already_signaled && state != condition_satisfied) return Delivery::pending;
    gl.delete_sync(fence_[index]); fence_[index] = nullptr;
    const size_t pitch = size_t(width_) * 4;
    const auto *rows = static_cast<const unsigned char *>(gl.map_range(buffer_[index], 0, ptrdiff_t(pitch * height_), map_read));
    if (!rows) { error_ = glGetError(); if (!error_) error_ = ERROR_INVALID_DATA; return Delivery::fault; }
    auto *pixels = reinterpret_cast<unsigned char *>(section_[index]) + GT_SECTION_HEADER_BYTES;
#ifndef RENDER_BRIDGE_OMIT_COPY // witness negative control: custody moves, the pixels do not
    for (unsigned y = 0; y < height_; ++y)
#ifdef RENDER_FIDELITY_OMIT_FLIP // witness negative control: bottom row first
        memcpy(pixels + y * pitch, rows + y * pitch, pitch);
#else
        memcpy(pixels + y * pitch, rows + (height_ - 1 - y) * pitch, pitch);
#endif
#else
    (void)pixels;
#endif
    const bool unmapped = gl.unmap(buffer_[index]) == GL_TRUE;
    const GLenum error = glGetError();
    if (!unmapped || error != GL_NO_ERROR) { error_ = error ? error : ERROR_INVALID_DATA; return Delivery::fault; }
    // The pixels are in the section before `written` says so.
    written_[index] += 1;
    InterlockedExchange64(&section_[index]->written, written_[index]);
    return Delivery::ready;
}
Bridge::KeyReturn Bridge::key0_returned(unsigned index) {
    if (!prepared_ || index >= slots) { error_ = ERROR_INVALID_PARAMETER; return KeyReturn::fault; }
    if (transport_ != GT_TRANSPORT_KEYED_MUTEX) {
        // CPU only: the helper's custody word in the section.
        const int64_t returned = InterlockedCompareExchange64(&section_[index]->returned, 0, 0);
        // Nothing written yet is a texture never handed over.
        const int64_t expected = transport_ == GT_TRANSPORT_SEMAPHORE && written_[index] ? written_[index] + 1 : written_[index];
        if (returned == expected) return KeyReturn::ready;
        if (returned > expected || returned < 0) { error_ = ERROR_INVALID_DATA; return KeyReturn::fault; }
        return KeyReturn::pending;
    }
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
    for (auto &section : section_) if (section) { UnmapViewOfFile(section); section = nullptr; }
    memset(written_, 0, sizeof written_);
    if (!gl_) { transport_ = GT_TRANSPORT_NONE; prepared_ = false; return true; }
    const auto &gl = *gl_;
    for (auto &f : fence_) if (f) { gl.delete_sync(f); f = nullptr; }
    if (read_) gl.delete_framebuffers(1, &read_);
    if (draw_) gl.delete_framebuffers(1, &draw_);
    if (transport_ == GT_TRANSPORT_READBACK) gl.delete_buffers(slots, buffer_);
    else {
        glDeleteTextures(slots, texture_);
        gl.delete_memory(slots, memory_);
        if (transport_ == GT_TRANSPORT_SEMAPHORE) gl.delete_semaphores(slots, semaphore_);
    }
    read_ = draw_ = 0; memset(texture_, 0, sizeof texture_); memset(memory_, 0, sizeof memory_);
    memset(semaphore_, 0, sizeof semaphore_); memset(buffer_, 0, sizeof buffer_);
    prepared_ = false; gl_ = nullptr; transport_ = GT_TRANSPORT_NONE;
    return glGetError() == GL_NO_ERROR;
}
} // namespace render_capture
