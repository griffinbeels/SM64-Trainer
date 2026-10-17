#include "ghost_annotation_assets.h"
#include <vector>
#include <cstring>

namespace ghost_annotation {
namespace {
uint32_t be32(const uint8_t* p) {return (uint32_t(p[0])<<24)|(uint32_t(p[1])<<16)|(uint32_t(p[2])<<8)|p[3];}
bool decode(const uint8_t* p,uint32_t span,std::vector<uint8_t>& out) {
    if(span<16 || std::memcmp(p,"MIO0",4))return false;
    const uint32_t size=be32(p+4),flags_end=be32(p+8),raw_start=be32(p+12);
    if(!size || size>1024*1024 || flags_end<16 || raw_start<flags_end || raw_start>span)return false;
    out.resize(size);uint32_t written=0,flags=16,packed=flags_end,raw=raw_start;unsigned bit=0;
    while(written<size) {
        if(flags>=flags_end)return false;
        if(p[flags]&(0x80>>bit)) {if(raw>=span)return false;out[written++]=p[raw++];}
        else {
            if(packed+2>raw_start)return false;
            const unsigned length=(p[packed]>>4)+3,distance=((p[packed]&15)<<8)+p[packed+1]+1;packed+=2;
            if(distance>written || length>size-written)return false;
            for(unsigned j=0;j<length;++j) {out[written]=out[written-distance];++written;}
        }
        if(++bit==8) {bit=0;++flags;}
    }
    return true;
}
// Where each practice cartridge keeps what the annotations draw, by its
// header's country byte. Every JP place was found by decoding
// SM64_USAMUNE_v1.93j.z64 (2026-10-17): its segment 2 (MIO0) holds JP's own
// dialogue font, an 8x16 one-bit font with no lowercase Latin, behind its
// own font LUT; its HUD numerals and letters are US's 512-byte tiles under
// its own HUD LUT; the Lethal Lava Land bank decodes to US's bytes; and
// Usamune JP's input display art and button table sit 0x6AA18 earlier than
// US's, under a RAM bias 0x6AAE0 larger (its payload copies cartridge
// 0x761BE0 to RAM 0x80400000, US's 0x7CC6C0). Each load proves its place
// (LUT pointers, table pointers, the chevron's hash) and refuses otherwise.
struct Cartridge {
    uint8_t country,font;
    uint32_t segment2,font_lut,first_glyph,hud_lut;
    int32_t usamune_shift;uint32_t usamune_bias;
    uint32_t chevron_bank;
};
constexpr Cartridge cartridges[]={
    {'E',font::us,0x108a40,0x77e8,0x02005900,0x7700,0,0x7fc33940,0x31e1d0},
    {'J',font::jp,0x1076d0,0x8338,0x02007100,0x8250,-0x6aa18,0x7fc9e420,0x31c770},
};
const Cartridge* cartridge(const uint8_t* rom,uint32_t bytes) {
    if(!rom || bytes<0x40)return nullptr;
    for(const auto& c:cartridges)if(rom[0x3e]==c.country)return &c;
    return nullptr;
}
// The chevron cell, only from the exact texture bytes; anything else draws none.
bool load_chevron(const uint8_t* rom,uint32_t bytes,uint32_t at,uint8_t* atlas) {
    if(bytes<at+16)return false;
    const uint32_t span=bytes-at<0x20000u?bytes-at:0x20000u;
    std::vector<uint8_t> bank;
    if(!decode(rom+at,span,bank) || bank.size()!=chevron_texture::decoded)return false;
    const uint8_t* texture=bank.data()+chevron_texture::texture;
    uint32_t hash=0x811c9dc5u;
    for(unsigned i=0;i<32*32*2;++i)hash=(hash^texture[i])*0x01000193u;
    if(hash!=chevron_texture::texture_fnv1a)return false;
    for(unsigned y=0;y<chevron_texture::size;++y)for(unsigned x=0;x<chevron_texture::size;++x) {
        const unsigned at_pixel=((chevron_texture::first_row+y)*32+chevron_texture::first_column+x)*2;
        const unsigned color=(unsigned(texture[at_pixel])<<8)|texture[at_pixel+1];
        const unsigned r=((color>>11)&31)*255/31,g=((color>>6)&31)*255/31,b=((color>>1)&31)*255/31;
        unsigned bright=r>g?r:g;bright=bright>b?bright:b;
        if(bright<chevron_texture::floor)bright=chevron_texture::floor;
        auto* pixel=atlas+((cell::chevron_y+y)*atlas_width+cell::chevron_x+x)*4;
        pixel[0]=pixel[1]=pixel[2]=uint8_t(bright);pixel[3]=((color>>1)&31)>3?255:0;
    }
    return true;
}
// US: the canonical 16x8 IA4 tile, rotated by vertex_ia8_char's UVs: screen
// right decrements tile T; screen up increments tile S.
void us_glyph(const uint8_t* src,uint8_t c,uint8_t* atlas) {
    for(unsigned y=0;y<16;++y)for(unsigned x=0;x<8;++x) {
        const unsigned at=(7-x)*16+y;const auto v=(src[at/2]>>(at%2?0:4))&15;
        auto* pixel=atlas+(((c/32)*16+(15-y))*atlas_width+(c%32)*8+x)*4;
        pixel[0]=pixel[1]=pixel[2]=uint8_t((v>>1)*255/7);pixel[3]=(v&1)?255:0;
    }
}
// JP: an 8x16 one-bit tile, row 0 at the top, unrotated (its own
// vertex_ia8_char; alloc_ia8_text_from_i1 lights a set bit, MSB first).
// Drawn into US code c's cell, moved by (dx, dy) pixels and clipped to it.
void jp_glyph(const uint8_t* src,uint8_t c,uint8_t* atlas,int dx=0,int dy=0) {
    for(int y=0;y<16;++y)for(int x=0;x<8;++x) {
        if(!(src[y]&(0x80>>x)))continue;
        const int tx=x+dx,ty=y+dy;
        if(tx<0 || tx>7 || ty<0 || ty>15)continue;
        auto* pixel=atlas+(((c/32)*16+unsigned(ty))*atlas_width+(c%32)*8+unsigned(tx))*4;
        pixel[0]=pixel[1]=pixel[2]=pixel[3]=255;
    }
}
// What a JP atlas draws in the cell of each US code glyph() uses. JP's font
// has digits, capitals, kana and a few marks, but no lowercase Latin and no
// apostrophe, period or colon. So, keeping every name readable: lowercase
// takes JP's own capital; the period is JP's own (print_generic_string draws
// its full stop as the handakuten circle moved 2 left and 5 down); the colon is that
// circle twice; the apostrophe is JP's comma stroke raised to the cap line.
// US's C-button arrows (^ | < >) and & have no JP glyph and draw JP's "?".
// (+ draws code 0xE4, a left-right arrow in both fonts, as on US.)
enum : int {none=-1,period=-2,colon=-3,apostrophe=-4};
int jp_source(uint8_t c) {
    if(c<=0x23)return c;                     // 0-9, A-Z: the same codes
    if(c<=0x3d)return c-0x1a;                // a-z: JP's A-Z
    switch(c) {
    case 0x3e:return apostrophe;case 0x3f:return period;case 0xe6:return colon;
    case 0x50:case 0x51:case 0x52:case 0x53:case 0xe5:return 0xf4;
    case 0x6f:case 0x9f:case 0xe1:case 0xe3:case 0xe4:case 0xf2:case 0xf3:case 0xf4:case 0xf7:case 0xf9:return c;
    default:return none;
    }
}
constexpr uint8_t jp_comma=0x6f,jp_handakuten=0xf1;
}
bool Assets::load(const uint8_t* rom,uint32_t bytes) {
    ready=usamune=chevron=false;font=font::us;rgba.fill(0);
    const Cartridge* cart=cartridge(rom,bytes);
    constexpr uint32_t span=128*1024;
    if(!cart || bytes<cart->segment2+span)return false;
    std::vector<uint8_t> data;
    if(!decode(rom+cart->segment2,span,data) || data.size()<cart->font_lut+256*4
            || be32(data.data()+cart->font_lut)!=cart->first_glyph || data.size()<cart->hud_lut+48*4
            || be32(data.data()+cart->hud_lut)!=0x02000000 || be32(data.data()+cart->hud_lut+4)!=0x02000200)
        return false;
    const auto lut_glyph=[&](uint32_t lut,unsigned c,uint32_t size)->const uint8_t* {
        const uint32_t pointer=be32(data.data()+lut+c*4);
        if((pointer>>24)!=2 || (pointer&0xffffff)>data.size()-size)return nullptr;
        return data.data()+(pointer&0xffffff);
    };
    if(cart->font==font::us) {
        for(unsigned c=0;c<256;++c)if(const auto* src=lut_glyph(cart->font_lut,c,64))us_glyph(src,uint8_t(c),rgba.data());
    } else {
        for(unsigned c=0;c<256;++c) {
            const int from=jp_source(uint8_t(c));
            if(from==none)continue;
            const auto* src=lut_glyph(cart->font_lut,from>=0?unsigned(from):from==apostrophe?jp_comma:jp_handakuten,16);
            if(!src)continue;
            if(from>=0)jp_glyph(src,uint8_t(c),rgba.data());
            else if(from==apostrophe)jp_glyph(src,uint8_t(c),rgba.data(),0,-8);
            else {
                jp_glyph(src,uint8_t(c),rgba.data(),-2,5);
                if(from==colon)jp_glyph(src,uint8_t(c),rgba.data(),-2,-1);
            }
        }
    }
    font=cart->font;
    auto blit=[&](const uint8_t* src,unsigned w,unsigned h,unsigned left,unsigned top) {
        for(unsigned y=0;y<h;++y)for(unsigned x=0;x<w;++x) {
            const unsigned color=(unsigned(src[(y*w+x)*2])<<8)|src[(y*w+x)*2+1];
            auto* pixel=rgba.data()+((top+y)*atlas_width+left+x)*4;
            pixel[0]=uint8_t(((color>>11)&31)*255/31);pixel[1]=uint8_t(((color>>6)&31)*255/31);
            pixel[2]=uint8_t(((color>>1)&31)*255/31);pixel[3]=(color&1)?255:0;
        }
    };
    // Canonical 16x16 HUD numerals and letters from segment2 (also unchanged
    // in Usamune, whose Values display prints with them), through the HUD LUT
    // (indexed like the dialogue codes: 0-9, then A=10): US's leaves out J,
    // Q, V, X and Z, JP's does not, and both hold the same tiles.
    for(unsigned i=0;i<10;++i) {
        const auto* src=lut_glyph(cart->hud_lut,i,512);if(!src)return false;
        blit(src,16,16,i*16,unsigned(cell::digit_y));
    }
    constexpr unsigned letters[]={30,13,21,27}; // cell::U, D, L, R
    for(unsigned j=0;j<4;++j) {
        const auto* src=lut_glyph(cart->hud_lut,letters[j],512);if(!src)return false;
        blit(src,16,16,unsigned(cell::letter_x)+j*16,unsigned(cell::letter_y));
    }
    // Practice ROM bootstrap copies cartridge0x7cc6c0 (JP 0x761be0) to
    // RAM00400000. Table ptr804195b8 resolves to cartridge7e5c78, and its icon
    // pointers resolve through that SAME bias. Read/prove the table rather
    // than guess an orange arrow's meaning from its pixel shape (7470 is
    // C-DOWN). The knob table follows it: the 8x8 dot, then the 16x16 ring.
    const auto at=[&](uint32_t us)->uint32_t {return uint32_t(int64_t(us)+cart->usamune_shift);};
    const uint32_t table=at(0x7e5c78),knobs=table+9*12,ring=at(0x7e77f0),base_tile_at=at(0x7e75f0),bias=cart->usamune_bias;
    usamune=bytes>=at(0x7e7a70);
    for(unsigned i=0;i<9 && usamune;++i)usamune=be32(rom+table+i*12)==(uint32_t(button_masks[i])<<16)
        && be32(rom+table+i*12+4)==at(button_rom[i])+bias && be32(rom+table+i*12+8)==at(button_big_rom[i])+bias;
    usamune=usamune && be32(rom+knobs)==at(0x7e79f0)+bias && be32(rom+knobs+4)==ring+bias;
    if(usamune) {
        for(unsigned i=0;i<9;++i)blit(rom+at(button_big_rom[i]),16,16,i*16,unsigned(cell::button_y));
        blit(rom+ring,16,16,unsigned(cell::ring_x),unsigned(cell::ring_y));
        // 16x16 base, RDP mirror in both axes/mask4 to a 32x32 rectangle.
        // Expand the actual tile here so GL never invents a stick surround.
        const uint8_t* base_tile=rom+base_tile_at;
        for(unsigned y=0;y<32;++y)for(unsigned x=0;x<32;++x) {
            const unsigned tx=x<16?x:31-x,ty=y<16?y:31-y;
            blit(base_tile+(ty*16+tx)*2,1,1,unsigned(cell::base_x)+x,unsigned(cell::base_y)+y);
        }
    }
    // A one-pixel white cell supports dialog cards without invented artwork.
    auto* white=rgba.data()+(atlas_width*atlas_height-1)*4;
    white[0]=white[1]=white[2]=white[3]=255;
    chevron=load_chevron(rom,bytes,cart->chevron_bank,rgba.data());
    ready=true;return true;
}
}
