/* Bounded source order at slot wrap and delayed renderer completion. */
#include "renderer_boundary.h"
#include <stdio.h>
#define CHECK(x) do { if(!(x)){fprintf(stderr,"source order line %d: %s\n",__LINE__,#x);return 1;} }while(0)
int surface(rb_surface *s){*s={};s->width=640;s->height=480;return 1;}
rb_stamp stamp{};
rb_ticket ready(){const auto t=rb_stage(&stamp);rb_finish(t);return t;}
rb_ticket preempted[2]{};
void produce_during_scan(){rb_test_set_take_probe(nullptr);preempted[0]=ready();preempted[1]=ready();}
int main(){
    CHECK(rb_bind_source(surface)==RB_INSTALLED);rb_rom_open();CHECK(rb_activate());
    // Move allocation to the end, then let three undelivered records cross slot0.
    for(unsigned n=0;n<RB_SLOTS-2;++n){const auto t=ready();const auto r=rb_take();CHECK(r&&r->occurrence==t.occurrence);CHECK(rb_release(t));}
    rb_ticket wrapped[3]={ready(),ready(),ready()};
    CHECK(wrapped[0].slot==RB_SLOTS-2 && wrapped[2].slot==0);
    for(auto t:wrapped){const auto r=rb_take();CHECK(r&&r->occurrence==t.occurrence);CHECK(r->outcome==RB_NOT_OBSERVED);}
    CHECK(!rb_take()); // borrowed records may coexist without recycling
    for(auto t:wrapped)CHECK(rb_release(t));
    // A real source callback retains its storage even after abnormal early finish.
    const auto slow=rb_stage(&stamp);CHECK(slow.occurrence);
    CHECK(rb_source_begin(slow).occurrence==slow.occurrence);rb_finish(slow);
    const auto later=ready();CHECK(later.occurrence>slow.occurrence);
    for(unsigned n=0;n<1000;++n)CHECK(!rb_take());
    rb_source_end(slow);
    const auto first=rb_take();CHECK(first&&first->occurrence==slow.occurrence&&first->outcome==RB_OBSERVED);
    const auto second=rb_take();CHECK(second&&second->occurrence==later.occurrence);
    CHECK(rb_release(later));CHECK(rb_release(slow));
    // Cancellation preserves the same ordering and explicit retired outcome.
    const auto cancelled=rb_stage(&stamp);CHECK(rb_source_begin(cancelled).occurrence);rb_finish(cancelled);
    rb_disarm();CHECK(rb_activate());const auto new_epoch=ready();CHECK(!rb_take());
    rb_source_end(cancelled);
    const auto retired=rb_take();CHECK(retired&&retired->occurrence==cancelled.occurrence&&retired->outcome==RB_RETIRED);
    const auto fresh=rb_take();CHECK(fresh&&fresh->occurrence==new_epoch.occurrence&&fresh->epoch!=retired->epoch);
    CHECK(rb_release(cancelled));CHECK(rb_release(new_epoch));CHECK(!rb_take());
    // Consumer observed slot0 FREE, then producer publishes slot0 and slot1.
    // Returning slot1 would skip an already admitted older occurrence.
    for(unsigned n=0;n<RB_SLOTS;++n){const auto t=ready();const auto item=rb_take();
        CHECK(item&&item->occurrence==t.occurrence);CHECK(rb_release(t));if(t.slot==RB_SLOTS-1)break;}
    rb_test_set_take_probe(produce_during_scan);CHECK(!rb_take());
    CHECK(preempted[0].occurrence && preempted[0].slot==0 && preempted[1].slot==1);
    for(auto t:preempted){const auto item=rb_take();CHECK(item&&item->occurrence==t.occurrence);CHECK(rb_release(t));}
    rb_rom_closed();rb_close();
    puts("source order passed: wrap, delayed head, independent custody, retired epoch, scan preemption");return 0;
}
