// SPDX-License-Identifier: MIT
// Bounded local self-test only. No EOS SDK, network, credentials or provider IDs.
#include "peer_state.h"
#include <iostream>
#include <stdexcept>

namespace {
unsigned checks = 0;
void expect(bool value) {
    ++checks;
    if (!value) throw std::runtime_error("self-test failed");
}
using namespace eos_probe;
wire::Nonce nonce(uint8_t value) { wire::Nonce result{}; result[0] = value; return result; }
wire::Packet data() {
    wire::Packet result{};
    result.kind = wire::Kind::Data; result.role = wire::Role::Guest;
    result.sender = nonce(1); result.recipient = nonce(2);
    result.sequence = 10; result.timestamp_ns = 0x8877665544332211ULL;
    return result;
}
void codec() {
    auto input = data();
    auto bytes = wire::encode(input);
    wire::Packet output{};
    expect(bytes.size() == 424 && bytes[0] == 'E' && bytes[3] == '2');
    expect(bytes[4] == 2 && bytes[5] == 0 && bytes[8] == 0xa8 && bytes[9] == 1);
    expect(bytes[64] == 0x11 && bytes[71] == 0x88);
    expect(wire::decode(bytes.data(), bytes.size(), wire::DataChannel, output));
    expect(output.sender == input.sender && output.recipient == input.recipient &&
           output.sequence == 10 && output.timestamp_ns == input.timestamp_ns);
    expect(!wire::decode(bytes.data(), 423, wire::DataChannel, output));
    expect(!wire::decode(bytes.data(), 425, wire::DataChannel, output));
    expect(!wire::decode(nullptr, 424, wire::DataChannel, output));
    expect(!wire::decode(bytes.data(), 424, wire::SessionChannel, output));
    for (size_t at : {0u, 4u, 6u, 7u, 8u, 10u, 12u, 72u, 423u}) {
        auto bad = bytes;
        bad[at] = 0xff;
        output.sequence = 777;
        expect(!wire::decode(bad.data(), bad.size(), wire::DataChannel, output));
        expect(output.sequence == 777);
    }
    for (unsigned variant = 0; variant < 6; ++variant) {
        auto bad = input;
        if (variant == 0) bad.sender = {};
        if (variant == 1) bad.recipient = {};
        if (variant == 2) bad.sequence = 0;
        if (variant == 3) bad.echoed_sequence = 5;
        if (variant == 4) bad.sequence = wire::MaximumSequence + 1;
        if (variant == 5) bad.timestamp_ns = 0;
        const auto encoded = wire::encode(bad);
        expect(!wire::decode(encoded.data(), encoded.size(), wire::DataChannel, output));
    }
    wire::Buffer r1{};
    expect(!wire::decode(r1.data(), r1.size(), wire::DataChannel, output));
    wire::Packet session{};
    session.sender = nonce(3);
    bytes = wire::encode(session);
    expect(wire::decode(bytes.data(), bytes.size(), wire::SessionChannel, output));
    expect(!wire::decode(bytes.data(), bytes.size(), wire::DataChannel, output));
    session.timestamp_ns = 1;
    bytes = wire::encode(session);
    expect(!wire::decode(bytes.data(), bytes.size(), wire::SessionChannel, output));
    auto echo = input;
    echo.kind = wire::Kind::Echo;
    bytes = wire::encode(echo);
    expect(!wire::decode(bytes.data(), bytes.size(), wire::DataChannel, output));
    echo.echoed_sequence = 10;
    bytes = wire::encode(echo);
    expect(wire::decode(bytes.data(), bytes.size(), wire::DataChannel, output));
}
void sequence_window() {
    wire::SequenceWindow state;
    expect(state.observe(1, 100) && state.observe(3, 200));
    expect(state.holes() == 1 && state.gap_events == 1 && state.gap_positions == 1);
    expect(state.observe(2, 300));
    expect(state.holes() == 0 && state.reordered == 1 && state.unique == 3);
    expect(!state.observe(3, 400) && state.duplicate == 1);
    expect(state.maximum_arrival_gap_ns == 100 && state.last_arrival_ns == 300);
    wire::SequenceWindow aged;
    expect(aged.observe(1, 1) && aged.observe(130, 2));
    expect(aged.low == 3 && aged.holes() == 127 && aged.aged_unresolved == 1);
    expect(!aged.observe(2, 3) && aged.older_than_window == 1 && aged.aged_unresolved == 1);
    expect(aged.observe(129, 4) && aged.reordered == 1 && aged.holes() == 126);
    expect(!aged.observe(129, 5) && aged.duplicate == 1);
    expect(!aged.observe(0, 6) && !aged.observe(wire::MaximumSequence + 1, 6));
    wire::SequenceWindow prefix;
    expect(prefix.observe(500, 1) && prefix.first == 500 && prefix.holes() == 0 && prefix.aged_unresolved == 0);
    wire::SequenceWindow jump;
    expect(jump.observe(1, 1) && jump.observe(wire::MaximumSequence, 2));
    expect(jump.aged_unresolved + jump.holes() == wire::MaximumSequence - 2);
}
void rtt() {
    constexpr uint64_t start = 1000000000, delay = 50000000;
    wire::RoundTrips state;
    state.sent(10, start);
    expect(state.pending() == 1);
    expect(state.echo(10, start + 1, start + delay) == wire::EchoResult::Invalid);
    expect(state.echo(20, start, start + delay) == wire::EchoResult::Invalid);
    expect(state.echo(10, start, start - 1) == wire::EchoResult::Invalid);
    expect(state.echo(10, start, start + delay) == wire::EchoResult::Accepted);
    expect(state.count == 1 && state.percentile(.5) == 50000.0 && state.pending() == 0);
    expect(state.echo(10, start, start + delay) == wire::EchoResult::Duplicate && state.count == 1);
    wire::RoundTrips other_peer;
    expect(other_peer.echo(10, start, start + delay) == wire::EchoResult::Invalid);
    other_peer.sent(11, start);
    expect(other_peer.pending() == 0);
    other_peer.sent(20, start);
    other_peer.expire(start + wire::EchoLifetimeNs + 1);
    expect(other_peer.expired == 1 && other_peer.pending() == 0);
    expect(other_peer.echo(20, start, start + wire::EchoLifetimeNs + 2) == wire::EchoResult::Expired);
    expect(other_peer.expired == 1 && other_peer.count == 0);
    wire::RoundTrips ending;
    ending.sent(10, start);
    expect(ending.pending() == 1 && ending.oldest_pending_ms(start + delay) == 50.0);
    wire::RoundTrips overwritten;
    overwritten.sent(10, start); overwritten.sent(650, start + 1);
    expect(overwritten.unresolved_overwritten == 1 && overwritten.pending() == 1);
    expect(overwritten.echo(10, start, start + delay) == wire::EchoResult::Invalid);
    wire::RoundTrips bounded;
    for (size_t at = 1; at <= wire::RoundTrips::Samples + 1; ++at) {
        const auto sequence = static_cast<uint64_t>(at * 10), stamp = start + at;
        bounded.sent(sequence, stamp);
        expect(bounded.echo(sequence, stamp, stamp + delay) == wire::EchoResult::Accepted);
    }
    expect(bounded.count == wire::RoundTrips::Samples && bounded.overflow == 1);
}
void sessions_and_freshness() {
    PeerExperiment peer;
    peer.role = wire::Role::Guest;
    auto packet = data();
    expect(!peer.matches(packet, nonce(2)) && !peer.session_bound);
    expect(!peer.bind_session(packet) && !peer.session_bound);  // DATA cannot admit a session.
    wire::Packet session{};
    session.sender = nonce(1); session.role = wire::Role::Host;
    expect(!peer.bind_session(session) && !peer.session_bound);
    session.role = wire::Role::Guest;
    expect(peer.bind_session(session) && peer.session_bound);
    expect(peer.matches(packet, nonce(2)));
    session.sender = nonce(3);
    expect(!peer.bind_session(session) && peer.remote_incarnation == nonce(1));
    packet.sender = nonce(3);
    expect(!peer.matches(packet, nonce(2)));
    packet.sender = nonce(1); packet.recipient = nonce(4);
    expect(!peer.matches(packet, nonce(2)));
    expect(peer.echo_due(10, 1000));
    expect(!peer.echo_due(20, 1001) && peer.echoes_suppressed == 1);
    expect(!peer.echo_due(21, wire::EchoPeriodNs + 1001));
    expect(peer.echo_due(30, wire::EchoPeriodNs + 1001));
    uint64_t due = 0;
    expect(wire::advance_sample(0, due) == 1 && due == wire::SamplePeriodNs);
    expect(wire::advance_sample(wire::SamplePeriodNs - 1, due) == 0);
    expect(wire::advance_sample(5 * wire::SamplePeriodNs + 7, due) == 5);
    expect(due == 6 * wire::SamplePeriodNs && wire::advance_sample(due - 1, due) == 0);
}
}  // namespace
int main() {
    try {
        codec(); sequence_window(); rtt(); sessions_and_freshness();
        std::cout << "{\"report_version\":2,\"probe_version\":2,\"event\":\"protocol_self_test\",\"checks\":"
                  << checks << ",\"passed\":true}\n";
        return 0;
    } catch (...) {
        std::cout << "{\"report_version\":2,\"probe_version\":2,\"event\":\"protocol_self_test\",\"passed\":false}\n";
        return 1;
    }
}
