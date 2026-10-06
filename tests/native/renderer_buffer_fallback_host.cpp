// The test generates includes from the pinned renderer, not copied methods.
// Only dependencies/GL boundaries below are inert. No Windows/GL library loads.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdarg>
#include <cstring>
#include <map>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

using u8 = uint8_t; using u16 = uint16_t; using u32 = uint32_t;
using s16 = int16_t; using s32 = int32_t; using f32 = float;
using GLenum = u32; using GLuint = u32; using GLint = s32;
using GLubyte = u8; using GLushort = u16; using GLfloat = float;
using GLbitfield = u32; using GLvoid = void;
using GLintptr = intptr_t; using GLsizeiptr = intptr_t;
constexpr GLenum GL_ARRAY_BUFFER = 0x8892, GL_ELEMENT_ARRAY_BUFFER = 0x8893;
constexpr GLenum GL_DYNAMIC_DRAW = 0x88E8, GL_FLOAT = 0x1406, GL_BYTE = 0x1400;
constexpr GLenum GL_UNSIGNED_SHORT = 0x1403, GL_LINES = 1, GL_TRIANGLES = 4;
constexpr u32 GL_MAP_WRITE_BIT = 2, GL_MAP_PERSISTENT_BIT = 64, GL_MAP_COHERENT_BIT = 128;
constexpr u32 GL_MAP_UNSYNCHRONIZED_BIT = 32, GL_MAP_FLUSH_EXPLICIT_BIT = 16;
constexpr bool GL_FALSE = false, GL_TRUE = true;
constexpr unsigned VERTBUFF_SIZE = 80;
constexpr u16 LOG_ERROR = 1;

static void require(bool ok, const char* reason) { if (!ok) throw std::runtime_error(reason); }

struct Object {
    unsigned generation = 0;
    bool immutable = false;
    std::vector<u8> bytes;
};
struct Vao { GLuint position = 0, elements = 0; GLint stride = 0; };
struct Draw {
    GLenum mode = 0;
    GLint first = 0, count = 0, base = 0;
    uintptr_t index_offset = 0;
    std::vector<u8> vertices, indices;
};
static struct FakeGL {
    std::map<GLuint, Object> objects;
    std::map<GLuint, Vao> vaos;
    GLuint array = 0, vao = 0, next_buffer = 1, next_vao = 1, recycled = 0;
    unsigned generations = 0, deleted = 0, map_calls = 0, subdata_calls = 0;
    unsigned mapped_copies = 0, failure_mask = 0, transient_maps = 0;
    std::vector<std::string> receipts;
    Draw draw;
    GLuint bound(GLenum target) const {
        return target == GL_ARRAY_BUFFER ? array : vaos.at(vao).elements;
    }
    Object& storage(GLenum target) {
        GLuint name = bound(target);
        require(name && objects.count(name), "allocation without live binding");
        return objects.at(name);
    }
} gl;

static void glGenBuffers(GLint count, GLuint* names) {
    for (GLint i = 0; i < count; ++i) {
        GLuint name = gl.recycled ? gl.recycled : gl.next_buffer++;
        gl.recycled = 0;
        require(!gl.objects.count(name), "reused a live buffer name");
        names[i] = name;
        gl.objects.emplace(name, Object{++gl.generations, false, {}});
    }
}
static void glBindBuffer(GLenum target, GLuint name) {
    require(!name || gl.objects.count(name), "binding a deleted name");
    if (target == GL_ARRAY_BUFFER) gl.array = name;
    else { require(gl.vao != 0, "element binding has no VAO"); gl.vaos.at(gl.vao).elements = name; }
}
static void glDeleteBuffers(GLint count, const GLuint* names) {
    for (GLint i = 0; i < count; ++i) {
        require(gl.objects.erase(names[i]) == 1, "buffer deleted twice");
        ++gl.deleted;
        if (gl.array == names[i]) gl.array = 0;
        for (auto& entry : gl.vaos) if (entry.second.elements == names[i]) entry.second.elements = 0;
        gl.recycled = names[i]; // Deliberately reuse the exact numeric name next.
    }
}
static void glGenVertexArrays(GLint count, GLuint* names) {
    for (GLint i = 0; i < count; ++i) { names[i] = gl.next_vao++; gl.vaos.emplace(names[i], Vao{}); }
}
static void glBindVertexArray(GLuint name) { require(!name || gl.vaos.count(name), "missing VAO"); gl.vao = name; }
static void glDeleteVertexArrays(GLint count, const GLuint* names) {
    for (GLint i = 0; i < count; ++i) require(gl.vaos.erase(names[i]) == 1, "VAO deleted twice");
}
static void glBufferStorage(GLenum target, GLsizeiptr size, const void*, GLbitfield) {
    Object& object = gl.storage(target);
    require(object.bytes.empty(), "immutable storage reallocated");
    object.immutable = true; object.bytes.resize(size_t(size));
}
static void* glMapBufferRange(GLenum target, GLintptr offset, GLsizeiptr size, GLbitfield flags) {
    Object& object = gl.storage(target);
    require(offset >= 0 && size >= 0 && size_t(offset + size) <= object.bytes.size(), "map out of bounds");
    if (!(flags & GL_MAP_PERSISTENT_BIT)) { ++gl.transient_maps; return nullptr; }
    unsigned bit = gl.map_calls++;
    if (gl.failure_mask & (1u << bit)) return nullptr;
    return object.bytes.data() + offset;
}
static void glBufferData(GLenum target, GLsizeiptr size, const void*, GLenum) {
    Object& object = gl.storage(target);
    require(!object.immutable, "mutable allocation on immutable object");
    object.bytes.resize(size_t(size));
}
static void glBufferSubData(GLenum target, GLintptr offset, GLsizeiptr size, const void* input) {
    Object& object = gl.storage(target);
    require(!object.immutable, "SubData on immutable object without dynamic storage");
    require(offset >= 0 && size >= 0 && size_t(offset + size) <= object.bytes.size(), "SubData out of bounds");
    std::memcpy(object.bytes.data() + offset, input, size_t(size)); ++gl.subdata_calls;
}
static void glUnmapBuffer(GLenum) { throw std::runtime_error("unexpected transient unmap"); }
static void glFlushMappedBufferRange(GLenum, GLintptr, GLsizeiptr) {}
static void glVertexAttribPointer(GLuint index, GLint, GLenum, bool, GLint stride, const void* offset) {
    if (index != 0) return;
    require(gl.array && gl.objects.count(gl.array), "attribute references missing buffer");
    require(offset == nullptr, "position offset changed");
    gl.vaos.at(gl.vao).position = gl.array; gl.vaos.at(gl.vao).stride = stride;
}
static void glVertexAttrib1f(GLuint, GLfloat) {}
static void glLineWidth(f32) {}
static void glDrawArrays(GLenum mode, GLint first, GLint count) {
    const Vao& vao = gl.vaos.at(gl.vao);
    const Object& object = gl.objects.at(vao.position);
    require(first >= 0 && count > 0 && size_t((first + count) * vao.stride) <= object.bytes.size(), "draw outside vertex data");
    gl.draw = Draw{}; gl.draw.mode = mode; gl.draw.first = first; gl.draw.count = count;
    gl.draw.vertices.assign(object.bytes.begin() + first * vao.stride, object.bytes.begin() + (first + count) * vao.stride);
}
static void glDrawRangeElementsBaseVertex(GLenum mode, GLuint, GLuint last, GLint count,
                                         GLenum type, const void* indices, GLint base) {
    require(type == GL_UNSIGNED_SHORT, "index type changed");
    const Vao& vao = gl.vaos.at(gl.vao);
    const Object& elements = gl.objects.at(vao.elements);
    uintptr_t offset = reinterpret_cast<uintptr_t>(indices);
    require(offset + size_t(count) * sizeof(u16) <= elements.bytes.size(), "index draw outside uploaded bytes");
    glDrawArrays(mode, base, GLint(last + 1));
    gl.draw.count = count; gl.draw.base = base; gl.draw.index_offset = offset;
    gl.draw.indices.assign(elements.bytes.begin() + offset, elements.bytes.begin() + offset + count * sizeof(u16));
}
static void LOG(u16 level, const char* format, ...) {
    require(level == LOG_ERROR, "unexpected receipt level");
    char line[256]; va_list args; va_start(args, format);
    int size = vsnprintf(line, sizeof line, format, args); va_end(args);
    require(size > 0 && size < int(sizeof line), "receipt unbounded"); gl.receipts.emplace_back(line);
}
static u32 CRC_Calculate(u32 hash, const void* data, u32 size) {
    const auto* input = static_cast<const u8*>(data);
    for (u32 i = 0; i < size; ++i) hash = (hash ^ input[i]) * 16777619u;
    return hash;
}
static bool isHWLightingAllowed() { return false; }
static void* witness_memcpy(void* destination, const void* source, size_t size) {
    uintptr_t start = reinterpret_cast<uintptr_t>(destination);
    for (auto& entry : gl.objects) {
        Object& object = entry.second;
        uintptr_t begin = reinterpret_cast<uintptr_t>(object.bytes.data());
        if (object.immutable && start >= begin && start - begin <= object.bytes.size() && size <= object.bytes.size() - (start - begin)) {
            ++gl.mapped_copies; return std::memcpy(destination, source, size);
        }
    }
    // A checked boundary captures the original bad destination without an AV/dialog.
    throw std::runtime_error("invalid mapped memcpy");
}

#include "Parameter.inc"
#include "ObjectHandle.inc"
graphics::ObjectHandle graphics::ObjectHandle::null;
graphics::ObjectHandle graphics::ObjectHandle::defaultFramebuffer;
struct SPVertex {
    f32 x,y,z,w,nx,ny,nz,pad,r,g,b,a,flat_r,flat_g,flat_b,flat_a,s,t;
    u32 modify; u8 HWLight,clip; s16 flag;
};
namespace graphics {
    struct RectVertex { f32 x,y,z,w,s0,t0,s1,t1; };
    struct Context {
        struct DrawRectParameters { DrawModeParam mode; bool texrect = true; u32 verticesCount = 0; RectVertex* vertices = nullptr; };
        struct DrawTriangleParameters {
            DrawModeParam mode; u32 verticesCount = 0, elementsCount = 0; bool flatColors = false;
            SPVertex* vertices = nullptr; void* elements = nullptr;
        };
    };
}
#include "drawer_cached_bind.inc"
namespace opengl {
    struct GLInfo { bool bufferStorage = true; };
    struct CachedVertexAttribArray { void enableVertexAttribArray(u32, bool) {} };
    namespace rectAttrib { enum {position, texcoord0, texcoord1}; }
    namespace triangleAttrib { enum {position, color, texcoord, modify, numlights}; }
    struct GraphicsDrawer {
        virtual ~GraphicsDrawer() = default;
        virtual void drawTriangles(const graphics::Context::DrawTriangleParameters&) = 0;
        virtual void drawRects(const graphics::Context::DrawRectParameters&) = 0;
        virtual void drawLine(f32, SPVertex*) = 0;
    };
}
#define private public
#include "drawer_class.inc"
#undef private
#define memcpy witness_memcpy
#include "drawer_methods.inc"
#undef memcpy

template<class T> static void bytes_equal(const std::vector<u8>& bytes, const T* data, size_t count, const char* reason) {
    require(bytes.size() == sizeof(T) * count && std::memcmp(bytes.data(), data, bytes.size()) == 0, reason);
}
static unsigned count_bits(unsigned value) { unsigned count = 0; while (value) { count += value & 1; value >>= 1; } return count; }
static void exercise(unsigned failure_mask, bool capability, bool flip, bool baseline = false) {
    gl = FakeGL{}; gl.failure_mask = failure_mask;
    opengl::GLInfo info{capability}; opengl::CachedVertexAttribArray attrs;
    opengl::CachedBindBuffer bind(&glBindBuffer);
    {
        opengl::BufferedDrawer drawer(info, &attrs, &bind);
        if (flip) info.bufferStorage = true;
        graphics::RectVertex rects[186]{};
        for (unsigned i = 0; i < 186; ++i) rects[i] = {f32(i), 2, 3, 1, 0.5f, 0.75f, f32(i + 1), 0};
        graphics::Context::DrawRectParameters rp; rp.mode = graphics::DrawModeParam(GL_TRIANGLES); rp.verticesCount = 186; rp.vertices = rects;
        drawer.drawRects(rp); // Original dump's 5952-byte upload goes through real code.
        bytes_equal(gl.draw.vertices, rects, 186, "first rectangle draw lost bytes");
        if (baseline) throw std::runtime_error("baseline did not expose null memcpy");
        require(gl.map_calls == (capability ? 3u : 0u) && gl.transient_maps == 0, "mapping lifecycle changed");
        require(gl.receipts.size() == (capability ? count_bits(failure_mask) : 0), "fallback receipt count");
        for (const auto& line : gl.receipts) {
            require(line.find("map=null mutable_request=issued error_observed=unavailable error_origin=unassigned") != std::string::npos, "receipt invented GL success/error");
            require(line.find("bytes=4194304") != std::string::npos, "receipt size changed");
        }
        const unsigned uploads = gl.subdata_calls + gl.mapped_copies;
        drawer.drawRects(rp); bytes_equal(gl.draw.vertices, rects, 186, "cached rectangle draw lost bytes");
        require(gl.subdata_calls + gl.mapped_copies == uploads + (drawer.m_rectsBuffers.vbo.data ? 1u : 0u), "per-buffer rectangle cache selection");
        // Force a normal wrap at real capacity, then revisit an old cache key.
        drawer.m_rectsBuffers.vbo.offset = drawer.m_rectsBuffers.vbo.size - sizeof rects;
        rects[0].x = -1; drawer.drawRects(rp);
        require(gl.draw.first == 0 && drawer.m_rectsBuffers.vbo.offset == sizeof rects, "rectangle wrap bookkeeping");
        rects[0].x = 0; drawer.drawRects(rp); bytes_equal(gl.draw.vertices, rects, 186, "rectangle cache survived a wrap");
        require(drawer.m_rectsBuffers.vbo.offset == 2 * sizeof rects, "rectangle cache was not retired");
        SPVertex vertices[3]{};
        for (unsigned i = 0; i < 3; ++i) {
            vertices[i].x = f32(i + 4); vertices[i].w = 1; vertices[i].r = 0.25f;
            vertices[i].flat_r = 0.75f; vertices[i].t = 0.5f; vertices[i].modify = i + 1;
        }
        graphics::Context::DrawTriangleParameters tp; tp.mode = graphics::DrawModeParam(GL_TRIANGLES);
        tp.verticesCount = 3; tp.vertices = vertices;
        drawer.drawTriangles(tp);
        bytes_equal(gl.draw.vertices, drawer.m_vertices.data(), 3, "nonindexed triangle bytes");
        require(drawer.m_vertices[0].r == 0.25f && gl.draw.first == 0, "triangle conversion/position");
        u16 indices[3] = {2, 0, 1}; tp.elements = indices; tp.elementsCount = 3; tp.flatColors = true;
        drawer.drawTriangles(tp);
        bytes_equal(gl.draw.vertices, drawer.m_vertices.data(), 3, "indexed triangle bytes");
        bytes_equal(gl.draw.indices, indices, 3, "index bytes");
        require(drawer.m_vertices[0].r == 0.75f && gl.draw.base == 3 && gl.draw.index_offset == 0, "indexed offsets/conversion");
        // Wrap both triangle and element buffers; offsets must describe this upload.
        drawer.m_trisBuffers.vbo.offset = drawer.m_trisBuffers.vbo.size - 3 * sizeof(opengl::BufferedDrawer::Vertex);
        drawer.m_trisBuffers.ebo.offset = drawer.m_trisBuffers.ebo.size - sizeof indices;
        drawer.drawTriangles(tp);
        bytes_equal(gl.draw.indices, indices, 3, "wrapped index bytes");
        require(gl.draw.base == 0 && gl.draw.index_offset == 0, "wrapped index/vertex offsets");
        drawer.drawLine(2.0f, vertices);
        bytes_equal(gl.draw.vertices, drawer.m_vertices.data(), 2, "line bytes");
        require(gl.draw.mode == GL_LINES && gl.draw.first == 3 && gl.draw.count == 2, "line range");
        require(gl.objects.size() == 3 && gl.vaos.size() == 2, "fallback leaked an object");
        require(gl.generations == 3 + (capability ? count_bits(failure_mask) : 0), "replacement ownership count");
    }
    require(gl.objects.empty() && gl.vaos.empty() && gl.deleted == gl.generations, "final cleanup not exactly once");
}
int main(int argc, char** argv) {
    try {
        require(argc == 2, "variant missing");
        if (std::string(argv[1]) == "baseline") exercise(7, true, false, true);
        else if (std::string(argv[1]) == "missing_binding_reset") exercise(1, true, false);
        else {
            for (unsigned mask = 0; mask < 8; ++mask) exercise(mask, true, false);
            exercise(0, false, false); exercise(0, false, true);
            std::puts("PASS 10 lifetimes: map masks 0..7, mutable, capability change; rect 5952, cache/wrap, triangles, indices, lines, cleanup");
        }
        return 0;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "drawer boundary: %s\n", error.what()); return 1;
    }
}
