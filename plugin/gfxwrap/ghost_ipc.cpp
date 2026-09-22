/* All IPC/files/model preparation live on this worker, never the renderer. */
#include "ghost_ipc.h"
#include "ghost_assets.h"
#include "ghost_stream.h"
#include "ghost_playback.h"
#include "practice_rom.h"
#include <windows.h>
#include <atomic>
#include <cmath>
#include <cstring>
#include <cwchar>
#include <limits>
#include <string>
#include <vector>

namespace {
std::atomic<uint32_t> opened{0}, lifetime{0};
std::atomic<uintptr_t> rom_image{0};
std::atomic<bool> running{false};
std::atomic<HANDLE> lifecycle_signal{nullptr};
std::atomic<uint64_t> displayed_revision{0};
uint64_t playback_revision = 0; // one worker at a time, never reset on reconnect

struct Handle {
    HANDLE value = nullptr;
    ~Handle() { if (value && value != INVALID_HANDLE_VALUE) CloseHandle(value); }
};
struct Mapping {
    gipc_page *page = nullptr;
    ~Mapping() { if (page) UnmapViewOfFile(page); }
};
struct Lock {
    HANDLE mutex; bool held;
    explicit Lock(HANDLE handle) : mutex(handle), held(false) {
        const DWORD result = WaitForSingleObject(handle, 0);
        held = result == WAIT_OBJECT_0 || result == WAIT_ABANDONED;
    }
    ~Lock() { if (held) ReleaseMutex(mutex); }
};
HANDLE owner_handle(const gipc_request &r) {
    HANDLE result = OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, FALSE, r.owner_pid);
    FILETIME birth{}, unused[3]{};
    if (!result) return nullptr;
    if (!GetProcessTimes(result, &birth, unused, unused+1, unused+2)
            || birth.dwLowDateTime != r.owner_created_lo || birth.dwHighDateTime != r.owner_created_hi) {
        CloseHandle(result); return nullptr;
    }
    return result;
}
bool same_owner(const gipc_request &a, const gipc_request &b) {
    return a.token == b.token && a.owner_pid == b.owner_pid
        && a.owner_created_lo == b.owner_created_lo && a.owner_created_hi == b.owner_created_hi
        && a.generation == b.generation;
}
bool same_assets(const gipc_request &a, const gipc_request &b) {
    return a.model_id == b.model_id && a.rom_generation == b.rom_generation
        && !std::memcmp(a.clip_id, b.clip_id, 16)
        && !std::memcmp(a.clip_sha256, b.clip_sha256, 32)
        && !std::memcmp(a.rom_path, b.rom_path, sizeof(a.rom_path))
        && !std::memcmp(a.clip_path, b.clip_path, sizeof(a.clip_path));
}
bool same_command(gipc_request a, gipc_request b) {
    a.heartbeat = b.heartbeat = 0;
    return !std::memcmp(&a, &b, sizeof(a));
}
bool read_live_rom(uint32_t generation, std::vector<uint8_t> &out) {
    const uintptr_t address = rom_image.load(std::memory_order_acquire);
    if (!address || !generation || opened.load(std::memory_order_acquire) != generation) return false;
    out.resize(8u << 20);
    SIZE_T got = 0;
    if (!ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<const void *>(address),
            out.data(), out.size(), &got) || got != out.size()
            || opened.load(std::memory_order_acquire) != generation
            || rom_image.load(std::memory_order_acquire) != address
            || !practice_rom(out.data())) { out.clear(); return false; }
    if (out[0] == 0x40) {
        for (size_t at = 0; at < out.size(); at += 4) {
            const uint8_t a = out[at], b = out[at + 1];
            out[at] = out[at + 3]; out[at + 1] = out[at + 2];
            out[at + 2] = b; out[at + 3] = a;
        }
    }
    // The capture layout and stock model adapter currently support US only.
    return out[0x3e] == 'E';
}

struct Session {
    const gs_api *capture = ghost_stream::api();
    const gp_api *play = ghost_playback::api();
    gipc_request previous{}, accepted{}, loaded{};
    Handle owner;
    uint32_t renewed = 0, rom = 0, prior_configuration = 0, configuration = 0;
    uint32_t reason = GIPC_OK, state = GIPC_PASSIVE;
    uint64_t applied = 0, attempted = 0, failed = 0;
    uint32_t last_dropped = 0;
    bool live = false, awaiting_configuration = false, assets_loaded = false, clip_prepared = false;
    std::vector<uint8_t> rom_bytes, clip_bytes;
    gclip_header clip_header{};
    ghost_origin::Ledger origins;
    go_marker resolved_marker{};
    go_origin resolved_origin{};
    bool have_origin = false;
    uint64_t qpc_frequency;
    int64_t qpc_anchor;
    double utc_anchor;

    explicit Session(const gipc_status &clock) : qpc_frequency(clock.qpc_frequency),
        qpc_anchor(clock.qpc_anchor), utc_anchor(clock.utc_anchor) {}

    void revoke(uint32_t why) {
        capture->disable();
        if (live || applied) play->revoke(why);
        live = false; applied = 0; configuration = 0; awaiting_configuration = false;
        clip_prepared = false;
        origins.clear();
        have_origin = false;
        reason = why; state = why ? GIPC_REFUSED : GIPC_PASSIVE;
    }
    bool demand(const gipc_request &r, uint32_t generation) {
        const uint32_t now = GetTickCount(), current_rom = opened.load(std::memory_order_acquire);
        const bool identity = r.token && r.revision && r.owner_pid && r.generation == generation;
        const bool changed = !same_owner(r, previous);
        if (changed) {
            revoke(GIPC_OK);
            if (owner.value) CloseHandle(owner.value);
            owner.value = identity ? owner_handle(r) : nullptr;
            renewed = now; attempted = applied = 0;
            accepted = {}; failed = 0;
        }
        if (r.heartbeat != previous.heartbeat) renewed = now;
        if (identity && ((accepted.revision && (r.revision < accepted.revision ||
                (r.revision == accepted.revision && !same_command(r, accepted)))) || r.reserved
                || r.capture_enabled > 1 || r.play_enabled > 1 || r.mode > 1
                || r.nudge < -600 || r.nudge > 600
                || !(r.opacity >= 0 && r.opacity <= 1) || r.stop_at_star > 1
                || r.star_action_count > 8 || r.tint_enabled > 1 || r.hue_degrees > 359
                || (r.play_enabled && !r.model_id))) {
            revoke(GIPC_PROTOCOL); previous = r; return false;
        }
        const go_marker empty_origin{};
        if (identity && r.play_enabled && (r.mode == GP_FIXED
                ? std::memcmp(&r.origin, &empty_origin, sizeof(r.origin)) != 0
                : (r.origin.kind != GO_STAR && r.origin.kind != GO_SEGMENT)
                    || !r.origin.configuration || !r.origin.epoch || !r.origin.session_id || !r.origin.arm_id
                    || !std::isfinite(r.origin.utc) || r.origin.utc <= 0)) {
            revoke(GIPC_PROTOCOL); previous = r; return false;
        }
        if (identity) accepted = r;
        if (!identity || !r.capture_enabled) { revoke(GIPC_OK); previous = r; return false; }
        if (!owner.value || WaitForSingleObject(owner.value, 0) != WAIT_TIMEOUT) {
            revoke(GIPC_OWNER_GONE); previous = r; return false;
        }
        if (static_cast<uint32_t>(now - renewed) >= GIPC_LEASE_MS) {
            revoke(GIPC_EXPIRED); previous = r; return false;
        }
        if (!current_rom || r.rom_generation != current_rom) {
            revoke(GIPC_NO_ROM); previous = r; return false;
        }
        if (!live || rom != current_rom || std::memcmp(&r.layout, &previous.layout, sizeof(r.layout))) {
            play->revoke(GP_WRONG_IDENTITY);
            clip_prepared = false;
            origins.clear();
            have_origin = false;
            prior_configuration = capture->stats().configuration;
            if (!capture->configure(&r.layout)) {
                revoke(GIPC_LAYOUT); previous = r; return false;
            }
            awaiting_configuration = true; configuration = 0; applied = 0;
            rom = current_rom; live = true;
        }
        const gs_stats stats = capture->stats();
        if (awaiting_configuration && stats.configuration != prior_configuration) {
            awaiting_configuration = false; configuration = stats.configuration;
        }
        if (r.play_enabled && (!assets_loaded || !same_assets(r, loaded))) {
            if (attempted != r.revision || changed) {
                play->revoke(GP_PREPARING); applied = 0;
                attempted = r.revision;
                loaded = r; assets_loaded = clip_prepared = false;
                state = GIPC_PREPARING; previous = r;
                return true; // caller loads outside the page mutex, then revalidates the lease
            }
        }
        if (r.play_enabled && failed == r.revision) {
            state = GIPC_REFUSED; previous = r; return false;
        }
        reason = GIPC_OK; state = configuration ? GIPC_CAPTURING : GIPC_PREPARING;
        gp_status playback{};
        if (applied && play->status(&playback, sizeof(playback)) == GP_OK
                && (playback.decision == GP_CONTEXT_LOST || playback.model_state == GP_MODEL_EMPTY)) {
            applied = 0; clip_prepared = false;
        }
        if (!r.play_enabled) {
            if (applied) play->withdraw();
            applied = 0;
        } else if (assets_loaded && configuration) {
            if (applied != r.revision) play->withdraw();
            const int model = play->prepare_model(r.model_id, rom_bytes.data(), static_cast<uint32_t>(rom_bytes.size()));
            const int clip = clip_prepared ? GP_OK : play->prepare_clip(&clip_header,
                reinterpret_cast<const gclip_frame *>(clip_bytes.data() + sizeof(gclip_header)));
            clip_prepared = clip == GP_OK;
            if (model == GP_OK && clip == GP_OK) {
                if (applied != r.revision) {
                    gp_request command{};
                    if (r.mode == GP_FOLLOW) {
                        const bool retained = have_origin
                            && !std::memcmp(&r.origin, &resolved_marker, sizeof(r.origin));
                        const auto resolved = retained ? GO_READY : origins.resolve(r.origin, qpc_frequency,
                            qpc_anchor, utc_anchor, r.layout.clock.near_zero, resolved_origin);
                        if (r.origin.configuration != configuration || resolved != GO_READY) {
                            have_origin = false;
                            state = resolved == GO_MISSING ? GIPC_PREPARING : GIPC_REFUSED;
                            reason = GIPC_ORIGIN; previous = r; return false;
                        }
                        resolved_marker = r.origin; have_origin = true;
                        command.origin = resolved_origin;
                    }
                    command.bytes = sizeof(command); command.version = GP_VERSION;
                    if (playback_revision == UINT64_MAX) { revoke(GIPC_PROTOCOL); previous = r; return false; }
                    command.revision = ++playback_revision; command.enabled = 1;
                    command.owner_token = r.token; command.model_id = r.model_id;
                    command.rom_generation = rom; command.configuration = configuration;
                    std::memcpy(command.clip_id, r.clip_id, 16);
                    command.mode = r.mode; command.seek_igt = r.seek_igt;
                    command.nudge_frames = r.nudge; command.opacity = r.opacity;
                    command.star_ends = r.stop_at_star; command.star_action_count = r.star_action_count;
                    std::memcpy(command.star_actions, r.star_actions, sizeof(r.star_actions));
                    command.tint_enabled = r.tint_enabled; command.hue_degrees = r.hue_degrees;
                    if (play->configure(&command) != GP_OK) {
                        state = GIPC_REFUSED; reason = GIPC_CLIP; failed = r.revision;
                    } else { applied = r.revision; state = GIPC_READY; }
                } else state = GIPC_READY;
            } else {
                state = GIPC_PREPARING;
                if (model < 0 && model != GP_BUSY) { state = GIPC_REFUSED; reason = GIPC_MODEL; }
                if (clip < 0 && clip != GP_BUSY) { state = GIPC_REFUSED; reason = GIPC_CLIP; }
                if (model == GP_MODEL_ERROR || model == GP_INVALID || clip == GP_INVALID)
                    failed = r.revision;
            }
        } else if (r.play_enabled && !assets_loaded && attempted == r.revision) {
            state = GIPC_REFUSED; reason = GIPC_FILE;
        }
        previous = r; return false;
    }
    void load_assets() {
        // Any previously presented ghost was revoked before this potentially slow IO.
        const bool rom_loaded = loaded.rom_path[0]
            ? ghost_assets::read(loaded.rom_path, GIPC_PATH_CHARS, 8u << 20, rom_bytes)
            : read_live_rom(loaded.rom_generation, rom_bytes);
        assets_loaded = rom_loaded
            && ghost_assets::read(loaded.clip_path, GIPC_PATH_CHARS,
                sizeof(gclip_header) + GCLIP_MAX_RECORDS * sizeof(gclip_frame), clip_bytes)
            && ghost_assets::clip(clip_bytes, loaded.clip_sha256, loaded.clip_id, clip_header);
        if (!assets_loaded) { rom_bytes.clear(); clip_bytes.clear(); reason = GIPC_FILE; state = GIPC_REFUSED; }
    }
    void publish(gipc_page &page, const gipc_request &r) {
        auto &s = page.status;
        const gs_stats stats = capture->stats();
        s.dropped += static_cast<uint32_t>(stats.dropped - last_dropped);
        last_dropped = stats.dropped;
        if (s.ack_token != r.token || s.rom_generation != opened.load()
                || s.configuration != configuration) s.consumed = s.produced;
        s.rom_generation = opened.load(std::memory_order_acquire); s.rom_open = s.rom_generation != 0;
        s.configuration = configuration; s.epoch = stats.epoch;
        s.state = state; s.reason = reason; s.ack_token = r.token; s.ack_revision = r.revision;
        gp_status playback{};
        s.displayed = 0;
        if (play->status(&playback, sizeof(playback)) == GP_OK) {
            s.playback_state = playback.model_state; s.playback_reason = playback.decision;
            s.displayed = state == GIPC_READY && applied == r.revision
                && playback.owner_token == r.token && playback.revision != 0
                && displayed_revision.load(std::memory_order_acquire) == playback.revision;
        }
        if (s.consumed > s.produced || s.produced - s.consumed > GIPC_SLOTS) {
            revoke(GIPC_PROTOCOL); s.consumed = s.produced; return;
        }
        gs_record record{};
        for (uint32_t count = 0; count < GS_SLOTS && capture->read(&record, sizeof(record)) == 1; ++count) {
            if (!live || !configuration || record.configuration != configuration) continue;
            origins.add(record.configuration, record.sample);
            if (have_origin && (record.sample.epoch != resolved_origin.epoch
                    || !(record.sample.clock.valid & GCC_ELAPSED_VALID)
                    || record.sample.clock.continuity_id != resolved_origin.continuity)) have_origin = false;
            if (s.produced == UINT64_MAX) { revoke(GIPC_PROTOCOL); return; }
            if (s.produced - s.consumed == GIPC_SLOTS) { ++s.dropped; continue; }
            gclip_frame &out = page.records[s.produced % GIPC_SLOTS];
            out = {sizeof(out), GCLIP_VERSION, record.configuration, record.world.status, record.sample};
            ++s.produced;
        }
    }
};

void start_worker();
DWORD WINAPI worker(void *argument) {
    HMODULE module = static_cast<HMODULE>(argument);
    const uint32_t starting_rom = opened.load(std::memory_order_acquire);
    try {
        wchar_t name[160]{};
        swprintf_s(name, L"%s%lu", GIPC_PREFIX, GetCurrentProcessId());
        const std::wstring base(name);
        Handle producer{CreateMutexW(nullptr, FALSE, (base + L"_producer").c_str())};
        Lock exclusive(producer.value);
        if (producer.value && exclusive.held) {
            Handle mapping{CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0,
                                             sizeof(gipc_page), base.c_str())};
            Mapping view{mapping.value ? static_cast<gipc_page *>(MapViewOfFile(mapping.value,
                FILE_MAP_ALL_ACCESS, 0, 0, sizeof(gipc_page))) : nullptr};
            Handle mutex{CreateMutexW(nullptr, FALSE, (base + L"_mutex").c_str())};
            Handle wake{CreateEventW(nullptr, FALSE, FALSE, (base + L"_wake").c_str())};
            if (view.page && mutex.value && wake.value) {
                uint32_t generation = 0;
                gipc_status source_clock{};
                while (!generation && opened.load()) {
                    Lock guard(mutex.value);
                    if (guard.held) {
                        generation = view.page->status.generation + 1;
                        if (!generation) break;
                        gipc_status initial{};
                        std::memcpy(initial.magic, GIPC_MAGIC, 8);
                        initial.version = GIPC_VERSION; initial.bytes = sizeof(gipc_page);
                        initial.producer_pid = GetCurrentProcessId(); initial.generation = generation;
                        initial.record_bytes = sizeof(gclip_frame); initial.capabilities = GS_CAP_POSE | GS_CAP_WORLD_PASS;
                        initial.state = GIPC_PASSIVE;
                        FILETIME birth{}, unused[3]{}, utc{};
                        GetProcessTimes(GetCurrentProcess(), &birth, unused, unused+1, unused+2);
                        initial.producer_created_lo = birth.dwLowDateTime; initial.producer_created_hi = birth.dwHighDateTime;
                        LARGE_INTEGER frequency{}, before{}, after{};
                        QueryPerformanceFrequency(&frequency); QueryPerformanceCounter(&before);
                        GetSystemTimePreciseAsFileTime(&utc); QueryPerformanceCounter(&after);
                        initial.qpc_frequency = frequency.QuadPart;
                        initial.qpc_anchor = before.QuadPart + (after.QuadPart - before.QuadPart) / 2;
                        const uint64_t ticks = (static_cast<uint64_t>(utc.dwHighDateTime) << 32) | utc.dwLowDateTime;
                        initial.utc_anchor = static_cast<double>(ticks - 116444736000000000ull) / 10000000.0;
                        source_clock = initial;
                        view.page->status = initial;
                    } else WaitForSingleObject(lifecycle_signal.load(), 16);
                }
                Session session(source_clock);
                while (generation) {
                    gipc_request request{};
                    bool received = false, published = false;
                    { Lock guard(mutex.value); if (guard.held) { request = view.page->request; received = true; } }
                    if (received) {
                        const bool load = session.demand(request, generation);
                        session.play->collect();
                        { Lock guard(mutex.value);
                            if (guard.held && same_command(request, view.page->request)) {
                                session.publish(*view.page, request);
                                published = true;
                            }
                        }
                        if (load) { session.load_assets(); continue; }
                    }
                    if (!opened.load(std::memory_order_acquire)) {
                        session.revoke(GIPC_NO_ROM); session.play->collect();
                        if (session.play->release_worker() == GP_OK) break;
                    }
                    HANDLE events[2] = {wake.value, lifecycle_signal.load()};
                    // Revocation can make the session idle before a reader
                    // releases the page. Retry its status before sleeping forever.
                    WaitForMultipleObjects(2, events, FALSE,
                        !received || !published || session.live || !opened.load() ? 16 : INFINITE);
                }
                // Publish closure before releasing producer ownership. Clients
                // cannot mistake retained named storage for a live producer.
                for (;;) {
                    { Lock guard(mutex.value);
                        if (guard.held) {
                            view.page->status.state = GIPC_CLOSED;
                            view.page->status.rom_open = 0;
                            view.page->status.rom_generation = 0;
                            break;
                        }
                    }
                    WaitForSingleObject(lifecycle_signal.load(), 16);
                }
            }
        }
    } catch (...) {
        ghost_stream::api()->disable(); ghost_playback::api()->revoke(GIPC_PROTOCOL);
        // Retain the DLL and worker if render-owned storage has not retired.
        while (ghost_playback::api()->release_worker() != GP_OK) {
            ghost_playback::api()->collect(); WaitForSingleObject(lifecycle_signal.load(), 100);
        }
    }
    running.store(false, std::memory_order_release);
    const uint32_t next_rom = opened.load(std::memory_order_acquire);
    if (next_rom && next_rom != starting_rom) start_worker();
    FreeLibraryAndExitThread(module, 0);
    return 0;
}
void start_worker() {
    bool expected = false;
    if (!running.compare_exchange_strong(expected, true)) return;
    HMODULE module = nullptr;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS,
            reinterpret_cast<LPCWSTR>(&worker), &module)) { running.store(false); return; }
    HANDLE thread = CreateThread(nullptr, 0, worker, module, 0, nullptr);
    if (thread) CloseHandle(thread);
    else { running.store(false); FreeLibrary(module); }
}
}
namespace ghost_ipc {
void rom(bool open, const uint8_t *image) {
    if (!open && !lifecycle_signal.load()) return;
    if (!lifecycle_signal.load()) {
        HANDLE created = CreateEventW(nullptr, FALSE, FALSE, nullptr), expected = nullptr;
        if (!lifecycle_signal.compare_exchange_strong(expected, created) && created) CloseHandle(created);
    }
    if (!lifecycle_signal.load()) return;
    if (open) {
        const uint32_t old = lifetime.load();
        if (old == UINT32_MAX) return;
        rom_image.store(reinterpret_cast<uintptr_t>(image), std::memory_order_release);
        lifetime.store(old + 1); opened.store(old + 1, std::memory_order_release);
        start_worker();
    } else {
        displayed_revision.store(0, std::memory_order_release);
        opened.store(0, std::memory_order_release);
        rom_image.store(0, std::memory_order_release);
        ghost_stream::api()->disable(); ghost_playback::api()->revoke(GP_WRONG_IDENTITY);
    }
    SetEvent(lifecycle_signal.load());
}
uint32_t rom_generation() { return opened.load(std::memory_order_acquire); }
void presented(uint64_t revision) { displayed_revision.store(revision, std::memory_order_release); }
}
