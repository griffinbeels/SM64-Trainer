/* Real GPU images + real boundary attachment, independently witnessed.
 * This FBO fixture does not certify LINK GL_FRONT or its source instrumentation. */
#include "gl_fixture.h"
#include "renderer_boundary.h"
#include "renderer_gl_state.h"
#include <cstring>

namespace {
snapshot::Pool capture_pool;
renderer::State renderer_state;
renderer::Identity identity{};
snapshot::Source capture_source{};
unsigned capture_generation = 0;
bool source_qualified = true, cancel_after_submit = false;
unsigned rendered_marker = 0, original_calls = 0, capture_callbacks = 0, capture_binds = 0;
void APIENTRY snapshot_bind(GLenum target, GLuint name) {
    ++capture_binds; actual_gl.bind_framebuffer(target,name);
}
int surface(rb_surface *out) {
    out->width=cw; out->height=ch; out->bottom_offset=2;
    out->context=identity.context; out->renderer_thread=identity.owner_thread;
    out->boundary_qpc=int64_t(rendered_marker)*103;
    return 1;
}
int image_capture(const rb_record *record, rb_image *out) {
    ++capture_callbacks;
    snapshot::RestoreBindings bindings{};
    if (!source_qualified || record->stamp.lists_since!=1 ||
        !renderer_state.default_bindings(identity,&bindings)) return 0;
    // Fixture source is a nonzero FBO with an independently established selector.
    // Production default-FRONT capture would use the unmodified state witness.
    bindings.source_read_buffer=GL_NONE;
    snapshot::Ticket ticket;
    const auto result=capture_pool.submit(capture_generation,capture_source,bindings,
                                          record->occurrence,record->surface.boundary_qpc,&ticket);
    out->status=uint32_t(result);
    if (result==snapshot::Result::fault) out->ownership=RB_IMAGE_QUARANTINED;
    if (result!=snapshot::Result::submitted) return 0;
    out->serial=ticket.serial; out->slot=ticket.slot; out->epoch=capture_generation;
    out->ownership=RB_IMAGE_SUBMITTED;
    if (cancel_after_submit) { cancel_after_submit=false; rb_disarm(); }
    return 1;
}
int image_completed(const rb_image *image) {
    return capture_pool.completed({image->serial,image->slot});
}
struct Command { virtual bool run() = 0; };
struct DrawCommand : Command {
    unsigned marker;
    explicit DrawCommand(unsigned n):marker(n){}
    __declspec(noinline) bool run() override {
        ++original_calls; rendered_marker=marker; draw(marker); return (marker&1)!=0;
    }
};
__declspec(noinline) bool invoke(Command *command) { return command->run(); }
rb_ticket id(const rb_record *record) { return {record->occurrence,record->slot}; }
snapshot::Ticket gpu(const rb_record *record) { return {record->image.serial,record->image.slot}; }
const rb_record *perform(unsigned marker, unsigned raw_counter, uint32_t expected_outcome=RB_OBSERVED) {
    rb_stamp stamp{}; stamp.lists_since=1; stamp.table_count=1; stamp.lengths[0]=8;
    uint32_t payload[]={raw_counter, marker^0xAC51u};
    std::memcpy(stamp.bytes,payload,sizeof(payload));
    const auto ticket=rb_stage(&stamp); CHECK(ticket.occurrence);
    std::memset(stamp.bytes,0xCC,sizeof(stamp.bytes)); // external RAM advances after offer
    DrawCommand command(marker); const auto before=original_calls;
    CHECK(invoke(&command)==bool(marker&1)); CHECK(original_calls==before+1);
    rb_finish(ticket);
    const auto record=rb_take(); CHECK(record && record->outcome==expected_outcome);
    CHECK(record->occurrence==ticket.occurrence);
    CHECK(std::memcmp(record->stamp.bytes,payload,sizeof(payload))==0);
    return record;
}
void state_witness_checks(BindFbo bind, GLuint first, GLuint second, GLuint texture,
                          Active active) {
    snapshot::RestoreBindings known{};
    auto verify_state=[&] {
        CHECK(renderer_state.default_bindings(identity,&known));
        GLint value=0;
        glGetIntegerv(0x8CAA,&value); CHECK(GLuint(value)==known.read_framebuffer);
        glGetIntegerv(GL_TEXTURE_BINDING_2D,&value); CHECK(GLuint(value)==known.texture_2d);
        bind(read_fbo,0); glGetIntegerv(GL_READ_BUFFER,&value); CHECK(GLenum(value)==known.source_read_buffer);
        bind(read_fbo,known.read_framebuffer); CHECK(glGetError()==GL_NO_ERROR);
    };
    verify_state();
    auto stale=identity; ++stale.context_generation;
    CHECK(!renderer_state.default_bindings(stale,&known));
    stale=identity; ++stale.drawable_generation; CHECK(!renderer_state.default_bindings(stale,&known));
    stale=identity; ++stale.owner_thread; CHECK(!renderer_state.default_bindings(stale,&known));
    bind(framebuffer,first); renderer_state.bind_framebuffer(framebuffer,first);
    glReadBuffer(attachment); renderer_state.read_buffer(attachment); verify_state();
    bind(draw_fbo,second); renderer_state.bind_framebuffer(draw_fbo,second); verify_state();
    bind(read_fbo,0); renderer_state.bind_framebuffer(read_fbo,0);
    glReadBuffer(GL_FRONT); renderer_state.read_buffer(GL_FRONT); verify_state();
    bind(read_fbo,first); renderer_state.bind_framebuffer(read_fbo,first);
    glReadBuffer(GL_NONE); renderer_state.read_buffer(GL_NONE); verify_state();
    // Cache invalidation is not GL state reset. It may only make capture unavailable.
    renderer_state.unknown(); CHECK(!renderer_state.default_bindings(identity,&known));
    renderer_state.bind_framebuffer(read_fbo,first);
    renderer_state.named_read_buffer(0,GL_FRONT); // actual default still FRONT
    active(texture3); renderer_state.active_texture(texture3);
    glBindTexture(GL_TEXTURE_2D,texture); renderer_state.bind_texture(GL_TEXTURE_2D,texture);
    verify_state();
    // Out-of-coverage unit refuses a witness without clamping to another unit.
    renderer_state.active_texture(0x84C0+renderer::State::texture_units);
    CHECK(!renderer_state.default_bindings(identity,&known));
    renderer_state.active_texture(texture3); verify_state();
    // Excessive deletion bookkeeping becomes unknown, never unbounded scan.
    renderer_state.delete_textures(1000,nullptr);
    CHECK(!renderer_state.default_bindings(identity,&known));
    renderer_state.bind_texture(GL_TEXTURE_2D,texture); verify_state();
    // Real deletion implicitly unbinds in this context, including every texture unit.
    GLuint temporary=0;
    glGenTextures(1,&temporary);
    for(GLenum u : {texture3, texture3+1}) {
        active(u); renderer_state.active_texture(u);
        glBindTexture(GL_TEXTURE_2D,temporary); renderer_state.bind_texture(GL_TEXTURE_2D,temporary);
    }
    glDeleteTextures(1,&temporary); renderer_state.delete_textures(1,&temporary);
    verify_state();
    active(texture3); renderer_state.active_texture(texture3); verify_state();
    glBindTexture(GL_TEXTURE_2D,texture); renderer_state.bind_texture(GL_TEXTURE_2D,texture);
    auto gen=proc<GenFbo>("glGenFramebuffers");
    auto remove=proc<void (APIENTRY *)(GLsizei,const GLuint *)>("glDeleteFramebuffers");
    gen(1,&temporary); bind(read_fbo,temporary); renderer_state.bind_framebuffer(read_fbo,temporary);
    remove(1,&temporary); renderer_state.delete_framebuffers(1,&temporary); verify_state();
    // Named default changes are tracked even when another READ FBO is current.
    bind(read_fbo,first); renderer_state.bind_framebuffer(read_fbo,first);
    auto named=proc<void (APIENTRY *)(GLuint,GLenum)>("glNamedFramebufferReadBuffer");
    named(0,GL_BACK); renderer_state.named_read_buffer(0,GL_BACK); verify_state();
    named(0,GL_FRONT); renderer_state.named_read_buffer(0,GL_FRONT); verify_state();
    // Restore fixture arrangement after independent state witnesses.
    bind(draw_fbo,first); renderer_state.bind_framebuffer(draw_fbo,first);
    bind(read_fbo,second); renderer_state.bind_framebuffer(read_fbo,second);
    verify_state();
}
}
int main(int argc,char **argv) {
    WNDCLASSW wc{}; wc.style=CS_OWNDC; wc.lpfnWndProc=DefWindowProcW;
    wc.hInstance=GetModuleHandleW(nullptr); wc.lpszClassName=L"SnapshotHiddenHost";
    CHECK(RegisterClassW(&wc)); Window producer;
    CHECK(wglMakeCurrent(producer.dc,producer.rc));
    identity={uintptr_t(producer.rc),uintptr_t(producer.dc),1,1,GetCurrentThreadId()};
    renderer_state.created(identity,true);
    printf("renderer=%s\n",glGetString(GL_RENDERER));
    auto gen=proc<GenFbo>("glGenFramebuffers"); auto bind=proc<BindFbo>("glBindFramebuffer");
    auto attach=proc<Attach>("glFramebufferTexture2D"); auto active=proc<Active>("glActiveTexture");
    auto check=proc<CheckFbo>("glCheckFramebufferStatus");
    GLuint textures[3]{},fbos[2]{}; glGenTextures(3,textures); gen(2,fbos);
    for(unsigned i=0;i<2;++i) {
        glBindTexture(GL_TEXTURE_2D,textures[i]); renderer_state.bind_texture(GL_TEXTURE_2D,textures[i]);
        glTexImage2D(GL_TEXTURE_2D,0,0x8058,sw,sh,0,GL_RGBA,GL_UNSIGNED_BYTE,nullptr);
        bind(framebuffer,fbos[i]); renderer_state.bind_framebuffer(framebuffer,fbos[i]);
        attach(framebuffer,attachment,GL_TEXTURE_2D,textures[i],0);
        glDrawBuffer(attachment); glReadBuffer(attachment); renderer_state.read_buffer(attachment);
        CHECK(check(framebuffer)==complete);
    }
    glDisable(GL_DITHER); glDisable(0x8DB9);
    state_witness_checks(bind,fbos[0],fbos[1],textures[2],active);
    capture_source={fbos[0],attachment,1,2,cw,ch,sw,sh};
    snapshot::Gl api;
    Worker worker(producer);
    worker.run([&] { CHECK(api.load(false)); actual_gl=api; api.wait=wait_probe; api.fence=fence_probe; api.bind_framebuffer=snapshot_bind;
        CHECK(capture_pool.prepare(api,producer.rc,cw,ch,4,768)); });
    capture_generation=capture_pool.generation();
    CHECK(rb_configure_images(image_capture,image_completed));
    DrawCommand prototype(1); auto vtable=*reinterpret_cast<void ***>(&prototype);
    CHECK(rb_install_test(vtable,vtable[0],surface)==RB_INSTALLED);
    CHECK(!rb_configure_images(image_capture,image_completed));
    rb_rom_open(); CHECK(rb_activate());

    const rb_record *records[4]{};
    for(unsigned i=0;i<4;++i) records[i]=perform(31+i,100+(i%2));
    DrawCommand overwrite(240); CHECK(invoke(&overwrite)==false);
    CHECK(original_calls==5);
    const auto extra=perform(90,100,RB_SURFACE_FAILED); // GPU full, original still rendered
    CHECK(extra->image.ownership==RB_IMAGE_NONE && rb_release(id(extra)));
    worker.run([&] {
        for(int i=3;i>=0;--i) {
            CHECK(!rb_release(id(records[i])));
            auto image=wait_image(capture_pool,gpu(records[i]));
            CHECK(image.occurrence==records[i]->occurrence);
            CHECK(image.qpc==int64_t(31+i)*103); verify_pixels(image,31+unsigned(i));
            CHECK(capture_pool.release(gpu(records[i])));
        }
        hold_return.store(true); capture_pool.reap();
        for(auto record:records) CHECK(!rb_release(id(record)));
        hold_return.store(false); reap_all(capture_pool);
    });
    // Reuse GPU slot while OLD metadata is still borrowed, with repeated raw counter.
    const auto reused=perform(71,100); CHECK(reused->image.ownership==RB_IMAGE_SUBMITTED);
    worker.run([&] {
        for(unsigned i=0;i<3;++i) CHECK(rb_release(id(records[i])));
        CHECK(records[3]->image.slot==3); // keep high slot through smaller reprepare
        auto image=wait_image(capture_pool,gpu(reused)); verify_pixels(image,71);
        CHECK(capture_pool.release(gpu(reused))); reap_all(capture_pool);
        CHECK(rb_release(id(reused)));
    });
    cancel_after_submit=true;
    const auto cancelled=perform(81,100,RB_RETIRED);
    CHECK(cancelled->image.ownership==RB_IMAGE_SUBMITTED);
    worker.run([&] {
        CHECK(!rb_release(id(cancelled)));
        auto image=wait_image(capture_pool,gpu(cancelled)); verify_pixels(image,81);
        CHECK(capture_pool.release(gpu(cancelled))); reap_all(capture_pool);
        capture_pool.stop(); CHECK(capture_pool.destroy());
        CHECK(capture_pool.prepare(api,producer.rc,cw,ch,1,192));
        CHECK(rb_release(id(cancelled)));
        CHECK(rb_release(id(records[3]))); // old slot3 retires across new count1
    });
    capture_generation=capture_pool.generation(); CHECK(rb_activate());
    const auto count_before=capture_pool.counters().submitted;
    const auto binds_before=capture_binds;
    source_qualified=false;
    const auto missing=perform(82,100,RB_SURFACE_FAILED);
    CHECK(missing->image.ownership==RB_IMAGE_NONE && rb_release(id(missing)));
    CHECK(capture_pool.counters().submitted==count_before && capture_binds==binds_before);
    source_qualified=true;
    renderer_state.unknown();
    const auto unknown=perform(83,100,RB_SURFACE_FAILED);
    CHECK(unknown->image.ownership==RB_IMAGE_NONE && rb_release(id(unknown)));
    CHECK(capture_pool.counters().submitted==count_before && capture_binds==binds_before);
    // Reconstruct only state actually known to still hold in this isolated host.
    renderer_state.bind_framebuffer(read_fbo,fbos[1]); renderer_state.named_read_buffer(0,GL_FRONT);
    renderer_state.active_texture(texture3); renderer_state.bind_texture(GL_TEXTURE_2D,textures[2]);
    if(argc>1 && strcmp(argv[1],"quarantine")==0) {
        fail_fence.store(true);
        const auto broken=perform(84,100,RB_SURFACE_FAILED);
        CHECK(broken->image.ownership==RB_IMAGE_QUARANTINED && !broken->image.serial);
        worker.run([&] { CHECK(!rb_release(id(broken))); CHECK(!capture_pool.destroy()); });
        CHECK(capture_pool.generation()==0);
        printf("quarantine attachment retained=yes\n");
    } else worker.run([&] { capture_pool.stop(); CHECK(capture_pool.destroy()); });
    rb_rom_closed(); rb_close(); renderer_state.lost();
    snapshot::RestoreBindings gone{}; CHECK(!renderer_state.default_bindings(identity,&gone));
    CHECK(glGetError()==GL_NO_ERROR);
    CHECK(wglMakeCurrent(nullptr,nullptr));
    printf("capture-chain passed: exact-pictures=6 repeated-counters=yes cancel-owned=yes return-fence-guard=yes old-pool-retirement=yes\n");
    return 0;
}
