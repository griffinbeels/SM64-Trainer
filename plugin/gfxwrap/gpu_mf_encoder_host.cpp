#include "gpu_mf_encoder.h"
#include <dxgi1_2.h>
#include <wrl/client.h>
#include <stdio.h>
#include <vector>
#include <string.h>
#pragma comment(lib,"d3d11.lib")
#pragma comment(lib,"dxgi.lib")
#pragma comment(lib,"mfplat.lib")
#pragma comment(lib,"mfuuid.lib")
#pragma comment(lib,"mf.lib")
#pragma comment(lib,"ole32.lib")
#pragma comment(lib,"oleaut32.lib")
using Microsoft::WRL::ComPtr;
#ifdef GPU_MF_USE_DLL
#include "gpu_mf_dll_witness.h"
const char* mf_witness_dll=nullptr;
using WitnessEncoder=MfDllWitness;
#else
using WitnessEncoder=MfEncoder;
#endif
int GBENC_CALL packet(void*opaque,const gbenc_packet_v1*p){
    FILE*f=(FILE*)opaque;if(fwrite(p->data,1,p->bytes,f)!=p->bytes)return GBENC_SINK_REJECTED;
    printf("packet,%llu,%llu,%llu,%u,%u\n",p->occurrence,p->pts,p->duration,p->flags,p->bytes);return GBENC_SINK_ACCEPTED;
}
int main(int argc,char**argv){
#ifdef GPU_MF_USE_DLL
    if(argc<4)return 9;mf_witness_dll=argv[3];
#endif
    const UINT vendor=argc>1?strtoul(argv[1],nullptr,16):0x1002;
    const bool av1=argc>2&&!strcmp(argv[2],"av1"),bgra=argc>2&&!strcmp(argv[2],"bgra");
    ComPtr<IDXGIFactory1> factory;if(FAILED(CreateDXGIFactory1(IID_PPV_ARGS(&factory))))return 1;
    ComPtr<ID3D11Device> device;ComPtr<ID3D11DeviceContext> context;
    for(UINT i=0;;++i){ComPtr<IDXGIAdapter1>a;if(factory->EnumAdapters1(i,&a)==DXGI_ERROR_NOT_FOUND)break;
        DXGI_ADAPTER_DESC1 d{};a->GetDesc1(&d);if(d.VendorId!=vendor)continue;
        printf("adapter=%ls vendor=%04x luid=%08x:%08x\n",d.Description,d.VendorId,d.AdapterLuid.HighPart,d.AdapterLuid.LowPart);
        HRESULT hr=D3D11CreateDevice(a.Get(),D3D_DRIVER_TYPE_UNKNOWN,nullptr,D3D11_CREATE_DEVICE_BGRA_SUPPORT|D3D11_CREATE_DEVICE_VIDEO_SUPPORT,nullptr,0,D3D11_SDK_VERSION,&device,nullptr,&context);
        if(FAILED(hr)){printf("device=%08x\n",unsigned(hr));return 2;}break;}
    if(!device)return 77;
    FILE*f=nullptr;fopen_s(&f,av1?"witness.av1":"witness.h264","wb");if(!f)return 3;
    MfEncoderSettings settings{};auto&s=settings.picture;s.struct_size=sizeof(s);s.version=1;s.width=320;s.height=240;
    s.nominal_fps_num=30;s.nominal_fps_den=1;s.profile=100;s.gop_frames=120;
    s.input_format=GBENC_INPUT_RGBA8;s.signal_color=1;s.matrix=5;s.primaries=2;s.transfer=2;s.idr_interval_ticks=90000;s.max_packet_bytes=1<<20;settings.quality=90;s.backend_quality=90;s.backend=GBENC_BACKEND_WINDOWS;
    if(bgra)s.input_format=GBENC_INPUT_BGRA8;
    if(av1){s.codec=GBENC_CODEC_AV1;s.profile=GBENC_AV1_MAIN;}
    if(argc>2&&!strcmp(argv[2],"bitrate-hint"))s.max_bitrate=20000000;
    gbenc_sink_v1 sink{sizeof(sink),1,packet,f};WitnessEncoder encoder;
    if(!encoder.prepare(device.Get(),context.Get(),settings,sink)){printf("prepare state=%u error=%u hr=%08x\n",encoder.state(),encoder.error(),encoder.driver_error());fclose(f);return 4;}
    printf("prepared D3D11->NV12->hardwareMF noCPUreadback=yes\n");
    D3D11_TEXTURE2D_DESC d{};d.Width=320;d.Height=240;d.MipLevels=1;d.ArraySize=1;d.Format=bgra?DXGI_FORMAT_B8G8R8A8_UNORM:DXGI_FORMAT_R8G8B8A8_UNORM;d.SampleDesc.Count=1;d.Usage=D3D11_USAGE_DEFAULT;d.BindFlags=D3D11_BIND_RENDER_TARGET;
    ComPtr<ID3D11Texture2D> texture;if(FAILED(device->CreateTexture2D(&d,nullptr,&texture)))return 5;
    std::vector<UINT> pixels(320*240);uint64_t pts[]={0,3001,3002,90000,91000,180000};uint64_t duration[]={3001,1,86998,1000,89000,9000};
    for(UINT i=0;i<6;++i){for(UINT y=0;y<240;++y)for(UINT x=0;x<320;++x){
        UINT r=(i*31+17+x/40*13)&255,g=(y/30*25+33+i*11)&255,b=(x/40*17+y/30*7+i*19+70)&255;pixels[y*320+x]=(bgra?b:r)|(g<<8)|((bgra?r:b)<<16)|0xff000000u;}
        context->UpdateSubresource(texture.Get(),0,nullptr,pixels.data(),320*4,0);
        const bool ok=i==4?encoder.encode_retained(encoder.retained_generation(),i+1,pts[i],duration[i]):encoder.encode(texture.Get(),i+1,pts[i],duration[i]);
        if(!ok){printf("encode i=%u state=%u error=%u hr=%08x submitted=%llu completed=%llu\n",i,encoder.state(),encoder.error(),encoder.driver_error(),encoder.submitted(),encoder.completed());fflush(f);fclose(f);return 6;}
        // A stale generation may never alias the newly selected texture. It
        // refuses without changing the ready session, transport or counters.
        if(i&&i!=4&&encoder.encode_retained(encoder.retained_generation()-1,900,pts[i]+1,1))return 8;
    }
    bool close=encoder.close();printf("closed=%u submitted=%llu completed=%llu copies=%llu repeats=%llu\n",close,encoder.submitted(),encoder.completed(),encoder.source_copies(),encoder.repeats());fclose(f);return close?0:7;
}
