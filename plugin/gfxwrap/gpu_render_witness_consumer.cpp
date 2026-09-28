/* Test-only x64 consumer for the renderer-thread capture witnesses. It plays
 * the encoder helper: it creates the two keyed-mutex textures under the
 * producer's names (as gpu_encoder_dll's open does), takes key 1, checks every
 * pixel against the independent fixture (or dumps the first picture), encodes
 * with the shipped NVENC encoder when built with RENDER_WITNESS_NVENC, and
 * returns key 0.   usage: <name> verify|dump */
#include "gpu_render_wire.h"
#include <d3d11_1.h>
#include <dxgi1_2.h>
#include <wrl/client.h>
#ifdef RENDER_WITNESS_NVENC
#include "gpu_bridge_encoder.h"
#include "gpu_bridge_test_sink.h"
#endif
#include <stdio.h>
#include <string.h>
#include <wchar.h>
#define REQUIRE(x) do{if(!(x)){fprintf(stderr,"consumer contract line %d: %s\n",__LINE__,#x);fflush(stderr);return 1;}}while(0)
using Microsoft::WRL::ComPtr;
static bool device_for_luid(LUID luid, ID3D11Device **device, ID3D11DeviceContext **context) {
    ComPtr<IDXGIFactory1> factory; if (FAILED(CreateDXGIFactory1(IID_PPV_ARGS(&factory)))) return false;
    for (unsigned i = 0; i < 32; ++i) {
        ComPtr<IDXGIAdapter1> adapter; if (factory->EnumAdapters1(i, &adapter) != S_OK) break;
        DXGI_ADAPTER_DESC1 desc{}; if (FAILED(adapter->GetDesc1(&desc)) || memcmp(&desc.AdapterLuid, &luid, sizeof luid)) continue;
        return SUCCEEDED(D3D11CreateDevice(adapter.Get(), D3D_DRIVER_TYPE_UNKNOWN, nullptr, 0, nullptr, 0,
                                           D3D11_SDK_VERSION, device, nullptr, context));
    }
    return false;
}
int wmain(int argc, wchar_t **argv) {
    REQUIRE(argc == 3 && (!wcscmp(argv[2], L"verify") || !wcscmp(argv[2], L"dump")));
    const bool dump = !wcscmp(argv[2], L"dump");
    wchar_t name[160]; swprintf_s(name, L"%s-control", argv[1]);
    HANDLE mapping = nullptr;
    for (unsigned n = 0; n < 10000 && !mapping; ++n) { mapping = OpenFileMappingW(FILE_MAP_ALL_ACCESS, FALSE, name); if (!mapping) Sleep(1); }
    REQUIRE(mapping);
    auto *ctl = static_cast<RenderWitnessControl *>(MapViewOfFile(mapping, FILE_MAP_ALL_ACCESS, 0, 0, sizeof(RenderWitnessControl)));
    REQUIRE(ctl);
    for (unsigned n = 0; n < 10000 && !InterlockedCompareExchange(&ctl->initialized, 0, 0); ++n) Sleep(1);
    REQUIRE(ctl->initialized && ctl->width >= 2 && ctl->height >= 2 && ctl->pictures >= 1);
    const unsigned width = ctl->width, height = ctl->height;
    ComPtr<ID3D11Device> device; ComPtr<ID3D11DeviceContext> context;
    REQUIRE(device_for_luid(ctl->adapter, &device, &context));
    D3D11_TEXTURE2D_DESC desc{}; desc.Width = width; desc.Height = height; desc.MipLevels = 1; desc.ArraySize = 1;
    desc.Format = DXGI_FORMAT_R8G8B8A8_UNORM; desc.SampleDesc.Count = 1; desc.Usage = D3D11_USAGE_DEFAULT;
    desc.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;
    desc.MiscFlags = D3D11_RESOURCE_MISC_SHARED_NTHANDLE | D3D11_RESOURCE_MISC_SHARED_KEYEDMUTEX;
    ComPtr<ID3D11Texture2D> textures[2], staging; ComPtr<IDXGIKeyedMutex> mutex[2]; HANDLE shares[2]{};
    for (unsigned i = 0; i < 2; ++i) {
        REQUIRE(SUCCEEDED(device->CreateTexture2D(&desc, nullptr, &textures[i])) && SUCCEEDED(textures[i].As(&mutex[i])));
        ComPtr<IDXGIResource1> resource; REQUIRE(SUCCEEDED(textures[i].As(&resource)));
        REQUIRE(SUCCEEDED(resource->CreateSharedHandle(nullptr, DXGI_SHARED_RESOURCE_READ | DXGI_SHARED_RESOURCE_WRITE,
                                                       ctl->names[i], &shares[i])));
        REQUIRE(mutex[i]->AcquireSync(1, 0) == WAIT_TIMEOUT); // a new texture starts at key 0
    }
    desc.Usage = D3D11_USAGE_STAGING; desc.BindFlags = 0; desc.MiscFlags = 0; desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    REQUIRE(SUCCEEDED(device->CreateTexture2D(&desc, nullptr, &staging)));
#ifdef RENDER_WITNESS_NVENC
#ifdef RENDER_WITNESS_AV1
    constexpr uint32_t codec = GBENC_CODEC_AV1; const char *path = "witness.av1";
#else
    constexpr uint32_t codec = GBENC_CODEC_H264; const char *path = "witness.h264";
#endif
    REQUIRE(ctl->pictures == 6);
    BridgeTestFileSink sink; REQUIRE(sink.open(path, "packets.csv"));
    BridgeEncoder encoder; REQUIRE(encoder.prepare(device.Get(), context.Get(), bridge_test_options(width, height, codec), sink.descriptor()));
#endif
    InterlockedExchange(&ctl->textures_ready, 1);
    unsigned checked = 0;
    for (unsigned occurrence = 1; occurrence <= ctl->pictures; ++occurrence) {
        const unsigned index = (occurrence - 1) % 2; auto &packet = ctl->packets[index];
        for (unsigned n = 0; n < 10000 && !InterlockedCompareExchange(&packet.ready, 0, 0); ++n) Sleep(1);
        REQUIRE(packet.ready == 1 && packet.occurrence == occurrence && packet.qpc == int64_t(occurrence) * 101);
        HRESULT acquired = WAIT_TIMEOUT;
        for (unsigned n = 0; n < 10000 && acquired == WAIT_TIMEOUT; ++n) { acquired = mutex[index]->AcquireSync(1, 0); if (acquired == WAIT_TIMEOUT) Sleep(1); }
        REQUIRE(acquired == S_OK);
#ifdef RENDER_WITNESS_NVENC
        const uint64_t pts[6] = {0, 3001, 3002, 90000, 91000, 180000}, durations[6] = {3001, 1, 86998, 1000, 89000, 9000};
        const bool idr = occurrence == 1 || occurrence == 4 || occurrence == 6;
        REQUIRE(encoder.encode(textures[index].Get(), occurrence, pts[occurrence - 1], durations[occurrence - 1], idr));
#endif
        context->CopyResource(staging.Get(), textures[index].Get());
        D3D11_MAPPED_SUBRESOURCE data{}; REQUIRE(SUCCEEDED(context->Map(staging.Get(), 0, D3D11_MAP_READ, 0, &data)));
        unsigned errors = 0;
        if (dump) {
            FILE *file = _wfopen(L"delivered.rgba", L"wb"); REQUIRE(file);
            for (unsigned y = 0; y < height; ++y)
                REQUIRE(fwrite(static_cast<const unsigned char *>(data.pData) + size_t(y) * data.RowPitch, 1, size_t(width) * 4, file) == size_t(width) * 4);
            REQUIRE(fclose(file) == 0);
        } else {
            for (unsigned y = 0; y < height; ++y) for (unsigned x = 0; x < width; ++x) {
                const auto *p = static_cast<const unsigned char *>(data.pData) + size_t(y) * data.RowPitch + size_t(x) * 4;
                // Top-down output row y is snapshot row (snapshot_height-1-y): an odd
                // snapshot loses its native row 0, an odd width its right column.
                const unsigned sx = ctl->crop_x + x, sy = ctl->crop_y + ctl->snapshot_height - 1 - y;
                for (unsigned c = 0; c < 3; ++c) if (p[c] != witness_channel(occurrence, sx, sy, c)) {
                    if (!errors) fprintf(stderr, "pixel mismatch occurrence=%u x=%u y=%u c=%u actual=%u expected=%u\n",
                                         occurrence, x, y, c, p[c], witness_channel(occurrence, sx, sy, c));
                    ++errors;
                }
            }
            checked += width * height * 4;
        }
        context->Unmap(staging.Get(), 0);
        REQUIRE(SUCCEEDED(mutex[index]->ReleaseSync(0)));
        InterlockedExchange(&packet.ready, 0);
        if (errors) { InterlockedExchange(&ctl->failed, 1); return 1; }
        InterlockedIncrement(&ctl->consumed);
    }
#ifdef RENDER_WITNESS_NVENC
    REQUIRE(encoder.close() && sink.close());
    REQUIRE(encoder.submitted() == 6 && encoder.completed() == 6 && encoder.delivered() == 6);
    printf("drained submitted=%llu completed=%llu eos=yes\n", encoder.submitted(), encoder.completed());
#endif
    printf("consumer passed: bits=%u pictures=%u bytes_checked=%u output=%ux%u created_textures=yes\n",
           unsigned(sizeof(void *) * 8), ctl->pictures, checked, width, height);
    for (auto share : shares) CloseHandle(share);
    UnmapViewOfFile(ctl); CloseHandle(mapping); return 0;
}
