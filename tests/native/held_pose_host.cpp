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
#include <utility>
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
    // scratch.evaluated: the model's whole mesh; pose.count: what is drawn,
    // the back faces the game culls left out under this camera.
    const uint32_t alone = scratch.evaluated, drawn_alone = pose.count;
    check(alone > 1800, "Mario's own vertices");
    check(drawn_alone > 0 && drawn_alone < alone, "his back faces are culled");
    // Held vertices per kind: the triangles its geo reaches (model_host).
    const struct { uint32_t behavior; int16_t animation; uint32_t vertices; const char *name; } kinds[] = {
        {0x13003174u, 1, 240, "Bob-omb"}, {0x130020E8u, 0, 402, "penguin"}, {0x130001F4u, 0, 348, "King Bob-omb"},
        {0x13000528u, 0, 132, "Chuckya"}, {0x13004218u, -1, 36, "cork box"}, {0x13001650u, -1, 36, "crazy box"},
        {0x130044FCu, 0, 588, "Mips"}, {0x130033ECu, 1, 594, "Hoot"},
        {0x13001F3Cu, -1, 186, "shell"}, {0x13000F08u, 12, 822, "Ukiki"},
        {0x13001CB0u, 12, 822, "macro Ukiki"}};
    for (const auto &k : kinds) {
        saved.valid_fields = GC_VALID_CLOCK | GC_VALID_POSE | GC_VALID_HELD;
        saved.held = {}; saved.held.behavior = k.behavior; saved.held.animation_id = k.animation;
        if(k.behavior==0x130033ECu || k.behavior==0x13001F3Cu)saved.held.released=1;
        saved.held.holp[0] = 45; saved.held.holp[1] = 75; saved.held.holp[2] = 45;
        saved.held.scale[0] = saved.held.scale[1] = saved.held.scale[2] = 1;
        const auto result = ghost_pose::evaluate(model, record, live, world, 1.0f, scratch, pose);
        std::fprintf(stderr, "%s: result %u vertices %u (Mario %u), drawn %u (Mario %u)\n", k.name, unsigned(result),
                     scratch.evaluated, alone, pose.count, drawn_alone);
        check(result == ghost_pose::Result::ok, k.name);
        check(scratch.evaluated == alone + k.vertices && pose.count > drawn_alone,
              "held object shaded by its own lights and drawn beside Mario");
        bool shaded = true;
        for (uint32_t i = drawn_alone; i < pose.count; ++i)
            for (int c = 0; c < 3; ++c)
                shaded &= std::isfinite(pose.vertices[i].color[c]) && pose.vertices[i].color[c] >= 0
                    && pose.vertices[i].color[c] <= 1;
        check(shaded, "held colours are F3D shades");
    }
    // The shell uses its own root. The hand-held Ukiki uses Mario's root
    // orientation at the recorded HOLP, including his canonical stolen-cap
    // switch state. All companion vertices share the overlay's opacity.
    saved.held={};saved.held.behavior=0x13001F3Cu;saved.held.released=1;saved.held.animation_id=-1;
    saved.held.holp[0]=400;saved.held.holp[1]=20;saved.held.holp[2]=-600;saved.held.angles[1]=0x2000;
    saved.held.scale[0]=saved.held.scale[1]=saved.held.scale[2]=1;
    check(ghost_pose::evaluate(model,record,live,world,.6f,scratch,pose)==ghost_pose::Result::ok
        && scratch.evaluated==alone+186 && pose.opacity==.6f,"ridden shell shares Mario mesh and opacity");
    const std::vector<float> shell_root(scratch.position+3*alone,scratch.position+3*scratch.evaluated);
    const auto mario_x=saved.pose.position[0];const auto mario_yaw=saved.pose.angles[1];
    saved.pose.position[0]+=1000;saved.pose.angles[1]=0x6000;
    check(ghost_pose::evaluate(model,record,live,world,.6f,scratch,pose)==ghost_pose::Result::ok
        && !std::memcmp(shell_root.data(),scratch.position+3*alone,shell_root.size()*sizeof(float)),
        "shell own gfx root stays independent of Mario root");
    saved.pose.position[0]=mario_x;saved.pose.angles[1]=mario_yaw;
    saved.held={};saved.held.behavior=0x13000F08u;saved.held.animation_id=12;
    saved.held.holp[0]=45;saved.held.holp[1]=75;saved.held.holp[2]=45;
    saved.held.scale[0]=saved.held.scale[1]=saved.held.scale[2]=1;
    for(uint16_t state=0;state<4;++state) {
        saved.held.anim_state=state;
        check(ghost_pose::evaluate(model,record,live,world,.6f,scratch,pose)==ghost_pose::Result::ok
            && scratch.evaluated==alone+(state<2?822:978) && pose.opacity==.6f,
            "canonical Ukiki eyes and stolen-cap states draw through ghost lighting and opacity");
    }
    saved.held.anim_state=0;
    ghost_pose::evaluate(model,record,live,world,.6f,scratch,pose);
    const std::vector<float> ukiki_root(scratch.position+3*alone,scratch.position+3*scratch.evaluated);
    saved.pose.angles[1]=0x6000;
    check(ghost_pose::evaluate(model,record,live,world,.6f,scratch,pose)==ghost_pose::Result::ok
        && std::memcmp(ukiki_root.data(),scratch.position+3*alone,ukiki_root.size()*sizeof(float)),
        "held Ukiki follows Mario orientation at the recorded HOLP");
    saved.pose.angles[1]=mario_yaw;saved.held.animation_frame=14;
    check(ghost_pose::evaluate(model,record,live,world,.6f,scratch,pose)==ghost_pose::Result::ok
        && scratch.evaluated==alone,"refused Ukiki frame leaves Mario visible");
    saved.held.behavior=0x13001F3Cu;saved.held.animation_id=0;saved.held.animation_frame=0;saved.held.released=1;
    check(ghost_pose::evaluate(model,record,live,world,.6f,scratch,pose)==ghost_pose::Result::ok
        && scratch.evaluated==alone,"refused shell animation leaves Mario visible");
    // A legacy held Mips record said id 0 even though the game's initializer
    // selected animation 4. It must produce the same full mesh as an explicit
    // current record, while released id 0 retains the upright idle pose.
    saved.held = {};saved.held.behavior = 0x130044FCu;saved.held.animation_id = 0;
    saved.held.holp[1] = 75;
    saved.held.scale[0] = saved.held.scale[1] = saved.held.scale[2] = 1;
    check(ghost_pose::evaluate(model,record,live,world,1,scratch,pose) == ghost_pose::Result::ok,
          "legacy held Mips evaluates");
    const std::vector<float> legacy_mips(scratch.position + 3*alone,scratch.position + 3*scratch.evaluated);
    float down = -1e9f;
    for(uint32_t i=alone;i<scratch.evaluated;++i)down=std::fmax(down,scratch.position[3*i+1]);
    saved.held.animation_id = 4;
    check(ghost_pose::evaluate(model,record,live,world,1,scratch,pose) == ghost_pose::Result::ok
          && legacy_mips.size() == 3*(scratch.evaluated-alone)
          && !std::memcmp(legacy_mips.data(),scratch.position+3*alone,legacy_mips.size()*sizeof(float)),
          "legacy held Mips draws the canonical ears-down mesh");
    saved.held.released = 1;saved.held.animation_id = 0;saved.held.angles[1] = saved.pose.angles[1];
    check(ghost_pose::evaluate(model,record,live,world,1,scratch,pose) == ghost_pose::Result::ok,
          "released Mips idle evaluates");
    float up = -1e9f;
    for(uint32_t i=alone;i<scratch.evaluated;++i)up=std::fmax(up,scratch.position[3*i+1]);
    std::fprintf(stderr,"Mips ears: held %.2f, released idle %.2f\n",down,up);
    check(down < up,"Mips held ears sit below the upright idle ears");
    // Owl's own world transform is independent of Mario's heading/root,
    // and its recorded flying animation flaps the actual model's wings.
    saved.held = {};saved.held.behavior = 0x130033ECu;saved.held.released = 1;
    saved.held.animation_id = 1;saved.held.holp[0] = 400;saved.held.holp[1] = 500;saved.held.holp[2] = -600;
    saved.held.scale[0] = saved.held.scale[1] = saved.held.scale[2] = 1;
    check(ghost_pose::evaluate(model,record,live,world,.6f,scratch,pose) == ghost_pose::Result::ok
          && scratch.evaluated == alone+594 && pose.opacity==.6f,"ridden Owl shares Mario's overlay mesh and opacity");
    const std::vector<float> owl_pose(scratch.position+3*alone,scratch.position+3*scratch.evaluated);
    const auto mario_position=saved.pose.position[0];const auto mario_heading=saved.pose.angles[1];
    saved.pose.position[0]=1000;saved.pose.angles[1]=0x6000;
    check(ghost_pose::evaluate(model,record,live,world,.6f,scratch,pose) == ghost_pose::Result::ok
          && !std::memcmp(owl_pose.data(),scratch.position+3*alone,owl_pose.size()*sizeof(float)),
          "Owl stays at its own root when Mario's root changes");
    saved.pose.position[0]=mario_position;saved.pose.angles[1]=mario_heading;saved.held.animation_frame=7;
    check(ghost_pose::evaluate(model,record,live,world,.6f,scratch,pose) == ghost_pose::Result::ok
          && std::memcmp(owl_pose.data(),scratch.position+3*alone,owl_pose.size()*sizeof(float)),
          "recorded Owl wing animation changes the mesh");
    // Thrown: the same Bob-omb drawn where it flies, by its own node's
    // TRS(position, angle, scale), whatever Mario's own root is.
    const auto released = [&](uint32_t behavior, int16_t animation, uint32_t vertices, int16_t yaw,
                              float &cx, float &cz, float &width_x, float &width_z) {
        saved.valid_fields = GC_VALID_CLOCK | GC_VALID_POSE | GC_VALID_HELD;
        saved.held = {}; saved.held.behavior = behavior; saved.held.animation_id = animation; saved.held.released = 1;
        saved.held.holp[0] = 600; saved.held.holp[1] = 20; saved.held.holp[2] = -400; saved.held.angles[1] = yaw;
        saved.held.scale[0] = saved.held.scale[1] = saved.held.scale[2] = 1;
        const auto result = ghost_pose::evaluate(model, record, live, world, 1.0f, scratch, pose);
        check(result == ghost_pose::Result::ok && scratch.evaluated == alone + vertices && pose.count > drawn_alone,
              "released object drawn beside Mario");
        float lo[3] = {1e9f, 1e9f, 1e9f}, hi[3] = {-1e9f, -1e9f, -1e9f};
        for (uint32_t i = alone; i < scratch.evaluated; ++i)
            for (int c = 0; c < 3; ++c) {
                lo[c] = std::fmin(lo[c], scratch.position[3 * i + c]);
                hi[c] = std::fmax(hi[c], scratch.position[3 * i + c]);
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
    // The same delivered pose fallback with an optional model's ROM segment
    // unavailable. Never hide Mario merely because a companion cannot draw.
    for(const auto &bad : std::vector<std::pair<uint32_t,uint32_t>>{
            {0x1F2200u,0x13001F3Cu},{0x1521D0u,0x13000F08u},{0x201410u,0x13000F08u}}) {
        auto missing=rom;missing[bad.first]=0;
        check(sg_model_open(missing.data(),missing.size(),&model)==SG_OK,"missing companion segment still opens Mario");
        if(!model)continue;
        saved.held={};saved.held.behavior=bad.second;
        saved.held.animation_id=bad.second==0x13001F3Cu?-1:12;
        saved.held.released=bad.second==0x13001F3Cu?1:0;
        saved.held.holp[1]=75;saved.held.scale[0]=saved.held.scale[1]=saved.held.scale[2]=1;
        check(ghost_pose::evaluate(model,record,live,world,.6f,scratch,pose)==ghost_pose::Result::ok
            && scratch.evaluated==alone,"missing companion textures omit only the companion");
        sg_model_close(&model);
    }
    return failures ? 1 : 0;
}
