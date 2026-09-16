/* Test-only dedicated core GL worker; never creates a visible window. */
#pragma once
class BridgeWorker {
    Window window_;
    HANDLE requested_=CreateEventW(nullptr,FALSE,FALSE,nullptr);
    HANDLE done_=CreateEventW(nullptr,FALSE,FALSE,nullptr);
    std::function<void()> task_;
    std::thread thread_;
public:
    explicit BridgeWorker(Window &producer) {
        CHECK(requested_&&done_);
        using CreateContext=HGLRC(WINAPI *)(HDC,HGLRC,const int*);
        auto create=proc<CreateContext>("wglCreateContextAttribsARB");
        const int attributes[]={0x2091,4,0x2092,5,0x9126,1,0};
        HGLRC core=create(window_.dc,producer.rc,attributes);CHECK(core);
        CHECK(wglDeleteContext(window_.rc));window_.rc=core;
        thread_=std::thread([this]{
            CHECK(wglMakeCurrent(window_.dc,window_.rc));
            GLint profile=0;glGetIntegerv(0x9126,&profile);CHECK(profile&1);
            printf("worker_profile=core\n");
            for(;;){CHECK(WaitForSingleObject(requested_,10000)==WAIT_OBJECT_0);if(!task_)break;task_();SetEvent(done_);}
            CHECK(wglMakeCurrent(nullptr,nullptr));
        });
    }
    void run(std::function<void()>next){task_=std::move(next);SetEvent(requested_);CHECK(WaitForSingleObject(done_,10000)==WAIT_OBJECT_0);}
    ~BridgeWorker(){task_={};SetEvent(requested_);thread_.join();CloseHandle(requested_);CloseHandle(done_);}
};
