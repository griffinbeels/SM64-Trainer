/* The ghost's path on the private display branch (ghost_path.h). Its depth
 * test is the shader's, against the frame's copied terrain depth like every
 * ghost part (so both depth routes draw it), with the shadow's decal offset:
 * a first pass draws what the game's terrain does not hide, a second the rest
 * at GP_PATH_HIDDEN_ALPHA, so a line around a corner stays one line. The
 * stencil lets each layer cover a pixel once, wherever its quads overlap.
 * All storage is prepared once; a frame uploads into it and allocates nothing. */
#pragma once
#include "ghost_compositor_gl.h"
#include "ghost_path.h"

namespace ghost_compositor::path {
constexpr const char* vertex_shader=R"(#version 430 core
layout(location=0) in vec3 position;
layout(location=1) in vec2 offset;
layout(location=2) in vec2 uv;
layout(location=3) in vec4 shade;
layout(location=4) in float mode;
uniform mat4 viewProjection;
uniform int flipY;
out vec2 texCoord;
out vec4 vertexShade;
flat out int vertexMode;
void main() {
    vec4 clip=viewProjection*vec4(position,1.0);
    clip.xy+=offset*clip.w; // one size on screen at any distance (the icons)
    if(flipY!=0)clip.y=-clip.y;
    gl_Position=clip;texCoord=uv;vertexShade=shade;vertexMode=int(mode+0.5);
})";
// Compiled after gl::fragment_version and the player-first rule like every
// ghost shader; the path needs no fade of its own: live Mario's depth is in
// the terrain copy, so the path behind him draws as hidden.
constexpr const char* fragment_shader=R"(
layout(location=0) out vec4 outputColor;
uniform sampler2D atlas;
uniform sampler2D terrain;
uniform vec2 depthMapping;
uniform int hiddenPass;
uniform float hiddenAlpha;
uniform int chevrons;
uniform vec2 chevronCell;
in vec2 texCoord;
in vec4 vertexShade;
flat in int vertexMode;
void main() {
    float depth=clamp((gl_FragCoord.z*2.0-1.0)*depthMapping.x+depthMapping.y,0.0,1.0);
    float scene=texelFetch(terrain,ivec2(gl_FragCoord.xy),0).r;
    bool hidden=depth>scene && scene<1.0;
    if(hidden!=(hiddenPass!=0))discard;
    vec4 color=vertexShade;
    if(vertexMode==1 && chevrons!=0) {
        // The Lethal Lava Land chevron: its 8 columns across the ribbon, one
        // 8-row cell per spacing along it, the V's tip the way he travels.
        ivec2 texel=ivec2(chevronCell)+ivec2(clamp(int(floor(texCoord.x*8.0)),0,7),int(floor(texCoord.y*8.0))&7);
        vec4 chevron=texelFetch(atlas,texel,0);
        if(chevron.a<=0.0)discard;
        color.rgb*=chevron.r; // its own four tones, kept as brightness under the tint
    } else if(vertexMode==2) color*=texture(atlas,texCoord);
    else if(vertexMode==3 && length(texCoord*2.0-1.0)>1.0)discard;
    if(hiddenPass!=0)color.a*=hiddenAlpha;
    if(color.a<=0.0)discard;
    outputColor=color;
})";
struct Storage {
    uint32_t program=0,vao=0,vbo=0;
    int32_t uniforms[9]{};
    ghost_path::Mesh mesh; // scratch only; uploaded before drawing
    bool ready=false;
    bool prepare(const Dispatch& g,uint32_t compiled) {
        using namespace gl;ready=false;
        if(!program) {
            program=compiled;if(!program)return false;
            constexpr const char* names[]={"viewProjection","flipY","atlas","terrain","depthMapping","hiddenPass",
                "hiddenAlpha","chevrons","chevronCell"};
            for(unsigned i=0;i<9;++i)if((uniforms[i]=g.GetUniformLocation(program,names[i]))<0)return false;
            g.GenVertexArrays(1,&vao);g.GenBuffers(1,&vbo);
        }
        if(!vao || !vbo)return false;
        g.BindVertexArray(vao);g.BindBuffer(ARRAY_BUFFER,vbo);
        g.BufferData(ARRAY_BUFFER,ghost_path::max_vertices*sizeof(ghost_path::Vertex),nullptr,DYNAMIC_DRAW);
        constexpr int sizes[]={3,2,2,4,1};
        constexpr size_t offsets[]={offsetof(ghost_path::Vertex,position),offsetof(ghost_path::Vertex,offset),
            offsetof(ghost_path::Vertex,uv),offsetof(ghost_path::Vertex,rgba),offsetof(ghost_path::Vertex,mode)};
        for(unsigned i=0;i<5;++i) {g.EnableVertexAttribArray(i);g.VertexAttribPointer(i,sizes[i],FLOAT,0,
            sizeof(ghost_path::Vertex),reinterpret_cast<const void*>(offsets[i]));}
        g.UseProgram(program);g.Uniform1i(uniforms[2],int32_t(unit_base));g.Uniform1i(uniforms[3],int32_t(unit_base+1));
        g.Uniform1f(uniforms[6],GP_PATH_HIDDEN_ALPHA);
        ready=true;return true;
    }
    /* Into the display framebuffer, its self-depth/stencil attached. atlas:
     * the annotation atlas (0 when not prepared: no chevrons or icons). */
    void draw(const Dispatch& g,const ghost_path::Frame& frame,const Target& t,uint32_t atlas,uint32_t terrain) {
        using namespace gl;
        if(!ready || !frame.count)return;
        ghost_path::build(frame,mesh);
        if(!mesh.total)return;
        const bool textured=atlas!=0;
        g.BindBuffer(COPY_WRITE_BUFFER,vbo);
        g.BufferSubData(COPY_WRITE_BUFFER,0,ptrdiff_t(mesh.total)*sizeof(ghost_path::Vertex),mesh.vertices);
        g.UseProgram(program);g.BindVertexArray(vao);
        const uint32_t units[2]={atlas,terrain};bind_units(g,0,2,units);
        g.UniformMatrix4fv(uniforms[0],1,0,frame.view_projection);g.Uniform1i(uniforms[1],int32_t(frame.flip_y));
        g.Uniform2f(uniforms[4],t.depth_scale,t.depth_translate);
        g.Uniform1i(uniforms[7],textured && frame.chevrons?1:0);
        g.Uniform2f(uniforms[8],float(ghost_annotation::cell::chevron_x),float(ghost_annotation::cell::chevron_y));
        g.Disablei(SCISSOR_TEST,0);g.DepthMask(1);g.StencilMaskSeparate(FRONT_AND_BACK,0xff);
        g.ClearBufferfi(DEPTH_STENCIL,0,1.0f,0);
        g.ViewportIndexedf(0,float(t.viewport[0]),float(t.viewport[1]),float(t.viewport[2]),float(t.viewport[3]));
        g.ScissorIndexed(0,t.scissor[0],t.scissor[1],t.scissor[2],t.scissor[3]);g.Enablei(SCISSOR_TEST,0);
        g.DepthRangeIndexed(0,0.0,1.0);g.Disable(DEPTH_TEST);g.DepthMask(0);
        g.Enable(POLYGON_OFFSET_FILL);g.PolygonOffset(-3.0f,-3.0f);
        g.Enablei(BLEND,0);g.BlendFuncSeparatei(0,SRC_ALPHA,ONE_MINUS_SRC_ALPHA,ZERO,ONE);
        g.BlendEquationSeparatei(0,FUNC_ADD,FUNC_ADD);g.ColorMaski(0,1,1,1,1);
        g.Enable(STENCIL_TEST);g.StencilOpSeparate(FRONT_AND_BACK,KEEP,KEEP,REPLACE);
        for(int32_t hidden=0;hidden<2;++hidden) {
            g.Uniform1i(uniforms[5],hidden);
            for(uint32_t layer=0;layer<ghost_path::layers;++layer) {
                if(!mesh.count[layer] || (layer==ghost_path::icons && !textured))continue;
                // A layer's bit: its first quad over a pixel draws, the rest
                // pass over (the icons overlap only each other: no bit).
                const uint32_t bit=layer==ghost_path::icons?0u:1u<<layer;
                g.StencilFuncSeparate(FRONT_AND_BACK,bit?NOTEQUAL:ALWAYS,int32_t(bit),bit);
                g.StencilMaskSeparate(FRONT_AND_BACK,bit);
                g.DrawArrays(TRIANGLES,int32_t(mesh.first[layer]),int32_t(mesh.count[layer]));
            }
        }
        g.Disable(POLYGON_OFFSET_FILL);g.Disable(STENCIL_TEST);
    }
    void destroy(const Dispatch& g) {
        g.DeleteVertexArrays(1,&vao);g.DeleteBuffers(1,&vbo);
        if(program)g.DeleteProgram(program);
        program=vao=vbo=0;ready=false;
    }
};
}
