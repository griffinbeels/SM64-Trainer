#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "ghost_rom.h"
#include "ghost_capture.h"
#include "practice_rom.h"
#include <algorithm>
#include <cstring>

namespace {
constexpr size_t HEADER_BYTES = PRACTICE_ROM_HEADER_BYTES, CHUNK_BYTES = 1u << 20;
bool copy(uintptr_t address, void* destination, size_t bytes) {
    SIZE_T got = 0;
    return ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<const void*>(address),
        destination, bytes, &got) && got == bytes;
}
bool overlaps(uintptr_t at, size_t bytes, const std::vector<uint8_t>& owned) {
    const uint64_t start = reinterpret_cast<uintptr_t>(owned.data());
    return owned.capacity() && uint64_t(at) < start + owned.capacity()
        && start < uint64_t(at) + bytes;
}
void normalize(std::vector<uint8_t>& bytes) {
    if (bytes[0] != 0x40) return;
    for (size_t at = 0; at < bytes.size(); at += 4) {
        std::swap(bytes[at], bytes[at + 3]);
        std::swap(bytes[at + 1], bytes[at + 2]);
    }
}
struct Scratch {
    std::vector<uint8_t> bytes;
    explicit Scratch(size_t count) : bytes(count) {}
    ~Scratch() { SecureZeroMemory(bytes.data(), bytes.size()); }
};
// The cartridge is ordinary memory. Write-combined and uncached pages are a
// driver's device memory: never the cartridge, and slow to read (an RTX 5090
// driver's write-combined region read at 167 MiB/s against ~12 GiB/s for
// ordinary pages, 2026-10-28), so a large one exhausts the search's deadline.
bool may_hold_cartridge(const MEMORY_BASIC_INFORMATION& region) {
    return region.State == MEM_COMMIT
        && !(region.Protect & (PAGE_GUARD | PAGE_NOACCESS | PAGE_WRITECOMBINE | PAGE_NOCACHE));
}
// Where the readable span this region opens ends: it and the regions after it
// in the same allocation. Pages of one allocation that differ in protection
// are separate regions, and a cartridge may cross them.
uint64_t span_end(const MEMORY_BASIC_INFORMATION& first) {
    uint64_t end = reinterpret_cast<uintptr_t>(first.BaseAddress) + first.RegionSize;
    MEMORY_BASIC_INFORMATION next{};
    while (end < 0x100000000ull
            && VirtualQuery(reinterpret_cast<const void*>(static_cast<uintptr_t>(end)), &next, sizeof(next))
            && reinterpret_cast<uintptr_t>(next.BaseAddress) == end
            && next.AllocationBase == first.AllocationBase && may_hold_cartridge(next))
        end += next.RegionSize;
    return end;
}
}
namespace ghost_rom {
uint32_t layout(const uint8_t* header) {
    uint8_t raw[HEADER_BYTES]{}, normal[HEADER_BYTES]{};
    if (!header || !copy(reinterpret_cast<uintptr_t>(header), raw, sizeof(raw))
            || !practice_rom(raw) || !practice_rom_normalise(raw, normal)) return 0;
    return normal[0x3e] == 'E' ? GC_ROM_US : normal[0x3e] == 'J' ? GC_ROM_JP : 0;
}
Result read(const uint8_t* header, const std::function<bool()>& current,
            std::vector<uint8_t>& out,
            const std::function<Validation(const uint8_t*, size_t)>& validate, Limits limits) {
    if (!out.empty()) SecureZeroMemory(out.data(), out.size());
    out.clear();
    uint8_t raw[HEADER_BYTES]{}, expected[HEADER_BYTES]{}, swapped[HEADER_BYTES]{};
    if (!header || !current() || !copy(reinterpret_cast<uintptr_t>(header), raw, sizeof(raw)))
        return Result::changed;
    // Either practice cartridge (US 'E' or JP 'J'); the model's asset loader
    // knows where each keeps its blocks (renderer/model/assets.c).
    if (!practice_rom(raw) || !practice_rom_normalise(raw, expected)
            || (expected[0x3e] != 'E' && expected[0x3e] != 'J'))
        return Result::missing;
    for (size_t at = 0; at < HEADER_BYTES; ++at) swapped[at] = expected[at ^ 3];
    const DWORD started = GetTickCount();
    uint64_t spent = 0;
    auto stable = [&] {
        uint8_t now[HEADER_BYTES]{};
        return current() && copy(reinterpret_cast<uintptr_t>(header), now, sizeof(now))
            && !std::memcmp(raw, now, sizeof(raw));
    };
    auto budget = [&](size_t bytes) {
        if (bytes > limits.bytes - spent || GetTickCount() - started >= limits.milliseconds)
            return false;
        spent += bytes;
        return true;
    };
    auto fail = [&](Result why) {
        SecureZeroMemory(out.data(), out.size()); out.clear(); return why;
    };
    // Allocate before enumerating. These buffers may themselves contain a full
    // matching image, so candidate spans intersecting any of them are excluded.
    out.resize(IMAGE_BYTES);
    Scratch candidate_storage(IMAGE_BYTES), scratch_storage(CHUNK_BYTES + HEADER_BYTES - 1);
    auto& candidate = candidate_storage.bytes;
    auto& scratch = scratch_storage.bytes;
    bool found = false;
    MEMORY_BASIC_INFORMATION region{};
    // The emulator's header normally opens its own cartridge: Project64 keeps
    // the image in one allocation with the header at its base. An image that
    // qualifies there is the one this emulator runs, so no other copy in the
    // process can outrank it and nothing else is read. A header copied
    // elsewhere has no image behind it and falls through to the search.
    {
        const auto at = reinterpret_cast<uintptr_t>(header);
        if (VirtualQuery(header, &region, sizeof(region)) && may_hold_cartridge(region)
                && uint64_t(at) + IMAGE_BYTES <= span_end(region)
                && !overlaps(at, IMAGE_BYTES, out) && !overlaps(at, IMAGE_BYTES, candidate)
                && !overlaps(at, IMAGE_BYTES, scratch)) {
            if (!budget(IMAGE_BYTES)) return fail(Result::exhausted);
            if (copy(at, candidate.data(), candidate.size())) {
                normalize(candidate);
                if (!std::memcmp(candidate.data(), expected, HEADER_BYTES)) {
                    if (!stable()) return fail(Result::changed);
                    const auto qualified = validate(candidate.data(), candidate.size());
                    if (qualified == Validation::unavailable) return fail(Result::exhausted);
                    if (qualified == Validation::valid) {
                        if (!stable()) return fail(Result::changed);
                        out = candidate;
                        return Result::ready;
                    }
                }
            }
        }
    }
    for (uint64_t address = 0; address < 0x100000000ull;) {
        if (!stable()) return fail(Result::changed);
        if (!budget(0)) return fail(Result::exhausted);
        if (!VirtualQuery(reinterpret_cast<const void*>(static_cast<uintptr_t>(address)),
                &region, sizeof(region))) break;
        const uint64_t base = reinterpret_cast<uintptr_t>(region.BaseAddress);
        if (base + region.RegionSize <= address) return fail(Result::exhausted);
        if (!may_hold_cartridge(region)) { address = base + region.RegionSize; continue; }
        // Project64's cartridge is one committed allocation. Small header copies
        // cannot qualify; a complete 8 MiB span must remain readable in it.
        const uint64_t end = span_end(region);
        address = end;
        if (end - base < IMAGE_BYTES) continue;
        const uint64_t last = end - IMAGE_BYTES;
        for (uint64_t at = base; at <= last;) {
            if (!stable()) return fail(Result::changed);
            const size_t starts = static_cast<size_t>(std::min<uint64_t>(CHUNK_BYTES, last - at + 1));
            const size_t bytes = starts + HEADER_BYTES - 1;
            if (!budget(bytes)) return fail(Result::exhausted);
            if (copy(static_cast<uintptr_t>(at), scratch.data(), bytes)) {
                for (size_t offset = 0; offset < starts; ++offset) {
                    if (!(offset & 0x3fff)) {
                        if (!stable()) return fail(Result::changed);
                        if (!budget(0)) return fail(Result::exhausted);
                    }
                    // Both supported byte orders have distinct first bytes. The
                    // whole header, not magic alone, establishes a candidate.
                    const auto* match = scratch.data() + offset;
                    if (*match != expected[0] && *match != swapped[0]) continue;
                    if (std::memcmp(match, expected, HEADER_BYTES) && std::memcmp(match, swapped, HEADER_BYTES))
                        continue;
                    const auto source = static_cast<uintptr_t>(at + offset);
                    if (overlaps(source, IMAGE_BYTES, out) || overlaps(source, IMAGE_BYTES, candidate)
                            || overlaps(source, IMAGE_BYTES, scratch)) continue;
                    if (!stable()) return fail(Result::changed);
                    if (!budget(IMAGE_BYTES)) return fail(Result::exhausted);
                    if (!copy(source, candidate.data(), candidate.size())) continue;
                    normalize(candidate);
                    if (std::memcmp(candidate.data(), expected, HEADER_BYTES)) continue;
                    if (!stable()) return fail(Result::changed);
                    // A copied header in a large heap is still only a header.
                    // The model's actual bounded asset parser qualifies the image.
                    const auto qualified = validate(candidate.data(), candidate.size());
                    if (qualified == Validation::unavailable) return fail(Result::exhausted);
                    if (qualified != Validation::valid) continue;
                    if (!stable()) return fail(Result::changed);
                    if (!budget(0)) return fail(Result::exhausted);
                    if (found && candidate != out) return fail(Result::ambiguous);
                    if (!found) { out = candidate; found = true; }
                }
            }
            at += starts;
        }
    }
    if (!stable()) return fail(Result::changed);
    return found ? Result::ready : fail(Result::missing);
}
}
