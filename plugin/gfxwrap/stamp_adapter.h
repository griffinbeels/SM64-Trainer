/* The wrapper's ProcessDList/UpdateScreen stamp transaction over SourceV2.
 * Process-local x86; no GL. */
#pragma once
#include "link_source_api.h"
#define SA_VERSION 1u
#define SA_SAME_ORIGIN 0x10000u
#define SA_CANCELLED 0x10001u
/* The VI origin moved but the renderer did not swap (config dialog open,
 * resize): the front buffer still shows the previous picture, so pairing
 * it with this stamp would be a mispair recorded as exact. Omitted. */
#define SA_NO_SWAP 0x10002u
/* Worker still preparing: the record is metadata only, no image. It still
 * acknowledges the stamp so the first ACTIVE picture can be exact. */
#define SA_BOOTSTRAP_OMISSION 0x20000u
#ifdef __cplusplus
extern "C" {
#endif
typedef struct { uint32_t offset,length; } sa_row;
typedef struct {
    uint32_t bytes,version,epoch,rdram_bytes,count,reserved;
    sa_row rows[RB_STAMP_FIELDS];
} sa_table;
typedef struct {
    uint32_t bytes,version;
    void *user; // fixed process-lifetime data, like the preregistered callbacks
    void (__cdecl *process_dlist)(void);
    void (__cdecl *update_screen)(void);
    uint32_t (__cdecl *current_epoch)(void*); // cancellation-safe control gate; zero is passive
    uint32_t (__cdecl *vi_origin)(void*); // read original VI register, never GL
    int (__cdecl *read_ram)(void*,uint32_t,uint32_t,uint8_t*); // guarded, read-only, no waits
    void (__cdecl *surface)(void*,const rb_record*); // optional metadata-only bootstrap
    int (__cdecl *capture)(void*,const rb_record*,rb_image*);
    int (__cdecl *completed)(void*,const rb_image*);
} sa_ops;
typedef struct {
    uint32_t sequence,epoch,refusal_generation,reserved;
    uint64_t committed_occurrence;
    int64_t qpc;
} sa_frontier;
enum sa_refusal_cause { SA_REFUSAL_FULL=1u, SA_REFUSAL_BUSY=2u, SA_REFUSAL_UNCLASSIFIED=4u };
typedef struct {
    uint32_t sequence,epoch,cause,reserved0;
    int64_t qpc;
    uint64_t committed_occurrence;
    uint32_t offered,refused_full,refused_busy,observed;
    uint32_t baseline_full,baseline_busy,reserved[2];
} sa_refusal;
// Once, BEFORE RomOpen. Even refusal preserves configured passive forwarding.
// Source and callback code are pinned; caller owns user-data lifetime until exit.
int __cdecl sa_configure(HMODULE renderer,const sa_ops*);
// One control publisher; bounded atomic table snapshot, tagged to its source epoch.
int __cdecl sa_publish_table(const sa_table*);
// Exactly one serialized producer, as required by original LINK graphics API.
void __cdecl sa_process_dlist(void);
void __cdecl sa_update_screen(void);
void __cdecl sa_reset(void); // original serialized lifecycle; never resets sequence/cursor
// Worker only: published_through must include every admission's posted offer or
// explicit outcome, not merely its rb_take. False means defer, never wait/retry.
// A changed refusal_generation means the source refused that many stage
// requests (pool full/busy): the worker counts them as MISSING pictures and
// keeps recording. It is never an implicit held-picture interval; the next
// captured picture carries lists_since>=2 and is filed inexact. Before round
// 48 one refusal ended the run and cost 10-60 s of footage.
int __cdecl sa_probe_frontier(uint64_t published_through,sa_frontier*);
// Cleanup only: same transaction/cursor proof with the gate still zero.
// Does not prove GPU custody; the worker drains/releases those separately.
int __cdecl sa_probe_quiescent(uint64_t published_through,sa_frontier*);
// Worker calls once at capture acceptance, after its bootstrap refusal baseline
// and before ACTIVE. Only atomic diagnostic configuration changes here.
void __cdecl sa_arm_refusal(uint32_t epoch);
// First failed stage after acceptance in each epoch; retained after retirement. Caller
// compares epoch before attribution. Three bounded atomic reads, never waits.
// Process-local facade only: no change to the renderer SourceV2 or channel ABI.
int __cdecl sa_read_refusal(sa_refusal*);
#ifdef SA_TEST_HOST
void __cdecl sa_test_set_probe(void (__cdecl *)(unsigned));
#endif
#ifdef __cplusplus
}
static_assert(sizeof(sa_refusal)==64,"source refusal diagnostic layout");
#endif
