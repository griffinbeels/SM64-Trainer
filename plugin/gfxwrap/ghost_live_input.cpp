#include "ghost_live_input.h"
#include <cstring>
#include <initializer_list>

namespace ghost_live {
namespace {
bool identity(const uint8_t *nonce) noexcept {
    if (!nonce) return false;
    for (unsigned i = 0; i < 16; ++i) if (nonce[i]) return true;
    return false;
}
bool drawable(const glive_value &value) noexcept {
    const uint32_t fields = GC_VALID_CLOCK | GC_VALID_POSE;
    return (value.sample.valid_fields & fields) == fields && !value.sample.pose.hidden;
}
}
Input::Input(uint64_t stale_after_ns) noexcept : stale_after_ns_(stale_after_ns) {
    if (!stale_after_ns) fail_closed();
}
void Input::fail_closed() noexcept {
    exhausted_ = true; revision_ = revision_limit;
    for (unsigned i = 0; i < GLIVE_MAX_PEERS; ++i) {
        boundary(i, false); clear_value(i);
        seats_[i].snapshot.authorized = 0;
    }
}
bool Input::advance() noexcept {
    if (exhausted_) return false;
    if (revision_ >= revision_limit - 1) { fail_closed(); return false; }
    ++revision_; return true;
}
bool Input::count(uint64_t &value) noexcept {
    if (value == UINT64_MAX) { fail_closed(); return false; }
    ++value; return true;
}
InputResult Input::reject(InputResult result) noexcept {
    return count(stats_.rejected) ? result : InputResult::exhausted;
}
void Input::boundary(unsigned seat, bool active) noexcept {
    const uint64_t gate = (revision_ << 1) | uint64_t(active);
    gates_[seat].store(gate, std::memory_order_release);
    seats_[seat].snapshot.gate = gate;
    seats_[seat].snapshot.revision = revision_;
}
void Input::clear_value(unsigned seat) noexcept {
    auto &out = seats_[seat].snapshot;
    std::memset(&out.value, 0, sizeof(out.value));
    out.received_ns = 0; out.present = out.visible = 0; out.revision = revision_;
}
InputResult Input::authorize(unsigned seat, const uint8_t nonce[16], const uint8_t room_scope[16]) noexcept {
    if (exhausted_) return InputResult::exhausted;
    if (seat >= GLIVE_MAX_PEERS) return reject(InputResult::bad_seat);
    if (!identity(nonce) || (room_scope && !identity(room_scope))) return reject(InputResult::bad_incarnation);
    const uint8_t implicit_scope[16]{};
    const auto *scope=room_scope?room_scope:implicit_scope;
    auto &s = seats_[seat];
    if (s.snapshot.authorized)
        return !std::memcmp(s.incarnation, nonce, 16) && !std::memcmp(s.room_scope,scope,16)
            ? InputResult::ok : reject(InputResult::active_member);
    for (unsigned i = 0; i < history_count_; ++i)
        if (!std::memcmp(nonce_history_[i].incarnation, nonce, 16)
                && !std::memcmp(nonce_history_[i].room_scope,scope,16)) return reject(InputResult::retired_incarnation);
    if (history_count_ == nonce_history_capacity) return reject(InputResult::capacity);
    if (!advance()) return InputResult::exhausted;
    auto &history=nonce_history_[history_count_++];
    std::memcpy(history.incarnation, nonce, 16);std::memcpy(history.room_scope,scope,16);
    s = {}; std::memcpy(s.incarnation, nonce, 16); s.snapshot.authorized = 1;
    std::memcpy(s.room_scope,scope,16);
    boundary(seat, true);
    return InputResult::ok;
}
InputResult Input::revoke(unsigned seat) noexcept {
    if (exhausted_) return InputResult::exhausted;
    if (seat >= GLIVE_MAX_PEERS) return reject(InputResult::bad_seat);
    if (!seats_[seat].snapshot.authorized) return InputResult::ok;
    if (!advance()) return InputResult::exhausted;
    boundary(seat, false); // authority withdrawn before any subsequent publication
    seats_[seat].snapshot.authorized = 0; clear_value(seat);
    return InputResult::ok;
}
InputResult Input::clear(unsigned seat) noexcept {
    if (exhausted_) return InputResult::exhausted;
    if (seat >= GLIVE_MAX_PEERS) return reject(InputResult::bad_seat);
    auto &s = seats_[seat];
    if (!s.snapshot.present) return InputResult::ok;
    if (!advance()) return InputResult::exhausted;
    boundary(seat, s.snapshot.authorized != 0); clear_value(seat);
    return InputResult::ok;
}
InputResult Input::ingest(unsigned seat, const void *packet, size_t original_bytes,
                          uint64_t received_ns, DecodeResult *decode_reason) noexcept {
    if (decode_reason) *decode_reason = DecodeResult::bad_argument;
    if (exhausted_) return InputResult::exhausted;
    if (seat >= GLIVE_MAX_PEERS) return reject(InputResult::bad_seat);
    auto &s = seats_[seat];
    if (!s.snapshot.authorized) return reject(InputResult::not_authorized);
    glive_value value{};
    const auto decoded = decode(packet, original_bytes, s.incarnation, value);
    if (decode_reason) *decode_reason = decoded;
    if (decoded != DecodeResult::ok) return reject(InputResult::bad_packet);
    if (s.have_receipt && received_ns < s.receipt_floor) return reject(InputResult::bad_time);
    const auto &h = value.header; const auto &sample = value.sample;
    const uint32_t fixed[] = {h.rom_layout, h.rom_generation, h.configuration, sample.epoch};
    if (h.source_generation < s.generation) return reject(InputResult::old_source);
    if (h.source_generation == s.generation) {
        if (std::memcmp(fixed, s.fixed, sizeof(fixed))) return reject(InputResult::source_changed);
        if (h.continuity < s.continuity || sample.sequence <= s.sequence || h.elapsed_ns < s.elapsed)
            return reject(InputResult::not_newer);
    }
    Boundary key{};
    key.level = sample.level; key.area = sample.area; key.warp = sample.pending_warp_op;
    key.warping = sample.delayed_warp_timer != 0;
    key.playable = (sample.valid_fields & (GC_VALID_CLOCK | GC_VALID_POSE)) == (GC_VALID_CLOCK | GC_VALID_POSE);
    key.hidden = sample.pose.hidden != 0;
    const auto &old = s.boundary;
    const bool changed = !s.have_fixed || h.source_generation != s.generation || h.continuity != s.continuity
        || key.level != old.level || key.area != old.area || key.warp != old.warp || key.warping != old.warping
        || key.playable != old.playable || key.hidden != old.hidden;
    if (!advance()) return InputResult::exhausted;
    if (!count(stats_.accepted)) return InputResult::exhausted;
    if (changed) boundary(seat, true); // retained even if a newer pose replaces this before publish
    s.generation = h.source_generation; s.continuity = h.continuity;
    s.sequence = sample.sequence; s.elapsed = h.elapsed_ns; s.receipt_floor = received_ns;
    std::memcpy(s.fixed, fixed, sizeof(fixed)); s.have_fixed = s.have_receipt = true; s.boundary = key;
    s.snapshot.value = value; s.snapshot.received_ns = received_ns; s.snapshot.revision = revision_;
    s.snapshot.present = 1; s.snapshot.visible = drawable(value);
    return InputResult::ok;
}
bool Input::expire(uint64_t now_ns) noexcept {
    for (const auto &seat : seats_)
        if (seat.snapshot.present && now_ns < seat.snapshot.received_ns) return false;
    for (unsigned i = 0; i < GLIVE_MAX_PEERS; ++i) {
        const auto &s = seats_[i].snapshot;
        if (s.present && now_ns - s.received_ns > stale_after_ns_) clear(i);
    }
    return true;
}
bool Input::publish(uint64_t now_ns) noexcept {
    if (!expire(now_ns)) { reject(InputResult::bad_time); return false; }
    Slot *chosen = nullptr; bool replaced = false;
    // Single producer; claim exclusive custody before touching ordinary bytes.
    // A consumer can own at most one Reading slot. These finite scans still
    // may lose a race; dropping then is preferable to waiting on rendering.
    for (uint32_t wanted : {uint32_t(Empty), uint32_t(Ready)}) {
        for (auto &slot : slots_) {
            auto expected = wanted;
            if (slot.custody.compare_exchange_strong(expected, Writing, std::memory_order_acq_rel,
                                                      std::memory_order_relaxed)) {
                chosen = &slot; replaced = wanted == Ready; break;
            }
        }
        if (chosen) break;
    }
    if (!chosen) { count(stats_.dropped); return false; }
    if (replaced) count(stats_.replaced);
    count(stats_.published);
    Snapshot next{}; next.revision = revision_; next.observed_ns = now_ns;
    for (unsigned i = 0; i < GLIVE_MAX_PEERS; ++i) next.seats[i] = seats_[i].snapshot;
    chosen->snapshot = next;
    chosen->custody.store(Ready, std::memory_order_release);
    return true;
}
bool Input::current(const Snapshot &adopted, unsigned seat, uint64_t now_ns) const noexcept {
    if (seat >= GLIVE_MAX_PEERS || !stale_after_ns_) return false;
    const auto &value = adopted.seats[seat];
    const uint64_t gate = gates_[seat].load(std::memory_order_acquire);
    return value.present && (gate & 1) && value.gate == gate && now_ns >= value.received_ns
        && now_ns - value.received_ns <= stale_after_ns_;
}
bool Input::consume(Snapshot &adopted, uint64_t now_ns) noexcept {
    bool changed = false;
    for (auto &slot : slots_) {
        uint32_t expected = Ready;
        if (!slot.custody.compare_exchange_strong(expected, Reading, std::memory_order_acq_rel,
                                                  std::memory_order_relaxed)) continue;
        if (slot.snapshot.revision > adopted.revision) { adopted = slot.snapshot; changed = true; }
        slot.custody.store(Empty, std::memory_order_release);
    }
    for (unsigned i = 0; i < GLIVE_MAX_PEERS; ++i) {
        auto &value = adopted.seats[i];
        const uint64_t gate = gates_[i].load(std::memory_order_acquire);
        const uint32_t active = uint32_t(gate & 1);
        if (value.authorized != active) { value.authorized = active; changed = true; }
        if (value.present && (!(gate & 1) || value.gate != gate || now_ns < value.received_ns
                || now_ns - value.received_ns > stale_after_ns_)) {
            std::memset(&value.value, 0, sizeof(value.value));
            value.present = value.visible = 0; value.received_ns = 0; changed = true;
        }
    }
    return changed;
}
}
