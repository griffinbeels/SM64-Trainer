/* The particle probe alone over synthetic word-swapped RDRAM: which objects
 * count as Mario's, which executed display lists match them, and which 16 are
 * kept when more were drawn. No renderer, GL or model. */
#include "ghost_particle_capture.h"
#include "ghost_ram.h"
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

static unsigned checks = 0;
static void require(bool value, const char *what) {
    ++checks;
    if (!value) { std::fprintf(stderr, "FAILED: %s\n", what); std::exit(1); }
}
struct Ram {
    static constexpr uint32_t base = 0x80000000u, pool = base + 0x100000u, mario_pointer = base + 0x200u;
    std::vector<uint8_t> bytes = std::vector<uint8_t>(4u << 20);
    void u32(uint32_t address, uint32_t value) { std::memcpy(&bytes[address - base], &value, 4); }
    void u16(uint32_t address, uint16_t value) { // big-endian halfword inside a native word
        uint32_t word; std::memcpy(&word, &bytes[(address & ~3u) - base], 4);
        word = (address & 2u) ? (word & 0xffff0000u) | value : (word & 0xffffu) | (uint32_t(value) << 16);
        std::memcpy(&bytes[(address & ~3u) - base], &word, 4);
    }
    void f32(uint32_t address, float value) { uint32_t bits; std::memcpy(&bits, &value, 4); u32(address, bits); }
    static uint32_t slot(uint32_t index) { return pool + index * 0x260u; }
    void object(uint32_t index, uint32_t parent_index, const float view[3], bool active = true) {
        const uint32_t at = slot(index);
        u16(at + 0x02, active ? 1 : 0); u16(at + 0x74, active ? 1 : 0); u32(at + 0x68, slot(parent_index));
        for (unsigned i = 0; i < 3; ++i) f32(at + 0x54 + 4 * i, view[i]);
    }
};
static void billboard(const float view[3], float scale, float out[16]) {
    std::memset(out, 0, 16 * sizeof(float)); out[0] = out[5] = out[10] = scale; out[15] = 1;
    for (unsigned i = 0; i < 3; ++i) out[12 + i] = view[i];
}

int main() {
    // The one RDRAM reader (capture, root and particles): in range it reads the
    // word-swapped bytes; past RDRAM it reads 0 and refuses, never the bytes
    // beyond; a refusal sticks; fits() only asks.
    {
        uint8_t bytes[16]; std::memset(bytes, 0xAA, sizeof(bytes));
        const uint32_t word = 0x11223344u; std::memcpy(bytes, &word, 4);
        ghost::Ram r{bytes, 8};
        require(r.u32(ghost::kseg0) == 0x11223344u && r.u16(ghost::kseg0) == 0x1122u
            && r.u8(ghost::kseg0 + 3) == 0x44u && r.ok, "in-range reads");
        require(!r.fits(ghost::kseg0 + 8, 4) && r.ok, "fits only asks");
        require(r.u32(ghost::kseg0 + 8) == 0 && !r.ok, "a word past RDRAM reads 0 and refuses");
        require(r.u32(ghost::kseg0) == 0, "a refused reader stays refused");
        ghost::Ram low{bytes, 8};
        require(low.u32(0x7ffffffcu) == 0 && !low.ok, "below KSEG0 refused");
        ghost::Ram odd{bytes, 8};
        require(odd.u32(ghost::kseg0 + 2) == 0 && !odd.ok, "an unaligned word refused");
        ghost::Ram none{nullptr, 8};
        require(!none.fits(ghost::kseg0, 4) && none.u32(ghost::kseg0) == 0, "no RAM reads nothing");
    }
    Ram ram;
    const float mario[3] = {0, -100, -600};
    ram.u32(Ram::mario_pointer, Ram::slot(0)); ram.object(0, 0, mario);
    // 20 bubbles, the k-th 10k units to Mario's right, pushed farthest first.
    for (uint32_t k = 1; k <= 20; ++k) {
        const float view[3] = {10.0f * k, -100, -600};
        ram.object(k, 0, view);
    }
    const float droplet[3] = {-30, -80, -590}, foreign[3] = {5, -100, -600}, sleeping[3] = {7, -100, -600};
    ram.object(21, 22, droplet); ram.object(22, 0, sleeping, true); // droplet: child of a splash (2 hops)
    ram.object(23, 23, foreign);                                     // another object's bubble: its own parent
    ram.object(24, 0, sleeping, false);                              // inactive: never drawn, never counted
    gc_particle_probe probe;
    ghost_particles_prepare(probe, ram.bytes.data(), uint32_t(ram.bytes.size()), Ram::mario_pointer, Ram::pool);
    require(probe.prepared && probe.count == 22, "20 bubbles, the droplet and its splash are Mario's; foreign and inactive are not");
    float m[16];
    for (uint32_t k = 20; k >= 1; --k) {
        const float view[3] = {10.0f * k, -100, -600};
        billboard(view, 1, m); ghost_particles_display_list(probe, 0x0401DD60u, m);
        billboard(view, 1, m); ghost_particles_display_list(probe, 0x04000123u, m); // a nested call: skipped
    }
    billboard(droplet, 1.5f, m); ghost_particles_display_list(probe, 0x04032A18u, m);
    billboard(foreign, 1, m); ghost_particles_display_list(probe, 0x0401DD60u, m);
    float off[16]; billboard(mario, 1, off); off[12] += 1; ghost_particles_display_list(probe, 0x0401DD60u, off);
    gc_sample sample{};
    ghost_particles_finish(probe, true, sample);
    const auto &kept = sample.particles;
    require((sample.valid_fields & GC_VALID_PARTICLES) && kept.count == GC_PARTICLES
        && (kept.flags & GC_PARTICLES_OVERFLOW) && probe.dropped == 5, "16 kept, 5 counted as dropped");
    // Nearest Mario first: bubbles 1..15 (10..150 units) and the droplet (~36).
    require(kept.entries[0].display_list == 0x0401DD60u && kept.entries[0].translation[0] == 10, "nearest first");
    require(kept.entries[3].display_list == 0x04032A18u && kept.entries[3].basis[0] == 6144, "droplet by its distance");
    for (uint32_t i = 0; i < kept.count; ++i) {
        require(kept.entries[i].display_list != 0x04000123u, "nested calls never recorded");
        require(kept.entries[i].translation[0] <= 150, "farther bubbles are the ones dropped");
    }
    // A ground-pound mist puff: the game's generated list (physical, in the
    // frame pool) sets its opacity just before its model list.
    {
        Ram mist_ram = ram;
        const float puff[3] = {0, -60, -600};
        mist_ram.object(25, 0, puff);
        const uint32_t generated = 0x00300000u;
        mist_ram.u32(Ram::base + generated, 0xFB000000u); mist_ram.u32(Ram::base + generated + 4, 0xFFFFFF60u);
        mist_ram.u32(Ram::base + generated + 8, 0xB8000000u); mist_ram.u32(Ram::base + generated + 12, 0);
        gc_particle_probe misty;
        ghost_particles_prepare(misty, mist_ram.bytes.data(), uint32_t(mist_ram.bytes.size()), Ram::mario_pointer, Ram::pool);
        billboard(puff, 1, m); ghost_particles_display_list(misty, generated, m);
        billboard(puff, 1, m); ghost_particles_display_list(misty, 0x03000880u, m);
        gc_sample faded{};
        ghost_particles_finish(misty, true, faded);
        require(faded.particles.count == 1 && faded.particles.entries[0].display_list == 0x03000880u
            && faded.particles.entries[0].opacity == (GC_PARTICLE_OPACITY | 0x60), "mist keeps its opacity, not the generated list");
    }
    // Under the cap: no overflow, nothing dropped; an incomplete list publishes nothing.
    gc_particle_probe few;
    ghost_particles_prepare(few, ram.bytes.data(), uint32_t(ram.bytes.size()), Ram::mario_pointer, Ram::pool);
    billboard(droplet, 1.5f, m); ghost_particles_display_list(few, 0x04032A18u, m);
    gc_sample small{};
    ghost_particles_finish(few, true, small);
    require(small.particles.count == 1 && !small.particles.flags && few.dropped == 0, "one particle, no overflow");
    gc_sample interrupted{}; interrupted.valid_fields = GC_VALID_PARTICLES;
    ghost_particles_finish(few, false, interrupted);
    require(!(interrupted.valid_fields & GC_VALID_PARTICLES) && !interrupted.particles.count, "incomplete list publishes none");
    gc_particle_probe bad;
    ghost_particles_prepare(bad, ram.bytes.data(), uint32_t(ram.bytes.size()), Ram::base + 0x7fffffe, Ram::pool);
    gc_sample none{};
    ghost_particles_finish(bad, true, none);
    require(!bad.prepared && !(none.valid_fields & GC_VALID_PARTICLES), "bad Mario pointer: unprepared, nothing published");
    std::printf("particle capture passed: mario=22 kept=16 nearest dropped=5 foreign=no nested=no mist=opacity checks=%u\n", checks);
    return 0;
}
