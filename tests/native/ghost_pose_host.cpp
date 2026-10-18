#include "ghost_pose.h"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <cmath>
#include <initializer_list>
static sg_pose applied{};
static float camera_x=0;
static float fake_normal[3]={0,1,0};static uint32_t fake_flags=0;static bool refuse_held=false;
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
// Mario's cap light as his ROM list loads it: diffuse 255,0,0, ambient
// 127,0,0, direction (40,40,40) in eye space.
static uint32_t fake_lights=1;
extern "C" int32_t sg_model_get_body_lights(sg_model *,sg_body_lights *out) {
    *out={};out->count=fake_lights;
    out->lights[0]={{255,0,0},{127,0,0},{40,40,40}};
    return SG_OK;
}
// A held object's own light (diffuse 0,0,255, ambient 0,0,100); its
// vertices follow Mario's three, coloured by that diffuse.
static bool fake_held_lights=true;static float held_blue=1;
extern "C" int32_t sg_model_get_held_lights(sg_model *,uint32_t,sg_body_lights *out) {
    *out={};out->count=fake_held_lights?1:0;
    out->lights[0]={{0,0,255},{0,0,100},{40,40,40}};
    return SG_OK;
}
// A shell's two walls, each counter-clockwise seen from its front (the
// model's winding): A at z=a_z facing +z, B at z=b_z facing -z.
// cull[t]: its list left G_CULL_BACK on.
static bool two_faced=false;static bool cull[2]={true,true};static float a_z=-5,b_z=-6;
extern "C" int32_t sg_model_evaluate(sg_model *,const sg_pose *pose,sg_mesh *mesh) {
    applied=*pose; assert(mesh->vertex_capacity>=6);
    if(two_faced) {
        const float at[6][3]={{0,0,a_z},{1,0,a_z},{0,1,a_z},{0,0,b_z},{0,1,b_z},{1,0,b_z}};
        for(unsigned i=0;i<6;++i) {
            for(unsigned k=0;k<3;++k){mesh->position[3*i+k]=at[i][k];mesh->normal[3*i+k]=fake_normal[k];}
            mesh->color[3*i]=1; mesh->color[3*i+1]=0; mesh->color[3*i+2]=0;
            mesh->uv[2*i]=0.25f; mesh->uv[2*i+1]=0.5f;
            mesh->material[i]=SG_MATERIAL_ALPHA_MIX|(cull[i/3]?uint32_t(SG_MATERIAL_CULL_BACK):0u);
        }
        mesh->vertex_count=6; mesh->error_flags=0; mesh->held_first_vertex=6;
        return SG_OK;
    }
    if(refuse_held && (pose->fields&SG_POSE_HELD)) return SG_INVALID_ANIMATION;
    for(unsigned i=0;i<3;++i) {
        mesh->position[3*i]=float(i+1); mesh->position[3*i+1]=2; mesh->position[3*i+2]=3;
        mesh->normal[3*i]=fake_normal[0]; mesh->normal[3*i+1]=fake_normal[1]; mesh->normal[3*i+2]=fake_normal[2];
        mesh->color[3*i]=1; mesh->color[3*i+1]=0; mesh->color[3*i+2]=0;
        mesh->uv[2*i]=0.25f; mesh->uv[2*i+1]=0.5f;
        mesh->material[i]=SG_MATERIAL_ALPHA_MIX|fake_flags;
    }
    mesh->vertex_count=3; mesh->error_flags=0; mesh->held_first_vertex=3;
    if(pose->fields&SG_POSE_HELD) {
        for(unsigned i=3;i<6;++i) {
            for(unsigned k=0;k<3;++k){mesh->position[3*i+k]=mesh->position[3*(i-3)+k];mesh->normal[3*i+k]=mesh->normal[3*(i-3)+k];}
            mesh->color[3*i]=0; mesh->color[3*i+1]=0; mesh->color[3*i+2]=held_blue;
            if(mesh->ambient){mesh->ambient[3*i]=0;mesh->ambient[3*i+1]=0;mesh->ambient[3*i+2]=100.0f/255.0f;}
            mesh->uv[2*i]=0.25f; mesh->uv[2*i+1]=0.5f; mesh->material[i]=SG_MATERIAL_ALPHA_MIX;
        }
        mesh->vertex_count=6;
    }
    return SG_OK;
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
    // A recorded ghost keeps its own cap state even when live Mario has the
    // opposite state. Start capless, recover, lose, recover; no latched default.
    for (uint8_t cap : {uint8_t(1), uint8_t(0), uint8_t(1), uint8_t(0), uint8_t(2), uint8_t(3), uint8_t(0)}) {
        saved.pose.cap_state=cap; live.pose.cap_state=cap^1;
        assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::ok);
        assert(applied.cap_state==cap);
    }
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
    // Vanish alpha/noise stays on Mario's retained body vertices. Ordinary
    // metal and expiry flicker have no noise despite raw cap flags.
    saved.pose.model_state=0x180;
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::ok);
    assert(pose.count==3 && pose.body_count==3 && pose.opacity==0.5f
        && std::fabs(pose.body_alpha-128.0f/255.0f)<1e-6f && pose.noise_alpha
        && pose.noise_seed==777 && pose.noise_scale==1 && applied.model_state==0x180);
    saved.pose.model_state=0x200;
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::ok);
    assert(pose.opacity==0.5f && pose.body_alpha==1 && !pose.noise_alpha && applied.model_state==0x200);
    saved.pose.mario_flags=0x2; // raw vanish bit can outlive the rendered effect
    for(uint16_t state : {uint16_t(0), uint16_t(0x1ff), uint16_t(0x200)}) {
        saved.pose.model_state=state;
        assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::ok);
        assert(pose.body_alpha==1 && !pose.noise_alpha && pose.opacity==0.5f);
    }
    saved.pose.model_state=0x340;world.target.noise_scale=2.5f;
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::ok);
    assert(pose.noise_alpha && std::fabs(pose.body_alpha-64.0f/255.0f)<1e-6f
        && pose.opacity==0.5f && pose.noise_scale==2.5f);
    saved.pose.model_state=0x200;world.target.noise_scale=1;
    // Metal: texture-generated coordinates follow the LIVE view's eye-space
    // normal exactly as GLideN64 does for G_TEXTURE_GEN on the 64x32 metal
    // texture (atlas cell 0 of 11); the model's own uv is replaced.
    fake_flags=SG_MATERIAL_TEXGEN;fake_normal[0]=1;fake_normal[1]=0;
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::ok);
    assert(pose.vertices[0].material==ghost_compositor::Material::alpha_mix);
    assert(std::fabs(pose.vertices[0].uv[0]-62.0f/64.0f/SG_ATLAS_CELLS)<1e-6f && std::fabs(pose.vertices[0].uv[1]-15.5f/64.0f)<1e-6f);
    live.camera.position[0]=-5; // the fake turns this view: +x now faces -z
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::ok);
    assert(std::fabs(pose.vertices[0].uv[0]-31.0f/64.0f/SG_ATLAS_CELLS)<1e-6f && std::fabs(pose.vertices[0].uv[1]-15.5f/64.0f)<1e-6f);
    // With the renderer's lookat enabled (its ROM-open default x=(0,1,0),
    // y=(1,0,0)), x/y come from the lookat, as GLideN64 does: swapped here.
    live.camera.position[0]=42;world.lookat={{0,1,0},{1,0,0},1};
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::ok);
    assert(std::fabs(pose.vertices[0].uv[0]-31.0f/64.0f/SG_ATLAS_CELLS)<1e-6f && std::fabs(pose.vertices[0].uv[1]-31.0f/64.0f)<1e-6f);
    world.lookat={};
    // A textured triangle's render tile rides above the compositor's mode.
    live.camera.position[0]=42;fake_normal[0]=0;fake_normal[1]=1;
    const uint32_t tile=SG_TILE_PRESENT|35u|SG_TILE_S_CLAMP|(5u<<SG_TILE_S_MASK_SHIFT)|(5u<<SG_TILE_S_SIZE_SHIFT);
    fake_flags=tile<<SG_MATERIAL_TILE_SHIFT;
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::ok);
    assert(pose.vertices[0].material==ghost_compositor::with_tile(ghost_compositor::Material::alpha_mix,tile));
    fake_flags=0;
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::ok);
    assert(pose.vertices[0].uv[0]==0.25f); // untextured-generation vertices keep their uv
    // Back faces the game culls are never drawn. With the live camera at the
    // origin: a shell in front of it shows its near wall, not the far wall's
    // inside; a wall its list left two-sided (his cap's wings) stays; a camera
    // inside the shell sees neither wall. The culled go after the drawn,
    // counted apart.
    live.camera.position[0]=0;two_faced=true;
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::ok);
    assert(pose.count==3 && pose.body_count==3 && pose.culled==3 && pose.vertices[0].position[2]==-5 && pose.vertices[2].position[1]==1);
    assert(pose.vertices[3].position[2]==-6 && scratch.evaluated==6);
    cull[1]=false;
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::ok);
    assert(pose.count==6 && !pose.culled && pose.vertices[3].position[2]==-6);
    cull[1]=true;a_z=1;b_z=-1; // the camera between the walls, inside the shell
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::ok);
    assert(pose.count==0 && pose.body_count==0 && pose.culled==6);
    two_faced=false;a_z=-5;b_z=-6;live.camera.position[0]=42;
    // A held Bob-omb (bhvBobomb) reaches the model with its animation, HOLP,
    // scale and blink, and the live view and roll its billboard faces.
    saved.valid_fields|=GC_VALID_HELD; saved.held.behavior=0x13003174u;
    saved.held.animation_id=1; saved.held.animation_frame=5; saved.held.anim_state=1;
    saved.held.holp[0]=40; saved.held.holp[1]=120; saved.held.holp[2]=60;
    saved.held.scale[0]=saved.held.scale[1]=saved.held.scale[2]=1; live.camera.roll=77;
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::ok);
    assert((applied.fields&SG_POSE_HELD) && applied.held_kind==SG_HELD_BOBOMB && applied.held_animation_id==1
        && applied.held_animation_frame==5 && applied.held_anim_state==1 && applied.held_position[1]==120
        && applied.held_scale[2]==1 && applied.roll==77 && applied.view[0]==1 && applied.view[15]==1
        && applied.held_global_timer==777);
    // The penguin, King Bob-omb, Chuckya, both boxes and Mips by their behaviours.
    const struct { uint32_t behavior, kind; } kinds[]={{0x130020E8u,SG_HELD_PENGUIN},
        {0x130001F4u,SG_HELD_KING_BOBOMB},{0x13000528u,SG_HELD_CHUCKYA},{0x13004218u,SG_HELD_BREAKABLE_BOX},
        {0x13001650u,SG_HELD_BREAKABLE_BOX},{0x130044FCu,SG_HELD_MIPS},{0x130033ECu,SG_HELD_HOOT},
        {0x13001F3Cu,SG_HELD_KOOPA_SHELL},{0x13000F08u,SG_HELD_UKIKI},{0x13001CB0u,SG_HELD_UKIKI}};
    for(const auto &k:kinds) {
        saved.held.behavior=k.behavior;
        assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::ok);
        assert((applied.fields&SG_POSE_HELD) && applied.held_kind==k.kind);
    }
    // A JP clip names its scripts by JP offsets (data/behaviours_jp.tsv): the
    // four that JP moves draw through the clip's own layout, and the other
    // cartridge's offset names nothing. The unmoved ones read the same.
    const struct { uint32_t us, jp, kind; } moved[]={{0x13003174u,0x13003154u,SG_HELD_BOBOMB},
        {0x13004218u,0x130041F8u,SG_HELD_BREAKABLE_BOX},{0x130044FCu,0x130044DCu,SG_HELD_MIPS},
        {0x130033ECu,0x130033CCu,SG_HELD_HOOT}};
    for(const auto &m:moved) {
        saved.held.behavior=m.jp;
        assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose,GC_ROM_JP)==Result::ok);
        assert((applied.fields&SG_POSE_HELD) && applied.held_kind==m.kind);
        assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose,GC_ROM_US)==Result::ok);
        assert(!(applied.fields&SG_POSE_HELD));
        saved.held.behavior=m.us;
        assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose,GC_ROM_JP)==Result::ok);
        assert(!(applied.fields&SG_POSE_HELD));
    }
    for(const auto &k:kinds) {
        if(k.behavior==0x13004218u || k.behavior==0x130044FCu || k.behavior==0x130033ECu)continue;
        saved.held.behavior=k.behavior;
        assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose,GC_ROM_JP)==Result::ok);
        assert((applied.fields&SG_POSE_HELD) && applied.held_kind==k.kind);
    }
    saved.held.behavior=0x13003174u;
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose,GC_ROM_JP+1)==Result::ok);
    assert(!(applied.fields&SG_POSE_HELD)); // an unknown cartridge names nothing
    saved.pose.model_state=0x180;
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::ok);
    assert(pose.count==6 && pose.body_count==3 && pose.opacity==0.5f && pose.noise_alpha
        && std::fabs(pose.body_alpha-128.0f/255.0f)<1e-6f);
    saved.pose.model_state=0x200;
    saved.held.behavior=0x130044FCu;saved.held.animation_id=0;
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::ok);
    assert(applied.held_animation_id==4); // legacy in-hand Mips: ears down
    saved.held.released=1;
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::ok);
    assert(applied.held_animation_id==0); // released Mips keeps the recorded animation
    saved.held.released=0;saved.held.behavior=0x130044DCu; // JP's Mips, the same rule
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose,GC_ROM_JP)==Result::ok);
    assert(applied.held_kind==SG_HELD_MIPS && applied.held_animation_id==4);
    saved.held.released=1;saved.held.behavior=0x130044FCu;
    saved.held.released=0;saved.held.animation_id=1;
    saved.held.behavior=0x130033ECu;saved.held.released=1;saved.held.angles[1]=0x4567;
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::ok);
    assert(applied.held_kind==SG_HELD_HOOT && (applied.fields&SG_POSE_HELD_RELEASED)
        && applied.held_animation_id==1 && applied.held_angles[1]==0x4567
        && pose.opacity==0.5f); // Owl and Mario share the same tinted mesh
    saved.held.released=0;saved.held.angles[1]=0;
    saved.held.behavior=0x13003174u;
    // The held object is lit by its own lights, not Mario's: its blue vertex
    // shades to its ambient plus diffuse (the fake normal faces the light).
    assert(pose.count==6 && pose.vertices[3].color[0]==0 && pose.vertices[3].color[2]>100.0f/255.0f);
    // Its lights missing, or a colour none of them names: Mario alone draws.
    fake_held_lights=false;
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::ok && pose.count==3);
    fake_held_lights=true;held_blue=0.5f;
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::ok && pose.count==3);
    held_blue=1;
    // A refused held object leaves Mario drawn; an unknown one never asks.
    refuse_held=true;
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::ok);
    assert(pose.count==3 && !(applied.fields&SG_POSE_HELD));
    refuse_held=false; saved.held.behavior=0x13000000u;
    assert(ghost_pose::evaluate(nullptr,record,live,world,0.5f,scratch,pose)==Result::ok);
    assert(pose.count==3 && !(applied.fields&SG_POSE_HELD));
    saved.valid_fields&=~GC_VALID_HELD; saved.held={}; live.camera.roll=0;
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
    // Mario lit as F3D lights him: ambient + diffuse * max(0, N.L) with his
    // own light in EYE space, so the lit side follows the live camera. A
    // normal facing away keeps exactly the ambient; turned toward the light
    // by the other camera it clamps to full red. A world-space light would
    // shade both cameras the same.
    {
        saved.pose.root_kind=GC_ROOT_TRS;saved.valid_fields&=~GC_VALID_SHADOW;
        std::memset(saved.pose.root_matrix,0,sizeof(saved.pose.root_matrix));
        for(float& axis:saved.pose.scale)axis=1;
        live.epoch=3;live.dlist_serial=20;live.sequence=20;live.level=saved.level;live.area=saved.area;
        world.epoch=3;world.list_serial=20;world.context_generation=2;
        fake_normal[0]=-1;fake_normal[1]=0;fake_normal[2]=0;fake_flags=0;
        live.camera.position[0]=42; // identity view: eye normal (-1,0,0), unlit
        assert(ghost_pose::evaluate(nullptr,record,live,world,.5f,scratch,pose)==Result::ok);
        assert(std::fabs(pose.vertices[0].color[0]-127.0f/255.0f)<1e-6f && pose.vertices[0].color[1]==0);
        assert(pose.ambient==1 && pose.diffuse==0); // the compositor adds no second light
        live.camera.position[0]=-5; // turned view: eye normal (0,0,1), N.L = 1/sqrt3
        assert(ghost_pose::evaluate(nullptr,record,live,world,.5f,scratch,pose)==Result::ok);
        assert(pose.vertices[0].color[0]==1 && pose.vertices[0].color[2]==0);
        // A colour none of his lights names is refused, never guessed.
        fake_lights=0;
        assert(ghost_pose::evaluate(nullptr,record,live,world,.5f,scratch,pose)==Result::model_failed);
        fake_lights=1;fake_normal[0]=0;fake_normal[1]=1;
    }
    // Sound placement: a world point under the same live look-at product as drawing.
    {
        gc_camera camera{};camera.position[0]=-5;const float point[3]={1,2,3};float out[3]{};
        assert(ghost_pose::camera_relative(nullptr,camera,point,out));
        assert(out[0]==13 && out[1]==22 && out[2]==29);
    }
    std::puts("recorded pose to live camera mapping passed");
}
