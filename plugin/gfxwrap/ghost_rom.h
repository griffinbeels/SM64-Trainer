/* Worker-only discovery of the loaded cartridge. Zilmar supplies a header,
 * never a full-ROM pointer. No emulator offsets, writes, or file selection. */
#pragma once
#include <cstddef>
#include <cstdint>
#include <functional>
#include <vector>

namespace ghost_rom {
constexpr uint32_t IMAGE_BYTES = 8u << 20;
enum class Result { ready, missing, changed, ambiguous, exhausted };
enum class Validation { valid, invalid, unavailable };
struct Limits {
    uint64_t bytes = 1ull << 30;
    uint32_t milliseconds = 2000;
};
// current() checks the owning ROM generation. It is called between bounded
// reads; the copied 64-byte header is also checked before accepting any image.
// Only a complete, unambiguous normalized practice image (US or JP) is returned in out.
Result read(const uint8_t* header, const std::function<bool()>& current,
            std::vector<uint8_t>& out,
            const std::function<Validation(const uint8_t*, size_t)>& validate, Limits limits = {});
// The practice cartridge behind Zilmar's header as a ghost rom_layout:
// GC_ROM_US for country 'E', GC_ROM_JP for 'J', 0 for anything else or an
// unreadable header. A capture config for the other one must never run.
uint32_t layout(const uint8_t* header);
}
