/* Bounded CPU layout. No heap, driver calls, strings or work on other threads.
 * Offsets are world sizes at the anchor, so the perspective divide shrinks a
 * far ghost's name like its body; every glyph keeps the anchor's clip depth. */
#pragma once
#include "ghost_annotation_assets.h"
#include "ghost_occlusion.h"
#include "ghost_tuning.h"
#include <cstdio>

namespace ghost_annotation {
inline bool valid(const Payload& p) {
    if(!valid_label(p.label) || !std::isfinite(p.scale) || p.scale<=0)return false;
    for(float value:p.position)if(!std::isfinite(value))return false;
    gc_sample sample{};sample.pad=p.pad;if(p.known)sample.valid_fields=GC_VALID_PAD;
    return valid_pad(sample);
}
// Layout pixels: x right and y up from the anchor, which the name centres on.
struct Layout {
    Mesh& mesh;float anchor[4],sx,sy,alpha; // sx, sy: clip units per layout pixel
    float hud_centre=0; // the Usamune x drawn over the name's centre
    void quad(float x,float y,float w,float h,float u,float v,float uw,float vh,float shade=1,float opacity=1) {
        if(mesh.count+6>max_vertices)return;
        constexpr unsigned corners[]={0,1,2,0,2,3};
        for(unsigned corner:corners) {
            auto& out=mesh.vertices[mesh.count++];
            out.clip[0]=anchor[0]+(x+(corner==1 || corner==2?w:0))*sx;
            out.clip[1]=anchor[1]+(y+(corner>=2?h:0))*sy;
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
    // One rectangle of Usamune's HUD (its pixels, y down), its x measured from
    // hud_centre and its bottom edge at `bottom`.
    void hud(float bottom,float x,float y,float w,float h,float u,float v,float uw,float vh,float opacity=1) {
        constexpr float s=GP_ANNOTATION_INPUT_SCALE;
        quad((x-hud_centre)*s,bottom+(usamune::bottom-(y+h))*s,w*s,h*s,u,v,uw,vh,1,opacity);
    }
    void hud_glyph(float bottom,uint8_t c,float x,float y) { // a dialogue glyph at HUD size
        constexpr float s=GP_ANNOTATION_INPUT_SCALE;
        character(c,(x-hud_centre)*s,bottom+(usamune::bottom-(y+16))*s,s);
    }
    // Usamune's Values row: no letter at zero, then |value| in HUD numerals.
    void row(float bottom,int value,float y,cell::Letter negative,cell::Letter positive) {
        if(value)hud(bottom,usamune::letter_x,y,16,16,cell::letter_x+16*float(value<0?negative:positive),cell::letter_y,16,16);
        char digits[8]{};std::snprintf(digits,sizeof(digits),"%d",value<0?-value:value);
        float x=usamune::digit_x;
        for(unsigned i=0;digits[i];++i,x+=usamune::digit_advance)
            hud(bottom,x,y,16,16,float(digits[i]-'0')*16,cell::digit_y,16,16);
    }
    void inputs(const Payload& p,float bottom) {
        using namespace usamune;
        unsigned held=0,digits=1;
        if(p.known)for(auto mask:button_masks)held+=(p.pad.buttons&mask)!=0;
        if(p.known)for(int value:{int(p.pad.stick_x),int(p.pad.stick_y)}) {
            unsigned n=1;for(int v=value<0?-value:value;v>=10;v/=10)++n;
            if(n>digits)digits=n;
        }
        const float left=p.values?values_left:stick_left,column=p.values?values_right(digits):stick_reach;
        const float first=column+GP_ANNOTATION_BUTTON_GAP;
        const float right=held?first+float(held)*button_advance-2:p.values?column:stick_right;
        hud_centre=(left+right)*0.5f;
        if(!p.known) { // unavailable evidence is never drawn as a neutral pad
            if(p.values) {hud_glyph(bottom,glyph('?'),digit_x,y_row);hud_glyph(bottom,glyph('?'),digit_x,x_row);}
            else {hud(bottom,base_x,base_y,32,32,cell::base_x,cell::base_y,32,32,base_alpha*0.5f);
                hud_glyph(bottom,glyph('?'),knob_x+4,knob_y);}
            return;
        }
        if(p.values) {
            row(bottom,p.pad.stick_y,y_row,cell::D,cell::U);
            row(bottom,p.pad.stick_x,x_row,cell::L,cell::R);
        } else {
            hud(bottom,base_x,base_y,32,32,cell::base_x,cell::base_y,32,32,base_alpha);
            hud(bottom,knob_x+float(stick_offset(p.pad.stick_x)),knob_y-float(stick_offset(p.pad.stick_y)),16,16,
                cell::ring_x,cell::ring_y,16,16);
        }
        float x=first;
        for(unsigned i=0;i<9;++i)if(p.pad.buttons&button_masks[i]) {
            hud(bottom,x,button_y,16,16,float(i*16),cell::button_y,16,16);x+=button_advance;
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
        anchor[r]=vp[r]*p.position[0]+vp[4+r]*(p.position[1]+GP_ANNOTATION_HEIGHT*p.scale)+vp[8+r]*p.position[2]+vp[12+r];
        body[r]=vp[r]*p.position[0]+vp[4+r]*(p.position[1]+80*p.scale)+vp[8+r]*p.position[2]+vp[12+r];
    }
    for(float f:anchor)if(!std::isfinite(f))return;
    if(anchor[3]<=1 || std::fabs(anchor[0]/anchor[3])>1.3f || std::fabs(anchor[1]/anchor[3])>1.3f)return;
    const float visibility=opacity*body_alpha*ghost_occlusion::point_fade(occlusion,body);
    if(!(visibility>0))return;
    // The projection's own per-axis scale (camera roll aside, its row lengths)
    // turns world units into clip units; dividing by w then shrinks with distance.
    const float px=std::sqrt(vp[0]*vp[0]+vp[4]*vp[4]+vp[8]*vp[8]),py=std::sqrt(vp[1]*vp[1]+vp[5]*vp[5]+vp[9]*vp[9]);
    if(!(px>0) || !(py>0) || !std::isfinite(px) || !std::isfinite(py))return;
    const float largest=2.0f*GP_ANNOTATION_MAX_SCREEN/240*anchor[3];
    const float sy=std::fmin(GP_ANNOTATION_WORLD_PER_PIXEL*py,largest),sx=sy*px/py;
    // The renderer draws the world upside down (flip_y) and turns the finished
    // picture over: offsets flip with the anchor, or every glyph reads mirrored.
    const float up=flip_y?-1.0f:1.0f;
    if(flip_y)anchor[1]=-anchor[1];
    Layout layout{mesh,{anchor[0],anchor[1],anchor[2],anchor[3]},sx,sy*up,visibility};
    if(p.names)layout.text(p.label[0]?p.label:"GHOST",0);
    // Usamune's display sits directly above the name.
    if(p.inputs && p.artwork)layout.inputs(p,p.names?14.0f:0.0f);
}
}
