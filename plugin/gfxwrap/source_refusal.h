/* Diagnostic-only failed-surface witness. No SourceV2 layout or admission rule
 * changes. Successful pictures retain their existing witness semantics.
 * A code identifies the observed gate, not the underlying driver cause. */
#pragma once
#include <stdint.h>
enum rs_surface_refusal : uint32_t {
    RS_SURFACE_BINDINGS=0x53520001u,
    RS_SURFACE_FORMAT=0x53520002u,
    RS_SURFACE_EXTENT=0x53520003u,
    RS_SURFACE_GEOMETRY=0x53520004u,
    RS_SURFACE_VI_REGISTER=0x53520005u,
    RS_SURFACE_CROP=0x53520006u,
    RS_SURFACE_NOT_OBSERVED=0x53520007u
};
inline bool rs_surface_refusal_valid(uint32_t value) {
    return value>=RS_SURFACE_BINDINGS && value<=RS_SURFACE_NOT_OBSERVED;
}
