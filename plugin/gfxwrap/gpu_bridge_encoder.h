/* Single-consumer NVENC encoder; compressed packet delivery has no file coupling. */
#pragma once
#include "gpu_bridge.h"
#include "nvEncodeAPI.h"
#include "gpu_bridge_encoder_api.h"
#include <vector>
using BridgeEncoderSettings=gbenc_options_v1;
class BridgeEncoder {
    HMODULE dll_=nullptr;void*encoder_=nullptr;
    NV_ENCODE_API_FUNCTION_LIST api_{};
    Microsoft::WRL::ComPtr<ID3D11Device> device_;
    Microsoft::WRL::ComPtr<ID3D11Texture2D> input_;
    Microsoft::WRL::ComPtr<ID3D11DeviceContext> context_;
    NV_ENC_REGISTERED_PTR registration_=nullptr;
    NV_ENC_OUTPUT_PTR output_=nullptr;
    NV_ENC_INPUT_PTR mapped_=nullptr;
    bool locked_=false,initialized_=false;
    uint64_t submitted_=0,completed_=0,delivered_=0,time_forced_=0,last_pts_=0;
    bool have_pts_=false;
    bool retained_valid_=false;
    uint64_t retained_generation_=0,retained_serial_=0,source_copies_=0,repeats_=0,last_serial_=0;
    DWORD owner_=0;
    gbenc_state state_=GBENC_EMPTY;
    gbenc_error_code error_=GBENC_OK;
    NVENCSTATUS driver_error_=NV_ENC_SUCCESS;
    BridgeEncoderSettings settings_{};gbenc_sink_v1 sink_{};
    std::vector<uint8_t> packet_bytes_;
    NV_ENC_BUFFER_FORMAT buffer_format_=NV_ENC_BUFFER_FORMAT_UNDEFINED;
    DXGI_FORMAT input_format_=DXGI_FORMAT_UNKNOWN;
    bool fail(gbenc_error_code,NVENCSTATUS=NV_ENC_SUCCESS,bool quarantine=false);
    bool prepare_failed(gbenc_error_code,NVENCSTATUS=NV_ENC_SUCCESS);
    bool release_picture();
    bool submit(uint64_t occurrence,uint64_t pts,uint64_t duration,bool force_idr);
#ifdef REPLAY_ENCODER_TESTING
    friend struct BridgeEncoderTestAccess;
#endif
public:
    BridgeEncoder()=default;
    ~BridgeEncoder();
    BridgeEncoder(const BridgeEncoder&)=delete;
    BridgeEncoder& operator=(const BridgeEncoder&)=delete;
    bool prepare(ID3D11Device*,ID3D11DeviceContext*,const BridgeEncoderSettings&,const gbenc_sink_v1&);
    bool encode(ID3D11Texture2D*,uint64_t occurrence,uint64_t pts,uint64_t duration,bool force_idr=false);
    bool encode_retained(uint64_t generation,uint64_t occurrence,uint64_t pts,uint64_t duration,bool force_idr=false);
    void invalidate_retained(){retained_valid_=false;} // Owning worker only.
    bool retained_valid() const{return retained_valid_&&state_==GBENC_READY;}
    uint64_t retained_generation() const{return retained_generation_;}
    uint64_t retained_serial() const{return retained_serial_;}
    uint64_t source_copies() const{return source_copies_;}
    uint64_t repeats() const{return repeats_;}
    uint64_t last_serial() const{return last_serial_;}
    bool close(); /* Idempotent; false on quarantine. Never resumes a failed session. */
    // Single owner thread, including destruction and diagnostics. Publish only after
    // prepare; foreign-thread/reentrant calls refuse without changing owner state.
    // error() is a retriable validation error or first terminal session cause;
    // state/affinity refusals alone do not overwrite it. driver_error() retains the
    // most recent NVENC failure, including cleanup failure after a sink rejection.
    gbenc_state state() const{return state_;}
    gbenc_error_code error() const{return error_;}
    NVENCSTATUS driver_error() const{return driver_error_;}
    uint64_t submitted() const{return submitted_;}
    uint64_t completed() const{return completed_;}
    uint64_t delivered() const{return delivered_;}
};
