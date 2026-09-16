/* Standalone witness utility only. Never include this in a production encoder owner. */
#pragma once
#include "gpu_bridge_encoder_api.h"
#include <stdio.h>
static gbenc_options_v1 bridge_test_options(unsigned width,unsigned height) {
    gbenc_options_v1 s{};s.struct_size=sizeof(s);s.version=GBENC_ABI_V1;
    s.width=width;s.height=height;s.nominal_fps_num=30;s.nominal_fps_den=1;
    s.profile=GBENC_H264_HIGH;s.preset=GBENC_PRESET_P4;s.tuning=GBENC_TUNE_HQ;s.rate_control=GBENC_RC_VBR;s.b_frames=0;
    s.cq=20;s.max_bitrate=30000000;s.vbv_buffer_bits=30000000;s.gop_frames=60;
    s.initial_qp_p=26;s.initial_qp_i=21;s.initial_qp_b=34;s.idr_interval_ticks=180000;
    s.input_format=GBENC_INPUT_RGBA8;s.signal_color=1;s.full_range=0;s.matrix=5;s.primaries=2;s.transfer=2;s.max_packet_bytes=1024*1024;
    return s;
}
class BridgeTestFileSink {
    FILE* video_=nullptr;FILE* packets_=nullptr;
    static int GBENC_CALL receive(void*user,const gbenc_packet_v1*packet) {
        auto*self=static_cast<BridgeTestFileSink*>(user);
        if(!packet||packet->struct_size!=sizeof(*packet)||packet->version!=GBENC_ABI_V1||!packet->data||!packet->bytes||
           !self||!self->video_||!self->packets_)return GBENC_SINK_REJECTED;
        if(fwrite(packet->data,1,packet->bytes,self->video_)!=packet->bytes)return GBENC_SINK_REJECTED;
        if(fprintf(self->packets_,"%llu,%llu,%llu,%u,%u\n",packet->occurrence,packet->pts,packet->duration,packet->bytes,
                   unsigned((packet->flags&GBENC_PACKET_KEYFRAME)!=0))<0)return GBENC_SINK_REJECTED;
        return GBENC_SINK_ACCEPTED;
    }
public:
    ~BridgeTestFileSink(){close();}
    bool open(const char*video_path,const char*packet_path) {
        if(video_||packets_)return false;
        video_=fopen(video_path,"wb");packets_=fopen(packet_path,"w");
        if(!video_||!packets_){close();return false;}
        return fprintf(packets_,"occurrence,pts,duration,bytes,idr\n")>=0;
    }
    gbenc_sink_v1 descriptor(){return {sizeof(gbenc_sink_v1),GBENC_ABI_V1,&receive,this};}
    bool close(){bool ok=true;if(video_){ok=fclose(video_)==0;video_=nullptr;}if(packets_){ok=(fclose(packets_)==0)&&ok;packets_=nullptr;}return ok;}
};
