/* Same executable/content/options for old-vs-new helper timing. Isolated only. */
#include "gpu_mf_encoder.h"
#include <d3d11_1.h>
#include <dxgi1_2.h>
#include <wrl/client.h>
#include <stdio.h>
#include <vector>
#include "gpu_mf_dll_witness.h"
#pragma comment(lib,"d3d11.lib")
#pragma comment(lib,"dxgi.lib")
using Microsoft::WRL::ComPtr;
const char* mf_witness_dll=nullptr;
struct Packets {uint64_t count=0,bytes=0;};
int GBENC_CALL receive(void*opaque,const gbenc_packet_v1*p){
    auto&seen=*static_cast<Packets*>(opaque);
    if(p->occurrence!=seen.count+1||p->pts!=seen.count*3000||p->duration!=3000||!p->bytes)return GBENC_SINK_REJECTED;
    ++seen.count;seen.bytes+=p->bytes;return GBENC_SINK_ACCEPTED;
}
LONGLONG now(){LARGE_INTEGER value;QueryPerformanceCounter(&value);return value.QuadPart;}
int main(int argc,char**argv){
    if(argc<3)return 1;mf_witness_dll=argv[1];FILE*file=nullptr;fopen_s(&file,argv[2],"rb");if(!file)return 2;
    MfEncoderSettings settings{};size_t bytes=fread(&settings.picture,1,sizeof(settings.picture),file);const bool extra=fgetc(file)!=EOF;fclose(file);
    auto&s=settings.picture;settings.quality=s.backend_quality;
    if(bytes!=sizeof(s)||extra||s.struct_size!=sizeof(s)||s.version!=GBENC_ABI_V1||!s.width||!s.height||s.width>3840||s.height>2160||s.input_format!=GBENC_INPUT_RGBA8)return 3;
    unsigned count=argc>3?strtoul(argv[3],nullptr,10):180;if(count<30||count>1800)return 4;
    const UINT vendor=s.backend==GBENC_BACKEND_NVENC?0x10de:0x1002;
    ComPtr<IDXGIFactory1> factory;if(FAILED(CreateDXGIFactory1(IID_PPV_ARGS(&factory))))return 5;
    ComPtr<ID3D11Device> device;ComPtr<ID3D11DeviceContext> context;
    for(UINT i=0;;++i){ComPtr<IDXGIAdapter1>a;if(factory->EnumAdapters1(i,&a)==DXGI_ERROR_NOT_FOUND)break;
        DXGI_ADAPTER_DESC1 desc{};a->GetDesc1(&desc);if(desc.VendorId!=vendor)continue;
        printf("adapter=%ls vendor=%04x luid=%08x:%08x\n",desc.Description,desc.VendorId,desc.AdapterLuid.HighPart,desc.AdapterLuid.LowPart);
        if(FAILED(D3D11CreateDevice(a.Get(),D3D_DRIVER_TYPE_UNKNOWN,nullptr,D3D11_CREATE_DEVICE_BGRA_SUPPORT,nullptr,0,D3D11_SDK_VERSION,&device,nullptr,&context)))return 6;break;}
    if(!device)return 7;
    D3D11_TEXTURE2D_DESC desc{};desc.Width=s.width;desc.Height=s.height;desc.MipLevels=1;desc.ArraySize=1;
    desc.Format=DXGI_FORMAT_R8G8B8A8_UNORM;desc.SampleDesc.Count=1;desc.Usage=D3D11_USAGE_DEFAULT;desc.BindFlags=D3D11_BIND_RENDER_TARGET;
    ComPtr<ID3D11Texture2D> fixtures[4];std::vector<UINT> pixels(size_t(s.width)*s.height);
    for(unsigned f=0;f<4;++f){for(unsigned y=0;y<s.height;++y)for(unsigned x=0;x<s.width;++x){
        UINT r=(f*61+x/8*13+y/11*7+17)&255,g=(f*31+x/23*17+y/7*11+41)&255,b=(f*19+x/17*23+y/13*3+71)&255;
        pixels[size_t(y)*s.width+x]=r|(g<<8)|(b<<16)|0xff000000u;}
        D3D11_SUBRESOURCE_DATA data{pixels.data(),s.width*4,0};if(FAILED(device->CreateTexture2D(&desc,&data,&fixtures[f])))return 8;}
    Packets packets;gbenc_sink_v1 sink{sizeof(sink),GBENC_ABI_V1,receive,&packets};MfDllWitness encoder;
    LARGE_INTEGER frequency;QueryPerformanceFrequency(&frequency);LONGLONG begin=now();
    if(!encoder.prepare(device.Get(),context.Get(),settings,sink)){printf("open failed error=%u hr=%08x\n",encoder.error(),encoder.driver_error());return 9;}
    printf("open_us=%.3f width=%u height=%u codec=%u\n",double(now()-begin)*1e6/double(frequency.QuadPart),s.width,s.height,s.codec);
    for(unsigned i=0;i<count+20;++i){bool repeat=(i%15)==14;begin=now();
        bool ok=repeat?encoder.encode_retained(encoder.retained_generation(),i+1,uint64_t(i)*3000,3000):
            encoder.encode(fixtures[i%4].Get(),i+1,uint64_t(i)*3000,3000);
        const double elapsed=double(now()-begin)*1e6/double(frequency.QuadPart);
        if(!ok){printf("encode failed at=%u state=%u error=%u hr=%08x\n",i,encoder.state(),encoder.error(),encoder.driver_error());return 10;}
        if(i>=20)printf("sample,%u,%.3f,%u\n",i-20,elapsed,unsigned(repeat));
    }
    begin=now();if(!encoder.close())return 11;
    printf("close_us=%.3f packets=%llu bytes=%llu copies=%llu repeats=%llu\n",double(now()-begin)*1e6/double(frequency.QuadPart),packets.count,packets.bytes,encoder.source_copies(),encoder.repeats());return 0;
}
