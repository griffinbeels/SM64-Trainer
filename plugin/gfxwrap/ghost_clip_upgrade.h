/* Older clips read as the current GCLIP7: the same header and samples, sizes
 * and versions moved on, the blocks they never had zero. GCLIP4 (GC5,
 * 652-byte records) has no particles; GCLIP5 (GC6, 1268-byte records) has a
 * held block without a released object's angle. GCLIP6 (GC7, 1276-byte records)
 * has no exact pad block. Saved and PB ghosts recorded
 * before either keep playing. Worker-only; allocates. Mirrored by clip.py
 * upgrade_record/decode_clip. */
#pragma once
#include "ghost_clip.h"
#include <cstring>
#include <new>
#include <vector>

namespace ghost_clip {
constexpr char v4_magic[] = "SM64GHO4";
constexpr uint32_t v4_version = 4, v4_record_bytes = 652, v4_sample_bytes = 636, v4_sample_version = 5;
static_assert(offsetof(gc_sample, particles) == v4_sample_bytes, "GC5 lacks particle, held and pad blocks");
constexpr char v5_magic[] = "SM64GHO5";
constexpr uint32_t v5_version = 5, v5_record_bytes = 1268, v5_sample_bytes = 1252, v5_sample_version = 6;
static_assert(offsetof(gc_sample, held) + offsetof(gc_held, angles) == v5_sample_bytes,
              "GC6 lacks the released object's angle and pad");
constexpr char v6_magic[] = "SM64GHO6";
constexpr uint32_t v6_version = 6, v6_record_bytes = 1276, v6_sample_bytes = 1260, v6_sample_version = 7;
static_assert(offsetof(gc_sample, pad) == v6_sample_bytes, "GC7 lacks only the optional exact pad block");

struct Older { const char *magic; uint32_t version, record_bytes, sample_bytes, sample_version; };
constexpr Older older[] = {{v4_magic, v4_version, v4_record_bytes, v4_sample_bytes, v4_sample_version},
                           {v5_magic, v5_version, v5_record_bytes, v5_sample_bytes, v5_sample_version},
                           {v6_magic, v6_version, v6_record_bytes, v6_sample_bytes, v6_sample_version}};

/* True when data holds a structurally sized GCLIP4, GCLIP5 or GCLIP6 file and it was
 * rewritten in place as the current version; false leaves data untouched
 * (current, or malformed: the ordinary validation then refuses it). */
inline bool upgrade(std::vector<uint8_t> &data, bool *out_of_memory = nullptr) {
    if (data.size() < sizeof(gclip_header)) return false;
    gclip_header header{}; std::memcpy(&header, data.data(), sizeof(header));
    const Older *from_format = nullptr;
    for (const auto &format : older)
        if (!std::memcmp(data.data(), format.magic, 8) && header.version == format.version) from_format = &format;
    if (!from_format) return false;
    const Older &f = *from_format;
    if (header.bytes != sizeof(header) || header.record_bytes != f.record_bytes
            || header.sample_version != f.sample_version || !header.record_count
            || header.record_count > GCLIP_MAX_RECORDS
            || data.size() != sizeof(header) + uint64_t(header.record_count) * f.record_bytes) return false;
    std::vector<uint8_t> out;
    try { out.assign(sizeof(header) + size_t(header.record_count) * sizeof(gclip_frame), 0); }
    catch (const std::bad_alloc &) { if (out_of_memory) *out_of_memory = true; return false; }
    catch (...) { return false; }
    std::memcpy(header.magic, GCLIP_MAGIC, 8);
    header.version = GCLIP_VERSION; header.record_bytes = sizeof(gclip_frame); header.sample_version = GC_VERSION;
    std::memcpy(out.data(), &header, sizeof(header));
    for (uint32_t i = 0; i < header.record_count; ++i) {
        const uint8_t *source = data.data() + sizeof(header) + size_t(i) * f.record_bytes;
        uint8_t *to = out.data() + sizeof(header) + size_t(i) * sizeof(gclip_frame);
        std::memcpy(to, source, f.record_bytes);
        gclip_frame frame{}; std::memcpy(&frame, to, sizeof(frame));
        // An older record never had the newer blocks: its sizes move on, nothing else.
        if (frame.bytes == f.record_bytes && frame.version == f.version) {
            frame.bytes = sizeof(gclip_frame); frame.version = GCLIP_VERSION;
        }
        if (frame.sample.bytes == f.sample_bytes && frame.sample.version == f.sample_version) {
            frame.sample.bytes = sizeof(gc_sample); frame.sample.version = GC_VERSION;
        }
        std::memcpy(to, &frame, sizeof(frame));
    }
    data.swap(out);
    return true;
}
}
