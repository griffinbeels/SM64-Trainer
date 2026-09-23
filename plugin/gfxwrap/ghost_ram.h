/* Word-swapped N64 RDRAM as the plugin sees it: each aligned 32-bit word in
 * host order, addressed through KSEG0. The one reader for the capture, root
 * and particle probes. Every read is bounds- and alignment-checked and reads 0
 * outside RDRAM. has() and a failed read clear ok, which then stays false for
 * that reader: a probe that walked off RAM refuses what it was reading. fits()
 * only asks. Renderer thread; no allocation. */
#pragma once
#include <stdint.h>
#include <string.h>

namespace ghost {
constexpr uint32_t kseg0 = 0x80000000u;
inline bool ram_span(uint32_t address, uint32_t count, uint32_t size, uint32_t alignment = 4) {
    return address >= kseg0 && !(address & (alignment - 1))
        && uint64_t(address - kseg0) + count <= size;
}
struct Ram {
    const uint8_t *data;
    uint32_t size;
    bool ok = true;
    bool fits(uint32_t address, uint32_t count, uint32_t alignment = 4) const {
        return data && ram_span(address, count, size, alignment);
    }
    bool has(uint32_t address, uint32_t count, uint32_t alignment = 4) {
        if (!fits(address, count, alignment)) ok = false;
        return ok;
    }
    uint8_t u8(uint32_t address) {
        if (!has(address, 1, 1)) return 0;
        return data[(address - kseg0) ^ 3u];
    }
    uint16_t u16(uint32_t address) {
        if (!has(address, 2, 2)) return 0;
        const uint32_t at = address - kseg0;
        return uint16_t(uint16_t(data[at ^ 3u]) << 8 | data[(at + 1) ^ 3u]);
    }
    uint32_t u32(uint32_t address) {
        if (!has(address, 4)) return 0;
        uint32_t value; memcpy(&value, data + (address - kseg0), 4); return value;
    }
    int16_t s16(uint32_t address) { return int16_t(u16(address)); }
    float f32(uint32_t address) {
        const uint32_t bits = u32(address);
        float value; memcpy(&value, &bits, sizeof(value)); return value;
    }
};
} // namespace ghost
