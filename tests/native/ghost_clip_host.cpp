/* Actual playback's portable acceptance, with model calls forbidden. No ROM/GL. */
#include "ghost_playback.h"
#include "ghost_pose.h"
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
namespace ghost_pose {
Result evaluate(sg_model*,const gs_record&,const gc_sample&,const ghost::WorldPass&,
                float,Scratch&,ghost_compositor::Pose&) {std::abort();}
}

static bool accepted(const std::string& path) {
    std::ifstream file(path,std::ios::binary|std::ios::ate);
    if(!file)std::exit(2);
    const auto length=file.tellg();
    if(length<std::streamoff(sizeof(gclip_header)) ||
       length>std::streamoff(sizeof(gclip_header)+uint64_t(GCLIP_MAX_RECORDS)*sizeof(gclip_frame)))return false;
    file.seekg(0);gclip_header header{};file.read(reinterpret_cast<char*>(&header),sizeof(header));
    // File-length checks protect the pointer passed to the real native validator.
    if(!header.record_count || header.record_count>GCLIP_MAX_RECORDS ||
       uint64_t(length)!=sizeof(header)+uint64_t(header.record_count)*sizeof(gclip_frame))return false;
    std::vector<gclip_frame> frames(header.record_count);
    file.read(reinterpret_cast<char*>(frames.data()),frames.size()*sizeof(gclip_frame));
    if(!file)std::exit(2);
    const auto* api=ghost_playback::api();
    const bool okay=api->prepare_clip(&header,frames.data())==GP_OK;
    api->revoke(GP_DISABLED);
    if(api->collect()!=GP_OK || api->release_worker()!=GP_OK)std::exit(3);
    return okay;
}
int main() {
    std::string path;while(std::getline(std::cin,path))std::cout<<(accepted(path)?1:0)<<'\n';
    return 0;
}
