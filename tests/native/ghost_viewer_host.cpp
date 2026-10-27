/* The scene viewer's ghosts witness: ghost_viewer.cpp's choice of each
 * ghost's row (start, gaps, bridged refusals, a dance tail's held fade, the
 * end), its refusals, its statelessness across any order of pictures and its
 * handover from the host's thread to the renderer's. Saved playback's own
 * clip validation and figure run as shipped; the model and pose conversion are
 * stubs (geometry has its own witnesses). No windows, no GL. */
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "ghost_viewer.h"
#include "ghost_playback.h"
#include "ghost_pose.h"
#include "ghost_particle_pose.h"
#include "ghost_stream.h"
#include "ghost_compositor.h"
#include <cmath>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <functional>
#include <mutex>
#include <stdexcept>
#include <thread>
#include <vector>

static unsigned checks = 0;
static void check(bool ok, const char *message) { ++checks; if (!ok) throw std::runtime_error(message); }

// GLideN64's shape: every list runs on the renderer's own thread while the caller waits.
class Renderer {
    std::mutex mutex;
    std::condition_variable wake, done;
    std::function<void()> job;
    bool pending = false, stopping = false;
    std::exception_ptr failed;
    std::thread thread;
public:
    Renderer() : thread([&] {
        std::unique_lock<std::mutex> lock(mutex);
        for (;;) {
            wake.wait(lock, [&] { return pending || stopping; });
            if (stopping) return;
            auto operation = std::move(job); lock.unlock();
            try { operation(); } catch (...) { failed = std::current_exception(); }
            lock.lock(); pending = false; done.notify_one();
        }
    }) {}
    ~Renderer() { { std::lock_guard<std::mutex> lock(mutex); stopping = true; } wake.notify_one(); thread.join(); }
    void run(std::function<void()> operation) {
        std::unique_lock<std::mutex> lock(mutex); job = std::move(operation); pending = true; wake.notify_one();
        done.wait(lock, [&] { return !pending; });
        if (failed) std::rethrow_exception(failed);
    }
};

struct sg_model { DWORD owner; };
static sg_model *fake_model = nullptr;
extern "C" int32_t sg_model_open(const uint8_t *, size_t, sg_model **out) {
    if (fake_model) return SG_BUSY;
    *out = fake_model = new sg_model{GetCurrentThreadId()}; return SG_OK;
}
extern "C" int32_t sg_model_detach(sg_model *p) {
    if (!p || p != fake_model || p->owner != GetCurrentThreadId()) return SG_WRONG_THREAD;
    p->owner = 0; return SG_OK;
}
extern "C" int32_t sg_model_adopt(sg_model *p) {
    if (!p || p != fake_model || p->owner) return SG_WRONG_THREAD;
    p->owner = GetCurrentThreadId(); return SG_OK;
}
extern "C" int32_t sg_model_close(sg_model **p) { delete *p; *p = fake_model = nullptr; return SG_OK; }
extern "C" int32_t sg_model_get_atlas(sg_model *p, sg_atlas_view *out) {
    *out = {};
    if (!p || p->owner != GetCurrentThreadId()) return SG_WRONG_THREAD;
    static const uint8_t pixel[] = {255, 0, 0, 255}; *out = {pixel, 1, 1, 209}; return SG_OK;
}
extern "C" int32_t sg_model_get_particles(sg_model *p, sg_particle_view *out) {
    *out = {}; return p && p->owner == GetCurrentThreadId() ? SG_OK : SG_WRONG_THREAD;
}
extern "C" int32_t sg_model_get_animation_frames(sg_model *, int32_t, int32_t *frames) { *frames = 1; return SG_OK; }
extern "C" uint32_t ghost_capture_validate_layout(const gc_layout *layout) { return layout ? GC_OK : GC_BAD_CONFIG; }
namespace ghost_particle_pose {
Placed place(sg_model *, const sg_particle_view &, const gc_sample &, const gc_camera &, const float *,
             ghost_compositor::ParticleVertex *, ghost_compositor::Particles &result) { result = {}; return {}; }
}
namespace ghost_pose {
// The drawn row names itself: its pose x is its row, and the vertices carry it.
Result evaluate(sg_model *p, const gs_record &recorded, const gc_sample &live, const ghost::WorldPass &world,
                float opacity, Scratch &scratch, ghost_compositor::Pose &out, uint32_t) {
    out = {};
    if (!p || p->owner != GetCurrentThreadId()) return Result::model_failed;
    if (!(live.valid_fields & GC_VALID_CAMERA) || world.epoch != live.epoch) return Result::missing_pose;
    for (unsigned i = 0; i < 3; ++i) {
        scratch.vertices[i] = {};
        scratch.vertices[i].position[0] = recorded.sample.pose.position[0];
        scratch.vertices[i].position[1] = 140.0f + float(i);
    }
    out.vertices = scratch.vertices; out.count = out.body_count = 3; out.opacity = opacity;
    out.view_projection[15] = 1; out.sort_depth = recorded.sample.pose.position[2];
    return Result::ok;
}
bool view_projection(sg_model *, const gc_camera &, const ghost::WorldPass &, float out[16]) {
    std::memset(out, 0, 16 * sizeof(float)); return false;
}
bool recorded_camera_inverse(sg_model *, const gc_camera &, float inverse[16]) {
    std::memset(inverse, 0, 16 * sizeof(float)); return false;
}
bool camera_relative(sg_model *, const gc_camera &, const float *, float out[3]) { out[0] = out[1] = out[2] = 0; return false; }
bool shadow(sg_model *, const gc_sample &, const float *, ghost_compositor::ShadowVertex *, uint8_t *,
            ghost_compositor::Shadow &out) { out = {}; return true; }
}
static unsigned configures = 0, disables = 0;
static int __cdecl stream_configure(const gc_layout *) { ++configures; return 1; }
static void __cdecl stream_disable() { ++disables; }
static int __cdecl stream_read(gs_record *, uint32_t) { return 0; }
static gs_stats __cdecl stream_stats() { return {}; }
static const gs_api stream_table = {sizeof(gs_api), GS_VERSION, 0, 0, stream_configure, stream_disable, stream_read,
                                    stream_stats};
namespace ghost_stream { const gs_api *api() { return &stream_table; } }

// A star clip of `rows` rows, origin raw IGT 10, so row 0 stands at
// coordinate 10. `gap`: rows from it on stand one game frame later (a frame
// the recording lacks); `refused`: that row has no pose (a bridged blink).
static std::vector<uint8_t> clip_bytes(uint8_t id, uint32_t rows, uint32_t gap, uint32_t refused, bool dance) {
    gclip_header h{};
    std::memcpy(h.magic, GCLIP_MAGIC, 8); h.version = GCLIP_VERSION; h.bytes = sizeof(h);
    h.record_bytes = sizeof(gclip_frame); h.record_count = rows; h.rom_layout = GC_ROM_US;
    h.sample_version = GC_VERSION; h.configuration = 2; h.epoch = 1; h.clip_id[0] = id;
    h.qpc_frequency = 10000000; h.utc_anchor = 1; h.first_sequence = 1; h.last_sequence = rows;
    h.level = 9; h.area = 1; h.clock_kind = GCLIP_STAR_CLOCK; h.origin_raw = 10;
    h.origin_sequence = 1; h.origin_elapsed = 1000; h.origin_continuity = 23; h.flags = dance ? GCLIP_DANCE_TAIL : 0;
    std::vector<gclip_frame> frames(rows);
    for (uint32_t i = 0; i < rows; ++i) {
        auto &f = frames[i]; auto &s = f.sample;
        const uint32_t step = i + (i >= gap ? 1 : 0);
        f.bytes = sizeof(f); f.version = GCLIP_VERSION; f.configuration = 2; f.world_status = GCLIP_WORLD_READY;
        s.bytes = sizeof(s); s.version = GC_VERSION; s.epoch = 1; s.sequence = s.dlist_serial = i + 1;
        s.global_timer = 100 + step; s.igt = uint16_t(10 + step); s.level = 9; s.area = 1;
        s.valid_fields = GC_VALID_CLOCK | GC_VALID_POSE | GC_VALID_ACTION | GC_VALID_WARP;
        s.live_action = 1; s.status = GC_NO_CAMERA; s.pose.root_kind = GC_ROOT_TRS;
        s.pose.scale[0] = s.pose.scale[1] = s.pose.scale[2] = 1; s.pose.position[0] = float(i);
        if (i == refused) { s.valid_fields &= ~GC_VALID_POSE; s.status = GC_UNSUPPORTED_ROOT; s.pose = {}; }
        s.clock = {GCC_RAW_VALID | GCC_ELAPSED_VALID, GCC_UNKNOWN_ORIGIN, GCC_STEADY, s.igt, 0, 0, 7, 1, 1000u + step, 23};
    }
    h.first_global_timer = h.origin_global_timer = frames[0].sample.global_timer;
    h.last_global_timer = frames[rows - 1].sample.global_timer;
    std::vector<uint8_t> out(sizeof(h) + rows * sizeof(gclip_frame));
    std::memcpy(out.data(), &h, sizeof(h));
    std::memcpy(out.data() + sizeof(h), frames.data(), rows * sizeof(gclip_frame));
    return out;
}

struct Drawn { int row; float opacity, hue; char label[64]; bool names; };
int main() {
    try {
        const auto *api = ghost_viewer::api();
        check(api->bytes == sizeof(gv_api) && api->version == GV_VERSION, "table version");
        const auto pb = clip_bytes(1, 12, 8, 5, false), dance = clip_bytes(2, 6, 99, 99, true);
        gv_look look{1.0f, 0.6f, GV_TINT | GV_NAMES | GV_SHADOW, 0};
        gv_ghost both[2] = {{0, 0, 250, 0}, {1, 0, 27, 0}};
        check(api->picture(nullptr, nullptr, 0) == GV_OK, "a host never set up draws nothing, untouched");
        check(api->picture(&look, both, 2) == GV_NOT_READY && api->load(0, pb.data(), uint32_t(pb.size()), "A") == GV_NOT_READY,
              "ghosts wait for setup");
        std::vector<uint8_t> rom(8u << 20);
        gc_layout layout{};
        check(api->setup(&layout, rom.data(), 100) == GV_INVALID, "setup takes the whole cartridge");
        check(api->setup(&layout, rom.data(), uint32_t(rom.size())) == GV_OK && fake_model && !fake_model->owner,
              "setup builds the model and hands it over detached");
        check(api->load(0, pb.data(), uint32_t(pb.size()), "YOUR PB") == GV_OK, "a valid clip loads");
        check(api->load(1, dance.data(), uint32_t(dance.size()), "Alice") == GV_OK, "a dance tail loads");
        auto broken = pb; broken[sizeof(gclip_header) + 3 * sizeof(gclip_frame) + 24] ^= 0xFF; // a row's sequence
        check(api->load(2, broken.data(), uint32_t(broken.size()), "Bob") == GV_CLIP, "playback's refusal refuses it");
        auto cut = pb; cut.resize(cut.size() - 10);
        check(api->load(2, cut.data(), uint32_t(cut.size()), "Bob") == GV_CLIP, "a cut file is refused");
        check(api->load(3, pb.data(), uint32_t(pb.size()), "bad\x01") == GV_INVALID, "a label it cannot show is refused");
        gv_ghost missing[1] = {{2, 0, 0, 0}};
        check(api->picture(&look, missing, 1) == GV_INVALID, "an empty slot cannot be drawn");
        gv_ghost twice[2] = {{0, 0, 0, 0}, {0, 3, 0, 0}};
        check(api->picture(&look, twice, 2) == GV_INVALID, "a slot draws once a picture");

        Renderer renderer;
        uint64_t serial = 0;
        // One picture: the host publishes the frames, the renderer plans the
        // list from its own sample and poses it at the seal.
        auto draw = [&](int32_t a, int32_t b, int16_t area = 1, bool plan = true) {
            both[0].frame = a; both[1].frame = b;
            check(api->picture(&look, both, 2) == GV_OK, "picture");
            std::vector<Drawn> drawn;
            renderer.run([&] {
                gc_sample live{}; live.bytes = sizeof(live); live.version = GC_VERSION; live.epoch = 3;
                live.sequence = live.dlist_serial = ++serial; live.level = 9; live.area = area;
                live.valid_fields = GC_VALID_CLOCK | GC_VALID_CAMERA;
                ghost::WorldPass world{}; world.epoch = 3; world.list_serial = serial; world.context_generation = 1;
                ghost_compositor::Pose poses[GV_GHOSTS]{}; uint32_t count = 0;
                const bool chosen = plan ? ghost_viewer::plan(live) : true;
                const bool posed = ghost_viewer::evaluate(live, world, poses, GV_GHOSTS, count);
                check(!plan || chosen == posed, "the plan chooses exactly what the seal draws");
                check(!posed || ghost_viewer::current(), "the picture holds while its list draws");
                for (uint32_t i = 0; i < count; ++i) {
                    Drawn d{int(poses[i].vertices[0].position[0]), poses[i].opacity, poses[i].hue_degrees, {},
                            poses[i].annotation.names};
                    std::memcpy(d.label, poses[i].annotation.label, sizeof(d.label));
                    drawn.push_back(d);
                }
                ghost_viewer::finished(posed);
            });
            return drawn;
        };
        auto rows = [](const std::vector<Drawn> &drawn) {
            std::vector<int> out; for (const auto &d : drawn) out.push_back(d.row); return out;
        };
        const unsigned configured = configures;
        // Ghost A (12 rows, frame 8 missing, row 5 refused); ghost B (6 rows, a dance tail).
        auto at = draw(3, 0);
        check(configures == configured + 1, "each picture with ghosts reads the stage afresh");
        check(rows(at) == std::vector<int>({3, 0}), "each ghost shows its own row");
        check(!std::strcmp(at[0].label, "YOUR PB") && !std::strcmp(at[1].label, "Alice") && at[0].names,
              "each name box carries its ghost's label");
        check(at[0].hue == 250.0f && at[1].hue == 27.0f && at[0].opacity == 1.0f, "tint and opacity as asked");
        gv_status status{};
        check(api->status(&status, sizeof(status)) == GV_OK && status.asked == 2 && status.planned == 1
              && status.chosen == 2 && status.posed == 2 && status.displayed == 1, "the status says what it drew");
        check(rows(draw(-2, -9)) == std::vector<int>({0, 0}), "before their start both show their first pose");
        check(rows(draw(5, 2)) == std::vector<int>({4, 2}), "a refused row shows the pose it bridges from");
        check(rows(draw(8, 1)) == std::vector<int>({1}), "a frame the recording lacks hides only that ghost");
        check(rows(draw(9, 1)) == std::vector<int>({8, 1}), "after its gap the ghost is back on its clock");
        check(rows(draw(12, 5)) == std::vector<int>({11, 5}), "both on their last rows");
        auto held = draw(13, 6);
        check(rows(held) == std::vector<int>({5}) && std::fabs(held[0].opacity - 14.0f / 15.0f) < 1e-6,
              "past its end a dance tail holds its last pose and fades; another clip is gone");
        auto faint = draw(30, 19);
        check(rows(faint) == std::vector<int>({5}) && std::fabs(faint[0].opacity - 1.0f / 15.0f) < 1e-6,
              "the fade takes GP_STAR_FADE_FRAMES");
        check(draw(30, 20).empty(), "and then it is gone");
        check(draw(3, 0, 2).empty(), "a stage in another area shows neither");
        // Any order draws the same: a seek, a step back, a repeat.
        const int order[] = {9, 3, 12, 0, 5, 9, 3};
        for (int frame : order) {
            const auto first = rows(draw(frame, frame % 6));
            check(first == rows(draw(frame, frame % 6, 1, false)), "a picture is the same however it is reached");
        }
        look.flags = GV_NAMES;
        auto plain = draw(3, 0);
        check(plain[0].hue == 0.0f && plain[1].hue == 0.0f, "tint off keeps the model's colours");
        // A new picture published after a list was planned retires it.
        both[0].frame = 4; check(api->picture(&look, both, 2) == GV_OK, "picture");
        renderer.run([&] { check(!ghost_viewer::current(), "a list from an older picture is not current"); });
        check(!ghost_viewer::plan(gc_sample{}), "only the renderer's thread draws");
        check(api->load(0, nullptr, 0, nullptr) == GV_OK && api->picture(&look, both, 2) == GV_INVALID,
              "an emptied slot is gone from later pictures");
        const unsigned disabled = disables;
        check(api->picture(&look, nullptr, 0) == GV_OK && disables == disabled + 1,
              "a picture without ghosts leaves the capture off");
        std::printf("{\"ok\":true,\"checks\":%u}\n", checks);
        return 0;
    } catch (const std::exception &error) {
        std::fprintf(stderr, "ghost viewer witness: %s\n", error.what());
        return 1;
    }
}
