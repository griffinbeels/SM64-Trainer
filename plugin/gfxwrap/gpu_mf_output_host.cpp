/* CPU-only, injected hardware-MFT boundary. The shipped submit/packet/lifetime
   path runs unchanged; only creating the input D3D surface is replaced. */
#include "gpu_mf_encoder.h"
#include "gpu_mf_format.h"
#include "gpu_mf_bitstream.h"
#include <mfidl.h>
#include <mftransform.h>
#include <strmif.h>
#include <wrl/implements.h>
#include <stdio.h>
#include <string>
#include <vector>
using Microsoft::WRL::ComPtr;
using Microsoft::WRL::Make;
#pragma comment(lib,"d3d11.lib")
#pragma comment(lib,"dxgi.lib")
#pragma comment(lib,"mfplat.lib")
#pragma comment(lib,"mfuuid.lib")
#pragma comment(lib,"ole32.lib")
#pragma comment(lib,"oleaut32.lib")

namespace {
std::vector<uint8_t> read(const char*path){
    std::vector<uint8_t> bytes;FILE*f=nullptr;fopen_s(&f,path,"rb");if(!f)return bytes;
    fseek(f,0,SEEK_END);const long size=ftell(f);rewind(f);
    if(size>0&&size<(16<<20)){bytes.resize(size);if(fread(bytes.data(),1,size,f)!=size_t(size))bytes.clear();}
    fclose(f);return bytes;
}
class Fake final:public Microsoft::WRL::RuntimeClass<Microsoft::WRL::RuntimeClassFlags<Microsoft::WRL::ClassicCom>,IMFTransform,IMFMediaEventGenerator,ICodecAPI>{
public:
    std::string mode;ComPtr<IMFAttributes> attributes;ComPtr<IMFMediaType> type,changed;
    ComPtr<IMFMediaEventQueue> queue;ComPtr<IMFSample> held;std::vector<uint8_t> payload;
    unsigned inputs=0,outputs=0,type_sets=0,force_keys=0,steps=0;
    HRESULT initialize(const char*name,const gbenc_options_v1&settings,const std::vector<uint8_t>&header,const std::vector<uint8_t>&data){
        mode=name;payload=data;HRESULT hr=MFCreateAttributes(&attributes,2);
        if(SUCCEEDED(hr))hr=attributes->SetUINT32(MF_TRANSFORM_ASYNC,TRUE);
        if(SUCCEEDED(hr))hr=MFCreateEventQueue(&queue);
        if(SUCCEEDED(hr))hr=mf_gpu::media_type(&type,settings.codec==GBENC_CODEC_AV1?MFVideoFormat_AV1:MFVideoFormat_H264,settings,true);
        if(SUCCEEDED(hr))hr=type->SetBlob(MF_MT_MPEG_SEQUENCE_HEADER,header.data(),UINT32(header.size()));
        if(SUCCEEDED(hr))hr=MFCreateMediaType(&changed);
        if(SUCCEEDED(hr))hr=type->CopyAllItems(changed.Get());
        if(mode=="inband_no_type"||mode=="missing_configuration"||mode=="av1_inband_no_type"||mode=="av1_missing_configuration"){
            type->DeleteItem(MF_MT_MPEG_SEQUENCE_HEADER);changed->DeleteItem(MF_MT_MPEG_SEQUENCE_HEADER);}
        if(mode=="changed_size")MFSetAttributeSize(changed.Get(),MF_MT_FRAME_SIZE,640,240);
        if(mode=="changed_codec")changed->SetGUID(MF_MT_SUBTYPE,MFVideoFormat_AV1);
        if(mode=="changed_color")changed->SetUINT32(MF_MT_VIDEO_NOMINAL_RANGE,MFNominalRange_0_255);
        return hr;
    }
    HRESULT signal(MediaEventType kind){return queue->QueueEventParamVar(kind,GUID_NULL,S_OK,nullptr);}
    STDMETHODIMP GetOutputStreamInfo(DWORD,MFT_OUTPUT_STREAM_INFO*out)override{
        *out={};out->dwFlags=MFT_OUTPUT_STREAM_WHOLE_SAMPLES|MFT_OUTPUT_STREAM_SINGLE_SAMPLE_PER_BUFFER;
        if(mode!="caller")out->dwFlags|=MFT_OUTPUT_STREAM_PROVIDES_SAMPLES;
        else{out->cbSize=4096;out->cbAlignment=16;}return S_OK;
    }
    STDMETHODIMP GetAttributes(IMFAttributes**out)override{return attributes.CopyTo(out);}
    STDMETHODIMP GetOutputCurrentType(DWORD,IMFMediaType**out)override{return type.CopyTo(out);}
    STDMETHODIMP GetOutputAvailableType(DWORD,DWORD index,IMFMediaType**out)override{return index?MF_E_NO_MORE_TYPES:changed.CopyTo(out);}
    STDMETHODIMP SetOutputType(DWORD,IMFMediaType*media,DWORD)override{type=media;++type_sets;return S_OK;}
    STDMETHODIMP ProcessMessage(MFT_MESSAGE_TYPE message,ULONG_PTR)override{
        if(message==MFT_MESSAGE_NOTIFY_START_OF_STREAM)return signal(METransformNeedInput);return S_OK;
    }
    STDMETHODIMP ProcessInput(DWORD,IMFSample*sample,DWORD)override{++inputs;steps=0;held=sample;
        return inputs==1||(inputs==2&&mode=="advertised_differs")?signal(METransformHaveOutput):E_UNEXPECTED;}
    STDMETHODIMP ProcessOutput(DWORD,DWORD count,MFT_OUTPUT_DATA_BUFFER*out,DWORD*status)override{
        if(count!=1||!held)return E_UNEXPECTED;++outputs;++steps;*status=0;
        if(steps==1||mode=="event_flood"){
            ComPtr<IMFCollection> collection;ComPtr<IMFMediaEvent> event;
            HRESULT hr=MFCreateCollection(&collection);
            if(SUCCEEDED(hr))hr=MFCreateMediaEvent(MEExtendedType,GUID_NULL,mode=="error_event"?E_ACCESSDENIED:S_OK,nullptr,&event);
            if(SUCCEEDED(hr))hr=collection->AddElement(event.Get());if(FAILED(hr))return hr;
            out->pEvents=collection.Detach();out->dwStatus=MFT_OUTPUT_DATA_BUFFER_NO_SAMPLE;
            return signal(METransformHaveOutput);
        }
        if(steps==2){out->dwStatus=MFT_OUTPUT_DATA_BUFFER_FORMAT_CHANGE;
            HRESULT hr=signal(METransformHaveOutput);return FAILED(hr)?hr:MF_E_TRANSFORM_STREAM_CHANGE;}
        ComPtr<IMFSample> sample;ComPtr<IMFMediaBuffer> buffer;
        HRESULT hr=S_OK;if(out->pSample)sample=out->pSample;else hr=MFCreateSample(&sample);
        if(SUCCEEDED(hr)&&!out->pSample){hr=MFCreateMemoryBuffer(UINT32(payload.size()),&buffer);
            if(SUCCEEDED(hr))hr=sample->AddBuffer(buffer.Get());}
        else if(SUCCEEDED(hr))hr=sample->GetBufferByIndex(0,&buffer);
        if(FAILED(hr))return hr;BYTE*bytes=nullptr;DWORD capacity=0;hr=buffer->Lock(&bytes,&capacity,nullptr);
        if(FAILED(hr))return hr;if(capacity<payload.size()){buffer->Unlock();return MF_E_BUFFERTOOSMALL;}
        memcpy(bytes,payload.data(),payload.size());buffer->Unlock();buffer->SetCurrentLength(UINT32(payload.size()));
        LONGLONG pts=0,duration=0;held->GetSampleTime(&pts);held->GetSampleDuration(&duration);
        sample->SetSampleTime(pts+(mode=="wrong_pts"?1000:0));
        sample->SetSampleDuration(duration+(mode=="wrong_duration"?1000:0));
        sample->SetUINT32(MFSampleExtension_CleanPoint,1);
        if(!out->pSample)out->pSample=sample.Detach();
        if(mode=="incomplete")out->dwStatus=MFT_OUTPUT_DATA_BUFFER_INCOMPLETE;
        if(mode!="held_input")held.Reset();return signal(METransformNeedInput);
    }
    STDMETHODIMP GetEvent(DWORD flags,IMFMediaEvent**out)override{return queue->GetEvent(flags,out);}
    STDMETHODIMP BeginGetEvent(IMFAsyncCallback*c,IUnknown*s)override{return queue->BeginGetEvent(c,s);}
    STDMETHODIMP EndGetEvent(IMFAsyncResult*r,IMFMediaEvent**e)override{return queue->EndGetEvent(r,e);}
    STDMETHODIMP QueueEvent(MediaEventType k,REFGUID g,HRESULT h,const PROPVARIANT*v)override{return queue->QueueEventParamVar(k,g,h,v);}
    STDMETHODIMP SetValue(const GUID*key,VARIANT*setting)override{
        if(*key!=CODECAPI_AVEncVideoForceKeyFrame||setting->vt!=VT_UI4||setting->ulVal!=1)return E_INVALIDARG;
        ++force_keys;return S_OK;
    }
#define UNIMPLEMENTED(name,args) STDMETHODIMP name args override{return E_NOTIMPL;}
    UNIMPLEMENTED(GetStreamLimits,(DWORD*,DWORD*,DWORD*,DWORD*))
    UNIMPLEMENTED(GetStreamCount,(DWORD*,DWORD*))
    UNIMPLEMENTED(GetStreamIDs,(DWORD,DWORD*,DWORD,DWORD*))
    UNIMPLEMENTED(GetInputStreamInfo,(DWORD,MFT_INPUT_STREAM_INFO*))
    UNIMPLEMENTED(GetInputStreamAttributes,(DWORD,IMFAttributes**))
    UNIMPLEMENTED(GetOutputStreamAttributes,(DWORD,IMFAttributes**))
    UNIMPLEMENTED(DeleteInputStream,(DWORD))
    UNIMPLEMENTED(AddInputStreams,(DWORD,DWORD*))
    UNIMPLEMENTED(GetInputAvailableType,(DWORD,DWORD,IMFMediaType**))
    UNIMPLEMENTED(SetInputType,(DWORD,IMFMediaType*,DWORD))
    UNIMPLEMENTED(GetInputCurrentType,(DWORD,IMFMediaType**))
    UNIMPLEMENTED(GetInputStatus,(DWORD,DWORD*))
    UNIMPLEMENTED(GetOutputStatus,(DWORD*))
    UNIMPLEMENTED(SetOutputBounds,(LONGLONG,LONGLONG))
    UNIMPLEMENTED(ProcessEvent,(DWORD,IMFMediaEvent*))
    UNIMPLEMENTED(IsSupported,(const GUID*))
    UNIMPLEMENTED(IsModifiable,(const GUID*))
    UNIMPLEMENTED(GetParameterRange,(const GUID*,VARIANT*,VARIANT*,VARIANT*))
    UNIMPLEMENTED(GetParameterValues,(const GUID*,VARIANT**,ULONG*))
    UNIMPLEMENTED(GetDefaultValue,(const GUID*,VARIANT*))
    UNIMPLEMENTED(GetValue,(const GUID*,VARIANT*))
    UNIMPLEMENTED(RegisterForEvent,(const GUID*,LONG_PTR))
    UNIMPLEMENTED(UnregisterForEvent,(const GUID*))
    UNIMPLEMENTED(SetAllDefaults,())
    UNIMPLEMENTED(SetValueWithNotify,(const GUID*,VARIANT*,GUID**,ULONG*))
    UNIMPLEMENTED(SetAllDefaultsWithNotify,(GUID**,ULONG*))
    UNIMPLEMENTED(GetAllSettings,(IStream*))
    UNIMPLEMENTED(SetAllSettings,(IStream*))
    UNIMPLEMENTED(SetAllSettingsWithNotify,(IStream*,GUID**,ULONG*))
#undef UNIMPLEMENTED
};
struct Delivered {unsigned count=0;FILE*file=nullptr;};
int GBENC_CALL receive(void*opaque,const gbenc_packet_v1*packet){
    auto&seen=*static_cast<Delivered*>(opaque);++seen.count;
    printf("packet occurrence=%llu pts=%llu duration=%llu flags=%u bytes=%u\n",packet->occurrence,packet->pts,packet->duration,packet->flags,packet->bytes);
    return fwrite(packet->data,1,packet->bytes,seen.file)==packet->bytes?GBENC_SINK_ACCEPTED:GBENC_SINK_REJECTED;
}
}
int main(int argc,char**argv){
    if(argc!=5)return 1;const auto header=read(argv[2]),payload=read(argv[3]);if(header.empty()||payload.empty())return 2;
    if(FAILED(CoInitializeEx(nullptr,COINIT_MULTITHREADED))||FAILED(MFStartup(MF_VERSION)))return 3;
    MfEncoderSettings settings{};auto&s=settings.picture;
    s.struct_size=sizeof s;s.version=1;s.width=320;s.height=240;s.nominal_fps_num=30;s.nominal_fps_den=1;
    s.profile=100;s.gop_frames=120;s.input_format=GBENC_INPUT_RGBA8;s.signal_color=1;s.matrix=5;s.primaries=2;s.transfer=2;
    s.idr_interval_ticks=90000;s.max_packet_bytes=1<<20;s.backend=GBENC_BACKEND_WINDOWS;s.backend_quality=settings.quality=90;
    const std::string mode=argv[1];const bool av1=mode.rfind("av1_",0)==0;
    if(av1){s.codec=GBENC_CODEC_AV1;s.profile=GBENC_AV1_MAIN;}
    ComPtr<IMFMediaType> negotiated;if(FAILED(mf_gpu::media_type(&negotiated,av1?MFVideoFormat_AV1:MFVideoFormat_H264,s,true)))return 4;
    UINT32 bitrate=0;if(FAILED(negotiated->GetUINT32(MF_MT_AVG_BITRATE,&bitrate))||!bitrate||s.max_bitrate)return 5;
    printf("positive media-type bitrate hint; native bitrate fields stay zero\n");
    auto fake=Make<Fake>();if(!fake||FAILED(fake->initialize(argv[1],s,header,payload)))return 6;
    if(mode=="malformed_header"){const BYTE bad[]={0,0,0,1,0x67,0};fake->type->SetBlob(MF_MT_MPEG_SEQUENCE_HEADER,bad,sizeof bad);}
    if(mode=="overflow"||mode=="av1_overflow")s.max_packet_bytes=UINT32(payload.size());
    Delivered delivered;fopen_s(&delivered.file,argv[4],"wb");if(!delivered.file)return 7;
    bool ready=false,accepted=false,closed=false;uint32_t state=0,error=0,hr=0;uint64_t submitted=0,completed=0;
    {MfEncoder encoder;gbenc_sink_v1 sink{sizeof sink,1,receive,&delivered};
        ready=encoder.test_prepare(fake.Get(),settings,sink);
        if(ready)accepted=encoder.test_submit(41,3002,86998);
        if(accepted&&mode=="advertised_differs")accepted=encoder.test_submit(42,90000,9000);
        state=encoder.state();error=encoder.error();hr=encoder.driver_error();submitted=encoder.submitted();completed=encoder.completed();
        closed=encoder.close();fake->held.Reset();}
    fclose(delivered.file);
    printf("ready=%u accepted=%u state=%u error=%u hr=%08x inputs=%u outputs=%u type_sets=%u forced=%u delivered=%u submitted=%llu completed=%llu closed=%u\n",
        ready,accepted,state,error,hr,fake->inputs,fake->outputs,fake->type_sets,fake->force_keys,delivered.count,submitted,completed,closed);
    const bool success=mode=="event"||mode=="inband"||mode=="inband_no_type"||mode=="caller"||mode=="missing_sps"||mode=="missing_pps"||
        mode=="av1_event"||mode=="av1_inband"||mode=="av1_inband_no_type"||mode=="advertised_differs";
    const unsigned pictures=mode=="advertised_differs"?2:1;
    const bool early=mode=="malformed_header"||mode=="av1_record"||mode=="av1_bad_leb";
    const bool valid=success?(ready&&accepted&&closed&&fake->inputs==pictures&&fake->outputs==3*pictures&&fake->type_sets==pictures&&fake->force_keys==pictures&&delivered.count==pictures&&submitted==pictures&&completed==pictures):
        (!accepted&&!delivered.count&&fake->inputs<=1&&(early?!ready:ready&&!closed&&state==GBENC_QUARANTINED));
    if(mode=="av1_record"&&error!=GBENC_CODEC_UNAVAILABLE)return 9;
    fake.Reset();MFShutdown();CoUninitialize();return valid?0:8;
}
