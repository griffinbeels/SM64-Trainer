#include "ghost_stream.h"
#include "ghost_root.h"
#include "ghost_particle_capture.h"
#include <atomic>
#include <cstring>
#include <limits>

namespace {
// Slot ownership prevents a C++ data race; seqlock-over-memcpy alone would not.
enum : uint32_t { Empty, Writing, Ready, Reading };
struct Slot { std::atomic<uint32_t> state{Empty}; gs_record record{}; };
Slot slots[GS_SLOTS];
uint32_t write_cursor = 0, read_cursor = 0;
std::atomic_flag control_writer = ATOMIC_FLAG_INIT;
std::atomic_flag consumer = ATOMIC_FLAG_INIT;
std::atomic<uint32_t> config_sequence{0}, configured{0};
std::atomic<uint32_t> config_words[sizeof(gc_layout) / sizeof(uint32_t)];
std::atomic<uint32_t> epoch{0}, configuration{0}, offered{0}, dropped{0}, taken{0}, busy{0};
bool rom_open = false, active = false;
uint32_t observed_config = 0;
uint64_t serial = 0;
gc_reader reader{};
gcc_clock source_clock{};
gc_layout layout{};
gc_sample current{};
gc_sample drawing_sample{};
gc_root_probe root_probe{};
gc_particle_probe particle_probe{};
gc_root_memory root_memory{}; // last executed root, carried only by value identity
ghost::Pass pass;

void __cdecl disable() { configured.store(0, std::memory_order_release); }
int __cdecl configure(const gc_layout *value) {
    if (control_writer.test_and_set(std::memory_order_acquire)) return 0;
    disable();
    const bool valid = value && ghost_capture_validate_layout(value) == GC_OK;
    const uint32_t seq = config_sequence.load(std::memory_order_relaxed);
    if (seq >= std::numeric_limits<uint32_t>::max() - 2) {
        control_writer.clear(std::memory_order_release); return 0;
    }
    config_sequence.store(seq + 1, std::memory_order_seq_cst);
    gc_layout copy{};
    if (valid) copy = *value;
    uint32_t words[sizeof(gc_layout) / sizeof(uint32_t)];
    std::memcpy(words, &copy, sizeof(copy));
    for (unsigned i = 0; i < sizeof(words) / sizeof(words[0]); ++i)
        config_words[i].store(words[i], std::memory_order_seq_cst);
    config_sequence.store(seq + 2, std::memory_order_seq_cst);
    if (valid) configured.store(seq + 2, std::memory_order_release);
    control_writer.clear(std::memory_order_release);
    return valid ? 1 : 0;
}
bool read_config(gc_layout &out, uint32_t &generation) {
    for (unsigned attempt = 0; attempt < 2; ++attempt) {
        const uint32_t wanted = configured.load(std::memory_order_acquire);
        const uint32_t before = config_sequence.load(std::memory_order_seq_cst);
        if (!wanted || before != wanted || (before & 1)) return false;
        uint32_t words[sizeof(gc_layout) / sizeof(uint32_t)];
        for (unsigned i = 0; i < sizeof(words) / sizeof(words[0]); ++i)
            words[i] = config_words[i].load(std::memory_order_seq_cst);
        if (config_sequence.load(std::memory_order_seq_cst) == before
                && configured.load(std::memory_order_acquire) == wanted) {
            std::memcpy(&out, words, sizeof(out)); generation = wanted;
            return true;
        }
    }
    busy.fetch_add(1, std::memory_order_relaxed);
    return false;
}
void new_epoch() {
    const uint32_t old = epoch.load(std::memory_order_relaxed);
    if (old == std::numeric_limits<uint32_t>::max()) { disable(); rom_open = false; return; }
    epoch.store(old + 1, std::memory_order_release);
    ghost_capture_reset(&reader); root_memory = {};
    gcc_reset(&source_clock);
}
void publish(const gs_record &record) {
    offered.fetch_add(1, std::memory_order_relaxed);
    Slot &slot = slots[write_cursor];
    uint32_t expected = Empty;
    if (!slot.state.compare_exchange_strong(expected, Writing, std::memory_order_acquire)) {
        dropped.fetch_add(1, std::memory_order_relaxed); return;
    }
    slot.record = record;
    slot.state.store(Ready, std::memory_order_release);
    write_cursor = (write_cursor + 1) % GS_SLOTS;
}
int __cdecl read(gs_record *out, uint32_t bytes) {
    if (!out || bytes != sizeof(*out)) return -1;
    *out = {};
    if (consumer.test_and_set(std::memory_order_acquire)) return 0;
    Slot &slot = slots[read_cursor];
    uint32_t expected = Ready;
    if (!slot.state.compare_exchange_strong(expected, Reading, std::memory_order_acquire)) {
        consumer.clear(std::memory_order_release); return 0;
    }
    *out = slot.record;
    slot.state.store(Empty, std::memory_order_release);
    read_cursor = (read_cursor + 1) % GS_SLOTS;
    taken.fetch_add(1, std::memory_order_relaxed);
    consumer.clear(std::memory_order_release);
    return 1;
}
gs_stats __cdecl stats() {
    return {sizeof(gs_stats), GS_VERSION, epoch.load(), configuration.load(),
        offered.load(), dropped.load(), taken.load(), busy.load(), configured.load() ? 1u : 0u};
}
const gs_api capture_api = {sizeof(gs_api), GS_VERSION, GS_CAP_POSE | GS_CAP_WORLD_PASS, 0,
    configure, disable, read, stats};
}
namespace ghost_stream {
const gs_api *api() { return &capture_api; }
bool requested() { return rom_open && configured.load(std::memory_order_acquire) != 0; }
void cancel() { active = false; current = {}; root_probe = {}; particle_probe = {}; pass.reset(); }
void rom(bool opened) {
    active = false; current = {}; root_probe = {}; particle_probe = {}; pass.reset(); rom_open = opened; new_epoch();
}
bool begin(const uint8_t *ram, uint32_t bytes, uint32_t generation, int64_t qpc) {
    active = false; current = {}; root_probe = {}; pass.reset();
    uint32_t revision = 0;
    if (!rom_open || !generation || !read_config(layout, revision)) return false;
    if (revision != observed_config) {
        new_epoch(); observed_config = revision; configuration.store(revision, std::memory_order_release);
        if (!gcc_init(&source_clock, &layout.clock)) { disable(); return false; }
    }
    if (!rom_open || serial == std::numeric_limits<uint64_t>::max()) { disable(); return false; }
    ++serial;
    gc_frame frame{ram,bytes,epoch.load(std::memory_order_relaxed),serial,serial,qpc};
    ghost_capture_begin(&reader, &layout, &frame, &current, &root_probe);
    ghost_particles_prepare(particle_probe, ram, bytes, layout.mario_object, layout.object_pool);
    gcc_input timing{};
    timing.fields = ((current.valid_fields & GC_VALID_CLOCK) ? GCC_CLOCK : 0)
        | ((current.valid_fields & GC_VALID_ACTION) ? GCC_ACTION : 0)
        | ((current.valid_fields & GC_VALID_WARP) ? GCC_WARP : 0);
    timing.epoch = current.epoch; timing.sequence = current.sequence;
    timing.global_timer = current.global_timer; timing.raw_igt = current.igt;
    timing.level = current.level; timing.area = current.area;
    timing.action = current.live_action; timing.warp_op = current.pending_warp_op;
    timing.warp_timer = current.delayed_warp_timer;
    // Classification precedes the bounded queue. A slow recorder may lose a
    // pose occurrence, but it cannot erase timing evidence seen by the source.
    gcc_step(&source_clock, &timing, &current.clock);
    // A rewind gets an explicit invalid occurrence, then the next occurrence
    // starts a new epoch. Never retry this sample under a newly invented clock.
    if (current.status == GC_RESET_REQUIRED) new_epoch();
    active = true;
    pass.begin(current.epoch, generation, serial, (current.valid_fields & GC_VALID_CAMERA) != 0);
    return true;
}
void projection(const float matrix[16]) { if (active) pass.projection(matrix); }
void triangles(const ghost::RasterTarget &target, uint32_t count, bool zbuffer) {
    if (active) pass.triangles(target,count,zbuffer);
}
bool geometry(bool was_zbuffer, bool is_zbuffer) {
    return active && pass.geometry(was_zbuffer,is_zbuffer);
}
void color_target_changed() { if (active) pass.color_target_changed(); }
void display_list(uint32_t pc, uint32_t dl, uint32_t physical_dl, uint32_t depth, const float *matrix) {
    if (active) ghost_root_display_list(root_probe,pc,dl,physical_dl,depth,matrix);
    if (active) ghost_particles_display_list(particle_probe,dl,matrix);
}
void end(bool completed) {
    if (!active) return;
    const bool valid = completed && configured.load(std::memory_order_acquire) == observed_config;
    pass.end(valid);
    ghost_root_finish(root_probe,valid,current,&root_memory);
    ghost_particles_finish(particle_probe,valid,current);
    gs_record out{};
    out.bytes = sizeof(out); out.version = GS_VERSION; out.configuration = observed_config;
    out.sample = current; out.world.status = static_cast<uint32_t>(pass.status());
    if (const auto *world = pass.candidate()) {
        const auto &t = world->target;
        out.world.context_generation = world->context_generation;
        out.world.triangles = world->triangles;
        out.world.color = t.color; out.world.depth = t.depth;
        out.world.width = t.width; out.world.height = t.height;
        out.world.samples = t.samples; out.world.depth_mode = t.depth_mode;
        std::memcpy(out.world.viewport,t.viewport,sizeof(t.viewport));
        std::memcpy(out.world.depth_scale,t.depth_scale,sizeof(t.depth_scale));
        std::memcpy(out.world.screen_scale,t.screen_scale,sizeof(t.screen_scale));
        std::memcpy(out.world.projection,world->projection,sizeof(world->projection));
    }
    publish(out); active = false;
}
const gc_sample *sample() {
    if (!active) return nullptr;
    drawing_sample=current;
    ghost_root_candidate(root_probe,drawing_sample);
    return &drawing_sample;
}
const ghost::WorldPass *candidate() {
    return requested() && configured.load(std::memory_order_acquire) == observed_config
        ? pass.candidate() : nullptr;
}
}
