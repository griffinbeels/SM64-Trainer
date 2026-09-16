/* Experimental worker-only GL -> D3D11 bridge. Not a production capture ABI. */
#pragma once
#include <windows.h>
#include <GL/gl.h>
#include <d3d11_1.h>
#include <dxgi1_2.h>
#include <wrl/client.h>
#include <stdint.h>

namespace gpu_bridge {
constexpr unsigned slots = 2;
// width/height describe TOP-DOWN even-sized RGBA output, alpha255.
struct Config { LUID adapter; unsigned width, height; wchar_t names[slots][128]; };
enum class Result { copied, full, fault, invalid };
enum class KeyReturn { ready, pending, fault, invalid };
using GenObjects = void(APIENTRY *)(GLsizei,GLuint*);
using BindObject = void(APIENTRY *)(GLuint);
using DeleteObjects = void(APIENTRY *)(GLsizei,const GLuint*);
using BindFramebuffer = void(APIENTRY *)(GLenum,GLuint);
using AttachTexture = void(APIENTRY *)(GLenum,GLenum,GLenum,GLuint,GLint);
using CheckFramebuffer = GLenum(APIENTRY *)(GLenum);
using ActiveTexture = void(APIENTRY *)(GLenum);
using BindSampler = void(APIENTRY *)(GLuint,GLuint);
using UniformInt = void(APIENTRY *)(GLint,GLint);
using UseProgram = void(APIENTRY *)(GLuint);
using DxOpen = HANDLE(WINAPI *)(void*);
using DxClose = BOOL(WINAPI *)(HANDLE);
using DxRegister = HANDLE(WINAPI *)(HANDLE,void*,GLuint,GLenum,GLenum);
using DxUnregister = BOOL(WINAPI *)(HANDLE,HANDLE);
using DxLock = BOOL(WINAPI *)(HANDLE,GLint,HANDLE*);

// No raw-image CPU transfer. ALL methods belong to the dedicated GL delivery
// worker, never the renderer. This API's WGL lock/unlock may block that worker.
// Caller holds snapshot texture ownership until copy returns and release fence.
// Caller guarantees a matched ready snapshot from the shared producer context.
// Fixed lifetime object: a fault quarantines its device/resources until process exit.
class Pool {
public:
    // width/height are ORIGINAL bottom-up RGBA8 snapshot dimensions. Output is
    // floor-even top-left crop of the top-down picture, matching the old BGRA
    // sink. Odd height drops native row0; odd width drops the rightmost column.
    bool prepare(unsigned width,unsigned height,const wchar_t *prefix,uint64_t byte_budget);
    Result copy(unsigned slot,GLuint source,unsigned width,unsigned height);
    // Zero-time actual keyed custody probe. Stop-only reclaim additionally
    // accepts unconsumed key1 after permanent session revocation; never active.
    KeyReturn key0_returned(unsigned slot, bool reclaim_unconsumed = false);
    bool quarantined() const { return poisoned_; }
    bool shutdown(); // worker, only after consumer has returned both keys
    const Config& config() const { return config_; }
    unsigned copies() const { return copies_; }
    unsigned locks() const { return locks_; }
    uint64_t logical_bytes() const { return bytes_; }
    DWORD error() const { return error_; }
private:
    Config config_{};
    struct Slot {
        Microsoft::WRL::ComPtr<ID3D11Texture2D> interop, shared;
        Microsoft::WRL::ComPtr<IDXGIKeyedMutex> mutex;
        HANDLE share_handle=nullptr, registration=nullptr;
        GLuint texture=0;
    } slot_[slots];
    Microsoft::WRL::ComPtr<ID3D11Device> device_;
    Microsoft::WRL::ComPtr<ID3D11DeviceContext> context_;
    HGLRC worker_context_=nullptr;
    DWORD worker_thread_=0,error_=0;
    HANDLE interop_device_=nullptr;
    DxClose close_=nullptr; DxRegister register_=nullptr;
    DxUnregister unregister_=nullptr; DxLock lock_=nullptr,unlock_=nullptr;
    BindFramebuffer bind_framebuffer_=nullptr;
    AttachTexture attach_texture_=nullptr;
    CheckFramebuffer check_framebuffer_=nullptr;
    ActiveTexture active_texture_=nullptr;
    BindSampler bind_sampler_=nullptr;
    UniformInt uniform_int_=nullptr;
    UseProgram use_program_=nullptr;
    BindObject bind_vao_=nullptr;
    DeleteObjects delete_framebuffers_=nullptr,delete_vaos_=nullptr,delete_samplers_=nullptr;
    UseProgram delete_program_=nullptr;
    GLuint framebuffer_=0,vao_=0,sampler_=0,program_=0;
    GLint source_height_uniform_=-1;
    unsigned source_width_=0,source_height_=0;
    bool prepare_transfer();
    uint64_t bytes_=0;
    unsigned copies_=0,locks_=0;
    bool enabled_=false,poisoned_=false;
    bool worker() const;
    Result fault(DWORD error);
};
bool device_for_luid(LUID,ID3D11Device**,ID3D11DeviceContext**);
} // namespace gpu_bridge
