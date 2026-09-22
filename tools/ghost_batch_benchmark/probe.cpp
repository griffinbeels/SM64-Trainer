// Synthetic, hidden WGL witness. No emulator or recording process interaction.
#define NOMINMAX
#define main compositor_fixture_program
#include <ghost_compositor_host.cpp>
#undef main
#include "model.h"
#include "ghost_presentation.h"
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <set>
#include <string>

namespace present=ghost_presentation;
using Clock=std::chrono::steady_clock;
static double milliseconds(Clock::time_point a,Clock::time_point b) {
    return std::chrono::duration<double,std::milli>(b-a).count();
}
static bool gateway=false;
static bool begin_gpu(){require(!gateway,"nested GPU gateway");gateway=true;return true;}
static bool end_gpu(){require(gateway,"unbalanced GPU gateway");gateway=false;return true;}

// Same small synthetic VI raster as tests/native/ghost_pipeline_host.cpp.
// This exercises actual presentation ownership, not GLideN64's VI shaders.
struct Raster {
    const gc::Dispatch& g;uint32_t program=0,vao=0;
    explicit Raster(const gc::Dispatch& d):g(d){
        const char* vs="#version 430 core\nout vec2 uv;uniform int flip;const vec2 p[6]=vec2[6](vec2(-1,-1),vec2(1,-1),vec2(-1,1),vec2(-1,1),vec2(1,-1),vec2(1,1));void main(){vec2 v=p[gl_VertexID];uv=(v+1)/2;if(flip!=0)uv.y=1-uv.y;gl_Position=vec4(v,0,1);}";
        const char* fs="#version 430 core\nin vec2 uv;uniform sampler2D t;out vec4 c;void main(){c=vec4(texture(t,uv).rgb,1);}";
        auto v=g.CreateShader(cgl::VERTEX_SHADER),f=g.CreateShader(cgl::FRAGMENT_SHADER);
        g.ShaderSource(v,1,&vs,nullptr);g.ShaderSource(f,1,&fs,nullptr);g.CompileShader(v);g.CompileShader(f);
        program=g.CreateProgram();g.AttachShader(program,v);g.AttachShader(program,f);g.LinkProgram(program);
        int32_t linked=0;g.GetProgramiv(program,cgl::LINK_STATUS,&linked);require(linked!=0,"VI shader link");
        g.DeleteShader(v);g.DeleteShader(f);g.GenVertexArrays(1,&vao);
    }
    ~Raster(){g.UseProgram(0);g.DeleteProgram(program);g.DeleteVertexArrays(1,&vao);}
    static void draw(void* owner,const present::Draw& d){
        auto& self=*static_cast<Raster*>(owner);const auto& g=self.g;
        g.BindFramebuffer(cgl::DRAW_FRAMEBUFFER,d.framebuffer);g.DrawBuffer(d.framebuffer?cgl::COLOR_ATTACHMENT0:GL_BACK);
        g.Disablei(GL_SCISSOR_TEST,0);g.Disablei(GL_BLEND,0);g.Disable(GL_DEPTH_TEST);g.Disable(GL_STENCIL_TEST);
        g.ColorMaski(0,1,1,1,1);g.Viewport(0,0,d.width,d.height);g.UseProgram(self.program);g.BindVertexArray(self.vao);
        g.ActiveTexture(cgl::TEXTURE0);g.BindSampler(0,0);g.BindTexture(GL_TEXTURE_2D,d.texture);
        g.Uniform1i(g.GetUniformLocation(self.program,"t"),0);
        g.Uniform1i(g.GetUniformLocation(self.program,"flip"),d.kind==present::DrawKind::vi);
        g.DrawArrays(GL_TRIANGLES,0,6);
    }
    present::Raster api(){return {this,draw,begin_gpu,end_gpu};}
};
struct Geometry {
    std::vector<float> positions,normals,colors,uv;
    std::vector<uint32_t> materials;
    std::vector<gc::Vertex> vertices;
    sg_mesh mesh;
    Geometry():positions(SG_MODEL_MAX_VERTICES*3),normals(positions.size()),colors(positions.size()),
        uv(SG_MODEL_MAX_VERTICES*2),materials(SG_MODEL_MAX_VERTICES),vertices(SG_MODEL_MAX_VERTICES),
        mesh{positions.data(),normals.data(),colors.data(),uv.data(),materials.data(),SG_MODEL_MAX_VERTICES,0,0}{}
    void pack(){for(unsigned i=0;i<mesh.vertex_count;++i){
        auto& v=vertices[i];std::memcpy(v.position,&positions[3*i],sizeof(v.position));
        std::memcpy(v.normal,&normals[3*i],sizeof(v.normal));std::memcpy(v.color,&colors[3*i],sizeof(v.color));
        std::memcpy(v.uv,&uv[2*i],sizeof(v.uv));v.material=static_cast<gc::Material>(materials[i]);}}
    uint64_t hash() const {
        uint64_t h=1469598103934665603ull;const auto* p=reinterpret_cast<const uint8_t*>(positions.data());
        for(size_t i=0;i<mesh.vertex_count*3*sizeof(float);++i){h^=p[i];h*=1099511628211ull;}return h;
    }
};
struct Times {double model,pack,compositor,presentation,submit,wait,completed,swap,total;};
struct Bench {
    Window window;gc::Dispatch g;Scene scene;Raster raster;
    sg_model* model=nullptr;gc::Instance* compositor=nullptr;present::Instance* presentation=nullptr;
    Geometry geometry[64];gc::Pose poses[64]{};
    unsigned width,height;uint64_t occurrence=100;bool swap_disabled=false;
    std::vector<uint8_t> clean,display_clean;std::vector<float> clean_depth;
    Bench(const std::vector<uint8_t>& rom,unsigned w,unsigned h):g(dispatch()),scene(g,w,h),raster(g),width(w),height(h){
        require(SetWindowPos(window.window,nullptr,0,0,w,h,SWP_NOACTIVATE|SWP_NOZORDER)!=0,"resize hidden window");
        require(!IsWindowVisible(window.window),"benchmark window must stay hidden");
        RECT rect{};require(GetClientRect(window.window,&rect) && rect.right==int(w) && rect.bottom==int(h),"drawable size");
        using SwapInterval=BOOL(WINAPI*)(int);using GetInterval=int(WINAPI*)();
        const auto set=reinterpret_cast<SwapInterval>(load("wglSwapIntervalEXT"));
        const auto get=reinterpret_cast<GetInterval>(load("wglGetSwapIntervalEXT"));
        swap_disabled=set && get && set(0) && get()==0;require(swap_disabled,"verified zero swap interval");
        require(sg_model_open(rom.data(),rom.size(),&model)==SG_OK,"actual model open");
        sg_atlas_view atlas{};require(sg_model_get_atlas(model,&atlas)==SG_OK,"actual atlas");
        compositor=gc::create(g,1);require(compositor!=nullptr,"compositor create");
        require(gc::prepare(compositor,w,h,64*SG_MODEL_MAX_VERTICES,atlas.rgba,atlas.width,atlas.height)==gc::Result::ok,"batch capacity");
        present::Dispatch d{};d.gl=g;d.CopyTexSubImage2D=glCopyTexSubImage2D;
        d.DrawBuffers=reinterpret_cast<decltype(d.DrawBuffers)>(load("glDrawBuffers"));
        presentation=present::create(d,1);require(presentation && present::prepare(presentation,1,w,h,w,h),"presentation prepare");
        ordinary_state(g,scene);std::vector<uint8_t> color(w*h*4);std::vector<float> z(w*h,1.f);
        for(unsigned y=0;y<h;++y)for(unsigned x=0;x<w;++x){const auto i=y*w+x;const auto tone=uint8_t(((x/32+y/32)&1)?170:200);
            color[4*i]=tone;color[4*i+1]=tone;color[4*i+2]=tone;color[4*i+3]=255;}
        g.BindTexture(GL_TEXTURE_2D,scene.color);g.TexImage2D(GL_TEXTURE_2D,0,cgl::RGBA8,w,h,0,GL_RGBA,GL_UNSIGNED_BYTE,color.data());
        g.TexParameteri(GL_TEXTURE_2D,cgl::TEXTURE_MIN_FILTER,GL_NEAREST);g.TexParameteri(GL_TEXTURE_2D,cgl::TEXTURE_MAG_FILTER,GL_NEAREST);
        g.BindTexture(GL_TEXTURE_2D,scene.depth);g.TexImage2D(GL_TEXTURE_2D,0,cgl::DEPTH_COMPONENT24,w,h,0,GL_DEPTH_COMPONENT,GL_FLOAT,z.data());
        clean=pixels(g,scene.fbo,w,h);clean_depth=depth(g,scene);display_clean=flip(clean);glFinish();
    }
    ~Bench(){present::destroy(presentation);gc::destroy(compositor);require(sg_model_close(&model)==SG_OK,"model close");}
    std::vector<uint8_t> flip(const std::vector<uint8_t>& source) const {
        std::vector<uint8_t> out(source.size());for(unsigned y=0;y<height;++y)
            std::memcpy(&out[y*width*4],&source[(height-1-y)*width*4],width*4);return out;
    }
    void evaluate(unsigned n,unsigned frame,bool overlap){
        for(unsigned i=0;i<n;++i){sg_pose p{};p.fields=SG_POSE_REQUIRED;p.scale[0]=p.scale[1]=p.scale[2]=1;
            p.action=p.body_action=0x04000440; // ACT_WALKING, with actual walking animation.
            p.animation_id=0x48;p.animation_frame=int16_t((frame+3*i)%30);p.eye_state=1;
            p.angles[1]=int16_t(12000+int(i)*331);p.area_update_counter=frame;
            require(sg_model_evaluate(model,&p,&geometry[i].mesh)==SG_OK && geometry[i].mesh.vertex_count>1000,"evaluate real animated Mario");
            (void)overlap;
        }
    }
    unsigned pack(unsigned n,bool overlap,float opacity=.35f){
        const unsigned cols=unsigned(std::ceil(std::sqrt(double(n)))),rows=(n+cols-1)/cols;
        unsigned total=0;
        for(unsigned i=0;i<n;++i){auto& geometry_i=geometry[i];geometry_i.pack();auto& p=poses[i];p={};
            p.vertices=geometry_i.vertices.data();p.count=geometry_i.mesh.vertex_count;total+=p.count;
            const float sx=overlap?.004f:1.7f/(300.f*cols),sy=overlap?.0055f:1.8f/(250.f*rows);
            p.view_projection[0]=sx;p.view_projection[5]=sy;p.view_projection[10]=-.001f;p.view_projection[15]=1;
            p.view_projection[12]=overlap?.2f*std::sin(float(i)*.9f):-1.f+(2.f*float(i%cols)+1.f)/cols;
            p.view_projection[13]=(overlap?.1f*std::cos(float(i)*.7f):1.f-(2.f*float(i/cols)+1.f)/rows)-100.f*sy;
            // Scalar depth ordering: every later body is closer, while limbs may intersect.
            p.view_projection[14]=.3f-float(i)*.6f/float(std::max(1u,n));
            p.light_direction[0]=.3f;p.light_direction[1]=.6f;p.light_direction[2]=.75f;
            p.ambient=.45f;p.diffuse=.55f;p.opacity=opacity;p.flip_y=1;p.hue_degrees=float((i*47)%360);
        }return total;
    }
    void composite(const gc::Pose* p,unsigned n){
        auto t=target(scene,gc::Depth::ordinary);t.occurrence=++occurrence;
        require(gc::begin_batch(compositor,t,p,n)==gc::Result::ok,"batch begin");
        require(gc::finalize(compositor,occurrence)==gc::Result::ok,"batch finalize");
    }
    void source_color(const std::vector<uint8_t>& color){
        ordinary_state(g,scene);g.BindTexture(GL_TEXTURE_2D,scene.color);
        g.TexImage2D(GL_TEXTURE_2D,0,cgl::RGBA8,width,height,0,GL_RGBA,GL_UNSIGNED_BYTE,color.data());
    }
    Times frame(unsigned n,unsigned frame_number,bool overlap,bool evidence=false){
        const auto start=Clock::now();evaluate(n,frame_number,overlap);const auto evaluated=Clock::now();
        pack(n,overlap);const auto packed=Clock::now();
        ordinary_state(g,scene);raster.draw(&raster,{present::DrawKind::vi,scene.color,0,width,height});
        const auto composite_start=Clock::now();composite(poses,n);const auto composited=Clock::now();
        present::begin_update(presentation,occurrence);const auto image=gc::display(compositor);
        const present::GhostViImage vi{image.texture,width,height,1,1,occurrence,image.framebuffer};
        require(present::replace_vi(presentation,vi,0x1000,false,raster.api()),"replace visible VI");
        require(present::before_swap(presentation,0x1000,raster.api()),"before swap");present::after_osd(presentation);
        const auto submitted=Clock::now();glFinish();const auto completed=Clock::now();
        if(evidence){
            const auto expected=flip(pixels(g,image.framebuffer,width,height));cgl::Guard guard(g);
            g.BindFramebuffer(cgl::READ_FRAMEBUFFER,0);g.ReadBuffer(GL_BACK);std::vector<uint8_t> actual(expected.size());
            glReadPixels(0,0,width,height,GL_RGBA,GL_UNSIGNED_BYTE,actual.data());require(actual==expected,"visible BACK equals batch VI");
        }
        const auto swap_start=Clock::now();const bool swapped=SwapBuffers(window.dc)!=FALSE;
        present::swap_completed(presentation,swapped);const auto swapped_at=Clock::now();require(swapped,"hidden swap");
        const auto surface=present::surface(presentation);require(surface.update_occurrence==occurrence && surface.presentation_serial,"clean occurrence");
        if(evidence){require(pixels(g,scene.fbo,width,height)==clean && depth(g,scene)==clean_depth,"source color/depth untouched");
            require(pixels(g,surface.framebuffer,width,height)==display_clean,"whole-picture clean untouched");}
        no_error("benchmark frame");require(!IsWindowVisible(window.window),"window remains hidden");
        return {milliseconds(start,evaluated),milliseconds(evaluated,packed),milliseconds(composite_start,composited),
            milliseconds(packed,composite_start)+milliseconds(composited,submitted),milliseconds(start,submitted),
            milliseconds(submitted,completed),milliseconds(start,completed),milliseconds(swap_start,swapped_at),
            milliseconds(start,completed)+milliseconds(swap_start,swapped_at)};
    }
    void image_file(const std::filesystem::path& path,const std::vector<uint8_t>& rgba){
        std::ofstream file(path,std::ios::binary);file<<"P6\n"<<width<<' '<<height<<"\n255\n";
        for(int y=int(height)-1;y>=0;--y)for(unsigned x=0;x<width;++x)
            file.write(reinterpret_cast<const char*>(&rgba[(y*width+x)*4]),3);require(bool(file),"write private image");
    }
    void proof(unsigned n,bool overlap,const std::filesystem::path& out,std::ofstream& report){
        constexpr unsigned f=7;evaluate(n,f,overlap);const auto vertices=pack(n,overlap);
        std::set<uint64_t> hashes;for(unsigned i=0;i<n;++i)hashes.insert(geometry[i].hash());
        require(hashes.size()==n,"every requested pose has distinct geometry");const auto first_hash=geometry[0].hash();
        std::vector<uint8_t> reference=clean,last_only=clean,black=clean,white=clean;
        for(size_t p=0;p<width*height;++p)for(unsigned c=0;c<3;++c){black[4*p+c]=0;white[4*p+c]=255;}
        std::vector<unsigned> ownership(width*height,0),covered(n,0);
        for(unsigned i=0;i<n;++i){auto solo=poses[i];solo.opacity=1;
            source_color(black);composite(&solo,1);const auto dark=pixels(g,gc::display(compositor).framebuffer,width,height);
            source_color(white);composite(&solo,1);const auto light=pixels(g,gc::display(compositor).framebuffer,width,height);
            for(size_t p=0;p<width*height;++p){bool coverage=false;
                for(unsigned c=0;c<3;++c)coverage|=int(light[4*p+c])-int(dark[4*p+c])<255;
                if(!coverage)continue;
                ++covered[i];if(!overlap)require(ownership[p]==0,"separated reference masks do not overlap");ownership[p]=i+1;
                for(unsigned c=0;c<3;++c){const auto offset=4*p+c;
                    // dark is premultiplied source RGB; white-dark is 1-alpha.
                    // Per-channel recovery tolerates the source's 8-bit quantization.
                    const float a=1.f-float(int(light[offset])-int(dark[offset]))/255.f;
                    reference[offset]=uint8_t(std::lround(.35f*dark[offset]+(1.f-.35f*a)*reference[offset]));
                    if(i==n-1)last_only[offset]=uint8_t(std::lround(.35f*dark[offset]+(1.f-.35f*a)*clean[offset]));}}
            require(covered[i]>20,"each individual Mario covers pixels");
        }
        source_color(clean);
        composite(poses,n);const auto actual=pixels(g,gc::display(compositor).framebuffer,width,height);
        unsigned mismatch=0,max_error=0,counterfactual=0;std::vector<unsigned> survived(n,0);
        for(size_t p=0;p<width*height;++p){bool bad=false,counter=false;
            for(unsigned c=0;c<3;++c){const auto error=unsigned(std::abs(int(actual[4*p+c])-int(reference[4*p+c])));
                max_error=std::max(max_error,error);bad|=error>4;counter|=std::abs(int(last_only[4*p+c])-int(reference[4*p+c]))>4;}
            mismatch+=bad;counterfactual+=counter;if(ownership[p] && std::memcmp(&actual[4*p],&clean[4*p],3))++survived[ownership[p]-1];}
        if(mismatch){std::fprintf(stderr,"case=%s-%u max_error=%u mismatch=%u\n",overlap?"overlap":"separated",n,max_error,mismatch);
            image_file(out/"failure-actual.ppm",flip(actual));image_file(out/"failure-reference.ppm",flip(reference));}
        require(mismatch==0,"batch matches independent sequential alpha composition within 4/255");
        if(n>1)require(counterfactual>20,"last-only counterfactual must fail witness");
        if(!overlap)for(unsigned i=0;i<n;++i)require(survived[i]>20,"all N survive the same batch frame");
        const std::string label=std::string(overlap?"overlap-":"separated-")+std::to_string(n);
        if(n==1 || n==4 || n==64)image_file(out/(label+".ppm"),flip(actual));
        frame(n,f,overlap,true);evaluate(n,f+1,overlap);require(first_hash!=geometry[0].hash(),"adjacent frames animate actual mesh");
        report<<label<<','<<vertices<<','<<hashes.size()<<','<<*std::min_element(covered.begin(),covered.end())<<','
              <<max_error<<','<<mismatch<<','<<counterfactual<<",1,1,1\n";
    }
};
int main(int argc,char** argv){
    require(argc==8,"usage: probe ROM output width height samples warmup max-count");
    std::ifstream file(argv[1],std::ios::binary);std::vector<uint8_t> rom((std::istreambuf_iterator<char>(file)),{});
    require(rom.size()==(8u<<20),"supplied local stock US ROM");const std::filesystem::path out(argv[2]);
    const unsigned width=std::stoul(argv[3]),height=std::stoul(argv[4]),samples=std::stoul(argv[5]),warmup=std::stoul(argv[6]),max_count=std::stoul(argv[7]);
    require(width<=1280 && height<=960 && samples>=1 && samples<=2000 && warmup<=100 && max_count<=64,"bounded benchmark");
    Bench b(rom,width,height);std::ofstream raw(out/"frames.csv"),proof(out/"proof.csv");
    raw<<"layout,count,frame,model_ms,pack_ms,compositor_ms,presentation_ms,submit_ms,gpu_wait_ms,completed_ms,swap_ms,total_ms\n";
    proof<<"case,vertices,distinct_geometry,min_covered_pixels,max_rgb_error,mismatch_pixels,last_only_mismatch_pixels,source_unchanged,clean_unchanged,visible_matches\n";
    for(bool overlap:{false,true})for(unsigned n:{1u,4u,16u,32u,64u}){if(n>max_count)continue;
        b.proof(n,overlap,out,proof);
        for(unsigned f=0;f<warmup+samples;++f){const auto t=b.frame(n,f,overlap);if(f<warmup)continue;
            raw<<(overlap?"overlap":"separated")<<','<<n<<','<<f<<','<<t.model<<','<<t.pack<<','<<t.compositor<<','
               <<t.presentation<<','<<t.submit<<','<<t.wait<<','<<t.completed<<','<<t.swap<<','<<t.total<<'\n';}
    }
    require(bool(raw) && bool(proof),"write private results");
    std::printf("{\"gpu_vendor\":\"%s\",\"gpu_renderer\":\"%s\",\"gl_version\":\"%s\",\"swap_interval\":0,\"hidden\":true,\"shadow\":false}\n",
        glGetString(GL_VENDOR),glGetString(GL_RENDERER),glGetString(GL_VERSION));return 0;
}
