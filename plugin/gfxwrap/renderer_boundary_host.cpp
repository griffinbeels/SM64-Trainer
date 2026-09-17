/* The boundary's ticket contract on a separate renderer thread. Each command
 * carries its ticket and brackets the original work with rb_source_begin and
 * rb_source_end, as the overlay's UpdateScreen command does. CPU contract host;
 * test waits emulate LINK's own synchronous handoff, never replay production. */
#include "renderer_boundary.h"
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cwchar>

#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "boundary contract line %d: %s\n", __LINE__, #x); std::exit(1); } } while (0)
struct Command { virtual bool run() = 0; };
HANDLE work_event, done_event, entered_event, release_event;
std::atomic<bool> stopping{false}, hold_original{false}, fail_surface{false}, hold_claim{false},
    hold_surface{false}, hold_activation{false};
uint32_t activation_result = 0;
void claim_probe() {
    if (hold_claim.exchange(false)) {
        SetEvent(entered_event);
        CHECK(WaitForSingleObject(release_event, 5000) == WAIT_OBJECT_0);
    }
}
std::atomic<uint32_t> calls{0}, surfaces{0}, displayed{0};
void activation_probe() {
    if (hold_activation.exchange(false)) {
        SetEvent(entered_event);
        CHECK(WaitForSingleObject(release_event, 5000) == WAIT_OBJECT_0);
    }
}
DWORD WINAPI activate_worker(void *) { activation_result = rb_activate(); return 0; }
Command *pending = nullptr;
DWORD renderer_id;
struct Update : Command {
    rb_ticket request;
    uint32_t picture;
    Update(rb_ticket ticket, uint32_t value) : request(ticket), picture(value) {}
    bool run() override {
        CHECK(GetCurrentThreadId() == renderer_id);
        const auto capture = rb_source_begin(request);
        ++calls;
        if (hold_original.load()) {
            SetEvent(entered_event);
            CHECK(WaitForSingleObject(release_event, 5000) == WAIT_OBJECT_0);
        }
        displayed.store(picture);
        rb_source_end(capture);
        return true;
    }
};
DWORD WINAPI render(void *) {
    renderer_id = GetCurrentThreadId();
    for (;;) {
        CHECK(WaitForSingleObject(work_event, 5000) == WAIT_OBJECT_0);
        if (stopping.load()) return 0;
        CHECK(pending->run());
        SetEvent(done_event);
    }
}
int surface(rb_surface *out) {
    CHECK(GetCurrentThreadId() == renderer_id);
    ++surfaces;
    if (hold_surface.exchange(false)) {
        SetEvent(entered_event);
        CHECK(WaitForSingleObject(release_event, 5000) == WAIT_OBJECT_0);
    }
    out->witness = displayed.load();
    out->renderer_thread = renderer_id;
    out->width = 320; out->height = 240;
    return !fail_surface.load();
}
void submit(Update &command) {
    pending = &command;
    SetEvent(work_event);
}
void finish_run() {
    CHECK(WaitForSingleObject(done_event, 5000) == WAIT_OBJECT_0);
}
rb_stamp stamp(uint32_t n) {
    rb_stamp value{};
    value.list_qpc = n * 17; value.vi_origin = 0x1122; value.lists_since = 1;
    value.table_count = 1; value.lengths[0] = 4;
    std::memcpy(value.bytes, &n, sizeof n);
    return value;
}
rb_ticket ticket(const rb_record *record) { return {record->occurrence, record->slot}; }
const rb_record *record_for(uint32_t n, uint32_t outcome) {
    const rb_record *r = rb_take();
    CHECK(r && r->outcome == outcome);
    uint32_t captured = 0; std::memcpy(&captured, r->stamp.bytes, 4);
    CHECK(captured == n && r->stamp.list_qpc == n * 17);
    if (outcome == RB_OBSERVED) CHECK(r->surface.witness == n);
    return r;
}
void perform(uint32_t n, uint32_t outcome = RB_OBSERVED) {
    auto value = stamp(n);
    auto id = rb_stage(&value); CHECK(id.occurrence);
    value = stamp(0xdead); // caller memory may change after frozen offer
    Update command(id, n);
    auto before = calls.load();
    submit(command); finish_run(); rb_finish(id);
    CHECK(calls.load() == before + 1);
    const auto r = record_for(n, outcome);
    CHECK(rb_release(ticket(r)));
    CHECK(!rb_release(id));
}
int wmain(int argc, wchar_t **argv) {
    work_event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    done_event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    entered_event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    release_event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    CHECK(work_event && done_event && entered_event && release_event);
    HANDLE worker = CreateThread(nullptr, 0, render, nullptr, 0, nullptr); CHECK(worker);
    if (argc == 2 && !std::wcscmp(argv[1], L"refused-bind")) {
        rb_rom_open();
        CHECK(rb_bind_source(surface) == RB_LIFECYCLE_REFUSED); // bind belongs before RomOpen
        CHECK(!rb_get_stats().installed && !rb_get_stats().observer_ready);
        CHECK(!rb_activate());
        auto value = stamp(1); const auto refused = rb_stage(&value); CHECK(!refused.occurrence);
        Update command(refused, 1); submit(command); finish_run(); rb_finish(refused);
        CHECK(calls.load() == 1 && surfaces.load() == 0 && !rb_take()); // refusal never breaks forwarding
        stopping.store(true); SetEvent(work_event);
        CHECK(WaitForSingleObject(worker, 5000) == WAIT_OBJECT_0);
        std::puts("refused-bind-forwarded"); return 0;
    }
    CHECK(rb_bind_source(surface) == RB_INSTALLED);
    CHECK(rb_bind_source(surface) == RB_ALREADY_INSTALLED);
    rb_test_set_claim_probe(claim_probe);
    rb_test_set_activation_probe(activation_probe);
    rb_rom_open();
    CHECK(rb_activate());
    for (uint32_t n = 1; n < 1001; ++n) perform(n);
    // Held references exhaust the finite pool without preventing original runs.
    rb_ticket borrowed[RB_SLOTS];
    for (uint32_t n = 0; n < RB_SLOTS; ++n) {
        auto value = stamp(2000+n); auto id = rb_stage(&value); CHECK(id.occurrence);
        Update command(id, 2000+n); submit(command); finish_run(); rb_finish(id);
        borrowed[n] = ticket(record_for(2000+n, RB_OBSERVED));
    }
    auto value = stamp(3000);
    const auto refused = rb_stage(&value); CHECK(!refused.occurrence);
    const auto before_full = calls.load();
    Update full(refused, 3000); submit(full); finish_run();
    CHECK(calls.load() == before_full+1 && rb_get_stats().refused_full == 1);
    for (auto id : borrowed) CHECK(rb_release(id));
    // Same slot recycled: a stale release must not free its new borrowed owner.
    auto id = rb_stage(&value); CHECK(id.occurrence);
    Update recycled(id, 3000); submit(recycled); finish_run(); rb_finish(id);
    auto r = record_for(3000, RB_OBSERVED);
    for (auto old : borrowed) CHECK(!rb_release(old));
    CHECK(rb_release(ticket(r)));
    // No renderer boundary is an explicit failure, not made-up footage.
    value = stamp(4000); id = rb_stage(&value); CHECK(id.occurrence);
    CHECK(!rb_stage(&value).occurrence);
    rb_finish(id); r = record_for(4000, RB_NOT_OBSERVED); CHECK(rb_release(ticket(r)));
    // Disable/reactivate while original work is held: old completion stays old.
    value = stamp(5000); id = rb_stage(&value); CHECK(id.occurrence);
    hold_original.store(true);
    Update held(id, 5000); submit(held);
    CHECK(WaitForSingleObject(entered_event, 5000) == WAIT_OBJECT_0);
    rb_disarm(); CHECK(rb_activate());
    rb_finish(id); // abnormal early caller return must not recycle RENDERING
    CHECK(!rb_take());
    SetEvent(release_event); finish_run(); hold_original.store(false);
    r = record_for(5000, RB_RETIRED); CHECK(rb_release(ticket(r)));
    perform(5001);
    fail_surface.store(true); perform(6000, RB_SURFACE_FAILED); fail_surface.store(false);
    // A paused claim cannot take a new occurrence at a recycled slot (ABA).
    value = stamp(6100); id = rb_stage(&value); CHECK(id.occurrence);
    hold_claim.store(true); Update late(id, 6100); submit(late);
    CHECK(WaitForSingleObject(entered_event, 5000) == WAIT_OBJECT_0);
    rb_finish(id); r = record_for(6100, RB_NOT_OBSERVED); CHECK(rb_release(ticket(r)));
    for (uint32_t n = 0; n < RB_SLOTS - 1; ++n) {
        auto skipped = stamp(6200+n); auto skip_id = rb_stage(&skipped); CHECK(skip_id.occurrence);
        rb_finish(skip_id); r = record_for(6200+n, RB_NOT_OBSERVED); CHECK(rb_release(ticket(r)));
    }
    value = stamp(6300); auto new_id = rb_stage(&value);
    CHECK(new_id.occurrence && new_id.slot == id.slot);
    SetEvent(release_event); finish_run();
    CHECK(!rb_take()); // the new occurrence still awaits its own boundary
    Update fresh(new_id, 6300); submit(fresh); finish_run(); rb_finish(new_id);
    r = record_for(6300, RB_OBSERVED); CHECK(rb_release(ticket(r)));

    // Cancellation inside the observer must trip the independent final check.
    value = stamp(6400); id = rb_stage(&value); CHECK(id.occurrence);
    hold_surface.store(true); Update delayed_surface(id, 6400); submit(delayed_surface);
    CHECK(WaitForSingleObject(entered_event, 5000) == WAIT_OBJECT_0);
    rb_disarm(); CHECK(rb_activate()); SetEvent(release_event);
    finish_run(); rb_finish(id);
    r = record_for(6400, RB_RETIRED); CHECK(rb_release(ticket(r)));
    // Activation publication delayed across close cannot reactivate capture.
    hold_activation.store(true);
    HANDLE activation = CreateThread(nullptr, 0, activate_worker, nullptr, 0, nullptr); CHECK(activation);
    CHECK(WaitForSingleObject(entered_event, 5000) == WAIT_OBJECT_0);
    rb_rom_closed(); SetEvent(release_event);
    CHECK(WaitForSingleObject(activation, 5000) == WAIT_OBJECT_0); CloseHandle(activation);
    CHECK(!activation_result && !rb_stage(&value).occurrence);
    rb_rom_open(); CHECK(!rb_stage(&value).occurrence); CHECK(rb_activate());
    // Server disabled: actual observer is never reached; gameplay still forwards.
    rb_disarm(); const auto before_surface = surfaces.load();
    const auto before_stats = rb_get_stats();
    for (uint32_t n = 0; n < 1000; ++n) {
        const auto idle_id = rb_stage(&value); CHECK(!idle_id.occurrence);
        Update idle(idle_id, n); submit(idle); finish_run();
    }
    CHECK(surfaces.load() == before_surface && rb_get_stats().offered == before_stats.offered);
    rb_rom_closed(); CHECK(!rb_activate());
    rb_rom_open(); CHECK(rb_activate()); perform(7000);
    rb_close(); CHECK(!rb_activate());
    rb_rom_closed(); rb_rom_open(); CHECK(!rb_activate()); // omitted RomClosed sticky refusal
    stopping.store(true); SetEvent(work_event);
    CHECK(WaitForSingleObject(worker, 5000) == WAIT_OBJECT_0);
    CloseHandle(worker); CloseHandle(work_event); CloseHandle(done_event);
    CloseHandle(entered_event); CloseHandle(release_event);
    std::printf("forwarded=%u observed=%u full=%u retired=%u missed=%u\n", calls.load(),
        rb_get_stats().observed, rb_get_stats().refused_full, rb_get_stats().retired,
        rb_get_stats().unobserved);
    return 0;
}
