/* GCLIP4 (GC5, 652-byte records, no particle block) read as GCLIP5: the same
 * header and samples, sizes and versions moved on, particles absent. Saved and
 * PB ghosts recorded before particles keep playing. Worker-only; allocates.
 * Mirrored by clip.py upgrade_record/decode_clip. */
#pragma once
#include "ghost_clip.h"
#include <cstring>
#include <vector>

namespace ghost_clip {
constexpr uint32_t v4_version = 4, v4_record_bytes = 652, v4_sample_bytes = 636, v4_sample_version = 5;
static_assert(offsetof(gc_sample, particles) == v4_sample_bytes, "GC5 is GC6 without its particle block");

/* True when data holds a structurally sized GCLIP4 file and it was rewritten
 * in place as GCLIP5; false leaves data untouched (not GCLIP4, or malformed:
 * the ordinary GCLIP5 validation then refuses it). */
inline bool upgrade_v4(std::vector<uint8_t> &data) {
    if (data.size() < sizeof(gclip_header) || std::memcmp(data.data(), "SM64GHO4", 8)) return false;
    gclip_header header{}; std::memcpy(&header, data.data(), sizeof(header));
    if (header.version != v4_version || header.bytes != sizeof(header) || header.record_bytes != v4_record_bytes
            || header.sample_version != v4_sample_version || !header.record_count
            || header.record_count > GCLIP_MAX_RECORDS
            || data.size() != sizeof(header) + uint64_t(header.record_count) * v4_record_bytes) return false;
    std::vector<uint8_t> out;
    try { out.assign(sizeof(header) + size_t(header.record_count) * sizeof(gclip_frame), 0); }
    catch (...) { return false; }
    std::memcpy(header.magic, GCLIP_MAGIC, 8);
    header.version = GCLIP_VERSION; header.record_bytes = sizeof(gclip_frame); header.sample_version = GC_VERSION;
    std::memcpy(out.data(), &header, sizeof(header));
    for (uint32_t i = 0; i < header.record_count; ++i) {
        const uint8_t *from = data.data() + sizeof(header) + size_t(i) * v4_record_bytes;
        uint8_t *to = out.data() + sizeof(header) + size_t(i) * sizeof(gclip_frame);
        std::memcpy(to, from, v4_record_bytes);
        gclip_frame frame{}; std::memcpy(&frame, to, sizeof(frame));
        // A GC5 record never had particles: its sizes move on, nothing else.
        if (frame.bytes == v4_record_bytes && frame.version == v4_version) {
            frame.bytes = sizeof(gclip_frame); frame.version = GCLIP_VERSION;
        }
        if (frame.sample.bytes == v4_sample_bytes && frame.sample.version == v4_sample_version) {
            frame.sample.bytes = sizeof(gc_sample); frame.sample.version = GC_VERSION;
        }
        std::memcpy(to, &frame, sizeof(frame));
    }
    data.swap(out);
    return true;
}
}
