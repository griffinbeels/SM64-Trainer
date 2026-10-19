#include "gpu_platform_encoder.h"
bool PlatformEncoder::prepare(ID3D11Device*device,ID3D11DeviceContext*context,const gbenc_options_v1&settings,const gbenc_sink_v1&sink){
    if(chosen_)return false;
    if(settings.struct_size!=sizeof(settings)||settings.version!=GBENC_ABI_V1||
       settings.backend>GBENC_BACKEND_WINDOWS||settings.reserved[0]||settings.reserved[1]||
       (settings.backend==GBENC_BACKEND_NVENC&&settings.backend_quality)){
        validation_=GBENC_INVALID_ARGUMENT;return false;}
    validation_=GBENC_OK;
    if(settings.backend==GBENC_BACKEND_WINDOWS){
        if(!device){validation_=GBENC_INVALID_ARGUMENT;return false;}
        Microsoft::WRL::ComPtr<IDXGIDevice> dxgi;Microsoft::WRL::ComPtr<IDXGIAdapter> adapter;DXGI_ADAPTER_DESC desc{};
        if(FAILED(device->QueryInterface(IID_PPV_ARGS(&dxgi)))||FAILED(dxgi->GetAdapter(&adapter))||FAILED(adapter->GetDesc(&desc))||
           (desc.VendorId!=0x1002&&desc.VendorId!=0x8086)){validation_=GBENC_API_UNAVAILABLE;return false;}
        backend_=GBENC_BACKEND_WINDOWS;chosen_=true;
        return windows_.prepare(device,context,MfEncoderSettings{settings,settings.backend_quality},sink);
    }
    // These words were checked zero in the old ABI. Forward the identical old
    // NVENC policy and keep its encoder implementation/hot path unchanged.
    gbenc_options_v1 old=settings;old.backend=0;old.backend_quality=0;
    backend_=GBENC_BACKEND_NVENC;chosen_=true;return nvenc_.prepare(device,context,old,sink);
}
