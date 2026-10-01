#include "ghost_capture.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <new>
#include <string>
#include <initializer_list>
#include <vector>

static bool inside_reader = false;
void* operator new(size_t size) {
    if (inside_reader) std::abort();
    if (void *value = std::malloc(size)) return value;
    throw std::bad_alloc();
}
void operator delete(void *value) noexcept { std::free(value); }
void operator delete(void *value, size_t) noexcept { std::free(value); }

static void check(bool value, const char *message) {
    if (!value) { std::fprintf(stderr, "%s\n", message); std::exit(2); }
}
template<class T> static bool zero(const T &value) {
    const T empty{}; return std::memcmp(&value, &empty, sizeof(value)) == 0;
}
// A refused pose keeps exactly one byte: the check that refused it.
static bool cleared(const gc_pose &pose, uint8_t reason) {
    gc_pose expected{}; expected.refusal = reason;
    return reason && std::memcmp(&pose, &expected, sizeof(pose)) == 0;
}

struct Fixture {
    static constexpr uint32_t base = 0x80000000u;
    static constexpr uint32_t area = base + 0x10000, root = base + 0x11000;
    static constexpr uint32_t perspective = base + 0x11100, camera = base + 0x11200;
    static constexpr uint32_t control = base + 0x12000, body = base + 0x13000;
    static constexpr uint32_t animation = base + 0x14000;
    gc_layout config{};
    std::vector<uint8_t> n64, ram;
    gc_reader reader{};
    gc_frame frame{};
    gc_sample sample{};

    Fixture(const char *path) {
        std::ifstream file(path, std::ios::binary);
        file.read(reinterpret_cast<char*>(&config), sizeof(config));
        check(bool(file), "cannot read Python config");
        check(ghost_capture_validate_layout(&config) == GC_OK, "Python/C config mismatch");
        n64.resize(config.rdram_bytes); ram.resize(config.rdram_bytes);
        frame = {ram.data(), config.rdram_bytes, 1, 1, 1, 1234};
        put32(config.global_timer, 0x12345678);
        put16(config.area_update_counter, 0x8123); put16(config.igt_overall, 0xffe1);
        put16(config.pending_warp_op, 0); put16(config.delayed_warp_timer, 0);
        put16(config.current_level, 24); put16(config.current_area, 1);
        put32(config.current_area_pointer, area); put8(area, 1); put8(area + 1, 1);
        put32(area + 4, root); put32(area + 0x24, control);
        node(root, 1, 0); put8(root + 0x14, 1); put32(root + 16, perspective);
        node(perspective, 0x103, root); put32(perspective + 16, camera);
        putfloat(perspective + 0x1c, 45); put16(perspective + 0x20, 100); put16(perspective + 0x22, 20000);
        camera_node(camera, perspective);
        put8(control, 1);
        const auto obj = config.object_pool, state = config.mario_state;
        put32(config.mario_object, obj); put32(state + 0x88, obj);
        put32(state + 0x90, area); put32(state + 0x98, body);
        put32(state + 0x0c, 0x0c400201); put32(state + 4, 0x11);
        put16(obj, 0x18); put16(obj + 2, 0x21); put8(obj + 0x18, 1);
        put16(obj + 0x1a, 0xfff1); put16(obj + 0x1c, 12345); put16(obj + 0x1e, 0x8001);
        for (unsigned i = 0; i < 3; ++i) {
            putfloat(obj + 0x20 + 4*i, float(20 + i));
            putfloat(obj + 0x2c + 4*i, 1.0f + float(i)*0.25f);
            put16(body + 0x0c + 2*i, uint16_t(100+i));
            put16(body + 0x12 + 2*i, uint16_t(200+i));
        }
        put16(obj + 0x38, 197); put16(obj + 0x3a, 189);
        put32(obj + 0x3c, animation); put16(obj + 0x40, 7); put16(obj + 0x42, 0x8123);
        put16(animation + 8, 30);
        put32(body, 0x0c400201); put8(body + 4, 2); put8(body + 5, 3);
        put8(body + 6, 4); put8(body + 7, 1); put16(body + 8, 0x180);
        put8(body + 10, 2); put8(body + 11, 0x82);
    }
    void put8(uint32_t address, uint8_t value) { n64.at(address-base) = value; }
    void put16(uint32_t address, uint16_t value) {
        put8(address, uint8_t(value >> 8)); put8(address+1, uint8_t(value));
    }
    void put32(uint32_t address, uint32_t value) {
        for (unsigned i = 0; i < 4; ++i) put8(address+i, uint8_t(value >> (24-8*i)));
    }
    void putfloat(uint32_t address, float value) {
        uint32_t bits; std::memcpy(&bits, &value, 4); put32(address, bits);
    }
    void node(uint32_t address, uint16_t type, uint32_t parent) {
        put16(address, type); put16(address+2, 1);
        put32(address+4, address); put32(address+8, address); put32(address+12, parent);
        put32(address+16, 0);
    }
    void camera_node(uint32_t address, uint32_t parent) {
        node(address, 0x114, parent); put32(address+0x18, control);
        putfloat(address+0x1c, 600); putfloat(address+0x20, 250); putfloat(address+0x24, -200);
        putfloat(address+0x28, 10); putfloat(address+0x2c, 100); putfloat(address+0x30, 25);
        put16(address+0x38, 0xffe0); put16(address+0x3a, 777);
    }
    uint32_t run(bool upload = true) {
        if (upload) for (size_t i = 0; i < n64.size(); ++i) ram[i] = n64[i ^ 3];
        const auto before = ram;
        std::memset(&sample, 0xcc, sizeof(sample));
        inside_reader = true;
        const auto result = ghost_capture_read(&reader, &config, &frame, &sample);
        inside_reader = false;
        check(ram == before, "reader wrote RDRAM");
        check(sample.status == result, "status mismatch");
        check(sample.sequence == frame.sequence && sample.epoch == frame.epoch, "metadata lost");
        check(zero(sample.clock), "capture derived or retained clock evidence");
        ++frame.sequence; ++frame.dlist_serial;
        return result;
    }
    void pose_refusal(uint32_t expected, uint8_t reason = 0) {
        check(run() == expected, "wrong pose refusal");
        check(sample.valid_fields == (GC_VALID_CLOCK | GC_VALID_CAMERA | GC_VALID_ACTION | GC_VALID_WARP), "pose refusal validity");
        check(cleared(sample.pose, sample.pose.refusal), "refused pose retained bytes or no reason");
        check(!reason || sample.pose.refusal == reason, "wrong refusal reason");
    }
    void camera_refusal(uint32_t expected) {
        check(run() == expected, "wrong camera refusal");
        check(sample.valid_fields == (GC_VALID_CLOCK | GC_VALID_POSE | GC_VALID_ACTION | GC_VALID_WARP), "camera refusal validity");
        check(zero(sample.camera), "refused camera retained bytes");
    }
};

int main(int argc, char **argv) {
    check(argc == 3, "usage: ghost_capture_host config scenario");
    Fixture f(argv[1]);
    const std::string mode(argv[2]);
    if (mode == "basic") {
        check(f.run() == GC_OK && f.sample.valid_fields == 31, "valid capture failed");
        const auto &s = f.sample;
        check(s.version == GC_VERSION && s.live_action == 0x0c400201 && s.pose.action == s.live_action, "live action endian");
        check(s.global_timer == 0x12345678 && s.area_update_counter == 0x8123 && s.igt == 0xffe1, "clock endian");
        check(s.pose.angles[0] == -15 && s.pose.angles[1] == 12345 && s.pose.angles[2] == -32767, "angle endian");
        check(s.pose.position[2] == 22 && s.pose.scale[1] == 1.25f, "float order");
        check(s.pose.animation_id == 197 && s.pose.animation_frame == 7 && s.pose.anim_y_translation == 189, "animation offsets");
        check(s.pose.cap_state == 2 && s.pose.eye_state == 3 && s.pose.punch_state == 0x82, "body bytes");
        check(s.pose.torso_angles[1] == 101 && s.pose.head_angles[2] == 202, "body angle meaning");
        check(s.camera.position[0] == 600 && s.camera.focus[1] == 100 && s.camera.roll == -32
              && s.camera.roll_screen == 777 && s.camera.fov == 45, "graph camera offsets");
        const auto first_pose = s.pose;
        check(f.run() == GC_OK && std::memcmp(&first_pose, &f.sample.pose, sizeof(first_pose)) == 0, "repeat changed");
        // Post-callback capState is per occurrence, including a hatless first
        // frame, Ukiki stealing it and Mario recovering it. Raw flags stay
        // unchanged here; they must not supply a default over the drawn state.
        Fixture hatless(argv[1]);hatless.put8(Fixture::body+4, 1);
        check(hatless.run() == GC_OK && hatless.sample.pose.cap_state == 1,
              "hatless recording first frame used a capped default");
        for (uint8_t cap : {uint8_t(1), uint8_t(0), uint8_t(1), uint8_t(0), uint8_t(2)}) {
            f.put8(Fixture::body+4, cap);
            check(f.run() == GC_OK && f.sample.pose.cap_state == cap,
                  "hat loss/return reused an earlier cap state");
        }
        f.putfloat(Fixture::camera+0x1c, 6000); // final graph pause-zoom, no Lakitu dependency
        check(f.run() == GC_OK && f.sample.camera.position[0] == 6000, "camera cache was stale");
    } else if (mode == "swizzle") {
        f.ram = f.n64; f.frame.rdram = f.ram.data();
        check(f.run(false) != GC_OK && !(f.sample.valid_fields & GC_VALID_POSE), "wrong byte order accepted");
        f.config.ram_order = 99;
        check(f.run() == GC_BAD_CONFIG && f.sample.valid_fields == 0, "unknown byte order accepted");
    } else if (mode == "bounds") {
        f.put32(f.config.mario_object, f.config.object_pool+1); f.pose_refusal(GC_BAD_POINTER);
        f.put32(f.config.mario_object, f.config.object_pool);
        f.put32(f.config.mario_state+0x98, Fixture::base+f.config.rdram_bytes-4); f.pose_refusal(GC_BAD_POINTER);
        f.put32(f.config.mario_state+0x98, Fixture::body);
        f.put32(Fixture::area+4, 0xfffffffcu); f.camera_refusal(GC_BAD_POINTER);
        f.frame.rdram_bytes -= 4;
        check(f.run() == GC_BAD_CONFIG && f.sample.valid_fields == 0, "short RAM span accepted");
    } else if (mode == "graph") {
        const auto a = Fixture::base+0x15000, b = a+0x40;
        f.node(a, 0x0a, Fixture::perspective); f.node(b, 0x0a, Fixture::perspective);
        f.put32(Fixture::perspective+16, a); f.put32(a+8, b); f.put32(b+8, b);
        f.camera_refusal(GC_BAD_GRAPH);
        f.put32(Fixture::perspective+16, Fixture::camera);
        f.put32(Fixture::camera+12, Fixture::root); f.camera_refusal(GC_BAD_GRAPH);
        f.put32(Fixture::camera+12, Fixture::perspective); f.put32(Fixture::camera+8, 0);
        f.camera_refusal(GC_BAD_GRAPH);
    } else if (mode == "duplicate") {
        const auto other = Fixture::base+0x15000;
        f.camera_node(other, Fixture::perspective);
        f.put32(Fixture::camera+8, other); f.put32(other+8, Fixture::camera);
        f.camera_refusal(GC_AMBIGUOUS_CAMERA);
        f.put16(other+2, 0);
        check(f.run() == GC_OK, "inactive camera created ambiguity");
        f.put16(Fixture::camera+2, 0); f.camera_refusal(GC_NO_CAMERA);
    } else if (mode == "limits") {
        const auto start = Fixture::base+0x18000;
        f.put32(Fixture::perspective+16, start);
        for (unsigned i = 0; i < GC_GRAPH_NODE_LIMIT; ++i) {
            const auto at = start+i*0x40;
            f.node(at, 0x0a, Fixture::perspective);
            f.put32(at+8, i+1 == GC_GRAPH_NODE_LIMIT ? start : at+0x40);
        }
        f.camera_refusal(GC_GRAPH_LIMIT);
        for (unsigned i = 0; i < GC_GRAPH_DEPTH_LIMIT+1; ++i) {
            const auto at = start+i*0x40;
            f.node(at, 0x0a, i ? at-0x40 : Fixture::perspective);
            f.put32(at+16, at+0x40);
        }
        f.camera_refusal(GC_GRAPH_LIMIT);
    } else if (mode == "skip_objects") {
        const auto object = Fixture::base+0x15000;
        f.node(object, 0x29, Fixture::perspective); f.put32(object+16, 0xfffffffc);
        f.put32(object+8, Fixture::camera); f.put32(Fixture::camera+8, object);
        f.put32(Fixture::perspective+16, object);
        check(f.run() == GC_OK, "object-parent subtree was traversed");
        f.put16(object, 0x18); check(f.run() == GC_OK, "object subtree was traversed");
        f.put16(object, 0x12e); check(f.run() == GC_OK, "held-object subtree was traversed");
    } else if (mode == "values") {
        f.put32(f.config.object_pool+0x20, 0x7fc00000); f.pose_refusal(GC_BAD_VALUE, GC_REFUSED_TRANSFORM);
        f.putfloat(f.config.object_pool+0x20, 20); f.putfloat(f.config.object_pool+0x2c, 0);
        f.pose_refusal(GC_BAD_VALUE, GC_REFUSED_TRANSFORM);
        f.putfloat(f.config.object_pool+0x2c, 1); f.put32(Fixture::camera+0x1c, 0x7f800000); f.camera_refusal(GC_BAD_VALUE);
        f.putfloat(Fixture::camera+0x1c, 600); f.put16(f.config.object_pool+0x40, 30);
        f.pose_refusal(GC_BAD_VALUE, GC_REFUSED_ANIMATION_RANGE);
        f.put16(f.config.object_pool+0x40, 7); f.put8(Fixture::body+5, 9); f.pose_refusal(GC_BAD_VALUE, GC_REFUSED_BODY_STATE);
        f.put8(Fixture::body+5, 3); f.put16(f.config.object_pool+0x42, 1);
        f.pose_refusal(GC_BAD_VALUE, GC_REFUSED_ANIMATION_STALE);
        // act_disappeared: an inactive Mario is skipped by the renderer, so
        // his animation stays at set_mario_animation's startFrame-1 and his
        // animTimer is stale. Hidden, it is a valid pose that draws nothing.
        f.put16(f.config.object_pool+0x40, 0xffff); f.put16(f.config.object_pool+2, 0x20);
        check(f.run() == GC_OK && f.sample.pose.hidden == 1 && f.sample.pose.animation_frame == -1
              && f.sample.pose.root_kind == GC_ROOT_TRS && !f.sample.pose.refusal, "disappeared Mario refused");
        f.put16(f.config.object_pool+2, 0x21); f.pose_refusal(GC_BAD_VALUE, GC_REFUSED_ANIMATION_RANGE);
        f.put16(f.config.object_pool+0x40, 7); f.put16(f.config.object_pool+0x42, 0x8123);
        {
            // A BLJ press: ACT_LONG_JUMP wrote animID -1 and the air step
            // landed that frame, so curAnim (Mario's DMA buffer) still holds
            // the land animation it last loaded. The DMA list names it.
            const uint32_t list = Fixture::base + 0x15000, table = Fixture::base + 0x15100, source = 0x004ec000;
            const auto state = f.config.mario_state;
            f.put16(f.config.object_pool+0x38, 0xffff);
            f.pose_refusal(GC_BAD_VALUE, GC_REFUSED_ANIMATION_RANGE);  // no list: unprovable
            f.put32(table, 209); f.put32(table+4, source);
            for (uint32_t i = 0; i < 209; ++i) {f.put32(table+8+8*i, 0x100*i); f.put32(table+12+8*i, 0x100);}
            f.put32(list, table); f.put32(list+4, source + 0x100*18); f.put32(list+8, Fixture::animation);
            f.put32(state+0xa0, list);
            check(f.run() == GC_OK && f.sample.pose.animation_id == 18 && f.sample.pose.animation_frame == 7
                  && !f.sample.pose.refusal, "landed jump's loaded animation not recovered");
            f.put32(list+8, Fixture::animation + 0x100);  // curAnim is not Mario's buffer
            f.pose_refusal(GC_BAD_VALUE, GC_REFUSED_ANIMATION_RANGE);
            f.put32(list+8, Fixture::animation); f.put32(table+8+8*19, 0x100*18);  // two entries match
            f.pose_refusal(GC_BAD_VALUE, GC_REFUSED_ANIMATION_RANGE);
            f.put32(table+8+8*19, 0x100*19); f.put32(table, 210);  // more than Mario's animations
            f.pose_refusal(GC_BAD_VALUE, GC_REFUSED_ANIMATION_RANGE);
            f.put32(table, 209); f.put32(list, Fixture::base + f.config.rdram_bytes - 4);  // table off RAM
            f.pose_refusal(GC_BAD_VALUE, GC_REFUSED_ANIMATION_RANGE);
            f.put32(list, table); f.put16(f.config.object_pool+0x38, 0xfffe);  // only -1 is recovered
            f.pose_refusal(GC_BAD_VALUE, GC_REFUSED_ANIMATION_RANGE);
            f.put16(f.config.object_pool+0x38, 197);
            check(f.run() == GC_OK && f.sample.pose.animation_id == 197, "a set animation id was replaced");
        }
        f.put32(f.config.object_pool+0x3c, 0); f.pose_refusal(GC_BAD_POINTER, GC_REFUSED_ANIMATION);
    } else if (mode == "unsupported") {
        f.put32(f.config.mario_state+0x0c, 0x04008448); f.pose_refusal(GC_UNSUPPORTED_ROOT, GC_REFUSED_FLOOR);
        f.put32(f.config.mario_state+0x0c, 0x00000386); f.pose_refusal(GC_UNSUPPORTED_ROOT, GC_REFUSED_FLOOR);
        f.put32(f.config.mario_state+0x0c, 0x0c400201); f.put32(f.config.mario_state+0x7c, 123);
        f.pose_refusal(GC_UNSUPPORTED_HELD, GC_REFUSED_HELD);
        f.put32(f.config.mario_state+0x7c, 0); f.put32(f.config.mario_state+0x84, 123);
        f.pose_refusal(GC_UNSUPPORTED_HELD, GC_REFUSED_HELD);
        f.put32(f.config.mario_state+0x84, 0); f.put32(f.config.object_pool+0x8c, 0x800);
        f.pose_refusal(GC_UNSUPPORTED_ROOT, GC_REFUSED_FLOOR);
        f.put32(f.config.object_pool+0x8c, 0); f.put32(f.config.object_pool+0x50, 0x1234);
        f.pose_refusal(GC_UNSUPPORTED_ROOT, GC_REFUSED_THROW_MATRIX);
        f.put32(f.config.object_pool+0x50, 0); f.put16(f.config.object_pool+2, 0x25);
        f.pose_refusal(GC_UNSUPPORTED_ROOT, GC_REFUSED_BILLBOARD);
        f.put16(f.config.object_pool+2, 0x21); f.put8(Fixture::control, 6); f.pose_refusal(GC_UNSUPPORTED_HEAD, GC_REFUSED_HEAD);
    } else if (mode == "action") {
        // A live grab must hide the ghost even if Mario's graphics root or
        // body is unsupported. Body action is independently post-callback.
        f.put32(f.config.mario_state+0x0c, 0x00001302);
        f.put32(f.config.mario_state+0x7c, 123);
        f.pose_refusal(GC_UNSUPPORTED_HELD);
        check(f.sample.live_action == 0x00001302, "held root lost grab action");
        f.put32(f.config.mario_state+0x0c, 0x1234abcd);
        f.put32(f.config.mario_state+0x98, 0);
        f.pose_refusal(GC_BAD_POINTER);
        check(f.sample.live_action == 0x1234abcd, "bad body lost current action");
        f.put32(Fixture::area+4, 0);
        check(f.run() == GC_BAD_POINTER
              && f.sample.valid_fields == (GC_VALID_CLOCK | GC_VALID_ACTION | GC_VALID_WARP)
              && f.sample.live_action == 0x1234abcd, "camera failure lost action");
        f.put32(Fixture::area+4, Fixture::root);
        f.put32(f.config.mario_state+0x0c, 0);
        check(f.run() == GC_BAD_VALUE && f.sample.live_action == 0
              && !(f.sample.valid_fields & GC_VALID_ACTION), "zero action retained old action");
        f.put32(f.config.mario_state+0x0c, 0x00001302);
        f.put32(f.config.mario_state+0x90, Fixture::area+0x40);
        check(f.run() == GC_BAD_VALUE && f.sample.live_action == 0
              && !(f.sample.valid_fields & GC_VALID_ACTION), "foreign area action accepted");
    } else if (mode == "warp") {
        const auto op_address = f.config.pending_warp_op, timer_address = f.config.delayed_warp_timer;
        f.put16(op_address, 0xabcd); f.put16(timer_address, 0x8123);
        check(f.run() == GC_OK && f.sample.pending_warp_op == 0xabcd
              && f.sample.delayed_warp_timer == -32477, "warp signed/unsigned halfword endian");
        f.put16(op_address, 0xffff); f.put16(timer_address, 0x8000);
        check(f.run() == GC_OK && f.sample.pending_warp_op == 65535
              && f.sample.delayed_warp_timer == -32768, "warp extrema were narrowed");
        f.put16(op_address, 0); f.put16(timer_address, 0x7fff);
        check(f.run() == GC_OK && f.sample.pending_warp_op == 0
              && f.sample.delayed_warp_timer == 32767, "warp zero/max timer were narrowed");
        f.config.pending_warp_op = 0;
        check(f.run() == GC_BAD_VALUE && f.sample.valid_fields == 15
              && f.sample.pending_warp_op == 0 && f.sample.delayed_warp_timer == 0
              && f.sample.pose.root_kind == GC_ROOT_TRS, "missing warp erased pose or reused timer");
        f.config.pending_warp_op = op_address; f.config.delayed_warp_timer = 0;
        check(f.run() == GC_BAD_VALUE && f.sample.valid_fields == 15
              && f.sample.pending_warp_op == 0 && f.sample.delayed_warp_timer == 0,
              "missing countdown retained warp evidence");
        f.config.delayed_warp_timer = timer_address;
        f.put16(op_address, 3); f.put16(timer_address, 1);
        f.put32(f.config.mario_state+0x7c, 123); f.pose_refusal(GC_UNSUPPORTED_HELD);
        check(f.sample.pending_warp_op == 3 && f.sample.delayed_warp_timer == 1,
              "unsupported pose erased independent warp evidence");
        f.put32(f.config.current_area_pointer, 0);
        check(f.run() == GC_BAD_POINTER && f.sample.valid_fields == (GC_VALID_CLOCK | GC_VALID_WARP)
              && f.sample.pending_warp_op == 3, "area unload erased raw warp evidence");
    } else if (mode == "epochs") {
        check(f.run() == GC_OK, "initial capture failed");
        --f.frame.sequence; --f.frame.dlist_serial;
        check(f.run() == GC_STALE_SEQUENCE && f.sample.valid_fields == 0
              && zero(f.sample.pose) && f.sample.live_action == 0
              && !f.sample.pending_warp_op && !f.sample.delayed_warp_timer, "duplicate retained old state");
        check(f.run() == GC_OK, "fresh sequence did not recover");
        f.put32(f.config.global_timer, 10);
        check(f.run() == GC_RESET_REQUIRED && f.sample.valid_fields == 0
              && f.sample.live_action == 0, "backwards clock accepted");
        f.put32(f.config.global_timer, 0x12345679);
        check(f.run() == GC_RESET_REQUIRED, "reset latch vanished without epoch");
        ++f.frame.epoch; f.frame.sequence = f.frame.dlist_serial = 1;
        f.put32(f.config.global_timer, 3);
        check(f.run() == GC_OK, "new epoch did not reset");
        ghost_capture_reset(&f.reader); f.frame.sequence = f.frame.dlist_serial = 1;
        check(f.run() == GC_OK, "explicit reset did not recover");
    } else if (mode == "transitions") {
        check(f.run() == GC_OK, "initial capture failed");
        f.put32(f.config.current_area_pointer, 0);
        check(f.run() == GC_BAD_POINTER && cleared(f.sample.pose, GC_REFUSED_AREA) && zero(f.sample.camera)
              && f.sample.live_action == 0 && !(f.sample.valid_fields & GC_VALID_ACTION), "unload retained data");
        f.put32(f.config.current_area_pointer, Fixture::area); f.put8(Fixture::area+1, 0);
        check(f.run() == GC_BAD_VALUE && zero(f.sample.camera), "inactive area retained camera");
        f.put8(Fixture::area+1, 1); f.put16(f.config.current_area, 2);
        check(f.run() == GC_BAD_VALUE && cleared(f.sample.pose, GC_REFUSED_AREA), "area mismatch retained data");
        f.put16(f.config.current_area, 1); f.put16(f.config.object_pool+2, 0x31);
        check(f.run() == GC_OK && f.sample.pose.hidden == 1, "hidden flag lost");
    } else if (mode == "sound") {
        // The US play_sound words the spike qualified in both stock and Usamune.
        const uint32_t code[12] = {0x3c068033, 0x24c62f44, 0x90c20000, 0x3c0f8036, 0x25ef1498,
            0x000270c0, 0x01cf1821, 0x24580001, 0xac640000, 0xac650004, 0x03e00008, 0xa0d80000};
        check(f.config.sound_code == 0x8031eb00 && f.config.sound_producer == 0x80332f44
              && f.config.sound_ring == 0x80361498 && f.config.sound_consumer == 0x80332f40,
              "Python layout lost the sound roots");
        for (unsigned i = 0; i < 12; ++i) f.put32(f.config.sound_code + 4*i, code[i]);
        const gc_layout roots = f.config; // the scenario later disables config roots
        const uint32_t mario = f.config.object_pool + 0x54, other = f.config.object_pool + 0x260 + 0x54;
        uint32_t timer = 0x12345678, producer = 250;
        const auto request = [&](uint32_t bits, uint32_t source) {
            f.put32(roots.sound_ring + 8*(producer & 255), bits);
            f.put32(roots.sound_ring + 8*(producer & 255) + 4, source);
            f.put8(roots.sound_producer, uint8_t(++producer));
        };
        const auto frame = [&](uint32_t step = 1) {
            timer += step; f.put32(f.config.global_timer, timer); f.put8(roots.sound_consumer, 3);
            check(f.run() == GC_OK, "sound changed the pose status");
        };
        const auto sounds = [&](std::initializer_list<uint32_t> expected) {
            const auto &s = f.sample.sound;
            if (!(f.sample.valid_fields & GC_VALID_SOUND) || s.count != expected.size()) return false;
            unsigned i = 0; for (auto bits : expected) if (s.bits[i++] != bits) return false;
            for (; i < GC_SOUND_EVENTS; ++i) if (s.bits[i]) return false;
            return true;
        };
        f.put8(f.config.sound_producer, uint8_t(producer)); f.put32(f.config.global_timer, timer);
        check(f.run() == GC_OK && f.sample.valid_fields == 31 && zero(f.sample.sound), "first frame has no baseline");
        request(0x24228080, mario); request(0x11111111, other); request(0x30008081, mario);
        frame();
        check(sounds({0x24228080, 0x30008081}) && f.sample.sound.producer == 253
              && f.sample.sound.consumer == 3 && !f.sample.sound.flags, "Mario's requests in issue order");
        frame(); check(sounds({}), "silent frame is valid and empty");
        request(0x44440000, other); frame(); check(sounds({}), "another object's sound was kept");
        // Producer wraps through zero inside one frame.
        request(0x1, mario); request(0x2, mario); request(0x3, mario); request(0x4, mario);
        frame(); check(sounds({1, 2, 3, 4}) && f.sample.sound.producer == 2, "ring wrap lost order");
        request(0x5, mario); frame(2);
        check(!(f.sample.valid_fields & GC_VALID_SOUND) && zero(f.sample.sound), "skipped frame attributed sounds");
        frame(); check(sounds({}), "baseline did not recover after a gap");
        for (uint32_t i = 0; i < 10; ++i) request(0x100 + i, mario);
        frame();
        check(sounds({0x100, 0x101, 0x102, 0x103, 0x104, 0x105, 0x106, 0x107})
              && f.sample.sound.flags == GC_SOUND_OVERFLOW, "overflow not bounded and flagged");
        f.put32(f.config.sound_code + 4, 0x24c62f48); request(0x6, mario); frame();
        check(!(f.sample.valid_fields & GC_VALID_SOUND) && zero(f.sample.sound), "different producer code accepted");
        f.put32(f.config.sound_code + 4, code[1]); frame();
        check(!(f.sample.valid_fields & GC_VALID_SOUND), "requalified code reused an old baseline");
        frame(); check(sounds({}), "requalified code did not recover");
        f.put32(f.config.mario_object, f.config.object_pool + 1); request(0x7, mario);
        timer += 1; f.put32(f.config.global_timer, timer); f.run();
        check(!(f.sample.valid_fields & GC_VALID_SOUND), "sound attributed without Mario's object");
        f.put32(f.config.mario_object, f.config.object_pool); frame(); check(sounds({}), "recovered after Mario returned");
        const auto layout = roots;
        f.config.sound_code = f.config.sound_producer = f.config.sound_consumer = f.config.sound_ring = 0;
        request(0x8, mario); frame(); check(!(f.sample.valid_fields & GC_VALID_SOUND), "unavailable sound reported valid");
        f.config = layout; f.config.sound_ring += 4;
        check(f.run() == GC_BAD_CONFIG, "misaligned ring accepted");
        f.config = layout; f.config.sound_code = 0;
        check(f.run() == GC_BAD_CONFIG, "partial sound configuration accepted");
    } else if (mode == "held") {
        // The legacy TRS reader still refuses a holding Mario, so it never
        // records a held object; the executed-root reader does (root host).
        check(f.config.behaviour_base == 0x800EB180, "Python layout lost the behaviour base");
        f.put32(f.config.mario_state + 0x7c, f.config.object_pool + 0x260);
        check(f.run() == GC_UNSUPPORTED_HELD && !(f.sample.valid_fields & (GC_VALID_POSE | GC_VALID_HELD))
              && zero(f.sample.held), "TRS reader recorded a held object");
    } else if (mode == "arguments") {
        f.config.version = GC_VERSION - 1;
        check(f.run() == GC_BAD_CONFIG && f.sample.valid_fields == 0 && f.sample.live_action == 0,
              "old capture ABI accepted");
        f.config.version = GC_VERSION;
        const auto clock_config = f.config.clock;
        f.config.clock.version = 99; check(f.run() == GC_BAD_CONFIG, "unknown clock config accepted");
        f.config.clock = clock_config; f.config.clock.area_window = 0;
        check(f.run() == GC_BAD_CONFIG, "invalid shared clock window accepted");
        f.config.clock = clock_config; f.config.clock.castle_levels[GCC_LIST_MAX-1] = 6;
        check(f.run() == GC_BAD_CONFIG, "unused shared clock slot accepted");
        f.config.clock = clock_config;
        const auto op = f.config.pending_warp_op, timer = f.config.delayed_warp_timer;
        f.config.pending_warp_op |= 1; check(f.run() == GC_BAD_CONFIG, "unaligned warp root accepted");
        f.config.pending_warp_op = op; f.config.delayed_warp_timer = Fixture::base + f.config.rdram_bytes;
        check(f.run() == GC_BAD_CONFIG, "outside countdown root accepted");
        f.config.delayed_warp_timer = timer;
        f.config.global_timer |= 1; check(f.run() == GC_BAD_CONFIG, "unaligned config accepted");
        f.config.global_timer &= ~1u; f.frame.epoch = 0; check(f.run() == GC_BAD_ARGUMENT, "zero epoch accepted");
        std::memset(&f.sample, 0xcc, sizeof(f.sample));
        check(ghost_capture_read(nullptr, nullptr, nullptr, &f.sample) == GC_BAD_ARGUMENT
              && f.sample.valid_fields == 0 && zero(f.sample.pose)
              && f.sample.live_action == 0, "bad args retained state");
    } else check(false, "unknown scenario");
    std::printf("ghost capture passed: %s; config=%zu sample=%zu\n", argv[2], sizeof(gc_layout), sizeof(gc_sample));
    return 0;
}
