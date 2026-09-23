/* Worker-only bounded reads of immutable native model/clip inputs. */
#pragma once
#include "ghost_clip.h"
#include <vector>
namespace ghost_assets {
bool sha256(const std::vector<uint8_t> &, uint8_t out[32]);
/* out_of_memory (optional) is set when the refusal was allocation, not the file. */
bool read(const uint16_t *path, uint32_t path_capacity, uint32_t maximum,
          std::vector<uint8_t> &out, bool *out_of_memory = nullptr);
/* Checks the saved bytes' SHA-256, then reads a GCLIP4 file as GCLIP5 in
 * place (ghost_clip_upgrade.h) before validating the header. */
bool clip(std::vector<uint8_t> &, const uint8_t expected_sha256[32],
          const uint8_t expected_id[16], gclip_header &out, bool *out_of_memory = nullptr);
}
