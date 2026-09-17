/* Control-only protocol. No pixels or game addresses live here.
 * Producer owns [0,128); one mutex-serialized client owns [128,256).
 * Both halves use an aligned 32-bit sequence: odd while writing. Readers
 * try a bounded snapshot; a dead writer cannot make either side spin. */
#pragma once
#include <stdint.h>
#include <stddef.h>
#define CONTROL_MAGIC "SM64CTL1"
#define CONTROL_VERSION 1u
#define CONTROL_BYTES 4096u
#define CONTROL_SUFFIX "_control_v1"
#define CONTROL_WAKE_SUFFIX "_wake"
#define CONTROL_CLIENT_SUFFIX "_client"
#define CONTROL_PRODUCER_SUFFIX "_producer"
#define CONTROL_LEASE_MS 3000u
#define CONTROL_CLOSED 0u
#define CONTROL_PASSIVE 1u
#define CONTROL_PREPARING 2u
#define CONTROL_UNAVAILABLE 3u
#define CONTROL_ACTIVE 4u
#define CONTROL_NO_BACKEND 1u
#define CONTROL_OWNER_GONE 2u
#define CONTROL_LEASE_EXPIRED 3u
#define CONTROL_PROTOCOL_ERROR 4u
#define CONTROL_BAD_GPU_REQUEST 5u
#define CONTROL_BACKEND_FAILED 6u
#define CONTROL_FRESH_REQUEST 7u
#define CONTROL_BACKEND_INVALID 8u
#define CONTROL_BACKEND_EXHAUSTED 9u
#define CONTROL_BACKEND_SYSTEM_ERROR 10u
#define CONTROL_CAP_PASSIVE 1u
#define CONTROL_CAP_GPU 2u
typedef struct {
    uint32_t seq, token, owner_pid, created_lo, created_hi;
    uint32_t generation, heartbeat, enabled;
} control_request_t;
typedef struct {
    char magic[8];
    uint32_t version, bytes, seq, producer_pid, generation, state;
    uint32_t reason, ack_token, ack_heartbeat, capabilities;
    /* rom_open: a PRACTICE ROM is open (practice_rom.h), so capture may be
     * requested. rom_baseline: some other ROM is open and the plugin is
     * running as the baseline renderer; the trainer stays idle. */
    uint32_t rom_open, producer_created_lo, producer_created_hi, rom_baseline, reserved[16];
    control_request_t request;
    uint8_t reserved_client[256 - 128 - sizeof(control_request_t)];
    char build_id[80];
    uint8_t padding[CONTROL_BYTES - 336];
} control_page_t;
#ifdef __cplusplus
static_assert(offsetof(control_page_t, request) == 128, "control request offset");
static_assert(offsetof(control_page_t, build_id) == 256, "control build identity offset");
static_assert(sizeof(control_page_t) == CONTROL_BYTES, "control page size");
#else
_Static_assert(offsetof(control_page_t, request) == 128, "control request offset");
_Static_assert(offsetof(control_page_t, build_id) == 256, "control build identity offset");
_Static_assert(sizeof(control_page_t) == CONTROL_BYTES, "control page size");
#endif
