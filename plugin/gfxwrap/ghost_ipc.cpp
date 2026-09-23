/* All IPC/files/model preparation live on this worker, never the renderer. */
#include "ghost_ipc.h"
#include "ghost_assets.h"
#include "ghost_rom.h"
#include "ghost_stream.h"
#include "ghost_playback.h"
#include <windows.h>
#include <atomic>
#include <cmath>
#include <cstring>
#include <cwchar>
#include <limits>
#include <string>
#include <vector>
#include <utility>

namespace {
std::atomic<uint32_t> opened{0}, lifetime{0};
std::atomic<uintptr_t> rom_header{0};
std::atomic<bool> running{false};
std::atomic<HANDLE> lifecycle_signal{nullptr};
std::atomic<uint64_t> displayed_revision{0};
std::atomic<const ghost_ipc::AudioHooks *> audio_hooks{nullptr};
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
bool same_rom(const gipc_request &a, const gipc_request &b) {
    return a.rom_generation == b.rom_generation
        && !std::memcmp(a.rom_path, b.rom_path, sizeof(a.rom_path));
}
bool same_command(gipc_request a, gipc_request b) {
    a.heartbeat = b.heartbeat = 0;
    return !std::memcmp(&a, &b, sizeof(a));
}
// Everything except the displayed set: an applied command with the same
// controls may keep drawing its clips while a changed set loads.
bool same_controls(gipc_request a, gipc_request b) {
    a.heartbeat = b.heartbeat = 0; a.revision = b.revision = 0;
    a.clip_count = b.clip_count = 0;
    std::memset(a.clips, 0, sizeof(a.clips)); std::memset(b.clips, 0, sizeof(b.clips));
    return !std::memcmp(&a, &b, sizeof(a));
}
bool valid_set(const gipc_request &r) {
    if (r.clip_count > GIPC_GHOSTS || r.reserved1 || (r.play_enabled && !r.clip_count)
            || r.shadow_enabled > 1 || r.reserved2 || r.reserved3
            || !(r.volume >= 0 && r.volume <= 1)) return false;
    for (uint32_t i = 0; i < r.clip_count; ++i) {
        const auto &clip = r.clips[i];
        if (clip.hue_degrees > 359 || clip.reserved) return false;
        if (!r.play_enabled) continue;
        uint8_t any = 0; for (auto byte : clip.clip_id) any |= byte;
        if (!any) return false;
        for (uint32_t j = 0; j < i; ++j)
            if (!std::memcmp(clip.clip_id, r.clips[j].clip_id, 16)) return false;
    }
    return true;
}
bool same_marker_owner(const go_marker &a,const go_marker &b) {
    // Capture namespaces change on a state load. The tracker owner does not.
    return a.kind==b.kind && a.session_id==b.session_id && a.arm_id==b.arm_id
        && a.global_timer==b.global_timer && a.utc==b.utc;
}
struct RecoveryCache {
    ghost_origin::Recovery timing;
    gipc_request owner{};
    go_marker marker{};
    uint8_t rom_digest[32]{};
    void clear(){timing.clear();owner={};marker={};std::memset(rom_digest,0,sizeof(rom_digest));}
} recovery_cache; // Only the one active IPC worker accesses this bounded cache.
bool read_live_rom(uint32_t generation, std::vector<uint8_t> &out) {
    const uintptr_t address = rom_header.load(std::memory_order_acquire);
    if (!address || !generation || opened.load(std::memory_order_acquire) != generation) return false;
    return ghost_rom::read(reinterpret_cast<const uint8_t*>(address), [=] {
        return opened.load(std::memory_order_acquire) == generation
            && rom_header.load(std::memory_order_acquire) == address;
    }, out, [](const uint8_t* bytes, size_t count) {
        const int result = sg_model_validate_rom(bytes, count);
        return result == SG_OK ? ghost_rom::Validation::valid
            : result == SG_INVALID_ROM ? ghost_rom::Validation::invalid
            : ghost_rom::Validation::unavailable;
    }) == ghost_rom::Result::ready;
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
    bool live = false, awaiting_configuration = false, rom_ready = false;
    std::vector<uint8_t> rom_bytes;
    uint32_t cached_rom_generation = 0;
    // One validated file per listed clip, keyed by id+SHA. Its playback copy
    // survives set changes; only a removed clip is released.
    struct Asset {
        uint8_t id[16]{}, sha256[32]{};
        gclip_header header{};
        std::vector<uint8_t> bytes;
        bool prepared = false;
        const gclip_frame *frames() const {
            return reinterpret_cast<const gclip_frame *>(bytes.data() + sizeof(gclip_header));
        }
    };
    std::vector<Asset> assets;
    gipc_request applied_request{};
    ghost_origin::Ledger origins;
    go_marker resolved_marker{};
    go_origin resolved_origin{};
    bool have_origin = false;
    ghost_origin::Recovery recovery;
    uint8_t cartridge_digest[32]{};
    bool cartridge_verified = false;
    uint64_t qpc_frequency;
    int64_t qpc_anchor;
    double utc_anchor;

    explicit Session(const gipc_status &clock) : qpc_frequency(clock.qpc_frequency),
        qpc_anchor(clock.qpc_anchor), utc_anchor(clock.utc_anchor) {}
    ~Session() { clear_rom(); }

    Asset *find(const gipc_clip &clip) {
        for (auto &asset : assets)
            if (!std::memcmp(asset.id, clip.clip_id, 16) && !std::memcmp(asset.sha256, clip.clip_sha256, 32))
                return &asset;
        return nullptr;
    }
    void forget_prepared() { for (auto &asset : assets) asset.prepared = false; }
    uint32_t prepared_count(const gipc_request &r) {
        uint32_t count = 0;
        for (uint32_t i = 0; i < r.clip_count; ++i) { auto *asset = find(r.clips[i]); count += asset && asset->prepared; }
        return count;
    }
    bool needs_load(const gipc_request &r) {
        if (!rom_ready || !same_rom(r, loaded)) return true;
        for (uint32_t i = 0; i < r.clip_count; ++i) if (!find(r.clips[i])) return true;
        return false;
    }
    // A clip that left the set stops drawing at the next render boundary;
    // the others keep their prepared copies.
    void release_unlisted(const gipc_request &r) {
        for (size_t i = 0; i < assets.size();) {
            bool listed = false;
            for (uint32_t k = 0; k < r.clip_count && !listed; ++k)
                listed = !std::memcmp(assets[i].id, r.clips[k].clip_id, 16)
                    && !std::memcmp(assets[i].sha256, r.clips[k].clip_sha256, 32);
            if (listed) { ++i; continue; }
            play->release_clip(assets[i].id);
            assets.erase(assets.begin() + static_cast<ptrdiff_t>(i));
        }
    }
    void withdraw() { play->withdraw(); applied = 0; }

    bool audio_running = false; // the sound engine holds a copy of rom_bytes
    void clear_rom() {
        if (audio_running) { if (const auto *hooks = audio_hooks.load()) hooks->stop(); audio_running = false; }
        // Do not leave an old full cartridge in allocator-retained storage that
        // a later generation's discovery could mistake for the live image.
        if (!rom_bytes.empty()) SecureZeroMemory(rom_bytes.data(), rom_bytes.size());
        std::vector<uint8_t>().swap(rom_bytes);
        cached_rom_generation = 0; rom_ready = false;
        cartridge_verified = false;
        std::memset(cartridge_digest,0,sizeof(cartridge_digest));
    }

    void restore_cache() {
        if(!cartridge_verified || !recovery_cache.timing.bound())return;
        const auto &old=recovery_cache.owner;
        if(loaded.owner_pid==old.owner_pid && loaded.owner_created_lo==old.owner_created_lo
                && loaded.owner_created_hi==old.owner_created_hi
                && loaded.origin.session_id==recovery_cache.marker.session_id
                && loaded.origin.kind==recovery_cache.marker.kind
                && !std::memcmp(cartridge_digest,recovery_cache.rom_digest,sizeof(cartridge_digest))) {
            recovery=std::move(recovery_cache.timing);recovery.suspend();
            resolved_marker=recovery_cache.marker;have_origin=false;
        }
        recovery_cache.clear();
    }

    void park_recovery() {
        if(cartridge_verified && recovery.bound()) {
            recovery_cache.timing=std::move(recovery);recovery_cache.timing.suspend();
            recovery_cache.owner=loaded;recovery_cache.marker=resolved_marker;
            std::memcpy(recovery_cache.rom_digest,cartridge_digest,sizeof(cartridge_digest));
        }
    }
    void revoke(uint32_t why) {
        if(why==GIPC_NO_ROM)park_recovery();
        else if(previous.token) recovery_cache.clear();
        recovery.clear();
        capture->disable();
        if (live || applied) play->revoke(why);
        live = false; applied = 0; configuration = 0; awaiting_configuration = false;
        forget_prepared();
        origins.clear();
        have_origin = false;
        reason = why; state = why ? GIPC_REFUSED : GIPC_PASSIVE;
        if (why == GIPC_NO_ROM) {
            clear_rom();
        }
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
                (r.revision == accepted.revision && !same_command(r, accepted)))) || r.owner_suspended > 1
                || r.capture_enabled > 1 || r.play_enabled > 1 || r.mode > 1
                || r.nudge < -600 || r.nudge > 600
                || !(r.opacity >= 0 && r.opacity <= 1) || r.stop_at_star > 1
                || r.star_action_count > 8 || r.tint_enabled > 1 || !valid_set(r)
                || r.proximity_enabled > 1 || !std::isfinite(r.proximity_distance)
                || r.proximity_distance < 0 || r.proximity_distance > 400
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
            // A fast RomClosed/RomOpen pair can finish before the worker wakes.
            // Its generation change must retain the same proof as the slow
            // path which retires this Session and starts a replacement worker.
            if(rom && rom!=current_rom)park_recovery();
            play->revoke(GP_WRONG_IDENTITY);
            forget_prepared();
            origins.clear();
            have_origin = false;
            recovery.clear();
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
        if (r.play_enabled) release_unlisted(r);
        if (r.play_enabled && needs_load(r) && (attempted != r.revision || changed)) {
            // Only a new clip is read. Unchanged clips keep drawing under an
            // applied command with the same controls; a new ROM rebuilds all.
            if (!(applied && same_controls(r, applied_request))) withdraw();
            if (!same_rom(r, loaded)) {
                if (rom_ready) { play->revoke(GP_PREPARING); applied = 0; forget_prepared(); rom_ready = false; }
                recovery.clear(); have_origin = false;
            }
            attempted = r.revision; loaded = r;
            state = GIPC_PREPARING; previous = r;
            return true; // caller loads outside the page mutex, then revalidates the lease
        }
        if (r.play_enabled && failed == r.revision) {
            withdraw(); state = GIPC_REFUSED; previous = r; return false;
        }
        reason = GIPC_OK; state = configuration ? GIPC_CAPTURING : GIPC_PREPARING;
        gp_status playback{};
        if (applied && play->status(&playback, sizeof(playback)) == GP_OK
                && (playback.decision == GP_CONTEXT_LOST || playback.model_state == GP_MODEL_EMPTY)) {
            applied = 0; forget_prepared();
        }
        if (!r.play_enabled) {
            if (applied) play->withdraw();
            recovery.clear();have_origin=false;
            applied = 0;
        } else if (!needs_load(r) && configuration) {
            // A suspended owner (the tracker's arm ended) keeps the command
            // applied, marked suspended: the renderer hides it, and a state
            // load it resolves brings it back on the load frame.
            const int model = play->prepare_model(r.model_id, rom_bytes.data(), static_cast<uint32_t>(rom_bytes.size()));
            int clip = GP_OK;
            for (uint32_t i = 0; i < r.clip_count; ++i) {
                auto *asset = find(r.clips[i]);
                if (asset->prepared) continue;
                const int result = play->prepare_clip(&asset->header, asset->frames());
                asset->prepared = result == GP_OK;
                if (result != GP_OK && (clip == GP_OK || result == GP_INVALID)) clip = result;
            }
            // The old command may keep drawing only while it means the same thing.
            if ((model != GP_OK || clip != GP_OK) && applied != r.revision
                    && !(applied && same_controls(r, applied_request))) withdraw();
            if (model == GP_OK && clip == GP_OK) {
                if (applied != r.revision) {
                    gp_request command{};
                    if (r.mode == GP_FOLLOW) {
                        const bool same_owner= same_marker_owner(r.origin,resolved_marker);
                        const bool handoff=r.origin.session_id==resolved_marker.session_id
                            && r.origin.kind==resolved_marker.kind && r.origin.arm_id!=resolved_marker.arm_id
                            && recovery.restored_near(r.origin,qpc_frequency,qpc_anchor,utc_anchor);
                        go_result resolved=GO_MISSING;
                        if(recovery.bound() && (same_owner || handoff)) {
                            if(recovery.current(resolved_origin))resolved=GO_READY;
                        } else {
                            resolved=origins.resolve(r.origin,qpc_frequency,qpc_anchor,utc_anchor,
                                r.layout.clock.near_zero,resolved_origin);
                            if(resolved==GO_READY)recovery.bind(resolved_origin);
                        }
                        if (r.origin.configuration != configuration || resolved != GO_READY) {
                            have_origin = false; withdraw();
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
                    command.clip_count = r.clip_count;
                    for (uint32_t i = 0; i < r.clip_count; ++i) {
                        std::memcpy(command.clips[i].clip_id, r.clips[i].clip_id, 16);
                        command.clips[i].hue_degrees = r.clips[i].hue_degrees;
                    }
                    command.mode = r.mode; command.seek_igt = r.seek_igt;
                    command.nudge_frames = r.nudge; command.opacity = r.opacity;
                    command.star_ends = r.stop_at_star; command.star_action_count = r.star_action_count;
                    std::memcpy(command.star_actions, r.star_actions, sizeof(r.star_actions));
                    command.tint_enabled = r.tint_enabled;
                    command.proximity_enabled = r.proximity_enabled;
                    command.proximity_distance = r.proximity_distance;
                    command.shadow_enabled = r.shadow_enabled; command.volume = r.volume;
                    command.suspended = r.owner_suspended;
                    if (play->configure(&command) != GP_OK) {
                        withdraw(); state = GIPC_REFUSED; reason = GIPC_CLIP; failed = r.revision;
                    } else { applied = r.revision; applied_request = r; state = GIPC_READY; }
                } else state = GIPC_READY;
            } else {
                state = GIPC_PREPARING;
                if (model < 0 && model != GP_BUSY) { state = GIPC_REFUSED; reason = GIPC_MODEL; }
                if (clip < 0 && clip != GP_BUSY) { state = GIPC_REFUSED; reason = GIPC_CLIP; }
                if (model == GP_MODEL_ERROR || model == GP_INVALID || clip == GP_INVALID)
                    failed = r.revision;
            }
        }
        previous = r; return false;
    }
    void load_assets() {
        // A retained command may still draw unchanged clips during this IO.
        if (!rom_ready) load_rom();
        if (!rom_ready) return;
        uint64_t total = 0;
        for (const auto &asset : assets) total += asset.bytes.size();
        for (uint32_t i = 0; i < loaded.clip_count; ++i) {
            const auto &clip = loaded.clips[i];
            if (find(clip)) continue;
            Asset asset;
            std::memcpy(asset.id, clip.clip_id, 16); std::memcpy(asset.sha256, clip.clip_sha256, 32);
            if (!ghost_assets::read(clip.clip_path, GIPC_PATH_CHARS,
                    sizeof(gclip_header) + GCLIP_MAX_RECORDS * sizeof(gclip_frame), asset.bytes)
                    || !ghost_assets::clip(asset.bytes, clip.clip_sha256, clip.clip_id, asset.header)) {
                failed = loaded.revision; reason = GIPC_FILE; state = GIPC_REFUSED; return;
            }
            // Retained files are bounded like their resident playback copies.
            total += asset.bytes.size();
            if (total > GP_CLIP_BUDGET + uint64_t(GIPC_GHOSTS) * sizeof(gclip_header)) {
                failed = loaded.revision; reason = GIPC_CLIP; state = GIPC_REFUSED; return;
            }
            assets.push_back(std::move(asset));
        }
        restore_cache();
    }
    void load_rom() {
        bool rom_loaded = false;
        if (loaded.rom_path[0]) {
            clear_rom();
            rom_loaded = ghost_assets::read(loaded.rom_path, GIPC_PATH_CHARS, 8u << 20, rom_bytes);
        } else if (cached_rom_generation == loaded.rom_generation && !rom_bytes.empty()) {
            rom_loaded = true;
        } else {
            cached_rom_generation = 0;
            rom_loaded = read_live_rom(loaded.rom_generation, rom_bytes);
            if (rom_loaded) cached_rom_generation = loaded.rom_generation;
        }
        if (!rom_loaded) {
            clear_rom(); failed = loaded.revision;
            reason = GIPC_ROM; state = GIPC_REFUSED; return;
        }
        cartridge_verified=ghost_assets::sha256(rom_bytes,cartridge_digest);
        if(!cartridge_verified) {
            clear_rom();failed=loaded.revision;
            reason=GIPC_ROM;state=GIPC_REFUSED;return;
        }
        rom_ready = true;
        // Ghost sound is optional: a refused engine or device leaves drawing intact.
        if (const auto *hooks = audio_hooks.load(); hooks && !audio_running)
            audio_running = hooks->start(rom_bytes.data(), rom_bytes.size());
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
        s.clips_requested = r.play_enabled ? r.clip_count : 0;
        s.clips_prepared = r.play_enabled ? prepared_count(r) : 0;
        s.clips_visible = 0;
        if (play->status(&playback, sizeof(playback)) == GP_OK) {
            s.playback_state = playback.model_state; s.playback_reason = playback.decision;
            s.displayed = state == GIPC_READY && applied == r.revision
                && playback.owner_token == r.token && playback.revision != 0
                && displayed_revision.load(std::memory_order_acquire) == playback.revision;
            if (s.displayed) s.clips_visible = playback.visible;
        }
        if (s.consumed > s.produced || s.produced - s.consumed > GIPC_SLOTS) {
            revoke(GIPC_PROTOCOL); s.consumed = s.produced; return;
        }
        gs_record record{};
        for (uint32_t count = 0; count < GS_SLOTS && capture->read(&record, sizeof(record)) == 1; ++count) {
            if (!live || !configuration || record.configuration != configuration) continue;
            origins.add(record.configuration, record.sample);
            const auto recovered=recovery.observe(record.sample,r.star_actions,r.stop_at_star?r.star_action_count:0);
            if(recovery.bound()) {
                go_origin next{};
                if(recovered==GO_READY && recovery.current(next)) {
                    if(!have_origin || std::memcmp(&next,&resolved_origin,sizeof(next))) {
                        resolved_origin=next;have_origin=true;applied=0;
                    }
                } else if(have_origin) {
                    // The renderer's live recovery refuses this same sample
                    // itself. Keep the applied command: withdrawing or
                    // re-resolving it would also hide the first frame of a
                    // later restore the renderer can already draw.
                    have_origin=false;
                    reason=GIPC_ORIGIN;state=GIPC_PREPARING;
                }
            }
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
void rom(bool open, const uint8_t *header) {
    if (!open && !lifecycle_signal.load()) return;
    if (!lifecycle_signal.load()) {
        HANDLE created = CreateEventW(nullptr, FALSE, FALSE, nullptr), expected = nullptr;
        if (!lifecycle_signal.compare_exchange_strong(expected, created) && created) CloseHandle(created);
    }
    if (!lifecycle_signal.load()) return;
    if (open) {
        const uint32_t old = lifetime.load();
        if (old == UINT32_MAX) return;
        rom_header.store(reinterpret_cast<uintptr_t>(header), std::memory_order_release);
        lifetime.store(old + 1); opened.store(old + 1, std::memory_order_release);
        start_worker();
    } else {
        displayed_revision.store(0, std::memory_order_release);
        opened.store(0, std::memory_order_release);
        rom_header.store(0, std::memory_order_release);
        ghost_stream::api()->disable(); ghost_playback::api()->revoke(GP_WRONG_IDENTITY);
    }
    SetEvent(lifecycle_signal.load());
}
uint32_t rom_generation() { return opened.load(std::memory_order_acquire); }
void presented(uint64_t revision) { displayed_revision.store(revision, std::memory_order_release); }
void set_audio(const AudioHooks *hooks) { audio_hooks.store(hooks, std::memory_order_release); }
}
