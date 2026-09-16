/* Actual patched LINK command/method bodies are included by the source test. */
#include "link_source_api.h"
#include <stdio.h>
#include <thread>
#include <mutex>
#include <condition_variable>
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"source boundary line %d: %s\n",__LINE__,#x); ExitProcess(1); } } while(0)
#define LOG(...) ((void)0)
#define LOG_APIFUNC 0
class APICommand { public: virtual bool run()=0; };
std::mutex queue_mutex;
std::condition_variable queue_changed;
APICommand *queued=nullptr;
bool done=false,quit=false;
unsigned original_calls=0,observed_calls=0,expected_byte=0,planned_swaps=0;
bool cancel_inside=false;
DWORD producer_thread=0,render_thread=0;
rb_surface visible{};
void VI_UpdateScreen() {
    CHECK(GetCurrentThreadId()!=producer_thread);
    ++original_calls; visible.width=640; visible.height=480;
    visible.post_vi_origin=original_calls; visible.swap_count+=planned_swaps;
    visible.witness=original_calls; render_thread=GetCurrentThreadId();
    if(cancel_inside) rb_disarm();
}
class PluginAPI {
public:
    void UpdateScreenCaptured(rb_ticket request);
    void _callAPICommand(APICommand &cmd) {
        std::unique_lock<std::mutex> lock(queue_mutex);
        queued=&cmd; done=false; queue_changed.notify_all();
        queue_changed.wait(lock,[]{return done;}); queued=nullptr;
    }
};
PluginAPI &api(){static PluginAPI value;return value;}
#include "source_update_command.inc"
int surface(rb_surface *out) { *out=visible; return 1; }
int capture(const rb_record *record,rb_image *image) {
    CHECK(record->stamp.bytes[0]==expected_byte);
    CHECK(record->surface.witness==original_calls && original_calls>observed_calls);
    CHECK(record->surface.post_vi_origin==original_calls);
    ++observed_calls; *image={record->occurrence,0,record->epoch,RB_IMAGE_SUBMITTED,0};return 1;
}
int complete(const rb_image *){return 1;}
int main(){
    producer_thread=GetCurrentThreadId();
    CHECK(rb_configure_images(capture,complete)); CHECK(rb_bind_source(surface)==RB_INSTALLED);
    CHECK(rb_bind_source(surface)==RB_ALREADY_INSTALLED);
    rb_rom_open(); CHECK(rb_activate());
    std::thread worker([]{
        std::unique_lock<std::mutex> lock(queue_mutex);
        for(;;){queue_changed.wait(lock,[]{return (queued&&!done)||quit;});if(quit)break;
            auto cmd=queued;lock.unlock();CHECK(cmd->run());lock.lock();done=true;queue_changed.notify_all();}
    });
    rb_stamp pending{}; pending.table_count=1; pending.lengths[0]=1; pending.bytes[0]=42;
    pending.lists_since=2;
    rb_ticket old{};
    for(unsigned swaps: {0u,1u,3u}){
        planned_swaps=swaps; expected_byte=pending.bytes[0];
        const auto token=rb_stage(&pending);CHECK(token.occurrence);
        rs_request request{sizeof(rs_request),RS_ABI_V2,{0,0},token};
        ++pending.bytes[0]; // caller's mutable pending bytes no longer own this occurrence
        const auto before=original_calls; update(&request);rb_finish(token);
        CHECK(original_calls==before+1 && render_thread!=producer_thread);
        const auto record=rb_take();CHECK(record && record->outcome==RB_OBSERVED);
        CHECK(record->stamp.bytes[0]==expected_byte && record->stamp.lists_since==pending.lists_since);
        CHECK(rb_release(token));old=token;pending.lists_since=0; // retained bytes, fresh occurrence
    }
    const auto before=original_calls;
    rs_request malformed{sizeof(rs_request),RS_ABI_V2,{1,0},old};
    update(&malformed);update(nullptr);
    rs_request stale{sizeof(rs_request),RS_ABI_V2,{0,0},old};update(&stale);
    rs_request old_version{sizeof(rs_request),1,{0,0},old};update(&old_version);
    CHECK(original_calls==before+4 && observed_calls==3 && !rb_take());
    expected_byte=pending.bytes[0];const auto token=rb_stage(&pending);
    rs_request cancelled{sizeof(rs_request),RS_ABI_V2,{0,0},token};
    cancel_inside=true;update(&cancelled);rb_finish(token);cancel_inside=false;
    const auto retired=rb_take();CHECK(retired && retired->outcome==RB_RETIRED && rb_release(token));
    CHECK(observed_calls==3);
    CHECK(rb_activate());
    rb_ticket held[RB_SLOTS]{};
    for(auto &item:held){item=rb_stage(&pending);CHECK(item.occurrence);rs_request request{sizeof(rs_request),RS_ABI_V2,{0,0},item};update(&request);rb_finish(item);CHECK(rb_take());}
    CHECK(!rb_stage(&pending).occurrence);
    const auto full_before=original_calls;update(nullptr);CHECK(original_calls==full_before+1);
    for(auto item:held)CHECK(rb_release(item));
    rb_rom_closed();rb_close();
    {std::lock_guard<std::mutex> lock(queue_mutex);quit=true;queue_changed.notify_all();}
    worker.join();
    printf("source boundary passed: original-once immutable-token retained-stamp 0-1-multi-swap malformed stale cancel full\n");
}
