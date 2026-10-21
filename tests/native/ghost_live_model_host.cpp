// Authentic compact live packets through the real ROM model and pose converter.
// CPU witness plus opt-in hidden GPU output; no saved playback, network or emulator.
#include "ghost_live_render.h"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iterator>
#include <memory>
#include <vector>

#ifdef SM64_LIVE_MODEL_GPU
#define main compositor_reference_main
#define require compositor_require
#include "ghost_compositor_host.cpp"
#undef require
#undef main
#endif

namespace {
void require(bool condition, const char *what) {
    if (!condition) { std::fprintf(stderr, "FAILED: %s\n", what); std::exit(1); }
}
std::vector<uint8_t> read(const char *path, size_t maximum) {
    std::ifstream file(path, std::ios::binary | std::ios::ate);
    require(bool(file), "open supplied local input");
    const auto size = file.tellg();
    require(size > 0 && size <= std::streamoff(maximum), "bounded local input");
    std::vector<uint8_t> bytes(static_cast<size_t>(size));
    file.seekg(0); file.read(reinterpret_cast<char *>(bytes.data()), bytes.size());
    require(bool(file), "read local input");
    return bytes;
}
void admit(ghost_live::Input& input, unsigned peers) {
    for (unsigned seat = 0; seat < peers; ++seat) {
        uint8_t nonce[16];
        for (unsigned i = 0; i < 16; ++i) nonce[i] = uint8_t(i);
        nonce[15] += uint8_t(seat);
        require(input.authorize(seat, nonce) == ghost_live::InputResult::ok, "trusted seat admission");
    }
}
void receive(ghost_live::Input& input, const uint8_t* packet, unsigned peers, uint64_t now) {
    for (unsigned seat = 0; seat < peers; ++seat) {
        glive_packet wire{}; std::memcpy(&wire, packet, sizeof(wire));
        wire.header.participant_incarnation[15] += uint8_t(seat);
        require(input.ingest(seat, &wire, sizeof(wire), now) == ghost_live::InputResult::ok,
                "actual native input accepts ordered compact source");
    }
    require(input.publish(now), "actual owned mailbox publication");
}
struct LocalScene { gc_sample local{}; ghost::WorldPass world{}; uint32_t source_fields = 0; };
LocalScene local_scene(const uint8_t* packet, uint32_t context) {
    uint8_t expected[16];
    for (unsigned i = 0; i < 16; ++i) expected[i] = uint8_t(i);
    glive_value camera_seed{};
    require(ghost_live::decode(packet, GLIVE_PACKET_BYTES, expected, camera_seed) == ghost_live::DecodeResult::ok,
            "validated fixture for synthetic local camera");
    LocalScene scene{}; scene.local = camera_seed.sample; scene.source_fields = camera_seed.sample.valid_fields;
    auto& local = scene.local; auto& world = scene.world;
    local.epoch = 99; local.sequence = local.dlist_serial = 999;
    local.valid_fields = GC_VALID_CLOCK | GC_VALID_CAMERA;
    local.camera.position[0] += 17; local.camera.focus[0] += 17;
    world.epoch = local.epoch; world.list_serial = local.dlist_serial; world.context_generation = context;
    const float n = float(local.camera.near_plane), f = float(local.camera.far_plane);
    const float scale = 1.f / std::tan(local.camera.fov * 3.14159265358979323846f / 360.f);
    world.projection[0] = scale / (640.f / 480.f); world.projection[5] = scale;
    world.projection[10] = -(f + n) / (f - n); world.projection[11] = -1;
    world.projection[14] = -2 * f * n / (f - n);
    world.target.screen_scale[0] = world.target.screen_scale[1] = 1;
    return scene;
}

#ifdef SM64_LIVE_MODEL_GPU
void write_image(const std::string& path, const std::vector<uint8_t>& bytes) {
    std::ofstream out(path, std::ios::binary);
    out << "P6\n640 480\n255\n";
    for (int y = 479; y >= 0; --y)
        for (unsigned x = 0; x < 640; ++x)
            out.write(reinterpret_cast<const char*>(&bytes[(y * 640 + x) * 4]), 3);
    require(bool(out), "write GPU evidence");
}
struct PixelProof {
    unsigned one = 0, three = 0, partial = 0, withdrawals = 0;
    bool clean = true;
    std::string renderer;
};
void arrange(gc::Pose* poses, uint32_t mask) {
    unsigned at = 0;
    for (unsigned seat = 0; seat < GLIVE_MAX_PEERS; ++seat) {
        if (!(mask & (1u << seat))) continue;
        // Synthetic screen placement makes every included seat inspectable.
        const float shift = mask == 1 ? 0.f : (float(seat) - 1.f) * .65f;
        for (unsigned column = 0; column < 4; ++column)
            poses[at].view_projection[column * 4] += shift * poses[at].view_projection[column * 4 + 3];
        ++at;
    }
}
PixelProof gpu_proof(sg_model *model, const uint8_t *packet, const char *image_prefix) {
    Window window;
    require(!IsWindowVisible(window.window), "GPU witness stays hidden");
    auto g = dispatch(); Scene scene(g, 640, 480); Bindings bindings(g);
    ordinary_state(g, scene);
    // Synthetic unobstructed world depth; this is not live GLideN64 terrain.
    const std::vector<float> unobstructed(640 * 480, 1.f);
    g.BindBuffer(cgl::PIXEL_UNPACK_BUFFER, 0);
    g.BindTexture(GL_TEXTURE_2D, scene.depth);
    g.TexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, 640, 480, GL_DEPTH_COMPONENT, GL_FLOAT, unobstructed.data());
    sg_atlas_view atlas{};
    require(sg_model_get_atlas(model, &atlas) == SG_OK, "actual ROM texture atlas");
    auto *compositor = gc::create(g, 1);
    require(compositor != nullptr, "actual live compositor");
    hostile(g, scene, bindings);
    require(gc::prepare(compositor, 640, 480, 3 * SG_MODEL_MAX_VERTICES,
                        atlas.rgba, atlas.width, atlas.height) == gc::Result::ok, "prepare real GPU atlas");
    require(gc::warm(compositor) == gc::Result::ok, "warm shaders outside frame evaluation");
    ordinary_state(g, scene);
    const auto baseline = pixels(g, scene.fbo, 640, 480);
    const auto baseline_depth = depth(g, scene);
    write_image(std::string(image_prefix) + "-baseline.ppm", baseline);
    PixelProof proof{};
    const auto *renderer = reinterpret_cast<const char*>(glGetString(GL_RENDERER));
    require(renderer != nullptr, "actual GPU renderer identity");
    proof.renderer = renderer;
    require(proof.renderer.size() < 256 && proof.renderer.find_first_of("\"\\\n\r") == std::string::npos,
            "bounded printable GPU identity");
    for (unsigned peers : {1u, GLIVE_MAX_PEERS}) {
        ghost_live::Input input(250'000'000);
        std::unique_ptr<ghost_live::Renderer> live(new ghost_live::Renderer(input));
        admit(input, peers); receive(input, packet, peers, 1);
        const auto scene_input = local_scene(packet, 1);
        const ghost_live::ModelBinding binding{model, 1, 1, 1, 1};
        gc::Pose poses[GLIVE_MAX_PEERS]{};
        uint32_t count = 0; ghost_live::DrawToken token{};
        require(live->begin(binding, scene_input.local, 1) && live->plan(), "reusable renderer live selection");
        require(live->evaluate(scene_input.local, scene_input.world, 1, .8f, poses, count, token)
                    && count == peers && live->current(token, 1), "reusable renderer real GPU geometry");
        arrange(poses, token.included_seats);
        hostile(g, scene, bindings); cgl::State before{}; before.capture(g);
        require(gc::begin_batch(compositor, target(scene, gc::Depth::ordinary), poses, peers) == gc::Result::ok,
                "actual one/three GPU live batch");
        equal_state(g, before, bindings);
        require(gc::finalize(compositor, 77) == gc::Result::ok, "finalized private display");
        ordinary_state(g, scene);
        const auto display = pixels(g, gc::display(compositor).framebuffer, 640, 480);
        unsigned changed = 0;
        for (size_t at = 0; at < display.size(); at += 4)
            if (std::memcmp(display.data() + at, baseline.data() + at, 3)) ++changed;
        require(changed > 100, "real Mario pixels drawn from live input");
        proof.clean &= pixels(g, scene.fbo, 640, 480) == baseline && depth(g, scene) == baseline_depth;
        if (peers == 1) proof.one = changed; else proof.three = changed;
        write_image(std::string(image_prefix) + (peers == 1 ? "-one.ppm" : "-three.ppm"), display);

        // Build a real provisional GPU candidate, then withdraw authority BEFORE
        // final presentation. The harness's presentation policy must abort the
        // whole candidate, just as production composition will have to do.
        require(gc::begin_batch(compositor, target(scene, gc::Depth::ordinary), poses, peers) == gc::Result::ok,
                "positive provisional candidate before withdrawal");
        const unsigned withdrawn = peers == 1 ? 0 : 1;
        const auto result = peers == 1 ? input.clear(withdrawn) : input.revoke(withdrawn);
        require(result == ghost_live::InputResult::ok && !live->current(token, 2),
                "actual authority gate withdraws after model and GPU evaluation");
        gc::abort(compositor);
        require(gc::display(compositor).texture == 0, "aborted GPU candidate cannot expose stale display");
        ++proof.withdrawals;

        if (peers == GLIVE_MAX_PEERS) {
            gc::Pose surviving[GLIVE_MAX_PEERS]{};
            require(live->begin(binding, scene_input.local, 2)
                && live->evaluate(scene_input.local, scene_input.world, 2, .8f, surviving, count, token)
                && count == 2 && token.included_seats == 5 && live->current(token, 2),
                "only current survivors selected by reusable renderer after revocation");
            arrange(surviving, token.included_seats);
            require(gc::begin_batch(compositor, target(scene, gc::Depth::ordinary), surviving, count) == gc::Result::ok,
                    "surviving real GPU geometry");
            require(gc::finalize(compositor, 77) == gc::Result::ok, "surviving private display");
            const auto partial = pixels(g, gc::display(compositor).framebuffer, 640, 480);
            for (size_t at = 0; at < partial.size(); at += 4)
                if (std::memcmp(partial.data() + at, baseline.data() + at, 3)) ++proof.partial;
            require(proof.partial > proof.one && proof.partial < proof.three, "withdrawn middle peer contributes no pixels");
            write_image(std::string(image_prefix) + "-withdrawn-one.ppm", partial);
            require(gc::begin_batch(compositor, target(scene, gc::Depth::ordinary), surviving, count) == gc::Result::ok,
                    "positive two-peer provisional candidate");
            for (unsigned seat : {0u, 2u})
                require(input.revoke(seat) == ghost_live::InputResult::ok && !live->current(token, 3),
                        "all remaining actual authority gates withdrawn");
            gc::abort(compositor);
            require(gc::display(compositor).texture == 0, "all-withdrawn GPU candidate unavailable");
            ++proof.withdrawals;
        }
        proof.clean &= pixels(g, scene.fbo, 640, 480) == baseline && depth(g, scene) == baseline_depth;
        live->retire();
    }
    require(proof.clean && proof.three > proof.one, "three distinct peers and clean scene isolation");
    gc::destroy(compositor);
    return proof;
}
#endif
}

int main(int argc, char **argv) {
    require(argc == 3 || argc == 4, "usage: ghost_live_model_host local-ROM compact-packets [GPU-image-prefix]");
    const auto rom = read(argv[1], 8u << 20);
    const auto packets = read(argv[2], GLIVE_PACKET_BYTES * 4096u);
    require(rom.size() == (8u << 20) && packets.size() % GLIVE_PACKET_BYTES == 0, "input shapes");
    sg_model *model = nullptr;
    require(sg_model_open(rom.data(), rom.size(), &model) == SG_OK, "actual local ROM model");
    uint64_t evaluations = 0, held = 0, vertices = 0, accepted = 0;
    uint32_t minimum_mesh = UINT32_MAX, minimum_visible = UINT32_MAX;
    double one_ms = 0, three_ms = 0;
    for (unsigned peers : {1u, GLIVE_MAX_PEERS}) {
        ghost_live::Input input(250'000'000);
        std::unique_ptr<ghost_live::Renderer> live(new ghost_live::Renderer(input));
        const ghost_live::ModelBinding binding{model, 1, 1, 1, 5};
        admit(input, peers);
        const auto start = std::chrono::steady_clock::now();
        for (size_t at = 0; at < packets.size(); at += GLIVE_PACKET_BYTES) {
            const uint64_t now = (at / GLIVE_PACKET_BYTES + 1) * 33'333'333ull;
            receive(input, packets.data() + at, peers, now);
            const auto scene_input = local_scene(packets.data() + at, 5);
            ghost_compositor::Pose poses[GLIVE_MAX_PEERS]{};
            uint32_t count = 0; ghost_live::DrawToken token{};
            require(live->begin(binding, scene_input.local, now) && live->plan(), "reusable renderer input selection");
            require(live->evaluate(scene_input.local, scene_input.world, now, .8f, poses, count, token)
                && count == peers && live->current(token, now), "reusable renderer actual ROM model conversion");
            for (unsigned seat = 0; seat < peers; ++seat) {
                const auto& pose = poses[seat];
                const uint32_t mesh = pose.count + pose.culled;
                require(mesh > 800 && pose.count > 0 && pose.opacity == .8f, "actual Mario mesh and opacity");
                if (seat) require(pose.vertices != poses[seat - 1].vertices, "peer mesh storage independent");
                for (uint32_t i = 0; i < pose.count; ++i)
                    for (unsigned axis = 0; axis < 3; ++axis)
                        require(std::isfinite(pose.vertices[i].position[axis]) &&
                                std::isfinite(pose.vertices[i].color[axis]), "finite actual mesh/shading");
                minimum_mesh = (std::min)(minimum_mesh, mesh);
                minimum_visible = (std::min)(minimum_visible, pose.count);
                vertices += mesh; ++evaluations;
                if (scene_input.source_fields & GC_VALID_HELD) ++held;
            }
        }
        const auto elapsed = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
        accepted += input.stats().accepted;
        if (peers == 1) one_ms = elapsed; else three_ms = elapsed;
        live->retire();
    }
#ifdef SM64_LIVE_MODEL_GPU
    require(argc == 4, "GPU image prefix required");
    const auto gpu = gpu_proof(model, packets.data(), argv[3]);
#endif
    require(sg_model_close(&model) == SG_OK && !model, "close actual model");
    std::printf("{\"real_model\":true,\"native_mailbox\":true,\"native_renderer\":true,\"input_accepted\":%llu,\"records\":%u,\"evaluations\":%llu,"
        "\"held_evaluations\":%llu,\"vertices\":%llu,\"minimum_mesh\":%u,\"minimum_visible\":%u,"
        "\"one_peer_total_ms\":%.6f,\"three_peer_total_ms\":%.6f",
        static_cast<unsigned long long>(accepted), unsigned(packets.size() / GLIVE_PACKET_BYTES), static_cast<unsigned long long>(evaluations),
        static_cast<unsigned long long>(held), static_cast<unsigned long long>(vertices),
        minimum_mesh, minimum_visible, one_ms, three_ms);
#ifdef SM64_LIVE_MODEL_GPU
    std::printf(",\"gpu_pixels\":true,\"one_peer_pixels\":%u,\"three_peer_pixels\":%u,\"partial_peer_pixels\":%u,"
                "\"withdrawal_checks\":%u,\"clean_unchanged\":true,\"gpu_renderer\":\"%s\"}\n",
                gpu.one,gpu.three,gpu.partial,gpu.withdrawals,gpu.renderer.c_str());
#else
    std::printf(",\"gpu_pixels\":false}\n");
#endif
    return 0;
}
