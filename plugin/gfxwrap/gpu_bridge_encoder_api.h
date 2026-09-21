/* Consumer-side packet ABI. No renderer, Python, filesystem or C++ types. */
#ifndef SM64_GPU_BRIDGE_ENCODER_API_H
#define SM64_GPU_BRIDGE_ENCODER_API_H
#include <stdint.h>
#if defined(_WIN32)
#define GBENC_CALL __cdecl
#else
#define GBENC_CALL
#endif
#define GBENC_ABI_V1 1u
#define GBENC_INPUT_RGBA8 1u
#define GBENC_INPUT_BGRA8 2u
#define GBENC_PACKET_KEYFRAME 1u
#define GBENC_SINK_ACCEPTED 0
#define GBENC_SINK_REJECTED 1
/* This implementation supports exactly H264 High or AV1 Main, P4/HQ/VBR, bf0.
   AV1 needs an NVENC AV1 encoder (RTX 40-series and newer); asking an older
   adapter for it is refused by codec, never by guessing at the hardware. */
#define GBENC_CODEC_H264 0u
#define GBENC_CODEC_AV1 1u
#define GBENC_H264_HIGH 100u
#define GBENC_AV1_MAIN 0u /* AV1 seq_profile 0; NVENC encodes no other. */
#define GBENC_PRESET_P4 4u
#define GBENC_TUNE_HQ 1u
#define GBENC_RC_VBR 1u
/* No quality defaults: caller resolves every field from its configuration owner.
   Strict V1 size/version and zero reserved fields; all units are explicit. */
typedef struct gbenc_options_v1 {
    uint32_t struct_size,version;
    uint32_t width,height,nominal_fps_num,nominal_fps_den;
    uint32_t profile,preset,tuning,rate_control,b_frames;
    uint32_t cq,max_bitrate,vbv_buffer_bits,gop_frames;
    uint32_t initial_qp_p,initial_qp_i,initial_qp_b;
    uint64_t idr_interval_ticks; /* 90kHz; zero disables time-driven IDRs. */
    uint32_t input_format,signal_color,full_range,matrix,primaries,transfer;
    uint32_t max_packet_bytes; /* Preallocated compressed-byte bound; no hot allocation. */
    /* Takes the FIRST of the five checked-zero reserved words, so the struct
       keeps its size, version and every other offset. H264 is deliberately
       zero: a caller predating AV1 sends a zeroed word and still gets H264,
       and an encoder predating AV1 refuses a nonzero one instead of quietly
       encoding the wrong codec. */
    uint32_t codec;
    uint32_t reserved[4];
} gbenc_options_v1;
typedef struct gbenc_packet_v1 {
    uint32_t struct_size,version;
    const uint8_t *data;
    uint32_t bytes,flags;
    uint64_t occurrence,pts,duration; /* 90kHz; no derived nominal-frame timestamps. */
} gbenc_packet_v1;
/* Runs synchronously on the single consumer owner thread AFTER NVENC unlock/unmap.
   Data is borrowed until return. Copy/enqueue into a bounded owner queue promptly.
   Return zero only after accepting the complete packet. Must not reenter encoder,
   retain data, throw, block indefinitely, or call renderer code. */
typedef int (GBENC_CALL *gbenc_packet_callback_v1)(void *user,const gbenc_packet_v1 *packet);
typedef struct gbenc_sink_v1 {
    uint32_t struct_size,version;
    gbenc_packet_callback_v1 on_packet;
    void *user;
} gbenc_sink_v1;
typedef enum gbenc_error_code {
    GBENC_OK=0,GBENC_INVALID_ARGUMENT=1,GBENC_INVALID_STATE=2,
    GBENC_API_UNAVAILABLE=3,GBENC_DRIVER_ERROR=4,GBENC_SINK_ERROR=5,
    GBENC_PACKET_ERROR=6,GBENC_MEMORY_ERROR=7,
    /* The NVENC API is present and this adapter does not offer the requested
       codec. Distinct from API_UNAVAILABLE so a caller can fall back to the
       other codec without masking a missing or too-old driver. */
    GBENC_CODEC_UNAVAILABLE=8
} gbenc_error_code;
typedef enum gbenc_state {
    GBENC_EMPTY=0,GBENC_READY=1,GBENC_DELIVERING=2,GBENC_FAILED=3,
    GBENC_CLOSED=4,GBENC_QUARANTINED=5
} gbenc_state;
#endif
