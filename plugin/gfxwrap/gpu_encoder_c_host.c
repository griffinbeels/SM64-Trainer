#include "gpu_bridge_encoder_api.h"
static int GBENC_CALL callback(void*user,const gbenc_packet_v1*p){return user&&p?GBENC_SINK_ACCEPTED:GBENC_SINK_REJECTED;}
int api_c_compile(void){gbenc_sink_v1 sink={sizeof(gbenc_sink_v1),GBENC_ABI_V1,callback,0};gbenc_options_v1 options={0};return (int)sink.version+(int)options.struct_size;}
