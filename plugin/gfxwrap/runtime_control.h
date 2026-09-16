/* Immutable bridge to gd's resident delivery singleton. No graphics on watchdog. */
#pragma once
#include <windows.h>
#include "control.h"
#include "gpu_channel.h"
#define RC_VERSION 1u
enum rc_request_result { RC_REJECTED=0, RC_ACCEPTED=1, RC_DEFERRED=2,
    RC_INVALID=3, RC_EXHAUSTED=4, RC_SYSTEM_ERROR=5 };
#ifdef __cplusplus
extern "C" {
#endif
typedef struct {
    uint32_t bytes,version;
    // DEFERRED retains the pending identity while prior native custody drains.
    // Only ACCEPTED permits this identity to consume backend status.
    int (__cdecl *request)(const gc_header*,uint64_t snapshot_budget);
    void (__cdecl *disarm)(uint32_t reason);
    /* Translate delivery state to CONTROL_PREPARING/ACTIVE/UNAVAILABLE. */
    int (__cdecl *status)(uint32_t *state,uint32_t *reason);
    HANDLE (__cdecl *signal)(void); // process lifetime, auto-reset status event
} rc_backend;
// Pre-ROM/setup only, after gd/sa configure. Pins immutable callback code once.
int __cdecl rc_configure(const rc_backend*,unsigned slots_cap,uint64_t snapshot_cap);
uint32_t __cdecl rc_capabilities(void);
void __cdecl rc_available(int supported); // current wrapper renderer session; false revokes
HANDLE __cdecl rc_signal(void);
// Original serialized lifecycle. Closing revokes before original renderer teardown.
void __cdecl rc_rom(int opened);
// Watchdog cancellation; no driver calls, waits, allocations or resource destruction.
void __cdecl rc_revoke(uint32_t reason);
// Only supervisor after owner+lease validation. New identity admitted once.
void __cdecl rc_demand(const control_page_t*,const control_request_t*,uint32_t *state,uint32_t *reason);
#ifdef __cplusplus
}
#endif
