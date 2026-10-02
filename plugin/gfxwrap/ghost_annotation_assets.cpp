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
}
bool Assets::load(const uint8_t* rom,uint32_t bytes) {
    ready=usamune=false;rgba.fill(0);
    constexpr uint32_t base=0x108a40,span=128*1024,lut=0x77e8;
    if(!rom || bytes<base+span)return false;
    std::vector<uint8_t> data;
    if(!decode(rom+base,span,data) || data.size()<lut+256*4 || be32(data.data()+lut)!=0x02005900)return false;
    for(unsigned c=0;c<256;++c) {
        const uint32_t pointer=be32(data.data()+lut+c*4);
        if((pointer>>24)!=2 || (pointer&0xffffff)>data.size()-64)continue;
        const auto* src=data.data()+(pointer&0xffffff);
        // The canonical 16x8 IA4 tile is rotated by vertex_ia8_char's UVs:
        // screen right decrements tile T; screen up increments tile S.
        for(unsigned y=0;y<16;++y)for(unsigned x=0;x<8;++x) {
            const unsigned at=(7-x)*16+y;const auto v=(src[at/2]>>(at%2?0:4))&15;
            auto* pixel=rgba.data()+(((c/32)*16+(15-y))*atlas_width+(c%32)*8+x)*4;
            pixel[0]=pixel[1]=pixel[2]=uint8_t((v>>1)*255/7);pixel[3]=(v&1)?255:0;
        }
    }
    auto blit=[&](const uint8_t* src,unsigned w,unsigned h,unsigned left,unsigned top) {
        for(unsigned y=0;y<h;++y)for(unsigned x=0;x<w;++x) {
            const unsigned color=(unsigned(src[(y*w+x)*2])<<8)|src[(y*w+x)*2+1];
            auto* pixel=rgba.data()+((top+y)*atlas_width+left+x)*4;
            pixel[0]=uint8_t(((color>>11)&31)*255/31);pixel[1]=uint8_t(((color>>6)&31)*255/31);
            pixel[2]=uint8_t(((color>>1)&31)*255/31);pixel[3]=(color&1)?255:0;
        }
    };
    // Canonical 16x16 HUD numerals and letters from segment2 (also unchanged
    // in Usamune, whose Values display prints with them): 0-9 lead the HUD
    // table, then A-Z without J, Q, V, X and Z, so D=13, L=20, R=25, U=28.
    for(unsigned i=0;i<10;++i)blit(data.data()+i*512,16,16,i*16,unsigned(cell::digit_y));
    constexpr unsigned letters[]={28,13,20,25}; // cell::U, D, L, R
    for(unsigned j=0;j<4;++j)blit(data.data()+letters[j]*512,16,16,unsigned(cell::letter_x)+j*16,unsigned(cell::letter_y));
    // Practice ROM bootstrap copies cartridge0x7cc6c0 to RAM00400000.
    // Table ptr804195b8 resolves to cartridge7e5c78, and its icon pointers
    // resolve through that SAME bias. Read/prove the table rather than guess
    // an orange arrow's meaning from its pixel shape (7470 is C-DOWN). The
    // knob table follows it: the 8x8 dot, then the 16x16 ring.
    constexpr uint32_t table=0x7e5c78,knobs=table+9*12,ring=0x7e77f0,base_tile_at=0x7e75f0,bias=0x7fc33940;
    usamune=bytes>=0x7e7a70;
    for(unsigned i=0;i<9 && usamune;++i)usamune=be32(rom+table+i*12)==(uint32_t(button_masks[i])<<16)
        && be32(rom+table+i*12+4)==button_rom[i]+bias && be32(rom+table+i*12+8)==button_big_rom[i]+bias;
    usamune=usamune && be32(rom+knobs)==0x7e79f0+bias && be32(rom+knobs+4)==ring+bias;
    if(usamune) {
        for(unsigned i=0;i<9;++i)blit(rom+button_big_rom[i],16,16,i*16,unsigned(cell::button_y));
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
    ready=true;return true;
}
}
