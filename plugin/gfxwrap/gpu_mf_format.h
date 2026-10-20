/* Windows media types are a negotiation contract, separate from NVENC tuning. */
#pragma once
#include "gpu_bridge_encoder_api.h"
#include <mfapi.h>
#include <mferror.h>
#include <codecapi.h>
#include <wrl/client.h>

namespace mf_gpu {
// H.264's media type requires a positive average bitrate even in quality mode.
// Quality mode ignores it for rate control; it is neither a target nor a cap.
constexpr UINT32 media_type_bitrate_hint = 8000000;
inline HRESULT media_type(IMFMediaType**out, const GUID&subtype,
                          const gbenc_options_v1&s, bool encoded) {
    if(!out)return E_POINTER;*out=nullptr;
    Microsoft::WRL::ComPtr<IMFMediaType> t;
    HRESULT hr=MFCreateMediaType(&t);if(FAILED(hr))return hr;
#define MF_TYPE_SET(call) do{hr=(call);if(FAILED(hr))return hr;}while(0)
    MF_TYPE_SET(t->SetGUID(MF_MT_MAJOR_TYPE,MFMediaType_Video));
    MF_TYPE_SET(t->SetGUID(MF_MT_SUBTYPE,subtype));
    MF_TYPE_SET(MFSetAttributeSize(t.Get(),MF_MT_FRAME_SIZE,s.width,s.height));
    MF_TYPE_SET(MFSetAttributeRatio(t.Get(),MF_MT_FRAME_RATE,s.nominal_fps_num,s.nominal_fps_den));
    MF_TYPE_SET(MFSetAttributeRatio(t.Get(),MF_MT_PIXEL_ASPECT_RATIO,1,1));
    MF_TYPE_SET(t->SetUINT32(MF_MT_INTERLACE_MODE,MFVideoInterlace_Progressive));
    if(s.signal_color){
        MF_TYPE_SET(t->SetUINT32(MF_MT_VIDEO_NOMINAL_RANGE,s.full_range?MFNominalRange_0_255:MFNominalRange_16_235));
        MF_TYPE_SET(t->SetUINT32(MF_MT_YUV_MATRIX,s.matrix==1?MFVideoTransferMatrix_BT709:MFVideoTransferMatrix_BT601));
    }
    if(encoded){
        MF_TYPE_SET(t->SetUINT32(MF_MT_AVG_BITRATE,media_type_bitrate_hint));
        MF_TYPE_SET(t->SetUINT32(MF_MT_MPEG2_PROFILE,s.codec==GBENC_CODEC_AV1?eAVEncAV1VProfile_Main_420_8:s.profile));
    }
#undef MF_TYPE_SET
    *out=t.Detach();return S_OK;
}

// The archive has one pinned sample description. A control/configuration event
// cannot silently change the pictures, profile, clock, orientation or color.
inline HRESULT compatible_type(IMFMediaType*t,const gbenc_options_v1&s) {
    if(!t)return E_POINTER;
    GUID value{};UINT32 a=0,b=0;
    if(FAILED(t->GetGUID(MF_MT_MAJOR_TYPE,&value))||value!=MFMediaType_Video||
       FAILED(t->GetGUID(MF_MT_SUBTYPE,&value))||value!=(s.codec==GBENC_CODEC_AV1?MFVideoFormat_AV1:MFVideoFormat_H264)||
       FAILED(MFGetAttributeSize(t,MF_MT_FRAME_SIZE,&a,&b))||a!=s.width||b!=s.height||
       FAILED(MFGetAttributeRatio(t,MF_MT_FRAME_RATE,&a,&b))||
           uint64_t(a)*s.nominal_fps_den!=uint64_t(b)*s.nominal_fps_num||
       FAILED(t->GetUINT32(MF_MT_INTERLACE_MODE,&a))||a!=MFVideoInterlace_Progressive||
       FAILED(t->GetUINT32(MF_MT_MPEG2_PROFILE,&a))||a!=(s.codec==GBENC_CODEC_AV1?eAVEncAV1VProfile_Main_420_8:s.profile))return MF_E_INVALIDMEDIATYPE;
    if(s.signal_color&&
       (FAILED(t->GetUINT32(MF_MT_VIDEO_NOMINAL_RANGE,&a))||a!=UINT32(s.full_range?MFNominalRange_0_255:MFNominalRange_16_235)||
        FAILED(t->GetUINT32(MF_MT_YUV_MATRIX,&a))||a!=UINT32(s.matrix==1?MFVideoTransferMatrix_BT709:MFVideoTransferMatrix_BT601)))return MF_E_INVALIDMEDIATYPE;
    // A driver may omit an optional square-pixel attribute, but may not change it.
    const HRESULT aspect=MFGetAttributeRatio(t,MF_MT_PIXEL_ASPECT_RATIO,&a,&b);
    if(aspect!=MF_E_ATTRIBUTENOTFOUND&&(FAILED(aspect)||!b||a!=b))return MF_E_INVALIDMEDIATYPE;
    return S_OK;
}
}
