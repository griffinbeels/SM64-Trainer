/* Exact LINK command-run adapter and finite asynchronous ticket ownership.
 * This module does not capture/encode pixels or claim a synchronized replay.
 * Install at a fresh pre-RomOpen boundary. Never call the installer per frame. */
#include "renderer_boundary.h"
#include <atomic>
#include <cstring>
#include <cstdio>
#include <bcrypt.h>

namespace {
using Run = bool (__thiscall *)(void *);
using Surface = int (*)(rb_surface *);
enum State : uint32_t { FREE, WRITING, OFFERED, RENDERING, READY, BORROWED };
constexpr uint64_t TAG_MASK = 7;
constexpr uint64_t LAST_OCCURRENCE = UINT64_MAX >> 3;
static_assert(RB_SLOTS == 8, "current token uses three slot bits");
// The occurrence participates in every ownership CAS, so a delayed callback
// cannot claim a recycled slot, even within the same enabled generation.
struct Slot { std::atomic<uint64_t> state{FREE}; rb_record record{}; };
Slot slots[RB_SLOTS];
std::atomic<uint64_t> current{0};
std::atomic<uint32_t> active_epoch{0};
// Cancellation advances independently of activation publication. A control
// request delayed across close/disable cannot resurrect its old generation.
std::atomic<uint32_t> serial{1};
std::atomic<bool> exhausted{false};
uint32_t cancel_generation() {
    // x86 lock xadd, not a 64-bit fetch-add CAS retry loop. The sticky limit
    // retires capture halfway through the range, before any possible reuse.
    if (exhausted.load(std::memory_order_acquire)) return 0;
    const auto previous = serial.fetch_add(1, std::memory_order_acq_rel);
    if (previous >= INT32_MAX - 1u) exhausted.store(true, std::memory_order_release);
    return previous + 1;
}
static_assert(std::atomic<uint64_t>::is_always_lock_free, "x86 lock-free serial required");
uint64_t next_occurrence = 0; // serialized original UpdateScreen caller
unsigned next_slot = 0;
struct Counters {
    std::atomic<uint32_t> offered{0}, full{0}, busy{0}, observed{0}, retired{0},
        missed{0}, surface_failed{0};
} counters;
/* Immutable after publication; deliberately independent of wrapper g_wrapped,
 * which its CloseDLL clears. Permanent pins cover late detached-thread exit. */
Run original = nullptr;
Surface read_surface = nullptr;
rb_capture_image capture_image = nullptr;
rb_image_completed image_completed = nullptr;
bool image_configuration = false;
void **installed_slot = nullptr;
HMODULE installed_module = nullptr;
std::atomic<bool> rom_open{false}, malformed_lifecycle{false};
bool install_attempted = false; // serialized installer only; pins bounded once
bool source_mode = false; // immutable before installed publication
bool install_healthy = false; // immutable before installed release publication
uint32_t capture_epoch() {
    const auto epoch = active_epoch.load(std::memory_order_acquire);
    return epoch && serial.load(std::memory_order_acquire) == epoch
        && rom_open.load(std::memory_order_acquire)
        && !malformed_lifecycle.load(std::memory_order_acquire)
        && !exhausted.load(std::memory_order_acquire) ? epoch : 0;
}
std::atomic<bool> installed{false};
#ifdef RB_TEST_HOST
rb_test_probe before_claim = nullptr, before_activation = nullptr, before_take = nullptr;
unsigned fail_protection_call = 0, protection_calls = 0;
#endif

Slot *claim_token(uint64_t token) {
    if (!token || !capture_epoch()) return nullptr;
#ifdef RB_TEST_HOST
    if (before_claim) before_claim();
#endif
    auto &offered = slots[token & TAG_MASK];
    uint64_t expected = (token & ~TAG_MASK) | OFFERED;
    return offered.state.compare_exchange_strong(expected,
        (token & ~TAG_MASK) | RENDERING, std::memory_order_acq_rel) ? &offered : nullptr;
}
void complete_record(Slot *ticket) {
    if (ticket) {
        auto &r = ticket->record;
        if (capture_epoch() != r.epoch) {
            r.outcome = RB_RETIRED;
            counters.retired.fetch_add(1, std::memory_order_relaxed);
        } else if (!read_surface || !read_surface(&r.surface)) {
            r.outcome = RB_SURFACE_FAILED;
            counters.surface_failed.fetch_add(1, std::memory_order_relaxed);
        } else if (capture_image && !capture_image(&r, &r.image)) {
            r.outcome = RB_SURFACE_FAILED;
            counters.surface_failed.fetch_add(1, std::memory_order_relaxed);
        } else if (capture_epoch() != r.epoch) {
            // The attachment still owns its GPU work even after cancellation.
            r.outcome = RB_RETIRED;
            counters.retired.fetch_add(1, std::memory_order_relaxed);
        } else {
            r.outcome = RB_OBSERVED;
            counters.observed.fetch_add(1, std::memory_order_relaxed);
        }
        ticket->state.store((r.occurrence << 3) | READY, std::memory_order_release);
    }
}
bool __fastcall dispatch(void *self, void *) noexcept {
    Slot *ticket = capture_epoch() ? claim_token(current.load(std::memory_order_acquire)) : nullptr;
    // Preserve exact original stack-command pointer, bool ABI, and one invocation.
    const bool result = original(self);
    complete_record(ticket);
    return result;
}

bool known_file(HMODULE module) {
    wchar_t path[MAX_PATH];
    DWORD length = GetModuleFileNameW(module, path, MAX_PATH);
    if (!length || length >= MAX_PATH) return false;
    HANDLE file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, nullptr,
                              OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return false;
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    BCRYPT_HASH_HANDLE hash = nullptr;
    unsigned char bytes[32768], digest[32];
    bool ok = false;
    DWORD read = 0;
    LARGE_INTEGER size{};
    /* Hash once outside all graphics callbacks; never allocate a DLL-sized
     * buffer. An unsupported or unreadable file leaves forwarding untouched. */
    if (GetFileSizeEx(file, &size) && size.QuadPart > 0 && size.QuadPart <= (32 << 20)
            && BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM,
                                            nullptr, 0) >= 0
            && BCryptCreateHash(algorithm, &hash, nullptr, 0, nullptr, 0, 0) >= 0) {
        ok = true;
        for (;;) {
            if (!ReadFile(file, bytes, sizeof bytes, &read, nullptr)) { ok = false; break; }
            if (!read) break;
            if (BCryptHashData(hash, bytes, read, 0) < 0) { ok = false; break; }
        }
        if (ok) ok = BCryptFinishHash(hash, digest, sizeof digest, 0) >= 0;
        static const unsigned char expected[32] = {
            0x49,0xf3,0x84,0xe8,0xc6,0x2f,0x61,0xff,0x85,0x55,0xd5,0x05,0x56,0x54,0xdf,0x7f,
            0x0c,0xdb,0xe8,0x14,0x97,0xcd,0x85,0xe6,0x85,0xe3,0xa5,0x08,0xd7,0xdf,0xfe,0x83};
        if (ok) ok = std::memcmp(digest, expected, sizeof digest) == 0;
    }
    if (hash) BCryptDestroyHash(hash);
    if (algorithm) BCryptCloseAlgorithmProvider(algorithm, 0);
    CloseHandle(file);
    return ok;
}

bool loaded_image(HMODULE module) {
    __try {
        const auto base = reinterpret_cast<const unsigned char *>(module);
        const auto dos = reinterpret_cast<const IMAGE_DOS_HEADER *>(base);
        if (dos->e_magic != IMAGE_DOS_SIGNATURE || dos->e_lfanew < 0
                || dos->e_lfanew > 4096) return false;
        const auto nt = reinterpret_cast<const IMAGE_NT_HEADERS32 *>(base + dos->e_lfanew);
        if (nt->Signature != IMAGE_NT_SIGNATURE || nt->FileHeader.Machine != IMAGE_FILE_MACHINE_I386
                || nt->OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR32_MAGIC
                || nt->OptionalHeader.SizeOfImage != 0xd8d000) return false;
        const unsigned char run[] = {0xe8,0x3b,0x32,0x06,0x00,0xb0,0x01,0xc3};
        if (std::memcmp(base + 0x9520, run, sizeof run)) return false;
        /* UpdateScreen's command constructor must still name this vtable;
         * otherwise replacing a seemingly matching table observes nothing. */
        if (reinterpret_cast<unsigned char *>(GetProcAddress(module, "UpdateScreen"))
                != base + 0x88e0) return false;
        const unsigned char construct[] = {0xc7, 0x44, 0x24, 0x04};
        if (std::memcmp(base + 0x88f8, construct, sizeof construct)
                || *reinterpret_cast<const uint32_t *>(base + 0x88fc)
                    != reinterpret_cast<uintptr_t>(base + 0x848204)) return false;
        const unsigned char dispatch_call[] = {0x8d,0x44,0x24,0x04,0x8b,0xce,0x50,
                                               0xe8,0x24,0x09,0x00,0x00,0x5e,0x59,0xc3};
        if (std::memcmp(base + 0x8900, dispatch_call, sizeof dispatch_call)) return false;
        return *reinterpret_cast<void *const *>(base + 0x848204) == base + 0x9520;
    } __except (EXCEPTION_EXECUTE_HANDLER) { return false; }
}

int link_surface(rb_surface *surface) {
    __try {
        const auto display = reinterpret_cast<const unsigned char *>(installed_module) + 0xcfc9f0;
        surface->width = *reinterpret_cast<const uint32_t *>(display + 0x14);
        surface->height = *reinterpret_cast<const uint32_t *>(display + 0x18);
        surface->bottom_offset = *reinterpret_cast<const uint32_t *>(display + 0x1c);
        if (!surface->width || !surface->height || surface->width > 3840
                || surface->height > 2160 || surface->bottom_offset > 2160) return 0;
        surface->context = reinterpret_cast<uintptr_t>(wglGetCurrentContext());
        if (!surface->context) return 0;
        surface->renderer_thread = GetCurrentThreadId();
        LARGE_INTEGER qpc;
        QueryPerformanceCounter(&qpc);
        surface->boundary_qpc = qpc.QuadPart;
        return 1;
    } __except (EXCEPTION_EXECUTE_HANDLER) { return 0; }
}

BOOL protect(void *address, SIZE_T bytes, DWORD protection, DWORD *previous) {
#ifdef RB_TEST_HOST
    if (++protection_calls == fail_protection_call) return FALSE;
#endif
    return VirtualProtect(address, bytes, protection, previous);
}

int bind(void **slot, void *expected, Surface surface, HMODULE module) {
    if (installed.load(std::memory_order_acquire))
        return installed_slot == slot ? (install_healthy ? RB_ALREADY_INSTALLED : RB_PROTECTION_FAILED) : RB_BOUND_TO_OTHER;
    if (rom_open || malformed_lifecycle) return RB_LIFECYCLE_REFUSED;
    if (!slot || (reinterpret_cast<uintptr_t>(slot) & 3)) return RB_UNSUPPORTED;
    MEMORY_BASIC_INFORMATION region{};
    if (VirtualQuery(slot, &region, sizeof region) != sizeof region
            || region.State != MEM_COMMIT || (region.Protect & (PAGE_NOACCESS | PAGE_GUARD)))
        return RB_UNSUPPORTED;
    if (*slot != expected) return RB_FOREIGN_SLOT;
    DWORD old_protection;
    if (!protect(slot, sizeof *slot, PAGE_READWRITE, &old_protection))
        return RB_PROTECTION_FAILED;
    original = reinterpret_cast<Run>(expected);
    read_surface = surface;
    installed_module = module;
    installed_slot = slot;
    void *previous = InterlockedCompareExchangePointer(slot, reinterpret_cast<void *>(&dispatch), expected);
    DWORD ignored;
    const BOOL restored = protect(slot, sizeof *slot, old_protection, &ignored);
    if (previous != expected) {
        original = nullptr; read_surface = nullptr; installed_module = nullptr; installed_slot = nullptr;
        return RB_FOREIGN_SLOT;
    }
    install_healthy = restored != FALSE;
    installed.store(true, std::memory_order_release);
    /* Publication already happened: never clear pointers or pins on failure. */
    return restored ? RB_INSTALLED : RB_PROTECTION_FAILED;
}
} // namespace

extern "C" int rb_configure_images(rb_capture_image capture, rb_image_completed completed) {
    if (installed.load() || rom_open.load() || image_configuration || !capture || !completed)
        return 0;
    capture_image = capture; image_completed = completed; image_configuration = true;
    return 1;
}
extern "C" int rb_bind_source(rb_source_surface surface) {
    if (installed.load(std::memory_order_acquire))
        return source_mode && read_surface == surface ? RB_ALREADY_INSTALLED : RB_BOUND_TO_OTHER;
    if (!surface || rom_open || malformed_lifecycle || install_attempted) return RB_LIFECYCLE_REFUSED;
    // The original source has a detached render thread; pin this code just as the
    // old adapter did, without changing any function pointer owned by the renderer.
    HMODULE self = nullptr, provider = nullptr;
    install_attempted = true;
    const DWORD flags = GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN;
    if (!GetModuleHandleExW(flags, reinterpret_cast<LPCWSTR>(&rb_source_begin), &self)
            || !GetModuleHandleExW(flags, reinterpret_cast<LPCWSTR>(surface), &provider)) return RB_PIN_FAILED;
    read_surface = surface; installed_module = self; source_mode = true; install_healthy = true;
    installed.store(true, std::memory_order_release);
    return RB_INSTALLED;
}
extern "C" rb_ticket rb_source_begin(rb_ticket ticket) {
    if (!ticket.occurrence || ticket.occurrence > LAST_OCCURRENCE || ticket.slot >= RB_SLOTS
            || !installed.load(std::memory_order_acquire) || !source_mode) return {0,0};
    return claim_token((ticket.occurrence << 3) | ticket.slot) ? ticket : rb_ticket{0,0};
}
extern "C" void rb_source_end(rb_ticket ticket) {
    if (!ticket.occurrence || ticket.occurrence > LAST_OCCURRENCE || ticket.slot >= RB_SLOTS
            || !installed.load(std::memory_order_acquire) || !source_mode) return;
    auto &slot = slots[ticket.slot];
    if (slot.state.load(std::memory_order_acquire) == ((ticket.occurrence << 3) | RENDERING))
        complete_record(&slot);
}
extern "C" int rb_validate_link(HMODULE wrapped) {
    return sizeof(void *) == 4 && wrapped && known_file(wrapped) && loaded_image(wrapped);
}
namespace {
int install_verified(void **slot, void *run, Surface surface, HMODULE wrapped, HMODULE wrapper) {
    if (installed.load(std::memory_order_acquire))
        return installed_module == wrapped ? (install_healthy ? RB_ALREADY_INSTALLED : RB_PROTECTION_FAILED) : RB_BOUND_TO_OTHER;
    if (rom_open || malformed_lifecycle || install_attempted) return RB_LIFECYCLE_REFUSED;
    HMODULE thunk_module = nullptr, run_module = nullptr;
    const DWORD from_address = GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT;
    if (!wrapper || !GetModuleHandleExW(from_address, reinterpret_cast<LPCWSTR>(&dispatch), &thunk_module)
            || thunk_module != wrapper || !GetModuleHandleExW(from_address, reinterpret_cast<LPCWSTR>(run), &run_module)
            || run_module != wrapped) return RB_PIN_FAILED;
    // At most one pin pair per process, including partial failures.
    install_attempted = true;
    HMODULE pinned_original = nullptr, pinned_wrapper = nullptr;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN,
                            reinterpret_cast<LPCWSTR>(run), &pinned_original)
            || !GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN,
                            reinterpret_cast<LPCWSTR>(&dispatch), &pinned_wrapper)) return RB_PIN_FAILED;
    return bind(slot, run, surface, wrapped);
}
}
extern "C" int rb_install_link(HMODULE wrapped, HMODULE wrapper) {
    if (installed.load(std::memory_order_acquire))
        return installed_module == wrapped ? (install_healthy ? RB_ALREADY_INSTALLED : RB_PROTECTION_FAILED) : RB_BOUND_TO_OTHER;
    if (rom_open || malformed_lifecycle || install_attempted) return RB_LIFECYCLE_REFUSED;
    if (!rb_validate_link(wrapped)) return RB_UNSUPPORTED;
    auto base = reinterpret_cast<unsigned char *>(wrapped);
    return install_verified(reinterpret_cast<void **>(base + 0x848204), base + 0x9520,
                            link_surface, wrapped, wrapper);
}
extern "C" void rb_rom_open(void) {
    rb_disarm();
    if (rom_open.exchange(true)) malformed_lifecycle.store(true);
}
extern "C" void rb_rom_closed(void) {
    rom_open.store(false);
    rb_disarm();
}
extern "C" void rb_close(void) {
    if (rom_open.load()) malformed_lifecycle.store(true); // CloseDLL does not stop LINK.
    rb_disarm();
}
extern "C" uint32_t rb_activate(void) {
    const uint32_t epoch = cancel_generation();
    if (!installed.load(std::memory_order_acquire) || !install_healthy
            || malformed_lifecycle.load() || !rom_open.load() || exhausted.load())
        return 0;
#ifdef RB_TEST_HOST
    if (before_activation) before_activation();
#endif
    active_epoch.store(epoch, std::memory_order_release);
    return capture_epoch();
}
extern "C" void rb_disarm(void) {
    cancel_generation();
}
extern "C" rb_ticket rb_stage(const rb_stamp *stamp) {
    const rb_ticket none{0, 0};
    const uint32_t epoch = capture_epoch();
    if (!epoch || !stamp) return none;
    counters.offered.fetch_add(1, std::memory_order_relaxed);
    if (current.load(std::memory_order_acquire) || next_occurrence == LAST_OCCURRENCE) {
        counters.busy.fetch_add(1, std::memory_order_relaxed); return none;
    }
    for (unsigned attempt = 0; attempt < RB_SLOTS; ++attempt) {
        const unsigned index = (next_slot + attempt) % RB_SLOTS;
        auto &slot = slots[index];
        uint64_t expected = FREE;
        if (!slot.state.compare_exchange_strong(expected, WRITING, std::memory_order_acq_rel)) continue;
        auto &r = slot.record;
        r.occurrence = ++next_occurrence; r.epoch = epoch; r.slot = index; r.outcome = 0;
        r.stamp = *stamp; r.surface = {}; r.image = {};
        slot.state.store((r.occurrence << 3) | OFFERED, std::memory_order_release);
        current.store((r.occurrence << 3) | index, std::memory_order_release);
        next_slot = (index + 1) % RB_SLOTS;
        return {r.occurrence, index};
    }
    counters.full.fetch_add(1, std::memory_order_relaxed);
    return none;
}
extern "C" void rb_finish(rb_ticket ticket) {
    if (!ticket.occurrence || ticket.occurrence > LAST_OCCURRENCE || ticket.slot >= RB_SLOTS) return;
    auto &slot = slots[ticket.slot];
    const uint64_t identity = ticket.occurrence << 3;
    uint64_t expected_current = identity | ticket.slot;
    if (!current.compare_exchange_strong(expected_current, 0, std::memory_order_acq_rel)) return;
    uint64_t expected = identity | OFFERED;
    if (slot.state.compare_exchange_strong(expected, identity | WRITING, std::memory_order_acq_rel)) {
        slot.record.outcome = RB_NOT_OBSERVED;
        counters.missed.fetch_add(1, std::memory_order_relaxed);
        slot.state.store(identity | READY, std::memory_order_release);
    }
    // RENDERING retains its fixed storage even after an abnormal early return.
}
extern "C" const rb_record *rb_take(void) {
    // Single consumer cursor, never reset: stage assigns gap-free identities.
    // A slot scan alone is not an atomic snapshot; a producer may populate an
    // earlier slot after we visited it. Only this exact identity may advance.
    static uint64_t next_delivery=1;
    for (auto &slot : slots) {
        uint64_t expected=slot.state.load(std::memory_order_acquire);
#ifdef RB_TEST_HOST
        if (before_take) before_take();
#endif
        if ((expected>>3)!=next_delivery) continue;
        if ((expected & TAG_MASK)!=READY) return nullptr;
        if (!slot.state.compare_exchange_strong(expected,(expected & ~TAG_MASK) | BORROWED,
                std::memory_order_acq_rel)) return nullptr;
        ++next_delivery;
        return &slot.record;
    }
    return nullptr;
}
extern "C" int rb_release(rb_ticket ticket) {
    if (!ticket.occurrence || ticket.occurrence > LAST_OCCURRENCE || ticket.slot >= RB_SLOTS) return 0;
    uint64_t expected = (ticket.occurrence << 3) | BORROWED;
    auto &slot = slots[ticket.slot];
    // A stale release must not read an attachment being overwritten by a producer.
    if (slot.state.load(std::memory_order_acquire) != expected) return 0;
    const auto &image = slot.record.image;
    if (image.ownership != RB_IMAGE_NONE &&
            (image.ownership != RB_IMAGE_SUBMITTED || !image_completed || !image_completed(&image)))
        return 0;
    return slot.state.compare_exchange_strong(expected, FREE, std::memory_order_release);
}
extern "C" rb_stats rb_get_stats(void) {
    const bool hooked = installed.load(std::memory_order_acquire);
    return {counters.offered.load(), counters.full.load(), counters.busy.load(),
        counters.observed.load(), counters.retired.load(), counters.missed.load(),
        counters.surface_failed.load(), hooked ? 1u : 0u,
        hooked && install_healthy ? 1u : 0u};
}
#ifdef RB_TEST_HOST
extern "C" void rb_test_fail_protect(unsigned call) { fail_protection_call = call; }
extern "C" int rb_install_test_pinned(void **slot, void *expected, rb_test_surface surface,
                                       HMODULE wrapped, HMODULE wrapper) {
    return install_verified(slot, expected, surface, wrapped, wrapper);
}
extern "C" void rb_test_set_activation_probe(rb_test_probe probe) { before_activation = probe; }
extern "C" void rb_test_set_take_probe(rb_test_probe probe) { before_take = probe; }
extern "C" void rb_test_set_claim_probe(rb_test_probe probe) { before_claim = probe; }
extern "C" int rb_install_test(void **slot, void *expected, rb_test_surface surface) {
    return bind(slot, expected, surface, nullptr);
}
#endif
