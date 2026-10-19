/* One packet/lifetime contract; vendor policies stay in their own backend. */
#pragma once
#include "gpu_bridge_encoder.h"
#include "gpu_mf_encoder.h"
class PlatformEncoder {
    BridgeEncoder nvenc_;MfEncoder windows_;
    uint32_t backend_=GBENC_BACKEND_NVENC;gbenc_error_code validation_=GBENC_OK;
    bool chosen_=false;
public:
    bool prepare(ID3D11Device*,ID3D11DeviceContext*,const gbenc_options_v1&,const gbenc_sink_v1&);
    bool encode(ID3D11Texture2D*t,uint64_t occurrence,uint64_t pts,uint64_t duration,bool idr=false){return backend_==GBENC_BACKEND_WINDOWS?windows_.encode(t,occurrence,pts,duration,idr):nvenc_.encode(t,occurrence,pts,duration,idr);}
    bool encode_retained(uint64_t generation,uint64_t occurrence,uint64_t pts,uint64_t duration,bool idr=false){return backend_==GBENC_BACKEND_WINDOWS?windows_.encode_retained(generation,occurrence,pts,duration,idr):nvenc_.encode_retained(generation,occurrence,pts,duration,idr);}
    void invalidate_retained(){if(backend_==GBENC_BACKEND_WINDOWS)windows_.invalidate_retained();else nvenc_.invalidate_retained();}
    bool close(){return backend_==GBENC_BACKEND_WINDOWS?windows_.close():nvenc_.close();}
#define PLATFORM_GET(type,name) type name()const{return backend_==GBENC_BACKEND_WINDOWS?windows_.name():nvenc_.name();}
    PLATFORM_GET(bool,retained_valid)
    PLATFORM_GET(uint64_t,retained_generation)
    PLATFORM_GET(uint64_t,retained_serial)
    PLATFORM_GET(uint64_t,source_copies)
    PLATFORM_GET(uint64_t,repeats)
    PLATFORM_GET(uint64_t,last_serial)
    PLATFORM_GET(gbenc_state,state)
    PLATFORM_GET(uint32_t,driver_error)
    PLATFORM_GET(uint64_t,submitted)
    PLATFORM_GET(uint64_t,completed)
    PLATFORM_GET(uint64_t,delivered)
#undef PLATFORM_GET
    gbenc_error_code error()const{return validation_!=GBENC_OK?validation_:backend_==GBENC_BACKEND_WINDOWS?windows_.error():nvenc_.error();}
};
