#include "gpu_selection.h"

namespace gpu_selection {
namespace {
template<class T> T proc(const char* name) {
    auto p = wglGetProcAddress(name);
    return p && uintptr_t(p) > 3 && uintptr_t(p) != UINTPTR_MAX ? reinterpret_cast<T>(p) : nullptr;
}
constexpr GLenum framebuffer = 0x8D40, color_attachment = 0x8CE0;
constexpr GLenum complete = 0x8CD5, pack_buffer = 0x88EB, unpack_buffer = 0x88EC;
}
bool Sampler::worker() const {
    return context_ && GetCurrentThreadId() == thread_ && wglGetCurrentContext() == context_;
}
Result Sampler::fault(DWORD error) {
    error_ = error; enabled_ = false; poisoned_ = true; return Result::fault;
}
bool Sampler::prepare(unsigned width, unsigned height, uint64_t budget) {
    if (context_ || poisoned_ || !width || !height || width > 8192 || height > 8192) return false;
    error_ = 0;
    width_ = width; height_ = height;
#ifdef GPU_SELECTION_CROP_FIRST
    columns_ = ((width & ~1u) + stride - 1) / stride;
    rows_ = ((height & ~1u) + stride - 1) / stride;
#else
    columns_ = (width + stride - 1) / stride;
    rows_ = (height + stride - 1) / stride;
#endif
    bytes_ = uint64_t(columns_) * rows_ * 4;
    if (!bytes_ || bytes_ > budget) { bytes_ = 0; return false; }
    context_ = wglGetCurrentContext(); thread_ = GetCurrentThreadId();
    if (!context_) { error_ = ERROR_INVALID_HANDLE; return false; }
    // This context is owned by this worker. Preserve a preexisting error as a
    // failed operation; never drain producer GL errors or proceed past it.
    GLenum existing = glGetError();
    if (existing != GL_NO_ERROR) { fault(existing); return false; }
    GLint major = 0, minor = 0;
    glGetIntegerv(0x821B, &major); glGetIntegerv(0x821C, &minor);
    if (major < 4 || (major == 4 && minor < 5)) { fault(ERROR_NOT_SUPPORTED); return false; }
    if (!prepare_program()) { fault(ERROR_NOT_SUPPORTED); return false; }
    using Gen = void(APIENTRY *)(GLsizei, GLuint*);
    using Attach = void(APIENTRY *)(GLenum, GLenum, GLenum, GLuint, GLint);
    const auto gen_fbos = proc<Gen>("glGenFramebuffers");
    const auto attach = proc<Attach>("glFramebufferTexture2D");
    if (!gen_fbos || !attach) { fault(ERROR_PROC_NOT_FOUND); return false; }
    bind_buffer_(unpack_buffer, 0);
    active_texture_(0x84C0); glGenTextures(1, &texture_); glBindTexture(GL_TEXTURE_2D, texture_);
    glTexImage2D(GL_TEXTURE_2D, 0, 0x8058, GLsizei(columns_), GLsizei(rows_), 0,
                 GL_RGBA, GL_UNSIGNED_BYTE, nullptr);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    gen_fbos(1, &framebuffer_); bind_framebuffer_(framebuffer, framebuffer_);
    attach(framebuffer, color_attachment, GL_TEXTURE_2D, texture_, 0);
    glDrawBuffer(color_attachment); glReadBuffer(color_attachment);
    if (!texture_ || !framebuffer_ || check_framebuffer_(framebuffer) != complete) {
        fault(ERROR_INVALID_DATA); return false;
    }
    const auto error = glGetError();
    if (error != GL_NO_ERROR) { fault(error); return false; }
    enabled_ = true; return true;
}
bool Sampler::prepare_program() {
    using CreateShader = GLuint(APIENTRY *)(GLenum);
    using ShaderSource = void(APIENTRY *)(GLuint, GLsizei, const char*const*, const GLint*);
    using Action = void(APIENTRY *)(GLuint);
    using Status = void(APIENTRY *)(GLuint, GLenum, GLint*);
    using CreateProgram = GLuint(APIENTRY *)();
    using AttachShader = void(APIENTRY *)(GLuint, GLuint);
    using Location = GLint(APIENTRY *)(GLuint, const char*);
    using Gen = void(APIENTRY *)(GLsizei, GLuint*);
    using SamplerParameter = void(APIENTRY *)(GLuint, GLenum, GLint);
    const auto create_shader = proc<CreateShader>("glCreateShader");
    const auto shader_source = proc<ShaderSource>("glShaderSource");
    const auto compile = proc<Action>("glCompileShader");
    const auto shader_status = proc<Status>("glGetShaderiv");
    const auto delete_shader = proc<Action>("glDeleteShader");
    const auto create_program = proc<CreateProgram>("glCreateProgram");
    const auto attach_shader = proc<AttachShader>("glAttachShader");
    const auto link = proc<Action>("glLinkProgram");
    const auto program_status = proc<Status>("glGetProgramiv");
    const auto location = proc<Location>("glGetUniformLocation");
    const auto gen_vaos = proc<Gen>("glGenVertexArrays");
    const auto gen_samplers = proc<Gen>("glGenSamplers");
    const auto sampler_parameter = proc<SamplerParameter>("glSamplerParameteri");
    bind_framebuffer_ = proc<decltype(bind_framebuffer_)>("glBindFramebuffer");
    check_framebuffer_ = proc<decltype(check_framebuffer_)>("glCheckFramebufferStatus");
    active_texture_ = proc<decltype(active_texture_)>("glActiveTexture");
    bind_sampler_ = proc<decltype(bind_sampler_)>("glBindSampler");
    bind_buffer_ = proc<decltype(bind_buffer_)>("glBindBuffer");
    bind_vao_ = proc<decltype(bind_vao_)>("glBindVertexArray");
    uniform_int_ = proc<decltype(uniform_int_)>("glUniform1i");
    use_program_ = proc<decltype(use_program_)>("glUseProgram");
    delete_framebuffers_ = proc<decltype(delete_framebuffers_)>("glDeleteFramebuffers");
    delete_vaos_ = proc<decltype(delete_vaos_)>("glDeleteVertexArrays");
    delete_samplers_ = proc<decltype(delete_samplers_)>("glDeleteSamplers");
    delete_program_ = proc<decltype(delete_program_)>("glDeleteProgram");
    clip_control_ = proc<decltype(clip_control_)>("glClipControl"); // qualified GL 4.5 worker
    if (!create_shader || !shader_source || !compile || !shader_status || !delete_shader ||
        !create_program || !attach_shader || !link || !program_status || !location ||
        !gen_vaos || !gen_samplers || !sampler_parameter || !bind_framebuffer_ ||
        !check_framebuffer_ || !active_texture_ || !bind_sampler_ || !bind_buffer_ ||
        !bind_vao_ || !uniform_int_ || !use_program_ || !delete_framebuffers_ ||
        !delete_vaos_ || !delete_samplers_ || !delete_program_ || !clip_control_) return false;
    static const char vertex[] = R"(#version 330 core
void main() {
    vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}
)";
    static const char fragment[] = R"(#version 330 core
uniform sampler2D original_pixels;
uniform int original_height;
out vec4 selected;
void main() {
    ivec2 cell = ivec2(gl_FragCoord.xy);
)"
#ifdef GPU_SELECTION_WRONG_Y
    R"(    int y = cell.y * 8;
)"
#else
    R"(    int y = original_height - 1 - cell.y * 8;
)"
#endif
    R"(    selected = vec4(texelFetch(original_pixels, ivec2(cell.x * 8, y), 0).rgb, 1.0);
}
)";
    GLuint shaders[2] = {create_shader(0x8B31), create_shader(0x8B30)};
    const char* text[2] = {vertex, fragment}; bool valid = true;
    for (unsigned i = 0; i < 2; ++i) {
        if (!shaders[i]) { valid = false; continue; }
        shader_source(shaders[i], 1, &text[i], nullptr); compile(shaders[i]);
        GLint ok = 0; shader_status(shaders[i], 0x8B81, &ok); valid &= ok != 0;
    }
    if (valid) {
        program_ = create_program();
        for (auto shader : shaders) attach_shader(program_, shader);
        link(program_); GLint ok = 0; program_status(program_, 0x8B82, &ok); valid = ok != 0;
    }
    for (auto shader : shaders) if (shader) delete_shader(shader);
    if (!valid) return false;
    use_program_(program_); const GLint input = location(program_, "original_pixels");
    height_uniform_ = location(program_, "original_height");
    if (input < 0) return false;
#ifndef GPU_SELECTION_WRONG_Y
    if (height_uniform_ < 0) return false;
#endif
    uniform_int_(input, 0); gen_vaos(1, &vao_); gen_samplers(1, &sampler_);
    sampler_parameter(sampler_, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    sampler_parameter(sampler_, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    return vao_ && sampler_ && glGetError() == GL_NO_ERROR;
}
Result Sampler::sample(GLuint source, unsigned width, unsigned height,
                       unsigned char* bytes, size_t capacity) {
    if (!worker()) return Result::wrong_worker;
    if (!enabled_) return Result::fault;
    if (!source || source == texture_ || width != width_ || height != height_ ||
        !bytes || capacity < bytes_) return Result::invalid;
    const auto before = glGetError();
    if (before != GL_NO_ERROR) return fault(before);
    bind_framebuffer_(framebuffer, framebuffer_);
    glDrawBuffer(color_attachment); glReadBuffer(color_attachment);
    if (check_framebuffer_(framebuffer) != complete) return fault(ERROR_INVALID_DATA);
    glDisable(GL_SCISSOR_TEST); glDisable(0x8DB9); glDisable(GL_DITHER); // framebuffer sRGB
    glDisable(GL_BLEND); glDisable(GL_DEPTH_TEST); glDisable(GL_STENCIL_TEST);
    glDisable(GL_CULL_FACE); glDisable(0x8C89); // rasterizer discard
    glDisable(0x809D); glDisable(0x809E); glDisable(0x80A0); glDisable(0x8E51); // multisample, coverage, mask
    glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE); glPolygonMode(GL_FRONT_AND_BACK, GL_FILL);
    if (clip_control_) clip_control_(0x8CA1, 0x935E); // LOWER_LEFT, NEGATIVE_ONE_TO_ONE
    glViewport(0, 0, GLsizei(columns_), GLsizei(rows_));
    use_program_(program_); uniform_int_(height_uniform_, GLint(height_));
    active_texture_(0x84C0); glBindTexture(GL_TEXTURE_2D, source); bind_sampler_(0, sampler_);
    bind_vao_(vao_); glDrawArrays(GL_TRIANGLES, 0, 3);
    bind_buffer_(pack_buffer, 0);
    glPixelStorei(GL_PACK_ALIGNMENT, 1); glPixelStorei(GL_PACK_ROW_LENGTH, 0);
    glPixelStorei(GL_PACK_SKIP_ROWS, 0); glPixelStorei(GL_PACK_SKIP_PIXELS, 0);
    glPixelStorei(GL_PACK_SWAP_BYTES, GL_FALSE); glPixelStorei(GL_PACK_LSB_FIRST, GL_FALSE);
    // Target row zero ALREADY denotes the source's top row. This is the only
    // readback: ceil(W/8)*ceil(H/8)*4 bytes, in the selector's BGRA order.
#ifdef GPU_SELECTION_RGBA_BYTES
    constexpr GLenum output_format = GL_RGBA;
#else
    constexpr GLenum output_format = 0x80E1; // BGRA
#endif
    glReadPixels(0, 0, GLsizei(columns_), GLsizei(rows_), output_format, GL_UNSIGNED_BYTE, bytes);
    const auto error = glGetError();
    if (error != GL_NO_ERROR) return fault(error);
    ++reads_; readback_bytes_ += bytes_; return Result::sampled;
}
bool Sampler::shutdown() {
    if (!context_) return true;
    if (!worker()) return false;
    if (use_program_) use_program_(0);
    if (bind_vao_) bind_vao_(0);
    if (bind_sampler_) bind_sampler_(0, 0);
    if (active_texture_) active_texture_(0x84C0);
    glBindTexture(GL_TEXTURE_2D, 0);
    if (bind_framebuffer_) bind_framebuffer_(framebuffer, 0);
    if (framebuffer_) delete_framebuffers_(1, &framebuffer_);
    if (texture_) glDeleteTextures(1, &texture_);
    if (vao_) delete_vaos_(1, &vao_);
    if (sampler_) delete_samplers_(1, &sampler_);
    if (program_) delete_program_(program_);
    framebuffer_ = texture_ = vao_ = sampler_ = program_ = 0;
    enabled_ = false; context_ = nullptr;
    return glGetError() == GL_NO_ERROR;
}
} // namespace gpu_selection
