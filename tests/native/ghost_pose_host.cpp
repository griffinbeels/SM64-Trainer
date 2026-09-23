#include "ghost_pose.h"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <cmath>
static sg_pose applied{};
static float camera_x=0;
static float fake_normal[3]={0,1,0};static uint32_t fake_flags=0;
extern "C" int32_t sg_model_view(sg_model *,const float *position,const float *,int16_t,float *out) {
    camera_x=position[0];
    const float identity[16]={1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1};
    std::memcpy(out,identity,sizeof(identity));
    if(position[0]==-5) {
        const float turned[16]={0,0,-1,0,0,1,0,0,1,0,0,0,10,20,30,1};
        std::memcpy(out,turned,sizeof(turned));
    }
    return SG_OK;
}
extern "C" int32_t sg_model_evaluate(sg_model *,const sg_pose *pose,sg_mesh *mesh) {
    applied=*pose; assert(mesh->vertex_capacity>=3);
    for(unsigned i=0;i<3;++i) {
        mesh->position[3*i]=float(i+1); mesh->position[3*i+1]=2; mesh->position[3*i+2]=3;
        mesh->normal[3*i]=fake_normal[0]; mesh->normal[3*i+1]=fake_normal[1]; mesh->normal[3*i+2]=fake_normal[2];
        mesh->color[3*i]=1; mesh->color[3*i+1]=0; mesh->color[3*i+2]=0;
        mesh->uv[2*i]=0.25f; mesh->uv[2*i+1]=0.5f;
        mesh->material[i]=SG_MATERIAL_ALPHA_MIX|fake_flags;
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
    // Historical framebuffer status is retained, while the current camera and
    // occurrence still determine whether the recorded pose can be projected.
    for(uint32_t status=0;status<=static_cast<uint32_t>(ghost::PassStatus::InvalidIdentity);++status) {
        record.world.status=status;
        assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::ok);
        assert(record.world.status==status && camera_x==42 && pose.count==3);
    }
    // act_disappeared: the game never advanced the hidden Mario's animation
    // (frame -1). The ghost draws nothing and the model is not consulted.
    saved.pose.hidden=1;saved.pose.animation_frame=-1;applied={};
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::ok);
    assert(!pose.count && !applied.fields && pose.opacity==0.5f);
    saved.pose.hidden=0;saved.pose.animation_frame=0;
    // Vanish cap: the recorded envcolor alpha (modelState 0x100 | alpha)
    // multiplies the user's opacity; metal alone does not.
    saved.pose.model_state=0x180;
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::ok);
    assert(pose.count==3 && std::fabs(pose.opacity-0.5f*128.0f/255.0f)<1e-6f && applied.model_state==0x180);
    saved.pose.model_state=0x200;
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::ok);
    assert(pose.opacity==0.5f && applied.model_state==0x200);
    // Metal: texture-generated coordinates follow the LIVE view's eye-space
    // normal exactly as GLideN64 does for G_TEXTURE_GEN on the 64x32 metal
    // texture (atlas cell 0 of 11); the model's own uv is replaced.
    fake_flags=SG_MATERIAL_TEXGEN;fake_normal[0]=1;fake_normal[1]=0;
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::ok);
    assert(pose.vertices[0].material==ghost_compositor::Material::alpha_mix);
    assert(std::fabs(pose.vertices[0].uv[0]-62.0f/64.0f/11.0f)<1e-6f && std::fabs(pose.vertices[0].uv[1]-15.5f/64.0f)<1e-6f);
    live.camera.position[0]=-5; // the fake turns this view: +x now faces -z
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::ok);
    assert(std::fabs(pose.vertices[0].uv[0]-31.0f/64.0f/11.0f)<1e-6f && std::fabs(pose.vertices[0].uv[1]-15.5f/64.0f)<1e-6f);
    live.camera.position[0]=42;fake_flags=0;fake_normal[0]=0;fake_normal[1]=1;
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::ok);
    assert(pose.vertices[0].uv[0]==0.25f); // untextured-generation vertices keep their uv
    saved.pose.model_state=0;
    saved.valid_fields&=~GC_VALID_POSE;
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::missing_pose);
    assert(!pose.vertices && !pose.count);
    saved.valid_fields|=GC_VALID_POSE;
    live.valid_fields&=~GC_VALID_CAMERA;
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::missing_pose);
    assert(!pose.vertices && !pose.count);
    live.valid_fields|=GC_VALID_CAMERA;
    world.context_generation=0;
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::wrong_occurrence);
    assert(!pose.vertices && !pose.count);
    world.context_generation=2;
    assert(ghost_pose::evaluate(nullptr,record,live,world,1.1f,scratch,pose)==Result::unsupported_pose);
    assert(!pose.vertices && !pose.count);
    saved.valid_fields|=GC_VALID_CAMERA;
    saved.camera.position[0]=-5;
    saved.pose.root_kind=GC_ROOT_SCALED_MODELVIEW;
    for(unsigned i=0;i<3;++i)saved.pose.scale[i]=float(i+1);
    const float executed[16]={0,0,-.25f,0,.3f,.4f,0,0,.6f,-.45f,0,0,310,220,-70,1};
    const float expected[16]={1,0,0,0,0,.8f,.6f,0,0,-.6f,.8f,0,100,200,300,1};
    std::memcpy(saved.pose.root_matrix,executed,sizeof(executed));
    assert(ghost_pose::evaluate(nullptr,record,live,world,.5f,scratch,pose)==Result::ok);
    assert(applied.fields&SG_POSE_THROW_MATRIX);
    for(unsigned i=0;i<16;++i)assert(std::fabs(applied.throw_matrix[i]-expected[i])<.00001f);
    assert(applied.scale[1]==2 && applied.scale[2]==3 && camera_x==42);
    // Changing the LIVE view must not change the recovered world root.
    live.camera.position[0]=43;
    assert(ghost_pose::evaluate(nullptr,record,live,world,.5f,scratch,pose)==Result::ok);
    for(unsigned i=0;i<16;++i)assert(std::fabs(applied.throw_matrix[i]-expected[i])<.00001f);
    assert(camera_x==43);
    saved.valid_fields&=~GC_VALID_CAMERA;
    assert(ghost_pose::evaluate(nullptr,record,live,world,.5f,scratch,pose)==Result::unsupported_pose);
    assert(!pose.vertices && !pose.count);
    saved.valid_fields|=GC_VALID_CAMERA;saved.pose.scale[0]=0;
    assert(ghost_pose::evaluate(nullptr,record,live,world,.5f,scratch,pose)==Result::unsupported_pose);
    saved.pose.scale[0]=1;saved.pose.root_matrix[15]=0;
    assert(ghost_pose::evaluate(nullptr,record,live,world,.5f,scratch,pose)==Result::unsupported_pose);
    // Shadow: executed G_MTX = translate(100,200,300) under the RECORDED turned
    // camera; its vertices land in world space under the live projection.
    {
        sg_model *model=reinterpret_cast<sg_model*>(&applied); // any non-null handle
        saved.valid_fields|=GC_VALID_SHADOW;
        const float shadow_executed[16]={0,0,-1,0, 0,1,0,0, 1,0,0,0, 310,220,-70,1};
        std::memcpy(saved.shadow.matrix,shadow_executed,sizeof(shadow_executed));
        for(unsigned v=0;v<9;++v){saved.shadow.position[v][0]=int16_t(v);saved.shadow.alpha[v]=uint8_t(v*20);}
        saved.shadow.position[0][0]=-25;saved.shadow.position[0][1]=-3;saved.shadow.position[0][2]=40;
        saved.shadow.uv[3][0]=480;saved.shadow.uv[3][1]=-480;saved.shadow.alpha[4]=180;
        saved.shadow.texture[0]=0x7e;saved.shadow.texture[127]=0x0f;
        ghost_compositor::ShadowVertex vertices[24]{};uint8_t texture[256]{};ghost_compositor::Shadow shadow{};
        float projection[16]{};projection[0]=7;projection[15]=1;
        assert(ghost_pose::shadow(model,saved,projection,vertices,texture,shadow));
        assert(camera_x==-5 && shadow.count==24 && shadow.vertices==vertices && shadow.texture_ia8==texture);
        assert(shadow.view_projection[0]==7 && shadow.view_projection[15]==1);
        const auto &corner=vertices[0]; // triangle 0 starts at vertex 0
        assert(std::fabs(corner.position[0]-75)<1e-4f && std::fabs(corner.position[1]-197)<1e-4f
               && std::fabs(corner.position[2]-340)<1e-4f);
        assert(std::fabs(vertices[1].position[0]-103)<1e-4f); // triangle 0's second vertex is 3
        assert(std::fabs(vertices[1].uv[0]-(15.0f*65535.0f/65536.0f+.5f)/16.0f)<1e-6f);
        assert(std::fabs(vertices[1].uv[1]-(-15.0f*65535.0f/65536.0f+.5f)/16.0f)<1e-6f);
        assert(std::fabs(vertices[2].alpha-180.0f/255.0f)<1e-6f); // centre vertex 4
        for(unsigned i=0;i<24;++i)assert(vertices[i].alpha==float(saved.shadow.alpha[gc_shadow_triangles[i]])/255.0f);
        assert(texture[0]==7 && texture[1]==14 && texture[254]==0 && texture[255]==15);
        saved.valid_fields&=~GC_VALID_CAMERA;
        assert(!ghost_pose::shadow(model,saved,projection,vertices,texture,shadow) && !shadow.count);
        saved.valid_fields|=GC_VALID_CAMERA;saved.shadow.matrix[0]=NAN;
        assert(!ghost_pose::shadow(model,saved,projection,vertices,texture,shadow) && !shadow.vertices);
        saved.shadow.matrix[0]=0;saved.valid_fields&=~GC_VALID_SHADOW;
        assert(!ghost_pose::shadow(model,saved,projection,vertices,texture,shadow));
        assert(!ghost_pose::shadow(nullptr,saved,projection,vertices,texture,shadow));
    }
    // Sound placement: a world point under the same live look-at product as drawing.
    {
        gc_camera camera{};camera.position[0]=-5;const float point[3]={1,2,3};float out[3]{};
        assert(ghost_pose::camera_relative(nullptr,camera,point,out));
        assert(out[0]==13 && out[1]==22 && out[2]==29);
    }
    std::puts("recorded pose to live camera mapping passed");
}
