// SPDX-License-Identifier: MIT
// Original finite experiment state; provider admission is owned by main.cpp.
#pragma once
#include "protocol.h"

namespace eos_probe {
struct PeerExperiment {
    wire::Nonce remote_incarnation{};
    wire::Role role = wire::Role::Guest;
    bool session_bound = false, session_confirmed = false;
    uint64_t binding = 0, next_sequence = 1, next_echo_sequence = 1;
    uint64_t next_session_ns = 0, next_echo_ns = 0;
    uint64_t sessions_sent = 0, sessions_received = 0, data_sent = 0, echoes_sent = 0, echoes_suppressed = 0;
    uint64_t session_mismatch = 0, packet_mismatch = 0, send_skipped = 0;
    wire::SequenceWindow arrivals;
    wire::RoundTrips round_trips;

    // The caller already proved provider membership, role, socket and relay
    // witness. SESSION can latch only once; DATA never authorizes or replaces it.
    bool bind_session(const wire::Packet& packet) {
        if (packet.kind != wire::Kind::Session || packet.role != role || !wire::nonzero(packet.sender)) {
            ++packet_mismatch; return false;
        }
        if (session_bound) {
            if (remote_incarnation != packet.sender) { ++session_mismatch; return false; }
            ++sessions_received;
            return true;
        }
        remote_incarnation = packet.sender;
        session_bound = true;
        ++sessions_received;
        return true;
    }
    bool matches(const wire::Packet& packet, const wire::Nonce& local_incarnation) {
        if (!session_bound || packet.role != role || packet.sender != remote_incarnation ||
                packet.recipient != local_incarnation) {
            ++packet_mismatch; return false;
        }
        return true;
    }
    bool echo_due(uint64_t sequence, uint64_t now_ns) {
        if (sequence % 10) return false;
        if (now_ns < next_echo_ns) { ++echoes_suppressed; return false; }
        // Reserve the rate slot even if the SDK send subsequently fails.
        next_echo_ns = now_ns + wire::EchoPeriodNs;
        return true;
    }
};
}  // namespace eos_probe
