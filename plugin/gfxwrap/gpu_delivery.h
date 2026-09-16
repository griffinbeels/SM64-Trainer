/* Process-local x86 resident runtime. Renderer callbacks never wait or allocate.
 * This facade is NOT a shared ABI: handles/pointers below stay in this process.
 */
#pragma once
#include "link_source_api.h"
#include "context_lifetime.h"
#include "gpu_channel.h"
#define GD_VERSION 1u

#ifdef __cplusplus
extern "C" {
#endif

enum gd_state { GD_OFF=0, GD_BOOTSTRAP=1, GD_PREPARING=2, GD_ACTIVE=3,
    GD_DRAINING=4, GD_FAULT=5, GD_UNAVAILABLE=6 };
enum gd_result { GD_ACCEPTED=1, GD_UNCHANGED=2, GD_BUSY=3, GD_INVALID=4,
    GD_EXHAUSTED=5, GD_SYSTEM_ERROR=6 };
enum gd_reason { GD_NONE=0, GD_CANCELLED=10000, GD_BAD_SOURCE, GD_BAD_REQUEST,
    GD_CONTEXT_UNAVAILABLE, GD_CONTEXT_CHANGED, GD_GL_UNSUPPORTED, GD_SNAPSHOT_FAILED,
    GD_SAMPLE_FAILED, GD_BRIDGE_FAILED, GD_CHANNEL_FAILED, GD_SOURCE_GAP,
    GD_OWNER_GONE, GD_DEADLINE, GD_QUARANTINED, GD_TABLE_FAILED, GD_COUNTER_EXHAUSTED,
    // Append-only terminal reasons; channel/control layouts remain unchanged.
    GD_SOURCE_IMAGE_MISSING, GD_SOURCE_RECORD_INVALID, GD_SOURCE_STAMP_COUNT,
    GD_SOURCE_STAMP_LENGTH, GD_SOURCE_CLIENT_FAILED, GD_SOURCE_EPOCH_CHANGED,
    GD_SOURCE_REFUSED };

typedef struct {
    uint32_t bytes, version, max_slots, max_age_ms;
    uint64_t snapshot_bytes, bridge_bytes;
    uint32_t channel_bytes, sample_bytes, poll_ms, reserved;
} gd_limits;

typedef struct {
    uint32_t seq, state, reason, error;
    uint32_t epoch, generation, producer_pid, owner_pid;
    uint64_t nonce_lo, nonce_hi;
    uint64_t snapshot_bytes, bridge_bytes, sample_bytes, mapping_bytes;
    uint64_t published_occurrence, published_token;
    uint32_t pending_records, pending_bridges, quarantined, reserved0;
    uint8_t reserved[16];
} gd_status;

/* Call once before RomOpen/sa_configure. API code and fixed singleton stay
 * resident. Configure creates only CPU events/worker, no graphics resources.
 * Wrapper pins the owning module before configuration (same lifetime as sa_ops).
 */
int __cdecl gd_configure(const rs_api*, const rcl_api*, const gd_limits*);

/* One supervisor. Exact repeats are idempotent. Only these template fields are
 * required: owner_pid/birth, nonce_lo/hi, generation (control generation),
 * offer_count, table_count/table16, packet_count/bytes, pending_bytes, ram_budget.
 * Every other field must be zero. Actual snapshot_budget may be lower than the
 * immutable pre-ROM limit and is used in snapshot preparation.
 *
 * This singleton is the sole SourceV2 epoch owner: revoke gate, activate once,
 * publish sa_table with the actual returned epoch, then enable metadata-only
 * bootstrap. No GPU preparation or waiting occurs on this call. Final metadata
 * geometry, QPC, producer identity, LUID/names and offsets come from the worker.
 */
int __cdecl gd_request(const gc_header*, uint64_t snapshot_budget);

/* Any-thread revoke: gate=0, source.disarm and Snapshots::stop precede cleanup
 * and wakeup. Call BEFORE forwarding original ROM/context close. Never joins.
 * The independent watchdog calls this even when the delivery worker is blocked
 * inside a driver operation. A fresh request waits for proven safe cleanup.
 */
void __cdecl gd_disarm(uint32_t reason);
uint32_t __cdecl gd_epoch(void); // current_epoch callback for sa_ops; zero means passive

/* Direct adapters for sa_ops.surface/capture/completed. Surface only publishes
 * a bounded descriptor mailbox. No source context lease or GPU calls there.
 * Capture delegates the resident prepared snapshot; completed is atomic only.
 */
void __cdecl gd_surface(const rb_record*);
int __cdecl gd_capture(const rb_record*, rb_image*);
int __cdecl gd_completed(const rb_image*);

/* Persistent process-lifetime event for the supervisor watchdog waitset.
 * Signals state changes, not every captured frame. Status has bounded reads.
 * The event and resident worker are not closed while callbacks may exist.
 */
HANDLE __cdecl gd_signal(void);
int __cdecl gd_read_status(gd_status*);

#ifdef __cplusplus
}
static_assert(sizeof(gd_limits)==48 && sizeof(gd_status)==128, "delivery facade layout");
#endif
