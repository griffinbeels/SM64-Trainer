/* x64, process-local consumer API; no renderer calls. Keep DLL/callback alive until Close succeeds. */
#ifndef SM64_GPU_ENCODER_DLL_H
#define SM64_GPU_ENCODER_DLL_H
#include "gpu_bridge_encoder_api.h"
#if defined(GBDLL_BUILD)
#define GBDLL_EXPORT __declspec(dllexport)
#else
#define GBDLL_EXPORT
#endif
#ifdef __cplusplus
extern "C" {
#endif
#define GBDLL_VERSION 1u
#define GBDLL_SLOTS 2u
#define GBDLL_NAME_UNITS 128u
#define GBDLL_FORCE_IDR 1u
/* Submit OK means that picture was accepted/delivered exactly once. Its key may
   still be pending; Poll checks each pending event once and returns PENDING.
   No API adds an internal poll/sleep loop; NVENC encode/shutdown are synchronous
   worker operations, and GetData may flush commands for progress. */
typedef enum gbdll_result {
 GBDLL_OK=0,GBDLL_ARGUMENT=1,GBDLL_ABI=2,GBDLL_BUSY=3,GBDLL_THREAD=4,
 GBDLL_HANDLE=5,GBDLL_ADAPTER=6,GBDLL_IMPORT=7,GBDLL_FORMAT=8,
 GBDLL_TIMEOUT=9,GBDLL_PENDING=10,GBDLL_ABANDONED=11,GBDLL_ENCODER=12,
 GBDLL_QUARANTINE=13,GBDLL_STATE=14,GBDLL_MEMORY=15
} gbdll_result;
typedef enum gbdll_state {GBDLL_OPEN=1,GBDLL_FAILED=2,GBDLL_FINISHING=3,GBDLL_FINISHED=4,GBDLL_QUARANTINED=5} gbdll_state;
typedef struct gbdll_config_v1 {
 uint32_t struct_size,version;int32_t adapter_high;uint32_t adapter_low;
 uint32_t slot_count,reserved;
 uint16_t names[GBDLL_SLOTS][GBDLL_NAME_UNITS]; /* NUL-terminated UTF16 NT resource names. */
 gbenc_options_v1 encoder;gbenc_sink_v1 sink;
} gbdll_config_v1;
typedef struct gbdll_picture_v1 {
 uint32_t struct_size,version,slot,flags;
 uint64_t occurrence,pts,duration; /* Opaque encoding serial; captured occurrence stays with caller.
                                   duration is encoder-duration, not a finalized VFR hold interval. */
} gbdll_picture_v1;
typedef struct gbdll_status_v1 {
 uint32_t struct_size,version,state,failure,hresult,encoder_state,encoder_error,nv_status;
 uint32_t held_mask,pending_mask,owner_thread;int32_t adapter_high;uint32_t adapter_low,reserved;
 uint64_t submitted,completed,delivered,acquired,released,timeouts;
} gbdll_status_v1;
typedef struct gbdll_abi_v1 {
 uint32_t struct_size,version,pointer_bits,config_bytes,picture_bytes,status_bytes,options_bytes,packet_bytes,sink_bytes,reserved;
} gbdll_abi_v1;
GBDLL_EXPORT uint32_t GBENC_CALL SM64GpuEncoderAbiV1(gbdll_abi_v1*);
/* One active session per owned worker process/module. Integer token, never a pointer.
   Normally failed Open writes token0. A quarantined initialization retains a nonzero
   token for Status only and requires worker process disposal. */
GBDLL_EXPORT uint32_t GBENC_CALL SM64GpuEncoderOpenV1(const gbdll_config_v1*,uint64_t*token);
GBDLL_EXPORT uint32_t GBENC_CALL SM64GpuEncoderSubmitV1(uint64_t,const gbdll_picture_v1*);
GBDLL_EXPORT uint32_t GBENC_CALL SM64GpuEncoderPollV1(uint64_t);
/* Finish stops admission; retry after PENDING until all owned keys return and EOS closes.
   Close deletes the session only after successful Finish; PENDING leaves token valid.
   Quarantine never releases guessed ownership, retries teardown, or deletes the session. */
GBDLL_EXPORT uint32_t GBENC_CALL SM64GpuEncoderFinishV1(uint64_t);
GBDLL_EXPORT uint32_t GBENC_CALL SM64GpuEncoderCloseV1(uint64_t,gbdll_status_v1*final_status);
GBDLL_EXPORT uint32_t GBENC_CALL SM64GpuEncoderStatusV1(uint64_t,gbdll_status_v1*);
/* Optional retained-input extension version1. Base V1 structures are unchanged.
   A valid generation identifies the last selected input accepted by the sink, after
   NVENC unlock/unmap. Query is read-only; Repeat performs a NEW NVENC submission.
   An old generation never aliases a later selected input. The session token scopes it.
   The caller orders selected offers/repeats and supplies a strictly increasing encoding
   serial and PTS. Repeat checks against the last accepted encoding serial; base
   SubmitV1 retains its opaque serial semantics, so the caller enforces selected
   serial ordering. Captured occurrence remains caller-owned metadata. */
#define GBDLL_RETAINED_VERSION 1u
typedef struct gbdll_retained_abi_v1 {
 uint32_t struct_size,version,query_bytes,repeat_bytes;
} gbdll_retained_abi_v1;
typedef struct gbdll_retained_v1 {
 uint32_t struct_size,version,valid,reserved;
 uint64_t generation,selected_serial,source_copies,repeats,last_serial;
} gbdll_retained_v1;
typedef struct gbdll_repeat_v1 {
 uint32_t struct_size,version,flags,reserved;
 uint64_t generation,occurrence,pts,duration;
} gbdll_repeat_v1;
GBDLL_EXPORT uint32_t GBENC_CALL SM64GpuEncoderRetainedAbiV1(gbdll_retained_abi_v1*);
GBDLL_EXPORT uint32_t GBENC_CALL SM64GpuEncoderRetainedV1(uint64_t,gbdll_retained_v1*);
GBDLL_EXPORT uint32_t GBENC_CALL SM64GpuEncoderRepeatV1(uint64_t,const gbdll_repeat_v1*);
#ifdef __cplusplus
}
#endif
#endif
