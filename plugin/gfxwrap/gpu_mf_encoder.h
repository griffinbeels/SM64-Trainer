/* Windows hardware encoder. D3D11 pictures never enter system memory. */
#pragma once
#include <windows.h>
#include <d3d11.h>
#include <stdint.h>
#include "gpu_bridge_encoder_api.h"
struct MfEncoderSettings { gbenc_options_v1 picture; uint32_t quality; };
class MfEncoder {
    struct Impl; Impl* impl_=nullptr;
    gbenc_state state_=GBENC_EMPTY;gbenc_error_code error_=GBENC_OK;HRESULT hr_=S_OK;
    DWORD owner_=0;
    uint64_t submitted_=0,completed_=0,delivered_=0,source_copies_=0,repeats_=0;
    uint64_t generation_=0,selected_serial_=0,last_serial_=0,last_pts_=0,time_forced_=0;
    bool retained_=false,have_pts_=false;
    bool fail(gbenc_error_code,HRESULT,bool quarantine=false);
    bool submit(uint64_t,uint64_t,uint64_t,bool);
public:
    MfEncoder()=default;~MfEncoder();
    MfEncoder(const MfEncoder&)=delete;MfEncoder&operator=(const MfEncoder&)=delete;
    bool prepare(ID3D11Device*,ID3D11DeviceContext*,const MfEncoderSettings&,const gbenc_sink_v1&);
    bool encode(ID3D11Texture2D*,uint64_t,uint64_t,uint64_t,bool=false);
    bool encode_retained(uint64_t,uint64_t,uint64_t,uint64_t,bool=false);
    bool close();
    void invalidate_retained(){retained_=false;}
    bool retained_valid()const{return retained_&&state_==GBENC_READY;}
    uint64_t retained_generation()const{return generation_;}
    uint64_t retained_serial()const{return selected_serial_;}
    uint64_t source_copies()const{return source_copies_;}
    uint64_t repeats()const{return repeats_;}
    uint64_t last_serial()const{return last_serial_;}
    gbenc_state state()const{return state_;}gbenc_error_code error()const{return error_;}
    uint32_t driver_error()const{return uint32_t(hr_);}
    uint64_t submitted()const{return submitted_;}uint64_t completed()const{return completed_;}
    uint64_t delivered()const{return delivered_;}
};
