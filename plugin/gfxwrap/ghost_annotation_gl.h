/* Private display only. UI atlas has point filtering like the stock dialogue
 * display list. Upload each ghost into a disjoint, preallocated GPU range. */
#pragma once
#include "ghost_compositor_gl.h"
#include "ghost_annotation_mesh.h"
namespace ghost_compositor::annotation {
constexpr const char* vertex_shader=R"(#version 430 core
layout(location=0) in vec4 position;
layout(location=1) in vec2 uv;
layout(location=2) in vec4 shade;
out vec2 texCoord;
out vec4 vertexShade;
void main() {gl_Position=position;texCoord=uv;vertexShade=shade;}
)";
constexpr const char* fragment_shader=R"(
layout(location=0) out vec4 outputColor;
uniform sampler2D atlas;
uniform sampler2D terrain;
uniform sampler2D layerColor;
uniform sampler2D layerDepth;
uniform int layerEnabled;
uniform vec2 depthMapping;
in vec2 texCoord;
in vec4 vertexShade;
void main() {
    float depth=clamp((gl_FragCoord.z*2.0-1.0)*depthMapping.x+depthMapping.y,0.0,1.0);
    float scene=texelFetch(terrain,ivec2(gl_FragCoord.xy),0).r;
    if(depth>=scene && scene<1.0)discard;
    vec4 color=texture(atlas,texCoord)*vertexShade;
    if(color.a<=0.0)discard;
    if(layerEnabled!=0 && depth>texelFetch(layerDepth,ivec2(gl_FragCoord.xy),0).r) {
        vec4 surface=texelFetch(layerColor,ivec2(gl_FragCoord.xy),0);
        color.rgb=surface.rgb+color.rgb*surface.a;
    }
    outputColor=color;
}
)";
struct Storage {
    uint32_t program=0,vao=0,vbo=0,texture=0;
    int32_t uniforms[6]{};
    uint32_t counts[max_poses]{};
    ghost_annotation::Mesh mesh; // scratch only; copied before the next ghost
    bool ready=false;
    bool prepare(const Dispatch& g,uint32_t compiled,const uint8_t* rgba,uint32_t w,uint32_t h) {
        using namespace gl;ready=false;
        if(!program) {
            program=compiled;if(!program)return false;
            constexpr const char* names[]={"atlas","terrain","layerColor","layerDepth","layerEnabled","depthMapping"};
            for(unsigned i=0;i<6;++i)if((uniforms[i]=g.GetUniformLocation(program,names[i]))<0)return false;
            g.GenVertexArrays(1,&vao);g.GenBuffers(1,&vbo);g.GenTextures(1,&texture);
        }
        if(!vao || !vbo || !texture || !rgba || w!=ghost_annotation::atlas_width || h!=ghost_annotation::atlas_height)return false;
        g.BindBuffer(PIXEL_UNPACK_BUFFER,0);g.PixelStorei(UNPACK_ALIGNMENT,1);
        g.PixelStorei(UNPACK_ROW_LENGTH,0);g.PixelStorei(UNPACK_SKIP_ROWS,0);g.PixelStorei(UNPACK_SKIP_PIXELS,0);
        g.ActiveTexture(TEXTURE0);g.BindTexture(TEXTURE_2D,texture);
        g.TexParameteri(TEXTURE_2D,TEXTURE_MIN_FILTER,NEAREST);g.TexParameteri(TEXTURE_2D,TEXTURE_MAG_FILTER,NEAREST);
        g.TexParameteri(TEXTURE_2D,TEXTURE_WRAP_S,CLAMP_TO_EDGE);g.TexParameteri(TEXTURE_2D,TEXTURE_WRAP_T,CLAMP_TO_EDGE);
        g.TexImage2D(TEXTURE_2D,0,RGBA8,w,h,0,RGBA,UNSIGNED_BYTE,rgba);
        g.BindVertexArray(vao);g.BindBuffer(ARRAY_BUFFER,vbo);
        g.BufferData(ARRAY_BUFFER,max_poses*ghost_annotation::max_vertices*sizeof(ghost_annotation::Vertex),nullptr,DYNAMIC_DRAW);
        constexpr int sizes[]={4,2,4};constexpr size_t offsets[]={offsetof(ghost_annotation::Vertex,clip),
            offsetof(ghost_annotation::Vertex,uv),offsetof(ghost_annotation::Vertex,rgba)};
        for(unsigned i=0;i<3;++i) {g.EnableVertexAttribArray(i);g.VertexAttribPointer(i,sizes[i],FLOAT,0,
            sizeof(ghost_annotation::Vertex),reinterpret_cast<const void*>(offsets[i]));}
        g.UseProgram(program);for(unsigned i=0;i<4;++i)g.Uniform1i(uniforms[i],unit_base+i);
        ready=true;return true;
    }
    void upload(const Dispatch& g,const Pose* poses,uint32_t count,const Target& t) {
        if(!ready)return;
        g.BindBuffer(gl::COPY_WRITE_BUFFER,vbo);
        // The stack's rows go in slot order, whatever the depth sort did.
        unsigned stacked=0;
        for(unsigned i=0;i<count;++i)stacked+=poses[i].annotation.hud;
        for(unsigned i=0;i<count;++i) {
            const auto& pose=poses[i];auto payload=pose.annotation;
            payload.hud_count=uint8_t(stacked);payload.hud_rank=0;
            for(unsigned j=0;j<count;++j)payload.hud_rank+=poses[j].annotation.hud
                && (poses[j].annotation.slot<payload.slot || (poses[j].annotation.slot==payload.slot && j<i));
            ghost_annotation::build(payload,pose.view_projection,pose.flip_y,
                pose.opacity,pose.body_alpha,pose.occlusion,t.viewport[2],t.viewport[3],mesh);
            counts[i]=mesh.count;
            if(mesh.count)g.BufferSubData(gl::COPY_WRITE_BUFFER,i*ghost_annotation::max_vertices*sizeof(ghost_annotation::Vertex),
                mesh.count*sizeof(ghost_annotation::Vertex),mesh.vertices);
        }
    }
    void draw(const Dispatch& g,uint32_t index,const Target& t,bool layered) const {
        using namespace gl;if(!ready || !counts[index])return;
        g.UseProgram(program);g.BindVertexArray(vao);bind_units(g,0,1,&texture);
        g.Uniform1i(uniforms[4],layered?1:0);g.Uniform2f(uniforms[5],t.depth_scale,t.depth_translate);
        // Terrain remains sampled from the clean copy. Annotations do not
        // write the body self-depth or leak into the clean recording surface.
        g.Disable(STENCIL_TEST);g.Disable(DEPTH_TEST);g.DepthMask(0);
        g.Enablei(BLEND,0);g.BlendFuncSeparatei(0,SRC_ALPHA,ONE_MINUS_SRC_ALPHA,ZERO,ONE);
        g.BlendEquationSeparatei(0,FUNC_ADD,FUNC_ADD);g.ColorMaski(0,1,1,1,1);
        g.DrawArrays(TRIANGLES,index*ghost_annotation::max_vertices,counts[index]);
    }
    void destroy(const Dispatch& g) {
        g.DeleteVertexArrays(1,&vao);g.DeleteBuffers(1,&vbo);g.DeleteTextures(1,&texture);
        if(program)g.DeleteProgram(program);
        program=vao=vbo=texture=0;ready=false;
    }
};
}
