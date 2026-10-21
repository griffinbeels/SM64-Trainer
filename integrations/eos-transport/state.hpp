// SPDX-License-Identifier: MIT
// SDK-independent epoch/challenge state. Provider membership is checked outside.
#pragma once
#include "wire.hpp"

namespace eos_transport {
enum class AckResult { Accepted, Rejected, Conflict };
struct Session {
    Nonce local{}, remote{}, previous{};
    bool bound = false;
    std::array<bool, 2> relayed{}, acknowledged{};
    std::array<uint64_t, 2> hello_due{};
    uint64_t sent_data = 0, received_data = 0, sent_control = 0, received_control = 0;
    void invalidate() {
        if (bound) previous = local;
        local = {}; remote = {}; bound = false; relayed = {}; acknowledged = {}; hello_due = {};
        sent_data = received_data = sent_control = received_control = 0;
    }
    bool bind(const Nonce& nonce) {
        if (!nonzero(nonce) || nonce == previous) return false;
        if (bound) return local == nonce;
        local = nonce; bound = true; remote = {}; acknowledged = {}; return true;
    }
    bool ready() const { return bound && nonzero(remote) && relayed[0] && relayed[1] && acknowledged[0] && acknowledged[1]; }
    AckResult acknowledge(const Packet& packet, size_t socket) {
        if (socket >= 2 || !bound || !relayed[socket] || packet.kind != Kind::Ack || packet.recipient != local || !nonzero(packet.sender)) return AckResult::Rejected;
        if (nonzero(remote) && remote != packet.sender) return AckResult::Conflict;
        remote = packet.sender; acknowledged[socket] = true; return AckResult::Accepted;
    }
    bool receive(const Packet& packet, bool control) {
        if (!ready() || packet.kind != (control ? Kind::Control : Kind::Data) || packet.sender != remote || packet.recipient != local) return false;
        auto& high = control ? received_control : received_data;
        if (!packet.sequence || packet.sequence <= high) return false;
        high = packet.sequence; return true;
    }
};
struct Budget {
    double credit = 0, capacity = 0, rate = 0;
    uint64_t sampled = 0;
    Budget(double burst, double per_second) : credit(burst), capacity(burst), rate(per_second) {}
    bool take(double amount, uint64_t now, uint64_t frequency) {
        if (sampled && now >= sampled) credit = (std::min)(capacity, credit + static_cast<double>(now - sampled) / static_cast<double>(frequency) * rate);
        sampled = now;
        if (amount > credit) return false;
        credit -= amount; return true;
    }
};
}  // namespace eos_transport
