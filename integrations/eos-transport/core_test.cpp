// SPDX-License-Identifier: MIT
// SDK-free contracts; no provider IDs, credentials, network or Windows APIs.
#include "json.hpp"
#include "state.hpp"
#include <iostream>

namespace {
using namespace eos_transport;
unsigned checks = 0;
void check(bool value) { ++checks; if (!value) throw Invalid(); }
template<class Function> void refuses(Function function) { bool refused = false; try { function(); } catch (const Invalid&) { refused = true; } check(refused); }
Nonce nonce(uint8_t value) { Nonce result{}; result[0] = value; return result; }
void json_tests() {
    const auto object = Parser(" {\"v\":1,\"op\":\"identity\",\"request_id\":18446744073709551615} ").parse();
    check(number(object, "v") == 1 && text(object, "op") == "identity");
    check(number(object, "request_id") == UINT64_MAX);
    refuses([] { Parser("{\"v\":1,\"v\":2}").parse(); });
    refuses([] { Parser("{\"n\":18446744073709551616}").parse(); });
    refuses([] { Parser("{\"n\":01}").parse(); });
    refuses([] { Parser("{\"n\":-1}").parse(); });
    refuses([] { Parser("{\"n\":1.0}").parse(); });
    refuses([] { Parser("{\"n\":true}").parse(); });
    refuses([] { Parser("{\"n\":{}}").parse(); });
    refuses([] { Parser("{\"n\":[]}").parse(); });
    refuses([] { Parser("{\"s\":\"\\u0000\"}").parse(); });
    refuses([] { Parser("{\"s\":\"\\u00e9\"}").parse(); });
    refuses([] { Parser("{\"s\":\"\\n\"}").parse(); });
    refuses([] { Parser(std::string(LineLimit + 1, ' ')).parse(); });
    check(text(Parser("{\"s\":\"\\u0041\\\"\\\\\"}").parse(), "s") == "A\"\\");
    check(quote("a\"b\\c") == "\"a\\\"b\\\\c\"");
    refuses([&] { number(object, "op"); });
    refuses([&] { text(object, "missing"); });
}
void encoding_tests() {
    for (size_t size : {1u, 2u, 3u, 4u, 511u, 512u, 1024u}) {
        std::vector<uint8_t> source(size); for (size_t at = 0; at < size; ++at) source[at] = static_cast<uint8_t>(at);
        std::vector<uint8_t> decoded; check(unbase64(base64(source), size, decoded) && decoded == source);
        check(!unbase64(base64(source), size - 1, decoded));
    }
    for (const auto* invalid : {"A", "AAAA=", "A===", "AA=A", "AB==", "AAB=", "!!!!", " AAAA", ""}) {
        std::vector<uint8_t> decoded; check(!unbase64(invalid, 1024, decoded));
    }
    Nonce decoded{}; check(unhex(hex(nonce(10)), decoded) && decoded == nonce(10));
    check(!unhex("bad", decoded));
    Packet data{}; data.kind = Kind::Data; data.sender = nonce(1); data.recipient = nonce(2); data.sequence = 8; data.payload = {0, 1, 255};
    auto bytes = encode(data); Packet result{};
    check(bytes.size() == HeaderBytes + 3 && bytes[48] == 8 && bytes[49] == 0);
    check(decode(bytes.data(), bytes.size(), false, 1, result) && result.payload == data.payload);
    check(!decode(bytes.data(), bytes.size(), true, 1, result));
    check(!decode(bytes.data(), bytes.size() - 1, false, 1, result));
    check(!decode(nullptr, bytes.size(), false, 1, result));
    for (size_t at : {0u, 4u, 6u, 7u, 8u, 10u, 12u, 56u}) { auto bad = bytes; bad[at] = 255; check(!decode(bad.data(), bad.size(), false, 1, result)); }
    data.kind = Kind::Control; bytes = encode(data);
    check(decode(bytes.data(), bytes.size(), true, 2, result));
    check(!decode(bytes.data(), bytes.size(), false, 2, result));
    data.payload.resize(ControlLimit + 1); bytes = encode(data); check(!decode(bytes.data(), bytes.size(), true, 2, result));
    data.kind = Kind::Data; data.payload.resize(PayloadLimit + 1); bytes = encode(data); check(!decode(bytes.data(), bytes.size(), false, 1, result));
    Packet hello{}; hello.sender = nonce(3); bytes = encode(hello);
    check(decode(bytes.data(), bytes.size(), false, 0, result));
    check(!decode(bytes.data(), bytes.size(), false, 1, result));
}
void challenge_tests() {
    Session session; check(!session.bind(Nonce{})); check(session.bind(nonce(1)));
    session.relayed = {true, true};
    Packet ack{}; ack.kind = Kind::Ack; ack.sender = nonce(2); ack.recipient = nonce(9);
    check(session.acknowledge(ack, 0) == AckResult::Rejected && !session.ready());
    ack.recipient = nonce(1);
    check(session.acknowledge(ack, 0) == AckResult::Accepted && !session.ready());
    check(session.acknowledge(ack, 1) == AckResult::Accepted && session.ready());
    ack.sender = nonce(3); check(session.acknowledge(ack, 1) == AckResult::Conflict && session.remote == nonce(2));
    Packet data{}; data.kind = Kind::Data; data.sender = nonce(2); data.recipient = nonce(1); data.sequence = 1;
    check(session.receive(data, false)); check(!session.receive(data, false));
    data.sequence = 2; data.recipient = nonce(9); check(!session.receive(data, false));
    session.invalidate(); check(!session.ready() && !session.bind(nonce(1)));
    check(session.bind(nonce(4))); session.relayed = {true, true};
    ack.sender = nonce(2); ack.recipient = nonce(1); check(session.acknowledge(ack, 0) == AckResult::Rejected);
    ack.recipient = nonce(4); check(session.acknowledge(ack, 0) == AckResult::Accepted);
    check(session.acknowledge(ack, 1) == AckResult::Accepted && session.ready());
    data.sender = nonce(2); data.recipient = nonce(1); check(!session.receive(data, false));
    data.recipient = nonce(4); check(session.receive(data, false));
    check(!session.receive(data, true));
    data.kind = Kind::Control;
    check(session.receive(data, true));  // Independent reliable/control high-water.
    check(!session.receive(data, true));
    Budget budget(2, 2); check(budget.take(1, 100, 100)); check(budget.take(1, 100, 100));
    check(!budget.take(1, 100, 100)); check(budget.take(1, 150, 100)); check(!budget.take(1, 150, 100));
}
}
int main() {
    try { json_tests(); encoding_tests(); challenge_tests(); std::cout << "{\"test\":\"eos_transport_core\",\"checks\":" << checks << ",\"passed\":true}\n"; return 0; }
    catch (...) { std::cout << "{\"test\":\"eos_transport_core\",\"checks\":" << checks << ",\"passed\":false}\n"; return 1; }
}
