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
enum Letter {U,D,L,R};
}
struct Assets {
    std::array<uint8_t,atlas_width*atlas_height*4> rgba{};
    bool ready=false,usamune=false;
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
