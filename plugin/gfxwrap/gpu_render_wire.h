/* Test-only: the renderer-thread capture witnesses' cross-process control page.
 * The x86 producer plays the renderer, the x64 consumer plays the encoder
 * helper. No image bytes or process pointers cross it. */
#pragma once
#include <windows.h>
#include <stddef.h>
#include <stdint.h>
struct alignas(8) RenderWitnessPacket { volatile LONG ready; uint32_t reserved; uint64_t occurrence; int64_t qpc; };
struct alignas(8) RenderWitnessControl {
    LUID adapter;                       // the producer's GL adapter, which the helper's device must match
    uint32_t width, height;             // the bridge textures: the floor-even crop
    uint32_t source_width, source_height, crop_x, crop_y, snapshot_width, snapshot_height;
    uint32_t pictures, reserved;
    wchar_t names[2][128];              // the helper creates the textures under these names
    RenderWitnessPacket packets[2];
    volatile LONG initialized, textures_ready, consumed, failed;
};
static_assert(sizeof(wchar_t) == 2 && offsetof(RenderWitnessControl, names) == 48, "cross-bitness names");
static_assert(sizeof(RenderWitnessPacket) == 24 && offsetof(RenderWitnessControl, packets) == 560, "cross-bitness packets");
static_assert(sizeof(RenderWitnessControl) == 624, "cross-bitness control");
/* The fixture picture: independent of every capture step, recomputed by each oracle. */
inline unsigned witness_channel(unsigned occurrence, unsigned x, unsigned y, unsigned c) {
    if (c == 0) return (occurrence * 29 + x * 11 + y * 3 + 17) & 255;
    if (c == 1) return (occurrence * 13 + x * 7 + y * 23 + 41) & 255;
    return (occurrence * 19 + x * 31 + y * 5 + 71) & 255;
}
