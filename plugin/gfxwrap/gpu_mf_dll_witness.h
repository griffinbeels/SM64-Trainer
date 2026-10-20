/* Hardware witness adapter for the real shared-texture DLL entry points. */
#pragma once
#include "gpu_encoder_dll.h"
#include <wrl/client.h>
#include <d3d11_1.h>
#include <dxgi1_2.h>
extern const char* mf_witness_dll;
class MfDllWitness {
    HMODULE library_=nullptr;uint64_t token_=0;gbdll_status_v1 status_{};gbdll_retained_v1 retained_{};
    Microsoft::WRL::ComPtr<ID3D11DeviceContext> context_;
    Microsoft::WRL::ComPtr<ID3D11Texture2D> textures_[2];Microsoft::WRL::ComPtr<IDXGIKeyedMutex> keys_[2];
    decltype(&SM64GpuEncoderOpenV1) open_=nullptr;decltype(&SM64GpuEncoderSubmitV1) submit_=nullptr;
    decltype(&SM64GpuEncoderPollV1) poll_=nullptr;decltype(&SM64GpuEncoderStatusV1) status_fn_=nullptr;
    decltype(&SM64GpuEncoderCloseV1) close_=nullptr;decltype(&SM64GpuEncoderRetainedV1) retained_fn_=nullptr;
    decltype(&SM64GpuEncoderRepeatV1) repeat_=nullptr;
    bool refresh(){status_.struct_size=sizeof(status_);status_.version=GBDLL_VERSION;
        if(status_fn_(token_,&status_)!=GBDLL_OK)return false;
        retained_.struct_size=sizeof(retained_);retained_.version=GBDLL_RETAINED_VERSION;return retained_fn_(token_,&retained_)==GBDLL_OK;}
    bool drain(){ULONGLONG deadline=GetTickCount64()+1500;uint32_t result;
        while((result=poll_(token_))==GBDLL_PENDING&&GetTickCount64()<deadline)Sleep(1);
        return result==GBDLL_OK&&refresh();}
public:
    ~MfDllWitness(){if(token_)close();if(library_&&!token_)FreeLibrary(library_);}
    bool prepare(ID3D11Device*device,ID3D11DeviceContext*context,const MfEncoderSettings&s,const gbenc_sink_v1&sink){
        library_=LoadLibraryA(mf_witness_dll);if(!library_)return false;
#define MF_PROC(name,field) field=reinterpret_cast<decltype(field)>(GetProcAddress(library_,#name));if(!field)return false;
        MF_PROC(SM64GpuEncoderOpenV1,open_)MF_PROC(SM64GpuEncoderSubmitV1,submit_)MF_PROC(SM64GpuEncoderPollV1,poll_)
        MF_PROC(SM64GpuEncoderStatusV1,status_fn_)MF_PROC(SM64GpuEncoderCloseV1,close_)MF_PROC(SM64GpuEncoderRetainedV1,retained_fn_)MF_PROC(SM64GpuEncoderRepeatV1,repeat_)
#undef MF_PROC
        context_=context;Microsoft::WRL::ComPtr<IDXGIDevice> dxgi;Microsoft::WRL::ComPtr<IDXGIAdapter> adapter;
        DXGI_ADAPTER_DESC desc{};if(FAILED(device->QueryInterface(IID_PPV_ARGS(&dxgi)))||FAILED(dxgi->GetAdapter(&adapter))||FAILED(adapter->GetDesc(&desc)))return false;
        gbdll_config_v1 config{};config.struct_size=sizeof(config);config.version=GBDLL_VERSION;
        config.adapter_low=desc.AdapterLuid.LowPart;config.adapter_high=desc.AdapterLuid.HighPart;config.slot_count=2;config.encoder=s.picture;config.sink=sink;
        for(unsigned i=0;i<2;++i)swprintf_s(reinterpret_cast<wchar_t*>(config.names[i]),GBDLL_NAME_UNITS,L"Local\\sm64-mf-witness-%u-%u",GetCurrentProcessId(),i);
        uint32_t result=open_(&config,&token_);if(result!=GBDLL_OK){printf("dll open result=%u\n",result);return false;}
        Microsoft::WRL::ComPtr<ID3D11Device1> device1;if(FAILED(device->QueryInterface(IID_PPV_ARGS(&device1))))return false;
        for(unsigned i=0;i<2;++i){if(FAILED(device1->OpenSharedResourceByName(reinterpret_cast<wchar_t*>(config.names[i]),DXGI_SHARED_RESOURCE_READ|DXGI_SHARED_RESOURCE_WRITE,IID_PPV_ARGS(&textures_[i])))||FAILED(textures_[i].As(&keys_[i])))return false;}
        printf("realDLL shared textures exactLUID=yes backend=%u\n",config.encoder.backend);return refresh();
    }
    bool encode(ID3D11Texture2D*source,uint64_t occurrence,uint64_t pts,uint64_t duration,bool idr=false){
        unsigned slot=unsigned(occurrence&1);if(keys_[slot]->AcquireSync(0,1000)!=S_OK)return false;
        context_->CopyResource(textures_[slot].Get(),source);context_->Flush();if(FAILED(keys_[slot]->ReleaseSync(1)))return false;
        gbdll_picture_v1 picture{sizeof(picture),GBDLL_VERSION,slot,idr?GBDLL_FORCE_IDR:0,occurrence,pts,duration};
        uint32_t result=submit_(token_,&picture);refresh();if(result!=GBDLL_OK)return false;return drain();
    }
    bool encode_retained(uint64_t generation,uint64_t occurrence,uint64_t pts,uint64_t duration,bool idr=false){
        gbdll_repeat_v1 picture{sizeof(picture),GBDLL_RETAINED_VERSION,idr?GBDLL_FORCE_IDR:0,0,generation,occurrence,pts,duration};
        uint32_t result=repeat_(token_,&picture);refresh();return result==GBDLL_OK;
    }
    bool close(){if(!token_)return true;if(!drain())return false;
        status_.struct_size=sizeof(status_);status_.version=GBDLL_VERSION;
        uint32_t result=close_(token_,&status_);if(result!=GBDLL_OK)return false;token_=0;return true;}
    uint64_t retained_generation()const{return retained_.generation;}
    uint32_t state()const{return status_.encoder_state;}uint32_t error()const{return status_.encoder_error;}
    uint32_t driver_error()const{return status_.nv_status;}
    uint64_t submitted()const{return status_.submitted;}uint64_t completed()const{return status_.completed;}
    uint64_t source_copies()const{return retained_.source_copies;}uint64_t repeats()const{return retained_.repeats;}
};
