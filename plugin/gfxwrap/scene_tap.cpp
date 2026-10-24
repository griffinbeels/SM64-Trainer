/* The scene tap (M4 experiment E0/E1): records what each graphics task makes
 * the renderer read, and the picture it presents, for a short armed session.
 *
 * Arming: %LOCALAPPDATA%\SM64Trainer\scene-lab\armed.txt must exist when a
 * practice ROM opens. The tap moves it into a new session folder (so one
 * arming records once) and reads key=value lines:
 *   seconds=<n>       recording length from the first task (default 20, max 600)
 *   reference=0|1     after each new VI origin, ReadScreen the presented picture
 *                     (reference instrumentation: a synchronous GPU readback
 *                     that slows emulation; never part of the producer cost)
 *   full_every=<n>    also copy all of RDRAM on the first and every n-th task
 *                     (diagnostic comparator only)
 *   extra=<addr>:<n>  copy n bytes at this RDRAM address with every task
 *                     (identity fields such as gGlobalTimer; the arming tool
 *                     takes them from memory/layout.py, the plugin knows none)
 * Without that file every call returns after one relaxed atomic load.
 *
 * The emulation thread never waits: it walks the task (scene_closure.h),
 * copies the closure into a preallocated ring and returns; a full ring drops
 * the record and counts it. A writer thread drains the ring to tap.bin.
 * Records are 32-byte aligned: a 32-byte header {magic 'SLT1', type, bytes,
 * sequence, qpc, reserved[2]} then the payload; tools/scene_lab/tap.py reads
 * them. Producer cost (walk + copy) is timed separately from the reference
 * readback. */
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
enum : uint32_t { REC_PAD = 0, REC_TASK = 1, REC_PRESENT = 2, REC_FULL = 3 };
struct RecordHeader { uint32_t magic, type, bytes, sequence; int64_t qpc; uint32_t reserved[2]; };
static_assert(sizeof(RecordHeader) == 32, "tap record header");
constexpr uint32_t kMaxExtras = 16, kMaxExtraBytes = 4096;
constexpr uint64_t kRingBytes = 128ull << 20, kSmallRingBytes = 48ull << 20;

struct Config {
    uint32_t seconds = 20, reference = 1, full_every = 0, extras = 0;
    uint32_t extra_begin[kMaxExtras], extra_bytes[kMaxExtras];
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
    char summary[1024];
    const uint32_t tasks = t.tasks.load();
    snprintf(summary, sizeof summary,
             "tasks=%u\npresents=%u\nlost_records=%u\nrefused_tasks=%u\nbytes_written=%llu\nwrite_failed=%d\n"
             "walk_ns_mean=%u\nwalk_ns_max=%u\ncopy_ns_mean=%u\ncopy_ns_max=%u\n",
             tasks, t.presents.load(), t.lost.load(), t.refused.load(), (unsigned long long)written, failed,
             tasks ? (uint32_t)(t.walk_ns_total / tasks) : 0, t.walk_ns_max,
             tasks ? (uint32_t)(t.copy_ns_total / tasks) : 0, t.copy_ns_max);
    write_text(L"summary.txt", summary);
    VirtualFree(t.ring, 0, MEM_RELEASE);
    t.ring = nullptr;
    return 0;
}

void parse_config(const wchar_t *path, Config &config) {
    FILE *file = _wfopen(path, L"rb");
    if (!file) return;
    char line[256];
    while (fgets(line, sizeof line, file)) {
        unsigned a = 0, b = 0;
        if (sscanf(line, "seconds=%u", &a) == 1) config.seconds = a < 600 ? a : 600;
        else if (sscanf(line, "reference=%u", &a) == 1) config.reference = a != 0;
        else if (sscanf(line, "full_every=%u", &a) == 1) config.full_every = a;
        else if (sscanf(line, "extra=%x:%u", &a, &b) == 2 && config.extras < kMaxExtras
                 && b > 0 && b <= kMaxExtraBytes) {
            config.extra_begin[config.extras] = a & 0x1FFFFFFF;
            config.extra_bytes[config.extras++] = b;
        }
    }
    fclose(file);
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
    t.capacity = kRingBytes;
    t.ring = (uint8_t *)VirtualAlloc(nullptr, (SIZE_T)t.capacity, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (!t.ring) {
        t.capacity = kSmallRingBytes;
        t.ring = (uint8_t *)VirtualAlloc(nullptr, (SIZE_T)t.capacity, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    }
    if (!t.ring) { write_text(L"summary.txt", "ring_allocation_failed=1\n"); return; }
    LARGE_INTEGER frequency;
    QueryPerformanceFrequency(&frequency);
    t.frequency = frequency.QuadPart;
    t.head = 0; t.tail = 0; t.pending = 0; t.sequence = 0; t.last_origin = 0;
    t.first_qpc = 0; t.deadline = 0;
    t.tasks = 0; t.presents = 0; t.lost = 0; t.refused = 0;
    t.walk_ns_total = t.copy_ns_total = 0; t.walk_ns_max = t.copy_ns_max = 0;
    char session[1024];
    int at = snprintf(session, sizeof session,
                      "schema=scene-lab-tap/1\nwrapper_build=%s\nqpc_frequency=%lld\nrdram_size=%u\n"
                      "ring_bytes=%llu\nseconds=%u\nreference=%u\nfull_every=%u\nrom_header=",
                      GFXWRAP_BUILD_ID, (long long)t.frequency, t.rdram_size,
                      (unsigned long long)t.capacity, t.config.seconds, t.config.reference, t.config.full_every);
    for (int i = 0; i < 0x40 && t.gfx.HEADER && at < (int)sizeof session - 4; ++i)
        at += snprintf(session + at, sizeof session - at, "%02x", t.gfx.HEADER[i]);
    if (at < (int)sizeof session - 2) { session[at++] = '\n'; session[at] = 0; }
    write_text(L"session.txt", session);
    t.finish = false;
    t.writer = CreateThread(nullptr, 0, writer_main, nullptr, 0, nullptr);
    if (!t.writer) { VirtualFree(t.ring, 0, MEM_RELEASE); t.ring = nullptr; return; }
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
    if (t.config.reference && read_screen) {
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
