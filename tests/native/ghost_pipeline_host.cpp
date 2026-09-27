/* Connected synthetic pipeline: real capture/model/playback/compositor/presentation.
 * A hidden WGL drawable replaces the live GLideN64 terrain/VI renderer. */
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#define main capture_fixture_program
#include <ghost_capture_host.cpp>
#undef main
#include "ghost_root_fixture.h"
#define main compositor_fixture_program
#include <ghost_compositor_host.cpp>
#undef main
#include "ghost_playback.h"
#include "ghost_clip_upgrade.h"
#include "ghost_pass.h"
#include "ghost_presentation.h"
#include "ghost_stream.h"
#include <chrono>
#include <condition_variable>
#include <functional>
#include <mutex>
#include <thread>
#include <memory>
#include <filesystem>
#include <iterator>
#include <stdexcept>

namespace present=ghost_presentation;
using Clock=std::chrono::steady_clock;
static uint32_t W=640,H=480; // the drawable; main's optional width and height
static double ms(Clock::time_point a,Clock::time_point b){return std::chrono::duration<double,std::milli>(b-a).count();}

class RenderOwner {
    std::mutex mutex;std::condition_variable wake,done;
    std::function<void()> job;std::exception_ptr failure;
    bool pending=false,stopping=false;std::thread thread;
public:
    RenderOwner():thread([&]{std::unique_lock<std::mutex> lock(mutex);for(;;){
        wake.wait(lock,[&]{return pending||stopping;});if(stopping)return;
        auto work=std::move(job);lock.unlock();try{work();}catch(...){failure=std::current_exception();}
        lock.lock();pending=false;done.notify_one();}}){}
    ~RenderOwner(){{std::lock_guard<std::mutex> lock(mutex);stopping=true;}wake.notify_one();thread.join();}
    void run(std::function<void()> work){std::unique_lock<std::mutex> lock(mutex);job=std::move(work);pending=true;
        wake.notify_one();done.wait(lock,[&]{return !pending;});if(failure)std::rethrow_exception(failure);}
};

static bool gateway_open=false;
static bool begin_gpu(){require(!gateway_open,"presentation gateway not nested");gateway_open=true;return true;}
static bool end_gpu(){require(gateway_open,"presentation gateway balanced");gateway_open=false;return true;}
struct ViRaster {
    const gc::Dispatch& g;uint32_t program=0,vao=0;
    explicit ViRaster(const gc::Dispatch& d):g(d){
        const char* vs="#version 430 core\nout vec2 uv;uniform int flip;const vec2 p[6]=vec2[6](vec2(-1,-1),vec2(1,-1),vec2(-1,1),vec2(-1,1),vec2(1,-1),vec2(1,1));void main(){vec2 v=p[gl_VertexID];uv=(v+1)/2;if(flip!=0)uv.y=1-uv.y;gl_Position=vec4(v,0,1);}";
        const char* fs="#version 430 core\nin vec2 uv;uniform sampler2D t;uniform float exponent;out vec4 c;void main(){c=vec4(pow(texture(t,uv).rgb,vec3(exponent)),1);}";
        auto v=g.CreateShader(cgl::VERTEX_SHADER),f=g.CreateShader(cgl::FRAGMENT_SHADER);
        g.ShaderSource(v,1,&vs,nullptr);g.ShaderSource(f,1,&fs,nullptr);g.CompileShader(v);g.CompileShader(f);
        program=g.CreateProgram();g.AttachShader(program,v);g.AttachShader(program,f);g.LinkProgram(program);
        int32_t linked=0;g.GetProgramiv(program,cgl::LINK_STATUS,&linked);require(linked!=0,"VI shader link");
        g.DeleteShader(v);g.DeleteShader(f);g.GenVertexArrays(1,&vao);
    }
    ~ViRaster(){g.UseProgram(0);g.DeleteProgram(program);g.DeleteVertexArrays(1,&vao);}
    static void draw(void* owner,const present::Draw& value){
        auto& self=*static_cast<ViRaster*>(owner);const auto& g=self.g;
        g.BindFramebuffer(cgl::DRAW_FRAMEBUFFER,value.framebuffer);g.DrawBuffer(value.framebuffer?cgl::COLOR_ATTACHMENT0:GL_BACK);
        g.Disablei(GL_SCISSOR_TEST,0);g.Disablei(GL_BLEND,0);g.Disable(GL_DEPTH_TEST);g.Disable(GL_STENCIL_TEST);
        g.ColorMaski(0,1,1,1,1);g.Viewport(0,0,value.width,value.height);g.UseProgram(self.program);g.BindVertexArray(self.vao);
        g.ActiveTexture(cgl::TEXTURE0);g.BindSampler(0,0);g.BindTexture(GL_TEXTURE_2D,value.texture);
        g.Uniform1i(g.GetUniformLocation(self.program,"t"),0);
        g.Uniform1i(g.GetUniformLocation(self.program,"flip"),value.kind==present::DrawKind::vi);
        g.Uniform1f(g.GetUniformLocation(self.program,"exponent"),value.kind==present::DrawKind::gamma?.5f:1.f);
        g.DrawArrays(GL_TRIANGLES,0,6);
    }
    present::Raster api(){return {this,draw,begin_gpu,end_gpu};}
};

// Mario's shadow G_MTX: translate to his feet (world origin, floor y=0) under
// the same look-at camera as his root: eye (0,100,600), focus (0,100,0).
static const float feet[16]={1,0,0,0,0,1,0,0,0,0,1,0,0,-100,-600,1};
// Mario's particles, as the game leaves them: active objects whose parent
// chain reaches Mario (a splash's droplet is two hops away), each drawn with a
// billboard matrix at its cameraToObject. A foreign bubble has another parent.
struct FixtureParticle { uint32_t slot, parent_slot, dl; float scale, view[3]; };
static const FixtureParticle fixture_particles[]={
    {3,0,0x0401DD60u,1.0f,{150,-20,-560}},    // bubble (Mario's child)
    {4,5,0x04032A18u,1.5f,{-60,130,-560}},    // droplet (child of the splash)
    {5,0,0x04032670u,1.0f,{-150,-100,-620}},  // water splash, third frame
    {6,6,0x0401DD60u,1.0f,{0,60,-560}},       // another object's bubble: never recorded
    // A ground-pound/wall-bonk star as the game draws it: oAnimState 4 selects
    // cartoon_star_geo's unlit yellow list (bhv_*_tiny_star_particle_loop, scale .28).
    {7,0,0x0302C318u,0.28f,{160,130,-600}},
    {8,0,0x03022F48u,0.25f,{170,-110,-600}},  // the wind-knocked cap (geo scale .25): lit, logo mixed
};
static void particle_matrix(const FixtureParticle& p,float out[16]){
    std::memset(out,0,16*sizeof(float));out[0]=out[5]=out[10]=p.scale;out[15]=1;
    for(unsigned i=0;i<3;++i)out[12+i]=p.view[i];
}
static void configure_fixture(Fixture& f){
    const auto obj=f.config.object_pool;
    f.put32(f.config.global_timer,100);f.put16(f.config.igt_overall,10);f.put16(f.config.area_update_counter,1);
    f.put16(obj+0x42,1);f.put16(obj+0x40,0);f.put16(obj+0x38,197);
    for(unsigned i=0;i<3;++i){f.put16(obj+0x1a+2*i,0);f.putfloat(obj+0x20+4*i,0);f.putfloat(obj+0x2c+4*i,1);
        f.put16(Fixture::body+0x0c+2*i,0);f.put16(Fixture::body+0x12+2*i,0);}
    for(unsigned i=4;i<12;++i)f.put8(Fixture::body+i,0);
    const float eye[3]={0,100,600},focus[3]={0,100,0};
    for(unsigned i=0;i<3;++i){f.putfloat(Fixture::camera+0x1c+4*i,eye[i]);f.putfloat(Fixture::camera+0x28+4*i,focus[i]);}
    f.put16(Fixture::camera+0x38,0);f.put16(Fixture::camera+0x3a,0);
    const float root[16]={.25f,0,0,0,0,.25f,0,0,0,0,.25f,0,0,-100,-600,1};
    configure_root_scene(f,root);
    realistic_shadow(f,feet);
    for(const auto& p:fixture_particles){
        const uint32_t object=obj+p.slot*0x260,parent=obj+p.parent_slot*0x260;
        f.put16(object+0x02,1);f.put16(object+0x74,1);f.put32(object+0x68,parent);
        for(unsigned i=0;i<3;++i)f.putfloat(object+0x54+4*i,p.view[i]);
    }
    check(f.run()==GC_OK,"pipeline fixture capture");
}
static void clock_word(Fixture& f,uint32_t address,uint32_t value,unsigned width){
    for(unsigned i=0;i<width;++i)f.ram[((address-Fixture::base)+i)^3]=uint8_t(value>>(8*(width-i-1)));
}
static void projection(float* out){
    const float near_plane=100,far_plane=20000,s=1.f/std::tan(45.f*3.14159265358979323846f/360.f);
    std::memset(out,0,16*sizeof(float));out[0]=s/(float(W)/H);out[5]=s;
    out[10]=-(far_plane+near_plane)/(far_plane-near_plane);out[11]=-1;out[14]=-2*far_plane*near_plane/(far_plane-near_plane);
}
static gs_record capture_source(Fixture& f,const ghost::RasterTarget& target,const float* matrix,int64_t qpc){
    require(ghost_stream::begin(f.ram.data(),uint32_t(f.ram.size()),1,qpc),"actual source begin");
    const float root[16]={.25f,0,0,0,0,.25f,0,0,0,0,.25f,0,0,-100,-600,1};
    ghost_stream::display_list(root_dl+8,alpha_dl,alpha_dl,1,root);
    ghost_stream::display_list(root_dl+24,body_dl,0x40080,1,root);
    // Later master-list layer: the game's own shadow list and its nested calls.
    ghost_stream::display_list(root_dl+40,shadow_list,shadow_list,1,feet);
    ghost_stream::display_list(shadow_list,seg_circle,physical2(seg_circle),2,feet);
    ghost_stream::display_list(physical2(seg_circle),seg_begin,physical2(seg_begin),3,feet);
    ghost_stream::display_list(shadow_list+16,seg_tris,physical2(seg_tris),2,feet);
    ghost_stream::display_list(shadow_list+24,seg_end,physical2(seg_end),2,feet);
    // The transparent layer: each particle's geo display list under its matrix.
    for(unsigned i=0;i<6;++i){float m[16];particle_matrix(fixture_particles[i],m);
        ghost_stream::display_list(root_dl+48+8*i,fixture_particles[i].dl,fixture_particles[i].dl,1,m);}
    ghost_stream::projection(matrix);ghost_stream::triangles(target,2,true);
    require(ghost_stream::geometry(true,false),"source qualifies synthetic world end");
    ghost_stream::end(true);gs_record out{};
    require(ghost_stream::api()->read(&out,sizeof(out))==1 && out.world.status==GCLIP_WORLD_READY,"actual source queue occurrence");
    require(out.sample.valid_fields&GC_VALID_SHADOW,"source captures Mario's executed shadow");
    const auto& recorded=out.sample.particles;
    require((out.sample.valid_fields&GC_VALID_PARTICLES) && recorded.count==5 && !recorded.flags
        // Nearest Mario (feet at view 0,-100,-600) first: splash 151, cap 170, bubble 175,
        // droplet 241, star 280 units.
        && recorded.entries[0].display_list==0x04032670u && recorded.entries[1].display_list==0x03022F48u
        && recorded.entries[2].display_list==0x0401DD60u && recorded.entries[3].display_list==0x04032A18u
        && recorded.entries[4].display_list==0x0302C318u && recorded.entries[3].basis[0]==6144
        && recorded.entries[2].translation[0]==150,"source records Mario's particles, not another object's");
    require(out.sample.status==GC_OK && (out.sample.clock.valid&(GCC_RAW_VALID|GCC_ELAPSED_VALID))
        ==(GCC_RAW_VALID|GCC_ELAPSED_VALID),"source derives usable elapsed clock");
    return out;
}
static void ppm(const std::filesystem::path& path,const std::vector<uint8_t>& rgba){
    std::ofstream file(path,std::ios::binary);file<<"P6\n"<<W<<' '<<H<<"\n255\n";
    for(int y=int(H)-1;y>=0;--y)for(unsigned x=0;x<W;++x)file.write(reinterpret_cast<const char*>(&rgba[(y*W+x)*4]),3);
    require(bool(file),"write evidence image");
}
static std::vector<uint8_t> back_pixels(const gc::Dispatch& g){
    cgl::Guard guard(g);g.BindFramebuffer(cgl::READ_FRAMEBUFFER,0);g.ReadBuffer(GL_BACK);
    std::vector<uint8_t> out(W*H*4);glReadPixels(0,0,W,H,GL_RGBA,GL_UNSIGNED_BYTE,out.data());no_error("BACK read");return out;
}
static unsigned changed(const std::vector<uint8_t>& a,const std::vector<uint8_t>& b){
    unsigned count=0;for(size_t i=0;i<a.size();i+=4)if(std::memcmp(a.data()+i,b.data()+i,3))++count;return count;
}
static void json_string(const std::string& value){
    std::putchar('"');for(unsigned char c:value){if(c=='"'||c=='\\')std::putchar('\\');
        if(c<32)std::printf("\\u%04x",unsigned(c));else std::putchar(c);}std::putchar('"');
}
struct Times {double pipeline=0,swap=0;};
/* The renderer state frame() leaves before the seal: ordinary_state, the VI
 * draw to the default framebuffer, then the depth, blend and scissor the
 * world pass ended with. */
static void world_state(const gc::Dispatch& g) {
    g.DepthFunc(cgl::LESS);g.DepthMask(1);g.BlendFuncSeparatei(0,cgl::ONE,cgl::ZERO,cgl::ONE,cgl::ZERO);
    g.ScissorIndexed(0,0,0,int32_t(W),int32_t(H));
}
/* ...and that state as GLideN64's glue hands it over: its dispatch's record
 * of every call, so the compositor asks the driver nothing. */
static gc::Known known_before_seal(const Scene& scene,uint32_t program,uint32_t vao) {
    gc::Known known{};
    known.fields=gc::Known::draw_framebuffer|gc::Known::read_framebuffer|gc::Known::viewport|gc::Known::scissor|
        gc::Known::program|gc::Known::vertex_array|gc::Known::depth|gc::Known::blend_factors;
    known.draw=0;known.read=scene.fbo;
    const int32_t box[4]={0,0,int32_t(W),int32_t(H)};
    std::memcpy(known.viewport_box,box,sizeof(box));std::memcpy(known.scissor_box,box,sizeof(box));
    known.caps=0x7f;known.enabled=0;known.current_program=program;known.bound_vertex_array=vao;
    known.depth_function=cgl::LESS;known.depth_write=1;
    known.blend_factor[0]=known.blend_factor[2]=cgl::ONE;known.blend_factor[1]=known.blend_factor[3]=cgl::ZERO;
    return known;
}
struct Pipeline {
    Window window;gc::Dispatch g;Scene scene;ViRaster raster;gc::Instance* compositor=nullptr;
    present::Instance* presentation=nullptr;Fixture& fixture;unsigned frames=0,vertices=0,visible=0;
    bool swap_interval_disabled=false;std::vector<uint8_t> clean,display_clean,last_visible,coverage;
    std::string gpu_vendor,gpu_renderer,gl_version;
    std::vector<float> clean_depth;ghost::WorldPass world{};uint32_t configuration=0;gcc_output origin_clock{};
    float live_shift=0; // moves live Mario along world z (the camera looks down -z) for the player-first proof
    explicit Pipeline(Fixture& f,const gs_record& first):g(dispatch()),scene(g,W,H),raster(g),fixture(f),
        configuration(first.configuration),origin_clock(first.sample.clock){
        require(SetWindowPos(window.window,nullptr,0,0,W,H,SWP_NOACTIVATE|SWP_NOZORDER)!=0,"resize hidden drawable");
        require(!IsWindowVisible(window.window),"pipeline window remains hidden");
        RECT rect{};GetClientRect(window.window,&rect);require(rect.right==W && rect.bottom==H,"actual hidden extent");
        using SwapInterval=BOOL(WINAPI*)(int);auto interval=reinterpret_cast<SwapInterval>(load("wglSwapIntervalEXT"));
        swap_interval_disabled=interval && interval(0);
        gpu_vendor=reinterpret_cast<const char*>(glGetString(GL_VENDOR));
        gpu_renderer=reinterpret_cast<const char*>(glGetString(GL_RENDERER));
        gl_version=reinterpret_cast<const char*>(glGetString(GL_VERSION));
        ghost_playback::safe_boundary(7,configuration);sg_atlas_view atlas{};uint64_t model=0;
        require(ghost_playback::atlas(&atlas,&model) && model==1,"actual adopted model atlas");
        compositor=gc::create(g,1);require(compositor!=nullptr,"pipeline compositor");
        require(gc::prepare(compositor,W,H,GP_GHOSTS*SG_MODEL_MAX_VERTICES,atlas.rgba,atlas.width,atlas.height)==gc::Result::ok,
            "real atlas upload with one vertex range per ghost");
        sg_particle_view particles{};uint64_t particle_model=0;
        require(ghost_playback::particles(&particles,&particle_model) && particle_model==1 &&
            gc::prepare_particles(compositor,particles.atlas_rgba,particles.atlas_width,particles.atlas_height)==gc::Result::ok,
            "real ROM particle atlas upload");
        present::Dispatch d{};d.gl=g;d.CopyTexSubImage2D=glCopyTexSubImage2D;
        d.DrawBuffers=reinterpret_cast<decltype(d.DrawBuffers)>(load("glDrawBuffers"));
        presentation=present::create(d,1);require(presentation && present::prepare(presentation,1,W,H,W,H),"pipeline clean presentation");
        world.context_generation=1;world.target={scene.color,scene.depth,W,H,1,0,{0,0,int32_t(W),int32_t(H)},{.5f,.5f},{1,1}};
        projection(world.projection);background(false);
        // Frames run in ordinary_state from here on: that is the renderer's stable state.
        gc::resync(compositor);glFinish();
    }
    ~Pipeline(){if(water_program){g.DeleteProgram(water_program);g.DeleteVertexArrays(1,&water_vao);}
        present::destroy(presentation);gc::destroy(compositor);ghost_playback::retire_on_context_loss();ghost_stream::rom(false);}
    void background(bool wall){
        ordinary_state(g,scene);std::vector<uint8_t> color(W*H*4);std::vector<float> z(W*H,1.f);
        for(unsigned y=0;y<H;++y)for(unsigned x=0;x<W;++x){const auto i=y*W+x;
            auto tone=uint8_t((((x/32+y/32)&1)?170:200)+y/24);color[4*i]=tone;color[4*i+1]=tone;color[4*i+2]=tone;color[4*i+3]=255;
            if(wall && x<W/2){color[4*i]=70;color[4*i+1]=90;color[4*i+2]=100;z[i]=.25f;}}
        g.BindTexture(GL_TEXTURE_2D,scene.color);g.TexImage2D(GL_TEXTURE_2D,0,cgl::RGBA8,W,H,0,GL_RGBA,GL_UNSIGNED_BYTE,color.data());
        g.TexParameteri(GL_TEXTURE_2D,cgl::TEXTURE_MIN_FILTER,GL_NEAREST);g.TexParameteri(GL_TEXTURE_2D,cgl::TEXTURE_MAG_FILTER,GL_NEAREST);
        g.BindTexture(GL_TEXTURE_2D,scene.depth);g.TexImage2D(GL_TEXTURE_2D,0,cgl::DEPTH_COMPONENT24,W,H,0,GL_DEPTH_COMPONENT,GL_FLOAT,z.data());
        clean=pixels(g,scene.fbo,W,H);clean_depth=depth(g,scene);
        display_clean.resize(clean.size());
        for(unsigned y=0;y<H;++y)std::memcpy(&display_clean[y*W*4],&clean[(H-1-y)*W*4],W*4);
    }
    // A translucent water plane across the view at Mario's waist (view-space
    // y=-20 under the same projection), drawn the way the pinned renderer
    // draws the game's water (premultiplied, ONE/ONE_MINUS_SRC_ALPHA, depth
    // tested, never written) and mirrored into the ghost's layer as the
    // renderer seam does. Off restores the plain terrain and drops the layer.
    uint32_t water_program=0,water_vao=0;
    static constexpr float water_rgb[3]={40.f/255,110.f/255,200.f/255},water_alpha=.6f;
    void water(bool on){
        if(!on){gc::clear_layer(compositor);background(false);return;}
        if(!water_program){
            const char* vs="#version 430 core\nuniform mat4 projection;"
                "const vec2 p[6]=vec2[6](vec2(-4000,-100),vec2(4000,-100),vec2(-4000,-6000),vec2(-4000,-6000),vec2(4000,-100),vec2(4000,-6000));"
                "void main(){vec2 q=p[gl_VertexID];gl_Position=projection*vec4(q.x,-20.0,q.y,1.0);gl_Position.y=-gl_Position.y;}";
            const char* fs="#version 430 core\nuniform vec3 shade;uniform float alpha;layout(location=0) out vec4 c;"
                "void main(){c=vec4(shade*alpha,alpha);}";
            auto vsh=g.CreateShader(cgl::VERTEX_SHADER),fsh=g.CreateShader(cgl::FRAGMENT_SHADER);
            g.ShaderSource(vsh,1,&vs,nullptr);g.ShaderSource(fsh,1,&fs,nullptr);g.CompileShader(vsh);g.CompileShader(fsh);
            water_program=g.CreateProgram();g.AttachShader(water_program,vsh);g.AttachShader(water_program,fsh);g.LinkProgram(water_program);
            g.DeleteShader(vsh);g.DeleteShader(fsh);int32_t linked=0;g.GetProgramiv(water_program,cgl::LINK_STATUS,&linked);
            require(linked!=0,"water program");g.GenVertexArrays(1,&water_vao);
        }
        background(false);gc::clear_layer(compositor);
        auto destination=target(scene,gc::Depth::ordinary);
        const auto draw=[&]{
            g.UseProgram(water_program);g.BindVertexArray(water_vao);
            g.UniformMatrix4fv(g.GetUniformLocation(water_program,"projection"),1,0,world.projection);
            g.Uniform3fv(g.GetUniformLocation(water_program,"shade"),1,water_rgb);
            g.Uniform1f(g.GetUniformLocation(water_program,"alpha"),water_alpha);g.DrawArrays(cgl::TRIANGLES,0,6);
        };
        ordinary_state(g,scene);g.Enable(cgl::DEPTH_TEST);g.DepthFunc(cgl::LESS);g.DepthMask(0);
        g.Enablei(cgl::BLEND,0);g.BlendFuncSeparatei(0,cgl::ONE,cgl::ONE_MINUS_SRC_ALPHA,cgl::ONE,cgl::ONE_MINUS_SRC_ALPHA);
        draw();
        for(uint32_t pass=0;pass<2;++pass){
            require(gc::begin_layer(compositor,destination,gc::LayerPass(pass))==gc::Result::ok,"water mirrored into the layer");
            draw();gc::end_layer(compositor);}
        g.Disablei(cgl::BLEND,0);g.DepthMask(1);g.Disable(cgl::DEPTH_TEST);g.UseProgram(0);g.BindVertexArray(0);
        clean=pixels(g,scene.fbo,W,H);clean_depth=depth(g,scene);
        for(unsigned y=0;y<H;++y)std::memcpy(&display_clean[y*W*4],&clean[(H-1-y)*W*4],W*4);
    }
    std::vector<uint8_t> body_coverage,shadow_coverage,particle_coverage;
    // coverage = bodies, their shadows and particles; the parts are also kept apart.
    void geometry_mask(const gc::Pose* poses,uint32_t count){
        coverage.assign(W*H,0);body_coverage.assign(W*H,0);shadow_coverage.assign(W*H,0);particle_coverage.assign(W*H,0);
        for(uint32_t ghost=0;ghost<count;++ghost){const auto& p=poses[ghost];
            cover(body_coverage,&p.vertices[0].position[0],sizeof(gc::Vertex),p.count,p.view_projection,p.flip_y);
            if(p.shadow.count)cover(shadow_coverage,&p.shadow.vertices[0].position[0],sizeof(gc::ShadowVertex),
                p.shadow.count,p.shadow.view_projection,p.flip_y);
            if(p.particles.count)cover(particle_coverage,&p.particles.vertices[0].position[0],sizeof(gc::ParticleVertex),
                p.particles.count,p.particles.view_projection,p.flip_y);}
        for(size_t i=0;i<coverage.size();++i)coverage[i]=body_coverage[i]|shadow_coverage[i]|particle_coverage[i];
    }
    static void cover(std::vector<uint8_t>& mask,const float* first,size_t stride,uint32_t count,const float* vp,uint32_t flip){
        const auto position=[&](unsigned index){return reinterpret_cast<const float*>(reinterpret_cast<const uint8_t*>(first)+index*stride);};
        for(unsigned at=0;at<count;at+=3){float v[3][2]{};bool visible=true;
            for(unsigned n=0;n<3;++n){const auto* q=position(at+n);float clip[4]{};
                for(unsigned r=0;r<4;++r)clip[r]=vp[r]*q[0]+vp[4+r]*q[1]+vp[8+r]*q[2]+vp[12+r];
                // The VI shader flips the top-left N64 image into the drawable.
                if(clip[3]<=0){visible=false;break;}v[n][0]=(clip[0]/clip[3]+1)*W/2;v[n][1]=((flip?1:-1)*clip[1]/clip[3]+1)*H/2;}
            if(!visible)continue;
            const int lo_x=std::max(0,int(std::floor(std::min({v[0][0],v[1][0],v[2][0]})))-1);
            const int hi_x=std::min(int(W)-1,int(std::ceil(std::max({v[0][0],v[1][0],v[2][0]})))+1);
            const int lo_y=std::max(0,int(std::floor(std::min({v[0][1],v[1][1],v[2][1]})))-1);
            const int hi_y=std::min(int(H)-1,int(std::ceil(std::max({v[0][1],v[1][1],v[2][1]})))+1);
            for(int y=lo_y;y<=hi_y;++y)for(int x=lo_x;x<=hi_x;++x){bool pos=true,neg=true;
                for(unsigned edge=0;edge<3;++edge){const auto next=(edge+1)%3;float dx=v[next][0]-v[edge][0],dy=v[next][1]-v[edge][1];
                    const float cross=dx*(y+.5f-v[edge][1])-dy*(x+.5f-v[edge][0]);const float tolerance=std::sqrt(dx*dx+dy*dy);
                    pos=pos && cross>=-tolerance;neg=neg && cross<=tolerance;}
                if(pos||neg)mask[y*W+x]=1;}}
    }
    gc::Cost cost{}; // the last ghost frame's compositor cost
    Times frame(bool draw,bool evidence=false){
        const auto start=Clock::now();++frames;
        clock_word(fixture,fixture.config.global_timer,100+frames,4);
        const auto captured_source=capture_source(fixture,world.target,world.projection,1234+frames);
        auto live=captured_source.sample;live.pose.position[2]+=live_shift;const auto* qualified=ghost_stream::candidate();
        require(qualified && qualified->list_serial==live.dlist_serial,"world and source share exact occurrence");world=*qualified;
        require(live.global_timer==100+frames && live.igt==10 && live.clock.elapsed_frames==origin_clock.elapsed_frames
            && live.clock.continuity_id==origin_clock.continuity_id,"paused IGT stays at origin while global timer advances");
        ghost_playback::safe_boundary(7,configuration);
        ordinary_state(g,scene);raster.draw(&raster,{present::DrawKind::vi,scene.color,0,W,H});world_state(g);
        present::begin_update(presentation,live.sequence);
        gc::Pose poses[GP_GHOSTS]{};uint32_t count=0;gp_draw_token token{};
        if(draw){require(ghost_playback::evaluate(live,world,poses,count,token) && poses[0].count>1800,"actual playback/model geometry");
            vertices=0;for(uint32_t i=0;i<count;++i)vertices+=poses[i].count;visible=count;
            auto destination=target(scene,gc::Depth::ordinary);destination.occurrence=live.sequence;
            const auto known=known_before_seal(scene,raster.program,raster.vao);
            if(evidence){ // the witness's model of the state is the driver's
                int32_t draw_fbo=-1,read_fbo=-1,program=-1,vao=-1,depth=-1,blend[4]{},scissor[4]{};float viewport[4]{};
                bool on=false;uint8_t mask=0;
                g.GetIntegerv(cgl::DRAW_FRAMEBUFFER_BINDING,&draw_fbo);g.GetIntegerv(cgl::READ_FRAMEBUFFER_BINDING,&read_fbo);
                g.GetFloati_v(cgl::VIEWPORT,0,viewport);g.GetIntegeri_v(cgl::SCISSOR_BOX,0,scissor);
                g.GetIntegerv(cgl::CURRENT_PROGRAM,&program);g.GetIntegerv(cgl::VERTEX_ARRAY_BINDING,&vao);
                g.GetIntegerv(cgl::DEPTH_FUNC,&depth);g.GetBooleanv(cgl::DEPTH_WRITEMASK,&mask);
                for(unsigned i=0;i<4;++i)g.GetIntegeri_v(cgl::blend_queries[i],0,&blend[i]);
                for(auto cap:cgl::volatile_caps)if(cap!=cgl::BLEND && cap!=cgl::SCISSOR_TEST)on=on || g.IsEnabled(cap)!=0;
                uint8_t indexed[2]{};g.GetBooleani_v(cgl::BLEND,0,&indexed[0]);g.GetBooleani_v(cgl::SCISSOR_TEST,0,&indexed[1]);
                bool same=uint32_t(draw_fbo)==known.draw && uint32_t(read_fbo)==known.read && viewport[2]==float(W) &&
                    viewport[3]==float(H) && !on && !indexed[0] && !indexed[1] && uint32_t(program)==known.current_program &&
                    uint32_t(vao)==known.bound_vertex_array && uint32_t(depth)==known.depth_function && mask==known.depth_write;
                for(unsigned i=0;i<4;++i)same=same && uint32_t(blend[i])==known.blend_factor[i] && scissor[i]==known.scissor_box[i];
                require(same,"the known state is the driver's");
            }
            (void)gc::take_cost(compositor);
            require(gc::begin_batch(compositor,destination,poses,count,&known)==gc::Result::ok,"real compositor batch");
            cost=gc::take_cost(compositor);
            require(gc::finalize(compositor,live.sequence)==gc::Result::ok && ghost_playback::current(token),"finalized current ghost");
            const auto image=gc::display(compositor);
            const present::GhostViImage vi{image.texture,W,H,configuration,1,live.sequence};
            require(present::replace_vi(presentation,vi,0x1000,false,raster.api()),"real display/clean split");}
        require(present::before_swap(presentation,0x1000,raster.api()),"before swap");present::after_osd(presentation);
        glFinish();const auto completed=Clock::now();
        if(evidence){last_visible=back_pixels(g);if(draw)geometry_mask(poses,count);
            else require(last_visible==display_clean,"normal VI orientation and clean pixels");
            require(pixels(g,scene.fbo,W,H)==clean && depth(g,scene)==clean_depth,"source scene color/depth unmodified");}
        const auto swap_start=Clock::now();const bool swapped=SwapBuffers(window.dc)!=FALSE;
        present::swap_completed(presentation,swapped);const auto swap_end=Clock::now();
        require(swapped,"hidden SwapBuffers");const auto captured=present::surface(presentation);
        require(captured.presentation_serial && captured.update_occurrence==live.sequence,"published clean occurrence");
        if(evidence)require(pixels(g,captured.framebuffer,W,H)==display_clean,"clean saved image excludes Mario");
        no_error("connected frame");return {ms(start,completed),ms(swap_start,swap_end)};
    }
};

struct Stages {std::vector<double> batch,validate,state,copy,draw,draw_max,restore,queries;
    void add(const gc::Cost& c){batch.push_back(c.batch_micros);validate.push_back(c.validate_micros);
        state.push_back(c.state_micros);copy.push_back(c.copy_micros);
        draw.push_back(c.draw_micros);draw_max.push_back(c.draw_max_micros);restore.push_back(c.restore_micros);
        queries.push_back(c.queries);}
};
static void percentiles(const char* name,std::vector<double> values){
    std::sort(values.begin(),values.end());
    std::printf("\"%s\":{\"p50\":%.1f,\"p95\":%.1f,\"max\":%.1f}",name,values[values.size()/2],
        values[size_t(std::ceil(values.size()*.95))-1],values.back());
}
static void stages(const char* name,const Stages& s){
    std::printf("\"%s\":{",name);percentiles("batch_us",s.batch);std::printf(",");percentiles("validate_us",s.validate);
    std::printf(",");percentiles("state_us",s.state);
    std::printf(",");percentiles("copy_us",s.copy);std::printf(",");percentiles("draw_us",s.draw);std::printf(",");
    percentiles("slowest_ghost_us",s.draw_max);std::printf(",");percentiles("restore_us",s.restore);std::printf(",");
    percentiles("driver_queries",s.queries);std::printf("}");
}
static void statistics(const char* name,std::vector<double> values){
    std::sort(values.begin(),values.end());unsigned exceeded=0;for(auto value:values)if(value>1000.0/30)++exceeded;
    std::printf("\"%s\":{\"samples\":%zu,\"p50_ms\":%.6f,\"p95_ms\":%.6f,\"max_ms\":%.6f,\"over_budget\":%u}",
        name,values.size(),values[values.size()/2],values[size_t(std::ceil(values.size()*.95))-1],values.back(),exceeded);
}
int main(int argc,char** argv){
    require(argc==5 || argc==7,"usage: ghost_pipeline_host layout ROM output saved-GCLIP4-clip [width height]");
    if(argc==7){W=uint32_t(std::stoul(argv[5]));H=uint32_t(std::stoul(argv[6]));}
    require(W>=320 && H>=240 && W<=4096 && H<=4096,"drawable size");const auto foreground=GetForegroundWindow();
    Fixture fixture(argv[1]);configure_fixture(fixture);
    std::ifstream file(argv[2],std::ios::binary);std::vector<uint8_t> rom((std::istreambuf_iterator<char>(file)),{});
    require(rom.size()==(8u<<20),"read-only supplied ROM");const auto* api=ghost_playback::api();
    const auto prepare_start=Clock::now();require(api->prepare_model(1,rom.data(),uint32_t(rom.size()))==GP_OK,"actual model preparation");
    RenderOwner renderer;gs_record captured{};
    require(ghost_stream::api()->configure(&fixture.config)==1,"configure real source clock");
    renderer.run([&]{ghost_stream::rom(true);float matrix[16];projection(matrix);
        const ghost::RasterTarget target{1,2,W,H,1,0,{0,0,int32_t(W),int32_t(H)},{.5f,.5f},{1,1}};
        captured=capture_source(fixture,target,matrix,1234);});
    const auto& first=captured.sample;
    gclip_frame recorded{sizeof(recorded),GCLIP_VERSION,captured.configuration,captured.world.status,first};
    gclip_header header{};std::memcpy(header.magic,GCLIP_MAGIC,8);header.version=GCLIP_VERSION;header.bytes=sizeof(header);
    header.record_bytes=sizeof(recorded);header.record_count=1;header.rom_layout=GC_ROM_US;header.sample_version=GC_VERSION;
    header.configuration=captured.configuration;header.epoch=first.epoch;header.clip_id[0]=1;header.qpc_frequency=1000;header.utc_anchor=1;
    header.first_sequence=header.last_sequence=first.sequence;header.first_global_timer=header.last_global_timer=first.global_timer;
    header.level=first.level;header.area=first.area;header.clock_kind=GCLIP_SEGMENT_CLOCK;
    header.origin_global_timer=first.global_timer;header.origin_raw=first.igt;header.origin_sequence=first.sequence;
    header.origin_elapsed=first.clock.elapsed_frames;header.origin_continuity=first.clock.continuity_id;
    require(api->prepare_clip(&header,&recorded)==GP_OK,"actual captured clip validation");
    gp_request request{};request.bytes=sizeof(request);request.version=GP_VERSION;request.revision=1;request.enabled=1;
    request.owner_token=1;request.model_id=1;request.rom_generation=7;request.configuration=captured.configuration;
    request.clip_count=1;request.clips[0].clip_id[0]=1;request.opacity=1;
    request.shadow_enabled=0; // body-only proofs first; the shadow has its own section
    require(first.valid_fields&GC_VALID_SHADOW,"recorded pose carries its shadow");
    request.origin={GO_SEGMENT,first.epoch,first.sequence,first.clock.continuity_id,first.clock.elapsed_frames,first.clock.origin_id,0,0};
    require(api->configure(&request)==GP_OK,"configure exact-origin FOLLOW playback");std::unique_ptr<Pipeline> pipeline;
    // The same captured pose, shifted in view space: a grid of distinct ghosts.
    for(uint32_t i=1;i<GP_GHOSTS;++i){auto h=header;h.clip_id[0]=uint8_t(1+i);auto f=recorded;
        f.sample.pose.root_matrix[12]+=(float(i%4)-1.5f)*70.f;f.sample.pose.root_matrix[13]+=(float(i/4)-1.5f)*45.f;
        f.sample.shadow.matrix[12]+=(float(i%4)-1.5f)*70.f;f.sample.shadow.matrix[13]+=(float(i/4)-1.5f)*45.f;
        require(api->prepare_clip(&h,&f)==GP_OK,"prepare one of several ghosts");}
    renderer.run([&]{pipeline=std::make_unique<Pipeline>(fixture,captured);});const auto setup_ms=ms(prepare_start,Clock::now());
    const std::filesystem::path out(argv[3]);std::filesystem::create_directories(out);
    std::vector<uint8_t> baseline,opaque,half,wall;std::vector<double> baseline_times,ghost_times,swap_times,total_times;
    std::vector<double> multi_times,multi_swap_times,multi_total_times,four_times;Stages one_stages,four_stages,multi_stages;
    unsigned particle_pixels=0,star_pixels=0,star_rgb[3]={},silhouette=0,outside=0,alpha_errors=0,occlusion_errors=0,multi_silhouette=0,multi_outside=0,multi_visible=0,multi_vertices=0;
    renderer.run([&]{pipeline->frame(false,true);baseline=pipeline->last_visible;ppm(out/"baseline.ppm",baseline);
        pipeline->frame(true,true);opaque=pipeline->last_visible;ppm(out/"opacity100.ppm",opaque);
        silhouette=changed(baseline,opaque);require(silhouette>1000,"real Mario silhouette");
        // His recorded particles, from the ROM's own frames, and nothing of another object's.
        for(size_t i=0;i<W*H;++i)if(pipeline->particle_coverage[i] && !pipeline->body_coverage[i]
                && std::memcmp(&baseline[4*i],&opaque[4*i],3))++particle_pixels;
        require(particle_pixels>200,"recorded particles drawn");
        // The star's colour, pinned to his footage: ground-pound stars in attempt 2794
        // (SSL, 2026-09-23) decode as flat yellow, median 255,253,0, whatever their angle.
        // Yellow pixels of the particles are the star's; no other fixture particle is yellow.
        uint64_t sum[3]={};
        for(size_t i=0;i<W*H;++i){const uint8_t* c=&opaque[4*i];
            if(!pipeline->particle_coverage[i] || pipeline->body_coverage[i] || c[0]<200 || c[1]<200 || c[2]>60)continue;
            ++star_pixels;for(unsigned k=0;k<3;++k)sum[k]+=c[k];}
        for(unsigned k=0;k<3;++k)star_rgb[k]=star_pixels?unsigned(sum[k]/star_pixels):0;
        require(star_pixels>30 && star_rgb[0]>=245 && star_rgb[1]>=243 && star_rgb[2]<=10,
            "star drawn in the footage's flat yellow");
        for(size_t i=0;i<W*H;++i)if(!pipeline->coverage[i] && std::memcmp(&baseline[4*i],&opaque[4*i],3))++outside;
        require(outside==0,"outside independently projected triangles unchanged");});
    request.opacity=.5f;++request.revision;require(api->configure(&request)==GP_OK,"opacity50 request");
    renderer.run([&]{pipeline->frame(true,true);half=pipeline->last_visible;ppm(out/"opacity50.ppm",half);
        for(size_t i=0;i<half.size();++i)if(i%4!=3 && std::abs(int(half[i])*2-int(baseline[i])-int(opaque[i]))>2)++alpha_errors;
        require(alpha_errors==0,"per-fragment opacity is one midpoint blend");});
    request.opacity=1;++request.revision;require(api->configure(&request)==GP_OK,"opaque request restored");
    // The recorded shadow: switched on, it darkens only its own decal, never
    // outside the ghost and the shadow; switched off it is the frame above.
    unsigned shadow_pixels=0,shadow_outside=0,shadow_switch_errors=0,shadow_darkening_errors=0;
    unsigned shadow_alpha_errors=0,shadow_occlusion_errors=0,shadow_would_occlude=0;
    request.shadow_enabled=1;++request.revision;require(api->configure(&request)==GP_OK,"shadow switch on");
    std::vector<uint8_t> shadowed;
    renderer.run([&]{pipeline->frame(true,true);shadowed=pipeline->last_visible;ppm(out/"shadow100.ppm",shadowed);
        const auto& body=pipeline->body_coverage;const auto& shadow=pipeline->shadow_coverage;
        for(size_t i=0;i<W*H;++i){const auto* now=&shadowed[4*i];const auto* was=&baseline[4*i];
            const bool moved=std::memcmp(now,was,3)!=0;
            if(!pipeline->coverage[i] && moved)++shadow_outside;
            if(!shadow[i] && std::memcmp(now,&opaque[4*i],3))++shadow_switch_errors;
            if(shadow[i] && !body[i] && moved){++shadow_pixels;
                // Black decal over grey terrain: every channel darkens equally.
                if(now[0]>was[0] || now[0]!=now[1] || now[1]!=now[2])++shadow_darkening_errors;}}
        require(shadow_pixels>300 && shadow_outside==0 && shadow_switch_errors==0 && shadow_darkening_errors==0,
            "recorded shadow darkens only its own decal");});
    request.opacity=.5f;++request.revision;require(api->configure(&request)==GP_OK,"shadow opacity50");
    renderer.run([&]{pipeline->frame(true,true);const auto half_shadow=pipeline->last_visible;ppm(out/"shadow50.ppm",half_shadow);
        for(size_t i=0;i<W*H;++i)if(pipeline->shadow_coverage[i] && !pipeline->body_coverage[i])
            for(unsigned c=0;c<3;++c)if(std::abs(int(half_shadow[4*i+c])*2-int(baseline[4*i+c])-int(shadowed[4*i+c]))>2)++shadow_alpha_errors;
        require(shadow_alpha_errors==0,"ghost opacity scales its shadow once");});
    request.opacity=1;++request.revision;require(api->configure(&request)==GP_OK,"shadow opacity restored");
    renderer.run([&]{pipeline->background(true);pipeline->frame(false,true);const auto wall_baseline=pipeline->last_visible;
        pipeline->frame(true,true);ppm(out/"shadow-wall.ppm",pipeline->last_visible);
        for(unsigned y=0;y<H;++y)for(unsigned x=0;x<W;++x){const auto i=y*W+x;
            if(!pipeline->shadow_coverage[i] || pipeline->body_coverage[i])continue;
            if(x<W/2){shadow_would_occlude+=std::memcmp(&shadowed[4*i],&baseline[4*i],3)!=0;
                if(std::memcmp(&pipeline->last_visible[4*i],&wall_baseline[4*i],3))++shadow_occlusion_errors;}
            else if(std::memcmp(&pipeline->last_visible[4*i],&shadowed[4*i],3))++shadow_occlusion_errors;}
        require(shadow_would_occlude>50 && shadow_occlusion_errors==0,"nearer terrain hides the shadow decal");
        pipeline->background(false);});
    request.shadow_enabled=0;++request.revision;require(api->configure(&request)==GP_OK,"body-only wall proof");
    renderer.run([&]{pipeline->background(true);pipeline->frame(false,true);ppm(out/"wall-baseline.ppm",pipeline->last_visible);
        const auto wall_baseline=pipeline->last_visible;pipeline->frame(true,true);wall=pipeline->last_visible;ppm(out/"wall-opacity100.ppm",wall);
        unsigned would_occlude=0;for(unsigned y=0;y<H;++y)for(unsigned x=0;x<W;++x){const auto i=(y*W+x)*4;
            if(x<W/2){would_occlude+=std::memcmp(&baseline[i],&opaque[i],3)!=0;if(std::memcmp(&wall[i],&wall_baseline[i],3))++occlusion_errors;}
            else if(std::memcmp(&wall[i],&opaque[i],3))++occlusion_errors;}
        require(would_occlude>100 && occlusion_errors==0,"independent foreground depth occludes only covered Mario");
        pipeline->background(false);});
    // Submerged: the water plane at Mario's waist, camera above it. Behind it
    // the ghost takes the water's own blend; above it the ghost is unchanged;
    // outside the ghost the frame is exactly the game's water frame.
    unsigned water_tinted=0,water_dry=0,water_outside=0,water_blend_errors=0;
    renderer.run([&]{pipeline->water(true);pipeline->frame(false,true);const auto water_baseline=pipeline->last_visible;
        ppm(out/"water-baseline.ppm",water_baseline);
        pipeline->frame(true,true);const auto submerged=pipeline->last_visible;ppm(out/"water-opacity100.ppm",submerged);
        gc::break_layer(pipeline->compositor);pipeline->frame(true,true);const auto unlayered=pipeline->last_visible;
        ppm(out/"water-unlayered.ppm",unlayered);
        for(size_t i=0;i<W*H;++i){const auto* now=&submerged[4*i];const auto* dry=&unlayered[4*i];
            if(!pipeline->coverage[i]){water_outside+=std::memcmp(now,&water_baseline[4*i],3)!=0;continue;}
            if(!pipeline->body_coverage[i] || pipeline->particle_coverage[i])continue;
            if(!std::memcmp(now,dry,3)){++water_dry;continue;}
            ++water_tinted;
            for(unsigned c=0;c<3;++c)
                if(std::abs(float(now[c])-(Pipeline::water_rgb[c]*255*Pipeline::water_alpha+dry[c]*(1-Pipeline::water_alpha)))>2.5f)
                    ++water_blend_errors;}
        require(water_tinted>300 && water_dry>300 && water_outside==0 && water_blend_errors==0,
            "ghost below the water plane takes the water's blend, above it is unchanged");
        pipeline->water(false);});
    // Player first: live Mario 300 units beyond the ghost, so the ghost stands
    // between him and the camera and fades over his projected body only;
    // 300 units nearer instead, the ghost is behind him and draws unchanged.
    unsigned occlusion_faded=0,occlusion_outside=0,occlusion_behind_errors=0;
    request.occlusion_fade=1;++request.revision;require(api->configure(&request)==GP_OK,"player-first fade on");
    renderer.run([&]{pipeline->live_shift=-300;pipeline->frame(true,true);ppm(out/"occlusion-front.ppm",pipeline->last_visible);
        for(size_t i=0;i<W*H;++i){const bool moved=std::memcmp(&pipeline->last_visible[4*i],&opaque[4*i],3)!=0;
            if(moved && pipeline->body_coverage[i])++occlusion_faded;
            if(moved && !pipeline->body_coverage[i])++occlusion_outside;}
        pipeline->live_shift=300;pipeline->frame(true,true);ppm(out/"occlusion-behind.ppm",pipeline->last_visible);
        occlusion_behind_errors=changed(pipeline->last_visible,opaque);
        require(occlusion_faded>1000 && occlusion_outside==0 && occlusion_behind_errors==0,
            "a ghost fades over live Mario only when it stands in front of him");
        pipeline->live_shift=-300;});
    // Timing and the full set draw every ghost with its shadow, the fade on
    // with the ghost in front of live Mario (its costliest branch).
    request.shadow_enabled=1;++request.revision;require(api->configure(&request)==GP_OK,"shadows for timing");
    renderer.run([&]{
        for(unsigned i=0;i<20;++i){pipeline->frame(false);pipeline->frame(true);}
        for(unsigned i=0;i<180;++i){Times base{},ghost{};if(i&1){ghost=pipeline->frame(true);base=pipeline->frame(false);}
            else{base=pipeline->frame(false);ghost=pipeline->frame(true);}
            one_stages.add(pipeline->cost);
            baseline_times.push_back(base.pipeline);ghost_times.push_back(ghost.pipeline);swap_times.push_back(ghost.swap);total_times.push_back(ghost.pipeline+ghost.swap);}
        require(!IsWindowVisible(pipeline->window.window),"no visible native window");});
    // Four ghosts, then every ghost in one batch.
    request.clip_count=4;request.tint_enabled=1;
    for(uint32_t i=0;i<4;++i){request.clips[i].clip_id[0]=uint8_t(1+i);request.clips[i].hue_degrees=(i*22)%360;}
    ++request.revision;require(api->configure(&request)==GP_OK,"configure four ghosts");
    renderer.run([&]{
        for(unsigned i=0;i<20;++i){pipeline->frame(false);pipeline->frame(true);}
        for(unsigned i=0;i<180;++i){if(i&1){four_times.push_back(pipeline->frame(true).pipeline);four_stages.add(pipeline->cost);pipeline->frame(false);}
            else{pipeline->frame(false);four_times.push_back(pipeline->frame(true).pipeline);four_stages.add(pipeline->cost);}}
        require(pipeline->visible==4,"four ghosts stay visible");});
    // Every ghost in one batch: one clean copy, per-body depth, one presentation.
    request.clip_count=GP_GHOSTS;request.tint_enabled=1;
    for(uint32_t i=0;i<GP_GHOSTS;++i){request.clips[i].clip_id[0]=uint8_t(1+i);request.clips[i].hue_degrees=(i*22)%360;}
    ++request.revision;require(api->configure(&request)==GP_OK,"configure the full ghost set");
    renderer.run([&]{pipeline->frame(true,true);multi_visible=pipeline->visible;multi_vertices=pipeline->vertices;
        ppm(out/"multi-ghosts.ppm",pipeline->last_visible);
        multi_silhouette=changed(baseline,pipeline->last_visible);
        for(size_t i=0;i<W*H;++i)if(!pipeline->coverage[i] && std::memcmp(&baseline[4*i],&pipeline->last_visible[4*i],3))++multi_outside;
        require(multi_visible==GP_GHOSTS && multi_silhouette>2*silhouette && multi_outside==0,"every ghost drawn, nothing else changed");
        for(unsigned i=0;i<20;++i){pipeline->frame(false);pipeline->frame(true);}
        for(unsigned i=0;i<180;++i){Times base{},ghost{};if(i&1){ghost=pipeline->frame(true);base=pipeline->frame(false);}
            else{base=pipeline->frame(false);ghost=pipeline->frame(true);}
            multi_stages.add(pipeline->cost);
            multi_times.push_back(ghost.pipeline);multi_swap_times.push_back(ghost.swap);multi_total_times.push_back(ghost.pipeline+ghost.swap);}
        require(pipeline->visible==GP_GHOSTS,"full set stays visible");});
    const auto vertex_count=pipeline->vertices;const auto swap_disabled=pipeline->swap_interval_disabled;
    const auto gpu_vendor=pipeline->gpu_vendor,gpu_renderer=pipeline->gpu_renderer,gl_version=pipeline->gl_version;
    // A real saved GCLIP4 clip (recorded before particles, Whomp's Fortress like
    // this fixture's live scene) loads through the worker's own GCLIP4 reading
    // and draws Mario, at a fixed seek, under the camera he was recorded with.
    unsigned saved_pixels=0,saved_visible=0,saved_records=0;
    {
        std::ifstream saved(argv[4],std::ios::binary);
        std::vector<uint8_t> bytes((std::istreambuf_iterator<char>(saved)),{});
        require(bytes.size()>8 && !std::memcmp(bytes.data(),"SM64GHO4",8) && ghost_clip::upgrade(bytes),"real GCLIP4 clip read as the current version");
        gclip_header h{};std::memcpy(&h,bytes.data(),sizeof(h));saved_records=h.record_count;
        const auto* rows=reinterpret_cast<const gclip_frame*>(bytes.data()+sizeof(h));
        require(api->prepare_clip(&h,rows)==GP_OK,"real GCLIP4 clip prepared for playback");
        const auto& shown=rows[h.record_count/2].sample;
        require(!(shown.valid_fields&GC_VALID_PARTICLES) && (shown.valid_fields&GC_VALID_POSE) && shown.level==24,"a posed row, no particles");
        request.clip_count=1;request.tint_enabled=0;for(auto& clip:request.clips)clip={};std::memcpy(request.clips[0].clip_id,h.clip_id,16);
        request.mode=GP_FIXED;request.origin={};request.seek_igt=shown.igt;request.opacity=1;request.shadow_enabled=0;
        ++request.revision;require(api->configure(&request)==GP_OK,"fixed seek into the saved clip");
        renderer.run([&]{
            auto camera_word=[&](uint32_t address,float value){uint32_t bits;std::memcpy(&bits,&value,4);clock_word(fixture,address,bits,4);};
            for(unsigned i=0;i<3;++i){camera_word(Fixture::camera+0x1c+4*i,shown.camera.position[i]);
                camera_word(Fixture::camera+0x28+4*i,shown.camera.focus[i]);}
            pipeline->frame(false,true);const auto before=pipeline->last_visible;
            pipeline->frame(true,true);saved_visible=pipeline->visible;saved_pixels=changed(before,pipeline->last_visible);
            ppm(out/"saved-gclip4.ppm",pipeline->last_visible);
            require(saved_visible==1 && saved_pixels>300,"the saved GCLIP4 ghost draws");});
    }
    renderer.run([&]{pipeline.reset();});ghost_stream::api()->disable();api->revoke(GP_DISABLED);require(api->collect()==GP_OK,"worker collects real retired model");
    require(api->release_worker()==GP_OK,"worker releases real storage");require(GetForegroundWindow()==foreground,"foreground unchanged");
    std::printf("{\"ok\":true,\"synthetic\":true,\"live_gliden64\":false,\"width\":%u,\"height\":%u,\"vertices\":%u,\"silhouette_pixels\":%u,\"outside_pixels_changed\":%u,\"alpha_errors\":%u,\"occlusion_errors\":%u,\"setup_ms\":%.6f,\"swap_interval_disabled\":%s,",
        W,H,vertex_count,silhouette,outside,alpha_errors,occlusion_errors,setup_ms,swap_disabled?"true":"false");
    std::printf("\"capture_version\":%u,\"clip_version\":%u,\"playback_version\":%u,\"follow_segment_origin\":true,\"paused_igt_ignores_global_timer\":true,",
        GC_VERSION,GCLIP_VERSION,GP_VERSION);
    std::printf("\"gamma\":false,\"gpu_vendor\":");json_string(gpu_vendor);
    std::printf(",\"gpu_renderer\":");json_string(gpu_renderer);std::printf(",\"gl_version\":");json_string(gl_version);std::printf(",");
    statistics("baseline",baseline_times);std::printf(",");statistics("pipeline_gpu_completed",ghost_times);std::printf(",");
    statistics("blocking_swap",swap_times);std::printf(",");statistics("including_swap",total_times);
    // The compositor's own stages per ghost frame (microseconds) and its driver queries.
    std::printf(",\"stages\":{");stages("1",one_stages);std::printf(",");stages("4",four_stages);std::printf(",");
    stages("16",multi_stages);std::printf("},");statistics("four_pipeline_gpu_completed",four_times);
    std::printf(",\"shadow\":{\"captured\":true,\"pixels\":%u,\"outside_pixels_changed\":%u,\"switch_errors\":%u,"
        "\"darkening_errors\":%u,\"alpha_errors\":%u,\"occlusion_errors\":%u,\"would_occlude\":%u}",
        shadow_pixels,shadow_outside,shadow_switch_errors,shadow_darkening_errors,shadow_alpha_errors,
        shadow_occlusion_errors,shadow_would_occlude);
    std::printf(",\"particles\":{\"recorded\":5,\"pixels\":%u,\"star_pixels\":%u,\"star_rgb\":[%u,%u,%u]}",
        particle_pixels,star_pixels,star_rgb[0],star_rgb[1],star_rgb[2]);
    std::printf(",\"saved_gclip4\":{\"records\":%u,\"visible\":%u,\"pixels\":%u}",saved_records,saved_visible,saved_pixels);
    std::printf(",\"water\":{\"tinted\":%u,\"dry\":%u,\"outside_pixels_changed\":%u,\"blend_errors\":%u}",
        water_tinted,water_dry,water_outside,water_blend_errors);
    std::printf(",\"occlusion\":{\"faded\":%u,\"outside_pixels_changed\":%u,\"behind_errors\":%u}",
        occlusion_faded,occlusion_outside,occlusion_behind_errors);
    std::printf(",\"multi\":{\"ghosts\":%u,\"visible\":%u,\"vertices\":%u,\"silhouette_pixels\":%u,\"outside_pixels_changed\":%u,",
        unsigned(GP_GHOSTS),multi_visible,multi_vertices,multi_silhouette,multi_outside);
    statistics("pipeline_gpu_completed",multi_times);std::printf(",");statistics("blocking_swap",multi_swap_times);
    std::printf(",");statistics("including_swap",multi_total_times);std::printf("}}\n");return 0;
}
