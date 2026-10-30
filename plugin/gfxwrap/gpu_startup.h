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
/* Appended after the V1 receipt (control page offset 784); zero from older
 * producers. The renderer's own GL identity from its context creation, which
 * names the GPU even when no surface qualified (the V1 strings stay empty
 * until a gateway opens), and the bindings cause of a no-surface deadline
 * (source_refusal.h; zero when the deadline's refusal was not BINDINGS).
 * Same epoch as the V1 receipt it follows. Diagnostic only. */
#define GPU_STARTUP_CONTEXT_MAGIC "SM64GPC1"
#define GPU_STARTUP_CONTEXT_VERSION 1u
typedef struct {
    char magic[8];
    uint32_t version,bytes,epoch,identity_source; /* rs_context_identity_source */
    uint32_t bindings_site,bindings_gl_error,bindings_flags,bindings_invalidations,bindings_after_ms,bindings_contexts;
    char vendor[80],renderer[128],gl_version[96];
} gpu_startup_context_v1;
#ifdef __cplusplus
static_assert(sizeof(gpu_startup_v1)==448,"GPU startup receipt size");
static_assert(sizeof(gpu_startup_context_v1)==352,"GPU startup context receipt size");
extern "C" {
#else
_Static_assert(sizeof(gpu_startup_v1)==448,"GPU startup receipt size");
_Static_assert(sizeof(gpu_startup_context_v1)==352,"GPU startup context receipt size");
#endif
int __cdecl gd_read_startup(gpu_startup_v1*);
int __cdecl gd_read_startup_context(gpu_startup_context_v1*);
#ifdef __cplusplus
}
#endif
