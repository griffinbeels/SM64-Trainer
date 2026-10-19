/* MFT stream alignment is bytes; MFCreateAlignedMemoryBuffer takes bytes-1.
   Resolve bounded caller-owned output before admitting a picture. */
#pragma once
#include <mftransform.h>
#include <mferror.h>
inline HRESULT gpu_mf_output_policy(const MFT_OUTPUT_STREAM_INFO&info,DWORD limit,DWORD*mask){
    if(!mask||!limit)return E_INVALIDARG;
    *mask=0;
    if(info.cbSize>limit)return MF_E_BUFFERTOOSMALL;
    if(info.dwFlags&MFT_OUTPUT_STREAM_PROVIDES_SAMPLES)return S_OK;
    const DWORD alignment=info.cbAlignment?info.cbAlignment:1;
    if((alignment&(alignment-1))||alignment>limit)return E_INVALIDARG;
    *mask=alignment-1;return S_OK;
}
