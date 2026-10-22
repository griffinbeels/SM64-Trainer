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
// A sender can spend its whole control allowance on one peer. Keep the
// per-peer receiver at least as permissive, with a small burst margin.
constexpr double ControlPacketBurst = 6, ControlPacketRate = 16;
struct PeerState {
    uint64_t ref = 0, member_epoch = 0, transport_epoch = 1, reconnect_until = 0;
    Session session;
    std::vector<uint8_t> latest;
    uint64_t sample_id = 0, sample_qpc = 0, sent_sample = 0, pending_since = 0;
    bool published_ready = false, failed = false, kicked = false;
    Budget incoming_data{12, 120}, incoming_control{ControlPacketBurst + 2, ControlPacketRate};

    bool reset(uint64_t now, uint64_t frequency, bool force_epoch) {
        const bool active = session.bound || published_ready;
        session.invalidate(); latest.clear(); sample_id = sample_qpc = sent_sample = pending_since = 0;
        published_ready = false;
        if (!active && !force_epoch) return false;
        ++transport_epoch; reconnect_until = now + 15 * frequency;
        return true;
    }
    template<class Reset> void failed_kick(Reset reset_peer) {
        if (!kicked) return;
        // The failed removal's first announcement was unbindable while kicked.
        // Clear it before creating another epoch and announcing a fresh bind.
        kicked = false;
        reset_peer();
    }
};

// QueryInvites is read-only and must never own the room-mutation slot. An
// expired SDK call stays active until its callback, bounding outstanding work.
struct InviteQuery {
    struct Completion { uint64_t request = 0; bool publish = false; };
    uint64_t request = 0, until = 0;
    bool active = false, expired = false;
    bool begin(uint64_t id, uint64_t now, uint64_t frequency) {
        if (active) return false;
        request = id; until = now + 10 * frequency; active = true; expired = false;
        return true;
    }
    bool timed_out(uint64_t now) const { return active && !expired && now > until; }
    uint64_t expire() {
        expired = true; const auto id = request; request = 0; return id;
    }
    Completion finish() {
        const Completion completed{request, active && !expired};
        request = until = 0; active = expired = false;
        return completed;
    }
};
}  // namespace eos_transport
