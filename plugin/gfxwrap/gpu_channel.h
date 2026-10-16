/* GPU metadata channel V1. Worker-only IPC, not a renderer callback API.
 * No graphics handles or pointers cross this independently versioned wire.
 * All reserved bytes are zero; all offsets are canonical and 64-byte aligned.
 */
#pragma once
#include <stdint.h>
#include <stddef.h>

/* The GPU channel wire; GC_VERSION is the ghost capture sample (ghost_capture.h).
 * 2 (2026-09-27): the encoder helper creates the two bridge textures under the
 * header's names (renderer-thread capture); a version-1 wrapper made them itself.
 * 3 (2026-10-03): the ghost copy's twin block (twin_offset, 768 bytes, always
 * present) and the two twin texture names a second helper creates.
 * replay/gpuchannel.py VERSION must match; test_gpu_channel compares them. */
#define GC_CHANNEL_VERSION 3u
#define GC_HEADER_BYTES 2048u
#define GC_MAX_OFFERS 8u
#define GC_BRIDGES 2u
#define GC_TABLE_FIELDS 16u
#define GC_STAMP_BYTES 2048u
#define GC_MAX_DIMENSION 8192u
#define GC_MAX_MAP_BYTES (64u * 1024u * 1024u)
#define GC_MAX_PACKET_BYTES (4u * 1024u * 1024u)
#define GC_NAMESPACE L"Local\\SM64Trainer.GpuChannel.v1."
#define GC_FORMAT_BGRA8 1u
#define GC_FORCE_IDR 1u
/* gc_status.reason: zero means no cause was supplied, not proof of a benign
 * stop. Nonzero terminal causes survive disposal. Tagged payloads separate
 * this channel's Result from runtime GD_*
 * (or forwarded CONTROL_*) codes. Untagged V1 reasons remain legacy values.
 * This extends reason semantics only; the V1 wire layout stays unchanged. */
#define GC_REASON_NAMESPACE_MASK 0xf0000000u
#define GC_REASON_CODE_MASK 0x0fffffffu
#define GC_REASON_CHANNEL 0x10000000u
#define GC_REASON_RUNTIME 0x20000000u

#pragma pack(push, 8)
typedef struct { uint32_t offset, length; } gc_field;
typedef struct {
    uint32_t seq, version; uint8_t magic[8];
    uint32_t header_bytes, total_bytes, producer_pid, owner_pid;
    uint64_t producer_birth, owner_birth, nonce_lo, nonce_hi;
    uint32_t epoch, generation; uint64_t qpc_frequency;
    uint32_t width, height, even_width, even_height, format, sample_stride;
    uint32_t luid_low; int32_t luid_high;
    uint32_t offer_count, bridge_count, table_count, stamp_capacity;
    uint32_t sample_bytes, sample_capacity, packet_count, packet_bytes;
    uint32_t pending_bytes, ram_budget, offer_offset, decision_offset;
    uint32_t bridge_offset, receipt_offset, payload_offset, payload_stride;
    uint32_t status_offset, status_bytes, client_offset, client_bytes;
    gc_field table[GC_TABLE_FIELDS];
    uint16_t texture_names[GC_BRIDGES][128];
    uint32_t publication_offset, publication_bytes;
    uint32_t twin_offset, twin_bytes;              /* v3: the ghost copy's block */
    uint16_t twin_texture_names[GC_BRIDGES][128];  /* "-copy-%u", never a clean name */
    uint8_t reserved[688];
} gc_header;

enum gc_state { GC_PREPARING = 1, GC_ACTIVE = 2, GC_CLOSED = 3, GC_FAULT = 4 };
typedef struct {
    uint32_t seq, state, reason, reserved0;
    uint64_t nonce_lo, nonce_hi; uint32_t epoch, generation;
    uint64_t heartbeat_qpc, frontier_qpc, frontier_occurrence, frontier_serial;
    uint8_t reserved[184];
} gc_status;

enum gc_offer_kind { GC_OFFER_EMPTY = 0, GC_OFFER_READY = 1 };
typedef struct {
    uint32_t seq, kind; uint64_t token, occurrence;
    uint32_t epoch, generation; int64_t list_qpc, boundary_qpc;
    uint32_t width, height, stamp_bytes, sample_bytes;
    uint32_t table_count, vi_origin, lists_since, outcome;
    uint32_t stamp_offset, sample_offset, lengths[GC_TABLE_FIELDS];
    uint64_t nonce_lo, nonce_hi;
    uint8_t reserved[88];
} gc_offer;

enum gc_decision_kind { GC_SELECTED = 1, GC_COALESCED = 2,
    GC_SUPPRESSED = 3, GC_FAILED = 4 };
typedef struct {
    uint32_t seq, kind; uint64_t token, occurrence, nonce_lo, nonce_hi;
    uint32_t epoch, generation;
    uint64_t encode_serial, pts, nominal_duration, retained_occurrence, retained_serial;
    uint32_t flags, reason; uint8_t reserved[32];
} gc_decision;

typedef struct {
    uint32_t seq, ready; uint64_t token, occurrence, encode_serial, pts, nominal_duration;
    uint64_t nonce_lo, nonce_hi; uint32_t epoch, generation, texture_index, flags;
    uint8_t reserved[48];
} gc_bridge;

/* A receipt ONLY acknowledges reading metadata. It conveys no GPU ownership.
 * Key0 return must be independently proved by the native bridge worker. */
typedef struct {
    uint32_t seq, accepted; uint64_t token, occurrence, encode_serial;
    uint64_t nonce_lo, nonce_hi; uint32_t epoch, generation, texture_index, reason;
    uint8_t reserved[64];
} gc_receipt;
typedef struct {
    uint32_t seq, state; uint64_t nonce_lo, nonce_hi;
    uint32_t epoch, generation, owner_pid, reserved0; uint64_t owner_birth;
    uint8_t reserved[80];
} gc_client;
typedef struct { uint32_t seq, reserved0; uint64_t last_token, last_bridge_token; uint8_t reserved[104]; } gc_publication;

/* The ghost copy (twin lane): a second encoder helper records the displayed
 * picture beside the clean one. RAM only, at twin_offset; every entry has its
 * own seq (odd while writing, +2 when done). The twin never delays or refuses
 * a clean picture, and a twin fault stops only the twin. */
#define GC_TWIN_BYTES 768u
enum gc_twin_client_state { GC_TWIN_CLIENT_OFF = 0, GC_TWIN_CLIENT_READY = 1 };
typedef struct {  /* client writes: READY once its helper created both twin textures */
    uint32_t seq, state; uint64_t lane; /* strictly increasing per READY, 0 when OFF */
    uint64_t nonce_lo, nonce_hi; uint32_t epoch, generation;
    uint8_t reserved[88];
} gc_twin_client;
enum gc_twin_state { GC_TWIN_OFF = 0, GC_TWIN_PREPARING = 1, GC_TWIN_ACTIVE = 2, GC_TWIN_STOPPED = 3 };
typedef struct {  /* producer writes; reason is a raw gd_reason (GD_TWIN_*) or 0 */
    uint32_t seq, state, reason, reserved0;
    uint64_t lane, resolved_pts, copied, missed; /* resolved_pts: every SELECTED pts <= it has its twin outcome */
    uint64_t nonce_lo, nonce_hi; uint32_t epoch, generation;
    uint8_t reserved[56];
} gc_twin_status;
typedef struct {
    gc_twin_client client;
    gc_twin_status status;
    gc_bridge bridges[GC_BRIDGES];   /* texture_index 0/1 name the twin textures; flags 0 */
    gc_receipt receipts[GC_BRIDGES];
} gc_twin;
#pragma pack(pop)

#ifdef __cplusplus
static_assert(sizeof(gc_header) == 2048 && offsetof(gc_header, table) == 192, "header wire");
static_assert(offsetof(gc_header, texture_names) == 320, "texture names wire");
static_assert(offsetof(gc_header, twin_offset) == 840 && offsetof(gc_header, twin_bytes) == 844
    && offsetof(gc_header, twin_texture_names) == 848 && offsetof(gc_header, reserved) == 1360, "twin header wire");
static_assert(sizeof(gc_twin_client) == 128 && sizeof(gc_twin_status) == 128 && sizeof(gc_twin) == GC_TWIN_BYTES
    && offsetof(gc_twin, status) == 128 && offsetof(gc_twin, bridges) == 256 && offsetof(gc_twin, receipts) == 512
    && offsetof(gc_twin_client, lane) == 8 && offsetof(gc_twin_client, nonce_lo) == 16 && offsetof(gc_twin_client, epoch) == 32
    && offsetof(gc_twin_status, lane) == 16 && offsetof(gc_twin_status, missed) == 40
    && offsetof(gc_twin_status, nonce_lo) == 48 && offsetof(gc_twin_status, epoch) == 64, "twin block wire");
static_assert(sizeof(gc_status) == 256 && sizeof(gc_offer) == 256, "producer wire");
static_assert(sizeof(gc_decision) == 128 && sizeof(gc_bridge) == 128
    && sizeof(gc_receipt) == 128 && sizeof(gc_client) == 128 && sizeof(gc_publication) == 128, "client/bridge wire");

#include <windows.h>
namespace gpu_channel {
enum class Result { ok, empty, busy, invalid, stale, repeated, out_of_order,
    closed, owner_gone, wrong_worker, exhausted, system_error };

/* Fill immutable configuration before create. It derives PID/birth, geometry,
 * canonical offsets and lengths. Invalid budgets fail before any allocation. */
bool layout(gc_header &header);
bool validate(const gc_header &header, uint64_t mapped_bytes);
bool decision_valid(const gc_decision &, const gc_header &);
bool stable_read(const void *source, void *out, uint32_t bytes);
uint64_t process_birth(HANDLE process);

class Producer {
public:
    Producer() = default;
    ~Producer();
    Producer(const Producer &) = delete;
    Producer &operator=(const Producer &) = delete;
    Result create(const gc_header &configuration);
    // Connected state1 permits header inspection. Only explicit state3 means
    // the consumer has opened the real encoder and may receive GPU pictures.
    Result reader_ready();
    Result publish(unsigned slot, gc_offer metadata, const void *stamps, const void *sample);
    Result take_decision(unsigned &slot, gc_decision &decision);
    // Called only after the owner has released actual source snapshot custody.
    Result retire_offer(unsigned slot, uint64_t token);
    // Called only after GPU copy succeeds AND the bridge releases key1.
    Result publish_bridge(unsigned texture_index, uint64_t bridge_token, const gc_decision &);
    Result take_receipt(unsigned &texture_index, gc_receipt &receipt);
    // A receipt never invokes this. Caller independently proves actual key0.
    Result retire_bridge_after_key0(unsigned texture_index, uint64_t bridge_token);
    // Frontier comes from the separately proven producer-transaction snapshot.
    Result status(uint32_t state, uint32_t reason, uint64_t heartbeat_qpc,
                  uint64_t frontier_qpc = 0, uint64_t frontier_occurrence = 0,
                  uint64_t frontier_serial = 0);
    // Keep the first nonzero terminal cause and an already-published FAULT.
    // A subsequent explicit close/destructor cannot make that failure clean.
    Result close(uint32_t reason = 0);
    // The twin lane. A never-written client reads as OFF. None of these
    // changes any clean state; the delivery maps their failures to the twin.
    Result twin_client(gc_twin_client &client);
    Result twin_status(uint32_t state, uint32_t reason, uint64_t lane, uint64_t resolved_pts,
                       uint64_t copied, uint64_t missed);
    // Called only after the twin copy succeeds; carries the clean decision's
    // occurrence, encode serial, pts and duration.
    Result publish_twin_bridge(unsigned texture_index, uint64_t bridge_token, const gc_decision &);
    Result take_twin_receipt(unsigned &texture_index, gc_receipt &receipt);
    Result retire_twin_bridge_after_key0(unsigned texture_index, uint64_t bridge_token);
    const gc_header &header() const { return header_; }
    const wchar_t *name() const { return name_; }
    HANDLE wake_event() const { return wake_; }
    HANDLE result_event() const { return result_; }
    // Hand the single-writer role to the calling thread. The delivery calls it
    // only under the lock that serializes its renderer and delivery threads.
    void adopt() { worker_ = GetCurrentThreadId(); }
#ifdef GC_TEST_HOST
    void *test_view() const { return view_; }
#endif
private:
    Result check();
    bool put(uint32_t offset, const void *data, uint32_t bytes);
    gc_header header_{};
    gc_status terminal_{}; // owner-local; never recover a cause from writable IPC
    HANDLE mapping_ = nullptr, owner_ = nullptr, wake_ = nullptr, result_ = nullptr;
    uint8_t *view_ = nullptr;
    DWORD worker_ = 0;
    wchar_t name_[128]{};
    gc_offer offers_[GC_MAX_OFFERS]{};
    gc_decision decisions_[GC_MAX_OFFERS]{};
    gc_bridge bridges_[GC_BRIDGES]{};
    uint32_t decision_seq_[GC_MAX_OFFERS]{}, receipt_seq_[GC_BRIDGES]{};
    gc_bridge twin_bridges_[GC_BRIDGES]{};
    uint32_t twin_receipt_seq_[GC_BRIDGES]{}, twin_client_seq_ = 0;
    bool twin_receipted_[GC_BRIDGES]{};
    uint64_t last_twin_bridge_token_ = 0;
    bool decided_[GC_MAX_OFFERS]{}, bridge_published_[GC_MAX_OFFERS]{}, receipted_[GC_BRIDGES]{};
    uint64_t last_token_ = 0, last_occurrence_ = 0, last_encode_ = 0;
    uint64_t last_pts_ = 0, last_bridge_token_ = 0;
    bool have_pts_ = false, closed_ = false;
    bool client_ready_ = false;
    uint32_t client_seq_ = 0;
};
}
#endif
