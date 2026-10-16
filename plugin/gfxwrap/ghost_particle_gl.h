/* Recorded Mario particles (bubbles, splashes, water rings, droplets, dust,
 * sparkles) on the private display branch, drawn right after their ghost's
 * body: tested against copied terrain like the body and against that body's
 * own depth, never writing depth. Geometry and textures are the ROM's own
 * particle frames (model particles.c); the pose supplies world-space quads.
 * Cutout frames keep texels with alpha >= .5 (the game's texture-edge layer),
 * blend frames blend texture alpha times vertex alpha. Storage is prepared
 * once; frame uploads are bounded and allocation-free. */
#pragma once
#include "ghost_compositor_gl.h"
#include <cmath>

namespace ghost_compositor::particle {
constexpr uint32_t max_vertices=particle_vertices, max_ghosts=max_poses;
constexpr const char* vertex_shader=R"(#version 430 core
layout(location=0) in vec3 position;
layout(location=1) in vec2 uv;
layout(location=2) in vec4 rect;
layout(location=3) in vec4 shade;
layout(location=4) in float cutout;
layout(location=5) in float mixing;
uniform mat4 viewProjection;
uniform int flipY;
out vec2 texCoord;
flat out vec4 cell;
out vec4 vertexShade;
flat out float cut;
flat out float mixed;
out vec4 occlusionClip;
void main() {
    gl_Position=viewProjection*vec4(position,1.0);
    occlusionClip=gl_Position;
    if(flipY!=0)gl_Position.y=-gl_Position.y;
    texCoord=uv;cell=rect;vertexShade=shade;cut=cutout;mixed=mixing;
})";
// Compiled after gl::fragment_version and the occlusion GLSL (compositor program()).
constexpr const char* fragment_shader=R"(
layout(location=0) out vec4 outputColor;
uniform sampler2D atlas;
uniform sampler2D terrain;
uniform sampler2D layerColor;
uniform sampler2D layerDepth;
uniform int layerEnabled;
uniform vec2 depthMapping;
uniform float opacity;
in vec2 texCoord;
flat in vec4 cell;
in vec4 vertexShade;
flat in float cut;
flat in float mixed;
in vec4 occlusionClip;
void main() {
    float depth=clamp((gl_FragCoord.z*2.0-1.0)*depthMapping.x+depthMapping.y,0.0,1.0);
    float scene=texelFetch(terrain,ivec2(gl_FragCoord.xy),0).r;
    if(depth>=scene && scene<1.0) discard;
    gl_FragDepth=depth;
    vec4 texel=texture(atlas,clamp(texCoord,cell.xy,cell.zw));
    vec4 color=mixed!=0.0 ? vec4(mix(vertexShade.rgb,texel.rgb,texel.a),vertexShade.a) : texel*vertexShade;
    if(cut==1.0) { if(color.a<0.5) discard; color.a=1.0; } // texture edge
    else if(cut==2.0) color.a=1.0;                          // opaque: alpha ignored
    vec3 rgb=color.rgb;
    if(layerEnabled!=0 && depth>texelFetch(layerDepth,ivec2(gl_FragCoord.xy),0).r) {
        vec4 surface=texelFetch(layerColor,ivec2(gl_FragCoord.xy),0);
        rgb=surface.rgb+rgb*surface.a;
    }
    outputColor=vec4(clamp(rgb,0.0,1.0),color.a*opacity*playerFirst(occlusionClip));
})";

inline bool valid(const Particles& p) {
    if(!p.count)return true;
    if(!p.vertices || p.count>max_vertices || p.count%3 || p.opaque_count>p.count || p.opaque_count%3)return false;
    for(float f:p.view_projection)if(!std::isfinite(f))return false;
    for(uint32_t i=0;i<p.count;++i) {
        const auto& v=p.vertices[i];
        for(float f:v.position)if(!std::isfinite(f))return false;
        for(float f:v.uv)if(!std::isfinite(f))return false;
        for(float f:v.rect)if(!std::isfinite(f))return false;
        for(float f:v.rgba)if(!std::isfinite(f) || f<0 || f>1)return false;
        if((v.cutout!=0 && v.cutout!=1 && v.cutout!=2) || (v.mix!=0 && v.mix!=1))return false;
    }
    return true;
}
struct Storage {
    uint32_t program=0,vao=0,vbo=0,texture=0,width=0,height=0;
    int32_t uniforms[10]={};
    gl::OcclusionUniforms occlusion; // the player-first fade, as for the body
    bool ready=false;
    bool prepare(const Dispatch& g,uint32_t compiled,const uint8_t* rgba,uint32_t w,uint32_t h) {
        using namespace gl;
        ready=false;
        if(!program) {
            program=compiled;
            if(!program)return false;
            constexpr const char* names[]={"viewProjection","flipY","atlas","terrain","layerColor","layerDepth",
                "layerEnabled","depthMapping","opacity"};
            for(size_t i=0;i<9;++i)if((uniforms[i]=g.GetUniformLocation(program,names[i]))<0)return false;
            if(!occlusion.find(g,program))return false;
            g.GenVertexArrays(1,&vao);g.GenBuffers(1,&vbo);g.GenTextures(1,&texture);
        }
        if(!vao || !vbo || !texture || !rgba || !w || !h || w>4096 || h>4096)return false;
        g.BindBuffer(PIXEL_UNPACK_BUFFER,0);g.PixelStorei(UNPACK_ALIGNMENT,1);
        g.PixelStorei(UNPACK_ROW_LENGTH,0);g.PixelStorei(UNPACK_SKIP_ROWS,0);g.PixelStorei(UNPACK_SKIP_PIXELS,0);
        g.ActiveTexture(TEXTURE0);g.BindTexture(TEXTURE_2D,texture);
        // The pinned renderer filters N64 textures; clamping to the cell rect
        // keeps a frame from sampling its neighbour.
        g.TexParameteri(TEXTURE_2D,TEXTURE_MIN_FILTER,LINEAR);g.TexParameteri(TEXTURE_2D,TEXTURE_MAG_FILTER,LINEAR);
        g.TexParameteri(TEXTURE_2D,TEXTURE_WRAP_S,CLAMP_TO_EDGE);g.TexParameteri(TEXTURE_2D,TEXTURE_WRAP_T,CLAMP_TO_EDGE);
        g.TexImage2D(TEXTURE_2D,0,RGBA8,w,h,0,RGBA,UNSIGNED_BYTE,rgba);
        g.BindVertexArray(vao);g.BindBuffer(ARRAY_BUFFER,vbo);
        g.BufferData(ARRAY_BUFFER,max_vertices*max_ghosts*sizeof(ParticleVertex),nullptr,DYNAMIC_DRAW);
        constexpr int32_t sizes[]={3,2,4,4,1,1};
        constexpr size_t offsets[]={offsetof(ParticleVertex,position),offsetof(ParticleVertex,uv),
            offsetof(ParticleVertex,rect),offsetof(ParticleVertex,rgba),offsetof(ParticleVertex,cutout),
            offsetof(ParticleVertex,mix)};
        for(uint32_t i=0;i<6;++i) {
            g.EnableVertexAttribArray(i);
            g.VertexAttribPointer(i,sizes[i],FLOAT,0,sizeof(ParticleVertex),reinterpret_cast<const void*>(offsets[i]));
        }
        // The compositor's own units: atlas, then the body's terrain and layer.
        g.UseProgram(program);
        for(uint32_t i=0;i<4;++i)g.Uniform1i(uniforms[2+i],int32_t(unit_base+i));
        width=w;height=h;ready=true;return true;
    }
    void upload(const Dispatch& g,const Pose* poses,uint32_t count) const {
        using namespace gl;
        if(!ready)return;
        g.BindBuffer(COPY_WRITE_BUFFER,vbo);
        for(uint32_t i=0;i<count;++i)if(poses[i].particles.count)
            g.BufferSubData(COPY_WRITE_BUFFER,i*max_vertices*sizeof(ParticleVertex),
                poses[i].particles.count*sizeof(ParticleVertex),poses[i].particles.vertices);
    }
    // Called with the ghost's own self-depth attached and the body's units
    // bound (unit_base+1 terrain, +2 layer color, +3 layer depth).
    void draw(const Dispatch& g,const Pose& pose,uint32_t index,const Target& t,bool layered) const {
        using namespace gl;
        if(!ready || !pose.particles.count || pose.opacity==0)return;
        g.UseProgram(program);g.BindVertexArray(vao);
        g.UniformMatrix4fv(uniforms[0],1,0,pose.particles.view_projection);g.Uniform1i(uniforms[1],pose.flip_y);
        g.Uniform1i(uniforms[6],layered ? 1 : 0);g.Uniform2f(uniforms[7],t.depth_scale,t.depth_translate);
        g.Uniform1f(uniforms[8],pose.opacity);occlusion.set(g,pose.occlusion);
        bind_units(g,0,1,&texture);
        g.Disable(STENCIL_TEST);g.Enable(DEPTH_TEST);
        g.Enablei(BLEND,0);g.BlendFuncSeparatei(0,SRC_ALPHA,ONE_MINUS_SRC_ALPHA,ZERO,ONE);
        g.BlendEquationSeparatei(0,FUNC_ADD,FUNC_ADD);
        const int32_t first=int32_t(index*max_vertices),opaque=int32_t(pose.particles.opaque_count);
        if(opaque) {
            // Opaque meshes as the game draws them, depth-writing: a nearest-
            // surface prepass, then one colour contribution per pixel.
            g.ColorMaski(0,0,0,0,0);g.DepthMask(1);g.DepthFunc(LESS);g.DrawArrays(TRIANGLES,first,opaque);
            g.ColorMaski(0,1,1,1,1);g.DepthMask(0);g.DepthFunc(EQUAL);g.DrawArrays(TRIANGLES,first,opaque);
        }
        g.ColorMaski(0,1,1,1,1);g.DepthMask(0);g.DepthFunc(LESS);
        if(int32_t(pose.particles.count)>opaque)g.DrawArrays(TRIANGLES,first+opaque,int32_t(pose.particles.count)-opaque);
    }
    void destroy(const Dispatch& g) {
        g.DeleteVertexArrays(1,&vao);g.DeleteBuffers(1,&vbo);g.DeleteTextures(1,&texture);
        if(program)g.DeleteProgram(program);
        *this={};
    }
};
}
