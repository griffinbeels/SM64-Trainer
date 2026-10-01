/* Metadata travels with its evaluated body through depth sorting. Inputs are
 * the selected record's pad, never the earlier geometry of a bridged pose. */
#pragma once
#include "ghost_capture.h"
#include <cmath>
#include <cstring>

namespace ghost_annotation {
constexpr unsigned label_bytes=64, max_vertices=2048, atlas_width=256, atlas_height=256;
struct Payload {
    char label[label_bytes]{};
    float position[3]{}, scale=1.0f;
    gc_pad pad{};
    bool names=false, inputs=false, known=false, values=false, artwork=false;
};
struct Vertex {float clip[4], uv[2], rgba[4];};
struct Mesh {Vertex vertices[max_vertices]{};unsigned count=0;};
struct View {const uint8_t* rgba=nullptr;uint32_t width=0,height=0;bool usamune=false;};
// Usamune's own nine-entry held-button table at cartridge0x7e5c78.
// D-pad/L have no colored asset in that HUD and use the stock dialog glyph.
constexpr uint16_t button_masks[]={0x8000,0x4000,0x1000,0x2000,0x10,0x8,0x4,0x2,0x1};
constexpr uint32_t button_rom[]={0x7e7570,0x7e74f0,0x7e7170,0x7e70f0,0x7e71f0,
    0x7e72f0,0x7e7470,0x7e73f0,0x7e7370};
inline int stick_offset(int raw) {return (raw < -70 ? -70 : raw > 70 ? 70 : raw)/5;}
inline bool valid_pad(const gc_sample& s) {
    const gc_pad zero{};
    if(!(s.valid_fields&GC_VALID_PAD))return !std::memcmp(&s.pad,&zero,sizeof(zero));
    return s.pad.stick_x>=-128 && s.pad.stick_x<=127 && s.pad.stick_y>=-128 && s.pad.stick_y<=127
        && !(s.pad.buttons&~uint16_t(0xff3f)) && !s.pad.reserved;
}
// A terminated UTF-8 label is a display value, not the recording's identity.
// Invalid encodings and controls are rejected before crossing the mailbox.
inline bool valid_label(const char (&label)[label_bytes]) {
    unsigned i=0;
    while(i<label_bytes && label[i]) {
        const auto c=uint8_t(label[i++]);unsigned tail=0;uint32_t value=0,minimum=0;
        if(c<0x80) {if(c<0x20 || c==0x7f)return false;continue;}
        if(c>=0xc2 && c<=0xdf) {tail=1;value=c&31;minimum=0x80;}
        else if(c>=0xe0 && c<=0xef) {tail=2;value=c&15;minimum=0x800;}
        else if(c>=0xf0 && c<=0xf4) {tail=3;value=c&7;minimum=0x10000;}
        else return false;
        if(i+tail>=label_bytes)return false;
        while(tail--) {const auto b=uint8_t(label[i++]);if((b&0xc0)!=0x80)return false;value=(value<<6)|(b&63);}
        if(value<minimum || value>0x10ffff || (value>=0xd800 && value<=0xdfff))return false;
    }
    if(i==label_bytes)return false;
    while(i<label_bytes)if(label[i++])return false;
    return true;
}
}
