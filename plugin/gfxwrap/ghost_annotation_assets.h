/* Setup worker only: extract canonical glyphs from the verified cartridge.
 * No texture or font binary is distributed; the source ROM remains the owner. */
#pragma once
#include "ghost_annotation.h"
#include <array>
namespace ghost_annotation {
// Atlas cells (pixels, row 0 at the top). Dialogue glyph c sits at
// ((c%32)*8, (c/32)*16); everything else below it.
namespace cell {
constexpr float digit_y=128;                 // HUD numeral i at (i*16, 128), 16x16
constexpr float letter_x=160,letter_y=128;   // HUD U, D, L, R at letter_x + j*16
constexpr float button_y=144;                // Usamune 16x16 button i at (i*16, 144)
constexpr float ring_x=144,ring_y=144;       // 16x16 stick ring
constexpr float base_x=160,base_y=144;       // 32x32 mirrored stick base
// The ghost path's 8x8 chevron (ghost_path.h chevron_x/y): grey = its own
// brightness, alpha = the chevron's pixels, rows in order (tip last).
constexpr unsigned chevron_x=192,chevron_y=144;
enum Letter {U,D,L,R};
}
// Lethal Lava Land's chevron, cropped from its fire-bank texture (his choice,
// 2026-10-03: "lll_chevron seems legit, just need to make sure it's cropped
// properly"). The bank's MIO0 block at ROM 0x31E1D0 decodes to 0xC000 bytes;
// at 0xB800 lies the 32x32 RGBA16 arrow-platform texture, identical in the US
// and Usamune ROMs (FNV-1a of its 2048 bytes below). The chevron alone is
// columns 12-19; rows 1-8 hold one whole V, its tip on the last row, and the
// texture repeats every 8 rows. Its pixels are those with blue above 3 of 31
// (the brown around them has 2-3); the four tones it has become brightness,
// max(r, g, b), floored at 0.35 (89 of 255).
namespace chevron_texture {
constexpr uint32_t bank=0x31e1d0,decoded=0xc000,texture=0xb800,texture_fnv1a=0x7e9f2d09u;
constexpr unsigned first_column=12,first_row=1,size=8;
constexpr uint8_t floor=89;
}
struct Assets {
    std::array<uint8_t,atlas_width*atlas_height*4> rgba{};
    bool ready=false,usamune=false,chevron=false;
    bool load(const uint8_t* rom,uint32_t bytes);
    View view() const {return ready?View{rgba.data(),atlas_width,atlas_height,usamune}:View{};}
};
// Pinned n64decomp 06ec56df7f951f88da05f468cdcacecba496145a:
// charmap.txt, ingame_menu.c:gDialogCharWidths, segment2.c:font LUT.
inline uint8_t glyph(uint32_t c) {
    if(c>='0' && c<='9')return uint8_t(c-'0');
    if(c>='A' && c<='Z')return uint8_t(c-'A'+10);
    if(c>='a' && c<='z')return uint8_t(c-'a'+36);
    switch(c) {
    case '\'':return 0x3e;case '.':return 0x3f;case '^':return 0x50;case '|':return 0x51;
    case '<':return 0x52;case '>':return 0x53;case ',':return 0x6f;case ' ':return 0x9e;
    // US dialogue D0 is a two-space control, not a drawable slash.
    case '-':return 0x9f;case '(':return 0xe1;case ')':return 0xe3;
    case '+':return 0xe4;case '&':return 0xe5;case ':':return 0xe6;case '!':return 0xf2;
    case '%':return 0xf3;case '?':return 0xf4;case '~':return 0xf7;case '$':return 0xf9;
    default:return 0xf4;
    }
}
inline uint8_t advance(uint8_t c) {
    constexpr uint8_t first[]={7,7,7,7,7,7,7,7,7,7,6,6,6,6,6,6,6,6,5,6,6,5,8,8,6,6,6,6,6,5,6,6,
        8,7,6,6,6,5,5,6,5,5,6,5,4,5,5,3,7,5,5,5,6,5,5,5,5,5,7,7,5,5,4,4};
    if(c<sizeof(first))return first[c];
    switch(c) {case 0x50:case 0x51:case 0x52:case 0x53:return 8;case 0x6f:return 4;
    case 0x9e:return 5;case 0x9f:return 6;case 0xd0:return 7;case 0xe1:return 5;case 0xe3:return 5;
    case 0xe4:return 9;case 0xe5:return 8;case 0xe6:return 4;case 0xf2:return 5;case 0xf3:return 7;
    case 0xf4:return 7;case 0xf7:return 8;case 0xf9:return 8;default:return 7;}
}
}
