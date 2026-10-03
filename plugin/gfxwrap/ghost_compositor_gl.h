#pragma once
#include "ghost_compositor.h"
#include <array>
#include <chrono>

namespace ghost_compositor::gl {
enum : uint32_t {
    ZERO=0, ONE=1, FRONT=0x404, BACK=0x405, FRONT_AND_BACK=0x408,
    TRIANGLES=4, FLOAT=0x1406, UNSIGNED_BYTE=0x1401, UNSIGNED_INT=0x1405,
    TEXTURE_2D=0xDE1, TEXTURE0=0x84C0, ACTIVE_TEXTURE=0x84E0,
    TEXTURE_BINDING_2D=0x8069, SAMPLER_BINDING=0x8919,
    TEXTURE_WIDTH=0x1000, TEXTURE_HEIGHT=0x1001, TEXTURE_INTERNAL_FORMAT=0x1003, R32F=0x822E,
    TEXTURE_MIN_FILTER=0x2801, TEXTURE_MAG_FILTER=0x2800, TEXTURE_WRAP_S=0x2802,
    TEXTURE_WRAP_T=0x2803, TEXTURE_COMPARE_MODE=0x884C, NEAREST=0x2600,
    CLAMP_TO_EDGE=0x812F, MIRRORED_REPEAT=0x8370, RGBA=0x1908, RGBA8=0x8058, DEPTH_COMPONENT=0x1902,
    DEPTH_COMPONENT24=0x81A6, DEPTH24_STENCIL8=0x88F0,
    READ_FRAMEBUFFER=0x8CA8, DRAW_FRAMEBUFFER=0x8CA9, FRAMEBUFFER=0x8D40,
    READ_FRAMEBUFFER_BINDING=0x8CAA, DRAW_FRAMEBUFFER_BINDING=0x8CA6,
    COLOR_ATTACHMENT0=0x8CE0, DEPTH_ATTACHMENT=0x8D00, DEPTH_STENCIL_ATTACHMENT=0x821A,
    FRAMEBUFFER_COMPLETE=0x8CD5, READ_BUFFER=0xC02, DRAW_BUFFER0=0x8825,
    FRAMEBUFFER_ATTACHMENT_OBJECT_TYPE=0x8CD0, FRAMEBUFFER_ATTACHMENT_DEPTH_SIZE=0x8216,
    FRAMEBUFFER_ATTACHMENT_STENCIL_SIZE=0x8217,
    FRAMEBUFFER_ATTACHMENT_RED_SIZE=0x8212, FRAMEBUFFER_ATTACHMENT_GREEN_SIZE=0x8213,
    FRAMEBUFFER_ATTACHMENT_BLUE_SIZE=0x8214, FRAMEBUFFER_ATTACHMENT_COMPONENT_TYPE=0x8211,
    FRAMEBUFFER_ATTACHMENT_COLOR_ENCODING=0x8210, UNSIGNED_NORMALIZED=0x8C17, LINEAR=0x2601, SAMPLES=0x80A9,
    COLOR_BUFFER_BIT=0x4000, DEPTH_BUFFER_BIT=0x100, RENDERBUFFER=0x8D41,
    RENDERBUFFER_BINDING=0x8CA7, ARRAY_BUFFER=0x8892, ARRAY_BUFFER_BINDING=0x8894,
    VERTEX_ARRAY_BINDING=0x85B5, PIXEL_UNPACK_BUFFER=0x88EC, PIXEL_UNPACK_BUFFER_BINDING=0x88EF,
    UNPACK_ALIGNMENT=0xCF5, UNPACK_ROW_LENGTH=0xCF2, UNPACK_SKIP_ROWS=0xCF3, UNPACK_SKIP_PIXELS=0xCF4,
    DYNAMIC_DRAW=0x88E8, VERTEX_SHADER=0x8B31, FRAGMENT_SHADER=0x8B30,
    COMPILE_STATUS=0x8B81, LINK_STATUS=0x8B82, CURRENT_PROGRAM=0x8B8D,
    VIEWPORT=0xBA2, SCISSOR_BOX=0xC10, DEPTH_RANGE=0xB70, DEPTH_FUNC=0xB74,
    DEPTH_WRITEMASK=0xB72, COLOR_WRITEMASK=0xC23, POLYGON_MODE=0xB40, FILL=0x1B02,
    BLEND=0xBE2, BLEND_SRC_RGB=0x80C9, BLEND_DST_RGB=0x80C8,
    BLEND_SRC_ALPHA=0x80CB, BLEND_DST_ALPHA=0x80CA, BLEND_EQUATION_RGB=0x8009,
    BLEND_EQUATION_ALPHA=0x883D, FUNC_ADD=0x8006, SRC_ALPHA=0x302, ONE_MINUS_SRC_ALPHA=0x303,
    DEPTH_TEST=0xB71, STENCIL_TEST=0xB90, SCISSOR_TEST=0xC11, CULL_FACE=0xB44,
    FRAMEBUFFER_SRGB=0x8DB9, DITHER=0xBD0, COLOR_LOGIC_OP=0xBF2,
    RASTERIZER_DISCARD=0x8C89, DEPTH_CLAMP=0x864F, POLYGON_OFFSET_FILL=0x8037,
    POLYGON_OFFSET_LINE=0x2A02, POLYGON_OFFSET_POINT=0x2A01,
    SAMPLE_ALPHA_TO_COVERAGE=0x809E, SAMPLE_ALPHA_TO_ONE=0x809F,
    SAMPLE_COVERAGE=0x80A0, SAMPLE_MASK=0x8E51, CLIP_DISTANCE0=0x3000,
    STENCIL_FUNC=0xB92, STENCIL_VALUE_MASK=0xB93, STENCIL_FAIL=0xB94,
    STENCIL_PASS_DEPTH_FAIL=0xB95, STENCIL_PASS_DEPTH_PASS=0xB96, STENCIL_REF=0xB97,
    STENCIL_WRITEMASK=0xB98, STENCIL_BACK_FUNC=0x8800, STENCIL_BACK_FAIL=0x8801,
    STENCIL_BACK_PASS_DEPTH_FAIL=0x8802, STENCIL_BACK_PASS_DEPTH_PASS=0x8803,
    STENCIL_BACK_REF=0x8CA3, STENCIL_BACK_VALUE_MASK=0x8CA4, STENCIL_BACK_WRITEMASK=0x8CA5,
    KEEP=0x1E00, REPLACE=0x1E01, INCR=0x1E02, EQUAL=0x202, LESS=0x201, LEQUAL=0x203, NOTEQUAL=0x205, ALWAYS=0x207,
    POLYGON_OFFSET_FACTOR=0x8038, POLYGON_OFFSET_UNITS=0x2A00,
    DEPTH_STENCIL=0x84F9, COLOR=0x1800,TRANSFORM_FEEDBACK_ACTIVE=0x8E24, MAJOR_VERSION=0x821B,
    MINOR_VERSION=0x821C, CONTEXT_PROFILE_MASK=0x9126, CONTEXT_CORE_PROFILE_BIT=1,
    SHADER_IMAGE_ACCESS_BARRIER_BIT=0x20, TEXTURE_FETCH_BARRIER_BIT=8,
    COPY_WRITE_BUFFER=0x8F37, COPY_WRITE_BUFFER_BINDING=0x8F37, MAX_COMBINED_TEXTURE_IMAGE_UNITS=0x8B4D
};
constexpr uint32_t enables[] = {DEPTH_TEST,STENCIL_TEST,CULL_FACE,
    FRAMEBUFFER_SRGB,DITHER,COLOR_LOGIC_OP,RASTERIZER_DISCARD,DEPTH_CLAMP,
    POLYGON_OFFSET_FILL,POLYGON_OFFSET_LINE,POLYGON_OFFSET_POINT,
    SAMPLE_ALPHA_TO_COVERAGE,SAMPLE_ALPHA_TO_ONE,SAMPLE_COVERAGE,SAMPLE_MASK,
    CLIP_DISTANCE0,CLIP_DISTANCE0+1,CLIP_DISTANCE0+2,CLIP_DISTANCE0+3,
    CLIP_DISTANCE0+4,CLIP_DISTANCE0+5,CLIP_DISTANCE0+6,CLIP_DISTANCE0+7};
constexpr uint32_t stencil_queries[2][7] = {
    {STENCIL_FUNC,STENCIL_REF,STENCIL_VALUE_MASK,STENCIL_FAIL,STENCIL_PASS_DEPTH_FAIL,STENCIL_PASS_DEPTH_PASS,STENCIL_WRITEMASK},
    {STENCIL_BACK_FUNC,STENCIL_BACK_REF,STENCIL_BACK_VALUE_MASK,STENCIL_BACK_FAIL,STENCIL_BACK_PASS_DEPTH_FAIL,STENCIL_BACK_PASS_DEPTH_PASS,STENCIL_BACK_WRITEMASK}};
constexpr uint32_t blend_queries[] = {BLEND_SRC_RGB,BLEND_DST_RGB,BLEND_SRC_ALPHA,BLEND_DST_ALPHA,BLEND_EQUATION_RGB,BLEND_EQUATION_ALPHA};
constexpr uint32_t unpack_queries[] = {UNPACK_ALIGNMENT,UNPACK_ROW_LENGTH,UNPACK_SKIP_ROWS,UNPACK_SKIP_PIXELS};

/* Only this module's VAO, texture units 0-3 and draw-buffer index 0 change.
 * In particular, no element-buffer binding on the renderer's VAO is changed. */
struct State {
    int32_t program, vao, array_buffer, unpack_buffer, renderbuffer;
    int32_t read_fbo, draw_fbo, read_buffer, active_texture;
    int32_t textures[4], samplers[4], scissor[4], polygon[2];
    float viewport[4];
    float polygon_offset[2];
    int32_t blend[6], stencil[2][7], depth_func, unpack[4];
    double depth_range[2];
    uint8_t depth_mask, color_mask[4], blend_enabled, scissor_enabled;
    uint8_t enabled[std::size(enables)];

    void capture(const Dispatch& g) {
        g.GetIntegerv(CURRENT_PROGRAM,&program); g.GetIntegerv(VERTEX_ARRAY_BINDING,&vao);
        g.GetIntegerv(ARRAY_BUFFER_BINDING,&array_buffer); g.GetIntegerv(PIXEL_UNPACK_BUFFER_BINDING,&unpack_buffer);
        g.GetIntegerv(RENDERBUFFER_BINDING,&renderbuffer); g.GetIntegerv(READ_FRAMEBUFFER_BINDING,&read_fbo);
        g.GetIntegerv(DRAW_FRAMEBUFFER_BINDING,&draw_fbo); g.GetIntegerv(READ_BUFFER,&read_buffer);
        g.GetIntegerv(ACTIVE_TEXTURE,&active_texture);
        for (uint32_t i=0;i<4;++i) {
            g.ActiveTexture(TEXTURE0+i); g.GetIntegerv(TEXTURE_BINDING_2D,&textures[i]);
            g.GetIntegerv(SAMPLER_BINDING,&samplers[i]);
        }
        g.ActiveTexture(active_texture);
        g.GetFloati_v(VIEWPORT,0,viewport); g.GetIntegeri_v(SCISSOR_BOX,0,scissor);
        g.GetIntegerv(POLYGON_MODE,polygon); g.GetIntegerv(DEPTH_FUNC,&depth_func);
        g.GetFloatv(POLYGON_OFFSET_FACTOR,polygon_offset);g.GetFloatv(POLYGON_OFFSET_UNITS,polygon_offset+1);
        // Core table 23.14 defines ONE polygon mode; compatibility has two.
        // Normalize core before restore, where separate FRONT/BACK is invalid.
        int32_t profile=0; g.GetIntegerv(CONTEXT_PROFILE_MASK,&profile);
        if(profile&CONTEXT_CORE_PROFILE_BIT) polygon[1]=polygon[0];
        g.GetDoublei_v(DEPTH_RANGE,0,depth_range); g.GetBooleanv(DEPTH_WRITEMASK,&depth_mask);
        g.GetBooleani_v(COLOR_WRITEMASK,0,color_mask); g.GetBooleani_v(BLEND,0,&blend_enabled);
        g.GetBooleani_v(SCISSOR_TEST,0,&scissor_enabled);
        for (size_t i=0;i<std::size(enables);++i) enabled[i]=g.IsEnabled(enables[i]);
        for (size_t i=0;i<6;++i) g.GetIntegeri_v(blend_queries[i],0,&blend[i]);
        for (size_t j=0;j<2;++j) for (size_t i=0;i<7;++i) g.GetIntegerv(stencil_queries[j][i],&stencil[j][i]);
        for (size_t i=0;i<4;++i) g.GetIntegerv(unpack_queries[i],&unpack[i]);
    }
    void restore(const Dispatch& g) const {
        g.UseProgram(program); g.BindVertexArray(vao); g.BindBuffer(ARRAY_BUFFER,array_buffer);
        g.BindBuffer(PIXEL_UNPACK_BUFFER,unpack_buffer); g.BindRenderbuffer(RENDERBUFFER,renderbuffer);
        g.BindFramebuffer(READ_FRAMEBUFFER,read_fbo); g.ReadBuffer(read_buffer);
        g.BindFramebuffer(DRAW_FRAMEBUFFER,draw_fbo);
        for (uint32_t i=0;i<4;++i) {g.ActiveTexture(TEXTURE0+i);g.BindTexture(TEXTURE_2D,textures[i]);g.BindSampler(i,samplers[i]);}
        g.ActiveTexture(active_texture);
        g.ViewportIndexedf(0,viewport[0],viewport[1],viewport[2],viewport[3]);
        g.ScissorIndexed(0,scissor[0],scissor[1],scissor[2],scissor[3]);
        if (polygon[0]==polygon[1]) g.PolygonMode(FRONT_AND_BACK,polygon[0]);
        else {g.PolygonMode(FRONT,polygon[0]);g.PolygonMode(BACK,polygon[1]);}
        g.PolygonOffset(polygon_offset[0],polygon_offset[1]);
        g.DepthFunc(depth_func); g.DepthRangeIndexed(0,depth_range[0],depth_range[1]); g.DepthMask(depth_mask);
        g.ColorMaski(0,color_mask[0],color_mask[1],color_mask[2],color_mask[3]);
        g.BlendFuncSeparatei(0,blend[0],blend[1],blend[2],blend[3]); g.BlendEquationSeparatei(0,blend[4],blend[5]);
        (blend_enabled ? g.Enablei : g.Disablei)(BLEND,0);
        (scissor_enabled ? g.Enablei : g.Disablei)(SCISSOR_TEST,0);
        for (size_t j=0;j<2;++j) {
            const auto side=j==0 ? FRONT : BACK; const auto& s=stencil[j];
            g.StencilFuncSeparate(side,s[0],s[1],s[2]);g.StencilOpSeparate(side,s[3],s[4],s[5]);g.StencilMaskSeparate(side,s[6]);
        }
        for (size_t i=0;i<std::size(enables);++i) (enabled[i] ? g.Enable : g.Disable)(enables[i]);
        for (size_t i=0;i<4;++i) g.PixelStorei(unpack_queries[i],unpack[i]);
    }
};
/* Every ghost fragment shader is this version line, then the player-first
 * fade (ghost_occlusion.h), then its own body; one rule for body and shadow. */
constexpr const char* fragment_version="#version 430 core\n";
struct OcclusionUniforms {
    int32_t at[ghost_occlusion::uniform_count]{};
    bool find(const Dispatch& g,uint32_t program) {
        for (unsigned i=0;i<ghost_occlusion::uniform_count;++i) {
            at[i]=g.GetUniformLocation(program,ghost_occlusion::uniform_names[i]);
            if (at[i]<0) return false;
        }
        return true;
    }
    void set(const Dispatch& g,const ghost_occlusion::Region& r) const {
        using namespace ghost_occlusion;
        g.Uniform1i(at[0],!r.enabled ? 0 : r.has_player ? 2 : 1);
        g.Uniform2f(at[1],r.center[0],r.center[1]);g.Uniform2f(at[2],r.radius[0],r.radius[1]);
        g.Uniform1f(at[3],r.player_w);g.Uniform2f(at[4],REGION_INNER,REGION_OUTER);
        g.Uniform2f(at[5],FRONT_BAND,PLAYER_STRENGTH);g.Uniform2f(at[6],NEAR_HIDDEN,NEAR_FULL);
        g.Uniform2f(at[7],r.overlap,GHOST_FLOOR);
        g.Uniform1f(at[8],r.minimum);
    }
};
struct Guard {
    const Dispatch& g; State state{};
    explicit Guard(const Dispatch& dispatch):g(dispatch) {state.capture(g);}
    ~Guard() {state.restore(g);}
};

/* Every driver query the compositor makes goes through a metered copy of its
 * dispatch, which counts and times it: under threaded optimization each one
 * waits for the driver's own thread, so the count is what a frame pays. */
#define GC_GL_QUERIES(X) X(GetIntegerv) X(GetBooleanv) X(GetFloatv) X(GetDoublev) X(GetIntegeri_v) \
    X(GetBooleani_v) X(GetFloati_v) X(GetDoublei_v) X(IsEnabled) X(IsTexture) X(CheckFramebufferStatus) \
    X(GetFramebufferAttachmentParameteriv) X(GetTexLevelParameteriv) X(GetShaderiv) X(GetProgramiv) \
    X(GetUniformLocation)
struct QueryMeter { uint32_t count=0; uint64_t nanos=0; };
namespace meter {
using clock=std::chrono::steady_clock;
// Renderer thread only, like every compositor call: the raw dispatch and the
// meter of the call in progress, set by Metering for its length.
inline const Dispatch* forward=nullptr;
inline QueryMeter* total=nullptr;
template<auto Member,class F> struct Shim;
template<auto Member,class R,class... A> struct Shim<Member,R (GC_GL_CALL*)(A...)> {
    static R GC_GL_CALL call(A... a) {
        struct Timed {
            clock::time_point start=clock::now();
            ~Timed() {
                ++total->count;
                total->nanos+=uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(clock::now()-start).count());
            }
        } timed;
        return (forward->*Member)(a...);
    }
};
}
inline Dispatch metered(const Dispatch& raw) {
    Dispatch d=raw;
#define GC_METER(name) d.name=meter::Shim<&Dispatch::name,decltype(Dispatch::name)>::call;
    GC_GL_QUERIES(GC_METER)
#undef GC_METER
    return d;
}
struct Metering {
    const Dispatch* forward; QueryMeter* total;
    Metering(const Dispatch& raw,QueryMeter& into):forward(meter::forward),total(meter::total) {
        meter::forward=&raw;meter::total=&into;
    }
    ~Metering() {meter::forward=forward;meter::total=total;}
};

/* The units the compositor's shaders sample: past every unit the pinned
 * renderer binds (0-7) and inside the 32 the capture overlay tracks, so a
 * frame leaves the renderer's own texture bindings alone. */
constexpr uint32_t unit_base=28, unit_count=4;
inline bool multibind(const Dispatch& g) {return g.BindTextures && g.BindSamplers;}
/* Without multi-bind this moves the active unit; the frame restores it. */
inline void bind_units(const Dispatch& g,uint32_t first,uint32_t count,const uint32_t* textures) {
    if(multibind(g)) {g.BindTextures(unit_base+first,int32_t(count),textures);return;}
    for(uint32_t i=0;i<count;++i) {g.ActiveTexture(TEXTURE0+unit_base+first+i);g.BindTexture(TEXTURE_2D,textures[i]);}
}
/* The capabilities the renderer switches per draw, in Known::Cap bit order:
 * the pinned GLideN64's whole set, every one through its CachedEnable (BLEND
 * at draw buffer 0, SCISSOR_TEST at viewport 0). The other `enables` are
 * stable: prepare records them (see ghost_compositor.h). */
constexpr uint32_t volatile_caps[]={DEPTH_TEST,CULL_FACE,POLYGON_OFFSET_FILL,DEPTH_CLAMP,CLIP_DISTANCE0,BLEND,SCISSOR_TEST};
inline size_t enable_index(uint32_t cap) {
    for(size_t i=0;i<std::size(enables);++i)if(enables[i]==cap)return i;
    return std::size(enables);
}

/* Everything a frame (begin_batch, begin_layer, begin_hud) touches, and
 * nothing more; prepare and other callers use Guard. capture() asks the
 * driver for all of it: prepare's record, and every frame of a caller that
 * knows nothing. Given a Known, assume() starts from that record, takes what
 * the caller knows and asks the driver only for what it does not know of
 * what the renderer changes per draw (nothing when the renderer tracks it
 * all; the active unit without multi-bind). restore() puts every field back. */
struct Frame {
    int32_t program, vao, read_fbo, draw_fbo, copy_buffer, unpack_buffer, active_texture;
    int32_t textures[unit_count], samplers[unit_count], scissor[4], polygon[2];
    float viewport[4], polygon_offset[2];
    int32_t blend[6], stencil[2][7], depth_func, unpack[3]; // row length, skip rows, skip pixels
    double depth_range[2];
    uint8_t depth_mask, color_mask[4], blend_enabled, scissor_enabled, feedback;
    uint8_t enabled[std::size(enables)];

    void capture(const Dispatch& g) {
        g.GetIntegerv(CURRENT_PROGRAM,&program);g.GetIntegerv(VERTEX_ARRAY_BINDING,&vao);
        g.GetIntegerv(READ_FRAMEBUFFER_BINDING,&read_fbo);g.GetIntegerv(DRAW_FRAMEBUFFER_BINDING,&draw_fbo);
        g.GetIntegerv(COPY_WRITE_BUFFER_BINDING,&copy_buffer);g.GetIntegerv(PIXEL_UNPACK_BUFFER_BINDING,&unpack_buffer);
        g.GetIntegerv(ACTIVE_TEXTURE,&active_texture);
        for(uint32_t i=0;i<unit_count;++i) {
            g.ActiveTexture(TEXTURE0+unit_base+i);g.GetIntegerv(TEXTURE_BINDING_2D,&textures[i]);
            g.GetIntegerv(SAMPLER_BINDING,&samplers[i]);
        }
        g.ActiveTexture(active_texture);
        g.GetFloati_v(VIEWPORT,0,viewport);g.GetIntegeri_v(SCISSOR_BOX,0,scissor);
        g.GetIntegerv(POLYGON_MODE,polygon);
        int32_t profile=0;g.GetIntegerv(CONTEXT_PROFILE_MASK,&profile);
        if(profile&CONTEXT_CORE_PROFILE_BIT)polygon[1]=polygon[0]; // see State::capture
        g.GetFloatv(POLYGON_OFFSET_FACTOR,polygon_offset);g.GetFloatv(POLYGON_OFFSET_UNITS,polygon_offset+1);
        for(size_t i=0;i<6;++i)g.GetIntegeri_v(blend_queries[i],0,&blend[i]);
        for(size_t j=0;j<2;++j)for(size_t i=0;i<7;++i)g.GetIntegerv(stencil_queries[j][i],&stencil[j][i]);
        g.GetIntegerv(DEPTH_FUNC,&depth_func);
        for(size_t i=0;i<3;++i)g.GetIntegerv(unpack_queries[i+1],&unpack[i]);
        g.GetDoublei_v(DEPTH_RANGE,0,depth_range);g.GetBooleanv(DEPTH_WRITEMASK,&depth_mask);
        g.GetBooleani_v(COLOR_WRITEMASK,0,color_mask);g.GetBooleani_v(BLEND,0,&blend_enabled);
        g.GetBooleani_v(SCISSOR_TEST,0,&scissor_enabled);g.GetBooleanv(TRANSFORM_FEEDBACK_ACTIVE,&feedback);
        for(size_t i=0;i<std::size(enables);++i)enabled[i]=g.IsEnabled(enables[i]);
    }
    void assume(const Frame& stable,const Known* known,const Dispatch& g) {
        if(!known) {capture(g);return;}
        *this=stable;
        const Known& k=*known;
        if(k.fields&Known::program)program=int32_t(k.current_program);
        else g.GetIntegerv(CURRENT_PROGRAM,&program);
        if(k.fields&Known::vertex_array)vao=int32_t(k.bound_vertex_array);
        else g.GetIntegerv(VERTEX_ARRAY_BINDING,&vao);
        if(k.fields&Known::depth) {depth_func=int32_t(k.depth_function);depth_mask=k.depth_write;}
        else {g.GetIntegerv(DEPTH_FUNC,&depth_func);g.GetBooleanv(DEPTH_WRITEMASK,&depth_mask);}
        if(k.fields&Known::blend_factors)for(size_t i=0;i<4;++i)blend[i]=int32_t(k.blend_factor[i]);
        else for(size_t i=0;i<4;++i)g.GetIntegeri_v(blend_queries[i],0,&blend[i]);
        if(k.fields&Known::draw_framebuffer)draw_fbo=int32_t(k.draw);
        else g.GetIntegerv(DRAW_FRAMEBUFFER_BINDING,&draw_fbo);
        if(k.fields&Known::read_framebuffer)read_fbo=int32_t(k.read);
        else g.GetIntegerv(READ_FRAMEBUFFER_BINDING,&read_fbo);
        if(k.fields&Known::viewport)for(size_t i=0;i<4;++i)viewport[i]=float(k.viewport_box[i]);
        else g.GetFloati_v(VIEWPORT,0,viewport);
        if(k.fields&Known::scissor)for(size_t i=0;i<4;++i)scissor[i]=k.scissor_box[i];
        else g.GetIntegeri_v(SCISSOR_BOX,0,scissor);
        for(uint32_t i=0;i<std::size(volatile_caps);++i) {
            const auto cap=volatile_caps[i];
            uint8_t value=uint8_t(k.enabled>>i&1);
            if(!(k.caps>>i&1)) {
                if(cap==BLEND || cap==SCISSOR_TEST)g.GetBooleani_v(cap,0,&value);
                else value=g.IsEnabled(cap);
            }
            if(cap==BLEND)blend_enabled=value;
            else if(cap==SCISSOR_TEST)scissor_enabled=value;
            else enabled[enable_index(cap)]=value;
        }
        if(!multibind(g))g.GetIntegerv(ACTIVE_TEXTURE,&active_texture);
    }
    void restore(const Dispatch& g) const {
        g.UseProgram(program);g.BindVertexArray(vao);
        g.BindFramebuffer(READ_FRAMEBUFFER,read_fbo);g.BindFramebuffer(DRAW_FRAMEBUFFER,draw_fbo);
        g.BindBuffer(COPY_WRITE_BUFFER,copy_buffer);g.BindBuffer(PIXEL_UNPACK_BUFFER,unpack_buffer);
        if(multibind(g)) {
            g.BindTextures(unit_base,unit_count,reinterpret_cast<const uint32_t*>(textures));
            g.BindSamplers(unit_base,unit_count,reinterpret_cast<const uint32_t*>(samplers));
        } else {
            for(uint32_t i=0;i<unit_count;++i) {
                g.ActiveTexture(TEXTURE0+unit_base+i);g.BindTexture(TEXTURE_2D,textures[i]);
                g.BindSampler(unit_base+i,samplers[i]);
            }
            g.ActiveTexture(active_texture);
        }
        g.ViewportIndexedf(0,viewport[0],viewport[1],viewport[2],viewport[3]);
        g.ScissorIndexed(0,scissor[0],scissor[1],scissor[2],scissor[3]);
        if(polygon[0]==polygon[1])g.PolygonMode(FRONT_AND_BACK,polygon[0]);
        else {g.PolygonMode(FRONT,polygon[0]);g.PolygonMode(BACK,polygon[1]);}
        g.PolygonOffset(polygon_offset[0],polygon_offset[1]);
        g.DepthFunc(depth_func);g.DepthRangeIndexed(0,depth_range[0],depth_range[1]);g.DepthMask(depth_mask);
        g.ColorMaski(0,color_mask[0],color_mask[1],color_mask[2],color_mask[3]);
        g.BlendFuncSeparatei(0,blend[0],blend[1],blend[2],blend[3]);g.BlendEquationSeparatei(0,blend[4],blend[5]);
        (blend_enabled ? g.Enablei : g.Disablei)(BLEND,0);
        (scissor_enabled ? g.Enablei : g.Disablei)(SCISSOR_TEST,0);
        for(size_t j=0;j<2;++j) {
            const auto side=j==0 ? FRONT : BACK;const auto& s=stencil[j];
            g.StencilFuncSeparate(side,s[0],s[1],s[2]);g.StencilOpSeparate(side,s[3],s[4],s[5]);g.StencilMaskSeparate(side,s[6]);
        }
        for(size_t i=0;i<std::size(enables);++i)(enabled[i] ? g.Enable : g.Disable)(enables[i]);
        for(size_t i=0;i<3;++i)g.PixelStorei(unpack_queries[i+1],unpack[i]);
    }
};
}
