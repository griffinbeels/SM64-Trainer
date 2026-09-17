#include "gpu_bridge_wire.h"
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>
#define REQUIRE(x) do{if(!(x)){fprintf(stderr,"fidelity consumer line%d: %s\n",__LINE__,#x);return 1;}}while(0)
int wmain(int argc,wchar_t**argv) {
    REQUIRE(argc==2);wchar_t name[128];swprintf_s(name,L"%s-control",argv[1]);
    HANDLE mapping=nullptr;
    for(unsigned n=0;n<10000&&!mapping;++n){mapping=OpenFileMappingW(FILE_MAP_ALL_ACCESS,FALSE,name);if(!mapping)Sleep(1);}REQUIRE(mapping);
    auto*control=static_cast<BridgeControl*>(MapViewOfFile(mapping,FILE_MAP_ALL_ACCESS,0,0,sizeof(BridgeControl)));REQUIRE(control);
    for(unsigned n=0;n<10000&&!InterlockedCompareExchange(&control->initialized,0,0);++n)Sleep(1);REQUIRE(control->initialized);
    REQUIRE(control->config.width==640&&control->config.height==480);
    Microsoft::WRL::ComPtr<ID3D11Device> device;Microsoft::WRL::ComPtr<ID3D11DeviceContext> context;
    REQUIRE(gpu_bridge::device_for_luid(control->config.adapter,&device,&context));
    Microsoft::WRL::ComPtr<ID3D11Device1> device1;REQUIRE(SUCCEEDED(device.As(&device1)));
    Microsoft::WRL::ComPtr<ID3D11Texture2D> shared,staging;
    REQUIRE(SUCCEEDED(device1->OpenSharedResourceByName(control->config.names[0],DXGI_SHARED_RESOURCE_READ|DXGI_SHARED_RESOURCE_WRITE,IID_PPV_ARGS(&shared))));
    Microsoft::WRL::ComPtr<IDXGIKeyedMutex> mutex;REQUIRE(SUCCEEDED(shared.As(&mutex)));
    D3D11_TEXTURE2D_DESC desc{};shared->GetDesc(&desc);REQUIRE(desc.Format==DXGI_FORMAT_R8G8B8A8_UNORM&&desc.Width==640&&desc.Height==480);
    desc.Usage=D3D11_USAGE_STAGING;desc.BindFlags=0;desc.MiscFlags=0;desc.CPUAccessFlags=D3D11_CPU_ACCESS_READ;
    REQUIRE(SUCCEEDED(device->CreateTexture2D(&desc,nullptr,&staging)));
    for(unsigned n=0;n<10000&&!InterlockedCompareExchange(&control->packets[0].ready,0,0);++n)Sleep(1);
    REQUIRE(control->packets[0].ready&&control->packets[0].occurrence==1&&control->packets[0].qpc==101);
    HRESULT acquired=WAIT_TIMEOUT;
    for(unsigned n=0;n<10000&&acquired==WAIT_TIMEOUT;++n){acquired=mutex->AcquireSync(1,0);if(acquired==WAIT_TIMEOUT)Sleep(1);}REQUIRE(acquired==S_OK);
    context->CopyResource(staging.Get(),shared.Get());D3D11_MAPPED_SUBRESOURCE data{};
    REQUIRE(SUCCEEDED(context->Map(staging.Get(),0,D3D11_MAP_READ,0,&data)));
    FILE*file=fopen("delivered.rgba","wb");REQUIRE(file);
    for(unsigned y=0;y<480;++y)REQUIRE(fwrite(static_cast<unsigned char*>(data.pData)+y*data.RowPitch,1,640*4,file)==640*4);
    REQUIRE(fclose(file)==0);context->Unmap(staging.Get(),0);REQUIRE(SUCCEEDED(mutex->ReleaseSync(0)));
    InterlockedExchange(&control->packets[0].ready,0);InterlockedExchange(&control->consumed,1);
    printf("GPU fidelity consumer bits=%u bytes=1228800 luid=%08lx:%08lx\n",unsigned(sizeof(void*)*8),DWORD(control->config.adapter.HighPart),control->config.adapter.LowPart);
    UnmapViewOfFile(control);CloseHandle(mapping);return 0;
}
