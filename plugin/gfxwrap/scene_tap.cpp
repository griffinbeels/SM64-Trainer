/* The scene tap (M4): records what each graphics task makes the renderer
 * read, and the picture it presents, in one of two modes.
 *
 * One-shot (experiments E0/E1): %LOCALAPPDATA%\SM64Trainer\scene-lab\armed.txt
 * exists when a practice ROM opens. The tap moves it into a new session
 * folder (so one arming records once), records at most `seconds` into tap.bin
 * and its writer thread exits.
 *
 * Live (the live spool, round 2): without that file, a tap thread starts for
 * the ROM run (Project64.exe only) and checks scene-lab\live\lease.txt at most
 * once a second. While the file's last write (the Trainer renews it every 2 s,
 * tools/scene_lab/live_spool.py) is younger than its lease_ms, capture is on,
 * without reopening the ROM; a stale or missing lease turns it off within a
 * second of the check, and a fresh one resumes it (same lines: the same
 * session, a new segment; new lines: a new session). The lease's lines are
 * an arming like armed.txt's, without pictures or full copies, plus
 * lease_ms, window_ms, max_bytes, segment_ms, ring_mib (8..128), boot_region
 * (copied once when capture starts: a practice ROM's code in expansion RAM,
 * REC_BOOT {begin, bytes, rdram_size, 0...} + bytes) and spool_dedupe /
 * spool_compress; its `# identity=` line names the extras the index reads.
 * The tap thread allocates the ring and shadows when capture turns on, frees
 * them when it turns off, and drains the ring into scene_spool.cpp's writer:
 * rolling segments under scene-lab\live\live-<time>\ (format there). It
 * deletes earlier runs' live-* folders unless a writer holds their
 * writer.lock. Turning capture off: the tap thread clears `active`, then waits
 * for the emulation thread's `busy` to clear (it marks itself busy on entry),
 * so it never changes the ring or the config under the producer.
 *
 * Arming lines (both modes; live ignores seconds, reference, full_every):
 *   seconds=<n>       recording length from the first task (default 20, max 600)
 *   reference=0|1     after each new VI origin, ReadScreen the presented picture
 *   reference_every=<n> read only every n-th new picture (default 1; disk is ~3 bytes/pixel)
 *                     (reference instrumentation: a synchronous GPU readback
 *                     that slows emulation; never part of the producer cost)
 *   full_every=<n>    also copy all of RDRAM on the first and every n-th task
 *                     (diagnostic comparator only)
 *   extra=<addr>:<n>  copy n bytes at this RDRAM address with every task
 *                     (identity fields such as gGlobalTimer; the arming tool
 *                     takes them from memory/layout.py, the plugin knows none)
 *   region=<addr>:<n> copy this RDRAM span with every task (the game state the
 *                     scene renderer reads: E2-live; up to 16, 4 MiB in all)
 *   task_region=<offset>:<n> copy n bytes at the task's own data pointer plus
 *                     offset with every task (up to 4, in the same budget): the
 *                     game's gfx pool around this task's list, such as the
 *                     matrices its update allocated before drawing
 *   level_region=<addr>:<n> copy this span whole on the first task and whenever
 *                     the task's RSP segment bases change (a level load), and
 *                     on every other task only its 256-byte blocks that differ
 *                     from the last copy (up to 8, 8 MiB in all)
 *   level_segments=<hex> segments whose bases key a level (default fffc: SM64
 *                     points segment 1 at the alternating gfx pool)
 *   level_delta=0|1   0: level regions only at a key change (default 1)
 *   vi_region=<addr>:<n> copy this span at every UpdateScreen (every VI): whole
 *                     on the first VI, else only its 256-byte blocks that changed
 *                     since the last VI (up to 8, 1 MiB in all). M4 stems: at a VI
 *                     the audio task the game just dispatched has not run yet, so
 *                     the audio spans hold its whole input, and at the next VI
 *                     its output
 *   vi_dmem=1         add SP DMEM (4 KiB, recorded at its physical address
 *                     0x04000000) to the VI spans: a list may read bytes the
 *                     last one left there
 * Without an arming or a fresh lease every call returns after one atomic load
 * (measured 2026-10-26 through tap_offline_host: 0.4 us p50 per call with the
 * host's own timing, the same as before the live mode).
 *
 * The emulation thread never waits, allocates or touches a file: it walks the
 * task (scene_closure.h), copies the closure into a preallocated ring and
 * returns; a full ring drops the record and counts it. A task whose own
 * record is dropped copies no regions either, so its level changes ride on
 * the next task's record. A writer thread drains the ring.
 * Records are 32-byte aligned: a 32-byte header {magic 'SLT1', type, bytes,
 * sequence, qpc, lost (records dropped so far), flags} then the payload;
 * tools/scene_lab/tap.py reads them. Producer cost (walk + copy) is timed
 * separately from the reference readback (2026-10-26, savestates at 30 Hz:
 * 212 us p50, 384-391 us p99 per task armed or live, as before; the live
 * boot copy is the one-off max, 0.76 ms).
 * A REGIONS record follows its task's record: {task_index, kind
 * (0 task, 1 level key, 2 level delta), count, data bytes, copy_ns, scan_ns,
 * block bytes, 0}, the task's 16 segment bases, count x {begin, bytes}, data.
 * A delta's spans apply over the last key and deltas; a lost delta leaves the
 * shadow copy unchanged, so the next one still carries those blocks. A VI
 * record (every UpdateScreen, before the present's) is {vi_index, kind (0 key,
 * 1 delta), count, data bytes, total_ns, scan_ns, block bytes, tasks so far},
 * count x {begin, bytes}, data; the same shadow rule holds, and a lost record
 * shows as a gap in vi_index. */
#define NOMINMAX
#define _CRT_SECURE_NO_WARNINGS
#include <windows.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <wchar.h>
#include <atomic>
#include <new>
#include "scene_tap.h"
#include "scene_closure.h"
#include "scene_spool.h"

#ifndef GFXWRAP_BUILD_ID
#define GFXWRAP_BUILD_ID "unknown"
#endif

namespace {

using namespace scene_record;
constexpr uint32_t kMaxExtras = 16, kMaxExtraBytes = 4096;
constexpr uint32_t kMaxRegions = 16, kMaxRegionBytes = 4u << 20, kMaxTaskRegions = 4;
constexpr uint32_t kMaxLevelRegions = 8, kMaxLevelBytes = 8u << 20;
constexpr uint32_t kBlockBytes = 256, kPageBytes = 4096;
constexpr uint32_t kMaxBlocks = kMaxLevelBytes / kBlockBytes + kMaxLevelRegions;
constexpr uint32_t kRegionsHead = 32 + 64;   // fields, then the 16 segment bases
constexpr uint32_t kMaxViRegions = 8, kMaxViBytes = 1u << 20, kViHead = 32;
constexpr uint32_t kDmemBegin = 0x04000000, kDmemBytes = 0x1000;   // SP DMEM's physical address
constexpr uint64_t kRingBytes = 128ull << 20, kSmallRingBytes = 48ull << 20;
constexpr uint32_t kLeaseCheckMs = 1000, kRetryMs = 10000, kMaxLeaseBytes = 64 << 10;

struct Span { uint32_t begin, bytes; };

struct Config {
    uint32_t seconds = 20, reference = 1, reference_every = 1, full_every = 0, extras = 0;
    uint32_t extra_begin[kMaxExtras], extra_bytes[kMaxExtras];
    uint32_t regions = 0, level_regions = 0, level_segments = 0xFFFC, level_delta = 1, task_regions = 0;
    Span region[kMaxRegions], level[kMaxLevelRegions], task_region[kMaxTaskRegions];   // task: {offset, bytes}
    uint32_t vi_regions = 0, vi_dmem = 0;
    Span vi[kMaxViRegions + 1];   // the RDRAM spans, then DMEM when vi_dmem
    // Live spool (the lease): how long a renewal holds, the writer's bounds,
    // the span copied once when capture starts, and which extras the index names.
    uint32_t lease_ms = 8000, ring_mib = 128, dedupe = 1, compress = 1;
    uint64_t window_ms = 15 * 60 * 1000, max_bytes = 2ull << 30, segment_ms = 15 * 1000;
    Span boot = {0, 0}, timer = {0, 0}, level_id = {0, 0}, area_id = {0, 0};
};

struct Tap {
    GFX_INFO gfx;
    bool have_gfx;
    uint32_t rdram_size;
    std::atomic<bool> active{false};
    std::atomic<bool> finish{false};
    // Live mode: the tap thread arms and disarms while the emulation thread
    // runs, so the producer marks itself busy and the tap thread waits for
    // that to clear after turning capture off (see enter and quiesce).
    // st_stop bumps the generation; the tap thread of an older one exits.
    bool live, boot_due;
    std::atomic<uint32_t> busy{0}, generation{0}, poke_asked{0}, poke_done{0};
    std::atomic<bool> held{false};
    HANDLE wake;
    HANDLE writer;
    uint8_t *ring;
    uint64_t capacity;
    std::atomic<uint64_t> head{0}, tail{0};
    uint64_t pending;
    wchar_t dir[MAX_PATH];
    Config config;
    uint32_t sequence, last_origin;
    int64_t frequency, first_qpc, deadline;
    std::atomic<uint32_t> tasks{0}, presents{0}, lost{0}, refused{0};
    uint64_t walk_ns_total, copy_ns_total;
    uint32_t walk_ns_max, copy_ns_max;
    scene_closure::Walker walker;
    // Regions (E2-live). The shadow holds the level regions as last emitted,
    // concatenated; `changed` is the delta scan's scratch. Both are allocated
    // at arming, so the producer never allocates.
    uint8_t *shadow;
    struct Change { Span span; uint32_t shadow; } *changed;
    uint32_t level_total, level_key[16];
    bool level_valid;
    uint32_t level_keys, level_deltas, region_records;
    uint64_t region_bytes_total, delta_bytes_total, region_ns_total, level_ns_total;
    uint32_t region_ns_max, level_ns_max;
    // VI regions (M4 stems): the same shadow scheme, kept per VI.
    uint8_t *vi_shadow;
    Change *vi_changed;
    uint32_t vi_total, vi_index, vi_keys, vi_deltas, vi_ns_max;
    bool vi_valid;
    uint64_t vi_delta_bytes_total, vi_ns_total;
};
Tap g_tap;

int64_t qpc() { LARGE_INTEGER value; QueryPerformanceCounter(&value); return value.QuadPart; }
uint32_t ns(int64_t ticks) { return (uint32_t)(ticks * 1000000000ll / g_tap.frequency); }
uint32_t align32(uint64_t bytes) { return (uint32_t)((bytes + 31) & ~31ull); }

// Producer: contiguous space for one record, or null (counted loss).
uint8_t *reserve(uint32_t bytes) {
    Tap &t = g_tap;
    uint64_t head = t.head.load(std::memory_order_relaxed);
    const uint64_t tail = t.tail.load(std::memory_order_acquire);
    const uint64_t offset = head % t.capacity, contiguous = t.capacity - offset;
    const uint64_t need = bytes + (contiguous < bytes ? contiguous : 0);
    if (bytes > t.capacity / 2 || t.capacity - (head - tail) < need) { ++t.lost; return nullptr; }
    if (contiguous < bytes) {
        RecordHeader pad = {kMagic, REC_PAD, (uint32_t)contiguous, 0, 0, 0, 0};
        memcpy(t.ring + offset, &pad, sizeof pad);
        head += contiguous;
    }
    t.pending = head;
    return t.ring + head % t.capacity;
}

// The header carries the drop count so far: a reader sees exactly between
// which records the ring dropped some (the live spool's holes).
void commit(uint8_t *record, uint32_t type, uint32_t bytes, int64_t when) {
    RecordHeader header = {kMagic, type, bytes, g_tap.sequence++, when,
                           g_tap.lost.load(std::memory_order_relaxed), 0};
    memcpy(record, &header, sizeof header);
    g_tap.head.store(g_tap.pending + bytes, std::memory_order_release);
}

void write_text(const wchar_t *name, const char *text) {
    wchar_t path[MAX_PATH];
    swprintf(path, MAX_PATH, L"%ls\\%ls", g_tap.dir, name);
    FILE *file = _wfopen(path, L"wb");
    if (!file) return;
    fputs(text, file);
    fclose(file);
}

// The producer's counts and costs for summary.txt; returns the length written.
int format_summary(char *summary, size_t size, uint64_t written, bool failed) {
    Tap &t = g_tap;
    const uint32_t tasks = t.tasks.load();
    const int at = snprintf(summary, size,
             "tasks=%u\npresents=%u\nlost_records=%u\nrefused_tasks=%u\nbytes_written=%llu\nwrite_failed=%d\n"
             "walk_ns_mean=%u\nwalk_ns_max=%u\ncopy_ns_mean=%u\ncopy_ns_max=%u\n",
             tasks, t.presents.load(), t.lost.load(), t.refused.load(), (unsigned long long)written, failed,
             tasks ? (uint32_t)(t.walk_ns_total / tasks) : 0, t.walk_ns_max,
             tasks ? (uint32_t)(t.copy_ns_total / tasks) : 0, t.copy_ns_max);
    const uint32_t records = t.region_records, levels = t.level_keys + t.level_deltas;
    const uint32_t vis = t.vi_keys + t.vi_deltas;
    return at + snprintf(summary + at, size - at,
             "region_records=%u\nregion_bytes_mean=%llu\nregion_ns_mean=%u\nregion_ns_max=%u\n"
             "level_keys=%u\nlevel_deltas=%u\nlevel_delta_bytes_mean=%llu\nlevel_ns_mean=%u\nlevel_ns_max=%u\n"
             "vi_calls=%u\nvi_keys=%u\nvi_deltas=%u\nvi_delta_bytes_mean=%llu\nvi_ns_mean=%u\nvi_ns_max=%u\n",
             records, records ? (unsigned long long)(t.region_bytes_total / records) : 0ull,
             records ? (uint32_t)(t.region_ns_total / records) : 0, t.region_ns_max,
             t.level_keys, t.level_deltas,
             t.level_deltas ? (unsigned long long)(t.delta_bytes_total / t.level_deltas) : 0ull,
             levels ? (uint32_t)(t.level_ns_total / levels) : 0, t.level_ns_max,
             t.vi_index, t.vi_keys, t.vi_deltas,
             t.vi_deltas ? (unsigned long long)(t.vi_delta_bytes_total / t.vi_deltas) : 0ull,
             vis ? (uint32_t)(t.vi_ns_total / vis) : 0, t.vi_ns_max);
}

void release_buffers() {
    Tap &t = g_tap;
    if (t.ring) { VirtualFree(t.ring, 0, MEM_RELEASE); t.ring = nullptr; }
    if (t.shadow) { VirtualFree(t.shadow, 0, MEM_RELEASE); t.shadow = nullptr; t.changed = nullptr; }
    if (t.vi_shadow) { VirtualFree(t.vi_shadow, 0, MEM_RELEASE); t.vi_shadow = nullptr; t.vi_changed = nullptr; }
}

// The ring's committed records, oldest first, into `take` (pads skipped).
template <typename Take> void drain(Take take) {
    Tap &t = g_tap;
    const uint64_t head = t.head.load(std::memory_order_acquire);
    uint64_t tail = t.tail.load(std::memory_order_relaxed);
    while (tail < head) {
        const uint8_t *record = t.ring + tail % t.capacity;
        RecordHeader header;
        memcpy(&header, record, sizeof header);
        if (header.type != REC_PAD) take(record, header);
        tail += header.bytes;
        t.tail.store(tail, std::memory_order_release);
    }
}

DWORD WINAPI writer_main(void *) {
    Tap &t = g_tap;
    wchar_t path[MAX_PATH];
    swprintf(path, MAX_PATH, L"%ls\\tap.bin", t.dir);
    HANDLE file = CreateFileW(path, GENERIC_WRITE, FILE_SHARE_READ, nullptr, CREATE_ALWAYS,
                              FILE_ATTRIBUTE_NORMAL, nullptr);
    uint64_t written = 0;
    bool failed = file == INVALID_HANDLE_VALUE;
    for (;;) {
        drain([&](const uint8_t *record, const RecordHeader &header) {
            if (failed) return;
            DWORD done = 0;
            failed = !WriteFile(file, record, header.bytes, &done, nullptr) || done != header.bytes;
            written += done;
        });
        if (t.finish.load(std::memory_order_acquire)
            && t.head.load(std::memory_order_acquire) == t.tail.load(std::memory_order_relaxed)) break;
        Sleep(4);
    }
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    char summary[2048];
    format_summary(summary, sizeof summary, written, failed);
    write_text(L"summary.txt", summary);
    release_buffers();
    return 0;
}

// `# identity=name:addr:bytes,...` (tap.py's comment line): the extras the
// live spool's index reads global_timer, curr_level and curr_area from.
void parse_identity(const char *line, Config &config) {
    const char *at = line + strlen("# identity=");
    while (*at && *at != '\n' && *at != '\r') {
        char name[32] = {};
        unsigned address = 0, bytes = 0;
        int used = 0;
        if (sscanf(at, "%31[^:]:%x:%u%n", name, &address, &bytes, &used) != 3) break;
        const Span span = {address & 0x1FFFFFFF, bytes <= 4 ? bytes : 4};
        if (!strcmp(name, "global_timer")) config.timer = span;
        else if (!strcmp(name, "curr_level")) config.level_id = span;
        else if (!strcmp(name, "curr_area")) config.area_id = span;
        at += used;
        if (*at == ',') ++at;
    }
}

void parse_line(const char *line, Config &config) {
    unsigned a = 0, b = 0;
    unsigned long long big = 0;
    if (!strncmp(line, "# identity=", 11)) parse_identity(line, config);
    else if (sscanf(line, "lease_ms=%u", &a) == 1) config.lease_ms = a < 1000 ? 1000 : a;
    else if (sscanf(line, "window_ms=%llu", &big) == 1) config.window_ms = big;
    else if (sscanf(line, "max_bytes=%llu", &big) == 1) config.max_bytes = big;
    else if (sscanf(line, "segment_ms=%llu", &big) == 1) config.segment_ms = big;
    else if (sscanf(line, "ring_mib=%u", &a) == 1) config.ring_mib = a < 8 ? 8 : a > 128 ? 128 : a;
    else if (sscanf(line, "spool_dedupe=%u", &a) == 1) config.dedupe = a != 0;
    else if (sscanf(line, "spool_compress=%u", &a) == 1) config.compress = a != 0;
    else if (sscanf(line, "boot_region=%x:%u", &a, &b) == 2) config.boot = {a & 0x1FFFFFFF, b};
    else if (sscanf(line, "seconds=%u", &a) == 1) config.seconds = a < 600 ? a : 600;
    else if (sscanf(line, "reference_every=%u", &a) == 1) config.reference_every = a ? a : 1;
    else if (sscanf(line, "reference=%u", &a) == 1) config.reference = a != 0;
    else if (sscanf(line, "full_every=%u", &a) == 1) config.full_every = a;
    else if (sscanf(line, "extra=%x:%u", &a, &b) == 2 && config.extras < kMaxExtras
             && b > 0 && b <= kMaxExtraBytes) {
        config.extra_begin[config.extras] = a & 0x1FFFFFFF;
        config.extra_bytes[config.extras++] = b;
    }
    else if (sscanf(line, "level_segments=%x", &a) == 1) config.level_segments = a & 0xFFFF;
    else if (sscanf(line, "level_delta=%u", &a) == 1) config.level_delta = a != 0;
    else if (sscanf(line, "level_region=%x:%u", &a, &b) == 2 && config.level_regions < kMaxLevelRegions)
        config.level[config.level_regions++] = {a & 0x1FFFFFFF, b};
    else if (sscanf(line, "task_region=%x:%u", &a, &b) == 2 && config.task_regions < kMaxTaskRegions)
        config.task_region[config.task_regions++] = {a & ~3u, (b + 3) & ~3u};
    else if (sscanf(line, "region=%x:%u", &a, &b) == 2 && config.regions < kMaxRegions)
        config.region[config.regions++] = {a & 0x1FFFFFFF, b};
    else if (sscanf(line, "vi_region=%x:%u", &a, &b) == 2 && config.vi_regions < kMaxViRegions)
        config.vi[config.vi_regions++] = {a & 0x1FFFFFFF, b};
    else if (sscanf(line, "vi_dmem=%u", &a) == 1) config.vi_dmem = a != 0;
}

void parse_config(const wchar_t *path, Config &config) {
    FILE *file = _wfopen(path, L"rb");
    if (!file) return;
    char line[256];
    while (fgets(line, sizeof line, file)) parse_line(line, config);
    fclose(file);
}

// The lease's lines. A live capture never reads back pictures or copies all of
// RDRAM, and runs until the lease ends rather than for `seconds`.
void parse_lease(const char *text, Config &config) {
    char line[256];
    for (const char *at = text; *at;) {
        const char *end = strchr(at, '\n');
        const size_t n = end ? (size_t)(end - at) : strlen(at);
        const size_t kept = n < sizeof line - 1 ? n : sizeof line - 1;
        memcpy(line, at, kept);
        line[kept] = 0;
        parse_line(line, config);
        at += n + (end ? 1 : 0);
    }
    config.reference = 0;
    config.full_every = 0;
}

// Whole words inside RDRAM, within the byte budget; anything else is dropped
// at arming (and missing from session.txt), never clipped mid-session.
uint32_t settle_spans(Span *spans, uint32_t count, uint32_t budget, uint32_t rdram_size) {
    uint32_t kept = 0, total = 0;
    for (uint32_t i = 0; i < count; ++i) {
        const uint32_t begin = spans[i].begin & ~3u;
        const uint64_t end = ((uint64_t)spans[i].begin + spans[i].bytes + 3) & ~3ull;
        if (!spans[i].bytes || end > rdram_size || end - begin > budget - total) continue;
        spans[kept++] = {begin, (uint32_t)(end - begin)};
        total += (uint32_t)(end - begin);
    }
    return kept;
}

void put_regions_head(uint8_t *record, uint32_t task_index, uint32_t kind, uint32_t count,
                      uint32_t data, uint32_t copy_ns, uint32_t scan_ns) {
    const uint32_t fields[8] = {task_index, kind, count, data, copy_ns, scan_ns, kBlockBytes, 0};
    memcpy(record + 32, fields, sizeof fields);
    memcpy(record + 64, g_tap.walker.segment, 64);
}

// Every task: the configured spans, whole, then the task-relative ones placed
// at this task's data pointer (a span that would leave RDRAM is skipped).
void copy_task_regions(uint32_t task_index, int64_t when, uint32_t data_ptr) {
    Tap &t = g_tap;
    const Config &c = t.config;
    if (!c.regions && !c.task_regions) return;
    const int64_t begin = qpc();
    Span spans[kMaxRegions + kMaxTaskRegions];
    uint32_t count = 0;
    uint64_t data = 0;
    for (uint32_t i = 0; i < c.regions; ++i) spans[count++] = c.region[i];
    for (uint32_t i = 0; i < c.task_regions; ++i) {
        const uint64_t at = (uint64_t)(data_ptr & 0x1FFFFFFC) + c.task_region[i].begin;
        if (at + c.task_region[i].bytes <= t.rdram_size) spans[count++] = {(uint32_t)at, c.task_region[i].bytes};
    }
    for (uint32_t i = 0; i < count; ++i) data += spans[i].bytes;
    const uint32_t bytes = align32(sizeof(RecordHeader) + kRegionsHead + 8ull * count + data);
    uint8_t *record = reserve(bytes);
    if (!record) return;
    uint8_t *entries = record + 32 + kRegionsHead, *at = entries + 8 * count;
    for (uint32_t i = 0; i < count; ++i) {
        memcpy(entries + 8 * i, &spans[i], 8);
        memcpy(at, t.gfx.RDRAM + spans[i].begin, spans[i].bytes);
        at += spans[i].bytes;
    }
    const uint32_t copy_ns = ns(qpc() - begin);
    put_regions_head(record, task_index, REGIONS_TASK, count, (uint32_t)data, copy_ns, 0);
    commit(record, REC_REGIONS, bytes, when);
    ++t.region_records;
    t.region_bytes_total += data;
    t.region_ns_total += copy_ns;
    if (copy_ns > t.region_ns_max) t.region_ns_max = copy_ns;
}

uint32_t smaller(uint32_t a, uint32_t b) { return a < b ? a : b; }

// The live bytes behind a span: RDRAM, or SP DMEM at its physical address.
const uint8_t *live(uint32_t begin) {
    return begin >= kDmemBegin ? g_tap.gfx.DMEM + (begin - kDmemBegin) : g_tap.gfx.RDRAM + begin;
}

// The 256-byte blocks of these spans that differ from their shadow (whole
// pages compared first), into `changed`: how many, and their bytes into `data`.
uint32_t changed_blocks(const Span *spans, uint32_t count, const uint8_t *shadow, Tap::Change *changed,
                        uint64_t &data) {
    uint32_t found = 0, offset = 0;
    for (uint32_t r = 0; r < count; offset += spans[r++].bytes) {
        const uint8_t *now = live(spans[r].begin), *old = shadow + offset;
        for (uint32_t page = 0; page < spans[r].bytes; page += kPageBytes) {
            const uint32_t page_end = page + smaller(kPageBytes, spans[r].bytes - page);
            if (!memcmp(now + page, old + page, page_end - page)) continue;
            for (uint32_t block = page; block < page_end; block += kBlockBytes) {
                const uint32_t n = smaller(kBlockBytes, page_end - block);
                if (!memcmp(now + block, old + block, n)) continue;
                changed[found++] = {{spans[r].begin + block, n}, offset + block};
                data += n;
            }
        }
    }
    return found;
}

// Every span whole, as changes: a key.
uint32_t whole_spans(const Span *spans, uint32_t count, Tap::Change *changed) {
    uint32_t offset = 0;
    for (uint32_t r = 0; r < count; offset += spans[r++].bytes) changed[r] = {spans[r], offset};
    return count;
}

// The changes into a record (their spans, then their bytes) and into the shadow.
void emit_blocks(uint8_t *entries, const Tap::Change *changes, uint32_t count, uint8_t *shadow) {
    uint8_t *at = entries + 8ull * count;
    for (uint32_t i = 0; i < count; ++i) {
        const Tap::Change change = changes[i];
        memcpy(entries + 8ull * i, &change.span, 8);
        memcpy(at, live(change.span.begin), change.span.bytes);
        memcpy(shadow + change.shadow, at, change.span.bytes);
        at += change.span.bytes;
    }
}

// Level regions: whole at a new level key (or when the shadow is not current),
// else only the blocks that differ from the shadow, which then follows what
// was emitted. A lost record leaves the shadow as it was.
void copy_level_regions(uint32_t task_index, int64_t when) {
    Tap &t = g_tap;
    const Config &c = t.config;
    if (!c.level_regions || !t.shadow) return;
    const int64_t begin = qpc();
    uint32_t key[16];
    for (uint32_t s = 0; s < 16; ++s)
        key[s] = (c.level_segments >> s & 1) && (t.walker.stats.segments_set >> s & 1) ? t.walker.segment[s] : 0;
    const bool new_key = !t.level_valid || memcmp(key, t.level_key, sizeof key) != 0;
    uint32_t count = 0, scan_ns = 0;
    uint64_t data = 0;
    if (!new_key) {
        if (!c.level_delta) return;
        count = changed_blocks(c.level, c.level_regions, t.shadow, t.changed, data);
        scan_ns = ns(qpc() - begin);
    }
    const bool key_frame = new_key || data > t.level_total / 2;
    if (key_frame) {
        count = whole_spans(c.level, c.level_regions, t.changed);
        data = t.level_total;
    }
    const uint32_t bytes = align32(sizeof(RecordHeader) + kRegionsHead + 8ull * count + data);
    uint8_t *record = reserve(bytes);
    if (!record) { if (key_frame) t.level_valid = false; return; }
    emit_blocks(record + 32 + kRegionsHead, t.changed, count, t.shadow);
    const uint32_t level_ns = ns(qpc() - begin);
    put_regions_head(record, task_index, key_frame ? REGIONS_LEVEL_KEY : REGIONS_LEVEL_DELTA,
                     count, (uint32_t)data, level_ns, scan_ns);
    commit(record, REC_REGIONS, bytes, when);
    if (key_frame) {
        memcpy(t.level_key, key, sizeof key);
        t.level_valid = true;
        ++t.level_keys;
    } else {
        ++t.level_deltas;
        t.delta_bytes_total += data;
    }
    t.level_ns_total += level_ns;
    if (level_ns > t.level_ns_max) t.level_ns_max = level_ns;
}

// VI regions (M4 stems), at every UpdateScreen: whole at the first VI (or
// after a lost key), else only the blocks that changed since the last VI. A
// lost record leaves the shadow as it was and a gap in vi_index.
void copy_vi_regions(int64_t when) {
    Tap &t = g_tap;
    const Config &c = t.config;
    if (!t.vi_shadow) return;
    const int64_t begin = qpc();
    const uint32_t vi_index = t.vi_index++;
    uint32_t count = 0, scan_ns = 0;
    uint64_t data = 0;
    if (t.vi_valid) {
        count = changed_blocks(c.vi, c.vi_regions, t.vi_shadow, t.vi_changed, data);
        scan_ns = ns(qpc() - begin);
    }
    const bool key = !t.vi_valid || data > t.vi_total / 2;
    if (key) {
        count = whole_spans(c.vi, c.vi_regions, t.vi_changed);
        data = t.vi_total;
    }
    const uint32_t bytes = align32(sizeof(RecordHeader) + kViHead + 8ull * count + data);
    uint8_t *record = reserve(bytes);
    if (!record) { if (key) t.vi_valid = false; return; }
    emit_blocks(record + 32 + kViHead, t.vi_changed, count, t.vi_shadow);
    const uint32_t vi_ns = ns(qpc() - begin);
    const uint32_t fields[8] = {vi_index, key ? VI_KEY : VI_DELTA, count, (uint32_t)data, vi_ns, scan_ns,
                                kBlockBytes, t.tasks.load(std::memory_order_relaxed)};
    memcpy(record + 32, fields, sizeof fields);
    commit(record, REC_VI, bytes, when);
    if (key) {
        t.vi_valid = true;
        ++t.vi_keys;
    } else {
        ++t.vi_deltas;
        t.vi_delta_bytes_total += data;
    }
    t.vi_ns_total += vi_ns;
    if (vi_ns > t.vi_ns_max) t.vi_ns_max = vi_ns;
}

uint32_t committed_rdram() {
    MEMORY_BASIC_INFORMATION info;
    if (!g_tap.gfx.RDRAM || VirtualQuery(g_tap.gfx.RDRAM, &info, sizeof info) != sizeof info) return 0;
    const size_t span = (size_t)((uint8_t *)info.BaseAddress + info.RegionSize - g_tap.gfx.RDRAM);
    return span >= (8u << 20) ? (8u << 20) : span >= (4u << 20) ? (4u << 20) : 0;
}

void copy_full(int64_t when) {
    Tap &t = g_tap;
    const uint32_t bytes = align32(sizeof(RecordHeader) + 16 + t.rdram_size);
    uint8_t *record = reserve(bytes);
    if (!record) return;
    const uint32_t meta[4] = {t.rdram_size, 0, 0, 0};
    memcpy(record + 32, meta, sizeof meta);
    memcpy(record + 48, t.gfx.RDRAM, t.rdram_size);
    commit(record, REC_FULL, bytes, when);
}

// A live capture's one copy when it starts, instead of all of RDRAM: the span
// the lease names (the expansion RAM a practice ROM copies its own code into
// at boot), so a package can name that code as a ROM load (package.code_load).
void copy_boot(int64_t when) {
    Tap &t = g_tap;
    const Span boot = t.config.boot;
    if (!boot.bytes) return;
    const uint32_t bytes = align32(sizeof(RecordHeader) + 32ull + boot.bytes);
    uint8_t *record = reserve(bytes);
    if (!record) return;
    const uint32_t fields[8] = {boot.begin, boot.bytes, t.rdram_size, 0, 0, 0, 0, 0};
    memcpy(record + 32, fields, sizeof fields);
    memcpy(record + 64, t.gfx.RDRAM + boot.begin, boot.bytes);
    commit(record, REC_BOOT, bytes, when);
}

// The arming's spans as kept: whole words inside RDRAM, within budget.
void settle_config() {
    Tap &t = g_tap;
    Config &c = t.config;
    c.regions = settle_spans(c.region, c.regions, kMaxRegionBytes, t.rdram_size);
    uint32_t budget = kMaxRegionBytes, kept = 0;
    for (uint32_t i = 0; i < c.regions; ++i) budget -= c.region[i].bytes;
    for (uint32_t i = 0; i < c.task_regions; ++i) {
        if (!c.task_region[i].bytes || c.task_region[i].bytes > budget) continue;
        budget -= c.task_region[i].bytes;
        c.task_region[kept++] = c.task_region[i];
    }
    c.task_regions = kept;
    c.level_regions = settle_spans(c.level, c.level_regions, kMaxLevelBytes, t.rdram_size);
    c.vi_regions = settle_spans(c.vi, c.vi_regions, kMaxViBytes, t.rdram_size);
    if (c.vi_dmem) c.vi[c.vi_regions++] = {kDmemBegin, kDmemBytes};
    if (!settle_spans(&c.boot, 1, t.rdram_size, t.rdram_size)) c.boot = {0, 0};
    t.level_total = t.vi_total = 0;
    for (uint32_t r = 0; r < c.level_regions; ++r) t.level_total += c.level[r].bytes;
    for (uint32_t r = 0; r < c.vi_regions; ++r) t.vi_total += c.vi[r].bytes;
}

// The shadows and the ring for the settled spans. False (the reason in
// summary.txt) when memory runs out; nothing stays allocated then. Their
// contents start invalid: the producer's next level and VI records are keys.
bool allocate(uint64_t ring_bytes) {
    Tap &t = g_tap;
    const Config &c = t.config;
    if (t.level_total) {
        const uint32_t blocks = t.level_total / kBlockBytes + c.level_regions;
        t.shadow = (uint8_t *)VirtualAlloc(nullptr, t.level_total + blocks * sizeof(Tap::Change),
                                           MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
        if (!t.shadow) { write_text(L"summary.txt", "shadow_allocation_failed=1\n"); return false; }
        t.changed = (Tap::Change *)(t.shadow + t.level_total);
    }
    if (t.vi_total) {
        const uint32_t blocks = t.vi_total / kBlockBytes + c.vi_regions;
        t.vi_shadow = (uint8_t *)VirtualAlloc(nullptr, t.vi_total + blocks * sizeof(Tap::Change),
                                              MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
        if (!t.vi_shadow) {
            write_text(L"summary.txt", "vi_shadow_allocation_failed=1\n");
            release_buffers();
            return false;
        }
        t.vi_changed = (Tap::Change *)(t.vi_shadow + t.vi_total);
    }
    t.capacity = ring_bytes;
    t.ring = (uint8_t *)VirtualAlloc(nullptr, (SIZE_T)t.capacity, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (!t.ring && ring_bytes > kSmallRingBytes) {
        t.capacity = kSmallRingBytes;
        t.ring = (uint8_t *)VirtualAlloc(nullptr, (SIZE_T)t.capacity, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    }
    if (!t.ring) {
        write_text(L"summary.txt", "ring_allocation_failed=1\n");
        release_buffers();
        return false;
    }
    t.head = 0; t.tail = 0; t.pending = 0;
    t.level_valid = t.vi_valid = false;
    return true;
}

// A new session's counters: task indexes, sequences and costs start at zero.
void reset_counters() {
    Tap &t = g_tap;
    LARGE_INTEGER frequency;
    QueryPerformanceFrequency(&frequency);
    t.frequency = frequency.QuadPart;
    t.level_keys = t.level_deltas = t.region_records = 0;
    t.region_bytes_total = t.delta_bytes_total = t.region_ns_total = t.level_ns_total = 0;
    t.region_ns_max = t.level_ns_max = 0;
    t.vi_index = t.vi_keys = t.vi_deltas = t.vi_ns_max = 0;
    t.vi_delta_bytes_total = t.vi_ns_total = 0;
    t.sequence = 0; t.last_origin = 0;
    t.first_qpc = 0; t.deadline = 0;
    t.tasks = 0; t.presents = 0; t.lost = 0; t.refused = 0;
    t.walk_ns_total = t.copy_ns_total = 0; t.walk_ns_max = t.copy_ns_max = 0;
}

// session.txt: the wrapper, the ROM and the spans as kept (whole words inside
// RDRAM, within budget), so a reader rebuilds what the producer copied.
void write_session(const char *schema) {
    Tap &t = g_tap;
    const Config &c = t.config;
    char session[2048];
    int at = snprintf(session, sizeof session,
                      "schema=%s\nwrapper_build=%s\nqpc_frequency=%lld\nrdram_size=%u\n"
                      "ring_bytes=%llu\nseconds=%u\nreference=%u\nfull_every=%u\n"
                      "level_segments=%x\nlevel_delta=%u\nlevel_block_bytes=%u\nrom_header=",
                      schema, GFXWRAP_BUILD_ID, (long long)t.frequency, t.rdram_size,
                      (unsigned long long)t.capacity, t.live ? 0 : c.seconds, c.reference, c.full_every,
                      c.level_segments, c.level_delta, kBlockBytes);
    for (int i = 0; i < 0x40 && t.gfx.HEADER && at < (int)sizeof session - 4; ++i)
        at += snprintf(session + at, sizeof session - at, "%02x", t.gfx.HEADER[i]);
    if (at < (int)sizeof session - 2) { session[at++] = '\n'; session[at] = 0; }
    for (uint32_t i = 0; i < c.regions && at < (int)sizeof session - 40; ++i)
        at += snprintf(session + at, sizeof session - at, "region=%x:%u\n", c.region[i].begin, c.region[i].bytes);
    for (uint32_t i = 0; i < c.task_regions && at < (int)sizeof session - 40; ++i)
        at += snprintf(session + at, sizeof session - at, "task_region=%x:%u\n", c.task_region[i].begin,
                       c.task_region[i].bytes);
    for (uint32_t i = 0; i < c.level_regions && at < (int)sizeof session - 40; ++i)
        at += snprintf(session + at, sizeof session - at, "level_region=%x:%u\n", c.level[i].begin, c.level[i].bytes);
    for (uint32_t i = 0; i < c.vi_regions && at < (int)sizeof session - 40; ++i)
        at += snprintf(session + at, sizeof session - at, c.vi[i].begin == kDmemBegin ? "vi_dmem=%x:%u\n"
                       : "vi_region=%x:%u\n", c.vi[i].begin, c.vi[i].bytes);
    if (t.live && at < (int)sizeof session - 160)
        snprintf(session + at, sizeof session - at,
                 "boot_region=%x:%u\nlease_ms=%u\nwindow_ms=%llu\nmax_bytes=%llu\nsegment_ms=%llu\n",
                 c.boot.begin, c.boot.bytes, c.lease_ms, (unsigned long long)c.window_ms,
                 (unsigned long long)c.max_bytes, (unsigned long long)c.segment_ms);
    write_text(L"session.txt", session);
}

// ------------------------------------------------------------ live capture
//
// While a practice ROM runs, the tap thread checks the lease file at most
// once a second (or when a test host pokes it). A fresh lease arms capture
// without reopening the ROM; a missing or stale one turns it off. While
// armed, the thread drains the ring into the live spool's writer.

int64_t ms_ticks(uint64_t ms) { return (int64_t)(ms * (uint64_t)g_tap.frequency / 1000); }

// The emulation thread enters only while capture is on and marks itself
// busy; the tap thread turns capture off, then waits until busy clears before
// it touches the ring, the shadows or the config. A store then a load on each
// side, so both are sequentially consistent.
bool enter() {
    Tap &t = g_tap;
    t.busy.store(1, std::memory_order_seq_cst);
    if (t.active.load(std::memory_order_seq_cst)) return true;
    t.busy.store(0, std::memory_order_release);
    return false;
}

void quiesce() {
    g_tap.active.store(false, std::memory_order_seq_cst);
    while (g_tap.busy.load(std::memory_order_seq_cst)) SwitchToThread();
}

struct LiveStart { HANDLE previous; uint32_t generation; };
bool g_any_process = false;   // a test host's tap_offline_host runs the producer outside Project64

struct Live {
    wchar_t lab[MAX_PATH], spool[MAX_PATH], lease[MAX_PATH];
    char text[kMaxLeaseBytes + 1], seen[kMaxLeaseBytes + 1];   // the armed lease's lines, the last read
    bool armed, session, cleaned;
    HANDLE lock;
    int64_t next_check, retry_after;
    uint64_t cpu_start;
    scene_spool::Writer writer;
};

uint64_t thread_cpu_ns() {
    FILETIME created, exited, kernel, user;
    if (!GetThreadTimes(GetCurrentThread(), &created, &exited, &kernel, &user)) return 0;
    const uint64_t k = (uint64_t)kernel.dwHighDateTime << 32 | kernel.dwLowDateTime;
    const uint64_t u = (uint64_t)user.dwHighDateTime << 32 | user.dwLowDateTime;
    return (k + u) * 100;
}

bool is_project64() {
    wchar_t path[MAX_PATH];
    const DWORD length = GetModuleFileNameW(nullptr, path, MAX_PATH);
    if (!length || length >= MAX_PATH) return false;
    const wchar_t *name = wcsrchr(path, L'\\');
    return !_wcsicmp(name ? name + 1 : path, L"project64.exe");
}

// The writer's view of the arming: the delta-coded spans and the identity extras.
scene_spool::Layout spool_layout() {
    const Tap &t = g_tap;
    const Config &c = t.config;
    scene_spool::Layout layout;
    layout.rdram_size = t.rdram_size;
    layout.qpc_frequency = t.frequency;
    layout.level_count = c.level_regions;
    for (uint32_t i = 0; i < c.level_regions; ++i) layout.level[i] = {c.level[i].begin, c.level[i].bytes};
    layout.vi_count = c.vi_regions;
    for (uint32_t i = 0; i < c.vi_regions; ++i) layout.vi[i] = {c.vi[i].begin, c.vi[i].bytes};
    layout.timer = {c.timer.begin, c.timer.bytes};
    layout.level_id = {c.level_id.begin, c.level_id.bytes};
    layout.area_id = {c.area_id.begin, c.area_id.bytes};
    return layout;
}

// Earlier runs' live-* sessions, never one a writer still holds (its
// writer.lock cannot be deleted while open).
void delete_old_sessions(Live &live) {
    wchar_t pattern[MAX_PATH], folder[MAX_PATH], path[MAX_PATH];
    swprintf(pattern, MAX_PATH, L"%ls\\live-*", live.spool);
    WIN32_FIND_DATAW found;
    HANDLE search = FindFirstFileW(pattern, &found);
    if (search == INVALID_HANDLE_VALUE) return;
    do {
        if (!(found.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) continue;
        swprintf(folder, MAX_PATH, L"%ls\\%ls", live.spool, found.cFileName);
        if (!_wcsicmp(folder, g_tap.dir)) continue;
        swprintf(path, MAX_PATH, L"%ls\\writer.lock", folder);
        if (!DeleteFileW(path) && GetLastError() != ERROR_FILE_NOT_FOUND) continue;
        WIN32_FIND_DATAW file;
        swprintf(path, MAX_PATH, L"%ls\\*", folder);
        HANDLE files = FindFirstFileW(path, &file);
        if (files != INVALID_HANDLE_VALUE) {
            do {
                if (file.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;
                swprintf(path, MAX_PATH, L"%ls\\%ls", folder, file.cFileName);
                DeleteFileW(path);
            } while (FindNextFileW(files, &file));
            FindClose(files);
        }
        RemoveDirectoryW(folder);
    } while (FindNextFileW(search, &found));
    FindClose(search);
}

// Capture off: the producer leaves, the ring drains into the open segment,
// the segment closes with the reason, and the ring and shadows are freed
// (Project64 holds none of it while no lease is held).
void disarm(Live &live, uint32_t reason) {
    if (!live.armed) return;
    quiesce();
    drain([&](const uint8_t *record, const RecordHeader &) { live.writer.add(record); });
    live.writer.close_segment(reason);
    live.armed = false;
    release_buffers();
}

const char *end_reason(uint32_t reason) {
    switch (reason) {
    case scene_spool::CLOSE_LEASE_STOPPED: return "lease_stopped";
    case scene_spool::CLOSE_LEASE_LAPSED: return "lease_lapsed";
    case scene_spool::CLOSE_ROM_CLOSED: return "rom_closed";
    case scene_spool::CLOSE_WRITE_FAILED: return "write_failed";
    case scene_spool::CLOSE_REARMED: return "rearmed";
    case scene_spool::CLOSE_NO_MEMORY: return "no_memory";
    default: return "unloaded";
    }
}

// The session's summary.txt (its last file: a reader takes it as the end).
void end_session(Live &live, uint32_t reason) {
    disarm(live, reason);
    if (!live.session) return;
    const scene_spool::Stats &s = live.writer.stats();
    char summary[3072];
    int at = format_summary(summary, sizeof summary, s.output_bytes, live.writer.failed());
    snprintf(summary + at, sizeof summary - at,
             "end_reason=%s\nwrite_error=%u\nspool_records=%llu\nspool_input_bytes=%llu\nspool_index_bytes=%llu\n"
             "spool_key_bytes=%llu\nspool_tasks=%u\nspool_lost_records=%u\nspool_holes=%u\nspool_refused=%u\n"
             "spool_segments=%u\nspool_deleted=%u\nspool_delete_retries=%u\nspool_compressed=%u\n"
             "writer_cpu_ms=%llu\n",
             end_reason(reason), live.writer.write_error(), (unsigned long long)s.records,
             (unsigned long long)s.input_bytes, (unsigned long long)s.index_bytes,
             (unsigned long long)s.key_bytes, s.tasks, s.lost, s.holes, s.refused, s.segments, s.deleted,
             s.delete_retries, s.compressed, (unsigned long long)((thread_cpu_ns() - live.cpu_start) / 1000000));
    write_text(L"summary.txt", summary);
    if (live.lock != INVALID_HANDLE_VALUE) CloseHandle(live.lock);
    live.lock = INVALID_HANDLE_VALUE;
    live.session = false;
    live.text[0] = 0;
    release_buffers();
}

// A new session folder for these lease lines: live-<local time>[-n].
bool begin_session(Live &live, const char *text) {
    Tap &t = g_tap;
    t.config = Config();
    parse_lease(text, t.config);
    SYSTEMTIME now;
    GetLocalTime(&now);
    CreateDirectoryW(live.lab, nullptr);
    CreateDirectoryW(live.spool, nullptr);
    bool made = false;
    for (int n = 1; n < 100 && !made; ++n) {
        swprintf(t.dir, MAX_PATH, n == 1 ? L"%ls\\live-%04u%02u%02u-%02u%02u%02u" : L"%ls\\live-%04u%02u%02u-%02u%02u%02u-%d",
                 live.spool, now.wYear, now.wMonth, now.wDay, now.wHour, now.wMinute, now.wSecond, n);
        made = CreateDirectoryW(t.dir, nullptr) != 0;
    }
    if (!made) return false;
    if (!live.cleaned) { delete_old_sessions(live); live.cleaned = true; }
    wchar_t path[MAX_PATH];
    swprintf(path, MAX_PATH, L"%ls\\writer.lock", t.dir);
    live.lock = CreateFileW(path, GENERIC_WRITE, FILE_SHARE_READ, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL,
                            nullptr);
    t.live = true;
    settle_config();
    reset_counters();
    t.capacity = (uint64_t)t.config.ring_mib << 20;   // as asked; allocate() may fall back to less
    write_text(L"armed.txt", text);   // the lines the session ran with (tap.identity_spec reads them)
    write_session("scene-lab-live/1");
    scene_spool::Settings settings;
    settings.window_ms = t.config.window_ms;
    settings.max_bytes = t.config.max_bytes;
    settings.segment_ms = t.config.segment_ms;
    settings.dedupe = t.config.dedupe;
    settings.compress = t.config.compress;
    if (!live.writer.begin(t.dir, spool_layout(), settings)) {
        write_text(L"summary.txt", "spool_allocation_failed=1\n");
        return false;
    }
    live.session = true;
    live.cpu_start = thread_cpu_ns();
    strcpy(live.text, text);
    return true;
}

// Capture on: the same lines resume the session (a new segment; task indexes
// continue), new lines start a new one. The producer's next level and VI
// records are keys, and it copies the boot span with its next task.
void arm(Live &live, const char *text) {
    Tap &t = g_tap;
    if (live.session && strcmp(text, live.text)) end_session(live, scene_spool::CLOSE_REARMED);
    const bool resumed = live.session;
    if ((!resumed && !begin_session(live, text)) || !allocate((uint64_t)t.config.ring_mib << 20)) {
        end_session(live, scene_spool::CLOSE_NO_MEMORY);
        if (live.lock != INVALID_HANDLE_VALUE) CloseHandle(live.lock);
        live.lock = INVALID_HANDLE_VALUE;
        live.retry_after = qpc() + ms_ticks(kRetryMs);
        return;
    }
    if (resumed) live.writer.resume();
    t.lost = 0;
    t.last_origin = 0;
    t.first_qpc = 0;
    t.deadline = INT64_MAX;
    t.boot_due = true;
    live.armed = true;
    t.active.store(true, std::memory_order_release);
}

// The lease: lease.txt beside the sessions, fresh while its last write (the
// Trainer renews it) is younger than its lease_ms. Its lines are the arming.
void check_lease(Live &live, int64_t now) {
    WIN32_FILE_ATTRIBUTE_DATA attributes;
    if (!GetFileAttributesExW(live.lease, GetFileExInfoStandard, &attributes)) {
        disarm(live, scene_spool::CLOSE_LEASE_STOPPED);
        return;
    }
    HANDLE file = CreateFileW(live.lease, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                              nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    DWORD read = 0;
    if (file == INVALID_HANDLE_VALUE) return;   // being replaced: next check
    const BOOL ok = ReadFile(file, live.seen, kMaxLeaseBytes, &read, nullptr);
    CloseHandle(file);
    if (!ok) return;
    live.seen[read] = 0;
    Config probe;
    parse_lease(live.seen, probe);
    FILETIME clock;
    GetSystemTimeAsFileTime(&clock);
    const int64_t written = (int64_t)((uint64_t)attributes.ftLastWriteTime.dwHighDateTime << 32
                                      | attributes.ftLastWriteTime.dwLowDateTime);
    const int64_t current = (int64_t)((uint64_t)clock.dwHighDateTime << 32 | clock.dwLowDateTime);
    if (current - written >= (int64_t)probe.lease_ms * 10000) {
        disarm(live, scene_spool::CLOSE_LEASE_LAPSED);
        return;
    }
    if (live.armed && !strcmp(live.seen, live.text)) return;   // renewed, unchanged
    if (now < live.retry_after) return;
    disarm(live, scene_spool::CLOSE_REARMED);
    arm(live, live.seen);
}

DWORD WINAPI live_main(void *argument) {
    Tap &t = g_tap;
    const LiveStart start = *(LiveStart *)argument;
    delete (LiveStart *)argument;
    if (start.previous) {   // the previous run's tap thread finishes first: it owns the buffers
        WaitForSingleObject(start.previous, INFINITE);
        CloseHandle(start.previous);
    }
    Live *live = new (std::nothrow) Live();
    if (!live) return 0;
    live->lock = INVALID_HANDLE_VALUE;
    wchar_t root[MAX_PATH];
    const DWORD length = GetEnvironmentVariableW(L"LOCALAPPDATA", root, MAX_PATH);
    if (length && length < MAX_PATH - 96) {
        swprintf(live->lab, MAX_PATH, L"%ls\\SM64Trainer\\scene-lab", root);
        swprintf(live->spool, MAX_PATH, L"%ls\\live", live->lab);
        swprintf(live->lease, MAX_PATH, L"%ls\\lease.txt", live->spool);
        while (t.generation.load(std::memory_order_acquire) == start.generation) {
            const int64_t now = qpc();
            const uint32_t asked = t.poke_asked.load(std::memory_order_acquire);
            const bool poked = asked != t.poke_done.load(std::memory_order_relaxed);
            if (now >= live->next_check || poked) {
                check_lease(*live, now);
                live->next_check = now + ms_ticks(kLeaseCheckMs);
            }
            if (live->armed && !t.held.load(std::memory_order_acquire)) {
                drain([&](const uint8_t *record, const RecordHeader &) { live->writer.add(record); });
                if (poked) live->writer.flush_rows();   // a test host reads right after
                live->writer.tick(qpc());
                if (live->writer.failed()) {
                    end_session(*live, scene_spool::CLOSE_WRITE_FAILED);
                    live->retry_after = qpc() + ms_ticks(kRetryMs);
                }
            }
            if (poked) t.poke_done.store(asked, std::memory_order_release);
            const int64_t left = (live->next_check - qpc()) * 1000 / t.frequency;
            WaitForSingleObject(t.wake, live->armed ? 4 : (DWORD)(left < 1 ? 1 : left > 1000 ? 1000 : left));
        }
        end_session(*live, scene_spool::CLOSE_ROM_CLOSED);
    }
    delete live;
    return 0;
}

}  // namespace

extern "C" void __cdecl st_configure(const GFX_INFO *info) {
    g_tap.gfx = *info;
    g_tap.have_gfx = info->RDRAM && info->DMEM && info->VI_ORIGIN_REG && info->MemoryBswaped;
}

// Without a one-shot arming file: the tap thread that watches the lease for
// this ROM run (Project64 only, unless a test host allowed any process). It
// joins the previous run's thread before touching the tap's buffers.
static void start_live() {
    Tap &t = g_tap;
    if (!g_any_process && !is_project64()) return;
    t.rdram_size = committed_rdram();
    if (!t.rdram_size) return;
    if (!t.wake) t.wake = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    LiveStart *start = new (std::nothrow) LiveStart{t.writer, t.generation.fetch_add(1) + 1};
    if (!t.wake || !start) { delete start; return; }
    LARGE_INTEGER frequency;
    QueryPerformanceFrequency(&frequency);
    t.frequency = frequency.QuadPart;
    t.live = true;
    HANDLE thread = CreateThread(nullptr, 0, live_main, start, 0, nullptr);
    if (!thread) { delete start; return; }
    t.writer = thread;
}

extern "C" void __cdecl st_rom_open(void) {
    Tap &t = g_tap;
    if (!t.have_gfx || t.active.load()) return;
    wchar_t root[MAX_PATH], armed[MAX_PATH];
    DWORD length = GetEnvironmentVariableW(L"LOCALAPPDATA", root, MAX_PATH);
    if (!length || length >= MAX_PATH - 64) return;
    swprintf(armed, MAX_PATH, L"%ls\\SM64Trainer\\scene-lab\\armed.txt", root);
    if (GetFileAttributesW(armed) == INVALID_FILE_ATTRIBUTES) { start_live(); return; }
    if (t.writer) {
        if (WaitForSingleObject(t.writer, 0) != WAIT_OBJECT_0) return;   // previous session still draining
        CloseHandle(t.writer);
        t.writer = nullptr;
    }
    t.live = false;
    t.rdram_size = committed_rdram();
    if (!t.rdram_size) return;
    SYSTEMTIME now;
    GetLocalTime(&now);
    swprintf(t.dir, MAX_PATH, L"%ls\\SM64Trainer\\scene-lab\\tap-%04u%02u%02u-%02u%02u%02u", root,
             now.wYear, now.wMonth, now.wDay, now.wHour, now.wMinute, now.wSecond);
    if (!CreateDirectoryW(t.dir, nullptr)) return;
    wchar_t moved[MAX_PATH];
    swprintf(moved, MAX_PATH, L"%ls\\armed.txt", t.dir);
    if (!MoveFileW(armed, moved)) return;
    t.config = Config();
    parse_config(moved, t.config);
    settle_config();
    if (!allocate(kRingBytes)) return;
    reset_counters();
    write_session("scene-lab-tap/1");
    t.finish = false;
    t.writer = CreateThread(nullptr, 0, writer_main, nullptr, 0, nullptr);
    if (!t.writer) {
        release_buffers();
        return;
    }
    t.active.store(true, std::memory_order_release);
}

static void process_dlist() {
    Tap &t = g_tap;
    const int64_t begin = qpc();
    if (!t.first_qpc) {
        t.first_qpc = begin;
        t.deadline = t.live ? INT64_MAX : begin + (int64_t)t.config.seconds * t.frequency;
    }
    if (begin >= t.deadline) { st_stop(); return; }
    uint32_t task[scene_closure::kTaskWords];
    memcpy(task, t.gfx.DMEM + 0xFC0, sizeof task);
    const uint32_t task_index = t.tasks.load(std::memory_order_relaxed);
    if (t.live) {
        if (t.boot_due) { t.boot_due = false; copy_boot(begin); }
    }
    else if (t.config.full_every && (task_index == 0 || task_index % t.config.full_every == 0)) copy_full(begin);
    else if (task_index == 0) copy_full(begin);

    const int64_t walk_begin = qpc();
    const uint32_t refusals = t.walker.walk(t.gfx.RDRAM, t.rdram_size, task, 1u << 20);
    const int64_t walk_end = qpc();
    uint32_t runs = 0;
    uint64_t closure = 0;
    t.walker.runs([&](uint32_t a, uint32_t b) { ++runs; closure += b - a; });
    uint64_t extra_bytes = 0;
    for (uint32_t i = 0; i < t.config.extras; ++i) extra_bytes += (t.config.extra_bytes[i] + 3) & ~3u;
    // payload: 12 u32 fields, task[16], runs*{begin,end}, extras*{begin,bytes}, run bytes, extra bytes
    const uint64_t payload = 48 + 64 + 8ull * runs + 8ull * t.config.extras + closure + extra_bytes;
    uint8_t *record = reserve(align32(sizeof(RecordHeader) + payload));
    if (record) {
        uint8_t *at = record + 32 + 48;
        memcpy(at, task, 64); at += 64;
        t.walker.runs([&](uint32_t a, uint32_t b) { const uint32_t pair[2] = {a, b}; memcpy(at, pair, 8); at += 8; });
        for (uint32_t i = 0; i < t.config.extras; ++i) {
            const uint32_t pair[2] = {t.config.extra_begin[i], t.config.extra_bytes[i]};
            memcpy(at, pair, 8); at += 8;
        }
        t.walker.runs([&](uint32_t a, uint32_t b) { memcpy(at, t.gfx.RDRAM + a, b - a); at += b - a; });
        for (uint32_t i = 0; i < t.config.extras; ++i) {
            const uint32_t a = t.config.extra_begin[i] & ~3u, n = (t.config.extra_bytes[i] + 3) & ~3u;
            if (a + n <= t.rdram_size) memcpy(at, t.gfx.RDRAM + a, n); else memset(at, 0, n);
            at += n;
        }
        const int64_t copy_end = qpc();
        const uint32_t walk_ns = ns(walk_end - walk_begin), copy_ns = ns(copy_end - walk_end);
        const uint32_t fields[12] = {refusals, t.walker.stats.commands, walk_ns, copy_ns,
                                     *t.gfx.VI_ORIGIN_REG, runs, t.config.extras, (uint32_t)closure,
                                     t.walker.last_cimg, t.walker.stats.color_images, task_index, 0};
        memcpy(record + 32, fields, sizeof fields);
        commit(record, REC_TASK, align32(sizeof(RecordHeader) + payload), begin);
        t.walk_ns_total += walk_ns; t.copy_ns_total += copy_ns;
        if (walk_ns > t.walk_ns_max) t.walk_ns_max = walk_ns;
        if (copy_ns > t.copy_ns_max) t.copy_ns_max = copy_ns;
    }
    // The game state the scene renderer read for this task (E2-live), copied at
    // the same instant: the emulation thread is inside ProcessDList. A task
    // whose own record was dropped copies none: its level changes ride on the
    // next task's record, never on the task before it.
    if (record) {
        copy_task_regions(task_index, begin, task[12]);
        copy_level_regions(task_index, begin);
    }
    if (refusals) ++t.refused;
    t.tasks.fetch_add(1, std::memory_order_relaxed);
}

extern "C" void __cdecl st_process_dlist(void) {
    Tap &t = g_tap;
    if (!t.active.load(std::memory_order_acquire)) return;   // idle: this load is the whole cost
    if (!enter()) return;
    process_dlist();
    t.busy.store(0, std::memory_order_release);
}

static void update_screen(fn_readscreen read_screen) {
    Tap &t = g_tap;
    if (!t.first_qpc) return;
    const int64_t vi_when = qpc();
    if (vi_when >= t.deadline) { st_stop(); return; }
    copy_vi_regions(vi_when);   // every VI: the audio task the game just dispatched, before the RSP runs it
    const uint32_t origin = *t.gfx.VI_ORIGIN_REG;
    if (origin == t.last_origin) return;
    t.last_origin = origin;
    const GFX_INFO &g = t.gfx;
    const uint32_t vi[14] = {*g.VI_STATUS_REG, *g.VI_ORIGIN_REG, *g.VI_WIDTH_REG, *g.VI_INTR_REG,
        *g.VI_V_CURRENT_LINE_REG, *g.VI_TIMING_REG, *g.VI_V_SYNC_REG, *g.VI_H_SYNC_REG, *g.VI_LEAP_REG,
        *g.VI_H_START_REG, *g.VI_V_START_REG, *g.VI_V_BURST_REG, *g.VI_X_SCALE_REG, *g.VI_Y_SCALE_REG};
    const int64_t when = qpc();
    void *pixels = nullptr;
    long width = 0, height = 0;
    uint32_t read_ns = 0;
    if (t.config.reference && read_screen && t.presents.load(std::memory_order_relaxed) % t.config.reference_every == 0) {
        read_screen(&pixels, &width, &height);
        read_ns = ns(qpc() - when);
        if (!pixels || width <= 0 || height <= 0 || width > 8192 || height > 8192) { width = height = 0; }
    }
    const uint64_t image = (uint64_t)width * (uint64_t)height * 3;
    const uint32_t bytes = align32(sizeof(RecordHeader) + 56 + 16 + image);
    if (uint8_t *record = reserve(bytes)) {
        memcpy(record + 32, vi, sizeof vi);
        const uint32_t meta[4] = {(uint32_t)width, (uint32_t)height, read_ns, 1 /* BGR24, bottom-up */};
        memcpy(record + 32 + 56, meta, sizeof meta);
        if (image) memcpy(record + 32 + 72, pixels, (size_t)image);
        commit(record, REC_PRESENT, bytes, when);
    }
    // The renderer allocates with its static CRT's malloc, which is the process heap.
    if (pixels) HeapFree(GetProcessHeap(), 0, pixels);
    t.presents.fetch_add(1, std::memory_order_relaxed);
}

extern "C" void __cdecl st_update_screen(fn_readscreen read_screen) {
    Tap &t = g_tap;
    if (!t.active.load(std::memory_order_acquire)) return;   // idle: this load is the whole cost
    if (!enter()) return;
    update_screen(read_screen);
    t.busy.store(0, std::memory_order_release);
}

// One-shot: capture ends and the writer drains on its own. Live: the tap
// thread of this ROM run turns capture off, drains and closes its session.
extern "C" void __cdecl st_stop(void) {
    Tap &t = g_tap;
    if (t.live) {
        t.generation.fetch_add(1, std::memory_order_acq_rel);
        if (t.wake) SetEvent(t.wake);
        return;
    }
    if (t.active.exchange(false)) t.finish.store(true, std::memory_order_release);
}

extern "C" void __cdecl st_close(unsigned long timeout_ms) {
    st_stop();
    if (g_tap.writer) {
        WaitForSingleObject(g_tap.writer, timeout_ms);
        CloseHandle(g_tap.writer);
        g_tap.writer = nullptr;
    }
}

extern "C" void __cdecl st_live_any_process(void) { g_any_process = true; }

extern "C" int __cdecl st_live_poke(unsigned long timeout_ms) {
    Tap &t = g_tap;
    if (!t.live || !t.wake) return -1;
    const uint32_t asked = t.poke_asked.fetch_add(1, std::memory_order_acq_rel) + 1;
    SetEvent(t.wake);
    const ULONGLONG until = GetTickCount64() + timeout_ms;
    while ((int32_t)(t.poke_done.load(std::memory_order_acquire) - asked) < 0) {
        if (GetTickCount64() >= until) return -1;
        Sleep(1);
    }
    return t.active.load(std::memory_order_acquire) ? 1 : 0;
}

extern "C" void __cdecl st_live_hold(int hold) { g_tap.held.store(hold != 0, std::memory_order_release); }
