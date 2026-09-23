#include "ghost_particle_capture.h"
#include <cmath>
#include <cstring>

namespace {
constexpr uint32_t kseg0 = 0x80000000u, object_size = 0x260, object_count = 240;
// n64decomp object_fields.h / graph_node.h: gfx.node.flags, gfx.cameraToObject,
// parentObj, activeFlags. GRAPH_RENDER_ACTIVE and ACTIVE_FLAG_ACTIVE are bit 0.
constexpr uint32_t node_flags = 0x02, camera_to_object = 0x54, parent = 0x68, active_flags = 0x74;
constexpr uint32_t chain_limit = 4; // splash -> droplet is the deepest Mario chain
constexpr float match_tolerance = 0.05f; // 16.16 executed matrix vs float source

struct Ram {
    const uint8_t *data; uint32_t size;
    bool has(uint32_t address, uint32_t count) const {
        return address >= kseg0 && !(address & 3u) && uint64_t(address - kseg0) + count <= size;
    }
    uint32_t u32(uint32_t address) const {
        const uint8_t *p = data + (address - kseg0);
        return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24;
    }
    uint16_t u16(uint32_t address) const { // word-swapped RDRAM
        const uint32_t word = u32(address & ~3u);
        return uint16_t((address & 2u) ? word : word >> 16);
    }
    float f32(uint32_t address) const { const uint32_t bits = u32(address); float v; std::memcpy(&v, &bits, 4); return v; }
};
}

void ghost_particles_prepare(gc_particle_probe &p, const uint8_t *ram, uint32_t bytes,
                             uint32_t mario_object, uint32_t pool) {
    p = {};
    const Ram r{ram, bytes};
    if (!ram || !r.has(mario_object, 4)) return;
    const uint32_t mario = r.u32(mario_object);
    if (!r.has(mario, object_size) || !r.has(pool, object_size * object_count)) return;
    p.ram = ram; p.ram_bytes = bytes;
    for (uint32_t slot = 0; slot < object_count; ++slot) {
        const uint32_t object = pool + slot * object_size;
        if (object == mario || !(r.u16(object + active_flags) & 1u) || !(r.u16(object + node_flags) & 1u)) continue;
        uint32_t at = object;
        bool mario_descended = false;
        for (uint32_t hop = 0; hop < chain_limit && !mario_descended; ++hop) {
            const uint32_t up = r.u32(at + parent);
            if (up == mario) mario_descended = true;
            else if (up == at || !r.has(up, object_size)) break;
            at = up;
        }
        if (!mario_descended) continue;
        if (p.count == 64) { p.overflow = true; break; }
        p.objects[p.count] = object;
        for (uint32_t i = 0; i < 3; ++i) p.camera_to_object[p.count][i] = r.f32(object + camera_to_object + 4 * i);
        ++p.count;
    }
    p.prepared = true;
}

void ghost_particles_display_list(gc_particle_probe &p, uint32_t dl, const float matrix[16]) {
    if (!p.prepared || !matrix || !p.count) return;
    for (uint32_t i = 0; i < 16; ++i) if (!std::isfinite(matrix[i])) return;
    for (uint32_t i = 0; i < p.count; ++i) {
        if (p.recorded[i]) continue;
        const float *c = p.camera_to_object[i];
        if (std::fabs(matrix[12] - c[0]) > match_tolerance || std::fabs(matrix[13] - c[1]) > match_tolerance
                || std::fabs(matrix[14] - c[2]) > match_tolerance) continue;
        // First push at this object's position is its geo display list; the
        // frame lists' own nested calls reuse the same matrix and are skipped.
        p.recorded[i] = true;
        if (p.particles.count == GC_PARTICLES) { p.particles.flags |= GC_PARTICLES_OVERFLOW; return; }
        gc_particle entry{};
        entry.display_list = dl;
        for (uint32_t column = 0; column < 3; ++column)
            for (uint32_t row = 0; row < 3; ++row) {
                const float value = matrix[column * 4 + row] * GC_PARTICLE_BASIS_ONE;
                if (!(value >= -32768.0f && value <= 32767.0f)) return; // outside 4.12: not recorded
                entry.basis[column * 3 + row] = int16_t(std::lround(value));
            }
        for (uint32_t row = 0; row < 3; ++row) entry.translation[row] = matrix[12 + row];
        p.particles.entries[p.particles.count++] = entry;
        return;
    }
}

void ghost_particles_finish(gc_particle_probe &p, bool completed, gc_sample &out) {
    out.particles = {};
    out.valid_fields &= ~GC_VALID_PARTICLES;
    if (!completed || !p.prepared) return;
    out.particles = p.particles;
    if (p.overflow) out.particles.flags |= GC_PARTICLES_OVERFLOW;
    out.valid_fields |= GC_VALID_PARTICLES;
}
