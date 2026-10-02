/* CPU-only annotation contract/asset/layout witness. Optional real-ROM read
 * exports a private atlas fixture; no emulator, window, device or RAM writes. */
#include "ghost_annotation_mesh.h"
#include <cstdio>
#include <cstring>
#include <vector>
#include <fstream>
#include <stdexcept>
static unsigned checks=0;
static void check(bool value,const char* why) {++checks;if(!value)throw std::runtime_error(why);}
static void put(std::vector<uint8_t>& data,unsigned at,uint32_t value) {
    for(unsigned i=0;i<4;++i)data[at+i]=uint8_t(value>>(24-8*i));
}
int main(int argc,char** argv) {
    try {
        using namespace ghost_annotation;
        char label[64]{};std::strcpy(label,"YOUR PB");check(valid_label(label),"plain terminated name");
        label[9]='x';check(!valid_label(label),"nonzero terminated tail refused");label[9]=0;
        std::memset(label,'x',64);check(!valid_label(label),"unterminated label refused");
        std::memset(label,0,64);std::memcpy(label,"Zo\xc3\xab",4);check(valid_label(label),"valid UTF-8 author accepted");
        check(glyph(0xeb)==glyph('?'),"unsupported Unicode gets the actual dialogue question-mark glyph");
        check(glyph('/')==glyph('?'),"US dialogue's slash spacing control cannot silently hide author text");
        label[0]=char(0xc0);label[1]=char(0xaf);label[2]=0;label[3]=0;
        check(!valid_label(label),"overlong UTF-8 refused");
        std::memset(label,0,64);label[0]='\n';check(!valid_label(label),"control injection refused");
        gc_sample sample{};check(valid_pad(sample),"unknown optional pad is zero");
        sample.pad.buttons=0x8000;check(!valid_pad(sample),"unknown pad cannot carry buttons");
        sample.valid_fields=GC_VALID_PAD;sample.pad.stick_x=-128;sample.pad.stick_y=127;sample.pad.buttons=0xff3f;
        check(valid_pad(sample),"all canonical held bits and signed extremes accepted");
        sample.pad.stick_x=128;check(!valid_pad(sample),"out-of-range signed raw axis refused");
        sample.pad.stick_x=0;sample.pad.buttons=0x40;check(!valid_pad(sample),"reserved button bit refused");
        sample.pad.buttons=0;sample.pad.reserved=1;check(!valid_pad(sample),"reserved field refused");
        constexpr unsigned size=100878,base=0x108a40,lut=0x77e8;
        std::vector<uint8_t> decoded(size);put(decoded,lut,0x02005900);
        // Asymmetric texel: tile S=2,T=1 appears at screen X=6,Y=13.
        decoded[0x5900+(1*16+2)/2]=0xf0;
        std::vector<uint8_t> rom(8*1024*1024);std::memcpy(rom.data()+base,"MIO0",4);
        const unsigned flags_end=16+(size+7)/8;put(rom,base+4,size);put(rom,base+8,flags_end);put(rom,base+12,flags_end);
        std::memset(rom.data()+base+16,255,flags_end-16);std::memcpy(rom.data()+base+flags_end,decoded.data(),size);
        Assets assets;check(assets.load(rom.data(),unsigned(rom.size())),"bounded literal MIO0 font fixture decoded");
        check(assets.rgba[(13*atlas_width+6)*4+3]==255 && assets.rgba[(2*atlas_width+1)*4+3]==0,
              "font quad uses canonical rotation rather than an invented font orientation");
        rom[base+12]=0xff;check(!assets.load(rom.data(),unsigned(rom.size())),"corrupt compressed stream fails optional atlas");
        Payload p;std::strcpy(p.label,"YOUR PB");p.names=true;
        float vp[16]{};vp[0]=vp[5]=vp[10]=1;vp[15]=500;
        Mesh mesh;ghost_occlusion::Region region{};
        build(p,vp,0,0.6f,0.5f,region,640,480,mesh);
        check(mesh.count>0 && mesh.count<=max_vertices,"name layout is bounded");
        check(mesh.vertices[6].rgba[3]==0.3f,"name receives body opacity including vanish cap alpha");
        const float depth=mesh.vertices[0].clip[2],w=mesh.vertices[0].clip[3];
        for(unsigned i=0;i<mesh.count;++i)check(mesh.vertices[i].clip[2]==depth && mesh.vertices[i].clip[3]==w,
            "every glyph retains the body anchor's terrain depth and perspective divisor");
        // Orientation: the live renderer always flips (flip_y=1). Glyph tops
        // must still face screen-up, and the input display sits above the name.
        p.inputs=p.artwork=p.known=true;
        for(uint32_t flip=0;flip<2;++flip) {
            build(p,vp,flip,1,1,region,640,480,mesh);
            const float sign=flip?-1.0f:1.0f; // screen-up in this pass's clip y
            const auto& bottom=mesh.vertices[6];const auto& top=mesh.vertices[6+2];
            check(sign*(top.clip[1]-bottom.clip[1])>0 && top.uv[1]<bottom.uv[1],
                  "a name glyph's atlas top is drawn screen-up in both renderer orientations");
            const auto& name_first=mesh.vertices[0];const auto& input_last=mesh.vertices[mesh.count-1];
            check(sign*(input_last.clip[1]-name_first.clip[1])>0,"the input display sits above the name");
        }
        p.artwork=p.known=false;build(p,vp,0,1,1,region,640,480,mesh);
        check(mesh.count==unsigned(2*6*6),"no supported artwork draws the name alone, never a guessed pad");
        p.artwork=true;build(p,vp,0,1,1,region,640,480,mesh);const auto unknown=mesh.count;
        p.known=true;build(p,vp,0,1,1,region,640,480,mesh);
        check(mesh.count!=unknown,"unknown pad has a readable indication distinct from neutral");
        p.artwork=false;
        // World-sized: twice as far is half the size on screen, and a ghost
        // beside the camera stops at the largest screen size.
        {
            Payload far=p;far.inputs=false;far.position[1]=-GP_ANNOTATION_HEIGHT;float persp[16]{};persp[0]=persp[5]=persp[10]=2.4f;persp[11]=1;
            auto width=[&](float z) {
                far.position[2]=z;build(far,persp,1,1,1,ghost_occlusion::Region{},640,480,mesh);
                float lo=1e9f,hi=-1e9f;for(unsigned i=0;i<mesh.count;++i) {
                    const float x=mesh.vertices[i].clip[0]/mesh.vertices[i].clip[3];lo=std::fmin(lo,x);hi=std::fmax(hi,x);}
                return mesh.count?hi-lo:0.0f;
            };
            const float near=width(800),twice=width(1600);
            check(near>0 && std::fabs(near/twice-2)<0.01f,"a ghost twice as far draws its name half as wide");
            const float glyph_ndc=(mesh.vertices[1].clip[0]-mesh.vertices[0].clip[0])/mesh.vertices[0].clip[3];
            check(glyph_ndc>0,"glyph quads have extent");
            width(20);const float cap=2.0f*GP_ANNOTATION_MAX_SCREEN/240*8*0.75f;
            const float close=(mesh.vertices[1].clip[0]-mesh.vertices[0].clip[0])/mesh.vertices[0].clip[3];
            check(close<=cap*1.001f,"a ghost beside the camera never exceeds the largest name size");
        }
        region.enabled=1;region.overlap=0.15f;vp[15]=10;region.minimum=0.15f;
        build(p,vp,0,1,1,region,640,480,mesh);
        // Move projection Y back into view without changing depth.
        vp[5]=0.001f;build(p,vp,0,1,1,region,640,480,mesh);
        check(mesh.count && mesh.vertices[6].rgba[3]==0.15f,"terminal names preserve the death visibility floor");
        region.minimum=0;build(p,vp,0,1,1,region,640,480,mesh);check(!mesh.count,"ordinary near-camera name shares body hiding");
        check(stick_offset(-128)==-14 && stick_offset(127)==14 && stick_offset(-4)==0 && stick_offset(69)==13,
              "Usamune signed clamp and division preserve extreme, near-neutral and asymmetric stick positions");
        vp[15]=500;vp[5]=1;region={};p.artwork=p.known=true;p.names=false;p.values=false;p.pad={0,0,0x8000,0};
        build(p,vp,0,1,1,region,640,480,mesh);
        check(mesh.count==18,"stick base, knob and held A use original atlas quads");
        const float neutral_x=mesh.vertices[6].clip[0],neutral_y=mesh.vertices[6].clip[1];
        p.pad={127,-128,0x4000,0};build(p,vp,0,1,1,region,640,480,mesh);
        check(mesh.vertices[6].clip[0]>neutral_x && mesh.vertices[6].clip[1]<neutral_y,
              "right/down raw input moves the exact knob right/down rather than swapping axes");
        check(mesh.vertices[12].uv[0]==16.0f/atlas_width,"held B resolves to its own canonical table cell");
        // His ask, 2026-10-02: the stick and buttons together are centred on the name.
        for(uint16_t buttons:{uint16_t(0),uint16_t(0xc004)}) {
            p.pad={70,70,buttons,0};build(p,vp,0,1,1,region,640,480,mesh);
            const unsigned last=buttons?(2+3-1)*6+1:1; // the last drawn button's right edge, or the base's
            const float centre=(mesh.vertices[0].clip[0]+mesh.vertices[last].clip[0])*0.5f;
            check(std::fabs(centre)<1e-4f,"the stick base through the last held button is centred on the name");
        }
        // His ask, 2026-10-02: "way too much of a gap between the stick and the
        // buttons". The first button follows the ring's farthest reach closely.
        {
            p.pad={127,0,0x8000,0};build(p,vp,0,1,1,region,640,480,mesh);
            const float hud_pixel=(mesh.vertices[1].clip[0]-mesh.vertices[0].clip[0])/32; // the 32-wide base
            const float base_right=mesh.vertices[1].clip[0],ring_right=mesh.vertices[7].clip[0],button_left=mesh.vertices[12].clip[0];
            check(button_left>=ring_right,"a ring at full right tilt never runs under the first button");
            check(button_left-base_right<=10*hud_pixel,"the first button sits close beside the stick base");
        }
        p.names=true;p.pad={-128,127,0xff3f,0};p.values=true;std::memset(p.label,'W',63);p.label[63]=0;
        unsigned maximum=0;for(unsigned i=0;i<16;++i) {
            p.position[0]=float(i);build(p,vp,0,1,1,region,640,480,mesh);
            check(mesh.count && mesh.count<=max_vertices,"sixteen worst-case annotation payloads fit their disjoint GPU ranges");
            if(mesh.count>maximum)maximum=mesh.count;
        }
        bool cartridge=false;
        if(argc>=2) {
            std::ifstream file(argv[1],std::ios::binary);std::vector<uint8_t> real((std::istreambuf_iterator<char>(file)),{});
            check(real.size()==8*1024*1024 && assets.load(real.data(),unsigned(real.size())),"canonical real-ROM atlas decoded");
            if(!std::memcmp(real.data()+0x20,"SM64 USAMUNE",12))check(assets.usamune,"actual Usamune table resolves every canonical icon");
            if(argc>=3) {std::ofstream out(argv[2],std::ios::binary);out.write(reinterpret_cast<const char*>(assets.rgba.data()),assets.rgba.size());}
            if(argc>=4) {
                std::ofstream out(argv[3],std::ios::binary);
                Payload preview;std::strcpy(preview.label,"YOUR PB");preview.names=preview.inputs=preview.known=true;
                preview.artwork=assets.usamune;preview.pad={-128,70,0xc004,0};preview.position[1]=-GP_ANNOTATION_HEIGHT;
                float near[16]{};near[0]=near[5]=near[10]=1;near[15]=60; // close enough to reach the size cap
                for(unsigned side=0;side<3;++side) {
                    preview.position[0]=side==0?-30.0f:side==1?0.0f:30.0f;preview.values=side==1;preview.known=side!=2;if(side==2)preview.pad={};
                    build(preview,near,0,0.85f,1,region,640,480,mesh);
                    out.write(reinterpret_cast<const char*>(&mesh.count),sizeof(mesh.count));
                    out.write(reinterpret_cast<const char*>(mesh.vertices),mesh.count*sizeof(Vertex));
                }
            }
            cartridge=true;
        }
        std::printf("{\"ok\":true,\"checks\":%u,\"rom\":%s,\"capacity\":%u,\"maximum\":%u}\n",checks,cartridge?"true":"false",max_vertices,maximum);
        return 0;
    }catch(const std::exception& e) {std::fprintf(stderr,"annotation witness failed: %s\n",e.what());return 1;}
}
