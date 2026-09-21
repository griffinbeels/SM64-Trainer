/* SourceV3 image address, valid only during that ticket's capture callback.
 * The texture is renderer-owned and mutable; consumers MUST copy immediately. */
#pragma once
#include <stdint.h>
enum rs_source_kind3 {
    RS_SOURCE_UNAVAILABLE=0, RS_SOURCE_DEFAULT_FRONT=1,
    RS_SOURCE_CLEAN_VI=2, RS_SOURCE_NO_NEW_PRESENTATION=3
};
typedef struct {
    uint32_t bytes,version,kind,reserved;
    uint32_t framebuffer,read_buffer,x,y,width,height,surface_width,surface_height;
    uint32_t source_format,context_generation,drawable_generation,resource_generation;
    uintptr_t context,read_drawable;
    uint32_t renderer_thread,vi_origin;
    uint64_t presentation_serial,update_occurrence;
} rs_source_descriptor3;
#ifdef __cplusplus
static_assert(sizeof(rs_source_descriptor3)==96,"SourceV3 descriptor layout, Windows x86");
#endif
