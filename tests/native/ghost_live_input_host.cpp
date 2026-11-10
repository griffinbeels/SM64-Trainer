/* Actual native input/control/custody witness. No model, GPU, SDK or service. */
#include "ghost_live_input.h"
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <thread>
#include <vector>

namespace ghost_live {
// Only the worker-owned numeric boundaries are seeded. Public methods perform
// the actual failure transition and atomic renderer withdrawal afterwards.
struct InputWitness {
    static void revision_ceiling(Input &value) { value.revision_ = Input::revision_limit - 1; }
    static void rejected_ceiling(Input &value) { value.stats_.rejected = UINT64_MAX; }
    static bool aligned(const Input &value) {
        return uintptr_t(&value.gates_[0]) % 8 == 0 && uintptr_t(&value.gates_[1]) % 8 == 0
            && value.gates_[0].is_lock_free();
    }
};
}
namespace {
using ghost_live::InputResult;
unsigned checks = 0;
void require(bool value, const char *message) {
    ++checks;
    if (!value) { std::fprintf(stderr, "FAILED %u: %s\n", checks, message); std::exit(1); }
}
void nonce(uint8_t (&out)[16], unsigned value) {
    std::memset(out, 0, sizeof(out)); out[15] = 17;
    std::memcpy(out, &value, sizeof(value));
}
const char *name(InputResult value) {
    switch (value) {
    case InputResult::ok: return "ok";
    case InputResult::bad_seat: return "bad_seat";
    case InputResult::bad_incarnation: return "bad_incarnation";
    case InputResult::active_member: return "active_member";
    case InputResult::retired_incarnation: return "retired_incarnation";
    case InputResult::capacity: return "capacity";
    case InputResult::not_authorized: return "not_authorized";
    case InputResult::bad_packet: return "bad_packet";
    case InputResult::bad_time: return "bad_time";
    case InputResult::old_source: return "old_source";
    case InputResult::source_changed: return "source_changed";
    case InputResult::not_newer: return "not_newer";
    case InputResult::exhausted: return "exhausted";
    }
    return "unknown";
}
std::vector<uint8_t> read_file(const char *path) {
    std::ifstream file(path, std::ios::binary | std::ios::ate);
    require(bool(file), "open disposable input fixture");
    const auto length = file.tellg();
    require(length >= 0 && length <= std::streamoff(4u << 20), "bounded disposable input fixture");
    std::vector<uint8_t> bytes(static_cast<size_t>(length));
    file.seekg(0);
    if (!bytes.empty()) file.read(reinterpret_cast<char *>(bytes.data()), std::streamsize(bytes.size()));
    require(bool(file), "read disposable input fixture"); return bytes;
}
template<class T> T scalar(const std::vector<uint8_t> &bytes, size_t at) {
    T value{}; require(at + sizeof(value) <= bytes.size(), "bounded test ledger scalar");
    std::memcpy(&value, bytes.data() + at, sizeof(value)); return value;
}
void emit(unsigned op, const char *result, const ghost_live::Input &input,
          const ghost_live::Snapshot &snapshot, uint64_t now) {
    const auto stats = input.stats(); // script mode is serial, including this telemetry read
    std::printf("{\"op\":%u,\"result\":\"%s\",\"revision\":%llu,\"replaced\":%llu,\"dropped\":%llu,\"seats\":[",
                op, result, static_cast<unsigned long long>(snapshot.revision),
                static_cast<unsigned long long>(stats.replaced), static_cast<unsigned long long>(stats.dropped));
    for (unsigned i = 0; i < GLIVE_MAX_PEERS; ++i) {
        const auto &s = snapshot.seats[i];
        std::printf("%s{\"authorized\":%u,\"present\":%u,\"visible\":%u,\"current\":%s,"
                    "\"sequence\":%llu,\"source\":%llu,\"continuity\":%llu,\"elapsed\":%llu,"
                    "\"received\":%llu,\"gate\":%llu}", i ? "," : "", s.authorized, s.present, s.visible,
                    input.current(snapshot, i, now) ? "true" : "false",
                    static_cast<unsigned long long>(s.value.sample.sequence),
                    static_cast<unsigned long long>(s.value.header.source_generation),
                    static_cast<unsigned long long>(s.value.header.continuity),
                    static_cast<unsigned long long>(s.value.header.elapsed_ns),
                    static_cast<unsigned long long>(s.received_ns), static_cast<unsigned long long>(s.gate));
    }
    std::puts("]}");
}
void script(const std::vector<uint8_t> &bytes, uint64_t timeout) {
    ghost_live::Input input(timeout); ghost_live::Snapshot adopted{};
    require(ghost_live::InputWitness::aligned(input), "atomic64 gate alignment");
    size_t at = 0;
    while (at < bytes.size()) {
        const auto op = scalar<uint32_t>(bytes, at), seat = scalar<uint32_t>(bytes, at + 4);
        const auto now = scalar<uint64_t>(bytes, at + 8);
        const auto length = scalar<uint32_t>(bytes, at + 16); at += 20;
        require(length <= 1024 && at + length <= bytes.size(), "bounded ledger payload");
        const auto *data = bytes.data() + at; at += length;
        const char *result = "unknown";
        if (op == 1) { require(length == 16, "control incarnation length"); result = name(input.authorize(seat, data)); }
        else if (op == 2) result = name(input.revoke(seat));
        else if (op == 3) result = name(input.clear(seat));
        else if (op == 4) result = name(input.ingest(seat, data, length, now));
        else if (op == 5) result = input.publish(now) ? "published" : "not_published";
        else if (op == 6) result = input.consume(adopted, now) ? "consumed" : "unchanged";
        else require(false, "known ledger operation");
        emit(op, result, input, adopted, now);
    }
}
glive_packet seed(const std::vector<uint8_t> &bytes) {
    require(bytes.size() == sizeof(glive_packet), "checked test seed size");
    glive_packet packet{}; std::memcpy(&packet, bytes.data(), sizeof(packet)); return packet;
}
void exhaustion(const std::vector<uint8_t> &bytes) {
    const auto packet = seed(bytes);
    for (unsigned counter = 0; counter < 2; ++counter) {
        ghost_live::Input input(100); ghost_live::Snapshot adopted{};
        require(input.authorize(0, packet.header.participant_incarnation) == InputResult::ok, "initial admission");
        require(input.ingest(0, &packet, sizeof(packet), 1) == InputResult::ok, "initial pose");
        require(input.publish(1) && input.consume(adopted, 1) && input.current(adopted, 0, 1), "initial current snapshot");
        if (!counter) {
            ghost_live::InputWitness::revision_ceiling(input);
            require(input.clear(0) == InputResult::exhausted, "revision exhaustion uses real clear");
        } else {
            ghost_live::InputWitness::rejected_ceiling(input);
            require(input.revoke(GLIVE_MAX_PEERS) == InputResult::exhausted, "diagnostic saturation uses real rejection");
        }
        require(input.exhausted() && !input.current(adopted, 0, 1), "exhaustion withdraws before publication");
        require(input.consume(adopted, 1) && !adopted.seats[0].present, "renderer drops old pose without new payload");
        require(input.publish(1) && input.consume(adopted, 1), "terminal snapshot remains publishable");
        require(!adopted.seats[0].authorized && !adopted.seats[0].visible, "terminal snapshot is empty");
        require(input.authorize(0, packet.header.participant_incarnation) == InputResult::exhausted, "no wrapped revival");
    }
    std::printf("{\"exhaustion\":true,\"checks\":%u}\n", checks);
}
void history_capacity() {
    ghost_live::Input input(100);
    for (unsigned i = 0; i < ghost_live::Input::nonce_history_capacity; ++i) {
        uint8_t id[16]{}; nonce(id, i);
        require(input.authorize(0, id) == InputResult::ok, "distinct control admission within bound");
        require(input.revoke(0) == InputResult::ok, "real revocation retains tombstone");
    }
    uint8_t fresh[16]{}, old[16]{}; nonce(fresh, ghost_live::Input::nonce_history_capacity); nonce(old, 0);
    require(input.authorize(0, fresh) == InputResult::capacity, "history never evicts to grant admission");
    require(input.authorize(1, old) == InputResult::retired_incarnation, "old nonce remains retired across seats");
    std::printf("{\"history_capacity\":%u,\"checks\":%u}\n", ghost_live::Input::nonce_history_capacity, checks);
}
void scoped_admission(const std::vector<uint8_t> &bytes) {
    auto packet=seed(bytes);
    ghost_live::Input input(100);ghost_live::Snapshot first{},second{};
    uint8_t room_a[16]{1},room_b[16]{2},invalid_room[16]{};
    const auto *source=packet.header.participant_incarnation;
    require(input.authorize(0,source,invalid_room)==InputResult::bad_incarnation,"explicit receiver scope must be nonzero");
    require(input.authorize(0,source,room_a)==InputResult::ok,"first receiver room admits source");
    require(input.ingest(0,&packet,sizeof(packet),1)==InputResult::ok && input.publish(1),"first receiver source value");
    input.consume(first,1);
    require(input.current(first,0,1),"first room owned snapshot current");
    require(input.authorize(0,source,room_b)==InputResult::active_member,"scope cannot replace an active membership silently");
    require(input.clear(0)==InputResult::ok && !input.current(first,0,1),"same-scope clear withdraws old snapshot");
    require(input.authorize(0,source,room_a)==InputResult::ok && input.ingest(0,&packet,sizeof(packet),2)==InputResult::not_newer,
            "same-scope active identity and source high-water marks survive clear");
    require(input.revoke(0)==InputResult::ok && input.authorize(0,source,room_b)==InputResult::ok,
            "new receiver room may admit a surviving friend's unchanged source identity");
    require(input.ingest(0,&packet,sizeof(packet),2)==InputResult::ok && input.publish(2),
            "new scoped admission accepts original unchanged source packet bytes");
    input.consume(second,2);
    require(input.current(second,0,2) && !input.current(first,0,2)
            && second.seats[0].gate>first.seats[0].gate,"global gates and old snapshots never reset across room scopes");
    require(!std::memcmp(second.seats[0].value.header.participant_incarnation,source,16),"receiver scope never rewrites the source nonce");
    require(input.revoke(0)==InputResult::ok && input.authorize(1,source,room_a)==InputResult::retired_incarnation
            && input.authorize(0,source,room_b)==InputResult::retired_incarnation,"each scoped source tombstone survives revocation across seats");
    ghost_live::Input bounded(100);
    for(unsigned i=0;i<ghost_live::Input::nonce_history_capacity;++i) {
        uint8_t room[16]{};nonce(room,i);
        require(bounded.authorize(0,source,room)==InputResult::ok && bounded.revoke(0)==InputResult::ok,
                "same source in distinct trusted receiver scopes consumes bounded admission history");
    }
    uint8_t fresh[16]{},old[16]{};nonce(fresh,ghost_live::Input::nonce_history_capacity);nonce(old,0);
    require(bounded.authorize(0,source,fresh)==InputResult::capacity
            && bounded.authorize(0,source,old)==InputResult::retired_incarnation,"scoped history never evicts a retired admission");
    std::printf("{\"scoped_admission\":true,\"history_capacity\":%u,\"checks\":%u}\n",ghost_live::Input::nonce_history_capacity,checks);
}
void stress(const std::vector<uint8_t> &bytes) {
    constexpr unsigned iterations = 10000;
    auto packet = seed(bytes);
    ghost_live::Input input(UINT64_MAX); ghost_live::Snapshot adopted{};
    uint8_t ids[GLIVE_MAX_PEERS][16]{};
    for (unsigned seat = 0; seat < GLIVE_MAX_PEERS; ++seat) {
        nonce(ids[seat], seat);
        require(input.authorize(seat, ids[seat]) == InputResult::ok, "three stress admissions");
    }
    gc_sample sample{}; std::memcpy(&sample, packet.core, sizeof(packet.core));
    std::atomic<bool> done{false}, failed{false};
    std::thread producer([&] {
        for (unsigned i = 1; i <= iterations; ++i) {
            sample.sequence = i; sample.dlist_serial = i + 100; sample.qpc = i + 1000;
            sample.global_timer = i + 100; sample.area_update_counter = sample.igt = uint16_t(i);
            sample.pose.anim_timer = uint16_t(i); sample.pose.position[0] = float(i);
            sample.clock.raw_igt = i; sample.clock.elapsed_frames = i;
            packet.header.elapsed_ns = i;
            std::memcpy(packet.core, &sample, sizeof(packet.core));
            for (unsigned seat = 0; seat < GLIVE_MAX_PEERS; ++seat) {
                std::memcpy(packet.header.participant_incarnation, ids[seat], 16);
                if (input.ingest(seat, &packet, sizeof(packet), i) != InputResult::ok) failed.store(true);
            }
            input.publish(i);
        }
        done.store(true, std::memory_order_release);
    });
    uint64_t previous = 0; unsigned consumed = 0;
    do {
        input.consume(adopted, iterations + 1);
        require(adopted.revision >= previous, "older snapshot cannot replace newer adoption"); previous = adopted.revision;
        for (unsigned seat = 0; seat < GLIVE_MAX_PEERS; ++seat) {
            const auto &view = adopted.seats[seat];
            if (!view.present) continue;
            ++consumed;
            require(view.value.sample.sequence == uint64_t(view.value.sample.pose.position[0])
                    && view.value.sample.dlist_serial == view.value.sample.sequence + 100
                    && view.value.sample.qpc == int64_t(view.value.sample.sequence + 1000)
                    && !std::memcmp(view.value.header.participant_incarnation, ids[seat], 16), "owned snapshot is never torn");
        }
        std::this_thread::yield();
    } while (!done.load(std::memory_order_acquire));
    producer.join();
    require(!failed.load(), "producer validates every correlated packet");
    input.consume(adopted, iterations + 1);
    // A final publish after joining removes nondeterminism from an earlier
    // bounded CAS miss; it is still the same owned mailbox, not direct state.
    require(input.publish(iterations + 1), "final owned publication"); input.consume(adopted, iterations + 1);
    for (const auto &view : adopted.seats)
        require(view.present && view.visible && view.value.sample.sequence == iterations, "latest of each seat survives");
    const auto stats = input.stats();
    require(stats.accepted == iterations * GLIVE_MAX_PEERS && stats.rejected == 0
            && stats.published + stats.dropped == iterations + 1, "truthful accepted and handoff counters");
    std::printf("{\"stress\":true,\"accepted\":%llu,\"consumed\":%u,\"replaced\":%llu,\"dropped\":%llu,\"checks\":%u}\n",
                static_cast<unsigned long long>(stats.accepted), consumed,
                static_cast<unsigned long long>(stats.replaced), static_cast<unsigned long long>(stats.dropped), checks);
}
}
int main(int argc, char **argv) {
    require(argc >= 2, "native witness mode");
    if (!std::strcmp(argv[1], "capacity")) { history_capacity(); return 0; }
    require(argc >= 3, "native witness input"); const auto bytes = read_file(argv[2]);
    if (!std::strcmp(argv[1], "script")) {
        require(argc == 4, "local receipt timeout"); script(bytes, std::strtoull(argv[3], nullptr, 10));
    } else if (!std::strcmp(argv[1], "exhaustion")) exhaustion(bytes);
    else if (!std::strcmp(argv[1], "scoped")) scoped_admission(bytes);
    else if (!std::strcmp(argv[1], "stress")) stress(bytes);
    else require(false, "known native witness mode");
}
