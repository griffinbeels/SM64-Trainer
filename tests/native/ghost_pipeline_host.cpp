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
static constexpr uint32_t W=640,H=480;
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
    ghost_stream::projection(matrix);ghost_stream::triangles(target,2,true);
    require(ghost_stream::geometry(true,false),"source qualifies synthetic world end");
    ghost_stream::end(true);gs_record out{};
    require(ghost_stream::api()->read(&out,sizeof(out))==1 && out.world.status==GCLIP_WORLD_READY,"actual source queue occurrence");
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
struct Pipeline {
    Window window;gc::Dispatch g;Scene scene;ViRaster raster;gc::Instance* compositor=nullptr;
    present::Instance* presentation=nullptr;Fixture& fixture;unsigned frames=0,vertices=0;
    bool swap_interval_disabled=false;std::vector<uint8_t> clean,display_clean,last_visible,coverage;
    std::string gpu_vendor,gpu_renderer,gl_version;
    std::vector<float> clean_depth;ghost::WorldPass world{};uint32_t configuration=0;gcc_output origin_clock{};
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
        require(gc::prepare(compositor,W,H,SG_MODEL_MAX_VERTICES,atlas.rgba,atlas.width,atlas.height)==gc::Result::ok,"real atlas upload");
        present::Dispatch d{};d.gl=g;d.CopyTexSubImage2D=glCopyTexSubImage2D;
        d.DrawBuffers=reinterpret_cast<decltype(d.DrawBuffers)>(load("glDrawBuffers"));
        presentation=present::create(d,1);require(presentation && present::prepare(presentation,1,W,H,W,H),"pipeline clean presentation");
        world.context_generation=1;world.target={scene.color,scene.depth,W,H,1,0,{0,0,int32_t(W),int32_t(H)},{.5f,.5f},{1,1}};
        projection(world.projection);background(false);glFinish();
    }
    ~Pipeline(){present::destroy(presentation);gc::destroy(compositor);ghost_playback::retire_on_context_loss();ghost_stream::rom(false);}
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
    void geometry_mask(const gc::Pose& p){
        coverage.assign(W*H,0);
        for(unsigned at=0;at<p.count;at+=3){float v[3][2]{};bool visible=true;
            for(unsigned n=0;n<3;++n){const auto& q=p.vertices[at+n].position;float clip[4]{};
                for(unsigned r=0;r<4;++r)clip[r]=p.view_projection[r]*q[0]+p.view_projection[4+r]*q[1]+p.view_projection[8+r]*q[2]+p.view_projection[12+r];
                // The VI shader flips the top-left N64 image into the drawable.
                if(clip[3]<=0){visible=false;break;}v[n][0]=(clip[0]/clip[3]+1)*W/2;v[n][1]=((p.flip_y?1:-1)*clip[1]/clip[3]+1)*H/2;}
            if(!visible)continue;
            const int lo_x=std::max(0,int(std::floor(std::min({v[0][0],v[1][0],v[2][0]})))-1);
            const int hi_x=std::min(int(W)-1,int(std::ceil(std::max({v[0][0],v[1][0],v[2][0]})))+1);
            const int lo_y=std::max(0,int(std::floor(std::min({v[0][1],v[1][1],v[2][1]})))-1);
            const int hi_y=std::min(int(H)-1,int(std::ceil(std::max({v[0][1],v[1][1],v[2][1]})))+1);
            for(int y=lo_y;y<=hi_y;++y)for(int x=lo_x;x<=hi_x;++x){bool pos=true,neg=true;
                for(unsigned edge=0;edge<3;++edge){const auto next=(edge+1)%3;float dx=v[next][0]-v[edge][0],dy=v[next][1]-v[edge][1];
                    const float cross=dx*(y+.5f-v[edge][1])-dy*(x+.5f-v[edge][0]);const float tolerance=std::sqrt(dx*dx+dy*dy);
                    pos=pos && cross>=-tolerance;neg=neg && cross<=tolerance;}
                if(pos||neg)coverage[y*W+x]=1;}}
    }
    Times frame(bool draw,bool evidence=false){
        const auto start=Clock::now();++frames;
        clock_word(fixture,fixture.config.global_timer,100+frames,4);
        const auto captured_source=capture_source(fixture,world.target,world.projection,1234+frames);
        const auto& live=captured_source.sample;const auto* qualified=ghost_stream::candidate();
        require(qualified && qualified->list_serial==live.dlist_serial,"world and source share exact occurrence");world=*qualified;
        require(live.global_timer==100+frames && live.igt==10 && live.clock.elapsed_frames==origin_clock.elapsed_frames
            && live.clock.continuity_id==origin_clock.continuity_id,"paused IGT stays at origin while global timer advances");
        ghost_playback::safe_boundary(7,configuration);
        ordinary_state(g,scene);raster.draw(&raster,{present::DrawKind::vi,scene.color,0,W,H});
        present::begin_update(presentation,live.sequence);
        gc::Pose p{};gp_draw_token token{};
        if(draw){require(ghost_playback::evaluate(live,world,p,token) && p.count>1800,"actual playback/model geometry");vertices=p.count;
            auto destination=target(scene,gc::Depth::ordinary);destination.occurrence=live.sequence;
            require(gc::begin(compositor,destination,p)==gc::Result::ok,"real compositor begin");
            require(gc::finalize(compositor,live.sequence)==gc::Result::ok && ghost_playback::current(token),"finalized current ghost");
            const auto image=gc::display(compositor);
            const present::GhostViImage vi{image.texture,W,H,configuration,1,live.sequence};
            require(present::replace_vi(presentation,vi,0x1000,false,raster.api()),"real display/clean split");}
        require(present::before_swap(presentation,0x1000,raster.api()),"before swap");present::after_osd(presentation);
        glFinish();const auto completed=Clock::now();
        if(evidence){last_visible=back_pixels(g);if(draw)geometry_mask(p);
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

static void statistics(const char* name,std::vector<double> values){
    std::sort(values.begin(),values.end());unsigned exceeded=0;for(auto value:values)if(value>1000.0/30)++exceeded;
    std::printf("\"%s\":{\"samples\":%zu,\"p50_ms\":%.6f,\"p95_ms\":%.6f,\"max_ms\":%.6f,\"over_budget\":%u}",
        name,values.size(),values[values.size()/2],values[size_t(std::ceil(values.size()*.95))-1],values.back(),exceeded);
}
int main(int argc,char** argv){
    require(argc==4,"usage: ghost_pipeline_host layout ROM output");const auto foreground=GetForegroundWindow();
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
    request.clip_id[0]=1;request.opacity=1;
    request.origin={GO_SEGMENT,first.epoch,first.sequence,first.clock.continuity_id,first.clock.elapsed_frames,first.clock.origin_id,0,0};
    require(api->configure(&request)==GP_OK,"configure exact-origin FOLLOW playback");std::unique_ptr<Pipeline> pipeline;
    renderer.run([&]{pipeline=std::make_unique<Pipeline>(fixture,captured);});const auto setup_ms=ms(prepare_start,Clock::now());
    const std::filesystem::path out(argv[3]);std::filesystem::create_directories(out);
    std::vector<uint8_t> baseline,opaque,half,wall;std::vector<double> baseline_times,ghost_times,swap_times,total_times;
    unsigned silhouette=0,outside=0,alpha_errors=0,occlusion_errors=0;
    renderer.run([&]{pipeline->frame(false,true);baseline=pipeline->last_visible;ppm(out/"baseline.ppm",baseline);
        pipeline->frame(true,true);opaque=pipeline->last_visible;ppm(out/"opacity100.ppm",opaque);
        silhouette=changed(baseline,opaque);require(silhouette>1000,"real Mario silhouette");
        for(size_t i=0;i<W*H;++i)if(!pipeline->coverage[i] && std::memcmp(&baseline[4*i],&opaque[4*i],3))++outside;
        require(outside==0,"outside independently projected triangles unchanged");});
    request.opacity=.5f;++request.revision;require(api->configure(&request)==GP_OK,"opacity50 request");
    renderer.run([&]{pipeline->frame(true,true);half=pipeline->last_visible;ppm(out/"opacity50.ppm",half);
        for(size_t i=0;i<half.size();++i)if(i%4!=3 && std::abs(int(half[i])*2-int(baseline[i])-int(opaque[i]))>2)++alpha_errors;
        require(alpha_errors==0,"per-fragment opacity is one midpoint blend");});
    request.opacity=1;++request.revision;require(api->configure(&request)==GP_OK,"opaque request restored");
    renderer.run([&]{pipeline->background(true);pipeline->frame(false,true);ppm(out/"wall-baseline.ppm",pipeline->last_visible);
        const auto wall_baseline=pipeline->last_visible;pipeline->frame(true,true);wall=pipeline->last_visible;ppm(out/"wall-opacity100.ppm",wall);
        unsigned would_occlude=0;for(unsigned y=0;y<H;++y)for(unsigned x=0;x<W;++x){const auto i=(y*W+x)*4;
            if(x<W/2){would_occlude+=std::memcmp(&baseline[i],&opaque[i],3)!=0;if(std::memcmp(&wall[i],&wall_baseline[i],3))++occlusion_errors;}
            else if(std::memcmp(&wall[i],&opaque[i],3))++occlusion_errors;}
        require(would_occlude>100 && occlusion_errors==0,"independent foreground depth occludes only covered Mario");
        pipeline->background(false);
        for(unsigned i=0;i<20;++i){pipeline->frame(false);pipeline->frame(true);}
        for(unsigned i=0;i<180;++i){Times base{},ghost{};if(i&1){ghost=pipeline->frame(true);base=pipeline->frame(false);}
            else{base=pipeline->frame(false);ghost=pipeline->frame(true);}
            baseline_times.push_back(base.pipeline);ghost_times.push_back(ghost.pipeline);swap_times.push_back(ghost.swap);total_times.push_back(ghost.pipeline+ghost.swap);}
        require(!IsWindowVisible(pipeline->window.window),"no visible native window");});
    const auto vertex_count=pipeline->vertices;const auto swap_disabled=pipeline->swap_interval_disabled;
    const auto gpu_vendor=pipeline->gpu_vendor,gpu_renderer=pipeline->gpu_renderer,gl_version=pipeline->gl_version;
    renderer.run([&]{pipeline.reset();});ghost_stream::api()->disable();api->revoke(GP_DISABLED);require(api->collect()==GP_OK,"worker collects real retired model");
    require(api->release_worker()==GP_OK,"worker releases real storage");require(GetForegroundWindow()==foreground,"foreground unchanged");
    std::printf("{\"ok\":true,\"synthetic\":true,\"live_gliden64\":false,\"width\":640,\"height\":480,\"vertices\":%u,\"silhouette_pixels\":%u,\"outside_pixels_changed\":%u,\"alpha_errors\":%u,\"occlusion_errors\":%u,\"setup_ms\":%.6f,\"swap_interval_disabled\":%s,",
        vertex_count,silhouette,outside,alpha_errors,occlusion_errors,setup_ms,swap_disabled?"true":"false");
    std::printf("\"capture_version\":%u,\"clip_version\":%u,\"playback_version\":%u,\"follow_segment_origin\":true,\"paused_igt_ignores_global_timer\":true,",
        GC_VERSION,GCLIP_VERSION,GP_VERSION);
    std::printf("\"gamma\":false,\"gpu_vendor\":");json_string(gpu_vendor);
    std::printf(",\"gpu_renderer\":");json_string(gpu_renderer);std::printf(",\"gl_version\":");json_string(gl_version);std::printf(",");
    statistics("baseline",baseline_times);std::printf(",");statistics("pipeline_gpu_completed",ghost_times);std::printf(",");
    statistics("blocking_swap",swap_times);std::printf(",");statistics("including_swap",total_times);std::printf("}\n");return 0;
}
