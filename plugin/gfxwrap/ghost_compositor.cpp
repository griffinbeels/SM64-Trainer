#include "ghost_compositor_gl.h"
#include "ghost_shadow_gl.h"
#include "ghost_particle_gl.h"
#include "ghost_annotation_gl.h"
#include <algorithm>
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
uniform int bodyEnd;
out vec3 vertexColor;
out vec2 texCoord;
out float illumination;
out vec4 occlusionClip;
flat out float mode;
flat out int marioBody;
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
    marioBody=gl_VertexID<bodyEnd ? 1 : 0;
})";
constexpr const char* fragment_shader=R"(
layout(location=0) out vec4 outputColor;
uniform sampler2D atlas;
uniform sampler2D terrain;
uniform vec2 depthMapping;
uniform float opacity;
uniform float hueDegrees;
uniform sampler2D layerColor;
uniform sampler2D layerDepth;
uniform int layerEnabled;
uniform float bodyAlpha;
uniform int noiseSeed;
uniform int noiseAlpha;
uniform float noiseScale;
in vec3 vertexColor;
in vec2 texCoord;
in float illumination;
in vec4 occlusionClip;
flat in float mode;
flat in int marioBody;
// G_AC_DITHER compares the combiner alpha to noise at N64 pixel scale. The
// live renderer's RNG is not recorded; this byte hash gives the same noise
// coverage class, stable for a pose and identical in both depth/color passes.
float bodyNoise() {
    ivec2 pixel=ivec2(floor(gl_FragCoord.xy/noiseScale));
    uint h=uint(pixel.x)*0x8da6b343u ^ uint(pixel.y)*0xd8163841u ^ uint(noiseSeed);
    h^=h>>16;h*=0x7feb352du;h^=h>>15;h*=0x846ca68bu;h^=h>>16;
    return float(h&255u)/255.0;
}
// The RDP's tile rule on one axis: clamp to the tile's size, then wrap by its
// mask (a power-of-two AND, which also wraps negative texels), inside the cell.
int tileAxis(float coordinate,uint clamped,uint mask,uint size) {
    int texel=int(floor(coordinate));
    if (clamped!=0u) texel=clamp(texel,0,(1<<size)-1);
    if (mask!=0u) texel&=(1<<mask)-1;
    return clamp(texel,0,63);
}
vec4 tiledTexel(uint tile) {
    ivec2 atlasSize=textureSize(atlas,0);
    int cell=int(tile&63u);
    int s=tileAxis(texCoord.x*float(atlasSize.x)-float(cell*64),(tile>>6)&1u,(tile>>7)&7u,(tile>>10)&7u);
    int t=tileAxis(texCoord.y*float(atlasSize.y),(tile>>13)&1u,(tile>>14)&7u,(tile>>17)&7u);
    return texelFetch(atlas,ivec2(cell*64+s,t),0);
}
void main() {
    float depth=clamp((gl_FragCoord.z*2.0-1.0)*depthMapping.x+depthMapping.y,0.0,1.0);
    float scene=texelFetch(terrain,ivec2(gl_FragCoord.xy),0).r;
    if (depth>=scene && scene<1.0) discard;
    gl_FragDepth=depth;
    vec4 material=vec4(clamp(vertexColor*illumination,0.0,1.0),1.0);
    // The word is an exact integer below 2^24 (flat, never interpolated). Not
    // +0.5: from 2^23, where every tiled word lies, floats step by 1 and the
    // sum rounds an odd word up, turning alpha-mix into a cut-out decal.
    uint word=uint(mode),kind=word&7u,tile=word>>3;
    if (kind!=0u) {
        vec4 textureColor=tile!=0u ? tiledTexel(tile) : texture(atlas,texCoord);
        if (kind==1u) material.rgb=mix(material.rgb,textureColor.rgb,textureColor.a);
        else if (kind==2u) {
            if (textureColor.a==0.0) discard;
            material=textureColor;
        }
        else if (kind==3u) material.rgb=textureColor.rgb;
        else if (kind==4u) material.rgb*=textureColor.rgb;
        else {
            if (textureColor.a==0.0) discard;
            material*=textureColor;
        }
    }
    if(marioBody!=0) {
        material.a*=bodyAlpha;
        if(noiseAlpha!=0 && material.a<bodyNoise()) discard;
    }
    vec3 rgb=material.rgb;
    if(hueDegrees!=0.0) {
        float c=cos(radians(hueDegrees)),s=sin(radians(hueDegrees));
        rgb=vec3(dot(rgb,vec3(.213+.787*c-.213*s,.715-.715*c-.715*s,.072-.072*c+.928*s)),
                 dot(rgb,vec3(.213-.213*c+.143*s,.715+.285*c+.140*s,.072-.072*c-.283*s)),
                 dot(rgb,vec3(.213-.213*c-.787*s,.715-.715*c+.715*s,.072+.928*c+.072*s)));
    }
    // Behind the game's translucent world layer (a water surface seen from
    // either side), take the same blend the game gave the pixels behind it:
    // its premultiplied color plus what it lets through.
    if(layerEnabled!=0 && depth>texelFetch(layerDepth,ivec2(gl_FragCoord.xy),0).r) {
        vec4 surface=texelFetch(layerColor,ivec2(gl_FragCoord.xy),0);
        rgb=surface.rgb+clamp(rgb,0.0,1.0)*surface.a;
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
    Dispatch raw, g; // g: raw with every driver query metered (GC_GL_QUERIES)
    uint64_t generation;
    uint32_t width=0,height=0,capacity=0;
    uint32_t textures[5]={}; // display color, copied ordinary depth, atlas, layer color, layer depth
    uint32_t fbos[4]={}; // display/self depth; copied terrain depth; layer color; layer depth
    uint32_t self_depth=0,vao=0,vbo=0,shader_program=0;
    int32_t uniforms[17]={};
    OcclusionUniforms occlusion;
    shadow::Storage shadow;
    particle::Storage particles;
    annotation::Storage annotations;
    struct Layer {
        bool open=false,broken=false,active=false;
        LayerPass pass=LayerPass::color;
        uint32_t source=0;
        // The game's state this mirror replaced, restored exactly at end_layer.
        int32_t draw_fbo=0,blend[6]={};
        uint8_t color_mask[4]={},depth_mask=0;
    } layer;
    LayerStats layer_stats{};
    Target target{};
    bool ready=false,begun=false,finalized=false,hud=false;
    int32_t hud_draw_fbo=0,target_draw_buffer=0;
    QueryMeter queries{};
    Cost batch{}; // the last begin_batch's stages
    Frame stable{}; // prepare's record of what a frame touches and the renderer never changes
    /* What was verified about one renderer framebuffer, reused while the
     * caller's name for its attachments holds (Target::attachments). */
    struct Facts {
        uint64_t attachments=0;
        uint32_t framebuffer=0,width=0,height=0,n64_texture=0;
        Depth depth{};
        Result result=Result::invalid;
        int32_t read_buffer=0,draw_buffer=0;
    } facts[4]{},unnamed{};
    uint32_t next_facts=0;
    Instance(const Dispatch& dispatch,uint64_t serial):raw(dispatch),g(metered(dispatch)),generation(serial) {}
};

namespace {
using meter::clock;
uint32_t micros(clock::time_point start,clock::time_point end=clock::now()) {
    const auto us=std::chrono::duration_cast<std::chrono::microseconds>(end-start).count();
    return us<0 ? 0u : us>INT32_MAX ? uint32_t(INT32_MAX) : uint32_t(us);
}
bool known_cap(const Known* known,uint32_t cap,uint8_t* value) {
    if(!known || !(known->caps&cap))return false;
    *value=(known->enabled&cap) ? 1 : 0;return true;
}
Result n64_depth(const Dispatch& g,const Target& t) {
    if(!g.IsTexture(t.n64_depth_texture))return Result::invalid;
    // Asked through the compositor's own unit; the frame restores its binding.
    int32_t active=0;g.GetIntegerv(ACTIVE_TEXTURE,&active);
    g.ActiveTexture(TEXTURE0+unit_base+1);g.BindTexture(TEXTURE_2D,t.n64_depth_texture);
    int32_t w=0,h=0,format=0;
    g.GetTexLevelParameteriv(TEXTURE_2D,0,TEXTURE_WIDTH,&w);g.GetTexLevelParameteriv(TEXTURE_2D,0,TEXTURE_HEIGHT,&h);
    g.GetTexLevelParameteriv(TEXTURE_2D,0,TEXTURE_INTERNAL_FORMAT,&format);
    g.ActiveTexture(uint32_t(active));
    return w!=int32_t(t.width) || h!=int32_t(t.height) || format!=R32F ? Result::unsupported : Result::ok;
}
/* The framebuffer's facts, asked once per name for its attachments. Asking
 * leaves t.framebuffer bound for drawing and reading; frames restore both. */
const Instance::Facts& verify(Instance* p,const Target& t) {
    const uint32_t n64=t.depth==Depth::n64_image ? t.n64_depth_texture : 0;
    if(t.attachments)for(const auto& f:p->facts)
        if(f.attachments==t.attachments && f.framebuffer==t.framebuffer && f.width==t.width &&
           f.height==t.height && f.depth==t.depth && f.n64_texture==n64)return f;
    auto& f=t.attachments ? p->facts[p->next_facts++%std::size(p->facts)] : p->unnamed;
    f={};f.attachments=t.attachments;f.framebuffer=t.framebuffer;f.width=t.width;f.height=t.height;
    f.depth=t.depth;f.n64_texture=n64;
    const auto& g=p->g;
    g.BindFramebuffer(DRAW_FRAMEBUFFER,t.framebuffer);
    if(g.CheckFramebufferStatus(DRAW_FRAMEBUFFER)!=FRAMEBUFFER_COMPLETE)f.result=Result::resources;
    else if(!source_format(g,t.depth))f.result=Result::unsupported;
    else f.result=n64 ? n64_depth(g,t) : Result::ok;
    g.GetIntegerv(DRAW_BUFFER0,&f.draw_buffer);
    g.BindFramebuffer(READ_FRAMEBUFFER,t.framebuffer);g.GetIntegerv(READ_BUFFER,&f.read_buffer);
    return f;
}
}

Instance* create(const Dispatch& g,uint64_t generation) {
    if(!generation)return nullptr;
#define GC_CHECK(result,name,args) if(!g.name)return nullptr;
    GC_GL_FUNCTIONS(GC_CHECK)
#undef GC_CHECK
    auto* p=new(std::nothrow) Instance(g,generation);if(!p)return nullptr;
    int32_t major=0,minor=0,units=0;
    {
        Metering metering(p->raw,p->queries);
        p->g.GetIntegerv(MAJOR_VERSION,&major);p->g.GetIntegerv(MINOR_VERSION,&minor);
        p->g.GetIntegerv(MAX_COMBINED_TEXTURE_IMAGE_UNITS,&units);
    }
    if(major<4 || (major==4 && minor<3) || units<int32_t(unit_base+unit_count)) {delete p;return nullptr;}
    return p;
}

void end_hud(Instance* p) {
    if(p && p->hud) {p->g.BindFramebuffer(DRAW_FRAMEBUFFER,p->hud_draw_fbo);p->hud=false;}
}
void abort(Instance* p) {
    if(!p)return;end_hud(p);p->begun=false;p->finalized=false;p->target={};
}

namespace {
Result allocate(Instance* p,uint32_t width,uint32_t height,uint32_t capacity,
                const uint8_t* atlas,uint32_t aw,uint32_t ah) {
    const auto& g=p->g;
    if(!p->shader_program) {
        p->shader_program=program(g);if(!p->shader_program)return Result::resources;
        constexpr const char* names[] = {"viewProjection","flipY","lightDirection","lighting","depthMapping","opacity","atlas","terrain","hueDegrees",
            "layerColor","layerDepth","layerEnabled","bodyEnd","bodyAlpha","noiseSeed","noiseAlpha","noiseScale"};
        for(size_t i=0;i<std::size(names);++i) {p->uniforms[i]=g.GetUniformLocation(p->shader_program,names[i]);if(p->uniforms[i]<0)return Result::resources;}
        if(!p->occlusion.find(g,p->shader_program))return Result::resources;
        g.GenTextures(5,p->textures);g.GenFramebuffers(4,p->fbos);
        g.GenRenderbuffers(1,&p->self_depth);g.GenVertexArrays(1,&p->vao);g.GenBuffers(1,&p->vbo);
    }
    for(auto id:p->textures)if(!id)return Result::resources;
    for(auto id:p->fbos)if(!id)return Result::resources;
    if(!p->self_depth || !p->vao || !p->vbo)return Result::resources;
    p->layer=Instance::Layer{};
    g.BindBuffer(PIXEL_UNPACK_BUFFER,0);g.PixelStorei(UNPACK_ALIGNMENT,1);
    g.PixelStorei(UNPACK_ROW_LENGTH,0);g.PixelStorei(UNPACK_SKIP_ROWS,0);g.PixelStorei(UNPACK_SKIP_PIXELS,0);
    if(!p->shadow.prepare(g,p->shadow.program?p->shadow.program:
            program(g,shadow::vertex_shader,shadow::fragment_shader)))return Result::resources;
    g.ActiveTexture(TEXTURE0);
    texture(g,p->textures[0],RGBA8,RGBA,UNSIGNED_BYTE,width,height,nullptr);
    texture(g,p->textures[1],DEPTH_COMPONENT24,DEPTH_COMPONENT,UNSIGNED_INT,width,height,nullptr);
    texture(g,p->textures[2],RGBA8,RGBA,UNSIGNED_BYTE,aw,ah,atlas);
    texture(g,p->textures[3],RGBA8,RGBA,UNSIGNED_BYTE,width,height,nullptr);
    texture(g,p->textures[4],DEPTH_COMPONENT24,DEPTH_COMPONENT,UNSIGNED_INT,width,height,nullptr);
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
    // Layer color tests against the same copied scene depth; layer depth
    // keeps the nearest translucent surface (initialized to that copy).
    g.BindFramebuffer(FRAMEBUFFER,p->fbos[2]);
    g.FramebufferTexture2D(FRAMEBUFFER,COLOR_ATTACHMENT0,TEXTURE_2D,p->textures[3],0);
    g.FramebufferTexture2D(FRAMEBUFFER,DEPTH_ATTACHMENT,TEXTURE_2D,p->textures[1],0);
    g.DrawBuffer(COLOR_ATTACHMENT0);g.ReadBuffer(COLOR_ATTACHMENT0);
    if(g.CheckFramebufferStatus(FRAMEBUFFER)!=FRAMEBUFFER_COMPLETE)return Result::resources;
    g.BindFramebuffer(FRAMEBUFFER,p->fbos[3]);
    g.FramebufferTexture2D(FRAMEBUFFER,DEPTH_ATTACHMENT,TEXTURE_2D,p->textures[4],0);
    g.DrawBuffer(ZERO);g.ReadBuffer(ZERO);
    if(g.CheckFramebufferStatus(FRAMEBUFFER)!=FRAMEBUFFER_COMPLETE)return Result::resources;
    g.BindVertexArray(p->vao);g.BindBuffer(ARRAY_BUFFER,p->vbo);
    g.BufferData(ARRAY_BUFFER,ptrdiff_t(capacity)*sizeof(Vertex),nullptr,DYNAMIC_DRAW);
    constexpr int32_t counts[]={3,3,3,2,1};
    constexpr size_t offsets[]={offsetof(Vertex,position),offsetof(Vertex,normal),offsetof(Vertex,color),offsetof(Vertex,uv),offsetof(Vertex,material)};
    for(uint32_t i=0;i<5;++i) {g.EnableVertexAttribArray(i);g.VertexAttribPointer(i,counts[i],i==4 ? UNSIGNED_INT : FLOAT,0,sizeof(Vertex),reinterpret_cast<const void*>(offsets[i]));}
    // The samplers read the compositor's own units, set once.
    g.UseProgram(p->shader_program);
    g.Uniform1i(p->uniforms[6],int32_t(unit_base));g.Uniform1i(p->uniforms[7],int32_t(unit_base+1));
    g.Uniform1i(p->uniforms[9],int32_t(unit_base+2));g.Uniform1i(p->uniforms[10],int32_t(unit_base+3));
    p->width=width;p->height=height;p->capacity=capacity;return Result::ok;
}
}

Result prepare(Instance* p,uint32_t width,uint32_t height,uint32_t capacity,
               const uint8_t* atlas,uint32_t aw,uint32_t ah) {
    if(!p)return Result::invalid;
    abort(p);p->ready=false;
    if(!width || !height || width>8192 || height>8192 || !capacity || capacity>64u*30000u ||
       !atlas || !aw || !ah || aw>8192 || ah>8192)return Result::invalid;
    Metering metering(p->raw,p->queries);
    Result result=Result::ok;
    {Guard guard(p->g);result=allocate(p,width,height,capacity,atlas,aw,ah);}
    if(result!=Result::ok)return result;
    for(auto& f:p->facts)f={};
    p->stable.capture(p->g);p->ready=true;return Result::ok;
}
void resync(Instance* p) {
    if(!p)return;
    Metering metering(p->raw,p->queries);p->stable.capture(p->g);
}
Cost take_cost(Instance* p) {
    if(!p)return {};
    Cost cost=p->batch;cost.queries=p->queries.count;cost.query_micros=uint32_t(p->queries.nanos/1000);
    p->queries={};p->batch={};return cost;
}

Result prepare_particles(Instance* p,const uint8_t* rgba,uint32_t width,uint32_t height) {
    if(!p || !p->ready)return Result::invalid;
    abort(p);
    Metering metering(p->raw,p->queries);
    const auto& g=p->g;Guard guard(g);
    return p->particles.prepare(g,p->particles.program?p->particles.program:
        program(g,particle::vertex_shader,particle::fragment_shader),rgba,width,height) ? Result::ok : Result::resources;
}
Result prepare_annotations(Instance* p,const uint8_t* rgba,uint32_t width,uint32_t height) {
    if(!p || !p->ready)return Result::invalid;
    abort(p);Metering metering(p->raw,p->queries);const auto& g=p->g;Guard guard(g);
    return p->annotations.prepare(g,p->annotations.program?p->annotations.program:
        program(g,annotation::vertex_shader,annotation::fragment_shader),rgba,width,height)?Result::ok:Result::resources;
}

Result begin(Instance* p,const Target& t,const Pose& pose,const Known* known) {
    return begin_batch(p,t,&pose,1,known);
}

Result begin_batch(Instance* p,const Target& t,const Pose* poses,uint32_t count,const Known* known) {
    if(!p)return Result::invalid;
    abort(p);
    const auto start=clock::now();p->batch={};
    if(!poses || !count || count>max_poses)return Result::invalid;
    if(!p->ready || t.generation!=p->generation || !t.occurrence || !t.framebuffer ||
       t.framebuffer==p->fbos[0] || t.framebuffer==p->fbos[1] || t.framebuffer==p->fbos[2] || t.framebuffer==p->fbos[3] ||
       t.width!=p->width || t.height!=p->height ||
       !rectangle(t.viewport,t.width,t.height) || !rectangle(t.scissor,t.width,t.height) ||
       !std::isfinite(t.depth_scale) || !std::isfinite(t.depth_translate) || t.depth_scale<=0)return Result::invalid;
    uint64_t total_vertices=0;
    for(uint32_t index=0;index<count;++index) {
      const auto& pose=poses[index];total_vertices+=pose.count;
      if(!pose.vertices || !pose.count || total_vertices>p->capacity || pose.count%3 ||
       !finite(pose.view_projection,16) || !finite(pose.light_direction,3) ||
       !std::isfinite(pose.ambient) || !std::isfinite(pose.diffuse) || !std::isfinite(pose.opacity) ||
       pose.opacity<0 || pose.opacity>1 || pose.flip_y>1 || !std::isfinite(pose.hue_degrees) ||
       pose.hue_degrees<0 || pose.hue_degrees>359 || !shadow::valid(pose.shadow) ||
       !particle::valid(pose.particles) || pose.body_count>pose.count || pose.body_count%3 ||
       !std::isfinite(pose.occlusion.minimum) || pose.occlusion.minimum<0 || pose.occlusion.minimum>1 ||
       !std::isfinite(pose.body_alpha) || pose.body_alpha<0 || pose.body_alpha>1 ||
       !std::isfinite(pose.noise_scale) || pose.noise_scale<=0 || !ghost_annotation::valid(pose.annotation))return Result::invalid;
    }
    if(t.samples!=1 || t.unsupported || (t.depth!=Depth::ordinary && t.depth!=Depth::n64_image))return Result::unsupported;
    if(t.depth==Depth::n64_image && (!t.n64_depth_texture || t.n64_depth_texture==p->textures[0] ||
        t.n64_depth_texture==p->textures[2]))return Result::invalid;
    for(uint32_t index=0;index<count;++index) {
      const auto& pose=poses[index];
      for(uint32_t i=0;i<pose.count;i+=3) {
        const auto material=pose.vertices[i].material;
        const uint32_t tile_word=uint32_t(material)>>tile::shift;
        if(uint32_t(mode(material))>uint32_t(Material::modulate_rgba) || tile_word>=(tile::present<<1)
                || (tile_word && (!(tile_word&tile::present) || ((tile_word>>tile::s_mask)&tile::field)>6
                    || ((tile_word>>tile::s_size)&tile::field)>6 || ((tile_word>>tile::t_mask)&tile::field)>6
                    || ((tile_word>>tile::t_size)&tile::field)>6)))return Result::unsupported;
        for(uint32_t j=0;j<3;++j) {
            const auto& v=pose.vertices[i+j];
            if(!finite(v.position,3)||!finite(v.normal,3)||!finite(v.color,3)||!finite(v.uv,2))return Result::invalid;
            if(v.material!=material)return Result::invalid;
        }
      }
    }
    const auto& g=p->g;Metering metering(p->raw,p->queries);
    // The renderer's state, from what it and prepare already know plus the
    // residue asked of the driver; every return below puts it back.
    const auto asked=clock::now();p->batch.validate_micros=micros(start,asked);
    Frame pre{};pre.assume(p->stable,known,g);
    if(pre.feedback)return Result::state; // nothing changed yet
    struct Close {
        Instance* p;const Frame& pre;clock::time_point start;
        ~Close() {
            const auto restoring=clock::now();pre.restore(p->g);
            p->batch.restore_micros=micros(restoring);p->batch.batch_micros=micros(start);
        }
    } close{p,pre,start};
    const auto& facts=verify(p,t);
    const auto stated=clock::now();p->batch.state_micros=micros(asked,stated);
    if(facts.result!=Result::ok)return facts.result;
    p->target_draw_buffer=facts.draw_buffer;
    for(auto cap:enables)g.Disable(cap);
    g.Disablei(SCISSOR_TEST,0);
    g.Disablei(BLEND,0);g.ColorMaski(0,1,1,1,1);g.PolygonMode(FRONT_AND_BACK,FILL);
    const bool layered=p->layer.open && !p->layer.broken && !p->layer.active &&
        p->layer.source==t.framebuffer && t.depth==Depth::ordinary;
    g.BindFramebuffer(READ_FRAMEBUFFER,t.framebuffer);
    // The game framebuffer's own read selector, verified once: usually
    // already COLOR_ATTACHMENT0, and put back when it is not.
    const bool select=facts.read_buffer!=int32_t(COLOR_ATTACHMENT0);
    if(select)g.ReadBuffer(COLOR_ATTACHMENT0);
    g.BindFramebuffer(DRAW_FRAMEBUFFER,p->fbos[0]);
    g.BlitFramebuffer(0,0,t.width,t.height,0,0,t.width,t.height,COLOR_BUFFER_BIT,NEAREST);
    if(select)g.ReadBuffer(uint32_t(facts.read_buffer));
    if(t.depth==Depth::ordinary) {
        g.BindFramebuffer(DRAW_FRAMEBUFFER,p->fbos[1]);
        g.BlitFramebuffer(0,0,t.width,t.height,0,0,t.width,t.height,DEPTH_BUFFER_BIT,NEAREST);
    } else g.MemoryBarrier(SHADER_IMAGE_ACCESS_BARRIER_BIT|TEXTURE_FETCH_BARRIER_BIT);
    g.BindFramebuffer(DRAW_FRAMEBUFFER,p->fbos[0]);
    const auto copied=clock::now();p->batch.copy_micros=micros(stated,copied);
    // The compositor's units read no sampler object; prepare's record says
    // whether one was there to put back.
    if(pre.samplers[0] || pre.samplers[1] || pre.samplers[2] || pre.samplers[3]) {
        const uint32_t none[unit_count]={};
        if(multibind(g))g.BindSamplers(unit_base,unit_count,none);
        else for(uint32_t i=0;i<unit_count;++i)g.BindSampler(unit_base+i,0);
    }
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
    // still being consumed by the GPU for the preceding Mario. Uploads go
    // through the copy-write target, leaving the renderer's array buffer alone.
    g.BindBuffer(COPY_WRITE_BUFFER,p->vbo);
    uint32_t first=0;
    for(uint32_t index=0;index<count;++index) {
        const auto& pose=poses[index];
        g.BufferSubData(COPY_WRITE_BUFFER,ptrdiff_t(first)*sizeof(Vertex),ptrdiff_t(pose.count)*sizeof(Vertex),pose.vertices);
        first+=pose.count;
    }
    p->particles.upload(g,poses,count);
    p->annotations.upload(g,poses,count,t);
    g.BindVertexArray(p->vao);
    first=0;
    for(uint32_t index=0;index<count;++index) {
      const auto& pose=poses[index];const auto drawing=clock::now();
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
        g.Uniform1f(p->uniforms[8],pose.hue_degrees);g.Uniform1i(p->uniforms[11],layered ? 1 : 0);
        g.Uniform1i(p->uniforms[12],int32_t(first+pose.body_count));g.Uniform1f(p->uniforms[13],pose.body_alpha);
        int32_t seed=0;std::memcpy(&seed,&pose.noise_seed,sizeof(seed));
        g.Uniform1i(p->uniforms[14],seed);g.Uniform1i(p->uniforms[15],pose.noise_alpha ? 1 : 0);
        g.Uniform1f(p->uniforms[16],pose.noise_scale);
        p->occlusion.set(g,pose.occlusion);
        const uint32_t units[unit_count]={p->textures[2],t.depth==Depth::ordinary ? p->textures[1] : t.n64_depth_texture,
            p->textures[3],p->textures[4]};
        bind_units(g,0,unit_count,units);
        // Private nearest-surface prepass, then exactly one color contribution
        // per pixel, including coplanar model seams and overlapping triangles.
        g.ColorMaski(0,0,0,0,0);g.DrawArrays(TRIANGLES,first,pose.count);
        g.ColorMaski(0,1,1,1,1);g.DepthMask(0);g.DepthFunc(EQUAL);
        g.Enable(STENCIL_TEST);g.StencilFuncSeparate(FRONT_AND_BACK,EQUAL,0,0xff);
        g.StencilOpSeparate(FRONT_AND_BACK,KEEP,KEEP,INCR);
        g.Enablei(BLEND,0);g.BlendFuncSeparatei(0,SRC_ALPHA,ONE_MINUS_SRC_ALPHA,ZERO,ONE);
        g.BlendEquationSeparatei(0,FUNC_ADD,FUNC_ADD);g.DrawArrays(TRIANGLES,first,pose.count);
        // This ghost's particles, against its body depth still in self-depth.
        p->particles.draw(g,pose,index,t,layered);
        p->annotations.draw(g,index,t,layered);
        g.BindVertexArray(p->vao);
    }
      first+=pose.count;
      p->batch.draw_max_micros=std::max(p->batch.draw_max_micros,micros(drawing));
    }
    p->batch.draw_micros=micros(copied);p->batch.ghosts=count;
    if(layered)++p->layer_stats.applied;
    p->target=t;p->begun=true;return Result::ok;
}

Result begin_layer(Instance* p,const Target& t,LayerPass pass,const Known* known) {
    if(!p || !p->ready || p->layer.active || uint32_t(pass)>uint32_t(LayerPass::depth))return Result::invalid;
    if(p->layer.broken)return Result::state;
    if(t.generation!=p->generation || !t.framebuffer || t.width!=p->width || t.height!=p->height ||
       t.framebuffer==p->fbos[0] || t.framebuffer==p->fbos[1] || t.framebuffer==p->fbos[2] || t.framebuffer==p->fbos[3])
        return Result::invalid;
    if(t.samples!=1 || t.unsupported || t.depth!=Depth::ordinary)return Result::unsupported;
    if(p->layer.open && p->layer.source!=t.framebuffer)return Result::invalid;
    const auto& g=p->g;Metering metering(p->raw,p->queries);
    int32_t draw=0;
    if(known && (known->fields&Known::draw_framebuffer))draw=int32_t(known->draw);
    else g.GetIntegerv(DRAW_FRAMEBUFFER_BINDING,&draw);
    if(uint32_t(draw)!=t.framebuffer)return Result::state;
    // Mirror only a draw that composites over what lies behind it and leaves
    // depth alone: the game's own dst = 1 - src alpha with a source factor
    // that never reads the destination. Anything else stays out of the layer.
    // A caller that knows its state leaves the blend equations and the color
    // mask as prepare recorded them; one that does not has them asked.
    int32_t blend[6]={0,0,0,0,p->stable.blend[4],p->stable.blend[5]};uint8_t blending=0,depth_mask=1,depth_test=0;
    uint8_t mask[4]={p->stable.color_mask[0],p->stable.color_mask[1],p->stable.color_mask[2],p->stable.color_mask[3]};
    if(!known_cap(known,Known::blend,&blending))g.GetBooleani_v(BLEND,0,&blending);
    if(known && (known->fields&Known::depth))depth_mask=known->depth_write;
    else g.GetBooleanv(DEPTH_WRITEMASK,&depth_mask);
    if(known && (known->fields&Known::blend_factors))for(size_t i=0;i<4;++i)blend[i]=int32_t(known->blend_factor[i]);
    else for(size_t i=0;i<(known ? 4u : 6u);++i)g.GetIntegeri_v(blend_queries[i],0,&blend[i]);
    if(!known)g.GetBooleani_v(COLOR_WRITEMASK,0,mask);
    if(!known_cap(known,Known::depth_test,&depth_test))depth_test=g.IsEnabled(DEPTH_TEST);
    if(!blending || depth_mask || !depth_test || blend[4]!=int32_t(FUNC_ADD) ||
       blend[1]!=int32_t(ONE_MINUS_SRC_ALPHA) || (blend[0]!=int32_t(ONE) && blend[0]!=int32_t(SRC_ALPHA))) {
        if(pass==LayerPass::color)++p->layer_stats.skipped_blend;
        return Result::unsupported;
    }
    if(!p->layer.open) {
        // Scissor applies to blits and clears; the game's value is restored below.
        uint8_t scissor=0;int32_t read=0;
        if(!known_cap(known,Known::scissor_test,&scissor))g.GetBooleani_v(SCISSOR_TEST,0,&scissor);
        if(known && (known->fields&Known::read_framebuffer))read=int32_t(known->read);
        else g.GetIntegerv(READ_FRAMEBUFFER_BINDING,&read);
        const auto& facts=verify(p,t);
        if(facts.result!=Result::ok) {g.BindFramebuffer(READ_FRAMEBUFFER,uint32_t(read));return Result::unsupported;}
        g.Disablei(SCISSOR_TEST,0);
        g.BindFramebuffer(READ_FRAMEBUFFER,t.framebuffer);
        for(uint32_t fbo:{p->fbos[1],p->fbos[3]}) {
            g.BindFramebuffer(DRAW_FRAMEBUFFER,fbo);
            g.BlitFramebuffer(0,0,t.width,t.height,0,0,t.width,t.height,DEPTH_BUFFER_BIT,NEAREST);
        }
        g.BindFramebuffer(DRAW_FRAMEBUFFER,p->fbos[2]);g.ColorMaski(0,1,1,1,1);
        const float clear[4]={0,0,0,1}; // nothing in front yet: no color, everything let through
        g.ClearBufferfv(COLOR,0,clear);
        g.ColorMaski(0,mask[0],mask[1],mask[2],mask[3]);
        g.BindFramebuffer(READ_FRAMEBUFFER,uint32_t(read));g.BindFramebuffer(DRAW_FRAMEBUFFER,t.framebuffer);
        if(scissor)g.Enablei(SCISSOR_TEST,0);
        p->layer.open=true;p->layer.source=t.framebuffer;++p->layer_stats.opened;
    }
    auto& l=p->layer;
    l.draw_fbo=draw;std::copy(blend,blend+6,l.blend);l.depth_mask=depth_mask;
    std::copy(mask,mask+4,l.color_mask);
    if(pass==LayerPass::color) {
        g.BindFramebuffer(DRAW_FRAMEBUFFER,p->fbos[2]);g.ColorMaski(0,1,1,1,1);
        g.BlendFuncSeparatei(0,blend[0],blend[1],ZERO,ONE_MINUS_SRC_ALPHA);
        g.BlendEquationSeparatei(0,FUNC_ADD,FUNC_ADD);
    } else {
        g.BindFramebuffer(DRAW_FRAMEBUFFER,p->fbos[3]);g.DepthMask(1);
    }
    l.active=true;l.pass=pass;return Result::ok;
}
void end_layer(Instance* p) {
    if(!p || !p->layer.active)return;
    const auto& g=p->g;const auto& l=p->layer;
    g.BindFramebuffer(DRAW_FRAMEBUFFER,l.draw_fbo);
    g.BlendFuncSeparatei(0,l.blend[0],l.blend[1],l.blend[2],l.blend[3]);
    g.BlendEquationSeparatei(0,l.blend[4],l.blend[5]);
    g.ColorMaski(0,l.color_mask[0],l.color_mask[1],l.color_mask[2],l.color_mask[3]);
    g.DepthMask(l.depth_mask);
    if(l.pass==LayerPass::color)++p->layer_stats.mirrored;
    p->layer.active=false;
}
void clear_layer(Instance* p) {
    if(!p)return;end_layer(p);p->layer.open=p->layer.broken=false;p->layer.source=0;
}
void break_layer(Instance* p) {
    if(p && p->layer.open && !p->layer.broken) {p->layer.broken=true;++p->layer_stats.broken;}
}
LayerStats layer_stats(const Instance* p) {return p ? p->layer_stats : LayerStats{};}

Result begin_hud(Instance* p,uint64_t occurrence,const Known* known) {
    if(!p || !p->begun || p->finalized || p->hud || p->target.occurrence!=occurrence) {abort(p);return Result::invalid;}
    const auto& g=p->g;Metering metering(p->raw,p->queries);
    int32_t fbo=0;uint8_t depth=0;
    if(known && (known->fields&Known::draw_framebuffer))fbo=int32_t(known->draw);
    else g.GetIntegerv(DRAW_FRAMEBUFFER_BINDING,&fbo);
    if(!known_cap(known,Known::depth_test,&depth))depth=g.IsEnabled(DEPTH_TEST);
    // The draw buffer is the target's, verified at begin_batch. Transform
    // feedback, stencil and rasterizer discard are the renderer's stable
    // state, asked only of a caller that knows nothing.
    const auto& s=p->stable;
    uint8_t feedback=s.feedback,stencil=s.enabled[enable_index(STENCIL_TEST)],discard=s.enabled[enable_index(RASTERIZER_DISCARD)];
    if(!known) {g.GetBooleanv(TRANSFORM_FEEDBACK_ACTIVE,&feedback);stencil=g.IsEnabled(STENCIL_TEST);discard=g.IsEnabled(RASTERIZER_DISCARD);}
    if(uint32_t(fbo)!=p->target.framebuffer || uint32_t(p->target_draw_buffer)!=COLOR_ATTACHMENT0 || feedback ||
       depth || stencil || discard) {
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
    p->shadow.destroy(g);p->particles.destroy(g);p->annotations.destroy(g);
    end_layer(p);
    g.DeleteTextures(5,p->textures);g.DeleteFramebuffers(4,p->fbos);g.DeleteRenderbuffers(1,&p->self_depth);
    g.DeleteVertexArrays(1,&p->vao);g.DeleteBuffers(1,&p->vbo);if(p->shader_program)g.DeleteProgram(p->shader_program);delete p;
}
void abandon(Instance* p) {delete p;}
}
