#include "gpu_bridge_encoder.h"
#include "gpu_bridge_test_sink.h"
#include <string>
#include <vector>
#include <stdexcept>
#include <cstring>
#include <thread>
#include <stdio.h>
#define CHECK(x) do{if(!(x)){fprintf(stderr,"failure line=%d %s\n",__LINE__,#x);return false;}}while(0)
struct Driver {
    std::string mode;std::vector<std::string> calls;bool mapped=false,locked=false;unsigned sink_calls=0;
    uint64_t pts=0,duration=0;uint32_t force=0;uint8_t bytes[8]={0,0,0,1,0x65,0xaa,0xbb,0xcc};
};
static Driver*d;
static NVENCSTATUS NVENCAPI map_input(void*,NV_ENC_MAP_INPUT_RESOURCE*p){d->calls.push_back("map");if(d->mode=="map")return NV_ENC_ERR_GENERIC;if(d->mode=="map-partial"){d->mapped=true;p->mappedResource=(void*)4;return NV_ENC_ERR_GENERIC;}d->mapped=true;p->mappedResource=(void*)4;p->mappedBufferFmt=NV_ENC_BUFFER_FORMAT_ABGR;return NV_ENC_SUCCESS;}
static NVENCSTATUS NVENCAPI encode_picture(void*,NV_ENC_PIC_PARAMS*p){
 if(p->encodePicFlags&NV_ENC_PIC_FLAG_EOS){d->calls.push_back("eos");return (d->mode=="eos"||d->mode=="invalid-then-eos")?NV_ENC_ERR_GENERIC:NV_ENC_SUCCESS;}
 d->calls.push_back("encode");d->pts=p->inputTimeStamp;d->duration=p->inputDuration;d->force=p->encodePicFlags;
 if(d->mode=="encode")return NV_ENC_ERR_GENERIC;if(d->mode=="need-more")return NV_ENC_ERR_NEED_MORE_INPUT;return NV_ENC_SUCCESS;
}
static NVENCSTATUS NVENCAPI lock_output(void*,NV_ENC_LOCK_BITSTREAM*p){d->calls.push_back("lock");if(d->mode=="lock")return NV_ENC_ERR_GENERIC;d->locked=true;p->bitstreamBufferPtr=d->mode=="null"?nullptr:d->bytes;p->bitstreamSizeInBytes=d->mode=="oversize"?65:d->mode=="empty"?0:8;p->outputTimeStamp=d->pts+(d->mode=="pts");p->outputDuration=d->duration+(d->mode=="duration");p->pictureType=(d->force&NV_ENC_PIC_FLAG_FORCEIDR)?NV_ENC_PIC_TYPE_IDR:NV_ENC_PIC_TYPE_P;return NV_ENC_SUCCESS;}
static NVENCSTATUS NVENCAPI unlock_output(void*,NV_ENC_OUTPUT_PTR){d->calls.push_back("unlock");if(d->mode=="unlock")return NV_ENC_ERR_GENERIC;d->locked=false;memset(d->bytes,0xee,sizeof(d->bytes));return NV_ENC_SUCCESS;}
static NVENCSTATUS NVENCAPI unmap_input(void*,NV_ENC_INPUT_PTR){d->calls.push_back("unmap");if(d->mode=="unmap")return NV_ENC_ERR_GENERIC;d->mapped=false;return NV_ENC_SUCCESS;}
static NVENCSTATUS NVENCAPI destroy_output(void*,NV_ENC_OUTPUT_PTR){d->calls.push_back("destroy-output");return d->mode=="destroy-output"?NV_ENC_ERR_GENERIC:NV_ENC_SUCCESS;}
static NVENCSTATUS NVENCAPI unregister_input(void*,NV_ENC_REGISTERED_PTR){d->calls.push_back("unregister");return d->mode=="unregister"?NV_ENC_ERR_GENERIC:NV_ENC_SUCCESS;}
static NVENCSTATUS NVENCAPI destroy_encoder(void*){d->calls.push_back("destroy-encoder");return d->mode=="destroy-encoder"?NV_ENC_ERR_GENERIC:NV_ENC_SUCCESS;}
struct BridgeEncoderTestAccess {
 static void seed(BridgeEncoder&e,const gbenc_sink_v1&sink){e.api_.nvEncMapInputResource=map_input;e.api_.nvEncEncodePicture=encode_picture;e.api_.nvEncLockBitstream=lock_output;e.api_.nvEncUnlockBitstream=unlock_output;e.api_.nvEncUnmapInputResource=unmap_input;e.api_.nvEncDestroyBitstreamBuffer=destroy_output;e.api_.nvEncUnregisterResource=unregister_input;e.api_.nvEncDestroyEncoder=destroy_encoder;e.encoder_=(void*)1;e.registration_=(void*)2;e.output_=(void*)3;e.settings_=bridge_test_options(320,240);e.sink_=sink;e.packet_bytes_.resize(64);e.state_=GBENC_READY;e.owner_=GetCurrentThreadId();e.initialized_=true;}
 static bool submit(BridgeEncoder&e){return e.submit(42,90001,3001,false);}
};
struct Sink {BridgeEncoder*encoder=nullptr;bool valid=false;};
static int GBENC_CALL receive(void*user,const gbenc_packet_v1*p){
 auto*s=static_cast<Sink*>(user);d->calls.push_back("sink");++d->sink_calls;
 s->valid=!d->mapped&&!d->locked&&p->struct_size==sizeof(*p)&&p->version==GBENC_ABI_V1&&p->data!=d->bytes&&p->bytes==8&&p->data[4]==0x65&&p->data[7]==0xcc&&p->pts==90001&&p->duration==3001&&p->occurrence==42&&p->flags==GBENC_PACKET_KEYFRAME;
 s->valid=s->valid&&!s->encoder->close()&&!s->encoder->encode(nullptr,1,2,3);
 if(d->mode=="throw")throw std::runtime_error("rejected");return s->valid&&d->mode!="reject"?GBENC_SINK_ACCEPTED:GBENC_SINK_REJECTED;
}
static bool run(const char*mode){
 Driver driver;driver.mode=mode;d=&driver;BridgeEncoder encoder;Sink sink{&encoder};gbenc_sink_v1 descriptor{sizeof(descriptor),GBENC_ABI_V1,receive,&sink};BridgeEncoderTestAccess::seed(encoder,descriptor);
 if(driver.mode=="wrong-owner") {bool refused=false;std::thread other([&]{refused=!encoder.encode(nullptr,1,0,1)&&!encoder.close();});other.join();CHECK(refused&&encoder.state()==GBENC_READY&&encoder.error()==GBENC_OK&&driver.calls.empty());}
 const bool accepted=BridgeEncoderTestAccess::submit(encoder);
 if(driver.mode=="invalid-then-eos"){CHECK(!encoder.encode(nullptr,99,90002,1)&&encoder.error()==GBENC_INVALID_ARGUMENT&&encoder.state()==GBENC_READY);}
 const bool delayed=driver.mode=="invalid-then-eos"||driver.mode=="eos"||driver.mode=="destroy-output"||driver.mode=="unregister"||driver.mode=="destroy-encoder";
 if(driver.mode=="ok"||driver.mode=="wrong-owner"||delayed){CHECK(accepted&&sink.valid&&driver.sink_calls==1&&encoder.completed()==1&&encoder.delivered()==1);}
 else CHECK(!accepted);
 const bool unsafe=driver.mode=="map-partial"||driver.mode=="encode"||driver.mode=="need-more"||driver.mode=="lock"||driver.mode=="unlock"||driver.mode=="unmap";
 if(unsafe){CHECK(encoder.state()==GBENC_QUARANTINED&&driver.sink_calls==0);const auto calls=driver.calls;CHECK(!BridgeEncoderTestAccess::submit(encoder)&&!encoder.close()&&driver.calls==calls);}
 else if(delayed){CHECK(!encoder.close()&&encoder.state()==GBENC_QUARANTINED);const auto calls=driver.calls;CHECK(!encoder.close()&&driver.calls==calls);}
 else {CHECK(encoder.close()&&encoder.state()==GBENC_CLOSED&&!driver.mapped&&!driver.locked);CHECK(encoder.close());}
 if(driver.mode=="invalid-then-eos")CHECK(encoder.error()==GBENC_DRIVER_ERROR&&encoder.driver_error()==NV_ENC_ERR_GENERIC);
 if(driver.mode=="reject"||driver.mode=="throw")CHECK(sink.valid&&driver.sink_calls==1&&encoder.delivered()==0&&encoder.error()==GBENC_SINK_ERROR);
 if(driver.mode=="pts"||driver.mode=="duration"||driver.mode=="empty"||driver.mode=="oversize"||driver.mode=="null")CHECK(driver.sink_calls==0&&encoder.error()==GBENC_PACKET_ERROR);
 printf("PASS %s calls=",mode);for(const auto&call:driver.calls)printf("%s,",call.c_str());printf(" state=%d submitted=%llu completed=%llu\n",encoder.state(),encoder.submitted(),encoder.completed());return true;
}
int main(){unsigned count=0;for(const char*mode:{"ok","map","map-partial","encode","need-more","lock","unlock","unmap","pts","duration","empty","oversize","null","reject","throw","eos","invalid-then-eos","wrong-owner","destroy-output","unregister","destroy-encoder"}){if(!run(mode))return 1;++count;}printf("CPU packet lifecycle: %u cases passed; no GPU driver loaded\n",count);return 0;}
