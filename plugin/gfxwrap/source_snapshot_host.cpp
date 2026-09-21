#include "source_snapshot.h"
#include "gl_fixture.h"
namespace {
unsigned begins=0,ends=0;
rs_source_descriptor3 clean_descriptor{};
uint64_t query_occurrence=0;
int __cdecl clean_query(rb_ticket ticket,rs_source_descriptor3* out){
    if(ticket.occurrence!=query_occurrence || ticket.slot!=2)return 0;
    *out=clean_descriptor;return 1;
}
void __cdecl clean_consumer(bool){}
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
    // SourceV3 reads an explicitly ticket-bound private FBO even when the
    // displayed FRONT contains a different image. Immediate ownership keeps
    // it stable after the renderer reuses its clean texture.
    const auto gen_fbo=proc<GenFbo>("glGenFramebuffers"),delete_fbo=proc<GenFbo>("glDeleteFramebuffers");
    const auto bind_fbo=proc<BindFbo>("glBindFramebuffer");const auto attach=proc<Attach>("glFramebufferTexture2D");
    GLuint clean_fbo=0,clean_texture=0;gen_fbo(1,&clean_fbo);glGenTextures(1,&clean_texture);
    glBindTexture(GL_TEXTURE_2D,clean_texture);std::vector<unsigned char> clean_bytes(64*64*4,255);
    for(unsigned y=0;y<64;++y)for(unsigned x=0;x<64;++x)for(unsigned c=0;c<3;++c)clean_bytes[(y*64+x)*4+c]=channel(97,x,y,c);
    glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA8,64,64,0,GL_RGBA,GL_UNSIGNED_BYTE,clean_bytes.data());
    bind_fbo(framebuffer,clean_fbo);attach(framebuffer,attachment,GL_TEXTURE_2D,clean_texture,0);glReadBuffer(attachment);glDrawBuffer(attachment);
    CHECK(proc<CheckFbo>("glCheckFramebufferStatus")(framebuffer)==complete);bind_fbo(framebuffer,0);glReadBuffer(GL_BACK);
    clean_descriptor={};clean_descriptor.bytes=sizeof(clean_descriptor);clean_descriptor.version=RS_ABI_V3;
    clean_descriptor.kind=RS_SOURCE_CLEAN_VI;clean_descriptor.framebuffer=clean_fbo;clean_descriptor.read_buffer=attachment;
    clean_descriptor.y=surface.bottom_offset;clean_descriptor.width=cw;clean_descriptor.height=ch;
    clean_descriptor.surface_width=clean_descriptor.surface_height=64;clean_descriptor.source_format=RB_SOURCE_RGB8_LINEAR;
    clean_descriptor.context=surface.context;clean_descriptor.read_drawable=surface.read_drawable;
    clean_descriptor.context_generation=clean_descriptor.drawable_generation=clean_descriptor.resource_generation=1;
    clean_descriptor.renderer_thread=surface.renderer_thread;clean_descriptor.presentation_serial=8;clean_descriptor.update_occurrence=9;
    rs_api_v3 source3{sizeof(rs_api_v3),RS_ABI_V3,RS_CAP_BOUNDARY|RS_CAP_SOURCE_FORMAT|RS_CAP_CLEAN_PRESENTATION,0,source,clean_query,clean_consumer};
    worker.run([&]{CHECK(capture.prepare(gl,source,surface,2,cw*ch*4*2,&source3));});
    record.occurrence=query_occurrence=97;record.slot=2;record.surface.restore_texture_2d=clean_texture;
    const unsigned before_queries=begins;
    ++clean_descriptor.context_generation;CHECK(!capture.submit(&record,&image));--clean_descriptor.context_generation;
    clean_descriptor.kind=RS_SOURCE_NO_NEW_PRESENTATION;CHECK(!capture.submit(&record,&image));clean_descriptor.kind=RS_SOURCE_CLEAN_VI;
    clean_descriptor.read_buffer=GL_FRONT;CHECK(!capture.submit(&record,&image));clean_descriptor.read_buffer=attachment;
    CHECK(begins==before_queries);front_pattern(222);CHECK(capture.submit(&record,&image));
    const auto clean_owned=image;clean_bytes.assign(clean_bytes.size(),0);glBindTexture(GL_TEXTURE_2D,clean_texture);
    glTexSubImage2D(GL_TEXTURE_2D,0,0,0,64,64,GL_RGBA,GL_UNSIGNED_BYTE,clean_bytes.data());
    worker.run([&]{auto owned=wait_image(capture.images(),{clean_owned.serial,clean_owned.slot});verify_front(owned,97);
        CHECK(capture.images().release(owned.ticket));reap_all(capture.images());});
    bind_fbo(read_fbo,clean_fbo);GLint private_selector=0;glGetIntegerv(GL_READ_BUFFER,&private_selector);CHECK(private_selector==GLint(attachment));
    bind_fbo(read_fbo,0);glGetIntegerv(GL_READ_BUFFER,&private_selector);CHECK(private_selector==GL_BACK);
    capture.stop();worker.run([&]{CHECK(capture.destroy());});delete_fbo(1,&clean_fbo);glDeleteTextures(1,&clean_texture);
    GLint read=0;glGetIntegerv(GL_READ_BUFFER,&read);CHECK(read==GL_BACK);
    CHECK(wglMakeCurrent(nullptr,nullptr));
    printf("source snapshot passed: FRONT overwrite and retained crop, stale descriptor refusal, custody, passive no-query; V3 clean FBO/crop/ownership verified\n");
    return 0;
}
