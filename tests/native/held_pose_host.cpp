// The real model and the real ghost_pose::evaluate: Mario holding each kind
// the ghost draws, under the F3D lighting. Every kind must shade by its own
// lists' lights and draw beside Mario; a held colour its lights do not name
// would drop the object (and before that, the whole ghost).
#include "ghost_pose.h"
#include <cmath>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iterator>
#include <vector>

static int failures = 0;
static void check(bool ok, const char *what) {
    if (!ok) { std::fprintf(stderr, "FAILED: %s\n", what); ++failures; }
}

int main(int argc, char **argv) {
    if (argc != 2) { std::fprintf(stderr, "usage: held_pose_host rom\n"); return 2; }
    std::ifstream in(argv[1], std::ios::binary);
    std::vector<uint8_t> rom((std::istreambuf_iterator<char>(in)), {});
    sg_model *model = nullptr;
    if (sg_model_open(rom.data(), rom.size(), &model) != SG_OK) { std::fprintf(stderr, "model open\n"); return 3; }
    gs_record record{}; record.bytes = sizeof(record); record.version = GS_VERSION; record.configuration = 2;
    auto &saved = record.sample;
    saved.bytes = sizeof(saved); saved.version = GC_VERSION; saved.epoch = 1; saved.sequence = saved.dlist_serial = 4;
    saved.level = 9; saved.area = 1;
    saved.pose.root_kind = GC_ROOT_TRS; saved.pose.action = saved.pose.body_action = 0x0c400201;
    saved.pose.eye_state = 1;
    for (int i = 0; i < 3; ++i) saved.pose.scale[i] = 1;
    saved.pose.angles[1] = 0x2000;
    gc_sample live = saved; live.epoch = 3; live.sequence = live.dlist_serial = 20;
    live.valid_fields = GC_VALID_CLOCK | GC_VALID_CAMERA;
    const float camera[3] = {0, 160, 700}, focus[3] = {0, 80, 0};
    std::memcpy(live.camera.position, camera, sizeof(camera)); std::memcpy(live.camera.focus, focus, sizeof(focus));
    ghost::WorldPass world{}; world.epoch = 3; world.context_generation = 2; world.list_serial = 20;
    world.projection[0] = world.projection[5] = world.projection[10] = world.projection[15] = 1;
    world.target.screen_scale[0] = world.target.screen_scale[1] = 1;
    static ghost_pose::Scratch scratch;
    ghost_compositor::Pose pose{};
    saved.valid_fields = GC_VALID_CLOCK | GC_VALID_POSE;
    check(ghost_pose::evaluate(model, record, live, world, 1.0f, scratch, pose) == ghost_pose::Result::ok, "Mario alone");
    const uint32_t alone = pose.count;
    check(alone > 1800, "Mario's own vertices");
    // Held vertices per kind: the triangles its geo reaches (model_host).
    const struct { uint32_t behavior; int16_t animation; uint32_t vertices; const char *name; } kinds[] = {
        {0x13003174u, 1, 240, "Bob-omb"}, {0x130020E8u, 0, 402, "penguin"}, {0x130001F4u, 0, 348, "King Bob-omb"},
        {0x13000528u, 0, 132, "Chuckya"}, {0x13004218u, -1, 36, "cork box"}, {0x13001650u, -1, 36, "crazy box"},
        {0x130044FCu, 0, 588, "Mips"}};
    for (const auto &k : kinds) {
        saved.valid_fields = GC_VALID_CLOCK | GC_VALID_POSE | GC_VALID_HELD;
        saved.held = {}; saved.held.behavior = k.behavior; saved.held.animation_id = k.animation;
        saved.held.holp[0] = 45; saved.held.holp[1] = 75; saved.held.holp[2] = 45;
        saved.held.scale[0] = saved.held.scale[1] = saved.held.scale[2] = 1;
        const auto result = ghost_pose::evaluate(model, record, live, world, 1.0f, scratch, pose);
        std::fprintf(stderr, "%s: result %u vertices %u (Mario %u)\n", k.name, unsigned(result), pose.count, alone);
        check(result == ghost_pose::Result::ok, k.name);
        check(pose.count == alone + k.vertices, "held object shaded by its own lights and drawn beside Mario");
        bool shaded = true;
        for (uint32_t i = alone; i < pose.count; ++i)
            for (int c = 0; c < 3; ++c)
                shaded &= std::isfinite(pose.vertices[i].color[c]) && pose.vertices[i].color[c] >= 0
                    && pose.vertices[i].color[c] <= 1;
        check(shaded, "held colours are F3D shades");
    }
    // Thrown: the same Bob-omb drawn where it flies, by its own node's
    // TRS(position, angle, scale), whatever Mario's own root is.
    const auto released = [&](uint32_t behavior, int16_t animation, uint32_t vertices, int16_t yaw,
                              float &cx, float &cz, float &width_x, float &width_z) {
        saved.valid_fields = GC_VALID_CLOCK | GC_VALID_POSE | GC_VALID_HELD;
        saved.held = {}; saved.held.behavior = behavior; saved.held.animation_id = animation; saved.held.released = 1;
        saved.held.holp[0] = 600; saved.held.holp[1] = 20; saved.held.holp[2] = -400; saved.held.angles[1] = yaw;
        saved.held.scale[0] = saved.held.scale[1] = saved.held.scale[2] = 1;
        const auto result = ghost_pose::evaluate(model, record, live, world, 1.0f, scratch, pose);
        check(result == ghost_pose::Result::ok && pose.count == alone + vertices, "released object drawn beside Mario");
        float lo[3] = {1e9f, 1e9f, 1e9f}, hi[3] = {-1e9f, -1e9f, -1e9f};
        for (uint32_t i = alone; i < pose.count; ++i)
            for (int c = 0; c < 3; ++c) {
                lo[c] = std::fmin(lo[c], pose.vertices[i].position[c]);
                hi[c] = std::fmax(hi[c], pose.vertices[i].position[c]);
            }
        cx = (lo[0] + hi[0]) / 2; cz = (lo[2] + hi[2]) / 2; width_x = hi[0] - lo[0]; width_z = hi[2] - lo[2];
    };
    float cx, cz, wx, wz, turned_cx, turned_cz, turned_wx, turned_wz;
    released(0x13003174u, 1, 240, 0, cx, cz, wx, wz);
    std::fprintf(stderr, "released Bob-omb: centre %.1f %.1f\n", cx, cz);
    check(std::fabs(cx - 600) < 60 && std::fabs(cz + 400) < 60, "released at its own position, not his hand");
    // The cork box is a cube with no billboard: an eighth turn of its own yaw
    // widens its footprint by the square root of two.
    released(0x13004218u, -1, 36, 0, cx, cz, wx, wz);
    released(0x13004218u, -1, 36, 0x2000, turned_cx, turned_cz, turned_wx, turned_wz);
    std::fprintf(stderr, "released box: %.1f x %.1f, turned %.1f x %.1f\n", wx, wz, turned_wx, turned_wz);
    check(std::fabs(turned_cx - 600) < 1 && std::fabs(turned_cz + 400) < 1, "turned about its own position");
    check(std::fabs(turned_wx / wx - 1.41421f) < 0.02f && std::fabs(turned_wz / wz - 1.41421f) < 0.02f,
          "its own yaw turns it");
    sg_model_close(&model);
    return failures ? 1 : 0;
}
