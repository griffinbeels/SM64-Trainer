/* Independent test-only upload oracle; no native full-image readback. */
#include "gl_fixture.h"
#include "gpu_bridge_worker.h"
#include "gpu_selection.h"
#include <wchar.h>

int wmain(int argc, wchar_t** argv) {
    CHECK(argc == 3);
    WNDCLASSW wc{}; wc.style = CS_OWNDC; wc.lpfnWndProc = DefWindowProcW;
    wc.hInstance = GetModuleHandleW(nullptr); wc.lpszClassName = L"SnapshotHiddenHost";
    CHECK(RegisterClassW(&wc));
    Window producer; CHECK(wglMakeCurrent(producer.dc, producer.rc));
    printf("renderer=%s\n", glGetString(GL_RENDERER));
    auto gen = proc<GenFbo>("glGenFramebuffers");
    auto bind = proc<BindFbo>("glBindFramebuffer");
    auto attach = proc<Attach>("glFramebufferTexture2D");
    auto check = proc<CheckFbo>("glCheckFramebufferStatus");
    auto bind_buffer = proc<BindBuffer>("glBindBuffer");
    auto delete_fbos = proc<GenFbo>("glDeleteFramebuffers");
    GLuint texture = 0, fbo = 0; glGenTextures(1, &texture); gen(1, &fbo);
    GLint major = 0, minor = 0; glGetIntegerv(0x821B, &major); glGetIntegerv(0x821C, &minor);
    const bool dsa = major > 4 || (major == 4 && minor >= 5);
    {
        BridgeWorker worker(producer);
        for (unsigned odd = 0; odd < 2; ++odd) {
            const unsigned width = 640 + odd, height = 480 + odd;
            const uint64_t expected_bytes = uint64_t((width + 7) / 8) * ((height + 7) / 8) * 4;
            wchar_t path[1024]; FILE* input = nullptr; FILE* output = nullptr;
            swprintf_s(path, L"%s\\input_%ux%u.rgba", argv[1], width, height);
            CHECK(_wfopen_s(&input, path, L"rb") == 0);
            swprintf_s(path, L"%s\\samples_%ux%u.bgra", argv[2], width, height);
            CHECK(_wfopen_s(&output, path, L"wb") == 0);
            glBindTexture(GL_TEXTURE_2D, texture); bind_buffer(0x88EC, 0);
            glTexImage2D(GL_TEXTURE_2D, 0, 0x8058, width, height, 0, GL_RGBA, GL_UNSIGNED_BYTE, nullptr);
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
            bind(framebuffer, fbo); attach(framebuffer, attachment, GL_TEXTURE_2D, texture, 0);
            glReadBuffer(attachment); glDrawBuffer(attachment); CHECK(check(framebuffer) == complete);
            glDisable(GL_DITHER); glDisable(0x8DB9); glDisable(GL_SCISSOR_TEST);
            glViewport(0, 0, width, height);
            snapshot::Pool snapshots; snapshot::Gl gl; gpu_selection::Sampler sampler;
            GLuint hostile_pbo = 0;
            worker.run([&] {
                CHECK(GetCurrentThreadId() != GetWindowThreadProcessId(producer.hwnd, nullptr));
                CHECK(gl.load(dsa)); CHECK(snapshots.prepare(gl, producer.rc, width, height, 1, width*height*4));
                if (!odd) CHECK(!sampler.prepare(width, height, expected_bytes - 1));
                CHECK(sampler.prepare(width, height, expected_bytes));
                CHECK(!sampler.prepare(width, height, expected_bytes)); // no active reallocation
                using Data = void(APIENTRY *)(GLenum, ptrdiff_t, const void*, GLenum);
                proc<GenFbo>("glGenBuffers")(1, &hostile_pbo); bind_buffer(pack_buffer, hostile_pbo);
                proc<Data>("glBufferData")(pack_buffer, 32, nullptr, 0x88E0);
                CHECK(glGetError() == GL_NO_ERROR);
            });
            std::vector<unsigned char> bytes(size_t(width) * height * 4);
            std::vector<unsigned char> selected(size_t(sampler.sample_bytes()) + 16, 0xD7);
            CHECK(sampler.sample(texture, width, height, selected.data() + 8,
                                 sampler.sample_bytes()) == gpu_selection::Result::wrong_worker);
            CHECK(!sampler.shutdown()); // owner-only resource destruction
            const snapshot::Source source{fbo, attachment, 0, 0, width, height, width, height};
            const snapshot::RestoreBindings restore{fbo, texture, attachment};
            unsigned pictures = 0;
            while (fread(bytes.data(), 1, bytes.size(), input) == bytes.size()) {
                ++pictures;
                glBindTexture(GL_TEXTURE_2D, texture); bind_buffer(0x88EC, 0);
                glPixelStorei(GL_UNPACK_ALIGNMENT, 1); glPixelStorei(GL_UNPACK_ROW_LENGTH, 0);
                glPixelStorei(GL_UNPACK_SKIP_ROWS, 0); glPixelStorei(GL_UNPACK_SKIP_PIXELS, 0);
                glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE, bytes.data());
                snapshot::Ticket ticket{}; snapshot::Image image{};
                CHECK(snapshots.submit(snapshots.generation(), source, restore, pictures, pictures*101,
                                       &ticket) == snapshot::Result::submitted);
                // Overwrite the original before the worker examines its OWNED image.
                glClearColor(0, 0, 0, 0); glClear(GL_COLOR_BUFFER_BIT); glFlush();
                worker.run([&] { image = wait_image(snapshots, ticket); });
                snapshot::Ticket refused{};
                CHECK(snapshots.submit(snapshots.generation(), source, restore, 999, 0,
                                       &refused) == snapshot::Result::full);
                worker.run([&] {
                    CHECK(image.occurrence == pictures && image.qpc == int64_t(pictures)*101);
                    CHECK(!snapshots.completed(ticket));
                    const auto reads = sampler.reads();
                    CHECK(sampler.sample(image.texture, width + 1, height, selected.data() + 8,
                                         sampler.sample_bytes()) == gpu_selection::Result::invalid);
                    CHECK(sampler.sample(image.texture, width, height, selected.data() + 8,
                                         sampler.sample_bytes() - 1) == gpu_selection::Result::invalid);
                    CHECK(reads == sampler.reads());
                    // Poison only this dedicated worker's state. The sampler must
                    // not inherit a bridge viewport, raster mode or pack-buffer offset.
                    glEnable(GL_SCISSOR_TEST); glScissor(0, 0, 1, 1);
                    glEnable(GL_DITHER); glEnable(0x8DB9); glEnable(GL_BLEND);
                    glEnable(0x8C89); glColorMask(GL_FALSE, GL_FALSE, GL_FALSE, GL_FALSE);
                    glPolygonMode(GL_FRONT_AND_BACK, GL_LINE); glViewport(7, 9, 1, 1);
                    proc<void(APIENTRY *)(GLenum, GLenum)>("glClipControl")(0x8CA2, 0x935F);
                    bind_buffer(pack_buffer, hostile_pbo);
                    glPixelStorei(GL_PACK_ALIGNMENT, 8); glPixelStorei(GL_PACK_ROW_LENGTH, 128);
                    glPixelStorei(GL_PACK_SKIP_ROWS, 3); glPixelStorei(GL_PACK_SKIP_PIXELS, 5);
                    CHECK(glGetError() == GL_NO_ERROR);
                    const auto result = sampler.sample(image.texture, width, height,
                                                       selected.data() + 8, sampler.sample_bytes());
                    if (result != gpu_selection::Result::sampled)
                        fprintf(stderr, "sampler result=%d GL/error=%lu\n", int(result), sampler.error());
                    CHECK(result == gpu_selection::Result::sampled);
                    for (unsigned i = 0; i < 8; ++i) {
                        CHECK(selected[i] == 0xD7);
                        CHECK(selected[selected.size() - 1 - i] == 0xD7);
                    }
                    CHECK(!snapshots.completed(ticket));
                    // ReadPixels returned actual client bytes; only now release.
                    CHECK(snapshots.release(ticket));
                    for (unsigned n = 0; n < 1000 && !snapshots.completed(ticket); ++n) {
                        snapshots.reap(); if (!snapshots.completed(ticket)) Sleep(1);
                    }
                    CHECK(snapshots.completed(ticket));
                });
                CHECK(fwrite(selected.data() + 8, 1, size_t(sampler.sample_bytes()), output) == sampler.sample_bytes());
            }
            CHECK(feof(input) && pictures == 11); fclose(input); fclose(output);
            printf("original=%ux%u sample=%ux%u bytes=%llu pictures=%u reads=%u readback_bytes=%llu custody=yes guard_bytes=yes worker_only=yes\n",
                   width, height, sampler.columns(), sampler.rows(), sampler.sample_bytes(), pictures,
                   sampler.reads(), sampler.readback_bytes());
            worker.run([&] {
                CHECK(sampler.shutdown());
                bind_buffer(pack_buffer, 0); proc<GenFbo>("glDeleteBuffers")(1, &hostile_pbo);
                snapshots.stop(); snapshots.reap(); CHECK(snapshots.destroy()); CHECK(glGetError() == GL_NO_ERROR);
            });
        }
    }
    bind(framebuffer, 0); glBindTexture(GL_TEXTURE_2D, 0); delete_fbos(1, &fbo); glDeleteTextures(1, &texture);
    CHECK(glGetError() == GL_NO_ERROR); CHECK(wglMakeCurrent(nullptr, nullptr));
    printf("selection witness completed\n"); return 0;
}
