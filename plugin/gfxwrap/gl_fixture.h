/* Shared hidden real-GPU test fixture; never compiled into the shipped plugin. */
#pragma once
#include "gl_snapshot.h"
#include <stdio.h>
#include <stdlib.h>
#include <functional>
#include <thread>
#include <vector>
#include <algorithm>
#include <string.h>

#define CHECK(x) do { if (!(x)) { fprintf(stderr, "snapshot contract line %d: %s\n", __LINE__, #x); fflush(stderr); ExitProcess(1); } } while (0)
namespace {
constexpr GLenum framebuffer = 0x8D40, read_fbo = 0x8CA8, draw_fbo = 0x8CA9;
constexpr GLenum attachment = 0x8CE0, complete = 0x8CD5;
constexpr GLenum texture3 = 0x84C3, pack_buffer = 0x88EB;
LARGE_INTEGER phases[6]{};
void mark_phase(unsigned phase) { QueryPerformanceCounter(&phases[phase]); }
HANDLE prep_entered = nullptr, prep_resume = nullptr;
void pause_preparation() {
    SetEvent(prep_entered);
    CHECK(WaitForSingleObject(prep_resume, 5000) == WAIT_OBJECT_0);
}
snapshot::Gl actual_gl;
std::atomic<bool> fail_fence{false}, fail_wait{false}, hold_return{false};
snapshot::Sync APIENTRY fence_probe(GLenum condition, GLbitfield flags) {
    return fail_fence.load() ? nullptr : actual_gl.fence(condition, flags);
}
GLenum APIENTRY wait_probe(snapshot::Sync fence, GLbitfield flags, uint64_t timeout) {
    return fail_wait.load() ? 0x911D : hold_return.load() ? 0x911B : actual_gl.wait(fence, flags, timeout);
}
constexpr unsigned sw = 12, sh = 10, cw = 8, ch = 6;
using GenFbo = void (APIENTRY *)(GLsizei, GLuint *);
using BindFbo = void (APIENTRY *)(GLenum, GLuint);
using Attach = void (APIENTRY *)(GLenum, GLenum, GLenum, GLuint, GLint);
using CheckFbo = GLenum (APIENTRY *)(GLenum);
using Active = void (APIENTRY *)(GLenum);
using BindBuffer = void (APIENTRY *)(GLenum, GLuint);
template<class T> T proc(const char *name) {
    auto p = wglGetProcAddress(name); CHECK(p && uintptr_t(p) > 3 && uintptr_t(p) != UINTPTR_MAX);
    return reinterpret_cast<T>(p);
}
struct Window {
    HWND hwnd; HDC dc; HGLRC rc;
    Window() {
        hwnd = CreateWindowExW(0, L"SnapshotHiddenHost", L"", WS_POPUP, 0, 0, 64, 64,
                               nullptr, nullptr, GetModuleHandleW(nullptr), nullptr);
        CHECK(hwnd && !IsWindowVisible(hwnd));
        dc = GetDC(hwnd);
        PIXELFORMATDESCRIPTOR pfd{};
        pfd.nSize = sizeof(pfd); pfd.nVersion = 1;
        pfd.dwFlags = PFD_DRAW_TO_WINDOW | PFD_SUPPORT_OPENGL | PFD_DOUBLEBUFFER;
        pfd.iPixelType = PFD_TYPE_RGBA; pfd.cColorBits = 32;
        const int pixel_format = ChoosePixelFormat(dc, &pfd);
        CHECK(pixel_format && SetPixelFormat(dc, pixel_format, &pfd));
        rc = wglCreateContext(dc); CHECK(rc);
    }
    ~Window() { CHECK(wglDeleteContext(rc)); ReleaseDC(hwnd, dc); DestroyWindow(hwnd); }
};
class Worker {
    Window window;
    HANDLE requested = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    HANDLE done = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    std::function<void()> task;
    std::thread thread;
public:
    explicit Worker(Window &producer) {
        CHECK(requested && done);
        CHECK(GetPixelFormat(producer.dc) == GetPixelFormat(window.dc));
        CHECK(wglShareLists(producer.rc, window.rc));
        thread = std::thread([this] {
            CHECK(wglMakeCurrent(window.dc, window.rc));
            for (;;) {
                CHECK(WaitForSingleObject(requested, 5000) == WAIT_OBJECT_0);
                if (!task) break;
                task(); SetEvent(done);
            }
            CHECK(wglMakeCurrent(nullptr, nullptr));
        });
    }
    void run(std::function<void()> next) {
        task = std::move(next); SetEvent(requested);
        CHECK(WaitForSingleObject(done, 5000) == WAIT_OBJECT_0);
    }
    ~Worker() { task = {}; SetEvent(requested); thread.join(); CloseHandle(requested); CloseHandle(done); }
};
unsigned channel(unsigned occurrence, unsigned x, unsigned y, unsigned c) {
    if (c == 0) return (occurrence * 29 + x * 11 + y * 3 + 17) & 255;
    if (c == 1) return (occurrence * 13 + x * 7 + y * 23 + 41) & 255;
    return (occurrence * 19 + x * 31 + y * 5 + 71) & 255;
}
void draw(unsigned occurrence) {
    // Actual GPU rendering into an FBO. Distinct borders and every pixel asymmetric.
    glEnable(GL_SCISSOR_TEST);
    for (unsigned y = 0; y < sh; ++y) for (unsigned x = 0; x < sw; ++x) {
        glScissor(GLint(x), GLint(y), 1, 1);
        glClearColor(channel(occurrence,x,y,0)/255.f, channel(occurrence,x,y,1)/255.f,
                     channel(occurrence,x,y,2)/255.f, 1.f);
        glClear(GL_COLOR_BUFFER_BIT);
    }
    glScissor(4, 4, 1, 1); // hostile state: copy must not inherit this crop
}
void verify_pixels(const snapshot::Image &image, unsigned expected) {
    CHECK(image.width == cw && image.height == ch);
    auto bind_buffer = proc<BindBuffer>("glBindBuffer");
    bind_buffer(pack_buffer, 0); // real client-memory destination, not a PBO offset
    glPixelStorei(GL_PACK_ALIGNMENT, 1); glPixelStorei(GL_PACK_ROW_LENGTH, 0);
    glPixelStorei(GL_PACK_SKIP_ROWS, 0); glPixelStorei(GL_PACK_SKIP_PIXELS, 0);
    glPixelStorei(GL_PACK_SWAP_BYTES, GL_FALSE);
    std::vector<unsigned char> pixels(cw * ch * 4, 0xD7);
    glBindTexture(GL_TEXTURE_2D, image.texture);
    glGetTexImage(GL_TEXTURE_2D, 0, GL_RGBA, GL_UNSIGNED_BYTE, pixels.data());
    CHECK(glGetError() == GL_NO_ERROR);
    // Independently derived expected bytes, never read from source or metadata.
    for (unsigned y = 0; y < ch; ++y) for (unsigned x = 0; x < cw; ++x) {
        const unsigned at = (y * cw + x) * 4;
        for (unsigned c = 0; c < 3; ++c) {
            const auto wanted = channel(expected, x + 1, y + 2, c);
            if (pixels[at+c] != wanted) {
                fprintf(stderr, "pixel mismatch occurrence=%u x=%u y=%u c=%u actual=%u expected=%u\n",
                        expected,x,y,c,pixels[at+c],wanted); CHECK(false);
            }
        }
        CHECK(pixels[at+3] == 255);
    }
}
void verify(const snapshot::Image &image, unsigned expected) {
    CHECK(image.occurrence == expected && image.qpc == int64_t(expected) * 101);
    verify_pixels(image, expected);
}
snapshot::Image wait_image(snapshot::Pool &pool, snapshot::Ticket ticket) {
    snapshot::Image image{};
    for (unsigned n = 0; n < 1000; ++n) {
        const auto state = pool.poll(ticket, &image);
        if (state == snapshot::Poll::ready) return image;
        CHECK(state == snapshot::Poll::pending);
        Sleep(1); // host worker only; product completion queries have zero timeout
    }
    CHECK(false); return {};
}
void reap_all(snapshot::Pool &pool) {
    // Reap is nonblocking; this bounded host worker loop waits for return fences.
    for (unsigned n = 0; n < 20; ++n) { pool.reap(); Sleep(1); }
}
}
