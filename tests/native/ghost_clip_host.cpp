/* Actual playback's portable acceptance and the refused rows it would hide,
 * with model calls forbidden. No ROM/GL. */
#include "ghost_playback.h"
#include "ghost_clip_upgrade.h"
#include "ghost_pose.h"
#include "ghost_particle_pose.h"
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

extern "C" int32_t sg_model_open(const uint8_t*,size_t,sg_model**) {std::abort();}
extern "C" int32_t sg_model_close(sg_model**) {std::abort();}
extern "C" int32_t sg_model_detach(sg_model*) {std::abort();}
extern "C" int32_t sg_model_adopt(sg_model*) {std::abort();}
extern "C" int32_t sg_model_get_atlas(sg_model*,sg_atlas_view*) {std::abort();}
extern "C" int32_t sg_model_get_particles(sg_model*,sg_particle_view*) {std::abort();}
extern "C" int32_t sg_model_get_animation_frames(sg_model*,int32_t,int32_t*) {std::abort();}
namespace ghost_particle_pose {
Placed place(sg_model*,const sg_particle_view&,const gc_sample&,const gc_camera&,const float*,
             ghost_compositor::ParticleVertex*,ghost_compositor::Particles&) {std::abort();}
}
namespace ghost_pose {
Result evaluate(sg_model*,const gs_record&,const gc_sample&,const ghost::WorldPass&,
                float,Scratch&,ghost_compositor::Pose&) {std::abort();}
bool shadow(sg_model*,const gc_sample&,const float*,ghost_compositor::ShadowVertex*,uint8_t*,
            ghost_compositor::Shadow&) {std::abort();}
bool camera_relative(sg_model*,const gc_camera&,const float*,float*) {std::abort();}
}

static uint32_t hidden=0; // the refused rows playback hides; 0 for a refused clip
static bool accepted(const std::string& path) {
    std::ifstream file(path,std::ios::binary|std::ios::ate);
    if(!file)std::exit(2);
    const auto length=file.tellg();
    if(length<std::streamoff(sizeof(gclip_header)) ||
       length>std::streamoff(sizeof(gclip_header)+uint64_t(GCLIP_MAX_RECORDS)*sizeof(gclip_frame)))return false;
    file.seekg(0);std::vector<uint8_t> bytes(static_cast<size_t>(length));
    file.read(reinterpret_cast<char*>(bytes.data()),bytes.size());
    if(!file)std::exit(2);
    ghost_clip::upgrade_v4(bytes); // the worker's own GCLIP4 reading (ghost_assets::clip)
    gclip_header header{};std::memcpy(&header,bytes.data(),sizeof(header));
    // File-length checks protect the pointer passed to the real native validator.
    if(!header.record_count || header.record_count>GCLIP_MAX_RECORDS ||
       uint64_t(bytes.size())!=sizeof(header)+uint64_t(header.record_count)*sizeof(gclip_frame))return false;
    std::vector<gclip_frame> frames(header.record_count);
    std::memcpy(frames.data(),bytes.data()+sizeof(header),frames.size()*sizeof(gclip_frame));
    const auto* api=ghost_playback::api();
    const bool okay=api->prepare_clip(&header,frames.data())==GP_OK;
    hidden=okay?ghost_playback::hidden_refusals(header,frames.data()):0;
    api->revoke(GP_DISABLED);
    if(api->collect()!=GP_OK || api->release_worker()!=GP_OK)std::exit(3);
    return okay;
}
int main() {
    std::string path;
    while(std::getline(std::cin,path)) {
        hidden=0;const bool okay=accepted(path);
        std::cout<<(okay?1:0)<<' '<<hidden<<'\n';
    }
    return 0;
}
