#include "gpu_bridge.h"
#include <stdio.h>
#include <string.h>
#ifdef GD_TEST_HOST
#include <atomic>
static std::atomic<bool> test_abandoned{false};
extern "C" void gpu_bridge_test_abandon_once(){test_abandoned.store(true);}
#endif

namespace gpu_bridge {
namespace {
template<class T> T proc(const char *name) {
    auto p=wglGetProcAddress(name);
    return p && uintptr_t(p)>3 && uintptr_t(p)!=UINTPTR_MAX ? reinterpret_cast<T>(p) : nullptr;
}
bool extension(const char* list,const char* name) {
    if(!list) return false;
    const size_t n=strlen(name);
    for(const char *p=list;(p=strstr(p,name))!=nullptr;p+=n)
        if((p==list || p[-1]==' ') && (p[n]==0 || p[n]==' ')) return true;
    return false;
}
}
bool device_for_luid(LUID luid,ID3D11Device**device,ID3D11DeviceContext**context) {
    Microsoft::WRL::ComPtr<IDXGIFactory1> factory;
    if(FAILED(CreateDXGIFactory1(IID_PPV_ARGS(&factory)))) return false;
    for(unsigned i=0;i<32;++i) {
        Microsoft::WRL::ComPtr<IDXGIAdapter1> adapter;
        if(factory->EnumAdapters1(i,&adapter)!=S_OK) break;
        DXGI_ADAPTER_DESC1 desc{};
        if(FAILED(adapter->GetDesc1(&desc))) continue;
        if(memcmp(&desc.AdapterLuid,&luid,sizeof(luid)) || (desc.Flags&DXGI_ADAPTER_FLAG_SOFTWARE)) continue;
        // Do not set SINGLETHREADED: WGL interop2's device contract forbids it.
        return SUCCEEDED(D3D11CreateDevice(adapter.Get(),D3D_DRIVER_TYPE_UNKNOWN,nullptr,
            D3D11_CREATE_DEVICE_BGRA_SUPPORT,nullptr,0,D3D11_SDK_VERSION,device,nullptr,context));
    }
    return false;
}
bool Pool::worker() const {return GetCurrentThreadId()==worker_thread_ && wglGetCurrentContext()==worker_context_;}
Result Pool::fault(DWORD error) {error_=error; enabled_=false; poisoned_=true;return Result::fault;}
bool Pool::prepare(unsigned width,unsigned height,const wchar_t*prefix,uint64_t budget) {
    if(device_ || enabled_ || poisoned_ || !width || !height || width>8192 || height>8192 || !prefix || wcslen(prefix)>100) return false;
    const unsigned output_width=width&~1u,output_height=height&~1u;
    if(!output_width||!output_height)return false;
    bytes_=uint64_t(output_width)*output_height*4*slots*2;
    if(bytes_>budget) {bytes_=0;return false;}
    worker_context_=wglGetCurrentContext();worker_thread_=GetCurrentThreadId();
    if(!worker_context_) {error_=ERROR_INVALID_HANDLE;return false;}
    // The dedicated worker may be core-profile. Legacy GL_EXTENSIONS is an
    // invalid query there; indexed enumeration does not touch renderer state.
    using StringAt=const GLubyte*(APIENTRY *)(GLenum,GLuint);
    const auto string_at=proc<StringAt>("glGetStringi");
    bool external=false;
    if(string_at) {
        GLint count=0;glGetIntegerv(0x821D,&count);
        if(count<0||count>4096){error_=ERROR_NOT_SUPPORTED;return false;}
        for(GLint i=0;i<count;++i) {
            const auto*name=reinterpret_cast<const char*>(string_at(GL_EXTENSIONS,GLuint(i)));
            if(name && (!strcmp(name,"GL_EXT_memory_object_win32")||!strcmp(name,"GL_EXT_semaphore_win32")))external=true;
        }
    } else {
        const char*list=reinterpret_cast<const char*>(glGetString(GL_EXTENSIONS));
        external=extension(list,"GL_EXT_memory_object_win32")||extension(list,"GL_EXT_semaphore_win32");
    }
    if(!external){error_=ERROR_NOT_SUPPORTED;return false;}
    using WglExtensions=const char*(WINAPI *)(HDC);
    const auto wgl_ext=proc<WglExtensions>("wglGetExtensionsStringARB");
    if(!wgl_ext || !extension(wgl_ext(wglGetCurrentDC()),"WGL_NV_DX_interop2")) {error_=ERROR_NOT_SUPPORTED;return false;}
    using Bytes=void(APIENTRY *)(GLenum,GLubyte*);
    const auto luid_query=proc<Bytes>("glGetUnsignedBytevEXT");
    const auto open=proc<DxOpen>("wglDXOpenDeviceNV");
    close_=proc<DxClose>("wglDXCloseDeviceNV");register_=proc<DxRegister>("wglDXRegisterObjectNV");
    unregister_=proc<DxUnregister>("wglDXUnregisterObjectNV");lock_=proc<DxLock>("wglDXLockObjectsNV");
    unlock_=proc<DxLock>("wglDXUnlockObjectsNV");
    if(!luid_query || !open || !close_ || !register_ || !unregister_ || !lock_ || !unlock_) {error_=ERROR_PROC_NOT_FOUND;return false;}
    // Initialization queries occur only in this owned worker context.
    luid_query(0x9599,reinterpret_cast<GLubyte*>(&config_.adapter));
    if(glGetError()!=GL_NO_ERROR) return false;
    if(!device_for_luid(config_.adapter,&device_,&context_)) {error_=ERROR_NOT_SUPPORTED;return false;}
    interop_device_=open(device_.Get());
    if(!interop_device_) {fault(GetLastError());return false;}
    source_width_=width;source_height_=height;
    config_.width=output_width;config_.height=output_height;
    if(!prepare_transfer()){fault(ERROR_INVALID_DATA);return false;}
    D3D11_TEXTURE2D_DESC desc{};
    desc.Width=output_width;desc.Height=output_height;desc.MipLevels=1;desc.ArraySize=1;
    desc.Format=DXGI_FORMAT_R8G8B8A8_UNORM;desc.SampleDesc.Count=1;
    desc.Usage=D3D11_USAGE_DEFAULT;desc.BindFlags=D3D11_BIND_RENDER_TARGET|D3D11_BIND_SHADER_RESOURCE;
    for(unsigned i=0;i<slots;++i) {
        auto&s=slot_[i];
        desc.MiscFlags=0;
        HRESULT hr=device_->CreateTexture2D(&desc,nullptr,&s.interop);
        if(FAILED(hr)) {fault(DWORD(hr));return false;}
        glGenTextures(1,&s.texture);
        s.registration=register_(interop_device_,s.interop.Get(),s.texture,GL_TEXTURE_2D,0x0002);
        if(!s.registration) {fault(GetLastError());return false;}
        desc.MiscFlags=D3D11_RESOURCE_MISC_SHARED_NTHANDLE|D3D11_RESOURCE_MISC_SHARED_KEYEDMUTEX;
        hr=device_->CreateTexture2D(&desc,nullptr,&s.shared);
        if(SUCCEEDED(hr))hr=s.shared.As(&s.mutex);
        if(FAILED(hr)) {fault(DWORD(hr));return false;}
        Microsoft::WRL::ComPtr<IDXGIResource1> resource;
        hr=s.shared.As(&resource);
        swprintf_s(config_.names[i],L"%s-texture-%u",prefix,i);
        if(SUCCEEDED(hr)) hr=resource->CreateSharedHandle(nullptr,DXGI_SHARED_RESOURCE_READ|DXGI_SHARED_RESOURCE_WRITE,
                                                         config_.names[i],&s.share_handle);
        if(FAILED(hr)) {fault(DWORD(hr));return false;}
    }
    if(glGetError()!=GL_NO_ERROR) {fault(ERROR_INVALID_DATA);return false;}
    enabled_=true;return true;
}
bool Pool::prepare_transfer() {
    using CreateShader=GLuint(APIENTRY *)(GLenum);
    using ShaderSource=void(APIENTRY *)(GLuint,GLsizei,const char*const*,const GLint*);
    using ShaderAction=void(APIENTRY *)(GLuint);
    using ObjectStatus=void(APIENTRY *)(GLuint,GLenum,GLint*);
    using CreateProgram=GLuint(APIENTRY *)();
    using AttachShader=void(APIENTRY *)(GLuint,GLuint);
    using UniformLocation=GLint(APIENTRY *)(GLuint,const char*);
    using SamplerParameter=void(APIENTRY *)(GLuint,GLenum,GLint);
    const auto create_shader=proc<CreateShader>("glCreateShader");
    const auto shader_source=proc<ShaderSource>("glShaderSource");
    const auto compile=proc<ShaderAction>("glCompileShader");
    const auto shader_status=proc<ObjectStatus>("glGetShaderiv");
    const auto delete_shader=proc<ShaderAction>("glDeleteShader");
    const auto create_program=proc<CreateProgram>("glCreateProgram");
    const auto attach_shader=proc<AttachShader>("glAttachShader");
    const auto link=proc<ShaderAction>("glLinkProgram");
    const auto program_status=proc<ObjectStatus>("glGetProgramiv");
    const auto uniform_location=proc<UniformLocation>("glGetUniformLocation");
    const auto gen_fbos=proc<GenObjects>("glGenFramebuffers");
    const auto gen_vaos=proc<GenObjects>("glGenVertexArrays");
    const auto gen_samplers=proc<GenObjects>("glGenSamplers");
    const auto sampler_parameter=proc<SamplerParameter>("glSamplerParameteri");
    bind_framebuffer_=proc<BindFramebuffer>("glBindFramebuffer");
    attach_texture_=proc<AttachTexture>("glFramebufferTexture2D");
    check_framebuffer_=proc<CheckFramebuffer>("glCheckFramebufferStatus");
    active_texture_=proc<ActiveTexture>("glActiveTexture");bind_sampler_=proc<BindSampler>("glBindSampler");
    uniform_int_=proc<UniformInt>("glUniform1i");use_program_=proc<UseProgram>("glUseProgram");
    bind_vao_=proc<BindObject>("glBindVertexArray");
    delete_framebuffers_=proc<DeleteObjects>("glDeleteFramebuffers");
    delete_vaos_=proc<DeleteObjects>("glDeleteVertexArrays");
    delete_samplers_=proc<DeleteObjects>("glDeleteSamplers");
    delete_program_=proc<UseProgram>("glDeleteProgram");
    if(!create_shader||!shader_source||!compile||!shader_status||!delete_shader||!create_program||
       !attach_shader||!link||!program_status||!uniform_location||!gen_fbos||!gen_vaos||!gen_samplers||
       !sampler_parameter||!bind_framebuffer_||!attach_texture_||!check_framebuffer_||!active_texture_||
       !bind_sampler_||!uniform_int_||!use_program_||!bind_vao_||!delete_framebuffers_||!delete_vaos_||
       !delete_samplers_||!delete_program_)return false;
    static const char vertex[]=R"(#version 330 core
void main(){ vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.0-1.0,0.0,1.0); }
)";
    static const char fragment[]=R"(#version 330 core
uniform sampler2D source_pixels;
uniform int source_height;
out vec4 out_color;
void main(){
    ivec2 p=ivec2(gl_FragCoord.xy);
)"
#ifdef GPU_FIDELITY_OMIT_FLIP
    R"(    int y=p.y;
)"
#elif defined(GPU_FIDELITY_WRONG_ODD_ROW)
    R"(    int y=(source_height&~1)-1-p.y;
)"
#else
    R"(    int y=source_height-1-p.y;
)"
#endif
#ifdef GPU_FIDELITY_SWAP_RB
    R"(    out_color=vec4(texelFetch(source_pixels,ivec2(p.x,y),0).bgr,1.0);
)"
#else
    R"(    out_color=vec4(texelFetch(source_pixels,ivec2(p.x,y),0).rgb,1.0);
)"
#endif
    R"(}
)";
    GLuint shaders[2]={create_shader(0x8B31),create_shader(0x8B30)};
    const char*source[2]={vertex,fragment};bool valid=true;
    for(unsigned i=0;i<2;++i){
        if(!shaders[i]){valid=false;continue;}
        shader_source(shaders[i],1,&source[i],nullptr);compile(shaders[i]);
        GLint ok=0;shader_status(shaders[i],0x8B81,&ok);valid&=ok!=0;
    }
    if(valid){
        program_=create_program();for(auto shader:shaders)attach_shader(program_,shader);link(program_);
        GLint ok=0;program_status(program_,0x8B82,&ok);valid=ok!=0;
    }
    for(auto shader:shaders)if(shader)delete_shader(shader);
    if(!valid)return false;
    use_program_(program_);const GLint source_uniform=uniform_location(program_,"source_pixels");
    source_height_uniform_=uniform_location(program_,"source_height");
#ifdef GPU_FIDELITY_OMIT_FLIP
    // This negative removes the uniform; keep its actual behavior observable.
    if(source_uniform<0)return false;
#else
    if(source_uniform<0||source_height_uniform_<0)return false;
#endif
    uniform_int_(source_uniform,0);
    gen_fbos(1,&framebuffer_);gen_vaos(1,&vao_);gen_samplers(1,&sampler_);
    sampler_parameter(sampler_,GL_TEXTURE_MIN_FILTER,GL_NEAREST);
    sampler_parameter(sampler_,GL_TEXTURE_MAG_FILTER,GL_NEAREST);
    return framebuffer_&&vao_&&sampler_&&glGetError()==GL_NO_ERROR;
}
Result Pool::copy(unsigned index,GLuint source,unsigned width,unsigned height) {
    if(!worker() || index>=slots || !source || width!=source_width_ || height!=source_height_) return Result::invalid;
    if(!enabled_) return Result::fault;
    auto&s=slot_[index];
    const HRESULT acquire=s.mutex->AcquireSync(0,0);
    if(acquire==WAIT_TIMEOUT) return Result::full;
    if(acquire!=S_OK) return fault(DWORD(acquire)); // WAIT_ABANDONED is NOT success
    // The consumer can never own interop: only this worker touches this ordinary
    // texture. It may wait on this worker's previous D3D GPU copy, never gameplay.
    ++locks_;
    if(!lock_(interop_device_,1,&s.registration)) return fault(GetLastError());
    // A single worker GPU pass combines inversion, integer crop and opaque
    // alpha. It neither reads CPU pixels nor mutates producer GL state.
    bind_framebuffer_(0x8CA9,framebuffer_);
    attach_texture_(0x8CA9,0x8CE0,GL_TEXTURE_2D,s.texture,0);
    glDrawBuffer(0x8CE0);
    GLenum error=GL_NO_ERROR;
    if(check_framebuffer_(0x8CA9)!=0x8CD5)error=0x0506;
    else {
        glDisable(GL_SCISSOR_TEST);glDisable(0x8DB9);glDisable(GL_DITHER);
        glDisable(GL_BLEND);glDisable(GL_DEPTH_TEST);glDisable(GL_STENCIL_TEST);
        glDisable(GL_CULL_FACE);glDisable(0x8C89); // rasterizer discard
        glColorMask(GL_TRUE,GL_TRUE,GL_TRUE,GL_TRUE);
        glViewport(0,0,GLsizei(config_.width),GLsizei(config_.height));
        use_program_(program_);uniform_int_(source_height_uniform_,GLint(source_height_));
        active_texture_(0x84C0);glBindTexture(GL_TEXTURE_2D,source);bind_sampler_(0,sampler_);
        bind_vao_(vao_);
#ifndef GPU_BRIDGE_OMIT_COPY
        glDrawArrays(GL_TRIANGLES,0,3);
#endif
    }
    // Error query belongs to worker's owned context; never drains renderer errors.
    const GLenum driver_error=glGetError();if(driver_error!=GL_NO_ERROR)error=driver_error;
    if(!unlock_(interop_device_,1,&s.registration)) return fault(GetLastError());
    if(error!=GL_NO_ERROR) return fault(error);
    context_->CopyResource(s.shared.Get(),s.interop.Get());
    context_->Flush();
    if(FAILED(device_->GetDeviceRemovedReason())) return fault(ERROR_DEVICE_NOT_CONNECTED);
    const HRESULT release=s.mutex->ReleaseSync(1);
    if(FAILED(release)) return fault(DWORD(release));
    return Result::copied;
}
KeyReturn Pool::key0_returned(unsigned index, bool reclaim_unconsumed) {
    if (!worker() || index >= slots || !slot_[index].mutex) return KeyReturn::invalid;
    if (poisoned_) return KeyReturn::fault;
    auto &mutex = slot_[index].mutex;
    HRESULT acquired;
#ifdef GD_TEST_HOST
    acquired = test_abandoned.exchange(false) ? WAIT_ABANDONED : mutex->AcquireSync(0, 0);
#else
    acquired = mutex->AcquireSync(0, 0);
#endif
    if (acquired == WAIT_TIMEOUT && reclaim_unconsumed) acquired = mutex->AcquireSync(1, 0);
    if (acquired == WAIT_TIMEOUT) return KeyReturn::pending;
    if (acquired != S_OK) { fault(DWORD(acquired)); return KeyReturn::fault; }
    const HRESULT released = mutex->ReleaseSync(0);
    if (FAILED(released)) { fault(DWORD(released)); return KeyReturn::fault; }
    return KeyReturn::ready;
}
bool Pool::shutdown() {
    if(!worker() || poisoned_) return false;
    // Preparation/retirement only. Never called by a renderer callback.
    for(unsigned i=0;i<slots;++i) {
        if(slot_[i].mutex) {
            const HRESULT acquire=slot_[i].mutex->AcquireSync(0,0);
            if(acquire==WAIT_TIMEOUT)return false;
            if(acquire!=S_OK){fault(DWORD(acquire));return false;}
        }
        if(slot_[i].mutex) {
            const HRESULT release=slot_[i].mutex->ReleaseSync(0);
            if(FAILED(release)) {fault(DWORD(release));return false;}
        }
    }
    enabled_=false;
    // The worker owns these bindings. Unbind before deletion so the program
    // cannot stay pending and FBO attachments cannot retain interop textures.
    if(use_program_)use_program_(0);
    if(bind_vao_)bind_vao_(0);
    if(bind_sampler_)bind_sampler_(0,0);
    if(active_texture_){active_texture_(0x84C0);glBindTexture(GL_TEXTURE_2D,0);}
    if(bind_framebuffer_)bind_framebuffer_(0x8D40,0);
    if(framebuffer_){delete_framebuffers_(1,&framebuffer_);framebuffer_=0;}
    for(auto&s:slot_) {
        if(s.registration && !unregister_(interop_device_,s.registration)) {fault(GetLastError());return false;}
        s.registration=nullptr;
        if(s.texture)glDeleteTextures(1,&s.texture);s.texture=0;
        if(s.share_handle)CloseHandle(s.share_handle);s.share_handle=nullptr;
        s.mutex.Reset();s.shared.Reset();s.interop.Reset();
    }
    if(vao_){delete_vaos_(1,&vao_);vao_=0;}
    if(sampler_){delete_samplers_(1,&sampler_);sampler_=0;}
    if(program_){delete_program_(program_);program_=0;}
    if(interop_device_ && !close_(interop_device_)) {fault(GetLastError());return false;}
    interop_device_=nullptr;context_.Reset();device_.Reset();bytes_=0;return true;
}
} // namespace gpu_bridge
