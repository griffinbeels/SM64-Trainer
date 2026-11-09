/* The scene tap without Project64 (E2-live's offline proof): feeds frame
 * inputs (savestate_frames.py format) through the REAL producer in
 * plugin/gfxwrap/scene_tap.cpp, so a tap session recorded here is exactly what
 * the live tap would have written for that RDRAM: same walker, same regions,
 * same record format, same timings measured on this machine.
 *
 *   tap_offline_host <lab_root> [vi:]<frame_dir>...
 *   tap_offline_host <lab_root> --stdin                 the same frames, one command per stdin line
 *   tap_offline_host <lab_root> --live [--stdin] [command]...
 *
 * <lab_root> stands in for %LOCALAPPDATA%: the arming file must already be at
 * <lab_root>\SM64Trainer\scene-lab\armed.txt (tools/scene_lab/tap.py offline
 * writes it), and the session lands beside it. Only this host's build has the
 * one-shot mode (tap.py compiles it with SCENE_TAP_ONE_SHOT); the shipped
 * wrapper records only through the lease. Frames are fed in the order
 * given, one ProcessDList each; repeat a folder to repeat a task. A folder
 * prefixed vi: is one UpdateScreen instead (a VI with no new task), which is
 * where the tap's VI regions are copied. Exit 0 when
 * every frame was offered to an armed tap; the session's summary.txt says
 * what it recorded. With --stdin the first line is "header <dir>", then one
 * frame command per line ("frame <dir>", "vi <dir>", quit), each answered
 * "ok" once fed: a writer can delete a folder after its answer, but a folder
 * name is read once (the cache below), so each frame needs its own name.
 *
 * --live runs the live spool instead: no armed.txt, the producer's tap
 * thread watches <lab_root>\SM64Trainer\scene-lab\live\lease.txt (written by
 * tools/scene_lab/live_spool.py's Lease). Commands, as arguments or one per
 * stdin line with --stdin (each answered by one "ok ..." line):
 *   frame <dir> | vi <dir>    one ProcessDList / UpdateScreen (a bare <dir> or vi:<dir> too)
 *   poke                      check the lease now, drain, write rows: "ok 1" while capturing
 *   hold | release            stop / resume draining the ring (forces ring overflow)
 *   sleep <ms>
 *   run <count> <ms> <dir>... ProcessDList then UpdateScreen every <ms>, cycling the dirs,
 *                             the VI origin alternating like a double-buffered game
 *   quit
 * Every mode prints, at exit, the producer's cost per call as this host
 * measured it around each call: "task_us p50 p99 max n" and "vi_us ...".
 *
 *   tap_offline_host --spool-bench <session> <out_dir> <dedupe 0|1> <compress 0|1>
 *
 * feeds a recorded session's tap.bin through the live spool's writer as live
 * capture would have made it (presents without pictures, the first full copy
 * as the boot span from 0x400000, no other full copy) into <out_dir>, and
 * prints the bytes in and out, the session's length and the writer's time. */
#define NOMINMAX
#include <windows.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <algorithm>
#include <map>
#include <string>
#include <vector>
#include "scene_tap.h"
#include "scene_spool.h"

namespace {

std::vector<uint8_t> read_file(const std::string &path) {
    std::vector<uint8_t> data;
    FILE *file = fopen(path.c_str(), "rb");
    if (!file) return data;
    fseek(file, 0, SEEK_END);
    const long size = ftell(file);
    fseek(file, 0, SEEK_SET);
    if (size > 0) {
        data.resize((size_t)size);
        if (fread(data.data(), 1, data.size(), file) != data.size()) data.clear();
    }
    fclose(file);
    return data;
}

// frame.json is ours (scene-lab-frame/1): one hex string, flat numbers.
bool json_hex(const std::string &json, const char *key, uint8_t *out, size_t bytes) {
    const size_t at = json.find(std::string("\"") + key + "\": \"");
    if (at == std::string::npos) return false;
    const size_t start = json.find('"', json.find(':', at)) + 1;
    for (size_t i = 0; i < bytes; ++i) {
        unsigned value = 0;
        if (sscanf(json.c_str() + start + 2 * i, "%2x", &value) != 1) return false;
        out[i] = (uint8_t)value;
    }
    return true;
}

unsigned json_number(const std::string &json, const char *key) {
    const size_t at = json.find(std::string("\"") + key + "\":");
    return at == std::string::npos ? 0u : (unsigned)strtoul(json.c_str() + json.find(':', at) + 1, nullptr, 10);
}

// frame.json's "vi" keys, in GFX_INFO's VI register order (tap.py's VI_NAMES); origin is regs[10].
const char *const VI_KEYS[] = {"status", "origin", "width", "intr", "v_current", "burst", "v_sync", "h_sync",
                               "leap", "h_start", "v_start", "v_burst", "x_scale", "y_scale"};
const int VI_FIRST_REG = 9;

// vi: every VI register the frame names (a simulated frame names them all; a savestate's names them too),
// so the present the producer records carries the VI a picture is drawn with.
struct Frame { std::vector<uint8_t> ram, sp; unsigned origin; std::vector<std::pair<int, unsigned>> vi; };

// Frames by folder, read once: a 30 Hz run must not wait on the disk.
const Frame *load(std::map<std::string, Frame> &cache, const std::string &dir) {
    auto found = cache.find(dir);
    if (found != cache.end()) return &found->second;
    if (cache.size() >= 16) cache.clear();   // a 32-bit host: at most 128 MiB of frames
    Frame frame;
    frame.ram = read_file(dir + "\\rdram.bin");
    frame.sp = read_file(dir + "\\dmem.bin");
    std::vector<uint8_t> json = read_file(dir + "\\frame.json");
    if (frame.ram.size() != (8u << 20) || frame.sp.size() != 0x1000 || json.empty()) {
        fprintf(stderr, "%s: needs an 8 MiB rdram.bin, a 4 KiB dmem.bin and frame.json\n", dir.c_str());
        return nullptr;
    }
    const std::string text(json.begin(), json.end());
    frame.origin = json_number(text, "origin");
    for (int i = 0; i < (int)(sizeof VI_KEYS / sizeof VI_KEYS[0]); ++i)
        if (i != 1 && text.find(std::string("\"") + VI_KEYS[i] + "\":") != std::string::npos)
            frame.vi.emplace_back(VI_FIRST_REG + i, json_number(text, VI_KEYS[i]));
    return &cache.emplace(dir, std::move(frame)).first->second;
}

int64_t now_qpc() { LARGE_INTEGER value; QueryPerformanceCounter(&value); return value.QuadPart; }

void report(const char *name, std::vector<double> &us) {
    if (us.empty()) { printf("%s none\n", name); return; }
    std::sort(us.begin(), us.end());
    auto pct = [&](double p) { return us[std::min(us.size() - 1, (size_t)(p * (us.size() - 1) + 0.5))]; };
    printf("%s p50=%.1f p99=%.1f max=%.1f n=%zu\n", name, pct(.5), pct(.99), us.back(), us.size());
}

// The writer's view of a recorded session: its level spans (session.txt) and
// its identity extras (armed.txt's "# identity=" line).
scene_spool::Layout bench_layout(const std::string &session) {
    scene_spool::Layout layout;
    layout.rdram_size = 8u << 20;
    char line[512];
    FILE *file = fopen((session + "\\session.txt").c_str(), "rb");
    while (file && fgets(line, sizeof line, file)) {
        unsigned a = 0, b = 0;
        long long frequency = 0;
        if (sscanf(line, "qpc_frequency=%lld", &frequency) == 1) layout.qpc_frequency = frequency;
        else if (sscanf(line, "rdram_size=%u", &a) == 1) layout.rdram_size = a;
        else if (sscanf(line, "level_region=%x:%u", &a, &b) == 2 && layout.level_count < 8)
            layout.level[layout.level_count++] = {a, b};
    }
    if (file) fclose(file);
    file = fopen((session + "\\armed.txt").c_str(), "rb");
    while (file && fgets(line, sizeof line, file)) {
        if (strncmp(line, "# identity=", 11)) continue;
        for (char *at = line + 11; *at && *at != '\n';) {
            char name[32] = {};
            unsigned address = 0, bytes = 0;
            int used = 0;
            if (sscanf(at, "%31[^:]:%x:%u%n", name, &address, &bytes, &used) != 3) break;
            const scene_spool::Span span = {address, bytes};
            if (!strcmp(name, "global_timer")) layout.timer = span;
            else if (!strcmp(name, "curr_level")) layout.level_id = span;
            else if (!strcmp(name, "curr_area")) layout.area_id = span;
            at += used + (at[used] == ',');
        }
    }
    if (file) fclose(file);
    return layout;
}

int spool_bench(char **argv) {
    using namespace scene_record;
    const std::string session = argv[2];
    const scene_spool::Layout layout = bench_layout(session);
    scene_spool::Settings settings;
    settings.max_bytes = 1ull << 40;   // no size rollover: segments of segment_ms, in the 64 files in turn
    settings.dedupe = atoi(argv[4]) != 0;
    settings.compress = atoi(argv[5]) != 0;
    wchar_t out[MAX_PATH];
    MultiByteToWideChar(CP_UTF8, 0, argv[3], -1, out, MAX_PATH);
    CreateDirectoryW(out, nullptr);
    static scene_spool::Writer writer;
    if (!writer.claim(out) || !writer.begin(1, layout, settings)) return 2;
    HANDLE file = CreateFileA((session + "\\tap.bin").c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr,
                              OPEN_EXISTING, FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
    if (file == INVALID_HANDLE_VALUE) return 2;
    std::vector<uint8_t> record(64u << 20);
    int64_t busy = 0, first_task = 0, last_task = 0;
    uint64_t skipped = 0;
    bool booted = false;
    for (;;) {
        DWORD got = 0;
        if (!ReadFile(file, record.data(), 32, &got, nullptr) || got != 32) break;
        RecordHeader header;
        memcpy(&header, record.data(), 32);
        if (header.magic != kMagic || header.bytes < 32) return 3;
        if (header.bytes > record.size()) record.resize(header.bytes);
        if (!ReadFile(file, record.data() + 32, header.bytes - 32, &got, nullptr) || got != header.bytes - 32) break;
        if (header.type == REC_FULL) {
            skipped += header.bytes;
            if (booted) continue;
            booted = true;   // the boot span: expansion RAM, as live capture copies it once
            const uint32_t begin = 0x400000, size = layout.rdram_size - begin;
            header = {kMagic, REC_BOOT, 64 + size, header.sequence, header.qpc, 0, 0};
            const uint32_t fields[8] = {begin, size, layout.rdram_size, 0, 0, 0, 0, 0};
            memmove(record.data() + 64, record.data() + 48 + begin, size);
            memcpy(record.data() + 32, fields, 32);
            memcpy(record.data(), &header, 32);
        } else if (header.type == REC_PRESENT && header.bytes > 128) {
            skipped += header.bytes - 128;   // no reference picture in live capture
            const uint32_t empty[4] = {0, 0, 0, 1};
            memcpy(record.data() + 32 + 56, empty, 16);
            header.bytes = 128;
            memcpy(record.data(), &header, 32);
        }
        if (header.type == REC_TASK) {
            if (!first_task) first_task = header.qpc;
            last_task = header.qpc;
        }
        const int64_t begin = now_qpc();
        if (!writer.add(record.data())) return 4;
        busy += now_qpc() - begin;
    }
    CloseHandle(file);
    const int64_t begin = now_qpc();
    writer.close_segment(scene_spool::CLOSE_ROM_CLOSED);
    busy += now_qpc() - begin;
    LARGE_INTEGER frequency;
    QueryPerformanceFrequency(&frequency);
    const scene_spool::Stats &s = writer.stats();
    printf("{\"input_bytes\": %llu, \"output_bytes\": %llu, \"index_bytes\": %llu, \"key_bytes\": %llu, "
           "\"skipped_bytes\": %llu, \"tasks\": %u, \"segments\": %u, \"compressed\": %u, "
           "\"files_created\": %u, \"files_reused\": %u, \"seconds\": %.3f, \"writer_seconds\": %.3f}\n",
           (unsigned long long)s.input_bytes, (unsigned long long)s.output_bytes, (unsigned long long)s.index_bytes,
           (unsigned long long)s.key_bytes, (unsigned long long)skipped, s.tasks, s.segments, s.compressed,
           s.files_created, s.files_reused,
           (double)(last_task - first_task) / (double)layout.qpc_frequency, (double)busy / (double)frequency.QuadPart);
    writer.release();
    return 0;
}

}  // namespace

int main(int argc, char **argv) {
    if (argc == 6 && !strcmp(argv[1], "--spool-bench")) return spool_bench(argv);
    if (argc < 3) {
        fprintf(stderr, "usage: tap_offline_host <lab_root> <frame_dir>...\n");
        return 2;
    }
    if (!SetEnvironmentVariableA("LOCALAPPDATA", argv[1])) return 2;
    const bool live = !strcmp(argv[2], "--live");
    const int stdin_at = live ? 3 : 2;   // one-shot frames can come from stdin too: a long scene outgrows a command line
    const bool interactive = argc > stdin_at && !strcmp(argv[stdin_at], "--stdin");
    const int commands = stdin_at + (interactive ? 1 : 0);
    static uint8_t dmem[0x1000], imem[0x1000], header[0x40];
    static unsigned int regs[32];
    uint8_t *rdram = (uint8_t *)VirtualAlloc(nullptr, 8u << 20, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (!rdram) return 2;
    // The ROM header comes from the first frame folder named anywhere (a whole
    // argument, or a word of a command).
    std::string first;
    for (int i = commands; i < argc && first.empty(); ++i) {
        std::string arg = argv[i];
        if (arg.rfind("vi:", 0) == 0) arg = arg.substr(3);
        std::vector<std::string> words = {arg};
        for (size_t at = 0, next; at < arg.size(); at = next + 1) {
            next = arg.find(' ', at);
            if (next == std::string::npos) next = arg.size();
            words.push_back(arg.substr(at, next - at));
        }
        for (const std::string &word : words)
            if (first.empty() && GetFileAttributesA((word + "\\frame.json").c_str()) != INVALID_FILE_ATTRIBUTES)
                first = word;
    }
    char line[4096];
    if (first.empty() && interactive) {   // the first stdin line names it: "header <dir>"
        if (!fgets(line, sizeof line, stdin) || strncmp(line, "header ", 7)) return 2;
        first = line + 7;
        while (!first.empty() && (first.back() == '\n' || first.back() == '\r')) first.pop_back();
        printf("ok\n");
        fflush(stdout);
    }
    first += "\\frame.json";
    std::vector<uint8_t> meta = read_file(first);
    if (meta.empty() || !json_hex(std::string(meta.begin(), meta.end()), "rom_header", header, sizeof header)) {
        fprintf(stderr, "%s: no rom_header\n", first.c_str());
        return 2;
    }
    GFX_INFO info = {};
    info.MemoryBswaped = TRUE;
    info.HEADER = header;
    info.RDRAM = rdram;
    info.DMEM = dmem;
    info.IMEM = imem;
    unsigned int **slots[] = {&info.MI_INTR_REG, &info.DPC_START_REG, &info.DPC_END_REG, &info.DPC_CURRENT_REG,
        &info.DPC_STATUS_REG, &info.DPC_CLOCK_REG, &info.DPC_BUFBUSY_REG, &info.DPC_PIPEBUSY_REG,
        &info.DPC_TMEM_REG, &info.VI_STATUS_REG, &info.VI_ORIGIN_REG, &info.VI_WIDTH_REG, &info.VI_INTR_REG,
        &info.VI_V_CURRENT_LINE_REG, &info.VI_TIMING_REG, &info.VI_V_SYNC_REG, &info.VI_H_SYNC_REG,
        &info.VI_LEAP_REG, &info.VI_H_START_REG, &info.VI_V_START_REG, &info.VI_V_BURST_REG,
        &info.VI_X_SCALE_REG, &info.VI_Y_SCALE_REG};
    for (size_t i = 0; i < sizeof slots / sizeof slots[0]; ++i) *slots[i] = &regs[i];
    st_configure(&info);
    if (live) st_live_any_process();
    st_rom_open();
    std::map<std::string, Frame> cache;
    std::vector<double> task_us, vi_us;
    LARGE_INTEGER frequency;
    QueryPerformanceFrequency(&frequency);
    const double tick_us = 1e6 / (double)frequency.QuadPart;
    int fed = 0;
    // One call with this frame's RDRAM and DMEM in place, timed around the call.
    auto feed = [&](const std::string &dir, bool vi_only, long origin) -> bool {
        const Frame *frame = load(cache, dir);
        if (!frame) return false;
        memcpy(rdram, frame->ram.data(), frame->ram.size());
        memcpy(dmem, frame->sp.data(), frame->sp.size());
        for (const auto &reg : frame->vi) regs[reg.first] = reg.second;
        *info.VI_ORIGIN_REG = origin >= 0 ? (unsigned)origin : frame->origin;
        const int64_t begin = now_qpc();
        if (vi_only) st_update_screen(nullptr);
        else st_process_dlist();
        (vi_only ? vi_us : task_us).push_back((double)(now_qpc() - begin) * tick_us);
        ++fed;
        return true;
    };
    // One command; false ends the run with exit code 2 (quit returns false with *done set).
    bool done = false;
    auto command = [&](std::string text) -> bool {
        while (!text.empty() && (text.back() == '\n' || text.back() == '\r' || text.back() == ' ')) text.pop_back();
        const size_t space = text.find(' ');
        const std::string verb = text.substr(0, space), rest = space == std::string::npos ? "" : text.substr(space + 1);
        if (verb.empty()) return true;
        if (verb == "quit") { done = true; return true; }
        if (verb == "poke") { printf("ok %d\n", st_live_poke(10000)); return true; }
        if (verb == "hold" || verb == "release") { st_live_hold(verb == "hold"); printf("ok\n"); return true; }
        if (verb == "sleep") { Sleep((DWORD)atoi(rest.c_str())); printf("ok\n"); return true; }
        if (verb == "run") {
            int count = 0, interval = 0, used = 0;
            if (sscanf(rest.c_str(), "%d %d %n", &count, &interval, &used) < 2) return false;
            std::vector<std::string> dirs;
            for (size_t at = used, next; at < rest.size(); at = next + 1) {
                next = rest.find(' ', at);
                if (next == std::string::npos) next = rest.size();
                if (next > at) dirs.push_back(rest.substr(at, next - at));
            }
            if (dirs.empty()) return false;
            const int64_t step = frequency.QuadPart * interval / 1000, start = now_qpc();
            for (int i = 0; i < count; ++i) {
                while (now_qpc() < start + step * i) Sleep(1);
                const std::string &dir = dirs[i % dirs.size()];
                const Frame *frame = load(cache, dir);
                if (!frame || !feed(dir, false, -1) || !feed(dir, true, frame->origin + (i % 2) * 0x100000))
                    return false;
            }
            printf("ok %d\n", count);
            return true;
        }
        std::string dir = verb == "frame" || verb == "vi" ? rest : text;
        const bool vi_only = verb == "vi" || dir.rfind("vi:", 0) == 0;
        if (dir.rfind("vi:", 0) == 0) dir = dir.substr(3);
        if (!feed(dir, vi_only, -1)) return false;
        if (interactive) printf("ok\n");
        return true;
    };
    for (int i = commands; i < argc && !done; ++i)
        if (!command(argv[i])) return 2;
    if (interactive) {
        fflush(stdout);
        while (!done && fgets(line, sizeof line, stdin)) {
            if (!command(line)) { printf("error %s", line); fflush(stdout); return 2; }
            fflush(stdout);
        }
    }
    st_close(120000);
    report("task_us", task_us);
    report("vi_us", vi_us);
    printf("fed %d frames\n", fed);
    return 0;
}
