/* Bounded CPU layout. No heap, driver calls, strings or work on other threads.
 * Screen offsets retain the anchor's clip depth and perspective divisor. */
#pragma once
#include "ghost_annotation_assets.h"
#include "ghost_occlusion.h"
#include <cstdio>

namespace ghost_annotation {
inline bool valid(const Payload& p) {
    if(!valid_label(p.label) || !std::isfinite(p.scale) || p.scale<=0)return false;
    for(float value:p.position)if(!std::isfinite(value))return false;
    gc_sample sample{};sample.pad=p.pad;if(p.known)sample.valid_fields=GC_VALID_PAD;
    return valid_pad(sample);
}
struct Layout {
    Mesh& mesh;float anchor[4],sx,sy,alpha;
    void quad(float x,float y,float w,float h,float u,float v,float uw,float vh,float shade=1,float opacity=1) {
        if(mesh.count+6>max_vertices)return;
        constexpr unsigned corners[]={0,1,2,0,2,3};
        for(unsigned corner:corners) {
            auto& out=mesh.vertices[mesh.count++];
            out.clip[0]=anchor[0]+(x+(corner==1 || corner==2?w:0))*sx*anchor[3];
            out.clip[1]=anchor[1]+(y+(corner>=2?h:0))*sy*anchor[3];
            out.clip[2]=anchor[2];out.clip[3]=anchor[3];
            out.uv[0]=(u+(corner==1 || corner==2?uw:0))/atlas_width;
            out.uv[1]=(v+(corner>=2?0:vh))/atlas_height;
            out.rgba[0]=out.rgba[1]=out.rgba[2]=shade;out.rgba[3]=alpha*opacity;
        }
    }
    void character(uint8_t c,float x,float y,float scale=0.75f) {
        if(c==0x9e)return;
        const float u=float(c%32)*8,v=float(c/32)*16;
        quad(x+scale,y-scale,8*scale,16*scale,u,v,8,16,0,0.65f);
        quad(x,y,8*scale,16*scale,u,v,8,16);
    }
    static unsigned characters(const char* text,uint8_t (&out)[64]) {
        unsigned count=0;
        for(unsigned i=0;text[i] && count<63;) {
            uint32_t c=uint8_t(text[i++]);unsigned tail=0;
            if(c>=0xf0) {tail=3;c&=7;}else if(c>=0xe0) {tail=2;c&=15;}else if(c>=0xc0) {tail=1;c&=31;}
            while(tail-- && text[i])c=(c<<6)|(uint8_t(text[i++])&63);
            out[count++]=glyph(c);
        }
        return count;
    }
    void text(const char* value,float y,float scale=0.75f) {
        uint8_t chars[64]{};unsigned n=characters(value,chars),original=n,width=0;
        for(unsigned i=0;i<n;++i)width+=advance(chars[i]);
        while(n && width*scale>180) {width-=advance(chars[--n]);}
        if(n<original) {
            while(n && (width+3*advance(glyph('.')))*scale>180)width-=advance(chars[--n]);
            for(unsigned i=0;i<3 && n<63;++i) {chars[n++]=glyph('.');width+=advance(glyph('.'));}
        }
        if(value[0] && n==0)return;
        float x=-float(width)*scale*0.5f;
        for(unsigned i=0;i<n;++i) {character(chars[i],x,y,scale);x+=advance(chars[i])*scale;}
    }
    void number(int value,float x,float y,float scale) {
        char digits[8]{};std::snprintf(digits,sizeof(digits),"%d",value);
        for(unsigned i=0;digits[i];++i) {
            if(digits[i]=='-')character(glyph('-'),x,y+2*scale,scale);
            else quad(x,y,16*scale,16*scale,float(digits[i]-'0')*16,128,16,16);
            x+=12*scale;
        }
    }
    void inputs(const Payload& p,float y) {
        constexpr float scale=0.625f;
        const struct {uint16_t mask;const char* name;} extra[]={{0x20,"L"},{0x800,"D^"},
            {0x400,"Dv"},{0x200,"D<"},{0x100,"D>"}};
        unsigned held=0;for(auto mask:button_masks)held+=(p.pad.buttons&mask)!=0;
        // Exact Usamune spacing (32-pixel mirrored base, buttons x80 vs
        // base x26, eight pixels each, ten-pixel advance), centered as a group.
        const float button_start=p.values?84.0f:54.0f;
        float width=held?button_start+held*10.0f-2:(p.values?78.0f:32.0f);
        for(const auto& key:extra)if(p.pad.buttons&key.mask) {
            if(!held && width<button_start)width=button_start;
            for(unsigned i=0;key.name[i];++i)width+=advance(glyph(key.name[i]));
            width+=2;
        }
        const float left=-width*scale*0.5f;
        if(p.values) {
            character(glyph('U'),left,y+16*scale,scale);character(glyph('D'),left+7*scale,y+16*scale,scale);
            number(p.pad.stick_y,left+24*scale,y+16*scale,scale);
            character(glyph('L'),left,y,scale);character(glyph('R'),left+7*scale,y,scale);
            number(p.pad.stick_x,left+24*scale,y,scale);
        } else {
            quad(left,y,32*scale,32*scale,80,144,32,32);
            quad(left+(12+stick_offset(p.pad.stick_x))*scale,y+(12+stick_offset(p.pad.stick_y))*scale,
                8*scale,8*scale,112,144,8,8);
        }
        // Raw values can occupy more than the original stick's 32 pixels.
        float x=left+button_start*scale;
        for(unsigned i=0;i<9;++i)if(p.pad.buttons&button_masks[i]) {
            quad(x,y-scale,8*scale,8*scale,float(i*8),144,8,8);x+=10*scale;
        }
        // These controls have no colored Usamune HUD texture. The stock
        // dialogue glyphs keep their exact held state readable.
        for(const auto& key:extra)if(p.pad.buttons&key.mask) {
            for(unsigned i=0;key.name[i];++i) {const auto c=glyph(key.name[i]);character(c,x,y,scale);x+=advance(c)*scale;}
            x+=2*scale;
        }
    }
};
inline void build(const Payload& p,const float vp[16],uint32_t flip_y,float opacity,float body_alpha,
                  const ghost_occlusion::Region& occlusion,unsigned viewport_width,unsigned viewport_height,
                  Mesh& mesh) {
    mesh.count=0;
    if((!p.names && !p.inputs) || !viewport_width || !viewport_height || !valid(p))return;
    float anchor[4]{},body[4]{};
    for(unsigned r=0;r<4;++r) {
        anchor[r]=vp[r]*p.position[0]+vp[4+r]*(p.position[1]+200*p.scale)+vp[8+r]*p.position[2]+vp[12+r];
        body[r]=vp[r]*p.position[0]+vp[4+r]*(p.position[1]+80*p.scale)+vp[8+r]*p.position[2]+vp[12+r];
    }
    for(float f:anchor)if(!std::isfinite(f))return;
    if(anchor[3]<=1 || std::fabs(anchor[0]/anchor[3])>1.3f || std::fabs(anchor[1]/anchor[3])>1.3f)return;
    const float visibility=opacity*body_alpha*ghost_occlusion::point_fade(occlusion,body);
    if(!(visibility>0))return;
    if(flip_y)anchor[1]=-anchor[1];
    Layout layout{mesh,{anchor[0],anchor[1],anchor[2],anchor[3]},
        2.0f*viewport_height/(240.0f*viewport_width),2.0f/240,visibility};
    if(p.names)layout.text(p.label[0]?p.label:"GHOST",0);
    if(!p.inputs)return;
    const float y=p.names?15.0f:0.0f;
    if(!p.known) {layout.text("INPUTS UNKNOWN",y,0.625f);return;}
    if(p.artwork)layout.inputs(p,y);
    else { // old/other cartridges can never masquerade as a neutral pad
        char values[48]{};std::snprintf(values,sizeof(values),"Y %d X %d PAD %04X",int(p.pad.stick_y),int(p.pad.stick_x),p.pad.buttons);
        layout.text(values,y,0.625f);
    }
}
}
