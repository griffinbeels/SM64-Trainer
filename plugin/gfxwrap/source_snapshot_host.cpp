#include "source_snapshot.h"
#include "gl_fixture.h"
namespace {
unsigned begins=0,ends=0;
bool __cdecl begin_capture(){++begins;return glGetError()==GL_NO_ERROR;}
bool __cdecl end_capture(){++ends;return glGetError()==GL_NO_ERROR;}
void front_pattern(unsigned id){
    glDrawBuffer(GL_FRONT); glEnable(GL_SCISSOR_TEST);
    for(unsigned y=0;y<64;++y) for(unsigned x=0;x<64;++x){
        glScissor(x,y,1,1);glClearColor(channel(id,x,y,0)/255.f,channel(id,x,y,1)/255.f,channel(id,x,y,2)/255.f,1.f);
        glClear(GL_COLOR_BUFFER_BIT);
    }
    glDrawBuffer(GL_BACK);
}
void verify_front(snapshot::Image image,unsigned id){
    glBindTexture(GL_TEXTURE_2D,image.texture); unsigned char bytes[cw*ch*4]{};
    glGetTexImage(GL_TEXTURE_2D,0,GL_RGBA,GL_UNSIGNED_BYTE,bytes);
    CHECK(glGetError()==GL_NO_ERROR);
    for(unsigned y=0;y<ch;++y)for(unsigned x=0;x<cw;++x)for(unsigned c=0;c<3;++c){
        const unsigned i=(y*cw+x)*4+c;
        if(bytes[i]!=channel(id,x,y+2,c)){fprintf(stderr,"front pixel mismatch x=%u y=%u c=%u actual=%u expected=%u\n",x,y,c,bytes[i],channel(id,x,y+2,c));ExitProcess(1);}
    }
}
}
int main(){
    WNDCLASSW wc{};wc.style=CS_OWNDC;wc.lpfnWndProc=DefWindowProcW;wc.hInstance=GetModuleHandleW(nullptr);wc.lpszClassName=L"SnapshotHiddenHost";
    CHECK(RegisterClassW(&wc));Window producer;CHECK(wglMakeCurrent(producer.dc,producer.rc));
    glDisable(GL_DITHER);glDisable(0x8DB9);glReadBuffer(GL_BACK);
    rb_surface surface{};surface.context=reinterpret_cast<uintptr_t>(producer.rc);
    surface.read_drawable=reinterpret_cast<uintptr_t>(producer.dc);surface.renderer_thread=GetCurrentThreadId();
    surface.context_generation=1;surface.drawable_generation=1;surface.width=cw;surface.height=ch;
    surface.source_format=RB_SOURCE_RGB8_LINEAR;surface.bottom_offset=2;surface.drawable_width=64;surface.drawable_height=64;surface.restore_read_buffer=GL_BACK;
    rs_api source{};source.bytes=sizeof(source);source.version=RS_ABI_V2;source.capabilities=RS_CAP_BOUNDARY|RS_CAP_SOURCE_FORMAT;
    source.begin_capture=begin_capture;source.end_capture=end_capture;
    source_capture::Snapshots capture;Worker worker(producer);snapshot::Gl gl;
    worker.run([&]{CHECK(gl.load());auto stale=source;stale.capabilities=RS_CAP_BOUNDARY;
        CHECK(!capture.prepare(gl,stale,surface,2,cw*ch*4*2));auto unsupported=surface;unsupported.source_format=RB_SOURCE_UNKNOWN;
        CHECK(!capture.prepare(gl,source,unsupported,2,cw*ch*4*2));CHECK(capture.prepare(gl,source,surface,2,cw*ch*4*2));});
    rb_record record{};record.occurrence=41;record.epoch=3;record.surface=surface;record.surface.boundary_qpc=1234567;
    record.stamp.bytes[0]=77;
    rb_image image{};auto changed=record;++changed.surface.drawable_generation;
    CHECK(!capture.submit(&changed,&image) && begins==0);
    changed=record;++changed.surface.context_generation;CHECK(!capture.submit(&changed,&image) && begins==0);
    changed=record;++changed.surface.width;CHECK(!capture.submit(&changed,&image) && begins==0);
    changed=record;++changed.surface.read_drawable;CHECK(!capture.submit(&changed,&image) && begins==0);
    changed=record;changed.surface.source_format=RB_SOURCE_RGB8_SRGB;CHECK(!capture.submit(&changed,&image) && begins==0);
    front_pattern(41);CHECK(capture.submit(&record,&image));CHECK(begins==1&&ends==1&&image.ownership==RB_IMAGE_SUBMITTED);
    const rb_image first=image;front_pattern(73); // original FRONT is overwritten before the worker consumes
    worker.run([&]{auto pixels=wait_image(capture.images(),{first.serial,first.slot});
        CHECK(pixels.occurrence==41 && pixels.qpc==1234567);verify_front(pixels,41);
        CHECK(!capture.completed(first));CHECK(capture.images().release(pixels.ticket));reap_all(capture.images());CHECK(capture.completed(first));});
    // Same retained FRONT, new immutable occurrence; no new draw/swap required.
    record.occurrence=42;CHECK(capture.submit(&record,&image));
    worker.run([&]{auto pixels=wait_image(capture.images(),{image.serial,image.slot});verify_front(pixels,73);
        CHECK(capture.images().release(pixels.ticket));reap_all(capture.images());});
    CHECK(record.stamp.bytes[0]==77);capture.stop();const unsigned checks=begins;
    CHECK(!capture.submit(&record,&image)&&begins==checks);
    worker.run([&]{CHECK(capture.destroy());});
    GLint read=0;glGetIntegerv(GL_READ_BUFFER,&read);CHECK(read==GL_BACK);
    CHECK(wglMakeCurrent(nullptr,nullptr));
    printf("source snapshot passed: FRONT overwrite and retained crop, stale descriptor refusal, custody, passive no-query\n");
    return 0;
}
