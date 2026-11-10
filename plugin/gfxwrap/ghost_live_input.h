/* Single worker -> single renderer latest-value seam. No provider or model. */
#pragma once
#include "ghost_live_codec.h"
#include <atomic>
#include <stdint.h>

namespace ghost_live {
enum class InputResult : uint32_t {
    ok, bad_seat, bad_incarnation, active_member, retired_incarnation, capacity,
    not_authorized, bad_packet, bad_time, old_source, source_changed, not_newer, exhausted
};
struct SeatSnapshot {
    glive_value value{};
    uint64_t received_ns = 0, revision = 0, gate = 0;
    uint32_t authorized = 0, present = 0, visible = 0;
};
struct Snapshot {
    uint64_t revision = 0, observed_ns = 0;
    SeatSnapshot seats[GLIVE_MAX_PEERS]{};
};
struct InputStats {
    uint64_t accepted = 0, rejected = 0, published = 0, replaced = 0, dropped = 0;
};
class Input {
public:
    // Prototype bound for scoped admissions over this Input lifetime, not a final product limit.
    // Reconnect uses the same active incarnation and consumes no history entry.
    static constexpr unsigned nonce_history_capacity = 256;
    explicit Input(uint64_t stale_after_ns) noexcept;
    Input(const Input &) = delete;
    Input &operator=(const Input &) = delete;

    // One trusted worker only. Packet receipt cannot call control operations.
    // Keep this object through the room lifetime and bounded reconnect. A new
    // object requires a new room authority namespace and retired callbacks.
    // Destroy only after both endpoints have stopped/joined.
    // Production supplies the trusted local receiver room namespace. A null
    // scope preserves the original single-Input namespace for direct users.
    // Source packet bytes never choose or carry this local authority scope.
    InputResult authorize(unsigned seat, const uint8_t incarnation[16], const uint8_t room_scope[16]=nullptr) noexcept;
    InputResult revoke(unsigned seat) noexcept;
    InputResult clear(unsigned seat) noexcept;
    InputResult ingest(unsigned seat, const void *packet, size_t original_bytes,
                       uint64_t received_ns, DecodeResult *decode_reason = nullptr) noexcept;
    bool publish(uint64_t now_ns) noexcept;
    InputStats stats() const noexcept { return stats_; } // worker only
    bool exhausted() const noexcept { return exhausted_; } // worker only
#if defined(SM64_GHOST_TEST_HOOKS)
    void test_drop_next_publication() noexcept {test_drop_=true;}
#endif

    // One renderer only; adopted is renderer-owned and initially {}. Bounded
    // CAS/copy work; no waits, allocation, IO or access to mutable worker data.
    // consume can redact an expired/withdrawn adopted value without publication.
    // Call current AGAIN before actual presentation, not just at pose selection.
    bool consume(Snapshot &adopted, uint64_t now_ns) noexcept;
    bool current(const Snapshot &adopted, unsigned seat, uint64_t now_ns) const noexcept;
    uint64_t gate(unsigned seat) const noexcept {return seat<GLIVE_MAX_PEERS?gates_[seat].load(std::memory_order_acquire):0;}

private:
    friend struct InputWitness; // defined only by the native counter-boundary host
    enum Custody : uint32_t { Empty, Writing, Ready, Reading };
    struct Slot { std::atomic<uint32_t> custody{Empty}; Snapshot snapshot{}; };
    struct Boundary {
        int16_t level = 0, area = 0;
        uint16_t warp = 0;
        bool warping = false, playable = false, hidden = false;
    };
    struct Seat {
        SeatSnapshot snapshot{};
        uint8_t incarnation[16]{};
        uint8_t room_scope[16]{};
        uint64_t generation = 0, continuity = 0, sequence = 0, elapsed = 0, receipt_floor = 0;
        uint32_t fixed[4]{}; // ROM layout/generation, configuration, epoch
        bool have_fixed = false, have_receipt = false;
        Boundary boundary{};
    };
    static constexpr uint64_t revision_limit = UINT64_MAX >> 1;
    const uint64_t stale_after_ns_;
    uint64_t revision_ = 0;
    bool exhausted_ = false;
#if defined(SM64_GHOST_TEST_HOOKS)
    bool test_drop_ = false;
#endif
    Seat seats_[GLIVE_MAX_PEERS]{};
    struct Admission {uint8_t room_scope[16]{},incarnation[16]{};};
    Admission nonce_history_[nonce_history_capacity]{};
    unsigned history_count_ = 0;
    InputStats stats_{};
    // Gate = monotonically ordered boundary/control revision plus admitted bit.
    // A single atomic value avoids an ABA window across separate active/revision
    // fields. Never replace this with memcpy/seqlock over ordinary pose memory.
    alignas(8) std::atomic<uint64_t> gates_[GLIVE_MAX_PEERS]{};
    Slot slots_[3]{};

    bool advance() noexcept;
    void fail_closed() noexcept;
    bool count(uint64_t &) noexcept;
    InputResult reject(InputResult) noexcept;
    void boundary(unsigned seat, bool active) noexcept;
    void clear_value(unsigned seat) noexcept;
    bool expire(uint64_t now_ns) noexcept;
};
static_assert(std::atomic<uint32_t>::is_always_lock_free, "live custody must not hide a lock");
static_assert(std::atomic<uint64_t>::is_always_lock_free, "live gates must be lock-free on x86 too");
static_assert(alignof(Input) >= 8, "live atomic64 gate alignment");
}
