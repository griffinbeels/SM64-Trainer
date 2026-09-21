#include "ghost_pose.h"
#include <cassert>
#include <cstdio>
#include <cstring>
static sg_pose applied{};
static float camera_x=0;
extern "C" int32_t sg_model_view(sg_model *,const float *position,const float *,int16_t,float *out) {
    camera_x=position[0];
    const float identity[16]={1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1};
    std::memcpy(out,identity,sizeof(identity));
    return SG_OK;
}
extern "C" int32_t sg_model_evaluate(sg_model *,const sg_pose *pose,sg_mesh *mesh) {
    applied=*pose; assert(mesh->vertex_capacity>=3);
    for(unsigned i=0;i<3;++i) {
        mesh->position[3*i]=float(i+1); mesh->position[3*i+1]=2; mesh->position[3*i+2]=3;
        mesh->normal[3*i]=0; mesh->normal[3*i+1]=1; mesh->normal[3*i+2]=0;
        mesh->color[3*i]=1; mesh->color[3*i+1]=0; mesh->color[3*i+2]=0;
        mesh->uv[2*i]=0.25f; mesh->uv[2*i+1]=0.5f;
        mesh->material[i]=SG_MATERIAL_ALPHA_MIX;
    }
    mesh->vertex_count=3; mesh->error_flags=0; return SG_OK;
}
int main() {
    gs_record record{}; record.bytes=sizeof(record); record.version=GS_VERSION; record.configuration=2;
    record.world.status=static_cast<uint32_t>(ghost::PassStatus::Ready);
    auto &saved=record.sample;
    saved.bytes=sizeof(saved); saved.version=GC_VERSION; saved.epoch=1;
    saved.sequence=4; saved.dlist_serial=4; saved.level=9; saved.area=1;
    saved.valid_fields=GC_VALID_CLOCK | GC_VALID_POSE;
    saved.area_update_counter=123; saved.global_timer=777;
    saved.pose.root_kind=GC_ROOT_TRS;
    saved.pose.action=1; saved.pose.body_action=2;
    saved.pose.head_angles[0]=10; saved.pose.head_angles[1]=20; saved.pose.head_angles[2]=30;
    gc_sample live=saved; live.epoch=3; live.dlist_serial=20; live.sequence=20;
    live.valid_fields=GC_VALID_CLOCK | GC_VALID_CAMERA; live.camera.position[0]=42;
    ghost::WorldPass world{}; world.epoch=3; world.context_generation=2; world.list_serial=20;
    world.projection[0]=2; world.projection[5]=3; world.projection[10]=4; world.projection[15]=1;
    world.target.screen_scale[0]=0.5f; world.target.screen_scale[1]=-1;
    static ghost_pose::Scratch scratch;
    ghost_compositor::Pose pose{};
    using ghost_pose::Result;
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::ok);
    assert(applied.action==1 && applied.body_action==2 && applied.area_update_counter==123);
    assert(applied.head_rotation[0]==20 && applied.head_rotation[1]==30 && applied.head_rotation[2]==10);
    assert(camera_x==42 && pose.flip_y==1 && pose.count==3 && pose.opacity==0.5f);
    assert(pose.view_projection[0]==1 && pose.view_projection[5]==-3);
    assert(pose.vertices[2].position[0]==3 && pose.vertices[2].uv[0]==0.25f);
    assert(pose.vertices[2].material==ghost_compositor::Material::alpha_mix);
    live.area=2;
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::wrong_scene);
    assert(!pose.vertices && !pose.count);
    live.area=1; world.list_serial=21;
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::wrong_occurrence);
    world.list_serial=20;
    record.world.status=static_cast<uint32_t>(ghost::PassStatus::Interrupted);
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::missing_pose);
    record.world.status=static_cast<uint32_t>(ghost::PassStatus::Ready);
    assert(ghost_pose::evaluate(nullptr,record,live,world,1.1f,scratch,pose)==Result::unsupported_pose);
    assert(!pose.vertices && !pose.count);
    std::puts("recorded pose to live camera mapping passed");
}
