/* Worker-only bounded reads of immutable native model/clip inputs. */
#pragma once
#include "ghost_clip.h"
#include <vector>
namespace ghost_assets {
bool sha256(const std::vector<uint8_t> &, uint8_t out[32]);
bool read(const uint16_t *path, uint32_t path_capacity, uint32_t maximum,
          std::vector<uint8_t> &out);
bool clip(const std::vector<uint8_t> &, const uint8_t expected_sha256[32],
          const uint8_t expected_id[16], gclip_header &out);
}
