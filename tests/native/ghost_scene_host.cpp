/* The shipped pose, shadow and particle conversion, with a transparent model
 * shell. No graphics, ROM, emulator, windows or heap-owned render values. */
#include "ghost_pose.h"
#include "ghost_particle_pose.h"
#include "ghost_scene.h"
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <initializer_list>

static unsigned checks=0;
static sg_pose applied{};
static void require(bool value,const char *what) {
    ++checks;
    if(!value) {std::fprintf(stderr,"FAILED: %s (check %u)\n",what,checks);std::exit(1);}
}
static bool near(float a,float b) {return std::fabs(a-b)<.001f;}
extern "C" int32_t sg_model_view(sg_model *,const float *position,const float *,int16_t,float *out) {
    std::memset(out,0,16*sizeof(float));out[0]=out[5]=out[10]=out[15]=1;
    for(unsigned a=0;a<3;++a)out[12+a]=-position[a];
    return SG_OK;
}
extern "C" int32_t sg_model_get_body_lights(sg_model *,sg_body_lights *out) {
    *out={};out->count=1;out->lights[0]={{255,0,0},{127,0,0},{40,40,40}};return SG_OK;
}
extern "C" int32_t sg_model_get_held_lights(sg_model *model,uint32_t,sg_body_lights *out) {
    return sg_model_get_body_lights(model,out);
}
extern "C" int32_t sg_model_evaluate(sg_model *,const sg_pose *pose,sg_mesh *mesh) {
    applied=*pose;mesh->held_first_vertex=3;mesh->vertex_count=pose->fields&SG_POSE_HELD?6:3;
    for(unsigned i=0;i<mesh->vertex_count;++i) {
        const float *root=i>=3?pose->held_position:pose->fields&SG_POSE_THROW_MATRIX?pose->throw_matrix+12:pose->position;
        for(unsigned a=0;a<3;++a) {
            mesh->position[3*i+a]=root[a]+float(i%3)*(a==0?1.0f:0.0f);
            mesh->normal[3*i+a]=a==1?1.0f:0.0f;mesh->color[3*i+a]=a==0?1.0f:0.0f;
            mesh->ambient[3*i+a]=a==0?127.0f/255.0f:0.0f;
        }
        mesh->uv[2*i]=mesh->uv[2*i+1]=0;mesh->material[i]=SG_MATERIAL_ALPHA_MIX;
    }
    mesh->error_flags=0;return SG_OK;
}
static gs_record recording(int16_t area,uint32_t root) {
    gs_record record{};record.bytes=sizeof(record);record.version=GS_VERSION;record.configuration=2;
    auto &s=record.sample;s.bytes=sizeof(s);s.version=GC_VERSION;s.epoch=1;s.sequence=s.dlist_serial=7;
    s.level=23;s.area=area;s.valid_fields=GC_VALID_CLOCK|GC_VALID_POSE|GC_VALID_CAMERA|GC_VALID_HELD|GC_VALID_SHADOW|GC_VALID_PARTICLES;
    const float offset=area==2?-8192.0f:0.0f;
    s.pose.root_kind=root;s.pose.position[0]=4100+offset;s.pose.position[1]=-300;s.pose.position[2]=200;
    for(float &a:s.pose.scale)a=1;
    s.camera.position[0]=3900+offset;s.camera.position[1]=-100;s.camera.position[2]=1000;
    s.camera.focus[0]=4100+offset;s.camera.focus[1]=-300;s.camera.focus[2]=200;
    if(root==GC_ROOT_SCALED_MODELVIEW) {
        s.pose.root_matrix[0]=s.pose.root_matrix[5]=s.pose.root_matrix[10]=.25f;s.pose.root_matrix[15]=1;
        s.pose.root_matrix[12]=200;s.pose.root_matrix[13]=-200;s.pose.root_matrix[14]=-800;
    }
    s.held.behavior=0x13003174u;s.held.released=1;s.held.holp[0]=4150+offset;s.held.holp[1]=-250;s.held.holp[2]=200;
    for(float &a:s.held.scale)a=1;
    s.shadow.matrix[0]=s.shadow.matrix[5]=s.shadow.matrix[10]=s.shadow.matrix[15]=1;
    s.shadow.matrix[12]=200;s.shadow.matrix[13]=-220;s.shadow.matrix[14]=-800;
    s.shadow.position[0][0]=10;s.shadow.position[0][2]=20;
    s.particles.count=1;auto &e=s.particles.entries[0];e.display_list=0x0401DD60u;
    e.basis[0]=e.basis[4]=e.basis[8]=GC_PARTICLE_BASIS_ONE;
    e.translation[0]=210;e.translation[1]=-180;e.translation[2]=-780;
    return record;
}
int main() {
    sg_model *model=reinterpret_cast<sg_model*>(&applied);
    static ghost_pose::Scratch scratch;
    sg_particle_vertex vertices[3]{};for(auto &v:vertices)for(auto &c:v.rgba)c=255;
    vertices[1].position[0]=10;
    sg_particle_frame frame{0x0401DD60u,SG_PARTICLE_CUTOUT,0,3,0};
    sg_particle_view view{&frame,vertices,nullptr,1,3,0,0};
    for(int16_t area:{int16_t(1),int16_t(2)})for(uint32_t root:{uint32_t(GC_ROOT_TRS),uint32_t(GC_ROOT_SCALED_MODELVIEW)}) {
        auto record=recording(area,root);const auto original=record;
        gc_sample live=record.sample;live.area=area==1?2:1;live.epoch=3;live.sequence=live.dlist_serial=20;
        live.valid_fields=GC_VALID_CLOCK|GC_VALID_CAMERA; // mapping never needs live Mario's pose
        const float offset=live.area==2?-8192.0f:0.0f;
        live.camera.position[0]=3900+offset;live.camera.focus[0]=4100+offset;
        ghost::WorldPass world{};world.epoch=3;world.context_generation=2;world.list_serial=20;
        world.projection[0]=world.projection[5]=world.projection[10]=world.projection[15]=1;
        world.target.screen_scale[0]=world.target.screen_scale[1]=1;
        ghost_compositor::Pose pose{};
        require(ghost_pose::evaluate(model,record,live,world,1,scratch,pose)==ghost_pose::Result::ok,"direct DDD pose admitted");
        require(near(applied.position[0],4100+offset) && near(pose.vertices[0].position[0],4100+offset),"TRS/modelview root translated once");
        if(root==GC_ROOT_SCALED_MODELVIEW)require(near(applied.throw_matrix[12],4100+offset),"modelview camera inversion rebases root");
        require(near(applied.held_position[0],4150+offset) && near(pose.vertices[3].position[0],4150+offset),"released held object rebases with HOLP");
        auto mapped=record.sample;require(ghost_scene::rebase(mapped,live),"shared sample rebase");
        require(mapped.area==live.area && near(mapped.camera.position[0],3900+offset)
                && near(mapped.camera.focus[0],4100+offset),"recorded camera and focus map together");
        require(!std::memcmp(mapped.pose.root_matrix,original.sample.pose.root_matrix,sizeof(mapped.pose.root_matrix))
                && !std::memcmp(&mapped.shadow,&original.sample.shadow,sizeof(mapped.shadow))
                && !std::memcmp(&mapped.particles,&original.sample.particles,sizeof(mapped.particles)),"recorded view space stays byte-identical");
        ghost_compositor::ShadowVertex shadow_vertices[24]{};uint8_t texture[256]{};ghost_compositor::Shadow shadow{};
        require(ghost_pose::shadow(model,mapped,pose.view_projection,shadow_vertices,texture,shadow)
                && near(shadow_vertices[0].position[0],4110+offset) && near(shadow_vertices[0].position[1],-320)
                && near(shadow_vertices[0].position[2],220),"shadow camera inversion translates once");
        ghost_compositor::ParticleVertex particle_vertices[ghost_compositor::particle_vertices]{};
        ghost_compositor::Particles particles{};
        require(ghost_particle_pose::place(model,view,mapped,live.camera,pose.view_projection,particle_vertices,particles).drawn==1
                && near(particle_vertices[0].position[0],4110+offset) && near(particle_vertices[1].position[0],4120+offset)
                && near(particle_vertices[0].position[1],-280) && near(particle_vertices[0].position[2],220),"particle camera inversion translates once");
        float sound[3]{};
        require(ghost_pose::camera_relative(model,live.camera,mapped.pose.position,sound)
                && near(sound[0],200) && near(sound[1],-200) && near(sound[2],-800),"rebased audio point agrees with live camera");
        require(!std::memcmp(&record,&original,sizeof(record)),"saved recording remains immutable");
        // Admission follows the common rendered tunnel, including its deeper
        // shafts and bend. Its bounding box also contains air and a duct only
        // area 1 renders; neither is a safe cross-area pose.
        const float source_offset=area==2?-8192.0f:0.0f;
        const float admitted[][3]={{3000,-1000,200},{5600,-2300,200},{8000,-2200,200},
            {2151,-4607,200},{3174,-4607,200},{3174,-511,200},{5019,-511,200},{5019,-2449,200},
            {5326,-2756,200},{9037,-2756,200},{9037,-1732,200},{6042,-1732,200},{6042,205,200},
            {5018,1229,200},{3175,1229,200},{2151,205,200},
            {5172.5f,-2602.5f,200},{4100,-300,-511},{4100,-300,512}};
        for(const auto &point:admitted) {
            auto boundary=record.sample;
            for(unsigned axis=0;axis<3;++axis)boundary.pose.position[axis]=point[axis];
            boundary.pose.position[0]+=source_offset;
            require(ghost_scene::compatible(boundary,live),"shared tunnel shafts, bend and boundary admitted");
        }
        const float refused[][3]={{5200,-2700,200},{5000,-2000,200},{6000,1000,200},
            {2150,-1000,200},{9038,-2200,200},{3000,-4608,200},{4100,1230,200},
            {3175,-1000,200},{4100,-300,-512},{4100,-300,513},{1000,-2200,200}};
        for(const auto &point:refused) {
            auto boundary=record.sample;
            for(unsigned axis=0;axis<3;++axis)boundary.pose.position[axis]=point[axis];
            boundary.pose.position[0]+=source_offset;
            const auto before=boundary;
            require(!ghost_scene::compatible(boundary,live) && !ghost_scene::rebase(boundary,live)
                    && !std::memcmp(&boundary,&before,sizeof(before)),"outside connector refuses without mutation");
        }
        auto wrong=live;wrong.level=9;
        require(!ghost_scene::compatible(record.sample,wrong),"different level refused");
        wrong=live;wrong.area=3;require(!ghost_scene::compatible(record.sample,wrong),"unrelated DDD area refused");
        auto other=record.sample;other.level=9;wrong=live;wrong.level=9;
        require(!ghost_scene::compatible(other,wrong),"ordinary cross-area scene refused");
        auto missing=record.sample;missing.valid_fields&=~uint32_t(GC_VALID_POSE|GC_VALID_HELD);
        missing.pose={};missing.held={};
        require(!ghost_scene::compatible(missing,live),"cross-area admission needs recorded root proof");
        require(ghost_scene::rebase_components(missing,record.sample,live)
                && near(missing.camera.position[0],3900+offset),"known particles on a bridged row use its admitted shown scene");
        const gc_pose zero_pose{};const gc_held zero_held{};
        require(!std::memcmp(&missing.pose,&zero_pose,sizeof(zero_pose))
                && !std::memcmp(&missing.held,&zero_held,sizeof(zero_held)),"missing pose and held fields stay zero after component rebase");
        missing=record.sample;missing.valid_fields&=~uint32_t(GC_VALID_CAMERA|GC_VALID_HELD);missing.camera={};missing.held={};
        const gc_camera zero_camera{};
        require(ghost_scene::rebase(missing,live) && !std::memcmp(&missing.camera,&zero_camera,sizeof(zero_camera))
                && !std::memcmp(&missing.held,&zero_held,sizeof(zero_held)),"missing optional camera and held fields stay zero");
        missing=record.sample;missing.area=3;
        require(!ghost_scene::rebase_components(missing,record.sample,live),"foreign particle scene remains refused");
    }
    std::printf("DDD portal passed: roots=TRS+modelview held=world shadow=once particles=once connector=bounded checks=%u\n",checks);
}
