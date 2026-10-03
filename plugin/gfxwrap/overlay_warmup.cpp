/* The overlay warm-up's policy and its one device kind (overlay_warmup.h). */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <d3d11.h>
#include <dxgi.h>
#include <string.h>
#include "overlay_warmup.h"

namespace {
typedef HRESULT (WINAPI *CreateDevice)(IDXGIAdapter *, D3D_DRIVER_TYPE, HMODULE, UINT, const D3D_FEATURE_LEVEL *,
                                        UINT, UINT, ID3D11Device **, D3D_FEATURE_LEVEL *, ID3D11DeviceContext **);
typedef HRESULT (WINAPI *CreateFactory)(REFIID, void **);
typedef void (WINAPI *GetUnsignedBytes)(unsigned int pname, unsigned char *data);
typedef unsigned int (WINAPI *GetError)(void);

ID3D11Device *devices[OW_MOST_ATTEMPTS] = {};
uint32_t device_count = 0, lists = 0, attempts = 0, absorbed = 0, holds = 0;
bool pending = false;
double last_vi_ms = 0;

// The adapter the current GL context renders on (GL_EXT_memory_object's
// GL_DEVICE_LUID_EXT), so the warmed driver is the one an overlay will use.
bool gl_luid(LUID *out) {
    auto get = reinterpret_cast<GetUnsignedBytes>(wglGetProcAddress("glGetUnsignedBytevEXT"));
    const auto proc = reinterpret_cast<uintptr_t>(get);
    if (proc <= 3 || proc == UINTPTR_MAX) return false;
    unsigned char bytes[8] = {};
    get(0x9599 /* GL_DEVICE_LUID_EXT */, bytes);
    // Leave no error of ours in the renderer's queue.
    auto error = reinterpret_cast<GetError>(GetProcAddress(GetModuleHandleW(L"opengl32.dll"), "glGetError"));
    bool clean = true;
    if (error) for (int i = 0; i < 8 && error() != 0; ++i) clean = false;
    memcpy(out, bytes, sizeof(*out));
    return clean && (out->LowPart || out->HighPart);
}

IDXGIAdapter *adapter_for(HMODULE dxgi, const LUID &luid) {
    auto create = reinterpret_cast<CreateFactory>(GetProcAddress(dxgi, "CreateDXGIFactory1"));
    IDXGIFactory1 *factory = nullptr;
    if (!create || FAILED(create(__uuidof(IDXGIFactory1), reinterpret_cast<void **>(&factory)))) return nullptr;
    IDXGIAdapter *found = nullptr, *adapter = nullptr;
    for (UINT index = 0; !found && factory->EnumAdapters(index, &adapter) != DXGI_ERROR_NOT_FOUND; ++index) {
        DXGI_ADAPTER_DESC desc{};
        if (SUCCEEDED(adapter->GetDesc(&desc)) && desc.AdapterLuid.LowPart == luid.LowPart
                && desc.AdapterLuid.HighPart == luid.HighPart) found = adapter;
        else adapter->Release();
    }
    factory->Release();
    return found;
}

int make_device() {
    if (device_count >= OW_MOST_ATTEMPTS) return 0;
    HMODULE d3d11 = LoadLibraryW(L"d3d11.dll"), dxgi = LoadLibraryW(L"dxgi.dll");
    auto create = d3d11 ? reinterpret_cast<CreateDevice>(GetProcAddress(d3d11, "D3D11CreateDevice")) : nullptr;
    if (!create) return 0;
    LUID luid{};
    IDXGIAdapter *adapter = dxgi && gl_luid(&luid) ? adapter_for(dxgi, luid) : nullptr;
    ID3D11Device *device = nullptr;
    ID3D11DeviceContext *context = nullptr;
    const HRESULT made = create(adapter, adapter ? D3D_DRIVER_TYPE_UNKNOWN : D3D_DRIVER_TYPE_HARDWARE, nullptr, 0,
                                nullptr, 0, D3D11_SDK_VERSION, &device, nullptr, &context);
    if (adapter) adapter->Release();
    if (context) context->Release();
    if (FAILED(made) || !device) return 0;
    devices[device_count++] = device; // kept for the life of the process, never used
    return 1;
}
int (*maker)(void) = make_device;
}

extern "C" void ow_list(void) { ++lists; holds = 0; }

extern "C" int ow_vi(void) {
    pending = false;
    if (++holds != OW_HOLD_VIS || lists < OW_SETTLE_LISTS || absorbed || attempts >= OW_MOST_ATTEMPTS) return 0;
    ++attempts;
    pending = maker() != 0;
    return pending ? 1 : 0;
}

extern "C" void ow_vi_done(double milliseconds) {
    if (!pending) return;
    pending = false;
    last_vi_ms = milliseconds;
    if (milliseconds >= OW_ABSORBED_MS) absorbed = 1;
}

extern "C" void ow_reset(void) { lists = holds = 0; pending = false; }

extern "C" ow_stats ow_read(void) {
    ow_stats out{};
    out.lists = lists; out.attempts = attempts; out.devices = device_count; out.absorbed = absorbed;
    out.last_vi_ms = last_vi_ms;
    return out;
}

extern "C" void ow_set_maker(int (*value)(void)) { maker = value ? value : make_device; }
