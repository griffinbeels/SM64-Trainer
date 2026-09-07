/* THE FRAME STREAM, C side -- the shared-memory ring the capture layer writes
 * and the trainer reads. The Python side is src/sm64_events/replay/framestream.py;
 * every number here is mirrored there and tests/test_framestream_layout.py
 * compares the two through `gfxwrap_host.exe --layout`. Change both or neither. */
#pragma once
#include <stddef.h>
#include <stdint.h>

#define GFXWRAP_VERSION 2

#define STREAM_NAME "sm64_trainer_gfx_v1"
#define STREAM_EVENT_SUFFIX "_frame"
#define STREAM_MAGIC "SM64GFX1"
#define STREAM_VERSION 1

#define HEADER_BYTES 4096
#define SLOT_COUNT 6
#define MAX_WIDTH 3840
#define MAX_HEIGHT 2160
#define BYTES_PER_PIXEL 3
#define TABLE_ENTRIES 16
#define TABLE_ENTRY_BYTES 128
#define SLOT_META_BYTES 4096
#define SLOT_BYTES (SLOT_META_BYTES + MAX_WIDTH * MAX_HEIGHT * BYTES_PER_PIXEL)
#define TOTAL_BYTES (HEADER_BYTES + SLOT_COUNT * SLOT_BYTES)

#define H_MAGIC 0
#define H_VERSION 8
#define H_HEADER_BYTES 12
#define H_PLUGIN_PID 16
#define H_ALIVE 20
#define H_STATUS 24
#define H_WIDTH 28
#define H_HEIGHT 32
#define H_FORMAT 36
#define H_SLOT_COUNT 40
#define H_SLOT_BYTES 44
#define H_SLOTS_OFFSET 48
#define H_WRITE_SEQ 52
#define H_DROPPED 56
#define H_WRAPPED_VERSION 60
#define H_PLUGIN_VERSION 64
#define H_LISTS 68
#define H_WRAPPED_NAME 128
#define H_WRAPPED_NAME_BYTES 128
#define H_WANT_FRAMES 256
#define H_RDRAM_BYTES 260
#define H_TABLE_COUNT 264
#define H_TRACKER_ALIVE 268
#define H_TABLE 272

#define STATUS_WRAPPED_LOADED 1
#define STATUS_GL_CONTEXT 2
#define STATUS_ROM_OPEN 4
#define STATUS_FRAME_TOO_LARGE 8
#define STATUS_INITIATED 16
#define STATUS_READSCREEN 32      /* pictures come through the wrapped plugin's ReadScreen */
#define FORMAT_BGR8_BOTTOM_UP 1
#define KIND_PICTURE 1

#define S_SEQ 0
#define S_KIND 4
#define S_LIST_QPC 8
#define S_PRESENT_QPC 16
#define S_VI_ORIGIN 24
#define S_WIDTH 28
#define S_HEIGHT 32
#define S_STRIDE 36
#define S_TABLE_COUNT 40
#define S_LISTS_SINCE 44
#define S_LENGTHS 48
#define S_SEQ_END 116
#define S_TABLE 128
#define S_PIXELS 4096

#pragma pack(push, 1)
typedef struct {
    uint32_t rdram_offset;
    uint32_t length;
} table_entry_t;

typedef struct {
    char magic[8];
    uint32_t version, header_bytes, plugin_pid, alive, status;
    uint32_t width, height, format, slot_count, slot_bytes, slots_offset;
    uint32_t write_seq, dropped, wrapped_version, plugin_version, lists;
    uint8_t reserved0[H_WRAPPED_NAME - 72];
    char wrapped_name[H_WRAPPED_NAME_BYTES];
    uint32_t want_frames, rdram_bytes, table_count, tracker_alive;
    table_entry_t table[TABLE_ENTRIES];
    uint8_t reserved1[HEADER_BYTES - (H_TABLE + TABLE_ENTRIES * 8)];
} stream_header_t;

typedef struct {
    uint32_t seq, kind;
    int64_t list_qpc, present_qpc;
    uint32_t vi_origin, width, height, stride, table_count, lists_since;
    uint32_t lengths[TABLE_ENTRIES];
    uint32_t reserved0;
    uint32_t seq_end;
    uint8_t reserved1[S_TABLE - (S_SEQ_END + 4)];
    uint8_t table[TABLE_ENTRIES * TABLE_ENTRY_BYTES];
    uint8_t reserved2[SLOT_META_BYTES - (S_TABLE + TABLE_ENTRIES * TABLE_ENTRY_BYTES)];
    uint8_t pixels[1];             /* stride * height bytes follow */
} stream_slot_t;
#pragma pack(pop)

_Static_assert(offsetof(stream_header_t, version) == H_VERSION, "H_VERSION");
_Static_assert(offsetof(stream_header_t, alive) == H_ALIVE, "H_ALIVE");
_Static_assert(offsetof(stream_header_t, status) == H_STATUS, "H_STATUS");
_Static_assert(offsetof(stream_header_t, write_seq) == H_WRITE_SEQ, "H_WRITE_SEQ");
_Static_assert(offsetof(stream_header_t, lists) == H_LISTS, "H_LISTS");
_Static_assert(offsetof(stream_header_t, wrapped_name) == H_WRAPPED_NAME, "H_WRAPPED_NAME");
_Static_assert(offsetof(stream_header_t, want_frames) == H_WANT_FRAMES, "H_WANT_FRAMES");
_Static_assert(offsetof(stream_header_t, table) == H_TABLE, "H_TABLE");
_Static_assert(sizeof(stream_header_t) == HEADER_BYTES, "HEADER_BYTES");
_Static_assert(offsetof(stream_slot_t, list_qpc) == S_LIST_QPC, "S_LIST_QPC");
_Static_assert(offsetof(stream_slot_t, present_qpc) == S_PRESENT_QPC, "S_PRESENT_QPC");
_Static_assert(offsetof(stream_slot_t, lists_since) == S_LISTS_SINCE, "S_LISTS_SINCE");
_Static_assert(offsetof(stream_slot_t, lengths) == S_LENGTHS, "S_LENGTHS");
_Static_assert(offsetof(stream_slot_t, seq_end) == S_SEQ_END, "S_SEQ_END");
_Static_assert(offsetof(stream_slot_t, table) == S_TABLE, "S_TABLE");
_Static_assert(offsetof(stream_slot_t, pixels) == S_PIXELS, "S_PIXELS");

/* Every constant, for `gfxwrap_host.exe --layout`: NAME VALUE per line. */
#define STREAM_LAYOUT(X) \
    X(HEADER_BYTES) X(SLOT_COUNT) X(MAX_WIDTH) X(MAX_HEIGHT) X(BYTES_PER_PIXEL) \
    X(TABLE_ENTRIES) X(TABLE_ENTRY_BYTES) X(SLOT_META_BYTES) X(SLOT_BYTES) X(TOTAL_BYTES) \
    X(H_MAGIC) X(H_VERSION) X(H_HEADER_BYTES) X(H_PLUGIN_PID) X(H_ALIVE) X(H_STATUS) \
    X(H_WIDTH) X(H_HEIGHT) X(H_FORMAT) X(H_SLOT_COUNT) X(H_SLOT_BYTES) X(H_SLOTS_OFFSET) \
    X(H_WRITE_SEQ) X(H_DROPPED) X(H_WRAPPED_VERSION) X(H_PLUGIN_VERSION) X(H_LISTS) \
    X(H_WRAPPED_NAME) X(H_WRAPPED_NAME_BYTES) X(H_WANT_FRAMES) X(H_RDRAM_BYTES) \
    X(H_TABLE_COUNT) X(H_TRACKER_ALIVE) X(H_TABLE) \
    X(STATUS_WRAPPED_LOADED) X(STATUS_GL_CONTEXT) X(STATUS_ROM_OPEN) \
    X(STATUS_FRAME_TOO_LARGE) X(STATUS_INITIATED) X(STATUS_READSCREEN) \
    X(FORMAT_BGR8_BOTTOM_UP) X(KIND_PICTURE) \
    X(S_SEQ) X(S_KIND) X(S_LIST_QPC) X(S_PRESENT_QPC) X(S_VI_ORIGIN) X(S_WIDTH) X(S_HEIGHT) \
    X(S_STRIDE) X(S_TABLE_COUNT) X(S_LISTS_SINCE) X(S_LENGTHS) X(S_SEQ_END) X(S_TABLE) \
    X(S_PIXELS) X(GFXWRAP_VERSION) X(STREAM_VERSION)
