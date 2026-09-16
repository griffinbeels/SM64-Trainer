#include "gpu_bridge_wire.h"
#ifdef GPU_BRIDGE_NVENC
#include "gpu_bridge_encoder.h"
#include "gpu_bridge_test_sink.h"
#endif
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>
#define REQUIRE(x) do{if(!(x)){fprintf(stderr,"consumer contract line %d: %s\n",__LINE__,#x);fflush(stderr);return 1;}}while(0)
int wmain(int argc,wchar_t**argv) {
    #ifdef GPU_BRIDGE_LARGE
    constexpr unsigned width=320,height=240;
    #else
    constexpr unsigned width=8,height=6;
    #endif
    REQUIRE(argc==2);wchar_t name[128];swprintf_s(name,L"%s-control",argv[1]);
    HANDLE mapping=nullptr;
    for(unsigned n=0;n<10000&&!mapping;++n){mapping=OpenFileMappingW(FILE_MAP_ALL_ACCESS,FALSE,name);if(!mapping)Sleep(1);}
    REQUIRE(mapping);
    auto*ctl=static_cast<BridgeControl*>(MapViewOfFile(mapping,FILE_MAP_ALL_ACCESS,0,0,sizeof(BridgeControl)));REQUIRE(ctl);
    for(unsigned n=0;n<10000&&!InterlockedCompareExchange(&ctl->initialized,0,0);++n)Sleep(1);
    REQUIRE(ctl->initialized && ctl->config.width==width && ctl->config.height==height);
    Microsoft::WRL::ComPtr<ID3D11Device> device;Microsoft::WRL::ComPtr<ID3D11DeviceContext> context;
    LUID absent{0xfedcba98,LONG(0x76543210)};
    REQUIRE(!gpu_bridge::device_for_luid(absent,&device,&context));
    REQUIRE(!device && !context);
    REQUIRE(gpu_bridge::device_for_luid(ctl->config.adapter,&device,&context));
    Microsoft::WRL::ComPtr<ID3D11Device1> device1;REQUIRE(SUCCEEDED(device.As(&device1)));
    Microsoft::WRL::ComPtr<ID3D11Texture2D> textures[2],readback;
    Microsoft::WRL::ComPtr<IDXGIKeyedMutex> mutex[2];
    for(unsigned i=0;i<2;++i) {
        REQUIRE(SUCCEEDED(device1->OpenSharedResourceByName(ctl->config.names[i],DXGI_SHARED_RESOURCE_READ|DXGI_SHARED_RESOURCE_WRITE,IID_PPV_ARGS(&textures[i]))));
        REQUIRE(SUCCEEDED(textures[i].As(&mutex[i])));
        // Initial shared-resource ownership belongs to key zero; key one must refuse.
        REQUIRE(mutex[i]->AcquireSync(1,0)==WAIT_TIMEOUT);
    }
    #ifdef GPU_BRIDGE_NVENC
    BridgeTestFileSink file_sink;REQUIRE(file_sink.open("witness.h264","packets.csv"));
    const auto encoder_settings=bridge_test_options(width,height);
    BridgeEncoder encoder;REQUIRE(encoder.prepare(device.Get(),context.Get(),encoder_settings,file_sink.descriptor()));
    #endif
    D3D11_TEXTURE2D_DESC desc{};textures[0]->GetDesc(&desc);
    REQUIRE(desc.Format==DXGI_FORMAT_R8G8B8A8_UNORM && desc.Width==width && desc.Height==height);
    desc.Usage=D3D11_USAGE_STAGING;desc.BindFlags=0;desc.MiscFlags=0;desc.CPUAccessFlags=D3D11_CPU_ACCESS_READ;
    REQUIRE(SUCCEEDED(device->CreateTexture2D(&desc,nullptr,&readback)));
    swprintf_s(name,L"%s-attached",argv[1]);HANDLE attached=OpenEventW(EVENT_MODIFY_STATE,FALSE,name);REQUIRE(attached);
    swprintf_s(name,L"%s-allow",argv[1]);HANDLE allow=OpenEventW(SYNCHRONIZE,FALSE,name);REQUIRE(allow);
    SetEvent(attached);REQUIRE(WaitForSingleObject(allow,10000)==WAIT_OBJECT_0);
    for(unsigned occurrence=1;occurrence<=6;++occurrence) {
        const unsigned slot=(occurrence-1)%2;auto&packet=ctl->packets[slot];
        for(unsigned n=0;n<10000&&!InterlockedCompareExchange(&packet.ready,0,0);++n)Sleep(1);
        REQUIRE(packet.ready==1 && packet.occurrence==occurrence && packet.qpc==occurrence*101);
        HRESULT acquire=WAIT_TIMEOUT;
        for(unsigned n=0;n<10000&&acquire==WAIT_TIMEOUT;++n){acquire=mutex[slot]->AcquireSync(1,0);if(acquire==WAIT_TIMEOUT)Sleep(1);}
        REQUIRE(acquire==S_OK);
        #ifdef GPU_BRIDGE_NVENC
        const uint64_t pts[6]={0,3001,3002,90000,91000,180000},durations[6]={3001,1,86998,1000,89000,9000};
        const bool force_idr=occurrence==1||occurrence==4||occurrence==6;
        REQUIRE(encoder.encode(textures[slot].Get(),occurrence,pts[occurrence-1],durations[occurrence-1],force_idr));
        #endif
        // Test-only oracle. No CPU pixels in producer or bridge; all bytes arrive
        // from the opened shared texture, independently checked against rendered markers.
        context->CopyResource(readback.Get(),textures[slot].Get());
        D3D11_MAPPED_SUBRESOURCE data{};
        REQUIRE(SUCCEEDED(context->Map(readback.Get(),0,D3D11_MAP_READ,0,&data)));
        unsigned errors=0;
        for(unsigned y=0;y<height;++y)for(unsigned x=0;x<width;++x) {
            const auto*p=static_cast<const unsigned char*>(data.pData)+y*data.RowPitch+x*4;
            // Snapshot rectangle begins at native (1,2); transport is top-down.
            const unsigned sx=x+1,sy=height-1-y+2;
            const unsigned expected[4]={(occurrence*29+sx*11+sy*3+17)&255,
              (occurrence*13+sx*7+sy*23+41)&255,(occurrence*19+sx*31+sy*5+71)&255,255};
            for(unsigned c=0;c<4;++c)if(p[c]!=expected[c]){
                if(errors==0)fprintf(stderr,"pixel mismatch occurrence=%u x=%u y=%u c=%u actual=%u expected=%u\n",occurrence,x,y,c,p[c],expected[c]);++errors;
            }
        }
        context->Unmap(readback.Get(),0);
        REQUIRE(SUCCEEDED(mutex[slot]->ReleaseSync(0)));
        InterlockedExchange(&packet.ready,0);
        if(errors){InterlockedExchange(&ctl->failed,1);return 1;}
        InterlockedIncrement(&ctl->consumed);
    }
    #ifdef GPU_BRIDGE_NVENC
    REQUIRE(encoder.close());REQUIRE(file_sink.close());
    REQUIRE(encoder.submitted()==6 && encoder.completed()==6 && encoder.delivered()==6);
    printf("drained submitted=%llu completed=%llu eos=yes\n",encoder.submitted(),encoder.completed());
    #endif
    printf("gpu-bridge consumer passed: bits=%u pictures=6 bytes_checked=%u luid=%08lx:%08lx\n",
        unsigned(sizeof(void*)*8),width*height*4*6,DWORD(ctl->config.adapter.HighPart),ctl->config.adapter.LowPart);
    CloseHandle(allow);CloseHandle(attached);UnmapViewOfFile(ctl);CloseHandle(mapping);return 0;
}
