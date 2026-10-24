// Offline E0 probe: walk one frame input's graphics task with the production
// closure walker, report what it reads and what the walk and copy cost.
//
//   closure_probe <frame_dir> <closure.json> [iterations]
//
// Writes the merged closure (scene-lab-closure/1) for replay.py poison and
// prints one JSON line of statistics. Timing walks the same RDRAM repeatedly,
// so it is a warm-cache CPU cost, not the in-process producer measurement.
#define NOMINMAX
#define _CRT_SECURE_NO_WARNINGS
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <vector>
#include <algorithm>
#include "scene_closure.h"

static std::vector<uint8_t> read_file(const char *dir, const char *name) {
    char path[MAX_PATH];
    snprintf(path, sizeof path, "%s\\%s", dir, name);
    std::vector<uint8_t> data;
    FILE *file = fopen(path, "rb");
    if (!file) return data;
    fseek(file, 0, SEEK_END);
    long size = ftell(file);
    fseek(file, 0, SEEK_SET);
    data.resize(size > 0 ? (size_t)size : 0);
    if (size > 0 && fread(data.data(), 1, data.size(), file) != data.size()) data.clear();
    fclose(file);
    return data;
}

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: closure_probe <frame_dir> <closure.json> [iterations]\n"); return 2; }
    const int iterations = argc > 3 ? atoi(argv[3]) : 200;
    std::vector<uint8_t> rdram = read_file(argv[1], "rdram.bin"), dmem = read_file(argv[1], "dmem.bin");
    if (rdram.empty() || dmem.size() != 0x1000) { fprintf(stderr, "missing rdram.bin/dmem.bin\n"); return 2; }
    uint32_t task[scene_closure::kTaskWords];
    memcpy(task, dmem.data() + 0xFC0, sizeof task);

    static scene_closure::Walker walker;
    const uint32_t size = (uint32_t)rdram.size();
    // SCENE_LAB_TRACE=<file>: one line per handler read on the first walk.
    FILE *trace = getenv("SCENE_LAB_TRACE") ? fopen(getenv("SCENE_LAB_TRACE"), "wb") : nullptr;
    if (trace) {
        walker.trace_context = trace;
        walker.trace = [](void *context, uint32_t begin, uint32_t bytes, scene_closure::Kind kind) {
            fprintf((FILE *)context, "%u %06x %u\n", (unsigned)kind, begin, bytes);
        };
    }
    uint32_t refusals = walker.walk(rdram.data(), size, task, 1u << 20);
    if (trace) { fclose(trace); walker.trace = nullptr; }

    struct Run { uint32_t begin, end; };
    std::vector<Run> runs;
    walker.runs([&](uint32_t begin, uint32_t end) { runs.push_back({begin, end}); });
    uint64_t closure = 0;
    for (const Run &run : runs) closure += run.end - run.begin;

    // Producer-shaped cost: walk, then copy every run into a preallocated slot.
    std::vector<uint8_t> slot((size_t)closure + 64);
    std::vector<double> walk_us, copy_us;
    LARGE_INTEGER frequency, a, b, c;
    QueryPerformanceFrequency(&frequency);
    for (int i = 0; i < iterations; ++i) {
        QueryPerformanceCounter(&a);
        walker.walk(rdram.data(), size, task, 1u << 20);
        QueryPerformanceCounter(&b);
        size_t at = 0;
        walker.runs([&](uint32_t begin, uint32_t end) {
            memcpy(slot.data() + at, rdram.data() + begin, end - begin);
            at += end - begin;
        });
        QueryPerformanceCounter(&c);
        walk_us.push_back(1e6 * (b.QuadPart - a.QuadPart) / frequency.QuadPart);
        copy_us.push_back(1e6 * (c.QuadPart - b.QuadPart) / frequency.QuadPart);
    }
    auto pct = [](std::vector<double> v, double p) {
        if (v.empty()) return 0.0;
        std::sort(v.begin(), v.end());
        return v[std::min(v.size() - 1, (size_t)(p * (v.size() - 1) + 0.5))];
    };

    FILE *out = fopen(argv[2], "wb");
    if (!out) { fprintf(stderr, "cannot write %s\n", argv[2]); return 2; }
    fprintf(out, "{\"schema\":\"scene-lab-closure/1\",\"refusals\":%u,\"ranges\":[", refusals);
    for (size_t i = 0; i < runs.size(); ++i)
        fprintf(out, "%s{\"begin\":%u,\"end\":%u}", i ? "," : "", runs[i].begin, runs[i].end);
    fprintf(out, "]}\n");
    fclose(out);

    static const char *kinds[] = {"ucode", "dl", "vtx", "mtx", "light", "viewport", "texture", "tlut"};
    const scene_closure::Stats &s = walker.stats;
    printf("{\"refusals\":%u,\"commands\":%u,\"max_depth\":%u,\"segments_set\":%u,\"color_images\":%u,"
           "\"runs\":%zu,\"closure_bytes\":%llu,", refusals, s.commands, s.max_depth, s.segments_set,
           s.color_images, runs.size(), (unsigned long long)closure);
    printf("\"kinds\":{");
    for (int k = 0; k < scene_closure::KIND_COUNT; ++k)
        printf("%s\"%s\":{\"reads\":%u,\"bytes\":%llu}", k ? "," : "", kinds[k], s.reads[k],
               (unsigned long long)s.bytes[k]);
    printf("},\"opcodes\":{");
    bool first = true;
    for (int op = 0; op < 256; ++op)
        if (s.opcodes[op]) { printf("%s\"%02x\":%u", first ? "" : ",", op, s.opcodes[op]); first = false; }
    printf("},\"walk_us\":{\"p50\":%.2f,\"p99\":%.2f,\"max\":%.2f},\"copy_us\":{\"p50\":%.2f,\"p99\":%.2f,\"max\":%.2f}}\n",
           pct(walk_us, 0.5), pct(walk_us, 0.99), pct(walk_us, 1.0),
           pct(copy_us, 0.5), pct(copy_us, 0.99), pct(copy_us, 1.0));
    return refusals ? 1 : 0;
}
