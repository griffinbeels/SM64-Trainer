/* Real Renderer/Input/codec, transparent pose/model shell. No ROM, GL or SDK. */
#include "ghost_live_render.h"
#include "ghost_scene.h"
#include <windows.h>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <limits>
#include <new>
#include <thread>
#include <vector>

namespace {
std::atomic<bool> measuring{false};
std::atomic<unsigned> allocations{0}, frees{0};
unsigned checks = 0;
void require(bool value, const char *message) {
    ++checks;
    if (!value) { std::fprintf(stderr, "FAILED %u: %s\n", checks, message); std::exit(1); }
}
struct NoHeap {
    unsigned before_allocations = allocations.load(), before_frees = frees.load();
    NoHeap() { measuring.store(true); }
    ~NoHeap() {
        measuring.store(false);
        require(allocations.load() == before_allocations && frees.load() == before_frees,
                "renderer operation does not allocate or free");
    }
};
}
void *operator new(size_t size) {
    if (measuring.load()) ++allocations;
    if (auto *value = std::malloc(size ? size : 1)) return value;
    throw std::bad_alloc();
}
void *operator new[](size_t size) { return ::operator new(size); }
void operator delete(void *value) noexcept {
    if (value && measuring.load()) ++frees;
    std::free(value);
}
void operator delete[](void *value) noexcept { ::operator delete(value); }
void operator delete(void *value, size_t) noexcept { ::operator delete(value); }
void operator delete[](void *value, size_t) noexcept { ::operator delete(value); }

// Deliberately not the real ROM model. R4 supplies that independent witness.
struct sg_model { unsigned marker; };
namespace {
sg_model model_a{71}, model_b{82};
unsigned model_calls = 0, fail_calls = 0, withdraw_on_call = 0, withdraw_seat = 0;
ghost_live::Input *withdraw_input = nullptr;
uint32_t observed_configuration = 0, observed_layout = 0;
int16_t observed_remote_area = 0;
float observed_rebased_x = 0, observed_camera_x = 0;
sg_model *observed_model = nullptr;
}
namespace ghost_pose {
Result evaluate(sg_model *model, const gs_record &record, const gc_sample &local,
                const ghost::WorldPass &world, float opacity, Scratch &scratch,
                ghost_compositor::Pose &out, uint32_t layout) {
    ++model_calls; out = {}; scratch.evaluated = 0;
    observed_model = model; observed_configuration = record.configuration;
    observed_layout = layout; observed_remote_area = record.sample.area;
    observed_camera_x = local.camera.position[0];
    if ((model != &model_a && model != &model_b) || record.bytes != sizeof(record)
            || record.version != GS_VERSION || !record.configuration) return Result::model_failed;
    if (world.epoch != local.epoch || world.list_serial != local.dlist_serial) return Result::wrong_occurrence;
    if (record.sample.pose.position[0] == -9999.f) { ++fail_calls; return Result::model_failed; }
    auto mapped = record.sample;
    if (!ghost_scene::rebase(mapped, local)) return Result::wrong_scene;
    observed_rebased_x = mapped.pose.position[0];
    for (unsigned i = 0; i < 3; ++i) {
        scratch.vertices[i] = {};
        scratch.vertices[i].position[0] = mapped.pose.position[0];
        scratch.vertices[i].position[1] = float(model->marker);
        scratch.vertices[i].position[2] = local.camera.position[0];
    }
    out.vertices = scratch.vertices; out.count = out.body_count = scratch.evaluated = 3;
    out.opacity = opacity; out.view_projection[15] = 1;
    // The reusable live leaf must strip these out of this body-only prototype.
    out.shadow.count = 24; out.particles.count = 1; out.annotation.hud = true;
    if (withdraw_on_call == model_calls && withdraw_input)
        withdraw_input->revoke(withdraw_seat);
    return Result::ok;
}
}
namespace ghost_live {
struct RendererWitness {
    static void boundary_ceiling(Renderer &value) { value.boundary_ = UINT64_MAX; }
    static void evaluation_ceiling(Renderer &value) { value.evaluation_ = UINT64_MAX; }
};
}
namespace {
using ghost_live::DrawToken;
using ghost_live::InputResult;
using ghost_live::ModelBinding;
using ghost_live::Renderer;
bool begin(Renderer &renderer, const ModelBinding &binding, const gc_sample &local, uint64_t now) {
    NoHeap guard; return renderer.begin(binding, local, now);
}
bool evaluate(Renderer &renderer, const gc_sample &local, const ghost::WorldPass &world, uint64_t now,
              ghost_compositor::Pose *out, uint32_t &count, DrawToken &token, float opacity = .8f) {
    NoHeap guard; return renderer.evaluate(local, world, now, opacity, out, count, token);
}
bool current(const Renderer &renderer, const DrawToken &token, uint64_t now) {
    NoHeap guard; return renderer.current(token, now);
}
bool plan(const Renderer &renderer) { NoHeap guard; return renderer.plan(); }
void retire(Renderer &renderer) { NoHeap guard; renderer.retire(); }

struct Context {
    ghost_live::Input input{50};
    Renderer renderer{input};
    glive_packet seed{};
    gc_sample source{}, local{};
    ghost::WorldPass world{};
    ModelBinding binding{&model_a, 900, 72, 91, 7};
    uint8_t ids[GLIVE_MAX_PEERS][16]{};
    uint64_t now = 0, local_serial = 100;
    unsigned next_nonce = 1;
    explicit Context(const glive_packet &packet) : seed(packet) {
        std::memcpy(&source, seed.core, sizeof(seed.core));
    }
    void reset(unsigned peers) {
        now += 100; ++local_serial; binding = {&model_a, 900, 72, 91, 7};
        withdraw_input = nullptr; withdraw_on_call = 0;
        for (unsigned seat = 0; seat < GLIVE_MAX_PEERS; ++seat) {
            require(input.revoke(seat) == InputResult::ok, "retire previous synthetic admission");
            std::memset(ids[seat], 0, 16); ids[seat][15] = 37;
            const unsigned id = next_nonce++; std::memcpy(ids[seat], &id, sizeof(id));
            if (seat < peers) require(input.authorize(seat, ids[seat]) == InputResult::ok, "fresh synthetic seat");
        }
        local = source; local.epoch = 99; local.sequence = local_serial;
        local.dlist_serial = local_serial + 400; local.qpc = int64_t(now);
        local.camera.position[0] = 123; local.camera.focus[0] = 124;
        world = {}; world.epoch = local.epoch; world.list_serial = local.dlist_serial;
        world.context_generation = binding.context_serial; world.triangles = 1;
    }
    void ingest(unsigned seat, gc_sample sample, uint64_t sequence = 1, uint32_t layout = GC_ROM_US) {
        auto packet = seed;
        std::memcpy(packet.header.participant_incarnation, ids[seat], 16);
        packet.header.elapsed_ns = sequence; packet.header.rom_layout = layout;
        sample.sequence = sequence; sample.dlist_serial = sequence + 100;
        sample.qpc = int64_t(sequence + 1000); sample.global_timer = uint32_t(sequence + 100);
        sample.area_update_counter = sample.igt = uint16_t(sequence);
        if (sample.valid_fields & GC_VALID_POSE) sample.pose.anim_timer = uint16_t(sequence);
        sample.clock.raw_igt = sample.igt;
        sample.clock.elapsed_frames = sequence;
        std::memcpy(packet.core, &sample, sizeof(packet.core));
        require(input.ingest(seat, &packet, sizeof(packet), now) == InputResult::ok, "real decoder/input accepts fixture");
    }
    void setup(unsigned peers) {
        reset(peers);
        for (unsigned seat = 0; seat < peers; ++seat) ingest(seat, source);
        require(input.publish(now), "publish synthetic input through actual custody");
        require(begin(renderer, binding, local, now + 1) == (peers != 0), "begin actual reusable renderer");
    }
};
void visible_cases(Context &c) {
    ghost_compositor::Pose out[GLIVE_MAX_PEERS]{}; uint32_t count = 99; DrawToken token{};
    c.setup(3);
    require(plan(c.renderer), "three-seat choice");
    c.local.camera.position[0] += 17; // seal camera, same actual occurrence
    require(evaluate(c.renderer, c.local, c.world, c.now + 2, out, count, token) && count == 3,
            "three independent poses through actual Renderer");
    require(token.included_seats == 7 && current(c.renderer, token, c.now + 2), "three included authority gates");
    require(out[0].vertices != out[1].vertices && out[1].vertices != out[2].vertices, "per-seat scratch is distinct");
    require(observed_camera_x == 140 && observed_model == &model_a, "actual seal camera and borrowed local model");
    require(observed_configuration == c.seed.header.configuration && observed_configuration != c.binding.configuration,
            "remote source configuration stays separate from local binding");
    for (const auto &pose : out)
        require(pose.count == 3 && pose.opacity == .8f && !pose.shadow.count && !pose.particles.count
                && !pose.annotation.hud, "body-only borrowed geometry");
    require(current(c.renderer, token, c.now + 50) && !current(c.renderer, token, c.now + 51),
            "expiry at actual presentation without another publication");
    require(!current(c.renderer, token, c.now + 1), "backward presentation observation refused");
    require(!evaluate(c.renderer, c.local, c.world, c.now + 51, out, count, token) && !count,
            "expired selection cannot be evaluated");
    require(!evaluate(c.renderer, c.local, c.world, c.now + 50, out, count, token) && !count && !token.owner,
            "backward repeat cannot revive an expired selection");

    c.setup(1);
    require(evaluate(c.renderer, c.local, c.world, c.now + 2, out, count, token) && count == 1, "one pose");
    gc_sample exact{};
    require(c.renderer.source(token,0,exact) && exact.sequence==1 && exact.level==c.source.level,
            "annotation source is the exact included drawn snapshot");
    require(!c.renderer.source(token,1,exact) && !exact.bytes,"excluded source copy refuses and clears");
    const auto previous = token;
    require(evaluate(c.renderer, c.local, c.world, c.now + 3, out, count, token), "repeat gets a new evaluation token");
    require(!current(c.renderer, previous, c.now + 3) && current(c.renderer, token, c.now + 3), "repeat retires old token");
    require(!evaluate(c.renderer, c.local, c.world, c.now + 4, nullptr, count, token) && !count && !token.owner,
            "null output refuses with empty token/count");
    require(!current(c.renderer, previous, c.now + 4), "failed repeat cannot retain earlier token");

    c.setup(0); count = 99;
    require(!plan(c.renderer) && !evaluate(c.renderer, c.local, c.world, c.now + 2, out, count, token)
            && !count && !token.owner, "no seat never manufactures a pose");
}
void peer_cases(Context &c) {
    ghost_compositor::Pose out[GLIVE_MAX_PEERS]{}; uint32_t count = 0; DrawToken token{};
    c.reset(3);
    auto hidden = c.source; hidden.pose.hidden = 1;
    auto refused = c.source;
    refused.valid_fields &= ~uint32_t(GC_VALID_POSE); refused.status = GC_UNSUPPORTED_ROOT;
    std::memset(&refused.pose, 0, sizeof(refused.pose)); refused.pose.refusal = GC_REFUSED_HELD;
    c.ingest(0, hidden); c.ingest(1, c.source); c.ingest(2, refused);
    require(c.input.publish(c.now) && begin(c.renderer, c.binding, c.local, c.now + 1), "one visible between hidden/refused seats");
    const auto before = model_calls;
    require(evaluate(c.renderer, c.local, c.world, c.now + 2, out, count, token) && count == 1
            && token.included_seats == 2 && model_calls == before + 1, "hidden/refused never reach model");
    require(c.input.revoke(0) == InputResult::ok && current(c.renderer, token, c.now + 3),
            "excluded seat withdrawal cannot invalidate included geometry");
    require(c.input.clear(1) == InputResult::ok && !current(c.renderer, token, c.now + 3),
            "included clear withdraws before new payload");

    c.setup(3);
    withdraw_input = &c.input; withdraw_seat = 0; withdraw_on_call = model_calls + 3;
    require(evaluate(c.renderer, c.local, c.world, c.now + 2, out, count, token)
            && count == 2 && token.included_seats == 6, "withdrawal during another pose removes only revoked contribution");
    withdraw_input = nullptr; withdraw_on_call = 0;
    require(current(c.renderer, token, c.now + 2), "survivor token names retained drawnSnapshot");
    require(c.input.revoke(2) == InputResult::ok && !current(c.renderer, token, c.now + 3), "later included revoke invalidates candidate");

    c.reset(3);
    auto failing = c.source; failing.pose.position[0] = -9999.f;
    c.ingest(0, c.source); c.ingest(1, failing); c.ingest(2, c.source);
    require(c.input.publish(c.now) && begin(c.renderer, c.binding, c.local, c.now + 1), "model refusal setup");
    const auto failed_before = fail_calls;
    require(evaluate(c.renderer, c.local, c.world, c.now + 2, out, count, token)
            && count == 2 && token.included_seats == 5 && fail_calls == failed_before + 1,
            "one peer's model refusal does not cancel other peers");
}
void scene_cases(Context &c) {
    ghost_compositor::Pose out[GLIVE_MAX_PEERS]{}; uint32_t count = 0; DrawToken token{};
    c.reset(1); c.ingest(0, c.source); c.local.level = 9;
    require(c.input.publish(c.now) && !begin(c.renderer, c.binding, c.local, c.now + 1)
            && !plan(c.renderer), "different level omitted before model work");
    const auto before = model_calls;
    require(!evaluate(c.renderer, c.local, c.world, c.now + 2, out, count, token)
            && model_calls == before && !count, "different level has no geometry");
    c.reset(1);
    auto tunnel = c.source; tunnel.level = 23; tunnel.area = 2;
    tunnel.pose.position[0] = -5192.f; tunnel.pose.position[1] = -1000.f; tunnel.pose.position[2] = 0;
    c.ingest(0, tunnel, 1, GC_ROM_JP); c.local.level = 23; c.local.area = 1;
    require(c.input.publish(c.now) && begin(c.renderer, c.binding, c.local, c.now + 1), "actual DDD connector compatibility");
    require(evaluate(c.renderer, c.local, c.world, c.now + 2, out, count, token) && count == 1
            && observed_layout == GC_ROM_JP && observed_remote_area == 2 && observed_rebased_x == 3000.f,
            "source layout and original scene reach shared pose converter");
}
void local_cases(Context &c) {
    ghost_compositor::Pose out[GLIVE_MAX_PEERS]{}; uint32_t count = 0; DrawToken token{};
    for (unsigned changed = 0; changed < 6; ++changed) {
        c.setup(1);
        require(evaluate(c.renderer, c.local, c.world, c.now + 2, out, count, token), "positive before local identity mutation");
        const auto previous = token; auto local = c.local; auto world = c.world;
        if (changed == 0) ++local.epoch;
        else if (changed == 1) ++local.sequence;
        else if (changed == 2) ++local.dlist_serial;
        else if (changed == 3) ++world.epoch;
        else if (changed == 4) ++world.list_serial;
        else ++world.context_generation;
        require(!evaluate(c.renderer, local, world, c.now + 3, out, count, token)
                && !count && !token.owner && !current(c.renderer, previous, c.now + 3), "mismatched actual local occurrence retires token");
    }
    c.setup(1);
    require(evaluate(c.renderer, c.local, c.world, c.now + 2, out, count, token), "before model/context replacement");
    const auto old = token;
    c.binding = {&model_b, 901, 73, 92, 8}; c.world.context_generation = 8;
    require(begin(c.renderer, c.binding, c.local, c.now + 3) && !current(c.renderer, old, c.now + 3), "new binding retires old draw before borrow reuse");
    require(evaluate(c.renderer, c.local, c.world, c.now + 4, out, count, token) && observed_model == &model_b, "replacement borrowed model only");
    const auto replaced = token;
    retire(c.renderer);
    require(!current(c.renderer, replaced, c.now + 5) && !plan(c.renderer), "retire precedes caller's model release");
    require(!evaluate(c.renderer, c.local, c.world, c.now + 5, out, count, token) && !count && !token.owner, "retired renderer has no borrow");

    for (unsigned field = 0; field < 5; ++field) {
        c.setup(1); require(evaluate(c.renderer, c.local, c.world, c.now + 2, out, count, token), "positive before invalid binding");
        const auto positive = token; auto binding = c.binding;
        if (field == 0) binding.model = nullptr;
        else if (field == 1) binding.model_id = 0;
        else if (field == 2) binding.rom_generation = 0;
        else if (field == 3) binding.configuration = 0;
        else binding.context_serial = 0;
        require(!begin(c.renderer, binding, c.local, c.now + 3) && !current(c.renderer, positive, c.now + 3), "missing local binding facts refuse");
    }
    for (float opacity : {-1.f, 2.f, std::numeric_limits<float>::quiet_NaN()}) {
        c.setup(1); const auto before = model_calls;
        require(!evaluate(c.renderer, c.local, c.world, c.now + 2, out, count, token, opacity)
                && !count && !token.owner && model_calls == before, "invalid opacity refuses without model work");
    }
}
void owner_and_counter_cases(Context &c) {
    ghost_compositor::Pose out[GLIVE_MAX_PEERS]{}; uint32_t count = 0; DrawToken token{};
    c.setup(1);
    require(evaluate(c.renderer, c.local, c.world, c.now + 2, out, count, token), "scope test positive");
    static Context independent(c.seed);
    independent.setup(1);
    DrawToken other_token{};
    require(evaluate(independent.renderer, independent.local, independent.world, independent.now + 2,
                out, count, other_token), "independent renderer test object has its own Input");
    auto scope_alias = token;
    scope_alias.boundary = other_token.boundary; scope_alias.evaluation = other_token.evaluation;
    auto reverse_alias = other_token;
    reverse_alias.boundary = token.boundary; reverse_alias.evaluation = token.evaluation;
    require(!current(independent.renderer, scope_alias, independent.now + 2)
            && !current(c.renderer, reverse_alias, c.now + 2), "matching counters cannot cross renderer scopes");
    auto forged = token; forged.included_seats ^= 2;
    require(!current(c.renderer, forged, c.now + 2), "token mask is exact");
    bool stolen = true;
    std::thread foreign([&] { stolen = c.renderer.begin(c.binding, c.local, c.now + 2)
        || c.renderer.plan() || c.renderer.current(token, c.now + 2); });
    foreign.join();
    require(!stolen && current(c.renderer, token, c.now + 2), "foreign render thread neither steals nor mutates owner");

    static Context boundary_limit(c.seed), evaluation_limit(c.seed);
    boundary_limit.setup(1);
    ghost_live::RendererWitness::boundary_ceiling(boundary_limit.renderer);
    require(!begin(boundary_limit.renderer, boundary_limit.binding, boundary_limit.local, boundary_limit.now + 3)
            && !begin(boundary_limit.renderer, boundary_limit.binding, boundary_limit.local, boundary_limit.now + 4),
            "boundary exhaustion never wraps");
    evaluation_limit.setup(1);
    require(evaluate(evaluation_limit.renderer, evaluation_limit.local, evaluation_limit.world,
                evaluation_limit.now + 2, out, count, other_token), "positive before evaluation exhaustion");
    const auto before = other_token;
    ghost_live::RendererWitness::evaluation_ceiling(evaluation_limit.renderer);
    require(!evaluate(evaluation_limit.renderer, evaluation_limit.local, evaluation_limit.world,
                evaluation_limit.now + 3, out, count, other_token)
            && !count && !other_token.owner
            && !current(evaluation_limit.renderer, before, evaluation_limit.now + 3), "evaluation exhaustion withdraws old geometry");
}
}
int main(int argc, char **argv) {
    require(argc == 2, "usage: live-render-host synthetic-compact-packet");
    std::ifstream file(argv[1], std::ios::binary | std::ios::ate);
    require(bool(file) && file.tellg() == std::streamoff(GLIVE_PACKET_BYTES), "exact disposable fixture size");
    glive_packet seed{}; file.seekg(0); file.read(reinterpret_cast<char *>(&seed), sizeof(seed));
    require(bool(file), "read disposable fixture");
    static Context context(seed);
    visible_cases(context); peer_cases(context); scene_cases(context);
    local_cases(context); owner_and_counter_cases(context);
    std::printf("{\"ok\":true,\"transparent_model_shell\":true,\"real_model\":false,\"gpu\":false,"
                "\"max_peers\":%u,\"model_calls\":%u,\"checks\":%u,\"render_allocations\":%u,\"render_frees\":%u}\n",
                GLIVE_MAX_PEERS, model_calls, checks, allocations.load(), frees.load());
}
