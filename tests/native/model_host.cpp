// Public C API witness: no GLideN64 or libsm64 decomp headers.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <GL/gl.h>
#include "model.h"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <limits>
#include <stdexcept>
#include <thread>
#include <vector>

extern "C" void sg_model_test_scratch_limit(size_t);
extern "C" size_t sg_model_test_scratch_used(void);

static unsigned checks;
static void check(bool ok, const char* message) {
    ++checks;
    if (!ok) throw std::runtime_error(message);
}
template<class T> struct Buffer {
    static constexpr T sentinel=static_cast<T>(12345.75f);
    std::vector<T> bytes;
    explicit Buffer(size_t n): bytes(n + 32, sentinel) {}
    T* data() { return bytes.data() + 16; }
    void guard() {
        for (size_t i=0; i<16; ++i)
            check(bytes[i] == sentinel && bytes[bytes.size()-1-i] == sentinel, "capacity canary");
    }
};
struct Mesh {
    Buffer<float> p, n, c, uv;
    Buffer<uint32_t> material;
    sg_mesh out;
    explicit Mesh(unsigned capacity=SG_MODEL_MAX_VERTICES):
        p(capacity*3), n(capacity*3), c(capacity*3), uv(capacity*2), material(capacity),
        out{p.data(), n.data(), c.data(), uv.data(), material.data(), capacity, 0, 0} {}
    void guard() { p.guard(); n.guard(); c.guard(); uv.guard(); material.guard(); }
    std::vector<float> snapshot() {
        std::vector<float> result;
        for (auto pair : {std::make_pair(out.position,3u), {out.normal,3u}, {out.color,3u}, {out.uv,2u}})
            result.insert(result.end(), pair.first, pair.first + out.vertex_count * pair.second);
        for(unsigned i=0;i<out.vertex_count;++i) result.push_back(static_cast<float>(out.material[i]));
        return result;
    }
};
static sg_pose pose() {
    sg_pose p{};
    p.fields = SG_POSE_REQUIRED;
    p.scale[0] = p.scale[1] = p.scale[2] = 1;
    p.action = p.body_action = 0x0c400201; // ACT_IDLE, values are captured state.
    p.animation_id = 0;
    p.eye_state = 1;
    return p;
}
static uint32_t be32(const uint8_t* p) {
    return uint32_t(p[0])<<24 | uint32_t(p[1])<<16 | uint32_t(p[2])<<8 | p[3];
}
static void malformed(const std::vector<uint8_t>& original, size_t at, unsigned count, uint8_t value) {
    auto bytes = original;
    std::fill_n(bytes.begin()+at, count, value);
    sg_model* model = nullptr;
    check(sg_model_open(bytes.data(), bytes.size(), &model) == SG_INVALID_ROM && !model, "malformed ROM accepted");
}

// Hidden WGL context plus FBO means window-system occlusion cannot mask output.
// This is model-to-GL evidence only, not the renderer's live camera/depth path.
struct HiddenGL {
    HWND window{}; HDC dc{}; HGLRC rc{};
    GLuint fbo{}, texture{};
    using Gen = void (APIENTRY*)(GLsizei,GLuint*);
    using Bind = void (APIENTRY*)(GLenum,GLuint);
    using Attach = void (APIENTRY*)(GLenum,GLenum,GLenum,GLuint,GLint);
    using Delete = void (APIENTRY*)(GLsizei,const GLuint*);
    Delete delete_fbo{};
    void open() {
        WNDCLASSW wc{}; wc.style=CS_OWNDC; wc.lpfnWndProc=DefWindowProcW;
        wc.hInstance=GetModuleHandleW(nullptr); wc.lpszClassName=L"SM64ModelTestHidden";
        check(RegisterClassW(&wc)!=0,"register hidden GL class");
        window=CreateWindowExW(WS_EX_NOACTIVATE|WS_EX_TOOLWINDOW,wc.lpszClassName,L"Model test",
            WS_POPUP,0,0,256,256,nullptr,nullptr,wc.hInstance,nullptr);
        check(window && !IsWindowVisible(window),"hidden window");
        dc=GetDC(window);
        PIXELFORMATDESCRIPTOR pd{}; pd.nSize=sizeof(pd); pd.nVersion=1;
        pd.dwFlags=PFD_DRAW_TO_WINDOW|PFD_SUPPORT_OPENGL; pd.iPixelType=PFD_TYPE_RGBA; pd.cColorBits=24;
        int format=ChoosePixelFormat(dc,&pd);
        check(format && SetPixelFormat(dc,format,&pd),"GL pixel format");
        rc=wglCreateContext(dc); check(rc && wglMakeCurrent(dc,rc),"hidden WGL");
        auto gen=reinterpret_cast<Gen>(wglGetProcAddress("glGenFramebuffers"));
        auto bind=reinterpret_cast<Bind>(wglGetProcAddress("glBindFramebuffer"));
        auto attach=reinterpret_cast<Attach>(wglGetProcAddress("glFramebufferTexture2D"));
        delete_fbo=reinterpret_cast<Delete>(wglGetProcAddress("glDeleteFramebuffers"));
        check(gen && bind && attach && delete_fbo,"FBO functions");
        gen(1,&fbo); bind(0x8d40,fbo);
        glGenTextures(1,&texture); glBindTexture(GL_TEXTURE_2D,texture);
        glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA,256,256,0,GL_RGBA,GL_UNSIGNED_BYTE,nullptr);
        attach(0x8d40,0x8ce0,GL_TEXTURE_2D,texture,0);
        glDrawBuffer(0x8ce0); glReadBuffer(0x8ce0);
    }
    unsigned draw(const sg_mesh& mesh) {
        glViewport(0,0,256,256); glClearColor(0,0,0,0); glClear(GL_COLOR_BUFFER_BIT);
        glMatrixMode(GL_PROJECTION); glLoadIdentity(); glOrtho(-200,200,-100,300,-1000,1000);
        glMatrixMode(GL_MODELVIEW); glLoadIdentity(); glColor3f(1,1,1);
        glBegin(GL_TRIANGLES);
        for (unsigned i=0; i<mesh.vertex_count; ++i) glVertex3fv(mesh.position+i*3);
        glEnd();
        std::vector<uint8_t> pixels(256*256*4);
        glReadPixels(0,0,256,256,GL_RGBA,GL_UNSIGNED_BYTE,pixels.data());
        check(glGetError()==GL_NO_ERROR,"GL model draw/readback");
        unsigned count=0; for (size_t i=0; i<pixels.size(); i+=4) count += pixels[i] != 0;
        check(!IsWindowVisible(window),"GL witness stayed hidden");
        return count;
    }
    ~HiddenGL() {
        if (texture) glDeleteTextures(1,&texture);
        if (fbo && delete_fbo) delete_fbo(1,&fbo);
        if (rc) { wglMakeCurrent(nullptr,nullptr); wglDeleteContext(rc); }
        if (dc) ReleaseDC(window,dc);
        if (window) DestroyWindow(window);
        UnregisterClassW(L"SM64ModelTestHidden",GetModuleHandleW(nullptr));
    }
};

int main(int argc, char** argv) {
    sg_model* model=nullptr;
    try {
        check(argc==2,"usage: model_host ROM");
        std::ifstream input(argv[1],std::ios::binary);
        std::vector<uint8_t> rom((std::istreambuf_iterator<char>(input)),{});
        check(rom.size()==8*1024*1024,"ROM size");
        check(sg_model_open(nullptr,0,&model)==SG_INVALID_ROM && !model,"null ROM");
        check(sg_model_open(rom.data(),rom.size()-1,&model)==SG_INVALID_ROM && !model,"short ROM");
        malformed(rom,0,4,0);
        malformed(rom,1132368,4,0); // MIO0 signature.
        malformed(rom,1132368+4,4,255); // Decoded capacity.
        malformed(rom,1132368+8,4,255); // Compressed stream offset.
        malformed(rom,0x4ec000,4,255); // Animation directory count.
        malformed(rom,0x4ec008,4,255); // Animation entry offset.
        const size_t first=0x4ec000+be32(rom.data()+0x4ec008);
        malformed(rom,first+16,4,255); // Animation index array offset.
        const size_t indices=first+be32(rom.data()+first+16);
        malformed(rom,indices,2,0); // Zero-length channel.
        check(sg_model_open(rom.data(),rom.size(),&model)==SG_OK && model,"valid model open");
        check(sg_model_open(rom.data(),rom.size(),&model)==SG_BUSY && model,"single runtime");
        sg_atlas_view atlas{};
        check(sg_model_get_atlas(model,&atlas)==SG_OK && atlas.animation_count==209 && atlas.rgba,"atlas");
        check(atlas.width==704 && atlas.height==64,"atlas dimensions");
        sg_pose a=pose(); Mesh mesh;
        check(sg_model_evaluate(model,&a,&mesh.out)==SG_OK,"base evaluation");
        check(mesh.out.vertex_count>1800 && mesh.out.vertex_count<=3072,"actual Mario geometry");
        check(std::find(mesh.out.material,mesh.out.material+mesh.out.vertex_count,SG_MATERIAL_ALPHA_MIX)!=
            mesh.out.material+mesh.out.vertex_count,"face alpha-mix combiner");
        check(std::find(mesh.out.material,mesh.out.material+mesh.out.vertex_count,SG_MATERIAL_SOLID)!=
            mesh.out.material+mesh.out.vertex_count,"solid body combiner");
        const unsigned vertices=mesh.out.vertex_count;
        const auto first_mesh=mesh.snapshot();
        for(unsigned id=0; id<atlas.animation_count; ++id) {
            sg_pose sampled=a; sampled.animation_id=static_cast<int32_t>(id);
            const size_t entry=0x4ec000+be32(rom.data()+0x4ec008+id*8);
            const unsigned end=(unsigned(rom[entry+8])<<8)|rom[entry+9];
            for(unsigned frame : {0u,end-1}) {
                sampled.animation_frame=static_cast<int16_t>(frame);
                check(sg_model_evaluate(model,&sampled,&mesh.out)==SG_OK,"all animation endpoints");
            }
        }
        for (unsigned i=0;i<40;++i) {
            check(sg_model_evaluate(model,&a,&mesh.out)==SG_OK && mesh.snapshot()==first_mesh,"frozen deterministic frame");
        }
        sg_pose b=a; b.animation_id=1;
        check(sg_model_evaluate(model,&b,&mesh.out)==SG_OK && mesh.snapshot()!=first_mesh,"animation changes mesh");
        check(sg_model_evaluate(model,&a,&mesh.out)==SG_OK && mesh.snapshot()==first_mesh,"A/B/A independence");
        b=a; b.cap_state=2;
        check(sg_model_evaluate(model,&b,&mesh.out)==SG_OK,"wing geometry");
        check(std::find(mesh.out.material,mesh.out.material+mesh.out.vertex_count,SG_MATERIAL_DECAL_RGBA)!=
            mesh.out.material+mesh.out.vertex_count,"wing decal-alpha combiner");
        for(unsigned i=0;i<mesh.out.vertex_count;i+=3)
            check(mesh.out.material[i]==mesh.out.material[i+1] && mesh.out.material[i]==mesh.out.material[i+2],
                "triangle material agreement");
        b=a; b.head_rotation[1]=8192;
        check(sg_model_evaluate(model,&b,&mesh.out)==SG_OK && mesh.snapshot()!=first_mesh,"effective head rotation");
        b=a; b.body_action=0x04000440; b.torso_angles[0]=4000; // ACT_WALKING.
        check(sg_model_evaluate(model,&b,&mesh.out)==SG_OK,"separate body action");
        const auto body_mesh=mesh.snapshot(); b.body_action=a.body_action;
        check(sg_model_evaluate(model,&b,&mesh.out)==SG_OK && mesh.snapshot()!=body_mesh,"body action controls torso");
        b=a; b.position[0]=25;
        check(sg_model_evaluate(model,&b,&mesh.out)==SG_OK,"translated pose");
        const auto translated=mesh.snapshot();
        b=a; b.fields|=SG_POSE_THROW_MATRIX;
        b.throw_matrix[0]=b.throw_matrix[5]=b.throw_matrix[10]=b.throw_matrix[15]=1;
        b.throw_matrix[12]=25;
        check(sg_model_evaluate(model,&b,&mesh.out)==SG_OK && mesh.snapshot()==translated,"throw matrix transform");
        b=a; b.scale[0]=2; b.scale[1]=0.5f; b.scale[2]=1.5f;
        check(sg_model_evaluate(model,&b,&mesh.out)==SG_OK,"nonuniform scale");
        for (unsigned i=0;i<vertices;++i) {
            float expected[3]; double length=0;
            for(unsigned j=0;j<3;++j) { expected[j]=first_mesh[vertices*3+i*3+j]/b.scale[j]; length+=expected[j]*expected[j]; }
            length=std::sqrt(length);
            if(length>0.1) for(unsigned j=0;j<3;++j)
                check(std::fabs(mesh.out.normal[i*3+j]-expected[j]/length)<0.015,"inverse-transpose normal");
        }
        for(unsigned frame : {32767u,32768u,65535u,0xffffffffu}) {
            b=a; b.area_update_counter=frame; b.punch_state=5;
            check(sg_model_evaluate(model,&b,&mesh.out)==SG_OK,"counter boundary");
            auto repeated=mesh.snapshot();
            check(sg_model_evaluate(model,&b,&mesh.out)==SG_OK && mesh.snapshot()==repeated,"counter/punch determinism");
        }
        Mesh small(30);
        check(sg_model_evaluate(model,&a,&small.out)==SG_CAPACITY && !small.out.vertex_count &&
            (small.out.error_flags&SG_MESH_TOO_SMALL),"bounded triangle writes"); small.guard();
        b=a; b.animation_id=209;
        check(sg_model_evaluate(model,&b,&mesh.out)==SG_INVALID_ANIMATION && !mesh.out.vertex_count,"animation bounds");
        b=a; b.animation_frame=32767;
        check(sg_model_evaluate(model,&b,&mesh.out)==SG_INVALID_ANIMATION,"frame bounds");
        b=a; b.fields&=~SG_POSE_HEAD_ROTATION;
        check(sg_model_evaluate(model,&b,&mesh.out)==SG_INVALID_ARGUMENT,"missing pose fields");
        b=a; b.scale[0]=std::numeric_limits<float>::quiet_NaN();
        check(sg_model_evaluate(model,&b,&mesh.out)==SG_INVALID_ARGUMENT,"nonfinite pose");
        b=a; b.fields|=SG_POSE_THROW_MATRIX;
        check(sg_model_evaluate(model,&b,&mesh.out)==SG_INVALID_ARGUMENT,"singular throw matrix");
        b=a; b.eye_state=4;
        check(sg_model_evaluate(model,&b,&mesh.out)==SG_INVALID_ARGUMENT,"unsupported texture state");
        b=a; b.fields|=SG_POSE_HIDDEN;
        check(sg_model_evaluate(model,&b,&mesh.out)==SG_OK && !mesh.out.vertex_count,"explicit hidden pose");
        sg_model_test_scratch_limit(1);
        check(sg_model_evaluate(model,&a,&mesh.out)==SG_CAPACITY && !mesh.out.vertex_count &&
            (mesh.out.error_flags&SG_MESH_SCRATCH_EXHAUSTED),"scratch fail closed");
        sg_model_test_scratch_limit(256*1024);
        check(sg_model_evaluate(model,&a,&mesh.out)==SG_OK && mesh.snapshot()==first_mesh,"scratch failure recovers");
        const size_t scratch=sg_model_test_scratch_used();
        float camera[3]={0,0,100}, focus[3]={0,0,0}, matrix[16];
        check(sg_model_view(model,camera,focus,0,matrix)==SG_OK,"look-at");
        check(matrix[0]==1 && matrix[5]==1 && matrix[10]==1 && matrix[14]==-100 && matrix[15]==1,"native look-at layout");
        check(sg_model_view(model,camera,camera,0,matrix)==SG_INVALID_ARGUMENT,"coincident camera");
        check(std::all_of(matrix,matrix+16,[](float f){return f==0;}),"camera failure clears output");
        camera[2]=0;camera[1]=100;
        check(sg_model_view(model,camera,focus,0,matrix)==SG_INVALID_ARGUMENT,"vertical camera degeneracy");
        int wrong=0; sg_model* borrowed=model;
        std::thread other([&]{sg_atlas_view view{}; wrong=sg_model_get_atlas(model,&view);
            if(sg_model_close(&borrowed)!=SG_WRONG_THREAD) wrong=0;}); other.join();
        check(wrong==SG_WRONG_THREAD && borrowed==model,"owner thread enforcement");
        HWND foreground=GetForegroundWindow(); unsigned covered=0;
        { HiddenGL gl; gl.open(); covered=gl.draw(mesh.out); }
        check(covered>1000 && covered<20000,"hidden GL Mario silhouette");
        check(GetForegroundWindow()==foreground,"foreground unchanged");
        mesh.guard();
        check(sg_model_close(&model)==SG_OK && !model,"close clears handle");
        check(sg_model_close(&model)==SG_OK,"idempotent close");
        check(sg_model_get_atlas(borrowed,&atlas)==SG_INVALID_MODEL,"closed handle rejected");
        check(sg_model_open(rom.data(),rom.size(),&model)==SG_OK,"reopen");
        check(sg_model_evaluate(model,&a,&mesh.out)==SG_OK && mesh.snapshot()==first_mesh,"reopen deterministic");
        check(sg_model_close(&model)==SG_OK,"final close");
        std::printf("{\"ok\":true,\"checks\":%u,\"vertices\":%u,\"scratch_bytes\":%zu,\"hidden_gl_pixels\":%u}\n",checks,vertices,scratch,covered);
        return 0;
    } catch(const std::exception& e) {
        if(model) sg_model_close(&model);
        std::fprintf(stderr,"model witness: %s (check %u)\n",e.what(),checks); return 1;
    }
}
