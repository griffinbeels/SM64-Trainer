// SPDX-License-Identifier: MIT
// Original standalone transport experiment; no Trainer wire/game definitions.
#pragma once
#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>

namespace eos_probe::wire {
constexpr uint16_t Version = 2;
constexpr size_t Bytes = 424, HeaderBytes = 80;
constexpr uint8_t DataChannel = 7, SessionChannel = 8;
constexpr uint64_t MaximumSequence = 1000000;
constexpr uint64_t SamplePeriodNs = 33333333, EchoPeriodNs = 333333333;
constexpr uint64_t EchoLifetimeNs = 8000000000;
using Nonce = std::array<uint8_t, 16>;
using Buffer = std::array<uint8_t, Bytes>;
enum class Kind : uint8_t { Data = 1, Echo = 2, Session = 3 };
enum class Role : uint8_t { Host = 1, Guest = 2 };

struct Packet {
    Kind kind = Kind::Session;
    Role role = Role::Guest;
    Nonce sender{}, recipient{};
    uint64_t sequence = 0, echoed_sequence = 0, timestamp_ns = 0;
};
inline bool nonzero(const Nonce& value) {
    return std::any_of(value.begin(), value.end(), [](uint8_t byte) { return byte != 0; });
}
inline void put(uint8_t* destination, uint64_t value, size_t width) {
    for (size_t at = 0; at < width; ++at) destination[at] = static_cast<uint8_t>(value >> (8 * at));
}
inline uint64_t get(const uint8_t* source, size_t width) {
    uint64_t value = 0;
    for (size_t at = 0; at < width; ++at) value |= static_cast<uint64_t>(source[at]) << (8 * at);
    return value;
}
inline Buffer encode(const Packet& packet) {
    Buffer result{};
    result[0] = 'E'; result[1] = 'P'; result[2] = 'R'; result[3] = '2';
    put(result.data() + 4, Version, 2);
    result[6] = static_cast<uint8_t>(packet.kind); result[7] = static_cast<uint8_t>(packet.role);
    put(result.data() + 8, Bytes, 2); put(result.data() + 10, HeaderBytes, 2);
    std::copy(packet.sender.begin(), packet.sender.end(), result.begin() + 16);
    put(result.data() + 32, packet.sequence, 8);
    std::copy(packet.recipient.begin(), packet.recipient.end(), result.begin() + 40);
    put(result.data() + 56, packet.echoed_sequence, 8);
    put(result.data() + 64, packet.timestamp_ns, 8);
    return result;
}
inline bool decode(const uint8_t* bytes, size_t size, uint8_t channel, Packet& result) {
    if (!bytes || size != Bytes || bytes[0] != 'E' || bytes[1] != 'P' || bytes[2] != 'R' ||
            bytes[3] != '2' || get(bytes + 4, 2) != Version || get(bytes + 8, 2) != Bytes ||
            get(bytes + 10, 2) != HeaderBytes || get(bytes + 12, 4) != 0 ||
            std::any_of(bytes + 72, bytes + Bytes, [](uint8_t byte) { return byte != 0; }) ||
            (bytes[7] != 1 && bytes[7] != 2)) return false;
    Packet candidate{};
    candidate.kind = static_cast<Kind>(bytes[6]); candidate.role = static_cast<Role>(bytes[7]);
    std::copy(bytes + 16, bytes + 32, candidate.sender.begin());
    std::copy(bytes + 40, bytes + 56, candidate.recipient.begin());
    candidate.sequence = get(bytes + 32, 8); candidate.echoed_sequence = get(bytes + 56, 8);
    candidate.timestamp_ns = get(bytes + 64, 8);
    if (!nonzero(candidate.sender) || candidate.sequence > MaximumSequence ||
            candidate.echoed_sequence > MaximumSequence) return false;
    if (candidate.kind == Kind::Session) {
        if (channel != SessionChannel || candidate.sequence || candidate.echoed_sequence ||
                candidate.timestamp_ns || nonzero(candidate.recipient)) return false;
    } else if (candidate.kind == Kind::Data || candidate.kind == Kind::Echo) {
        if (channel != DataChannel || !candidate.sequence || !candidate.timestamp_ns ||
                !nonzero(candidate.recipient) ||
                (candidate.kind == Kind::Data && candidate.echoed_sequence) ||
                (candidate.kind == Kind::Echo && !candidate.echoed_sequence)) return false;
    } else return false;
    result = candidate;
    return true;
}

// Counts observations within a finite window, not inferred network-loss rates.
// Once a hole ages out we cannot distinguish a later old packet from a duplicate.
struct SequenceWindow {
    static constexpr size_t Width = 128;
    std::array<uint64_t, Width> seen{};
    uint64_t first = 0, high = 0, low = 0, unique = 0, duplicate = 0, reordered = 0;
    uint64_t older_than_window = 0, gap_events = 0, gap_positions = 0, aged_unresolved = 0;
    uint64_t last_arrival_ns = 0, maximum_arrival_gap_ns = 0;

    bool observe(uint64_t sequence, uint64_t now_ns) {
        if (!sequence || sequence > MaximumSequence) return false;
        if (!high) first = high = low = sequence;
        else if (sequence < low) { ++older_than_window; return false; }
        else if (sequence > high) {
            if (sequence > high + 1) { ++gap_events; gap_positions += sequence - high - 1; }
            const uint64_t next_low = (std::max)(first, sequence >= Width ? sequence - Width + 1 : 1);
            if (next_low > low) {
                uint64_t observed = 0;
                for (auto value : seen) if (value >= low && value < next_low) ++observed;
                aged_unresolved += next_low - low - observed;
                low = next_low;
            }
            high = sequence;
        }
        if (seen[sequence % Width] == sequence) { ++duplicate; return false; }
        if (sequence < high) ++reordered;
        seen[sequence % Width] = sequence;
        ++unique;
        if (last_arrival_ns && now_ns >= last_arrival_ns)
            maximum_arrival_gap_ns = (std::max)(maximum_arrival_gap_ns, now_ns - last_arrival_ns);
        last_arrival_ns = now_ns;
        return true;
    }
    uint64_t holes() const {
        if (!high) return 0;
        uint64_t observed = 0;
        for (auto value : seen) if (value >= low && value <= high) ++observed;
        return high - low + 1 - observed;
    }
};

enum class EchoResult { Accepted, Invalid, Duplicate, Expired };
struct RoundTrips {
    struct Outstanding { uint64_t sequence = 0, sent_ns = 0; bool replied = false, expired = false; };
    static constexpr size_t Pending = 64, Samples = 1024;
    std::array<Outstanding, Pending> outstanding{};
    std::array<double, Samples> sample_us{};
    size_t count = 0;
    uint64_t invalid = 0, duplicate = 0, expired = 0, unresolved_overwritten = 0, overflow = 0;

    void expire(uint64_t now_ns) {
        for (auto& item : outstanding) if (item.sequence && !item.replied && !item.expired &&
                now_ns >= item.sent_ns && now_ns - item.sent_ns > EchoLifetimeNs) {
            item.expired = true; ++expired;
        }
    }
    void sent(uint64_t sequence, uint64_t now_ns) {
        if (!sequence || sequence % 10) return;
        auto& slot = outstanding[(sequence / 10) % Pending];
        if (slot.sequence && !slot.replied && !slot.expired) ++unresolved_overwritten;
        slot = Outstanding{sequence, now_ns, false, false};
    }
    EchoResult echo(uint64_t sequence, uint64_t stamped_ns, uint64_t now_ns) {
        if (!sequence || sequence % 10) { ++invalid; return EchoResult::Invalid; }
        auto& item = outstanding[(sequence / 10) % Pending];
        if (item.sequence != sequence || item.sent_ns != stamped_ns || now_ns < stamped_ns) {
            ++invalid; return EchoResult::Invalid;
        }
        if (item.replied) { ++duplicate; return EchoResult::Duplicate; }
        if (item.expired || now_ns - stamped_ns > EchoLifetimeNs) {
            if (!item.expired) { item.expired = true; ++expired; }
            return EchoResult::Expired;
        }
        item.replied = true;
        if (count < Samples) sample_us[count++] = static_cast<double>(now_ns - stamped_ns) / 1000.0;
        else ++overflow;
        return EchoResult::Accepted;
    }
    uint64_t pending() const {
        uint64_t result = 0;
        for (const auto& item : outstanding) if (item.sequence && !item.replied && !item.expired) ++result;
        return result;
    }
    double oldest_pending_ms(uint64_t now_ns) const {
        uint64_t oldest = now_ns;
        for (const auto& item : outstanding) if (item.sequence && !item.replied && !item.expired)
            oldest = (std::min)(oldest, item.sent_ns);
        return static_cast<double>(now_ns - oldest) / 1000000.0;
    }
    double percentile(double fraction) const {
        if (!count) return 0;
        auto sorted = sample_us;
        std::sort(sorted.begin(), sorted.begin() + count);
        return sorted[static_cast<size_t>((count - 1) * fraction)];
    }
};

// Return only the newest scheduled sample. Missed intervals are counted, not
// replayed as a burst of stale application sends after a pause.
inline uint64_t advance_sample(uint64_t now_ns, uint64_t& due_ns) {
    if (now_ns < due_ns) return 0;
    const uint64_t intervals = 1 + (now_ns - due_ns) / SamplePeriodNs;
    due_ns += intervals * SamplePeriodNs;
    return intervals;
}
}  // namespace eos_probe::wire
