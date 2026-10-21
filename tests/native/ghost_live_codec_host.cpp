/* CPU witness: real wire decoder + gs_record + shipped pose/scene conversion.
 * The model shell exposes its inputs and emits a triangle; it is not a ROM
 * model, GL compositor, network, playback scheduler or pixel-quality witness.
 */
#include "ghost_live_codec.h"
#include "ghost_pose.h"
#include "ghost_scene.h"
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <vector>

namespace {
unsigned checks = 0, evaluations = 0;
sg_pose applied{};
void require(bool condition, const char *message) {
    ++checks;
    if (!condition) { std::fprintf(stderr, "FAILED %u: %s\n", checks, message); std::exit(1); }
}
bool near(float a, float b) { return std::fabs(a - b) < 0.01f; }
void incarnation(uint8_t (&out)[16], unsigned seat = 0) {
    for (unsigned i = 0; i < 16; ++i) out[i] = uint8_t(i);
    out[15] = uint8_t(15 + seat); // authorized test seats; never taken from packets
}
bool cleared(const glive_value &value) {
    const glive_value empty{};
    return std::memcmp(&value, &empty, sizeof(value)) == 0;
}
const char *name(ghost_live::DecodeResult r) {
    using R = ghost_live::DecodeResult;
    switch (r) {
    case R::ok: return "ok";
    case R::bad_argument: return "bad_argument";
    case R::bad_size: return "bad_size";
    case R::bad_header: return "bad_header";
    case R::wrong_incarnation: return "wrong_incarnation";
    case R::bad_sample: return "bad_sample";
    case R::bad_pose: return "bad_pose";
    case R::bad_camera: return "bad_camera";
    case R::bad_clock: return "bad_clock";
    case R::bad_held: return "bad_held";
    case R::bad_pad: return "bad_pad";
    }
    return "unknown";
}
std::vector<uint8_t> read_file(const char *path) {
    std::ifstream stream(path, std::ios::binary | std::ios::ate);
    require(bool(stream), "open disposable wire fixture");
    const auto end = stream.tellg();
    require(end >= 0 && end <= std::streamoff(GLIVE_PACKET_BYTES * 4096u), "bounded wire fixture");
    std::vector<uint8_t> bytes(static_cast<size_t>(end));
    stream.seekg(0);
    if (!bytes.empty()) stream.read(reinterpret_cast<char *>(bytes.data()), std::streamsize(bytes.size()));
    require(bool(stream), "read disposable wire fixture");
    return bytes;
}
gs_record record(const glive_value &value) {
    gs_record out{};
    out.bytes = sizeof(out); out.version = GS_VERSION; out.configuration = value.header.configuration;
    out.sample = value.sample;
    return out; // historical world stays empty; only the local world qualifies drawing
}
void argument_checks() {
    using R = ghost_live::DecodeResult;
    uint8_t expected[16]{}; incarnation(expected);
    glive_value out{}; std::memset(&out, 0xcc, sizeof(out));
    // An unreadable address proves the original size is checked before copying.
    const void *unreadable = reinterpret_cast<const void *>(uintptr_t(1));
    require(ghost_live::decode(unreadable, 423, expected, out) == R::bad_size && cleared(out), "short before copy");
    std::memset(&out, 0xcc, sizeof(out));
    require(ghost_live::decode(unreadable, 425, expected, out) == R::bad_size && cleared(out), "oversized before copy");
    require(ghost_live::decode(nullptr, 424, expected, out) == R::bad_argument && cleared(out), "null packet refused");
    uint8_t packet[424]{};
    require(ghost_live::decode(packet, 424, nullptr, out) == R::bad_argument && cleared(out), "null admission refused");
    std::memset(expected, 0, sizeof(expected));
    require(ghost_live::decode(packet, 424, expected, out) == R::bad_argument && cleared(out), "empty admission refused");
    std::printf("{\"arguments\":true,\"checks\":%u}\n", checks);
}
void pose_checks(const std::vector<uint8_t> &bytes, unsigned count) {
    using R = ghost_live::DecodeResult;
    require(count == 1 || count == GLIVE_MAX_PEERS, "one or three independent decoded values");
    require(bytes.size() == 3 * GLIVE_PACKET_BYTES, "three admitted fixture packets");
    glive_value values[GLIVE_MAX_PEERS]{};
    for (unsigned seat = 0; seat < count; ++seat) {
        uint8_t expected[16]{}; incarnation(expected, seat);
        require(ghost_live::decode(bytes.data() + seat * GLIVE_PACKET_BYTES, GLIVE_PACKET_BYTES,
                                  expected, values[seat]) == R::ok, "admitted native decode");
    }
    static ghost_pose::Scratch scratch[GLIVE_MAX_PEERS];
    sg_model *model = reinterpret_cast<sg_model *>(&applied); // transparent shell handle
    gc_sample local = values[0].sample;
    local.epoch = 99; local.sequence = local.dlist_serial = 999;
    local.valid_fields = GC_VALID_CLOCK | GC_VALID_CAMERA;
    local.camera.position[0] += 17; local.camera.focus[0] += 17;
    ghost::WorldPass world{};
    world.epoch = local.epoch; world.list_serial = local.dlist_serial; world.context_generation = 5;
    world.projection[0] = world.projection[5] = world.projection[10] = world.projection[15] = 1;
    world.target.screen_scale[0] = world.target.screen_scale[1] = 1;
    for (unsigned seat = 0; seat < count; ++seat) {
        const auto before = values[seat];
        const auto input = record(values[seat]);
        ghost_compositor::Pose pose{};
        require(ghost_pose::evaluate(model, input, local, world, 1, scratch[seat], pose,
                                    values[seat].header.rom_layout) == ghost_pose::Result::ok, "actual pose conversion");
        require(pose.count == 3 && pose.vertices == scratch[seat].vertices, "caller-owned independent pose output");
        require(applied.animation_id == input.sample.pose.animation_id
                && applied.animation_frame == input.sample.pose.animation_frame, "authentic animation retained");
        if (input.sample.pose.root_kind == GC_ROOT_SCALED_MODELVIEW) {
            require(applied.fields & SG_POSE_THROW_MATRIX, "authentic modelview reaches root conversion");
            for (unsigned a = 0; a < 3; ++a)
                require(near(applied.throw_matrix[12 + a], input.sample.pose.root_matrix[12 + a]
                             + input.sample.camera.position[a]), "recorded camera removed under transparent view shell");
        }
        require(!std::memcmp(&values[seat], &before, sizeof(before)), "decoded value immutable through evaluation");
        if (seat) require(pose.vertices != scratch[seat - 1].vertices, "seats do not alias scratch");
    }
    require(evaluations == count, "one model evaluation per admitted visible peer");

    // The exact shipped DDD connector rule, through decoder -> gs_record -> evaluate.
    auto tunnel = values[0];
    auto &s = tunnel.sample;
    s.level = 23; s.area = 1; s.pose.root_kind = GC_ROOT_TRS;
    s.pose.position[0] = 4100; s.pose.position[1] = -300; s.pose.position[2] = 200;
    for (float &axis : s.pose.scale) axis = 1;
    std::memset(s.pose.root_matrix, 0, sizeof(s.pose.root_matrix));
    glive_packet wire{}; wire.header = tunnel.header;
    std::memcpy(wire.core, &s, sizeof(wire.core)); wire.held = s.held; wire.pad = s.pad;
    uint8_t expected[16]{}; incarnation(expected);
    require(ghost_live::decode(&wire, sizeof(wire), expected, tunnel) == R::ok, "tunnel sample uses same decoder");
    local.level = 23; local.area = 2;
    ghost_compositor::Pose tunnel_pose{};
    require(ghost_scene::compatible(tunnel.sample, local), "existing scene admits shared connector");
    require(ghost_pose::evaluate(model, record(tunnel), local, world, 1, scratch[0], tunnel_pose,
                                tunnel.header.rom_layout) == ghost_pose::Result::ok, "actual cross-area evaluation");
    require(near(applied.position[0], 4100 - 8192.0f), "scene translation occurs exactly once");
    local.level = 24;
    require(ghost_pose::evaluate(model, record(tunnel), local, world, 1, scratch[0], tunnel_pose,
                                tunnel.header.rom_layout) == ghost_pose::Result::wrong_scene
            && !tunnel_pose.vertices && !tunnel_pose.count, "incompatible scene clears output");
    std::printf("{\"peers\":%u,\"evaluations\":%u,\"scene\":true,\"checks\":%u}\n", count, evaluations, checks);
}
}

extern "C" int32_t sg_model_view(sg_model *, const float *position, const float *, int16_t, float *out) {
    std::memset(out, 0, 16 * sizeof(float)); out[0] = out[5] = out[10] = out[15] = 1;
    for (unsigned a = 0; a < 3; ++a) out[12 + a] = -position[a];
    return SG_OK;
}
extern "C" int32_t sg_model_get_body_lights(sg_model *, sg_body_lights *out) {
    *out = {}; out->count = 1; out->lights[0] = {{255, 0, 0}, {127, 0, 0}, {40, 40, 40}};
    return SG_OK;
}
extern "C" int32_t sg_model_get_held_lights(sg_model *model, uint32_t, sg_body_lights *out) {
    return sg_model_get_body_lights(model, out);
}
extern "C" int32_t sg_model_evaluate(sg_model *, const sg_pose *pose, sg_mesh *mesh) {
    ++evaluations; applied = *pose;
    if (mesh->vertex_capacity < 3) return SG_CAPACITY;
    const float *root = pose->fields & SG_POSE_THROW_MATRIX ? pose->throw_matrix + 12 : pose->position;
    for (unsigned i = 0; i < 3; ++i) {
        for (unsigned a = 0; a < 3; ++a) {
            mesh->position[3 * i + a] = root[a] + ((i == 1 && a == 0) || (i == 2 && a == 1) ? 1.0f : 0.0f);
            mesh->normal[3 * i + a] = a == 1 ? 1.0f : 0.0f;
            mesh->color[3 * i + a] = a == 0 ? 1.0f : 0.0f;
        }
        mesh->uv[2 * i] = mesh->uv[2 * i + 1] = 0;
        mesh->material[i] = SG_MATERIAL_ALPHA_MIX;
    }
    mesh->vertex_count = mesh->held_first_vertex = 3; mesh->error_flags = 0;
    return SG_OK;
}

int main(int argc, char **argv) {
    require(argc >= 2, "witness mode supplied");
    if (!std::strcmp(argv[1], "arguments")) { argument_checks(); return 0; }
    require(argc >= 3, "wire file supplied");
    const auto bytes = read_file(argv[2]);
    if (!std::strcmp(argv[1], "pose1") || !std::strcmp(argv[1], "pose3")) {
        pose_checks(bytes, argv[1][4] == '1' ? 1u : 3u); return 0;
    }
    uint8_t expected[16]{}; incarnation(expected);
    if (!std::strcmp(argv[1], "decode")) {
        glive_value value{}; std::memset(&value, 0xcc, sizeof(value));
        std::vector<uint8_t> unaligned(bytes.size() + 1);
        if (!bytes.empty()) std::memcpy(unaligned.data() + 1, bytes.data(), bytes.size());
        const auto status = ghost_live::decode(unaligned.data() + 1, bytes.size(), expected, value);
        if (status == ghost_live::DecodeResult::ok && argc == 4) {
            std::ofstream out(argv[3], std::ios::binary);
            out.write(reinterpret_cast<const char *>(&value), sizeof(value)); require(bool(out), "write trusted output");
        }
        std::printf("{\"result\":\"%s\",\"cleared\":%s,\"value_bytes\":%zu,\"model_evaluations\":%u}\n",
                    name(status), cleared(value) ? "true" : "false", sizeof(value), evaluations);
        return 0;
    }
    require(!std::strcmp(argv[1], "batch") && argc == 4 && bytes.size() % GLIVE_PACKET_BYTES == 0,
            "bounded complete packet batch");
    std::ofstream output(argv[3], std::ios::binary);
    unsigned decoded = 0, known_pad = 0, held = 0;
    for (size_t at = 0; at < bytes.size(); at += GLIVE_PACKET_BYTES) {
        glive_value value{};
        require(ghost_live::decode(bytes.data() + at, GLIVE_PACKET_BYTES, expected, value)
                == ghost_live::DecodeResult::ok, "authentic fixture decoded");
        ++decoded; known_pad += bool(value.sample.valid_fields & GC_VALID_PAD);
        held += bool(value.sample.valid_fields & GC_VALID_HELD);
        output.write(reinterpret_cast<const char *>(&value), sizeof(value));
    }
    require(bool(output), "write decoded batch");
    std::printf("{\"decoded\":%u,\"known_pad\":%u,\"held\":%u,\"value_bytes\":%zu}\n",
                decoded, known_pad, held, sizeof(glive_value));
}
