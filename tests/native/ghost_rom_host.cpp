/* Real guarded process memory, no emulator or model. The header allocation
 * matches the Zilmar ABI; all candidate cartridges are separately allocated. */
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "ghost_rom.h"
#include "ghost_capture.h"
#include <algorithm>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <utility>
#include <vector>

namespace {
unsigned checks = 0;
void require(bool value, const char* message) {
    ++checks;
    if (!value) throw std::runtime_error(message);
}
struct Allocation {
    uint8_t* bytes;
    explicit Allocation(size_t count, DWORD protect = PAGE_READWRITE) : bytes(static_cast<uint8_t*>(
        VirtualAlloc(nullptr, count, MEM_RESERVE | MEM_COMMIT, protect))) {
        require(bytes != nullptr, "fixture allocation");
    }
    ~Allocation() { VirtualFree(bytes, 0, MEM_RELEASE); }
};
// The cartridge the fixture plants: its internal name and country byte.
const char* title = "SM64 USAMUNE v1.93u";
uint8_t country = 'E';
void header(uint8_t* bytes) {
    std::memset(bytes, 0, 64);
    const uint8_t magic[] = {0x80, 0x37, 0x12, 0x40};
    std::memcpy(bytes, magic, 4);
    std::memset(bytes + 0x20, ' ', 20);
    std::memcpy(bytes + 0x20, title, std::strlen(title));
    bytes[0x3e] = country;
}
void swap(uint8_t* bytes, size_t count) {
    for (size_t at = 0; at < count; at += 4) {
        std::swap(bytes[at], bytes[at + 3]);
        std::swap(bytes[at + 1], bytes[at + 2]);
    }
}
void image(uint8_t* bytes, bool swapped) {
    std::memset(bytes, 0x55, ghost_rom::IMAGE_BYTES);
    header(bytes);
    for (size_t at : {size_t(1024), size_t(4u << 20), size_t(ghost_rom::IMAGE_BYTES - 4)}) {
        bytes[at] = 0x12; bytes[at + 1] = 0x34; bytes[at + 2] = 0x56; bytes[at + 3] = 0x78;
    }
    if (swapped) swap(bytes, ghost_rom::IMAGE_BYTES);
}
void normalized(const std::vector<uint8_t>& bytes) {
    uint8_t expected[64]; header(expected);
    require(bytes.size() == ghost_rom::IMAGE_BYTES && !std::memcmp(bytes.data(), expected, 64),
            "normalized image and exact header");
    for (size_t at : {size_t(1024), size_t(4u << 20), size_t(ghost_rom::IMAGE_BYTES - 4)})
        require(bytes[at] == 0x12 && bytes[at + 1] == 0x34 && bytes[at + 2] == 0x56
            && bytes[at + 3] == 0x78, "whole image byte order, including last word");
}
}
int main() {
    using ghost_rom::Result;
    try {
        SYSTEM_INFO info{}; GetSystemInfo(&info);
        Allocation small(2 * info.dwPageSize);
        DWORD old = 0;
        require(VirtualProtect(small.bytes + info.dwPageSize, info.dwPageSize, PAGE_NOACCESS, &old) != 0,
                "header guard page");
        uint8_t* copied_header = small.bytes + info.dwPageSize - 64;
        header(copied_header); swap(copied_header, 64);
        std::vector<uint8_t> out;
        const auto current = [] { return true; };
        // This host tests discovery; the real model parser has its own native
        // witness. A header alone cannot satisfy this synthetic asset contract.
        const auto validate = [](const uint8_t* bytes, size_t count) {
            return count == ghost_rom::IMAGE_BYTES && bytes[100] == 0x55
                && bytes[1024] == 0x12 && bytes[4u << 20] == 0x12
                && bytes[ghost_rom::IMAGE_BYTES - 1] == 0x78
                ? ghost_rom::Validation::valid : ghost_rom::Validation::invalid;
        };
        const auto discover = [&](const std::function<bool()>& alive, ghost_rom::Limits limits = {}) {
            return ghost_rom::read(copied_header, alive, out, validate, limits);
        };
        require(discover(current) == Result::missing && out.empty(),
                "a copied header alone is not an image");
        require(ghost_rom::layout(copied_header) == GC_ROM_US, "US header names the US ghost layout");
        for (bool swapped : {false, true}) {
            // A cartridge may begin inside its allocation, across a scan-chunk
            // boundary; the header copied by the emulator remains word-swapped.
            const size_t offset = (1u << 20) - 31;
            Allocation cartridge(ghost_rom::IMAGE_BYTES + offset);
            image(cartridge.bytes + offset, swapped);
            require(discover(current) == Result::ready,
                    "complete cartridge discovered independently of header pointer/order");
            normalized(out);
            require(discover(current, {0, 2000}) == Result::exhausted
                    && out.empty(), "byte budget refuses incomplete discovery");
            require(ghost_rom::read(copied_header, current, out, [](const uint8_t*, size_t) {
                return ghost_rom::Validation::unavailable;
            }) == Result::exhausted && out.empty(), "unqualified resource failure cannot hide ambiguity");
            unsigned calls = 0;
            require(discover([&] { return ++calls < 3; }) == Result::changed
                    && out.empty(), "generation cancellation discards candidate");
            calls = 0;
            require(discover([&] {
                if (++calls == 3) copied_header[0x10] ^= 1;
                return true;
            }) == Result::changed && out.empty(), "header mutation cancels discovery");
            copied_header[0x10] ^= 1;
        }
        require(discover(current) == Result::missing && out.empty(),
                "released cartridge and owned scratch cannot satisfy discovery");
        {
            // Repeated first-byte matches must not repeatedly search the entire
            // remainder for the absent alternate byte order (quadratic work).
            Allocation dense(16u << 20);
            std::memset(dense.bytes, 0x80, 16u << 20);
            unsigned calls = 0;
            require(discover([&] { return ++calls < 100; }) == Result::changed
                    && out.empty(), "dense magic scan remains cancellable inside a chunk");
            require(discover(current) == Result::missing && out.empty(),
                    "dense decoy scan completes within its deadline");
        }
        {
            Allocation a(ghost_rom::IMAGE_BYTES), b(ghost_rom::IMAGE_BYTES), decoy(16u << 20);
            image(a.bytes, false); image(b.bytes, true);
            std::memcpy(decoy.bytes + 1024, copied_header, 64);
            require(discover(current) == Result::ready,
                    "identical cartridges in different byte orders are unambiguous");
            normalized(out);
            b.bytes[200] ^= 1;
            require(discover(current) == Result::ambiguous && out.empty(),
                    "matching headers with different contents are refused");
        }
        {
            Allocation decoy(ghost_rom::IMAGE_BYTES);
            image(decoy.bytes, false);
            require(VirtualProtect(decoy.bytes + 4u * 1024u * 1024u, info.dwPageSize,
                    PAGE_NOACCESS, &old) != 0, "partial cartridge guard");
            require(discover(current) == Result::missing && out.empty(),
                    "header with incomplete readable image is refused");
        }
        {
            // Project64's own header opens its cartridge. That image is the one
            // it runs: read with no search, whatever other copies exist.
            Allocation cartridge(ghost_rom::IMAGE_BYTES), other(ghost_rom::IMAGE_BYTES);
            image(cartridge.bytes, true); image(other.bytes, true); other.bytes[200] ^= 1;
            require(ghost_rom::read(cartridge.bytes, current, out, validate, {ghost_rom::IMAGE_BYTES, 2000})
                    == Result::ready, "the header's own cartridge is read with no search");
            normalized(out);
            require(discover(current) == Result::ambiguous && out.empty(),
                    "a copied header still refuses differing cartridges");
            other.bytes[100 ^ 3] = 0; // its header still matches; its image no longer qualifies
            require(ghost_rom::read(other.bytes, current, out, validate) == Result::ready,
                    "a header whose own image does not qualify falls back to the search");
            normalized(out);
        }
        {
            // Pages of one allocation that differ in protection are separate
            // regions; a cartridge across them is still one image.
            Allocation cartridge(ghost_rom::IMAGE_BYTES);
            image(cartridge.bytes, true);
            require(VirtualProtect(cartridge.bytes + ghost_rom::IMAGE_BYTES / 2, ghost_rom::IMAGE_BYTES / 2,
                    PAGE_READONLY, &old) != 0, "split cartridge protection");
            require(discover(current) == Result::ready, "a cartridge across protection changes is discovered");
            normalized(out);
        }
        {
            // Write-combined pages are a driver's device memory: never the
            // cartridge, and slow enough to exhaust the search's deadline.
            Allocation cartridge(ghost_rom::IMAGE_BYTES), device(ghost_rom::IMAGE_BYTES, PAGE_READWRITE | PAGE_WRITECOMBINE);
            image(device.bytes, true); device.bytes[200] ^= 1;
            require(discover(current) == Result::missing && out.empty(), "a write-combined copy is never read");
            image(cartridge.bytes, false);
            require(discover(current) == Result::ready, "a write-combined copy cannot make discovery ambiguous");
            normalized(out);
        }
        {
            // The JP practice cartridge is discovered the same way; a header
            // naming no practice ROM (vanilla JP, Usamune's name under any
            // other country) is not, whatever image follows it.
            title = "SM64 USAMUNE v1.93j"; country = 'J';
            header(copied_header); swap(copied_header, 64);
            Allocation cartridge(ghost_rom::IMAGE_BYTES);
            image(cartridge.bytes, true);
            require(discover(current) == Result::ready, "JP practice cartridge discovered");
            normalized(out);
            require(ghost_rom::layout(copied_header) == GC_ROM_JP, "JP header names the JP ghost layout");
            const std::pair<const char*, uint8_t> others[] = {{"SUPER MARIO 64", 'J'}, {"SM64 USAMUNE v1.93u", 'P'}};
            for (const auto& other : others) {
                title = other.first; country = other.second;
                header(copied_header); swap(copied_header, 64);
                image(cartridge.bytes, true);
                require(discover(current) == Result::missing && out.empty(), "non-practice cartridge discovered");
                require(!ghost_rom::layout(copied_header), "non-practice header names a ghost layout");
            }
            require(!ghost_rom::layout(nullptr), "no header names a ghost layout");
        }
        std::printf("{\"ok\":true,\"checks\":%u,\"header_bytes\":64}\n", checks);
        return 0;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "ROM discovery: %s (check %u)\n", error.what(), checks);
        return 1;
    }
}
