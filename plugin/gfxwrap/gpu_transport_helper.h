/* The encoder helper's half of the bridge transport (gpu_transport.h): one
 * texture it creates under the channel's name, and that texture's custody.
 * The shipped helper (gpu_encoder_dll.cpp) and every helper-role witness use
 * this one implementation, so a witness exercises the shipped transport.
 *
 * acquire() is zero-time and answers like IDXGIKeyedMutex::AcquireSync:
 * S_OK once the renderer's picture is the helper's to read (any GPU-side wait
 * it needs is queued on `context` first), WAIT_TIMEOUT before that,
 * WAIT_ABANDONED or a failure code otherwise. after_read() runs right after
 * the helper's own read of the texture was queued on the same context;
 * release() runs once that read completed and hands the texture back. */
#pragma once
#include "gpu_transport.h"
#include <d3d11_4.h>
#include <dxgi1_2.h>
#include <wrl/client.h>
#include <string.h>

namespace gpu_transport {
class HelperSlot {
public:
    HelperSlot() = default;
    HelperSlot(const HelperSlot &) = delete;
    HelperSlot &operator=(const HelperSlot &) = delete;
    ~HelperSlot() { reset(); }
    // KEYED_MUTEX opens an existing texture of that name (an older capture
    // layer made its own) or creates it; the others always create. `format`
    // is the encoder's GBENC_INPUT_* value.
    HRESULT create(ID3D11Device *device, uint32_t transport, const wchar_t *name, unsigned width, unsigned height,
                   DXGI_FORMAT dxgi_format, uint32_t format) {
        reset();
        if (transport == GT_TRANSPORT_NONE || transport >= GT_TRANSPORT_COUNT || !name || !name[0] || !width || !height)
            return E_INVALIDARG;
        transport_ = transport;
        HRESULT hr = S_OK;
        if (transport == GT_TRANSPORT_KEYED_MUTEX) {
            Microsoft::WRL::ComPtr<ID3D11Device1> device1;
            hr = device->QueryInterface(IID_PPV_ARGS(&device1));
            if (FAILED(hr)) return hr;
            hr = device1->OpenSharedResourceByName(name, DXGI_SHARED_RESOURCE_READ | DXGI_SHARED_RESOURCE_WRITE,
                                                   IID_PPV_ARGS(&texture_));
            if (FAILED(hr)) {
                hr = make_texture(device, name, width, height, dxgi_format,
                                  D3D11_RESOURCE_MISC_SHARED_NTHANDLE | D3D11_RESOURCE_MISC_SHARED_KEYEDMUTEX);
                if (FAILED(hr)) return hr;
            }
            return texture_.As(&mutex_);
        }
        wchar_t derived[GT_NAME_UNITS];
        if (!gt_derived_name(derived, name, L"-section")) return E_INVALIDARG;
        const uint64_t bytes = gt_section_bytes(transport, width, height);
        section_handle_ = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, DWORD(bytes >> 32),
                                             DWORD(bytes), derived);
        if (!section_handle_) return HRESULT_FROM_WIN32(GetLastError());
        if (GetLastError() == ERROR_ALREADY_EXISTS) return HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS);
        section_ = static_cast<gt_section *>(MapViewOfFile(section_handle_, FILE_MAP_ALL_ACCESS, 0, 0, SIZE_T(bytes)));
        if (!section_) return HRESULT_FROM_WIN32(GetLastError());
        if (transport == GT_TRANSPORT_SEMAPHORE) {
            hr = make_texture(device, name, width, height, dxgi_format,
                              D3D11_RESOURCE_MISC_SHARED_NTHANDLE | D3D11_RESOURCE_MISC_SHARED);
            if (FAILED(hr)) return hr;
            Microsoft::WRL::ComPtr<ID3D11Device5> device5;
            hr = device->QueryInterface(IID_PPV_ARGS(&device5));
            if (FAILED(hr)) return hr;
            hr = device5->CreateFence(0, D3D11_FENCE_FLAG_SHARED, IID_PPV_ARGS(&fence_));
            if (FAILED(hr)) return hr;
            if (!gt_derived_name(derived, name, L"-fence")) return E_INVALIDARG;
            hr = fence_->CreateSharedHandle(nullptr, GENERIC_ALL, derived, &fence_share_);
            if (FAILED(hr)) return hr;
        } else {
            // READBACK: the picture arrives through the section, so the
            // texture is this device's alone.
            hr = make_texture(device, nullptr, width, height, dxgi_format, 0);
            if (FAILED(hr)) return hr;
        }
        section_->magic = GT_SECTION_MAGIC; section_->version = GT_SECTION_VERSION; section_->transport = transport;
        section_->format = format; section_->width = width; section_->height = height; section_->pitch = width * 4;
        section_->header_bytes = GT_SECTION_HEADER_BYTES;
        MemoryBarrier();
        return S_OK;
    }
    HRESULT acquire(ID3D11DeviceContext *context) {
        if (transport_ == GT_TRANSPORT_KEYED_MUTEX) return mutex_ ? mutex_->AcquireSync(1, 0) : E_UNEXPECTED;
        if (!section_ || !context || held_) return E_UNEXPECTED;
        const int64_t written = InterlockedCompareExchange64(&section_->written, 0, 0);
        const int64_t returned = InterlockedCompareExchange64(&section_->returned, 0, 0);
        if (transport_ == GT_TRANSPORT_SEMAPHORE) {
            // The renderer signals odd values, each two past the helper's last.
            if (written == (returned ? returned - 1 : 0)) return WAIT_TIMEOUT;
            if (!(written & 1) || written != returned + 1) return E_FAIL;
            Microsoft::WRL::ComPtr<ID3D11DeviceContext4> context4;
            const HRESULT hr = context->QueryInterface(IID_PPV_ARGS(&context4));
            if (FAILED(hr)) return hr;
            // GPU-side: the helper's later reads wait for the renderer's blit.
            const HRESULT waited = context4->Wait(fence_.Get(), uint64_t(written));
            if (FAILED(waited)) return waited;
        } else {
            if (written == returned) return WAIT_TIMEOUT;
            if (written != returned + 1) return E_FAIL;
            // The runtime copies the source bytes before UpdateSubresource returns.
            context->UpdateSubresource(texture_.Get(), 0, nullptr,
                reinterpret_cast<const unsigned char *>(section_) + GT_SECTION_HEADER_BYTES, section_->pitch, 0);
        }
        value_ = written; held_ = true;
        return S_OK;
    }
    HRESULT after_read(ID3D11DeviceContext *context) {
        if (transport_ != GT_TRANSPORT_SEMAPHORE) return S_OK;
        if (!held_ || !context) return E_UNEXPECTED;
        Microsoft::WRL::ComPtr<ID3D11DeviceContext4> context4;
        const HRESULT hr = context->QueryInterface(IID_PPV_ARGS(&context4));
        if (FAILED(hr)) return hr;
        return context4->Signal(fence_.Get(), uint64_t(value_ + 1));
    }
    HRESULT release() {
        if (transport_ == GT_TRANSPORT_KEYED_MUTEX) return mutex_ ? mutex_->ReleaseSync(0) : E_UNEXPECTED;
        if (!held_ || !section_) return E_UNEXPECTED;
        held_ = false;
        InterlockedExchange64(&section_->returned, transport_ == GT_TRANSPORT_SEMAPHORE ? value_ + 1 : value_);
        return S_OK;
    }
    ID3D11Texture2D *texture() const { return texture_.Get(); }
    uint32_t transport() const { return transport_; }
    void reset() {
        mutex_.Reset(); texture_.Reset(); fence_.Reset();
        if (share_) CloseHandle(share_);
        if (fence_share_) CloseHandle(fence_share_);
        if (section_) UnmapViewOfFile(section_);
        if (section_handle_) CloseHandle(section_handle_);
        share_ = fence_share_ = section_handle_ = nullptr; section_ = nullptr;
        transport_ = GT_TRANSPORT_NONE; value_ = 0; held_ = false;
    }
private:
    HRESULT make_texture(ID3D11Device *device, const wchar_t *name, unsigned width, unsigned height,
                         DXGI_FORMAT format, UINT misc) {
        D3D11_TEXTURE2D_DESC desc{}; desc.Width = width; desc.Height = height; desc.MipLevels = 1; desc.ArraySize = 1;
        desc.Format = format; desc.SampleDesc.Count = 1; desc.Usage = D3D11_USAGE_DEFAULT;
        desc.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE; desc.MiscFlags = misc;
        HRESULT hr = device->CreateTexture2D(&desc, nullptr, &texture_);
        if (FAILED(hr) || !name) return hr;
        Microsoft::WRL::ComPtr<IDXGIResource1> resource;
        hr = texture_.As(&resource);
        if (FAILED(hr)) return hr;
        return resource->CreateSharedHandle(nullptr, DXGI_SHARED_RESOURCE_READ | DXGI_SHARED_RESOURCE_WRITE, name, &share_);
    }
    uint32_t transport_ = GT_TRANSPORT_NONE;
    Microsoft::WRL::ComPtr<ID3D11Texture2D> texture_;
    Microsoft::WRL::ComPtr<IDXGIKeyedMutex> mutex_;
    Microsoft::WRL::ComPtr<ID3D11Fence> fence_;
    HANDLE share_ = nullptr, fence_share_ = nullptr, section_handle_ = nullptr;
    gt_section *section_ = nullptr;
    int64_t value_ = 0; // the `written` this helper holds
    bool held_ = false;
};
} // namespace gpu_transport
