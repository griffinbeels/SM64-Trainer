/* The scene capture's closure walker (M4 experiment E0): which RDRAM bytes one
 * SM64 graphics task makes the renderer read.
 *
 * The HLE renderer processes the whole task synchronously inside ProcessDList,
 * so the bytes it reads at that instant ARE the frame's input. This walker
 * follows the same Fast3D command stream the renderer executes (LINK's
 * GLideN64 v4.2, uCodes/F3D.cpp, gSP.cpp, gDP.cpp) and marks every byte range
 * those handlers read: the display lists themselves plus the 8-byte peek past
 * each command, vertices, matrices, lights/look-at, viewports, texture and
 * palette loads, and the 4 KiB ucode text / 2 KiB ucode data the renderer
 * hashes to identify the microcode. It records nothing else and decides
 * nothing about mutability: copying a range coherently at this occurrence is
 * what makes a frame reproducible; deduplication belongs to the writer.
 *
 * The walk is a superset where the renderer branches on computed state: a
 * G_CULLDL that the renderer takes would skip commands this walker still
 * marks. Anything the walker cannot follow is a refusal bit, never a guess:
 * the caller must not publish a frame whose closure is not complete.
 *
 * Bounded and allocation-free: a 32-byte-granule bitmap over 8 MiB (32 KiB)
 * plus fixed counters. Addresses are physical. RDRAM is Project64's
 * word-swapped image (GFX_INFO.MemoryBswaped): an aligned u32 reads natively.
 * Proof that the marked set is sufficient is external (tools/scene_lab): the
 * real renderer draws the task from RDRAM poisoned outside these ranges. */
#pragma once
#include <stdint.h>
#include <string.h>

namespace scene_closure {

constexpr uint32_t kGranuleShift = 5;
constexpr uint32_t kMaxRdram = 8u << 20;
constexpr uint32_t kBitmapWords = (kMaxRdram >> kGranuleShift) / 64;
constexpr uint32_t kStackDepth = 10;        // GBI.PCStackSize for F3D
constexpr uint32_t kTaskWords = 16;         // OSTask at DMEM 0xFC0

enum Kind : uint8_t { UCODE, DL, VTX, MTX, LIGHT, VIEWPORT, TEXTURE, TLUT, KIND_COUNT };

enum Refusal : uint32_t {
    COMMAND_LIMIT = 1u << 0,      // more commands than the caller allows
    STACK_OVERFLOW = 1u << 1,     // a push past the renderer's 10-entry stack
    OUT_OF_RANGE = 1u << 2,       // a read past RDRAM (the renderer skips or wraps)
    UNBOUND_SEGMENT = 1u << 3,    // a segment used before this task set it
    UNKNOWN_COMMAND = 1u << 4,    // an opcode this walker does not model
    FRAMEBUFFER_TEXTURE = 1u << 5,// a texture read from this task's color image
    INFINITE_LOOP = 1u << 6,      // branch to self: the renderer halts there
    BAD_TASK = 1u << 7,           // not a graphics task this walker models
};

// Fast3D opcodes (uCodes/F3D.h) and the RDP commands that read RDRAM.
enum : uint8_t {
    F3D_MTX = 0x01, F3D_MOVEMEM = 0x03, F3D_VTX = 0x04, F3D_DL = 0x06,
    F3D_SPRITE2D_BASE = 0x09, F3D_TRI1 = 0xBF, F3D_CULLDL = 0xBE, F3D_POPMTX = 0xBD,
    F3D_MOVEWORD = 0xBC, F3D_ENDDL = 0xB8,
    G_SETCIMG = 0xFF, G_SETZIMG = 0xFE, G_SETTIMG = 0xFD, G_SETTILE = 0xF5,
    G_LOADBLOCK = 0xF3, G_LOADTILE = 0xF4, G_LOADTLUT = 0xF0, G_SETTILESIZE = 0xF2,
};

struct Stats {
    uint32_t commands;
    uint32_t refusals;
    uint32_t max_depth;
    uint32_t segments_set;        // bit per segment set by this task
    uint32_t color_images;
    uint32_t reads[KIND_COUNT];   // handler reads per kind
    uint64_t bytes[KIND_COUNT];   // bytes read per kind, before merging
    uint32_t opcodes[256];
};

struct Walker {
    uint64_t bitmap[kBitmapWords];
    Stats stats;

    uint32_t mask = kMaxRdram - 1;
    uint32_t segment[16];
    // Texture state the load handlers need (gDP.textureImage, gDP.tiles[]).
    uint32_t timg_address, timg_size, timg_width;
    struct Tile { uint32_t size, line, uls, ult, lrs, lrt; } tile[8];
    uint32_t cimg[4];
    uint32_t cimg_count;
    uint32_t last_cimg;           // the image the task draws last: SM64's framebuffer
    // Optional per-read observer for probes; null in the producer.
    void (*trace)(void *context, uint32_t begin, uint32_t bytes, Kind kind) = nullptr;
    void *trace_context = nullptr;

    void reset(uint32_t rdram_size) {
        memset(bitmap, 0, sizeof bitmap);
        memset(&stats, 0, sizeof stats);
        memset(segment, 0, sizeof segment);
        memset(tile, 0, sizeof tile);
        mask = rdram_size - 1;
        timg_address = timg_size = timg_width = 0;
        cimg_count = 0;
        last_cimg = 0;
    }

    void mark(uint32_t begin, uint32_t bytes, Kind kind) {
        ++stats.reads[kind];
        stats.bytes[kind] += bytes;
        if (trace) trace(trace_context, begin, bytes, kind);
        if (bytes == 0) return;
        if (begin > mask || bytes > mask + 1 - begin) { stats.refusals |= OUT_OF_RANGE; return; }
        uint32_t first = begin >> kGranuleShift, last = (begin + bytes - 1) >> kGranuleShift;
        for (uint32_t g = first; g <= last; ++g) bitmap[g >> 6] |= 1ull << (g & 63);
    }

    uint32_t physical(uint32_t segmented) {
        uint32_t index = (segmented >> 24) & 0x0F;
        if (index && !(stats.segments_set & (1u << index))) stats.refusals |= UNBOUND_SEGMENT;
        return (segment[index] + (segmented & mask)) & mask;   // RSP_SegmentToPhysical
    }

    static uint32_t word(const uint8_t *rdram, uint32_t address) {
        uint32_t value;
        memcpy(&value, rdram + address, 4);
        return value;
    }

    void texture_load(uint8_t op, uint32_t w0, uint32_t w1) {
        Tile &t = tile[(w1 >> 24) & 7];
        const uint32_t bpl = timg_width << timg_size >> 1;
        if (cimg_count) {
            for (uint32_t i = 0; i < cimg_count && i < 4; ++i)
                if (timg_address == cimg[i]) stats.refusals |= FRAMEBUFFER_TEXTURE;
        }
        if (op == G_LOADBLOCK) {
            // gDPLoadBlock: raw uls/ult/lrs; bytes from the load tile's size, rounded to 8.
            uint32_t uls = (w0 >> 12) & 0xFFF, ult = w0 & 0xFFF, lrs = (w1 >> 12) & 0xFFF;
            uint32_t width = (lrs - uls + 1) & 0x0FFF;
            uint32_t size = t.size > timg_size ? t.size : timg_size;
            uint32_t bytes = (width << size >> 1);
            bytes = (bytes + 7) & ~7u;
            if (t.size == 3) bytes *= 2;   // gDPLoadBlock32 reads both halves
            mark(timg_address + ult * bpl + (uls << timg_size >> 1), bytes, TEXTURE);
        } else if (op == G_LOADTILE) {
            // gDPLoadTile after gDPSetTileSize: 10.2 coordinates, whole rows.
            t.uls = (w0 >> 14) & 0x3FF; t.ult = (w0 >> 2) & 0x3FF;
            t.lrs = (w1 >> 14) & 0x3FF; t.lrt = (w1 >> 2) & 0x3FF;
            if (t.lrs < t.uls || t.lrt < t.ult) return;
            uint32_t width = (t.lrs - t.uls + 1) & 0x3FF, height = (t.lrt - t.ult + 1) & 0x3FF;
            uint32_t row = ((width + 7) & ~7u) << (t.size > timg_size ? t.size : timg_size) >> 1;
            uint32_t start = timg_address + t.ult * bpl + (t.uls << timg_size >> 1);
            mark(start, (height - 1) * bpl + row, TEXTURE);
        } else {
            // gDPLoadTLUT: (lrs-uls+1)*(lrt-ult+1) 16-bit entries.
            t.uls = (w0 >> 14) & 0x3FF; t.ult = (w0 >> 2) & 0x3FF;
            t.lrs = (w1 >> 14) & 0x3FF; t.lrt = (w1 >> 2) & 0x3FF;
            uint32_t count = ((t.lrs - t.uls + 1) * (t.lrt - t.ult + 1)) & 0xFFFF;
            mark(timg_address + t.ult * bpl + (t.uls << timg_size >> 1), count * 2, TLUT);
            mark(timg_address, count * 2, TLUT);   // the texture filter's palette copy
        }
    }

    // Walk one task. `task` is the OSTask words as the renderer reads them from
    // DMEM 0xFC0 (physical pointers). Returns the refusal bits (0 = complete).
    uint32_t walk(const uint8_t *rdram, uint32_t rdram_size, const uint32_t task[kTaskWords],
                  uint32_t command_limit) {
        reset(rdram_size);
        if (task[0] != 1 || rdram_size > kMaxRdram || (rdram_size & (rdram_size - 1)))
            return stats.refusals |= BAD_TASK;
        mark(task[4] & 0x1FFFFFFF, 4096, UCODE);
        mark(task[6] & 0x1FFFFFFF, 2048, UCODE);
        uint32_t pc[kStackDepth];
        uint32_t depth = 0;
        pc[0] = task[12] & mask;
        uint32_t run = pc[0];
        auto close_run = [&](uint32_t end) { if (end > run) mark(run, end - run, DL); };
        for (;;) {
            if (stats.commands >= command_limit) { stats.refusals |= COMMAND_LIMIT; break; }
            uint32_t at = pc[depth];
            if (at + 16 > mask + 1) { stats.refusals |= OUT_OF_RANGE; break; }
            const uint32_t w0 = word(rdram, at), w1 = word(rdram, at + 4);
            const uint8_t op = (uint8_t)(w0 >> 24);
            ++stats.commands;
            ++stats.opcodes[op];
            pc[depth] = at + 8;
            switch (op) {
            case F3D_MTX:
                if ((w0 & 0xFFFF) == 64) mark(physical(w1), 64, MTX);
                break;
            case F3D_MOVEMEM: {
                const uint32_t index = (w0 >> 16) & 0xFF, address = physical(w1);
                if (index == 0x80) mark(address, 16, VIEWPORT);
                else if (index == 0x9E) { mark(address, 64, MTX); pc[depth] += 24; }
                else if (index >= 0x82 && index <= 0x94) mark(address, 12, LIGHT);
                break;
            }
            case F3D_VTX: {
                const uint32_t n = ((w0 >> 20) & 0xF) + 1, v0 = (w0 >> 16) & 0xF;
                if (n + v0 <= 80) mark(physical(w1), n * 16, VTX);
                break;
            }
            case F3D_DL: {
                const uint32_t target = physical(w1);
                close_run(at + 16);
                if (((w0 >> 16) & 0xFF) == 0) {
                    if (depth + 1 >= kStackDepth) { stats.refusals |= STACK_OVERFLOW; return stats.refusals; }
                    pc[++depth] = target;
                    if (depth > stats.max_depth) stats.max_depth = depth;
                } else {
                    if (target == at) { stats.refusals |= INFINITE_LOOP; return stats.refusals; }
                    pc[depth] = target;
                }
                run = pc[depth];
                break;
            }
            case F3D_ENDDL:
                close_run(at + 16);
                if (depth == 0) return stats.refusals;
                run = pc[--depth];
                break;
            case F3D_MOVEWORD:
                if ((w0 & 0xFF) == 0x06) {   // G_MW_SEGMENT
                    const uint32_t index = (w0 >> 10) & 0xF;
                    segment[index] = w1 & 0x00FFFFFF;
                    stats.segments_set |= 1u << index;
                }
                break;
            case F3D_SPRITE2D_BASE:
                stats.refusals |= UNKNOWN_COMMAND;
                break;
            case G_SETCIMG:
                last_cimg = physical(w1);
                if (cimg_count < 4) cimg[cimg_count] = last_cimg;
                ++cimg_count;
                ++stats.color_images;
                break;
            case G_SETZIMG:
                physical(w1);
                break;
            case G_SETTIMG:
                timg_size = (w0 >> 19) & 3;
                timg_width = (w0 & 0xFFF) + 1;
                timg_address = physical(w1);
                break;
            case G_SETTILE: {
                Tile &t = tile[(w1 >> 24) & 7];
                t.size = (w0 >> 19) & 3;
                t.line = (w0 >> 9) & 0x1FF;
                break;
            }
            case G_SETTILESIZE: {
                Tile &t = tile[(w1 >> 24) & 7];
                t.uls = (w0 >> 14) & 0x3FF; t.ult = (w0 >> 2) & 0x3FF;
                t.lrs = (w1 >> 14) & 0x3FF; t.lrt = (w1 >> 2) & 0x3FF;
                break;
            }
            case G_LOADBLOCK: case G_LOADTILE: case G_LOADTLUT:
                texture_load(op, w0, w1);
                break;
            default:
                // F3D immediates, the RDP state/draw commands and RDPHALF words
                // read nothing beyond the command stream itself.
                if (op >= 0x0A && op < 0xB1) stats.refusals |= UNKNOWN_COMMAND;
                break;
            }
        }
        close_run(pc[depth] + 8);
        return stats.refusals;
    }

    // Visit the merged closure as [begin, end) runs at granule resolution.
    // Word-at-a-time: empty and full 64-granule words cost one comparison.
    template <class Visit> void runs(Visit visit) const {
        const uint32_t words = ((mask + 1) >> kGranuleShift) / 64;
        uint32_t begin = 0;
        bool open = false;
        for (uint32_t w = 0; w < words; ++w) {
            const uint64_t bits = bitmap[w];
            if (bits == (open ? ~0ull : 0ull)) continue;
            for (uint32_t b = 0; b < 64; ++b) {
                const bool set = (bits >> b) & 1;
                if (set == open) continue;
                const uint32_t at = (w * 64 + b) << kGranuleShift;
                if (set) begin = at; else visit(begin, at);
                open = set;
            }
        }
        if (open) visit(begin, mask + 1);
    }
};

}  // namespace scene_closure
