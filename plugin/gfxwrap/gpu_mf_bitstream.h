/* A hardware MFT may expose H.264 configuration only on its output type.
   Normalize that boundary once; the shared mux still gets self-describing IDRs. */
#pragma once
#include <mfapi.h>
#include <mferror.h>
#include <vector>
#include <stdint.h>
#include <string.h>
namespace mf_gpu {
constexpr UINT32 sequence_header_limit=64u<<10;
struct H264Units {
    std::vector<uint8_t> sps,pps;
    size_t after_aud=0;
    bool idr=false,vcl=false;
};
inline size_t start_code(const uint8_t*data,size_t size,size_t at){
    if(at+3<=size&&data[at]==0&&data[at+1]==0){
        if(data[at+2]==1)return 3;
        if(at+4<=size&&data[at+2]==0&&data[at+3]==1)return 4;
    }return 0;
}
inline HRESULT h264_units(const uint8_t*data,size_t size,bool configuration,H264Units&out){
    if(!data||!size||!start_code(data,size,0))return MF_E_INVALID_STREAM_DATA;
    try{
        size_t at=0;unsigned units=0;
        while(at<size){
            const size_t prefix=start_code(data,size,at);
            if(!prefix||at+prefix>=size||++units>4096)return MF_E_INVALID_STREAM_DATA;
            const size_t body=at+prefix;size_t end=body+1;
            while(end<size&&!start_code(data,size,end))++end;
            size_t payload_end=end;while(payload_end>body+1&&data[payload_end-1]==0)--payload_end;
            const unsigned kind=data[body]&31;
            if((data[body]&0x80)||!kind||kind>23||payload_end<=body+1||
               (configuration&&kind!=7&&kind!=8))return MF_E_INVALID_STREAM_DATA;
            if(kind==7||kind==8){
                if((kind==7&&payload_end-body<4)||(kind==8&&payload_end-body<2))return MF_E_INVALID_STREAM_DATA;
                auto&bytes=kind==7?out.sps:out.pps;
                if(bytes.size()+4+payload_end-body>sequence_header_limit)return MF_E_INVALID_STREAM_DATA;
                const uint8_t code[]={0,0,0,1};bytes.insert(bytes.end(),code,code+4);
                bytes.insert(bytes.end(),data+body,data+payload_end);
            }
            if(kind==9&&at==out.after_aud)out.after_aud=end;
            out.idr|=kind==5;out.vcl|=kind==1||kind==5;at=end;
        }
    }catch(...){return E_OUTOFMEMORY;}
    return configuration&&(out.sps.empty()||out.pps.empty())?MF_E_INVALID_STREAM_DATA:S_OK;
}
struct H264Configuration {
    std::vector<uint8_t> sps,pps;
    std::vector<uint8_t> advertised_sps,advertised_pps;
    bool sealed=false;
    HRESULT update(const H264Units&units){
        if(units.sps.empty()||units.pps.empty())return MF_E_INVALID_STREAM_DATA;
        if(sealed&&(sps!=units.sps||pps!=units.pps))return MF_E_INVALIDMEDIATYPE;
        try{sps=units.sps;pps=units.pps;}catch(...){return E_OUTOFMEMORY;}return S_OK;
    }
    HRESULT read_type(IMFMediaType*type){
        UINT32 size=0;HRESULT hr=type->GetBlobSize(MF_MT_MPEG_SEQUENCE_HEADER,&size);
        if(hr==MF_E_ATTRIBUTENOTFOUND)return S_OK;
        if(FAILED(hr))return hr;if(!size||size>sequence_header_limit)return MF_E_INVALID_STREAM_DATA;
        std::vector<uint8_t> bytes;try{bytes.resize(size);}catch(...){return E_OUTOFMEMORY;}
        UINT32 actual=0;hr=type->GetBlob(MF_MT_MPEG_SEQUENCE_HEADER,bytes.data(),size,&actual);
        if(FAILED(hr))return hr;if(actual!=size)return MF_E_INVALID_STREAM_DATA;
        H264Units units;hr=h264_units(bytes.data(),bytes.size(),true,units);
        if(FAILED(hr))return hr;
        if(sealed){
            const auto&a=advertised_sps.empty()?sps:advertised_sps;
            const auto&b=advertised_pps.empty()?pps:advertised_pps;
            if(a!=units.sps||b!=units.pps)return MF_E_INVALIDMEDIATYPE;
        }
        try{advertised_sps=units.sps;advertised_pps=units.pps;}catch(...){return E_OUTOFMEMORY;}
        return sealed?S_OK:update(units);
    }
    HRESULT normalize(std::vector<uint8_t>&packet,DWORD&size,bool key){
        if(!size||size>packet.size())return MF_E_INVALID_STREAM_DATA;
        H264Units units;HRESULT hr=h264_units(packet.data(),size,false,units);if(FAILED(hr))return hr;
        if(!units.vcl||units.idr!=key)return MF_E_INVALID_STREAM_DATA;
        if(!units.sps.empty()&&!units.pps.empty()){
            // Some real hardware advertises provisional SPS/PPS on its type.
            // The first actual packet owns the bytes the archive will decode.
            if(sealed&&((!sps.empty()&&sps!=units.sps)||(!pps.empty()&&pps!=units.pps)))return MF_E_INVALIDMEDIATYPE;
            hr=update(units);if(FAILED(hr))return hr;
        }else if((!units.sps.empty()&&units.sps!=sps)||(!units.pps.empty()&&units.pps!=pps))return MF_E_INVALIDMEDIATYPE;
        if(key){
            if(sps.empty()||pps.empty())return MF_E_INVALID_STREAM_DATA;
            const size_t a=units.sps.empty()?sps.size():0,b=units.pps.empty()?pps.size():0;
            if(a+b>packet.size()-size)return MF_E_BUFFERTOOSMALL;
            if(a+b){
                // An access-unit delimiter stays first. All parameter sets
                // precede the slice; already-present parameter sets stay single.
                const size_t at=units.after_aud;
                memmove(packet.data()+at+a+b,packet.data()+at,size-at);
                if(a)memcpy(packet.data()+at,sps.data(),a);
                if(b)memcpy(packet.data()+at+a,pps.data(),b);
                size+=DWORD(a+b);
            }
        }
        sealed=true;return S_OK;
    }
};

struct Av1Units {
    std::vector<uint8_t> sequence;
    size_t after_delimiter=0;
    bool frame=false;
};
// AV1 low-overhead OBUs have a bounded LEB128 payload length. Accept that
// documented representation only; never reinterpret av1C bytes as raw OBUs.
inline HRESULT av1_units(const uint8_t*data,size_t size,bool configuration,Av1Units&out){
    if(!data||!size)return MF_E_INVALID_STREAM_DATA;
    try{
        size_t at=0;unsigned units=0;
        while(at<size){
            if(++units>4096)return MF_E_INVALID_STREAM_DATA;
            const size_t begin=at;const uint8_t header=data[at++];const unsigned kind=(header>>3)&15;
            if((header&0x81)||!(header&2))return MF_E_INVALID_STREAM_DATA;
            if(header&4){if(at==size||data[at]||(kind==1||kind==2))return MF_E_INVALID_STREAM_DATA;++at;}
            uint64_t length=0;unsigned shift=0;bool complete=false;
            for(unsigned i=0;i<8&&at<size;++i){const uint8_t octet=data[at++];length|=uint64_t(octet&127)<<shift;
                if(!(octet&128)){complete=true;break;}shift+=7;}
            if(!complete||length>size-at||(!length&&kind!=2&&kind!=15))return MF_E_INVALID_STREAM_DATA;
            const size_t end=at+size_t(length);
            if(configuration&&kind!=1&&kind!=5&&kind!=15)return MF_E_INVALID_STREAM_DATA;
            if(kind==1){
                if(out.frame||end-begin>sequence_header_limit)return MF_E_INVALID_STREAM_DATA;
                std::vector<uint8_t> sequence(data+begin,data+end);
                if(!out.sequence.empty()&&out.sequence!=sequence)return MF_E_INVALIDMEDIATYPE;
                out.sequence=std::move(sequence);
            }
            if(kind==2){if(length||begin!=out.after_delimiter)return MF_E_INVALID_STREAM_DATA;out.after_delimiter=end;}
            if(kind==3||kind==6){if(out.frame)return MF_E_INVALID_STREAM_DATA;out.frame=true;}at=end;
        }
    }catch(...){return E_OUTOFMEMORY;}
    return configuration&&out.sequence.empty()?MF_E_INVALID_STREAM_DATA:S_OK;
}
struct Av1Configuration {
    std::vector<uint8_t> sequence,advertised;bool sealed=false;
    HRESULT update(const Av1Units&units){
        if(units.sequence.empty())return MF_E_INVALID_STREAM_DATA;
        if(sealed&&sequence!=units.sequence)return MF_E_INVALIDMEDIATYPE;
        try{sequence=units.sequence;}catch(...){return E_OUTOFMEMORY;}return S_OK;
    }
    HRESULT read_type(IMFMediaType*type){
        UINT32 size=0;HRESULT hr=type->GetBlobSize(MF_MT_MPEG_SEQUENCE_HEADER,&size);
        if(hr==MF_E_ATTRIBUTENOTFOUND)return S_OK;if(FAILED(hr))return hr;
        if(!size||size>sequence_header_limit)return MF_E_INVALID_STREAM_DATA;
        std::vector<uint8_t> bytes;try{bytes.resize(size);}catch(...){return E_OUTOFMEMORY;}
        UINT32 actual=0;hr=type->GetBlob(MF_MT_MPEG_SEQUENCE_HEADER,bytes.data(),size,&actual);
        if(FAILED(hr))return hr;if(actual!=size)return MF_E_INVALID_STREAM_DATA;
        // av1C is a different representation, not a packet prefix. Fall back
        // by codec before input admission rather than reconstructing it.
        if(size>=4&&bytes[0]==0x81)return MF_E_UNSUPPORTED_REPRESENTATION;
        Av1Units units;hr=av1_units(bytes.data(),bytes.size(),true,units);
        if(FAILED(hr))return hr;
        if(sealed&&(advertised.empty()?sequence:advertised)!=units.sequence)return MF_E_INVALIDMEDIATYPE;
        try{advertised=units.sequence;}catch(...){return E_OUTOFMEMORY;}
        return sealed?S_OK:update(units);
    }
    HRESULT normalize(std::vector<uint8_t>&packet,DWORD&size,bool key){
        if(!size||size>packet.size())return MF_E_INVALID_STREAM_DATA;
        Av1Units units;HRESULT hr=av1_units(packet.data(),size,false,units);if(FAILED(hr))return hr;
        if(!units.frame)return MF_E_INVALID_STREAM_DATA;
        if(!units.sequence.empty()){
            if(sealed&&!sequence.empty()&&sequence!=units.sequence)return MF_E_INVALIDMEDIATYPE;
            hr=update(units);if(FAILED(hr))return hr;
        }
        if(key&&units.sequence.empty()){
            if(sequence.empty())return MF_E_INVALID_STREAM_DATA;
            if(sequence.size()>packet.size()-size)return MF_E_BUFFERTOOSMALL;
            const size_t at=units.after_delimiter;
            memmove(packet.data()+at+sequence.size(),packet.data()+at,size-at);
            memcpy(packet.data()+at,sequence.data(),sequence.size());size+=DWORD(sequence.size());
        }
        sealed=true;return S_OK;
    }
};
}
