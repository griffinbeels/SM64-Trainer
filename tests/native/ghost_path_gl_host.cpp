/* The ghost's path through the real compositor on a hidden core context:
 * depth-tested against the copied terrain with its faint pass behind it, each
 * layer covering a pixel once, button icons one size at any distance, and
 * (given a ROM) the chevron cell as drawn, for tests/test_ghost_path.py to
 * compare with its own decode of the cartridge. Reuses the compositor
 * witness's window, scene and state checks. */
#define main compositor_witness_main
#include "ghost_compositor_host.cpp"
#undef main
#include "ghost_path.h"
#include "ghost_annotation_assets.h"
#include <fstream>
#include <iterator>

namespace gp=ghost_path;
// World x right in pixels, world z down the picture in pixels, world y the
// depth (clip z = y / 100): the ribbon's cross-section runs down the picture.
// scale: the same picture with every clip coordinate times it (w = scale);
// pixels: picture pixels per world unit.
static void pixel_camera(gp::Frame& f,uint32_t w,uint32_t h,float scale=1,float pixels=1) {
    float* m=f.view_projection;std::memset(m,0,16*sizeof(float));
    m[0]=2.0f/float(w)*scale*pixels;m[12]=-scale;m[9]=2.0f/float(h)*scale*pixels;m[13]=-scale;m[6]=0.01f*scale;m[15]=scale;
    f.flip_y=0;f.eye[0]=0;f.eye[1]=1e5f;f.eye[2]=0;
}
// A straight ground line at depth y (lifted onto y + GP_PATH_LIFT), one point a frame.
static void line(gp::Frame& f,const float* xs,uint32_t n,float z,float y,float speed) {
    f.count=n;
    for(uint32_t i=0;i<n;++i) {
        auto& p=f.points[i];p={};
        p.position[0]=xs[i];p.position[1]=y;p.position[2]=z;
        std::memcpy(p.floor,p.position,sizeof(p.floor));p.normal[1]=1;
        p.coordinate=i;p.alpha=1;p.speed=speed;p.flags=uint8_t(i?gp::joined:0);
    }
}
static gp::Frame frame_storage;
static bool near_rgb(const uint8_t* px,float r,float g,float b) {
    return std::fabs(px[0]-r)<=1.6f && std::fabs(px[1]-g)<=1.6f && std::fabs(px[2]-b)<=1.6f;
}
static void blend(float* c,float r,float g,float b,float a) {c[0]=c[0]*(1-a)+r*a;c[1]=c[1]*(1-a)+g*a;c[2]=c[2]*(1-a)+b*a;}
static unsigned path_checks=0;
static void expect(bool ok,const char* what) {++path_checks;require(ok,what);}

int main(int argc,char** argv) {
    const auto foreground=GetForegroundWindow();
    Window window;auto g=dispatch();
    const uint8_t white[4]={255,255,255,255};
    const float casing=0x1E,tint[3]={0x38,0xD4,0x5E}; // 32 units a frame: a full run
    // 1. Behind the game's wall at 20%, in front of it whole; each layer once.
    {
        Scene scene(g,64,64);Bindings bindings(g);
        auto* compositor=gc::create(g,1);expect(compositor!=nullptr,"path compositor create");
        expect(gc::prepare(compositor,64,64,60,white,1,1)==gc::Result::ok,"path prepare");
        ordinary_state(g,scene);const auto clean=pixels(g,scene.fbo,64,64);const auto z=depth(g,scene);
        auto& f=frame_storage;f=gp::Frame{};pixel_camera(f,64,64);f.width=16;f.opacity=1;f.spacing_ms=100;
        float xs[15];for(uint32_t i=0;i<15;++i)xs[i]=4+float(i)*4;
        // Depth 0.5 (y -2 lifted to 0): behind the terrain's left half (0.25),
        // in front of its right half (nothing drawn there: 1).
        line(f,xs,15,32,-GP_PATH_LIFT,32);
        for(auto mode:{gc::Depth::ordinary,gc::Depth::n64_image}) {
            hostile(g,scene,bindings);cgl::State before{};before.capture(g);const auto frame_before=frame_state(g);
            expect(gc::begin_batch(compositor,target(scene,mode),nullptr,0,nullptr,&f)==gc::Result::ok,
                   "the path alone draws a batch");
            equal_state(g,before,bindings);equal_frame(g,frame_before);
            expect(gc::finalize(compositor,77)==gc::Result::ok,"path finalize");
            ordinary_state(g,scene);
            const auto image=pixels(g,gc::display(compositor).framebuffer,64,64);
            auto at=[&](int x,int y){return &image[(size_t(y)*64+x)*4];};
            float shown[3]={20,40,60},hidden[3]={20,40,60},rim[3]={20,40,60};
            blend(shown,casing,casing,casing,GP_PATH_CASING_ALPHA);blend(shown,tint[0],tint[1],tint[2],1);
            blend(hidden,casing,casing,casing,GP_PATH_CASING_ALPHA*GP_PATH_HIDDEN_ALPHA);
            blend(hidden,tint[0],tint[1],tint[2],GP_PATH_HIDDEN_ALPHA);
            blend(rim,casing,casing,casing,GP_PATH_CASING_ALPHA);
            expect(near_rgb(at(48,32),shown[0],shown[1],shown[2]),"in front of the terrain: the speed colour, whole");
            expect(near_rgb(at(16,32),hidden[0],hidden[1],hidden[2]),"behind it: the faint pass at 20%");
            expect(near_rgb(at(48,22),rim[0],rim[1],rim[2]),"the dark casing beside the ribbon, 1.35 times as wide");
            expect(near_rgb(at(48,10),20,40,60) && near_rgb(at(2,32),20,40,60),"nothing past the line");
            expect(pixels(g,scene.fbo,64,64)==clean && depth(g,scene)==z,"the clean picture and the game's depth unchanged");
        }
        // Doubling back over itself at half strength: still one blend a layer.
        float there_and_back[16];for(uint32_t i=0;i<8;++i){there_and_back[i]=36+float(i)*3;there_and_back[15-i]=36+float(i)*3;}
        line(f,there_and_back,16,32,-GP_PATH_LIFT,32);f.opacity=.5f;
        ordinary_state(g,scene);
        expect(gc::begin_batch(compositor,target(scene,gc::Depth::ordinary),nullptr,0,nullptr,&f)==gc::Result::ok,"overlap batch");
        expect(gc::finalize(compositor,77)==gc::Result::ok,"overlap finalize");ordinary_state(g,scene);
        {
            const auto image=pixels(g,gc::display(compositor).framebuffer,64,64);
            float once[3]={20,40,60};blend(once,casing,casing,casing,GP_PATH_CASING_ALPHA*.5f);blend(once,tint[0],tint[1],tint[2],.5f);
            expect(near_rgb(&image[(size_t(32)*64+48)*4],once[0],once[1],once[2]),"overlapping quads blend once per layer");
        }
        // A ribbon on the terrain's own depth (0.25) is drawn, not lost to it.
        line(f,xs,15,32,-50-GP_PATH_LIFT,32);f.opacity=1;ordinary_state(g,scene);
        expect(gc::begin_batch(compositor,target(scene,gc::Depth::ordinary),nullptr,0,nullptr,&f)==gc::Result::ok,"coplanar batch");
        expect(gc::finalize(compositor,77)==gc::Result::ok,"coplanar finalize");ordinary_state(g,scene);
        {
            const auto image=pixels(g,gc::display(compositor).framebuffer,64,64);
            float shown[3]={20,40,60};blend(shown,casing,casing,casing,GP_PATH_CASING_ALPHA);blend(shown,tint[0],tint[1],tint[2],1);
            expect(near_rgb(&image[(size_t(32)*64+16)*4],shown[0],shown[1],shown[2]),"on the floor it lies on: whole");
        }
        // Refusals before any drawing.
        f.count=gp::samples+1;expect(gc::begin_batch(compositor,target(scene,gc::Depth::ordinary),nullptr,0,nullptr,&f)
            ==gc::Result::invalid,"an oversized path is refused");
        f.count=0;expect(gc::begin_batch(compositor,target(scene,gc::Depth::ordinary),nullptr,0,nullptr,&f)
            ==gc::Result::invalid,"no ghost and no path: nothing to draw");
        gc::destroy(compositor);no_error("path witness cleanup");
    }
    // 2. Button icons: one size on the picture at any distance, above the line.
    unsigned icon_near=0,icon_far=0;
    {
        Scene scene(g,320,240);Bindings bindings(g);
        auto* compositor=gc::create(g,1);expect(compositor!=nullptr,"icon compositor create");
        expect(gc::prepare(compositor,320,240,60,white,1,1)==gc::Result::ok,"icon prepare");
        std::vector<uint8_t> atlas(ghost_annotation::atlas_width*ghost_annotation::atlas_height*4,255);
        expect(gc::prepare_annotations(compositor,atlas.data(),ghost_annotation::atlas_width,ghost_annotation::atlas_height)
            ==gc::Result::ok,"icon atlas");
        auto& f=frame_storage;
        for(float scale:{1.0f,4.0f}) {
            f=gp::Frame{};pixel_camera(f,320,240,scale);f.width=10;f.opacity=1;f.spacing_ms=100;
            f.buttons=f.icons=true;
            float xs[2]={240,250};line(f,xs,2,120,-GP_PATH_LIFT-60,0);f.points[0].pressed=0x8000;
            ordinary_state(g,scene);
            expect(gc::begin_batch(compositor,target(scene,gc::Depth::ordinary),nullptr,0,nullptr,&f)==gc::Result::ok,"icon batch");
            expect(gc::finalize(compositor,77)==gc::Result::ok,"icon finalize");ordinary_state(g,scene);
            const auto image=pixels(g,gc::display(compositor).framebuffer,320,240);
            unsigned white_pixels=0;
            for(size_t i=0;i<image.size();i+=4)white_pixels+=image[i]==255 && image[i+1]==255 && image[i+2]==255;
            (scale==1?icon_near:icon_far)=white_pixels;
        }
        expect(icon_near==144 && icon_far==icon_near,"a 12-pixel icon on a 320x240 picture, near and far");
        f.buttons=false;ordinary_state(g,scene);
        expect(gc::begin_batch(compositor,target(scene,gc::Depth::ordinary),nullptr,0,nullptr,&f)==gc::Result::ok,"no-icon batch");
        expect(gc::finalize(compositor,77)==gc::Result::ok,"no-icon finalize");ordinary_state(g,scene);
        {
            const auto image=pixels(g,gc::display(compositor).framebuffer,320,240);
            unsigned white_pixels=0;
            for(size_t i=0;i<image.size();i+=4)white_pixels+=image[i]==255 && image[i+1]==255 && image[i+2]==255;
            expect(white_pixels==0,"button presses are off unless asked for");
        }
        gc::destroy(compositor);no_error("icon witness cleanup");
    }
    // 3. The chevron as drawn from the cartridge's own bytes: 8 pixels across
    // the ribbon, one 8-row cell per 100 ms along it, at 8/3 pixels a frame.
    std::printf("{\"ok\":true,\"checks\":%u,\"icon_pixels\":[%u,%u]",path_checks,icon_near,icon_far);
    if(argc>1) {
        std::ifstream file(argv[1],std::ios::binary);
        std::vector<uint8_t> rom((std::istreambuf_iterator<char>(file)),{});
        ghost_annotation::Assets assets;
        expect(assets.load(rom.data(),uint32_t(rom.size())) && assets.chevron,"the cartridge's chevron is verified");
        Scene scene(g,64,64);
        auto* compositor=gc::create(g,1);expect(compositor!=nullptr,"chevron compositor create");
        expect(gc::prepare(compositor,64,64,60,white,1,1)==gc::Result::ok,"chevron prepare");
        expect(gc::prepare_annotations(compositor,assets.rgba.data(),ghost_annotation::atlas_width,ghost_annotation::atlas_height)
            ==gc::Result::ok,"chevron atlas");
        auto& f=frame_storage;f=gp::Frame{};pixel_camera(f,64,64,1,.8f);f.width=10;f.opacity=1;f.spacing_ms=100;f.chevrons=true;
        // 10 world units across are 8 pixels: one a chevron column.
        float xs[13];for(uint32_t i=0;i<13;++i)xs[i]=(32+float(i)*8/3)/.8f;
        line(f,xs,13,32/.8f,-GP_PATH_LIFT,64); // white: the chevron's own brightness
        ordinary_state(g,scene);
        expect(gc::begin_batch(compositor,target(scene,gc::Depth::ordinary),nullptr,0,nullptr,&f)==gc::Result::ok,"chevron batch");
        expect(gc::finalize(compositor,77)==gc::Result::ok,"chevron finalize");ordinary_state(g,scene);
        const auto image=pixels(g,gc::display(compositor).framebuffer,64,64);
        std::printf(",\"chevron\":[");
        for(int y=28;y<36;++y) {
            std::printf("%s[",y>28?",":"");
            for(int x=32;x<64;++x) {
                const auto* px=&image[(size_t(y)*64+x)*4];
                std::printf("%s[%u,%u,%u]",x>32?",":"",px[0],px[1],px[2]);
            }
            std::printf("]");
        }
        std::printf("]");
        gc::destroy(compositor);no_error("chevron witness cleanup");
    }
    std::printf(",\"final_checks\":%u}\n",path_checks);
    require(GetForegroundWindow()==foreground,"the witness never takes focus");
    return 0;
}
