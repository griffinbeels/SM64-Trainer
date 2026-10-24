// The closure walker against hand-built Fast3D lists: what it marks, and each
// way it refuses instead of guessing. Prints "scene closure passed" on success.
#include <stdio.h>
#include <stdlib.h>
#include <vector>
#include "scene_closure.h"

static int g_failures;
#define CHECK(cond) do { if (!(cond)) { printf("FAIL line %d: %s\n", __LINE__, #cond); ++g_failures; } } while (0)

struct Image {
    std::vector<uint8_t> rdram = std::vector<uint8_t>(4u << 20);
    uint32_t task[16] = {1, 0, 0, 0, 0x300000, 0x1000, 0x301000, 0x800, 0, 0, 0, 0, 0x1000, 0, 0, 0};
    void cmd(uint32_t at, uint32_t w0, uint32_t w1) {
        memcpy(&rdram[at], &w0, 4);
        memcpy(&rdram[at + 4], &w1, 4);
    }
};

static bool marked(const scene_closure::Walker &w, uint32_t begin, uint32_t end) {
    bool inside = false;
    w.runs([&](uint32_t a, uint32_t b) { if (a <= begin && end <= b) inside = true; });
    return inside;
}

static uint32_t total(const scene_closure::Walker &w) {
    uint32_t bytes = 0;
    w.runs([&](uint32_t a, uint32_t b) { bytes += b - a; });
    return bytes;
}

static scene_closure::Walker walker;

int main() {
    {   // A complete list: segments, nesting, branch, every reader kind.
        Image m;
        uint32_t at = 0x1000;
        auto put = [&](uint32_t w0, uint32_t w1) { m.cmd(at, w0, w1); at += 8; };
        put(0xBC000006 | (6 << 10), 0x80100000);       // G_MW_SEGMENT 6 -> 0x100000
        put(0x01010040, 0x06000000);                    // G_MTX 64 bytes at seg 6
        put(0x03800010, 0x06000040);                    // viewport
        put(0x03860010, 0x06000080);                    // light 0
        put(0x04300040, 0x06000100);                    // G_VTX n=4 v0=0
        put(0x06000000, 0x06000200);                    // G_DL push
        put(0xFD100000 | 31, 0x00200000);               // SETTIMG 16b width 32
        put(0xF5100000, 0x07000000);                    // SETTILE tile 7, 16b
        put(0xF3000000, 0x073FF000);                    // LOADBLOCK lrs=1023
        put(0xFD100000, 0x00210000);                    // SETTIMG palette
        put(0xF5100100, 0x07000000);                    // SETTILE tmem 256
        put(0xF0000000, 0x0703C000);                    // LOADTLUT 16 entries
        put(0x06010000, 0x00003000);                    // G_DL branch
        m.cmd(0x100200, 0xBF000000, 0x00000A14);        // sub list: TRI1, ENDDL
        m.cmd(0x100208, 0xB8000000, 0);
        m.cmd(0x3000, 0xB8000000, 0);                    // branch target ends the task
        CHECK(walker.walk(m.rdram.data(), 4u << 20, m.task, 1000) == 0);
        CHECK(walker.stats.segments_set == (1u << 6));
        CHECK(walker.stats.max_depth == 1);
        CHECK(marked(walker, 0x300000, 0x301000) && marked(walker, 0x301000, 0x301800));
        CHECK(marked(walker, 0x1000, at + 8));          // master list plus the renderer's peek
        CHECK(marked(walker, 0x100200, 0x100218));
        CHECK(marked(walker, 0x3000, 0x3010));
        CHECK(marked(walker, 0x100000, 0x100040));      // matrix
        CHECK(marked(walker, 0x100040, 0x100050));      // viewport
        CHECK(marked(walker, 0x100080, 0x10008C));      // light
        CHECK(marked(walker, 0x100100, 0x100140));      // 4 vertices
        CHECK(marked(walker, 0x200000, 0x200800));      // 1024 16-bit texels
        CHECK(marked(walker, 0x210000, 0x210020));      // 16 palette entries
        CHECK(!marked(walker, 0x200800, 0x200820));
        CHECK(walker.stats.bytes[scene_closure::TEXTURE] == 2048);
        CHECK(total(walker) < 0x5000);
    }
    {   // A segment the task never set: the renderer would use a stale base.
        Image m;
        m.cmd(0x1000, 0x04300040, 0x05000100);
        m.cmd(0x1008, 0xB8000000, 0);
        CHECK(walker.walk(m.rdram.data(), 4u << 20, m.task, 1000) & scene_closure::UNBOUND_SEGMENT);
    }
    {   // Branch to self: the renderer halts there.
        Image m;
        m.cmd(0x1000, 0x06010000, 0x00001000);
        CHECK(walker.walk(m.rdram.data(), 4u << 20, m.task, 1000) & scene_closure::INFINITE_LOOP);
    }
    {   // Eleven nested pushes overflow the renderer's ten-entry stack.
        Image m;
        for (uint32_t i = 0; i < 12; ++i) m.cmd(0x1000 + i * 0x100, 0x06000000, 0x1000 + (i + 1) * 0x100);
        CHECK(walker.walk(m.rdram.data(), 4u << 20, m.task, 1000) & scene_closure::STACK_OVERFLOW);
    }
    {   // An opcode outside the modelled microcode.
        Image m;
        m.cmd(0x1000, 0x20000000, 0);
        m.cmd(0x1008, 0xB8000000, 0);
        CHECK(walker.walk(m.rdram.data(), 4u << 20, m.task, 1000) & scene_closure::UNKNOWN_COMMAND);
    }
    {   // A list longer than the caller allows.
        Image m;
        for (uint32_t i = 0; i < 64; ++i) m.cmd(0x1000 + i * 8, 0xE7000000, 0);
        m.cmd(0x1000 + 64 * 8, 0xB8000000, 0);
        CHECK(walker.walk(m.rdram.data(), 4u << 20, m.task, 32) & scene_closure::COMMAND_LIMIT);
        CHECK(walker.walk(m.rdram.data(), 4u << 20, m.task, 1000) == 0);
    }
    {   // A texture read from the image this task draws: not an RDRAM input.
        Image m;
        m.cmd(0x1000, 0xFF100000 | 319, 0x00380000);
        m.cmd(0x1008, 0xFD100000 | 319, 0x00380000);
        m.cmd(0x1010, 0xF3000000, 0x073FF000);
        m.cmd(0x1018, 0xB8000000, 0);
        CHECK(walker.walk(m.rdram.data(), 4u << 20, m.task, 1000) & scene_closure::FRAMEBUFFER_TEXTURE);
        CHECK(walker.last_cimg == 0x380000);
    }
    {   // Not a graphics task.
        Image m;
        m.task[0] = 2;
        CHECK(walker.walk(m.rdram.data(), 4u << 20, m.task, 1000) & scene_closure::BAD_TASK);
    }
    if (g_failures) return 1;
    printf("scene closure passed\n");
    return 0;
}
