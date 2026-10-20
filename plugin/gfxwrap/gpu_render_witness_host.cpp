/* Test-only x86 producer for the renderer-thread capture witnesses. One GL
 * context on one thread plays the renderer; every capture step runs in it, as
 * render_tick and gd_capture run them in Project64. Hidden window, no emulator.
 *   selection <input_dir> <out_dir>      the sample against the selector's input
 *   bridge <name> <source_w> <source_h> <crop_x> <crop_y> <snapshot_w> <snapshot_h> <pictures>
 *                                         snapshot + bridge into the helper-role consumer */
#include "gl_fixture.h"
#include "gpu_render.h"
#include "gpu_render_wire.h"
#include <wchar.h>
#include <string>

namespace {
using Bind = void (APIENTRY *)(GLenum, GLuint);
using SubImage = void (APIENTRY *)(GLuint, GLint, GLint, GLint, GLsizei, GLsizei, GLenum, GLenum, const void *);
Bind bind_buffer = nullptr;
SubImage texture_sub_image = nullptr;
GLuint hostile_pack = 0;

// A renderer can leave any of this set when capture runs; a blit honours the
// scissor test and sRGB writes, and the sample's readback the pack state.
void hostile_on() {
    glEnable(GL_SCISSOR_TEST); glScissor(7, 9, 1, 1); glEnable(0x8DB9 /* FRAMEBUFFER_SRGB */);
    glEnable(GL_DITHER); glEnable(GL_BLEND); glBlendFunc(GL_ZERO, GL_ZERO); glEnable(GL_DEPTH_TEST);
    glEnable(GL_STENCIL_TEST); glEnable(GL_CULL_FACE); glColorMask(GL_FALSE, GL_FALSE, GL_FALSE, GL_FALSE);
    glPixelStorei(GL_PACK_ALIGNMENT, 2); glPixelStorei(GL_PACK_ROW_LENGTH, 5);
    glPixelStorei(GL_PACK_SKIP_ROWS, 1); glPixelStorei(GL_PACK_SKIP_PIXELS, 1);
    bind_buffer(0x88EB /* PIXEL_PACK_BUFFER */, hostile_pack);
}
void hostile_check() {
    GLint box[4]{}, alignment = 0, row = 0, rows = 0, pixels = 0, pack = 0; GLboolean mask[4]{GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE};
    glGetIntegerv(GL_SCISSOR_BOX, box); glGetBooleanv(GL_COLOR_WRITEMASK, mask);
    glGetIntegerv(GL_PACK_ALIGNMENT, &alignment); glGetIntegerv(GL_PACK_ROW_LENGTH, &row);
    glGetIntegerv(GL_PACK_SKIP_ROWS, &rows); glGetIntegerv(GL_PACK_SKIP_PIXELS, &pixels); glGetIntegerv(0x88ED, &pack);
    CHECK(glIsEnabled(GL_SCISSOR_TEST) && box[0] == 7 && box[1] == 9 && box[2] == 1 && box[3] == 1);
    CHECK(glIsEnabled(0x8DB9) && glIsEnabled(GL_DITHER) && glIsEnabled(GL_BLEND) && glIsEnabled(GL_DEPTH_TEST)
          && glIsEnabled(GL_STENCIL_TEST) && glIsEnabled(GL_CULL_FACE) && !mask[0] && !mask[1] && !mask[2] && !mask[3]);
    CHECK(alignment == 2 && row == 5 && rows == 1 && pixels == 1 && GLuint(pack) == hostile_pack);
}
void hostile_off() {
    glDisable(GL_SCISSOR_TEST); glDisable(0x8DB9); glDisable(GL_BLEND); glDisable(GL_DEPTH_TEST);
    glDisable(GL_STENCIL_TEST); glDisable(GL_CULL_FACE); glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE);
    glPixelStorei(GL_PACK_ALIGNMENT, 4); glPixelStorei(GL_PACK_ROW_LENGTH, 0);
    glPixelStorei(GL_PACK_SKIP_ROWS, 0); glPixelStorei(GL_PACK_SKIP_PIXELS, 0); bind_buffer(0x88EB, 0);
}
GLuint texture(const render_capture::Gl &gl, unsigned width, unsigned height) {
    GLuint name = 0; gl.create_textures(GL_TEXTURE_2D, 1, &name); CHECK(name);
    gl.texture_storage(name, 1, 0x8058 /* RGBA8 */, GLsizei(width), GLsizei(height));
    return name;
}
void upload(GLuint name, unsigned width, unsigned height, const unsigned char *rgba) {
    texture_sub_image(name, 0, 0, 0, GLsizei(width), GLsizei(height), GL_RGBA, GL_UNSIGNED_BYTE, rgba);
}
std::vector<unsigned char> pattern(unsigned occurrence, unsigned width, unsigned height) {
    std::vector<unsigned char> rgba(size_t(width) * height * 4);
    for (unsigned y = 0; y < height; ++y) for (unsigned x = 0; x < width; ++x) {
        auto *p = &rgba[(size_t(y) * width + x) * 4]; // GL row 0 is the bottom row
        for (unsigned c = 0; c < 3; ++c) p[c] = (unsigned char)witness_channel(occurrence, x, y, c);
        p[3] = (unsigned char)witness_channel(occurrence, x, y, 0); // alpha deliberately varies
    }
    return rgba;
}

int selection(const std::wstring &input, const std::wstring &output, const render_capture::Gl &gl) {
    const unsigned sizes[2][2] = {{640, 480}, {641, 481}};
    for (const auto &size : sizes) {
        const unsigned width = size[0], height = size[1];
        wchar_t path[512]; swprintf_s(path, L"%s\\input_%ux%u.rgba", input.c_str(), width, height);
        FILE *in = _wfopen(path, L"rb"); CHECK(in);
        swprintf_s(path, L"%s\\samples_%ux%u.bgra", output.c_str(), width, height);
        FILE *out = _wfopen(path, L"wb"); CHECK(out);
        render_capture::Sampler sampler;
        CHECK(sampler.prepare(gl, width, height, 2, 1u << 20));
        const GLuint source = texture(gl, width, height);
        std::vector<unsigned char> frame(size_t(width) * height * 4);
        const size_t bytes = size_t(sampler.sample_bytes());
        std::vector<unsigned char> sample(bytes + 64);
        unsigned pictures = 0, reads = 0; uint64_t readback = 0;
        while (fread(frame.data(), 1, frame.size(), in) == frame.size()) {
            upload(source, width, height, frame.data()); // bottom-up original bytes, as the renderer drew them
            const unsigned slot = pictures % 2;
            hostile_on();
            { render_capture::StateGuard guard(gl); CHECK(sampler.issue(slot, source)); }
            hostile_check(); hostile_off();
            memset(sample.data(), 0xCD, sample.size());
            auto polled = render_capture::Sampler::Poll::pending;
            for (unsigned n = 0; n < 5000 && polled == render_capture::Sampler::Poll::pending; ++n) {
                polled = sampler.take(slot, sample.data(), sample.size());
                if (polled == render_capture::Sampler::Poll::pending) Sleep(1);
            }
            CHECK(polled == render_capture::Sampler::Poll::ready);
            for (size_t i = bytes; i < sample.size(); ++i) CHECK(sample[i] == 0xCD);
            CHECK(fwrite(sample.data(), 1, bytes, out) == bytes);
            ++pictures; ++reads; readback += bytes;
        }
        CHECK(pictures == 11 && fclose(in) == 0 && fclose(out) == 0);
        glDeleteTextures(1, &source); CHECK(sampler.destroy()); CHECK(glGetError() == GL_NO_ERROR);
        printf("sample %ux%u bytes=%zu pictures=%u reads=%u readback_bytes=%llu state_restored=yes guard_bytes=yes renderer_thread=yes\n",
               width, height, bytes, pictures, reads, (unsigned long long)readback);
    }
    printf("selection witness completed\n");
    return 0;
}

int bridge(wchar_t **argv, const render_capture::Gl &gl) {
    const unsigned source_width = _wtoi(argv[3]), source_height = _wtoi(argv[4]), crop_x = _wtoi(argv[5]),
        crop_y = _wtoi(argv[6]), width = _wtoi(argv[7]), height = _wtoi(argv[8]), pictures = _wtoi(argv[9]);
    CHECK(width >= 2 && height >= 2 && crop_x + width <= source_width && crop_y + height <= source_height && pictures >= 1);
    wchar_t name[160]; swprintf_s(name, L"%s-control", argv[2]);
    HANDLE mapping = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0, sizeof(RenderWitnessControl), name);
    CHECK(mapping && GetLastError() != ERROR_ALREADY_EXISTS);
    auto *control = static_cast<RenderWitnessControl *>(MapViewOfFile(mapping, FILE_MAP_ALL_ACCESS, 0, 0, sizeof(RenderWitnessControl)));
    CHECK(control);
    CHECK(render_capture::adapter_luid(gl, &control->adapter));
    control->width = width & ~1u; control->height = height & ~1u;
    control->source_width = source_width; control->source_height = source_height;
    control->crop_x = crop_x; control->crop_y = crop_y; control->snapshot_width = width; control->snapshot_height = height;
    control->pictures = pictures;
    for (unsigned i = 0; i < 2; ++i) swprintf_s(control->names[i], L"%s-texture-%u", argv[2], i);
    InterlockedExchange(&control->initialized, 1);
    // The renderer's picture, in its own framebuffer, drawn at a crop offset.
    const GLuint source = texture(gl, source_width, source_height);
    GLuint fbo = 0; proc<GenFbo>("glGenFramebuffers")(1, &fbo); proc<BindFbo>("glBindFramebuffer")(framebuffer, fbo);
    proc<Attach>("glFramebufferTexture2D")(framebuffer, attachment, GL_TEXTURE_2D, source, 0);
    glReadBuffer(attachment); glDrawBuffer(attachment); CHECK(proc<CheckFbo>("glCheckFramebufferStatus")(framebuffer) == complete);
    glBindTexture(GL_TEXTURE_2D, source);
    snapshot::Gl copy_gl; CHECK(copy_gl.load(true) && copy_gl.copy_texture);
    snapshot::Pool pool; // prepared with the renderer's own context current: one context
    CHECK(pool.prepare(copy_gl, wglGetCurrentContext(), width, height, 2, uint64_t(width) * height * 4 * 2));
    for (unsigned n = 0; n < 10000 && !InterlockedCompareExchange(&control->textures_ready, 0, 0); ++n) Sleep(1);
    CHECK(control->textures_ready); // the helper created them; the renderer only imports
    render_capture::Bridge bridge;
    const bool imported = bridge.prepare(gl, width, height, control->names);
    if (!imported) fprintf(stderr, "bridge import failed error=%lu\n", bridge.error());
    CHECK(imported);
    const snapshot::Source frame{fbo, attachment, int(crop_x), int(crop_y), width, height, source_width, source_height};
    const snapshot::RestoreBindings restore{fbo, source, attachment};
    const auto doomed = pattern(243, source_width, source_height);
    for (unsigned occurrence = 1; occurrence <= pictures; ++occurrence) {
        const unsigned index = (occurrence - 1) % 2;
        auto &packet = control->packets[index];
        for (unsigned n = 0; n < 10000 && InterlockedCompareExchange(&packet.ready, 0, 0); ++n) Sleep(1);
        CHECK(!packet.ready && !control->failed);
        auto key = render_capture::Bridge::KeyReturn::pending; // the helper's key 0, as bridge_returns polls it
        for (unsigned n = 0; n < 10000 && key == render_capture::Bridge::KeyReturn::pending; ++n) {
            key = bridge.key0_returned(index);
            if (key == render_capture::Bridge::KeyReturn::pending) Sleep(1);
        }
        CHECK(key == render_capture::Bridge::KeyReturn::ready);
        const auto picture = pattern(occurrence, source_width, source_height);
        upload(source, source_width, source_height, picture.data());
        snapshot::Ticket ticket{};
        CHECK(pool.submit(pool.generation(), frame, restore, occurrence, int64_t(occurrence) * 101, &ticket) == snapshot::Result::submitted);
        upload(source, source_width, source_height, doomed.data()); // the renderer draws on before the copy
        snapshot::Image image{};
        CHECK(pool.poll(ticket, &image) == snapshot::Poll::ready && image.width == width && image.height == height);
        hostile_on();
        render_capture::Bridge::Result copied;
        { render_capture::StateGuard guard(gl); copied = bridge.copy(index, image.texture); }
        hostile_check(); hostile_off();
        CHECK(copied == render_capture::Bridge::Result::copied);
        glFlush(); // the key release reaches the GPU queue, as the renderer's own flush would take it
        packet.occurrence = image.occurrence; packet.qpc = image.qpc; InterlockedExchange(&packet.ready, 1);
        CHECK(pool.release(ticket));
    }
    for (unsigned n = 0; n < 20000 && unsigned(InterlockedCompareExchange(&control->consumed, 0, 0)) < pictures
         && !control->failed; ++n) Sleep(1);
    CHECK(unsigned(control->consumed) == pictures && !control->failed);
    CHECK(bridge.destroy()); pool.stop(); CHECK(pool.destroy()); CHECK(glGetError() == GL_NO_ERROR);
    UnmapViewOfFile(control); CloseHandle(mapping);
    printf("producer passed: pictures=%u state_restored=yes source=%ux%u snapshot=%ux%u output=%ux%u\n",
           pictures, source_width, source_height, width, height, width & ~1u, height & ~1u);
    return 0;
}
}

int wmain(int argc, wchar_t **argv) {
    CHECK(argc >= 2);
    WNDCLASSW wc{}; wc.style = CS_OWNDC; wc.lpfnWndProc = DefWindowProcW;
    wc.hInstance = GetModuleHandleW(nullptr); wc.lpszClassName = L"SnapshotHiddenHost"; CHECK(RegisterClassW(&wc));
    Window renderer; CHECK(wglMakeCurrent(renderer.dc, renderer.rc));
    const auto *vendor = reinterpret_cast<const char *>(glGetString(GL_VENDOR));
    const auto *device = reinterpret_cast<const char *>(glGetString(GL_RENDERER));
    CHECK(vendor && device);
    printf("renderer vendor=%s device=%s\n", vendor, device); fflush(stdout);
    // An opt-in AMD/Intel run must not quietly qualify the default NVIDIA
    // context on a mixed machine. The consumer independently opens this actual
    // GL LUID and checks delivered pixels, not merely adapter inventory names.
    const char *expected = getenv("SM64_TEST_GPU_VENDOR");
    if (expected && *expected) {
        const bool matched = (!strcmp(expected, "nvidia") && strstr(vendor, "NVIDIA"))
            || (!strcmp(expected, "amd") && (strstr(vendor, "ATI") || strstr(vendor, "AMD")))
            || (!strcmp(expected, "intel") && strstr(vendor, "Intel"));
        if (!matched) {
            fprintf(stderr, "renderer vendor mismatch: expected=%s actual=%s\n", expected, vendor);
            CHECK(wglMakeCurrent(nullptr, nullptr)); return 2;
        }
    }
    glDisable(GL_DITHER); glDisable(0x8DB9);
    render_capture::Gl gl;
    if (!gl.load()) {
        fprintf(stderr, "renderer GL prerequisite failed: detail=%u entry_point=%s\n",
                unsigned(gl.load_error), gl.missing_entry_point ? gl.missing_entry_point : "");
        CHECK(wglMakeCurrent(nullptr, nullptr)); return 3;
    }
    LUID adapter{}; CHECK(render_capture::adapter_luid(gl, &adapter));
    printf("renderer adapter_luid=%08lx:%08lx\n", adapter.HighPart, adapter.LowPart); fflush(stdout);
    bind_buffer = proc<Bind>("glBindBuffer"); texture_sub_image = proc<SubImage>("glTextureSubImage2D");
    proc<void (APIENTRY *)(GLsizei, GLuint *)>("glGenBuffers")(1, &hostile_pack);
    int result = 1;
    if (!wcscmp(argv[1], L"capabilities") && argc == 2) result = 0;
    else if (!wcscmp(argv[1], L"selection") && argc == 4) result = selection(argv[2], argv[3], gl);
    else if (!wcscmp(argv[1], L"bridge") && argc == 10) result = bridge(argv, gl);
    else CHECK(false);
    CHECK(wglMakeCurrent(nullptr, nullptr));
    return result;
}
