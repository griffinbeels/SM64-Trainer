/* CPU-only, hidden-process cross-bitness witness. No capture/GPU/leases. */
#include "gpu_delivery.h" // real runtime reason constants; no runtime/GPU code linked
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <sstream>
#include <vector>
#include <iostream>
using gpu_channel::Result;
const char *label(Result r) {
    const char *names[] = {"ok", "empty", "busy", "invalid", "stale", "repeated", "out_of_order",
        "closed", "owner_gone", "wrong_worker", "exhausted", "system_error"};
    return names[unsigned(r)];
}
int main(int argc, char **argv) {
    if (argc != 8) return 2;
    gc_header h{};
    h.owner_pid = strtoul(argv[1], nullptr, 10); h.owner_birth = _strtoui64(argv[2], nullptr, 10);
    h.nonce_lo = _strtoui64(argv[3], nullptr, 10); h.nonce_hi = _strtoui64(argv[4], nullptr, 10);
    h.width = strtoul(argv[5], nullptr, 10); h.height = strtoul(argv[6], nullptr, 10);
    h.epoch = 7; h.generation = strtoul(argv[7], nullptr, 10); h.qpc_frequency = 10000000;
    h.luid_low = 0xabcdef12; h.luid_high = -7; h.offer_count = 8; h.table_count = 2;
    h.table[0] = {0x100, 4}; h.table[1] = {0x200, 8};
    h.packet_count = 8; h.packet_bytes = 1024*1024; h.pending_bytes = 4*1024*1024; h.ram_budget = GC_MAX_MAP_BYTES;
    const wchar_t *names[2] = {L"Local\\SM64.GpuChannel.fixture.a", L"Local\\SM64.GpuChannel.fixture.b"};
    for (unsigned i = 0; i < 2; ++i) memcpy(h.texture_names[i], names[i], (wcslen(names[i])+1)*2);
    gpu_channel::Producer producer;
    Result created = producer.create(h);
    std::cout << "{\"result\":\"" << label(created) << "\",\"pid\":" << GetCurrentProcessId()
        << ",\"birth\":" << gpu_channel::process_birth(GetCurrentProcess()) << ",\"pointer_bytes\":" << sizeof(void *)
        << ",\"total_bytes\":" << producer.header().total_bytes << ",\"sample_bytes\":" << producer.header().sample_bytes
        << ",\"header_bytes\":" << sizeof(gc_header) << ",\"offer_bytes\":" << sizeof(gc_offer)
        << ",\"table_offset\":" << offsetof(gc_header, table) << ",\"name_offset\":" << offsetof(gc_header, texture_names)
        << "}" << std::endl;
    if (created != Result::ok) return 0;
    std::vector<uint8_t> sample(producer.header().sample_bytes);
    gc_decision cache[GC_MAX_OFFERS]{};
    std::string line;
    while (std::getline(std::cin, line)) {
        std::istringstream in(line); std::string command; in >> command;
        Result result = Result::invalid; unsigned slot = 0; uint64_t token = 0, occurrence = 0;
        gc_decision d{}; gc_receipt receipt{};
        if (command == "quit") { producer.close(); break; }
        if (command == "publish") {
            unsigned counter = 0; in >> slot >> token >> occurrence >> counter;
            gc_offer o{}; o.token = token; o.occurrence = occurrence;
            o.list_qpc = 10000 + occurrence; o.boundary_qpc = o.list_qpc + 5;
            o.stamp_bytes = 12; o.lengths[0] = 4; o.lengths[1] = 8;
            o.vi_origin = 0x123450; o.lists_since = 1; o.outcome = 1;
            uint8_t stamps[12]{}; memcpy(stamps, &counter, 4);
            for (unsigned i = 4; i < 12; ++i) stamps[i] = uint8_t(occurrence+i);
            for (size_t i = 0; i < sample.size(); ++i) sample[i] = (i%4 == 3) ? 255 : uint8_t(i*17+occurrence*13);
            result = producer.publish(slot, o, stamps, sample.data());
        } else if (command == "take") {
            result = producer.take_decision(slot, d);
            if (result == Result::ok) cache[slot] = d;
        } else if (command == "retire") {
            in >> slot >> token; result = producer.retire_offer(slot, token);
        } else if (command == "bridge") {
            unsigned decision_slot; in >> slot >> token >> decision_slot;
            if (decision_slot < GC_MAX_OFFERS) result = producer.publish_bridge(slot, token, cache[decision_slot]);
        } else if (command == "receipt") {
            result = producer.take_receipt(slot, receipt);
        } else if (command == "key0") {
            in >> slot >> token; result = producer.retire_bridge_after_key0(slot, token);
        } else if (command == "status") {
            unsigned state, reason=0; in >> state >> reason; result = producer.status(state, reason, 12345);
        } else if (command == "sourcegap") {
            result = producer.close(GC_REASON_RUNTIME | GD_SOURCE_GAP);
        } else if (command == "fault") {
            result = producer.status(GC_FAULT, GC_REASON_RUNTIME | GD_SOURCE_GAP, 12345);
        } else if (command == "cancel") {
            result = producer.close(GC_REASON_RUNTIME | GD_CANCELLED);
        } else if (command == "frontier") {
            uint64_t heartbeat, qpc, occurrence, serial;
            in >> heartbeat >> qpc >> occurrence >> serial;
            result = producer.status(GC_ACTIVE, 0, heartbeat, qpc, occurrence, serial);
        } else if (command == "close") {
            unsigned reason=0; in >> reason; result = producer.close(reason);
        } else if (command == "validate") {
            gc_header snapshot{};
            if (producer.test_view()) {
                memcpy(&snapshot, producer.test_view(), sizeof snapshot);
                result = gpu_channel::validate(snapshot, producer.header().total_bytes) ? Result::ok : Result::invalid;
            }
        } else if (command == "thread") {
            HANDLE thread = CreateThread(nullptr, 0, [](void *arg)->DWORD {
                auto *p = static_cast<gpu_channel::Producer *>(arg);
                return DWORD(p->status(GC_ACTIVE, 0, 100));
            }, &producer, 0, nullptr);
            if (thread) {
                if (WaitForSingleObject(thread, 1000) == WAIT_OBJECT_0) { DWORD code; GetExitCodeThread(thread, &code); result = Result(code); }
                CloseHandle(thread);
            }
        }
        std::cout << "{\"result\":\"" << label(result) << "\",\"slot\":" << slot
            << ",\"kind\":" << d.kind << ",\"occurrence\":" << d.occurrence << ",\"token\":" << d.token
            << ",\"encode_serial\":" << d.encode_serial << ",\"pts\":" << d.pts
            << ",\"duration\":" << d.nominal_duration << ",\"retained\":" << d.retained_occurrence << "}" << std::endl;
    }
    return 0;
}
