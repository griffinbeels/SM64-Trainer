/* The E2 evaluator's per-block and per-instruction hooks in native code (x64 DLL, lab only).
 *
 * e2_georender.Sandbox registers these with Unicorn's uc_hook_add instead of its Python hooks:
 * a Python callback per executed instruction costs ~0.7 us, which made one geo_process_root run
 * ~190 ms. The semantics are cpu_hooks.py's reference implementation, byte for byte:
 *
 * scene_on_block    counts instructions and calls back into Python (miss) for every block whose
 *                   start has not passed the ROM check; a passed block marks its bytes executed.
 * scene_on_access   decodes the instruction's load or store (cpu_hooks.ACCESS) before it runs and
 *                   marks each RDRAM byte: first touch (1 read, 2 written), read, written.
 *
 * scene_on_view_entry / scene_on_view_return  (registered on one address each) log every
 *                   obj_is_in_view decision: the node it was given and what it returned.
 *
 * Every mask byte set also marks its 4 KiB page dirty, so the next run clears only those pages.
 * No Unicorn header is needed: the two API functions used come in as pointers from Python.
 */
#include <stdint.h>
#include <string.h>

#define RDRAM_BYTES (8u << 20)
#define PAGE_SHIFT 12

typedef int (*reg_read_fn)(void *uc, int regid, void *value);
typedef void (*miss_fn)(void *uc, uint64_t address, uint32_t size, void *user);

typedef struct {
    uint8_t *ram;                       /* RDRAM as the CPU sees it, big-endian */
    uint8_t *first, *read, *wrote, *code;
    uint8_t *verified;                  /* per word: a block starting here passed the ROM check */
    uint8_t *dirty;                     /* per page: a mask byte was set since the last clear */
    reg_read_fn reg_read;
    miss_fn miss;
    int32_t gpr[32];                    /* Unicorn register ids of r0..r31 */
    uint32_t sentinel;
    int32_t stop;                       /* the miss callback refused the block */
    uint64_t instructions;
    uint64_t stack_accesses;
    uint32_t *culling;                  /* (node, visible) pairs, culling_capacity of them */
    uint32_t culling_capacity;
    uint32_t culling_count;             /* may exceed the capacity: the excess is not stored */
    uint32_t pending_view;
    int32_t reg_a0, reg_v0;
} scene_cpu;

/* opcode -> write, bytes, kind (0 plain, 1 left, 2 right); bytes 0: not a load or store */
static const uint8_t ACCESS[64][3] = {
    [0x20] = {0, 1, 0}, [0x21] = {0, 2, 0}, [0x22] = {0, 4, 1}, [0x23] = {0, 4, 0}, [0x24] = {0, 1, 0},
    [0x25] = {0, 2, 0}, [0x26] = {0, 4, 2}, [0x27] = {0, 4, 0}, [0x30] = {0, 4, 0}, [0x31] = {0, 4, 0},
    [0x34] = {0, 8, 0}, [0x35] = {0, 8, 0}, [0x37] = {0, 8, 0}, [0x1A] = {0, 8, 1}, [0x1B] = {0, 8, 2},
    [0x28] = {1, 1, 0}, [0x29] = {1, 2, 0}, [0x2A] = {1, 4, 1}, [0x2B] = {1, 4, 0}, [0x2C] = {1, 8, 1},
    [0x2D] = {1, 8, 2}, [0x2E] = {1, 4, 2}, [0x38] = {1, 4, 0}, [0x39] = {1, 4, 0}, [0x3C] = {1, 8, 0},
    [0x3D] = {1, 8, 0}, [0x3F] = {1, 8, 0},
};

static void mark_pages(scene_cpu *c, uint32_t begin, uint32_t end)
{
    for (uint32_t page = begin >> PAGE_SHIFT; page <= (end - 1) >> PAGE_SHIFT; page++)
        c->dirty[page] = 1;
}

/* The access an instruction word makes: 1 and (write, base register, offset, bytes, kind), or 0. */
__declspec(dllexport) int scene_decode(uint32_t word, int32_t out[5])
{
    const uint8_t *entry = ACCESS[word >> 26];
    if (!entry[1])
        return 0;
    out[0] = entry[0];
    out[1] = (word >> 21) & 31;
    out[2] = (int16_t)(word & 0xFFFF);
    out[3] = entry[1];
    out[4] = entry[2];
    return 1;
}

/* Mark one access at a register value: the semantics of cpu_hooks.mark_access. */
__declspec(dllexport) void scene_mark(scene_cpu *c, uint32_t word, uint64_t base_value)
{
    int32_t access[5];
    if (!scene_decode(word, access))
        return;
    uint32_t size = (uint32_t)access[3];
    uint32_t target = ((uint32_t)(base_value + (int64_t)access[2])) & 0x1FFFFFFF;
    if (access[4] == 1) {
        size -= target & (size - 1);
    } else if (access[4] == 2) {
        uint32_t aligned = target & ~(size - 1);
        size = (target & (size - 1)) + 1;
        target = aligned;
    }
    if (target >= RDRAM_BYTES) {
        c->stack_accesses++;
        return;
    }
    uint32_t end = target + size > RDRAM_BYTES ? RDRAM_BYTES : target + size;
    uint8_t mark = access[0] ? 2 : 1;
    uint8_t *set = access[0] ? c->wrote : c->read;
    for (uint32_t at = target; at < end; at++) {
        if (!c->first[at])
            c->first[at] = mark;
        set[at] = 1;
    }
    mark_pages(c, target, end);
}

__declspec(dllexport) void scene_on_access(void *uc, uint64_t address, uint32_t size, scene_cpu *c)
{
    (void)size;
    uint32_t at = (uint32_t)address & 0x1FFFFFFF;
    if (at > RDRAM_BYTES - 4)
        return;
    const uint8_t *p = c->ram + at;
    uint32_t word = ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3];
    if (!ACCESS[word >> 26][1])
        return;
    uint64_t base_value = 0;
    c->reg_read(uc, c->gpr[(word >> 21) & 31], &base_value);
    scene_mark(c, word, base_value);
}

__declspec(dllexport) void scene_on_block(void *uc, uint64_t address, uint32_t size, scene_cpu *c)
{
    uint32_t virtual_address = (uint32_t)address;
    if (virtual_address == c->sentinel)
        return;
    c->instructions += size / 4;
    uint32_t at = virtual_address & 0x1FFFFFFF;
    if (at >= RDRAM_BYTES || !c->verified[at >> 2]) {
        c->stop = 0;
        c->miss(uc, address, size, c);
        if (c->stop)
            return;
    }
    if (c->code && at < RDRAM_BYTES) {
        uint32_t end = at + size > RDRAM_BYTES ? RDRAM_BYTES : at + size;
        memset(c->code + at, 1, end - at);
        mark_pages(c, at, end);
    }
}

__declspec(dllexport) void scene_on_view_entry(void *uc, uint64_t address, uint32_t size, scene_cpu *c)
{
    (void)address;
    (void)size;
    uint64_t node = 0;
    c->reg_read(uc, c->reg_a0, &node);
    c->pending_view = (uint32_t)node;
}

__declspec(dllexport) void scene_on_view_return(void *uc, uint64_t address, uint32_t size, scene_cpu *c)
{
    (void)address;
    (void)size;
    uint64_t visible = 0;
    c->reg_read(uc, c->reg_v0, &visible);
    if (c->culling_count < c->culling_capacity) {
        c->culling[2 * c->culling_count] = c->pending_view;
        c->culling[2 * c->culling_count + 1] = (uint32_t)visible;
    }
    c->culling_count++;
}

/* Clear every mask byte set since the last clear, page by page, and the per-run counters. */
__declspec(dllexport) void scene_clear(scene_cpu *c)
{
    uint8_t *masks[4] = {c->first, c->read, c->wrote, c->code};
    for (uint32_t page = 0; page < RDRAM_BYTES >> PAGE_SHIFT; page++) {
        if (!c->dirty[page])
            continue;
        for (int m = 0; m < 4; m++)
            if (masks[m])
                memset(masks[m] + ((size_t)page << PAGE_SHIFT), 0, (size_t)1 << PAGE_SHIFT);
        c->dirty[page] = 0;
    }
    c->instructions = 0;
    c->stack_accesses = 0;
    c->stop = 0;
    c->culling_count = 0;
    c->pending_view = 0;
}
