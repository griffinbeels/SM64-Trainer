/* The bridge transport: how a selected picture crosses from the renderer's GL
 * context (x86, inside Project64) into the encoder helper's D3D11 texture
 * (x64), and how that texture's custody comes back (docs/replay-gpu-bridge.md).
 *
 *   KEYED_MUTEX  zero copies: GL imports the helper's keyed-mutex textures and
 *                hands them over with GL_EXT_win32_keyed_mutex (NVIDIA's path).
 *   SEMAPHORE    zero copies: the same imported textures, custody synced with
 *                a shared D3D11 fence per texture imported as a GL semaphore
 *                (GL_EXT_semaphore_win32, HANDLE_TYPE_D3D12_FENCE_EXT).
 *   READBACK     one copy: GL reads the picture into a pack buffer without
 *                waiting, the renderer copies it into a shared section, and
 *                the helper uploads it into an unshared texture.
 *
 * Numbers are wire values (gc_header.transport, gbdll_config_v1.transport and
 * the startup receipt): append, never renumber. Python mirrors them in
 * replay/gpuchannel.py. */
#pragma once
#include <stdint.h>
#include <stddef.h>

#define GT_TRANSPORT_NONE 0u
#define GT_TRANSPORT_KEYED_MUTEX 1u
#define GT_TRANSPORT_SEMAPHORE 2u
#define GT_TRANSPORT_READBACK 3u
#define GT_TRANSPORT_COUNT 4u

/* What the renderer's context offers, probed once per request on the
 * renderer's thread (render_capture::probe_capabilities). Bit 31 marks a
 * completed probe, so zero always means "not probed". */
#define GT_CAP_GL45 0x1u
#define GT_CAP_MEMORY_OBJECT 0x2u
#define GT_CAP_MEMORY_OBJECT_WIN32 0x4u
#define GT_CAP_KEYED_MUTEX 0x8u
#define GT_CAP_SEMAPHORE 0x10u
#define GT_CAP_SEMAPHORE_WIN32 0x20u
#define GT_CAP_WGL_DX_INTEROP2 0x40u
#define GT_CAP_PROBED 0x80000000u

/* SEMAPHORE and READBACK: one section per texture, created by the helper
 * under "<texture name>-section", mapped by the renderer. The renderer writes
 * `written` after a picture is in the texture (SEMAPHORE: the fence value it
 * signalled, always odd; READBACK: a count, the pixels are already in the
 * section); the helper writes `returned` once the picture left its hands
 * (SEMAPHORE: the fence value it signalled after reading, written + 1;
 * READBACK: written). Both are 64-bit interlocked words. READBACK pixels
 * follow the header, top row first, `pitch` bytes a row. A section is the
 * CPU-visible custody record: the channel's receipt still only acknowledges
 * metadata (gpu_channel.h). */
#define GT_SECTION_MAGIC 0x31545347u /* "GST1" */
#define GT_SECTION_VERSION 1u
#define GT_SECTION_HEADER_BYTES 64u
#define GT_NAME_UNITS 128u
#pragma pack(push, 8)
typedef struct {
    uint32_t magic, version, transport, format; /* format: GBENC_INPUT_* */
    uint32_t width, height, pitch, header_bytes;
    volatile int64_t written, returned;
    uint8_t reserved[16];
} gt_section;
#pragma pack(pop)

#ifdef __cplusplus
static_assert(sizeof(gt_section) == GT_SECTION_HEADER_BYTES && offsetof(gt_section, written) == 32
    && offsetof(gt_section, returned) == 40, "cross-bitness transport section");
#include <wchar.h>
/* "<texture name><suffix>" in GT_NAME_UNITS, or false when it does not fit. */
inline bool gt_derived_name(wchar_t *out, const wchar_t *texture, const wchar_t *suffix) {
    const size_t a = wcsnlen(texture, GT_NAME_UNITS), b = wcslen(suffix);
    if (!a || a >= GT_NAME_UNITS || a + b >= GT_NAME_UNITS) return false;
    wmemcpy(out, texture, a); wmemcpy(out + a, suffix, b + 1);
    return true;
}
inline uint64_t gt_section_bytes(uint32_t transport, uint32_t width, uint32_t height) {
    return transport == GT_TRANSPORT_READBACK ? GT_SECTION_HEADER_BYTES + uint64_t(width) * height * 4
                                              : GT_SECTION_HEADER_BYTES;
}
#endif
