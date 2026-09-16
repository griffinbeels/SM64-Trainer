/* Explicit source boundary ABI, Windows x86, immutable registration pre-ROM.
 * This is metadata/capture plumbing; it does not certify successful GL pixels. */
#pragma once
#include "renderer_boundary.h"
#include <stddef.h>
#ifndef __cplusplus
#include <stdbool.h>
#endif
#define RS_ABI_V2 2u
#define RS_CAP_BOUNDARY 1u
#define RS_CAP_SOURCE_FORMAT 2u
#ifdef __cplusplus
extern "C" {
#endif
typedef struct { uint32_t bytes, version, reserved[2]; rb_ticket ticket; } rs_request;
typedef struct {
    uint32_t bytes, version, reserved[2];
    rb_capture_image capture;
    rb_image_completed completed;
} rs_callbacks;
typedef struct {
    uint32_t bytes, version, capabilities, reserved;
    int (__cdecl *configure)(const rs_callbacks *);
    rb_ticket (__cdecl *stage)(const rb_stamp *);
    // ALWAYS forwards one normal update, including malformed/refused requests.
    void (__cdecl *update)(const rs_request *);
    void (__cdecl *finish)(rb_ticket);
    uint32_t (__cdecl *activate)(void);
    void (__cdecl *disarm)(void);
    const rb_record *(__cdecl *take)(void);
    int (__cdecl *release)(rb_ticket);
    rb_stats (__cdecl *stats)(void);
    bool (__cdecl *begin_capture)(void);
    bool (__cdecl *end_capture)(void);
} rs_api;
const rs_api *__cdecl SM64ReplaySourceV2(uint32_t version, uint32_t bytes);
#ifdef __cplusplus
}
static_assert(sizeof(rs_request)==32, "source request ABI layout");
static_assert(offsetof(rs_request,ticket)==16, "source request ticket offset");
static_assert(sizeof(void*)==4, "source ABI requires Windows x86");
static_assert(sizeof(rb_surface)==80, "SourceV2 surface layout");
static_assert(offsetof(rb_surface,read_drawable)==52, "SourceV2 drawable offset");
static_assert(offsetof(rb_surface,restore_read_framebuffer)==64, "SourceV2 restore offset");
static_assert(offsetof(rb_surface,source_format)==76, "SourceV2 RGB format offset");
static_assert(sizeof(rb_record)==2264 && offsetof(rb_record,surface)==2160, "SourceV2 record layout");
static_assert(sizeof(rs_callbacks)==24 && sizeof(rs_api)==60, "SourceV2 API layout");
#endif
