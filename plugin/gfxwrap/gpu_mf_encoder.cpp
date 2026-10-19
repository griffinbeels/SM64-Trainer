#include "gpu_mf_encoder.h"
#include "gpu_mf_deadline.h"
#include <mfapi.h>
#include <mfidl.h>
#include <mferror.h>
#include <mftransform.h>
#include <codecapi.h>
#include <strmif.h>
#include <d3d10.h>
#include <d3d11_1.h>
#include <dxgi1_2.h>
#include <wrl/client.h>
#include <vector>
#include <atomic>
#include <stdio.h>
#include <new>
using Microsoft::WRL::ComPtr;
namespace {
// An output packet does not by itself prove that a hardware MFT has released
// its input sample. Track its actual lifetime before reusing the retained NV12.
class Released final:public IMFAsyncCallback {
    std::atomic<ULONG> refs_{1};
public:
    HANDLE event=CreateEventW(nullptr,TRUE,FALSE,nullptr);
    ~Released(){if(event)CloseHandle(event);}
    STDMETHODIMP QueryInterface(REFIID id,void**out)override{
        if(!out)return E_POINTER;*out=nullptr;
        if(id==__uuidof(IUnknown)||id==__uuidof(IMFAsyncCallback)){*out=static_cast<IMFAsyncCallback*>(this);AddRef();return S_OK;}return E_NOINTERFACE;}
    STDMETHODIMP_(ULONG) AddRef()override{return ++refs_;}
    STDMETHODIMP_(ULONG) Release()override{ULONG n=--refs_;if(!n)delete this;return n;}
    STDMETHODIMP GetParameters(DWORD*,DWORD*)override{return E_NOTIMPL;}
    STDMETHODIMP Invoke(IMFAsyncResult*)override{SetEvent(event);return S_OK;}
};
HRESULT property(ICodecAPI*api,const GUID&key,VARTYPE type,ULONG value){
    VARIANT v;VariantInit(&v);v.vt=type;if(type==VT_BOOL)v.boolVal=value?VARIANT_TRUE:VARIANT_FALSE;else v.ulVal=value;
    return api->SetValue(&key,&v);
}
HRESULT type(IMFMediaType**out,const GUID&subtype,const gbenc_options_v1&s,bool encoded){
    ComPtr<IMFMediaType> t;HRESULT hr=MFCreateMediaType(&t);if(FAILED(hr))return hr;
    t->SetGUID(MF_MT_MAJOR_TYPE,MFMediaType_Video);t->SetGUID(MF_MT_SUBTYPE,subtype);
    MFSetAttributeSize(t.Get(),MF_MT_FRAME_SIZE,s.width,s.height);
    MFSetAttributeRatio(t.Get(),MF_MT_FRAME_RATE,s.nominal_fps_num,s.nominal_fps_den);
    MFSetAttributeRatio(t.Get(),MF_MT_PIXEL_ASPECT_RATIO,1,1);
    t->SetUINT32(MF_MT_INTERLACE_MODE,MFVideoInterlace_Progressive);
    if(s.signal_color){
        t->SetUINT32(MF_MT_VIDEO_NOMINAL_RANGE,s.full_range?MFNominalRange_0_255:MFNominalRange_16_235);
        t->SetUINT32(MF_MT_YUV_MATRIX,s.matrix==1?MFVideoTransferMatrix_BT709:MFVideoTransferMatrix_BT601);
    }
    if(encoded){t->SetUINT32(MF_MT_AVG_BITRATE,s.max_bitrate);t->SetUINT32(MF_MT_MPEG2_PROFILE,s.codec==GBENC_CODEC_AV1?eAVEncAV1VProfile_Main_420_8:s.profile);}
    *out=t.Detach();return S_OK;
}
// 90k <-> 100ns conversion with no floating point. Quantization is less than a
// 90k tick and roundtrips; original opaque metadata is delivered only after it matches.
LONGLONG hns(uint64_t ticks){return LONGLONG((ticks/9)*1000+((ticks%9)*1000+4)/9);}
uint64_t ticks(LONGLONG value){return uint64_t(value/1000)*9+(uint64_t(value%1000)*9+500)/1000;}
}
struct MfEncoder::Impl {
    MfEncoderSettings settings{};gbenc_sink_v1 sink{};
    bool co=false,mf=false,streaming=false,asynchronous=false;UINT input_credit=0,output_credit=0;
    ComPtr<ID3D11Device> device;ComPtr<ID3D11DeviceContext> context;
    ComPtr<ID3D11VideoDevice> video;ComPtr<ID3D11VideoContext> video_context;
    ComPtr<ID3D11VideoProcessorEnumerator> enumeration;ComPtr<ID3D11VideoProcessor> processor;
    ComPtr<ID3D11Texture2D> nv12;ComPtr<ID3D11VideoProcessorOutputView> output_view;
    ComPtr<IMFDXGIDeviceManager> manager;ComPtr<IMFTransform> mft;
    ComPtr<IMFMediaEventGenerator> events;ComPtr<IMFActivate> activation;ComPtr<ICodecAPI> codec;
    std::vector<uint8_t> packet;ComPtr<Released> release;
    HRESULT event_until(bool output,ULONGLONG deadline){
        if(!asynchronous)return S_OK;
        while(!(output?output_credit:input_credit)){
            ComPtr<IMFMediaEvent> e;HRESULT hr=events->GetEvent(MF_EVENT_FLAG_NO_WAIT,&e);
            if(hr==MF_E_NO_EVENTS_AVAILABLE){if(GetTickCount64()>=deadline)return HRESULT_FROM_WIN32(WAIT_TIMEOUT);Sleep(1);continue;}
            if(FAILED(hr))return hr;HRESULT status=S_OK;e->GetStatus(&status);if(FAILED(status))return status;
            MediaEventType kind=MEUnknown;e->GetType(&kind);
            if(kind==METransformNeedInput)++input_credit;
            else if(kind==METransformHaveOutput)++output_credit;
            else if(kind==MEError)return E_FAIL;
        }return S_OK;
    }
    ~Impl(){
        if(mft){ComPtr<IMFShutdown> shutdown;if(SUCCEEDED(mft.As(&shutdown)))shutdown->Shutdown();}
        events.Reset();codec.Reset();mft.Reset();if(activation)activation->ShutdownObject();activation.Reset();
        manager.Reset();output_view.Reset();nv12.Reset();processor.Reset();enumeration.Reset();
        video_context.Reset();video.Reset();context.Reset();device.Reset();release.Reset();
        if(mf)MFShutdown();if(co)CoUninitialize();
    }
};
bool MfEncoder::fail(gbenc_error_code e,HRESULT hr,bool quarantine){
    retained_=false;if(state_!=GBENC_FAILED&&state_!=GBENC_QUARANTINED){error_=e;hr_=hr;}
    state_=quarantine?GBENC_QUARANTINED:GBENC_FAILED;return false;
}
MfEncoder::~MfEncoder(){if(state_!=GBENC_QUARANTINED)close();/* quarantined process owns unresolved resources */}
#define MF_TRY(call) do{const HRESULT mf_status=(call);if(FAILED(mf_status)){ \
    fprintf(stderr,"SM64_GPU_MF operation=%s hresult=%08x\n",#call,unsigned(mf_status)); \
    return fail(GBENC_DRIVER_ERROR,mf_status,submitted_!=completed_);}}while(0)
bool MfEncoder::prepare(ID3D11Device*device,ID3D11DeviceContext*context,const MfEncoderSettings&settings,const gbenc_sink_v1&sink){
    if(owner_&&owner_!=GetCurrentThreadId())return false;if(state_!=GBENC_EMPTY)return false;
    const auto&s=settings.picture;
    if(!device||!context||s.struct_size!=sizeof(s)||s.version!=GBENC_ABI_V1||sink.struct_size!=sizeof(sink)||sink.version!=GBENC_ABI_V1||!sink.on_packet||
       !s.width||!s.height||(s.width&1)||(s.height&1)||s.width>3840||s.height>2160||!s.nominal_fps_num||!s.nominal_fps_den||
       settings.quality>100||s.b_frames||!s.gop_frames||!s.max_packet_bytes||s.max_packet_bytes>(16u<<20)||
       (s.codec!=GBENC_CODEC_H264&&s.codec!=GBENC_CODEC_AV1)||s.profile!=(s.codec==GBENC_CODEC_H264?GBENC_H264_HIGH:GBENC_AV1_MAIN)||
       s.backend_quality!=settings.quality||s.preset||s.tuning||s.rate_control||s.cq||s.vbv_buffer_bits||
       s.full_range>1||s.signal_color>1||(s.matrix!=5&&s.matrix!=1)||s.primaries!=2||s.transfer!=2||s.reserved[0]||s.reserved[1]||
       (s.input_format!=GBENC_INPUT_RGBA8&&s.input_format!=GBENC_INPUT_BGRA8)){
        error_=GBENC_INVALID_ARGUMENT;return false;}
    ComPtr<ID3D11Device> context_device;context->GetDevice(&context_device);
    if(context_device.Get()!=device||context->GetType()!=D3D11_DEVICE_CONTEXT_IMMEDIATE){error_=GBENC_INVALID_ARGUMENT;return false;}
    owner_=GetCurrentThreadId();impl_=new(std::nothrow)Impl;if(!impl_)return fail(GBENC_MEMORY_ERROR,E_OUTOFMEMORY);
    auto&p=*impl_;p.settings=settings;p.sink=sink;p.device=device;p.context=context;
    try{p.packet.resize(s.max_packet_bytes);}catch(...){return fail(GBENC_MEMORY_ERROR,E_OUTOFMEMORY);}
    HRESULT hr=CoInitializeEx(nullptr,COINIT_MULTITHREADED);if(FAILED(hr))return fail(GBENC_API_UNAVAILABLE,hr);p.co=true;
    MF_TRY(MFStartup(MF_VERSION));p.mf=true;
    ComPtr<ID3D10Multithread> multithread;MF_TRY(device->QueryInterface(IID_PPV_ARGS(&multithread)));multithread->SetMultithreadProtected(TRUE);
    ComPtr<IDXGIDevice> dxgi;ComPtr<IDXGIAdapter> adapter;DXGI_ADAPTER_DESC desc{};
    MF_TRY(device->QueryInterface(IID_PPV_ARGS(&dxgi)));MF_TRY(dxgi->GetAdapter(&adapter));MF_TRY(adapter->GetDesc(&desc));
    ComPtr<IMFAttributes> attrs;MF_TRY(MFCreateAttributes(&attrs,1));MF_TRY(attrs->SetBlob(MFT_ENUM_ADAPTER_LUID,(BYTE*)&desc.AdapterLuid,sizeof(LUID)));
    const GUID subtype=s.codec==GBENC_CODEC_AV1?MFVideoFormat_AV1:MFVideoFormat_H264;
    MFT_REGISTER_TYPE_INFO out{MFMediaType_Video,subtype};IMFActivate**acts=nullptr;UINT32 count=0;
    MF_TRY(MFTEnum2(MFT_CATEGORY_VIDEO_ENCODER,MFT_ENUM_FLAG_HARDWARE|MFT_ENUM_FLAG_SORTANDFILTER,nullptr,&out,attrs.Get(),&acts,&count));
    // Never substitute a software transform or another adapter's encoder.
    for(UINT32 i=0;i<count;++i){if(!p.mft){ComPtr<IMFTransform> candidate;
        if(SUCCEEDED(acts[i]->ActivateObject(IID_PPV_ARGS(&candidate)))){ComPtr<IMFAttributes>a;UINT32 aware=0;
            if(SUCCEEDED(candidate->GetAttributes(&a))&&SUCCEEDED(a->GetUINT32(MF_SA_D3D11_AWARE,&aware))&&aware){p.mft=candidate;p.activation=acts[i];}
            else acts[i]->ShutdownObject();}}
        acts[i]->Release();}CoTaskMemFree(acts);
    if(!p.mft)return fail(count?GBENC_API_UNAVAILABLE:GBENC_CODEC_UNAVAILABLE,MF_E_TOPO_CODEC_NOT_FOUND);
    ComPtr<IMFAttributes> transform_attrs;MF_TRY(p.mft->GetAttributes(&transform_attrs));UINT32 async=0;
    transform_attrs->GetUINT32(MF_TRANSFORM_ASYNC,&async);p.asynchronous=async!=0;
    if(p.asynchronous){MF_TRY(transform_attrs->SetUINT32(MF_TRANSFORM_ASYNC_UNLOCK,TRUE));MF_TRY(p.mft.As(&p.events));}
    UINT reset=0;MF_TRY(MFCreateDXGIDeviceManager(&reset,&p.manager));MF_TRY(p.manager->ResetDevice(device,reset));
    MF_TRY(p.mft->ProcessMessage(MFT_MESSAGE_SET_D3D_MANAGER,ULONG_PTR(p.manager.Get())));
    MF_TRY(p.mft.As(&p.codec));
    // Mandatory rather than best-effort: no B pictures or lookahead buffering.
    MF_TRY(property(p.codec.Get(),CODECAPI_AVLowLatencyMode,VT_BOOL,1));
    MF_TRY(property(p.codec.Get(),CODECAPI_AVEncMPVDefaultBPictureCount,VT_UI4,0));
    MF_TRY(property(p.codec.Get(),CODECAPI_AVEncMPVGOPSize,VT_UI4,s.gop_frames));
    MF_TRY(property(p.codec.Get(),CODECAPI_AVEncCommonRateControlMode,VT_UI4,eAVEncCommonRateControlMode_Quality));
    MF_TRY(property(p.codec.Get(),CODECAPI_AVEncCommonQuality,VT_UI4,settings.quality));
    ComPtr<IMFMediaType> encoded,input;MF_TRY(type(&encoded,subtype,s,true));MF_TRY(type(&input,MFVideoFormat_NV12,s,false));
    MF_TRY(p.mft->SetOutputType(0,encoded.Get(),0));MF_TRY(p.mft->SetInputType(0,input.Get(),0));
    MF_TRY(device->QueryInterface(IID_PPV_ARGS(&p.video)));MF_TRY(context->QueryInterface(IID_PPV_ARGS(&p.video_context)));
    D3D11_VIDEO_PROCESSOR_CONTENT_DESC content{};content.InputFrameFormat=D3D11_VIDEO_FRAME_FORMAT_PROGRESSIVE;
    content.InputFrameRate={s.nominal_fps_num,s.nominal_fps_den};content.OutputFrameRate=content.InputFrameRate;
    content.InputWidth=s.width;content.InputHeight=s.height;content.OutputWidth=s.width;content.OutputHeight=s.height;
    content.Usage=D3D11_VIDEO_USAGE_PLAYBACK_NORMAL;MF_TRY(p.video->CreateVideoProcessorEnumerator(&content,&p.enumeration));
    UINT flags=0;const DXGI_FORMAT format=s.input_format==GBENC_INPUT_RGBA8?DXGI_FORMAT_R8G8B8A8_UNORM:DXGI_FORMAT_B8G8R8A8_UNORM;
    MF_TRY(p.enumeration->CheckVideoProcessorFormat(format,&flags));if(!(flags&D3D11_VIDEO_PROCESSOR_FORMAT_SUPPORT_INPUT))return fail(GBENC_DRIVER_ERROR,MF_E_INVALIDMEDIATYPE);
    MF_TRY(p.enumeration->CheckVideoProcessorFormat(DXGI_FORMAT_NV12,&flags));if(!(flags&D3D11_VIDEO_PROCESSOR_FORMAT_SUPPORT_OUTPUT))return fail(GBENC_DRIVER_ERROR,MF_E_INVALIDMEDIATYPE);
    MF_TRY(p.video->CreateVideoProcessor(p.enumeration.Get(),0,&p.processor));
    D3D11_TEXTURE2D_DESC texture{};texture.Width=s.width;texture.Height=s.height;texture.MipLevels=1;texture.ArraySize=1;
    texture.Format=DXGI_FORMAT_NV12;texture.SampleDesc.Count=1;texture.Usage=D3D11_USAGE_DEFAULT;texture.BindFlags=D3D11_BIND_RENDER_TARGET;
    MF_TRY(device->CreateTexture2D(&texture,nullptr,&p.nv12));
    D3D11_VIDEO_PROCESSOR_OUTPUT_VIEW_DESC view{};view.ViewDimension=D3D11_VPOV_DIMENSION_TEXTURE2D;
    MF_TRY(p.video->CreateVideoProcessorOutputView(p.nv12.Get(),p.enumeration.Get(),&view,&p.output_view));
    D3D11_VIDEO_PROCESSOR_COLOR_SPACE rgb{};rgb.RGB_Range=0;rgb.YCbCr_Matrix=0;rgb.Nominal_Range=2;
    D3D11_VIDEO_PROCESSOR_COLOR_SPACE yuv{};yuv.YCbCr_Matrix=s.matrix==1?1:0;yuv.Nominal_Range=s.full_range?2:1;
    p.video_context->VideoProcessorSetStreamColorSpace(p.processor.Get(),0,&rgb);
    p.video_context->VideoProcessorSetOutputColorSpace(p.processor.Get(),&yuv);
    p.video_context->VideoProcessorSetStreamFrameFormat(p.processor.Get(),0,D3D11_VIDEO_FRAME_FORMAT_PROGRESSIVE);
    p.video_context->VideoProcessorSetStreamAutoProcessingMode(p.processor.Get(),0,FALSE);
    p.release.Attach(new(std::nothrow)Released);if(!p.release||!p.release->event)return fail(GBENC_MEMORY_ERROR,E_OUTOFMEMORY);
    MF_TRY(p.mft->ProcessMessage(MFT_MESSAGE_NOTIFY_BEGIN_STREAMING,0));
    MF_TRY(p.mft->ProcessMessage(MFT_MESSAGE_NOTIFY_START_OF_STREAM,0));p.streaming=true;
    state_=GBENC_READY;return true;
}
bool MfEncoder::encode(ID3D11Texture2D*source,uint64_t occurrence,uint64_t pts,uint64_t duration,bool idr){
    if(owner_!=GetCurrentThreadId()||state_!=GBENC_READY)return false;retained_=false;
    auto&p=*impl_;auto&s=p.settings.picture;
    const uint64_t limit=uint64_t(INT64_MAX/1000)*9;
    if(!source||!duration||duration>limit||pts>limit-duration||(have_pts_&&pts<=last_pts_)||generation_==UINT64_MAX){error_=GBENC_INVALID_ARGUMENT;return false;}
    D3D11_TEXTURE2D_DESC d{};source->GetDesc(&d);ComPtr<ID3D11Device> device;source->GetDevice(&device);
    const DXGI_FORMAT format=s.input_format==GBENC_INPUT_RGBA8?DXGI_FORMAT_R8G8B8A8_UNORM:DXGI_FORMAT_B8G8R8A8_UNORM;
    if(device.Get()!=p.device.Get()||d.Width!=s.width||d.Height!=s.height||d.Format!=format||d.MipLevels!=1||d.ArraySize!=1||d.SampleDesc.Count!=1||d.SampleDesc.Quality){error_=GBENC_INVALID_ARGUMENT;return false;}
    D3D11_VIDEO_PROCESSOR_INPUT_VIEW_DESC vd{};vd.ViewDimension=D3D11_VPIV_DIMENSION_TEXTURE2D;
    ComPtr<ID3D11VideoProcessorInputView> input;MF_TRY(p.video->CreateVideoProcessorInputView(source,p.enumeration.Get(),&vd,&input));
    D3D11_VIDEO_PROCESSOR_STREAM stream{};stream.Enable=TRUE;stream.pInputSurface=input.Get();
    MF_TRY(p.video_context->VideoProcessorBlt(p.processor.Get(),p.output_view.Get(),0,1,&stream));p.context->Flush();
    ++generation_;++source_copies_;selected_serial_=occurrence;
    retained_=submit(occurrence,pts,duration,idr);return retained_;
}
bool MfEncoder::encode_retained(uint64_t generation,uint64_t occurrence,uint64_t pts,uint64_t duration,bool idr){
    if(owner_!=GetCurrentThreadId()||state_!=GBENC_READY||!retained_||!generation||generation!=generation_)return false;
    const uint64_t limit=uint64_t(INT64_MAX/1000)*9;
    if(!duration||duration>limit||pts>limit-duration||pts<=last_pts_||occurrence<=last_serial_){error_=GBENC_INVALID_ARGUMENT;return false;}
    retained_=false;retained_=submit(occurrence,pts,duration,idr);if(retained_)++repeats_;return retained_;
}
bool MfEncoder::submit(uint64_t occurrence,uint64_t pts,uint64_t duration,bool idr){
    auto&p=*impl_;auto&s=p.settings.picture;ULONGLONG deadline=GetTickCount64()+1500;
    MF_TRY(p.event_until(false,deadline));
    const bool timed=s.idr_interval_ticks&&time_forced_<=pts/s.idr_interval_ticks;
    if(idr||timed)MF_TRY(property(p.codec.Get(),CODECAPI_AVEncVideoForceKeyFrame,VT_UI4,1));
    ComPtr<IMFTrackedSample> tracked;MF_TRY(MFCreateTrackedSample(&tracked));ComPtr<IMFSample> sample;MF_TRY(tracked.As(&sample));
    ComPtr<IMFMediaBuffer> buffer;MF_TRY(MFCreateDXGISurfaceBuffer(__uuidof(ID3D11Texture2D),p.nv12.Get(),0,FALSE,&buffer));
    MF_TRY(sample->AddBuffer(buffer.Get()));MF_TRY(sample->SetSampleTime(hns(pts)));MF_TRY(sample->SetSampleDuration(hns(duration)));
    ResetEvent(p.release->event);MF_TRY(tracked->SetAllocator(p.release.Get(),nullptr));tracked.Reset();
    HRESULT hr=p.mft->ProcessInput(0,sample.Get(),0);sample.Reset();buffer.Reset();
    if(FAILED(hr))return fail(GBENC_DRIVER_ERROR,hr);if(p.asynchronous)--p.input_credit;++submitted_;
    if(timed)++time_forced_;last_pts_=pts;last_serial_=occurrence;have_pts_=true;
    hr=p.event_until(true,deadline);if(FAILED(hr))return fail(GBENC_DRIVER_ERROR,hr,true);
    MFT_OUTPUT_STREAM_INFO info{};hr=p.mft->GetOutputStreamInfo(0,&info);if(FAILED(hr))return fail(GBENC_DRIVER_ERROR,hr,true);
    MFT_OUTPUT_DATA_BUFFER output{};ComPtr<IMFSample> supplied;
    if(!(info.dwFlags&MFT_OUTPUT_STREAM_PROVIDES_SAMPLES)){
        hr=MFCreateSample(&supplied);ComPtr<IMFMediaBuffer> memory;
        if(SUCCEEDED(hr))hr=MFCreateMemoryBuffer(DWORD(p.packet.size()),&memory);
        if(SUCCEEDED(hr))hr=supplied->AddBuffer(memory.Get());if(FAILED(hr))return fail(GBENC_MEMORY_ERROR,hr,true);output.pSample=supplied.Get();}
    DWORD status=0;hr=p.mft->ProcessOutput(0,1,&output,&status);if(output.pEvents)output.pEvents->Release();
    ComPtr<IMFSample> result;if(output.pSample==supplied.Get())result=supplied;else result.Attach(output.pSample);
    if(p.asynchronous)--p.output_credit;
    if(FAILED(hr)||!result)return fail(GBENC_DRIVER_ERROR,FAILED(hr)?hr:E_UNEXPECTED,true);
    LONGLONG out_pts=0,out_duration=0;UINT32 clean=0;
    hr=result->GetSampleTime(&out_pts);if(SUCCEEDED(hr))hr=result->GetSampleDuration(&out_duration);
    if(FAILED(hr)||out_pts<0||out_duration<=0||ticks(out_pts)!=pts||ticks(out_duration)!=duration)return fail(GBENC_PACKET_ERROR,FAILED(hr)?hr:E_UNEXPECTED,true);
    result->GetUINT32(MFSampleExtension_CleanPoint,&clean);
    if((idr||timed)&&!clean)return fail(GBENC_PACKET_ERROR,E_UNEXPECTED,true);
    ComPtr<IMFMediaBuffer> bytes;MF_TRY(result->ConvertToContiguousBuffer(&bytes));BYTE*data=nullptr;DWORD size=0;
    MF_TRY(bytes->Lock(&data,nullptr,&size));const bool valid=size&&size<=p.packet.size();if(valid)memcpy(p.packet.data(),data,size);
    hr=bytes->Unlock();if(FAILED(hr))return fail(GBENC_DRIVER_ERROR,hr,true);bytes.Reset();result.Reset();supplied.Reset();
    if(!valid)return fail(GBENC_PACKET_ERROR,E_UNEXPECTED,true);
    const ULONGLONG release_now=GetTickCount64();
    if(WaitForSingleObject(p.release->event,gpu_mf_wait_ms(deadline,release_now))!=WAIT_OBJECT_0)return fail(GBENC_DRIVER_ERROR,HRESULT_FROM_WIN32(WAIT_TIMEOUT),true);
    ++completed_;const gbenc_packet_v1 packet{sizeof(gbenc_packet_v1),GBENC_ABI_V1,p.packet.data(),size,clean?GBENC_PACKET_KEYFRAME:0,occurrence,pts,duration};
    state_=GBENC_DELIVERING;int accepted=GBENC_SINK_REJECTED;try{accepted=p.sink.on_packet(p.sink.user,&packet);}catch(...){accepted=GBENC_SINK_REJECTED;}
    if(accepted!=GBENC_SINK_ACCEPTED)return fail(GBENC_SINK_ERROR,E_FAIL);
    ++delivered_;state_=GBENC_READY;error_=GBENC_OK;hr_=S_OK;return true;
}
#undef MF_TRY
bool MfEncoder::close(){
    if(owner_&&owner_!=GetCurrentThreadId())return false;if(state_==GBENC_CLOSED)return true;
    if(state_==GBENC_DELIVERING||state_==GBENC_QUARANTINED)return false;retained_=false;
    if(submitted_!=completed_)return fail(GBENC_DRIVER_ERROR,E_UNEXPECTED,true);
    if(impl_){if(impl_->streaming){impl_->mft->ProcessMessage(MFT_MESSAGE_NOTIFY_END_OF_STREAM,0);impl_->mft->ProcessMessage(MFT_MESSAGE_NOTIFY_END_STREAMING,0);}
        delete impl_;impl_=nullptr;}state_=GBENC_CLOSED;return true;
}
