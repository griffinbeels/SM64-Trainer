/* Untrusted live wire -> checked native values. No IO, model, room or SDK. */
#pragma once
#include "ghost_live_protocol.h"
#include <stddef.h>

namespace ghost_live {
enum class DecodeResult : uint32_t {
    ok, bad_argument, bad_size, bad_header, wrong_incarnation, bad_sample,
    bad_pose, bad_camera, bad_clock, bad_held, bad_pad
};
/* original_bytes is the actual datagram size, never a truncated receive count.
 * The caller supplies the current admitted incarnation, never one learned from
 * this packet. Its readable input/expected buffers must not alias out. Refusal
 * clears out. The Windows scalar ABI is little-endian / IEEE binary32.
 * Room/sender binding and generation/sequence/time high-water fences remain
 * the caller's responsibility. Valid decoded facts are not anti-cheat proof.
 */
DecodeResult decode(const void *packet, size_t original_bytes,
                    const uint8_t expected_incarnation[16], glive_value &out) noexcept;
}
