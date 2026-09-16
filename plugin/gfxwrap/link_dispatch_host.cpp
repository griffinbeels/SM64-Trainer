/* Executes the actual pinned LINK GL loader and caches on a hidden real context. */
#include "GLFunctions.h"
#include "opengl_CachedFunctions.h"
#include "N64.h"
#include "practice_rom_fixture.h"
#include <stdio.h>
#include <cstring>
// GLideN64's own ROM header pointer (N64.cpp is not part of this host).
unsigned char cartridge[0x40];
u8 *HEADER = cartridge;
unsigned logged=0;
void LOG(unsigned short, const char*, ...) { ++logged; } // fixture sink; upstream checked() stays intact
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"link witness line %d: %s\n",__LINE__,#x); ExitProcess(1); } } while(0)
void check_bindings(GLuint read, GLuint texture, GLenum selector) {
    renderer::Identity identity{}; snapshot::RestoreBindings witness{};
    CHECK(replay_gl::CandidateBindings(&identity,&witness));
    CHECK(witness.read_framebuffer==read && witness.texture_2d==texture && witness.source_read_buffer==selector);
    GLint actual=0;
    glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING,&actual); CHECK(GLuint(actual)==read);
    glGetIntegerv(GL_TEXTURE_BINDING_2D,&actual); CHECK(GLuint(actual)==texture);
    // Independently inspect the DEFAULT FBO selector using raw GL. Fully restore
    // the temporary bind; the production observer and renderer cache see no edit.
    auto bind=reinterpret_cast<PFNGLBINDFRAMEBUFFERPROC>(wglGetProcAddress("glBindFramebuffer"));
    CHECK(bind); bind(GL_READ_FRAMEBUFFER,0);
    glGetIntegerv(GL_READ_BUFFER,&actual); CHECK(GLenum(actual)==witness.source_read_buffer);
    bind(GL_READ_FRAMEBUFFER,read);
    CHECK(glGetError()==GL_NO_ERROR);
}
int main(int argc, char **argv) {
    const bool unsupported=argc==2 && std::strcmp(argv[1],"format_unsupported")==0;
    const bool baseline=argc==2 && std::strcmp(argv[1],"baseline_rom")==0;
    if (baseline) PRACTICE_FIXTURE_VANILLA(cartridge); else PRACTICE_FIXTURE_USAMUNE(cartridge);
    WNDCLASSW cls{}; cls.lpfnWndProc=DefWindowProcW; cls.hInstance=GetModuleHandleW(nullptr); cls.lpszClassName=L"LinkWitnessHidden";
    CHECK(RegisterClassW(&cls));
    HWND window=CreateWindowW(cls.lpszClassName,L"",WS_POPUP,0,0,64,64,nullptr,nullptr,cls.hInstance,nullptr);
    CHECK(window && !IsWindowVisible(window)); HDC dc=GetDC(window); CHECK(dc);
    PIXELFORMATDESCRIPTOR pfd{}; pfd.nSize=sizeof(pfd); pfd.nVersion=1;
    pfd.dwFlags=PFD_DRAW_TO_WINDOW|PFD_SUPPORT_OPENGL|PFD_DOUBLEBUFFER; pfd.iPixelType=PFD_TYPE_RGBA; pfd.cColorBits=32;
    int pf=ChoosePixelFormat(dc,&pfd);
    if (unsupported) {
        pf=0; PIXELFORMATDESCRIPTOR choice{};
        const int total=DescribePixelFormat(dc,1,sizeof(choice),&choice);
        for(int index=1;index<=total && index<=4096;++index) {
            if (!DescribePixelFormat(dc,index,sizeof(choice),&choice)) continue;
            const DWORD needed=PFD_DRAW_TO_WINDOW|PFD_SUPPORT_OPENGL|PFD_DOUBLEBUFFER;
            if ((choice.dwFlags&needed)==needed && !(choice.dwFlags&PFD_GENERIC_FORMAT)
                    && choice.iPixelType==PFD_TYPE_RGBA && choice.cRedBits && choice.cGreenBits && choice.cBlueBits
                    && (choice.cRedBits!=8 || choice.cGreenBits!=8 || choice.cBlueBits!=8)) {
                pf=index; pfd=choice; break;
            }
        }
        CHECK(pf); // this driver must expose a real unsupported default format for this witness
    }
    CHECK(pf && SetPixelFormat(dc,pf,&pfd));
    // A profiled context, as the real renderer creates: a legacy wglCreateContext
    // context reports GL_CONTEXT_PROFILE_MASK 0, which the anchor publisher refuses.
    HGLRC bootstrap=wglCreateContext(dc); CHECK(bootstrap && wglMakeCurrent(dc,bootstrap));
    auto create=reinterpret_cast<HGLRC(WINAPI*)(HDC,HGLRC,const int*)>(wglGetProcAddress("wglCreateContextAttribsARB"));
    CHECK(create);
    const int attributes[]={0x2091,4,0x2092,6,0x9126,2,0}; // 4.6 compatibility profile
    HGLRC context=create(dc,nullptr,attributes); CHECK(context);
    CHECK(wglMakeCurrent(nullptr,nullptr) && wglDeleteContext(bootstrap) && wglMakeCurrent(dc,context));
    CHECK(DescribePixelFormat(dc,pf,sizeof(pfd),&pfd));
    replay_gl::ContextCreated(context,dc,(pfd.dwFlags&PFD_DOUBLEBUFFER)!=0);
    if (baseline) {
        // Vanilla SM64: nothing observed, GLideN64's raw dispatch untouched.
        CHECK(replay_gl::DrawableCommitted(window,true));
        unsigned w=0,h=0; CHECK(!replay_gl::DrawableExtent(&w,&h));
        initGLFunctions();
        CHECK(g_glBindFramebuffer==reinterpret_cast<PFNGLBINDFRAMEBUFFERPROC>(wglGetProcAddress("glBindFramebuffer")));
        CHECK(g_glActiveTexture==reinterpret_cast<PFNGLACTIVETEXTUREPROC>(wglGetProcAddress("glActiveTexture")));
        renderer::Identity none{}; snapshot::RestoreBindings unused{};
        CHECK(!replay_gl::CandidateBindings(&none,&unused));
        CHECK(replay_gl::SourceFormat()==RB_SOURCE_UNKNOWN && !replay_gl::BeginCapture());
#ifdef SM64_REPLAY_CONTEXT_LIFETIME
        CHECK(!replay_gl::EnsureAnchor());
#endif
        glBindTexture(GL_TEXTURE_2D,0); CHECK(glGetError()==GL_NO_ERROR);
        replay_gl::ContextLost(); CHECK(wglMakeCurrent(nullptr,nullptr)); CHECK(wglDeleteContext(context));
        CHECK(ReleaseDC(window,dc) && DestroyWindow(window));
        printf("LINK baseline witness passed: vanilla cartridge, raw dispatch, no source\n");
        return 0;
    }
    CHECK(replay_gl::DrawableCommitted(window,true));
    unsigned width=0,height=0;
    CHECK(replay_gl::DrawableExtent(&width,&height) && width==64 && height==64);
    renderer::Identity identity{}; snapshot::RestoreBindings witness{};
    CHECK(!replay_gl::CandidateBindings(&identity,&witness)); // before dispatch qualification
    initGLFunctions(); check_bindings(0,0,GL_BACK);
#ifdef SM64_REPLAY_CONTEXT_LIFETIME
    if (!unsupported) {
        // The anchor is demand-gated: absent after context creation and
        // dispatch qualification, present after EnsureAnchor, gone after loss.
        const rcl_api *contexts=SM64ReplayContextV1(RCL_ABI_V1,sizeof(rcl_api)); CHECK(contexts);
        renderer::Identity created{}; snapshot::RestoreBindings ignored{};
        CHECK(replay_gl::CandidateBindings(&created,&ignored));
        rcl_lease lease{};
        CHECK(contexts->acquire(created.context,created.context_generation,&lease)==RCL_UNAVAILABLE);
        const bool anchored=replay_gl::EnsureAnchor();
        if (!anchored) {
            GLint mj=0,mn=0,pf=0; glGetIntegerv(0x821B,&mj); glGetIntegerv(0x821C,&mn); glGetIntegerv(0x9126,&pf);
            fprintf(stderr,"anchor refused: error=%lu health=%u gl=%d.%d profile=%d pixel_format=%d current=%d\n",
                contexts->error(),contexts->health(),mj,mn,pf,GetPixelFormat(dc),int(wglGetCurrentContext()==context));
        }
        CHECK(anchored && replay_gl::EnsureAnchor());
        CHECK(contexts->acquire(created.context,created.context_generation,&lease)==RCL_ACQUIRED);
        CHECK(lease.context && lease.context!=created.context && lease.context_generation==created.context_generation);
        CHECK(contexts->release(lease.token)==RCL_RELEASED);
        CHECK(glGetError()==GL_NO_ERROR);
    }
#endif
    if (unsupported) {
        auto query=reinterpret_cast<PFNGLGETFRAMEBUFFERATTACHMENTPARAMETERIVPROC>(wglGetProcAddress("glGetFramebufferAttachmentParameteriv"));
        CHECK(query); GLint red=0,green=0,blue=0,type=0;
        query(GL_READ_FRAMEBUFFER,GL_FRONT_LEFT,GL_FRAMEBUFFER_ATTACHMENT_RED_SIZE,&red);
        query(GL_READ_FRAMEBUFFER,GL_FRONT_LEFT,GL_FRAMEBUFFER_ATTACHMENT_GREEN_SIZE,&green);
        query(GL_READ_FRAMEBUFFER,GL_FRONT_LEFT,GL_FRAMEBUFFER_ATTACHMENT_BLUE_SIZE,&blue);
        query(GL_READ_FRAMEBUFFER,GL_FRONT_LEFT,GL_FRAMEBUFFER_ATTACHMENT_COMPONENT_TYPE,&type);
        CHECK(glGetError()==GL_NO_ERROR);
        CHECK(red!=8 || green!=8 || blue!=8 || type!=GL_UNSIGNED_NORMALIZED);
        CHECK(replay_gl::SourceFormat()==RB_SOURCE_UNKNOWN);
        glEnable(0xFFFFFFFF);
        CHECK(!replay_gl::BeginCapture() && glGetError()==GL_INVALID_ENUM);
        replay_gl::ContextLost(); CHECK(wglMakeCurrent(nullptr,nullptr)); CHECK(wglDeleteContext(context));
        CHECK(ReleaseDC(window,dc) && DestroyWindow(window));
        printf("LINK source format refused: actual GL RGB bits %d/%d/%d type=0x%x\n",red,green,blue,unsigned(type));
        return 0;
    }
    CHECK(replay_gl::SourceFormat()==RB_SOURCE_RGB8_LINEAR || replay_gl::SourceFormat()==RB_SOURCE_RGB8_SRGB);
    if (argc==2 && std::strcmp(argv[1],"format_copy")==0) {
        unsigned char legacy[64*4*3]{}, copied[64*4*4]{};
        GLuint texture=0; glGenTextures(1,&texture); glBindTexture(GL_TEXTURE_2D,texture);
        glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA8,64,4,0,GL_RGBA,GL_UNSIGNED_BYTE,nullptr);
        auto dsa=reinterpret_cast<PFNGLCOPYTEXTURESUBIMAGE2DPROC>(wglGetProcAddress("glCopyTextureSubImage2D"));
        GLint major=0,minor=0; glGetIntegerv(GL_MAJOR_VERSION,&major);glGetIntegerv(GL_MINOR_VERSION,&minor);
        const bool have_dsa=dsa && (major>4 || (major==4 && minor>=5));
        glDrawBuffer(GL_FRONT);glReadBuffer(GL_FRONT);glDisable(GL_DITHER);glEnable(GL_SCISSOR_TEST);
        for(unsigned srgb=0;srgb<2;++srgb) {
            if(srgb)glEnable(GL_FRAMEBUFFER_SRGB);else glDisable(GL_FRAMEBUFFER_SRGB);
            for(unsigned value=0;value<256;++value) {
                glScissor(value%64,value/64,1,1);
                glClearColor(value/255.f,((value*73)%256)/255.f,(255-value)/255.f,0.375f);
                glClear(GL_COLOR_BUFFER_BIT);
            }
            glReadPixels(0,0,64,4,GL_BGR,GL_UNSIGNED_BYTE,legacy); // exact old Windows readback type
            for(unsigned path=0;path<1+unsigned(have_dsa);++path) {
                if(path)dsa(texture,0,0,0,0,0,64,4);
                else glCopyTexSubImage2D(GL_TEXTURE_2D,0,0,0,0,0,64,4);
                glGetTexImage(GL_TEXTURE_2D,0,GL_RGBA,GL_UNSIGNED_BYTE,copied);
                CHECK(glGetError()==GL_NO_ERROR);
                for(unsigned i=0;i<256;++i) {
                    CHECK(copied[i*4]==legacy[i*3+2] && copied[i*4+1]==legacy[i*3+1] && copied[i*4+2]==legacy[i*3]);
                }
            }
        }
        const auto format=replay_gl::SourceFormat();glDeleteTextures(1,&texture);
        replay_gl::ContextLost(); CHECK(wglMakeCurrent(nullptr,nullptr)); CHECK(wglDeleteContext(context));
        CHECK(ReleaseDC(window,dc) && DestroyWindow(window));
        printf("LINK source format copy passed: encoding=%u, RGB8 all256 values, srgb enable0/1, bound1 dsa%u\n",format,unsigned(have_dsa));
        return 0;
    }
    if (argc==2) {
        auto raw_error=reinterpret_cast<GLenum (APIENTRY *)()>(GetProcAddress(GetModuleHandleW(L"opengl32.dll"),"glGetError"));
        CHECK(raw_error && !replay_gl::EndCapture());
        if (std::strcmp(argv[1],"pre")==0) {
            glEnable(0xFFFFFFFF); // original renderer error, no observer sees this call
            CHECK(!replay_gl::BeginCapture() && !replay_gl::EndCapture());
            CHECK(raw_error()==GL_NO_ERROR); // gateway consumed exactly one flag
            glScissor(0,0,-1,1); // a later distinct error remains in driver state
            CHECK(glGetError()==GL_INVALID_ENUM && glGetError()==GL_INVALID_VALUE && glGetError()==GL_NO_ERROR);
        } else if (std::strcmp(argv[1],"post")==0) {
            CHECK(replay_gl::BeginCapture() && !replay_gl::BeginCapture());
            glEnable(0xFFFFFFFF); // stands for a failing raw capture call
            CHECK(!replay_gl::EndCapture() && !replay_gl::EndCapture());
            CHECK(raw_error()==GL_NO_ERROR);
            CHECK(glGetError()==GL_INVALID_ENUM && glGetError()==GL_NO_ERROR);
        } else if (std::strcmp(argv[1],"renderer")==0) {
            glEnable(0xFFFFFFFF);
            CHECK(glGetError()==GL_INVALID_ENUM && !replay_gl::BeginCapture());
        } else if (std::strcmp(argv[1],"checked")==0) {
            bool caught=false;
            try { checked([]{glEnable(0xFFFFFFFF);},"seeded invalid capability"); }
            catch (const std::runtime_error &) { caught=true; }
            CHECK(caught && logged==1 && !replay_gl::BeginCapture()); // actual upstream template sees gateway
        } else if (std::strcmp(argv[1],"inactive")==0) {
            replay_gl::ContextLost(); glEnable(0xFFFFFFFF);
            CHECK(!replay_gl::BeginCapture() && !replay_gl::EndCapture());
            CHECK(raw_error()==GL_INVALID_ENUM); // no inactive driver error read
        } else if (std::strcmp(argv[1],"clean")==0) {
            CHECK(replay_gl::BeginCapture() && replay_gl::EndCapture());
            CHECK(replay_gl::CandidateBindings(&identity,&witness));
            replay_gl::ContextLost();
        } else { CHECK(false); }
        CHECK(!replay_gl::CandidateBindings(&identity,&witness));
        replay_gl::ContextLost(); CHECK(wglMakeCurrent(nullptr,nullptr)); CHECK(wglDeleteContext(context));
        CHECK(ReleaseDC(window,dc) && DestroyWindow(window));
        printf("LINK source errors passed: %s\n",argv[1]); return 0;
    }
    opengl::CachedBindFramebuffer cached(g_glBindFramebuffer);
    opengl::CachedBindTexture textures;
    GLuint fbos[2]={}, names[2]={}; glGenFramebuffers(2,fbos); glGenTextures(2,names);
    cached.bind(graphics::Parameter(GL_FRAMEBUFFER),graphics::ObjectHandle(fbos[0]));
    glReadBuffer(GL_COLOR_ATTACHMENT0);
    textures.bind(graphics::Parameter(3),graphics::Parameter(GL_TEXTURE_2D),graphics::ObjectHandle(names[0]));
    check_bindings(fbos[0],names[0],GL_BACK);
    // A cached no-op must not manufacture a state mutation.
    cached.bind(graphics::Parameter(GL_FRAMEBUFFER),graphics::ObjectHandle(fbos[0]));
    check_bindings(fbos[0],names[0],GL_BACK);
    cached.bind(graphics::Parameter(GL_READ_FRAMEBUFFER),graphics::ObjectHandle(0));
    glReadBuffer(GL_FRONT); check_bindings(0,names[0],GL_FRONT);
    cached.bind(graphics::Parameter(GL_DRAW_FRAMEBUFFER),graphics::ObjectHandle(fbos[1]));
    check_bindings(0,names[0],GL_FRONT);
    glActiveTexture(GL_TEXTURE0+4); glBindTexture(GL_TEXTURE_2D,names[0]);
    glDeleteTextures(1,names); check_bindings(0,0,GL_FRONT);
    glActiveTexture(GL_TEXTURE0+3); check_bindings(0,0,GL_FRONT);
    cached.bind(graphics::Parameter(GL_READ_FRAMEBUFFER),graphics::ObjectHandle(fbos[0]));
    glDeleteFramebuffers(1,fbos); check_bindings(0,0,GL_FRONT);
    cached.reset(); textures.reset(); check_bindings(0,0,GL_FRONT);
    CHECK(replay_gl::CandidateBindings(&identity,&witness));
    const auto old_identity=identity;
    replay_gl::DrawableChanged(); check_bindings(0,0,GL_FRONT);
    CHECK(replay_gl::SourceFormat()!=RB_SOURCE_UNKNOWN); // same pixel format after same-context resize
    CHECK(!replay_gl::DrawableExtent(&width,&height));
    CHECK(!replay_gl::DrawableCommitted(window,false)); // failed resize preserves return and refuses extent
    CHECK(!replay_gl::DrawableExtent(&width,&height));
    CHECK(replay_gl::DrawableCommitted(window,true));
    CHECK(replay_gl::DrawableExtent(&width,&height) && width==64 && height==64);
    CHECK(replay_gl::CandidateBindings(&identity,&witness));
    CHECK(identity.context_generation==old_identity.context_generation);
    CHECK(identity.drawable_generation==old_identity.drawable_generation+1);
    // Real invalid cross-FBO selector: shim preserves the original GL error and refuses.
    glReadBuffer(GL_COLOR_ATTACHMENT0);
    CHECK(glGetError()==GL_INVALID_OPERATION);
    CHECK(!replay_gl::CandidateBindings(&identity,&witness));
    replay_gl::ContextLost(); CHECK(!replay_gl::CandidateBindings(&identity,&witness));
    glDeleteTextures(1,names+1); glDeleteFramebuffers(1,fbos+1);
    CHECK(wglMakeCurrent(nullptr,nullptr)); CHECK(wglDeleteContext(context));
    context=wglCreateContext(dc); CHECK(context && wglMakeCurrent(dc,context));
    replay_gl::ContextCreated(context,dc,(pfd.dwFlags&PFD_DOUBLEBUFFER)!=0);
    CHECK(replay_gl::DrawableCommitted(window,true));
    CHECK(!replay_gl::CandidateBindings(&identity,&witness)); // old dispatch cannot authorize recreated context
    initGLFunctions(); check_bindings(0,0,GL_BACK);
    replay_gl::InstallDispatch(); CHECK(!replay_gl::CandidateBindings(&identity,&witness)); // no recursive install
    replay_gl::ContextLost(); CHECK(wglMakeCurrent(nullptr,nullptr)); CHECK(wglDeleteContext(context));
    CHECK(ReleaseDC(window,dc)); CHECK(DestroyWindow(window));
    printf("LINK source witness passed: actual-loader cached-bind core-alias delete reset invalid-selector context-recreate\n");
}
