/* Streams scalar traces through the real allocation-free clock. No Windows,
 * game assets, capture/player ABI, renderer stub, graphics or visible window. */
#include "ghost_clock.h"
#include <atomic>
#include <cstdlib>
#include <iostream>
#include <new>

static bool checking=false;
static unsigned allocations=0,frees=0,steps=0;
void* operator new(size_t bytes) {
    if(checking)++allocations;
    if(void* value=std::malloc(bytes?bytes:1))return value;
    throw std::bad_alloc();
}
void* operator new[](size_t bytes){return ::operator new(bytes);}
void operator delete(void* value) noexcept {if(checking && value)++frees;std::free(value);}
void operator delete[](void* value) noexcept {::operator delete(value);}
void operator delete(void* value,size_t) noexcept {::operator delete(value);}
void operator delete[](void* value,size_t) noexcept {::operator delete(value);}
int main() {
    gcc_config config{};config.version=GCC_VERSION;
    std::cin>>config.near_zero>>config.area_window>>config.teleport_window>>config.level_tail
        >>config.warp_window>>config.teleport_action>>config.wrap_ceiling>>config.castle_count>>config.warp_count;
    if(config.castle_count>GCC_LIST_MAX || config.warp_count>GCC_LIST_MAX)return 2;
    for(uint32_t i=0;i<config.castle_count;++i)std::cin>>config.castle_levels[i];
    for(uint32_t i=0;i<config.warp_count;++i)std::cin>>config.warp_ops[i];
    gcc_clock state{};
    if(!std::cin || !gcc_init(&state,&config))return 3;
    char operation;
    while(std::cin>>operation) {
        if(operation=='N'){checking=true;gcc_init(&state,&config);checking=false;continue;}
        if(operation=='R'){checking=true;gcc_reset(&state);checking=false;continue;}
        if(operation=='O'){state.origin=UINT64_MAX;continue;}
        if(operation=='L'){state.leg=UINT64_MAX;continue;}
        if(operation=='B'){state.banked=UINT64_MAX;continue;}
        if(operation=='E'){state.elapsed=UINT64_MAX;continue;}
        if(operation=='C'){state.continuity=UINT64_MAX;continue;}
        if(operation!='S')return 4;
        gcc_input in{};gcc_output out{};
        std::cin>>in.fields>>in.epoch>>in.sequence>>in.global_timer>>in.raw_igt>>in.level>>in.area
            >>in.action>>in.warp_op>>in.warp_timer>>in.origin_epoch>>in.origin_sequence;
        if(!std::cin)return 5;
        checking=true;const auto refusal=gcc_step(&state,&in,&out);checking=false;++steps;
        if(refusal!=out.refusal)return 6;
        std::cout<<out.valid<<' '<<out.refusal<<' '<<out.transition<<' '<<out.raw_igt<<' '
            <<out.banked_frames<<' '<<out.progress_frames<<' '<<out.origin_id<<' '<<out.leg_id<<' '
            <<out.elapsed_frames<<' '<<out.continuity_id<<'\n';
    }
    std::cout<<"@ "<<steps<<' '<<allocations<<' '<<frees<<'\n';
    return allocations || frees?7:0;
}
