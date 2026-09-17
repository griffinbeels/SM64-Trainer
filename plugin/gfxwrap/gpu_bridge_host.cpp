#include "gl_fixture.h"
#include "gpu_bridge_worker.h"
#include "gpu_bridge_wire.h"
#include <wchar.h>
int wmain(int argc,wchar_t**argv) {
    CHECK(argc==2);
    #ifdef GPU_BRIDGE_LARGE
    constexpr unsigned source_width=324,source_height=244,width=320,height=240;
    #else
    constexpr unsigned source_width=12,source_height=10,width=8,height=6;
    #endif
    const auto render_pattern=[=](unsigned occurrence) {
        glEnable(GL_SCISSOR_TEST);
        for(unsigned y=0;y<source_height;++y)for(unsigned x=0;x<source_width;++x) {
            glScissor(x,y,1,1);glClearColor(channel(occurrence,x,y,0)/255.f,
                channel(occurrence,x,y,1)/255.f,channel(occurrence,x,y,2)/255.f,
                channel(occurrence,x,y,0)/255.f); // source alpha deliberately varies
            glClear(GL_COLOR_BUFFER_BIT);
        }
        glScissor(4,4,1,1);
    };
    wchar_t name[128];swprintf_s(name,L"%s-control",argv[1]);
    HANDLE mapping=CreateFileMappingW(INVALID_HANDLE_VALUE,nullptr,PAGE_READWRITE,0,sizeof(BridgeControl),name);
    CHECK(mapping && GetLastError()!=ERROR_ALREADY_EXISTS);
    auto*ctl=static_cast<BridgeControl*>(MapViewOfFile(mapping,FILE_MAP_ALL_ACCESS,0,0,sizeof(BridgeControl)));CHECK(ctl);
    swprintf_s(name,L"%s-attached",argv[1]);HANDLE attached=CreateEventW(nullptr,TRUE,FALSE,name);CHECK(attached);
    swprintf_s(name,L"%s-allow",argv[1]);HANDLE allow=CreateEventW(nullptr,TRUE,FALSE,name);CHECK(allow);
    WNDCLASSW wc{};wc.style=CS_OWNDC;wc.lpfnWndProc=DefWindowProcW;
    wc.hInstance=GetModuleHandleW(nullptr);wc.lpszClassName=L"SnapshotHiddenHost";CHECK(RegisterClassW(&wc));
    Window producer;CHECK(wglMakeCurrent(producer.dc,producer.rc));
    printf("producer_bits=%u renderer=%s\n",unsigned(sizeof(void*)*8),glGetString(GL_RENDERER));
    auto gen=proc<GenFbo>("glGenFramebuffers");auto bind=proc<BindFbo>("glBindFramebuffer");
    auto attach=proc<Attach>("glFramebufferTexture2D");auto check=proc<CheckFbo>("glCheckFramebufferStatus");
    GLuint texture=0,fbo=0;glGenTextures(1,&texture);glBindTexture(GL_TEXTURE_2D,texture);
    glTexImage2D(GL_TEXTURE_2D,0,0x8058,source_width,source_height,0,GL_RGBA,GL_UNSIGNED_BYTE,nullptr);
    gen(1,&fbo);bind(framebuffer,fbo);attach(framebuffer,attachment,GL_TEXTURE_2D,texture,0);
    glReadBuffer(attachment);glDrawBuffer(attachment);CHECK(check(framebuffer)==complete);
    glDisable(GL_DITHER);glDisable(0x8DB9);
    GLint producer_major=0,producer_minor=0;
    glGetIntegerv(0x821B,&producer_major);glGetIntegerv(0x821C,&producer_minor);
    const bool producer_dsa=producer_major>4||(producer_major==4&&producer_minor>=5);
    snapshot::Pool snapshots;gpu_bridge::Pool bridge;BridgeWorker worker(producer);
    snapshot::Gl gl;
    worker.run([&] {
        CHECK(GetCurrentThreadId()!=GetWindowThreadProcessId(producer.hwnd,nullptr));
        CHECK(gl.load(producer_dsa));CHECK(snapshots.prepare(gl,producer.rc,width,height,2,width*height*4*2));
        CHECK(!bridge.prepare(width,height,argv[1],width*height*4*4-1));
        const bool prepared=bridge.prepare(width,height,argv[1],width*height*4*4);
        if(!prepared)fprintf(stderr,"prepare bridge failed error=%lu\n",bridge.error());CHECK(prepared);
        ctl->config=bridge.config();InterlockedExchange(&ctl->initialized,1);
        printf("worker_luid=%08lx:%08lx bridge_bytes=%llu snapshot_bytes=%llu\n",
            DWORD(ctl->config.adapter.HighPart),ctl->config.adapter.LowPart,
            bridge.logical_bytes(),snapshots.counters().texture_bytes);fflush(stdout);
    });
    CHECK(bridge.copy(0,texture,width,height)==gpu_bridge::Result::invalid); // renderer may not enter interop
    CHECK(WaitForSingleObject(attached,10000)==WAIT_OBJECT_0);
    snapshot::Source source{fbo,attachment,1,2,width,height,source_width,source_height};
    snapshot::RestoreBindings restore{fbo,texture,attachment};
    snapshot::Ticket tickets[2]{};snapshot::Image images[2]{};
    const auto generation=snapshots.generation();
    for(unsigned round=0;round<3;++round) {
        if(round==2) {
            for(unsigned n=0;n<10000 && InterlockedCompareExchange(&ctl->consumed,0,0)<4;++n)Sleep(1);
            CHECK(InterlockedCompareExchange(&ctl->consumed,0,0)==4);
        }
        for(unsigned i=0;i<2;++i) {
            const unsigned occurrence=1+round*2+i;render_pattern(occurrence);
            CHECK(snapshots.submit(generation,source,restore,occurrence,occurrence*101,&tickets[i])==snapshot::Result::submitted);
        }
        render_pattern(243); // destroy source before worker reads the owned snapshots
        worker.run([&] {
            for(unsigned i=0;i<2;++i) images[i]=wait_image(snapshots,tickets[i]);
            if(round==1) {
                const unsigned locks=bridge.locks();
                for(unsigned i=0;i<2;++i)CHECK(bridge.copy(i,images[i].texture,width,height)==gpu_bridge::Result::full);
                CHECK(bridge.locks()==locks); // no WGL lock when transport has no room
            }
        });
        if(round==1) {
            LARGE_INTEGER a,b,f;QueryPerformanceFrequency(&f);QueryPerformanceCounter(&a);
            for(unsigned n=0;n<2000;++n) {
                glClearColor(float(n&1),0,0,1);glClear(GL_COLOR_BUFFER_BIT);
                snapshot::Ticket refused;
                CHECK(snapshots.submit(generation,source,restore,999,0,&refused)==snapshot::Result::full);
            }
            QueryPerformanceCounter(&b);glFlush();
            printf("consumer_stalled=yes render_progress=2000 full_refusals=2000 elapsed_ms=%.3f\n",
                   double(b.QuadPart-a.QuadPart)*1000/double(f.QuadPart));fflush(stdout);
            CHECK(InterlockedCompareExchange(&ctl->consumed,0,0)==0);
            // Independent completion witness for the ORIGINAL source GL commands.
            // Completion waits belong to the worker, still before allowing consumer.
            const auto progress=gl.fence(0x9117,0);CHECK(progress);glFlush();
            worker.run([&]{
                bool completed=false;
                for(unsigned n=0;n<10000&&!completed;++n){
                    const GLenum state=gl.wait(progress,0,0);CHECK(state!=0x911D);
                    completed=state==0x911A||state==0x911C;if(!completed)Sleep(1);
                }
                CHECK(completed);gl.delete_sync(progress);
            });
            CHECK(InterlockedCompareExchange(&ctl->consumed,0,0)==0);
            printf("source_gpu_progress=completed consumer_still_stalled=yes\n");fflush(stdout);
            SetEvent(allow);
            for(unsigned n=0;n<10000 && InterlockedCompareExchange(&ctl->consumed,0,0)<2;++n)Sleep(1);
            CHECK(InterlockedCompareExchange(&ctl->consumed,0,0)==2);
        }
        worker.run([&] {
            for(unsigned i=0;i<2;++i) {
                CHECK(!InterlockedCompareExchange(&ctl->packets[i].ready,0,0));
                CHECK(bridge.copy(i,images[i].texture,width,height)==gpu_bridge::Result::copied);
                ctl->packets[i].occurrence=images[i].occurrence;ctl->packets[i].qpc=images[i].qpc;
                InterlockedExchange(&ctl->packets[i].ready,1);
                CHECK(snapshots.release(tickets[i]));
            }
            reap_all(snapshots);
        });
    }
    for(unsigned n=0;n<10000 && InterlockedCompareExchange(&ctl->consumed,0,0)<6;++n)Sleep(1);
    CHECK(InterlockedCompareExchange(&ctl->consumed,0,0)==6 && !ctl->failed);
    worker.run([&] {CHECK(bridge.shutdown());CHECK(glGetError()==GL_NO_ERROR);snapshots.stop();reap_all(snapshots);CHECK(snapshots.destroy());});
    CHECK(wglMakeCurrent(nullptr,nullptr));
    UnmapViewOfFile(ctl);CloseHandle(mapping);CloseHandle(attached);CloseHandle(allow);
    printf("gpu-bridge producer passed: pictures=6 worker-only-interop=yes cross-process=yes\n");return 0;
}
