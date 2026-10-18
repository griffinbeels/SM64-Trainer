/* CPU-only annotation contract/asset/layout witness. Optional real-ROM read
 * exports a private atlas fixture; no emulator, window, device or RAM writes. */
#include "ghost_annotation_mesh.h"
#include <cstdio>
#include <cstring>
#include <utility>
#include <vector>
#include <fstream>
#include <stdexcept>
static unsigned checks=0;
static ghost_occlusion::Region ghost_annotation_region() {return {};}
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
        check(supported('_') && glyph('_')!=glyph('?'),"underscore is a readable glyph, never a question mark");
        check(!supported('/') && !supported(0x30ac) && supported('?'),"unsupported credit is distinguishable from a literal question mark");
        // The underscore reuses the ROM hyphen's exact texels and advance. Its
        // lowered baseline works at both stack and world label scales.
        for(float scale:{0.5f,0.75f})for(bool shadow:{false,true}) {
            Mesh dash,under;
            Layout d{dash,{0,0,0,1},1,1,1},u{under,{0,0,0,1},1,1,1};
            d.character(glyph('-'),0,0,scale,1,shadow);u.character(glyph('_'),0,0,scale,1,shadow);
            check(dash.count==under.count && advance(glyph('_'))==advance(glyph('-')),"underscore keeps canonical stroke size and advance");
            for(unsigned i=0;i<dash.count;++i)
                check(dash.vertices[i].uv[0]==under.vertices[i].uv[0] && dash.vertices[i].uv[1]==under.vertices[i].uv[1]
                      && dash.vertices[i].clip[0]==under.vertices[i].clip[0]
                      && std::fabs(dash.vertices[i].clip[1]-under.vertices[i].clip[1]-4*scale)<1e-6f,
                      "underscore keeps the original atlas coordinates and moves only the baseline");
        }
        // Use the actual stack column calculation: two-digit controls leave
        // 92 HUD pixels; three-digit extremes leave 83. Full 20-character
        // names fit by pixels, or visibly end in an ellipsis when wider.
        for(unsigned digits:{2u,3u}) {
            const float left=std::fmax(stack::buttons_x,stack::digits_start(digits)+stack::digits_width(digits)+3);
            const float limit=stack::column_width-(left-usamune::letter_x)-2;
            check(limit==(digits==2?92.0f:83.0f),"normal and three-digit corner budgets follow the live layout");
            uint8_t chars[64]{};unsigned width=0;
            check(Layout::fitted("SullyLikesBigChungus",stack::scale,limit,chars,width)==20 && width==99,
                  "the longest measured Sheet credit fits both real corner widths");
            check(Layout::fitted("WaterproofTeabag_",stack::scale,limit,chars,width)==17 && chars[16]==underscore,
                  "a real Sheet underscore stays intact at both corner widths");
            check(Layout::fitted("WWWWWWWWWWWWWWWWWWWW",stack::scale,limit,chars,width)==20 && width==160,
                  "twenty wide letters still fit the three-digit corner");
            const unsigned n=Layout::fitted("++++++++++++++++++++",stack::scale,limit,chars,width);
            check(width*stack::scale<=limit && (digits==2?n==20:
                  n<=20 && chars[n-1]==glyph('.') && chars[n-2]==glyph('.') && chars[n-3]==glyph('.')),
                  "wide punctuation is cut by pixels and visibly marked at the narrower corner");
            char legacy[64]{};std::memset(legacy,'W',63);
            const unsigned shortened=Layout::fitted(legacy,stack::scale,limit,chars,width);
            check(shortened<63 && width*stack::scale<=limit && chars[shortened-1]==glyph('.'),
                  "legacy long credits retain a bounded, visibly shortened display");
            check(Layout::fitted("RIVAL 4",stack::scale,limit,chars,width)==7 && width*stack::scale<=limit,
                  "the slot fallback is fully readable at both corner widths");
        }
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
        // A literal (all-raw) MIO0 segment 2 at a cartridge's place: its font
        // LUT naming the first glyph, and its HUD LUT 0-9, A-Z at 512 bytes each.
        const auto segment2=[&](char country,unsigned base,unsigned size,unsigned lut,uint32_t first,unsigned hud,
                                const std::vector<std::pair<unsigned,uint8_t>>& texels) {
            std::vector<uint8_t> decoded(size);put(decoded,lut,first);
            for(unsigned i=0;i<48;++i)put(decoded,hud+4*i,0x02000000u+i*512);
            for(const auto& t:texels)decoded[t.first]=t.second;
            std::vector<uint8_t> rom(8*1024*1024);rom[0x3e]=uint8_t(country);std::memcpy(rom.data()+base,"MIO0",4);
            const unsigned flags_end=16+(size+7)/8;put(rom,base+4,size);put(rom,base+8,flags_end);put(rom,base+12,flags_end);
            std::memset(rom.data()+base+16,255,flags_end-16);std::memcpy(rom.data()+base+flags_end,decoded.data(),size);
            return rom;
        };
        // Asymmetric texel: tile S=2,T=1 appears at screen X=6,Y=13.
        auto rom=segment2('E',0x108a40,100878,0x77e8,0x02005900,0x7700,{{0x5900+(1*16+2)/2,0xf0}});
        Assets assets;check(assets.load(rom.data(),unsigned(rom.size())),"bounded literal MIO0 font fixture decoded");
        check(assets.font==font::us,"a US cartridge fills the cells from the US font");
        check(assets.rgba[(13*atlas_width+6)*4+3]==255 && assets.rgba[(2*atlas_width+1)*4+3]==0,
              "font quad uses canonical rotation rather than an invented font orientation");
        rom[0x3e]='P';check(!assets.load(rom.data(),unsigned(rom.size())),"a cartridge with no known places draws no atlas");
        rom[0x3e]='J';check(!assets.load(rom.data(),unsigned(rom.size())),"US places are never read on a JP cartridge");
        rom[0x3e]='E';rom[0x108a40+12]=0xff;check(!assets.load(rom.data(),unsigned(rom.size())),"corrupt compressed stream fails optional atlas");
        {
            // JP: its own 8x16 one-bit font, upright, at JP's places. Glyph
            // A's top row lights its leftmost pixel; the handakuten its middle.
            constexpr unsigned glyph_a=0x7100+10*16,handakuten=0x7100+0xf1*16,comma=0x7100+0x6f*16,question=0x7100+0xf4*16;
            auto jp=segment2('J',0x1076d0,0x15bae,0x8338,0x02007100,0x8250,
                {{glyph_a,0x80},{handakuten+7,0x20},{comma+12,0x40},{question+3,0x10}});
            const unsigned base=0x1076d0,flags_end=16+(0x15bae+7)/8;
            for(unsigned c=0;c<256;++c) { // every LUT entry names its glyph by its code
                const uint32_t pointer=0x02007100u+c*16;
                for(unsigned i=0;i<4;++i)jp[base+flags_end+0x8338+c*4+i]=uint8_t(pointer>>(24-8*i));
            }
            Assets japanese;check(japanese.load(jp.data(),unsigned(jp.size())),"JP segment 2 decoded at JP's place");
            check(japanese.font==font::jp,"a JP cartridge fills the cells from JP's font");
            const auto lit=[&](uint8_t c,unsigned x,unsigned y) {
                return japanese.rgba[(((c/32)*16+y)*atlas_width+(c%32)*8+x)*4+3]==255;
            };
            check(lit(glyph('A'),0,0) && !lit(glyph('A'),7,0),"JP glyphs are upright one-bit tiles");
            check(lit(glyph('a'),0,0),"lowercase takes JP's own capital");
            check(lit(glyph('.'),0,12) && !lit(glyph('.'),2,7),"the period is JP's handakuten moved 2 left, 5 down");
            check(lit(glyph(':'),0,12) && lit(glyph(':'),0,6),"the colon is that circle twice");
            check(lit(glyph('\''),1,4) && !lit(glyph('\''),1,12),"the apostrophe is JP's comma raised to the cap line");
            for(uint32_t c:{uint32_t('^'),uint32_t('|'),uint32_t('<'),uint32_t('>'),uint32_t('&')})
                check(lit(glyph(c),3,3),"a mark JP cannot draw shows JP's question mark");
            check(!lit(glyph('+'),3,3),"+ keeps code 0xE4's own glyph, as on US");
            check(advance(glyph('W'),font::jp)==10 && advance(glyph('i'),font::jp)==10 && advance(glyph(' '),font::jp)==5
                  && advance(glyph('.'),font::jp)==5,"JP glyphs advance by JP's own monospaced rule");
            uint8_t chars[64]{};unsigned width=0;
            check(Layout::fitted("Mario",1,1000,chars,width,font::jp)==5 && width==50,"a JP name is 10 pixels a glyph");
            check(Layout::fitted("Mario",1,1000,chars,width)==5 && width<50,"a US name keeps US widths");
            jp[0x3e]='E';check(!japanese.load(jp.data(),unsigned(jp.size())),"JP places are never read on a US cartridge");
        }
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
        // His ask, 2026-10-02: the name "directly over mario's head". It rides the
        // top of the drawn body (a crouch brings it down), never above his cap.
        {
            Payload q;q.position[1]=100;
            check(name_height(q)==GP_ANNOTATION_HEIGHT,"with no body measured the name keeps its standing height");
            q.top=160;check(std::fabs(name_height(q)-(60+GP_ANNOTATION_GAP))<1e-4f,"a crouched body lowers the name");
            q.top=500;check(std::fabs(name_height(q)-GP_ANNOTATION_HEIGHT)<1e-4f,"raised arms never lift it past the cap");
        }
        // SM64's dialog boxes behind the name: the NPC's black box with white
        // text, the sign's white box with black text; no box keeps the shadow.
        {
            Payload q;std::strcpy(q.label,"YOUR PB");q.names=true;q.background_opacity=0.6f;
            for(uint8_t background:{uint8_t(GP_NAME_BLACK),uint8_t(GP_NAME_WHITE)}) {
                q.background=background;build(q,vp,0,1,1,ghost_annotation_region(),640,480,mesh);
                const float box=background==GP_NAME_WHITE?1.0f:0.0f,ink=1.0f-box;
                check(mesh.count==6+6*6 && mesh.vertices[0].rgba[0]==box && mesh.vertices[0].rgba[3]==0.6f,
                      "the box is one quad in its colour and opacity, the glyphs have no shadow on it");
                check(mesh.vertices[0].uv[0]==float(atlas_width-1)/atlas_width,"the box samples the white atlas cell");
                check(mesh.vertices[6].rgba[0]==ink,"the text takes the dialog's own colour on its box");
            }
            q.background_opacity=0;build(q,vp,0,1,1,ghost_annotation_region(),640,480,mesh);
            check(mesh.count==2*6*6,"no box: white glyphs with their drop shadow");
        }
        // The stack above the game's input display: one row per ghost in slot
        // order, in screen space, drawn even when the ghost itself is off screen.
        {
            Payload q;std::strcpy(q.label,"YOUR PB");q.hud=q.artwork=q.known=true;q.pad={0,0,0x8000,0};q.hud_count=3;
            float rows[3]{};
            for(uint8_t rank=0;rank<3;++rank) {
                q.hud_rank=rank;build(q,vp,0,1,1,ghost_annotation_region(),640,480,mesh);
                check(mesh.count>0 && mesh.vertices[0].clip[3]==1,"a stack row is drawn in screen space");
                rows[rank]=mesh.vertices[mesh.count-1].clip[1];
            }
            check(rows[0]<rows[1] && rows[1]<rows[2],"later ghosts' rows climb above earlier ones");
            check(rows[0]>-0.6f && rows[2]<0.2f,"the stack starts just above the game's own display");
            q.hud_rank=0;q.position[2]=-1e6f;float behind[16]{};std::memcpy(behind,vp,sizeof(behind));behind[11]=1;behind[15]=0;
            build(q,behind,0,1,1,ghost_annotation_region(),640,480,mesh);
            check(mesh.count>0,"a ghost behind the camera still shows its row");
            q.hud=false;build(q,behind,0,1,1,ghost_annotation_region(),640,480,mesh);
            check(mesh.count==0,"the stack is its own switch");
            // His ask, 2026-10-02: values "directly in line with the player's", buttons
            // "aligned with where the player's button inputs actually show up", the name
            // over the buttons. HUD x to NDC: x/160 - 1.
            auto ndc=[](float x) {return x/160.0f-1.0f;};
            q.hud=true;q.values=true;q.hud_rank=0;q.pad={12,83,0xc000,0};q.position[2]=0;
            build(q,vp,0,1,1,ghost_annotation_region(),640,480,mesh);
            // U | 8 3 / R | 1 2 / A B / the name's shadowed glyphs. His round d: "if we draw
            // a line through the middle of the ghost inputs numbers" it runs through his.
            const float his_middle=usamune::digit_x+(usamune::digit_advance+16)*0.5f; // his two digits
            check(mesh.count>48 && std::fabs((mesh.vertices[6].clip[0]+mesh.vertices[13].clip[0])*0.5f-ndc(his_middle))<1e-5f,
                  "the ghost's two digits are centred on the player's two digits");
            check(std::fabs(mesh.vertices[1].clip[0]-mesh.vertices[6].clip[0]-ndc(1)+ndc(0))<1e-5f,
                  "the ghost's letter tucks against its digits in Usamune's own proportion");
            // "the buttons + the name need to be more compact & shifted to the left":
            // they pack at half pitch in a column just past a two-digit ghost number.
            const float column=his_middle+(usamune::digit_advance+16)*0.25f+3;
            check(std::fabs(mesh.vertices[36].clip[0]-ndc(column))<1e-5f && column<usamune::button_x
                  && std::fabs(mesh.vertices[42].clip[0]-ndc(column+usamune::button_advance*0.5f))<1e-5f,
                  "the ghost's buttons pack at half pitch just past its numbers, left of the player's");
            check(std::fabs(mesh.vertices[54].clip[0]-ndc(column))<1e-5f
                  && mesh.vertices[54].clip[1]>mesh.vertices[38].clip[1],
                  "the name starts over the buttons and sits above them");
            // "the top set of 00 should be center aligned with the below set of 00".
            q.pad={0,0,0,0};build(q,vp,0,1,1,ghost_annotation_region(),640,480,mesh);
            const float his_single=usamune::digit_x+8; // his lone digit's middle
            check(std::fabs((mesh.vertices[0].clip[0]+mesh.vertices[1].clip[0])*0.5f-ndc(his_single))<1e-5f
                  && std::fabs((mesh.vertices[6].clip[0]+mesh.vertices[7].clip[0])*0.5f-ndc(his_single))<1e-5f,
                  "a ghost 0 over 0 is centred on the player's 0 over 0");
            q.pad={12,83,0xc000,0};
            q.values=false;q.pad={0,0,0,0};build(q,vp,0,1,1,ghost_annotation_region(),640,480,mesh);
            check(std::fabs((mesh.vertices[0].clip[0]+mesh.vertices[1].clip[0])*0.5f-ndc(usamune::base_x+16))<1e-5f,
                  "the ghost's stick box is centred on the player's");
            q.hud_count=9;q.hud_rank=7;build(q,vp,0,1,1,ghost_annotation_region(),640,480,mesh);
            check(mesh.count==0,"the corner shows at most seven ghosts");
            q.hud_rank=6;build(q,vp,0,1,1,ghost_annotation_region(),640,480,mesh);
            check(mesh.count>0 && std::fabs((mesh.vertices[0].clip[0]+mesh.vertices[1].clip[0])*0.5f
                                            -ndc(usamune::base_x+16))<1e-5f,"seven rows fit one column");
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
            check(assets.chevron,"the cartridge's own chevron texture loads");
            check(assets.font==(real[0x3e]=='J'?font::jp:font::us),"the cartridge's own dialogue font fills the cells");
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
        std::printf("{\"ok\":true,\"checks\":%u,\"rom\":%s,\"capacity\":%u,\"maximum\":%u,\"label_characters\":[",
                    checks,cartridge?"true":"false",max_vertices,maximum);
        bool first=true;for(uint32_t c=32;c<=126;++c)if(supported(c)) {
            std::printf("%s%u",first?"":",",c);first=false;
        }
        std::puts("]}");
        return 0;
    }catch(const std::exception& e) {std::fprintf(stderr,"annotation witness failed: %s\n",e.what());return 1;}
}
