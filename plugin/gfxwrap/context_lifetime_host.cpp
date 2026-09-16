/* Isolated CPU race/real hidden-WGL lifetime host; never links into a plugin. */
#include "context_lifetime.h"
#include <gl/GL.h>
#include <stdio.h>
#include <string.h>
#include <thread>
#define CHECK(x) do {if(!(x)){fprintf(stderr,"context lifetime line %d: %s\n",__LINE__,#x);ExitProcess(1);}}while(0)
using source_context::Registry;
namespace {
std::atomic<unsigned> deletes{0}, delete_thread{0};
std::atomic<bool> fail_delete{false};
std::atomic<unsigned> creates{0},deleted_context{0};
bool fail_create=false;
BOOL WINAPI fake_delete(HGLRC) {
    ++deletes; delete_thread.store(GetCurrentThreadId());
    if(fail_delete.load()){SetLastError(ERROR_INVALID_HANDLE);return FALSE;} return TRUE;
}
BOOL WINAPI anchor_delete(HGLRC c){deleted_context.store(unsigned(reinterpret_cast<uintptr_t>(c)));return fake_delete(c);}
HGLRC WINAPI fake_create(HDC,HGLRC source,const int*){
    ++creates;if(fail_create){SetLastError(ERROR_NOT_ENOUGH_MEMORY);return nullptr;}
    return reinterpret_cast<HGLRC>(reinterpret_cast<uintptr_t>(source)+1000);
}
BOOL WINAPI real_delete(HGLRC context) {++deletes;delete_thread.store(GetCurrentThreadId());return wglDeleteContext(context);}
struct Event {HANDLE h=CreateEventW(nullptr,TRUE,FALSE,nullptr); Event(){CHECK(h);} ~Event(){CloseHandle(h);}
    void set(){CHECK(SetEvent(h));} void wait(){CHECK(WaitForSingleObject(h,10000)==WAIT_OBJECT_0);} };
HGLRC fake(unsigned n=1){return reinterpret_cast<HGLRC>(uintptr_t(n));}
Event *probe_entered=nullptr,*probe_continue=nullptr;unsigned probe_phase=0;
void __cdecl acquisition_probe(unsigned phase){if(phase==probe_phase){probe_entered->set();probe_continue->wait();}}
void acquiring(unsigned phase) {
    Registry r(fake_delete);CHECK(r.publish(fake(),1,17));Event entered,resume;
    probe_entered=&entered;probe_continue=&resume;probe_phase=phase;r.test_acquire_probe(acquisition_probe);
    std::thread worker([&]{rcl_lease lease{};CHECK(r.acquire(1,1,&lease)==RCL_UNAVAILABLE);});
    entered.wait();auto token=r.revoke(fake());
    CHECK(r.unbound(token,TRUE)==(phase==1?RCL_DELETED:RCL_DEFERRED));
    if(phase==1)CHECK(r.publish(fake(),2,17)); // old pre-CAS token cannot claim replacement
    resume.set();worker.join();CHECK(deletes.load()==1);
    r.test_acquire_probe(nullptr);
    if(phase==1)CHECK(r.unbound(r.revoke(fake()),TRUE)==RCL_DELETED);
}
void creation_failure() {
    Registry r(anchor_delete);fail_create=true;
    CHECK(!r.publish(fake(),1,17,fake_create));CHECK(creates.load()==1 && deletes.load()==0);
    CHECK(r.error()==ERROR_NOT_ENOUGH_MEMORY && !r.health());
    rcl_lease lease{};CHECK(r.acquire(1,1,&lease)==RCL_UNAVAILABLE);
    fail_create=false;CHECK(r.publish(fake(),2,17,fake_create));CHECK(!r.error());
    CHECK(!r.publish(fake(),2,17,fake_create) && creates.load()==2);
    CHECK(r.acquire(1,2,&lease)==RCL_ACQUIRED && lease.context==1001);
    CHECK(r.unbound(r.revoke(fake()),TRUE)==RCL_DEFERRED);
    CHECK(r.release(lease.token)==RCL_DELETED && deleted_context.load()==1001);
}
void interleaving(const char *mode) {
    Registry r(fake_delete); CHECK(r.publish(fake(),1,17));
    Event acquired,go,released; rcl_lease lease{}; unsigned worker_id=0;
    std::thread worker([&]{worker_id=GetCurrentThreadId(); CHECK(r.acquire(1,1,&lease)==RCL_ACQUIRED);
        acquired.set(); go.wait(); const int result=r.release(lease.token);
        CHECK(result==RCL_RELEASED || result==RCL_DELETED); released.set();});
    acquired.wait();
    const bool before=!strcmp(mode,"before"), between=!strcmp(mode,"between");
    if(before){go.set();released.wait();}
    const auto retirement=r.revoke(fake()); CHECK(retirement.value);
    rcl_lease refused{}; CHECK(r.acquire(1,1,&refused)==RCL_UNAVAILABLE);
    if(between){go.set();released.wait();CHECK(deletes.load()==0);}
    CHECK(r.unbound(retirement,TRUE)==(before||between?RCL_DELETED:RCL_DEFERRED));
    if(!before&&!between){CHECK(deletes.load()==0);go.set();released.wait();}
    worker.join(); CHECK(deletes.load()==1 && !r.health());
    CHECK(delete_thread.load()==(before||between?GetCurrentThreadId():worker_id));
    CHECK(r.release(lease.token)==RCL_STALE);
}
void stale() {
    Registry r(fake_delete); CHECK(r.publish(fake(),1,17));
    rcl_lease first{},second{},replacement{};
    CHECK(r.acquire(1,1,&first)==RCL_ACQUIRED); CHECK(r.release(first.token)==RCL_RELEASED);
    CHECK(r.acquire(1,1,&second)==RCL_ACQUIRED);
    CHECK(first.token.value!=second.token.value && r.release(first.token)==RCL_STALE);
    auto retirement=r.revoke(fake()); CHECK(r.unbound(retirement,TRUE)==RCL_DEFERRED);
    CHECK(r.release(second.token)==RCL_DELETED);
    CHECK(r.publish(fake(),2,19)); CHECK(r.acquire(1,1,&replacement)==RCL_UNAVAILABLE);
    CHECK(r.acquire(1,2,&replacement)==RCL_ACQUIRED && replacement.pixel_format==19);
    CHECK(r.release(second.token)==RCL_STALE);
    CHECK(r.unbound(retirement,TRUE)==RCL_STALE && deletes.load()==1);
    retirement=r.revoke(fake()); CHECK(r.unbound(retirement,TRUE)==RCL_DEFERRED);
    CHECK(r.release(replacement.token)==RCL_DELETED && deletes.load()==2);
}
void capacity() {
    Registry r(fake_delete); rcl_lease leases[RCL_SLOTS]{};
    for(unsigned n=0;n<RCL_SLOTS;++n){CHECK(r.publish(fake(n+1),n+1,17,fake_create));
        CHECK(r.acquire(n+1,n+1,&leases[n])==RCL_ACQUIRED);
        CHECK(r.unbound(r.revoke(fake(n+1)),TRUE)==RCL_DEFERRED);}
    CHECK(!r.publish(fake(100),100,17,fake_create) && deletes.load()==0 && creates.load()==RCL_SLOTS);
    for(auto &l:leases) CHECK(r.release(l.token)==RCL_DELETED);
    CHECK(deletes.load()==RCL_SLOTS && r.publish(fake(100),100,17));
    CHECK(r.unbound(r.revoke(fake(100)),TRUE)==RCL_DELETED);
}
void failure(bool unbind) {
    Registry r(fake_delete); CHECK(r.publish(fake(),1,17)); rcl_lease lease{};
    CHECK(r.acquire(1,1,&lease)==RCL_ACQUIRED);
    if(!unbind)fail_delete.store(true);
    CHECK(r.unbound(r.revoke(fake()),unbind?FALSE:TRUE)==RCL_DEFERRED);
    CHECK(r.release(lease.token)==RCL_QUARANTINED);
    CHECK(r.health()==uint32_t(unbind?RCL_UNBIND_FAILED:RCL_DELETE_FAILED));
    CHECK(r.error()==uint32_t(unbind?ERROR_BUSY:ERROR_INVALID_HANDLE));
    CHECK(deletes.load()==(unbind?0u:1u)); CHECK(r.release(lease.token)==RCL_STALE);
    CHECK(!r.publish(fake(2),2,17)); CHECK(r.acquire(1,1,&lease)==RCL_UNAVAILABLE);
}
void simultaneous() {
    Registry r(fake_delete);
    for(unsigned n=1;n<=100;++n){
        CHECK(r.publish(fake(n),n,17)); rcl_lease lease{};
        CHECK(r.acquire(n,n,&lease)==RCL_ACQUIRED); auto retired=r.revoke(fake(n)); Event go;
        std::thread worker([&]{go.wait();const int result=r.release(lease.token);
            CHECK(result==RCL_RELEASED || result==RCL_DELETED);});
        go.set(); const int result=r.unbound(retired,TRUE);
        CHECK(result==RCL_RELEASED || result==RCL_DELETED || result==RCL_DEFERRED);
        worker.join(); CHECK(deletes.load()==n);
    }
}
using CreateContext=HGLRC (WINAPI *)(HDC,HGLRC,const int*);
struct Window {
    HWND hwnd=nullptr; HDC dc=nullptr; HGLRC rc=nullptr;
    explicit Window(int format=0) {
        hwnd=CreateWindowExW(0,L"ContextLifetimeHidden",L"",WS_POPUP,0,0,32,32,nullptr,nullptr,GetModuleHandleW(nullptr),nullptr);
        CHECK(hwnd && !IsWindowVisible(hwnd)); dc=GetDC(hwnd); CHECK(dc);
        PIXELFORMATDESCRIPTOR p{};p.nSize=sizeof(p);p.nVersion=1;
        p.dwFlags=PFD_DRAW_TO_WINDOW|PFD_SUPPORT_OPENGL|PFD_DOUBLEBUFFER;p.iPixelType=PFD_TYPE_RGBA;p.cColorBits=32;
        if(!format)format=ChoosePixelFormat(dc,&p);
        else CHECK(DescribePixelFormat(dc,format,sizeof(p),&p));
        CHECK(format && SetPixelFormat(dc,format,&p));
        rc=wglCreateContext(dc);CHECK(rc);
    }
    void release_dc(){if(dc){CHECK(ReleaseDC(hwnd,dc));dc=nullptr;}}
    ~Window(){if(rc)CHECK(wglDeleteContext(rc));release_dc();if(hwnd)CHECK(DestroyWindow(hwnd));}
};
void real(bool after,bool share_lists=false,bool anchor=false,bool production=false) {
    WNDCLASSW cls{}; cls.style=CS_OWNDC;cls.lpfnWndProc=DefWindowProcW;
    cls.hInstance=GetModuleHandleW(nullptr);cls.lpszClassName=L"ContextLifetimeHidden";
    CHECK(RegisterClassW(&cls));
    {
        Registry r(real_delete); Window source;
        CHECK(wglMakeCurrent(source.dc,source.rc));
        auto create_source=reinterpret_cast<CreateContext>(wglGetProcAddress("wglCreateContextAttribsARB"));
        CHECK(reinterpret_cast<uintptr_t>(create_source)>3 && reinterpret_cast<uintptr_t>(create_source)!=UINTPTR_MAX);
        const int core_attributes[]={0x2091,4,0x2092,5,0x9126,1,0};
        const HGLRC core=create_source(source.dc,nullptr,core_attributes);CHECK(core);
        CHECK(wglMakeCurrent(nullptr,nullptr));CHECK(wglDeleteContext(source.rc));source.rc=core;
        CHECK(wglMakeCurrent(source.dc,source.rc));const HGLRC original=source.rc;
        const int pixel_format=GetPixelFormat(source.dc);
        LARGE_INTEGER begin{},end{},frequency{};QueryPerformanceFrequency(&frequency);QueryPerformanceCounter(&begin);
        if(production)CHECK(source_context::Publish(original,source.dc,31,pixel_format));
        else CHECK(r.publish(original,31,pixel_format,anchor?create_source:nullptr,source.dc,core_attributes));
        QueryPerformanceCounter(&end);
        if(anchor)printf("startup anchor creation us=%.3f; one hidden fixture sample\n",double(end.QuadPart-begin.QuadPart)*1000000.0/double(frequency.QuadPart));
        const auto public_api=production?SM64ReplayContextV1(1,sizeof(rcl_api)):nullptr;
        unsigned char wanted[4*3*4];for(unsigned i=0;i<sizeof(wanted);++i)wanted[i]=static_cast<unsigned char>(i*37+11);
        GLuint texture=0;glGenTextures(1,&texture);glBindTexture(GL_TEXTURE_2D,texture);
        glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA8,4,3,0,GL_RGBA,GL_UNSIGNED_BYTE,wanted);glFlush();
        CHECK(glGetError()==GL_NO_ERROR);
        Event acquired,go,created,verify; rcl_lease lease{};
        std::thread worker([&]{
            Window target(pixel_format);CHECK(wglMakeCurrent(target.dc,target.rc));
            auto create=reinterpret_cast<CreateContext>(wglGetProcAddress("wglCreateContextAttribsARB"));
            CHECK(reinterpret_cast<uintptr_t>(create)>3 && reinterpret_cast<uintptr_t>(create)!=UINTPTR_MAX);
            CHECK(wglMakeCurrent(nullptr,nullptr)); CHECK(wglDeleteContext(target.rc));target.rc=nullptr;
            CHECK((production?public_api->acquire(reinterpret_cast<uintptr_t>(original),31,&lease):r.acquire(reinterpret_cast<uintptr_t>(original),31,&lease))==RCL_ACQUIRED);
            if(anchor)CHECK(lease.context!=reinterpret_cast<uintptr_t>(original));
            CHECK(lease.pixel_format==unsigned(pixel_format));acquired.set();go.wait();
            const int attributes[]={0x2091,4,0x2092,5,0x9126,1,0};
            bool creation_ok=false;
            if(share_lists){target.rc=create(target.dc,nullptr,attributes);CHECK(target.rc);
                creation_ok=wglShareLists(reinterpret_cast<HGLRC>(lease.context),target.rc)!=FALSE;}
            else {target.rc=create(target.dc,reinterpret_cast<HGLRC>(lease.context),attributes);creation_ok=target.rc!=nullptr;}
            const DWORD create_error=creation_ok?0:GetLastError();
            const int released=production?public_api->release(lease.token):r.release(lease.token); // every creation outcome
            if(!after&&!anchor&&!creation_ok){CHECK(create_error!=0);
                printf("direct current-source sharing refused: error=%lu, share_lists=%u, released=%d\n",create_error,share_lists?1u:0u,released);}
            else CHECK(creation_ok);
            CHECK(released==(after?RCL_DELETED:RCL_RELEASED));created.set();verify.wait();
            if(!creation_ok)return;
            CHECK(wglMakeCurrent(target.dc,target.rc));glBindTexture(GL_TEXTURE_2D,texture);
            unsigned char actual[sizeof(wanted)]{};
            glGetTexImage(GL_TEXTURE_2D,0,GL_RGBA,GL_UNSIGNED_BYTE,actual);
            CHECK(glGetError()==GL_NO_ERROR && !memcmp(actual,wanted,sizeof(wanted)));
            glDeleteTextures(1,&texture);CHECK(wglMakeCurrent(nullptr,nullptr));
        });
        acquired.wait();
        if(!after){go.set();created.wait();}
        const auto retirement=production?source_context::Revoke(original):r.revoke(original);CHECK(retirement.value);
        CHECK(wglMakeCurrent(nullptr,nullptr));
        if(anchor)CHECK(wglDeleteContext(original));
        CHECK((production?source_context::Retire(retirement):r.unbound(retirement,TRUE))==(after?RCL_DEFERRED:RCL_DELETED));
        source.rc=nullptr;source.release_dc(); // original DC ownership remains here
        if(after){CHECK(deletes.load()==0);go.set();created.wait();}
        CHECK(production||deletes.load()==1);verify.set();worker.join();CHECK(production?!public_api->health():!r.health());
    }
    CHECK(UnregisterClassW(L"ContextLifetimeHidden",GetModuleHandleW(nullptr)));
    if(after||anchor)printf("real WGL shared texture survived source retirement: %s\n",after?"share after DC release":"hot attach via never-current anchor");
}
void abi(const char *path) {
    HMODULE module=LoadLibraryA(path);CHECK(module);
    using Query=const rcl_api *(__cdecl *)(uint32_t,uint32_t);
    auto query=reinterpret_cast<Query>(GetProcAddress(module,"SM64ReplayContextV1"));CHECK(query);
    CHECK(!query(2,sizeof(rcl_api)) && !query(1,sizeof(rcl_api)-4));
    const auto api=query(1,sizeof(rcl_api));CHECK(api && api->bytes==32 && api->version==1 && !api->reserved);
    CHECK(api->capabilities==RCL_CAP_NEVER_CURRENT_ANCHOR);
    CHECK(FreeLibrary(module));HMODULE pinned=nullptr;
    CHECK(GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
        reinterpret_cast<LPCWSTR>(query),&pinned) && pinned==module);
    rcl_lease lease{};CHECK(api->acquire(1,1,&lease)==RCL_UNAVAILABLE && !api->health() && !api->error());
    CHECK(api->release({})==RCL_STALE);
}
}
int main(int argc,char **argv) {
    CHECK(argc>=2);const char *mode=argv[1];
    if(!strcmp(mode,"before")||!strcmp(mode,"between")||!strcmp(mode,"after"))interleaving(mode);
    else if(!strcmp(mode,"stale"))stale();else if(!strcmp(mode,"capacity"))capacity();
    else if(!strcmp(mode,"delete_failure"))failure(false);else if(!strcmp(mode,"unbind_failure"))failure(true);
    else if(!strcmp(mode,"simultaneous"))simultaneous();
    else if(!strcmp(mode,"real_after"))real(true);else if(!strcmp(mode,"real_before"))real(false);
    else if(!strcmp(mode,"real_lists"))real(false,true);
    else if(!strcmp(mode,"real_anchor"))real(false,false,true);
    else if(!strcmp(mode,"production_anchor"))real(false,false,true,true);
    else if(!strcmp(mode,"acquiring_before"))acquiring(1);else if(!strcmp(mode,"acquiring_after"))acquiring(2);
    else if(!strcmp(mode,"create_failure"))creation_failure();
    else if(!strcmp(mode,"abi")){CHECK(argc==3);abi(argv[2]);}else CHECK(false);
    printf("context lifetime passed: %s\n",mode);return 0;
}
