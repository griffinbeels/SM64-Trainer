#include "gl_fixture.h"
void APIENTRY invalid_copy(GLuint texture,GLint level,GLint dx,GLint dy,GLint sx,GLint sy,GLsizei w,GLsizei h) {
    CHECK(actual_gl.copy_texture);
    // Actual driver copy, destination width exceeds its immutable allocation.
    actual_gl.copy_texture(texture,level,dx,dy,sx,sy,w+1,h);
}
int main(int argc, char **argv) {
    WNDCLASSW wc{}; wc.style = CS_OWNDC; wc.lpfnWndProc = DefWindowProcW;
    wc.hInstance = GetModuleHandleW(nullptr); wc.lpszClassName = L"SnapshotHiddenHost";
    CHECK(RegisterClassW(&wc));
    Window producer;
    CHECK(wglMakeCurrent(producer.dc, producer.rc));
    printf("vendor=%s\nrenderer=%s\nversion=%s\n", glGetString(GL_VENDOR),
           glGetString(GL_RENDERER), glGetString(GL_VERSION));
    auto gen_fbo = proc<GenFbo>("glGenFramebuffers");
    auto bind_fbo = proc<BindFbo>("glBindFramebuffer");
    auto attach = proc<Attach>("glFramebufferTexture2D");
    auto check_fbo = proc<CheckFbo>("glCheckFramebufferStatus");
    auto active = proc<Active>("glActiveTexture");
    glDisable(GL_DITHER); glDisable(0x8DB9); // framebuffer sRGB; deliberate linear RGBA8 fixture
    GLuint textures[3]{}, fbos[2]{};
    glGenTextures(3, textures); gen_fbo(2, fbos);
    for (unsigned i = 0; i < 2; ++i) {
        glBindTexture(GL_TEXTURE_2D, textures[i]);
        glTexImage2D(GL_TEXTURE_2D, 0, 0x8058, sw, sh, 0, GL_RGBA, GL_UNSIGNED_BYTE, nullptr);
        bind_fbo(framebuffer, fbos[i]);
        attach(framebuffer, attachment, GL_TEXTURE_2D, textures[i], 0);
        glReadBuffer(attachment); glDrawBuffer(attachment);
        CHECK(check_fbo(framebuffer) == complete);
    }
    bind_fbo(framebuffer, fbos[0]); glReadBuffer(GL_NONE);
    bind_fbo(read_fbo, fbos[1]);
    active(texture3); glBindTexture(GL_TEXTURE_2D, textures[2]);
    CHECK(glGetError() == GL_NO_ERROR);
    GLint producer_major=0, producer_minor=0;
    glGetIntegerv(0x821B,&producer_major); glGetIntegerv(0x821C,&producer_minor);
    const bool producer_dsa = producer_major>4 || (producer_major==4 && producer_minor>=5);
    const bool bound_copy = argc>1 && strcmp(argv[1],"bound-copy")==0;
    snapshot::Pool pool;
    Worker worker(producer);
    snapshot::Gl api;
    worker.run([&] {
        CHECK(api.load(producer_dsa && !bound_copy));
        if (bound_copy) CHECK(!api.copy_texture);
        printf("copy_api=%s\n",api.copy_texture ? "direct-state-access" : "bound-texture");
        actual_gl = api; api.fence = fence_probe; api.wait = wait_probe;
        GLuint pbo=0;
        auto gen_buffers=proc<GenFbo>("glGenBuffers");
        auto bind_buffer=proc<BindBuffer>("glBindBuffer");
        gen_buffers(1,&pbo); bind_buffer(0x88EC,pbo);
        CHECK(!pool.prepare(api,producer.rc,cw,ch,4,768));
        GLint bound=0; glGetIntegerv(0x88EF,&bound); CHECK(GLuint(bound)==pbo);
        bind_buffer(0x88EC,0);
        proc<void (APIENTRY *)(GLsizei,const GLuint *)>("glDeleteBuffers")(1,&pbo);
        CHECK(!pool.prepare(api, producer.rc, cw, ch, 4, cw*ch*4*4-1));
        CHECK(!pool.prepare(api, producer.rc, cw, ch, snapshot::max_slots+1, UINT64_MAX));
    });
    prep_entered=CreateEventW(nullptr,FALSE,FALSE,nullptr);
    prep_resume=CreateEventW(nullptr,FALSE,FALSE,nullptr);
    CHECK(prep_entered && prep_resume);
    pool.before_activate=pause_preparation;
    std::thread cancel_preparation([&] {
        CHECK(WaitForSingleObject(prep_entered,5000)==WAIT_OBJECT_0);
        CHECK(pool.generation()==0); pool.stop(); SetEvent(prep_resume);
    });
    worker.run([&] { CHECK(!pool.prepare(api,producer.rc,cw,ch,4,768)); });
    cancel_preparation.join(); pool.before_activate=nullptr;
    CloseHandle(prep_entered); CloseHandle(prep_resume);
    CHECK(pool.generation()==0);
    worker.run([&] {
        CHECK(pool.prepare(api, producer.rc, cw, ch, 4, cw*ch*4*4));
        CHECK(pool.counters().texture_bytes == 768);
    });
    pool.submission_probe=mark_phase;
    const unsigned epoch = pool.generation();
    snapshot::Source source{fbos[0], attachment, 1, 2, cw, ch, sw, sh};
    const snapshot::RestoreBindings bindings{fbos[1], textures[2], GL_NONE};
    snapshot::Ticket tickets[4];
    LARGE_INTEGER freq{}, start{}, end{}; QueryPerformanceFrequency(&freq);
    double max_submit_us = 0, sum_submit_us = 0, max_full_us = 0;
    unsigned submits = 0;
    auto submit = [&](unsigned occurrence, snapshot::Ticket *ticket) {
        QueryPerformanceCounter(&start);
        const auto result = pool.submit(epoch, source, bindings, occurrence, int64_t(occurrence)*101, ticket);
        QueryPerformanceCounter(&end);
        const double us = double(end.QuadPart-start.QuadPart)*1e6/double(freq.QuadPart);
        if (result == snapshot::Result::submitted) { max_submit_us=(std::max)(max_submit_us,us); sum_submit_us+=us; ++submits; }
        if (result == snapshot::Result::submitted) {
            printf("occurrence=%u submit_us=%.3f",occurrence,us);
            const char *names[]={"save_bind","copy","restore","fence","flush"};
            for (unsigned k=0;k<5;++k) printf(" %s_us=%.3f",names[k],
                double(phases[k+1].QuadPart-phases[k].QuadPart)*1e6/double(freq.QuadPart));
            printf("\n");
        }
        if (result == snapshot::Result::full) max_full_us=(std::max)(max_full_us,us);
        return result;
    };
    for (unsigned round = 0; round < 4; ++round) {
        // Worker is idle through this entire producer loop. It cannot aid admission.
        for (unsigned i = 0; i < 4; ++i) {
            const unsigned occurrence = 1+round*4+i;
            draw(occurrence);
            CHECK(submit(occurrence, &tickets[i]) == snapshot::Result::submitted);
            GLint value=0; glGetIntegerv(0x8CAA, &value); CHECK(GLuint(value)==fbos[1]);
            glGetIntegerv(0x8CA6, &value); CHECK(GLuint(value)==fbos[0]);
            glGetIntegerv(GL_READ_BUFFER, &value); CHECK(value==attachment);
            glGetIntegerv(GL_TEXTURE_BINDING_2D, &value); CHECK(GLuint(value)==textures[2]);
            glGetIntegerv(0x84E0, &value); CHECK(value==texture3);
            bind_fbo(read_fbo, fbos[0]); glGetIntegerv(GL_READ_BUFFER, &value); CHECK(value==GL_NONE);
            bind_fbo(read_fbo, fbos[1]); CHECK(glGetError()==GL_NO_ERROR);
        }
        draw(210+round); // overwrite source before any consumer accesses a snapshot
        for (unsigned i=0; i<489; ++i) {
            snapshot::Ticket rejected;
            CHECK(submit(999, &rejected)==snapshot::Result::full && !rejected.serial);
        }
        snapshot::Image images[4];
        worker.run([&] {
            for (int i=3; i>=0; --i) { images[i]=wait_image(pool,tickets[i]); verify(images[i],1+round*4+unsigned(i)); }
        });
        // All producer fences are complete, but worker still owns every texture.
        draw(240+round);
        for (unsigned i=0; i<10; ++i) {
            snapshot::Ticket rejected;
            CHECK(submit(999,&rejected)==snapshot::Result::full);
        }
        worker.run([&] {
            for (unsigned i=0;i<4;++i) verify(images[i],1+round*4+i);
            for (unsigned i=0;i<4;++i) CHECK(pool.release(tickets[i]));
            CHECK(!pool.release(tickets[0]));
            hold_return.store(true); pool.reap();
        });
        snapshot::Ticket pending_return;
        CHECK(submit(999,&pending_return)==snapshot::Result::full);
        worker.run([&] {
            hold_return.store(false); reap_all(pool);
            CHECK(pool.poll(tickets[0], &images[0])==snapshot::Poll::stale);
        });
    }
    snapshot::Ticket invalid;
    auto bad=source; bad.y=9;
    CHECK(pool.submit(epoch,bad,bindings,777,0,&invalid)==snapshot::Result::invalid);
    CHECK(!invalid.serial && glGetError()==GL_NO_ERROR);
    // Final picture: intentionally NO producer GL calls after submission until worker finishes.
    draw(55);
    CHECK(submit(55, &tickets[0])==snapshot::Result::submitted);
    worker.run([&] {
        auto image=wait_image(pool,tickets[0]); verify(image,55);
        pool.stop(); CHECK(!pool.destroy()); // borrowed texture cannot be reclaimed
        CHECK(pool.release(tickets[0])); reap_all(pool); CHECK(pool.destroy());
        CHECK(pool.prepare(api,producer.rc,cw,ch,4,768));
    });
    // Old caller and stale release cannot operate on a newly prepared generation.
    CHECK(pool.submit(epoch,source,bindings,56,5656,&invalid)==snapshot::Result::inactive);
    worker.run([&] { CHECK(!pool.release(tickets[0])); pool.stop(); CHECK(pool.destroy()); });
    printf("gpu-witness passed: pictures=17 bytes=768 full=2000 crop=1,2 state=preserved final-without-next-render=yes\n");
    printf("submission_us_mean=%.3f submission_us_max=%.3f full_refusal_us_max=%.3f\n",
           sum_submit_us/submits,max_submit_us,max_full_us);
    if (argc > 1 && !bound_copy) {
        if (strcmp(argv[1],"copy-error")==0) api.copy_texture=invalid_copy;
        worker.run([&] { CHECK(pool.prepare(api,producer.rc,cw,ch,4,768)); });
        const unsigned fault_epoch=pool.generation();
        draw(77);
        const bool producer_failure=strcmp(argv[1],"producer-fence")==0;
        const bool pre_error=strcmp(argv[1],"source-preerror")==0;
        const bool copy_error=strcmp(argv[1],"copy-error")==0;
        const bool restore_error=strcmp(argv[1],"restore-error")==0;
        auto attempted_source=source; auto attempted_bindings=bindings;
        if (producer_failure) fail_fence.store(true);
        if (pre_error) glEnable(0xFFFF); // real error before ANY capture work
        if (restore_error) attempted_bindings.source_read_buffer=0xFFFF;
        const auto offered_before=pool.counters().submitted;
        const auto result=pool.submit(fault_epoch,attempted_source,attempted_bindings,77,7777,&tickets[0]);
        if (pre_error) {
            CHECK(result==snapshot::Result::unqualified && !tickets[0].serial);
            CHECK(pool.counters().submitted==offered_before && pool.generation()==0);
            worker.run([&] { CHECK(pool.destroy()); });
            printf("source-preflight refused-before-copy clean-retirement=yes\n");
            return 0;
        }
        if (producer_failure || copy_error || restore_error) {
            CHECK(result==snapshot::Result::fault && !tickets[0].serial);
            CHECK(pool.counters().submitted==offered_before);
            worker.run([&] { snapshot::Image unavailable{};
                CHECK(pool.poll(tickets[0],&unavailable)==snapshot::Poll::stale); });
        }
        else {
            CHECK(result==snapshot::Result::submitted);
            worker.run([&] {
                if (strcmp(argv[1],"worker-wait")==0) {
                    fail_wait.store(true); snapshot::Image image{};
                    CHECK(pool.poll(tickets[0],&image)==snapshot::Poll::fault);
                } else {
                    wait_image(pool,tickets[0]); fail_fence.store(true);
                    CHECK(!pool.release(tickets[0]));
                }
            });
        }
        CHECK(pool.generation()==0);
        CHECK(pool.submit(fault_epoch,source,bindings,78,7878,&invalid)==snapshot::Result::inactive);
        worker.run([&] {
            CHECK(!pool.destroy() && pool.counters().faults==1);
            CHECK(!pool.prepare(api,producer.rc,cw,ch,4,768));
        });
        printf("completion-failure quarantined=%s bytes=768 no-reuse=yes\n",argv[1]);
    }
    // No normal plugin, desktop capture, encoder, or GL_FRONT correctness claim here.
    CHECK(glGetError()==GL_NO_ERROR);
    CHECK(wglMakeCurrent(nullptr,nullptr));
    (void)argc; (void)argv;
    return 0;
}
