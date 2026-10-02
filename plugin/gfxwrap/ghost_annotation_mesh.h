/* Bounded CPU layout. No heap, driver calls, strings or work on other threads.
 * Offsets are world sizes at the anchor, so the perspective divide shrinks a
 * far ghost's name like its body; every glyph keeps the anchor's clip depth.
 * The stack above the game's input display is the one screen-space part. */
#pragma once
#include "ghost_annotation_assets.h"
#include "ghost_occlusion.h"
#include "ghost_tuning.h"
#include <cstdio>

namespace ghost_annotation {
inline bool valid(const Payload& p) {
    if(!valid_label(p.label) || !std::isfinite(p.scale) || p.scale<=0)return false;
    for(float value:p.position)if(!std::isfinite(value))return false;
    if(!std::isnan(p.top) && !std::isfinite(p.top))return false;
    if(p.background>GP_NAME_WHITE || !(p.background_opacity>=0 && p.background_opacity<=1))return false;
    gc_sample sample{};sample.pad=p.pad;if(p.known)sample.valid_fields=GC_VALID_PAD;
    return valid_pad(sample);
}
// The name's baseline above the ghost's feet: just over the top of his drawn
// body, so a crouch, slide or dive brings it down with him.
inline float name_height(const Payload& p) {
    if(std::isnan(p.top))return GP_ANNOTATION_HEIGHT*p.scale;
    const float top=std::fmin(std::fmax(p.top-p.position[1],0.0f),GP_ANNOTATION_TOP_MAX*p.scale);
    return top+GP_ANNOTATION_GAP*p.scale;
}
// The white atlas cell: a box's solid colour.
constexpr float white_u=atlas_width-1,white_v=atlas_height-1;
// Layout pixels: x right and y up from the anchor.
struct Layout {
    Mesh& mesh;float anchor[4],sx,sy,alpha; // sx, sy: clip units per layout pixel
    float hud_centre=0; // the Usamune x drawn at layout x = hud_left
    float hud_left=0,hud_scale=GP_ANNOTATION_INPUT_SCALE;
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
    // A dialogue glyph: white with a drop shadow over the world, or plain in
    // the given shade on a dialog box (the game's own boxes draw no shadow).
    void character(uint8_t c,float x,float y,float scale=0.75f,float shade=1,bool shadow=true) {
        if(c==0x9e)return;
        const float u=float(c%32)*8,v=float(c/32)*16;
        if(shadow)quad(x+scale,y-scale,8*scale,16*scale,u,v,8,16,0,0.65f);
        quad(x,y,8*scale,16*scale,u,v,8,16,shade);
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
    // The glyphs that fit in `limit` layout pixels, ending "..." when cut.
    static unsigned fitted(const char* value,float scale,float limit,uint8_t (&chars)[64],unsigned& width) {
        unsigned n=characters(value,chars),original=n;width=0;
        for(unsigned i=0;i<n;++i)width+=advance(chars[i]);
        while(n && width*scale>limit) {width-=advance(chars[--n]);}
        if(n<original) {
            while(n && (width+3*advance(glyph('.')))*scale>limit)width-=advance(chars[--n]);
            for(unsigned i=0;i<3 && n<63;++i) {chars[n++]=glyph('.');width+=advance(glyph('.'));}
        }
        return n;
    }
    // The name, centred on the anchor, on SM64's NPC (black, white text) or
    // sign (white, black text) dialog box when `box` is above zero.
    void text(const char* value,float y,float scale=0.75f,float box=0,uint8_t background=GP_NAME_BLACK) {
        uint8_t chars[64]{};unsigned width=0;const unsigned n=fitted(value,scale,180,chars,width);
        if(value[0] && n==0)return;
        float x=-float(width)*scale*0.5f;
        const bool boxed=box>0,white=background==GP_NAME_WHITE;
        if(boxed) {
            // The glyph cell is 16 tall with its letters in rows 2-15 (caps 2-12).
            constexpr float pad=3,bottom=1,top=1;
            quad(x-pad*scale,y+bottom*scale,(float(width)+2*pad)*scale,(16-bottom-top)*scale,
                 white_u,white_v,1,1,white?1.0f:0.0f,box);
        }
        for(unsigned i=0;i<n;++i) {
            character(chars[i],x,y,scale,boxed && white?0.0f:1.0f,!boxed);x+=advance(chars[i])*scale;
        }
    }
    // A left-aligned label (the stack's rows), cut to `limit`.
    void label(const char* value,float x,float y,float scale,float limit) {
        uint8_t chars[64]{};unsigned width=0;const unsigned n=fitted(value,scale,limit,chars,width);
        for(unsigned i=0;i<n;++i) {character(chars[i],x,y,scale);x+=advance(chars[i])*scale;}
    }
    // One rectangle of Usamune's HUD (its pixels, y down), its x measured from
    // hud_centre and its bottom edge at `bottom`.
    void hud(float bottom,float x,float y,float w,float h,float u,float v,float uw,float vh,float opacity=1) {
        const float s=hud_scale;
        quad(hud_left+(x-hud_centre)*s,bottom+(usamune::bottom-(y+h))*s,w*s,h*s,u,v,uw,vh,1,opacity);
    }
    void hud_glyph(float bottom,uint8_t c,float x,float y) { // a dialogue glyph at HUD size
        const float s=hud_scale;
        character(c,hud_left+(x-hud_centre)*s,bottom+(usamune::bottom-(y+16))*s,s);
    }
    // Usamune's Values row: no letter at zero, then |value| in HUD numerals.
    void row(float bottom,int value,float y,cell::Letter negative,cell::Letter positive) {
        if(value)hud(bottom,usamune::letter_x,y,16,16,cell::letter_x+16*float(value<0?negative:positive),cell::letter_y,16,16);
        char digits[8]{};std::snprintf(digits,sizeof(digits),"%d",value<0?-value:value);
        float x=usamune::digit_x;
        for(unsigned i=0;digits[i];++i,x+=usamune::digit_advance)
            hud(bottom,x,y,16,16,float(digits[i]-'0')*16,cell::digit_y,16,16);
    }
    // The drawn span's left and right edges, in Usamune pixels.
    static void extents(const Payload& p,float& left,float& first,float& right) {
        using namespace usamune;
        unsigned held=0,digits=1;
        if(p.known)for(auto mask:button_masks)held+=(p.pad.buttons&mask)!=0;
        if(p.known)for(int value:{int(p.pad.stick_x),int(p.pad.stick_y)}) {
            unsigned n=1;for(int v=value<0?-value:value;v>=10;v/=10)++n;
            if(n>digits)digits=n;
        }
        left=p.values?values_left:stick_left;const float column=p.values?values_right(digits):stick_reach;
        first=column+GP_ANNOTATION_BUTTON_GAP;
        right=held?first+float(held)*button_advance-2:p.values?column:stick_right;
    }
    // Usamune's display with its left edge at hud_left (centred = false), or
    // its drawn span centred on x = 0.
    void inputs(const Payload& p,float bottom,bool centred=true) {
        using namespace usamune;
        float left=0,first=0,right=0;extents(p,left,first,right);
        hud_centre=centred?(left+right)*0.5f:left;
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
// The stack's geometry in the game's 320x240 HUD space (y down): rows climb
// from just above the game's own input display, shrinking to fit when many
// ghosts show one.
namespace stack {
constexpr float left=usamune::letter_x,base=usamune::y_row-2;
// A row is Usamune's display plus room for the ring's travel above its base.
constexpr float row_height=usamune::bottom-usamune::y_row+6;
inline float scale(unsigned count) {
    const float fit=count?GP_HUD_STACK_PIXELS/(float(count)*row_height):GP_HUD_ROW_SCALE;
    return std::fmin(GP_HUD_ROW_SCALE,fit);
}
inline float row_pixels(float scale) {return row_height*scale;}
inline float label_pixels(float scale) {return 64*scale;}
}
// This ghost's row of the stack: its name, then its inputs, at full alpha in
// screen space (a readout beside the player's own, never hidden by terrain).
inline void stack_row(const Payload& p,uint32_t flip_y,Mesh& mesh) {
    if(!p.hud || !p.artwork || !p.hud_count || p.hud_rank>=p.hud_count)return;
    const float r=stack::scale(p.hud_count);
    // One layout pixel is one HUD pixel: NDC is 2/320 across, 2/240 down.
    float anchor[4]={stack::left/160.0f-1.0f,1.0f-stack::base/120.0f,-0.99f,1.0f};
    const float up=flip_y?-1.0f:1.0f;
    if(flip_y)anchor[1]=-anchor[1];
    Layout layout{mesh,{anchor[0],anchor[1],anchor[2],anchor[3]},2.0f/320,2.0f/240*up,1.0f};
    const float bottom=float(p.hud_rank)*stack::row_pixels(r);
    const float glyph=r*1.1f,label_width=stack::label_pixels(r);
    layout.label(p.label[0]?p.label:"GHOST",0,bottom+((usamune::bottom-usamune::y_row)*r-16*glyph)*0.5f,glyph,label_width);
    layout.hud_scale=r;layout.hud_left=label_width+2*r;
    layout.inputs(p,bottom,false);
}
inline void build(const Payload& p,const float vp[16],uint32_t flip_y,float opacity,float body_alpha,
                  const ghost_occlusion::Region& occlusion,unsigned viewport_width,unsigned viewport_height,
                  Mesh& mesh) {
    mesh.count=0;
    if(!viewport_width || !viewport_height || !valid(p))return;
    stack_row(p,flip_y,mesh);
    if(!p.names && !p.inputs)return;
    float anchor[4]{},body[4]{};
    const float height=name_height(p);
    for(unsigned r=0;r<4;++r) {
        anchor[r]=vp[r]*p.position[0]+vp[4+r]*(p.position[1]+height)+vp[8+r]*p.position[2]+vp[12+r];
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
    if(p.names)layout.text(p.label[0]?p.label:"GHOST",0,0.75f,p.background_opacity,p.background);
    // Usamune's display sits directly above the name.
    if(p.inputs && p.artwork)layout.inputs(p,p.names?14.0f:0.0f);
}
}
