/* Test-only bounded interprocess metadata; no image bytes or process pointers. */
#pragma once
#include "gpu_bridge.h"
#include <stddef.h>
struct alignas(8) BridgePacket {volatile LONG ready; uint32_t reserved; uint64_t occurrence; int64_t qpc;};
struct alignas(8) BridgeControl {
    gpu_bridge::Config config;
    BridgePacket packets[gpu_bridge::slots];
    volatile LONG initialized, consumed, failed;
};
static_assert(sizeof(gpu_bridge::Config)==528,"cross-bitness configuration");
static_assert(sizeof(BridgePacket)==24 && offsetof(BridgeControl,packets)==528,"cross-bitness packet");
static_assert(sizeof(BridgeControl)==592,"cross-bitness wire");
