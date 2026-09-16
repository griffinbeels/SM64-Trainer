/* Real x86 virtual dispatch on a separate renderer thread. CPU contract host;
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
volatile LONG abi_failure = 0;
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
void *expected_this = nullptr;
DWORD renderer_id;
bool returned = false;
struct Update : Command {
    uint32_t picture;
    bool result;
    Update(uint32_t value, bool answer) : picture(value), result(answer) {}
    __declspec(noinline) bool run() override {
        CHECK(this == expected_this && GetCurrentThreadId() == renderer_id);
        ++calls;
        if (hold_original.load()) {
            SetEvent(entered_event);
            CHECK(WaitForSingleObject(release_event, 5000) == WAIT_OBJECT_0);
        }
        displayed.store(picture);
        return result;
    }
};
// Exercise ABI with independent stack/register sentinels, through the vtable.
__declspec(naked) bool invoke(Command *) {
    __asm {
        push ebp
        mov ebp, esp
        push ebx
        push esi
        push edi
        mov ebx, 11223344h
        mov esi, 55667788h
        mov edi, 1234ABCDh
        mov ecx, [ebp+8]
        mov eax, [ecx]
        call dword ptr [eax]
        cmp ebx, 11223344h
        jne bad_abi
        cmp esi, 55667788h
        jne bad_abi
        cmp edi, 1234ABCDh
        jne bad_abi
        lea edx, [ebp-12]
        cmp esp, edx
        je restore_abi
    bad_abi:
        mov abi_failure, 1
    restore_abi:
        lea esp, [ebp-12]
        pop edi
        pop esi
        pop ebx
        pop ebp
        ret
    }
}
DWORD WINAPI render(void *) {
    renderer_id = GetCurrentThreadId();
    for (;;) {
        CHECK(WaitForSingleObject(work_event, 5000) == WAIT_OBJECT_0);
        if (stopping.load()) return 0;
        returned = invoke(pending);
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
    pending = &command; expected_this = &command;
    SetEvent(work_event);
}
void finish_run(Update &command) {
    CHECK(WaitForSingleObject(done_event, 5000) == WAIT_OBJECT_0);
    CHECK(returned == command.result && !abi_failure);
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
    Update command(n, (n & 1) != 0);
    auto before = calls.load();
    submit(command); finish_run(command); rb_finish(id);
    CHECK(calls.load() == before + 1);
    const auto r = record_for(n, outcome);
    CHECK(rb_release(ticket(r)));
    CHECK(!rb_release(id));
}
int wmain(int argc, wchar_t **argv) {
    if ((argc == 3 || argc == 4) && !std::wcscmp(argv[1], L"validate")) {
        HMODULE module = LoadLibraryExW(argv[2], nullptr, DONT_RESOLVE_DLL_REFERENCES);
        CHECK(module);
        if (argc == 4) {
            auto base = reinterpret_cast<unsigned char *>(module);
            unsigned offset = !std::wcscmp(argv[3], L"constructor") ? 0x88fc
                : !std::wcscmp(argv[3], L"dispatch") ? 0x8907
                : !std::wcscmp(argv[3], L"run") ? 0x9520 : 0x848204;
            DWORD old, ignored; CHECK(VirtualProtect(base+offset, 1, PAGE_READWRITE, &old));
            base[offset] ^= 0xff; // isolated mapped image only; original file untouched
            CHECK(VirtualProtect(base+offset, 1, old, &ignored));
        }
        const int valid = rb_validate_link(module);
        FreeLibrary(module);
        std::printf("descriptor=%d\n", valid);
        return valid ? 0 : 2;
    }
    Update prototype(0, true);
    auto vtable = *reinterpret_cast<void ***>(&prototype);
    auto original = vtable[0];
    CHECK(rb_install_test(vtable, reinterpret_cast<void *>(1), surface) == RB_FOREIGN_SLOT);
    CHECK(vtable[0] == original);
    if (argc == 2 && (!std::wcscmp(argv[1], L"protect-first") || !std::wcscmp(argv[1], L"protect-restore"))) {
        const bool restore = !std::wcscmp(argv[1], L"protect-restore");
        rb_test_fail_protect(restore ? 2 : 1);
        CHECK(rb_install_test(vtable, original, surface) == RB_PROTECTION_FAILED);
        CHECK((*reinterpret_cast<void *volatile *>(vtable) != original) == restore);
        CHECK(!rb_get_stats().observer_ready);
        if (restore) CHECK(rb_install_test(vtable, original, surface) == RB_PROTECTION_FAILED);
        rb_rom_open(); CHECK(!rb_activate());
        renderer_id = GetCurrentThreadId(); expected_this = &prototype;
        CHECK(invoke(&prototype)); // refusal never breaks forwarding
        std::puts("protection-refusal-forwarded"); return 0;
    }
    CHECK(rb_install_test(vtable, original, surface) == RB_INSTALLED);
    CHECK(rb_install_test(vtable, original, surface) == RB_ALREADY_INSTALLED);
    rb_test_set_claim_probe(claim_probe);
    rb_test_set_activation_probe(activation_probe);
    rb_rom_open();
    CHECK(rb_activate());
    work_event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    done_event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    entered_event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    release_event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    CHECK(work_event && done_event && entered_event && release_event);
    HANDLE worker = CreateThread(nullptr, 0, render, nullptr, 0, nullptr); CHECK(worker);
    for (uint32_t n = 1; n < 1001; ++n) perform(n);
    // Held references exhaust the finite pool without preventing original runs.
    rb_ticket borrowed[RB_SLOTS];
    for (uint32_t n = 0; n < RB_SLOTS; ++n) {
        auto value = stamp(2000+n); auto id = rb_stage(&value); CHECK(id.occurrence);
        Update command(2000+n, true); submit(command); finish_run(command); rb_finish(id);
        borrowed[n] = ticket(record_for(2000+n, RB_OBSERVED));
    }
    auto value = stamp(3000);
    CHECK(!rb_stage(&value).occurrence);
    const auto before_full = calls.load();
    Update full(3000, false); submit(full); finish_run(full);
    CHECK(calls.load() == before_full+1 && rb_get_stats().refused_full == 1);
    for (auto id : borrowed) CHECK(rb_release(id));
    // Same slot recycled: a stale release must not free its new borrowed owner.
    auto id = rb_stage(&value); CHECK(id.occurrence);
    Update recycled(3000, true); submit(recycled); finish_run(recycled); rb_finish(id);
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
    Update held(5000, false); submit(held);
    CHECK(WaitForSingleObject(entered_event, 5000) == WAIT_OBJECT_0);
    rb_disarm(); CHECK(rb_activate());
    rb_finish(id); // abnormal early caller return must not recycle RENDERING
    CHECK(!rb_take());
    SetEvent(release_event); finish_run(held); hold_original.store(false);
    r = record_for(5000, RB_RETIRED); CHECK(rb_release(ticket(r)));
    perform(5001);
    fail_surface.store(true); perform(6000, RB_SURFACE_FAILED); fail_surface.store(false);
    // Paused dispatch cannot claim a new occurrence at a recycled slot (ABA).
    value = stamp(6100); id = rb_stage(&value); CHECK(id.occurrence);
    hold_claim.store(true); Update late(6100, true); submit(late);
    CHECK(WaitForSingleObject(entered_event, 5000) == WAIT_OBJECT_0);
    rb_finish(id); r = record_for(6100, RB_NOT_OBSERVED); CHECK(rb_release(ticket(r)));
    for (uint32_t n = 0; n < RB_SLOTS - 1; ++n) {
        auto skipped = stamp(6200+n); auto skip_id = rb_stage(&skipped); CHECK(skip_id.occurrence);
        rb_finish(skip_id); r = record_for(6200+n, RB_NOT_OBSERVED); CHECK(rb_release(ticket(r)));
    }
    value = stamp(6300); auto new_id = rb_stage(&value);
    CHECK(new_id.occurrence && new_id.slot == id.slot);
    SetEvent(release_event); finish_run(late);
    CHECK(!rb_take()); // the new occurrence still awaits its own boundary
    Update fresh(6300, false); submit(fresh); finish_run(fresh); rb_finish(new_id);
    r = record_for(6300, RB_OBSERVED); CHECK(rb_release(ticket(r)));

    // Cancellation inside the observer must trip the independent final check.
    value = stamp(6400); id = rb_stage(&value); CHECK(id.occurrence);
    hold_surface.store(true); Update delayed_surface(6400, true); submit(delayed_surface);
    CHECK(WaitForSingleObject(entered_event, 5000) == WAIT_OBJECT_0);
    rb_disarm(); CHECK(rb_activate()); SetEvent(release_event);
    finish_run(delayed_surface); rb_finish(id);
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
        CHECK(!rb_stage(&value).occurrence);
        Update idle(n, (n & 1) != 0); submit(idle); finish_run(idle);
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
