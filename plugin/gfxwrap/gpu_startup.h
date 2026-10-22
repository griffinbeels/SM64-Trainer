/* Optional, bounded ControlV1 producer-tail receipt. Older readers ignore it.
 * Admission publishes identity with zero dimensions; a renderer surface adds
 * dimensions before its gateway opens, then GL facts follow inside the gateway.
 * These are progress receipts, not proof of a delivered picture. Readers copy
 * CPU metadata only. The layout and version remain compatible with V1 readers. */
#pragma once
#include <stdint.h>
#define GPU_STARTUP_MAGIC "SM64GPU1"
#define GPU_STARTUP_VERSION 1u
typedef struct {
    char magic[8];
    uint32_t version,bytes,epoch,load_error,width,height,luid_available;
    int32_t luid_high;uint32_t luid_low,control_generation;
    uint64_t nonce_lo,nonce_hi;
    char vendor[80],renderer[128],gl_version[96],missing_entry_point[80];
} gpu_startup_v1;
#ifdef __cplusplus
static_assert(sizeof(gpu_startup_v1)==448,"GPU startup receipt size");
extern "C" {
#else
_Static_assert(sizeof(gpu_startup_v1)==448,"GPU startup receipt size");
#endif
int __cdecl gd_read_startup(gpu_startup_v1*);
#ifdef __cplusplus
}
#endif
