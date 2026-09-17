/* Finite asynchronous ticket ownership between the emulation thread's
 * stage/finish, the renderer command's source begin/end and one consumer.
 * This module does not capture/encode pixels or claim a synchronized replay;
 * the configured image callbacks do. Bind at a fresh pre-RomOpen boundary. */
#include "renderer_boundary.h"
#include <atomic>

namespace {
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
rb_source_surface read_surface = nullptr;
rb_capture_image capture_image = nullptr;
rb_image_completed image_completed = nullptr;
bool image_configuration = false;
std::atomic<bool> rom_open{false}, malformed_lifecycle{false};
bool install_attempted = false; // serialized binder only; pins bounded once
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
} // namespace

extern "C" int rb_configure_images(rb_capture_image capture, rb_image_completed completed) {
    if (installed.load() || rom_open.load() || image_configuration || !capture || !completed)
        return 0;
    capture_image = capture; image_completed = completed; image_configuration = true;
    return 1;
}
extern "C" int rb_bind_source(rb_source_surface surface) {
    if (installed.load(std::memory_order_acquire))
        return read_surface == surface ? RB_ALREADY_INSTALLED : RB_BOUND_TO_OTHER;
    if (!surface || rom_open || malformed_lifecycle || install_attempted) return RB_LIFECYCLE_REFUSED;
    // LINK's render thread is detached and can outlive CloseDLL/FreeLibrary: pin
    // this code and the surface provider without changing any renderer pointer.
    HMODULE self = nullptr, provider = nullptr;
    install_attempted = true;
    const DWORD flags = GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN;
    if (!GetModuleHandleExW(flags, reinterpret_cast<LPCWSTR>(&rb_source_begin), &self)
            || !GetModuleHandleExW(flags, reinterpret_cast<LPCWSTR>(surface), &provider)) return RB_PIN_FAILED;
    read_surface = surface;
    installed.store(true, std::memory_order_release);
    return RB_INSTALLED;
}
extern "C" rb_ticket rb_source_begin(rb_ticket ticket) {
    if (!ticket.occurrence || ticket.occurrence > LAST_OCCURRENCE || ticket.slot >= RB_SLOTS
            || !installed.load(std::memory_order_acquire)) return {0,0};
    return claim_token((ticket.occurrence << 3) | ticket.slot) ? ticket : rb_ticket{0,0};
}
extern "C" void rb_source_end(rb_ticket ticket) {
    if (!ticket.occurrence || ticket.occurrence > LAST_OCCURRENCE || ticket.slot >= RB_SLOTS
            || !installed.load(std::memory_order_acquire)) return;
    auto &slot = slots[ticket.slot];
    if (slot.state.load(std::memory_order_acquire) == ((ticket.occurrence << 3) | RENDERING))
        complete_record(&slot);
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
    if (!installed.load(std::memory_order_acquire)
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
    const uint32_t bound = installed.load(std::memory_order_acquire) ? 1u : 0u;
    return {counters.offered.load(), counters.full.load(), counters.busy.load(),
        counters.observed.load(), counters.retired.load(), counters.missed.load(),
        counters.surface_failed.load(), bound, bound};
}
#ifdef RB_TEST_HOST
extern "C" void rb_test_set_activation_probe(rb_test_probe probe) { before_activation = probe; }
extern "C" void rb_test_set_take_probe(rb_test_probe probe) { before_take = probe; }
extern "C" void rb_test_set_claim_probe(rb_test_probe probe) { before_claim = probe; }
#endif
