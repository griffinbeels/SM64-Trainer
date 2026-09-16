#include "gl_fixture.h"
#include "gpu_bridge_worker.h"
#include "gpu_bridge_wire.h"
#include <wchar.h>
int wmain(int argc,wchar_t**argv) {
    CHECK(argc==4);const unsigned width=unsigned(_wtoi(argv[2])),height=unsigned(_wtoi(argv[3]));
    CHECK((width==640&&height==480)||(width==641&&height==481));
    wchar_t name[128];swprintf_s(name,L"%s-control",argv[1]);
    HANDLE mapping=CreateFileMappingW(INVALID_HANDLE_VALUE,nullptr,PAGE_READWRITE,0,sizeof(BridgeControl),name);
    CHECK(mapping&&GetLastError()!=ERROR_ALREADY_EXISTS);
    auto*control=static_cast<BridgeControl*>(MapViewOfFile(mapping,FILE_MAP_ALL_ACCESS,0,0,sizeof(BridgeControl)));CHECK(control);
    WNDCLASSW wc{};wc.style=CS_OWNDC;wc.lpfnWndProc=DefWindowProcW;
    wc.hInstance=GetModuleHandleW(nullptr);wc.lpszClassName=L"SnapshotHiddenHost";CHECK(RegisterClassW(&wc));
    Window producer;CHECK(wglMakeCurrent(producer.dc,producer.rc));
    GLint major=0,minor=0;glGetIntegerv(0x821B,&major);glGetIntegerv(0x821C,&minor);
    const bool dsa=major>4||(major==4&&minor>=5);
    GLuint texture=0,fbo=0;glGenTextures(1,&texture);glBindTexture(GL_TEXTURE_2D,texture);
    glTexImage2D(GL_TEXTURE_2D,0,0x8058,width,height,0,GL_RGBA,GL_UNSIGNED_BYTE,nullptr);
    proc<GenFbo>("glGenFramebuffers")(1,&fbo);auto bind=proc<BindFbo>("glBindFramebuffer");
    bind(framebuffer,fbo);proc<Attach>("glFramebufferTexture2D")(framebuffer,attachment,GL_TEXTURE_2D,texture,0);
    glReadBuffer(attachment);glDrawBuffer(attachment);CHECK(proc<CheckFbo>("glCheckFramebufferStatus")(framebuffer)==complete);
    glDisable(GL_DITHER);glDisable(0x8DB9);glEnable(GL_SCISSOR_TEST);
    auto rectangle=[](int x,int y,int w,int h,unsigned r,unsigned g,unsigned b,unsigned a) {
        glScissor(x,y,w,h);glClearColor(r/255.f,g/255.f,b/255.f,a/255.f);glClear(GL_COLOR_BUFFER_BIT);
    };
    rectangle(0,0,width,height,37,83,151,17);
    rectangle(0,0,16,16,255,0,0,3);rectangle(width-16,0,16,16,0,255,0,5);
    rectangle(0,height-16,16,16,0,0,255,7);rectangle(width-16,height-16,16,16,255,255,0,11);
    rectangle(0,0,1,height,11,29,43,19);rectangle(width-1,0,1,height,53,71,89,23);
    rectangle(0,0,width,1,101,127,149,29);rectangle(0,height-1,width,1,173,197,229,31);
    const unsigned colors[8][3]={{0,0,0},{16,16,16},{40,100,220},{220,100,40},
        {127,127,127},{235,235,235},{255,255,255},{17,201,93}};
    for(unsigned n=0;n<8;++n)rectangle(48+n*64,80,48,64,colors[n][0],colors[n][1],colors[n][2],37+n);
    for(unsigned x=0;x<width;++x)rectangle(x,200,1,32,x&255,x&255,x&255,53);
    snapshot::Pool snapshots;gpu_bridge::Pool bridge;BridgeWorker worker(producer);snapshot::Gl gl;
    worker.run([&]{
        CHECK(gl.load(dsa));CHECK(snapshots.prepare(gl,producer.rc,width,height,2,uint64_t(width)*height*8));
        CHECK(bridge.prepare(width,height,argv[1],uint64_t(width&~1u)*(height&~1u)*16));
        CHECK(bridge.config().width==(width&~1u)&&bridge.config().height==(height&~1u));
        control->config=bridge.config();InterlockedExchange(&control->initialized,1);
    });
    snapshot::Source source{fbo,attachment,0,0,width,height,width,height};
    snapshot::RestoreBindings restore{fbo,texture,attachment};snapshot::Ticket ticket{};
    CHECK(snapshots.submit(snapshots.generation(),source,restore,1,101,&ticket)==snapshot::Result::submitted);
    // Deliberately destroy original contents before any delivery-worker access.
    rectangle(0,0,width,height,199,23,197,0);
    worker.run([&]{
        const auto image=wait_image(snapshots,ticket);CHECK(image.width==width&&image.height==height);
        // Hostile state belongs only to this worker. The transfer must establish
        // its own raster state without touching renderer state or sampler objects.
        glEnable(GL_SCISSOR_TEST);glScissor(7,9,1,1);glEnable(0x8DB9);glEnable(GL_DITHER);
        glEnable(GL_BLEND);glBlendFunc(GL_ZERO,GL_ZERO);glEnable(GL_DEPTH_TEST);
        glEnable(GL_STENCIL_TEST);glEnable(GL_CULL_FACE);glEnable(0x8C89);glColorMask(GL_FALSE,GL_FALSE,GL_FALSE,GL_FALSE);
        CHECK(bridge.copy(0,image.texture,width,height)==gpu_bridge::Result::copied);
        control->packets[0].occurrence=image.occurrence;control->packets[0].qpc=image.qpc;
        InterlockedExchange(&control->packets[0].ready,1);
        CHECK(snapshots.release(ticket));reap_all(snapshots);
    });
    for(unsigned n=0;n<10000&&!InterlockedCompareExchange(&control->consumed,0,0);++n)Sleep(1);
    CHECK(control->consumed==1);
    worker.run([&]{CHECK(bridge.shutdown());CHECK(glGetError()==GL_NO_ERROR);snapshots.stop();reap_all(snapshots);CHECK(snapshots.destroy());});
    CHECK(wglMakeCurrent(nullptr,nullptr));UnmapViewOfFile(control);CloseHandle(mapping);
    printf("GPU fidelity completed source=%ux%u output=%ux%u opaque-alpha=yes producer-bits=%u\n",width,height,width&~1u,height&~1u,unsigned(sizeof(void*)*8));return 0;
}
