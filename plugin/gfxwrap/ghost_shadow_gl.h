/* Optional original-game shadow on the private display branch. Terrain uses
 * a copied D24 attachment and GLideN64's ordinary ZMODE_DEC offset (-3,-3).
 * N64 image depth needs the separate delta-Z image and is deliberately omitted.
 * All storage is prepared once; frame uploads are bounded and allocation-free. */
#pragma once
#include "ghost_compositor_gl.h"
#include <cmath>

namespace ghost_compositor::shadow {
constexpr uint32_t max_vertices=24, max_ghosts=64;
constexpr const char* vertex_shader=R"(#version 430 core
layout(location=0) in vec3 position;
layout(location=1) in vec2 uv;
layout(location=2) in float alpha;
uniform mat4 viewProjection;
uniform int flipY;
out vec2 texCoord;
out float vertexAlpha;
out vec4 occlusionClip;
void main() {
    gl_Position=viewProjection*vec4(position,1.0);
    occlusionClip=gl_Position;
    if(flipY!=0)gl_Position.y=-gl_Position.y;
    texCoord=uv;vertexAlpha=alpha;
})";
constexpr const char* fragment_shader=R"(
layout(location=0) out vec4 outputColor;
uniform sampler2D shadowTexture;
uniform float opacity;
in vec2 texCoord;
in float vertexAlpha;
in vec4 occlusionClip;
void main() {
    // Pinned GLideN64 BILINEAR_3POINT. The texture sampler mirrors both axes;
    // the pose adapter has already applied the source scale and half texel.
    vec2 off=fract(texCoord*16.0-0.5);
    off-=step(1.0,off.x+off.y);
    float a0=texture(shadowTexture,texCoord-off/16.0).a;
    float a1=texture(shadowTexture,texCoord-(off-vec2(sign(off.x),0.0))/16.0).a;
    float a2=texture(shadowTexture,texCoord-(off-vec2(0.0,sign(off.y)))/16.0).a;
    float alpha=a0+abs(off.x)*(a1-a0)+abs(off.y)*(a2-a0);
    outputColor=vec4(0.0,0.0,0.0,clamp(alpha*vertexAlpha*opacity*playerFirst(occlusionClip),0.0,1.0));
})";

inline bool valid(const Shadow& s) {
    if(!s.count)return true;
    if(!s.vertices || !s.texture_ia8 || s.count>max_vertices || s.count%3)return false;
    for(float f:s.view_projection)if(!std::isfinite(f))return false;
    for(uint32_t i=0;i<s.count;++i) {
        const auto& v=s.vertices[i];
        for(float f:v.position)if(!std::isfinite(f))return false;
        for(float f:v.uv)if(!std::isfinite(f))return false;
        if(!std::isfinite(v.alpha) || v.alpha<0 || v.alpha>1)return false;
    }
    for(unsigned i=0;i<256;++i)if(s.texture_ia8[i]&0xf0)return false;
    return true;
}
struct Storage {
    uint32_t program=0,vao=0,vbo=0,texture=0;
    int32_t matrix=-1,flip=-1,opacity=-1,sampler=-1;
    gl::OcclusionUniforms occlusion;
    bool prepare(const Dispatch& g,uint32_t compiled) {
        using namespace gl;
        if(!program) {
            program=compiled;
            if(!program)return false;
            matrix=g.GetUniformLocation(program,"viewProjection");flip=g.GetUniformLocation(program,"flipY");
            opacity=g.GetUniformLocation(program,"opacity");sampler=g.GetUniformLocation(program,"shadowTexture");
            g.GenVertexArrays(1,&vao);g.GenBuffers(1,&vbo);g.GenTextures(1,&texture);
            if(!occlusion.find(g,program))return false;
        }
        if(!vao || !vbo || !texture || matrix<0 || flip<0 || opacity<0 || sampler<0)return false;
        g.ActiveTexture(TEXTURE0);g.BindTexture(TEXTURE_2D,texture);
        g.TexParameteri(TEXTURE_2D,TEXTURE_MIN_FILTER,NEAREST);g.TexParameteri(TEXTURE_2D,TEXTURE_MAG_FILTER,NEAREST);
        g.TexParameteri(TEXTURE_2D,TEXTURE_WRAP_S,MIRRORED_REPEAT);g.TexParameteri(TEXTURE_2D,TEXTURE_WRAP_T,MIRRORED_REPEAT);
        g.TexImage2D(TEXTURE_2D,0,RGBA8,16,16,0,RGBA,UNSIGNED_BYTE,nullptr);
        g.BindVertexArray(vao);g.BindBuffer(ARRAY_BUFFER,vbo);
        g.BufferData(ARRAY_BUFFER,max_vertices*max_ghosts*sizeof(ShadowVertex),nullptr,DYNAMIC_DRAW);
        constexpr int32_t sizes[]={3,2,1};
        constexpr size_t offsets[]={offsetof(ShadowVertex,position),offsetof(ShadowVertex,uv),offsetof(ShadowVertex,alpha)};
        for(uint32_t i=0;i<3;++i) {
            g.EnableVertexAttribArray(i);g.VertexAttribPointer(i,sizes[i],FLOAT,0,sizeof(ShadowVertex),reinterpret_cast<const void*>(offsets[i]));
        }
        return true;
    }
    void upload(const Dispatch& g,const Pose* poses,uint32_t count) const {
        using namespace gl;
        g.BindVertexArray(vao);g.BindBuffer(ARRAY_BUFFER,vbo);
        for(uint32_t i=0;i<count;++i)if(poses[i].shadow.count)
            g.BufferSubData(ARRAY_BUFFER,i*max_vertices*sizeof(ShadowVertex),
                poses[i].shadow.count*sizeof(ShadowVertex),poses[i].shadow.vertices);
    }
    void draw(const Dispatch& g,const Pose& pose,uint32_t index) const {
        using namespace gl;
        if(!pose.shadow.count || pose.opacity==0)return;
        uint8_t rgba[1024]{};
        for(unsigned i=0;i<256;++i)rgba[4*i+3]=(pose.shadow.texture_ia8[i]&15)*17;
        g.BindBuffer(PIXEL_UNPACK_BUFFER,0);g.PixelStorei(UNPACK_ALIGNMENT,1);
        g.PixelStorei(UNPACK_ROW_LENGTH,0);g.PixelStorei(UNPACK_SKIP_ROWS,0);g.PixelStorei(UNPACK_SKIP_PIXELS,0);
        g.ActiveTexture(TEXTURE0);g.BindTexture(TEXTURE_2D,texture);g.BindSampler(0,0);
        g.TexSubImage2D(TEXTURE_2D,0,0,0,16,16,RGBA,UNSIGNED_BYTE,rgba);
        g.UseProgram(program);g.BindVertexArray(vao);
        g.UniformMatrix4fv(matrix,1,0,pose.shadow.view_projection);g.Uniform1i(flip,pose.flip_y);
        g.Uniform1f(opacity,pose.opacity);g.Uniform1i(sampler,0);occlusion.set(g,pose.occlusion);
        g.DrawArrays(TRIANGLES,index*max_vertices,pose.shadow.count);
    }
    void destroy(const Dispatch& g) {
        g.DeleteVertexArrays(1,&vao);g.DeleteBuffers(1,&vbo);g.DeleteTextures(1,&texture);
        if(program)g.DeleteProgram(program);
        *this={};
    }
};
}
