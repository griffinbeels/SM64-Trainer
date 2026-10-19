#pragma once
#include <stdint.h>
// A single clock sample owns both comparison and subtraction. Clamp even an
// unexpected distant deadline so a driver/input lifetime wait stays bounded.
inline uint32_t gpu_mf_wait_ms(uint64_t deadline,uint64_t now){
    if(now>=deadline)return 0;
    const uint64_t remaining=deadline-now;
    return remaining>1500?1500:uint32_t(remaining);
}
