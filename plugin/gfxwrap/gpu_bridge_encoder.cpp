#include "gpu_bridge_encoder.h"
#include <string.h>
#include <new>
#define NV_CHECK(call) do {const NVENCSTATUS status=(call);if(status!=NV_ENC_SUCCESS)return prepare_failed(GBENC_DRIVER_ERROR,status);}while(0)
bool BridgeEncoder::fail(gbenc_error_code code,NVENCSTATUS driver,bool quarantine) {
    retained_valid_=false;
    // Preserve a terminal session cause, not an earlier retriable argument error.
    if(state_!=GBENC_FAILED&&state_!=GBENC_QUARANTINED){error_=code;driver_error_=driver;}
    else if(driver!=NV_ENC_SUCCESS)driver_error_=driver;
    state_=quarantine?GBENC_QUARANTINED:GBENC_FAILED;return false;
}
bool BridgeEncoder::prepare_failed(gbenc_error_code code,NVENCSTATUS driver) {
    fail(code,driver);close();return false;
}
BridgeEncoder::~BridgeEncoder() {
    if(!close()&&state_!=GBENC_QUARANTINED)state_=GBENC_QUARANTINED;
    if(state_==GBENC_QUARANTINED) {
        // Ownership is uncertain. Do not release resources the driver may still use.
        // Dispose of this owned worker PROCESS before creating another session.
        input_.Detach();context_.Detach();device_.Detach();
    }
}
bool BridgeEncoder::prepare(ID3D11Device*device,ID3D11DeviceContext*context,
                            const BridgeEncoderSettings& settings,const gbenc_sink_v1& sink) {
    if(owner_&&owner_!=GetCurrentThreadId())return false;
    if(state_!=GBENC_EMPTY)return false;
    retained_valid_=false;
    if(!device||!context||settings.struct_size!=sizeof(settings)||settings.version!=GBENC_ABI_V1||
       sink.struct_size!=sizeof(sink)||sink.version!=GBENC_ABI_V1||!sink.on_packet||
       !settings.width||!settings.height||(settings.width&1)||(settings.height&1)||settings.width>3840||settings.height>2160||
       !settings.nominal_fps_num||!settings.nominal_fps_den||settings.cq>51||!settings.max_bitrate||
       !settings.vbv_buffer_bits||!settings.gop_frames||settings.full_range>1||settings.signal_color>1||
       settings.initial_qp_p>51||settings.initial_qp_i>51||settings.initial_qp_b>51||
       settings.profile!=GBENC_H264_HIGH||settings.preset!=GBENC_PRESET_P4||settings.tuning!=GBENC_TUNE_HQ||
       settings.rate_control!=GBENC_RC_VBR||settings.b_frames!=0||
       !settings.max_packet_bytes||settings.max_packet_bytes>16u*1024u*1024u||
       (settings.matrix!=5&&settings.matrix!=1)||settings.primaries!=2||settings.transfer!=2) {
        error_=GBENC_INVALID_ARGUMENT;return false;
    }
    for(uint32_t v:settings.reserved)if(v){error_=GBENC_INVALID_ARGUMENT;return false;}
    if(settings.input_format==GBENC_INPUT_RGBA8){buffer_format_=NV_ENC_BUFFER_FORMAT_ABGR;input_format_=DXGI_FORMAT_R8G8B8A8_UNORM;}
    else if(settings.input_format==GBENC_INPUT_BGRA8){buffer_format_=NV_ENC_BUFFER_FORMAT_ARGB;input_format_=DXGI_FORMAT_B8G8R8A8_UNORM;}
    else {error_=GBENC_INVALID_ARGUMENT;return false;}
    Microsoft::WRL::ComPtr<ID3D11Device> context_device;context->GetDevice(&context_device);
    if(context_device.Get()!=device||context->GetType()!=D3D11_DEVICE_CONTEXT_IMMEDIATE){error_=GBENC_INVALID_ARGUMENT;return false;}
    try {packet_bytes_.resize(settings.max_packet_bytes);}catch(const std::bad_alloc&){error_=GBENC_MEMORY_ERROR;return false;}
    settings_=settings;sink_=sink;owner_=GetCurrentThreadId();device_=device;context_=context;error_=GBENC_OK;
    dll_=LoadLibraryExW(L"nvEncodeAPI64.dll",nullptr,LOAD_LIBRARY_SEARCH_SYSTEM32);
    if(!dll_) return prepare_failed(GBENC_API_UNAVAILABLE);
    using Create=NVENCSTATUS(NVENCAPI *)(NV_ENCODE_API_FUNCTION_LIST*);
    using Version=NVENCSTATUS(NVENCAPI *)(uint32_t*);
    const auto create=reinterpret_cast<Create>(GetProcAddress(dll_,"NvEncodeAPICreateInstance"));
    const auto version=reinterpret_cast<Version>(GetProcAddress(dll_,"NvEncodeAPIGetMaxSupportedVersion"));
    uint32_t supported=0;
    if(!create||!version||version(&supported)!=NV_ENC_SUCCESS||supported<((NVENCAPI_MAJOR_VERSION<<4)|NVENCAPI_MINOR_VERSION)) return prepare_failed(GBENC_API_UNAVAILABLE);
    api_.version=NV_ENCODE_API_FUNCTION_LIST_VER;NV_CHECK(create(&api_));
    NV_ENC_OPEN_ENCODE_SESSION_EX_PARAMS open{NV_ENC_OPEN_ENCODE_SESSION_EX_PARAMS_VER};
    open.device=device;open.deviceType=NV_ENC_DEVICE_TYPE_DIRECTX;open.apiVersion=NVENCAPI_VERSION;
    NV_CHECK(api_.nvEncOpenEncodeSessionEx(&open,&encoder_));
    NV_ENC_BUFFER_FORMAT formats[32]{};uint32_t count=0;
    NV_CHECK(api_.nvEncGetInputFormats(encoder_,NV_ENC_CODEC_H264_GUID,formats,32,&count));
    bool format_supported=false;for(unsigned i=0;i<count&&i<32;++i)format_supported|=formats[i]==buffer_format_;
    if(!format_supported)return prepare_failed(GBENC_INVALID_ARGUMENT);
    NV_ENC_CAPS_PARAM caps{NV_ENC_CAPS_PARAM_VER};int min_width=0,min_height=0;
    caps.capsToQuery=NV_ENC_CAPS_WIDTH_MIN;NV_CHECK(api_.nvEncGetEncodeCaps(encoder_,NV_ENC_CODEC_H264_GUID,&caps,&min_width));
    caps.capsToQuery=NV_ENC_CAPS_HEIGHT_MIN;NV_CHECK(api_.nvEncGetEncodeCaps(encoder_,NV_ENC_CODEC_H264_GUID,&caps,&min_height));
    if(min_width>int(settings.width)||min_height>int(settings.height))return prepare_failed(GBENC_INVALID_ARGUMENT);
    NV_ENC_PRESET_CONFIG preset{NV_ENC_PRESET_CONFIG_VER};preset.presetCfg.version=NV_ENC_CONFIG_VER;
    NV_CHECK(api_.nvEncGetEncodePresetConfigEx(encoder_,NV_ENC_CODEC_H264_GUID,NV_ENC_PRESET_P4_GUID,NV_ENC_TUNING_INFO_HIGH_QUALITY,&preset));
    auto config=preset.presetCfg;
    config.profileGUID=NV_ENC_H264_PROFILE_HIGH_GUID;config.frameIntervalP=1;config.gopLength=settings.gop_frames;
    config.frameFieldMode=NV_ENC_PARAMS_FRAME_FIELD_MODE_FRAME;
    config.rcParams.rateControlMode=NV_ENC_PARAMS_RC_VBR;config.rcParams.targetQuality=static_cast<uint8_t>(settings.cq);config.rcParams.targetQualityLSB=0;
    // Caller supplies the resolved FFmpeg-compatible initial QPs; no quality registry here.
    config.rcParams.multiPass=NV_ENC_MULTI_PASS_DISABLED;
    config.rcParams.enableInitialRCQP=1;
    config.rcParams.initialRCQP.qpInterP=settings.initial_qp_p;config.rcParams.initialRCQP.qpIntra=settings.initial_qp_i;config.rcParams.initialRCQP.qpInterB=settings.initial_qp_b;
    config.rcParams.averageBitRate=0;config.rcParams.maxBitRate=settings.max_bitrate;config.rcParams.vbvBufferSize=settings.vbv_buffer_bits;
    config.rcParams.enableLookahead=0;config.rcParams.lookaheadDepth=0;config.rcParams.enableAQ=0;config.rcParams.enableTemporalAQ=0;
    config.encodeCodecConfig.h264Config.idrPeriod=settings.gop_frames;
    config.encodeCodecConfig.h264Config.chromaFormatIDC=1;
    config.encodeCodecConfig.h264Config.sliceMode=3;config.encodeCodecConfig.h264Config.sliceModeData=1;
    if(settings.signal_color) {
        auto &vui=config.encodeCodecConfig.h264Config.h264VUIParameters;
        vui.videoFormat=NV_ENC_VUI_VIDEO_FORMAT_UNSPECIFIED;
        vui.videoSignalTypePresentFlag=1;vui.colourDescriptionPresentFlag=1;
        vui.videoFullRangeFlag=settings.full_range;
        vui.colourMatrix=static_cast<NV_ENC_VUI_MATRIX_COEFFS>(settings.matrix);
        vui.colourPrimaries=static_cast<NV_ENC_VUI_COLOR_PRIMARIES>(settings.primaries);
        vui.transferCharacteristics=static_cast<NV_ENC_VUI_TRANSFER_CHARACTERISTIC>(settings.transfer);
    }
    config.encodeCodecConfig.h264Config.repeatSPSPPS=1;
    NV_ENC_INITIALIZE_PARAMS init{NV_ENC_INITIALIZE_PARAMS_VER};
    init.encodeGUID=NV_ENC_CODEC_H264_GUID;init.presetGUID=NV_ENC_PRESET_P4_GUID;
    init.encodeWidth=settings.width;init.encodeHeight=settings.height;init.darWidth=settings.width;init.darHeight=settings.height;
    init.frameRateNum=settings.nominal_fps_num;init.frameRateDen=settings.nominal_fps_den;init.enablePTD=1;init.enableEncodeAsync=0;
    init.tuningInfo=NV_ENC_TUNING_INFO_HIGH_QUALITY;init.encodeConfig=&config;
    NV_CHECK(api_.nvEncInitializeEncoder(encoder_,&init));initialized_=true;
    D3D11_TEXTURE2D_DESC desc{};desc.Width=settings.width;desc.Height=settings.height;desc.MipLevels=1;desc.ArraySize=1;
    desc.Format=input_format_;desc.SampleDesc.Count=1;desc.Usage=D3D11_USAGE_DEFAULT;
    desc.BindFlags=D3D11_BIND_RENDER_TARGET|D3D11_BIND_SHADER_RESOURCE;
    if(FAILED(device->CreateTexture2D(&desc,nullptr,&input_)))return prepare_failed(GBENC_DRIVER_ERROR);
    context_=context;
    NV_ENC_REGISTER_RESOURCE resource{NV_ENC_REGISTER_RESOURCE_VER};resource.resourceType=NV_ENC_INPUT_RESOURCE_TYPE_DIRECTX;
    resource.resourceToRegister=input_.Get();resource.width=settings.width;resource.height=settings.height;
    resource.bufferFormat=buffer_format_;resource.bufferUsage=NV_ENC_INPUT_IMAGE;
    NV_CHECK(api_.nvEncRegisterResource(encoder_,&resource));registration_=resource.registeredResource;
    NV_ENC_CREATE_BITSTREAM_BUFFER bitstream{NV_ENC_CREATE_BITSTREAM_BUFFER_VER};
    NV_CHECK(api_.nvEncCreateBitstreamBuffer(encoder_,&bitstream));output_=bitstream.bitstreamBuffer;
    state_=GBENC_READY;return true;
}
#undef NV_CHECK
bool BridgeEncoder::encode(ID3D11Texture2D*source,uint64_t occurrence,uint64_t pts,uint64_t duration,bool force_idr) {
    if(owner_!=GetCurrentThreadId())return false;
    if(state_!=GBENC_READY)return false;
    retained_valid_=false;
    if(!source||source==input_.Get()||!duration||pts>UINT64_MAX-duration||(have_pts_&&pts<=last_pts_)||retained_generation_==UINT64_MAX){error_=GBENC_INVALID_ARGUMENT;return false;}
    D3D11_TEXTURE2D_DESC desc{};source->GetDesc(&desc);
    Microsoft::WRL::ComPtr<ID3D11Device> source_device;source->GetDevice(&source_device);
    if(source_device.Get()!=device_.Get()||desc.Width!=settings_.width||desc.Height!=settings_.height||desc.Format!=input_format_||
       desc.MipLevels!=1||desc.ArraySize!=1||desc.SampleDesc.Count!=1||desc.SampleDesc.Quality!=0){error_=GBENC_INVALID_ARGUMENT;return false;}
    error_=GBENC_OK;driver_error_=NV_ENC_SUCCESS;
    // Consumer owns any transport key. Only the ordinary registered texture reaches NVENC.
    ++retained_generation_;++source_copies_;retained_serial_=occurrence;
    context_->CopyResource(input_.Get(),source);context_->Flush();
    const bool accepted=submit(occurrence,pts,duration,force_idr);
    retained_valid_=accepted;return accepted;
}
bool BridgeEncoder::encode_retained(uint64_t generation,uint64_t occurrence,uint64_t pts,uint64_t duration,bool force_idr) {
    if(owner_!=GetCurrentThreadId()||state_!=GBENC_READY)return false;
    if(!retained_valid_||!generation||generation!=retained_generation_)return false;
    if(!duration||pts>UINT64_MAX-duration||(have_pts_&&(pts<=last_pts_||occurrence<=last_serial_))){error_=GBENC_INVALID_ARGUMENT;return false;}
    // The last accepted picture is fully unlocked/unmapped in registered input_.
    // No CopyResource, transport import/acquire, or previous packet bytes here.
    retained_valid_=false;error_=GBENC_OK;driver_error_=NV_ENC_SUCCESS;
    const bool accepted=submit(occurrence,pts,duration,force_idr);
    retained_valid_=accepted;if(accepted)++repeats_;return accepted;
}
bool BridgeEncoder::release_picture() {
    if(locked_) {
        const auto status=api_.nvEncUnlockBitstream(encoder_,output_);
        if(status!=NV_ENC_SUCCESS)return fail(GBENC_DRIVER_ERROR,status,true);
        locked_=false;
    }
    if(mapped_) {
        const auto status=api_.nvEncUnmapInputResource(encoder_,mapped_);
        if(status!=NV_ENC_SUCCESS)return fail(GBENC_DRIVER_ERROR,status,true);
        mapped_=nullptr;
    }
    return true;
}
bool BridgeEncoder::submit(uint64_t occurrence,uint64_t pts,uint64_t duration,bool force_idr) {
    if(state_!=GBENC_READY||mapped_||locked_)return false;
    const bool timed_idr=settings_.idr_interval_ticks&&time_forced_<=pts/settings_.idr_interval_ticks;
    NV_ENC_MAP_INPUT_RESOURCE map{NV_ENC_MAP_INPUT_RESOURCE_VER};map.registeredResource=registration_;
    auto status=api_.nvEncMapInputResource(encoder_,&map);
    if(status!=NV_ENC_SUCCESS){mapped_=map.mappedResource;return fail(GBENC_DRIVER_ERROR,status,mapped_!=nullptr);}
    mapped_=map.mappedResource;
    if(!mapped_)return fail(GBENC_PACKET_ERROR,NV_ENC_SUCCESS,true);
    NV_ENC_PIC_PARAMS picture{NV_ENC_PIC_PARAMS_VER};picture.inputBuffer=mapped_;
    picture.bufferFmt=map.mappedBufferFmt;picture.inputWidth=settings_.width;picture.inputHeight=settings_.height;
    picture.outputBitstream=output_;picture.pictureStruct=NV_ENC_PIC_STRUCT_FRAME;
    picture.inputTimeStamp=pts;picture.inputDuration=duration;
    if(force_idr||timed_idr)picture.encodePicFlags=NV_ENC_PIC_FLAG_FORCEIDR;
    status=api_.nvEncEncodePicture(encoder_,&picture);
    // NEED_MORE_INPUT also retains input. No guessed recovery/reuse in this synchronous implementation.
    if(status!=NV_ENC_SUCCESS){if(status==NV_ENC_ERR_NEED_MORE_INPUT)++submitted_;return fail(GBENC_DRIVER_ERROR,status,true);}
    ++submitted_;if(timed_idr)++time_forced_;last_pts_=pts;last_serial_=occurrence;have_pts_=true;
    NV_ENC_LOCK_BITSTREAM locked{NV_ENC_LOCK_BITSTREAM_VER};locked.outputBitstream=output_;locked.doNotWait=0;
    status=api_.nvEncLockBitstream(encoder_,&locked);
    if(status!=NV_ENC_SUCCESS)return fail(GBENC_DRIVER_ERROR,status,true);
    locked_=true;
    const bool valid=locked.bitstreamBufferPtr&&locked.bitstreamSizeInBytes&&locked.bitstreamSizeInBytes<=packet_bytes_.size()&&
        locked.outputTimeStamp==pts&&locked.outputDuration==duration;
    const uint32_t bytes=locked.bitstreamSizeInBytes;
    const uint32_t flags=locked.pictureType==NV_ENC_PIC_TYPE_IDR?GBENC_PACKET_KEYFRAME:0;
    if(valid)memcpy(packet_bytes_.data(),locked.bitstreamBufferPtr,bytes);
    if(!release_picture())return false;
    ++completed_;
    if(!valid)return fail(GBENC_PACKET_ERROR);
    const gbenc_packet_v1 packet{sizeof(gbenc_packet_v1),GBENC_ABI_V1,packet_bytes_.data(),bytes,flags,occurrence,pts,duration};
    state_=GBENC_DELIVERING;
    int accepted=GBENC_SINK_REJECTED;
    try {accepted=sink_.on_packet(sink_.user,&packet);}catch(...){accepted=GBENC_SINK_REJECTED;}
    if(accepted!=GBENC_SINK_ACCEPTED)return fail(GBENC_SINK_ERROR);
    ++delivered_;state_=GBENC_READY;error_=GBENC_OK;return true;
}
bool BridgeEncoder::close() {
    if(owner_&&owner_!=GetCurrentThreadId())return false;
    if(state_==GBENC_CLOSED)return true;
    if(state_==GBENC_DELIVERING)return false;
    retained_valid_=false;
    if(state_==GBENC_QUARANTINED)return false;
    if(mapped_||locked_||submitted_!=completed_)return fail(GBENC_DRIVER_ERROR,NV_ENC_SUCCESS,true);
    if(encoder_&&initialized_) {
        NV_ENC_PIC_PARAMS eos{NV_ENC_PIC_PARAMS_VER};eos.encodePicFlags=NV_ENC_PIC_FLAG_EOS;
        const auto status=api_.nvEncEncodePicture(encoder_,&eos);
        if(status!=NV_ENC_SUCCESS)return fail(GBENC_DRIVER_ERROR,status,true);
        initialized_=false;
    }
    if(output_) {
        const auto status=api_.nvEncDestroyBitstreamBuffer(encoder_,output_);
        if(status!=NV_ENC_SUCCESS)return fail(GBENC_DRIVER_ERROR,status,true);
        output_=nullptr;
    }
    if(registration_) {
        const auto status=api_.nvEncUnregisterResource(encoder_,registration_);
        if(status!=NV_ENC_SUCCESS)return fail(GBENC_DRIVER_ERROR,status,true);
        registration_=nullptr;
    }
    if(encoder_) {
        const auto status=api_.nvEncDestroyEncoder(encoder_);
        if(status!=NV_ENC_SUCCESS)return fail(GBENC_DRIVER_ERROR,status,true);
        encoder_=nullptr;
    }
    input_.Reset();context_.Reset();device_.Reset();
    if(dll_)FreeLibrary(dll_);dll_=nullptr;state_=GBENC_CLOSED;return true;
}
