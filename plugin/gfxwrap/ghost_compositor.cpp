#include "ghost_compositor_gl.h"
#include "ghost_shadow_gl.h"
#include <cmath>
#include <new>

namespace ghost_compositor {
using namespace gl;
namespace {
constexpr const char* vertex_shader=R"(#version 430 core
layout(location=0) in vec3 position;
layout(location=1) in vec3 normal;
layout(location=2) in vec3 color;
layout(location=3) in vec2 uv;
layout(location=4) in float materialMode;
uniform mat4 viewProjection;
uniform int flipY;
uniform vec3 lightDirection;
uniform vec2 lighting;
out vec3 vertexColor;
out vec2 texCoord;
out float illumination;
out vec4 occlusionClip;
flat out float mode;
void main() {
    gl_Position=viewProjection*vec4(position,1.0);
    occlusionClip=gl_Position;
    if (flipY!=0) gl_Position.y=-gl_Position.y;
    float n=length(normal);
    float lit=lighting.x+lighting.y*max(n>0.0 ? dot(normal/n,lightDirection) : 0.0,0.0);
    vertexColor=color;
    illumination=lit;
    texCoord=uv;
    mode=materialMode;
})";
constexpr const char* fragment_shader=R"(
layout(location=0) out vec4 outputColor;
uniform sampler2D atlas;
uniform sampler2D terrain;
uniform vec2 depthMapping;
uniform float opacity;
uniform float hueDegrees;
in vec3 vertexColor;
in vec2 texCoord;
in float illumination;
in vec4 occlusionClip;
flat in float mode;
void main() {
    float depth=clamp((gl_FragCoord.z*2.0-1.0)*depthMapping.x+depthMapping.y,0.0,1.0);
    float scene=texelFetch(terrain,ivec2(gl_FragCoord.xy),0).r;
    if (depth>=scene && scene<1.0) discard;
    gl_FragDepth=depth;
    vec4 material=vec4(clamp(vertexColor*illumination,0.0,1.0),1.0);
    if (mode!=0.0) {
        vec4 textureColor=texture(atlas,texCoord);
        if (mode==1.0) material.rgb=mix(material.rgb,textureColor.rgb,textureColor.a);
        else if (mode==2.0) {
            if (textureColor.a==0.0) discard;
            material=textureColor;
        }
        else if (mode==3.0) material.rgb=textureColor.rgb;
        else if (mode==4.0) material.rgb*=textureColor.rgb;
        else {
            if (textureColor.a==0.0) discard;
            material*=textureColor;
        }
    }
    vec3 rgb=material.rgb;
    if(hueDegrees!=0.0) {
        float c=cos(radians(hueDegrees)),s=sin(radians(hueDegrees));
        rgb=vec3(dot(rgb,vec3(.213+.787*c-.213*s,.715-.715*c-.715*s,.072-.072*c+.928*s)),
                 dot(rgb,vec3(.213-.213*c+.143*s,.715+.285*c+.140*s,.072-.072*c-.283*s)),
                 dot(rgb,vec3(.213-.213*c-.787*s,.715-.715*c+.715*s,.072+.928*c+.072*s)));
    }
    outputColor=vec4(clamp(rgb,0.0,1.0),opacity*material.a*playerFirst(occlusionClip));
})";

uint32_t shader(const Dispatch& g,uint32_t type,const char* const* sources,int32_t count) {
    const auto id=g.CreateShader(type); if (!id) return 0;
    g.ShaderSource(id,count,sources,nullptr);g.CompileShader(id);
    int32_t compiled=0;g.GetShaderiv(id,COMPILE_STATUS,&compiled);
    if (!compiled) {g.DeleteShader(id);return 0;} return id;
}
uint32_t program(const Dispatch& g,const char* vertex=vertex_shader,const char* fragment=fragment_shader) {
    const char* const fragment_sources[]={fragment_version,ghost_occlusion::glsl,fragment};
    const auto vs=shader(g,VERTEX_SHADER,&vertex,1), fs=shader(g,FRAGMENT_SHADER,fragment_sources,3);
    if (!vs || !fs) {if(vs)g.DeleteShader(vs);if(fs)g.DeleteShader(fs);return 0;}
    const auto id=g.CreateProgram();
    if(id) {g.AttachShader(id,vs);g.AttachShader(id,fs);g.LinkProgram(id);}
    g.DeleteShader(vs);g.DeleteShader(fs);
    int32_t linked=0;if(id)g.GetProgramiv(id,LINK_STATUS,&linked);
    if(!linked) {if(id)g.DeleteProgram(id);return 0;}return id;
}
bool finite(const float* p,size_t size) {for(size_t i=0;i<size;++i)if(!std::isfinite(p[i]))return false;return true;}
void texture(const Dispatch& g,uint32_t id,int32_t format,uint32_t external,uint32_t type,
             uint32_t width,uint32_t height,const void* data) {
    g.BindTexture(TEXTURE_2D,id);g.TexParameteri(TEXTURE_2D,TEXTURE_MIN_FILTER,NEAREST);
    g.TexParameteri(TEXTURE_2D,TEXTURE_MAG_FILTER,NEAREST);
    g.TexParameteri(TEXTURE_2D,TEXTURE_WRAP_S,CLAMP_TO_EDGE);g.TexParameteri(TEXTURE_2D,TEXTURE_WRAP_T,CLAMP_TO_EDGE);
    g.TexParameteri(TEXTURE_2D,TEXTURE_COMPARE_MODE,ZERO);
    g.TexImage2D(TEXTURE_2D,0,format,width,height,0,external,type,data);
}
bool rectangle(const int32_t* r,uint32_t width,uint32_t height) {
    return r[0]>=0 && r[1]>=0 && r[2]>0 && r[3]>0 &&
        uint64_t(r[0])+r[2]<=width && uint64_t(r[1])+r[3]<=height;
}
bool source_format(const Dispatch& g,Depth depth) {
    int32_t samples=0;g.GetIntegerv(SAMPLES,&samples);if(samples)return false;
    int32_t type=0,encoding=0;
    g.GetFramebufferAttachmentParameteriv(DRAW_FRAMEBUFFER,COLOR_ATTACHMENT0,FRAMEBUFFER_ATTACHMENT_OBJECT_TYPE,&type);
    if(!type)return false;
    g.GetFramebufferAttachmentParameteriv(DRAW_FRAMEBUFFER,COLOR_ATTACHMENT0,FRAMEBUFFER_ATTACHMENT_COMPONENT_TYPE,&type);
    g.GetFramebufferAttachmentParameteriv(DRAW_FRAMEBUFFER,COLOR_ATTACHMENT0,FRAMEBUFFER_ATTACHMENT_COLOR_ENCODING,&encoding);
    if(type!=UNSIGNED_NORMALIZED || encoding!=LINEAR)return false;
    for(auto channel:{FRAMEBUFFER_ATTACHMENT_RED_SIZE,FRAMEBUFFER_ATTACHMENT_GREEN_SIZE,FRAMEBUFFER_ATTACHMENT_BLUE_SIZE}) {
        int32_t bits=0;g.GetFramebufferAttachmentParameteriv(DRAW_FRAMEBUFFER,COLOR_ATTACHMENT0,channel,&bits);
        if(bits!=8)return false;
    }
    if(depth==Depth::ordinary) {
        g.GetFramebufferAttachmentParameteriv(DRAW_FRAMEBUFFER,DEPTH_ATTACHMENT,FRAMEBUFFER_ATTACHMENT_OBJECT_TYPE,&type);
        if(!type)return false;
        int32_t bits=0,stencil=0;g.GetFramebufferAttachmentParameteriv(DRAW_FRAMEBUFFER,DEPTH_ATTACHMENT,FRAMEBUFFER_ATTACHMENT_DEPTH_SIZE,&bits);
        g.GetFramebufferAttachmentParameteriv(DRAW_FRAMEBUFFER,DEPTH_ATTACHMENT,FRAMEBUFFER_ATTACHMENT_STENCIL_SIZE,&stencil);
        g.GetFramebufferAttachmentParameteriv(DRAW_FRAMEBUFFER,DEPTH_ATTACHMENT,FRAMEBUFFER_ATTACHMENT_COMPONENT_TYPE,&type);
        if(bits!=24 || stencil || type!=UNSIGNED_NORMALIZED)return false;
    }
    return true;
}
}

struct Instance {
    Dispatch g;
    uint64_t generation;
    uint32_t width=0,height=0,capacity=0;
    uint32_t textures[3]={}; // display color, copied ordinary depth, atlas
    uint32_t fbos[2]={}; // display/self depth; copied terrain depth
    uint32_t self_depth=0,vao=0,vbo=0,shader_program=0;
    int32_t uniforms[9]={};
    OcclusionUniforms occlusion;
    shadow::Storage shadow;
    Target target{};
    bool ready=false,begun=false,finalized=false,hud=false;
    int32_t hud_draw_fbo=0;
    Instance(const Dispatch& dispatch,uint64_t serial):g(dispatch),generation(serial) {}
};

Instance* create(const Dispatch& g,uint64_t generation) {
    if(!generation)return nullptr;
#define GC_CHECK(result,name,args) if(!g.name)return nullptr;
    GC_GL_FUNCTIONS(GC_CHECK)
#undef GC_CHECK
    int32_t major=0,minor=0;g.GetIntegerv(MAJOR_VERSION,&major);g.GetIntegerv(MINOR_VERSION,&minor);
    if(major<4 || (major==4 && minor<3))return nullptr;
    return new(std::nothrow) Instance(g,generation);
}

void end_hud(Instance* p) {
    if(p && p->hud) {p->g.BindFramebuffer(DRAW_FRAMEBUFFER,p->hud_draw_fbo);p->hud=false;}
}
void abort(Instance* p) {
    if(!p)return;end_hud(p);p->begun=false;p->finalized=false;p->target={};
}

Result prepare(Instance* p,uint32_t width,uint32_t height,uint32_t capacity,
               const uint8_t* atlas,uint32_t aw,uint32_t ah) {
    if(!p)return Result::invalid;
    abort(p);p->ready=false;
    if(!width || !height || width>8192 || height>8192 || !capacity || capacity>64u*30000u ||
       !atlas || !aw || !ah || aw>8192 || ah>8192)return Result::invalid;
    const auto& g=p->g;Guard guard(g);
    if(!p->shader_program) {
        p->shader_program=program(g);if(!p->shader_program)return Result::resources;
        constexpr const char* names[] = {"viewProjection","flipY","lightDirection","lighting","depthMapping","opacity","atlas","terrain","hueDegrees"};
        for(size_t i=0;i<9;++i) {p->uniforms[i]=g.GetUniformLocation(p->shader_program,names[i]);if(p->uniforms[i]<0)return Result::resources;}
        if(!p->occlusion.find(g,p->shader_program))return Result::resources;
        g.GenTextures(3,p->textures);g.GenFramebuffers(2,p->fbos);
        g.GenRenderbuffers(1,&p->self_depth);g.GenVertexArrays(1,&p->vao);g.GenBuffers(1,&p->vbo);
    }
    if(!p->textures[0] || !p->textures[1] || !p->textures[2] || !p->fbos[0] || !p->fbos[1] ||
       !p->self_depth || !p->vao || !p->vbo)return Result::resources;
    g.BindBuffer(PIXEL_UNPACK_BUFFER,0);g.PixelStorei(UNPACK_ALIGNMENT,1);
    g.PixelStorei(UNPACK_ROW_LENGTH,0);g.PixelStorei(UNPACK_SKIP_ROWS,0);g.PixelStorei(UNPACK_SKIP_PIXELS,0);
    if(!p->shadow.prepare(g,p->shadow.program?p->shadow.program:
            program(g,shadow::vertex_shader,shadow::fragment_shader)))return Result::resources;
    g.ActiveTexture(TEXTURE0);
    texture(g,p->textures[0],RGBA8,RGBA,UNSIGNED_BYTE,width,height,nullptr);
    texture(g,p->textures[1],DEPTH_COMPONENT24,DEPTH_COMPONENT,UNSIGNED_INT,width,height,nullptr);
    texture(g,p->textures[2],RGBA8,RGBA,UNSIGNED_BYTE,aw,ah,atlas);
    g.BindRenderbuffer(RENDERBUFFER,p->self_depth);g.RenderbufferStorage(RENDERBUFFER,DEPTH24_STENCIL8,width,height);
    g.BindFramebuffer(DRAW_FRAMEBUFFER,p->fbos[0]);
    g.FramebufferTexture2D(DRAW_FRAMEBUFFER,COLOR_ATTACHMENT0,TEXTURE_2D,p->textures[0],0);
    g.FramebufferRenderbuffer(DRAW_FRAMEBUFFER,DEPTH_STENCIL_ATTACHMENT,RENDERBUFFER,p->self_depth);
    g.DrawBuffer(COLOR_ATTACHMENT0);
    if(g.CheckFramebufferStatus(DRAW_FRAMEBUFFER)!=FRAMEBUFFER_COMPLETE)return Result::resources;
    g.BindFramebuffer(FRAMEBUFFER,p->fbos[1]);
    g.FramebufferTexture2D(FRAMEBUFFER,DEPTH_ATTACHMENT,TEXTURE_2D,p->textures[1],0);
    g.DrawBuffer(ZERO);g.ReadBuffer(ZERO);
    if(g.CheckFramebufferStatus(FRAMEBUFFER)!=FRAMEBUFFER_COMPLETE)return Result::resources;
    g.BindVertexArray(p->vao);g.BindBuffer(ARRAY_BUFFER,p->vbo);
    g.BufferData(ARRAY_BUFFER,ptrdiff_t(capacity)*sizeof(Vertex),nullptr,DYNAMIC_DRAW);
    constexpr int32_t counts[]={3,3,3,2,1};
    constexpr size_t offsets[]={offsetof(Vertex,position),offsetof(Vertex,normal),offsetof(Vertex,color),offsetof(Vertex,uv),offsetof(Vertex,material)};
    for(uint32_t i=0;i<5;++i) {g.EnableVertexAttribArray(i);g.VertexAttribPointer(i,counts[i],i==4 ? UNSIGNED_INT : FLOAT,0,sizeof(Vertex),reinterpret_cast<const void*>(offsets[i]));}
    p->width=width;p->height=height;p->capacity=capacity;p->ready=true;return Result::ok;
}

Result begin(Instance* p,const Target& t,const Pose& pose) {
    return begin_batch(p,t,&pose,1);
}

Result begin_batch(Instance* p,const Target& t,const Pose* poses,uint32_t count) {
    if(!p)return Result::invalid;
    abort(p);
    if(!poses || !count || count>64)return Result::invalid;
    if(!p->ready || t.generation!=p->generation || !t.occurrence || !t.framebuffer ||
       t.framebuffer==p->fbos[0] || t.framebuffer==p->fbos[1] || t.width!=p->width || t.height!=p->height ||
       !rectangle(t.viewport,t.width,t.height) || !rectangle(t.scissor,t.width,t.height) ||
       !std::isfinite(t.depth_scale) || !std::isfinite(t.depth_translate) || t.depth_scale<=0)return Result::invalid;
    uint64_t total_vertices=0;
    for(uint32_t index=0;index<count;++index) {
      const auto& pose=poses[index];total_vertices+=pose.count;
      if(!pose.vertices || !pose.count || total_vertices>p->capacity || pose.count%3 ||
       !finite(pose.view_projection,16) || !finite(pose.light_direction,3) ||
       !std::isfinite(pose.ambient) || !std::isfinite(pose.diffuse) || !std::isfinite(pose.opacity) ||
       pose.opacity<0 || pose.opacity>1 || pose.flip_y>1 || !std::isfinite(pose.hue_degrees) ||
       pose.hue_degrees<0 || pose.hue_degrees>359 || !shadow::valid(pose.shadow))return Result::invalid;
    }
    if(t.samples!=1 || t.unsupported || (t.depth!=Depth::ordinary && t.depth!=Depth::n64_image))return Result::unsupported;
    if(t.depth==Depth::n64_image && (!t.n64_depth_texture || t.n64_depth_texture==p->textures[0] ||
        t.n64_depth_texture==p->textures[2]))return Result::invalid;
    for(uint32_t index=0;index<count;++index) {
      const auto& pose=poses[index];
      for(uint32_t i=0;i<pose.count;i+=3) {
        const auto material=pose.vertices[i].material;
        if(uint32_t(material)>uint32_t(Material::modulate_rgba))return Result::unsupported;
        for(uint32_t j=0;j<3;++j) {
            const auto& v=pose.vertices[i+j];
            if(!finite(v.position,3)||!finite(v.normal,3)||!finite(v.color,3)||!finite(v.uv,2))return Result::invalid;
            if(v.material!=material)return Result::invalid;
        }
      }
    }
    const auto& g=p->g;uint8_t feedback=0;g.GetBooleanv(TRANSFORM_FEEDBACK_ACTIVE,&feedback);
    if(feedback)return Result::state;
    Guard guard(g);
    g.BindFramebuffer(DRAW_FRAMEBUFFER,t.framebuffer);
    if(g.CheckFramebufferStatus(DRAW_FRAMEBUFFER)!=FRAMEBUFFER_COMPLETE)return Result::resources;
    if(!source_format(g,t.depth))return Result::unsupported;
    if(t.depth==Depth::n64_image) {
        if(!g.IsTexture(t.n64_depth_texture))return Result::invalid;
        g.ActiveTexture(TEXTURE0);g.BindTexture(TEXTURE_2D,t.n64_depth_texture);
        int32_t w=0,h=0,format=0;
        g.GetTexLevelParameteriv(TEXTURE_2D,0,TEXTURE_WIDTH,&w);g.GetTexLevelParameteriv(TEXTURE_2D,0,TEXTURE_HEIGHT,&h);
        g.GetTexLevelParameteriv(TEXTURE_2D,0,TEXTURE_INTERNAL_FORMAT,&format);
        if(w!=int32_t(t.width) || h!=int32_t(t.height) || format!=R32F)return Result::unsupported;
    }
    for(auto cap:enables)g.Disable(cap);
    g.Disablei(SCISSOR_TEST,0);
    g.Disablei(BLEND,0);g.ColorMaski(0,1,1,1,1);g.PolygonMode(FRONT_AND_BACK,FILL);
    g.BindFramebuffer(READ_FRAMEBUFFER,t.framebuffer);
    if(g.CheckFramebufferStatus(READ_FRAMEBUFFER)!=FRAMEBUFFER_COMPLETE)return Result::resources;
    int32_t source_selector=0;g.GetIntegerv(READ_BUFFER,&source_selector);g.ReadBuffer(COLOR_ATTACHMENT0);
    g.BindFramebuffer(DRAW_FRAMEBUFFER,p->fbos[0]);
    g.BlitFramebuffer(0,0,t.width,t.height,0,0,t.width,t.height,COLOR_BUFFER_BIT,NEAREST);
    g.ReadBuffer(source_selector); // restore the GAME FBO's selector, even when not previously bound
    if(t.depth==Depth::ordinary) {
        g.BindFramebuffer(DRAW_FRAMEBUFFER,p->fbos[1]);
        g.BlitFramebuffer(0,0,t.width,t.height,0,0,t.width,t.height,DEPTH_BUFFER_BIT,NEAREST);
    } else g.MemoryBarrier(SHADER_IMAGE_ACCESS_BARRIER_BIT|TEXTURE_FETCH_BARRIER_BIT);
    g.BindFramebuffer(DRAW_FRAMEBUFFER,p->fbos[0]);
    // Original shadow geometry is a decal over THIS frame's clean copy, below
    // every Mario. Attach the just-copied terrain depth so hardware applies the
    // pinned GLideN64 offset and LEQUAL comparison; its depth is never written.
    bool shadows=false;for(uint32_t i=0;i<count;++i)shadows|=poses[i].shadow.count>0;
    if(shadows && t.depth==Depth::ordinary && t.depth_translate>=t.depth_scale &&
            t.depth_translate+t.depth_scale<=1.0f) {
        p->shadow.upload(g,poses,count);
        g.FramebufferRenderbuffer(DRAW_FRAMEBUFFER,DEPTH_STENCIL_ATTACHMENT,RENDERBUFFER,0);
        g.FramebufferTexture2D(DRAW_FRAMEBUFFER,DEPTH_ATTACHMENT,TEXTURE_2D,p->textures[1],0);
        g.ViewportIndexedf(0,float(t.viewport[0]),float(t.viewport[1]),float(t.viewport[2]),float(t.viewport[3]));
        g.ScissorIndexed(0,t.scissor[0],t.scissor[1],t.scissor[2],t.scissor[3]);g.Enablei(SCISSOR_TEST,0);
        g.DepthRangeIndexed(0,t.depth_translate-t.depth_scale,t.depth_translate+t.depth_scale);
        g.DepthMask(0);g.Enable(DEPTH_TEST);g.DepthFunc(LEQUAL);
        g.Enable(POLYGON_OFFSET_FILL);g.PolygonOffset(-3.0f,-3.0f);
        g.Enablei(BLEND,0);g.BlendFuncSeparatei(0,SRC_ALPHA,ONE_MINUS_SRC_ALPHA,ZERO,ONE);
        g.BlendEquationSeparatei(0,FUNC_ADD,FUNC_ADD);
        for(uint32_t i=0;i<count;++i)p->shadow.draw(g,poses[i],i);
        g.Disable(POLYGON_OFFSET_FILL);
        g.FramebufferTexture2D(DRAW_FRAMEBUFFER,DEPTH_ATTACHMENT,TEXTURE_2D,0,0);
        g.FramebufferRenderbuffer(DRAW_FRAMEBUFFER,DEPTH_STENCIL_ATTACHMENT,RENDERBUFFER,p->self_depth);
    }
    // Upload disjoint ranges before issuing draws: avoid overwriting a range
    // still being consumed by the GPU for the preceding Mario.
    g.BindVertexArray(p->vao);g.BindBuffer(ARRAY_BUFFER,p->vbo);
    uint32_t first=0;
    for(uint32_t index=0;index<count;++index) {
        const auto& pose=poses[index];
        g.BufferSubData(ARRAY_BUFFER,ptrdiff_t(first)*sizeof(Vertex),ptrdiff_t(pose.count)*sizeof(Vertex),pose.vertices);
        first+=pose.count;
    }
    first=0;
    for(uint32_t index=0;index<count;++index) {
      const auto& pose=poses[index];
      g.Disablei(SCISSOR_TEST,0);g.Disablei(BLEND,0);g.Disable(STENCIL_TEST);
      g.DepthMask(1);g.StencilMaskSeparate(FRONT_AND_BACK,0xff);
      g.ClearBufferfi(DEPTH_STENCIL,0,1.0f,0);
    if(pose.opacity>0) {
        g.ViewportIndexedf(0,float(t.viewport[0]),float(t.viewport[1]),float(t.viewport[2]),float(t.viewport[3]));
        g.ScissorIndexed(0,t.scissor[0],t.scissor[1],t.scissor[2],t.scissor[3]);g.Enablei(SCISSOR_TEST,0);
        g.DepthRangeIndexed(0,0.0,1.0);g.Enable(DEPTH_TEST);g.DepthFunc(LESS);
        g.UseProgram(p->shader_program);
        g.UniformMatrix4fv(p->uniforms[0],1,0,pose.view_projection);g.Uniform1i(p->uniforms[1],pose.flip_y);
        g.Uniform3fv(p->uniforms[2],1,pose.light_direction);g.Uniform2f(p->uniforms[3],pose.ambient,pose.diffuse);
        g.Uniform2f(p->uniforms[4],t.depth_scale,t.depth_translate);g.Uniform1f(p->uniforms[5],pose.opacity);
        g.Uniform1i(p->uniforms[6],0);g.Uniform1i(p->uniforms[7],1);
        g.Uniform1f(p->uniforms[8],pose.hue_degrees);p->occlusion.set(g,pose.occlusion);
        g.ActiveTexture(TEXTURE0);g.BindTexture(TEXTURE_2D,p->textures[2]);g.BindSampler(0,0);
        g.ActiveTexture(TEXTURE0+1);g.BindTexture(TEXTURE_2D,t.depth==Depth::ordinary ? p->textures[1] : t.n64_depth_texture);g.BindSampler(1,0);
        // Private nearest-surface prepass, then exactly one color contribution
        // per pixel, including coplanar model seams and overlapping triangles.
        g.ColorMaski(0,0,0,0,0);g.DrawArrays(TRIANGLES,first,pose.count);
        g.ColorMaski(0,1,1,1,1);g.DepthMask(0);g.DepthFunc(EQUAL);
        g.Enable(STENCIL_TEST);g.StencilFuncSeparate(FRONT_AND_BACK,EQUAL,0,0xff);
        g.StencilOpSeparate(FRONT_AND_BACK,KEEP,KEEP,INCR);
        g.Enablei(BLEND,0);g.BlendFuncSeparatei(0,SRC_ALPHA,ONE_MINUS_SRC_ALPHA,ZERO,ONE);
        g.BlendEquationSeparatei(0,FUNC_ADD,FUNC_ADD);g.DrawArrays(TRIANGLES,first,pose.count);
    }
      first+=pose.count;
    }
    p->target=t;p->begun=true;return Result::ok;
}

Result begin_hud(Instance* p,uint64_t occurrence) {
    if(!p || !p->begun || p->finalized || p->hud || p->target.occurrence!=occurrence) {abort(p);return Result::invalid;}
    const auto& g=p->g;int32_t fbo=0,buffer=0;uint8_t feedback=0;
    g.GetIntegerv(DRAW_FRAMEBUFFER_BINDING,&fbo);g.GetIntegerv(DRAW_BUFFER0,&buffer);
    g.GetBooleanv(TRANSFORM_FEEDBACK_ACTIVE,&feedback);
    if(uint32_t(fbo)!=p->target.framebuffer || uint32_t(buffer)!=COLOR_ATTACHMENT0 || feedback ||
       g.IsEnabled(DEPTH_TEST) || g.IsEnabled(STENCIL_TEST) || g.IsEnabled(RASTERIZER_DISCARD)) {
        abort(p);return Result::state;
    }
    p->hud_draw_fbo=fbo;p->hud=true;g.BindFramebuffer(DRAW_FRAMEBUFFER,p->fbos[0]);return Result::ok;
}
Result finalize(Instance* p,uint64_t occurrence) {
    if(!p || !p->begun || p->hud || p->target.occurrence!=occurrence) {abort(p);return Result::invalid;}
    p->finalized=true;return Result::ok;
}
Display display(const Instance* p) {
    if(!p || !p->finalized || p->hud)return {};
    return {p->target.occurrence,p->generation,p->fbos[0],p->textures[0],p->width,p->height};
}
void destroy(Instance* p) {
    if(!p)return;abort(p);const auto& g=p->g;
    p->shadow.destroy(g);
    g.DeleteTextures(3,p->textures);g.DeleteFramebuffers(2,p->fbos);g.DeleteRenderbuffers(1,&p->self_depth);
    g.DeleteVertexArrays(1,&p->vao);g.DeleteBuffers(1,&p->vbo);if(p->shader_program)g.DeleteProgram(p->shader_program);delete p;
}
void abandon(Instance* p) {delete p;}
}
