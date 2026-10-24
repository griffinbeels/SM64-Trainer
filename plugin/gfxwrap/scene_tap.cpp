/* The scene tap (M4 experiment E0/E1): records what each graphics task makes
 * the renderer read, and the picture it presents, for a short armed session.
 *
 * Arming: %LOCALAPPDATA%\SM64Trainer\scene-lab\armed.txt must exist when a
 * practice ROM opens. The tap moves it into a new session folder (so one
 * arming records once) and reads key=value lines:
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
 *   level_region=<addr>:<n> copy this span whole on the first task and whenever
 *                     the task's RSP segment bases change (a level load), and
 *                     on every other task only its 256-byte blocks that differ
 *                     from the last copy (up to 8, 8 MiB in all)
 *   level_segments=<hex> segments whose bases key a level (default fffc: SM64
 *                     points segment 1 at the alternating gfx pool)
 *   level_delta=0|1   0: level regions only at a key change (default 1)
 * Without that file every call returns after one relaxed atomic load.
 *
 * The emulation thread never waits: it walks the task (scene_closure.h),
 * copies the closure into a preallocated ring and returns; a full ring drops
 * the record and counts it. A writer thread drains the ring to tap.bin.
 * Records are 32-byte aligned: a 32-byte header {magic 'SLT1', type, bytes,
 * sequence, qpc, reserved[2]} then the payload; tools/scene_lab/tap.py reads
 * them. Producer cost (walk + copy) is timed separately from the reference
 * readback. A REGIONS record follows its task's record: {task_index, kind
 * (0 task, 1 level key, 2 level delta), count, data bytes, copy_ns, scan_ns,
 * block bytes, 0}, the task's 16 segment bases, count x {begin, bytes}, data.
 * A delta's spans apply over the last key and deltas; a lost delta leaves the
 * shadow copy unchanged, so the next one still carries those blocks. */
#define NOMINMAX
#define _CRT_SECURE_NO_WARNINGS
#include <windows.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <wchar.h>
#include <atomic>
#include "scene_tap.h"
#include "scene_closure.h"

#ifndef GFXWRAP_BUILD_ID
#define GFXWRAP_BUILD_ID "unknown"
#endif

namespace {

constexpr uint32_t kMagic = 0x31544C53;   // "SLT1" little-endian
enum : uint32_t { REC_PAD = 0, REC_TASK = 1, REC_PRESENT = 2, REC_FULL = 3, REC_REGIONS = 4 };
enum : uint32_t { REGIONS_TASK = 0, REGIONS_LEVEL_KEY = 1, REGIONS_LEVEL_DELTA = 2 };
struct RecordHeader { uint32_t magic, type, bytes, sequence; int64_t qpc; uint32_t reserved[2]; };
static_assert(sizeof(RecordHeader) == 32, "tap record header");
constexpr uint32_t kMaxExtras = 16, kMaxExtraBytes = 4096;
constexpr uint32_t kMaxRegions = 16, kMaxRegionBytes = 4u << 20;
constexpr uint32_t kMaxLevelRegions = 8, kMaxLevelBytes = 8u << 20;
constexpr uint32_t kBlockBytes = 256, kPageBytes = 4096;
constexpr uint32_t kMaxBlocks = kMaxLevelBytes / kBlockBytes + kMaxLevelRegions;
constexpr uint32_t kRegionsHead = 32 + 64;   // fields, then the 16 segment bases
constexpr uint64_t kRingBytes = 128ull << 20, kSmallRingBytes = 48ull << 20;

struct Span { uint32_t begin, bytes; };

struct Config {
    uint32_t seconds = 20, reference = 1, reference_every = 1, full_every = 0, extras = 0;
    uint32_t extra_begin[kMaxExtras], extra_bytes[kMaxExtras];
    uint32_t regions = 0, level_regions = 0, level_segments = 0xFFFC, level_delta = 1;
    Span region[kMaxRegions], level[kMaxLevelRegions];
};

struct Tap {
    GFX_INFO gfx;
    bool have_gfx;
    uint32_t rdram_size;
    std::atomic<bool> active{false};
    std::atomic<bool> finish{false};
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
        RecordHeader pad = {kMagic, REC_PAD, (uint32_t)contiguous, 0, 0, {0, 0}};
        memcpy(t.ring + offset, &pad, sizeof pad);
        head += contiguous;
    }
    t.pending = head;
    return t.ring + head % t.capacity;
}

void commit(uint8_t *record, uint32_t type, uint32_t bytes, int64_t when) {
    RecordHeader header = {kMagic, type, bytes, g_tap.sequence++, when, {0, 0}};
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

DWORD WINAPI writer_main(void *) {
    Tap &t = g_tap;
    wchar_t path[MAX_PATH];
    swprintf(path, MAX_PATH, L"%ls\\tap.bin", t.dir);
    HANDLE file = CreateFileW(path, GENERIC_WRITE, FILE_SHARE_READ, nullptr, CREATE_ALWAYS,
                              FILE_ATTRIBUTE_NORMAL, nullptr);
    uint64_t written = 0;
    bool failed = file == INVALID_HANDLE_VALUE;
    for (;;) {
        const uint64_t head = t.head.load(std::memory_order_acquire);
        uint64_t tail = t.tail.load(std::memory_order_relaxed);
        while (tail < head) {
            const uint8_t *record = t.ring + tail % t.capacity;
            RecordHeader header;
            memcpy(&header, record, sizeof header);
            if (header.type != REC_PAD && !failed) {
                DWORD done = 0;
                failed = !WriteFile(file, record, header.bytes, &done, nullptr) || done != header.bytes;
                written += done;
            }
            tail += header.bytes;
            t.tail.store(tail, std::memory_order_release);
        }
        if (t.finish.load(std::memory_order_acquire) && t.head.load(std::memory_order_acquire) == tail) break;
        Sleep(4);
    }
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    char summary[2048];
    const uint32_t tasks = t.tasks.load();
    const int at = snprintf(summary, sizeof summary,
             "tasks=%u\npresents=%u\nlost_records=%u\nrefused_tasks=%u\nbytes_written=%llu\nwrite_failed=%d\n"
             "walk_ns_mean=%u\nwalk_ns_max=%u\ncopy_ns_mean=%u\ncopy_ns_max=%u\n",
             tasks, t.presents.load(), t.lost.load(), t.refused.load(), (unsigned long long)written, failed,
             tasks ? (uint32_t)(t.walk_ns_total / tasks) : 0, t.walk_ns_max,
             tasks ? (uint32_t)(t.copy_ns_total / tasks) : 0, t.copy_ns_max);
    const uint32_t records = t.region_records, levels = t.level_keys + t.level_deltas;
    snprintf(summary + at, sizeof summary - at,
             "region_records=%u\nregion_bytes_mean=%llu\nregion_ns_mean=%u\nregion_ns_max=%u\n"
             "level_keys=%u\nlevel_deltas=%u\nlevel_delta_bytes_mean=%llu\nlevel_ns_mean=%u\nlevel_ns_max=%u\n",
             records, records ? (unsigned long long)(t.region_bytes_total / records) : 0ull,
             records ? (uint32_t)(t.region_ns_total / records) : 0, t.region_ns_max,
             t.level_keys, t.level_deltas,
             t.level_deltas ? (unsigned long long)(t.delta_bytes_total / t.level_deltas) : 0ull,
             levels ? (uint32_t)(t.level_ns_total / levels) : 0, t.level_ns_max);
    write_text(L"summary.txt", summary);
    VirtualFree(t.ring, 0, MEM_RELEASE);
    t.ring = nullptr;
    if (t.shadow) { VirtualFree(t.shadow, 0, MEM_RELEASE); t.shadow = nullptr; t.changed = nullptr; }
    return 0;
}

void parse_config(const wchar_t *path, Config &config) {
    FILE *file = _wfopen(path, L"rb");
    if (!file) return;
    char line[256];
    while (fgets(line, sizeof line, file)) {
        unsigned a = 0, b = 0;
        if (sscanf(line, "seconds=%u", &a) == 1) config.seconds = a < 600 ? a : 600;
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
        else if (sscanf(line, "region=%x:%u", &a, &b) == 2 && config.regions < kMaxRegions)
            config.region[config.regions++] = {a & 0x1FFFFFFF, b};
    }
    fclose(file);
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

// Every task: the configured spans, whole.
void copy_task_regions(uint32_t task_index, int64_t when) {
    Tap &t = g_tap;
    const Config &c = t.config;
    if (!c.regions) return;
    const int64_t begin = qpc();
    uint64_t data = 0;
    for (uint32_t i = 0; i < c.regions; ++i) data += c.region[i].bytes;
    const uint32_t bytes = align32(sizeof(RecordHeader) + kRegionsHead + 8ull * c.regions + data);
    uint8_t *record = reserve(bytes);
    if (!record) return;
    uint8_t *entries = record + 32 + kRegionsHead, *at = entries + 8 * c.regions;
    for (uint32_t i = 0; i < c.regions; ++i) {
        memcpy(entries + 8 * i, &c.region[i], 8);
        memcpy(at, t.gfx.RDRAM + c.region[i].begin, c.region[i].bytes);
        at += c.region[i].bytes;
    }
    const uint32_t copy_ns = ns(qpc() - begin);
    put_regions_head(record, task_index, REGIONS_TASK, c.regions, (uint32_t)data, copy_ns, 0);
    commit(record, REC_REGIONS, bytes, when);
    ++t.region_records;
    t.region_bytes_total += data;
    t.region_ns_total += copy_ns;
    if (copy_ns > t.region_ns_max) t.region_ns_max = copy_ns;
}

uint32_t smaller(uint32_t a, uint32_t b) { return a < b ? a : b; }

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
    uint32_t count = 0, scan_ns = 0, offset = 0;
    uint64_t data = 0;
    if (!new_key) {
        if (!c.level_delta) return;
        for (uint32_t r = 0; r < c.level_regions; offset += c.level[r++].bytes) {
            const uint8_t *live = t.gfx.RDRAM + c.level[r].begin, *old = t.shadow + offset;
            for (uint32_t page = 0; page < c.level[r].bytes; page += kPageBytes) {
                const uint32_t page_end = page + smaller(kPageBytes, c.level[r].bytes - page);
                if (!memcmp(live + page, old + page, page_end - page)) continue;
                for (uint32_t block = page; block < page_end; block += kBlockBytes) {
                    const uint32_t n = smaller(kBlockBytes, page_end - block);
                    if (!memcmp(live + block, old + block, n)) continue;
                    t.changed[count++] = {{c.level[r].begin + block, n}, offset + block};
                    data += n;
                }
            }
        }
        scan_ns = ns(qpc() - begin);
    }
    const bool key_frame = new_key || data > t.level_total / 2;
    if (key_frame) {
        count = 0;
        offset = 0;
        for (uint32_t r = 0; r < c.level_regions; offset += c.level[r++].bytes)
            t.changed[count++] = {c.level[r], offset};
        data = t.level_total;
    }
    const uint32_t bytes = align32(sizeof(RecordHeader) + kRegionsHead + 8ull * count + data);
    uint8_t *record = reserve(bytes);
    if (!record) { if (key_frame) t.level_valid = false; return; }
    uint8_t *entries = record + 32 + kRegionsHead, *at = entries + 8ull * count;
    for (uint32_t i = 0; i < count; ++i) {
        const Tap::Change change = t.changed[i];
        memcpy(entries + 8ull * i, &change.span, 8);
        memcpy(at, t.gfx.RDRAM + change.span.begin, change.span.bytes);
        memcpy(t.shadow + change.shadow, at, change.span.bytes);
        at += change.span.bytes;
    }
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

}  // namespace

extern "C" void __cdecl st_configure(const GFX_INFO *info) {
    g_tap.gfx = *info;
    g_tap.have_gfx = info->RDRAM && info->DMEM && info->VI_ORIGIN_REG && info->MemoryBswaped;
}

extern "C" void __cdecl st_rom_open(void) {
    Tap &t = g_tap;
    if (!t.have_gfx || t.active.load()) return;
    if (t.writer) {
        if (WaitForSingleObject(t.writer, 0) != WAIT_OBJECT_0) return;   // previous session still draining
        CloseHandle(t.writer);
        t.writer = nullptr;
    }
    wchar_t root[MAX_PATH], armed[MAX_PATH];
    DWORD length = GetEnvironmentVariableW(L"LOCALAPPDATA", root, MAX_PATH);
    if (!length || length >= MAX_PATH - 64) return;
    swprintf(armed, MAX_PATH, L"%ls\\SM64Trainer\\scene-lab\\armed.txt", root);
    if (GetFileAttributesW(armed) == INVALID_FILE_ATTRIBUTES) return;
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
    Config &c = t.config;
    c.regions = settle_spans(c.region, c.regions, kMaxRegionBytes, t.rdram_size);
    c.level_regions = settle_spans(c.level, c.level_regions, kMaxLevelBytes, t.rdram_size);
    t.level_total = 0;
    for (uint32_t r = 0; r < c.level_regions; ++r) t.level_total += c.level[r].bytes;
    t.shadow = nullptr;
    t.changed = nullptr;
    if (t.level_total) {
        const uint32_t blocks = t.level_total / kBlockBytes + c.level_regions;
        t.shadow = (uint8_t *)VirtualAlloc(nullptr, t.level_total + blocks * sizeof(Tap::Change),
                                           MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
        if (!t.shadow) { write_text(L"summary.txt", "shadow_allocation_failed=1\n"); return; }
        t.changed = (Tap::Change *)(t.shadow + t.level_total);
    }
    t.level_valid = false;
    t.level_keys = t.level_deltas = t.region_records = 0;
    t.region_bytes_total = t.delta_bytes_total = t.region_ns_total = t.level_ns_total = 0;
    t.region_ns_max = t.level_ns_max = 0;
    t.capacity = kRingBytes;
    t.ring = (uint8_t *)VirtualAlloc(nullptr, (SIZE_T)t.capacity, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (!t.ring) {
        t.capacity = kSmallRingBytes;
        t.ring = (uint8_t *)VirtualAlloc(nullptr, (SIZE_T)t.capacity, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    }
    if (!t.ring) {
        write_text(L"summary.txt", "ring_allocation_failed=1\n");
        if (t.shadow) { VirtualFree(t.shadow, 0, MEM_RELEASE); t.shadow = nullptr; t.changed = nullptr; }
        return;
    }
    LARGE_INTEGER frequency;
    QueryPerformanceFrequency(&frequency);
    t.frequency = frequency.QuadPart;
    t.head = 0; t.tail = 0; t.pending = 0; t.sequence = 0; t.last_origin = 0;
    t.first_qpc = 0; t.deadline = 0;
    t.tasks = 0; t.presents = 0; t.lost = 0; t.refused = 0;
    t.walk_ns_total = t.copy_ns_total = 0; t.walk_ns_max = t.copy_ns_max = 0;
    char session[2048];
    int at = snprintf(session, sizeof session,
                      "schema=scene-lab-tap/1\nwrapper_build=%s\nqpc_frequency=%lld\nrdram_size=%u\n"
                      "ring_bytes=%llu\nseconds=%u\nreference=%u\nfull_every=%u\n"
                      "level_segments=%x\nlevel_delta=%u\nlevel_block_bytes=%u\nrom_header=",
                      GFXWRAP_BUILD_ID, (long long)t.frequency, t.rdram_size,
                      (unsigned long long)t.capacity, c.seconds, c.reference, c.full_every,
                      c.level_segments, c.level_delta, kBlockBytes);
    for (int i = 0; i < 0x40 && t.gfx.HEADER && at < (int)sizeof session - 4; ++i)
        at += snprintf(session + at, sizeof session - at, "%02x", t.gfx.HEADER[i]);
    if (at < (int)sizeof session - 2) { session[at++] = '\n'; session[at] = 0; }
    // The spans as kept (whole words inside RDRAM, within budget).
    for (uint32_t i = 0; i < c.regions && at < (int)sizeof session - 40; ++i)
        at += snprintf(session + at, sizeof session - at, "region=%x:%u\n", c.region[i].begin, c.region[i].bytes);
    for (uint32_t i = 0; i < c.level_regions && at < (int)sizeof session - 40; ++i)
        at += snprintf(session + at, sizeof session - at, "level_region=%x:%u\n", c.level[i].begin, c.level[i].bytes);
    write_text(L"session.txt", session);
    t.finish = false;
    t.writer = CreateThread(nullptr, 0, writer_main, nullptr, 0, nullptr);
    if (!t.writer) {
        VirtualFree(t.ring, 0, MEM_RELEASE);
        t.ring = nullptr;
        if (t.shadow) { VirtualFree(t.shadow, 0, MEM_RELEASE); t.shadow = nullptr; t.changed = nullptr; }
        return;
    }
    t.active.store(true, std::memory_order_release);
}

extern "C" void __cdecl st_process_dlist(void) {
    Tap &t = g_tap;
    if (!t.active.load(std::memory_order_relaxed)) return;
    const int64_t begin = qpc();
    if (!t.first_qpc) { t.first_qpc = begin; t.deadline = begin + (int64_t)t.config.seconds * t.frequency; }
    if (begin >= t.deadline) { st_stop(); return; }
    uint32_t task[scene_closure::kTaskWords];
    memcpy(task, t.gfx.DMEM + 0xFC0, sizeof task);
    const uint32_t task_index = t.tasks.load(std::memory_order_relaxed);
    if (t.config.full_every && (task_index == 0 || task_index % t.config.full_every == 0)) copy_full(begin);
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
    // the same instant: the emulation thread is inside ProcessDList.
    copy_task_regions(task_index, begin);
    copy_level_regions(task_index, begin);
    if (refusals) ++t.refused;
    t.tasks.fetch_add(1, std::memory_order_relaxed);
}

extern "C" void __cdecl st_update_screen(fn_readscreen read_screen) {
    Tap &t = g_tap;
    if (!t.active.load(std::memory_order_relaxed) || !t.first_qpc) return;
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

extern "C" void __cdecl st_stop(void) {
    Tap &t = g_tap;
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
